# This Python file uses the following encoding: utf-8
"""同心战斗报错后的接续豁免测试。

规则（见 ScriptTask._should_resume_instead_of_fail）：
- 同心本轮打过至少一场再报错 → 保持 pending，下轮接续剩余场次；
- 同心一场未打就报错 → 照旧标 failed 跳过；
- 其余子任务不享受豁免，一律标 failed。
"""
import pytest

from module.exception import GameStuckError
from tasks.MultiDailyAltAcc.progress import STATUS_FAILED

KEY = 'mail@x.com|小号一|两情相悦'


class FakeStore:
    """带同心场次计数的假 store，记录所有 mark_task 调用。"""

    def __init__(self, battle_count=0):
        self.marks = []
        self.battle_count = battle_count

    def get_battle_count(self, key, task='alliedteam'):
        return self.battle_count

    def mark_task(self, key, task, status, **extra):
        self.marks.append((key, task, status, extra))
        # 统一报告为首次 failed 迁移，便于断言通知是否被触发
        return status == STATUS_FAILED

    def mark_task_false(self, key, task):
        return False

    def is_task_finished(self, key, task):
        return False

    @property
    def failed_marks(self):
        return [m for m in self.marks if m[2] == STATUS_FAILED]


class FakeNotifier:
    def __init__(self):
        self.pushes = []

    def push(self, **kwargs):
        self.pushes.append(kwargs)
        return True


def _make_task(store):
    """构造绕过 __init__ 的裸 ScriptTask，只装配挂钩需要的属性。"""
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


def _battle_then_raise(store, battles, exc):
    """模拟打了 battles 场后报错的子任务：每场都累加已落盘场次。"""

    def run():
        store.battle_count += battles
        raise exc

    return run


# ---------- 设备级异常（原样上抛） ----------

@pytest.mark.unit
def test_alliedteam_with_progress_keeps_pending_and_still_raises():
    """打了 5 场后卡死：不标 failed（下轮可接续），但异常照旧上抛走 Restart。"""
    store = FakeStore(battle_count=0)
    task, notifier = _make_task(store)

    with pytest.raises(GameStuckError):
        task._run_with_stat(
            'alliedteam', _battle_then_raise(store, 5, GameStuckError('Wait too long'))
        )

    assert store.failed_marks == [], '有进展时不得标 failed，否则下轮会被跳过'
    assert notifier.pushes == [], '可自愈情况不发「已跳过」通知'
    assert store.battle_count == 5, '已落盘场次必须保留给下轮接续'


@pytest.mark.unit
def test_alliedteam_without_progress_marks_failed():
    """一场未打就卡死：卡在入口环节，标 failed 跳过并发通知。"""
    store = FakeStore(battle_count=0)
    task, notifier = _make_task(store)

    with pytest.raises(GameStuckError):
        task._run_with_stat(
            'alliedteam', _battle_then_raise(store, 0, GameStuckError('Wait too long'))
        )

    assert len(store.failed_marks) == 1
    assert store.failed_marks[0][1] == 'alliedteam'
    assert len(notifier.pushes) == 1


@pytest.mark.unit
def test_alliedteam_resume_counts_only_this_round_progress():
    """基线取本轮进入前的值：历史已打 5 场、本轮 0 场仍算零进展。"""
    store = FakeStore(battle_count=5)
    task, _ = _make_task(store)

    with pytest.raises(GameStuckError):
        task._run_with_stat(
            'alliedteam', _battle_then_raise(store, 0, GameStuckError('Wait too long'))
        )

    assert len(store.failed_marks) == 1, '本轮零进展必须标 failed，避免每轮重复同一失败'


@pytest.mark.unit
def test_other_subtask_with_store_still_marks_failed():
    """豁免不外溢：其他子任务即使 store 里有场次也照旧标 failed。"""
    store = FakeStore(battle_count=5)
    task, notifier = _make_task(store)

    with pytest.raises(GameStuckError):
        task._run_with_stat(
            'courtyard', _battle_then_raise(store, 3, GameStuckError('Wait too long'))
        )

    assert len(store.failed_marks) == 1
    assert store.failed_marks[0][1] == 'courtyard'
    assert len(notifier.pushes) == 1


# ---------- 业务异常（吞掉返回 None） ----------

@pytest.mark.unit
def test_alliedteam_business_error_with_progress_keeps_pending():
    """业务异常同一判据：有进展则保持 pending 并吞掉异常。"""
    store = FakeStore(battle_count=0)
    task, notifier = _make_task(store)

    assert task._run_with_stat(
        'alliedteam', _battle_then_raise(store, 4, RuntimeError('ocr failed'))
    ) is None
    assert store.failed_marks == []
    assert notifier.pushes == []


@pytest.mark.unit
def test_alliedteam_business_error_without_progress_marks_failed():
    store = FakeStore(battle_count=0)
    task, notifier = _make_task(store)

    assert task._run_with_stat(
        'alliedteam', _battle_then_raise(store, 0, RuntimeError('ocr failed'))
    ) is None
    assert len(store.failed_marks) == 1
    assert len(notifier.pushes) == 1


# ---------- 无 store 时不改变旧行为 ----------

@pytest.mark.unit
def test_no_store_alliedteam_device_error_still_raises():
    """单任务直跑（无 store）：判据退化为 False，维持原样上抛。"""
    task, _ = _make_task(None)
    task._progress_key = None

    with pytest.raises(GameStuckError):
        task._run_with_stat(
            'alliedteam', lambda: (_ for _ in ()).throw(GameStuckError('Wait too long'))
        )


# ---------- 失败退避时长 ----------

@pytest.mark.unit
def test_failure_backoff_is_three_minutes():
    """报错重启退避改为 3 分钟，让同心接续的下一轮更快开始。"""
    from pathlib import Path

    source = Path('tasks/MultiDailyAltAcc/script_task.py').read_text(encoding='utf-8')
    assert 'timedelta(minutes=3)' in source
    assert 'timedelta(minutes=10)' not in source
