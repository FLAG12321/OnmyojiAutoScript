# This Python file uses the following encoding: utf-8
# 集成测试：WARM/COLD 分级热重载与跨进程配置状态通道。
# 覆盖 WARM 重建不泄漏 COLD 值、pending 独立计算与回退清除、generation mismatch、
# config_event_queue 方向/丢事件 mtime 兜底、inactive 首帧、stop 清理与进程重启快照。
import asyncio
import json
import os
import queue as queue_module
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import script as script_module
from module.config.config import Config
from module.config.config_reload import COLD, DEFAULT_RELOAD_POLICY, WARM
from module.config.config_store import ConfigStore
from module.server import script_process as script_process_module
from module.server.script_process import ScriptProcess, ScriptState

TEMPLATE_PATH = Path.cwd() / "config" / "template.json"
OROCHI_LIMIT = ("orochi", "orochi_config", "limit_count")


def canonical_template() -> dict:
    raw = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    raw["meta_demon"].pop("md_strategies_1", None)
    return raw


def _child_verify_generation(
    config_root, config_name, expected_generation, attempt_nonce, ready_queue, gate
):
    """真实 multiprocessing child：barrier 后读取 active generation 并发送握手结果。"""
    gate.wait(timeout=5)
    child_store = ConfigStore(config_root=Path(config_root))
    actual_generation = child_store.load(config_name).generation
    if actual_generation != expected_generation:
        ready_queue.put({
            "status": "failed",
            "generation": actual_generation,
            "nonce": attempt_nonce,
            "reason": "generation_mismatch",
        })
        return
    ready_queue.put({
        "status": "ready",
        "generation": actual_generation,
        "nonce": attempt_nonce,
    })


def make_generation_store(tmp_path) -> ConfigStore:
    return ConfigStore(config_root=tmp_path / "config")


def make_session(tmp_path, serial="old", limit_count=1):
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    store.patch_user_field("oas1", ("script", "device", "serial"), serial)
    store.patch_user_field("oas1", OROCHI_LIMIT, limit_count)
    session = Config("oas1", store=store)
    return session, store


def make_script_shell(session, event_queue, state_queue):
    """构造绕过 __init__ 的 Script 壳，只承载跨进程事件排空与 checkpoint 方法。"""
    s = script_module.Script.__new__(script_module.Script)
    s.config_name = session.config_name
    s.config = session
    s.config_event_queue = event_queue
    s.state_queue = state_queue
    return s


def drain_queue(q):
    """排空任意 queue，返回剩余元素列表。"""
    items = []
    while True:
        try:
            items.append(q.get_nowait())
        except Exception:
            break
    return items


# ---------- WARM / COLD 分级刷新 ----------

def test_warm_reload_never_leaks_new_device_value(tmp_path):
    session, store = make_session(tmp_path, serial="old", limit_count=1)
    session.begin_device_initialization()
    session.freeze_startup_device_snapshot()
    store.patch_user_field("oas1", ("script", "device", "serial"), "new")
    store.patch_user_field("oas1", OROCHI_LIMIT, 9)

    result = session.refresh_from_disk("task_boundary")

    # WARM 值进入运行模型；COLD 值保持启动快照
    assert session.model.script.device.serial == "old"
    assert session.model.orochi.orochi_config.limit_count == 9
    assert session.base["script"]["device"]["serial"] == "old"
    assert session.pending_restart_paths == {("script", "device", "serial")}
    assert result.status == "restart_required"


def test_pending_restart_clears_when_disk_returns_to_snapshot(tmp_path):
    session, store = make_session(tmp_path, serial="old", limit_count=1)
    session.begin_device_initialization()
    session.freeze_startup_device_snapshot()
    store.patch_user_field("oas1", ("script", "device", "serial"), "new")

    session.refresh_from_disk("checkpoint")
    assert session.pending_restart_paths == {("script", "device", "serial")}

    store.patch_user_field("oas1", ("script", "device", "serial"), "old")
    session.refresh_from_disk("checkpoint")

    assert session.pending_restart_paths == set()
    assert session.config_state()["status"] == "current"


def test_warm_refresh_clears_pending_warm_but_not_cold(tmp_path):
    session, store = make_session(tmp_path, serial="old", limit_count=1)
    session.begin_device_initialization()
    session.freeze_startup_device_snapshot()
    store.patch_user_field("oas1", ("script", "device", "serial"), "new")
    session.report_config_changed([("script", "device", "serial")])
    session.report_config_changed([OROCHI_LIMIT])

    assert session.pending_restart_paths == {("script", "device", "serial")}
    assert OROCHI_LIMIT in session.pending_warm_paths

    result = session.refresh_from_disk("task_boundary")

    # WARM pending 被边界重建清除；COLD pending 独立保留
    assert session.pending_warm_paths == set()
    assert session.pending_restart_paths == {("script", "device", "serial")}
    assert result.status == "restart_required"


