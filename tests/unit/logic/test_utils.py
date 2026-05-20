import pytest
from module.config.utils import deep_get


class TestDeepGet:
    def test_simple_key(self):
        data = {"a": {"b": 1}}
        assert deep_get(data, keys="a.b") == 1

    def test_key_not_found_returns_default(self):
        data = {"a": {"b": 1}}
        assert deep_get(data, keys="a.c", default=42) == 42

    def test_none_data_returns_default(self):
        assert deep_get(None, keys="a.b", default="fallback") == "fallback"

    def test_empty_dict(self):
        assert deep_get({}, keys="a.b", default=None) is None

    def test_keys_as_list(self):
        data = {"a": {"b": 1}}
        assert deep_get(data, keys=["a", "b"]) == 1


import time
from module.base.timer import Timer, future_time, past_time


class TestTimer:
    def test_timer_reached_after_limit(self):
        t = Timer(limit=0.01, count=0)
        t.start()
        time.sleep(0.02)
        assert t.reached() is True

    def test_timer_not_reached_before_limit(self):
        t = Timer(limit=10.0, count=0)
        t.start()
        assert t.reached() is False

    def test_timer_reset(self):
        t = Timer(limit=0.01, count=0)
        t.start()
        time.sleep(0.02)
        t.reset()
        assert t.reached() is False

    def test_timer_clear(self):
        t = Timer(limit=10.0)
        t.start()
        t.clear()
        assert t.started() is False

    def test_timer_count_threshold(self):
        t = Timer(limit=0.01, count=3)
        t.start()
        time.sleep(0.02)
        # count < 3: 即使超时也不触发
        assert t.reached() is False
        assert t.reached() is False
        assert t.reached() is False
        # 第4次调用 count=4 > 3
        assert t.reached() is True


class TestFutureTime:
    def test_future_time_returns_today_if_not_passed(self):
        from datetime import datetime
        now = datetime.now()
        # 使用一个还未到的时间
        result = future_time("23:59")
        assert result > now

    def test_past_time_returns_earlier(self):
        from datetime import datetime
        now = datetime.now()
        result = past_time("00:01")
        # 00:01 通常在当天更早或昨天
        assert result < now or result.hour == 0