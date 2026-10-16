"""现有实例选择模拟器的隔离路由测试，所有配置写入临时目录。"""
import copy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from filelock import Timeout

from module.config.config_model import ConfigModel
from module.config.config_store import ConfigStore
from module.server.config_manager import ConfigManager


pytestmark = pytest.mark.unit  # 独立路由应用不启动生产服务生命周期或脚本任务。


@pytest.fixture
def emulator_fields():
    """模拟用户确认选择后提交的五个设备字段。"""
    return {
        "serial": "192.168.1.211:5555", "handle": "小号陪刷",
        "emulatorinfo_name": "MuMuPlayer-12.0-0",
        "emulatorinfo_path": "I:/MuMu/nx_main/MuMuNxMain.exe", "emulatorinfo_type": "MuMuPlayer12",
    }


@pytest.fixture
def manager(tmp_path, monkeypatch):
    """临时实例和模板均来自默认模型，不接触真实账号配置。"""
    store = ConfigStore(config_root=tmp_path / "config")
    raw = ConfigModel().model_dump(mode="json")
    raw["script"]["device"]["handle"] = "原窗口"
    store.replace_template(raw)
    store.create_from_template("existing", raw)
    result = ConfigManager(store=store)
    result.notifications = []
    monkeypatch.setattr(result, "notify_config_changed", lambda name, change: result.notifications.append((name, change)), raising=False)
    return result


@pytest.fixture
def client(monkeypatch, manager):
    """仅挂载脚本路由，通知由替身记录，不创建或启动任何任务。"""
    from module.server import script_router

    monkeypatch.setattr(script_router, "mm", manager)
    app = FastAPI()
    app.include_router(script_router.script_app)
    test_client = TestClient(app)
    yield test_client
    test_client.close()


def _selection_payload(client, emulator_fields):
    """保存请求携带设置页获取的完整设备快照及代际。"""
    response = client.get("/existing/emulator")
    assert response.status_code == 200
    snapshot = response.json()
    return {"emulator": copy.deepcopy(emulator_fields), "expected_device": snapshot["device"],
            "expected_generation": snapshot["generation"]}


def test_plain_manager_copy_keeps_template_device(manager):
    """普通新建配置继续复制模板，不承担绑定已有实例的行为。"""
    before = manager.config_path("template").read_bytes()
    original = manager.store.load("template").canonical["script"]["device"]
    manager.copy("plain")
    assert manager.store.load("plain").canonical["script"]["device"] == original
    assert manager.config_path("template").read_bytes() == before


def test_plain_config_copy_route_remains_compatible(client, manager):
    """旧客户端无请求体创建配置保持成功，原有实例不变。"""
    before = manager.config_path("existing").read_bytes()
    response = client.post("/config_copy", params={"file": "plain"})
    assert response.status_code == 200
    assert response.json() == ["existing", "plain"]
    assert manager.store.load("plain").canonical["script"]["device"] == manager.store.load("template").canonical["script"]["device"]
    assert manager.config_path("existing").read_bytes() == before


def test_get_device_snapshot_is_read_only(client, manager, monkeypatch):
    """读取旧值用于确认页，不写配置或通知运行实例。"""
    loaded = manager.store.load("existing")
    before = manager.config_path("existing").read_bytes()
    monkeypatch.setattr(manager.store, "_write_config", lambda *args: pytest.fail("读取设置不应写入配置"))
    response = client.get("/existing/emulator")
    assert response.status_code == 200
    # 新增能力标记不影响设备快照内容，并让管家识别原子启动代际核对。
    assert response.json() == {"device": loaded.canonical["script"]["device"], "generation": loaded.generation,
                              "start_generation_check": True}
    assert manager.config_path("existing").read_bytes() == before
    assert manager.store.load("existing").mtime_ns == loaded.mtime_ns
    assert manager.notifications == []


