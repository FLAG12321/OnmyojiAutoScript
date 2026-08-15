import hashlib
import importlib
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from filelock import Timeout

from module.config.config_store import ConfigJsonError, ConfigNotFoundError, ConfigStore
from module.config.config_validation import ConfigValidationError
from module.config.config_generation import ConfigGenerationError
from module.server.config_manager import (
    ConfigAlreadyExistsError,
    ConfigJsonError as ManagerConfigJsonError,
    ConfigNameError,
    ConfigValidationError as ManagerConfigValidationError,
)


def _canonical_template() -> dict:
    raw = json.loads((Path.cwd() / "config" / "template.json").read_text(encoding="utf-8"))
    raw["meta_demon"].pop("md_strategies_1", None)
    return raw


class RecordingScriptProcess:
    """记录 stop 事件顺序的假脚本进程；shared 用于与 Store 提交事件合并成统一时间线。"""

    def __init__(self, name, shared_events=None):
        self.name = name
        self.events = []
        self._shared = shared_events
        self.state = 1

    async def start(self):
        self.events.append("start")
        if self._shared is not None:
            self._shared.append("start")
        self.state = 1

    async def stop(self):
        self.events.append("stop_begin")
        if self._shared is not None:
            self._shared.append("stop_begin")
        self.state = 0
        self.events.append("stop_complete")
        if self._shared is not None:
            self._shared.append("stop_complete")

    def is_alive(self):
        return False


class RestoreFailingScriptProcess(RecordingScriptProcess):
    """模拟恢复 start 在改状态前或 spawn 后失败，并记录清理后的可观察状态。"""

    def __init__(self, name, failure_phase):
        super().__init__(name)
        self.failure_phase = failure_phase
        self.spawned = True

    async def start(self):
        self.events.append("start_begin")
        if self.failure_phase == "before_state":
            raise RuntimeError("restore start failed before state")
        self.state = 1
        self.spawned = True
        raise RuntimeError("restore start failed after spawn")

    async def stop(self):
        self.events.append("stop_begin")
        self.state = 0
        self.spawned = False
        self.events.append("stop_complete")


class RecordingStore(ConfigStore):
    """在生命周期提交点记录事件的真实 Store。"""

    def __init__(self, root):
        super().__init__(config_root=root)
        self.events = []

    def rename_config(self, source, destination):
        self.events.append("rename_commit")
        return super().rename_config(source, destination)

    def delete_config(self, name):
        self.events.append("delete_commit")
        return super().delete_config(name)


def _make_manager(store, processes):
    from module.server.main_manager import MainManager

    manager = MainManager(store=store)
    manager.script_process = dict(processes)
    for name, process in manager.script_process.items():
        # 生命周期恢复现要求 generation 精确匹配；为旧测试替身补齐真实磁盘身份。
        if not hasattr(process, "generation"):
            process.generation = store.load(name).generation
    return manager


def test_rename_awaits_stop_before_store_commit_and_rebuilds_registry(tmp_path):
    import asyncio

    store = RecordingStore(tmp_path / "config")
    store.initialize()
    store.create_from_template("old", _canonical_template())
    process = RecordingScriptProcess("old", shared_events=store.events)
    manager = _make_manager(store, {"old": process})

    asyncio.run(manager.rename_config("old", "new"))

    assert process.events[:2] == ["stop_begin", "stop_complete"]
    assert store.events.index("rename_commit") > process.events.index("stop_complete")
    assert set(manager.script_process) == {"new"}
    assert manager.store.load("new").canonical["config_name"] == "new"
    assert "old" not in manager.script_process


def test_delete_awaits_stop_and_removes_old_registry(tmp_path):
    import asyncio

    store = RecordingStore(tmp_path / "config")
    store.initialize()
    store.create_from_template("oas1", _canonical_template())
    process = RecordingScriptProcess("oas1", shared_events=store.events)
    manager = _make_manager(store, {"oas1": process})

    asyncio.run(manager.delete_config("oas1"))

    assert process.events[:2] == ["stop_begin", "stop_complete"]
    assert store.events.index("delete_commit") > process.events.index("stop_complete")
    assert "oas1" not in manager.script_process
    assert "oas1" not in manager.store.active_config_names()


def test_rename_updates_registry_without_enumerating_unrelated_identities(tmp_path, monkeypatch):
    import asyncio

    store = RecordingStore(tmp_path / "config")
    store.initialize()
    store.create_from_template("old", _canonical_template())
    store.create_from_template("unrelated", _canonical_template())
    old_process = RecordingScriptProcess("old")
    unrelated_process = RecordingScriptProcess("unrelated")
    manager = _make_manager(
        store,
        {"old": old_process, "unrelated": unrelated_process},
    )

    def fail_enumeration(*_args, **_kwargs):
        # rename 已知只改变两个身份，不应依赖可能因无关身份锁超时而失败的全量枚举。
        raise TimeoutError("injected unrelated enumeration timeout")

    monkeypatch.setattr(store, "active_config_names", fail_enumeration)
    asyncio.run(manager.rename_config("old", "new"))

    assert set(manager.script_process) == {"new", "unrelated"}
    assert manager.script_process["unrelated"] is unrelated_process
    assert unrelated_process.events == []


@pytest.mark.parametrize("constructor_error", [OSError, TimeoutError])
def test_rename_committed_destination_constructor_failure_raises_config_generation_error(
    tmp_path,
    monkeypatch,
    constructor_error,
):
    """真实 Store rename 提交后 destination wrapper 构造失败：必须抛 ConfigGenerationError，
    保留已提交磁盘状态（source tombstone、destination active），source registry 已移除，
    且 destination 后续可被 ensure_script_process 按已提交身份重建，不能假成功。"""
    import asyncio
    import module.server.main_manager as manager_module
    from module.config.config_generation import ConfigGenerationError

    store = RecordingStore(tmp_path / "config")
    store.initialize()
    store.create_from_template("old", _canonical_template())
    old_process = RecordingScriptProcess("old")
    manager = _make_manager(store, {"old": old_process})
    real_process_type = manager_module.ScriptProcess

    def fail_destination(name, *args, **kwargs):
        if name == "new":
            raise constructor_error("injected destination constructor failure")
        return real_process_type(name, *args, **kwargs)

    monkeypatch.setattr(manager_module, "ScriptProcess", fail_destination)

    # 磁盘 rename 已提交但 destination wrapper 构造失败时，不得假成功：必须抛 ConfigGenerationError。
    with pytest.raises(
        ConfigGenerationError, match="destination registry reconciliation"
    ):
        asyncio.run(manager.rename_config("old", "new"))

    # 已提交磁盘状态保留；source registry 移除且 destination 未假成功安装。
    assert "old" not in manager.script_process
    assert "new" not in manager.script_process
    assert store.generation.read_active_generation("old").state == "tombstone"
    assert store.generation.read_active_generation("new").state == "active"
    assert store.load("new").canonical["config_name"] == "new"

    # 后续 ensure 可按已提交目标身份重建 wrapper，证明磁盘身份一致、未假成功。
    monkeypatch.setattr(manager_module, "ScriptProcess", real_process_type)
    recovered = asyncio.run(manager.ensure_script_process("new"))
    assert manager.script_process["new"] is recovered


def test_delete_updates_registry_without_enumerating_unrelated_identities(tmp_path, monkeypatch):
    import asyncio

    store = RecordingStore(tmp_path / "config")
    store.initialize()
    store.create_from_template("target", _canonical_template())
    store.create_from_template("unrelated", _canonical_template())
    target_process = RecordingScriptProcess("target")
    unrelated_process = RecordingScriptProcess("unrelated")
    manager = _make_manager(
        store,
        {"target": target_process, "unrelated": unrelated_process},
    )
    monkeypatch.setattr(
        store,
        "active_config_names",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            TimeoutError("injected unrelated enumeration timeout")
        ),
    )

    asyncio.run(manager.delete_config("target"))

    assert set(manager.script_process) == {"unrelated"}
    assert manager.script_process["unrelated"] is unrelated_process
    assert unrelated_process.events == []


@pytest.mark.skipif(os.name != "nt", reason="Windows case-insensitive identity only")
def test_case_only_rename_fails_before_stopping_process(tmp_path):
    import asyncio
    from module.config.config_generation import ConfigIdentityNameError

    store = RecordingStore(tmp_path / "config")
    store.initialize()
    store.create_from_template("oas1", _canonical_template())
    process = RecordingScriptProcess("oas1")
    manager = _make_manager(store, {"oas1": process})

    with pytest.raises(ConfigIdentityNameError):
        asyncio.run(manager.rename_config("oas1", "OAS1"))

    assert process.events == []
    assert set(manager.script_process) == {"oas1"}
    assert store.load("oas1").canonical["config_name"] == "oas1"


def test_stop_failure_prevents_store_commit(tmp_path):
    import asyncio

    class FailingProcess(RecordingScriptProcess):
        async def stop(self):
            self.events.append("stop_begin")
            self.state = 0
            raise RuntimeError("stop failed")

    store = RecordingStore(tmp_path / "config")
    store.initialize()
    store.create_from_template("old", _canonical_template())
    process = FailingProcess("old")
    manager = _make_manager(store, {"old": process})

    with pytest.raises(RuntimeError):
        asyncio.run(manager.rename_config("old", "new"))

    # Store 事务未开始：old 仍 active，new 不存在，注册保持 source
    assert "rename_commit" not in store.events
    assert store.generation.read_active_generation("old").state == "active"
    assert store.generation.read_active_generation("new") is None
    assert "old" in manager.script_process
    assert process.state == 1
    assert process.events[-1] == "start"


def test_stop_failure_prevents_delete_commit(tmp_path):
    import asyncio

    class FailingProcess(RecordingScriptProcess):
        async def stop(self):
            self.events.append("stop_begin")
            self.state = 0
            raise RuntimeError("stop failed")

    store = RecordingStore(tmp_path / "config")
    store.initialize()
    store.create_from_template("oas1", _canonical_template())
    process = FailingProcess("oas1")
    manager = _make_manager(store, {"oas1": process})

    with pytest.raises(RuntimeError):
        asyncio.run(manager.delete_config("oas1"))

    assert "delete_commit" not in store.events
    assert store.generation.read_active_generation("oas1").state == "active"
    assert manager.script_process["oas1"] is process
    assert process.state == 1
    assert process.events[-1] == "start"


def test_rename_existing_destination_keeps_source_running(tmp_path):
    """真实 MainManager 预检目标冲突时不得停止 source。"""
    import asyncio
    from module.config.config_generation import ConfigIdentityConflictError

    store = RecordingStore(tmp_path / "config")
    store.initialize()
    store.create_from_template("source", _canonical_template())
    store.create_from_template("destination", _canonical_template())
    process = RecordingScriptProcess("source")
    manager = _make_manager(store, {"source": process})

    with pytest.raises(ConfigIdentityConflictError):
        asyncio.run(manager.rename_config("source", "destination"))

    assert process.events == []
    assert process.state == 1
    assert manager.script_process["source"] is process
    assert store.load("source").canonical["config_name"] == "source"
    assert store.load("destination").canonical["config_name"] == "destination"


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(OSError("injected rename I/O before commit"), id="io-error"),
        pytest.param(Timeout("injected rename lock timeout"), id="lock-timeout"),
    ],
)
def test_rename_store_failure_before_commit_restores_source_running(
    tmp_path, monkeypatch, failure
):
    """stop 完成后 rename 未提交失败时，必须恢复原 source 运行状态。"""
    import asyncio

    store = RecordingStore(tmp_path / "config")
    store.initialize()
    store.create_from_template("source", _canonical_template())
    process = RecordingScriptProcess("source")
    manager = _make_manager(store, {"source": process})

    def fail_before_commit(*_args, **_kwargs):
        # 模拟锁超时或 I/O 在事务提交点前失败。
        raise failure

    monkeypatch.setattr(store, "rename_config", fail_before_commit)
    with pytest.raises(type(failure)):
        asyncio.run(manager.rename_config("source", "destination"))

    assert process.events == ["stop_begin", "stop_complete", "start"]
    assert process.state == 1
    assert manager.script_process["source"] is process
    assert store.generation.read_active_generation("source").state == "active"
    assert store.generation.read_active_generation("destination") is None


def test_rename_racing_destination_conflict_restores_source_running(tmp_path, monkeypatch):
    """目标在预检后并发创建时，rename 未提交且 source 必须恢复运行。"""
    import asyncio
    from module.config.config_generation import ConfigIdentityConflictError

    store = RecordingStore(tmp_path / "config")
    store.initialize()
    store.create_from_template("source", _canonical_template())
    process = RecordingScriptProcess("source")
    manager = _make_manager(store, {"source": process})
    original_rename = store.rename_config

    def conflict_after_stop(source, destination):
        # 模拟预检与正式事务之间由并发请求创建目标身份。
        store.create_from_template(destination, _canonical_template())
        original_rename(source, destination)

    monkeypatch.setattr(store, "rename_config", conflict_after_stop)
    with pytest.raises(ConfigIdentityConflictError):
        asyncio.run(manager.rename_config("source", "destination"))

    assert process.events == ["stop_begin", "stop_complete", "start"]
    assert process.state == 1
    assert manager.script_process["source"] is process
    assert store.generation.read_active_generation("source").state == "active"
    assert store.generation.read_active_generation("destination").state == "active"


def test_delete_store_failure_before_commit_restores_source_running(tmp_path, monkeypatch):
    """stop 完成后 delete 未提交失败时，必须恢复原 source 运行状态。"""
    import asyncio

    store = RecordingStore(tmp_path / "config")
    store.initialize()
    store.create_from_template("source", _canonical_template())
    process = RecordingScriptProcess("source")
    manager = _make_manager(store, {"source": process})

    def fail_before_commit(*_args, **_kwargs):
        # 模拟锁超时或 I/O 在 tombstone 提交前失败。
        raise OSError("injected delete before commit")

    monkeypatch.setattr(store, "delete_config", fail_before_commit)
    with pytest.raises(OSError, match="before commit"):
        asyncio.run(manager.delete_config("source"))

    assert process.events == ["stop_begin", "stop_complete", "start"]
    assert process.state == 1
    assert manager.script_process["source"] is process
    assert store.generation.read_active_generation("source").state == "active"


def test_rename_failure_after_commit_reconciles_registry_to_destination(tmp_path, monkeypatch):
    """Store 在提交后抛错时 registry 必须按磁盘身份移除旧 source。"""
    import asyncio

    store = RecordingStore(tmp_path / "config")
    store.initialize()
    store.create_from_template("source", _canonical_template())
    process = RecordingScriptProcess("source")
    manager = _make_manager(store, {"source": process})

    def fail_at_commit(point):
        # committed journal 是 rename 提交点；后续由 MainManager 对账时前滚。
        if point == "rename.after_committed_journal":
            raise OSError("injected rename after commit")

    monkeypatch.setattr(store.generation.fault_injector, "hit", fail_at_commit)
    with pytest.raises(OSError, match="after commit"):
        asyncio.run(manager.rename_config("source", "destination"))

    assert "source" not in manager.script_process
    assert "destination" in manager.script_process
    assert process.events == ["stop_begin", "stop_complete"]
    assert store.generation.read_active_generation("source").state == "tombstone"
    assert store.generation.read_active_generation("destination").state == "active"


