# This Python file uses the following encoding: utf-8
"""MultiAccountRunner 账号级进度集成测试。

覆盖 progress=None 时旧行为不变、progress 非空时已完成账号被跳过、
处理成功即时落盘、设备级异常穿透（不被 except Exception 吞成 False）。
纯文件读写 + monkeypatch，不依赖真实设备。
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from tasks.Component.MultiAccountRunner.multi_account_runner import MultiAccountRunner
from tasks.Component.MultiAccountRunner.progress import ProgressStore, acc_key


def _runner(store=None, need_login=True, login_time=None, config_name='oas1',
            on_account_error=None):
    config = SimpleNamespace(config_name=config_name)
    device = SimpleNamespace()
    if login_time is None:
        login_time = datetime.now()
    return MultiAccountRunner(
        task_name='test_runner',
        config=config,
        device=device,
        account_list=[],
        need_login=need_login,
        login_time=login_time,
        update_login_history_func=lambda *a, **k: None,
        save_config_func=lambda *a, **k: None,
        on_account_error=on_account_error,
        progress=store,
    )


def _account(character='小号一', svr='两情相悦', account='mail@x.com',
             last_complete_time=None):
    if last_complete_time is None:
        last_complete_time = datetime(2023, 1, 1, 0, 0, 0)
    return SimpleNamespace(account=account, character=character, svr=svr,
                           last_complete_time=last_complete_time)


@pytest.mark.unit
def test_without_progress_need_login_true_processes_all():
    runner = _runner(need_login=True)
    # 旧行为：need_login 为 True 时所有账号都需要处理
    assert runner.should_process_account(_account()) is True


@pytest.mark.unit
def test_without_progress_uses_login_time():
    now = datetime.now()
    runner = _runner(need_login=False, login_time=now)
    # 完成时间早于 login_time：需要处理
    assert runner.should_process_account(_account(last_complete_time=now - timedelta(hours=1))) is True
    # 完成时间晚于 login_time：已完成，跳过
    assert runner.should_process_account(_account(last_complete_time=now + timedelta(hours=1))) is False


@pytest.mark.unit
def test_with_progress_skips_done_account(tmp_path):
    store = ProgressStore('test_runner', 'oas1', base_dir=tmp_path)
    store.ensure_phase({'dummy': 1}, '20260730-0020')
    store.mark_account_done(acc_key('mail@x.com', '小号一', '两情相悦'))
    runner = _runner(store=store, need_login=True)
    # progress 非空时完成判定改由进度文件驱动，need_login 不再生效
    assert runner.should_process_account(_account(character='小号一')) is False
    assert runner.should_process_account(_account(character='小号二')) is True


@pytest.mark.unit
def test_process_success_marks_account_done(tmp_path):
    store = ProgressStore('test_runner', 'oas1', base_dir=tmp_path)
    store.ensure_phase({'dummy': 1}, '20260730-0020')
    runner = _runner(store=store)
    account = _account()
    # process_func 返回 True 视为成功，应即时落盘
    assert runner._process_account_with_retry(account, lambda a: True) is True
    assert store.is_account_done(acc_key('mail@x.com', '小号一', '两情相悦')) is True


@pytest.mark.unit
def test_device_level_error_propagates(tmp_path):
    from module.exception import GameStuckError

    store = ProgressStore('test_runner', 'oas1', base_dir=tmp_path)
    store.ensure_phase({'dummy': 1}, '20260730-0020')
    called = []
    runner = _runner(store=store, on_account_error=lambda a, e: called.append(e))

    def boom(a):
        raise GameStuckError('Wait too long')

    # 设备级异常必须原样上抛到 script.py 的 Restart 逻辑，不能吞成 return False
    with pytest.raises(GameStuckError):
        runner._process_account_with_retry(_account(), boom)
    assert called == []
    assert store.is_account_done(acc_key('mail@x.com', '小号一', '两情相悦')) is False


@pytest.mark.unit
def test_generic_error_returns_false_and_calls_callback(tmp_path):
    store = ProgressStore('test_runner', 'oas1', base_dir=tmp_path)
    store.ensure_phase({'dummy': 1}, '20260730-0020')
    called = []
    runner = _runner(store=store, on_account_error=lambda a, e: called.append(e))

    def boom(a):
        raise ValueError('bad')

    assert runner._process_account_with_retry(_account(), boom) is False
    assert len(called) == 1


@pytest.mark.unit
def test_run_skips_done_and_processes_pending(tmp_path, monkeypatch):
    store = ProgressStore('test_runner', 'oas1', base_dir=tmp_path)
    store.ensure_phase({'dummy': 1}, '20260730-0020')
    done_key = acc_key('a@x.com', '已完', 's1')
    store.mark_account_done(done_key)

    runner = _runner(store=store, config_name='oas1')
    runner.account_list = [
        _account(account='a@x.com', character='已完', svr='s1'),
        _account(account='b@x.com', character='待做', svr='s2'),
    ]
    processed = []

    def process(account):
        processed.append(account.character)
        return True

    # 运行态标记文件指向 tmp，避免污染仓库 config/tasks_config
    runner_path = tmp_path / 'runner_progress.json'
    monkeypatch.setattr(MultiAccountRunner, '_get_progress_file',
                        lambda self: runner_path)
    assert runner.run(process_func=process) is True
    # 已完成账号不被 process_func 再次处理，待做账号被处理并落盘
    assert processed == ['待做']
    assert store.is_account_done(acc_key('b@x.com', '待做', 's2')) is True
