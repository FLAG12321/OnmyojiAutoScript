import threading
import types

from module.device.device import Device, EmulatorState


def make_bare_device(cancel_event):
    # 不走 __init__，手工构造一个仅用于测试 _is_cancelled 的 Device 实例
    device = object.__new__(Device)
    device._cancel_event = cancel_event
    return device


def test_is_cancelled_none_event_returns_false():
    device = make_bare_device(None)
    assert Device._is_cancelled(device) is False


def test_is_cancelled_unset_event_returns_false():
    device = make_bare_device(threading.Event())
    assert Device._is_cancelled(device) is False


def test_is_cancelled_set_event_returns_true():
    event = threading.Event()
    event.set()
    device = make_bare_device(event)
    assert Device._is_cancelled(device) is True


def build_cancelable_recovery_device(cancel_event):
    # 手工构造 Device，注入桩对象，统计 reset/start/watch 调用次数
    device = object.__new__(Device)
    device._cancel_event = cancel_event
    device.reset_calls = 0
    device.start_calls = 0
    device.watch_calls = 0
    device.emulator_state = EmulatorState.COLD
    device.health = types.SimpleNamespace(
        is_alive=lambda: True,
        why_dead=lambda: 'fake',
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
        return True

    device._emulator_start = emulator_start
    device.emulator_start_watch = emulator_start_watch
    device._transition_to = lambda target: setattr(device, 'emulator_state', target)
    return device


def test_full_recovery_aborts_when_cancelled_before_first_attempt():
    event = threading.Event()
    event.set()
    device = build_cancelable_recovery_device(event)

    assert Device.full_recovery(device) is False
    # 取消发生在 attempt 循环顶部：不应触发任何 reset/start/watch
    assert device.reset_calls == 0
    assert device.start_calls == 0
    assert device.watch_calls == 0


def test_full_recovery_proceeds_when_not_cancelled():
    device = build_cancelable_recovery_device(threading.Event())

    assert Device.full_recovery(device) is True
    # 未取消：正常走第一轮 reset → start → watch
    assert device.reset_calls == 1
    assert device.start_calls == 1
    assert device.watch_calls == 1