@pytest.mark.parametrize("stop_mode", ["live", "unknown", "stop_error"])
def test_rename_rejects_stale_destination_occupancy_before_source_stop(
    tmp_path, stop_mode
):
    """目标磁盘 tombstone 但 registry 残留无法确认退出的旧 wrapper 时，rename 必须在
    停止 source 与 Store 提交前以 ConfigIdentityConflictError 拒绝，保留目标 wrapper/句柄。"""
    import asyncio
    from module.config.config_generation import ConfigIdentityConflictError
    from module.server.script_process import ScriptState

    class DestinationHandle:
        def __init__(self, mode):
            self.mode = mode
            self.alive = mode == "live"

        def is_alive(self):
            if self.mode == "unknown":
                return None
            return self.alive

    class StaleDestinationProcess:
        generation = "stale-destination-generation"

        def __init__(self, mode):
            self.mode = mode
            self.state = ScriptState.RUNNING
            self._process = DestinationHandle(mode)
            self.stop_calls = 0

        async def stop(self):
            self.stop_calls += 1
            if self.mode == "stop_error":
                raise RuntimeError("injected destination stop failure")
            self.state = ScriptState.INACTIVE

    store = RecordingStore(tmp_path / "config")
    store.initialize()
    store.create_from_template("source", _canonical_template())
    store.create_from_template("destination", _canonical_template())
    # 先删除 destination 使磁盘 tombstone，同时 registry 保留删除时未退役的旧 wrapper。
    store.delete_config("destination")
    source_process = RecordingScriptProcess("source")
    destination_process = StaleDestinationProcess(stop_mode)
    manager = _make_manager(
        store,
        {"source": source_process, "destination": destination_process},
    )

    with pytest.raises(ConfigIdentityConflictError):
        asyncio.run(manager.rename_config("source", "destination"))

    # source 原运行不得被 stop，Store 未提交，destination 仍 tombstone。
    assert source_process.events == []
    assert source_process.state == 1
    assert manager.script_process["source"] is source_process
    assert store.generation.read_active_generation("source").state == "active"
    assert store.generation.read_active_generation("destination").state == "tombstone"
    with pytest.raises(ConfigNotFoundError):
        store.load("destination")
    # 旧 wrapper 与句柄保留，可供后续重试。
    assert manager.script_process["destination"] is destination_process
    assert destination_process._process is not None


def test_rename_reuses_confirmed_inactive_destination_and_installs_new_generation(tmp_path):
    """目标磁盘 tombstone 且 registry 旧 wrapper 可安全退役时，rename 提交并安装新 generation。"""
    import asyncio
    from module.server.script_process import ScriptState

    class RetireableDestinationProcess:
        generation = "stale-destination-generation"

        def __init__(self):
            self.state = ScriptState.INACTIVE
            self._process = None
            self.stop_calls = 0

        async def stop(self):
            self.stop_calls += 1
            self.state = ScriptState.INACTIVE

    store = RecordingStore(tmp_path / "config")
    store.initialize()
    store.create_from_template("source", _canonical_template())
    store.create_from_template("destination", _canonical_template())
    store.delete_config("destination")
    source_process = RecordingScriptProcess("source")
    destination_process = RetireableDestinationProcess()
    manager = _make_manager(
        store,
        {"source": source_process, "destination": destination_process},
    )

    asyncio.run(manager.rename_config("source", "destination"))

    assert store.generation.read_active_generation("source").state == "tombstone"
    assert store.load("destination").canonical["config_name"] == "destination"
    assert "source" not in manager.script_process
    # 旧 wrapper 在预检中安全退役，registry 安装与新 generation 精确一致的对象。
    assert destination_process.stop_calls == 1
    destination_wrapper = manager.script_process["destination"]
    assert destination_wrapper is not destination_process
    assert destination_wrapper.generation == store.load("destination").generation
    assert destination_wrapper.state == ScriptState.INACTIVE
    assert destination_wrapper._process is None


@pytest.mark.parametrize("retireable", [True, False])
def test_reconcile_destination_generation_mismatch_replaces_or_fails(
    tmp_path, retireable
):
    """Store 已提交后对账目标旧 generation wrapper：可退役则替换，无法退役则保留并显式失败。"""
    import asyncio
    from module.config.config_generation import ConfigGenerationError
    from module.server.main_manager import MainManager
    from module.server.script_process import ScriptState

    class DestinationHandle:
        def __init__(self, alive):
            self.alive = alive

        def is_alive(self):
            return self.alive

    class StaleDestinationProcess:
        generation = "old-destination-generation"

        def __init__(self, retireable):
            self.retireable = retireable
            self.state = ScriptState.RUNNING
            self._process = DestinationHandle(not retireable)
            self.stop_calls = 0

        async def stop(self):
            self.stop_calls += 1
            self.state = ScriptState.INACTIVE

    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("source", _canonical_template())
    # 直接提交 Store rename 模拟已完成的 rename（source tombstone、destination active）。
    store.rename_config("source", "destination")
    manager = MainManager(store=store)
    source_process = RecordingScriptProcess("source")
    destination_process = StaleDestinationProcess(retireable)
    manager.script_process = {
        "source": source_process,
        "destination": destination_process,
    }

    if retireable:
        asyncio.run(
            manager._reconcile_lifecycle_registry(
                "source", "destination", process=source_process, was_running=False
            )
        )
        installed = manager.script_process["destination"]
        assert installed is not destination_process
        assert installed.generation == store.load("destination").generation
        assert installed.state == ScriptState.INACTIVE
        assert "source" not in manager.script_process
    else:
        with pytest.raises(ConfigGenerationError):
            asyncio.run(
                manager._reconcile_lifecycle_registry(
                    "source", "destination", process=source_process, was_running=False
                )
            )
        # 旧对象与句柄保留，不覆盖、不丢句柄，且显式失败。
        assert manager.script_process["destination"] is destination_process
        assert destination_process._process is not None


def test_reconcile_destination_same_generation_wrapper_is_preserved(tmp_path):
    """destination 已有同 generation wrapper 时，reconcile 不停止也不替换。"""
    import asyncio
    from module.server.main_manager import MainManager
    from module.server.script_process import ScriptState

    class SameGenerationProcess:
        def __init__(self, generation):
            self.generation = generation
            self.state = ScriptState.INACTIVE
            self._process = object()
            self.stop_calls = 0

        async def stop(self):
            self.stop_calls += 1
            raise AssertionError("同 generation destination wrapper 不得被停止")

    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("source", _canonical_template())
    store.rename_config("source", "destination")
    manager = MainManager(store=store)
    current_generation = store.load("destination").generation
    source_process = RecordingScriptProcess("source")
    destination_process = SameGenerationProcess(current_generation)
    manager.script_process = {
        "source": source_process,
        "destination": destination_process,
    }

    asyncio.run(
        manager._reconcile_lifecycle_registry(
            "source", "destination", process=source_process, was_running=False
        )
    )

    assert manager.script_process["destination"] is destination_process
    assert destination_process.stop_calls == 0
    assert "source" not in manager.script_process


def test_reconcile_destination_retire_race_keeps_concurrent_wrapper(tmp_path):
    """退役 stop 期间并发安装同 generation wrapper 时，reconcile 保留并发对象且不报错。"""
    import asyncio
    from module.server.main_manager import MainManager
    from module.server.script_process import ScriptState

    class DestinationHandle:
        def __init__(self, alive):
            self.alive = alive

        def is_alive(self):
            return self.alive

    class SameGenerationProcess:
        def __init__(self, generation):
            self.generation = generation
            self.state = ScriptState.INACTIVE
            self._process = None
            self.stop_calls = 0

        async def stop(self):
            self.stop_calls += 1

    class ConcurrentInstallProcess:
        generation = "old-destination-generation"

        def __init__(self, manager, name, new_generation):
            self.manager = manager
            self.name = name
            self.new_generation = new_generation
            self.state = ScriptState.RUNNING
            self._process = DestinationHandle(True)
            self.stop_calls = 0
            self.installed = None

        async def stop(self):
            # 模拟并发路径在退役 stop 期间安装同 generation 新 wrapper。
            self.stop_calls += 1
            self.state = ScriptState.INACTIVE
            self.installed = SameGenerationProcess(self.new_generation)
            with self.manager._registry_lock:
                self.manager.script_process[self.name] = self.installed

    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("source", _canonical_template())
    store.rename_config("source", "destination")
    manager = MainManager(store=store)
    current_generation = store.load("destination").generation
    source_process = RecordingScriptProcess("source")
    stale_process = ConcurrentInstallProcess(manager, "destination", current_generation)
    manager.script_process = {
        "source": source_process,
        "destination": stale_process,
    }

    asyncio.run(
        manager._reconcile_lifecycle_registry(
            "source", "destination", process=source_process, was_running=False
        )
    )

    # 并发安装的同 generation wrapper 保留，旧对象只被停止一次，不产生新对象覆盖。
    assert manager.script_process["destination"] is stale_process.installed
    assert stale_process.stop_calls == 1
    assert stale_process.installed.stop_calls == 0
    assert "source" not in manager.script_process


def test_rename_stale_destination_occupancy_api_is_409(client):
    """目标磁盘 tombstone 但 registry 残留无法退役 wrapper 时，rename 路由返回 409。"""
    from module.server.main_manager import mm
    from module.server.script_process import ScriptState

    source = "stale-occ-source"
    destination = "stale-occ-destination"
    mm.store.create_from_template(source, _canonical_template())
    mm.store.create_from_template(destination, _canonical_template())
    mm.store.delete_config(destination)

    class DestinationHandle:
        def is_alive(self):
            return True

    class StaleDestinationProcess:
        generation = "stale-destination-generation"

        def __init__(self):
            self.state = ScriptState.RUNNING
            self._process = DestinationHandle()
            self.stop_calls = 0

        async def stop(self):
            self.stop_calls += 1
            self.state = ScriptState.INACTIVE

    source_process = RecordingScriptProcess(source)
    destination_process = StaleDestinationProcess()
    mm.script_process[source] = source_process
    mm.script_process[destination] = destination_process

    response = client.put(
        "/config",
        params={"old_name": source, "new_name": destination},
    )

    assert response.status_code == 409
    assert source_process.events == []
    assert mm.store.generation.read_active_generation(source).state == "active"
    assert mm.store.generation.read_active_generation(destination).state == "tombstone"
    assert mm.script_process[source] is source_process
    assert mm.script_process[destination] is destination_process
    assert destination_process.stop_calls == 1


def test_delete_failure_after_commit_removes_source_registry(tmp_path, monkeypatch):
    """delete 已提交 tombstone 后即使抛错，也不得重新注册或启动旧 source。"""
    import asyncio

    store = RecordingStore(tmp_path / "config")
    store.initialize()
    store.create_from_template("source", _canonical_template())
    process = RecordingScriptProcess("source")
    manager = _make_manager(store, {"source": process})

    def fail_at_commit(point):
        # tombstone 是 delete 提交点；后续物理删除失败也不能恢复旧身份。
        if point == "delete.after_tombstone":
            raise OSError("injected delete after commit")

    monkeypatch.setattr(store.generation.fault_injector, "hit", fail_at_commit)
    with pytest.raises(OSError, match="after commit"):
        asyncio.run(manager.delete_config("source"))

    assert "source" not in manager.script_process
    assert process.events == ["stop_begin", "stop_complete"]
    assert store.generation.read_active_generation("source").state == "tombstone"


@pytest.mark.parametrize("operation", ["rename", "delete"])
@pytest.mark.parametrize("failure_phase", ["before_state", "after_spawn"])
def test_lifecycle_timeout_type_survives_restore_start_failure(
    tmp_path, monkeypatch, operation, failure_phase
):
    """恢复 start 的次生失败不得覆盖 rename/delete 原始 Timeout。"""
    import asyncio

    store = RecordingStore(tmp_path / "config")
    store.initialize()
    store.create_from_template("source", _canonical_template())
    process = RestoreFailingScriptProcess("source", failure_phase)
    manager = _make_manager(store, {"source": process})

    def fail_before_commit(*_args, **_kwargs):
        # 原始生命周期异常固定为 filelock.Timeout，供 API 映射 503。
        raise Timeout(f"injected {operation} lock timeout")

    if operation == "rename":
        monkeypatch.setattr(store, "rename_config", fail_before_commit)
        call = manager.rename_config("source", "destination")
    else:
        monkeypatch.setattr(store, "delete_config", fail_before_commit)
        call = manager.delete_config("source")

    with pytest.raises(Timeout):
        asyncio.run(call)

    assert store.generation.read_active_generation("source").state == "active"
    assert store.generation.read_active_generation("destination") is None
    assert manager.script_process["source"] is process
    assert process.state == 0
    assert process.spawned is False
    assert process.events == [
        "stop_begin",
        "stop_complete",
        "start_begin",
        "stop_begin",
        "stop_complete",
    ]


def _snapshot_real_config() -> dict:
    """真实 config 树摘要：覆盖 config/*.json 与 .generations 全部文件（不含 .lock）。"""
    root = Path.cwd() / "config"
    protected = list(root.glob("*.json"))
    generations = root / ".generations"
    if generations.exists():
        protected.extend(path for path in generations.rglob("*") if path.is_file())
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(protected)
        if path.suffix != ".lock"
    }


