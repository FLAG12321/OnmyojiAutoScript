# This Python file uses the following encoding: utf-8
"""ProgressStore 单元测试：纯文件读写，不依赖模拟器与设备。"""
import json
from datetime import datetime, timedelta

import pytest

from tasks.MultiDailyAltAcc.progress import (
    FALSE_LIMIT,
    STALE_HOURS,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SKIPPED,
    ProgressStore,
    acc_key,
)

FLAGS_A = {'total_mail_enable': True, 'total_alliedteam_battle_enable': False}
FLAGS_B = {'total_mail_enable': False, 'total_alliedteam_battle_enable': True}


@pytest.mark.unit
def test_acc_key_joins_account_character_svr():
    # acc_key 是账号在进度文件中的唯一标识，格式必须稳定
    assert acc_key('mail@x.com', '小号一', '两情相悦') == 'mail@x.com|小号一|两情相悦'


@pytest.mark.unit
def test_ensure_phase_creates_file_on_first_call(tmp_path):
    store = ProgressStore('oas1', base_dir=tmp_path)
    # 首次调用应创建文件并返回 True（表示这是新阶段）
    assert store.ensure_phase(FLAGS_A, '20260730-0020') is True
    assert store.path.exists()
    assert store.path.name == 'multi_daily_progress_oas1.json'


@pytest.mark.unit
def test_ensure_phase_resumes_when_flags_unchanged(tmp_path):
    store = ProgressStore('oas1', base_dir=tmp_path)
    store.ensure_phase(FLAGS_A, '20260730-0020')
    # 失败重调度场景：开关没变，应接续（返回 False）而不是重建
    resumed = ProgressStore('oas1', base_dir=tmp_path)
    assert resumed.ensure_phase(FLAGS_A, '20260730-0020') is False


@pytest.mark.unit
def test_ensure_phase_rebuilds_when_flags_changed(tmp_path):
    store = ProgressStore('oas1', base_dir=tmp_path)
    store.ensure_phase(FLAGS_A, '20260730-0020')
    # 正常完成后 _schedule_* 改写了开关，进入新阶段，旧进度必须作废
    nxt = ProgressStore('oas1', base_dir=tmp_path)
    assert nxt.ensure_phase(FLAGS_B, '20260730-0605') is True


@pytest.mark.unit
def test_ensure_phase_rebuilds_when_progress_is_stale(tmp_path):
    store = ProgressStore('oas1', base_dir=tmp_path)
    store.ensure_phase(FLAGS_A, '20260730-0020')
    data = json.loads(store.path.read_text(encoding='utf-8'))
    data['created_at'] = (datetime.now() - timedelta(hours=STALE_HOURS + 1)).isoformat()
    store.path.write_text(json.dumps(data), encoding='utf-8')
    # 超过 STALE_HOURS 的残留进度（脚本长期停机）视为失效
    stale = ProgressStore('oas1', base_dir=tmp_path)
    assert stale.ensure_phase(FLAGS_A, '20260730-0020') is True


