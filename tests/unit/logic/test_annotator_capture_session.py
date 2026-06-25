from module.server import tool


class FakeThread:
    created = []

    def __init__(self, target=None, daemon=None, name=None):
        self.target = target
        self.daemon = daemon
        self.name = name
        self.started = False
        self.join_calls = 0
        FakeThread.created.append(self)

    def start(self):
        self.started = True

    def is_alive(self):
        return self.started

    def join(self, timeout=None):
        self.join_calls += 1
        self.started = False


def test_emulator_capture_start_is_idempotent_for_same_running_request(monkeypatch):
    # 同一会话、同一配置重复启动时应复用现有采集线程，避免反复 stop/start 放大后台 error22。
    FakeThread.created = []
    monkeypatch.setattr(tool.threading, "Thread", FakeThread)
    session = tool.EmulatorCaptureSession("session-1")

    first_rate = session.start("oas1", 2)
    second_rate = session.start("oas1", 2)

    assert first_rate == 2
    assert second_rate == 2
    assert len(FakeThread.created) == 1
    assert FakeThread.created[0].join_calls == 0


def test_emulator_capture_start_restarts_when_request_changes(monkeypatch):
    # 配置或帧率变化代表用户切换采集目标，应保留原有重启行为。
    FakeThread.created = []
    monkeypatch.setattr(tool.threading, "Thread", FakeThread)
    session = tool.EmulatorCaptureSession("session-1")

    session.start("oas1", 2)
    changed_rate = session.start("oas1", 3)

    assert changed_rate == 3
    assert len(FakeThread.created) == 2
    assert FakeThread.created[0].join_calls == 1
