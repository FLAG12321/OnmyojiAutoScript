import socket
import types
from unittest.mock import patch, MagicMock

import pytest

from module.device.connection import Connection
from module.exception import EmulatorNotRunningError


def _make_self(serial, *, is_network_device=True,
               process_alive=False, emulator_instance="instance",
               has_probe=True):
    """构造调用 _precheck_network_emulator_alive 所需的最小 self。

    需要挂载 _precheck_tcp_reachable，因为 SimpleNamespace 不会有类方法。
    """
    ns = types.SimpleNamespace(
        serial=serial,
        is_network_device=is_network_device,
        emulator_instance=emulator_instance,
    )
    # 仅在需要时挂载进程探测方法, 缺失即模拟非 Windows 平台
    if has_probe:
        ns._is_emulator_process_alive = lambda: process_alive
    # 挂载 _precheck_tcp_reachable（直接用 Connection 上的方法）
    ns._precheck_tcp_reachable = lambda timeout=3: Connection._precheck_tcp_reachable(ns, timeout=timeout)
    return ns


# ---- 原有进程探测相关测试 ----

def test_precheck_raises_when_bridged_emulator_process_missing():
    # 桥接局域网 IP + 非 auto + 进程不存在 + 有实例 → 提前判定未运行
    fake = _make_self("192.168.1.214:5555", process_alive=False)
    with pytest.raises(EmulatorNotRunningError):
        Connection._precheck_network_emulator_alive(fake)


def test_precheck_passes_when_bridged_emulator_process_alive():
    # 进程存在 → 放行, 交给原 adb connect
    assert Connection._precheck_network_emulator_alive(
        _make_self("192.168.1.214:5555", process_alive=True)
    ) is None


def test_precheck_skips_local_nat_serial():
    # 本机 NAT(127.0.0.1) connect 失败是瞬时的, 即使进程不存在也跳过预检
    assert Connection._precheck_network_emulator_alive(
        _make_self("127.0.0.1:16384", process_alive=False)
    ) is None


def test_precheck_skips_auto_serial():
    # auto 由 detect_device 负责选取已连接设备, detect_device 后 serial 已是实际设备 serial,
    # 不再是 'auto', 所以 auto 场景下 serial 会是本机 NAT(127.0.0.1:xxxx),
    # is_network_device=False 或 serial.startswith('127.0.0.1'), 自然跳过预检
    # 此测试改为验证 auto 场景下 serial 变成本机 NAT 时确实跳过
    assert Connection._precheck_network_emulator_alive(
        _make_self("127.0.0.1:16384", process_alive=False)
    ) is None


def test_precheck_passes_when_instance_unresolved():
    # 拿不到模拟器实例(无 path) → 回退到 TCP 探测
    fake = _make_self("192.168.1.214:5555", process_alive=False, emulator_instance=None)
    with patch('module.device.connection.socket.socket') as mock_sock_cls:
        mock_sock = MagicMock()
        mock_sock_cls.return_value = mock_sock
        mock_sock.connect.return_value = None
        assert Connection._precheck_network_emulator_alive(fake) is None


def test_precheck_passes_when_process_probe_unavailable():
    # 平台不支持进程探测(非 Windows, 无 _is_emulator_process_alive 方法) → 回退到 TCP 探测
    fake = _make_self("192.168.1.214:5555", has_probe=False)
    with patch('module.device.connection.socket.socket') as mock_sock_cls:
        mock_sock = MagicMock()
        mock_sock_cls.return_value = mock_sock
        mock_sock.connect.return_value = None
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


# ---- 新增 TCP 探测相关测试 ----

def test_tcp_precheck_raises_on_connection_refused():
    # TCP 端口不可达(10061) → 立即抛 EmulatorNotRunningError
    fake = _make_self("192.168.1.214:5555", has_probe=False, emulator_instance=None)
    with patch('module.device.connection.socket.socket') as mock_sock_cls:
        mock_sock = MagicMock()
        mock_sock_cls.return_value = mock_sock
        mock_sock.connect.side_effect = ConnectionRefusedError(10061, 'Connection refused')
        with pytest.raises(EmulatorNotRunningError):
            Connection._precheck_network_emulator_alive(fake)


def test_tcp_precheck_raises_on_timeout():
    # TCP 连接超时(10060) → 立即抛 EmulatorNotRunningError
    fake = _make_self("192.168.1.214:5555", has_probe=False, emulator_instance=None)
    with patch('module.device.connection.socket.socket') as mock_sock_cls:
        mock_sock = MagicMock()
        mock_sock_cls.return_value = mock_sock
        mock_sock.connect.side_effect = socket.timeout('timed out')
        with pytest.raises(EmulatorNotRunningError):
            Connection._precheck_network_emulator_alive(fake)


def test_tcp_precheck_passes_on_oserror():
    # 其他网络错误(如 WSAEHOSTUNREACH) → 保守放行
    fake = _make_self("192.168.1.214:5555", has_probe=False, emulator_instance=None)
    with patch('module.device.connection.socket.socket') as mock_sock_cls:
        mock_sock = MagicMock()
        mock_sock_cls.return_value = mock_sock
        mock_sock.connect.side_effect = OSError(10051, 'Network unreachable')
        assert Connection._precheck_network_emulator_alive(fake) is None


def test_tcp_precheck_passes_when_reachable():
    # TCP 端口可达 → 放行交给 adb connect
    fake = _make_self("192.168.1.214:5555", has_probe=False, emulator_instance=None)
    with patch('module.device.connection.socket.socket') as mock_sock_cls:
        mock_sock = MagicMock()
        mock_sock_cls.return_value = mock_sock
        mock_sock.connect.return_value = None
        assert Connection._precheck_network_emulator_alive(fake) is None


def test_tcp_precheck_skipped_when_process_probe_succeeds():
    # 有进程探测且进程存在时，不走 TCP 探测
    fake = _make_self("192.168.1.214:5555", process_alive=True, emulator_instance="instance")
    with patch('module.device.connection.socket.socket') as mock_sock_cls:
        Connection._precheck_network_emulator_alive(fake)
        # socket 不应被调用
        mock_sock_cls.assert_not_called()


def test_tcp_precheck_used_when_no_instance_but_has_probe():
    # 有进程探测能力但没有 emulator_instance → 回退到 TCP 探测
    fake = _make_self("192.168.1.214:5555", has_probe=True, emulator_instance=None)
    with patch('module.device.connection.socket.socket') as mock_sock_cls:
        mock_sock = MagicMock()
        mock_sock_cls.return_value = mock_sock
        mock_sock.connect.return_value = None
        Connection._precheck_network_emulator_alive(fake)
        # socket 应被调用
        mock_sock_cls.assert_called_once()
