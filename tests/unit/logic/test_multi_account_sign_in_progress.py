# This Python file uses the following encoding: utf-8
"""MultiAccountSignIn 账号级续做进度测试。

run() 依赖跨 config 扫描与真实切号/签到流程，这里全部打桩：
- ProgressStore 构造重定向到 tmp_path，避免污染仓库 config/tasks_config
- _load_accounts / SwitchAccount / _run_sign_in / set_next_run 打桩

覆盖：已签到账号被跳过、签到失败计入失败调度、跨天进度重建。
"""
from datetime import datetime
from types import SimpleNamespace

import pytest

from module.exception import TaskEnd
from tasks.Component.MultiAccountRunner.progress import ProgressStore, acc_key

ACCOUNTS = [
    ('oas1', SimpleNamespace(account='a@x.com', character='甲', svr='s1')),
    ('oas1', SimpleNamespace(account='b@x.com', character='乙', svr='s2')),
]
PHASE_FLAGS_DAY1 = {
    'accounts': [acc_key('a@x.com', '甲', 's1'), acc_key('b@x.com', '乙', 's2')],
    'day': '2026-08-07',
}


def _make_task():
    from tasks.MultiAccountSignIn.script_task import ScriptTask

    task = object.__new__(ScriptTask)
    task.config = SimpleNamespace(config_name='oas1')
    task.start_time = datetime(2026, 8, 7, 12, 0, 0)
    return task


def _redirect_store(tmp_path, monkeypatch):
    """把 script_task 模块内的 ProgressStore 构造重定向到 tmp_path。"""
    import tasks.MultiAccountSignIn.script_task as mod

    def factory(task_name, config_name, base_dir='config/tasks_config'):
        return ProgressStore(task_name, config_name, base_dir=tmp_path)

    monkeypatch.setattr(mod, 'ProgressStore', factory)


def _stub_switch(monkeypatch, ok=True):
    import tasks.MultiAccountSignIn.script_task as mod

    monkeypatch.setattr(mod, 'SwitchAccount',
                        lambda config, device, account: SimpleNamespace(
                            switchAccount=lambda: ok))


@pytest.mark.unit
def test_run_skips_signed_in_account_and_clears_on_success(tmp_path, monkeypatch):
    _redirect_store(tmp_path, monkeypatch)
    _stub_switch(monkeypatch, ok=True)
    task = _make_task()
    monkeypatch.setattr(task, '_load_accounts', lambda: list(ACCOUNTS))
    signed_in = []
    monkeypatch.setattr(task, '_run_sign_in',
                        lambda source, account: signed_in.append(account.character) or True)
    scheduled = []
    monkeypatch.setattr(task, 'set_next_run', lambda task, **k: scheduled.append(k))

    # 预置甲账号为已签到，模拟中断后重跑
    store = ProgressStore('multi_account_sign_in', 'oas1', base_dir=tmp_path)
    store.ensure_phase(PHASE_FLAGS_DAY1, '20260807-1200')
    store.mark_account_done(acc_key('a@x.com', '甲', 's1'))

    with pytest.raises(TaskEnd):
        task.run()

    # 甲已签到被跳过（只签到乙），全部成功收尾后进度被清
    assert signed_in == ['乙']
    assert not store.path.exists()
    assert any(s.get('success') is True for s in scheduled)


@pytest.mark.unit
def test_run_signin_failure_counts_as_failure(tmp_path, monkeypatch):
    _redirect_store(tmp_path, monkeypatch)
    _stub_switch(monkeypatch, ok=True)
    task = _make_task()
    single = [('oas1', SimpleNamespace(account='a@x.com', character='甲', svr='s1'))]
    single_phase = {
        'accounts': [acc_key('a@x.com', '甲', 's1')],
        'day': '2026-08-07',
    }
    monkeypatch.setattr(task, '_load_accounts', lambda: single)
    # 签到导航失败：返回 False
    monkeypatch.setattr(task, '_run_sign_in', lambda source, account: False)
    scheduled = []
    monkeypatch.setattr(task, 'set_next_run', lambda task, **k: scheduled.append(k))

    with pytest.raises(TaskEnd):
        task.run()

    # 签到失败计入失败调度，进度保留（账号未标完成，可接续）
    failure_calls = [s for s in scheduled if s.get('success') is False]
    assert len(failure_calls) == 1
    resumed = ProgressStore('multi_account_sign_in', 'oas1', base_dir=tmp_path)
    assert resumed.ensure_phase(single_phase, 'x') is False
    assert resumed.is_account_done(acc_key('a@x.com', '甲', 's1')) is False


@pytest.mark.unit
def test_run_cross_day_rebuilds_progress(tmp_path, monkeypatch):
    _redirect_store(tmp_path, monkeypatch)
    _stub_switch(monkeypatch, ok=True)
    task = _make_task()
    monkeypatch.setattr(task, '_load_accounts', lambda: list(ACCOUNTS))
    processed = []
    monkeypatch.setattr(task, '_run_sign_in',
                        lambda source, account: processed.append(account.character) or True)
    monkeypatch.setattr(task, 'set_next_run', lambda task, **k: None)

    # 预置昨日（2026-08-07）进度：甲已签到
    store = ProgressStore('multi_account_sign_in', 'oas1', base_dir=tmp_path)
    store.ensure_phase(PHASE_FLAGS_DAY1, '20260807-1200')
    store.mark_account_done(acc_key('a@x.com', '甲', 's1'))

    # 今日运行（跨天）：阶段标识含自然日，昨日进度失效重建，甲需重新处理
    task.start_time = datetime(2026, 8, 8, 12, 0, 0)
    with pytest.raises(TaskEnd):
        task.run()

    assert set(processed) == {'甲', '乙'}
