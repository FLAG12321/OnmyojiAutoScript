import pytest
from datetime import datetime, timedelta

from module.exception import GameNotRunningError, EmulatorNotRunningError
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
        self.close_game_wait_duration = type('TimeValue', (), {'hour': 0, 'minute': 10, 'second': 0})()
        self.close_emulator_wait_duration = type('TimeValue', (), {'hour': 0, 'minute': 30, 'second': 0})()


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


class FakeHealth:
    def __init__(self):
        self.is_alive_calls = 0

    def is_alive(self):
        self.is_alive_calls += 1
        return True

    def why_dead(self):
        return 'fake healthy'


class FakeDevice:
    def __init__(self, status='device'):
        self.serial = '127.0.0.1:16384'
        self.status = status
        self.emulator_stop_calls = 0
        self.full_recovery_calls = 0
        self.health = FakeHealth()

    def detect_emulator_status(self, serial):
        assert serial == self.serial
        return self.status

    def emulator_stop(self):
        self.emulator_stop_calls += 1

    def full_recovery(self):
        self.full_recovery_calls += 1
        return True

    def stuck_record_clear(self):
        return None

    def click_record_clear(self):
        return None

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


def test_loop_runs_full_recovery_when_recovery_requested_before_task(monkeypatch):
    script = Script('oas1')
    restart_task = FakeWaitingTask(datetime.now() - timedelta(seconds=1))
    restart_task.command = 'Restart'
    script.config = FakeQueueConfig(restart_task)
    script.device = FakeDevice(status='device')
    script._needs_recovery = True
    script.is_first_task = False

    run_calls = []

    def fake_run(command):
        run_calls.append(command)
        raise SystemExit(0)

    monkeypatch.setattr(script, 'run', fake_run)

    with pytest.raises(SystemExit):
        script.loop()

    assert script.device.full_recovery_calls == 1
    assert script._needs_recovery is False
    assert run_calls == ['Restart']


def test_run_schedules_restart_for_game_not_running_without_health_probe():
    script = make_script_with_device(status='device')

    def raise_game_not_running():
        raise GameNotRunningError('Game not running')

    script.device.screenshot = raise_game_not_running
    script.device.app_is_running = lambda: True

    success = script.run('ReturnGift')

    assert success is False
    assert script._needs_recovery is False
    assert script.device.health.is_alive_calls == 0
    assert script.config.called_tasks == ['Restart']


def test_run_schedules_restart_and_recovery_for_emulator_not_running():
    script = make_script_with_device(status='device')

    def raise_emulator_not_running():
        raise EmulatorNotRunningError('emulator is not running')

    script.device.screenshot = raise_emulator_not_running
    script.device.app_is_running = lambda: True

    success = script.run('ReturnGift')

    assert success is False
    assert script._needs_recovery is True
    assert script.config.called_tasks == ['Restart']


def test_nemu_retry_converts_nemu_error_to_emulator_not_running():
    from module.device.method import nemu_ipc

    calls = []

    class DummyImpl:
        def reconnect(self):
            calls.append('reconnect')

    @nemu_ipc.retry
    def broken(self):
        raise NemuIpcError('emulator instance is probably dead')

    with pytest.raises(EmulatorNotRunningError, match='NemuIpc unavailable during broken'):
        broken(DummyImpl())

    assert calls
