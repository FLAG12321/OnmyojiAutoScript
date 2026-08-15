from types import SimpleNamespace
from datetime import datetime, timedelta

import script as script_module
from module.config.config import Config


def make_pending(command: str, next_run: datetime | None = None) -> SimpleNamespace:
    """构造一个已到点的待运行任务（伪 Function）。"""
    if next_run is None:
        next_run = datetime.now() - timedelta(minutes=5)
    return SimpleNamespace(command=command, next_run=next_run)


class FakeConfig:
    """伪 Config：只承载 get_next_task 用到的接口，避免访问真实配置文件。"""

    def __init__(self, tasks, forbidden_end_by_task):
        self._tasks = list(tasks)
        self.task = None
        self.forbidden_end_by_task = forbidden_end_by_task
        self.task_delay_calls = []

    def get_next(self):
        self.task = self._tasks.pop(0)
        return self.task

    def get_forbidden_time_end(self, task, now=None):
        return self.forbidden_end_by_task.get(task)

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