def test_confirm_binding_writes_five_fields_once_and_notifies(client, manager, emulator_fields, monkeypatch):
    """确认后原子替换设备子树，保留其他设备设置、其他任务和模板。"""
    payload = _selection_payload(client, emulator_fields)
    loaded = manager.store.load("existing")
    template_bytes = manager.config_path("template").read_bytes()
    expected = copy.deepcopy(loaded.canonical)
    expected["script"]["device"].update(emulator_fields)
    writes = []
    original_write = manager.store._write_config
    original_replace = manager.store.replace_subtree
    state = {"inside_replace": False}

    def capture_write(name, canonical):
        writes.append((name, copy.deepcopy(canonical)))
        return original_write(name, canonical)

    def capture_replace(*args, **kwargs):
        state["inside_replace"] = True
        try:
            return original_replace(*args, **kwargs)
        finally:
            state["inside_replace"] = False

    def capture_notification(name, change):
        assert not state["inside_replace"]  # 通知发生在配置事务返回之后。
        assert manager.store.load(name).canonical == expected
        manager.notifications.append((name, change))

    monkeypatch.setattr(manager.store, "_write_config", capture_write)
    monkeypatch.setattr(manager.store, "replace_subtree", capture_replace)
    monkeypatch.setattr(manager, "notify_config_changed", capture_notification)
    response = client.put("/existing/emulator", json=payload)
    assert response.status_code == 200
    assert response.json() is True
    assert writes == [("existing", expected)]
    current = manager.store.load("existing")
    assert current.canonical == expected
    assert current.generation == loaded.generation
    assert manager.config_path("template").read_bytes() == template_bytes
    assert manager.store.active_config_names(include_template=True) == ["existing", "template"]
    assert len(manager.notifications) == 1
    name, change = manager.notifications[0]
    assert name == "existing"
    assert change.success is True
    assert change.changed_paths == [("script", "device")]
    assert change.generation == loaded.generation


def test_saving_identical_selection_is_noop(client, manager, emulator_fields, monkeypatch):
    """再次保存同一选择时保持文件字节与修改时间，不重复落盘。"""
    assert client.put("/existing/emulator", json=_selection_payload(client, emulator_fields)).status_code == 200
    payload = _selection_payload(client, emulator_fields)
    before = manager.store.load("existing")
    raw = manager.config_path("existing").read_bytes()
    monkeypatch.setattr(manager.store, "_write_config", lambda *args: pytest.fail("相同选择不应重复落盘"))
    response = client.put("/existing/emulator", json=payload)
    assert response.status_code == 200
    assert response.json() is True
    assert manager.config_path("existing").read_bytes() == raw
    assert manager.store.load("existing").mtime_ns == before.mtime_ns
    assert manager.store.last_operation.changed_paths == []


def test_stale_device_snapshot_returns_409_and_keeps_concurrent_change(client, manager, emulator_fields):
    """设备在确认期间被其他编辑者修改时，旧快照不能覆盖新值。"""
    payload = _selection_payload(client, emulator_fields)
    current_device = copy.deepcopy(payload["expected_device"])
    current_device["handle"] = "并发修改后的窗口"
    manager.store.replace_subtree("existing", ("script", "device"), payload["expected_device"], current_device,
                                  payload["expected_generation"])
    before = manager.config_path("existing").read_bytes()
    response = client.put("/existing/emulator", json=payload)
    assert response.status_code == 409
    assert manager.config_path("existing").read_bytes() == before
    assert manager.notifications == []


def test_stale_generation_returns_409_without_write(client, manager, emulator_fields):
    """同名配置删除重建后，旧页面保存请求必须被代际校验拒绝。"""
    payload = _selection_payload(client, emulator_fields)
    canonical = manager.store.load("existing").canonical
    manager.store.generation.delete_config("existing")
    manager.store.create_from_template("existing", canonical)
    assert manager.store.load("existing").generation != payload["expected_generation"]
    before = manager.config_path("existing").read_bytes()
    response = client.put("/existing/emulator", json=payload)
    assert response.status_code == 409
    assert manager.config_path("existing").read_bytes() == before
    assert manager.notifications == []


@pytest.mark.parametrize("field", ["emulator", "expected_device", "expected_generation"])
def test_missing_confirmation_field_returns_422(client, manager, emulator_fields, field):
    """确认请求缺少选择或旧快照时在入口拒绝，现有配置保持不变。"""
    payload = _selection_payload(client, emulator_fields)
    del payload[field]
    before = manager.config_path("existing").read_bytes()
    response = client.put("/existing/emulator", json=payload)
    assert response.status_code == 422
    assert manager.config_path("existing").read_bytes() == before
    assert manager.notifications == []