@pytest.mark.parametrize("operation", ["rename", "delete"])
def test_lifecycle_stop_broadcast_cancel_reconciles_and_restores_source(
    tmp_path, monkeypatch, operation
):
    """stop 已收敛后广播取消仍须对账 active source，并恢复可运行实例。"""
    import asyncio
    import module.server.script_process as script_process_module
    from module.server.main_manager import MainManager
    from module.server.script_process import ScriptState

    class FakeChild:
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

    monkeypatch.setattr(script_process_module.multiprocessing, "Process", FakeChild)
    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("source", _canonical_template())
    manager = MainManager(store=store)
    process = script_process_module.ScriptProcess("source", store=store)

    async def no_broadcast(_data):
        return None

    process.broadcast_state = no_broadcast
    assert asyncio.run(process.start()) is True
    with manager._registry_lock:
        manager.script_process = {"source": process}

    async def cancel_broadcast(_data):
        raise asyncio.CancelledError()

    process.broadcast_state = cancel_broadcast
    call = (
        manager.rename_config("source", "destination")
        if operation == "rename"
        else manager.delete_config("source")
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(call)

    assert store.generation.read_active_generation("source").state == "active"
    assert store.generation.read_active_generation("destination") is None
    assert manager.script_process["source"] is process
    assert process.state == ScriptState.RUNNING
    assert process._process is not None and process._process.is_alive()

    process.broadcast_state = no_broadcast
    asyncio.run(process.stop())


@pytest.mark.parametrize("operation", ["rename", "delete"])
def test_lifecycle_store_error_survives_restore_broadcast_cancel(
    tmp_path, monkeypatch, operation
):
    """Store 原始错误优先；恢复 start 广播取消不能打断恢复或覆盖业务异常。"""
    import asyncio
    import module.server.script_process as script_process_module
    from module.server.main_manager import MainManager
    from module.server.script_process import ScriptState

    class FakeChild:
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

    monkeypatch.setattr(script_process_module.multiprocessing, "Process", FakeChild)
    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("source", _canonical_template())
    manager = MainManager(store=store)
    process = script_process_module.ScriptProcess("source", store=store)

    async def no_broadcast(_data):
        return None

    process.broadcast_state = no_broadcast
    assert asyncio.run(process.start()) is True
    with manager._registry_lock:
        manager.script_process = {"source": process}

    async def cancel_running_broadcast(data):
        if data.get("state") == ScriptState.RUNNING:
            raise asyncio.CancelledError()

    process.broadcast_state = cancel_running_broadcast

    def fail_store(*_args, **_kwargs):
        raise Timeout("original store timeout")

    monkeypatch.setattr(
        store,
        "rename_config" if operation == "rename" else "delete_config",
        fail_store,
    )
    call = (
        manager.rename_config("source", "destination")
        if operation == "rename"
        else manager.delete_config("source")
    )
    with pytest.raises(Timeout, match="original store timeout"):
        asyncio.run(call)

    assert store.generation.read_active_generation("source").state == "active"
    assert manager.script_process["source"] is process
    assert process.state == ScriptState.RUNNING
    assert process._process is not None and process._process.is_alive()

    process.broadcast_state = no_broadcast
    asyncio.run(process.stop())


def test_push_tasks_rebind_same_name_generation_and_consume_new_channels(tmp_path):
    """同名 A→B 替换后取消 A 推送任务，并消费 B 的 state/log 通道。"""
    import asyncio
    from module.server.main_manager import MainManager
    from module.server.script_process import ScriptState

    class QueueProcess:
        def __init__(self, generation):
            self.generation = generation
            self.state = ScriptState.RUNNING
            self.state_queue = asyncio.Queue()
            self.log_queue = asyncio.Queue()
            self.events = []

        async def coroutine_broadcast_state(self):
            self.events.append("state_started")
            try:
                self.events.append(("state", await self.state_queue.get()))
                await asyncio.Event().wait()
            finally:
                self.events.append("state_stopped")

        async def coroutine_broadcast_log(self):
            self.events.append("log_started")
            try:
                self.events.append(("log", await self.log_queue.get()))
                await asyncio.Event().wait()
            finally:
                self.events.append("log_stopped")

    async def scenario():
        manager = MainManager(store=ConfigStore(config_root=tmp_path / "config"))
        old = QueueProcess("generation-a")
        new = QueueProcess("generation-b")
        with manager._registry_lock:
            manager.script_process = {"oas1": old}
        tasks = {}

        await manager._sync_push_tasks(tasks)
        await asyncio.sleep(0)
        await old.state_queue.put("a-state")
        await old.log_queue.put("a-log")
        await asyncio.sleep(0)

        with manager._registry_lock:
            manager.script_process["oas1"] = new
        await manager._sync_push_tasks(tasks)
        await asyncio.sleep(0)
        await new.state_queue.put("b-state")
        await new.log_queue.put("b-log")
        await asyncio.sleep(0)

        assert "state_stopped" in old.events
        assert "log_stopped" in old.events
        assert ("state", "b-state") in new.events
        assert ("log", "b-log") in new.events
        assert tasks["oas1"][0] is new

        new.state = ScriptState.INACTIVE
        await manager._sync_push_tasks(tasks)
        assert tasks == {}

    asyncio.run(scenario())


def test_push_thread_survives_locked_registry_mutation_and_iteration_error(tmp_path):
    """线程屏障验证 registry 变更不会打断迭代，单轮异常也不会终止推送线程。"""
    import threading
    import time
    from module.server.main_manager import MainManager
    from module.server.script_process import ScriptState

    iteration_started = threading.Event()
    allow_snapshot = threading.Event()

    class InactiveProcess:
        state = ScriptState.INACTIVE

    class BarrierDict(dict):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.calls = 0

        def items(self):
            self.calls += 1
            if self.calls == 1:
                iteration_started.set()
                assert allow_snapshot.wait(timeout=2)
            elif self.calls == 2:
                raise RuntimeError("injected iteration failure")
            return super().items()

    manager = MainManager(store=ConfigStore(config_root=tmp_path / "config"))
    manager._push_interval = 0.001
    with manager._registry_lock:
        manager.script_process = BarrierDict({"old": InactiveProcess()})
    manager.start_push_data_thread()
    assert iteration_started.wait(timeout=2)

    mutation_done = threading.Event()

    def mutate_registry():
        with manager._registry_lock:
            manager.script_process["new"] = InactiveProcess()
        mutation_done.set()

    mutator = threading.Thread(target=mutate_registry)
    mutator.start()
    time.sleep(0.05)
    assert mutation_done.is_set() is False
    allow_snapshot.set()
    assert mutation_done.wait(timeout=2)
    mutator.join(timeout=2)

    # 第二轮注入 RuntimeError 后，长期推送线程仍应继续运行。
    time.sleep(0.05)
    assert manager.push_data_thread.is_alive()
    manager._push_shutdown_event.set()
    manager.push_data_thread.join(timeout=2)
    assert manager.push_data_thread.is_alive() is False


def test_kill_server_dispatches_stop_to_main_loop_and_isolates_failures(tmp_path):
    """推送线程通过主 loop 停止全部实例，首个异常不得跳过后续实例。"""
    import asyncio
    import threading
    from module.server.main_manager import MainManager

    class LoopBoundProcess:
        # 明确声明合法 stop 终态，覆盖 stop_all_processes 的严格哨兵校验。
        state = 0
        _process = None

        def __init__(self, name, fail=False):
            self.name = name
            self.fail = fail
            self.loop = None
            self.stop_calls = []

        async def stop(self):
            running_loop = asyncio.get_running_loop()
            if self.loop is None:
                self.loop = running_loop
            assert running_loop is self.loop
            self.stop_calls.append(threading.get_ident())
            if self.fail:
                raise RuntimeError(f"{self.name} stop failed")

    manager = MainManager(store=ConfigStore(config_root=tmp_path / "config"))
    first = LoopBoundProcess("first", fail=True)
    second = LoopBoundProcess("second")

    async def scenario():
        manager._main_loop = asyncio.get_running_loop()
        first.loop = manager._main_loop
        second.loop = manager._main_loop
        with manager._registry_lock:
            manager.script_process = {"first": first, "second": second}

        done = threading.Event()

        def push_thread_request():
            result = manager._request_stop_all_from_push_thread()
            manager._test_stop_request_result = result
            done.set()

        thread = threading.Thread(target=push_thread_request)
        thread.start()
        while not done.is_set():
            await asyncio.sleep(0.01)
        thread.join(timeout=2)
        assert thread.is_alive() is False

    main_thread = threading.get_ident()
    asyncio.run(scenario())
    assert first.stop_calls == [main_thread]
    assert second.stop_calls == [main_thread]
    assert manager._test_stop_request_result is False
    assert [name for name, _error in manager._last_stop_all_errors] == ["first"]


def test_stop_request_rejects_unavailable_and_same_loop(tmp_path):
    """loop None/closed/stopped 及主 loop 同线程调用都必须明确失败且不阻塞。"""
    import asyncio
    from module.server.main_manager import MainManager

    manager = MainManager(store=ConfigStore(config_root=tmp_path / "config"))
    assert manager._request_stop_all_from_push_thread() is False

    closed_loop = asyncio.new_event_loop()
    closed_loop.close()
    manager._main_loop = closed_loop
    assert manager._request_stop_all_from_push_thread() is False

    stopped_loop = asyncio.new_event_loop()
    manager._main_loop = stopped_loop
    assert manager._request_stop_all_from_push_thread() is False
    stopped_loop.close()

    async def same_loop_case():
        manager._main_loop = asyncio.get_running_loop()
        assert manager._request_stop_all_from_push_thread() is False

    asyncio.run(same_loop_case())


def test_stop_request_timeout_is_retryable(tmp_path):
    """future timeout 会取消本次请求并返回失败，不能伪装为批量停止成功。"""
    import asyncio
    import threading
    from module.server.main_manager import MainManager

    class BlockingProcess:
        async def stop(self):
            await asyncio.Event().wait()

    manager = MainManager(store=ConfigStore(config_root=tmp_path / "config"))
    manager._stop_request_timeout = 0.01
    process = BlockingProcess()

    async def scenario():
        manager._main_loop = asyncio.get_running_loop()
        with manager._registry_lock:
            manager.script_process = {"blocking": process}
        done = threading.Event()
        result = []

        def request():
            result.append(manager._request_stop_all_from_push_thread())
            done.set()

        thread = threading.Thread(target=request)
        thread.start()
        while not done.is_set():
            await asyncio.sleep(0.005)
        thread.join(timeout=2)
        assert thread.is_alive() is False
        assert result == [False]

    asyncio.run(scenario())


def test_kill_signal_retries_failed_batch_until_success(tmp_path):
    """kill signal 首批失败时推送线程保持运行并重试，成功后才退出。"""
    import asyncio
    import threading
    from module.server.main_manager import MainManager

    class RetryProcess:
        # 明确声明合法 stop 终态，避免把测试替身的缺失属性当成成功。
        state = 0
        _process = None

        def __init__(self, fail_once=False):
            self.fail_once = fail_once
            self.stop_calls = 0
            self.loop = None

        async def stop(self):
            self.loop = asyncio.get_running_loop()
            self.stop_calls += 1
            if self.fail_once and self.stop_calls == 1:
                raise RuntimeError("first stop failed")

    manager = MainManager(store=ConfigStore(config_root=tmp_path / "config"))
    manager._push_interval = 0.001
    first = RetryProcess(fail_once=True)
    second = RetryProcess()
    old_signal = MainManager.signal_kill_server
    MainManager.signal_kill_server = False

    async def scenario():
        manager._main_loop = asyncio.get_running_loop()
        with manager._registry_lock:
            manager.script_process = {"first": first, "second": second}
        manager.start_push_data_thread()
        await asyncio.sleep(0.02)
        MainManager.signal_kill_server = True
        for _ in range(400):
            if not manager.push_data_thread.is_alive():
                break
            await asyncio.sleep(0.005)
        assert first.stop_calls >= 2
        assert second.stop_calls >= 2
        assert manager.push_data_thread.is_alive() is False

    try:
        asyncio.run(scenario())
    finally:
        MainManager.signal_kill_server = old_signal
        manager._push_shutdown_event.set()
        if manager.push_data_thread is not None:
            manager.push_data_thread.join(timeout=2)


@pytest.fixture
def client(isolated_config_root, monkeypatch):
    """把 mm.store 替换为隔离 Store，并禁用推送线程，再启动 TestClient lifespan。"""
    from module.server.main_manager import mm

    mm.store = ConfigStore(config_root=isolated_config_root)
    mm.script_process = {}
    monkeypatch.setattr(mm, "start_push_data_thread", lambda: None)
    # lifespan on_startup 会调用 I18n.sync_missing_keys 写 assets/i18n/*.json，
    # 这是 Task 3 之外的资产副作用，测试必须禁用它。
    monkeypatch.setattr("module.server.i18n.I18n.sync_missing_keys", classmethod(lambda cls, *a, **k: 0))

    from module.server.app import app
    app.state.script_instances = None

    with TestClient(app) as c:
        yield c
    mm.script_process = {}


@pytest.mark.parametrize("operation", ["rename", "delete"])
@pytest.mark.parametrize("failure_phase", ["before_state", "after_spawn"])
def test_lifecycle_restore_start_failure_api_keeps_503(
    client, monkeypatch, operation, failure_phase
):
    """真实路由必须继续按原始 Timeout 返回 503，而不是被恢复 RuntimeError 改成 500。"""
    from module.server.main_manager import mm

    process = RestoreFailingScriptProcess("oas1", failure_phase)
    mm.script_process["oas1"] = process

    def fail_before_commit(*_args, **_kwargs):
        raise Timeout(f"injected {operation} lock timeout")

    if operation == "rename":
        monkeypatch.setattr(mm.store, "rename_config", fail_before_commit)
        response = client.put(
            "/config",
            params={"old_name": "oas1", "new_name": "timeout-destination"},
        )
    else:
        monkeypatch.setattr(mm.store, "delete_config", fail_before_commit)
        response = client.delete("/config", params={"name": "oas1"})

    assert response.status_code == 503
    assert mm.store.generation.read_active_generation("oas1").state == "active"
    assert mm.store.generation.read_active_generation("timeout-destination") is None
    assert mm.script_process["oas1"] is process
    assert process.state == 0
    assert process.spawned is False


@pytest.mark.parametrize("operation", ["rename", "delete"])
@pytest.mark.parametrize("failure_point", ["recovery", "sidecar"])
def test_committed_lifecycle_reconcile_failure_returns_500(
    client, monkeypatch, operation, failure_point
):
    """Store 已提交后对账失败不得返回 200，旧 source registry 必须保持移除。"""
    from module.server.main_manager import mm

    source = f"committed-{failure_point}-{operation}-source"
    destination = f"committed-{failure_point}-{operation}-destination"
    mm.store.create_from_template(source, _canonical_template())
    process = RecordingScriptProcess(source)
    mm.script_process[source] = process

    def fail_recovery(*_args, **_kwargs):
        raise OSError("injected lifecycle recovery failure")

    def fail_sidecar(*_args, **_kwargs):
        raise OSError("injected sidecar read failure")

    if failure_point == "recovery":
        monkeypatch.setattr(mm.store, "reconcile_lifecycle_transactions", fail_recovery)
    else:
        monkeypatch.setattr(mm, "_disk_identity_active", fail_sidecar)

    if operation == "rename":
        response = client.put(
            "/config",
            params={"old_name": source, "new_name": destination},
        )
    else:
        response = client.delete("/config", params={"name": source})

    assert response.status_code == 500
    assert source not in mm.script_process
    assert process.state == 0
    assert process.events == ["stop_begin", "stop_complete"]
    assert mm.store.generation.read_active_generation(source).state == "tombstone"
    if operation == "rename":
        assert mm.store.generation.read_active_generation(destination).state == "active"
        assert destination not in mm.script_process


@pytest.mark.parametrize("constructor_error", [OSError, TimeoutError])
def test_rename_destination_constructor_failure_api_is_500(
    client, monkeypatch, constructor_error
):
    """destination wrapper 构造失败（磁盘已提交）时 rename 路由必须返回 500，
    不能假成功返回 200，也不能被误映射为 409/503；磁盘 destination 已提交并可 load。"""
    import module.server.main_manager as manager_module
    from module.server.main_manager import mm

    # isolated_config_root 是 session 级共享目录，参数化两次运行必须使用不同身份名。
    suffix = constructor_error.__name__.lower()
    source = f"dest-construct-source-{suffix}"
    destination = f"dest-construct-destination-{suffix}"
    mm.store.create_from_template(source, _canonical_template())
    process = RecordingScriptProcess(source)
    mm.script_process[source] = process
    real_process_type = manager_module.ScriptProcess

    def fail_destination(name, *args, **kwargs):
        if name == destination:
            raise constructor_error("injected destination constructor failure")
        return real_process_type(name, *args, **kwargs)

    monkeypatch.setattr(manager_module, "ScriptProcess", fail_destination)

    response = client.put(
        "/config",
        params={"old_name": source, "new_name": destination},
    )

    # postcommit 一致性失败映射为 500，不是 200 假成功 / 409 冲突 / 503 锁超时。
    assert response.status_code == 500
    assert source not in mm.script_process
    assert destination not in mm.script_process
    assert mm.store.generation.read_active_generation(source).state == "tombstone"
    assert mm.store.generation.read_active_generation(destination).state == "active"
    assert mm.store.load(destination).canonical["config_name"] == destination


def test_active_config_names_propagates_lock_timeout(store, monkeypatch):
    """active 枚举遇到瞬时锁超时必须整体失败，不能静默返回部分身份列表。

    枚举改为单次遍历（一把身份锁 + 每名一把 lifecycle 锁）后，锁内走的是
    _load_unlocked 而不是 load，注入点随之下移；被断言的不变量不变。
    """
    original_load_unlocked = store._load_unlocked

    def timeout_one(name):
        if name == "oas1":
            raise TimeoutError("injected active enumeration timeout")
        return original_load_unlocked(name)

    monkeypatch.setattr(store, "_load_unlocked", timeout_one)

    with pytest.raises(TimeoutError, match="active enumeration timeout"):
        store.active_config_names()
    with pytest.raises(TimeoutError, match="active enumeration timeout"):
        store.active_canonical_snapshots()


def test_import_generation_corruption_is_not_duplicate_conflict(tmp_path, monkeypatch):
    from module.config.config_generation import ConfigGenerationError
    from module.config.config_store import ConfigStore
    from module.server.config_manager import ConfigManager

    manager = ConfigManager(store=ConfigStore(config_root=tmp_path / "config"))
    monkeypatch.setattr(
        manager.store,
        "import_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ConfigGenerationError("corrupt identity state")
        ),
    )

    with pytest.raises(ConfigGenerationError, match="corrupt identity state"):
        manager.import_config("oas1", _canonical_template())