def test_new_session_rebuilds_startup_snapshot_and_clears_pending(tmp_path):
    """进程级重启 = 新 Config session：重新冻结新 COLD 快照，pending 为空。"""
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    store.patch_user_field("oas1", ("script", "device", "serial"), "new")

    session1 = Config("oas1", store=store)
    session1.begin_device_initialization()
    session1.freeze_startup_device_snapshot()
    store.patch_user_field("oas1", ("script", "device", "serial"), "new2")
    session1.refresh_from_disk("checkpoint")
    assert session1.pending_restart_paths == {("script", "device", "serial")}

    session2 = Config("oas1", store=store)
    session2.begin_device_initialization()
    session2.freeze_startup_device_snapshot()
    assert session2.model.script.device.serial == "new2"
    assert session2.pending_restart_paths == set()
    assert session2.config_state()["status"] == "current"


def test_config_state_structure(tmp_path):
    session, store = make_session(tmp_path, serial="old")
    session.begin_device_initialization()
    session.freeze_startup_device_snapshot()
    store.patch_user_field("oas1", ("script", "device", "serial"), "new")
    session.refresh_from_disk("checkpoint")

    state = session.config_state()
    assert state["pending_restart_paths"] == [["script", "device", "serial"]]
    assert state["pending_warm_paths"] == []
    assert state["observed_mtime_ns"] > 0
    assert state["status"] == "restart_required"


def test_cold_pending_does_not_abort_wait(tmp_path):
    """COLD pending 需要进程重启才生效，不中止调度等待；只有 WARM 变更才中止。"""
    session, store = make_session(tmp_path, serial="old")
    session.begin_device_initialization()
    session.freeze_startup_device_snapshot()
    store.patch_user_field("oas1", ("script", "device", "serial"), "new")
    session.refresh_from_disk("checkpoint")

    assert session.pending_restart_paths == {("script", "device", "serial")}
    assert session.has_pending_changes() is False

    # COLD 事件经 report_config_changed 也只进 pending_restart，不中止等待
    session.report_config_changed([("script", "device", "serial")])
    assert session.has_pending_changes() is False
    assert session.pending_restart_paths == {("script", "device", "serial")}

    # 只有 WARM 变更才中止等待
    session.report_config_changed([OROCHI_LIMIT])
    assert session.has_pending_changes() is True


def test_reload_policy_default_deny():
    policy = DEFAULT_RELOAD_POLICY
    assert policy.classify(("script", "device", "serial")) == COLD
    assert policy.classify(("script", "device", "screenshot", "interval")) == COLD
    assert policy.classify(("orochi", "orochi_config", "limit_count")) == WARM
    assert policy.classify(("running_task",)) == WARM
    assert policy.classify(("script", "optimization", "schedule_rule")) == WARM
    assert policy.classify(("meta_demon", "meta_demon_config", "md_strategy_count")) == WARM
    assert policy.classify(("find_jade", "invite_info_list_1", "name")) == WARM


def test_warm_refresh_rebuilds_cached_notifier(tmp_path):
    """边界 WARM 提交后实际 notifier 必须与新模型一致，不得沿用旧目标。"""
    session, store = make_session(tmp_path)
    first = session.notifier
    assert first.enable == session.model.script.error.notify_enable

    new_target = "provider: custom\nurl: https://new.invalid/notify"
    store.patch_user_field("oas1", ("script", "error", "notify_enable"), True)
    store.patch_user_field("oas1", ("script", "error", "notify_config"), new_target)
    session.report_config_changed([
        ("script", "error", "notify_enable"),
        ("script", "error", "notify_config"),
    ])

    session.refresh_from_disk("task_boundary")
    second = session.notifier

    assert second is not first
    assert second.enable is session.model.script.error.notify_enable is True
    assert second.provider_name == "custom"
    assert second.config["url"] == "https://new.invalid/notify"
    assert session.model.script.error.notify_config == new_target


# ---------- generation mismatch ----------

def test_generation_mismatch_stops_session_persistence(tmp_path):
    session, store = make_session(tmp_path)
    session.begin_device_initialization()
    session.freeze_startup_device_snapshot()
    store.delete_config("oas1")
    store.create_from_template("oas1", canonical_template())

    result = session.refresh_from_disk("task_boundary")
    assert result.generation_mismatch is True
    assert session.generation_mismatch is True
    # 后续 save 终止持久化，不写盘也不抛错
    session.save()
    assert store.load("oas1").generation != session.generation


def test_save_detects_generation_mismatch(tmp_path):
    session, store = make_session(tmp_path)
    session.begin_device_initialization()
    session.freeze_startup_device_snapshot()
    store.delete_config("oas1")
    store.create_from_template("oas1", canonical_template())

    session.save()

    assert session.generation_mismatch is True


