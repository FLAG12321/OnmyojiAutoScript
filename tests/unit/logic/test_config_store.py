# This Python file uses the following encoding: utf-8
# 测试 ConfigStore：generation 身份、三方合并、blocked 指纹、动态 path-set、COLD 快照与 template
import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from module.config.config import Config
from module.config.config_operations import delete_path, set_path
from module.config.config_store import (
    ConfigGenerationMismatchError,
    ConfigStore,
    advance_blocked_state,
)
from module.config.config_validation import ConfigValidationError

TEMPLATE_PATH = Path.cwd() / "config" / "template.json"
OROCHI_LIMIT = ("orochi", "orochi_config", "limit_count")


# ---------- 测试数据辅助 ----------

def canonical_template() -> dict:
    raw = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    raw["meta_demon"].pop("md_strategies_1", None)
    return raw


def make_generation_store(tmp_path) -> ConfigStore:
    return ConfigStore(config_root=tmp_path / "config")


def make_session(tmp_path, serial="old", screenshot_method="auto"):
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    store.patch_user_field("oas1", ("script", "device", "serial"), serial)
    store.patch_user_field("oas1", ("script", "device", "screenshot_method"), screenshot_method)
    session = Config("oas1", store=store)
    return session, store


def save_conflicting_local_value(store, local_value=8, disk_value=9, base_value=1):
    """构造一次三方冲突：session 基线=base_value，磁盘并发改为 disk_value，local 想写 local_value。"""
    store.patch_user_field("oas1", OROCHI_LIMIT, base_value)
    loaded = store.load("oas1")
    base = loaded.canonical
    store.patch_user_field("oas1", OROCHI_LIMIT, disk_value)
    local = set_path(copy.deepcopy(base), OROCHI_LIMIT, local_value)
    result = store.save_background("oas1", base, local, loaded.generation, [])
    assert OROCHI_LIMIT in result.conflicted_paths
    return SimpleNamespace(base=base, local=local, generation=loaded.generation, blocked=result.blocked)


def replace_local(local, value):
    return set_path(copy.deepcopy(local), OROCHI_LIMIT, value)


# ---------- 三方合并与 generation 身份 ----------

def test_normal_save_does_not_change_generation_and_survives_reinitialize(tmp_path):
    store = make_generation_store(tmp_path)
    loaded = store.create_from_template("oas1", canonical_template())
    original_generation = loaded.generation
    store.patch_user_field("oas1", ("orochi", "orochi_config", "limit_count"), 9)
    reopened = ConfigStore(config_root=store.config_root)
    reopened.initialize()
    current = reopened.load("oas1")
    assert current.generation == original_generation
    assert current.model.orochi.orochi_config.limit_count == 9


def test_stale_background_save_preserves_disjoint_user_patch(tmp_path):
    store = make_generation_store(tmp_path)
    loaded_a = store.create_from_template("oas1", canonical_template())
    loaded_b = store.load("oas1")
    store.patch_user_field("oas1", ("orochi", "orochi_config", "limit_count"), 9)
    loaded_b.model.running_task = "Orochi"
    result = store.save_background(
        "oas1",
        loaded_b.canonical,
        loaded_b.model,
        loaded_b.generation,
        [],
    )
    current = store.load("oas1").canonical
    assert current["orochi"]["orochi_config"]["limit_count"] == 9
    assert current["running_task"] == "Orochi"
    assert result.conflicted_paths == []


def test_same_leaf_conflict_keeps_user_value(tmp_path):
    store = make_generation_store(tmp_path)
    loaded = store.create_from_template("oas1", canonical_template())
    store.patch_user_field("oas1", ("orochi", "orochi_config", "limit_count"), 9)
    loaded.model.orochi.orochi_config.limit_count = 8
    result = store.save_background(
        "oas1", loaded.canonical, loaded.model, loaded.generation, []
    )
    assert store.load("oas1").model.orochi.orochi_config.limit_count == 9
    assert result.conflicted_paths == [("orochi", "orochi_config", "limit_count")]


def test_no_change_save_does_not_write(tmp_path):
    store = make_generation_store(tmp_path)
    loaded = store.create_from_template("oas1", canonical_template())
    before_mtime = store.load("oas1").mtime_ns
    result = store.save_background("oas1", loaded.canonical, loaded.model, loaded.generation, [])
    assert result.wrote_file is False
    assert store.load("oas1").mtime_ns == before_mtime


