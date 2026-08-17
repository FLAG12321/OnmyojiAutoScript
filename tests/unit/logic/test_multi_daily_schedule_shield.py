# This Python file uses the following encoding: utf-8
"""MultiDailyAltAcc 调度屏蔽测试：小号批量执行不得改写大号单账号任务的下次运行时间。

不依赖设备与真实 Config：用裸实例 + 假 config 只记录 task_delay 的任务名。
"""
import pytest

from tasks.Component.SchedulingShield import shield_scheduling
from tasks.DailyAltAcc.script_task import ScriptTask as DailyAltAccTask
from tasks.KekkaiActivation.script_task import ScriptTask as KekkaiActivationTask
from tasks.KekkaiUtilize.script_task import ScriptTask as KekkaiUtilizeTask
from tasks.MultiDailyAltAcc import DailyAltAccEx


class FakeConfig:
    """只记录 task_delay 收到的任务名，其余一概不做。"""

    def __init__(self):
        self.delayed: list[str] = []

    def task_delay(self, task, **kwargs):
        self.delayed.append(task)


def _bare(cls, config):
    """绕过 __init__ 造裸实例：set_next_run 只用到 config 与 start_time。"""
    obj = cls.__new__(cls)
    obj.config = config
    obj.start_time = None
    return obj


def _injected_daily_cls():
    """复刻 MultiDailyAltAcc.CreatObjectFromModule 对 DailyAltAcc 的注入。"""
    return type('WQEX', (DailyAltAccTask,), {
        'get_config': DailyAltAccEx.get_config,
        'set_next_run': DailyAltAccEx.shield_self(DailyAltAccTask).set_next_run,
        '_create_nested_task': DailyAltAccEx.create_nested_task,
    })


@pytest.mark.unit
def test_shield_blocks_daily_alt_acc_own_schedule():
    """DailyAltAcc.run() 尾部的 set_next_run 必须被吞掉，否则每个小号都改一次大号调度。"""
    config = FakeConfig()
    task = _bare(_injected_daily_cls(), config)

    task.set_next_run(task='DailyAltAcc', finish=True, success=True)

    assert config.delayed == []


@pytest.mark.unit
def test_shield_forwards_unrelated_tasks():
    """非屏蔽任务（如勾协产生的 WantedQuests）必须原样转发，与 MultiActivityShikigami 策略一致。"""
    config = FakeConfig()
    task = _bare(_injected_daily_cls(), config)

    task.set_next_run(task='WantedQuests')

    assert config.delayed == ['WantedQuests']


@pytest.mark.unit
def test_shield_accepts_new_kwargs():
    """收 **kwargs 转发：基类新增参数（如 persist）时不得抛 TypeError。"""
    config = FakeConfig()
    task = _bare(_injected_daily_cls(), config)

    task.set_next_run(task='WantedQuests', persist=False, server=False)

    assert config.delayed == ['WantedQuests']


@pytest.mark.unit
@pytest.mark.parametrize('nested_cls', [KekkaiActivationTask, KekkaiUtilizeTask])
def test_nested_task_schedules_are_blocked(nested_cls):
    """挂卡/寄养是 DailyAltAcc 内部另建的独立实例，其调度同样必须被屏蔽。

    这两个任务在小号上是用写死的 DAILY 配置跑的，与大号自身设置无关；
    一旦泄漏，大号的 KekkaiActivation/KekkaiUtilize 会被拖到几分钟后甚至当下触发。
    """
    config = FakeConfig()
    shielded_cls = shield_scheduling(
        nested_cls, DailyAltAccEx.BLOCKED_NESTED, DailyAltAccEx.OWNER)
    nested = _bare(shielded_cls, config)

    nested.set_next_run(task='KekkaiActivation')
    nested.set_next_run(task='KekkaiUtilize')
    nested.set_next_run(task='WantedQuests')

    assert issubclass(shielded_cls, nested_cls)
    assert config.delayed == ['WantedQuests']


class SpyTask:
    """探针任务类：记录构造参数，避免测试触碰 BaseTask 的真实初始化。"""

    def __init__(self, config, device):
        self.config = config
        self.device = device
        self.start_time = None

    def set_next_run(self, task, **kwargs):
        self.config.task_delay(task, **kwargs)


@pytest.mark.unit
def test_standalone_daily_alt_acc_keeps_original_behaviour():
    """单账号直跑 DailyAltAcc 不受影响：调度照常落地，嵌套钩子返回原类实例。"""
    config = FakeConfig()
    task = _bare(DailyAltAccTask, config)
    task.device = object()

    task.set_next_run(task='DailyAltAcc', finish=True, success=True)
    assert config.delayed == ['DailyAltAcc']

    # 未被注入覆写时，钩子必须原样 task_cls(self.config, self.device)，不带任何屏蔽
    nested = task._create_nested_task(SpyTask)
    assert type(nested) is SpyTask
    assert nested.config is config and nested.device is task.device


@pytest.mark.unit
def test_injected_hook_returns_shielded_subclass():
    """注入覆写后，钩子必须返回屏蔽子类实例，且构造参数与原来一致。"""
    config = FakeConfig()
    task = _bare(_injected_daily_cls(), config)
    task.device = object()

    nested = task._create_nested_task(SpyTask)

    assert isinstance(nested, SpyTask) and type(nested) is not SpyTask
    assert nested.config is config and nested.device is task.device
    nested.set_next_run(task='KekkaiUtilize')
    assert config.delayed == []


@pytest.mark.unit
def test_nested_creation_goes_through_overridable_hook():
    """DailyAltAcc 必须经由 _create_nested_task 建挂卡/寄养实例，否则 Adapter 拦不住。"""
    from pathlib import Path

    source = Path('tasks/DailyAltAcc/script_task.py').read_text(encoding='utf-8')
    assert 'self._create_nested_task(KekkaiActivation)' in source
    assert 'self._create_nested_task(KekkaiUtilize)' in source
    assert 'KekkaiActivation(self.config, self.device)' not in source
    assert 'KekkaiUtilize(self.config, self.device)' not in source


@pytest.mark.unit
def test_multi_daily_injects_both_overrides():
    """MultiDailyAltAcc 必须同时注入 set_next_run 与 _create_nested_task，缺一则留泄漏。"""
    from pathlib import Path

    source = Path('tasks/MultiDailyAltAcc/script_task.py').read_text(encoding='utf-8')
    assert '"set_next_run": DailyAltAccEx.shield_self(module.ScriptTask).set_next_run,' in source
    assert '"_create_nested_task": DailyAltAccEx.create_nested_task,' in source