# ---------- 跨进程队列方向 / mtime 兜底 ----------

def test_deliver_config_changed_sorts_dedups_and_drain_merges(tmp_path):
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    loaded = store.load("oas1")
    process = ScriptProcess("oas1", store=store)
    process.deliver_config_changed(loaded.generation, loaded.mtime_ns,
                                   [("b",), ("a",), ("a",)])

    s = make_script_shell(Config("oas1", store=store), process.config_event_queue, None)
    changed = s._drain_config_events()

    # 排序去重，且主进程→子进程方向正确
    assert changed == [("a",), ("b",)]


def test_script_drain_discards_stale_generation_event(tmp_path):
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    process = ScriptProcess("oas1", store=store)
    # 旧 generation 事件直接丢弃，真正的身份变化由 refresh_from_disk 检测
    process.deliver_config_changed("stale-gen", 1, [OROCHI_LIMIT])

    s = make_script_shell(Config("oas1", store=store), process.config_event_queue, None)
    changed = s._drain_config_events()

    assert changed == []


def test_mtime_fallback_detects_change_without_event(tmp_path):
    """丢失/无事件时，子进程仍以 mtime_ns 兜底检出磁盘变化。"""
    session, store = make_session(tmp_path)
    session.begin_device_initialization()
    session.freeze_startup_device_snapshot()
    session.start_watching()

    store.patch_user_field("oas1", OROCHI_LIMIT, 9)

    assert session.should_reload() is True
    session.refresh_from_disk("checkpoint")
    assert session.model.orochi.orochi_config.limit_count == 9


def test_start_watching_preserves_loaded_mtime_before_external_write(tmp_path):
    """start_watching 不能把未加载的外部版本当成会话基线。"""
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    session = Config("oas1", store=store)
    loaded_mtime = session.mtime_ns

    # 使用另一个 Store 模拟外部进程在 wait 入口前写入 B 版本。
    external_store = make_generation_store(tmp_path)
    external_store.patch_user_field("oas1", OROCHI_LIMIT, 9)
    assert external_store.load("oas1").mtime_ns > loaded_mtime

    session.start_watching()

    assert session._watch_mtime_ns == loaded_mtime
    assert session.should_reload() is True
    session.report_config_changed([OROCHI_LIMIT])
    session.refresh_from_disk("wait")
    assert session.model.orochi.orochi_config.limit_count == 9
    assert session.should_reload() is False


def test_start_watching_without_external_write_does_not_reload(tmp_path):
    """无外部写入时，保持原有的无变化路径。"""
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    session = Config("oas1", store=store)

    session.start_watching()

    assert session.should_reload() is False


def test_watcher_detects_deleted_config_without_event(tmp_path):
    """无事件删除配置时，watcher 必须触发 mismatch 刷新并请求停止。"""
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    session = Config("oas1", store=store)
    session.start_watching()

    store.delete_config("oas1")

    assert session.should_reload() is True
    result = session.refresh_from_disk("wait")
    assert result.generation_mismatch is True
    assert session.generation_mismatch is True


def test_watcher_detects_nonincreasing_mtime_with_content_change(tmp_path):
    """mtime 回退/不递增但内容变化时，digest fallback 仍须触发刷新。"""
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    session = Config("oas1", store=store)
    baseline = session.mtime_ns
    session.start_watching()

    store.patch_user_field("oas1", OROCHI_LIMIT, 9)
    config_path = store.generation._config_path("oas1")
    os.utime(config_path, ns=(baseline, baseline))

    assert session.should_reload() is True
    session.refresh_from_disk("wait")
    assert session.model.orochi.orochi_config.limit_count == 9


def test_watcher_save_commit_baseline_does_not_self_reload(tmp_path):
    """成功保存提交后，watcher 基线应吸收自身写入而不误报。"""
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    session = Config("oas1", store=store)
    session.model.orochi.orochi_config.limit_count = 9
    session.save()
    session.start_watching()

    assert session.should_reload() is False


def test_digest_permission_error_refresh_stops_without_event(tmp_path, monkeypatch):
    """digest 无事件路径遇到 PermissionError 时，refresh 应收敛为 mismatch 而非抛出。"""
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    session = Config("oas1", store=store)
    session.start_watching()
    config_path = store.generation._config_path("oas1")
    original_read_bytes = Path.read_bytes

    def deny_config_read(path):
        if path == config_path:
            raise PermissionError("injected shared-file conflict")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", deny_config_read)

    assert session.should_reload() is True
    result = session.refresh_from_disk("wait")
    assert result.generation_mismatch is True
    assert session.generation_mismatch is True


