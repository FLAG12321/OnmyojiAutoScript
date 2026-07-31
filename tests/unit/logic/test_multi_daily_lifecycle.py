# This Python file uses the following encoding: utf-8
"""MultiDailyAltAcc 阶段生命周期与账号级跳过测试。"""
from types import SimpleNamespace

import pytest

from tasks.MultiDailyAltAcc.progress import ProgressStore


def _make_task():
    from tasks.MultiDailyAltAcc.script_task import ScriptTask

    return object.__new__(ScriptTask)


def _account(account='mail@x.com', character='小号一', svr='两情相悦'):
    return SimpleNamespace(account=account, character=character, svr=svr)


@pytest.mark.unit
def test_progress_key_of_uses_account_character_svr():
    task = _make_task()
    assert task._progress_key_of(_account()) == 'mail@x.com|小号一|两情相悦'


@pytest.mark.unit
def test_should_process_account_skips_done_account(tmp_path):
    task = _make_task()
    store = ProgressStore('oas1', base_dir=tmp_path)
    store.ensure_phase({'total_mail_enable': True}, '20260730-0020')
    store.mark_account_done('mail@x.com|小号一|两情相悦')
    task._progress = store
    # 已完成的账号不再登录处理
    assert task._should_process_account(_account()) is False
    # 未完成的另一个角色照常处理
    assert task._should_process_account(_account(character='小号二')) is True


@pytest.mark.unit
def test_should_process_account_true_without_store():
    task = _make_task()
    task._progress = None
    # 没有 store（异常兜底）时一律处理，宁多跑不漏跑
    assert task._should_process_account(_account()) is True


@pytest.mark.unit
def test_should_process_account_source_no_longer_reads_need_login():
    """完成判定必须完全来自进度文件，不能再回退到 need_login 时间比较。"""
    from pathlib import Path

    source = Path('tasks/MultiDailyAltAcc/script_task.py').read_text(encoding='utf-8')
    assert 'def is_need_login' not in source
    assert 'self.is_need_login(' not in source
    assert 'def _should_process_account(self, account_info):' in source


@pytest.mark.unit
def test_run_clears_progress_only_on_success():
    """成功收尾才清进度；失败路径必须保留进度以便接续。"""
    from pathlib import Path

    source = Path('tasks/MultiDailyAltAcc/script_task.py').read_text(encoding='utf-8')
    assert 'self.next_run("MultiDailyAltAcc", success=True)' in source
    assert 'self._progress.clear()' in source
    success_pos = source.index('self.next_run("MultiDailyAltAcc", success=True)')
    clear_pos = source.index('self._progress.clear()')
    # clear 紧邻成功分支
    assert abs(clear_pos - success_pos) < 300
    # 账号异常分支不再直接调度，改为置 phase_failed 标志、由尾部统一分流；
    # 否则「先排 10 分钟重试、后被尾部 success=True 覆盖」会删掉本应保留的进度
    assert 'phase_failed = True' in source
    assert 'if phase_failed:' in source


@pytest.mark.unit
def test_create_task_instance_injects_progress_context():
    """子任务实例必须拿到 store、账号键与同心上限，否则挂钩全部退化为空操作。"""
    from pathlib import Path

    source = Path('tasks/MultiDailyAltAcc/script_task.py').read_text(encoding='utf-8')
    assert 'dff._progress = self._progress' in source
    assert 'dff._progress_key = self._progress_key_of(source_account_info)' in source
    assert 'dff._alliedteam_limit = config.alliedteam_limit_count' in source


