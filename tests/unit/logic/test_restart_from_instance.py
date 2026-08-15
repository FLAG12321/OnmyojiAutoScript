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


class FakeMainManager:
    """伪 manager：提供 restart_script_process 身份协议（stop→start），并记录 manager 级调用。"""

    def __init__(self, process):
        self.script_process = {'oas': process}
        self.manager_calls = []

    async def restart_script_process(self, script_name):
        # 与真实 MainManager.restart_script_process 的语义一致：先 stop 再 start。
        self.manager_calls.append('restart_script_process')
        process = self.script_process[script_name]
        await process.stop()
        await process.start()


def test_restart_from_instance_stops_then_starts_existing_process(monkeypatch):
    async def run_case():
        process = FakeScriptProcess()
        fake_mm = FakeMainManager(process)
        monkeypatch.setattr(script_router, 'mm', fake_mm)

        result = await script_router.script_restart_from_instance('oas')
        await asyncio.sleep(0)

        assert result == {'restarting': True}
        # 重启必须经由 manager 协议方法完成，且进程调用顺序为 stop→start。
        assert fake_mm.manager_calls == ['restart_script_process']
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
