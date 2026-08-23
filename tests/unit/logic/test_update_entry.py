# This Python file uses the following encoding: utf-8
"""独立更新器入口（deploy/update.py）门禁。

锁住这个入口存在的唯一理由：它必须是一个**不持有 onnxruntime DLL** 的进程。

Windows 锁定已加载的 onnxruntime_providers_shared.dll，任何进程都无法删除或替换
它；而 server.py / gui.py / script.py 入口都会 preload_ocr_backend()，Python 又
没有卸载已加载扩展 DLL 的手段。所以 web 更新器（跑在 server 进程内）发起的 OCR
换包必然 WinError 5 拒绝访问，杀掉 OCR RPC 子进程也救不回来。

一旦有人在这条 import 链上引入 onnxruntime 或 zerorpc(pyzmq)，独立更新器就退化成
和 web 更新器一样换不了包，而且失败方式同样隐蔽。下面的测试就是防这个。
"""
import ast
import pathlib
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parents[3]
UPDATE_PATH = ROOT / 'deploy/update.py'
BAT_PATH = ROOT / 'oas-update.bat'


def test_update_entry_exists():
    assert UPDATE_PATH.is_file(), 'deploy/update.py 缺失，独立更新器入口不可用'


def test_update_entry_has_main_guard():
    """必须可被 python -m deploy.update 调用。"""
    source = UPDATE_PATH.read_text(encoding='utf-8')
    assert "__name__ == '__main__'" in source
    assert 'sys.exit(main())' in source


def test_update_entry_does_not_import_ocr_backend():
    """静态检查：整个模块（含函数体内延迟 import）不得碰 onnxruntime / zerorpc。"""
    tree = ast.parse(UPDATE_PATH.read_text(encoding='utf-8'))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    for forbidden in ('onnxruntime', 'zerorpc', 'zmq', 'module.ocr.preload',
                      'module.ocr.rpc', 'module.ocr.models'):
        assert forbidden not in modules, (
            f'deploy/update.py 不得 import {forbidden}：'
            f'该进程一旦持有 onnxruntime DLL 就换不了 ORT 包，入口失去意义'
        )


@pytest.mark.slow
def test_update_entry_process_holds_no_ort_dll():
    """端到端门禁：真实 import 后 sys.modules 里不得出现 onnxruntime / zmq。

    静态检查看不到间接 import（比如 module.server.updater 哪天引入了 OCR 依赖），
    这条用真实子进程兜住整条 import 链。
    """
    script = (
        'import sys\n'
        'import deploy.update\n'
        'print("ORT=" + str("onnxruntime" in sys.modules))\n'
        'print("ZMQ=" + str("zmq" in sys.modules))\n'
    )
    # 显式 utf-8：deploy.logger 输出含中文，Windows 默认 GBK 解码会抛
    # UnicodeDecodeError 让 stdout 变成 None，掩盖真正要断言的内容
    out = subprocess.run([sys.executable, '-c', script], cwd=str(ROOT),
                         capture_output=True, text=True,
                         encoding='utf-8', errors='replace', timeout=300)
    assert 'ORT=False' in out.stdout, (
        f'deploy.update 的 import 链把 onnxruntime 拉进来了，换包会被 DLL 锁挡住。\n'
        f'stdout={out.stdout[-1500:]}\nstderr={out.stderr[-1500:]}'
    )
    assert 'ZMQ=False' in out.stdout, (
        f'deploy.update 的 import 链把 pyzmq 拉进来了。\n'
        f'stdout={out.stdout[-1500:]}\nstderr={out.stderr[-1500:]}'
    )


