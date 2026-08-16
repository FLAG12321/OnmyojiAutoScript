# This Python file uses the following encoding: utf-8
"""OCR RPC 服务进程结构门禁。

锁住一个真实踩过的坑：zerorpc 依赖 gevent，gevent 替换线程与 TLS 原语后
再 import onnxruntime 会失败：

    ImportError: DLL load failed while importing onnxruntime_pybind11_state:
    动态链接库(DLL)初始化例程失败。

实测导入顺序是唯一变量（先 ORT 后 zerorpc 正常，反之必然失败）。
而 Windows 的 multiprocessing 用 spawn，子进程会 re-import 目标函数所在模块，
module/ocr/rpc.py 顶层就 import zerorpc，因此绝不能用 multiprocessing 起服务。

这些测试保证服务进程始终通过独立入口 module/ocr/server_boot.py 启动，
且该入口里 onnxruntime 先于任何 gevent/zerorpc 加载。
"""
import ast
import pathlib

import pytest

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parents[3]
RPC_PATH = ROOT / 'module/ocr/rpc.py'
BOOT_PATH = ROOT / 'module/ocr/server_boot.py'


def imported_modules(path: pathlib.Path) -> set:
    """所有 import 的模块名（含函数体内的延迟 import）。"""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def top_level_modules(path: pathlib.Path) -> set:
    """仅模块顶层 import 的模块名。

    顶层 import 在模块被加载时立即执行；函数体内的延迟 import 不算，
    区分这两者是本文件的核心——修复正是把 rpc 相关 import 挪进函数体。
    """
    tree = ast.parse(path.read_text(encoding='utf-8'))
    modules = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_server_boot_entry_exists():
    """独立服务入口必须存在。"""
    assert BOOT_PATH.is_file(), 'module/ocr/server_boot.py 缺失，RPC 服务将无法正确启动'


def test_rpc_does_not_use_multiprocessing():
    """rpc.py 不得再用 multiprocessing 起服务。

    spawn 会 re-import rpc.py（顶层 import zerorpc），
    导致服务进程内 onnxruntime 的 DLL 初始化失败。
    """
    assert 'multiprocessing' not in imported_modules(RPC_PATH), \
        'rpc.py 不得 import multiprocessing，必须用 subprocess 启独立入口'


def test_rpc_spawns_server_boot_module():
    """服务必须通过 python -m module.ocr.server_boot 启动。"""
    source = RPC_PATH.read_text(encoding='utf-8')
    assert 'module.ocr.server_boot' in source, \
        'rpc.py 未通过 module.ocr.server_boot 启动服务进程'
    assert 'subprocess.Popen' in source


def test_server_boot_does_not_import_zerorpc_at_module_level():
    """入口模块顶层不得 import zerorpc / gevent / rpc 模块。

    顶层 import 在 preload_backend() 之前就已执行，等于没修。
    这些 import 必须留在函数体内。
    """
    modules = top_level_modules(BOOT_PATH)
    for forbidden in ('zerorpc', 'gevent', 'module.ocr.rpc', 'module.logger'):
        assert forbidden not in modules, \
            f'server_boot.py 顶层不得 import {forbidden}（必须晚于 onnxruntime）'


def test_server_boot_does_not_import_onnxruntime_at_module_level():
    """连 onnxruntime 也不在顶层：加载时机由 preload_backend() 显式控制，
    这样 --help 之类的调用不必付出加载 ORT 的代价。"""
    assert 'onnxruntime' not in top_level_modules(BOOT_PATH)


def test_server_boot_loads_onnxruntime_before_rpc():
    """在函数体内，onnxruntime 必须先于 rpc 相关 import 出现。"""
    source = BOOT_PATH.read_text(encoding='utf-8')
    tree = ast.parse(source)

    ort_line = None
    rpc_line = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == 'onnxruntime' and ort_line is None:
                    ort_line = node.lineno
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith('module.ocr.rpc') and rpc_line is None:
                rpc_line = node.lineno

    assert ort_line is not None, 'server_boot.py 未 import onnxruntime'
    assert rpc_line is not None, 'server_boot.py 未 import module.ocr.rpc'
    assert ort_line < rpc_line, \
        'server_boot.py 必须先 import onnxruntime 再 import module.ocr.rpc'


def test_serve_forever_is_exposed():
    """server_boot 调用的 serve_forever 必须存在于 rpc.py。"""
    tree = ast.parse(RPC_PATH.read_text(encoding='utf-8'))
    functions = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert 'serve_forever' in functions


def test_model_proxy_has_timeout():
    """ModelProxy 必须设超时，否则服务异常会让任务线程永久阻塞。"""
    from module.ocr.rpc import ModelProxy

    assert isinstance(ModelProxy.TIMEOUT, int)
    assert ModelProxy.TIMEOUT > 0
    source = RPC_PATH.read_text(encoding='utf-8')
    assert 'zerorpc.Client(timeout=' in source, 'zerorpc.Client 未传 timeout'


def test_model_proxy_retries_connect():
    from module.ocr.rpc import ModelProxy

    assert ModelProxy.CONNECT_RETRY >= 2, '服务刚拉起时模型仍在加载，握手需要重试'


def test_rpc_server_returns_serializable_box():
    """检测框必须转成 list：msgpack 无法序列化 ndarray。"""
    import numpy as np

    from module.ocr.rpc import _box_to_list

    assert _box_to_list(np.array([[1, 2], [3, 4]])) == [[1, 2], [3, 4]]
    assert _box_to_list([[1, 2]]) == [[1, 2]]
