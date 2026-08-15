# This Python file uses the following encoding: utf-8
# AST 静态门禁：确保所有 before model validator 对输入 dict 的 literal key 迁移都已登记。
# 任何新增"删除/重命名 literal key"的 validator 都会让本测试失败，必须同步登记到
# LEGACY_ALIAS_MIGRATIONS（动态 serializer 的 _N 重建走 DYNAMIC_PATH_SET_REGISTRY，不在此列）。
import ast
from pathlib import Path

from module.config.config_validation import legacy_source_paths
from module.config.utils import convert_to_underscore


def _model_validator_aliases(tree: ast.Module) -> tuple[set[str], set[str]]:
    """收集 `model_validator` 的直接导入别名与 pydantic 模块别名。"""
    direct: set[str] = {"model_validator"}
    modules: set[str] = {"pydantic"}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "pydantic":
            for name in node.names:
                if name.name == "model_validator":
                    direct.add(name.asname or name.name)
        elif isinstance(node, ast.Import):
            for name in node.names:
                if name.name == "pydantic":
                    modules.add(name.asname or name.name)
    return direct, modules


def _decorator_is_model_validator(func: ast.expr, direct: set[str], modules: set[str]) -> bool:
    if isinstance(func, ast.Name):
        return func.id in direct
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "model_validator"
        and isinstance(func.value, ast.Name)
        and func.value.id in modules
    )


def _is_before_validator(fn, direct: set[str], modules: set[str]) -> bool:
    """判断函数是否被任意合法导入形式的 model_validator(mode='before') 装饰。"""
    for dec in fn.decorator_list:
        if not isinstance(dec, ast.Call) or not _decorator_is_model_validator(dec.func, direct, modules):
            continue
        for kw in dec.keywords:
            if kw.arg == "mode" and isinstance(kw.value, ast.Constant) and kw.value.value == "before":
                return True
    return False


def _walk_scoped_assignments(fn) -> list[tuple[tuple[int, ...], ast.AST]]:
    """按源码顺序产出 (嵌套函数作用域栈, 赋值节点)。

    遍历时维护 FunctionDef/AsyncFunctionDef 作用域栈，避免嵌套内层函数的
    赋值被当作外层重绑定污染别名/literal 分析。
    """
    result: list[tuple[tuple[int, ...], ast.AST]] = []

    def visit(node, scope_stack):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, scope_stack + (id(child),))
            elif isinstance(child, (ast.Assign, ast.AnnAssign)):
                result.append((scope_stack, child))
                visit(child, scope_stack)
            else:
                visit(child, scope_stack)

    visit(fn, ())
    return result


def _scope_stack_of(fn, target: ast.AST) -> tuple[int, ...]:
    """返回 target 相对 fn 的嵌套函数作用域栈（外层函数 id 在前）。"""
    def find(node, scope_stack):
        if node is target:
            return scope_stack
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found = find(child, scope_stack + (id(child),))
                if found is not None:
                    return found
            else:
                found = find(child, scope_stack)
                if found is not None:
                    return found
        return None

    return find(fn, ()) or ()


def _scoped_assignments_before(fn, use_node: ast.AST) -> list[ast.AST]:
    """收集使用点之前且处于使用点同函数或祖先作用域的赋值节点。

    嵌套内层函数内的赋值不属于使用点的祖先/同层作用域，一律忽略。
    """
    use_scope = _scope_stack_of(fn, use_node)
    use_position = (use_node.lineno, use_node.col_offset)
    result = []
    for scope, node in _walk_scoped_assignments(fn):
        if (node.lineno, node.col_offset) >= use_position:
            continue
        if scope != use_scope[:len(scope)]:
            continue
        result.append(node)
    return result


def _literal_bindings_before(fn, use_node: ast.AST) -> dict[str, str]:
    """按源码顺序解析使用点之前仍生效的 literal 名称绑定。"""
    bindings: dict[str, str] = {}
    for node in _scoped_assignments_before(fn, use_node):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        target_names = {target.id for target in targets if isinstance(target, ast.Name)}
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for target in target_names:
                bindings[target] = node.value.value
        elif isinstance(node.value, ast.Name) and node.value.id in bindings:
            for target in target_names:
                bindings[target] = bindings[node.value.id]
        else:
            # 名称改绑为非 literal 后清除旧值，避免后续 pop 使用过期 key。
            for target in target_names:
                bindings.pop(target, None)
    return bindings


def _literal_key(node: ast.expr, bindings: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    return None


def _input_parameter_name(fn) -> str | None:
    """before validator 的配置输入是排除 cls/self 后的第一个位置参数。"""
    for arg in fn.args.args:
        if arg.arg not in {"cls", "self"}:
            return arg.arg
    return None


def _is_input_dict_derivation(value: ast.expr, aliases: set[str]) -> bool:
    """判断赋值值是否由输入 dict/其后代通过下标或 get 派生。"""
    if isinstance(value, ast.Name):
        return value.id in aliases
    if isinstance(value, ast.Subscript):
        return isinstance(value.value, ast.Name) and value.value.id in aliases
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and value.func.attr == "get"
        and isinstance(value.func.value, ast.Name)
        and value.func.value.id in aliases
    )


