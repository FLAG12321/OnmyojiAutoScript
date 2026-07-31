# This Python file uses the following encoding: utf-8
"""_run_with_stat 的进度挂钩行为测试：不依赖设备，用裸实例 + 假 store。"""
import pytest

from module.exception import (
    EmulatorNotRunningError,
    GameBugError,
    GameNotRunningError,
    GamePageUnknownError,
    GameStuckError,
    GameTooManyClickError,
    RequestHumanTakeover,
    ScriptError,
    TaskEnd,
)
from tasks.MultiDailyAltAcc.progress import STATUS_DONE, STATUS_FAILED

KEY = 'mail@x.com|小号一|两情相悦'


class FakeStore:
    """记录 mark_task / mark_task_false 调用的假 store。

    first_failed 控制 mark_task 是否报告首次 failed 迁移；
    false_reached 控制 mark_task_false 是否报告达到上限迁移 skipped。
    """

    def __init__(self, first_failed=True, false_reached=False):
        self.marks = []
        self.false_marks = []
        self._first_failed = first_failed
        self._false_reached = false_reached

    def mark_task(self, key, task, status, **extra):
        self.marks.append((key, task, status, extra))
        return status == STATUS_FAILED and self._first_failed

    def mark_task_false(self, key, task):
        self.false_marks.append((key, task))
        return self._false_reached

    def is_task_finished(self, key, task):
        return False


class FakeNotifier:
    def __init__(self):
        self.pushes = []

    def push(self, **kwargs):
        self.pushes.append(kwargs)
        return True


def _make_task(store, first_failed=True):
    """构造绕过 __init__ 的裸 ScriptTask 实例，只装配挂钩需要的属性。"""
    from types import SimpleNamespace

    from tasks.DailyAltAcc.script_task import ScriptTask

    task = object.__new__(ScriptTask)
    task._stat_ctx = None
    task._progress = store
    task._progress_key = KEY
    task._alliedteam_limit = 13
    notifier = FakeNotifier()
    task.config = SimpleNamespace(notifier=notifier)
    return task, notifier


@pytest.mark.unit
def test_success_marks_task_done():
    store = FakeStore()
    task, notifier = _make_task(store)
    assert task._run_with_stat('mail', lambda: 'ok') == 'ok'
    assert store.marks == [(KEY, 'mail', STATUS_DONE, {})]
    # 成功不应发通知
    assert notifier.pushes == []


@pytest.mark.unit
def test_explicit_false_return_records_false_count():
    store = FakeStore()
    task, notifier = _make_task(store)
    # courtyard 超时会显式 return False：业务上未完成，不能标 done（漏领奖励），
    # 也不是异常（不标 failed 不发邮件）；记一次 False 计数，由 store 决定
    # 保持 pending 重跑还是达到上限迁移 skipped
    assert task._run_with_stat('courtyard', lambda: False) is False
    assert store.marks == []
    assert store.false_marks == [(KEY, 'courtyard')]
    assert notifier.pushes == []


@pytest.mark.unit
def test_false_reaching_limit_does_not_push_mail():
    # 达到上限迁移 skipped 只记 warning 日志，不发通知（不是异常，避免刷屏）
    store = FakeStore(false_reached=True)
    task, notifier = _make_task(store)
    assert task._run_with_stat('courtyard', lambda: False) is False
    assert notifier.pushes == []


@pytest.mark.unit
def test_business_error_marks_failed_and_is_swallowed():
    store = FakeStore()
    task, notifier = _make_task(store)

    def boom():
        raise ValueError('ocr timeout')

    # 业务异常被吞掉，返回 None，让 run() 继续下一个子任务
    assert task._run_with_stat('courtyard', boom) is None
    key, name, status, extra = store.marks[0]
    assert (name, status) == ('courtyard', STATUS_FAILED)
    assert extra['etype'] == 'ValueError'
    assert extra['emsg'] == 'ocr timeout'
    # 首次迁移发且只发一封邮件
    assert len(notifier.pushes) == 1
    assert 'courtyard' in notifier.pushes[0]['content']


@pytest.mark.unit
def test_repeated_failure_does_not_resend_mail():
    store = FakeStore(first_failed=False)
    task, notifier = _make_task(store)

    def boom():
        raise ValueError('ocr timeout')

    task._run_with_stat('courtyard', boom)
    # store 报告非首次迁移时不再发邮件，避免每 10 分钟轰炸一次
    assert notifier.pushes == []