def test_oasx_routers_are_registered():
    from module.server.app import app

    # 路由 smoke 测试：确认 OASX 前端依赖的路径已注册。
    paths = {route.path for route in app.routes}

    assert "/logs/{script_name}" in paths
    assert "/logs/{script_name}/stream" in paths
    assert "/logs/errors" in paths
    assert "/stats/{script_name}/dates" in paths
    assert "/stats/{script_name}" in paths
    assert "/stats/{script_name}/stream" in paths
    assert "/config/import" in paths
    assert "/config/export" in paths
    assert "/config/task/import" in paths
    assert "/config/task/export" in paths
    assert "/config/task/copy-json" in paths


def test_importing_server_app_has_no_config_side_effects():
    """仅 import/reload app 后真实 config 摘要不变且 mm.script_process 仍为空。"""
    from module.server.main_manager import mm

    before = _snapshot_real_config()
    mm.script_process = {}
    mm.push_data_thread = None
    server_app = importlib.reload(importlib.import_module("module.server.app"))
    assert _snapshot_real_config() == before
    assert server_app.mm.script_process == {}


def test_mainmanager_initialize_recovers_before_enumerating(tmp_path):
    """启动恢复测试：committed rename journal + creating 残留，initialize 先恢复，
    再只为 active destination 创建 ScriptProcess，source/tombstone 均不枚举。"""
    import asyncio
    import hashlib
    from module.config.config_generation import GenerationRecord
    from module.config.config_store import ConfigStore
    from module.server.main_manager import MainManager

    raw = json.loads((Path.cwd() / "config" / "template.json").read_text(encoding="utf-8"))
    raw["meta_demon"].pop("md_strategies_1", None)
    # 为 rename 目标预置一个可展示账号，验证动态选项仅来自恢复后的 active 身份。
    raw["multi_daily_alt_acc"]["sup_account_list_1"].update({
        "character": "恢复账号",
        "svr": "测试服",
        "account": "recover@example.com",
    })
    store = ConfigStore(config_root=tmp_path / "config")
    store.initialize()  # 建立 migration marker
    store.create_from_template("old", raw)
    manager = store.generation

    # committed rename journal：目标已写 active sidecar 与配置，源仍物理存在
    src_generation = store.load("old").generation
    src_digest = manager._config_digest("old")
    tgt_canonical = dict(store.load("old").canonical)
    tgt_canonical["config_name"] = "new"
    tgt_path = manager._config_path("new")
    tgt_path.write_text(json.dumps(tgt_canonical, indent=2), encoding="utf-8")
    tgt_digest = hashlib.sha256(tgt_path.read_bytes()).hexdigest()
    manager._write_sidecar("new", GenerationRecord("g-new", "active", None))
    txid = "00000000-0000-0000-0000-000000000040"
    manager._write_journal(txid, {
        "transaction_id": txid,
        "operation": "rename",
        "state": "committed",
        "source": {"name": "old", "generation": src_generation, "digest": src_digest},
        "target": {"name": "new", "generation": "g-new", "digest": tgt_digest},
    })
    # creating 残留（配置与 digest 不匹配 → 恢复为 tombstone）
    manager._write_sidecar("stale", GenerationRecord("g-stale", "creating", "deadbeef"))
    (manager._config_path("stale")).write_text("{}", encoding="utf-8")

    # 用新的 Store 实例模拟重启进程：_initialized=False，initialize 会先执行 journal 恢复
    mm = MainManager(store=ConfigStore(config_root=store.config_root))
    asyncio.run(mm.initialize())

    # 只枚举 active destination，source/tombstone 不进入注册和动态账号开关
    assert set(mm.script_process) == {"new"}
    assert store.load("new").canonical["config_name"] == "new"
    assert store.generation.read_active_generation("old").state == "tombstone"
    assert store.generation.read_active_generation("stale").state == "tombstone"
    account_items = mm.config_cache("new").script_task(
        "MultiAccountSignIn"
    )["account_config_selection"]
    expected_field = "config_" + hashlib.sha256(b"new").hexdigest()[:16]
    assert [(item["name"], item["title"]) for item in account_items] == [
        (expected_field, "new")
    ]


def test_get_args_uses_manager_injected_store(client, isolated_config_root):
    from module.server.main_manager import mm

    response = client.get("/oas1/Script/args")
    assert response.status_code == 200
    assert mm.config_cache("oas1").store is mm.store
    assert mm.store.config_root == isolated_config_root


@pytest.mark.parametrize("old_running", [False, True])
def test_start_route_replaces_aba_registry_for_inactive_and_running_old_process(
    client, isolated_config_root, monkeypatch, old_running
):
    """真实 start 路由在 delete+create ABA 后重建并启动 B generation。"""
    import module.server.script_process as script_process_module
    from module.server.main_manager import mm
    from module.server.script_process import ScriptState

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.alive = False
            self.terminated = False

        def start(self):
            self.alive = True

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminated = True
            self.alive = False

        def join(self, timeout=0):
            return None

        def kill(self):
            self.alive = False

    monkeypatch.setattr(script_process_module.multiprocessing, "Process", FakeProcess)
    old_process = mm.script_process["oas1"]
    old_generation = old_process.generation
    old_child = None
    if old_running:
        old_child = FakeProcess()
        old_child.start()
        old_process._process = old_child
        old_process.state = ScriptState.RUNNING

    # 另一个 Store 执行 delete+create，模拟外部进程把同名身份替换为 B。
    external_store = ConfigStore(config_root=isolated_config_root)
    external_store.delete_config("oas1")
    external_store.create_from_template("oas1", _canonical_template())
    new_generation = external_store.load("oas1").generation
    assert new_generation != old_generation

    response = client.get("/oas1/start")

    assert response.status_code == 200
    current = mm.script_process["oas1"]
    assert current is not old_process
    assert current.generation == new_generation
    assert current.state == ScriptState.RUNNING
    assert current._process is not None and current._process.is_alive()
    if old_running:
        assert old_child.terminated is True
    else:
        assert old_process.state == ScriptState.INACTIVE

    # 重复 ensure/start 应复用 B registry，不能回退到 A 或创建错误身份。
    response = client.get("/oas1/start")
    assert response.status_code == 200
    assert mm.script_process["oas1"] is current
    assert mm.script_process["oas1"].generation == new_generation


@pytest.mark.parametrize("start_result", [False, None, "unexpected"])
def test_start_route_maps_non_true_result_to_non_success(client, monkeypatch, start_result):
    """start() 只有明确 True 才能返回 HTTP 200。"""
    from module.server.main_manager import mm

    async def fake_start(_name):
        return start_result

    monkeypatch.setattr(mm, "start_script_process", fake_start)

    response = client.get("/oas1/start")

    assert response.status_code == 409


@pytest.mark.parametrize("replacement", [False, True])
def test_start_route_rechecks_identity_before_spawn(
    client, isolated_config_root, monkeypatch, replacement
):
    """spawn 前注入 delete 或 delete+create 时，不得启动旧 wrapper。"""
    import module.server.script_process as script_process_module
    from module.server.main_manager import mm

    spawned = []

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            spawned.append("construct")

        def start(self):
            spawned.append("start")

    monkeypatch.setattr(script_process_module.multiprocessing, "Process", FakeProcess)
    external_store = ConfigStore(config_root=isolated_config_root)
    if "oas1" not in mm.script_process:
        # 参数化前一用例可能留下 tombstone，显式重建隔离身份以保持用例独立。
        external_store.create_from_template("oas1", _canonical_template())
        with mm._registry_lock:
            mm.script_process["oas1"] = script_process_module.ScriptProcess(
                "oas1", store=mm.store
            )
    process = mm.script_process["oas1"]
    original_load = process.store.load
    mutated = False

    def mutate_after_initial_load(name):
        nonlocal mutated
        loaded = original_load(name)
        if not mutated:
            mutated = True
            external_store.delete_config("oas1")
            if replacement:
                external_store.create_from_template("oas1", _canonical_template())
        return loaded

    # 初次快速 load 返回后、最终 lifecycle 锁内复核前注入身份变化。
    monkeypatch.setattr(process.store, "load", mutate_after_initial_load)

    response = client.get("/oas1/start")

    assert response.status_code == (409 if replacement else 404)
    assert spawned == []


@pytest.mark.parametrize(
    "error_factory,status",
    [
        (lambda: ConfigNotFoundError("missing"), 404),
        (lambda: Timeout("lock timeout"), 503),
        (lambda: ConfigJsonError("bad json"), 500),
        (lambda: ConfigValidationError("invalid"), 500),
        (lambda: OSError("io failure"), 500),
    ],
)
def test_start_route_preserves_start_exception_mapping(client, monkeypatch, error_factory, status):
    """start 的具体读取异常必须按既有 HTTP 状态映射返回。"""
    from module.server.main_manager import mm

    async def fake_start(_name):
        raise error_factory()

    monkeypatch.setattr(mm, "start_script_process", fake_start)

    response = client.get("/oas1/start")

    assert response.status_code == status


def test_start_route_maps_handshake_timeout_to_500(client, monkeypatch):
    """子进程 generation 握手超时（专用异常）必须映射 500，而不是被误判为锁超时 503。"""
    from module.server.main_manager import mm
    from module.server.script_process import ScriptStartupTimeoutError

    async def fake_start(_name):
        raise ScriptStartupTimeoutError("injected handshake timeout")

    monkeypatch.setattr(mm, "start_script_process", fake_start)

    response = client.get("/oas1/start")

    assert response.status_code == 500


def test_start_route_handshake_timeout_via_real_path_is_500(
    client, monkeypatch
):
    """真实 start 路径里 _wait_for_spawn_handshake 抛专用异常时，路由返回 500 而非 503。"""
    import module.server.script_process as script_process_module
    from module.server.main_manager import mm
    from module.server.script_process import ScriptStartupTimeoutError

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.alive = False
            self.terminated = False

        def start(self):
            self.alive = True

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminated = True
            self.alive = False

    monkeypatch.setattr(script_process_module.multiprocessing, "Process", FakeProcess)

    async def fail_handshake(self, *args, **kwargs):
        raise ScriptStartupTimeoutError("injected handshake timeout")

    monkeypatch.setattr(
        script_process_module.ScriptProcess,
        "_wait_for_spawn_handshake",
        fail_handshake,
    )

    response = client.get("/oas1/start")

    assert response.status_code == 500
    # 清理失败路径已把进程句柄收敛为 INACTIVE，不能残留 RUNNING 假象。
    assert mm.script_process["oas1"].state == script_process_module.ScriptState.INACTIVE


def test_start_missing_process_uses_manager_factory(client, monkeypatch):
    from module.server.main_manager import mm

    calls = []

    async def fake_start(name):
        calls.append(name)
        return True

    monkeypatch.setattr(mm, "start_script_process", fake_start)

    response = client.get("/oas1/start")
    assert response.status_code == 200
    assert calls == ["oas1"]


def test_websocket_missing_process_uses_manager_factory(client, monkeypatch):
    from module.server.main_manager import mm

    calls = []

    class FakeProcess:
        state = 0

        async def connect(self, websocket):
            await websocket.accept()

        async def disconnect(self, websocket):
            pass

        async def broadcast_state(self, data):
            pass

        async def send_state(self, websocket, data):
            await websocket.send_json(data)

        def cached_config_state(self):
            return {
                "pending_restart_paths": [],
                "pending_warm_paths": [],
                "observed_mtime_ns": 0,
                "status": "current",
            }

        async def start(self):
            pass

        async def stop(self):
            pass

    async def fake_ensure(name):
        calls.append(name)
        return FakeProcess()

    monkeypatch.setattr(mm, "ensure_script_process", fake_ensure)

    with client.websocket_connect("/ws/oas1") as websocket:
        websocket.send_text("get_schedule")
    assert calls == ["oas1"]