def test_event_refresh_permission_error_stops_without_raising(tmp_path, monkeypatch):
    """已有 config_changed 事件时，OSError 同样走统一停止语义。"""
    session, store = make_session(tmp_path)
    session.report_config_changed([OROCHI_LIMIT])

    def fail_load(_name):
        raise PermissionError("injected event load conflict")

    monkeypatch.setattr(store, "load", fail_load)

    result = session.refresh_from_disk("event")
    assert result.generation_mismatch is True
    assert session.generation_mismatch is True
    assert session.pending_warm_paths == set()


def test_config_checkpoint_reports_state_after_event(tmp_path):
    session, store = make_session(tmp_path, serial="old")
    session.begin_device_initialization()
    session.freeze_startup_device_snapshot()
    # 磁盘真实变化后再投递事件，refresh 以磁盘对比重算 COLD pending
    store.patch_user_field("oas1", ("script", "device", "serial"), "new")
    process = ScriptProcess("oas1", store=store)
    process.deliver_config_changed(store.load("oas1").generation, store.load("oas1").mtime_ns,
                                   [("script", "device", "serial")])

    s = make_script_shell(session, process.config_event_queue, queue_module.Queue())
    s._config_checkpoint("task_boundary")

    state = s.state_queue.get_nowait()["config_state"]
    assert state["status"] == "restart_required"
    assert state["pending_restart_paths"] == [["script", "device", "serial"]]


# ---------- inactive 首帧 / stop 清理 / start 复核 ----------

def test_inactive_first_frame_returns_empty_pending(tmp_path):
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    process = ScriptProcess("oas1", store=store)

    assert process.state == ScriptState.INACTIVE
    state = process.cached_config_state()
    assert state["status"] == "current"
    assert state["pending_restart_paths"] == []
    assert state["pending_warm_paths"] == []


def test_stop_clears_config_state_cache_and_queue(tmp_path):
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    process = ScriptProcess("oas1", store=store)
    process._config_state_cache = {
        "pending_restart_paths": [["script", "device", "serial"]],
        "pending_warm_paths": [],
        "observed_mtime_ns": 1,
        "status": "restart_required",
    }
    process.deliver_config_changed(store.load("oas1").generation, 1, [OROCHI_LIMIT])

    asyncio.run(process.stop())

    assert process._config_state_cache is None
    assert drain_queue(process.config_event_queue) == []


def test_start_refuses_generation_mismatch(tmp_path):
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    process = ScriptProcess("oas1", store=store)
    store.delete_config("oas1")
    store.create_from_template("oas1", canonical_template())

    asyncio.run(process.start())

    assert process.state == ScriptState.INACTIVE
    assert process._process is None


# ---------- review 修复回归 ----------

def test_wait_mismatch_clears_pending_warm_and_mtime(tmp_path):
    """mismatch 分支清空 WARM pending 使终端态收敛，并推进 mtime 但不吸收新身份。"""
    session, store = make_session(tmp_path)
    session.begin_device_initialization()
    session.freeze_startup_device_snapshot()
    session.report_config_changed([OROCHI_LIMIT])  # WARM pending
    before_mtime = session.mtime_ns
    store.delete_config("oas1")
    store.create_from_template("oas1", canonical_template())

    result = session.refresh_from_disk("wait")

    assert result.generation_mismatch is True
    assert session.generation_mismatch is True
    assert session.pending_warm_paths == set()
    assert session.has_pending_changes() is False
    assert session.mtime_ns != before_mtime
    assert session.generation != store.load("oas1").generation  # 不吸收新身份


def test_wait_until_exits_on_generation_mismatch(tmp_path, monkeypatch):
    """wait_until 排空 WARM 事件后检测到 mismatch 时立即 exit，不忽略返回值继续调度。

    复现 reviewer 场景：OASX patch WARM 字段事件入队 → 配置 delete+recreate 换身份
    → wait_until 因 WARM pending 触发刷新并命中 mismatch。
    """
    session, store = make_session(tmp_path)
    session.begin_device_initialization()
    session.freeze_startup_device_snapshot()
    process = ScriptProcess("oas1", store=store)
    loaded = store.load("oas1")
    process.deliver_config_changed(loaded.generation, loaded.mtime_ns, [OROCHI_LIMIT])
    store.delete_config("oas1")
    store.create_from_template("oas1", canonical_template())
    s = make_script_shell(session, process.config_event_queue, None)
    monkeypatch.setattr(script_module.time, "sleep", lambda sec: None)

    with pytest.raises(SystemExit):
        s.wait_until(datetime.now() + timedelta(seconds=1))


def test_config_deleted_refresh_returns_mismatch(tmp_path):
    """配置被 tombstone/删除时 refresh_from_disk 按 mismatch 语义干净返回，不抛 ConfigNotFoundError。"""
    session, store = make_session(tmp_path)
    session.begin_device_initialization()
    session.freeze_startup_device_snapshot()
    store.delete_config("oas1")

    result = session.refresh_from_disk("task_boundary")

    assert result.generation_mismatch is True
    assert session.generation_mismatch is True