def test_generation_mismatch_blocks_stale_session_save(tmp_path):
    store = make_generation_store(tmp_path)
    loaded = store.create_from_template("oas1", canonical_template())
    # 同名重建会产生新 generation，旧 session 保存必须被拒绝
    store.delete_config("oas1")
    store.create_from_template("oas1", canonical_template())
    with pytest.raises(ConfigGenerationMismatchError):
        store.save_background("oas1", loaded.canonical, loaded.model, loaded.generation, [])


# ---------- blocked 指纹状态转移 ----------

def test_same_fingerprint_is_skipped_without_new_write(store):
    first = save_conflicting_local_value(store, local_value=8, disk_value=9)
    second = store.save_background("oas1", first.base, first.local, first.generation, first.blocked)
    assert second.wrote_file is False
    assert second.skipped_blocked_paths == [("orochi", "orochi_config", "limit_count")]


def test_local_change_releases_blocked_and_retries_merge(store):
    first = save_conflicting_local_value(store, local_value=8, disk_value=9)
    result = store.save_background("oas1", first.base, replace_local(first.local, 7), first.generation, first.blocked)
    assert result.skipped_blocked_paths == []
    assert result.conflicted_paths == [("orochi", "orochi_config", "limit_count")]


def test_disk_change_releases_blocked_and_reclassifies(store):
    first = save_conflicting_local_value(store, local_value=8, disk_value=9)
    store.patch_user_field("oas1", ("orochi", "orochi_config", "limit_count"), 10)
    result = store.save_background("oas1", first.base, first.local, first.generation, first.blocked)
    assert result.skipped_blocked_paths == []
    assert result.conflicted_paths == [("orochi", "orochi_config", "limit_count")]


def test_disk_equals_blocked_local_advances_base_and_clears(store):
    first = save_conflicting_local_value(store, local_value=8, disk_value=9)
    store.patch_user_field("oas1", ("orochi", "orochi_config", "limit_count"), 8)
    result = store.save_background("oas1", first.base, first.local, first.generation, first.blocked)
    assert result.already_equal_paths == [("orochi", "orochi_config", "limit_count")]
    assert result.blocked_cleared_paths == [("orochi", "orochi_config", "limit_count")]
    assert result.blocked == []
    assert result.base["orochi"]["orochi_config"]["limit_count"] == 8


def test_advance_blocked_state_classifies_three_ways(store):
    base = {"a": {"x": 1}}
    local = {"a": {"x": 2}}
    disk = {"a": {"x": 3}}
    blocked = [SimpleNamespace(path=("a", "x"), operation="SET", blocked_local_value=2, observed_disk_value=3)]
    result = advance_blocked_state(blocked, base, local, disk)
    assert result.skip == blocked
    assert result.clear == []
    assert result.release == []

    result = advance_blocked_state(blocked, base, {"a": {"x": 5}}, disk)
    assert result.skip == []
    assert result.release == blocked

    result = advance_blocked_state(blocked, base, local, {"a": {"x": 2}})
    assert result.clear == blocked


# ---------- REPLACE_PATH_SET 变体的 blocked 四类状态转移 ----------

FIND_JADE_MEMBER_NAME = ("find_jade", "invite_info_list_1", "name")


def save_conflicting_path_set(store, local_name="b", disk_name="c"):
    """构造一次动态 path-set 三方冲突：base name=a，local 想写 b，磁盘改为 c。"""
    store.patch_user_argument("oas1", "FindJade", "inviteInfoList_1", "name", "a")
    loaded = store.load("oas1")
    base = loaded.canonical
    store.patch_user_argument("oas1", "FindJade", "inviteInfoList_1", "name", disk_name)
    local = set_path(copy.deepcopy(base), FIND_JADE_MEMBER_NAME, local_name)
    result = store.save_background("oas1", base, local, loaded.generation, [])
    assert result.conflicted_paths
    assert result.blocked and result.blocked[0].operation == "REPLACE_PATH_SET"
    return SimpleNamespace(base=base, local=local, generation=loaded.generation, blocked=result.blocked)


def replace_path_set_local(local, name):
    return set_path(copy.deepcopy(local), FIND_JADE_MEMBER_NAME, name)


def test_path_set_same_fingerprint_is_skipped_without_new_write(store):
    first = save_conflicting_path_set(store, local_name="b", disk_name="c")
    second = store.save_background("oas1", first.base, first.local, first.generation, first.blocked)
    assert second.wrote_file is False
    assert len(second.skipped_blocked_paths) == 1


def test_path_set_local_change_releases_blocked_and_retries(store):
    first = save_conflicting_path_set(store, local_name="b", disk_name="c")
    result = store.save_background(
        "oas1", first.base, replace_path_set_local(first.local, "x"), first.generation, first.blocked)
    assert result.skipped_blocked_paths == []
    assert result.conflicted_paths