def test_websocket_first_frame_order_and_directed_config_state(client, monkeypatch):
    """WebSocket 新连接定向首帧顺序：state、schedule、cached config_state；
    get_config_state 也是单 socket 定向发送，不使用广播模拟首帧。"""
    from module.server.main_manager import mm

    sent = []

    class FakeConfig:
        def get_next(self):
            return None

        def get_schedule_data(self):
            return {"running": {}, "pending": [], "waiting": []}

    class FakeProcess:
        state = 1

        def cached_config_state(self):
            return {
                "pending_restart_paths": [["script", "device", "serial"]],
                "pending_warm_paths": [],
                "observed_mtime_ns": 1,
                "status": "restart_required",
            }

        async def connect(self, websocket):
            await websocket.accept()

        async def disconnect(self, websocket):
            pass

        async def send_state(self, websocket, data):
            sent.append(data)
            await websocket.send_json(data)

        async def broadcast_state(self, data):
            sent.append({"__broadcast__": data})

        async def start(self):
            pass

        async def stop(self):
            pass

    async def fake_ensure(_name):
        return FakeProcess()

    monkeypatch.setattr(mm, "ensure_script_process", fake_ensure)
    monkeypatch.setattr(mm, "config_cache", lambda name: FakeConfig())

    with client.websocket_connect("/ws/oas1") as websocket:
        websocket.send_text("get_config_state")
        for _ in range(4):
            websocket.receive_json()

    # 首帧顺序固定：state → schedule → config_state；get_config_state 响应为第 4 帧
    assert list(sent[0].keys()) == ["state"]
    assert list(sent[1].keys()) == ["schedule"]
    assert list(sent[2].keys()) == ["config_state"]
    assert list(sent[3].keys()) == ["config_state"]
    assert sent[3]["config_state"]["status"] == "restart_required"
    # 首帧与命令响应都走 send_state，未用 broadcast 模拟
    assert not any("__broadcast__" in s for s in sent)


def test_config_copy_invalid_name_is_400(client):
    """复制目标名非法必须在入口返回 400，不能继续枚举并伪装成功。"""
    response = client.post(
        "/config_copy",
        params={"file": "../invalid", "template": "template"},
    )
    assert response.status_code == 400


def test_config_copy_missing_template_is_404(client):
    """复制源身份不存在必须返回 404。"""
    response = client.post(
        "/config_copy",
        params={"file": "copied", "template": "missing-template"},
    )
    assert response.status_code == 404


def test_config_copy_existing_target_is_409(client):
    """复制目标已存在必须返回冲突，不得吞掉 generation 异常。"""
    from module.server.main_manager import mm

    mm.store.create_from_template("existing-copy", _canonical_template())
    response = client.post(
        "/config_copy",
        params={"file": "existing-copy", "template": "template"},
    )
    assert response.status_code == 409


def test_config_copy_lock_timeout_is_503(client, monkeypatch):
    """复制获取身份锁超时必须返回 503。"""
    from filelock import Timeout
    from module.server.main_manager import mm

    def fail_copy(*_args, **_kwargs):
        raise Timeout("injected copy lock timeout")

    monkeypatch.setattr(mm, "copy", fail_copy)
    response = client.post(
        "/config_copy",
        params={"file": "copy-timeout", "template": "template"},
    )
    assert response.status_code == 503


def test_config_copy_generation_error_is_500(client, monkeypatch):
    """身份损坏或文件系统错误必须返回 500，失败不得返回 200。"""
    from module.config.config_generation import ConfigGenerationError
    from module.server.main_manager import mm

    def fail_copy(*_args, **_kwargs):
        raise ConfigGenerationError("injected corrupt identity")

    monkeypatch.setattr(mm, "copy", fail_copy)
    response = client.post(
        "/config_copy",
        params={"file": "copy-corrupt", "template": "template"},
    )
    assert response.status_code == 500


def test_config_copy_io_error_is_500(client, monkeypatch):
    """复制文件系统失败必须返回 500。"""
    from module.server.main_manager import mm

    def fail_copy(*_args, **_kwargs):
        raise OSError("injected copy write failure")

    monkeypatch.setattr(mm, "copy", fail_copy)
    response = client.post(
        "/config_copy",
        params={"file": "copy-io", "template": "template"},
    )
    assert response.status_code == 500


@pytest.mark.parametrize(
    "error_factory,status",
    [
        (lambda: ConfigAlreadyExistsError("duplicate target"), 409),
        (lambda: ManagerConfigValidationError([{"loc": ["__root__"], "msg": "invalid", "type": "value_error"}]), 400),
        (lambda: ConfigNameError("bad name"), 400),
        (lambda: ManagerConfigJsonError("bad json"), 400),
        (lambda: Timeout("lock timeout"), 503),
        (lambda: ConfigGenerationError("corrupt identity"), 500),
        (lambda: OSError("io failure"), 500),
    ],
)
def test_config_import_preserves_exception_mapping(client, monkeypatch, error_factory, status):
    """config/import 的既有 400/409 与锁超时/身份损坏等生命周期失败必须返回稳定状态码。"""
    from module.server.main_manager import mm

    def fail_import(*_args, **_kwargs):
        raise error_factory()

    monkeypatch.setattr(mm, "import_config", fail_import)
    response = client.post(
        "/config/import",
        data={"name": "import-target"},
        files={"file": ("payload.json", b"{}", "application/json")},
    )
    assert response.status_code == status


def test_reserved_template_rename_and_delete_are_400(client):
    from module.server.main_manager import mm

    before = mm.store.load("template")

    rename_response = client.put(
        "/config",
        params={"old_name": "template", "new_name": "renamed_template"},
    )
    delete_response = client.delete("/config", params={"name": "template"})

    assert rename_response.status_code == 400
    assert delete_response.status_code == 400
    after = mm.store.load("template")
    assert after.generation == before.generation
    assert after.canonical == before.canonical


def test_reserved_template_case_alias_delete_is_400_on_windows(client):
    import os
    from module.server.main_manager import mm

    if os.name != "nt":
        pytest.skip("Windows case-insensitive identity only")
    before = mm.store.load("template")

    response = client.delete("/config", params={"name": "Template"})

    assert response.status_code == 400
    after = mm.store.load("template")
    assert after.generation == before.generation
    assert after.canonical == before.canonical


def test_rename_missing_identity_is_404(client, monkeypatch):
    from module.config.config_generation import ConfigIdentityNotFoundError
    from module.server.main_manager import mm

    async def fail_rename(*_args, **_kwargs):
        # 生命周期层明确报告源身份不存在。
        raise ConfigIdentityNotFoundError("missing source")

    monkeypatch.setattr(mm, "rename_config", fail_rename)
    response = client.put(
        "/config",
        params={"old_name": "missing", "new_name": "new"},
    )

    assert response.status_code == 404


def test_rename_existing_target_is_409(client, monkeypatch):
    from module.config.config_generation import ConfigIdentityConflictError
    from module.server.main_manager import mm

    async def fail_rename(*_args, **_kwargs):
        # 生命周期层明确报告目标身份冲突。
        raise ConfigIdentityConflictError("target exists")

    monkeypatch.setattr(mm, "rename_config", fail_rename)
    response = client.put(
        "/config",
        params={"old_name": "old", "new_name": "existing"},
    )

    assert response.status_code == 409


def test_delete_missing_identity_is_404(client, monkeypatch):
    from module.config.config_generation import ConfigIdentityNotFoundError
    from module.server.main_manager import mm

    async def fail_delete(*_args, **_kwargs):
        # 生命周期层明确报告删除目标不存在。
        raise ConfigIdentityNotFoundError("missing identity")

    monkeypatch.setattr(mm, "delete_config", fail_delete)
    response = client.delete("/config", params={"name": "missing"})

    assert response.status_code == 404


def test_task_copy_deleted_after_load_is_404(client, monkeypatch):
    from module.config.config_generation import ConfigIdentityNotFoundError
    from module.server.main_manager import mm

    mm.script_process = {"oas1": object(), "source": object()}
    mm.store.create_from_template("source", _canonical_template())

    def fail_deleted(*_args, **_kwargs):
        # 模拟目标在 load 与 replace_subtree 之间被删除且尚未同名重建。
        raise ConfigIdentityNotFoundError("target deleted")

    monkeypatch.setattr(mm.store, "replace_subtree", fail_deleted)
    response = client.put(
        "/config/task/copy",
        params={
            "task_name": "Orochi",
            "dest_config_name": "oas1",
            "source_config_name": "source",
        },
    )

    assert response.status_code == 404


def test_task_copy_generation_conflict_is_409(client, monkeypatch):
    from module.config.config_store import ConfigGenerationMismatchError
    from module.server.main_manager import mm

    source_name = "source_generation_conflict"
    # 路由前置注册检查要求 source/destination 均有运行实例占位。
    mm.script_process = {"oas1": object(), source_name: object()}
    mm.store.create_from_template(source_name, _canonical_template())

    def fail_generation(*_args, **_kwargs):
        # 模拟目标在 load 与 replace_subtree 之间发生同名重建。
        raise ConfigGenerationMismatchError("injected subtree generation conflict")

    monkeypatch.setattr(mm.store, "replace_subtree", fail_generation)
    response = client.put(
        "/config/task/copy",
        params={
            "task_name": "Orochi",
            "dest_config_name": "oas1",
            "source_config_name": source_name,
        },
    )

    assert response.status_code == 409


def test_task_copy_success_notifies_config_changed(client, monkeypatch):
    """task copy 成功后必须锁外投递 config_changed，参数含 generation/mtime/paths。"""
    from module.server.main_manager import mm

    source_name = "copy_source"
    dest_name = "copy_dest"
    mm.store.create_from_template(source_name, _canonical_template())
    mm.store.create_from_template(dest_name, _canonical_template())
    # 前置注册检查要求 source/destination 均有运行实例占位。
    mm.script_process = {dest_name: object(), source_name: object()}
    # 让 dest 的 orochi 子树与 source 不同，确保替换产生 changed_paths。
    mm.store.patch_user_argument(dest_name, "Orochi", "orochi_config", "soul_buff_enable", True)

    calls = []
    monkeypatch.setattr(mm, "notify_config_changed", lambda name, result: calls.append((name, result)))

    response = client.put(
        "/config/task/copy",
        params={
            "task_name": "Orochi",
            "dest_config_name": dest_name,
            "source_config_name": source_name,
        },
    )

    assert response.status_code == 200
    assert response.json() is True
    assert len(calls) == 1
    call_name, result = calls[0]
    assert call_name == dest_name
    dest_after = mm.store.load(dest_name)
    assert result.generation == dest_after.generation
    assert result.mtime_ns == dest_after.mtime_ns
    assert result.changed_paths == [("orochi",)]
    # 替换确实落盘：dest 的 orochi 与 source 一致（soul_buff_enable 恢复 False）。
    assert dest_after.canonical["orochi"]["orochi_config"]["soul_buff_enable"] is False


def test_task_group_copy_success_notifies_config_changed(client, monkeypatch):
    """group copy 成功后必须锁外投递 config_changed，参数含 generation/mtime/paths。"""
    from module.server.main_manager import mm

    source_name = "group_copy_source"
    dest_name = "group_copy_dest"
    mm.store.create_from_template(source_name, _canonical_template())
    mm.store.create_from_template(dest_name, _canonical_template())
    mm.script_process = {dest_name: object(), source_name: object()}
    # 让 dest 的 orochi_config 与 source 不同，确保替换产生 changed_paths。
    mm.store.patch_user_argument(dest_name, "Orochi", "orochi_config", "soul_buff_enable", True)

    calls = []
    monkeypatch.setattr(mm, "notify_config_changed", lambda name, result: calls.append((name, result)))

    response = client.put(
        "/config/task/group/copy",
        params={
            "task_name": "Orochi",
            "group_name": "orochi_config",
            "dest_config_name": dest_name,
            "source_config_name": source_name,
        },
    )

    assert response.status_code == 200
    assert response.json() is True
    assert len(calls) == 1
    call_name, result = calls[0]
    assert call_name == dest_name
    dest_after = mm.store.load(dest_name)
    assert result.generation == dest_after.generation
    assert result.mtime_ns == dest_after.mtime_ns
    assert result.changed_paths == [("orochi", "orochi_config")]
    assert dest_after.canonical["orochi"]["orochi_config"]["soul_buff_enable"] is False


def test_oasx_put_returns_bare_json_boolean(client, isolated_config_root):
    from module.server.main_manager import mm

    response = client.put(
        "/oas1/FindJade/findJadeConfig/inviteInfoCount/value",
        params={"types": "integer", "value": 2},
    )
    assert response.status_code == 200
    assert response.json() is True
    assert mm.store.load("oas1").canonical["find_jade"]["find_jade_config"]["invite_info_count"] == 2


def test_oasx_invalid_boolean_is_400_and_disk_unchanged(client):
    from module.server.main_manager import mm

    before = mm.store.load("oas1")
    response = client.put(
        "/oas1/Restart/tasksConfigReset/resetTaskDatetimeEnable/value",
        params={"types": "boolean", "value": "not-a-boolean"},
    )

    assert response.status_code == 400
    after = mm.store.load("oas1")
    assert after.canonical == before.canonical
    assert after.mtime_ns == before.mtime_ns


def _reset_request(client):
    """发送 OASX 全局 reset 请求。"""
    return client.put(
        "/oas1/Restart/tasksConfigReset/resetTaskDatetimeEnable/value",
        params={"types": "boolean", "value": "true"},
    )


def test_oasx_global_reset_commits_flag_and_next_runs_together(client):
    from module.server.main_manager import mm

    target = "2026-05-01 09:00:00"
    mm.store.patch_user_field(
        "oas1",
        ("restart", "tasks_config_reset", "reset_task_datetime"),
        target,
    )
    mm.store.patch_user_field("oas1", ("orochi", "scheduler", "enable"), True)
    mm.store.patch_user_field(
        "oas1",
        ("orochi", "scheduler", "next_run"),
        "2026-01-01 00:00:00",
    )

    response = _reset_request(client)

    assert response.status_code == 200
    assert response.json() is True
    canonical = mm.store.load("oas1").canonical
    assert canonical["restart"]["tasks_config_reset"]["reset_task_datetime_enable"] is True
    assert canonical["orochi"]["scheduler"]["next_run"] == target
    assert mm.store.last_operation.operation == "RESET_ENABLED_NEXT_RUNS"


def test_oasx_global_reset_validation_failure_is_atomic(client, monkeypatch):
    from module.config.config_validation import ConfigValidationError
    from module.server.main_manager import mm

    before = mm.store.load("oas1").canonical

    def fail_validation(*_args, **_kwargs):
        # 模拟候选 canonical 严格校验失败，写入不得发生。
        raise ConfigValidationError("injected reset validation failure")

    with monkeypatch.context() as patch:
        patch.setattr(
            "module.config.config_store.validate_persisted_config",
            fail_validation,
        )
        response = _reset_request(client)

    assert response.status_code == 400
    assert mm.store.load("oas1").canonical == before


