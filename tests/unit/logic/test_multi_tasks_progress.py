# This Python file uses the following encoding: utf-8
"""MultiTasks 主流程与账号级续做进度测试。

run() 依赖真实切号与子任务流程，这里全部打桩：
- ProgressStore 构造重定向到 tmp_path，避免污染仓库 config/tasks_config
- 账号来源、切号、子任务执行、set_next_run 打桩

覆盖：已完成账号跳过、失败保留进度、全成功清进度、load_failure 不清进度、
改子任务/改来源触发进度重建、邮箱分组排序。
"""
from datetime import datetime
from types import SimpleNamespace

import pytest

from module.exception import TaskEnd
from tasks.Component.MultiAccountRunner.progress import ProgressStore, acc_key
from tasks.MultiTasks.config import AccountSourceType, SubTaskType


def _account(account, character, svr='s1'):
    return SimpleNamespace(
        account=account, character=character, svr=svr,
        account_alias='', apple_or_android=True,
        last_complete_time=datetime(2023, 1, 1),
    )


def _make_task(sub_task=SubTaskType.ACTIVITY_SIGN_IN,
               account_source=AccountSourceType.CONFIG_SELECTION):
    from tasks.MultiTasks.script_task import ScriptTask

    task = object.__new__(ScriptTask)
    task.start_time = datetime(2026, 8, 17, 12, 0, 0)
    task.config = SimpleNamespace(
        config_name='oas1',
        multi_tasks=SimpleNamespace(
            multi_tasks_config=SimpleNamespace(
                sub_task=sub_task, account_source=account_source),
        ),
        notifier=SimpleNamespace(push=lambda **kwargs: None),
    )
    task.device = SimpleNamespace(stuck_record_clear=lambda: None)
    return task


def _redirect_store(tmp_path, monkeypatch):
    """把 script_task 模块内的 ProgressStore 构造重定向到 tmp_path。"""
    import tasks.MultiTasks.script_task as mod

    def factory(task_name, config_name, base_dir='config/tasks_config'):
        return ProgressStore(task_name, config_name, base_dir=tmp_path)

    monkeypatch.setattr(mod, 'ProgressStore', factory)


def _stub_source(monkeypatch, items, warnings=None, load_failure=False):
    """打桩来源注册表：三种来源都返回同一组执行项。"""
    import tasks.MultiTasks.script_task as mod

    monkeypatch.setattr(mod, 'ACCOUNT_SOURCES', {
        source: (lambda config, _i=items, _w=warnings, _f=load_failure:
                 (_i, list(_w or []), _f))
        for source in AccountSourceType
    })


def _stub_execution(monkeypatch, task, results):
    """打桩切号与子任务执行：results 是 {角色名: True/False}，并记录执行顺序。"""
    executed = []

    def run_sub_task(account):
        executed.append(account.character)
        return results[account.character]

    monkeypatch.setattr(task, '_switch_and_run', run_sub_task)
    return executed


def _phase_flags(items, sub_task=SubTaskType.ACTIVITY_SIGN_IN,
                 account_source=AccountSourceType.CONFIG_SELECTION,
                 day='2026-08-17'):
    return {
        'sub_task': sub_task.value,
        'account_source': account_source.value,
        'accounts': [acc_key(a.account, a.character, a.svr) for _n, a in items],
        'day': day,
    }


@pytest.mark.unit
def test_run_skips_done_account_and_clears_on_success(tmp_path, monkeypatch):
    _redirect_store(tmp_path, monkeypatch)
    items = [('oas1', _account('a@x.com', '甲')), ('oas1', _account('b@x.com', '乙'))]
    _stub_source(monkeypatch, items)
    task = _make_task()
    executed = _stub_execution(monkeypatch, task, {'甲': True, '乙': True})
    scheduled = []
    monkeypatch.setattr(task, 'set_next_run', lambda name, **k: scheduled.append(k))

    # 预置甲为已完成，模拟中断后重跑
    store = ProgressStore('multi_tasks', 'oas1', base_dir=tmp_path)
    store.ensure_phase(_phase_flags(items), '20260817-1200')
    store.mark_account_done(acc_key('a@x.com', '甲', 's1'))

    with pytest.raises(TaskEnd):
        task.run()

    assert executed == ['乙']
    assert any(s.get('success') is True for s in scheduled)
    # 全部成功收尾：先调度后清，进度文件应已删除
    assert not store.path.exists()


@pytest.mark.unit
def test_run_keeps_progress_when_sub_task_fails(tmp_path, monkeypatch):
    _redirect_store(tmp_path, monkeypatch)
    items = [('oas1', _account('a@x.com', '甲'))]
    _stub_source(monkeypatch, items)
    task = _make_task()
    _stub_execution(monkeypatch, task, {'甲': False})
    scheduled = []
    monkeypatch.setattr(task, 'set_next_run', lambda name, **k: scheduled.append(k))

    with pytest.raises(TaskEnd):
        task.run()

    assert [s for s in scheduled if s.get('success') is False]
    resumed = ProgressStore('multi_tasks', 'oas1', base_dir=tmp_path)
    assert resumed.ensure_phase(_phase_flags(items), 'x') is False
    assert resumed.is_account_done(acc_key('a@x.com', '甲', 's1')) is False