@pytest.mark.unit
@pytest.mark.parametrize('exc', [
    GameNotRunningError('game gone'),
    RequestHumanTakeover('need human'),
    # GameStuckError 由 script.py 捕获后重启游戏，绝不能被当业务异常吞掉，
    # 否则游戏一直卡着、后续子任务连环失败并逐个发邮件
    GameStuckError('Wait too long'),
    GameTooManyClickError('too many click'),
    GameBugError('game bug'),
    EmulatorNotRunningError('emulator gone'),
    # 页面持续无法识别是环境故障：吞掉会连锁误标所有后续子任务并把账号误判完成
    GamePageUnknownError('page unknown'),
    # 开发级错误：吞掉会掩盖 bug，维持旧行为直达 script.py
    ScriptError('dev mistake'),
])
def test_device_level_errors_propagate_without_marking(exc):
    store = FakeStore()
    task, notifier = _make_task(store)

    def boom():
        raise exc

    # 设备级异常必须上抛，交给账号级重试/调度级恢复，且不能标 failed
    with pytest.raises(type(exc)):
        task._run_with_stat('alliedteam', boom)
    assert store.marks == []
    assert notifier.pushes == []


@pytest.mark.unit
def test_business_error_propagates_without_store():
    # 单任务直跑（无 store）时业务异常维持旧行为原样上抛：
    # 此时无进度可标、无跳过机制，吞掉会让故障从「显式报错」变成静默成功
    task, notifier = _make_task(None)
    task._progress = None

    def boom():
        raise ValueError('ocr timeout')

    with pytest.raises(ValueError):
        task._run_with_stat('courtyard', boom)
    assert notifier.pushes == []


@pytest.mark.unit
def test_task_end_propagates_and_marks_done():
    store = FakeStore()
    task, _ = _make_task(store)

    def finish():
        raise TaskEnd('done signal')

    # TaskEnd 是部分子任务的正常结束信号，保持上抛且算完成
    with pytest.raises(TaskEnd):
        task._run_with_stat('KekkaiUtilize', finish)
    assert store.marks == [(KEY, 'KekkaiUtilize', STATUS_DONE, {})]


@pytest.mark.unit
def test_hooks_are_noop_without_store():
    task, notifier = _make_task(None)
    task._progress = None
    # 没有 store 时（例如单任务直跑）行为退化为原样，不应崩
    assert task._run_with_stat('mail', lambda: 'ok') == 'ok'
    assert notifier.pushes == []


@pytest.mark.unit
def test_should_skip_reflects_store_state():
    from types import SimpleNamespace

    from tasks.DailyAltAcc.script_task import ScriptTask

    class Store:
        def is_task_finished(self, key, task):
            return task in ('mail', 'courtyard')

    task = object.__new__(ScriptTask)
    task._progress = Store()
    task._progress_key = KEY
    assert task._should_skip('mail') is True
    assert task._should_skip('courtyard') is True
    assert task._should_skip('cooperation') is False


@pytest.mark.unit
def test_should_skip_false_without_store():
    from tasks.DailyAltAcc.script_task import ScriptTask

    task = object.__new__(ScriptTask)
    task._progress = None
    task._progress_key = None
    # 单任务直跑（无 store）时不跳过任何子任务
    assert task._should_skip('mail') is False


@pytest.mark.unit
def test_run_source_guards_every_subtask_with_should_skip():
    """run() 里每个子任务入口都必须带跳过判断，漏一个就会重复执行。"""
    from pathlib import Path

    source = Path('tasks/DailyAltAcc/script_task.py').read_text(encoding='utf-8')
    for flag in [
        'courtyard_enable',
        'mail_enable',
        'cooperation_enable',
        'donatejade_enable',
        'returngift_enable',
        'weekaward_enable',
        'mysteryshop_enable',
        'trialbattle_enable',
        'summon_up_enable',
        'publish_sr_enable',
        'kekkaiActivation_enable',
        'KekkaiUtilize_enable',
    ]:
        assert f'con.daily_alt_acc_config.{flag} and not self._should_skip(' in source, flag
    # 种树与同心的判断形式不同，单独校验
    assert 'tree_planting_enable > 0 and not self._should_skip("tree")' in source
    assert 'not self._should_skip("alliedteam")' in source


@pytest.mark.unit
def test_courtyard_recovery_only_on_explicit_false():
    """庭院补救循环没有超时，只能在显式返回 False 时进入，不能被吞掉的异常触发。"""
    from pathlib import Path

    source = Path('tasks/DailyAltAcc/script_task.py').read_text(encoding='utf-8')
    assert 'courtyard_result = self._run_with_stat("courtyard", self.run_courtyard)' in source
    assert 'if courtyard_result is False:' in source
