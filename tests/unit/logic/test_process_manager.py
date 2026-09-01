import subprocess
import sys

import pytest

from deploy.process import ProcessManager

pytestmark = pytest.mark.unit


def test_process_path_filter_respects_directory_boundary():
    # 中文注释：C:\OAS 不得匹配相邻的 C:\OAS-backup，避免跨安装误杀进程。
    assert ProcessManager._path_under(r'C:\OAS\toolkit\python.exe', r'C:\OAS')
    assert not ProcessManager._path_under(r'C:\OAS-backup\toolkit\python.exe', r'C:\OAS')


def test_process_path_filter_normalizes_slashes_and_case():
    # 中文注释：WMI 可能返回反斜杠，而配置路径可能使用正斜杠，比较必须统一。
    assert ProcessManager._path_under('c:/oas/toolkit/python.exe', r'C:\OAS')

def test_wait_process_exit_uses_tasklist_when_psutil_missing(monkeypatch):
    # 首次安装缺少 psutil 时必须退回系统 tasklist，不能在 pip 自修复前崩溃。
    import builtins

    real_import = builtins.__import__
    calls = []

    def fake_import(name, *args, **kwargs):
        if name == 'psutil':
            raise ModuleNotFoundError("No module named 'psutil'")
        return real_import(name, *args, **kwargs)

    result = type('Result', (), {
        'returncode': 0,
        'stdout': 'INFO: No tasks are running which match the specified criteria.',
    })()
    monkeypatch.setattr(builtins, '__import__', fake_import)
    monkeypatch.setattr('deploy.process.subprocess.run',
                        lambda *args, **kwargs: calls.append(args) or result)

    assert ProcessManager._wait_process_exit(12345, timeout=0.1) is True
    assert calls[0][0][0] == 'tasklist'


def test_process_module_import_does_not_require_psutil():
    """installer 的 bootstrap 阶段即使没有 psutil，也必须能导入 ProcessManager。"""
    snippet = 'import builtins\nreal_import = builtins.__import__\ndef blocked(name, *args, **kwargs):\n    if name == \'psutil\':\n        raise ModuleNotFoundError("No module named \'psutil\'")\n    return real_import(name, *args, **kwargs)\nbuiltins.__import__ = blocked\nimport deploy.process\nprint(\'OK\')\n'
    result = subprocess.run(
        [sys.executable, '-c', snippet],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert 'OK' in result.stdout


def test_kill_by_name_treats_tree_killed_process_as_success(monkeypatch):
    """树杀连带场景：taskkill 对已死 PID 报错但进程确已退出时应判成功。

    枚举列表里父进程排在子进程之前时，对父进程的 `taskkill /f /t` 树杀会
    连带终止子进程，随后对已死子进程 PID 的 taskkill 报「找不到进程」。
    若按 taskkill 退出码判失败，process_kill 会返回 False 并触发
    ExecutionError('无法确认 OAS 进程已退出') 中止安装——正常清理被误判。
    """
    manager = ProcessManager.__new__(ProcessManager)  # 跳过 DeployConfig 读盘

    def make_rows(*pids):
        return [(r'C:/OAS/toolkit/pythonw.exe', 'pythonw.exe', pid) for pid in pids]

    # 场景 1：taskkill 全部报错，但进程实际都已退出（被树杀连带终止）→ 成功
    monkeypatch.setattr(ProcessManager, 'iter_process_by_name',
                        lambda self, name: iter(make_rows(111, 222)))
    monkeypatch.setattr(ProcessManager, 'execute',
                        lambda self, cmd, **kwargs: False)
    monkeypatch.setattr(ProcessManager, '_wait_process_exit',
                        lambda self, pid, timeout=5.0: True)
    assert manager.kill_by_name('pythonw.exe') is True

    # 场景 2：taskkill 报错且进程确实没死（如跨提权杀不掉）→ 仍判失败
    monkeypatch.setattr(ProcessManager, 'iter_process_by_name',
                        lambda self, name: iter(make_rows(111)))
    monkeypatch.setattr(ProcessManager, '_wait_process_exit',
                        lambda self, pid, timeout=5.0: False)
    assert manager.kill_by_name('pythonw.exe') is False

