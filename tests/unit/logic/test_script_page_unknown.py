# This Python file uses the following encoding: utf-8
"""GamePageUnknownError 连续失败兜底测试。

覆盖：
1. 连续 3 次页面未识别 → task_call('Restart') 触发一次并清零计数
2. 未满 3 次（1~2 次）不触发重启
3. 中途任务正常结束（TaskEnd）→ 计数清零，之后重新累计
"""
import pytest
from types import SimpleNamespace

from module.exception import GamePageUnknownError, GameNotRunningError, TaskEnd


class _FakeScriptTask:
    """假任务类：按类属性预设的异常抛出，模拟页面识别失败/成功"""
    exc = None

    def __init__(self, config, device):
        pass

    def run(self):
        raise self.exc()


def _make_script(monkeypatch):
    """构造绕过 __init__ 的 Script 实例并 mock 掉外部依赖。

    command 用 'Restart'（在 SKIP_APP_CHECK_TASKS 里），绕开 device 截图检查。
    """
    from script import Script

    monkeypatch.setattr(Script, 'save_error_log', lambda *a, **k: None)
    monkeypatch.setattr(Script, '_resolve_task_end_name', lambda self, command, e: command)
    monkeypatch.setattr(Script, '_should_notify_task_end', lambda self, name: False)

    calls = []
    fake_config = SimpleNamespace(
        notifier=SimpleNamespace(push=lambda **kw: calls.append(('push', kw))),
        task_call=lambda name: calls.append(('task_call', name)),
    )
    fake_module = SimpleNamespace(ScriptTask=_FakeScriptTask)
    monkeypatch.setattr('script.load_module', lambda name, path: fake_module)

    s = object.__new__(Script)
    s.config = fake_config
    # device 需满足 loop() 的 stuck_record_clear/click_record_clear 调用，
    # 假任务本身不使用 device
    s.device = SimpleNamespace(stuck_record_clear=lambda: None,
                               click_record_clear=lambda: None)
    s.config_name = 'test'
    s.page_unknown_count = 0
    s._page_unknown_recoverable = False
    # loop() 路径需要的最小属性
    s.is_first_task = False
    s.failure_record = {}
    s.recovery_failure_count = 0
    s._needs_recovery = False
    return s, calls


@pytest.mark.unit
def test_below_threshold_no_restart(monkeypatch):
    """连续 2 次未识别不触发重启，计数保留"""
    s, calls = _make_script(monkeypatch)
    _FakeScriptTask.exc = GamePageUnknownError
    assert s._run_task('Restart') is False
    assert s._run_task('Restart') is False
    assert s.page_unknown_count == 2
    assert not [c for c in calls if c[0] == 'task_call']


@pytest.mark.unit
def test_restart_on_third_unknown(monkeypatch):
    """第 3 次未识别触发 task_call('Restart') 并清零计数"""
    s, calls = _make_script(monkeypatch)
    _FakeScriptTask.exc = GamePageUnknownError
    s._run_task('Restart')
    s._run_task('Restart')
    assert s._run_task('Restart') is False
    restarts = [c for c in calls if c[0] == 'task_call' and c[1] == 'Restart']
    assert len(restarts) == 1
    assert s.page_unknown_count == 0


@pytest.mark.unit
def test_reset_on_task_end(monkeypatch):
    """3 次内任务正常结束则清零计数，重新累计"""
    s, calls = _make_script(monkeypatch)
    _FakeScriptTask.exc = GamePageUnknownError
    s._run_task('Restart')
    s._run_task('Restart')
    assert s.page_unknown_count == 2
    # 下一个任务正常结束（TaskEnd 被捕获 → 返回 True）
    _FakeScriptTask.exc = TaskEnd
    assert s._run_task('Restart') is True
    assert s.page_unknown_count == 0
    # 清零后再失败 2 次仍不触发重启
    _FakeScriptTask.exc = GamePageUnknownError
    s._run_task('Restart')
    s._run_task('Restart')
    assert s.page_unknown_count == 2
    assert not [c for c in calls if c[0] == 'task_call']


@pytest.mark.unit
def test_loop_no_stall_on_page_unknown(monkeypatch):
    """handle_error=False 时连续 page unknown 不停摆：loop 靠放行标志继续调度。

    用哨兵异常在第 4 次 get_next_task 时终止 loop——若 loop 意外 break 停摆，
    哨兵不会触发，断言失败。
    """
    from script import Script

    s, calls = _make_script(monkeypatch)
    # handle_error=False：普通失败会 break，page unknown 靠放行标志继续
    s.config.script = SimpleNamespace(error=SimpleNamespace(handle_error=False))
    monkeypatch.setattr(Script, '_config_checkpoint', lambda self, trigger: None)
    monkeypatch.setattr('script.logger.set_file_logger', lambda name: None)

    next_calls = {'n': 0}

    def fake_get_next_task(self):
        next_calls['n'] += 1
        if next_calls['n'] > 3:
            raise RuntimeError('loop-terminated')  # 哨兵：证明 loop 仍在转
        return 'Restart'

    monkeypatch.setattr(Script, 'get_next_task', fake_get_next_task)
    # 绕开 run() 的 OCR RPC 活跃保持包装，直接走真实异常分支
    monkeypatch.setattr(Script, 'run', lambda self, command: self._run_task('Restart'))

    _FakeScriptTask.exc = GamePageUnknownError
    with pytest.raises(RuntimeError, match='loop-terminated'):
        s.loop()

    # 3 轮 page unknown 全部放行，第 3 轮触发 Restart 并清零计数
    assert next_calls['n'] == 4
    restarts = [c for c in calls if c[0] == 'task_call' and c[1] == 'Restart']
    assert len(restarts) == 1
    assert s.page_unknown_count == 0


@pytest.mark.unit
def test_loop_still_stalls_on_other_failures(monkeypatch):
    """handle_error=False 时其他失败仍按原逻辑 break：只对 page unknown 放行"""
    from script import Script

    s, _ = _make_script(monkeypatch)
    s.config.script = SimpleNamespace(error=SimpleNamespace(handle_error=False))
    monkeypatch.setattr(Script, '_config_checkpoint', lambda self, trigger: None)
    monkeypatch.setattr('script.logger.set_file_logger', lambda name: None)

    next_calls = {'n': 0}

    def fake_get_next_task(self):
        next_calls['n'] += 1
        return 'Restart'

    monkeypatch.setattr(Script, 'get_next_task', fake_get_next_task)
    monkeypatch.setattr(Script, 'run', lambda self, command: self._run_task('Restart'))

    _FakeScriptTask.exc = GameNotRunningError
    s.loop()  # 第一轮失败后 break 正常退出，不抛哨兵
    assert next_calls['n'] == 1  # 第二轮 get_next_task 未被调用，确认已停摆