def test_oasx_global_reset_lock_timeout_is_503_and_atomic(client, monkeypatch):
    from filelock import Timeout
    from module.server.main_manager import mm

    before = mm.store.load("oas1").canonical

    def fail_lock(*_args, **_kwargs):
        # 模拟专用事务获取生命周期锁超时。
        raise Timeout("injected reset lock timeout")

    monkeypatch.setattr(mm.store, "reset_enabled_next_runs", fail_lock)
    response = _reset_request(client)

    assert response.status_code == 503
    assert mm.store.load("oas1").canonical == before


def test_oasx_global_reset_atomic_write_failure_is_500_and_atomic(client, monkeypatch):
    from module.server.main_manager import mm

    # session 级隔离 Store 会被本文件多个 TestClient 复用，显式恢复待 reset 状态。
    mm.store.patch_user_field(
        "oas1",
        ("restart", "tasks_config_reset", "reset_task_datetime_enable"),
        False,
    )
    mm.store.patch_user_field("oas1", ("orochi", "scheduler", "enable"), True)
    mm.store.patch_user_field(
        "oas1",
        ("orochi", "scheduler", "next_run"),
        "2026-01-01 00:00:00",
    )
    before = mm.store.load("oas1").canonical

    def fail_write(*_args, **_kwargs):
        # 模拟 atomic writer 在替换正式文件前失败。
        raise OSError("injected reset write failure")

    monkeypatch.setattr(mm.store, "_write_config", fail_write)
    response = _reset_request(client)

    assert response.status_code == 500
    assert mm.store.load("oas1").canonical == before


def test_oasx_put_validation_failure_is_400_and_disk_unchanged(client):
    from module.server.main_manager import mm

    before = mm.store.load("oas1").canonical
    response = client.put(
        "/oas1/FindJade/findJadeConfig/inviteInfoCount/value",
        params={"types": "integer", "value": 0},
    )
    assert response.status_code == 400
    assert mm.store.load("oas1").canonical == before


def test_oasx_dynamic_group_updates_one_atomic_path_set(client):
    from module.server.main_manager import mm

    before = mm.store.load("oas1").canonical
    response = client.put(
        "/oas1/FindJade/inviteInfoList_1/name/value",
        params={"types": "string", "value": "测试账号"},
    )
    assert response.status_code == 200
    assert response.json() is True
    after = mm.store.load("oas1").canonical
    assert after["find_jade"]["invite_info_list_1"]["name"] == "测试账号"
    assert after["find_jade"]["find_jade_config"]["invite_info_count"] == \
        before["find_jade"]["find_jade_config"]["invite_info_count"]
    assert mm.store.last_operation.operation == "REPLACE_PATH_SET"


def test_oasx_dynamic_count_shrink_deletes_residual_members(client):
    from module.server.main_manager import mm

    mm.store.patch_user_argument("oas1", "FindJade", "findJadeConfig", "inviteInfoCount", 2)
    response = client.put(
        "/oas1/FindJade/findJadeConfig/inviteInfoCount/value",
        params={"types": "integer", "value": 1},
    )
    assert response.status_code == 200
    task = mm.store.load("oas1").canonical["find_jade"]
    assert task["find_jade_config"]["invite_info_count"] == 1
    assert "invite_info_list_1" in task
    assert "invite_info_list_2" not in task


def test_oasx_dynamic_group_rejects_out_of_range_index(client):
    response = client.put(
        "/oas1/FindJade/inviteInfoList_99/name/value",
        params={"types": "string", "value": "不存在"},
    )
    assert response.status_code == 400


def test_oasx_put_missing_config_is_404(client):
    response = client.put(
        "/does-not-exist/Orochi/orochiConfig/limitCount/value",
        params={"types": "integer", "value": 1},
    )
    assert response.status_code == 404


@pytest.mark.parametrize("operation", ["rename", "delete"])
def test_lifecycle_real_task_cancellation_reconciles_before_reraise(tmp_path, monkeypatch, operation):
    """真实 task.cancel 发生在 stop 广播等待期间时，先恢复 source 再重抛取消。"""
    import asyncio
    import module.server.script_process as script_process_module
    from module.server.main_manager import MainManager
    from module.server.script_process import ScriptState

    class FakeChild:
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

    monkeypatch.setattr(script_process_module.multiprocessing, "Process", FakeChild)
    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("source", _canonical_template())
    manager = MainManager(store=store)
    process = script_process_module.ScriptProcess("source", store=store)

    async def quiet_broadcast(_data):
        return None

    process.broadcast_state = quiet_broadcast
    assert asyncio.run(process.start()) is True
    manager.script_process = {"source": process}

    async def scenario():
        entered = asyncio.Event()
        broadcast_calls = 0
        release = asyncio.Event()

        async def cancellation_point(_data):
            nonlocal broadcast_calls
            broadcast_calls += 1
            if broadcast_calls == 1:
                entered.set()
                await release.wait()

        process.broadcast_state = cancellation_point
        task = asyncio.create_task(
            manager.rename_config("source", "destination")
            if operation == "rename"
            else manager.delete_config("source")
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert store.generation.read_active_generation("source").state == "active"
    if operation == "rename":
        assert store.generation.read_active_generation("destination") is None
    assert manager.script_process["source"] is process
    assert process.state == ScriptState.RUNNING
    assert process._process is not None and process._process.is_alive()

    process.broadcast_state = quiet_broadcast
    asyncio.run(process.stop())


@pytest.mark.parametrize("operation", ["rename", "delete"])
def test_store_error_survives_real_cancel_during_restore(tmp_path, monkeypatch, operation):
    """Store 原错后恢复 task 被取消时，仍完成恢复并优先重抛 Store 异常。"""
    import asyncio
    import module.server.script_process as script_process_module
    from filelock import Timeout
    from module.server.main_manager import MainManager
    from module.server.script_process import ScriptState

    class FakeChild:
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

    monkeypatch.setattr(script_process_module.multiprocessing, "Process", FakeChild)
    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("source", _canonical_template())
    manager = MainManager(store=store)
    process = script_process_module.ScriptProcess("source", store=store)

    async def quiet_broadcast(_data):
        return None

    process.broadcast_state = quiet_broadcast
    assert asyncio.run(process.start()) is True
    manager.script_process = {"source": process}

    def fail_store(*_args, **_kwargs):
        raise Timeout("original store timeout")

    monkeypatch.setattr(
        store,
        "rename_config" if operation == "rename" else "delete_config",
        fail_store,
    )

    async def scenario():
        restore_entered = asyncio.Event()
        release_restore = asyncio.Event()

        async def pause_restore(data):
            if data.get("state") == ScriptState.RUNNING:
                restore_entered.set()
                await release_restore.wait()

        process.broadcast_state = pause_restore
        task = asyncio.create_task(
            manager.rename_config("source", "destination")
            if operation == "rename"
            else manager.delete_config("source")
        )
        await restore_entered.wait()
        task.cancel()
        # shield 的 reconcile task 仍须完成，取消不能使原 Store 错误丢失。
        release_restore.set()
        with pytest.raises(Timeout, match="original store timeout"):
            await task

    asyncio.run(scenario())

    assert store.generation.read_active_generation("source").state == "active"
    if operation == "rename":
        assert store.generation.read_active_generation("destination") is None
    assert manager.script_process["source"] is process
    assert process.state == ScriptState.RUNNING
    assert process._process is not None and process._process.is_alive()

    process.broadcast_state = quiet_broadcast
    asyncio.run(process.stop())



def test_kill_signal_retries_live_failed_instance_before_thread_exit(tmp_path):
    """kill signal 首批含仍活失败实例时，后续实例照停且推送线程不得假成功退出。"""
    import asyncio
    import threading
    from module.server.main_manager import MainManager

    class LiveHandle:
        def __init__(self):
            self.alive = True

        def is_alive(self):
            return self.alive

    class RetryProcess:
        # 明确声明合法 stop 终态，仍活句柄由 stop_all_processes 额外核验。
        state = 0

        def __init__(self, fail_once=False):
            self.fail_once = fail_once
            self.stop_calls = 0
            self._process = LiveHandle() if fail_once else None

        async def stop(self):
            self.stop_calls += 1
            if self.fail_once and self.stop_calls == 1:
                raise RuntimeError("live process stop failed")
            self._process = None

    manager = MainManager(store=ConfigStore(config_root=tmp_path / "config"))
    manager._push_interval = 0.001
    first = RetryProcess(fail_once=True)
    second = RetryProcess()
    old_signal = MainManager.signal_kill_server
    MainManager.signal_kill_server = False

    async def scenario():
        manager._main_loop = asyncio.get_running_loop()
        manager.script_process = {"first": first, "second": second}
        manager.start_push_data_thread()
        await asyncio.sleep(0.02)
        MainManager.signal_kill_server = True
        for _ in range(400):
            if manager.push_data_thread is not None and not manager.push_data_thread.is_alive():
                break
            await asyncio.sleep(0.005)
        assert first.stop_calls >= 2
        assert second.stop_calls >= 2
        assert manager.push_data_thread.is_alive() is False

    try:
        asyncio.run(scenario())
    finally:
        MainManager.signal_kill_server = old_signal
        manager._push_shutdown_event.set()
        if manager.push_data_thread is not None:
            manager.push_data_thread.join(timeout=2)


@pytest.mark.parametrize("operation", ["rename", "delete"])
def test_lifecycle_reconcile_timeout_is_bounded_and_retains_handle(tmp_path, monkeypatch, operation):
    """恢复广播永不返回时，对账有界收敛并保留 source 句柄供后续 stop 重试。"""
    import asyncio
    import module.server.script_process as script_process_module
    from module.server.main_manager import MainManager
    from module.server.script_process import ScriptState

    class FakeChild:
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

    monkeypatch.setattr(script_process_module.multiprocessing, "Process", FakeChild)
    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("source", _canonical_template())
    manager = MainManager(store=store)
    manager._reconcile_timeout = 0.05
    manager._reconcile_cancel_timeout = 0.05
    process = script_process_module.ScriptProcess("source", store=store)

    async def quiet_broadcast(_data):
        return None

    process.broadcast_state = quiet_broadcast
    assert asyncio.run(process.start()) is True
    manager.script_process = {"source": process}

    async def stuck_broadcast(data):
        if data.get("state") == ScriptState.INACTIVE:
            raise asyncio.CancelledError()
        await asyncio.Event().wait()

    process.broadcast_state = stuck_broadcast
    started = __import__("time").monotonic()
    call = (
        manager.rename_config("source", "destination")
        if operation == "rename"
        else manager.delete_config("source")
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(call)
    elapsed = __import__("time").monotonic() - started

    assert elapsed < 1.0
    assert manager.script_process["source"] is process
    assert process.state == ScriptState.RUNNING
    assert process._process is not None and process._process.is_alive()
    assert not manager._managed_reconcile_tasks

    process.broadcast_state = quiet_broadcast
    asyncio.run(process.stop())


def test_stop_all_rejects_missing_and_unknown_termination_state(tmp_path):
    """kill-server 对缺失属性与未知 alive 状态 fail-closed，仍继续停止合法实例。"""
    import asyncio
    from module.server.main_manager import MainManager
    from module.server.script_process import ScriptState

    class MissingState:
        async def stop(self):
            self.called = True

    class UnknownAlive:
        state = ScriptState.INACTIVE

        class Handle:
            def is_alive(self):
                raise OSError("alive unavailable")

        def __init__(self):
            self._process = self.Handle()

        async def stop(self):
            self.called = True

    class ValidInactive:
        state = ScriptState.INACTIVE
        _process = None

        async def stop(self):
            self.called = True

    manager = MainManager(store=ConfigStore(config_root=tmp_path / "config"))
    missing = MissingState()
    unknown = UnknownAlive()
    valid = ValidInactive()
    manager.script_process = {"missing": missing, "unknown": unknown, "valid": valid}

    assert asyncio.run(manager.stop_all_processes()) is False
    assert missing.called is True
    assert unknown.called is True
    assert valid.called is True
    assert [name for name, _error in manager._last_stop_all_errors] == [
        "missing",
        "unknown",
    ]



def test_kill_request_blocked_callback_closes_without_unawaited_warning(tmp_path):
    """loop callback 被阻塞后停止/关闭时，不创建未拥有的 coroutine 或产生 RuntimeWarning。"""
    import asyncio
    import threading
    import time
    import warnings
    from module.server.main_manager import MainManager

    manager = MainManager(store=ConfigStore(config_root=tmp_path / "config"))
    manager._stop_request_timeout = 0.01
    loop = asyncio.new_event_loop()
    manager._main_loop = loop
    blocker_started = threading.Event()

    def blocker():
        blocker_started.set()
        time.sleep(0.15)

    loop.call_soon(blocker)
    thread = threading.Thread(target=loop.run_forever)
    thread.start()
    assert blocker_started.wait(timeout=2)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert manager._request_stop_all_from_push_thread() is False
        # 停止时提交 callback 尚未执行，关闭 loop 应安全丢弃 callback。
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        loop.close()

    assert thread.is_alive() is False
    assert not any("never awaited" in str(item.message).lower() for item in caught)


@pytest.mark.parametrize("alive_value", [None, "yes"])
def test_stop_all_rejects_non_boolean_alive_result(tmp_path, alive_value):
    """kill-server 只有明确 False 才能确认句柄退出，None/string 必须失败。"""
    import asyncio
    from module.server.main_manager import MainManager
    from module.server.script_process import ScriptState

    class Handle:
        def is_alive(self):
            return alive_value

    class Process:
        state = ScriptState.INACTIVE

        def __init__(self):
            self._process = Handle()

        async def stop(self):
            return None

    manager = MainManager(store=ConfigStore(config_root=tmp_path / "config"))
    manager.script_process = {"non_boolean": Process()}

    assert asyncio.run(manager.stop_all_processes()) is False
    assert [name for name, _error in manager._last_stop_all_errors] == ["non_boolean"]



def test_stop_all_detects_registry_insertion_during_snapshot(tmp_path):
    """stop await 期间插入的新 wrapper 必须被停止且批次返回失败供重试。"""
    import asyncio
    from module.server.main_manager import MainManager
    from module.server.script_process import ScriptState

    class Process:
        state = ScriptState.INACTIVE
        _process = None

        def __init__(self, name, on_stop=None):
            self.name = name
            self.on_stop = on_stop
            self.stop_calls = 0

        async def stop(self):
            self.stop_calls += 1
            if self.on_stop is not None:
                await self.on_stop()

    manager = MainManager(store=ConfigStore(config_root=tmp_path / "config"))
    inserted = Process("inserted")
    inserted_once = False

    async def insert_new():
        nonlocal inserted_once
        if not inserted_once:
            inserted_once = True
            manager.script_process["inserted"] = inserted
            await asyncio.sleep(0)

    first = Process("first", on_stop=insert_new)
    manager.script_process = {"first": first}

    assert asyncio.run(manager.stop_all_processes()) is False
    assert first.stop_calls == 1
    assert inserted.stop_calls == 1
    assert any(name == "__registry__" for name, _error in manager._last_stop_all_errors)



def test_kill_request_create_task_failure_closes_coroutine_without_warning(tmp_path):
    """loop.create_task 提交失败时显式关闭 coroutine，不产生 RuntimeWarning。"""
    import asyncio
    import threading
    import warnings
    from module.server.main_manager import MainManager

    manager = MainManager(store=ConfigStore(config_root=tmp_path / "config"))
    manager._stop_request_timeout = 0.2

    async def scenario():
        loop = asyncio.get_running_loop()
        manager._main_loop = loop
        original_create_task = loop.create_task

        def fail_create_task(coroutine, *args, **kwargs):
            raise RuntimeError("injected create_task failure")

        loop.create_task = fail_create_task
        result = []
        done = threading.Event()

        def request():
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                result.append(manager._request_stop_all_from_push_thread())
                result.append(
                    any("never awaited" in str(item.message).lower() for item in caught)
                )
            done.set()

        thread = threading.Thread(target=request)
        thread.start()
        while not done.is_set():
            await asyncio.sleep(0.005)
        thread.join(timeout=2)
        loop.create_task = original_create_task
        assert thread.is_alive() is False
        assert result == [False, False]

    asyncio.run(scenario())



def test_kill_timeout_retains_old_batch_before_retry(tmp_path):
    """批次 timeout 后旧 stop task 仍托管时，重试不得与旧批次重叠抢锁。"""
    import asyncio
    import threading
    from module.server.main_manager import MainManager
    from module.server.script_process import ScriptState

    class First:
        state = ScriptState.INACTIVE
        _process = None

        async def stop(self):
            return None

    class Second:
        state = ScriptState.INACTIVE
        _process = None

        def __init__(self):
            self.stop_calls = 0
            self.release = asyncio.Event()

        async def stop(self):
            self.stop_calls += 1
            if self.stop_calls == 1:
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    # 模拟不立即响应取消的旧批次，直到测试显式释放。
                    await self.release.wait()
                    raise

    manager = MainManager(store=ConfigStore(config_root=tmp_path / "config"))
    manager._stop_request_timeout = 0.01
    manager._stop_cancel_timeout = 0.01
    second = Second()
    manager.script_process = {"first": First(), "second": second}

    async def scenario():
        manager._main_loop = asyncio.get_running_loop()
        done = threading.Event()
        result = []

        def request():
            result.append(manager._request_stop_all_from_push_thread())
            done.set()

        thread = threading.Thread(target=request)
        thread.start()
        while not done.is_set():
            await asyncio.sleep(0.005)
        thread.join(timeout=2)
        assert result == [False]
        assert manager._managed_stop_tasks
        # 旧 task 未收敛期间，新的请求明确失败且不增加 stop 调用。
        assert manager._request_stop_all_from_push_thread() is False
        assert second.stop_calls == 1
        second.release.set()
        for _ in range(100):
            if not manager._managed_stop_tasks:
                break
            await asyncio.sleep(0.005)
        assert not manager._managed_stop_tasks

    asyncio.run(scenario())



def test_reconcile_sync_store_lock_timeout_does_not_block_event_loop(tmp_path, monkeypatch):
    """同步 Store/FileLock 卡住时，to_thread 让 reconcile timeout 在短时间内生效。"""
    import asyncio
    import threading
    import time
    from module.server.main_manager import MainManager

    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("source", _canonical_template())
    manager = MainManager(store=store)
    manager._reconcile_timeout = 0.05
    entered = threading.Event()
    release = threading.Event()

    def blocked_reconcile():
        entered.set()
        release.wait(timeout=2)

    monkeypatch.setattr(store, "reconcile_lifecycle_transactions", blocked_reconcile)

    async def scenario():
        task = asyncio.create_task(manager._reconcile_after_lifecycle_failure("source"))
        assert await asyncio.to_thread(entered.wait, 1)
        started = time.monotonic()
        try:
            with pytest.raises(asyncio.CancelledError):
                await task
            elapsed = time.monotonic() - started
            assert elapsed < 0.5
        finally:
            release.set()

    asyncio.run(scenario())
    assert not manager._managed_reconcile_tasks



@pytest.mark.parametrize("replace_generation", [False, True])
def test_preserve_reconcile_registry_uses_object_and_generation_cas(
    tmp_path, replace_generation
):
    """旧 reconcile 不能覆盖并发安装的新 wrapper，也不能保留 ABA 的旧 generation。"""
    import asyncio
    from module.server.main_manager import MainManager
    from module.server.script_process import ScriptProcess

    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("source", _canonical_template())
    manager = MainManager(store=store)
    old_process = ScriptProcess("source", store=store)
    if replace_generation:
        store.delete_config("source")
        store.create_from_template("source", _canonical_template())
    new_process = ScriptProcess("source", store=store)
    manager.script_process = {"source": new_process}

    asyncio.run(
        manager._preserve_reconcile_registry(
            ("source",),
            {"process": old_process, "was_running": True},
        )
    )

    assert manager.script_process["source"] is new_process



def test_reconcile_completed_or_cancelled_has_no_managed_task(tmp_path, monkeypatch):
    """立即完成或取消已收敛的 reconcile task 不得残留在托管集合。"""
    import asyncio
    from module.server.main_manager import MainManager

    manager = MainManager(store=ConfigStore(config_root=tmp_path / "config"))

    async def scenario():
        async def completed(*_args, **_kwargs):
            return None

        monkeypatch.setattr(manager, "_reconcile_lifecycle_registry", completed)
        await manager._reconcile_after_lifecycle_failure("source")
        assert not manager._managed_reconcile_tasks

        cancellable = asyncio.Event()

        async def wait_until_cancelled(*_args, **_kwargs):
            await cancellable.wait()

        manager._reconcile_timeout = 0.001
        manager._reconcile_cancel_timeout = 0.1
        monkeypatch.setattr(
            manager,
            "_reconcile_lifecycle_registry",
            wait_until_cancelled,
        )
        with pytest.raises(asyncio.CancelledError):
            await manager._reconcile_after_lifecycle_failure("source")
        assert not manager._managed_reconcile_tasks

    asyncio.run(scenario())



def test_managed_reconcile_task_consumes_error_and_removes_owner(tmp_path, monkeypatch):
    """托管 reconcile task 完成后必须消费异常并移除所有权记录。"""
    import asyncio
    import module.server.main_manager as main_manager_module
    from module.server.main_manager import MainManager

    manager = MainManager(store=ConfigStore(config_root=tmp_path / "config"))
    error_messages = []
    monkeypatch.setattr(main_manager_module.logger, "error", error_messages.append)

    async def scenario():
        release = asyncio.Event()

        async def fail_late():
            await release.wait()
            raise RuntimeError("late reconcile failure")

        task = asyncio.create_task(fail_late())
        manager._remember_reconcile_task(task)
        try:
            assert not task.done()
            assert task in manager._managed_reconcile_tasks
            release.set()

            async def wait_until_removed():
                while task in manager._managed_reconcile_tasks:
                    await asyncio.sleep(0)

            await asyncio.wait_for(wait_until_removed(), timeout=1.0)
            assert task.done()
            assert not manager._managed_reconcile_tasks
            assert error_messages == [
                "lifecycle reconcile task completed after timeout: "
                "RuntimeError: late reconcile failure"
            ]
        finally:
            # 即使断言失败也释放并回收 task，避免污染 asyncio.run 的 teardown。
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["delete", "rename"])
def test_identity_guard_serializes_start_behind_lifecycle_commit(
    tmp_path, monkeypatch, operation
):
    """INACTIVE 广播窗口中的并发启动必须等待身份提交，不能遗留 source 孤儿进程。"""
    import asyncio
    import module.server.script_process as script_process_module
    from module.server.main_manager import MainManager
    from module.server.script_process import ScriptState

    children = []

    class FakeChild:
        def __init__(self, *args, **kwargs):
            self.alive = False
            children.append(self)

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

    monkeypatch.setattr(script_process_module.multiprocessing, "Process", FakeChild)
    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("source", _canonical_template())
    manager = MainManager(store=store)
    process = script_process_module.ScriptProcess("source", store=store)

    async def quiet(_data):
        return None

    process.broadcast_state = quiet
    assert asyncio.run(process.start()) is True
    manager.script_process = {"source": process}

    async def scenario():
        inactive_broadcast_entered = asyncio.Event()
        release_broadcast = asyncio.Event()
        lifecycle_task = None
        start_task = None

        async def block_inactive(data):
            if data.get("state") == ScriptState.INACTIVE:
                inactive_broadcast_entered.set()
                await release_broadcast.wait()

        process.broadcast_state = block_inactive
        try:
            lifecycle_call = (
                manager.delete_config("source")
                if operation == "delete"
                else manager.rename_config("source", "destination")
            )
            lifecycle_task = asyncio.create_task(lifecycle_call)
            await asyncio.wait_for(inactive_broadcast_entered.wait(), timeout=1.0)

            start_task = asyncio.create_task(manager.start_script_process("source"))
            await asyncio.sleep(0)
            # manager 身份锁必须让启动停在 load/ensure 之前，直到 Store 提交完成。
            assert not start_task.done()

            release_broadcast.set()
            await asyncio.wait_for(lifecycle_task, timeout=1.0)
            with pytest.raises(ConfigNotFoundError):
                await asyncio.wait_for(start_task, timeout=1.0)
        finally:
            release_broadcast.set()
            for task in (lifecycle_task, start_task):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (lifecycle_task, start_task) if task is not None),
                return_exceptions=True,
            )

        assert process.state == ScriptState.INACTIVE
        assert process._process is None
        assert all(not child.is_alive() for child in children)
        assert "source" not in manager.script_process
        with pytest.raises(ConfigNotFoundError):
            store.load("source")
        if operation == "rename":
            assert store.load("destination").canonical["config_name"] == "destination"
            assert manager.script_process["destination"].state == ScriptState.INACTIVE

    asyncio.run(scenario())


