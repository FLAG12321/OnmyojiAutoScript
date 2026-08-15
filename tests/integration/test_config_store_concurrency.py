# This Python file uses the following encoding: utf-8
# 集成测试：ConfigStore 跨进程并发（Windows spawn 多进程），覆盖不同字段交错、同字段冲突、
# 动态列表缩容、JSON 损坏、锁超时、写入子进程终止与 import/save 竞争。
import copy
import json
import multiprocessing
import os
from pathlib import Path

import pytest
from filelock import Timeout

from module.config.config_operations import set_path
from module.config.config_store import (
    ConfigGenerationMismatchError,
    ConfigStore,
)

TEMPLATE_PATH = Path.cwd() / "config" / "template.json"
OROCHI_LIMIT = ("orochi", "orochi_config", "limit_count")


def canonical_template() -> dict:
    raw = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    raw["meta_demon"].pop("md_strategies_1", None)
    return raw


def make_store(root: Path) -> ConfigStore:
    store = ConfigStore(config_root=root)
    store.create_from_template("oas1", canonical_template())
    return store


# ---------- Windows spawn 子进程顶层目标（必须是模块级函数） ----------

def child_patch_field(root: str, name: str, path, value) -> None:
    store = ConfigStore(Path(root))
    store.patch_user_field(name, tuple(path), value)


def child_patch_argument(root: str, name: str, task: str, group: str, argument: str, value) -> None:
    store = ConfigStore(Path(root))
    store.patch_user_argument(name, task, group, argument, value)


def child_create(root: str, name: str) -> None:
    store = ConfigStore(Path(root))
    store.create_from_template(name, canonical_template())


def child_stale_save(root: str, name: str, ready, go, value: int) -> None:
    """加载旧基线，等待父进程改磁盘后，以旧 local 保存同字段（应冲突、磁盘优先）。"""
    store = ConfigStore(Path(root))
    loaded = store.load(name)
    local = set_path(copy.deepcopy(loaded.canonical), OROCHI_LIMIT, value)
    ready.put("ready")
    go.get()
    store.save_background(name, loaded.canonical, local, loaded.generation, [])


def spawn(target, *args):
    ctx = multiprocessing.get_context("spawn")
    p = ctx.Process(target=target, args=args)
    p.start()
    return p


def join_ok(process):
    process.join(30)
    assert process.exitcode == 0


# ---------- 多进程写测试 ----------

def test_two_processes_disjoint_fields_interleave(tmp_path):
    store = make_store(tmp_path / "config")
    p1 = spawn(child_patch_field, str(store.config_root), "oas1",
               ("orochi", "orochi_config", "limit_count"), 9)
    p2 = spawn(child_patch_field, str(store.config_root), "oas1", ("running_task",), "Orochi")
    join_ok(p1)
    join_ok(p2)
    current = store.load("oas1").canonical
    assert current["orochi"]["orochi_config"]["limit_count"] == 9
    assert current["running_task"] == "Orochi"


def test_two_processes_same_field_serialized_no_corruption(tmp_path):
    store = make_store(tmp_path / "config")
    p1 = spawn(child_patch_field, str(store.config_root), "oas1", OROCHI_LIMIT, 9)
    p2 = spawn(child_patch_field, str(store.config_root), "oas1", OROCHI_LIMIT, 8)
    join_ok(p1)
    join_ok(p2)
    # 锁内串行写，最终是二者之一，配置仍严格合法
    value = store.load("oas1").canonical["orochi"]["orochi_config"]["limit_count"]
    assert value in (8, 9)


def test_dynamic_list_shrink_in_subprocess(tmp_path):
    store = make_store(tmp_path / "config")
    store.patch_user_argument("oas1", "FindJade", "findJadeConfig", "inviteInfoCount", 2)
    p = spawn(child_patch_argument, str(store.config_root), "oas1",
              "FindJade", "findJadeConfig", "inviteInfoCount", 1)
    join_ok(p)
    fj = store.load("oas1").canonical["find_jade"]
    assert fj["find_jade_config"]["invite_info_count"] == 1
    assert "invite_info_list_1" in fj
    assert "invite_info_list_2" not in fj


def test_stale_background_save_conflicts_and_disk_wins(tmp_path):
    store = make_store(tmp_path / "config")
    ready = multiprocessing.Queue()
    go = multiprocessing.Queue()
    p = spawn(child_stale_save, str(store.config_root), "oas1", ready, go, 8)
    assert ready.get(timeout=20) == "ready"
    # 父进程把磁盘改为 9，让旧 local=8 冲突
    store.patch_user_field("oas1", OROCHI_LIMIT, 9)
    go.put("go")
    join_ok(p)
    assert store.load("oas1").canonical["orochi"]["orochi_config"]["limit_count"] == 9


# ---------- 错误行为 ----------

def test_json_corruption_fails_closed(tmp_path):
    store = make_store(tmp_path / "config")
    config_path = store.generation._config_path("oas1")
    config_path.write_text("{corrupt json", encoding="utf-8")
    with pytest.raises(Exception):
        store.load("oas1")


def test_lock_timeout_raises(tmp_path):
    store = ConfigStore(config_root=tmp_path / "config", timeout=0.5)
    store.create_from_template("oas1", canonical_template())
    lock = store.generation._lifecycle_lock("oas1")
    lock.acquire()
    try:
        with pytest.raises(Timeout):
            store.patch_user_field("oas1", ("running_task",), "X")
    finally:
        lock.release()


def test_generation_mismatch_after_delete_recreate(tmp_path):
    store = make_store(tmp_path / "config")
    loaded = store.load("oas1")
    store.delete_config("oas1")
    store.create_from_template("oas1", canonical_template())
    with pytest.raises(ConfigGenerationMismatchError):
        store.save_background("oas1", loaded.canonical, loaded.model, loaded.generation, [])


def test_writer_subprocess_terminated_leaves_valid_config(tmp_path):
    """写入子进程在 atomic write 中间被强杀：配置必须仍可严格加载，不留半写文件。"""
    store = make_store(tmp_path / "config")
    p = spawn(child_patch_field, str(store.config_root), "oas1", OROCHI_LIMIT, 7)
    # 随机时序下强杀子进程，验证恢复路径
    p.terminate()
    p.join(30)
    # 重新打开 Store 可幂等初始化并加载（atomic write 保证不产生半写状态）
    reopened = ConfigStore(config_root=store.config_root)
    reopened.initialize()
    canonical = reopened.load("oas1").canonical
    # 默认 limit_count=30；子进程可能在写入 7 之前或之后被杀，二者都合法
    assert canonical["orochi"]["orochi_config"]["limit_count"] in (7, 30)


def test_import_and_save_competition(tmp_path):
    store = make_store(tmp_path / "config")
    # 一个进程创建新配置 oas2，另一个进程对已有 oas1 做字段 patch，互不干扰
    p1 = spawn(child_patch_field, str(store.config_root), "oas1", OROCHI_LIMIT, 9)
    p2 = spawn(child_create, str(store.config_root), "oas2")
    join_ok(p1)
    join_ok(p2)
    assert store.active_config_names() == ["oas1", "oas2"]
    assert store.load("oas1").canonical["orochi"]["orochi_config"]["limit_count"] == 9
    assert store.load("oas2").canonical["config_name"] == "oas2"
