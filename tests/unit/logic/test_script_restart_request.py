import types

import pytest

import script as script_module


class FakeResponse:
    """伪 HTTP 响应：支持 with 语法，避免单元测试访问真实 server。"""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return b'{"restarting": true}'


class FakeScript(script_module.Script):
    """伪脚本：绕过真实初始化，只测试主动请求 server 重启的 URL。"""

    def __init__(self):
        self.config_name = 'oas'


def test_request_server_restart_from_instance_uses_env_port(monkeypatch):
    requested = []
    fake_script = FakeScript()
    monkeypatch.setenv('OAS_WEBUI_PORT', '33333')
    monkeypatch.setattr(script_module.urllib.request, 'urlopen', lambda url, timeout: requested.append((url, timeout)) or FakeResponse())

    fake_script._request_server_restart_from_instance()

    assert requested == [('http://127.0.0.1:33333/oas/restart_from_instance', 3)]


def test_request_server_restart_from_instance_uses_deploy_port_without_env(monkeypatch):
    requested = []
    fake_script = FakeScript()
    monkeypatch.delenv('OAS_WEBUI_PORT', raising=False)
    monkeypatch.setattr(script_module.State, 'deploy_config', types.SimpleNamespace(WebuiPort=22288))
    monkeypatch.setattr(script_module.urllib.request, 'urlopen', lambda url, timeout: requested.append((url, timeout)) or FakeResponse())

    fake_script._request_server_restart_from_instance()

    assert requested == [('http://127.0.0.1:22288/oas/restart_from_instance', 3)]


class FakeRecoveryFailureDevice:
    """伪设备：用 full_recovery 模拟失败路径中的真实模拟器 kill。"""

    def __init__(self, calls):
        self.calls = calls

    def full_recovery(self):
        # full_recovery 返回 False 前必须先完成模拟器清理。
        self.calls.append('kill_emulator')
        return False


class FakeRecoveryFailureScript(script_module.Script):
    """伪脚本：只运行 loop 中 full_recovery 失败后的重启链路。"""

    def __init__(self):
        self.config_name = 'QMUMU2'
        self.calls = []
        self._needs_recovery = True
        self.is_first_task = False
        self._device = FakeRecoveryFailureDevice(self.calls)

    @property
    def device(self):
        return self._device

    def get_next_task(self):
        return 'Orochi'

    def _request_server_restart_from_instance(self):
        self.calls.append('restart_from_instance')


def test_loop_full_recovery_failure_kills_emulator_then_requests_server_restart(monkeypatch):
    fake_script = FakeRecoveryFailureScript()
    monkeypatch.setattr(script_module.os, '_exit', lambda code: (_ for _ in ()).throw(SystemExit(code)))

    with pytest.raises(SystemExit) as exc:
        fake_script.loop()

    assert fake_script.calls == ['kill_emulator', 'restart_from_instance']
    assert exc.value.code == 1
