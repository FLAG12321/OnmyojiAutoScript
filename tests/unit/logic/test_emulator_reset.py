import types

from module.device.emulator_reset import FullReset
from module.device.platform2.emulator_windows import Emulator, EmulatorInstance


class FakeHealth:
    def _process_check(self):
        return False, 'process dead'


class FakeDevice:
    def __init__(self):
        self.health = FakeHealth()
        self.emulator_instance = EmulatorInstance(
            serial='127.0.0.1:16384',
            name='MuMuPlayer-12.0-1',
            path='I:/Program Files/Netease/MuMu/nx_main/MuMuNxMain.exe',
        )


class FakeProcess:
    def __init__(self, pid, name, cmdline):
        self.info = {'pid': pid, 'name': name, 'cmdline': cmdline}
        self.pid = pid
        self.killed = False

    def kill(self):
        self.killed = True


def test_full_reset_force_kills_mumu_nx_process_by_instance_index(monkeypatch):
    calls = []
    target = FakeProcess(
        1001,
        'MuMuNxDevice.exe',
        ['I:/Program Files/Netease/MuMu/nx_device/12.0/shell/MuMuNxDevice.exe', '-v', '0'],
    )

    class MumuNxDevice(FakeDevice):
        def __init__(self):
            self.health = types.SimpleNamespace(_process_check=lambda: (True, 'process alive'))
            self.emulator_instance = EmulatorInstance(
                serial='127.0.0.1:16384',
                name='MuMuPlayer-12.0-0',
                path='I:/Program Files/Netease/MuMu/nx_main/MuMuNxMain.exe',
            )

    monkeypatch.setattr(
        'module.device.emulator_reset.psutil.process_iter',
        lambda attrs: [] if target.killed else [target],
    )
    monkeypatch.setattr('module.device.emulator_reset.subprocess.run', lambda *args, **kwargs: calls.append(args))

    reset = FullReset(MumuNxDevice())

    assert reset._teardown_layer1_process() is True
    assert target.killed is True
    assert calls == []


def test_full_reset_does_not_kill_non_mumu_by_exe_name_only(monkeypatch):
    target = FakeProcess(
        2001,
        'HD-Player.exe',
        ['C:/Program Files/BlueStacks_nxt/HD-Player.exe'],
    )

    class BlueStacksDevice:
        def __init__(self):
            self.health = types.SimpleNamespace(_process_check=lambda: (True, 'process alive'))
            self.emulator_instance = EmulatorInstance(
                serial='127.0.0.1:5555',
                name='Pie64',
                path='C:/Program Files/BlueStacks_nxt/HD-Player.exe',
            )

    monkeypatch.setattr('module.device.emulator_reset.subprocess.run', lambda *args, **kwargs: None)
    monkeypatch.setattr('module.device.emulator_reset.psutil.process_iter', lambda attrs: [target])

    reset = FullReset(BlueStacksDevice())

    assert reset._teardown_layer1_process() is False
    assert target.killed is False


def test_full_reset_process_alive_returns_false_when_health_check_raises(monkeypatch):
    class BrokenHealthDevice(FakeDevice):
        def __init__(self):
            super().__init__()
            self.health = types.SimpleNamespace(_process_check=lambda: (_ for _ in ()).throw(RuntimeError('boom')))

    monkeypatch.setattr('module.device.emulator_reset.psutil.process_iter', lambda attrs: [])

    reset = FullReset(BrokenHealthDevice())

    assert reset._process_alive() is False


def test_full_reset_mumu_comment_match_is_exact(monkeypatch):
    target = FakeProcess(
        5001,
        'MuMuVMMHeadless.exe',
        ['MuMuVMMHeadless.exe', '--comment', 'MuMuPlayer-12.0-1'],
    )
    neighbor = FakeProcess(
        5002,
        'MuMuVMMHeadless.exe',
        ['MuMuVMMHeadless.exe', '--comment', 'MuMuPlayer-12.0-10'],
    )
    processes = [target, neighbor]

    monkeypatch.setattr('module.device.emulator_reset.subprocess.run', lambda *args, **kwargs: None)
    monkeypatch.setattr(
        'module.device.emulator_reset.psutil.process_iter',
        lambda attrs: [proc for proc in processes if not proc.killed],
    )

    reset = FullReset(FakeDevice())

    assert reset._teardown_layer1_process() is True
    assert target.killed is True
    assert neighbor.killed is False


