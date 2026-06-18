import asyncio
import types

from module.server import script_router


class FakeScriptProcess:
    """伪脚本进程：只记录 stop/start 调用顺序，避免单元测试启动真实子进程。"""

    def __init__(self):
        self.calls = []

    async def stop(self):
        self.calls.append('stop')

    async def start(self):
        self.calls.append('start')


def test_restart_from_instance_stops_then_starts_existing_process(monkeypatch):
    async def run_case():
        process = FakeScriptProcess()
        fake_mm = types.SimpleNamespace(script_process={'oas': process})
        monkeypatch.setattr(script_router, 'mm', fake_mm)

        result = await script_router.script_restart_from_instance('oas')
        await asyncio.sleep(0)

        assert result == {'restarting': True}
        assert process.calls == ['stop', 'start']

    asyncio.run(run_case())


def test_restart_from_instance_returns_false_when_process_missing(monkeypatch):
    async def run_case():
        fake_mm = types.SimpleNamespace(script_process={})
        monkeypatch.setattr(script_router, 'mm', fake_mm)

        result = await script_router.script_restart_from_instance('missing')
        await asyncio.sleep(0)

        assert result == {'restarting': False}

    asyncio.run(run_case())
