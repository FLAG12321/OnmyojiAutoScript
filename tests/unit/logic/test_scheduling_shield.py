# This Python file uses the following encoding: utf-8
"""共用调度屏蔽组件与 Plotline 借跑经验妖怪的屏蔽测试。

Plotline 在剧情流程中会借跑一次 ExperienceYoukai 的完整 run()，其
experience_exit() 写死了 set_next_run('ExperienceYoukai')。按产品决定，
剧情任务内部借跑不应改动该单账号任务自身的下次运行时间。
"""
import pytest

from tasks.Component.SchedulingShield import shield_scheduling
from tasks.ExperienceYoukai.script_task import ScriptTask as ExperienceYoukaiTask


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
def test_plotline_blocks_experience_youkai_schedule():
    """Plotline 借跑经验妖怪时，ExperienceYoukai 的调度必须被吞掉。"""
    config = FakeConfig()
    shielded = shield_scheduling(ExperienceYoukaiTask, ('ExperienceYoukai',), 'Plotline')
    task = _bare(shielded, config)

    # experience_exit() 尾部的真实调用形态
    task.set_next_run(task='ExperienceYoukai', success=True, finish=False)

    assert config.delayed == []
    assert issubclass(shielded, ExperienceYoukaiTask)


@pytest.mark.unit
def test_plotline_shield_forwards_other_tasks():
    """非屏蔽任务（如勾协产生的 WantedQuests）仍须原样转发。"""
    config = FakeConfig()
    shielded = shield_scheduling(ExperienceYoukaiTask, ('ExperienceYoukai',), 'Plotline')
    task = _bare(shielded, config)

    task.set_next_run(task='WantedQuests')

    assert config.delayed == ['WantedQuests']


@pytest.mark.unit
def test_plotline_uses_shielded_class():
    """Plotline 必须经由 shield_scheduling 创建经验妖怪实例，否则调度仍会泄漏。"""
    from pathlib import Path

    source = Path('tasks/Plotline/script_task.py').read_text(encoding='utf-8')
    assert 'shield_scheduling(' in source
    assert "('ExperienceYoukai',), 'Plotline'" in source
    # 裸实例化必须已被替换
    assert 'ExperienceYoukaiScriptTask(self.config, self.device)' not in source


@pytest.mark.unit
def test_shield_is_shared_component():
    """屏蔽工具须位于 Component 层，供 MultiDailyAltAcc 与 Plotline 共用。"""
    from tasks.MultiDailyAltAcc import DailyAltAccEx

    assert DailyAltAccEx.shield_scheduling is shield_scheduling


@pytest.mark.unit
def test_owner_prefix_is_parameterised():
    """owner 参数决定日志前缀，避免复用方共用写死的 [MultiDailyAltAcc]。"""
    import inspect

    source = inspect.getsource(shield_scheduling)
    assert "f'[{owner}] 屏蔽子任务调度: {task}'" in source


@pytest.mark.unit
def test_shield_preserves_base_behaviour_for_unblocked_names():
    """未被屏蔽的任务必须真正落到 base_cls 的原实现上（不是静默丢弃）。"""
    config = FakeConfig()
    shielded = shield_scheduling(ExperienceYoukaiTask, ('ExperienceYoukai',), 'Plotline')
    task = _bare(shielded, config)

    # finish=True 走 datetime.now() 分支，finish=False 走 self.start_time
    task.set_next_run(task='Tako', finish=True, success=True)
    task.set_next_run(task='GoldYoukai', persist=False)

    assert config.delayed == ['Tako', 'GoldYoukai']