def test_full_reset_memu_uses_official_listvms_pid(monkeypatch):
    subprocess_calls = []
    current = FakeProcess(5001, 'MEmu.exe', ['C:/Program Files/Microvirt/MEmu/MEmu.exe'])
    other = FakeProcess(5002, 'MEmu.exe', ['C:/Program Files/Microvirt/MEmu/MEmu.exe'])
    processes = [current, other]

    class MEmuDevice:
        def __init__(self):
            self.health = types.SimpleNamespace(_process_check=lambda: (True, 'process alive'))
            self.emulator_instance = EmulatorInstance(
                serial='127.0.0.1:21513',
                name='MEmu_1',
                path='C:/Program Files/Microvirt/MEmu/MEmu.exe',
            )

    def fake_run(cmd, capture_output, text=False, timeout=None, shell=False):
        subprocess_calls.append({
            'cmd': cmd,
            'capture_output': capture_output,
            'text': text,
            'timeout': timeout,
            'shell': shell,
        })
        return types.SimpleNamespace(
            returncode=0,
            stdout=(
                '0,MEmu,10001,stopped,0\n'
                '1,MEmu_1,10002,running,5001\n'
                '2,MEmu_2,10003,running,5002\n'
            ),
        )

    def fake_process(pid):
        for proc in processes:
            if proc.pid == pid:
                return proc
        raise Exception(f'unexpected pid {pid}')

    monkeypatch.setattr('module.device.emulator_reset.subprocess.run', fake_run)
    monkeypatch.setattr('module.device.emulator_reset.psutil.Process', fake_process)
    monkeypatch.setattr(
        'module.device.emulator_reset.psutil.process_iter',
        lambda attrs: [proc for proc in processes if not proc.killed],
    )

    reset = FullReset(MEmuDevice())

    assert reset._teardown_layer1_process() is True
    assert current.killed is True
    assert other.killed is False
    assert subprocess_calls[0]['cmd'][0].endswith('memuc.exe')
    assert subprocess_calls[0]['cmd'][1] == 'listvms'


def test_full_reset_nox_uses_official_console_list_pid(monkeypatch):
    subprocess_calls = []
    current = FakeProcess(6001, 'NoxVMHandle.exe', ['D:/Nox/bin/NoxVMHandle.exe'])
    other = FakeProcess(6002, 'NoxVMHandle.exe', ['D:/Nox/bin/NoxVMHandle.exe'])
    processes = [current, other]

    class NoxDevice:
        def __init__(self):
            self.health = types.SimpleNamespace(_process_check=lambda: (True, 'process alive'))
            self.emulator_instance = EmulatorInstance(
                serial='127.0.0.1:62027',
                name='Nox_2',
                path='D:/Nox/bin/Nox.exe',
            )

    def fake_run(cmd, capture_output, text=False, timeout=None, shell=False):
        subprocess_calls.append({
            'cmd': cmd,
            'capture_output': capture_output,
            'text': text,
            'timeout': timeout,
            'shell': shell,
        })
        return types.SimpleNamespace(
            returncode=0,
            stdout=(
                'nox,NoxPlayer,2032678,1704928,3567547,7456\n'
                'Nox_2,NoxPlayer2,852422,590830,36566,6001\n'
                'Nox_3,NoxPlayer3,852423,590831,36567,6002\n'
            ),
        )

    def fake_process(pid):
        for proc in processes:
            if proc.pid == pid:
                return proc
        raise Exception(f'unexpected pid {pid}')

    monkeypatch.setattr('module.device.emulator_reset.subprocess.run', fake_run)
    monkeypatch.setattr('module.device.emulator_reset.psutil.Process', fake_process)
    monkeypatch.setattr(
        'module.device.emulator_reset.psutil.process_iter',
        lambda attrs: [proc for proc in processes if not proc.killed],
    )

    reset = FullReset(NoxDevice())

    assert reset._teardown_layer1_process() is True
    assert current.killed is True
    assert other.killed is False
    assert subprocess_calls[0]['cmd'][0].endswith('NoxConsole.exe')
    assert 'list' in subprocess_calls[0]['cmd']


