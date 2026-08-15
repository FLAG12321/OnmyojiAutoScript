import json
from pathlib import Path

import numpy as np
import pytest

from module.config.config_store import ConfigStore
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


def _canonical_template() -> dict:
    """读取真实模板生成隔离配置，不修改生产 config。"""
    raw = json.loads((Path.cwd() / "config" / "template.json").read_text(encoding="utf-8"))
    raw["meta_demon"].pop("md_strategies_1", None)
    return raw


@pytest.mark.parametrize(
    ("serial", "expected_runtime_serial", "expected_config_serial", "expected_method"),
    [
        ("wsa-0", "127.0.0.1:58526", "wsa-0", "uiautomator2"),
        ("127.0.0.1：7555", "127.0.0.1:7555", "127.0.0.1:7555", None),
    ],
)
def test_annotator_build_device_freezes_normalized_startup_snapshot(
    tmp_path,
    monkeypatch,
    serial,
    expected_runtime_serial,
    expected_config_serial,
    expected_method,
):
    """标注入口必须覆盖 WSA/中文冒号归一化，并在 Device 成功后冻结 COLD 快照。"""
    from module.config.config import Config
    from module.device.connection_attr import ConnectionAttr

    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("oas1", _canonical_template())
    store.patch_user_field("oas1", ("script", "device", "serial"), serial)
    config = Config("oas1", store=store)

    class NormalizingDevice:
        def __init__(self, config, cancel_event=None):
            connection = object.__new__(ConnectionAttr)
            connection.config = config
            connection.serial = config.model.script.device.serial
            ConnectionAttr.serial_check(connection)
            self.config = config
            self.runtime_serial = connection.serial

        def disable_stuck_detection(self):
            pass

        def screenshot_interval_set(self, interval):
            self.interval = interval

    monkeypatch.setattr(tool, "Device", NormalizingDevice)
    capture = tool.EmulatorCaptureSession("session-normalize")

    device = capture._build_device(config, 0.5)

    assert device.config is config
    assert device.runtime_serial == expected_runtime_serial
    assert config._provisional_device_snapshot is None
    assert config._startup_device_snapshot.serial == expected_config_serial
    assert config.base["script"]["device"]["serial"] == expected_config_serial
    assert store.load("oas1").canonical["script"]["device"]["serial"] == expected_config_serial
    if expected_method is not None:
        assert config._startup_device_snapshot.screenshot_method == expected_method
        assert config._startup_device_snapshot.control_method == expected_method


def test_annotator_first_device_failure_retries_with_fresh_config(tmp_path, monkeypatch):
    """首次 Device 构造失败后必须丢弃 provisional 状态，以全新 Config 重试。"""
    from module.config.config import Config

    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("oas1", _canonical_template())
    created_configs = []
    capture = tool.EmulatorCaptureSession("session-retry")

    def config_factory(config_name):
        config = Config(config_name, store=store)
        created_configs.append(config)
        return config

    class RetryDevice:
        attempts = 0

        def __init__(self, config, cancel_event=None):
            RetryDevice.attempts += 1
            if RetryDevice.attempts == 1:
                raise RuntimeError("injected first device failure")
            self.config = config

        def disable_stuck_detection(self):
            pass

        def screenshot_interval_set(self, interval):
            self.interval = interval

        def screenshot(self):
            # 第二次构造成功后只采一帧，随后让生产循环正常退出。
            capture._stop_event.set()
            return np.zeros((2, 2, 3), dtype=np.uint8)

        def release_during_wait(self):
            pass

    monkeypatch.setattr(tool, "Config", config_factory)
    monkeypatch.setattr(tool, "Device", RetryDevice)
    monkeypatch.setattr(tool, "EMULATOR_CAPTURE_RETRY_BACKOFF_SECONDS", 0)
    capture.config_name = "oas1"
    capture.frame_rate = 2

    capture._run()

    assert RetryDevice.attempts == 2
    assert len(created_configs) == 2
    assert created_configs[0] is not created_configs[1]
    assert created_configs[0]._provisional_device_snapshot is not None
    assert created_configs[0]._startup_device_snapshot is None
    assert created_configs[1]._provisional_device_snapshot is None
    assert created_configs[1]._startup_device_snapshot is not None
