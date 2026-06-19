import threading

from module.device.device import Device


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
