from types import SimpleNamespace
from datetime import datetime, timedelta

import pytest

import script as script_module
import tasks.base_task as base_task_module
from module.config.config import Config
from module.exception import TaskEnd
from tasks.base_task import BaseTask


def make_pending(command: str, next_run: datetime | None = None) -> SimpleNamespace:
    """构造一个已到点的待运行任务（伪 Function）。"""
    if next_run is None:
        next_run = datetime.now() - timedelta(minutes=5)
    return SimpleNamespace(command=command, next_run=next_run)


class FakeConfig:
    """伪 Config：只承载 get_next_task 用到的接口，避免访问真实配置文件。"""

    def __init__(self, tasks, forbidden_end_by_task, random_delay_by_task=None):
        self._tasks = list(tasks)
        self.task = None
        self.forbidden_end_by_task = forbidden_end_by_task
        # 任务名 -> 随机延时 timedelta（None 表示无延时）
        self.random_delay_by_task = random_delay_by_task or {}
        self.task_delay_calls = []

    def get_next(self):
        self.task = self._tasks.pop(0)
        return self.task

    def get_forbidden_time_end(self, task, now=None):
        return self.forbidden_end_by_task.get(task)

    def get_task_random_delay(self, task, **kwargs):
        return self.random_delay_by_task.get(task)

    def task_delay(self, task, start_time=None, success=None, server=True, target=None, **kwargs):
        # 收 **kwargs 兼容真实 Config.task_delay 的后续扩参（如 persist），
        # 避免替身签名落后于被替代接口时抛 TypeError
        self.task_delay_calls.append((task, target, server))


class FakeScript(script_module.Script):
    """伪脚本：绕开真实初始化，只测 get_next_task 的禁止时间段接线。"""

    def __init__(self, config):
        self.config_name = 'oas'
        self.config = config
        self.state_queue = None


def test_get_next_task_delays_forbidden_task_without_dispatch():
    forbidden_end = datetime.now() + timedelta(hours=1)
    cfg = FakeConfig(
        tasks=[make_pending('KekkaiUtilize'), make_pending('Orochi')],
        forbidden_end_by_task={'KekkaiUtilize': forbidden_end},
    )
    script = FakeScript(cfg)

    command = script.get_next_task()

    # 命中禁止区间：推迟而非派发，随后重新选择下一个任务
    assert command == 'Orochi'
    assert cfg.task_delay_calls == [('KekkaiUtilize', forbidden_end, False)]


def test_get_next_task_runs_normal_pending_task():
    cfg = FakeConfig(
        tasks=[make_pending('Orochi')],
        forbidden_end_by_task={'KekkaiUtilize': datetime.now() + timedelta(hours=1)},
    )
    script = FakeScript(cfg)

    command = script.get_next_task()

    assert command == 'Orochi'
    assert cfg.task_delay_calls == []


class TestGetForbiddenTimeEnd:
    def _make_fake(self, submodel_name='utilize_config', enable=True, time_range='01:00-02:00'):
        """构造带指定禁止时间段配置的伪 Config（仅 model 结构）。"""
        sub = SimpleNamespace(
            forbidden_time_enable=enable,
            forbidden_time_range=time_range,
        )
        task_obj = SimpleNamespace(**{submodel_name: sub})
        model = SimpleNamespace(kekkai_utilize=task_obj)
        return SimpleNamespace(model=model)

    def test_hit_returns_interval_end(self):
        fake = self._make_fake()
        end = Config.get_forbidden_time_end(fake, 'KekkaiUtilize', now=datetime(2026, 8, 8, 1, 30))
        assert end == datetime(2026, 8, 8, 2, 0)

    def test_cross_day_range(self):
        fake = self._make_fake(time_range='23:00-01:00')
        end = Config.get_forbidden_time_end(fake, 'KekkaiUtilize', now=datetime(2026, 8, 8, 0, 30))
        assert end == datetime(2026, 8, 8, 1, 0)

    def test_not_in_range_returns_none(self):
        fake = self._make_fake()
        assert Config.get_forbidden_time_end(fake, 'KekkaiUtilize', now=datetime(2026, 8, 8, 3, 0)) is None

    def test_disabled_returns_none(self):
        fake = self._make_fake(enable=False)
        assert Config.get_forbidden_time_end(fake, 'KekkaiUtilize', now=datetime(2026, 8, 8, 1, 30)) is None

    def test_unregistered_task_returns_none(self):
        fake = self._make_fake()
        assert Config.get_forbidden_time_end(fake, 'Orochi', now=datetime(2026, 8, 8, 1, 30)) is None

    def test_kekkai_activation_uses_activation_config(self):
        sub = SimpleNamespace(forbidden_time_enable=True, forbidden_time_range='01:00-02:00')
        task_obj = SimpleNamespace(activation_config=sub)
        model = SimpleNamespace(kekkai_activation=task_obj)
        fake = SimpleNamespace(model=model)
        end = Config.get_forbidden_time_end(fake, 'KekkaiActivation', now=datetime(2026, 8, 8, 1, 30))
        assert end == datetime(2026, 8, 8, 2, 0)


def test_get_next_task_applies_random_delay_on_forbidden_end():
    """调度层解禁路径：配置了随机延时时，task_delay 收到的目标时间应为区间结束 + 随机延时。"""
    forbidden_end = datetime.now() + timedelta(hours=1)
    delay = timedelta(minutes=15)
    cfg = FakeConfig(
        tasks=[make_pending('KekkaiUtilize'), make_pending('Orochi')],
        forbidden_end_by_task={'KekkaiUtilize': forbidden_end},
        random_delay_by_task={'KekkaiUtilize': delay},
    )
    script = FakeScript(cfg)

    command = script.get_next_task()

    assert command == 'Orochi'
    assert cfg.task_delay_calls == [('KekkaiUtilize', forbidden_end + delay, False)]