@pytest.mark.unit
def test_finalize_only_marks_account_done_without_pending_tasks(tmp_path):
    """账号里仍有 pending 子任务时不能标账号 done，否则下轮会整账号跳过并漏做。"""
    from tasks.MultiDailyAltAcc.progress import STATUS_DONE

    task = _make_task()
    store = ProgressStore('oas1', base_dir=tmp_path)
    store.ensure_phase({'total_mail_enable': True}, '20260730-0020')
    task._progress = store
    task.daily_conf = SimpleNamespace(
        update_account_login_history=lambda account: None,
        multi_daily_alt_acc_config=SimpleNamespace(),
    )
    task.config = SimpleNamespace(
        model=SimpleNamespace(),
        save=lambda: None,
    )
    account = _account()
    account_config = SimpleNamespace(
        mail_enable=True,
        courtyard_enable=True,
        cooperation_enable=False,
        donatejade_enable=False,
        returngift_enable=False,
        weekaward_enable=False,
        mysteryshop_enable=False,
        kekkaiActivation_enable=False,
        KekkaiUtilize_enable=False,
        tree_planting_enable=0,
        trialbattle_enable=False,
        summon_up_enable=False,
        publish_sr_enable=False,
        alliedteam_battle_enable=False,
        alliedteam_ap_enable=False,
    )
    key = task._progress_key_of(account)
    store.mark_task(key, 'mail', STATUS_DONE)

    # courtyard 尚无 done/failed 记录，仍是 pending：账号不得标 done，返回 False 触发重试
    assert task._finalize_account_progress(account, account_config) is False
    assert store.is_account_done(key) is False

    store.mark_task(key, 'courtyard', STATUS_DONE)
    # 所有启用子任务均已了结后才允许账号级 done，从而保留后续整账号跳过优化
    assert task._finalize_account_progress(account, account_config) is True
    assert store.is_account_done(key) is True


@pytest.mark.unit
def test_device_errors_propagate_through_execute_daily_tasks(monkeypatch):
    """_run_with_stat 上抛后，_execute_daily_tasks 不得再次吞成 False。"""
    from module.exception import GameStuckError
    from tasks.MultiDailyAltAcc import script_task as mod

    # save_error_log 会截图/写日志，单元测试打桩跳过
    monkeypatch.setattr(mod.Script, 'save_error_log', lambda *a, **k: None)

    task = _make_task()
    task._create_task_instance = lambda *a, **k: SimpleNamespace(
        run=lambda: (_ for _ in ()).throw(GameStuckError('Wait too long'))
    )
    task._emit_account_error = lambda *a, **k: None
    task._emit_account_end = lambda *a, **k: None

    with pytest.raises(GameStuckError):
        task._execute_daily_tasks(SimpleNamespace(), _account())


@pytest.mark.unit
def test_direct_recovery_raises_schedule_failure_first():
    """直接上抛路径走不到尾部分流，必须在 raise 前安排 10 分钟退避。"""
    from pathlib import Path

    source = Path('tasks/MultiDailyAltAcc/script_task.py').read_text(encoding='utf-8')
    # 先断言存在性：index() 找不到会抛 ValueError，报错信息不如断言清晰
    assert 'raise GameNotRunningError("Game Not Running")' in source
    assert '设备级异常必须继续穿透' in source
    # GameNotRunningError 非切号崩溃分支：先调度再 raise
    game_raise = source.index('raise GameNotRunningError("Game Not Running")')
    game_schedule = source.rfind('self.next_run("MultiDailyAltAcc", success=False)', 0, game_raise)
    assert game_schedule != -1
    # 设备级异常统一分支：先调度再裸 raise 穿透到 script.py
    stuck_branch = source.index('设备级异常必须继续穿透')
    stuck_schedule = source.index('self.next_run("MultiDailyAltAcc", success=False)', stuck_branch)
    stuck_raise = source.index('raise', stuck_schedule)
    assert stuck_schedule < stuck_raise


@pytest.mark.unit
def test_no_dead_need_login_writes_remain():
    """need_login/need_login_time 已退役，不应再有任何写入（避免误导后续维护者）。"""
    from pathlib import Path

    source = Path('tasks/MultiDailyAltAcc/script_task.py').read_text(encoding='utf-8')
    assert 'need_login = False' not in source
    assert 'need_login = True' not in source
    assert 'need_login_time = ' not in source


@pytest.mark.unit
def test_account_retry_loop_preserved():
    """账号级 3 次重试保留：重试基于持久化进度只补未完成部分。"""
    from pathlib import Path

    source = Path('tasks/MultiDailyAltAcc/script_task.py').read_text(encoding='utf-8')
    assert 'max_retries = 3' in source
    assert 'while retry_count < max_retries:' in source


@pytest.mark.unit
def test_deprecated_fields_are_labelled():
    """保留字段兼容老配置，但描述必须标明已弃用，避免用户以为还有效。"""
    from tasks.MultiDailyAltAcc.config import MultiDailyAltAccConfig

    fields = MultiDailyAltAccConfig.model_fields
    assert '已弃用' in fields['need_login'].description
    assert '已弃用' in fields['need_login_time'].description