def _input_aliases_before(fn, root: str, use_node: ast.AST) -> set[str]:
    """按源码顺序返回使用点之前仍指向输入 dict 或其嵌套 dict 的名称别名。"""
    aliases = {root}
    for node in _scoped_assignments_before(fn, use_node):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        target_names = {target.id for target in targets if isinstance(target, ast.Name)}
        if _is_input_dict_derivation(node.value, aliases):
            aliases.update(target_names)
        else:
            # 名称重新绑定到其他值后不再视为输入 dict，避免扫描无关字典。
            aliases.difference_update(target_names)
    return aliases


def _root_subscript_key(node: ast.expr, roots: set[str], bindings: dict[str, str]) -> str | None:
    if not isinstance(node, ast.Subscript):
        return None
    if not isinstance(node.value, ast.Name) or node.value.id not in roots:
        return None
    return _literal_key(node.slice, bindings)


def _migration_source_keys(fn) -> set[str]:
    """只识别配置输入 dict 自身的 pop/del 与赋值式重命名源 key。"""
    root = _input_parameter_name(fn)
    if root is None:
        return set()
    keys: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            bindings = _literal_bindings_before(fn, node)
            roots = _input_aliases_before(fn, root, node)
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "pop"
                and isinstance(func.value, ast.Name)
                and func.value.id in roots
                and node.args
            ):
                literal = _literal_key(node.args[0], bindings)
                if literal is not None:
                    keys.add(literal)
        elif isinstance(node, ast.Delete):
            bindings = _literal_bindings_before(fn, node)
            roots = _input_aliases_before(fn, root, node)
            for target in node.targets:
                literal = _root_subscript_key(target, roots, bindings)
                if literal is not None:
                    keys.add(literal)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            # 仅 `data['new'] = data['old']` 或 `data['new'] = data.get('old')` 属重命名
            bindings = _literal_bindings_before(fn, node)
            roots = _input_aliases_before(fn, root, node)
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(_root_subscript_key(target, roots, bindings) is not None for target in targets):
                continue
            literal = _root_subscript_key(node.value, roots, bindings)
            if literal is not None:
                keys.add(literal)
                continue
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute):
                if (
                    node.value.func.attr == "get"
                    and isinstance(node.value.func.value, ast.Name)
                    and node.value.func.value.id in roots
                    and node.value.args
                ):
                    literal = _literal_key(node.value.args[0], bindings)
                    if literal is not None:
                        keys.add(literal)
    return keys