def test_config_deleted_checkpoint_exits(tmp_path):
    """配置被删除时 _config_checkpoint 干净退出，而非带 traceback 崩溃。"""
    session, store = make_session(tmp_path)
    session.begin_device_initialization()
    session.freeze_startup_device_snapshot()
    store.delete_config("oas1")
    s = make_script_shell(session, None, None)

    with pytest.raises(SystemExit):
        s._config_checkpoint("task_boundary")


def test_status_reflects_generation_mismatch(tmp_path):
    """_status() 前置感知 generation mismatch，避免 device 无差异时误报 current。"""
    session, store = make_session(tmp_path)
    session.begin_device_initialization()
    session.freeze_startup_device_snapshot()
    store.delete_config("oas1")
    store.create_from_template("oas1", canonical_template())
    store.patch_user_field("oas1", ("script", "device", "serial"), "old")

    session.refresh_from_disk("checkpoint")

    assert session.pending_restart_paths == set()  # device 无差异
    assert session.config_state()["status"] == "restart_required"  # 因 mismatch


def test_stop_clears_state_queue_residue(tmp_path):
    """stop 后 state_queue 残留被清空，restart 不会消费上一进程上报。"""
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    process = ScriptProcess("oas1", store=store)
    process.state_queue.put({"config_state": {"status": "restart_required"}})

    asyncio.run(process.stop())

    assert drain_queue(process.state_queue) == []
    assert process._config_state_cache is None


def test_child_exit_clears_config_state(tmp_path):
    """子进程异常退出/mismatch 退出后，主进程 coroutine 检测到死亡并清理缓存置 INACTIVE。"""
    import multiprocessing as mp

    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    process = ScriptProcess("oas1", store=store)
    process._config_state_cache = {"status": "restart_required"}
    process.state = ScriptState.RUNNING
    # 用未启动的伪进程模拟已死亡的子进程（is_alive() 返回 False）
    process._process = mp.Process(target=lambda: None)

    async def _run():
        task = asyncio.create_task(process.coroutine_broadcast_state())
        await asyncio.sleep(0.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())

    assert process.state == ScriptState.INACTIVE
    assert process._config_state_cache is None


def test_stop_raises_when_process_remains_alive_after_kill(tmp_path):
    """kill 后仍存活时 stop 必须抛错并保留进程引用，阻止生命周期事务提交。"""
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    process = ScriptProcess("oas1", store=store)

    class UnkillableProc:
        def __init__(self):
            self.join_calls = []
            self.kill_calls = 0

        def is_alive(self):
            return True

        def terminate(self):
            pass

        def join(self, timeout=0):
            self.join_calls.append(timeout)

        def kill(self):
            self.kill_calls += 1

    fake = UnkillableProc()
    process._process = fake

    with pytest.raises(RuntimeError, match="subprocess kill failed"):
        asyncio.run(process.stop())

    assert fake.join_calls == [0.7, 2.0]
    assert fake.kill_calls == 1
    assert process._process is fake


def test_start_stops_live_process_on_generation_mismatch(tmp_path):
    """start() 复核 generation 失败时，若存在存活旧进程同样终止，避免遗留无人管理子进程。"""
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    process = ScriptProcess("oas1", store=store)
    store.delete_config("oas1")
    store.create_from_template("oas1", canonical_template())

    class FakeProc:
        def __init__(self):
            self.killed = False
            self.alive = True

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.killed = True

        def join(self, timeout=0):
            pass

        def kill(self):
            self.killed = True
            self.alive = False

    fake = FakeProc()
    process._process = fake

    asyncio.run(process.start())

    assert fake.killed is True
    assert process._process is None
    assert process.state == ScriptState.INACTIVE


# ---------- 第二轮 review 修复回归 ----------

def test_spawn_start_exception_cleans_local_live_process_and_preserves_error(tmp_path, monkeypatch):
    """Process.start 已产生存活子进程后抛错时，必须回收局部进程并保留原异常。"""
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    process = ScriptProcess("oas1", store=store)
    created = []

    class FaultyProcess:
        def __init__(self, *args, **kwargs):
            self.alive = False
            self.terminated = False
            self.killed = False
            self.joins = []
            created.append(self)

        def start(self):
            self.alive = True
            raise RuntimeError("spawn failed after child creation")

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminated = True

        def join(self, timeout=0):
            self.joins.append(timeout)

        def kill(self):
            self.killed = True
            self.alive = False

    monkeypatch.setattr(script_process_module.multiprocessing, "Process", FaultyProcess)

    with pytest.raises(RuntimeError, match="spawn failed after child creation"):
        asyncio.run(process.start())

    faulty = created[0]
    assert faulty.terminated is True
    assert faulty.killed is True
    assert faulty.joins == [0.7, 2.0]
    assert faulty.is_alive() is False
    assert process._process is None
    assert process.state == ScriptState.INACTIVE


