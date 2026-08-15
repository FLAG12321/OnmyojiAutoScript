# This Python file uses the following encoding: utf-8
# 集成测试：Task 3 全部写入方迁移到 ConfigStore，验证 copy/import/reset、离线 GUI、
# ConfigModify、MultiActivityShikigami 扫描、Device startup normalization 与 template。
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from module.config.config_store import ConfigStore
from module.config.config_operations import get_path

TEMPLATE_PATH = Path.cwd() / "config" / "template.json"


def canonical_template() -> dict:
    raw = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    raw["meta_demon"].pop("md_strategies_1", None)
    return raw


def _manager(tmp_path) -> "ConfigManager":
    from module.server.config_manager import ConfigManager

    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("template", canonical_template())
    return ConfigManager(store=store)


def test_config_manager_copy_uses_store(tmp_path):
    manager = _manager(tmp_path)
    manager.copy("oas1", "template")
    assert manager.store.load("oas1").canonical["config_name"] == "oas1"
    assert manager.all_script_files() == ["oas1"]
    assert manager.all_json_file() == ["template", "oas1"]


def test_config_manager_import_and_export_roundtrip(tmp_path):
    manager = _manager(tmp_path)
    name = manager.import_config("oas9", canonical_template())
    assert name == "oas9"
    exported_name, data = manager.load_config_for_export("oas9")
    assert exported_name == "oas9"
    assert data["config_name"] == "oas9"
    # 导出经过脱敏；重命名/删除只保留 Store 入口：生产路由走 MainManager.rename_config /
    # delete_config，会先停止运行实例再提交身份。ConfigManager 上吞异常的同名薄封装已移除，
    # 防止将来误用绕过该协议在实例仍存活时改身份。
    manager.store.rename_config("oas9", "oas10")
    assert "oas10" in manager.all_script_files()
    manager.store.delete_config("oas10")
    assert "oas10" not in manager.all_script_files()


def test_replace_subtree_rejects_same_name_recreated_generation(store):
    """旧请求即使 expected 子树值相同，也不得写入同名重建后的新身份。"""
    loaded = store.load("oas1")
    expected = loaded.canonical["orochi"]
    replacement = json.loads(json.dumps(expected))
    replacement["orochi_config"]["limit_count"] = 9

    store.delete_config("oas1")
    store.create_from_template("oas1", canonical_template())
    recreated_before = store.load("oas1")

    from module.config.config_store import ConfigGenerationMismatchError

    with pytest.raises(ConfigGenerationMismatchError):
        store.replace_subtree(
            "oas1",
            ("orochi",),
            expected,
            replacement,
            loaded.generation,
        )

    recreated_after = store.load("oas1")
    assert recreated_after.generation == recreated_before.generation
    assert recreated_after.canonical == recreated_before.canonical


def test_reset_next_runs_rejects_same_name_recreated_generation(store):
    """旧 generation 的批量 reset 不得修改同名重建后的新身份。"""
    loaded = store.load("oas1")
    store.delete_config("oas1")
    store.create_from_template("oas1", canonical_template())
    recreated_before = store.load("oas1")

    from module.config.config_store import ConfigGenerationMismatchError

    with pytest.raises(ConfigGenerationMismatchError):
        store.reset_next_runs(
            "oas1",
            datetime(2026, 5, 1, 9, 0, 0),
            loaded.generation,
        )

    recreated_after = store.load("oas1")
    assert recreated_after.generation == recreated_before.generation
    assert recreated_after.canonical == recreated_before.canonical


def test_reset_enabled_next_runs_is_one_atomic_store_transaction(store):
    """全局重置标志与所有启用任务的 next_run 必须由一次 Store 提交完成。"""
    target = datetime(2026, 5, 1, 9, 0, 0)
    store.patch_user_field(
        "oas1",
        ("restart", "tasks_config_reset", "reset_task_datetime"),
        target,
    )
    store.patch_user_field("oas1", ("orochi", "scheduler", "enable"), True)
    store.patch_user_field(
        "oas1",
        ("orochi", "scheduler", "next_run"),
        "2026-01-01 00:00:00",
    )
    store.patch_user_field("oas1", ("restart", "scheduler", "enable"), False)
    before_restart = store.load("oas1").canonical["restart"]["scheduler"]["next_run"]

    result = store.reset_enabled_next_runs("oas1")

    canonical = store.load("oas1").canonical
    assert canonical["restart"]["tasks_config_reset"]["reset_task_datetime_enable"] is True
    assert canonical["orochi"]["scheduler"]["next_run"] == "2026-05-01 09:00:00"
    assert canonical["restart"]["scheduler"]["next_run"] == before_restart
    assert result.operation == "RESET_ENABLED_NEXT_RUNS"
    assert ("restart", "tasks_config_reset", "reset_task_datetime_enable") in result.changed_paths
    assert ("orochi", "scheduler", "next_run") in result.changed_paths