def test_full_reset_nox_list_parses_name_with_comma(monkeypatch):
    subprocess_calls = []
    current = FakeProcess(6001, 'NoxVMHandle.exe', ['D:/Nox/bin/NoxVMHandle.exe'])

    class NoxDevice:
        def __init__(self):
            self.health = types.SimpleNamespace(_process_check=lambda: (True, 'process alive'))
            self.emulator_instance = EmulatorInstance(
                serial='127.0.0.1:62027',
                name='Nox,2',
                path='D:/Nox/bin/Nox.exe',
            )

    def fake_run(cmd, capture_output, text=False, timeout=None, shell=False):
        subprocess_calls.append({
            'cmd': cmd,
            'capture_output': capture_output,
            'text': text,
            'timeout': timeout,
            'shell': shell,
        })
        return types.SimpleNamespace(
            returncode=0,
            stdout='Nox,2,NoxPlayer2,852422,590830,36566,6001\n',
        )

    monkeypatch.setattr('module.device.emulator_reset.subprocess.run', fake_run)
    monkeypatch.setattr('module.device.emulator_reset.psutil.Process', lambda pid: current)
    monkeypatch.setattr(
        'module.device.emulator_reset.psutil.process_iter',
        lambda attrs: [] if current.killed else [current],
    )

    reset = FullReset(NoxDevice())

    assert reset._teardown_layer1_process() is True
    assert current.killed is True
    assert subprocess_calls[0]['cmd'][0].endswith('NoxConsole.exe')
    assert 'list' in subprocess_calls[0]['cmd']


def test_full_reset_nox_skips_official_pid_with_unexpected_process_name(monkeypatch):
    notepad = FakeProcess(7001, 'notepad.exe', ['C:/Windows/System32/notepad.exe'])

    class NoxDevice:
        def __init__(self):
            self.health = types.SimpleNamespace(_process_check=lambda: (True, 'process alive'))
            self.emulator_instance = EmulatorInstance(
                serial='127.0.0.1:62027',
                name='Nox_2',
                path='D:/Nox/bin/Nox.exe',
            )

    def fake_run(cmd, capture_output, text=False, timeout=None, shell=False):
        return types.SimpleNamespace(
            returncode=0,
            stdout='Nox_2,NoxPlayer2,852422,590830,36566,7001\n',
        )

    monkeypatch.setattr('module.device.emulator_reset.subprocess.run', fake_run)
    monkeypatch.setattr('module.device.emulator_reset.psutil.Process', lambda pid: notepad)
    monkeypatch.setattr(
        'module.device.emulator_reset.psutil.process_iter',
        lambda attrs: [notepad],
    )

    reset = FullReset(NoxDevice())

    assert reset._teardown_layer1_process() is False
    assert notepad.killed is False


def test_full_reset_ldplayer_list2_parses_pids_from_line_tail(monkeypatch):
    subprocess_calls = []
    current = FakeProcess(3001, 'dnplayer.exe', ['D:/LDPlayer/dnplayer.exe'])
    vbox = FakeProcess(3002, 'LdVBoxHeadless.exe', ['D:/LDPlayer/LdVBoxHeadless.exe'])
    shifted_pid = FakeProcess(1, 'dnplayer.exe', ['D:/LDPlayer/dnplayer.exe'])
    other = FakeProcess(4001, 'dnplayer.exe', ['D:/LDPlayer/dnplayer.exe'])
    processes = [current, vbox, shifted_pid, other]

    class LDPlayerDevice:
        def __init__(self):
            self.health = types.SimpleNamespace(_process_check=lambda: (True, 'process alive'))
            self.emulator_instance = EmulatorInstance(
                serial='127.0.0.1:5557',
                name='leidian1',
                path='D:/LDPlayer/dnplayer.exe',
            )

    def fake_run(cmd, capture_output, text=False, timeout=None, shell=False):
        subprocess_calls.append({
            'cmd': cmd,
            'capture_output': capture_output,
            'text': text,
            'timeout': timeout,
            'shell': shell,
        })
        return types.SimpleNamespace(
            returncode=0,
            stdout=(
                '0,LDPlayer,2032678,1704928,1,7456,3500\n'
                '1,LD,Player,852422,590830,1,3001,3002\n'
            ),
        )

    def fake_process(pid):
        for proc in processes:
            if proc.pid == pid:
                return proc
        raise Exception(f'unexpected pid {pid}')

    monkeypatch.setattr('module.device.emulator_reset.subprocess.run', fake_run)
    monkeypatch.setattr('module.device.emulator_reset.psutil.Process', fake_process)
    monkeypatch.setattr(
        'module.device.emulator_reset.psutil.process_iter',
        lambda attrs: [proc for proc in processes if not proc.killed],
    )

    reset = FullReset(LDPlayerDevice())

    assert reset._teardown_layer1_process() is True
    assert current.killed is True
    assert vbox.killed is True
    assert shifted_pid.killed is False
    assert other.killed is False
    assert subprocess_calls[0]['cmd'][1] == 'list2'
    assert subprocess_calls[0]['cmd'][0].endswith('ldconsole.exe') or subprocess_calls[0]['cmd'][0].endswith('dnconsole.exe')
    assert subprocess_calls[0]['text'] is True
    assert subprocess_calls[0]['shell'] is False