def test_spawn_cleanup_retains_unreaped_handle_for_retry(tmp_path, monkeypatch):
    """terminate/kill 均失败时保留句柄，后续 stop 成功后才清空引用。"""
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    process = ScriptProcess("oas1", store=store)
    created = []

    class UnkillableProcess:
        def __init__(self, *args, **kwargs):
            self.alive = False
            self.allow_cleanup = False
            self.terminate_calls = 0
            self.kill_calls = 0
            created.append(self)

        def start(self):
            self.alive = True
            raise RuntimeError("spawn original error")

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminate_calls += 1
            if not self.allow_cleanup:
                raise PermissionError("terminate denied")
            self.alive = False

        def join(self, timeout=0):
            return None

        def kill(self):
            self.kill_calls += 1
            if not self.allow_cleanup:
                raise PermissionError("kill denied")
            self.alive = False

    monkeypatch.setattr(script_process_module.multiprocessing, "Process", UnkillableProcess)

    with pytest.raises(RuntimeError, match="spawn original error"):
        asyncio.run(process.start())

    child = created[0]
    assert child.terminate_calls == 1
    assert child.kill_calls == 1
    assert child.is_alive() is True
    assert process._process is child
    # 仍存活且句柄可达时保持 RUNNING，不能虚报已停。
    assert process.state == ScriptState.RUNNING

    child.allow_cleanup = True
    asyncio.run(process.stop())
    assert child.is_alive() is False
    assert process._process is None


def test_lifecycle_lock_serializes_concurrent_start_and_stop(tmp_path, monkeypatch):
    """start 提交后广播暂停期间 stop 仍可回收句柄，最终不能留下不可见子进程。"""
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    process = ScriptProcess("oas1", store=store)
    created = []

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.alive = False
            created.append(self)

        def start(self):
            self.alive = True

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.alive = False

        def join(self, timeout=0):
            return None

        def kill(self):
            self.alive = False

    monkeypatch.setattr(script_process_module.multiprocessing, "Process", FakeProcess)

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        first_running_broadcast = True

        async def broadcast(data):
            nonlocal first_running_broadcast
            if data.get("state") == ScriptState.RUNNING and first_running_broadcast:
                first_running_broadcast = False
                entered.set()
                await release.wait()

        process.broadcast_state = broadcast
        start_task = asyncio.create_task(process.start())
        await entered.wait()
        stop_task = asyncio.create_task(process.stop())
        await asyncio.sleep(0)
        # 广播已移出 lifecycle lock，stop 不应再被永不返回的广播卡住。
        assert stop_task.done() is True
        release.set()
        assert await start_task is True
        await stop_task

        # 覆盖已运行实例重复 start，以及重复 stop 的幂等收敛。
        assert await process.start() is True
        assert await process.start() is True
        await process.stop()
        await process.stop()

    asyncio.run(scenario())
    assert all(child.is_alive() is False for child in created)
    assert process._process is None
    assert process.state == ScriptState.INACTIVE


