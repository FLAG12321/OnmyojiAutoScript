import types

import pytest

from module.device.device import Device
from module.exception import EmulatorNotRunningError


class FakeHealth:
    def __init__(self, device):
        self.device = device
        self.calls = 0

    def is_alive(self):
        self.calls += 1
        return getattr(self.device, 'recovered', False)

    def why_dead(self):
        return 'fake emulator down'


class FakeReset:
    def __init__(self, device):
        self.device = device


def test_device_init_retries_base_initialization_after_recovery(monkeypatch):
    init_calls = []

    def fake_platform_init(self, config):
        init_calls.append(config)
        self.config = config
        if len(init_calls) == 1:
            raise EmulatorNotRunningError('emulator is not running')
        self.package = config.script.device.package_name.value

    def fake_full_recovery(self):
        self.recovered = True
        return True

    monkeypatch.setattr('module.device.emulator_health.EmulatorHealth', FakeHealth)
    monkeypatch.setattr('module.device.emulator_reset.FullReset', FakeReset)
    monkeypatch.setattr(Device.__mro__[1], '__init__', fake_platform_init)
    monkeypatch.setattr(Device, 'full_recovery', fake_full_recovery)
    monkeypatch.setattr(Device, 'screenshot_interval_set', lambda self: None)
    monkeypatch.setattr(Device, 'run_simple_screenshot_benchmark', lambda self: None)

    config = types.SimpleNamespace(
        script=types.SimpleNamespace(
            device=types.SimpleNamespace(
                emulatorinfo_type='manual',
                screenshot_method='adb',
                package_name=types.SimpleNamespace(value='com.netease.onmyoji.wyzymnqsd_cps'),
            )
        )
    )

    device = Device(config)

    assert init_calls == [config, config]
    assert device.package == 'com.netease.onmyoji.wyzymnqsd_cps'


def test_device_init_skips_health_probe_after_base_reports_emulator_down(monkeypatch):
    init_calls = []
    recovery_calls = []

    def fake_platform_init(self, config):
        init_calls.append(config)
        self.config = config
        if len(init_calls) == 1:
            raise EmulatorNotRunningError('emulator is not running')
        self.package = config.script.device.package_name.value

    class FailingHealth(FakeHealth):
        def is_alive(self):
            raise AssertionError('health probe should be skipped after EmulatorNotRunningError')

    def fake_full_recovery(self):
        recovery_calls.append(True)
        self.recovered = True
        return True

    monkeypatch.setattr('module.device.emulator_health.EmulatorHealth', FailingHealth)
    monkeypatch.setattr('module.device.emulator_reset.FullReset', FakeReset)
    monkeypatch.setattr(Device.__mro__[1], '__init__', fake_platform_init)
    monkeypatch.setattr(Device, 'full_recovery', fake_full_recovery)
    monkeypatch.setattr(Device, 'screenshot_interval_set', lambda self: None)
    monkeypatch.setattr(Device, 'run_simple_screenshot_benchmark', lambda self: None)

    config = types.SimpleNamespace(
        script=types.SimpleNamespace(
            device=types.SimpleNamespace(
                emulatorinfo_type='manual',
                screenshot_method='adb',
                package_name=types.SimpleNamespace(value='com.netease.onmyoji.wyzymnqsd_cps'),
            )
        )
    )

    device = Device(config)

    assert recovery_calls == [True]
    assert init_calls == [config, config]
    assert device.package == 'com.netease.onmyoji.wyzymnqsd_cps'
