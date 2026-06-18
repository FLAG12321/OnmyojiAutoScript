import types

import pytest

from module.device.device import Device, EmulatorState
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


def build_recovery_device(watch_results, health_alive=True):
    device = object.__new__(Device)
    device.reset_calls = 0
    device.start_calls = 0
    device.watch_calls = 0
    device.emulator_state = EmulatorState.COLD
    device.health = types.SimpleNamespace(
        is_alive=lambda: health_alive,
        why_dead=lambda: 'fake health failure',
    )
    device.reset = types.SimpleNamespace(
        execute=lambda: setattr(device, 'reset_calls', device.reset_calls + 1)
    )
    device._resolve_emulator_instance = lambda: object()
    device._emulator_function_wrapper = lambda fn: fn()

    def emulator_start():
        device.start_calls += 1
        return True

    def emulator_start_watch():
        device.watch_calls += 1
        return watch_results.pop(0)

    device._emulator_start = emulator_start
    device.emulator_start_watch = emulator_start_watch
    device._transition_to = lambda target: setattr(device, 'emulator_state', target)
    return device


def test_full_recovery_resets_and_restarts_after_first_watch_failure():
    device = build_recovery_device([False, True])

    assert Device.full_recovery(device) is True
    assert device.reset_calls == 2
    assert device.start_calls == 2
    assert device.watch_calls == 2


def test_full_recovery_returns_false_after_two_watch_failures():
    device = build_recovery_device([False, False])

    assert Device.full_recovery(device) is False
    # 两次启动前各清理一次，最终失败返回前再清理一次，确保进程退出前模拟器已被 kill。
    assert device.reset_calls == 3
    assert device.start_calls == 2
    assert device.watch_calls == 2


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
