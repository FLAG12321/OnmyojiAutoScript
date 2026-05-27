import types

from module.device.emulator_reset import FullReset
from module.device.platform2.emulator_windows import Emulator


class FakeHealth:
    def _process_check(self):
        return False, 'process dead'


class FakeDevice:
    def __init__(self):
        self.health = FakeHealth()
        self.emulator_instance = types.SimpleNamespace(
            MuMuPlayer12_id=1,
            emulator=types.SimpleNamespace(path='I:/Program Files/Netease/MuMu/nx_main/MuMuNxMain.exe'),
        )


def test_full_reset_graceful_uses_existing_emulator_import(monkeypatch):
    calls = []

    def fake_run(cmd, capture_output, timeout, shell):
        calls.append((cmd, capture_output, timeout, shell))
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr('module.device.emulator_reset.subprocess.run', fake_run)
    monkeypatch.setattr(Emulator, 'single_to_console', staticmethod(lambda exe: 'MuMuManager.exe'))

    reset = FullReset(FakeDevice())

    assert reset._teardown_layer1_process_graceful() is True
    assert calls == [('"MuMuManager.exe" api -v 1 shutdown_player', True, 10, True)]


def test_full_reset_force_stop_uses_existing_emulator_import(monkeypatch):
    calls = []

    def fake_run(cmd, capture_output, timeout, shell):
        calls.append((cmd, capture_output, timeout, shell))
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr('module.device.emulator_reset.subprocess.run', fake_run)
    monkeypatch.setattr(Emulator, 'single_to_console', staticmethod(lambda exe: 'MuMuManager.exe'))

    reset = FullReset(FakeDevice())

    assert reset._teardown_layer1_process_force_stop() is True
    assert calls == [('"MuMuManager.exe" control -v 1 force_stop', True, 10, True)]
