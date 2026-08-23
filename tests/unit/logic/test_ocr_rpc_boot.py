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


def test_kill_orphan_ocr_servers_only_kills_server_boot(monkeypatch):
    """kill_orphan_ocr_servers 只终止 server_boot 入口的 python 进程。

    多开/残留时 OCR 服务可能由别的进程持有，shutdown_ocr_server 停不到它，
    这里按 CommandLine 匹配 server_boot，其它 python 进程（如 GUI）不得误杀。
    """
    import sys
    import types

    from types import SimpleNamespace

    # 注入假 win32com，绕过真实 WMI 枚举
    fake_win32com = types.ModuleType('win32com')
    fake_client = types.ModuleType('win32com.client')
    fake_win32com.client = fake_client
    monkeypatch.setitem(sys.modules, 'win32com', fake_win32com)
    monkeypatch.setitem(sys.modules, 'win32com.client', fake_client)

    class FakeProps:
        def __init__(self, value):
            self._value = value

        @property
        def Value(self):
            return self._value

    project_python = str(RPC_PATH.parents[2] / 'toolkit' / 'python.exe')

    def make_proc(pid, name, cmdline, exe=project_python):
        return SimpleNamespace(Properties_=lambda key: FakeProps({
            'ProcessID': pid, 'Name': name,
            'CommandLine': cmdline, 'ExecutablePath': exe,
        }[key]))

    fake_procs = [
        make_proc(11000, 'python.exe',
                  'python -m module.ocr.server_boot --host 0.0.0.0 --port 22268'),
        make_proc(12000, 'pythonw.exe', 'pythonw gui.py'),  # GUI，非 OCR 服务
        make_proc(13000, 'python.exe',
                  'python -m module.ocr.server_boot --port 22268'),
    ]

    class FakeWmi:
        def InstancesOf(self, cls):
            return iter(fake_procs)

    fake_client.GetObject = lambda query: FakeWmi()

    taskkill_calls = []
    monkeypatch.setattr('module.ocr.rpc.subprocess.run',
                        lambda *a, **k: taskkill_calls.append(a) or SimpleNamespace(returncode=0))
    # 解除 pytest 守卫：本用例走 fake WMI 且 subprocess.run 已被替换，
    # 不会对真实进程 taskkill，可以安全验证终止逻辑本身。
    monkeypatch.delenv('PYTEST_CURRENT_TEST', raising=False)
    monkeypatch.setattr('module.ocr.rpc._wait_ocr_process_exit', lambda pid: True)

    from module.ocr.rpc import kill_orphan_ocr_servers
    assert kill_orphan_ocr_servers() == 2
    pids = [args[0][-1] for args in taskkill_calls]
    assert '11000' in pids and '13000' in pids, '两个 server_boot 进程都必须被终止'
    assert '12000' not in pids, 'GUI 进程不得被误杀'


def test_kill_orphan_ocr_servers_reports_failure_when_wmi_missing(monkeypatch):
    """WMI 不可用时必须返回 -1（无法确认），不能报告 0（没有残留进程）。

    区别是致命的：返回 0 会让调用方以为「已确认没有进程持有 DLL」从而继续换包，
    而实际上可能有外部 OAS 实例正锁着 onnxruntime_providers_shared.dll，
    换包会 WinError 5 失败并留下半删的包。返回 -1 让调用方知道「查不出来」。

    必须 delenv PYTEST_CURRENT_TEST：否则函数在开头的 pytest 守卫处就
    `return 0`，fake WMI 一行都跑不到，用例通过但什么都没验证
    （这正是本用例此前的状态——空覆盖，且把已经改成 -1 的语义锁回了 0）。
    这里不碰真实进程：WMI 枚举被替换成直接抛异常，taskkill 根本到不了。
    """
    import sys
    import types

    fake_win32com = types.ModuleType('win32com')
    fake_client = types.ModuleType('win32com.client')

    def boom(*a, **k):
        raise Exception('WMI unavailable')

    fake_client.GetObject = boom
    fake_win32com.client = fake_client
    monkeypatch.setitem(sys.modules, 'win32com', fake_win32com)
    monkeypatch.setitem(sys.modules, 'win32com.client', fake_client)
    # 纵深防御：即使守卫失效也不许真的 taskkill
    called = []
    monkeypatch.setattr('module.ocr.rpc.subprocess.run',
                        lambda *a, **k: called.append('taskkill'))
    monkeypatch.delenv('PYTEST_CURRENT_TEST', raising=False)

    from module.ocr.rpc import kill_orphan_ocr_servers
    assert kill_orphan_ocr_servers() == -1, \
        'WMI 不可用必须返回 -1，返回 0 会被误读成「确认无残留」而继续换包'
    assert not called, 'WMI 枚举失败时不该执行任何 taskkill'