@pytest.mark.parametrize("field", ["serial", "handle", "emulatorinfo_name", "emulatorinfo_path"])
def test_missing_required_emulator_field_returns_422(client, manager, emulator_fields, field):
    """选择缺少连接、窗口、实例名称或路径时不能执行保存。"""
    payload = _selection_payload(client, emulator_fields)
    del payload["emulator"][field]
    before = manager.config_path("existing").read_bytes()
    response = client.put("/existing/emulator", json=payload)
    assert response.status_code == 422
    assert manager.config_path("existing").read_bytes() == before
    assert manager.notifications == []


@pytest.mark.parametrize("field,value", [
    ("emulator", []), ("expected_device", []), ("expected_generation", 123),
], ids=["selection-list", "snapshot-list", "generation-number"])
def test_invalid_confirmation_structure_returns_422(client, manager, emulator_fields, field, value):
    """拒绝错误的请求结构和代际类型，不让配置写入边界处理坏输入。"""
    payload = _selection_payload(client, emulator_fields)
    payload[field] = value
    before = manager.config_path("existing").read_bytes()
    response = client.put("/existing/emulator", json=payload)
    assert response.status_code == 422
    assert manager.config_path("existing").read_bytes() == before
    assert manager.notifications == []


@pytest.mark.parametrize("field,value", [
    ("serial", ""), ("handle", ""), ("emulatorinfo_path", None), ("serial", 123),
], ids=["empty-serial", "empty-handle", "null-path", "numeric-serial"])
def test_invalid_emulator_values_return_422(client, manager, emulator_fields, field, value):
    """空必填值及非字符串字段不能改写现有实例。"""
    payload = _selection_payload(client, emulator_fields)
    payload["emulator"][field] = value
    before = manager.config_path("existing").read_bytes()
    response = client.put("/existing/emulator", json=payload)
    assert response.status_code == 422
    assert manager.config_path("existing").read_bytes() == before
    assert manager.notifications == []


@pytest.mark.parametrize("method", ["get", "put"])
def test_missing_instance_returns_404(client, manager, emulator_fields, method):
    """不存在的实例不能读取或绑定，也不能隐式创建新配置。"""
    payload = _selection_payload(client, emulator_fields)
    response = client.get("/missing/emulator") if method == "get" else client.put("/missing/emulator", json=payload)
    assert response.status_code == 404
    assert not manager.config_path("missing").exists()
    assert manager.notifications == []


@pytest.mark.parametrize("method", ["get", "put"])
def test_lock_timeout_returns_503_without_write(client, manager, emulator_fields, monkeypatch, method):
    """读取或写入锁超时统一上报可重试状态，原配置不变。"""
    payload = _selection_payload(client, emulator_fields)
    before = manager.config_path("existing").read_bytes()

    def timeout(*args, **kwargs):
        raise Timeout("隔离测试锁")

    monkeypatch.setattr(manager.store, "load" if method == "get" else "replace_subtree", timeout)
    response = client.get("/existing/emulator") if method == "get" else client.put("/existing/emulator", json=payload)
    assert response.status_code == 503
    assert manager.config_path("existing").read_bytes() == before
    assert manager.notifications == []


def test_atomic_write_failure_returns_500_and_does_not_notify(client, manager, emulator_fields, monkeypatch):
    """底层写入失败保留原字节，不向运行实例广播未成功的设备选择。"""
    payload = _selection_payload(client, emulator_fields)
    before = manager.config_path("existing").read_bytes()

    def failed_write(*args, **kwargs):
        raise OSError("隔离测试写入失败")

    monkeypatch.setattr(manager.store, "_write_config", failed_write)
    response = client.put("/existing/emulator", json=payload)
    assert response.status_code == 500
    assert manager.config_path("existing").read_bytes() == before
    assert manager.notifications == []


def test_scan_route_is_read_only(client, manager, monkeypatch, emulator_fields):
    """扫描仅返回候选窗口，不更改当前实例设备设置。"""
    from module.server import script_router

    scan_result = {"devices": [dict(emulator_fields, id="test-window", display_name="小号陪刷")], "warnings": ["测试提示"]}
    monkeypatch.setattr(script_router, "scan_emulators", lambda: scan_result)
    before = manager.config_path("existing").read_bytes()
    response = client.get("/emulators")
    assert response.status_code == 200
    assert response.json() == scan_result
    assert manager.config_path("existing").read_bytes() == before
    assert manager.notifications == []