def test_path_set_disk_change_releases_blocked_and_reclassifies(store):
    first = save_conflicting_path_set(store, local_name="b", disk_name="c")
    store.patch_user_argument("oas1", "FindJade", "inviteInfoList_1", "name", "d")
    result = store.save_background("oas1", first.base, first.local, first.generation, first.blocked)
    assert result.skipped_blocked_paths == []
    assert result.conflicted_paths


def test_path_set_disk_equals_blocked_local_advances_base_and_clears(store):
    first = save_conflicting_path_set(store, local_name="b", disk_name="c")
    store.patch_user_argument("oas1", "FindJade", "inviteInfoList_1", "name", "b")
    result = store.save_background("oas1", first.base, first.local, first.generation, first.blocked)
    assert result.blocked_cleared_paths
    assert result.blocked == []
    # base 推进到 local/disk 一致的值
    assert result.base["find_jade"]["invite_info_list_1"]["name"] == "b"


def test_path_set_disk_equals_blocked_local_advances_deleted_base(tmp_path):
    """动态缩容与磁盘收敛后必须删除陈旧基线，后续幸存成员外改不应再假冲突。"""
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    store.patch_user_argument("oas1", "FindJade", "findJadeConfig", "inviteInfoCount", 2)
    loaded = store.load("oas1")
    base = loaded.canonical
    local = set_path(
        copy.deepcopy(base),
        ("find_jade", "find_jade_config", "invite_info_count"),
        1,
    )
    local = delete_path(local, ("find_jade", "invite_info_list_2"))

    # 磁盘先扩容为另一组 path-set，制造一次与本地缩容相冲突的并发修改。
    store.patch_user_argument("oas1", "FindJade", "findJadeConfig", "inviteInfoCount", 3)
    first = store.save_background("oas1", base, local, loaded.generation, [])
    assert first.blocked and first.conflicted_paths

    # 磁盘随后完成同一缩容，blocked 应清除，且删除成员不能残留在新基线。
    store.patch_user_argument("oas1", "FindJade", "findJadeConfig", "inviteInfoCount", 1)
    second = store.save_background(
        "oas1", base, local, loaded.generation, first.blocked
    )
    assert second.blocked == []
    assert second.already_equal_paths
    assert "invite_info_list_2" not in second.base["find_jade"]

    # 仅修改幸存成员时，本地已无缩容操作，下一轮不得产生永久假冲突。
    store.patch_user_argument("oas1", "FindJade", "inviteInfoList_1", "name", "磁盘幸存成员")
    third = store.save_background(
        "oas1", second.base, local, loaded.generation, second.blocked
    )
    assert third.conflicted_paths == []
    assert third.blocked == []


# ---------- 动态 group_N / count ----------

def test_normalize_user_group(tmp_path):
    store = make_generation_store(tmp_path)
    assert store._normalize_user_group('inviteInfoList_1') == ('invite_info_list', 1)
    assert store._normalize_user_group('invite_info_list_1') == ('invite_info_list', 1)
    assert store._normalize_user_group('taskConfig') == ('task_config', None)


def test_patch_dynamic_member_updates_one_atomic_path_set(tmp_path):
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    before = store.load("oas1").canonical
    result = store.patch_user_argument("oas1", "FindJade", "inviteInfoList_1", "name", "测试账号")
    assert result.operation == "REPLACE_PATH_SET"
    after = store.load("oas1").canonical
    assert after["find_jade"]["invite_info_list_1"]["name"] == "测试账号"
    assert after["find_jade"]["find_jade_config"]["invite_info_count"] == \
        before["find_jade"]["find_jade_config"]["invite_info_count"]


def test_patch_dynamic_count_expand_and_shrink_deletes_residual(tmp_path):
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    store.patch_user_argument("oas1", "FindJade", "findJadeConfig", "inviteInfoCount", 2)
    fj = store.load("oas1").canonical["find_jade"]
    assert fj["find_jade_config"]["invite_info_count"] == 2
    assert "invite_info_list_2" in fj
    store.patch_user_argument("oas1", "FindJade", "findJadeConfig", "inviteInfoCount", 1)
    fj = store.load("oas1").canonical["find_jade"]
    assert fj["find_jade_config"]["invite_info_count"] == 1
    assert "invite_info_list_1" in fj
    assert "invite_info_list_2" not in fj


