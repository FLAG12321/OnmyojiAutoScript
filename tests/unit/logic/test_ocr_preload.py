# This Python file uses the following encoding: utf-8
"""OCR 后端预加载门禁。

锁住一个真实踩过的阻断性缺陷：Windows 上 pyzmq 自带的 libzmq/libsodium
与 onnxruntime 的依赖 DLL 冲突。先加载 pyzmq 之后，import onnxruntime 会报

    ImportError: DLL load failed while importing onnxruntime_pybind11_state:
    动态链接库(DLL)初始化例程失败。

冲突是单向的：onnxruntime 先加载则两者可以共存。而 script.py 顶部就
import zerorpc（依赖 pyzmq），OCR 模块在 import 链里排到第 19 位之后，
因此所有任务进程的本地 OCR 都会崩溃。

修复是在每个进程入口的最前面调用 preload_ocr_backend()。
这些测试保证那一行不会在后续重构中被挪动或删除。
"""
import ast
import pathlib
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parents[3]
# 会 import zerorpc/pyzmq 的进程入口，必须先预加载 OCR 后端
# 旧 PySide6 GUI(gui.py) 已移除，前端统一由 OASX 承担
ENTRY_POINTS = ['script.py', 'server.py']
# pyzmq 相关模块名：出现在预加载之前即为缺陷
ZMQ_MODULES = ('zerorpc', 'zmq')


def top_level_statements(path: pathlib.Path):
    return ast.parse((ROOT / path).read_text(encoding='utf-8')).body


def first_lineno(statements, predicate):
    """返回第一个满足条件的顶层语句行号，没有则返回 None。"""
    for node in statements:
        if predicate(node):
            return node.lineno
    return None


def imports_module(node, names) -> bool:
    if isinstance(node, ast.Import):
        return any(a.name.split('.')[0] in names for a in node.names)
    if isinstance(node, ast.ImportFrom) and node.module:
        return node.module.split('.')[0] in names
    return False


def is_preload_import(node) -> bool:
    return (isinstance(node, ast.ImportFrom)
            and node.module == 'module.ocr.preload')


def is_preload_call(node) -> bool:
    return (isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == 'preload_ocr_backend')


@pytest.mark.parametrize('entry', ENTRY_POINTS)
def test_entry_imports_preload(entry):
    """入口必须 import preload_ocr_backend。"""
    statements = top_level_statements(entry)
    assert first_lineno(statements, is_preload_import) is not None, \
        f'{entry} 未 import module.ocr.preload'


@pytest.mark.parametrize('entry', ENTRY_POINTS)
def test_entry_calls_preload(entry):
    """入口必须真的调用 preload_ocr_backend()，光 import 不生效。"""
    statements = top_level_statements(entry)
    assert first_lineno(statements, is_preload_call) is not None, \
        f'{entry} 未在模块顶层调用 preload_ocr_backend()'


@pytest.mark.parametrize('entry', ENTRY_POINTS)
def test_preload_runs_before_zmq_import(entry):
    """预加载调用必须早于任何 zerorpc / pyzmq 的顶层 import。"""
    statements = top_level_statements(entry)
    preload_line = first_lineno(statements, is_preload_call)
    zmq_line = first_lineno(statements, lambda n: imports_module(n, ZMQ_MODULES))

    assert preload_line is not None, f'{entry} 未调用 preload_ocr_backend()'
    if zmq_line is None:
        # 该入口没有直接 import zerorpc/zmq，间接引入也已被预加载覆盖
        return
    assert preload_line < zmq_line, (
        f'{entry} 在第 {zmq_line} 行 import 了 pyzmq，但预加载在第 '
        f'{preload_line} 行——顺序反了，OCR 后端会初始化失败'
    )


def test_preload_module_has_no_project_imports():
    """preload 模块本身不得 import 项目内模块。

    任何项目内 import 都可能间接把 zerorpc 先拉进来，让预加载失效。
    """
    statements = top_level_statements('module/ocr/preload.py')
    for node in statements:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith(('module', 'tasks')), \
                    f'preload.py 不得 import 项目内模块 {alias.name}'
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith(('module', 'tasks')), \
                f'preload.py 不得 import 项目内模块 {node.module}'


def test_preload_is_idempotent():
    """重复调用不得重复付出加载代价，也不得改变结果。"""
    from module.ocr import preload

    first = preload.preload_ocr_backend()
    second = preload.preload_ocr_backend()
    assert first is second


@pytest.mark.slow
def test_script_import_chain_can_run_ocr():
    """端到端门禁：import script 之后本地 OCR 必须仍可用。

    这是上面所有静态检查的真正目的，用独立子进程验证真实 import 链。
    """
    script = (
        'import script, sys, numpy as np\n'
        'assert "zmq" in sys.modules, "zmq should be loaded by script"\n'
        'from module.ocr.models import get_local_ocr_model\n'
        'm = get_local_ocr_model("ch")\n'
        'text, score = m.ocr_single_line(np.zeros((32, 64, 3), dtype=np.uint8))\n'
        'print("OCR_OK device=" + m.resolved_device)\n'
    )
    # 显式 utf-8：子进程输出含中文日志，Windows 默认 GBK 解码会抛
    # UnicodeDecodeError 让 stdout 变成 None，断言拿不到真实内容
    out = subprocess.run([sys.executable, '-c', script], cwd=str(ROOT),
                         capture_output=True, text=True,
                         encoding='utf-8', errors='replace', timeout=600)
    assert 'OCR_OK' in out.stdout, (
        f'import script 后 OCR 不可用。\n'
        f'stdout={out.stdout[-1500:]}\nstderr={out.stderr[-1500:]}'
    )
