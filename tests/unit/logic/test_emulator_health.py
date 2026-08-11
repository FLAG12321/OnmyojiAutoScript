import psutil

from module.device.emulator_health import EmulatorHealth
from module.device.platform2.emulator_windows import Emulator, EmulatorInstance
from tasks.Script.config_device import ScreenshotMethod


class FakeDevice:
    def __init__(self, mumu_state=None):
        self.config = None
        self.mumu_state = mumu_state

    def _query_mumu12_state(self):
        return self.mumu_state


def test_health_allows_unavailable_mumu_state_when_other_checks_pass(monkeypatch):
    health = EmulatorHealth(FakeDevice())

    monkeypatch.setattr(health, '_process_check', lambda: (True, 'process ok'))
    monkeypatch.setattr(health, '_adb_check', lambda: (True, 'adb ok'))
    monkeypatch.setattr(health, '_screenshot_channel_check', lambda: (True, 'channel ok'))

    assert health.is_alive() is True
    assert health.why_dead() == 'alive (no failures recorded)'


def test_health_still_fails_other_checks_when_mumu_state_unavailable(monkeypatch):
    health = EmulatorHealth(FakeDevice())

    monkeypatch.setattr(health, '_process_check', lambda: (False, 'process dead'))
    monkeypatch.setattr(health, '_adb_check', lambda: (True, 'adb ok'))
    monkeypatch.setattr(health, '_screenshot_channel_check', lambda: (True, 'channel ok'))

    assert health.is_alive() is False
    assert health.why_dead() == 'process=process dead'


def test_health_fails_when_mumu_state_is_not_start_finished(monkeypatch):
    health = EmulatorHealth(FakeDevice({'player_state': 'starting'}))

    monkeypatch.setattr(health, '_process_check', lambda: (True, 'process ok'))
    monkeypatch.setattr(health, '_adb_check', lambda: (True, 'adb ok'))
    monkeypatch.setattr(health, '_screenshot_channel_check', lambda: (True, 'channel ok'))

    assert health.is_alive() is False
    assert health.why_dead() == "state=player_state='starting'"


def test_non_mumu_process_check_uses_generic_process_probe():
    class NonMumuDevice(FakeDevice):
        def __init__(self):
            super().__init__()
            self.emulator_instance = object()
            self.generic_probe_calls = 0

        def _query_mumu12_state(self):
            return None

        def _is_emulator_process_alive(self):
            self.generic_probe_calls += 1
            return True

    device = NonMumuDevice()
    health = EmulatorHealth(device)

    assert health._process_check() == (True, 'generic emulator process alive')
    assert device.generic_probe_calls == 1


class _FakeDeviceWithMethod(FakeDevice):
    """带 config.script.device.screenshot_method 的假设备。"""

    def __init__(self, method, handle_alive=True, adb_pong='pong'):
        super().__init__()
        self.method = method
        self.handle_alive = handle_alive
        self.adb_pong = adb_pong
        self._config = type('Config', (), {})()
        self._device = type('Device', (), {'screenshot_method': method})()
        self._script = type('Script', (), {'device': self._device})()
        self._config.script = self._script
        self.config = self._config

    def adb_shell(self, args):
        return self.adb_pong

    def _is_configured_handle_alive(self):
        return self.handle_alive


def test_channel_check_accepts_adb_enum():
    # ScreenshotMethod.ADB 的值是大写 'ADB', 不应被当作 unknown 处理
    device = _FakeDeviceWithMethod(ScreenshotMethod.ADB)
    health = EmulatorHealth(device)

    ok, reason = health._screenshot_channel_check()
    assert ok is True
    assert reason == 'adb shell ok'


def test_channel_check_accepts_window_background_when_handle_alive():
    device = _FakeDeviceWithMethod(ScreenshotMethod.WINDOW_BACKGROUND, handle_alive=True)
    health = EmulatorHealth(device)

    ok, reason = health._screenshot_channel_check()
    assert ok is True
    assert reason == 'window handle alive: True'


def test_channel_check_fails_window_background_when_handle_missing():
    device = _FakeDeviceWithMethod(ScreenshotMethod.WINDOW_BACKGROUND, handle_alive=None)
    health = EmulatorHealth(device)

    ok, reason = health._screenshot_channel_check()
    assert ok is False
    assert 'requires configured handle' in reason


def test_mumu12_process_check_falls_back_to_generic_probe(monkeypatch):
    # 安卓 15 实例名 MuMuPlayer-15.0-4 在严格 MuMuVMMHeadless --comment 扫描中找不到,
    # 应回退到通用探测, 不把已运行的模拟器误判为死亡
    class FallbackDevice(_FakeDeviceWithMethod):
        def __init__(self):
            super().__init__(ScreenshotMethod.ADB)
            self.emulator_instance = EmulatorInstance(
                serial='127.0.0.1:16512',
                name='MuMuPlayer-15.0-4',
                path='E:/MuMuPlayer/nx_main/MuMuNxMain.exe',
            )
            self.generic_probe_calls = 0

        def _is_emulator_process_alive(self):
            self.generic_probe_calls += 1
            return True

    device = FallbackDevice()
    health = EmulatorHealth(device)
    monkeypatch.setattr(psutil, 'process_iter', lambda *a, **k: [])

    ok, reason = health._process_check()
    assert ok is True
    assert reason == 'generic emulator process alive (fallback)'
    assert device.generic_probe_calls == 1
