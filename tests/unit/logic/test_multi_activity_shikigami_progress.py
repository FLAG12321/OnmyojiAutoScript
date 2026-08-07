# This Python file uses the following encoding: utf-8
"""MultiActivityShikigami 账号级续做进度测试。

run() 依赖跨 config 扫描与真实活动流程，这里全部打桩：
- ProgressStore 构造重定向到 tmp_path，避免污染仓库 config/tasks_config
- _load_execution_items / _notify_unmatched / _run_one_account / set_next_run 打桩

覆盖：已完成账号被跳过且进度保留、load_failure 时不清进度、全部成功后进度被清。
"""
from datetime import datetime
from types import SimpleNamespace

import pytest

from module.exception import TaskEnd
from tasks.Component.MultiAccountRunner.progress import ProgressStore, acc_key

PHASE_FLAGS = {'characters': ['甲', '乙'], 'day': '2026-08-07'}


def _make_task():
    from tasks.MultiActivityShikigami.script_task import ScriptTask

    task = object.__new__(ScriptTask)
    task.start_time = datetime(2026, 8, 7, 12, 0, 0)
    task.config = SimpleNamespace(
        config_name='oas1',
        multi_activity_shikigami=SimpleNamespace(
            multi_activity_shikigami_config=SimpleNamespace(account_characters='甲,乙'),
        ),
    )
    return task


def _account(account, character, svr):
    return SimpleNamespace(account=account, character=character, svr=svr)


def _redirect_store(tmp_path, monkeypatch):
    """把 script_task 模块内的 ProgressStore 构造重定向到 tmp_path。"""
    import tasks.MultiActivityShikigami.script_task as mod

    def factory(task_name, config_name, base_dir='config/tasks_config'):
        return ProgressStore(task_name, config_name, base_dir=tmp_path)

    monkeypatch.setattr(mod, 'ProgressStore', factory)


def _fresh_resumed(tmp_path):
    store = ProgressStore('multi_activity_shikigami', 'oas1', base_dir=tmp_path)
    resumed = store.ensure_phase(PHASE_FLAGS, 'x')
    return store, resumed


@pytest.mark.unit
def test_run_skips_done_account_and_preserves_progress(tmp_path, monkeypatch):
    import tasks.MultiActivityShikigami.script_task as mod

    _redirect_store(tmp_path, monkeypatch)
    task = _make_task()
    monkeypatch.setattr(task, '_load_execution_items',
                        lambda chars: ([( 'oas1', _account('a@x', '甲', 's1')),
                                        ('oas1', _account('b@x', '乙', 's1'))], [], False))
    monkeypatch.setattr(task, '_notify_unmatched', lambda unmatched: None)
    calls = []
    monkeypatch.setattr(task, '_run_one_account',
                        lambda source, account: calls.append(account.character) or False)
    monkeypatch.setattr(task, 'set_next_run', lambda *a, **k: None)

    # 预置甲账号为已完成，模拟中断后重跑
    store = ProgressStore('multi_activity_shikigami', 'oas1', base_dir=tmp_path)
    store.ensure_phase(PHASE_FLAGS, '20260807-1200')
    store.mark_account_done(acc_key('a@x', '甲', 's1'))

    with pytest.raises(TaskEnd):
        task.run()

    # 甲被跳过（run_one 只处理乙），乙失败；进度保留可接续
    assert calls == ['乙']
    resumed_store, resumed = _fresh_resumed(tmp_path)
    assert resumed is False
    assert resumed_store.is_account_done(acc_key('a@x', '甲', 's1')) is True
    assert resumed_store.is_account_done(acc_key('b@x', '乙', 's1')) is False


@pytest.mark.unit
def test_run_keeps_progress_when_load_failure(tmp_path, monkeypatch):
    import tasks.MultiActivityShikigami.script_task as mod

    _redirect_store(tmp_path, monkeypatch)
    task = _make_task()
    # load_failure=True：即使全部账号成功，也不能清进度（账号集合可能不完整）
    monkeypatch.setattr(task, '_load_execution_items',
                        lambda chars: ([( 'oas1', _account('a@x', '甲', 's1'))], [], True))
    monkeypatch.setattr(task, '_notify_unmatched', lambda unmatched: None)
    monkeypatch.setattr(task, '_run_one_account', lambda source, account: True)
    monkeypatch.setattr(task, 'set_next_run', lambda *a, **k: None)

    with pytest.raises(TaskEnd):
        task.run()

    resumed_store, resumed = _fresh_resumed(tmp_path)
    assert resumed is False
    assert resumed_store.is_account_done(acc_key('a@x', '甲', 's1')) is True


@pytest.mark.unit
def test_run_clears_progress_on_full_success(tmp_path, monkeypatch):
    import tasks.MultiActivityShikigami.script_task as mod

    _redirect_store(tmp_path, monkeypatch)
    task = _make_task()
    monkeypatch.setattr(task, '_load_execution_items',
                        lambda chars: ([( 'oas1', _account('a@x', '甲', 's1'))], [], False))
    monkeypatch.setattr(task, '_notify_unmatched', lambda unmatched: None)
    monkeypatch.setattr(task, '_run_one_account', lambda source, account: True)
    monkeypatch.setattr(task, 'set_next_run', lambda *a, **k: None)

    with pytest.raises(TaskEnd):
        task.run()

    # 全部成功收尾：先调度后清，进度文件应已删除
    resumed_store = ProgressStore('multi_activity_shikigami', 'oas1', base_dir=tmp_path)
    assert not resumed_store.path.exists()