def test_update_entry_stops_processes_before_pull():
    """必须先停 OAS 进程再拉取：外部进程持有的 DLL 也要释放。

    按 AST 比对两个调用的行号，而不是文本查找——
    模块 docstring 里也提到 execute_pull，文本查找会命中说明文字。
    两个调用都在 run_update() 里（main() 只做子命令分发）。
    """
    tree = ast.parse(UPDATE_PATH.read_text(encoding='utf-8'))
    target = next((n for n in tree.body
                   if isinstance(n, ast.FunctionDef) and n.name == 'run_update'), None)
    assert target is not None, 'deploy/update.py 缺少 run_update()'

    stop_line = pull_line = None
    for node in ast.walk(target):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == 'stop_oas_processes':
            stop_line = node.lineno if stop_line is None else stop_line
        elif isinstance(func, ast.Attribute) and func.attr in ('execute_pull', '_execute_pull_locked'):
            pull_line = node.lineno if pull_line is None else pull_line

    assert stop_line is not None, 'run_update() 未调用 stop_oas_processes'
    assert pull_line is not None, 'run_update() 未调用 execute_pull'
    assert stop_line < pull_line, '停止 OAS 进程必须早于 execute_pull'


def test_bat_entry_exists_and_calls_module():
    """双击入口必须存在，且通过 -m deploy.update 调用（而不是复制流程）。"""
    assert BAT_PATH.is_file(), 'oas-update.bat 缺失，用户没有双击入口'
    source = BAT_PATH.read_text(encoding='utf-8')
    assert 'deploy.update' in source
    # 失败时必须 pause，否则窗口一闪而过，用户看不到中断原因
    assert 'pause' in source


def test_update_entry_has_info_and_set_config_subcommands():
    """--info / --set-config 必须存在：OASX 更新器页靠它们在 server 未启动时工作。"""
    source = UPDATE_PATH.read_text(encoding='utf-8')
    assert "'--info'" in source, '缺少 --info 子命令，OASX 无法读取分支信息'
    assert "'--set-config'" in source, '缺少 --set-config 子命令，OASX 无法写 deploy.yaml'
    tree = ast.parse(source)
    functions = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    for name in ('run_info', 'run_set_config', 'run_update'):
        assert name in functions, f'deploy/update.py 缺少 {name}()'


def test_info_fields_match_home_router_update_info():
    """--info 的字段必须与 /home/update_info 完全一致。

    OASX 侧直接把 --info 的 JSON 喂给 UpdateInfoModel.fromJson（原本解析 HTTP
    响应的同一个模型）。字段一旦漂移，前端不会报错，只会静默把 commit 表格
    渲染成占位符 '—'，属于最难发现的那类退化，所以在这里锁死。
    """
    router_src = (ROOT / 'module/server/home_router.py').read_text(encoding='utf-8')
    update_src = UPDATE_PATH.read_text(encoding='utf-8')

    def dict_keys_after(source, marker):
        """取 marker 之后第一个字典字面量的字符串键集合。"""
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict) and node.keys:
                keys = {k.value for k in node.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                if marker in keys:
                    return keys
        return set()

    # 两侧都以 is_update 作为锚点定位那个结果字典
    router_keys = dict_keys_after(router_src, 'is_update')
    update_keys = dict_keys_after(update_src, 'is_update')

    assert router_keys, '未能在 home_router.py 定位 update_info 的结果字典'
    assert update_keys, '未能在 deploy/update.py 定位 --info 的结果字典'
    assert router_keys == update_keys, (
        f'--info 与 /home/update_info 字段不一致\n'
        f'仅 router 有：{router_keys - update_keys}\n'
        f'仅 update 有：{update_keys - router_keys}'
    )


def test_set_config_validates_repository_scheme():
    """--set-config 必须复用统一的 repository / branch 校验。"""
    source = UPDATE_PATH.read_text(encoding='utf-8')
    assert 'validate_repository' in source
    assert 'validate_branch' in source


def test_json_prefix_is_stable():
    """JSON 结果行前缀是与 OASX 的硬约定，改动必须两侧同步。"""
    from deploy.update import JSON_PREFIX

    assert JSON_PREFIX == 'OAS_JSON:', (
        '前缀变更必须同步 OASX 的 UpdaterLauncher.jsonPrefix，'
        '否则前端解析不到结果行'
    )
