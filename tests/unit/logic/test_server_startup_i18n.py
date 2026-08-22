import asyncio


def test_on_startup_does_not_sync_i18n(monkeypatch):
    from module.server import app as server_app
    from module.server.i18n import I18n

    calls = []

    async def fake_initialize():
        return None

    def record_sync(*args, **kwargs):
        calls.append((args, kwargs))
        return 0

    monkeypatch.setattr(server_app.mm, 'initialize', fake_initialize)
    monkeypatch.setattr(I18n, 'sync_missing_keys', record_sync)
    monkeypatch.setattr(server_app.app.state, 'script_instances', None, raising=False)

    asyncio.run(server_app.on_startup())

    assert calls == []
