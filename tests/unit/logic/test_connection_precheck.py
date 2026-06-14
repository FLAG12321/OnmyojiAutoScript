import types

import pytest

from module.device.connection import Connection
from module.exception import EmulatorNotRunningError


def _make_self(serial, *, config_serial=None, is_network_device=True,
               process_alive=False, emulator_instance="instance",
               has_probe=True):
    """构造调用 _precheck_network_emulator_alive 所需的最小 self。"""
    if config_serial is None:
        config_serial = serial
    ns = types.SimpleNamespace(
        serial=serial,
        is_network_device=is_network_device,
        emulator_instance=emulator_instance,
        config=types.SimpleNamespace(
            script=types.SimpleNamespace(
                device=types.SimpleNamespace(serial=config_serial)
            )
        ),
    )
    # 仅在需要时挂载进程探测方法, 缺失即模拟非 Windows 平台
    if has_probe:
        ns._is_emulator_process_alive = lambda: process_alive
    return ns


def test_precheck_raises_when_bridged_emulator_process_missing():
    # 桥接局域网 IP + 非 auto + 进程不存在 + 有实例 → 提前判定未运行
    fake = _make_self("192.168.1.214:5555", process_alive=False)
    with pytest.raises(EmulatorNotRunningError):
        Connection._precheck_network_emulator_alive(fake)


def test_precheck_passes_when_bridged_emulator_process_alive():
    # 进程存在 → 放行, 交给原 adb connect
    fake = _make_self("192.168.1.214:5555", process_alive=True)
    assert Connection._precheck_network_emulator_alive(fake) is None


def test_precheck_skips_local_nat_serial():
    # 本机 NAT(127.0.0.1) connect 失败是瞬时的, 即使进程不存在也跳过预检
    fake = _make_self("127.0.0.1:16384", process_alive=False)
    assert Connection._precheck_network_emulator_alive(fake) is None


def test_precheck_skips_auto_serial():
    # auto 由 detect_device 负责选取已连接设备, 跳过预检
    fake = _make_self("192.168.1.214:5555", config_serial="auto", process_alive=False)
    assert Connection._precheck_network_emulator_alive(fake) is None


def test_precheck_passes_when_instance_unresolved():
    # 拿不到模拟器实例(无 path) → 放行, 避免误判纯远程设备
    fake = _make_self("192.168.1.214:5555", process_alive=False, emulator_instance=None)
    assert Connection._precheck_network_emulator_alive(fake) is None


def test_precheck_passes_when_process_probe_unavailable():
    # 平台不支持进程探测(非 Windows, 无 _is_emulator_process_alive 方法) → 放行
    fake = _make_self("192.168.1.214:5555", has_probe=False)
    assert Connection._precheck_network_emulator_alive(fake) is None


def test_adb_connect_treats_connection_reset_as_emulator_not_running():
    # 桥接 ADB 可能直接抛 10054, 应进入恢复流程而不是冒泡为未处理异常。
    fake = types.SimpleNamespace(
        config=types.SimpleNamespace(DEVICE_OVER_HTTP=False),
        adb_client=types.SimpleNamespace(
            connect=lambda serial: (_ for _ in ()).throw(
                ConnectionResetError(10054, '远程主机强迫关闭了一个现有的连接。')
            )
        ),
        list_device=lambda: [],
    )

    with pytest.raises(EmulatorNotRunningError):
        Connection.adb_connect(fake, "192.168.1.211:5555")