def discover_literal_key_migrations(tasks_dir: Path) -> set[tuple[str, ...]]:
    """扫描 tasks/**/config.py 中 before validator 的 literal key 迁移。

    返回 (task_key, class_key, literal_key) 三元组集合。
    """
    found: set[tuple[str, ...]] = set()
    for file in sorted(tasks_dir.rglob("config.py")):
        task_key = convert_to_underscore(file.parent.name)
        try:
            tree = ast.parse(file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        direct, modules = _model_validator_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for fn in node.body:
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not _is_before_validator(fn, direct, modules):
                    continue
                class_key = convert_to_underscore(node.name)
                for literal in _migration_source_keys(fn):
                    found.add((task_key, class_key, literal))
    return found


def test_every_before_validator_key_migration_is_registered():
    discovered = discover_literal_key_migrations(Path("tasks"))
    registered = set(legacy_source_paths())
    assert discovered == {
        ("master_disciple", "master_disciple_config", "master_battle_mode")
    }
    assert discovered <= registered


def test_scanner_detects_alias_variable_and_assignment_migrations(tmp_path):
    task_dir = tmp_path / "AliasTask"
    task_dir.mkdir()
    (task_dir / "config.py").write_text(
        """
from pydantic import model_validator as mv

class AliasConfig:
    @mv(mode='before')
    @classmethod
    def migrate(cls, data):
        old_key = 'old_by_variable'
        data.pop(old_key, None)
        data['new_name'] = data['old_by_assignment']
        data['new_from_get'] = data.get('old_by_get')
        del data['old_by_delete']
        return data
""",
        encoding="utf-8",
    )
    assert discover_literal_key_migrations(tmp_path) == {
        ("alias_task", "alias_config", "old_by_variable"),
        ("alias_task", "alias_config", "old_by_assignment"),
        ("alias_task", "alias_config", "old_by_get"),
        ("alias_task", "alias_config", "old_by_delete"),
    }


def test_scanner_ignores_nested_function_input_rebinding(tmp_path):
    task_dir = tmp_path / "NestedScopeTask"
    task_dir.mkdir()
    (task_dir / "config.py").write_text(
        """
from pydantic import model_validator

class NestedScopeConfig:
    @model_validator(mode='before')
    @classmethod
    def migrate(cls, data):
        def helper():
            data = {'local': 1}
            data.pop('not_real', None)
        data.pop('real_migration', None)
        helper()
        return data
""",
        encoding="utf-8",
    )
    # 嵌套 helper 对 data 的重绑定只作用于内层作用域，不能漏掉外层真实迁移。
    assert discover_literal_key_migrations(tmp_path) == {
        ("nested_scope_task", "nested_scope_config", "real_migration"),
    }


def test_scanner_uses_outer_literal_binding_not_nested(tmp_path):
    task_dir = tmp_path / "NestedKeyTask"
    task_dir.mkdir()
    (task_dir / "config.py").write_text(
        """
from pydantic import model_validator

class NestedKeyConfig:
    @model_validator(mode='before')
    @classmethod
    def migrate(cls, data):
        key = 'outer_key'
        def helper():
            key = 'inner_key'
            cache = {}
            cache.pop(key)
        data.pop(key)
        return data
""",
        encoding="utf-8",
    )
    # 外层 data.pop(key) 必须解析外层绑定，不能被嵌套函数同名重绑定污染。
    assert discover_literal_key_migrations(tmp_path) == {
        ("nested_key_task", "nested_key_config", "outer_key"),
    }


def test_scanner_tracks_literal_key_rebinding_by_use_order(tmp_path):
    task_dir = tmp_path / "LiteralOrderTask"
    task_dir.mkdir()
    (task_dir / "config.py").write_text(
        """
from pydantic import model_validator

class LiteralOrderConfig:
    @model_validator(mode='before')
    @classmethod
    def migrate(cls, data):
        key = 'old_before'
        data.pop(key, None)
        key = 'old_after'
        data.pop(key, None)
        return data
""",
        encoding="utf-8",
    )
    assert discover_literal_key_migrations(tmp_path) == {
        ("literal_order_task", "literal_order_config", "old_before"),
        ("literal_order_task", "literal_order_config", "old_after"),
    }


def test_scanner_tracks_direct_and_transitive_input_aliases(tmp_path):
    task_dir = tmp_path / "AliasChainTask"
    task_dir.mkdir()
    (task_dir / "config.py").write_text(
        """
from pydantic import model_validator

class AliasChainConfig:
    @model_validator(mode='before')
    @classmethod
    def migrate(cls, data):
        alias = data
        second_alias = alias
        second_alias.pop('old_direct', None)
        second_alias['new_name'] = second_alias['old_assignment']
        return second_alias
""",
        encoding="utf-8",
    )
    assert discover_literal_key_migrations(tmp_path) == {
        ("alias_chain_task", "alias_chain_config", "old_direct"),
        ("alias_chain_task", "alias_chain_config", "old_assignment"),
    }


def test_scanner_keeps_migration_before_alias_rebind(tmp_path):
    task_dir = tmp_path / "OrderedAliasTask"
    task_dir.mkdir()
    (task_dir / "config.py").write_text(
        """
from pydantic import model_validator

class OrderedAliasConfig:
    @model_validator(mode='before')
    @classmethod
    def migrate(cls, data):
        alias = data
        alias.pop('old_before_rebind', None)
        alias = {'cache_key': 1}
        alias.pop('not_a_migration', None)
        return data
""",
        encoding="utf-8",
    )
    assert discover_literal_key_migrations(tmp_path) == {
        ("ordered_alias_task", "ordered_alias_config", "old_before_rebind"),
    }


def test_scanner_ignores_alias_rebound_to_other_dict(tmp_path):
    task_dir = tmp_path / "ReboundTask"
    task_dir.mkdir()
    (task_dir / "config.py").write_text(
        """
from pydantic import model_validator

class ReboundConfig:
    @model_validator(mode='before')
    @classmethod
    def inspect(cls, data):
        alias = data
        alias = {'cache_key': 1}
        alias.pop('not_a_migration', None)
        return data
""",
        encoding="utf-8",
    )
    assert discover_literal_key_migrations(tmp_path) == set()


def test_scanner_tracks_nested_input_dict_aliases(tmp_path):
    task_dir = tmp_path / "NestedAliasTask"
    task_dir.mkdir()
    (task_dir / "config.py").write_text(
        """
from pydantic import model_validator

class NestedAliasConfig:
    @model_validator(mode='before')
    @classmethod
    def migrate(cls, data):
        section = data['group']
        nested = section.get('child')
        section.pop('old_section_key', None)
        nested.pop('old_nested_key', None)
        return data
""",
        encoding="utf-8",
    )
    assert discover_literal_key_migrations(tmp_path) == {
        ("nested_alias_task", "nested_alias_config", "old_section_key"),
        ("nested_alias_task", "nested_alias_config", "old_nested_key"),
    }


def test_scanner_ignores_plain_reads_and_other_dict_pop(tmp_path):
    task_dir = tmp_path / "ReadOnlyTask"
    task_dir.mkdir()
    (task_dir / "config.py").write_text(
        """
from pydantic import model_validator

class ReadOnlyConfig:
    @model_validator(mode='before')
    @classmethod
    def inspect(cls, data):
        current = data['ordinary_read']
        cache = {'cache_key': current}
        cache.pop('cache_key')
        return data
""",
        encoding="utf-8",
    )
    assert discover_literal_key_migrations(tmp_path) == set()