@pytest.mark.parametrize("error", [OSError("broadcast failed"), asyncio.CancelledError()])
def test_start_broadcast_failure_keeps_started_process_consistent(tmp_path, monkeypatch, error):
    """start 广播失败/取消发生在 spawn 后，句柄与 RUNNING 状态必须保持一致。"""
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    process = ScriptProcess("oas1", store=store)

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.alive = False

        def start(self):
            self.alive = True

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.alive = False

        def join(self, timeout=0):
            return None

        def kill(self):
            self.alive = False

    async def fail_broadcast(_data):
        raise error

    monkeypatch.setattr(script_process_module.multiprocessing, "Process", FakeProcess)
    process.broadcast_state = fail_broadcast

    if isinstance(error, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(process.start())
    else:
        # 普通广播异常只记录，不能破坏已经提交的启动事务。
        assert asyncio.run(process.start()) is True

    assert process.state == ScriptState.RUNNING
    assert process._process is not None and process._process.is_alive()


@pytest.mark.parametrize("error", [OSError("broadcast failed"), asyncio.CancelledError()])
def test_stop_broadcast_failure_happens_after_process_cleanup(tmp_path, error):
    """stop 广播失败/取消不得阻止进程终止，终态必须与真实进程一致。"""
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    process = ScriptProcess("oas1", store=store)

    class LiveProcess:
        def __init__(self):
            self.alive = True

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.alive = False

        def join(self, timeout=0):
            return None

        def kill(self):
            self.alive = False

    child = LiveProcess()
    process._process = child
    process.state = ScriptState.RUNNING

    async def fail_broadcast(_data):
        raise error

    process.broadcast_state = fail_broadcast
    if isinstance(error, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(process.stop())
    else:
        asyncio.run(process.stop())

    assert child.is_alive() is False
    assert process._process is None
    assert process.state == ScriptState.INACTIVE


def test_spawn_cleanup_never_loses_local_handle(tmp_path, monkeypatch):
    """spawn 原错清理阶段即使任务被取消，也必须先清理或保留可达句柄。"""
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    process = ScriptProcess("oas1", store=store)
    created = []

    class FaultyProcess:
        def __init__(self, *args, **kwargs):
            self.alive = False
            created.append(self)

        def start(self):
            self.alive = True
            raise RuntimeError("spawn original error")

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.alive = False

        def join(self, timeout=0):
            return None

        def kill(self):
            self.alive = False

    monkeypatch.setattr(script_process_module.multiprocessing, "Process", FaultyProcess)

    with pytest.raises(RuntimeError, match="spawn original error"):
        asyncio.run(process.start())

    child = created[0]
    assert child.is_alive() is False
    assert process._process is None
    assert process.state == ScriptState.INACTIVE


def test_start_double_start_restores_running(tmp_path, monkeypatch):
    """generation 匹配 + 旧进程存活时再次 start：终止旧进程、恢复 RUNNING 并 spawn 新进程。

    修复 double-start 后 state 卡死 INACTIVE 导致新进程状态通道失效的问题。
    """
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    process = ScriptProcess("oas1", store=store)

    class OldProc:
        def __init__(self):
            self.killed = False
            self.alive = True

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.killed = True

        def join(self, timeout=0):
            pass

        def kill(self):
            self.killed = True
            self.alive = False

    old = OldProc()
    process._process = old

    spawned = []

    class NewProc:
        def __init__(self, *args, **kwargs):
            self.args = args

        def start(self):
            spawned.append(self)

    monkeypatch.setattr(script_process_module.multiprocessing, "Process", NewProc)

    asyncio.run(process.start())

    assert old.killed is True
    assert process.state == ScriptState.RUNNING  # double-start 后恢复 RUNNING，状态通道可消费
    assert len(spawned) == 1
    assert process._config_state_cache is None


def test_save_catches_generation_error_after_delete(tmp_path):
    """配置 delete 后（tombstone）save() 捕获 ConfigGenerationError，按 mismatch 终止不崩溃。"""
    session, store = make_session(tmp_path)
    session.begin_device_initialization()
    session.freeze_startup_device_snapshot()
    store.delete_config("oas1")

    session.save()

    assert session.generation_mismatch is True


def test_refresh_catches_generation_error_on_missing_config(tmp_path):
    """active sidecar 但配置物理缺失 → refresh_from_disk 按 mismatch 干净返回。"""
    session, store = make_session(tmp_path)
    session.begin_device_initialization()
    session.freeze_startup_device_snapshot()
    config_path = store.generation._config_path("oas1")
    config_path.unlink()

    result = session.refresh_from_disk("checkpoint")

    assert result.generation_mismatch is True
    assert session.generation_mismatch is True


def test_refresh_catches_json_error_on_corrupt_config(tmp_path):
    """JSON 损坏 → refresh_from_disk 按 mismatch 干净返回，不穿透崩溃。"""
    session, store = make_session(tmp_path)
    session.begin_device_initialization()
    session.freeze_startup_device_snapshot()
    config_path = store.generation._config_path("oas1")
    config_path.write_text("{corrupt json", encoding="utf-8")

    result = session.refresh_from_disk("checkpoint")

    assert result.generation_mismatch is True
    assert session.generation_mismatch is True


def test_refresh_converges_on_lock_timeout(tmp_path):
    """另一进程持锁超时 → refresh_from_disk 按 mismatch 干净停止，不穿透崩溃。"""
    store = ConfigStore(config_root=tmp_path / "config", timeout=0.5)
    store.create_from_template("oas1", canonical_template())
    session = Config("oas1", store=store)
    session.begin_device_initialization()
    session.freeze_startup_device_snapshot()

    lock = store.generation._lifecycle_lock("oas1")
    lock.acquire()
    try:
        result = session.refresh_from_disk("checkpoint")
    finally:
        lock.release()

    assert result.generation_mismatch is True
    assert session.generation_mismatch is True


def test_save_converges_on_lock_timeout(tmp_path):
    """另一进程持锁超时 → save() 按 mismatch 干净停止，不穿透崩溃。"""
    store = ConfigStore(config_root=tmp_path / "config", timeout=0.5)
    store.create_from_template("oas1", canonical_template())
    session = Config("oas1", store=store)
    session.begin_device_initialization()
    session.freeze_startup_device_snapshot()

    lock = store.generation._lifecycle_lock("oas1")
    lock.acquire()
    try:
        session.save()
    finally:
        lock.release()

    assert session.generation_mismatch is True





def test_stop_retries_recovery_when_alive_check_always_raises(tmp_path):
    """is_alive 永久抛 OSError 时，每次 stop 仍尝试 terminate/kill 并保留句柄。"""
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    process = ScriptProcess("oas1", store=store)
    process.state = ScriptState.RUNNING

    class UnknownProcess:
        def __init__(self):
            self.terminate_calls = 0
            self.kill_calls = 0
            self.join_calls = []

        def is_alive(self):
            raise OSError("alive state unavailable")

        def terminate(self):
            self.terminate_calls += 1

        def join(self, timeout=0):
            self.join_calls.append(timeout)

        def kill(self):
            self.kill_calls += 1

    child = UnknownProcess()
    process._process = child

    with pytest.raises(OSError, match="alive state unavailable"):
        asyncio.run(process.stop())
    with pytest.raises(OSError, match="alive state unavailable"):
        asyncio.run(process.stop())

    assert child.terminate_calls == 2
    assert child.kill_calls == 2
    assert child.join_calls == [0.7, 2.0, 0.7, 2.0]
    assert process._process is child
    assert process.state == ScriptState.RUNNING


def test_state_broadcaster_keeps_managing_unknown_alive_handle(tmp_path):
    """is_alive 查询异常不能让状态广播 task 永久退出；取消必须由调用方显式完成。"""
    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    process = ScriptProcess("oas1", store=store)
    process.state = ScriptState.RUNNING

    class UnknownProcess:
        def is_alive(self):
            raise OSError("alive state unavailable")

    process._process = UnknownProcess()

    async def scenario():
        task = asyncio.create_task(process.coroutine_broadcast_state())
        await asyncio.sleep(0.25)
        assert task.done() is False
        # 真实取消 task，验证 broadcaster 的退出不是由 alive 查询异常触发。
        task.cancel()
        await task

    asyncio.run(scenario())



def test_real_child_generation_handshake_refuses_post_spawn_aba(tmp_path):
    """真实 child 在 spawn 后遇到 delete+create ABA 时发送 failed，父端不报告启动成功。"""
    import asyncio
    import multiprocessing
    import module.server.script_process as script_process_module

    from module.config.config_store import ConfigGenerationMismatchError

    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    expected_generation = store.load("oas1").generation
    process = ScriptProcess("oas1", store=store)
    gate = multiprocessing.Event()
    process._spawn_attempt_nonce = "real-aba-attempt"
    child = multiprocessing.Process(
        target=_child_verify_generation,
        args=(
            str(store.config_root),
            "oas1",
            expected_generation,
            process._spawn_attempt_nonce,
            process.ready_queue,
            gate,
        ),
    )
    child.start()

    # child 已完成 spawn 但尚未读取配置，此时替换同名 identity。
    store.delete_config("oas1")
    store.create_from_template("oas1", canonical_template())
    gate.set()
    process._process = child
    process.state = script_process_module.ScriptState.RUNNING

    with pytest.raises(ConfigGenerationMismatchError):
        asyncio.run(
            process._wait_for_spawn_handshake(child, process._spawn_attempt_nonce)
        )

    child.join(timeout=5)
    assert child.is_alive() is False
    process.broadcast_state = lambda _data: None
    asyncio.run(process.stop())
    assert process._process is None
    assert process.state == script_process_module.ScriptState.INACTIVE



def _child_emit_handshake_messages(ready_queue, messages):
    """真实 child 依次投递旧/新 attempt 握手，模拟 feeder 延迟。"""
    import time

    for delay, message in messages:
        time.sleep(delay)
        ready_queue.put(message)


def test_spawn_handshake_ignores_delayed_old_nonce_before_new_failure(tmp_path):
    """旧同 generation ready 延迟到达时不能覆盖新 attempt 的 failed。"""
    import asyncio
    import multiprocessing
    from module.config.config_store import ConfigGenerationMismatchError

    store = make_generation_store(tmp_path)
    store.create_from_template("oas1", canonical_template())
    process = ScriptProcess("oas1", store=store)
    process._spawn_attempt_nonce = "new-attempt"
    messages = [
        (0.0, {
            "status": "ready",
            "generation": process.generation,
            "nonce": "old-attempt",
        }),
        (0.03, {
            "status": "failed",
            "generation": "replacement-generation",
            "nonce": "new-attempt",
            "reason": "generation_mismatch",
        }),
    ]
    child = multiprocessing.Process(
        target=_child_emit_handshake_messages,
        args=(process.ready_queue, messages),
    )
    child.start()

    with pytest.raises(ConfigGenerationMismatchError):
        asyncio.run(
            process._wait_for_spawn_handshake(child, process._spawn_attempt_nonce)
        )

    child.join(timeout=5)
    assert child.is_alive() is False
