# This Python file uses the following encoding: utf-8
"""MultiTasks 子任务注册表与 Adapter 的测试。

Adapter 复用 Component 层的 shield_scheduling，工厂本身的行为已由
test_scheduling_shield.py 覆盖，这里只验证「三条注册项指向正确的类与
屏蔽名单」以及接线确实生效。
"""
import pytest

from tasks.Component.SchedulingShield import shield_scheduling
from tasks.MultiTasks.config import SubTaskType
from tasks.MultiTasks.runners import ADAPTERS, OWNER, SUB_TASKS, SubTaskSpec


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


@pytest.mark.unit
def test_runners_reuses_shared_shield_component():
    """必须复用 Component 层的屏蔽工厂，不得自造一套。"""
    from tasks.MultiTasks import runners

    assert runners.shield_scheduling is shield_scheduling
    assert OWNER == 'MultiTasks'


@pytest.mark.unit
def test_sub_tasks_registry_covers_every_enum_member():
    assert set(SUB_TASKS) == set(SubTaskType)
    assert set(ADAPTERS) == set(SubTaskType)


@pytest.mark.unit
def test_each_spec_suppresses_its_own_task_end_name():
    """每个子任务都必须屏蔽自己的调度，否则会污染单账号任务的 next_run。"""
    for key, spec in SUB_TASKS.items():
        assert spec.task_end_name in spec.suppressed, key


@pytest.mark.unit
def test_activity_shikigami_also_suppresses_souls_tidy():
    """多账号爬塔不清理御魂，沿用旧 MultiActivityShikigami 的行为。"""
    spec = SUB_TASKS[SubTaskType.ACTIVITY_SHIKIGAMI]
    assert spec.suppressed == ('ActivityShikigami', 'SoulsTidy')


@pytest.mark.unit
def test_spec_base_classes_point_at_single_account_tasks():
    """三条注册项必须指向对应的单账号 ScriptTask，而非任何多账号包装。"""
    from tasks.ActivityShikigami.script_task import ScriptTask as ShikigamiTask
    from tasks.ActivitySignIn.script_task import ScriptTask as SignInTask
    from tasks.ExperienceYoukai.script_task import ScriptTask as YoukaiTask

    assert SUB_TASKS[SubTaskType.ACTIVITY_SIGN_IN].base_cls is SignInTask
    assert SUB_TASKS[SubTaskType.ACTIVITY_SHIKIGAMI].base_cls is ShikigamiTask
    assert SUB_TASKS[SubTaskType.EXPERIENCE_YOUKAI].base_cls is YoukaiTask


@pytest.mark.unit
def test_adapters_are_built_once_and_subclass_their_base():
    """Adapter 类在模块导入时建一次；实例才是每账号新建。"""
    from tasks.MultiTasks import runners

    assert runners.ADAPTERS is ADAPTERS
    for key, adapter_cls in ADAPTERS.items():
        assert issubclass(adapter_cls, SUB_TASKS[key].base_cls)


@pytest.mark.unit
@pytest.mark.parametrize('sub_task', list(SubTaskType))
def test_adapter_swallows_own_schedule(sub_task):
    """接线验证：每个 Adapter 都真的吞掉自己那条调度，不落到 task_delay。"""
    config = FakeConfig()
    task = _bare(ADAPTERS[sub_task], config)

    task.set_next_run(task=SUB_TASKS[sub_task].task_end_name, success=True, finish=False)

    assert config.delayed == []


@pytest.mark.unit
@pytest.mark.parametrize('sub_task', list(SubTaskType))
def test_adapter_forwards_unblocked_tasks(sub_task):
    """非屏蔽任务原样转发：爬塔处理悬赏邀请产生的 WantedQuests 必须放过去。"""
    config = FakeConfig()
    task = _bare(ADAPTERS[sub_task], config)

    task.set_next_run(task='WantedQuests', finish=True, success=True)

    assert config.delayed == ['WantedQuests']


@pytest.mark.unit
def test_spec_is_frozen():
    """注册项不可变：Adapter 在导入期建好后改 spec 不会生效，索性禁止改。"""
    import dataclasses

    spec = SUB_TASKS[SubTaskType.ACTIVITY_SIGN_IN]
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.task_end_name = 'X'