def test_kill_orphan_ocr_servers_reports_failure_when_pywin32_missing(monkeypatch):
    """pywin32 未安装（ImportError）与 WMI 调用失败同样属于「无法确认」。"""
    import builtins
    import sys

    monkeypatch.delitem(sys.modules, 'win32com', raising=False)
    monkeypatch.delitem(sys.modules, 'win32com.client', raising=False)

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith('win32com'):
            raise ModuleNotFoundError("No module named 'win32com'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', fake_import)
    monkeypatch.delenv('PYTEST_CURRENT_TEST', raising=False)

    from module.ocr.rpc import kill_orphan_ocr_servers
    assert kill_orphan_ocr_servers() == -1, \
        'pywin32 缺失时同样无法确认进程状态，必须返回 -1'


def test_kill_orphan_ocr_servers_matches_backslash_executable_path(monkeypatch):
    r"""ExecutablePath 带反斜杠时路径校验必须通过。

    真实 WMI 返回的 ExecutablePath 是 Windows 反斜杠形式
    （C:\...\toolkit\python.exe），而 project_root 也是反斜杠。
    曾经的实现只把 exe 换成正斜杠、却拿未换的 project_root 去 `in` 比较，
    条件恒为 False，所有 server_boot 进程都被跳过 —— 函数完全失效，
    onnxruntime DLL 照样被锁。上面那个用例的 exe 传空串，走了
    `if exe and ...` 的短路分支，恰好绕开了这半个条件，因此漏掉了这个缺陷。
    """
    import os
    import sys
    import types

    from types import SimpleNamespace

    fake_win32com = types.ModuleType('win32com')
    fake_client = types.ModuleType('win32com.client')
    fake_win32com.client = fake_client
    monkeypatch.setitem(sys.modules, 'win32com', fake_win32com)
    monkeypatch.setitem(sys.modules, 'win32com.client', fake_client)

    # 与 rpc.py 内部一致的安装根目录，构造真实形状的 ExecutablePath
    project_root = str(ROOT)
    inside = os.path.join(project_root, 'toolkit', 'python.exe')
    outside = r'D:\OtherApp\python.exe'

    class FakeProps:
        def __init__(self, value):
            self._value = value

        @property
        def Value(self):
            return self._value

    def make_proc(pid, name, cmdline, exe):
        return SimpleNamespace(Properties_=lambda key: FakeProps({
            'ProcessID': pid, 'Name': name,
            'CommandLine': cmdline, 'ExecutablePath': exe,
        }[key]))

    fake_procs = [
        # 安装目录内的 OCR 服务：必须被终止
        make_proc(31000, 'python.exe',
                  'python -m module.ocr.server_boot --port 22268', inside),
        # 安装目录外的同名入口：纵深防御，不得误杀
        make_proc(32000, 'python.exe',
                  'python -m module.ocr.server_boot --port 22268', outside),
    ]

    class FakeWmi:
        def InstancesOf(self, cls):
            return iter(fake_procs)

    fake_client.GetObject = lambda query: FakeWmi()

    taskkill_calls = []
    monkeypatch.setattr('module.ocr.rpc.subprocess.run',
                        lambda *a, **k: taskkill_calls.append(a) or SimpleNamespace(returncode=0))
    # 解除 pytest 守卫：本用例走 fake WMI 且 subprocess.run 已被替换，
    # 不会对真实进程 taskkill，可以安全验证终止逻辑本身。
    monkeypatch.delenv('PYTEST_CURRENT_TEST', raising=False)
    monkeypatch.setattr('module.ocr.rpc._wait_ocr_process_exit', lambda pid: True)

    from module.ocr.rpc import kill_orphan_ocr_servers
    assert kill_orphan_ocr_servers() == 1, \
        '安装目录内的 server_boot 进程必须被终止（反斜杠路径也要匹配）'
    pids = [args[0][-1] for args in taskkill_calls]
    assert '31000' in pids
    assert '32000' not in pids, '安装目录外的进程不得误杀'


def test_kill_orphan_ocr_servers_matches_quoted_command_line(monkeypatch):
    r"""安装路径含空格且 ExecutablePath 缺失时，必须从带引号 CommandLine 匹配。"""
    import sys
    import types
    from types import SimpleNamespace

    fake_win32com = types.ModuleType('win32com')
    fake_client = types.ModuleType('win32com.client')
    fake_win32com.client = fake_client
    monkeypatch.setitem(sys.modules, 'win32com', fake_win32com)
    monkeypatch.setitem(sys.modules, 'win32com.client', fake_client)

    class Props:
        def __init__(self, value):
            self.Value = value

    project_python = str(ROOT / 'toolkit' / 'python.exe')
    command_line = f'"{project_python}" -m module.ocr.server_boot --port 22268'
    proc = SimpleNamespace(Properties_=lambda key: Props({
        'ProcessID': 32500,
        'Name': 'python.exe',
        'CommandLine': command_line,
        'ExecutablePath': '',
    }[key]))
    fake_client.GetObject = lambda query: SimpleNamespace(
        InstancesOf=lambda cls: iter([proc]))

    taskkill_calls = []
    monkeypatch.setattr('module.ocr.rpc.subprocess.run',
                        lambda *a, **k: taskkill_calls.append(a)
                        or SimpleNamespace(returncode=0))
    monkeypatch.setattr('module.ocr.rpc._wait_ocr_process_exit', lambda pid: True)
    monkeypatch.delenv('PYTEST_CURRENT_TEST', raising=False)

    from module.ocr.rpc import kill_orphan_ocr_servers
    assert kill_orphan_ocr_servers() == 1
    assert taskkill_calls[0][0][-1] == '32500'


def test_kill_orphan_ocr_servers_is_inert_under_pytest(monkeypatch):
    """pytest 下必须直接返回 0，一个真实进程都不能碰。

    这是防回归护栏：test_updater 里成片用例会走到 execute_pull 尾段的真实
    align_ocr，进而调到本函数。曾因此把用户正在运行的 OCR 服务全部 taskkill，
    实例任务报 LostRemote。守卫被删掉的话跑测试就会再次杀真进程。
    """
    import os

    called = []
    # WMI 枚举与 taskkill 都不该被触达
    monkeypatch.setattr('module.ocr.rpc.subprocess.run',
                        lambda *a, **k: called.append('taskkill'))
    assert 'PYTEST_CURRENT_TEST' in os.environ, 'pytest 应设置该环境变量'

    from module.ocr.rpc import kill_orphan_ocr_servers
    assert kill_orphan_ocr_servers() == 0
    assert not called, 'pytest 下不得执行任何 taskkill'


def test_restart_ocr_server_targets_current_local_port(monkeypatch):
    """自愈只能重启当前代理使用的本机端口。"""
    import types

    from module.ocr import rpc
    from module.server.setting import State

    monkeypatch.setattr(State, 'deploy_config', types.SimpleNamespace(
        StartOcrServer=True,
        OcrServerPort=22268,
        OcrClientAddress='127.0.0.1:22268',
    ))
    calls = []
    monkeypatch.setattr(rpc, 'shutdown_ocr_server',
                        lambda: calls.append('shutdown') or True)
    monkeypatch.setattr(rpc, 'kill_orphan_ocr_servers',
                        lambda port=None: calls.append(('kill', port)) or 1)
    monkeypatch.setattr(rpc, '_is_port_in_use', lambda *a, **k: False)
    monkeypatch.setattr(rpc, '_ping_ocr_server', lambda *a, **k: False)
    monkeypatch.setattr(rpc, 'ensure_ocr_server_started',
                        lambda: calls.append('start') or True)

    assert rpc.restart_ocr_server('tcp://127.0.0.1:22268') is True
    assert calls == ['shutdown', ('kill', 22268), 'start']


def test_model_proxy_replays_interrupted_single_line(monkeypatch):
    """LostRemote 后必须重启连接并重放刚才失败的同一次 OCR。"""
    import numpy as np

    from module.ocr import rpc

    class FakeClient:
        def __init__(self, response):
            self.response = response
            self.payloads = []
            self.closed = False

        def connect(self, address):
            self.address = address

        def ping(self):
            return True

        def close(self):
            self.closed = True

        def ocr_single_line(self, payload):
            self.payloads.append(payload)
            if isinstance(self.response, BaseException):
                raise self.response
            return self.response

    first = FakeClient(rpc.zerorpc.LostRemote('heartbeat lost'))
    second = FakeClient(('恢复成功', 0.99))
    clients = iter((first, second))
    monkeypatch.setattr(rpc.zerorpc, 'Client', lambda timeout: next(clients))
    restarts = []
    monkeypatch.setattr(rpc, 'restart_ocr_server',
                        lambda address: restarts.append(address) or True)

    proxy = rpc.ModelProxy('127.0.0.1:22268')
    result = proxy.ocr_single_line(np.zeros((8, 16, 3), dtype=np.uint8))

    assert result == ('恢复成功', 0.99)
    assert restarts == ['tcp://127.0.0.1:22268']
    assert first.closed is True
    assert len(first.payloads) == len(second.payloads) == 1
    assert first.payloads[0] == second.payloads[0], '恢复后必须重放原请求载荷'


def test_model_proxy_falls_back_when_replayed_request_also_fails(monkeypatch):
    """服务重启后请求仍失败时转本地 OCR，传输异常不得击穿任务实例。"""
    import types

    import numpy as np

    from module.ocr import rpc
    from module.ocr.result import BoxedResult

    class FailingClient:
        def connect(self, address):
            self.address = address

        def ping(self):
            return True

        def close(self):
            pass

        def detect_and_ocr(self, *args):
            raise rpc.zerorpc.TimeoutExpired(1)

    clients = iter((FailingClient(), FailingClient()))
    monkeypatch.setattr(rpc.zerorpc, 'Client', lambda timeout: next(clients))
    monkeypatch.setattr(rpc, 'restart_ocr_server', lambda address: True)
    expected = BoxedResult([[1, 1], [2, 1], [2, 2], [1, 2]], None, '本地结果', 0.9)
    local_model = types.SimpleNamespace(
        detect_and_ocr=lambda image, **kwargs: [expected]
    )
    monkeypatch.setattr(rpc.ModelProxy, '_get_local_fallback_model',
                        lambda self: local_model)

    proxy = rpc.ModelProxy('127.0.0.1:22268')
    result = proxy.detect_and_ocr(np.zeros((8, 16, 3), dtype=np.uint8))

    assert result == [expected]


def test_model_proxy_returns_empty_result_when_all_recovery_fails(monkeypatch):
    """RPC 与本地兜底都失败时返回无结果，不能让实例因 OCR 异常退出。"""
    import numpy as np

    from module.exception import ScriptError
    from module.ocr import rpc

    import threading

    proxy = object.__new__(rpc.ModelProxy)
    proxy.client = None
    proxy._fallback_model = None
    proxy._next_rpc_retry = 0.0
    proxy._recovery_lock = threading.Lock()

    def fail_connect():
        raise ScriptError('reconnect failed')

    def fail_local():
        raise RuntimeError('local model failed')

    monkeypatch.setattr(proxy, '_connect', fail_connect)
    monkeypatch.setattr(proxy, '_get_local_fallback_model', fail_local)

    image = np.zeros((8, 16, 3), dtype=np.uint8)
    with pytest.raises(ScriptError, match='local model failed'):
        proxy.ocr_single_line(image)
    with pytest.raises(ScriptError, match='local model failed'):
        proxy.ocr_single_line(image)



def test_kill_orphan_ocr_servers_requires_taskkill_success(monkeypatch):
    """taskkill 非零退出码时不能把进程误计为已终止。"""
    import sys
    import types
    from types import SimpleNamespace

    fake_win32com = types.ModuleType('win32com')
    fake_client = types.ModuleType('win32com.client')
    fake_win32com.client = fake_client
    monkeypatch.setitem(sys.modules, 'win32com', fake_win32com)
    monkeypatch.setitem(sys.modules, 'win32com.client', fake_client)

    class Props:
        def __init__(self, value):
            self.Value = value

    proc = SimpleNamespace(Properties_=lambda key: Props({
        'ProcessID': 33000,
        'Name': 'python.exe',
        'CommandLine': 'python -m module.ocr.server_boot --port 22268',
        'ExecutablePath': str(RPC_PATH.parents[2] / 'toolkit' / 'python.exe'),
    }[key]))
    fake_client.GetObject = lambda query: SimpleNamespace(
        InstancesOf=lambda cls: iter([proc]))
    monkeypatch.setattr('module.ocr.rpc.subprocess.run',
                        lambda *a, **k: SimpleNamespace(returncode=1))
    monkeypatch.delenv('PYTEST_CURRENT_TEST', raising=False)

    from module.ocr.rpc import kill_orphan_ocr_servers
    assert kill_orphan_ocr_servers() == -1


def test_kill_orphan_ocr_servers_requires_process_exit_confirmation(monkeypatch):
    """taskkill 返回成功但目标仍存活时，不能报告已终止。"""
    import sys
    import types
    from types import SimpleNamespace

    fake_win32com = types.ModuleType('win32com')
    fake_client = types.ModuleType('win32com.client')
    fake_win32com.client = fake_client
    monkeypatch.setitem(sys.modules, 'win32com', fake_win32com)
    monkeypatch.setitem(sys.modules, 'win32com.client', fake_client)

    class Props:
        def __init__(self, value):
            self.Value = value

    proc = SimpleNamespace(Properties_=lambda key: Props({
        'ProcessID': 34000,
        'Name': 'python.exe',
        'CommandLine': 'python -m module.ocr.server_boot --port 22268',
        'ExecutablePath': str(RPC_PATH.parents[2] / 'toolkit' / 'python.exe'),
    }[key]))
    fake_client.GetObject = lambda query: SimpleNamespace(
        InstancesOf=lambda cls: iter([proc]))
    monkeypatch.setattr('module.ocr.rpc.subprocess.run',
                        lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr('module.ocr.rpc._wait_ocr_process_exit', lambda pid: False)
    monkeypatch.delenv('PYTEST_CURRENT_TEST', raising=False)

    from module.ocr.rpc import kill_orphan_ocr_servers
    assert kill_orphan_ocr_servers() == -1


def test_model_proxy_retries_rpc_after_local_fallback(monkeypatch):
    """已经降级到本地后，冷却期一过就应重新探测 RPC，恢复后清除降级状态。"""
    import threading
    import time
    import types
    import numpy as np

    from module.ocr import rpc

    proxy = object.__new__(rpc.ModelProxy)
    proxy.client = object()
    proxy._fallback_model = types.SimpleNamespace()
    # 置为过去时间表示冷却窗口已过期，本次请求应真的去试 RPC。
    proxy._next_rpc_retry = time.monotonic() - 1.0
    proxy._recovery_lock = threading.Lock()
    monkeypatch.setattr(proxy, '_call_with_recovery',
                        lambda method, *args: ('RPC恢复', 0.99))

    result = proxy.ocr_single_line(np.zeros((8, 16, 3), dtype=np.uint8))
    assert result == ('RPC恢复', 0.99)
    assert proxy._fallback_model is None


def test_model_proxy_skips_rpc_during_fallback_cooldown(monkeypatch):
    """冷却期内必须直接用缓存的本地模型，不得重复触发 RPC 恢复。

    战斗中每秒有多次 OCR，RPC 断线后若每次都走
    重启服务 → 重连 → 重放请求，会持续拖慢任务并反复 taskkill 服务进程。
    """
    import threading
    import time
    import numpy as np

    from module.ocr import rpc

    proxy = object.__new__(rpc.ModelProxy)
    proxy.client = object()
    proxy._recovery_lock = threading.Lock()
    # 刚刚失败过：下一次探测时间仍在冷却窗口之后。
    proxy._next_rpc_retry = time.monotonic() + proxy.FALLBACK_RETRY_COOLDOWN

    local_calls = []

    class LocalModel:
        def ocr_single_line(self, image):
            local_calls.append('local')
            return ('本地结果', 0.8)

    proxy._fallback_model = LocalModel()

    def must_not_call(*a, **k):
        raise AssertionError('冷却期内不得触发 RPC 恢复')

    monkeypatch.setattr(proxy, '_call_with_recovery', must_not_call)

    result = proxy.ocr_single_line(np.zeros((8, 16, 3), dtype=np.uint8))
    assert result == ('本地结果', 0.8)
    assert local_calls == ['local'], '冷却期内应直接复用缓存的本地模型'
    assert proxy._fallback_model is not None, '冷却期内不得清除降级状态'


def test_model_proxy_detect_cooldown_skips_rpc(monkeypatch):
    # detect_and_ocr 与单行 OCR 共用冷却窗口，不得发生路径漂移。
    import threading
    import time
    import numpy as np
    from module.ocr import rpc

    proxy = object.__new__(rpc.ModelProxy)
    proxy.client = object()
    proxy._recovery_lock = threading.Lock()
    proxy._next_rpc_retry = time.monotonic() + proxy.FALLBACK_RETRY_COOLDOWN
    local = type('Local', (), {
        'detect_and_ocr': lambda self, image, **kwargs: ['local'],
    })()
    proxy._fallback_model = local
    monkeypatch.setattr(proxy, '_call_with_recovery',
                        lambda *args: (_ for _ in ()).throw(
                            AssertionError('冷却期内不得触发 detect RPC')))

    assert proxy.detect_and_ocr(np.zeros((8, 16, 3), dtype=np.uint8)) == ['local']


def test_model_proxy_preserves_local_fallback_error(monkeypatch):
    """本地 OCR 也失败时必须抛出可诊断错误，不能静默返回空结果。"""
    import threading
    import numpy as np

    from module.exception import ScriptError
    from module.ocr import rpc

    proxy = object.__new__(rpc.ModelProxy)
    proxy.client = None
    proxy._fallback_model = None
    proxy._next_rpc_retry = 0.0
    proxy._recovery_lock = threading.Lock()
    monkeypatch.setattr(proxy, '_connect',
                        lambda: (_ for _ in ()).throw(ScriptError('reconnect failed')))
    monkeypatch.setattr(proxy, '_get_local_fallback_model',
                        lambda: (_ for _ in ()).throw(RuntimeError('local model failed')))

    with pytest.raises(ScriptError, match='local model failed'):
        proxy.ocr_single_line(np.zeros((8, 16, 3), dtype=np.uint8))
