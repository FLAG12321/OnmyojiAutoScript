"""结界挂卡状态机测试。"""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from tasks.KekkaiActivation import script_task as activation_module
from tasks.KekkaiActivation.config import CardType


pytestmark = pytest.mark.unit


def test_empty_card_slot_screens_card_before_checking_effect(monkeypatch):
    """空槽位必须先筛选结界卡，不能进入效果标志等待。"""
    task = object.__new__(activation_module.ScriptTask)
    task.I_A_ACTIVATE_YELLOW = object()
    task.I_A_ACTIVATE_GRAY = object()
    task.I_A_DEMOUNT = object()
    task.config = SimpleNamespace(
        kekkai_activation=SimpleNamespace(
            activation_config=SimpleNamespace(card_type=CardType.DAILY)
        )
    )

    status = iter([False, True])
    events = []
    task.goto_cards = lambda: events.append('goto_cards')
    task.screenshot = lambda: events.append('screenshot')
    task.check_card_status = lambda: next(status)
    task.appear = lambda *_args, **_kwargs: False
    task.screening_card = lambda rule: events.append(('screening_card', rule))
    task.check_card_effect = lambda: events.append('check_card_effect') or True
    task.ocr_time = lambda: timedelta(minutes=30)
    task.set_next_run = lambda *args, **kwargs: events.append('set_next_run')
    monkeypatch.setattr(activation_module.time, 'sleep', lambda *_args: None)

    activation_module.ScriptTask.run_activation(task, task.config.kekkai_activation.activation_config)

    assert ('screening_card', CardType.DAILY) in events
    assert events.index(('screening_card', CardType.DAILY)) < events.index('check_card_effect')


def test_unknown_card_effect_returns_after_timeout(monkeypatch):
    """按钮标志持续未知时应返回 None，而不是无限循环。"""
    task = object.__new__(activation_module.ScriptTask)
    task.I_A_INVITE = object()
    task.I_A_ACTIVATE_YELLOW = object()
    task.I_A_ACTIVATE_GRAY = object()
    task.screenshot = lambda: None
    task.appear = lambda *_args, **_kwargs: False

    class FakeTimer:
        def __init__(self, _limit):
            self.calls = 0

        def start(self):
            return self

        def reached(self):
            self.calls += 1
            return self.calls > 1

    monkeypatch.setattr(activation_module, 'Timer', FakeTimer)

    assert activation_module.ScriptTask.check_card_effect(task) is None


def test_zero_card_time_returns_none_without_immediate_task_end(monkeypatch):
    """剩余时间连续为0时交由外层延迟重试，不在卡页直接 TaskEnd。"""
    task = object.__new__(activation_module.ScriptTask)
    task.device = SimpleNamespace(image=object())
    task.screenshot = lambda: None
    task.O_CARD_ALL_TIME = SimpleNamespace(
        ocr_duration=lambda _image: timedelta(0)
    )
    monkeypatch.setattr(activation_module.time, 'sleep', lambda *_args: None)

    assert activation_module.ScriptTask.ocr_time(task) is None


def test_active_card_with_unknown_time_delays_retry(monkeypatch):
    """有卡但时间无效时应延迟一分钟并退出状态机，避免立即循环。"""
    task = object.__new__(activation_module.ScriptTask)
    task.config = SimpleNamespace(
        kekkai_activation=SimpleNamespace(
            activation_config=SimpleNamespace(card_type=CardType.DAILY)
        )
    )
    task.goto_cards = lambda: None
    task.screenshot = lambda: None
    task.check_card_status = lambda: True
    task.check_card_effect = lambda: True
    task.ocr_time = lambda: None
    scheduled = []
    task.set_next_run = lambda name, target: scheduled.append((name, target))
    monkeypatch.setattr(activation_module.time, 'sleep', lambda *_args: None)

    before = activation_module.datetime.now()
    result = activation_module.ScriptTask.run_activation(
        task, task.config.kekkai_activation.activation_config
    )

    assert result is False
    assert scheduled[0][0] == 'KekkaiActivation'
    assert scheduled[0][1] >= before + timedelta(seconds=59)