class TestGetTaskRandomDelay:
    """Config.get_task_random_delay：读取任务配置计算下次上号随机延时。"""

    def _make_fake(self, enable=True, dmin=10, dmax=30, submodel_name='utilize_config',
                   model_name='kekkai_utilize'):
        sub = SimpleNamespace(
            random_delay_enable=enable,
            random_delay_min=dmin,
            random_delay_max=dmax,
        )
        task_obj = SimpleNamespace(**{submodel_name: sub})
        model = SimpleNamespace(**{model_name: task_obj})
        return SimpleNamespace(model=model)

    def test_enabled_returns_delay_within_range(self):
        fake = self._make_fake()
        delay = Config.get_task_random_delay(fake, 'KekkaiUtilize')
        assert timedelta(minutes=10) <= delay <= timedelta(minutes=30)

    def test_disabled_returns_none(self):
        fake = self._make_fake(enable=False)
        assert Config.get_task_random_delay(fake, 'KekkaiUtilize') is None

    def test_unregistered_task_returns_none(self):
        fake = self._make_fake()
        assert Config.get_task_random_delay(fake, 'Orochi') is None

    def test_nonpositive_max_returns_none(self):
        fake = self._make_fake(dmin=0, dmax=0)
        assert Config.get_task_random_delay(fake, 'KekkaiUtilize') is None

    def test_swapped_min_max_still_in_range(self):
        # min/max 配颠倒时自动对调，返回值仍落在 [小值, 大值] 区间
        fake = self._make_fake(dmin=30, dmax=10)
        delay = Config.get_task_random_delay(fake, 'KekkaiUtilize')
        assert timedelta(minutes=10) <= delay <= timedelta(minutes=30)

    def test_kekkai_activation_uses_activation_config(self):
        fake = self._make_fake(submodel_name='activation_config', model_name='kekkai_activation')
        delay = Config.get_task_random_delay(fake, 'KekkaiActivation')
        assert timedelta(minutes=10) <= delay <= timedelta(minutes=30)


class FakeBaseTask:
    """伪 BaseTask 实例：只承载 check_forbidden_time / apply_random_delay 用到的接口。"""

    def __init__(self, config):
        self.config = config
        self.set_next_run_calls = []

    def set_next_run(self, task, target=None, server=True, **kwargs):
        self.set_next_run_calls.append((task, target, server))


class TestCheckForbiddenTimeRandomDelay:
    """任务内禁止时间段路径：解禁时刻应叠加随机延时。"""

    def _make_config(self, delay):
        return SimpleNamespace(
            get_task_random_delay=lambda task: delay,
        )

    def test_forbidden_hit_applies_delay(self, monkeypatch):
        # 固定 now，让当前时间稳定落在禁止区间 01:00-02:00 内
        class FakeDateTime:
            @staticmethod
            def now():
                return datetime(2026, 8, 8, 1, 30)

        monkeypatch.setattr(base_task_module, 'datetime', FakeDateTime)
        delay = timedelta(minutes=20)
        fake_task = FakeBaseTask(self._make_config(delay))

        with pytest.raises(TaskEnd):
            BaseTask.check_forbidden_time(fake_task, 'KekkaiUtilize', True, '01:00-02:00')

        # 下次运行时间 = 区间结束 02:00 + 随机延时 20 分钟
        assert fake_task.set_next_run_calls == [
            ('KekkaiUtilize', datetime(2026, 8, 8, 2, 20), False)
        ]

    def test_forbidden_hit_without_delay_keeps_interval_end(self, monkeypatch):
        class FakeDateTime:
            @staticmethod
            def now():
                return datetime(2026, 8, 8, 1, 30)

        monkeypatch.setattr(base_task_module, 'datetime', FakeDateTime)
        fake_task = FakeBaseTask(self._make_config(None))

        with pytest.raises(TaskEnd):
            BaseTask.check_forbidden_time(fake_task, 'KekkaiUtilize', True, '01:00-02:00')

        assert fake_task.set_next_run_calls == [
            ('KekkaiUtilize', datetime(2026, 8, 8, 2, 0), False)
        ]

    def test_not_in_range_does_nothing(self, monkeypatch):
        class FakeDateTime:
            @staticmethod
            def now():
                return datetime(2026, 8, 8, 3, 0)

        monkeypatch.setattr(base_task_module, 'datetime', FakeDateTime)
        fake_task = FakeBaseTask(self._make_config(timedelta(minutes=20)))

        BaseTask.check_forbidden_time(fake_task, 'KekkaiUtilize', True, '01:00-02:00')

        assert fake_task.set_next_run_calls == []


class TestApplyRandomDelay:
    """BaseTask.apply_random_delay：正常完成后的下次运行时间叠加随机延时。"""

    def test_delay_applied(self):
        delay = timedelta(minutes=12)
        fake_task = FakeBaseTask(SimpleNamespace(get_task_random_delay=lambda task: delay))
        target = datetime(2026, 8, 8, 12, 0)

        result = BaseTask.apply_random_delay(fake_task, 'KekkaiUtilize', target)

        assert result == target + delay

    def test_no_delay_returns_target(self):
        fake_task = FakeBaseTask(SimpleNamespace(get_task_random_delay=lambda task: None))
        target = datetime(2026, 8, 8, 12, 0)

        result = BaseTask.apply_random_delay(fake_task, 'KekkaiUtilize', target)

        assert result == target
