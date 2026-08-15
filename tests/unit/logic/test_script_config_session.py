# This Python file uses the following encoding: utf-8
# 测试 Script 调度循环四条 reload 路径保持 script.config is script.device.config，
# 且外部 device patch 不进入运行 session 与 Device。
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from module.config.config import Config, Function
from module.config.config_store import ConfigStore
from module.config.config_operations import set_path

TEMPLATE_PATH = Path.cwd() / "config" / "template.json"
DEVICE_SERIAL = ("script", "device", "serial")


def _fresh_store(tmp_path) -> ConfigStore:
    raw = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    raw["meta_demon"].pop("md_strategies_1", None)
    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("oas1", raw)
    store.patch_user_field("oas1", DEVICE_SERIAL, "old")
    return store


def _make_script(store):
    """构造 Script，注入隔离 Store 的 Config session 与同 session 的 fake device。"""
    from script import Script

    script = Script("oas1")
    script.config = Config("oas1", store=store)
    script.config.script.error.handle_error = True

    class FakeDevice:
        def __init__(self, config):
            self.config = config
        def release_during_wait(self):
            pass
        def stuck_record_clear(self):
            pass
        def click_record_clear(self):
            pass

    script.device = FakeDevice(script.config)
    return script


def _freeze_cold(script, store):
    """建立并冻结 COLD 启动快照，随后外部把磁盘 serial 改成 new。"""
    script.config.begin_device_initialization()
    script.config.freeze_startup_device_snapshot()
    store.patch_user_field("oas1", DEVICE_SERIAL, "new")


def _task(command: str, next_run: datetime) -> Function:
    f = Function(command, {})
    f.command = command
    f.enable = True
    f.next_run = next_run
    f.priority = 1
    return f


# ---------- 等待返回路径 ----------

def test_wait_return_reloads_and_keeps_same_session(tmp_path):
    store = _fresh_store(tmp_path)
    script = _make_script(store)
    _freeze_cold(script, store)

    calls = []

    def fake_get_next():
        if calls:
            return _task("Orochi", datetime.now() - timedelta(minutes=1))
        calls.append(1)
        return _task("Orochi", datetime.now() + timedelta(hours=1))

    script.config.get_next = fake_get_next
    script.config.task = None
    script.wait_until = lambda future: False
    # 避开 when_task_queue_empty 分支的真实 run/设备操作
    script._handle_goto_main = lambda: None
    script._handle_close_game = lambda task, limit: None
    script._handle_close_emulator_or = lambda task, gl, el, m: None

    command = script.get_next_task()

    assert command == "Orochi"
    assert script.config is script.device.config
    # reload 后受保护快照覆盖，外部 new serial 不进入 session
    assert script.config.model.script.device.serial == "old"
    assert script.config.base["script"]["device"]["serial"] == "old"


# ---------- 首个 Restart 跳过路径 ----------

def test_skip_first_restart_reloads_and_keeps_same_session(tmp_path):
    store = _fresh_store(tmp_path)
    script = _make_script(store)
    _freeze_cold(script, store)
    script.is_first_task = True

    calls = []

    def fake_get_next():
        if calls:
            raise SystemExit
        calls.append(1)
        # 真实 get_next_task 返回任务名大驼峰字符串
        return "Restart"

    script.get_next_task = fake_get_next
    script.run = lambda command: True

    with pytest.raises(SystemExit):
        script.loop()

    assert script.config is script.device.config
    assert script.config.model.script.device.serial == "old"


# ---------- 任务成功路径 ----------

def test_task_success_reloads_and_keeps_same_session(tmp_path):
    store = _fresh_store(tmp_path)
    script = _make_script(store)
    _freeze_cold(script, store)
    script.is_first_task = True

    calls = []

    def fake_get_next():
        if calls:
            raise SystemExit
        calls.append(1)
        return "Orochi"

    script.get_next_task = fake_get_next
    script.run = lambda command: True

    with pytest.raises(SystemExit):
        script.loop()

    assert script.config is script.device.config
    assert script.config.model.script.device.serial == "old"


# ---------- 可恢复失败路径 ----------

def test_recoverable_failure_reloads_and_keeps_same_session(tmp_path):
    store = _fresh_store(tmp_path)
    script = _make_script(store)
    _freeze_cold(script, store)
    script.is_first_task = True
    script.config.script.error.handle_error = True

    calls = []

    def fake_get_next():
        if calls:
            raise SystemExit
        calls.append(1)
        return "Orochi"

    script.get_next_task = fake_get_next
    script.run = lambda command: False

    with pytest.raises(SystemExit):
        script.loop()

    assert script.config is script.device.config
    assert script.config.model.script.device.serial == "old"
