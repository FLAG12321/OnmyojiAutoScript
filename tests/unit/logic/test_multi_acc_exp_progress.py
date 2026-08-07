# This Python file uses the following encoding: utf-8
"""MultiAccExp 续做进度与 need_login 退役回归测试。

核心回归点：旧版在一次账号异常后会把 need_login 置 False 而 need_login_time 不变
（仍为默认 2023-01-01），导致「从未跑过的账号 last_complete_time 默认值也 >= 该时间」
被误判为已完成，下一轮全部账号被过滤、任务永久空转且无人能翻回 need_login。
新版完成判定改由进度文件驱动：只有真正完成的账号被跳过，报错账号下一轮照常处理。

本测试直接驱动 MultiAccountRunner（MultiAccExp 实际使用的执行器），
纯文件读写（ProgressStore 指向 tmp_path）+ SimpleNamespace，不碰真实设备。
"""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tasks.Component.MultiAccountRunner.multi_account_runner import MultiAccountRunner
from tasks.Component.MultiAccountRunner.progress import ProgressStore, acc_key

PHASE_FLAGS = {
    'accounts': [acc_key('a@x.com', '甲', 's1'), acc_key('b@x.com', '乙', 's2')],
    'day': '2026-08-07',
}


def _account(account, character, svr):
    return SimpleNamespace(account=account, character=character, svr=svr,
                           last_complete_time=datetime(2023, 1, 1, 0, 0, 0))


def _make_runner(store, account_list):
    return MultiAccountRunner(
        task_name='MultiAccExp',
        config=SimpleNamespace(config_name='oas1'),
        device=SimpleNamespace(),
        account_list=account_list,
        need_login=False,
        login_time=datetime(2026, 8, 7, 12, 0, 0),
        update_login_history_func=lambda *a, **k: None,
        save_config_func=lambda *a, **k: None,
        on_account_error=lambda a, e: None,
        progress=store,
    )


@pytest.mark.unit
def test_pending_account_processed_after_error(tmp_path):
    """账号报错后进度保留 pending，下一轮接续处理而不是被过滤（旧 bug 回归点）。"""
    store = ProgressStore('multi_acc_exp', 'oas1', base_dir=tmp_path)
    store.ensure_phase(PHASE_FLAGS, '20260807-1200')
    account_a = _account('a@x.com', '甲', 's1')
    account_b = _account('b@x.com', '乙', 's2')
    account_list = [account_a, account_b]

    # 第 1 轮：甲成功，乙抛业务异常
    runner1 = _make_runner(store, account_list)

    def process_round1(account):
        if account.character == '甲':
            return True
        raise ValueError('boom')

    assert runner1._process_account_with_retry(account_a, process_round1) is True
    assert runner1._process_account_with_retry(account_b, process_round1) is False

    # 第 2 轮（新实例，同一进度文件）：甲已完成被跳过，乙仍未完成需接续处理
    runner2 = _make_runner(store, account_list)
    assert runner2.should_process_account(account_a) is False
    assert runner2.should_process_account(account_b) is True
    assert runner2._process_account_with_retry(account_b, lambda a: True) is True
    assert store.is_account_done(acc_key('b@x.com', '乙', 's2')) is True


@pytest.mark.unit
def test_on_account_error_no_longer_writes_need_login():
    """_on_account_error 不再改写 need_login，这是永久空转的根因。"""
    source = Path('tasks/MultiAccExp/script_task.py').read_text(encoding='utf-8')
    assert 'need_login = False' not in source
    assert 'need_login_time = ' not in source


@pytest.mark.unit
def test_need_login_fields_deprecated_in_config():
    """保留字段兼容老配置，但描述必须标明已弃用，避免用户以为还有效。"""
    from tasks.MultiAccExp.config import MultiAccExpConfig

    fields = MultiAccExpConfig.model_fields
    assert '已弃用' in fields['need_login'].description
    assert '已弃用' in fields['need_login_time'].description
