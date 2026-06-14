import types

from module.device.platform2.emulator_base import EmulatorInstanceBase
from module.device.platform2.emulator_windows import Emulator
from module.device.platform2.platform_windows import PlatformWindows


class FakeEmulatorInstance(EmulatorInstanceBase):
    @property
    def type(self):
        return "MuMuPlayer12"


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

    platform.emulator_instance = Emulator.MuMuPlayer12
    platform.list_device = lambda: state["devices"]
    platform.adb_client = types.SimpleNamespace(
        connect=connect,
        disconnect=lambda serial: state["disconnect_calls"].append(serial),
    )
    platform.adb_shell = lambda cmd: "pong"
    platform.list_app_packages = lambda show_log=False: ["com.netease.onmyoji"]
    platform._query_mumu12_state = query_mumu_state
    return platform


DEFAULT_REACHED_SCHEDULE = {
    180: [False, False, False],
    10: [False, True],
    15: [True, True],
    90: [False, False],
    30: [False, False],
}


class FakeTimer:
    instances = []
    reached_schedule = DEFAULT_REACHED_SCHEDULE

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


def reset_fake_timer():
    """重置 FakeTimer，避免单测之间共享计时状态。"""
    FakeTimer.reached_schedule = {
        limit: values.copy()
        for limit, values in DEFAULT_REACHED_SCHEDULE.items()
    }
    FakeTimer.instances = []


def test_find_emulator_instance_uses_config_for_bridged_mumu12():
    # 桥接模式: serial 为局域网 IP, 配置填全时直接用配置构造实例, 完全不依赖本机枚举
    platform = object.__new__(PlatformWindows)
    platform.serial = "192.168.1.214:5555"
    # 故意置空枚举结果, 证明桥接场景不读取 all_emulator_instances
    platform.all_emulator_instances = []

    instance = platform.find_emulator_instance(
        serial="192.168.1.214:5555",
        name="MuMuPlayer-12.0-0",
        path="I:/Program Files/Netease/MuMu/nx_main/MuMuNxMain.exe",
        emulator="MuMuPlayer12",
    )

    assert instance.serial == "192.168.1.214:5555"
    assert instance.name == "MuMuPlayer-12.0-0"
    assert instance.path == "I:/Program Files/Netease/MuMu/nx_main/MuMuNxMain.exe"
    assert instance.type == Emulator.MuMuPlayer12
    assert instance.MuMuPlayer12_id == 0


def test_find_emulator_instance_uses_config_for_local_nat_mumu12():
    # 本机 NAT(127.0.0.1) 同样信任配置: 类型非 auto 且 name/path 填全即直接构造, 不枚举
    platform = object.__new__(PlatformWindows)
    platform.serial = "127.0.0.1:16384"
    platform.all_emulator_instances = []

    instance = platform.find_emulator_instance(
        serial="127.0.0.1:16384",
        name="MuMuPlayer-12.0-1",
        path="I:/Program Files/Netease/MuMu/nx_main/MuMuNxMain.exe",
        emulator="MuMuPlayer12",
    )

    assert instance.serial == "127.0.0.1:16384"
    assert instance.name == "MuMuPlayer-12.0-1"
    assert instance.type == Emulator.MuMuPlayer12
    assert instance.MuMuPlayer12_id == 1


def test_find_emulator_instance_falls_back_to_enumeration_when_auto():
    # 类型为 auto(emulator 为空)时回退到原有枚举探测, 按 serial/id 匹配已枚举实例
    platform = object.__new__(PlatformWindows)
    platform.serial = "127.0.0.1:16384"
    platform.all_emulator_instances = [
        FakeEmulatorInstance(
            serial="127.0.0.1:16384",
            name="MuMuPlayer-12.0-0",
            path="I:/Program Files/Netease/MuMu/nx_main/MuMuNxMain.exe",
        ),
        FakeEmulatorInstance(
            serial="127.0.0.1:16416",
            name="MuMuPlayer-12.0-1",
            path="I:/Program Files/Netease/MuMu/nx_main/MuMuNxMain.exe",
        ),
    ]

    instance = platform.find_emulator_instance(
        serial="127.0.0.1:16384",
        name=None,
        path=None,
        emulator=None,
    )

    assert instance is platform.all_emulator_instances[0]


def test_emulator_start_watch_uses_180s_recovery_time_budget(monkeypatch):
    reset_fake_timer()
    state = {
        "devices": FakeSelectedDevices(FakeDevice("127.0.0.1:16608", "device")),
        "mumu_states": [
            {
                "player_state": "starting",
                "is_process_started": True,
            }
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
    limits = [timer.limit for timer in FakeTimer.instances]
    assert 180 in limits
    assert 90 in limits
    assert 45 in limits
    assert 30 in limits
    assert 300 not in limits
    assert 200 not in limits
    assert 100 not in limits
    assert 60 not in limits


def test_emulator_start_watch_keeps_waiting_during_mumu_startup_grace(monkeypatch):
    reset_fake_timer()
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
    startup_grace_timer = next(timer for timer in FakeTimer.instances if timer.limit == 90)
    assert startup_grace_timer.calls == 1


def test_emulator_start_watch_accepts_ready_adb_when_mumu_state_stays_starting(monkeypatch):
    reset_fake_timer()
    state = {
        "devices": FakeSelectedDevices(FakeDevice("127.0.0.1:16608", "device")),
        # MuMuManager 偶发持续返回 starting；ADB、包和窗口都已可用时不应阻塞恢复。
        "mumu_states": [
            {
                "player_state": "starting",
                "is_process_started": True,
            }
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


def test_emulator_start_watch_accepts_ready_adb_after_mumu_stuck_grace_expires(monkeypatch):
    reset_fake_timer()
    # stuck_grace 到期时仍应先尝试实际可用性验证，而不是只凭 MuMuManager 状态失败。
    FakeTimer.reached_schedule[45] = [True]
    state = {
        "devices": FakeSelectedDevices(FakeDevice("127.0.0.1:16608", "device")),
        "mumu_states": [
            {
                "player_state": "starting",
                "is_process_started": True,
            }
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


def test_emulator_start_watch_fails_when_mumu_stuck_and_packages_not_ready(monkeypatch):
    reset_fake_timer()
    # 第一轮建立 ADB 并启动 stuck_grace；第二轮状态超时且包不可用，验证失败分支可达。
    FakeTimer.reached_schedule[45] = [False, True]
    state = {
        "devices": FakeSelectedDevices(FakeDevice("127.0.0.1:16608", "device")),
        "mumu_states": [
            {
                "player_state": "starting",
                "is_process_started": True,
            }
        ],
        "connect_calls": [],
        "disconnect_calls": [],
    }
    platform = build_platform(state)
    package_calls = {"count": 0}

    def list_app_packages(show_log=False):
        package_calls["count"] += 1
        # 第一轮让流程继续等待窗口；第二轮在 mumu_state_stuck=True 时触发失败。
        return ["com.netease.onmyoji"] if package_calls["count"] == 1 else []

    platform.list_app_packages = list_app_packages

    monkeypatch.setattr("module.device.platform2.platform_windows.Timer", FakeTimer)
    monkeypatch.setattr("module.device.platform2.platform_windows.get_focused_window", lambda: 0)
    monkeypatch.setattr("module.device.platform2.platform_windows.set_focus_window", lambda hwnd: None)
    monkeypatch.setattr("module.device.platform2.platform_windows.Handle.handle_has_children", lambda hwnd: True)

    assert platform.emulator_start_watch() is False