def test_reconcile_stubborn_broadcast_does_not_hold_lifecycle_lock(tmp_path, monkeypatch):
    """广播忽略取消时 reconcile 可被托管，后续 stop 仍能获取 lifecycle lock 并回收句柄。"""
    import asyncio
    import module.server.script_process as script_process_module
    from module.server.main_manager import MainManager
    from module.server.script_process import ScriptState

    class FakeChild:
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

    monkeypatch.setattr(script_process_module.multiprocessing, "Process", FakeChild)
    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("source", _canonical_template())
    manager = MainManager(store=store)
    # 给恢复流程足够时间进入 stubborn 广播，再由测试主动触发取消。
    manager._reconcile_timeout = 0.5
    manager._reconcile_cancel_timeout = 0.03
    process = script_process_module.ScriptProcess("source", store=store)

    async def quiet(_data):
        return None

    process.broadcast_state = quiet
    assert asyncio.run(process.start()) is True
    manager.script_process = {"source": process}

    async def scenario():
        release = asyncio.Event()
        stubborn_started = asyncio.Event()
        delete_task = None

        async def stubborn(data):
            if data.get("state") == ScriptState.INACTIVE:
                raise asyncio.CancelledError()
            stubborn_started.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    # 模拟第三方广播实现错误地忽略取消。
                    continue

        process.broadcast_state = stubborn
        try:
            delete_task = asyncio.create_task(manager.delete_config("source"))
            await asyncio.wait_for(stubborn_started.wait(), timeout=1.0)
            delete_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await delete_task
            assert manager._managed_reconcile_tasks

            process.broadcast_state = quiet
            await asyncio.wait_for(process.stop(), timeout=0.5)
            assert process._process is None
        finally:
            # 无论断言是否成功，都释放 stubborn task 并回收 delete/reconcile 所有权。
            process.broadcast_state = quiet
            release.set()
            if delete_task is not None and not delete_task.done():
                delete_task.cancel()
            if delete_task is not None:
                await asyncio.gather(delete_task, return_exceptions=True)
            for _ in range(100):
                if not manager._managed_reconcile_tasks:
                    break
                await asyncio.sleep(0.005)
        assert not manager._managed_reconcile_tasks

    asyncio.run(scenario())



def test_reconcile_held_identity_lock_respects_async_timeout(tmp_path):
    """真实 per-identity FileLock 被占用时，主 loop 不等待 Store 默认十秒。"""
    import asyncio
    import time
    from module.server.main_manager import MainManager

    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("source", _canonical_template())
    manager = MainManager(store=store)
    manager._reconcile_timeout = 0.05
    lock = store.generation._lifecycle_lock("source")
    lock.acquire()
    started = time.monotonic()
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(asyncio.CancelledError):
            loop.run_until_complete(manager._reconcile_after_lifecycle_failure("source"))
        assert time.monotonic() - started < 0.5
    finally:
        lock.release()
        loop.close()



