# This Python file uses the following encoding: utf-8
"""MasterDisciple 代做标记屏蔽测试。

师徒流程结尾会按开关把 Exploration / ExperienceYoukai / Tako / GoldYoukai
标记成已完成（推迟其单账号任务）。这个标记只在「活动确实发生在当前账号上」时成立：
徒弟模式自动切号后设备停在徒弟号且无切回逻辑，标记会污染大号调度。

不依赖设备与真实 Config：用裸实例 + 假 config 只记录 task_delay 的任务名。
"""
import pytest

from module.exception import TaskEnd
from tasks.MasterDisciple.config import MasterDiscipleMode
from tasks.MasterDisciple.script_task import ScriptTask as MasterDiscipleTask

# 师徒流程会代做标记的四个单账号任务
DELEGATED = ['Exploration', 'ExperienceYoukai', 'Tako', 'GoldYoukai']


class FakeConfig:
    """只记录 task_delay 收到的任务名，其余一概不做。"""

    def __init__(self, master_disciple):
        self.delayed: list[str] = []
        self.master_disciple = master_disciple

    def task_delay(self, task, **kwargs):
        self.delayed.append(task)


def _make_task(mode, switched, **flags):
    """造裸实例：跳过 __init__，只装配 run() 收尾分支用得到的字段。"""
    from datetime import time as dt_time
    from types import SimpleNamespace

    md_config = SimpleNamespace(
        mode=mode,
        limit_count=1,
        limit_time=dt_time(hour=1, minute=0, second=0),
        run_exploration=True,
        run_exp_monster=True,
        run_stone_ju=True,
        run_coin_monster=True,
        run_guard=True,
        **flags,
    )
    master_disciple = SimpleNamespace(master_disciple_config=md_config)

    task = MasterDiscipleTask.__new__(MasterDiscipleTask)
    task.config = FakeConfig(master_disciple)
    task.start_time = None
    task._progress = None
    task._account_switched = switched
    return task


def _run_tail(task, success=True):
    """直接驱动 run() 的收尾分支，绕开需要设备的主体流程。

    用打桩的模式执行函数替代真实 run_as_master / run_as_disciple，
    并跳过 run() 开头的截图与页面导航。
    """
    task.screenshot = lambda: None
    task.ui_get_current_page = lambda: None
    task.ui_goto = lambda page, **kw: None
    # run() 开头会重置 _account_switched，这里让打桩的模式函数还原测试意图
    switched = task._account_switched

    def _mode_impl():
        task._account_switched = switched
        return success

    task.run_as_master = _mode_impl
    task.run_as_disciple = _mode_impl
    with pytest.raises(TaskEnd):
        task.run()
    return task.config.delayed


@pytest.mark.unit
def test_disciple_switched_blocks_delegated_marks():
    """徒弟模式切过号：四个代做标记必须全部屏蔽，只留 MasterDisciple 自身调度。"""
    task = _make_task(MasterDiscipleMode.DISCIPLE, switched=True)

    delayed = _run_tail(task)

    assert delayed == ['MasterDisciple']
    assert not any(name in delayed for name in DELEGATED)


@pytest.mark.unit
def test_disciple_without_switch_keeps_delegated_marks():
    """徒弟模式未切号（auto_switch 关或账号列表为空）：活动在当前账号做，标记合法。"""
    task = _make_task(MasterDiscipleMode.DISCIPLE, switched=False)

    delayed = _run_tail(task)

    assert delayed == ['MasterDisciple'] + DELEGATED


@pytest.mark.unit
def test_master_mode_keeps_delegated_marks():
    """师父模式不切号，在大号上带徒打，代做标记必须保留（这是开关的本意）。"""
    task = _make_task(MasterDiscipleMode.MASTER, switched=False)

    delayed = _run_tail(task)

    assert delayed == ['MasterDisciple'] + DELEGATED


@pytest.mark.unit
def test_failure_never_emits_delegated_marks():
    """失败收尾只按失败间隔重排自身，任何模式都不得代做标记。"""
    task = _make_task(MasterDiscipleMode.DISCIPLE, switched=True)

    delayed = _run_tail(task, success=False)

    assert delayed == ['MasterDisciple']


@pytest.mark.unit
def test_switch_marks_flag_regardless_of_result():
    """发起切号即置标记：切号失败同样无法保证仍在原账号上。"""
    from types import SimpleNamespace

    task = _make_task(MasterDiscipleMode.DISCIPLE, switched=False)
    task.device = SimpleNamespace(stuck_record_clear=lambda: None)
    task.config.notifier = SimpleNamespace(push=lambda **kw: None)
    account = SimpleNamespace(account='a@x.com', character='徒弟一', svr='两情相悦')

    import tasks.MasterDisciple.script_task as mod
    original = mod.SwitchAccount
    try:
        # 打桩成切号失败，验证标记仍被置上
        mod.SwitchAccount = lambda *a, **k: SimpleNamespace(switchAccount=lambda: False)
        assert task.switch_to_disciple_account(account) is False
    finally:
        mod.SwitchAccount = original

    assert task._account_switched is True


@pytest.mark.unit
def test_run_resets_switch_flag():
    """run() 开头必须重置标记，避免同一实例复用时残留上一轮状态。"""
    from pathlib import Path

    source = Path('tasks/MasterDisciple/script_task.py').read_text(encoding='utf-8')
    assert 'self._account_switched = False' in source
    assert 'if self._account_switched:' in source