@pytest.mark.unit
def test_ensure_phase_rebuilds_on_corrupted_file(tmp_path):
    store = ProgressStore('oas1', base_dir=tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text('{not json', encoding='utf-8')
    # 文件损坏时宁可全量重跑，不能因解析失败崩掉任务
    assert store.ensure_phase(FLAGS_A, '20260730-0020') is True


@pytest.mark.unit
def test_clear_removes_file_and_is_idempotent(tmp_path):
    store = ProgressStore('oas1', base_dir=tmp_path)
    store.ensure_phase(FLAGS_A, '20260730-0020')
    store.clear()
    assert not store.path.exists()
    # 重复删除不应抛异常
    store.clear()


@pytest.mark.unit
def test_status_constants_values():
    assert (STATUS_PENDING, STATUS_DONE, STATUS_FAILED, STATUS_SKIPPED) == (
        'pending', 'done', 'failed', 'skipped')
    # false_count 达到该上限即迁移 skipped，保证账号收尾必然可达
    assert FALSE_LIMIT == 2


KEY = 'mail@x.com|小号一|两情相悦'


def _fresh_store(tmp_path):
    """创建一个已初始化阶段的 store，供状态类测试复用。"""
    store = ProgressStore('oas1', base_dir=tmp_path)
    store.ensure_phase(FLAGS_A, '20260730-0020')
    return store


@pytest.mark.unit
def test_unknown_account_and_task_are_not_finished(tmp_path):
    store = _fresh_store(tmp_path)
    # 没有任何记录时，账号与子任务都应视为未完成
    assert store.is_account_done(KEY) is False
    assert store.is_task_finished(KEY, 'mail') is False


@pytest.mark.unit
def test_mark_task_done_makes_it_finished(tmp_path):
    store = _fresh_store(tmp_path)
    store.mark_task(KEY, 'mail', STATUS_DONE)
    assert store.is_task_finished(KEY, 'mail') is True
    # 其他子任务不受影响
    assert store.is_task_finished(KEY, 'courtyard') is False


@pytest.mark.unit
def test_mark_task_failed_is_finished_and_reports_transition(tmp_path):
    store = _fresh_store(tmp_path)
    # 首次标 failed 应返回 True（触发发邮件）
    assert store.mark_task(KEY, 'alliedteam', STATUS_FAILED, etype='GameStuckError') is True
    assert store.is_task_finished(KEY, 'alliedteam') is True
    # 再次标 failed 不再是新迁移，避免重复发邮件
    assert store.mark_task(KEY, 'alliedteam', STATUS_FAILED, etype='GameStuckError') is False
    # 从已了结状态（done）改写为 failed 也不算首次迁移，不触发邮件
    store.mark_task(KEY, 'mail', STATUS_DONE)
    assert store.mark_task(KEY, 'mail', STATUS_FAILED, etype='ValueError') is False


@pytest.mark.unit
def test_mark_task_failed_scope_is_per_account(tmp_path):
    store = _fresh_store(tmp_path)
    other = 'mail@x.com|小号二|两情相悦'
    store.mark_task(KEY, 'alliedteam', STATUS_FAILED, etype='GameStuckError')
    # failed 只作用于当前角色，其他角色的同名子任务照常执行
    assert store.is_task_finished(other, 'alliedteam') is False


@pytest.mark.unit
def test_mark_task_extra_fields_persisted(tmp_path):
    store = _fresh_store(tmp_path)
    store.mark_task(KEY, 'alliedteam', STATUS_FAILED, etype='GameStuckError', emsg='timeout')
    task = store.get_task(KEY, 'alliedteam')
    assert task['status'] == STATUS_FAILED
    assert task['etype'] == 'GameStuckError'
    assert task['emsg'] == 'timeout'


@pytest.mark.unit
def test_mark_account_done(tmp_path):
    store = _fresh_store(tmp_path)
    store.mark_account_done(KEY)
    assert store.is_account_done(KEY) is True
    # 重新读取文件后仍然成立（确认已落盘）
    reloaded = ProgressStore('oas1', base_dir=tmp_path)
    assert reloaded.ensure_phase(FLAGS_A, '20260730-0020') is False
    assert reloaded.is_account_done(KEY) is True


@pytest.mark.unit
def test_mark_task_false_first_time_keeps_pending(tmp_path):
    store = _fresh_store(tmp_path)
    # 首次返回 False：计数 +1、保持 pending（保留一次进程内重跑机会），未达上限返回 False
    assert store.mark_task_false(KEY, 'courtyard') is False
    assert store.is_task_finished(KEY, 'courtyard') is False
    assert store.get_task(KEY, 'courtyard')['false_count'] == 1


@pytest.mark.unit
def test_mark_task_false_reaching_limit_marks_skipped(tmp_path):
    store = _fresh_store(tmp_path)
    store.mark_task_false(KEY, 'courtyard')
    # 第 2 次达到 FALSE_LIMIT：迁移终态 skipped，返回 True（调用方据此记一条 warning）
    assert store.mark_task_false(KEY, 'courtyard') is True
    assert store.get_task(KEY, 'courtyard')['status'] == STATUS_SKIPPED
    # skipped 算已了结：账号收尾不再被永远无法完成的子任务阻塞（否则每 10 分钟无限重调度）
    assert store.is_task_finished(KEY, 'courtyard') is True
    # 已了结后再报 False 不再重复迁移（不重复记 warning）
    assert store.mark_task_false(KEY, 'courtyard') is False


@pytest.mark.unit
def test_mark_task_false_preserves_battle_count(tmp_path):
    store = _fresh_store(tmp_path)
    # 用 extra 预置已打场次（battle_count 累加接口在 Task 3 才引入）
    store.mark_task(KEY, 'alliedteam', STATUS_PENDING, battle_count=10)
    store.mark_task_false(KEY, 'alliedteam')
    store.mark_task_false(KEY, 'alliedteam')
    # 计数型子任务被放行时已打场次必须保留（skipped 不是 done，不污染真实完成语义）
    task = store.get_task(KEY, 'alliedteam')
    assert task['status'] == STATUS_SKIPPED
    assert task['battle_count'] == 10


@pytest.mark.unit
def test_has_pending_tasks_detects_unfinished_subtask(tmp_path):
    store = _fresh_store(tmp_path)
    enabled = ['mail', 'courtyard', 'cooperation']
    # 一个都没跑：全是 pending
    assert store.has_pending_tasks(KEY, enabled) is True
    store.mark_task(KEY, 'mail', STATUS_DONE)
    store.mark_task(KEY, 'cooperation', STATUS_FAILED, etype='ValueError')
    # courtyard 仍未了结（例如超时返回 False 保持 pending）
    assert store.has_pending_tasks(KEY, enabled) is True
    store.mark_task(KEY, 'courtyard', STATUS_DONE)
    # done 与 failed 都算了结，此时才允许把账号标 done
    assert store.has_pending_tasks(KEY, enabled) is False


@pytest.mark.unit
def test_has_pending_tasks_empty_enabled_list(tmp_path):
    store = _fresh_store(tmp_path)
    # 没有启用任何子任务时不应判为有 pending（否则账号永远无法标完成）
    assert store.has_pending_tasks(KEY, []) is False


@pytest.mark.unit
def test_battle_count_starts_at_zero(tmp_path):
    store = _fresh_store(tmp_path)
    assert store.get_battle_count(KEY) == 0


@pytest.mark.unit
def test_add_battle_count_accumulates_and_persists(tmp_path):
    store = _fresh_store(tmp_path)
    store.add_battle_count(KEY)
    total = store.add_battle_count(KEY)
    assert total == 2
    # 重新加载后计数仍在，这是崩溃后接续剩余场次的前提
    reloaded = ProgressStore('oas1', base_dir=tmp_path)
    reloaded.ensure_phase(FLAGS_A, '20260730-0020')
    assert reloaded.get_battle_count(KEY) == 2


@pytest.mark.unit
def test_battle_count_survives_marking_task_failed(tmp_path):
    store = _fresh_store(tmp_path)
    for _ in range(10):
        store.add_battle_count(KEY)
    store.mark_task(KEY, 'alliedteam', STATUS_FAILED, etype='GameStuckError')
    # 已打场次必须保留，异常邮件要报告「已打 10 场」
    assert store.get_battle_count(KEY) == 10


@pytest.mark.unit
def test_battle_count_ignores_corrupted_value(tmp_path):
    store = _fresh_store(tmp_path)
    store.mark_task(KEY, 'alliedteam', STATUS_PENDING, battle_count='bad')
    # 数值异常时按 0 处理，宁可多打也不能崩
    assert store.get_battle_count(KEY) == 0


@pytest.mark.unit
def test_phase_flags_of_collects_total_switches_only():
    from tasks.MultiDailyAltAcc.config import MultiDailyAltAccConfig
    from tasks.MultiDailyAltAcc.progress import phase_flags_of

    base = MultiDailyAltAccConfig()
    flags = phase_flags_of(base)
    # 只收集 _schedule_* 会改写的「本轮做什么」开关
    assert 'total_mail_enable' in flags
    assert 'total_alliedteam_battle_enable' in flags
    assert 'total_tree_planting_enable' in flags
    # need_login 系列即将退役，且失败分支会改写它，不能参与阶段判定
    assert 'need_login' not in flags
    assert 'need_login_time' not in flags
    # 与调度无关的字段也不应进入快照
    assert 'sup_account_count' not in flags
    assert 'shutdown_after_finish' not in flags
    # total_KekkaiUtilize_enable 会在运行期被 MSGType.Utilize 改写并落盘，
    # 若参与判定会把失败重试误判成新阶段 → 重建进度 → 已完成账号重跑重复领奖
    assert 'total_KekkaiUtilize_enable' not in flags
    # 静态用户配置，不随阶段变化
    assert 'total_donatejade_enable' not in flags
    assert 'total_kekkaiActivation_enable' not in flags


@pytest.mark.unit
def test_phase_flags_of_reflects_switch_changes():
    from tasks.MultiDailyAltAcc.config import MultiDailyAltAccConfig
    from tasks.MultiDailyAltAcc.progress import phase_flags_of

    base = MultiDailyAltAccConfig()
    before = phase_flags_of(base)
    # 模拟 _schedule_evening 改写开关进入回礼阶段
    base.total_returngift_enable = True
    base.total_mail_enable = False
    base.total_cooperation_enable = False
    assert phase_flags_of(base) != before


@pytest.mark.unit
def test_phase_id_of_formats_date_and_time():
    from tasks.MultiDailyAltAcc.progress import phase_id_of

    assert phase_id_of(datetime(2026, 7, 30, 0, 20, 31)) == '20260730-0020'
