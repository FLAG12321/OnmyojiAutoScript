import types

from module.device.platform2.platform_windows import PlatformWindows


def build_platform(state):
    platform = object.__new__(PlatformWindows)
    platform.serial = "127.0.0.1:16608"
    platform.config = types.SimpleNamespace(
        script=types.SimpleNamespace(
            device=types.SimpleNamespace(
                emulator_window_minimize=False,
                run_background_only=False,
                handle="同心2",
            )
        )
    )

    def connect(serial):
        state["connect_calls"].append(serial)
        device = state["devices"].first_or_none()
        if device is not None:
            device.status = "device"

    def query_mumu_state():
        states = state["mumu_states"]
        if len(states) > 1:
            return states.pop(0)
        return states[0]

    platform.list_device = lambda: state["devices"]
    platform.adb_client = types.SimpleNamespace(
        connect=connect,
        disconnect=lambda serial: state["disconnect_calls"].append(serial),
    )
    platform.adb_shell = lambda cmd: "pong"
    platform.list_app_packages = lambda show_log=False: ["com.netease.onmyoji"]
    platform._query_mumu12_state = query_mumu_state
    return platform


class FakeTimer:
    instances = []
    reached_schedule = {
        180: [False, False, False],
        10: [False, True],
        30: [True, True],
        120: [False, False],
        60: [False, True],
    }

    def __init__(self, limit, count=0):
        self.limit = limit
        self.count = count
        self.calls = 0
        self._started = False
        self._remain = 150.0
        FakeTimer.instances.append(self)

    def start(self):
        self._started = True
        return self

    def wait(self):
        return self

    def reset(self):
        return self

    def started(self):
        return self._started

    def reached(self):
        self.calls += 1
        schedule = self.reached_schedule.get(self.limit)
        if schedule is None:
            return False
        index = min(self.calls - 1, len(schedule) - 1)
        return schedule[index]

    def remain(self):
        self._remain -= 1.0
        return self._remain


class FakeSelectedDevices:
    def __init__(self, device):
        self.device = device

    def select(self, **kwargs):
        serial = kwargs.get("serial")
        if self.device and serial == self.device.serial:
            return self
        return FakeSelectedDevices(None)

    def first_or_none(self):
        return self.device

    def __bool__(self):
        return self.device is not None


class FakeDevice:
    def __init__(self, serial, status):
        self.serial = serial
        self.status = status

    def __str__(self):
        return f"AdbDevice({self.serial}, {self.status})"


def test_emulator_start_watch_keeps_waiting_during_mumu_startup_grace(monkeypatch):
    FakeTimer.instances = []
    state = {
        "devices": FakeSelectedDevices(FakeDevice("127.0.0.1:16608", "offline")),
        "mumu_states": [
            {
                "player_state": "starting",
                "is_process_started": False,
            },
            {
                "player_state": "start_finished",
                "is_process_started": True,
            },
        ],
        "connect_calls": [],
        "disconnect_calls": [],
    }
    platform = build_platform(state)

    monkeypatch.setattr("module.device.platform2.platform_windows.Timer", FakeTimer)
    monkeypatch.setattr("module.device.platform2.platform_windows.get_focused_window", lambda: 0)
    monkeypatch.setattr("module.device.platform2.platform_windows.set_focus_window", lambda hwnd: None)
    monkeypatch.setattr("module.device.platform2.platform_windows.Handle.handle_has_children", lambda hwnd: True)

    assert platform.emulator_start_watch() is True
    assert state["disconnect_calls"] == ["127.0.0.1:16608"]
    assert state["connect_calls"] == ["127.0.0.1:16608"]
    grace_timer = next(timer for timer in FakeTimer.instances if timer.limit == 60)
    assert grace_timer.calls == 1
