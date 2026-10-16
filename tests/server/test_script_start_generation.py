"""启动方案代际回归：临时配置与假进程覆盖同名重建，不接触真实任务。"""
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from module.config.config_model import ConfigModel
from module.config.config_store import ConfigStore
from module.server.main_manager import MainManager


pytestmark = pytest.mark.unit


@pytest.fixture
def start_client(tmp_path, monkeypatch):
    """仅挂载路由，进程启动由替身记录，配置全部来自默认模型。"""
    from module.server import main_manager, script_router

    raw = ConfigModel().model_dump(mode="json")
    store = ConfigStore(tmp_path / "config")
    store.create_from_template("existing", raw)
    manager = MainManager(store=store)
    started = []

    class FakeProcess:
        # 保留 wrapper 身份字段，只记录启动事件，不创建系统进程。
        def __init__(self, name, store, generation):
            self.name = name
            self.generation = generation

        async def start(self):
            started.append((self.name, self.generation))
            return True

        async def stop(self):
            pass

    monkeypatch.setattr(main_manager, "ScriptProcess", FakeProcess)
    monkeypatch.setattr(script_router, "mm", manager)
    app = FastAPI()
    app.include_router(script_router.script_app)
    with TestClient(app) as client:
        yield client, manager, started, raw


@pytest.mark.parametrize("replaced", [False, True])
def test_start_binds_generation_from_device_snapshot(start_client, replaced):
    """读取后同名重建必须返回冲突，原身份仍存在时才允许启动。"""
    client, manager, started, raw = start_client
    snapshot = client.get("/existing/emulator").json()
    assert snapshot["start_generation_check"] is True
    expected = snapshot["generation"]
    if replaced:
        manager.store.delete_config("existing", expected_generation=expected)
        manager.store.create_from_template("existing", raw)
        assert manager.store.load("existing").generation != expected

    response = client.get("/existing/start", params={"expected_generation": expected})

    assert response.status_code == (409 if replaced else 200)
    assert started == ([] if replaced else [("existing", expected)])
    if replaced:
        # 被拒绝的陈旧方案不影响用户使用新代际正常启动。
        current = manager.store.load("existing").generation
        response = client.get("/existing/start", params={"expected_generation": current})
        assert response.status_code == 200
        assert started == [("existing", current)]


def test_start_without_generation_keeps_single_argument_manager_call(start_client, monkeypatch):
    """旧客户端不传参数时仍调用单参数入口，兼容现有调用者和替身。"""
    client, manager, started, _ = start_client
    calls = []

    async def start(name):
        calls.append(name)
        return True

    monkeypatch.setattr(manager, "start_script_process", start)
    assert client.get("/existing/start").status_code == 200
    assert calls == ["existing"]
    assert started == []


def test_manager_start_without_generation_remains_available(start_client):
    """管理器原调用仍按当前身份启动，不要求调用者预读代际。"""
    _, manager, started, _ = start_client
    current = manager.store.load("existing").generation
    assert asyncio.run(manager.start_script_process("existing")) is True
    assert started == [("existing", current)]