@pytest.mark.unit
def test_run_keeps_progress_when_load_failure(tmp_path, monkeypatch):
    """load_failure 时账号集合不完整，即使全部成功也不能清进度。"""
    _redirect_store(tmp_path, monkeypatch)
    items = [('oas1', _account('a@x.com', '甲'))]
    _stub_source(monkeypatch, items, load_failure=True)
    task = _make_task()
    _stub_execution(monkeypatch, task, {'甲': True})
    monkeypatch.setattr(task, 'set_next_run', lambda name, **k: None)

    with pytest.raises(TaskEnd):
        task.run()

    resumed = ProgressStore('multi_tasks', 'oas1', base_dir=tmp_path)
    assert resumed.ensure_phase(_phase_flags(items), 'x') is False
    assert resumed.is_account_done(acc_key('a@x.com', '甲', 's1')) is True


@pytest.mark.unit
def test_run_fails_fast_on_empty_account_list(tmp_path, monkeypatch):
    _redirect_store(tmp_path, monkeypatch)
    _stub_source(monkeypatch, [])
    task = _make_task()
    scheduled = []
    monkeypatch.setattr(task, 'set_next_run', lambda name, **k: scheduled.append(k))

    with pytest.raises(TaskEnd):
        task.run()

    assert scheduled == [{'finish': True, 'success': False, 'server': False}]


@pytest.mark.unit
def test_run_pushes_warnings_once(tmp_path, monkeypatch):
    _redirect_store(tmp_path, monkeypatch)
    items = [('oas1', _account('a@x.com', '甲'))]
    _stub_source(monkeypatch, items, warnings=['不存在1', '不存在2'])
    task = _make_task()
    _stub_execution(monkeypatch, task, {'甲': True})
    monkeypatch.setattr(task, 'set_next_run', lambda name, **k: None)
    pushed = []
    task.config.notifier = SimpleNamespace(push=lambda **kwargs: pushed.append(kwargs))

    with pytest.raises(TaskEnd):
        task.run()

    assert len(pushed) == 1
    assert '不存在1' in pushed[0]['content'] and '不存在2' in pushed[0]['content']


@pytest.mark.unit
@pytest.mark.parametrize('changed', ['sub_task', 'account_source'])
def test_changing_sub_task_or_source_rebuilds_progress(tmp_path, monkeypatch, changed):
    """改子任务或改来源都必须重建进度：绝不能沿用另一组合留下的完成标记。"""
    _redirect_store(tmp_path, monkeypatch)
    items = [('oas1', _account('a@x.com', '甲'))]
    _stub_source(monkeypatch, items)

    # 预置「签到 + 勾选实例」组合下甲已完成
    store = ProgressStore('multi_tasks', 'oas1', base_dir=tmp_path)
    store.ensure_phase(_phase_flags(items), '20260817-1200')
    store.mark_account_done(acc_key('a@x.com', '甲', 's1'))

    if changed == 'sub_task':
        task = _make_task(sub_task=SubTaskType.EXPERIENCE_YOUKAI)
    else:
        task = _make_task(account_source=AccountSourceType.CHARACTERS)
    executed = _stub_execution(monkeypatch, task, {'甲': True})
    monkeypatch.setattr(task, 'set_next_run', lambda name, **k: None)

    with pytest.raises(TaskEnd):
        task.run()

    # 阶段标识变了 -> 进度重建 -> 甲必须重新执行
    assert executed == ['甲']


@pytest.mark.unit
def test_sorted_accounts_groups_by_email_preserving_first_seen_order():
    """同邮箱角色连续，组间与组内均保持首次出现顺序，不依赖 last_complete_time。"""
    from tasks.MultiTasks.script_task import _MultiTasksRunner

    a1 = _account('a@x.com', '甲')
    b1 = _account('b@x.com', '乙')
    a2 = _account('a@x.com', '丙')
    b2 = _account('b@x.com', '丁')
    # last_complete_time 故意打乱，确认它不参与排序
    a2.last_complete_time = datetime(2030, 1, 1)
    b1.last_complete_time = datetime(2029, 1, 1)

    runner = object.__new__(_MultiTasksRunner)
    runner.task_name = 'MultiTasks'
    runner.account_list = [a1, b1, a2, b2]
    runner.progress = None
    runner.need_login = True
    runner.login_time = datetime(2023, 1, 1)

    assert [a.character for a in runner.get_sorted_accounts()] == ['甲', '丙', '乙', '丁']
