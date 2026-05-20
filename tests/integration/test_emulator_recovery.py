import pytest
from datetime import datetime, timedelta

from module.exception import GameNotRunningError
from module.device.method.nemu_ipc import NemuIpcError
from script import Script


class FakeConfig:
    def __init__(self):
        self.called_tasks = []

    def task_call(self, task: str):
        self.called_tasks.append(task)


class FakeWaitingTask:
    def __init__(self, next_run):
        self.command = 'ReturnGift'
        self.next_run = next_run


class FakeOptimization:
    def __init__(self, method='close_emulator_or_close_game'):
        self.when_task_queue_empty = method
        self.close_game_limit_time = type('TimeValue', (), {'hour': 0, 'minute': 10, 'second': 0})()
        self.close_emulator_limit_time = type('TimeValue', (), {'hour': 0, 'minute': 30, 'second': 0})()


class FakeScriptConfig:
    def __init__(self, task):
        self.optimization = FakeOptimization()
        self.error = type('ErrorConfig', (), {'handle_error': True})()
        self.task = None
        self.pending_task = []
        self.waiting_task = [task]

    def start_watching(self):
        return None

    def should_reload(self):
        return False


class FakeQueueConfig:
    def __init__(self, task):
        self._task = task
        self.script = FakeScriptConfig(task)
        self.task = None

    def get_next(self):
        return self._task

    def get_schedule_data(self):
        return {}


class FakeIdleDevice:
    def __init__(self):
        self.release_during_wait_calls = 0

    def release_during_wait(self):
        self.release_during_wait_calls += 1


class FakeDevice:
    def __init__(self, status='device'):
        self.serial = '127.0.0.1:16384'
        self.status = status
        self.emulator_stop_calls = 0

    def detect_emulator_status(self, serial):
        assert serial == self.serial
        return self.status

    def emulator_stop(self):
        self.emulator_stop_calls += 1

def make_script_with_device(status='device'):
    script = Script('oas1')
    script.config = FakeConfig()
    script.device = FakeDevice(status=status)
    return script


def test_get_next_task_closes_emulator_during_idle_even_without_cached_device(monkeypatch):
    future_task = FakeWaitingTask(datetime.now() + timedelta(hours=1))
    script = Script('oas1')
    script.config = FakeQueueConfig(future_task)

    idle_device = FakeIdleDevice()
    close_calls = []

    def fake_close(self, task, close_game_limit_time, close_emulator_limit_time, method):
        close_calls.append((task.command, method))

    monkeypatch.setattr(Script, '_handle_close_emulator_or', fake_close)
    monkeypatch.setattr(Script, 'device', property(lambda self: idle_device))
    monkeypatch.setattr(Script, 'wait_until', lambda self, future: True)

    task_name = script.get_next_task()

    assert task_name == 'ReturnGift'
    assert close_calls == [('ReturnGift', 'close_emulator_or_close_game')]
    assert idle_device.release_during_wait_calls == 1


def test_refresh_emulator_state_marks_emulator_down_when_status_offline():
    script = make_script_with_device(status='offline')

    script._refresh_emulator_state_before_task_start()

    assert script._emulator_down is True


def test_refresh_emulator_state_keeps_emulator_up_when_status_device():
    script = make_script_with_device(status='device')

    script._refresh_emulator_state_before_task_start()

    assert script._emulator_down is False


def test_run_restarts_when_nemu_ipc_reports_game_not_running():
    script = make_script_with_device(status='device')

    def raise_nemu_error():
        raise GameNotRunningError('NemuIpc unavailable during screenshot(): emulator closed')

    script.device.screenshot = raise_nemu_error
    script.device.app_is_running = lambda: True

    success = script.run('ReturnGift')

    assert success is False
    assert script._emulator_down is True
    assert script.device.emulator_stop_calls == 1
    assert script.config.called_tasks == ['Restart']


def test_nemu_retry_converts_nemu_error_to_game_not_running():
    from module.device.method import nemu_ipc

    calls = []

    class DummyImpl:
        def reconnect(self):
            calls.append('reconnect')

    @nemu_ipc.retry
    def broken(self):
        raise NemuIpcError('emulator instance is probably dead')

    with pytest.raises(GameNotRunningError, match='NemuIpc unavailable during broken'):
        broken(DummyImpl())

    assert calls