def test_reset_next_runs_only_enabled_tasks(store):
    target = datetime(2026, 5, 1, 9, 0, 0)
    store.patch_user_field("oas1", ("orochi", "scheduler", "enable"), True)
    store.patch_user_field("oas1", ("orochi", "scheduler", "next_run"), "2026-01-01 00:00:00")
    store.patch_user_field("oas1", ("restart", "scheduler", "enable"), False)
    store.patch_user_field("oas1", ("restart", "scheduler", "next_run"), "2026-01-01 00:00:00")

    loaded = store.load("oas1")
    store.reset_next_runs("oas1", target, loaded.generation)

    canonical = store.load("oas1").canonical
    # 只重置 enable=True 的任务；禁用任务保持不变
    assert canonical["orochi"]["scheduler"]["next_run"] == "2026-05-01 09:00:00"
    assert canonical["restart"]["scheduler"]["next_run"] == "2026-01-01 00:00:00"


def test_annotator_list_configs_uses_manager_instance(store, monkeypatch):
    """标注器枚举配置必须走实例调用，且复用 MainManager 的单一 Store。

    all_script_files 从 @staticmethod 改为实例方法后，AnnotatorManager.list_configs
    仍按未绑定方式调用（ConfigManager.all_script_files()），必然抛 TypeError：
    标注器配置下拉接口与 start_emulator 全断，而静态门禁只查裸 I/O 旁路查不出符号绑定。
    """
    from module.server import main_manager
    from module.server.tool import annotator_manager

    monkeypatch.setattr(main_manager.mm, "store", store)
    assert annotator_manager.list_configs() == ["oas1"]
    # start_emulator 的配置存在性校验走同一入口，不得因未绑定调用而崩在校验之前
    with pytest.raises(Exception) as excinfo:
        annotator_manager.start_emulator("no-such-session", "oas1", 30)
    assert not isinstance(excinfo.value, TypeError)


def test_gui_add_uses_store_active_names(store):
    from module.gui.context.add import Add

    add = Add()
    add.store = store
    assert add.all_script_files() == ["oas1"]
    assert add.all_json_file() == ["template", "oas1"]
    # copy 走 create_from_template
    add.copy("oas2", "template")
    assert store.load("oas2").canonical["config_name"] == "oas2"


def test_config_modify_gui_set_task_uses_store_patch(store):
    from module.config.config_modify import ConfigModify

    cm = ConfigModify("oas1", store=store)
    assert cm.gui_set_task("Orochi", "orochiConfig", "limitCount", 7) is True
    assert store.load("oas1").model.orochi.orochi_config.limit_count == 7
    # 非法值走 Store 校验失败，磁盘不变
    assert cm.gui_set_task("FindJade", "findJadeConfig", "inviteInfoCount", 0) is False


def test_multi_activity_shikigami_scans_via_store(store):
    from tasks.MultiActivityShikigami.script_task import ScriptTask

    task = object.__new__(ScriptTask)
    task.config = SimpleNamespace(store=store)
    execution_items, unmatched, load_failure = task._load_execution_items(["甲"])
    # 模板默认账号无切号资料，全部 unmatched，但扫描本身不裸读配置文件
    assert load_failure is False
    assert unmatched == ["甲"]
    assert execution_items == []


def test_template_replace_preserves_generation(store):
    before = store.load("template")
    raw = dict(before.canonical)
    raw["running_task"] = "Restart"
    after = store.replace_template(raw)
    assert after.generation == before.generation
    assert store.load("template").canonical["running_task"] == "Restart"


def test_device_init_desktop_uses_startup_normalize(tmp_path):
    from module.config.config import Config
    from module.device.device import Device

    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("oas1", canonical_template())
    store.patch_user_field("oas1", ("script", "device", "screenshot_method"), "auto")
    session = Config("oas1", store=store)
    session.begin_device_initialization()

    dev = object.__new__(Device)
    dev.config = session
    dev._transition_to = lambda target: None
    dev.screenshot_interval_set = lambda: None
    dev.desktop_window_set_size = lambda: False

    Device._init_desktop(dev)

    # startup_normalize 把声明路径合入 session model/base 与磁盘
    assert session.model.script.device.screenshot_method == "window_background"
    assert session.model.script.device.control_method == "window_message"
    assert store.load("oas1").canonical["script"]["device"]["screenshot_method"] == "window_background"


