from module.device.emulator_health import EmulatorHealth


class FakeDevice:
    def __init__(self, mumu_state=None):
        self.config = None
        self.mumu_state = mumu_state

    def _query_mumu12_state(self):
        return self.mumu_state


def test_health_allows_unavailable_mumu_state_when_other_checks_pass(monkeypatch):
    health = EmulatorHealth(FakeDevice())

    monkeypatch.setattr(health, '_process_check', lambda: (True, 'process ok'))
    monkeypatch.setattr(health, '_adb_check', lambda: (True, 'adb ok'))
    monkeypatch.setattr(health, '_screenshot_channel_check', lambda: (True, 'channel ok'))

    assert health.is_alive() is True
    assert health.why_dead() == 'alive (no failures recorded)'


def test_health_still_fails_other_checks_when_mumu_state_unavailable(monkeypatch):
    health = EmulatorHealth(FakeDevice())

    monkeypatch.setattr(health, '_process_check', lambda: (False, 'process dead'))
    monkeypatch.setattr(health, '_adb_check', lambda: (True, 'adb ok'))
    monkeypatch.setattr(health, '_screenshot_channel_check', lambda: (True, 'channel ok'))

    assert health.is_alive() is False
    assert health.why_dead() == 'process=process dead'


def test_health_fails_when_mumu_state_is_not_start_finished(monkeypatch):
    health = EmulatorHealth(FakeDevice({'player_state': 'starting'}))

    monkeypatch.setattr(health, '_process_check', lambda: (True, 'process ok'))
    monkeypatch.setattr(health, '_adb_check', lambda: (True, 'adb ok'))
    monkeypatch.setattr(health, '_screenshot_channel_check', lambda: (True, 'channel ok'))

    assert health.is_alive() is False
    assert health.why_dead() == "state=player_state='starting'"


def test_non_mumu_process_check_uses_generic_process_probe():
    class NonMumuDevice(FakeDevice):
        def __init__(self):
            super().__init__()
            self.emulator_instance = object()
            self.generic_probe_calls = 0

        def _query_mumu12_state(self):
            return None

        def _is_emulator_process_alive(self):
            self.generic_probe_calls += 1
            return True

    device = NonMumuDevice()
    health = EmulatorHealth(device)

    assert health._process_check() == (True, 'generic emulator process alive')
    assert device.generic_probe_calls == 1