def test_state_broadcaster_stale_handle_cas_preserves_restarted_child(
    tmp_path, monkeypatch
):
    """旧句柄探针返回前完成 restart 时，broadcaster 不得清理新句柄与状态。"""
    import asyncio
    import threading
    import module.server.script_process as script_process_module
    from module.server.script_process import ScriptState

    children = []

    class FakeChild:
        def __init__(self, *args, **kwargs):
            self.alive = False
            self.block_broadcaster = False
            self.probe_entered = threading.Event()
            self.probe_release = threading.Event()
            self.broadcaster_observed = threading.Event()
            children.append(self)

        def start(self):
            self.alive = True

        def arm_blocking_probe(self):
            self.block_broadcaster = True
            self.probe_entered = threading.Event()
            self.probe_release = threading.Event()

        def is_alive(self):
            if threading.current_thread().name == "stale-state-broadcaster":
                self.broadcaster_observed.set()
                if self.block_broadcaster:
                    self.probe_entered.set()
                    if not self.probe_release.wait(timeout=2.0):
                        raise TimeoutError("state broadcaster probe was not released")
                    self.block_broadcaster = False
            return self.alive

        def terminate(self):
            self.alive = False

        def join(self, timeout=0):
            return None

        def kill(self):
            self.alive = False

    async def fast_sleep(_delay):
        # 缩短 broadcaster 固定轮询间隔，让二十轮竞态保持确定且快速。
        await asyncio.sleep(0.001)

    monkeypatch.setattr(script_process_module.multiprocessing, "Process", FakeChild)
    monkeypatch.setattr(script_process_module, "sleep", fast_sleep)
    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("source", _canonical_template())
    process = script_process_module.ScriptProcess("source", store=store)
    broadcasts = []

    async def quiet_broadcast(data):
        broadcasts.append(data)

    process.broadcast_state = quiet_broadcast
    broadcaster_ready = threading.Event()
    broadcaster_done = threading.Event()
    broadcaster_owner = {}
    broadcaster_errors = []

    def run_broadcaster():
        async def runner():
            broadcaster_owner["loop"] = asyncio.get_running_loop()
            task = asyncio.create_task(process.coroutine_broadcast_state())
            broadcaster_owner["task"] = task
            broadcaster_ready.set()
            await asyncio.gather(task)

        try:
            asyncio.run(runner())
        except BaseException as error:
            broadcaster_errors.append(error)
        finally:
            broadcaster_done.set()

    thread = threading.Thread(
        target=run_broadcaster,
        name="stale-state-broadcaster",
        daemon=True,
    )

    async def scenario():
        assert await process.start() is True
        thread.start()
        assert broadcaster_ready.wait(timeout=2.0)
        try:
            for iteration in range(20):
                checked_child = process._process
                checked_child.arm_blocking_probe()
                assert checked_child.probe_entered.wait(timeout=2.0)

                # 同一 wrapper 在旧探针阻塞期间完成 stop + 新句柄替换。
                assert await process.start() is True
                replacement = process._process
                assert replacement is not checked_child
                marker = {"iteration": iteration}
                process._config_state_cache = marker

                checked_child.probe_release.set()
                assert replacement.broadcaster_observed.wait(timeout=2.0)
                assert process._process is replacement
                assert replacement.is_alive() is True
                assert process.state == ScriptState.RUNNING
                assert process._config_state_cache is marker
        finally:
            for child in children:
                child.probe_release.set()
            loop = broadcaster_owner.get("loop")
            task = broadcaster_owner.get("task")
            if loop is not None and task is not None and not task.done():
                loop.call_soon_threadsafe(task.cancel)
            assert broadcaster_done.wait(timeout=2.0)
            thread.join(timeout=2.0)
            assert thread.is_alive() is False
            await process.stop()

        assert process._process is None
        assert process.state == ScriptState.INACTIVE
        assert all(child.is_alive() is False for child in children)

    asyncio.run(scenario())
    assert broadcaster_errors == []
    assert all(data.get("state") != ScriptState.INACTIVE for data in broadcasts[:-1])


def test_state_broadcaster_post_check_commit_serializes_restart(
    tmp_path, monkeypatch
):
    """broadcaster 已复核身份并进入提交区后，restart 必须等待同一线程锁。"""
    import asyncio
    import threading
    import module.server.script_process as script_process_module
    from module.server.script_process import ScriptState

    children = []

    class FakeChild:
        def __init__(self, *args, **kwargs):
            self.alive = False
            children.append(self)

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

    async def fast_sleep(_delay):
        # 缩短固定轮询间隔，二十轮精确临界区竞态仍保持确定。
        await asyncio.sleep(0.001)

    monkeypatch.setattr(script_process_module.multiprocessing, "Process", FakeChild)
    monkeypatch.setattr(script_process_module, "sleep", fast_sleep)
    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("source", _canonical_template())
    process = script_process_module.ScriptProcess("source", store=store)
    broadcasts = []

    async def quiet_broadcast(data):
        broadcasts.append(data)

    process.broadcast_state = quiet_broadcast
    clear_gate = {
        "armed": False,
        "entered": threading.Event(),
        "release": threading.Event(),
    }
    snapshot_gate = {
        "armed": False,
        "entered": threading.Event(),
    }
    original_clear = process._clear_config_state
    original_snapshot = process._process_snapshot

    def blocking_clear():
        if (
            clear_gate["armed"]
            and threading.current_thread().name == "post-check-state-broadcaster"
        ):
            clear_gate["entered"].set()
            if not clear_gate["release"].wait(timeout=2.0):
                raise TimeoutError("post-check process commit was not released")
        original_clear()

    def observed_snapshot():
        if (
            snapshot_gate["armed"]
            and threading.current_thread().name == "post-check-main-loop"
        ):
            # 事件在真正申请线程锁前触发，可确定 start 已抵达被串行化的提交点。
            snapshot_gate["entered"].set()
        return original_snapshot()

    monkeypatch.setattr(process, "_clear_config_state", blocking_clear)
    monkeypatch.setattr(process, "_process_snapshot", observed_snapshot)

    main_ready = threading.Event()
    main_done = threading.Event()
    main_owner = {}
    main_errors = []

    def run_main_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        main_owner["loop"] = loop
        main_ready.set()
        try:
            loop.run_forever()
        except BaseException as error:
            main_errors.append(error)
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
            main_done.set()

    broadcaster_ready = threading.Event()
    broadcaster_done = threading.Event()
    broadcaster_owner = {}
    broadcaster_errors = []

    def run_broadcaster():
        async def runner():
            broadcaster_owner["loop"] = asyncio.get_running_loop()
            task = asyncio.create_task(process.coroutine_broadcast_state())
            broadcaster_owner["task"] = task
            broadcaster_ready.set()
            await asyncio.gather(task)

        try:
            asyncio.run(runner())
        except BaseException as error:
            broadcaster_errors.append(error)
        finally:
            broadcaster_done.set()

    main_thread = threading.Thread(target=run_main_loop, name="post-check-main-loop")
    broadcaster_thread = threading.Thread(
        target=run_broadcaster,
        name="post-check-state-broadcaster",
    )
    pending_start = None
    main_loop = None
    main_thread.start()
    try:
        assert main_ready.wait(timeout=2.0)
        main_loop = main_owner["loop"]
        pending_start = asyncio.run_coroutine_threadsafe(process.start(), main_loop)
        assert pending_start.result(timeout=2.0) is True
        pending_start = None
        broadcaster_thread.start()
        assert broadcaster_ready.wait(timeout=2.0)

        for iteration in range(20):
            checked_child = process._process_snapshot()
            clear_gate.update(
                armed=True,
                entered=threading.Event(),
                release=threading.Event(),
            )
            snapshot_gate.update(armed=True, entered=threading.Event())
            checked_child.alive = False
            assert clear_gate["entered"].wait(timeout=2.0)

            pending_start = asyncio.run_coroutine_threadsafe(process.start(), main_loop)
            assert snapshot_gate["entered"].wait(timeout=2.0)
            # start 已尝试取得短锁，但 broadcaster 的原子清理未释放前不能构造 replacement。
            assert pending_start.done() is False
            assert len(children) == iteration + 1

            clear_gate["release"].set()
            assert pending_start.result(timeout=2.0) is True
            pending_start = None
            clear_gate["armed"] = False
            snapshot_gate["armed"] = False

            replacement = process._process_snapshot()
            assert replacement is not checked_child
            assert replacement.is_alive() is True
            with process._process_state_lock:
                assert process.state == ScriptState.RUNNING
    finally:
        clear_gate["armed"] = False
        snapshot_gate["armed"] = False
        clear_gate["release"].set()
        if pending_start is not None:
            try:
                pending_start.result(timeout=2.0)
            except BaseException:
                pending_start.cancel()

        cleanup_loop = main_loop or main_owner.get("loop")
        if cleanup_loop is not None and main_thread.is_alive():
            try:
                stop_future = asyncio.run_coroutine_threadsafe(
                    process.stop(), cleanup_loop
                )
                stop_future.result(timeout=2.0)
            except BaseException as error:
                main_errors.append(error)

        broadcaster_loop = broadcaster_owner.get("loop")
        broadcaster_task = broadcaster_owner.get("task")
        if (
            broadcaster_loop is not None
            and broadcaster_task is not None
            and not broadcaster_task.done()
        ):
            broadcaster_loop.call_soon_threadsafe(broadcaster_task.cancel)
        if broadcaster_thread.is_alive():
            broadcaster_done.wait(timeout=2.0)
            broadcaster_thread.join(timeout=2.0)

        if cleanup_loop is not None and main_thread.is_alive():
            cleanup_loop.call_soon_threadsafe(cleanup_loop.stop)
            main_done.wait(timeout=2.0)
        main_thread.join(timeout=2.0)

    assert broadcaster_thread.is_alive() is False
    assert main_thread.is_alive() is False
    assert broadcaster_errors == []
    assert main_errors == []
    assert process._process_snapshot() is None
    with process._process_state_lock:
        assert process.state == ScriptState.INACTIVE
    assert all(child.is_alive() is False for child in children)
    assert sum(data.get("state") == ScriptState.INACTIVE for data in broadcasts) >= 20


def test_stop_locked_stale_handle_cas_preserves_replacement(tmp_path):
    """stop 在旧句柄阻塞期间出现 replacement 时，最终提交不得覆盖新句柄。"""
    import asyncio
    import threading
    import module.server.script_process as script_process_module
    from module.server.script_process import ScriptState

    probe_entered = threading.Event()
    probe_release = threading.Event()

    class BlockingHandle:
        def __init__(self, *, block=False):
            self.alive = True
            self.block = block
            self.blocked_once = False

        def is_alive(self):
            if (
                self.block
                and not self.blocked_once
                and threading.current_thread().name == "stale-stop-worker"
            ):
                self.blocked_once = True
                probe_entered.set()
                if not probe_release.wait(timeout=2.0):
                    raise TimeoutError("stale stop probe was not released")
            return self.alive

        def terminate(self):
            self.alive = False

        def join(self, timeout=0):
            return None

        def kill(self):
            self.alive = False

    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("source", _canonical_template())
    process = script_process_module.ScriptProcess("source", store=store)

    async def quiet_broadcast(_data):
        return None

    process.broadcast_state = quiet_broadcast
    stale = BlockingHandle(block=True)
    replacement = BlockingHandle()
    marker = {"replacement": True}
    assert process._commit_process_if_current(
        None, stale, ScriptState.RUNNING
    ) is True

    worker_errors = []

    def run_stale_stop():
        try:
            asyncio.run(process._stop_locked(broadcast=False))
        except BaseException as error:
            worker_errors.append(error)

    worker = threading.Thread(target=run_stale_stop, name="stale-stop-worker")
    worker.start()
    try:
        assert probe_entered.wait(timeout=2.0)
        assert process._commit_process_if_current(
            stale, replacement, ScriptState.RUNNING
        ) is True
        with process._process_state_lock:
            process._config_state_cache = marker
        probe_release.set()
        worker.join(timeout=2.0)

        assert worker.is_alive() is False
        assert worker_errors == []
        assert stale.is_alive() is False
        assert process._process_snapshot() is replacement
        assert replacement.is_alive() is True
        with process._process_state_lock:
            assert process.state == ScriptState.RUNNING
            assert process._config_state_cache is marker
    finally:
        probe_release.set()
        worker.join(timeout=2.0)
        asyncio.run(process.stop())

    assert worker.is_alive() is False
    assert process._process_snapshot() is None
    assert replacement.is_alive() is False
    with process._process_state_lock:
        assert process.state == ScriptState.INACTIVE


@pytest.mark.parametrize(
    ("stop_mode", "retained"),
    [
        ("live", True),
        ("unknown", True),
        ("stop_error", True),
        ("confirmed", False),
    ],
)
def test_preserve_reconcile_generation_mismatch_retires_only_confirmed_exit(
    tmp_path, stop_mode, retained
):
    """timeout preserve 仅在 stop 与终态探针都确认成功后移除旧 wrapper。"""
    import asyncio
    from module.server.main_manager import MainManager
    from module.server.script_process import ScriptState

    class ProbeHandle:
        def __init__(self, alive):
            self.alive = alive

        def is_alive(self):
            return self.alive

    class StaleProcess:
        generation = "stale-generation"

        def __init__(self):
            self.state = ScriptState.RUNNING
            alive = True if stop_mode == "live" else None
            self._process = ProbeHandle(alive)
            self.stop_calls = 0

        async def stop(self):
            self.stop_calls += 1
            if stop_mode == "stop_error":
                raise RuntimeError("injected stop failure")
            self.state = ScriptState.INACTIVE
            if stop_mode == "confirmed":
                self._process = None

    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("source", _canonical_template())
    manager = MainManager(store=store)
    process = StaleProcess()
    manager.script_process = {"source": process}

    asyncio.run(
        manager._preserve_reconcile_registry(
            ("source",), {"process": process, "was_running": True}
        )
    )

    assert process.stop_calls == 1
    assert (manager.script_process.get("source") is process) is retained


@pytest.mark.parametrize(
    ("stop_mode", "retained"),
    [("unknown", True), ("confirmed", False)],
)
def test_reconcile_active_new_generation_safely_retires_old_wrapper(
    tmp_path, stop_mode, retained
):
    """active source 已换 generation 时，普通 reconcile 安全回收旧身份或保留重试。"""
    import asyncio
    from module.server.main_manager import MainManager
    from module.server.script_process import ScriptState

    class UnknownHandle:
        def is_alive(self):
            return None

    class StaleProcess:
        generation = "stale-generation"

        def __init__(self):
            self.state = ScriptState.RUNNING
            self._process = UnknownHandle()
            self.stop_calls = 0
            self.start_calls = 0

        async def stop(self):
            self.stop_calls += 1
            self.state = ScriptState.INACTIVE
            if stop_mode == "confirmed":
                self._process = None

        async def start(self):
            self.start_calls += 1
            raise AssertionError("旧 generation 不得恢复启动")

    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("source", _canonical_template())
    manager = MainManager(store=store)
    process = StaleProcess()
    manager.script_process = {"source": process}

    asyncio.run(
        manager._reconcile_lifecycle_registry(
            "source", process=process, was_running=True
        )
    )

    assert process.stop_calls == 1
    assert process.start_calls == 0
    assert (manager.script_process.get("source") is process) is retained


def test_reconcile_generation_mismatch_never_touches_installed_new_wrapper(tmp_path):
    """registry 已安装新 generation wrapper 时，陈旧 reconcile 不停止或覆盖任何新对象。"""
    import asyncio
    from module.server.main_manager import MainManager
    from module.server.script_process import ScriptState

    class TrackingProcess:
        def __init__(self, generation):
            self.generation = generation
            self.state = ScriptState.RUNNING
            self._process = object()
            self.stop_calls = 0
            self.start_calls = 0

        async def stop(self):
            self.stop_calls += 1
            raise AssertionError("已替换对象不得被陈旧 reconcile 停止")

        async def start(self):
            self.start_calls += 1
            raise AssertionError("旧 generation 不得恢复启动")

    store = ConfigStore(config_root=tmp_path / "config")
    store.create_from_template("source", _canonical_template())
    current_generation = store.load("source").generation
    manager = MainManager(store=store)
    stale = TrackingProcess("stale-generation")
    installed = TrackingProcess(current_generation)
    manager.script_process = {"source": installed}

    asyncio.run(
        manager._reconcile_lifecycle_registry(
            "source", process=stale, was_running=True
        )
    )

    assert manager.script_process["source"] is installed
    assert stale.stop_calls == 0
    assert stale.start_calls == 0
    assert installed.stop_calls == 0
    assert installed.start_calls == 0
