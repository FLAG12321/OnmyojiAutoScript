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