def test_patch_dynamic_group_rejects_out_of_range_index(tmp_path):
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    with pytest.raises(ConfigValidationError):
        store.patch_user_argument("oas1", "FindJade", "inviteInfoList_99", "name", "不存在")


def test_patch_user_argument_falls_back_to_leaf_patch(tmp_path):
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    result = store.patch_user_argument("oas1", "Orochi", "orochiConfig", "limitCount", 7)
    assert result.operation == "SET"
    assert store.load("oas1").model.orochi.orochi_config.limit_count == 7


def test_desktop_handle_option_is_resolved_to_pid(tmp_path):
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    store.patch_user_field("oas1", ("script", "device", "serial"), "desktop")
    result = store.patch_user_argument("oas1", "Script", "device", "handle", "27272 (154,38)")
    assert result.success is True
    assert store.load("oas1").canonical["script"]["device"]["handle"] == "27272"


# ---------- COLD 启动快照 ----------

def test_task_delay_does_not_leak_or_overwrite_external_device_patch(tmp_path):
    session, store = make_session(tmp_path, serial="old")
    session.begin_device_initialization()
    session.freeze_startup_device_snapshot()
    store.patch_user_field("oas1", ("script", "device", "serial"), "new")

    session.task_delay("Orochi", target=datetime.now() + timedelta(hours=1), server=False)

    assert session.model.script.device.serial == "old"
    assert store.load("oas1").model.script.device.serial == "new"


def test_startup_normalize_updates_device_snapshot_and_reload_overlay(tmp_path):
    session, store = make_session(tmp_path, screenshot_method="auto")
    session.begin_device_initialization()
    session.startup_normalize({
        ("script", "device", "screenshot_method"): "ADB",
    })
    session.freeze_startup_device_snapshot()
    store.patch_user_field(
        "oas1", ("script", "device", "screenshot_method"), "scrcpy"
    )

    session.reload()

    assert session.model.script.device.screenshot_method == "ADB"
    assert store.load("oas1").model.script.device.screenshot_method == "scrcpy"


def test_concurrent_cold_patch_during_device_initialization_is_not_frozen(tmp_path):
    session, store = make_session(tmp_path, serial="old", screenshot_method="auto")
    session.begin_device_initialization()
    device_serial = session.model.script.device.serial

    store.patch_user_field("oas1", ("script", "device", "serial"), "new")
    session.startup_normalize({
        ("script", "device", "screenshot_method"): "ADB",
    })
    session.freeze_startup_device_snapshot()

    assert device_serial == "old"
    assert session.model.script.device.serial == "old"
    assert session.base["script"]["device"]["serial"] == "old"
    assert session._startup_device_snapshot.serial == "old"
    current = store.load("oas1").model.script.device
    assert current.serial == "new"
    assert current.screenshot_method == "ADB"


def test_freeze_without_begin_raises(tmp_path):
    session, _store = make_session(tmp_path)
    with pytest.raises(RuntimeError):
        session.freeze_startup_device_snapshot()


def test_startup_normalize_after_freeze_raises(tmp_path):
    session, _store = make_session(tmp_path)
    session.begin_device_initialization()
    session.freeze_startup_device_snapshot()
    with pytest.raises(RuntimeError):
        session.startup_normalize({("script", "device", "screenshot_method"): "ADB"})


# ---------- template ----------

def test_replace_template_preserves_active_generation_and_replaces_atomically(store):
    before = store.load("template")
    raw = copy.deepcopy(before.canonical)
    raw["running_task"] = "Restart"
    after = store.replace_template(raw)
    assert after.generation == before.generation
    assert store.load("template").canonical["running_task"] == "Restart"


def test_replace_template_validation_failure_keeps_original(store):
    before = store.load("template")
    invalid = copy.deepcopy(before.canonical)
    invalid["find_jade"]["find_jade_config"]["invite_info_count"] = 0
    invalid["find_jade"].pop("invite_info_list_1", None)
    with pytest.raises(ConfigValidationError):
        store.replace_template(invalid)
    current = store.load("template")
    assert current.generation == before.generation
    assert current.canonical == before.canonical


# ---------- 记忆覆盖项：MetaDemon count=0 默认形状可被严格校验接受 ----------

def test_meta_demon_migrated_shape_passes_strict_validation(store):
    """迁移后 MetaDemon 默认形状（count=0 且无 md_strategies_*）必须通过严格持久化校验。"""
    canonical = store.load("oas1").canonical
    md = canonical["meta_demon"]
    assert md["meta_demon_config"]["md_strategy_count"] == 0
    assert not any(k.startswith("md_strategies_") for k in md)
