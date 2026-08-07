# This Python file uses the following encoding: utf-8
"""MasterDisciple 徒弟轮询续做与调度修复测试。

run() / run_as_disciple 依赖真实设备与大量游戏 UI 流程，这里全部打桩：
- ProgressStore 构造重定向到 tmp_path
- 设备/UI 相关方法（screenshot、ui_goto 等）与子任务执行打桩

覆盖：异常上抛时按 success=False 调度、已完成徒弟被跳过、全部成功后进度被清。
"""
from datetime import datetime
from types import SimpleNamespace

import pytest

from module.exception import TaskEnd
from tasks.Component.MultiAccountRunner.progress import ProgressStore, acc_key

DISCIPLES = [
    ('d1@x.com', '徒一', 's1'),
    ('d2@x.com', '徒二', 's2'),
]
PHASE_FLAGS = {'disciples': [acc_key(*d) for d in DISCIPLES], 'day': '2026-08-07'}


def _make_task():
    from tasks.MasterDisciple.script_task import ScriptTask
    from tasks.MasterDisciple.config import MasterDiscipleMode

    task = object.__new__(ScriptTask)
    task.start_time = datetime(2026, 8, 7, 12, 0, 0)
    task.config = SimpleNamespace(
        config_name='oas1',
        master_disciple=SimpleNamespace(
            disciple_account_list=[SimpleNamespace(account=a, character=c, svr=s)
                                   for a, c, s in DISCIPLES],
            master_disciple_config=SimpleNamespace(
                limit_count=10,
                limit_time=SimpleNamespace(hour=1, minute=0, second=0),
                mode=MasterDiscipleMode.DISCIPLE,
                cycle_all_disciples=True,
                auto_switch_account=True,
                run_exploration=False,
                run_exp_monster=False,
                run_stone_ju=False,
                run_coin_monster=False,
                run_guard=False,
            ),
        ),
    )
    # 设备/UI 打桩：run() 开头会截图并回庭院
    task.device = SimpleNamespace(stuck_record_clear=lambda: None)
    task.screenshot = lambda *a, **k: None
    task.ui_get_current_page = lambda *a, **k: None
    task.ui_goto = lambda *a, **k: True
    return task


def _redirect_store(tmp_path, monkeypatch):
    """把 script_task 模块内的 ProgressStore 构造重定向到 tmp_path。"""
    import tasks.MasterDisciple.script_task as mod

    def factory(task_name, config_name, base_dir='config/tasks_config'):
        return ProgressStore(task_name, config_name, base_dir=tmp_path)

    monkeypatch.setattr(mod, 'ProgressStore', factory)


@pytest.mark.unit
def test_run_exception_schedules_failure(monkeypatch):
    task = _make_task()
    scheduled = []
    monkeypatch.setattr(task, 'set_next_run', lambda task, **k: scheduled.append(k))
    monkeypatch.setattr(task, 'run_as_disciple', lambda: (_ for _ in ()).throw(ValueError('boom')))

    with pytest.raises(ValueError):
        task.run()

    # 异常上抛前成功置 False：按失败间隔调度，而不是被 finally 误判为成功
    failure_calls = [s for s in scheduled if s.get('success') is False]
    assert len(failure_calls) == 1
    assert failure_calls[0]['finish'] is False


@pytest.mark.unit
def test_run_clears_progress_on_success(tmp_path, monkeypatch):
    _redirect_store(tmp_path, monkeypatch)
    task = _make_task()
    scheduled = []
    monkeypatch.setattr(task, 'set_next_run', lambda task, **k: scheduled.append(k))
    monkeypatch.setattr(task, 'run_as_disciple', lambda: True)
    # 预置已完成全部徒弟的进度，模拟上一轮已跑完
    store = ProgressStore('master_disciple', 'oas1', base_dir=tmp_path)
    store.ensure_phase(PHASE_FLAGS, '20260807-1200')
    for _, c, s in DISCIPLES:
        store.mark_account_done(acc_key(*[d for d in DISCIPLES if d[1] == c][0]))
    task._progress = store

    with pytest.raises(TaskEnd):
        task.run()

    # 成功收尾后进度被清
    assert not store.path.exists()
    assert any(s.get('success') is True for s in scheduled)


@pytest.mark.unit
def test_run_as_disciple_skips_done_account(tmp_path, monkeypatch):
    import tasks.MasterDisciple.script_task as mod

    _redirect_store(tmp_path, monkeypatch)
    task = _make_task()
    executed = []
    # 预置徒一已完成，模拟中断后重跑
    store = ProgressStore('master_disciple', 'oas1', base_dir=tmp_path)
    store.ensure_phase(PHASE_FLAGS, '20260807-1200')
    store.mark_account_done(acc_key('d1@x.com', '徒一', 's1'))
    task._progress = store

    monkeypatch.setattr(task, '_execute_disciple_tasks',
                        lambda: executed.append(task._current_disciple_character))

    def switch(account):
        task._current_disciple_character = account.character
        return True

    monkeypatch.setattr(task, 'switch_to_disciple_account', switch)

    result = task.run_as_disciple()

    assert result is True
    # 徒一已完成被跳过，只执行徒二
    assert executed == ['徒二']
    # 重新读取进度文件确认徒二已落盘（原 store 实例的 _data 已过期）
    fresh = ProgressStore('master_disciple', 'oas1', base_dir=tmp_path)
    fresh.ensure_phase(PHASE_FLAGS, 'x')
    assert fresh.is_account_done(acc_key('d2@x.com', '徒二', 's2')) is True