def test_device_init_wsa_uses_single_startup_normalize(tmp_path):
    """WSA 初始化通过一次事务同步方法配置、会话基线和 provisional 快照。"""
    from module.config.config import Config
    from module.device.connection_attr import ConnectionAttr

    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("oas1", canonical_template())
    store.patch_user_field("oas1", ("script", "device", "serial"), "wsa-0")
    session = Config("oas1", store=store)
    session.begin_device_initialization()

    connection = object.__new__(ConnectionAttr)
    connection.config = session
    connection.serial = session.model.script.device.serial
    ConnectionAttr.serial_check(connection)

    assert connection.serial == "127.0.0.1:58526"
    assert session.model.script.device.screenshot_method == "uiautomator2"
    assert session.model.script.device.control_method == "uiautomator2"
    assert session.base["script"]["device"]["screenshot_method"] == "uiautomator2"
    assert session._provisional_device_snapshot.control_method == "uiautomator2"
    disk_device = store.load("oas1").canonical["script"]["device"]
    assert disk_device["screenshot_method"] == "uiautomator2"
    assert disk_device["control_method"] == "uiautomator2"


def test_device_init_serial_chinese_colon_uses_startup_normalize(tmp_path):
    """中文冒号归一化必须持久化到正式 serial 字段，不再写旧兼容属性。"""
    from module.config.config import Config
    from module.device.connection_attr import ConnectionAttr

    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("oas1", canonical_template())
    store.patch_user_field("oas1", ("script", "device", "serial"), "127.0.0.1：7555")
    session = Config("oas1", store=store)
    session.begin_device_initialization()

    connection = object.__new__(ConnectionAttr)
    connection.config = session
    connection.serial = session.model.script.device.serial
    ConnectionAttr.serial_check(connection)

    assert connection.serial == "127.0.0.1:7555"
    assert session.model.script.device.serial == "127.0.0.1:7555"
    assert session.base["script"]["device"]["serial"] == "127.0.0.1:7555"
    assert session._provisional_device_snapshot.serial == "127.0.0.1:7555"
    assert store.load("oas1").canonical["script"]["device"]["serial"] == "127.0.0.1:7555"


def test_device_init_wsa_normalize_failure_keeps_session_state(tmp_path, monkeypatch):
    """WSA 启动事务失败时不得局部推进 model/base/provisional 或磁盘。"""
    from module.config.config import Config
    from module.device.connection_attr import ConnectionAttr

    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("oas1", canonical_template())
    store.patch_user_field("oas1", ("script", "device", "serial"), "wsa-0")
    session = Config("oas1", store=store)
    session.begin_device_initialization()
    before_model = session.model.model_dump(mode="json")
    before_base = json.loads(json.dumps(session.base))
    before_provisional = session._provisional_device_snapshot.model_dump(mode="json")
    before_disk = store.load("oas1").canonical

    def fail_startup_normalize(*_args, **_kwargs):
        # 模拟底层原子事务在写入前失败。
        raise OSError("injected startup normalize failure")

    monkeypatch.setattr(store, "startup_normalize", fail_startup_normalize)
    connection = object.__new__(ConnectionAttr)
    connection.config = session
    connection.serial = session.model.script.device.serial

    with pytest.raises(OSError, match="injected startup normalize failure"):
        ConnectionAttr.serial_check(connection)

    assert session.model.model_dump(mode="json") == before_model
    assert session.base == before_base
    assert session._provisional_device_snapshot.model_dump(mode="json") == before_provisional
    assert store.load("oas1").canonical == before_disk


def test_background_save_does_not_overwrite_newer_disk(tmp_path):
    """writer 侧回归：陈旧 session 后台保存不得覆盖 OASX 已写入的字段。"""
    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("oas1", canonical_template())
    loaded = store.load("oas1")
    base = loaded.canonical
    local = dict(base)
    local["running_task"] = "Orochi"
    store.patch_user_field("oas1", ("orochi", "orochi_config", "limit_count"), 9)
    result = store.save_background("oas1", base, local, loaded.generation, [])
    current = store.load("oas1").canonical
    assert current["orochi"]["orochi_config"]["limit_count"] == 9
    assert current["running_task"] == "Orochi"
    assert result.conflicted_paths == []
