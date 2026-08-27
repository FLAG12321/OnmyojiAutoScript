# -*- coding: utf-8 -*-
"""minitouch 拟人化接入测试（Plan Task 16）。

覆盖四块：
1. B0/B1 按事件状态分支（契约 #11）：`_run_humanized_minitouch` 在首个原生传输
   开始前失败只重放一次无装饰 legacy；写入已开始但结果未知（含 DOWN 的同包批次）
   必须先完成可验证的 session reset + 新连接 ready，才允许一次 legacy 重放。
2. TCP 恢复的严格三行 fullmatch 握手（v / ^ / $ <pid>）且 PID 改变。
3. HTTP 恢复的 stop/not-running、start/running、新 WS 两条 ready 五阶段证据，
   全部基于 time.monotonic() deadline，二元 timeout 与 retry=False。
4. Control 开档直通 humanized impl（绝不经公开 @retry）、off 保持原分派，
   以及 humanized 滑动/点击的命令形状与整数毫秒量化。

测试里 `write:('DOWN', 'COMMIT')` 是"已开始写入且远端结果未知"的真实事件注入点，
不是 `cleanup=True`。所有 B1 用例都断言没有 decorator retry。
"""
import asyncio
import io
import requests
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
import websockets

from module.device.control import Control
from module.device.humanize.plan import MovePlan, TailPlan
from module.device.method import minitouch as minitouch_module
from module.device.method import utils as method_utils
from module.device.method.minitouch import Minitouch, _MinitouchRecoveryFailed
from module.device.method.minitouch import MinitouchOccupiedError
from module.exception import RequestHumanTakeover

pytestmark = pytest.mark.unit


class _Builder:
    def __init__(self, events):
        self.events = events
        self.commands = []
        self.delay = 0

    def down(self):
        self.commands.append('DOWN')
        return self

    def move(self):
        self.commands.append('MOVE')
        return self

    def commit(self):
        self.commands.append('COMMIT')
        return self

    def clear(self):
        self.events.append('builder.clear')
        self.commands.clear()
        self.delay = 0


class _B1Harness:
    def __init__(self, fail_send_number, recovery_ready, transport_error=ConnectionResetError):
        self.events = []
        self.builder = _Builder(self.events)
        self.fail_send_number = fail_send_number
        self.recovery_ready = recovery_ready
        self.transport_error = transport_error
        self.send_number = 0

    @property
    def minitouch_builder(self):
        return self.builder

    def minitouch_send(self):
        self.send_number += 1
        payload = tuple(self.builder.commands)
        self.events.append(f'write:{payload}')
        if self.send_number == self.fail_send_number:
            raise self.transport_error('传输结果未知')
        self.builder.clear()

    def recover_humanized_minitouch_b1(self):
        self.builder.clear()
        self.events.append('reset-attempt')
        if not self.recovery_ready:
            raise RequestHumanTakeover
        self.events.append('session-ready')
        self.builder = _Builder(self.events)


def test_humanized_minitouch_cache_miss_uses_bounded_prepare():
    """首次开档初始化失败时应立即接管，不进入旧的无界 minitouch_init。"""
    device = object.__new__(Minitouch)
    device.config = SimpleNamespace(DEVICE_OVER_HTTP=True)
    device.serial = 'http://127.0.0.1:7912'
    device.__dict__['orientation'] = 2
    device.minitouch_init = Mock(side_effect=AssertionError('不得调用 legacy 初始化'))
    recovery = Mock(side_effect=RequestHumanTakeover)
    device._recover_humanized_minitouch_http = recovery

    with pytest.raises(RequestHumanTakeover):
        Minitouch._prepare_humanized_minitouch_builder(device)

    recovery.assert_called_once()
    assert device.orientation == 2
    device.minitouch_init.assert_not_called()


def test_humanized_minitouch_tcp_cache_miss_uses_bounded_prepare():
    """首次 TCP 开档初始化失败时不进入旧的 adb_forward/无界握手。"""
    device = object.__new__(Minitouch)
    device.config = SimpleNamespace(DEVICE_OVER_HTTP=False)
    device.__dict__['orientation'] = 3
    device.adb_forward = Mock(side_effect=AssertionError('不得进入 legacy adb_forward'))
    device.minitouch_init = Mock(side_effect=AssertionError('不得调用 legacy 初始化'))
    recovery = Mock(side_effect=OSError('tcp prepare timeout'))
    device._recover_humanized_minitouch_tcp = recovery

    with pytest.raises(RequestHumanTakeover):
        Minitouch._prepare_humanized_minitouch_builder(device)

    recovery.assert_called_once()
    assert recovery.call_args.kwargs['restart_atx'] is False
    assert device.orientation == 3
    device.minitouch_init.assert_not_called()


def test_humanized_orientation_tcp_query_is_single_bounded_call(monkeypatch):
    """TCP 方向查询不得调用带 @retry 的 get_orientation。"""
    device = object.__new__(Minitouch)
    device.config = SimpleNamespace(DEVICE_OVER_HTTP=False)
    device.serial = 'emulator-5554'
    device.adb_binary = 'adb'
    device.get_orientation = Mock(side_effect=AssertionError('不得调用 retry orientation'))
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(
            stdout='DisplayViewport{valid=true, orientation=2, deviceWidth=1280, deviceHeight=720}'
        )

    monkeypatch.setattr(minitouch_module.subprocess, 'run', run)
    device._prepare_humanized_orientation(minitouch_module.time.monotonic() + 1.0)

    assert device.orientation == 2
    assert calls[0][0][-3:] == ['shell', 'dumpsys', 'display']
    assert calls[0][1]['timeout'] > 0
    device.get_orientation.assert_not_called()


def test_humanized_orientation_http_query_is_single_rpc(monkeypatch):
    """HTTP 方向查询只走项目单次 JSON-RPC，不触发 uiautomator2 cached property。"""
    device = object.__new__(Minitouch)
    device.config = SimpleNamespace(DEVICE_OVER_HTTP=True)
    device.serial = 'https://127.0.0.1:7912'
    device.get_orientation = Mock(side_effect=AssertionError('不得调用 retry orientation'))
    seen = []

    def request(url, method, **kwargs):
        seen.append((url, method, kwargs))
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                'jsonrpc': '2.0', 'id': 'humanized-orientation',
                'result': {'displayRotation': 1},
            },
        )

    monkeypatch.setattr(minitouch_module, 'humanized_http_request', request)
    device._prepare_humanized_orientation(minitouch_module.time.monotonic() + 1.0)

    assert device.orientation == 1
    assert seen[0][0] == 'https://127.0.0.1:7912/jsonrpc/0'
    assert seen[0][1] == 'post'
    device.get_orientation.assert_not_called()


@pytest.mark.parametrize('rotation', [True, False, 1.0, '1'])
def test_humanized_orientation_http_rejects_non_integer_rotation(monkeypatch, rotation):
    """方向协议只接受真正的整数，不能把 bool/浮点/字符串静默当作旋转值。"""
    device = object.__new__(Minitouch)
    device.config = SimpleNamespace(DEVICE_OVER_HTTP=True)
    device.serial = 'http://127.0.0.1:7912'

    monkeypatch.setattr(
        minitouch_module,
        'humanized_http_request',
        lambda *args, **kwargs: SimpleNamespace(
            status_code=200,
            json=lambda: {
                'jsonrpc': '2.0', 'id': 'humanized-orientation',
                'result': {'displayRotation': rotation},
            },
        ),
    )

    with pytest.raises(_MinitouchRecoveryFailed, match='invalid rotation'):
        device._prepare_humanized_orientation(minitouch_module.time.monotonic() + 1.0)


def test_http_recovery_preserves_https_websocket_scheme(monkeypatch):
    """HTTPS service 必须使用 WSS 恢复，不能降级为明文 WS。"""
    clock = _Clock()
    device = _HttpRecoveryHarness(clock, failure_stage=None)
    device.serial = 'https://127.0.0.1:7912'
    urls = []

    async def connect(url):
        urls.append(url)
        return _WebSocket(None)

    monkeypatch.setattr(minitouch_module.websockets, 'connect', connect)
    device._minitouch_loop_run(device._recover_humanized_minitouch_http_async())

    assert urls == ['wss://127.0.0.1:7912/minitouch']


def _run_click_with_unknown_down(builder, send):
    builder.down().commit()
    send()


def _run_swipe_with_move_failure(builder, send):
    builder.down().commit()
    send()
    builder.move().commit()
    send()


def test_b1_unknown_down_without_ready_never_replays_legacy():
    device = _B1Harness(fail_send_number=1, recovery_ready=False)
    legacy = Mock(side_effect=lambda: device.events.append('legacy'))

    with pytest.raises(RequestHumanTakeover):
        Minitouch._run_humanized_minitouch(device, _run_click_with_unknown_down, legacy)

    assert device.events == [
        "write:('DOWN', 'COMMIT')",
        'builder.clear',
        'reset-attempt',
    ]
    legacy.assert_not_called()
    assert device.builder.commands == []


def test_b1_move_failure_replays_once_only_after_ready():
    device = _B1Harness(fail_send_number=2, recovery_ready=True)
    legacy = Mock(side_effect=lambda: device.events.append('legacy'))

    Minitouch._run_humanized_minitouch(device, _run_swipe_with_move_failure, legacy)

    assert device.events == [
        "write:('DOWN', 'COMMIT')",
        'builder.clear',
        "write:('MOVE', 'COMMIT')",
        'builder.clear',
        'reset-attempt',
        'session-ready',
        'legacy',
    ]
    legacy.assert_called_once_with()
    assert device.builder.commands == []


def test_b1_websocket_protocol_error_is_recovered_before_legacy():
    device = _B1Harness(
        fail_send_number=1,
        recovery_ready=True,
        transport_error=websockets.WebSocketException,
    )
    legacy = Mock(side_effect=lambda: device.events.append('legacy'))

    Minitouch._run_humanized_minitouch(device, _run_click_with_unknown_down, legacy)

    assert device.events == [
        "write:('DOWN', 'COMMIT')",
        'builder.clear',
        'reset-attempt',
        'session-ready',
        'legacy',
    ]
    legacy.assert_called_once_with()


def test_b1_asyncio_send_timeout_is_recovered_before_legacy():
    """wait_for 的 asyncio.TimeoutError 也必须进入 B1，而不是逸出 runner。"""
    device = _B1Harness(
        fail_send_number=1,
        recovery_ready=True,
        transport_error=asyncio.TimeoutError,
    )
    legacy = Mock(side_effect=lambda: device.events.append('legacy'))

    Minitouch._run_humanized_minitouch(device, _run_click_with_unknown_down, legacy)

    assert device.events == [
        "write:('DOWN', 'COMMIT')",
        'builder.clear',
        'reset-attempt',
        'session-ready',
        'legacy',
    ]
    legacy.assert_called_once_with()


def test_b1_plain_oserror_is_recovered_before_legacy():
    """TCP sendall 的普通 OSError 也属于写入结果未知，必须进入 B1。"""
    device = _B1Harness(
        fail_send_number=1,
        recovery_ready=True,
        transport_error=OSError,
    )
    legacy = Mock(side_effect=lambda: device.events.append('legacy'))

    Minitouch._run_humanized_minitouch(device, _run_click_with_unknown_down, legacy)

    assert device.events == [
        "write:('DOWN', 'COMMIT')",
        'builder.clear',
        'reset-attempt',
        'session-ready',
        'legacy',
    ]
    legacy.assert_called_once_with()


def test_humanized_http_request_rejects_drip_past_absolute_deadline(monkeypatch):
    """每次 socket recv 都有数据也不能续期绝对 deadline。"""
    clock = _Clock()

    class DripSocket:
        def settimeout(self, timeout):
            assert timeout > 0

        def sendall(self, data):
            assert data.startswith(b'GET /status HTTP/1.1')

        def recv(self, size):
            clock.now += 0.06
            return b'H'

        def close(self):
            return None

    monkeypatch.setattr(method_utils.time, 'monotonic', clock.monotonic)
    monkeypatch.setattr(method_utils.socket, 'create_connection',
                        lambda address, timeout: DripSocket())

    with pytest.raises(TimeoutError, match='deadline exceeded'):
        method_utils.humanized_http_request(
            'http://127.0.0.1:7912/status', 'get', deadline=0.1)


def test_humanized_http_request_parses_single_json_response(monkeypatch):
    """专用 socket 通道能解析正常 HTTP JSON 响应。"""
    class ResponseSocket:
        def __init__(self):
            self.sent = b''
            self.chunks = [
                b'HTTP/1.1 200 OK\r\nContent-Length: 17\r\n\r\n',
                b'{"running":false}',
                b'',
            ]

        def settimeout(self, timeout):
            assert timeout > 0

        def sendall(self, data):
            self.sent = data

        def recv(self, size):
            return self.chunks.pop(0)

        def close(self):
            return None

    sock = ResponseSocket()
    monkeypatch.setattr(method_utils.socket, 'create_connection',
                        lambda address, timeout: sock)
    response = method_utils.humanized_http_request(
        'http://127.0.0.1:7912/services/minitouch', 'get',
        deadline=method_utils.time.monotonic() + 10.0)

    assert response.status_code == 200
    assert response.json() == {'running': False}
    assert sock.sent.startswith(b'GET /services/minitouch HTTP/1.1')


def test_minitouch_loop_maps_websocket_protocol_error_to_transport_error():
    class _LoopHarness:
        def __init__(self):
            self._minitouch_loop = asyncio.new_event_loop()

    async def fail():
        raise websockets.WebSocketException('protocol failure')

    device = _LoopHarness()
    device._humanized_minitouch_transport = True
    try:
        with pytest.raises(MinitouchOccupiedError):
            Minitouch._minitouch_loop_run(device, fail())
    finally:
        device._minitouch_loop.close()


def test_minitouch_loop_preserves_websocket_error_for_off_path():
    class _LoopHarness:
        def __init__(self):
            self._minitouch_loop = asyncio.new_event_loop()

    async def fail():
        raise websockets.WebSocketException('off protocol failure')

    device = _LoopHarness()
    try:
        with pytest.raises(websockets.WebSocketException):
            Minitouch._minitouch_loop_run(device, fail())
    finally:
        device._minitouch_loop.close()


def test_minitouch_loop_preserves_legacy_connection_closed_mapping():
    class _LoopHarness:
        def __init__(self):
            self._minitouch_loop = asyncio.new_event_loop()

    async def fail():
        raise websockets.ConnectionClosedError(None, None)

    device = _LoopHarness()
    try:
        with pytest.raises(MinitouchOccupiedError):
            Minitouch._minitouch_loop_run(device, fail())
    finally:
        device._minitouch_loop.close()


def test_tcp_recovery_blocking_restart_hits_wall_clock_deadline(monkeypatch):
    device = _TcpRecoveryDevice('v 1\n^ 2 1280 720 50\n$ 202\n')

    def blocked_run(command, **kwargs):
        # subprocess.run(timeout=...) 会在 deadline 到达时终止 adb 子进程；
        # fake 显式验证 timeout，避免用签名错误制造假通过。
        assert kwargs['timeout'] <= 0.01
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])

    monkeypatch.setattr(minitouch_module.subprocess, 'run', blocked_run)
    monkeypatch.setattr(minitouch_module, 'MINITOUCH_RECOVERY_TCP_CONNECT_TIMEOUT_S', 0.01)
    with pytest.raises(RequestHumanTakeover):
        device.recover_humanized_minitouch_b1()


def test_b0_calls_the_undecorated_legacy_impl_once():
    device = _B1Harness(fail_send_number=99, recovery_ready=False)
    legacy = Mock(side_effect=lambda: device.events.append('legacy'))

    def fail_before_transport(builder, send):
        raise ConnectionResetError('尚未写入设备')

    Minitouch._run_humanized_minitouch(device, fail_before_transport, legacy)

    assert device.events == ['legacy']
    legacy.assert_called_once_with()


@pytest.mark.parametrize(
    'version, capability, pid_line',
    [
        ('x 1', '^ 2 1280 720 50', '$ 202'),
        ('v 1', '^ two 1280 720 50', '$ 202'),
        ('v 1', '^ 2 1280 720 50', '$ 101'),
        ('v 1', '^ 2 1280 720 50', 'pid 202'),
    ],
)
def test_tcp_recovery_rejects_bad_or_same_pid_handshake(version, capability, pid_line):
    with pytest.raises(_MinitouchRecoveryFailed):
        Minitouch._validate_humanized_minitouch_handshake(
            object(), version, capability, pid_line, old_pid='101'
        )


def test_tcp_recovery_accepts_only_a_complete_new_pid_handshake():
    assert Minitouch._validate_humanized_minitouch_handshake(
        object(), 'v 1', '^ 2 1280 720 50', '$ 202', old_pid='101'
    ) == (1280, 720, '202')


def test_tcp_recovery_accepts_zero_max_pressure_handshake():
    """真实设备可能返回压力上限 0，不能因该合法值触发人工接管。"""
    assert Minitouch._validate_humanized_minitouch_handshake(
        object(), 'v 1', '^ 10 720 1280 0', '$ 202', old_pid=None
    ) == (720, 1280, '202')


class _TcpStrictB1Harness(Minitouch):
    def __init__(self, handshake):
        self.events = []
        self.handshake = handshake
        self.config = SimpleNamespace(DEVICE_OVER_HTTP=False)
        self._minitouch_pid = '101'
        self.builder = _Builder(self.events)
        self.__dict__['minitouch_builder'] = self.builder

    def minitouch_send(self):
        self.events.append(f'write:{tuple(self.minitouch_builder.commands)}')
        raise ConnectionResetError('DOWN 写入结果未知')

    def _recover_humanized_minitouch_tcp(self, old_pid, deadline=None):
        self.events.append('reset-attempt')
        return Minitouch._validate_humanized_minitouch_handshake(
            self, *self.handshake, old_pid=old_pid
        )


@pytest.mark.parametrize(
    'handshake',
    [
        ('v 1', '^ 2 1280 720 50', '$ 101'),
        ('v 1', '^ 2 1280 720 50', 'wrong 202'),
    ],
)
def test_b1_bad_tcp_handshake_takes_over_without_legacy(handshake):
    device = _TcpStrictB1Harness(handshake)
    legacy = Mock(side_effect=lambda: device.events.append('legacy'))

    with pytest.raises(RequestHumanTakeover):
        Minitouch._run_humanized_minitouch(device, _run_click_with_unknown_down, legacy)

    assert device.events == [
        "write:('DOWN', 'COMMIT')",
        'builder.clear',
        'reset-attempt',
    ]
    legacy.assert_not_called()
    assert device.builder.commands == []


def test_tcp_recovery_rejects_missing_handshake_line(monkeypatch):
    class _Clock:
        def monotonic(self):
            return 0.0

    class _Client:
        def settimeout(self, timeout):
            assert 0 < timeout <= 1.0

    monkeypatch.setattr(minitouch_module.time, 'monotonic', _Clock().monotonic)
    stream = io.StringIO('v 1\n^ 2 1280 720 50\n')
    device = object()
    client = _Client()

    assert Minitouch._read_humanized_minitouch_line(device, stream, client, 1.0, 'version') == 'v 1'
    assert Minitouch._read_humanized_minitouch_line(device, stream, client, 1.0, 'capability') == '^ 2 1280 720 50'
    with pytest.raises(_MinitouchRecoveryFailed, match='pid handshake line missing'):
        Minitouch._read_humanized_minitouch_line(device, stream, client, 1.0, 'pid')


def test_tcp_handshake_rejects_line_completed_after_global_deadline(monkeypatch):
    """逐字节滴流可让 readline 返回有效文本，但过期行不能成为恢复证据。"""
    clock = _Clock()

    class SlowStream:
        def readline(self):
            clock.now = 1.01
            return 'v 1\n'

    class Client:
        def settimeout(self, timeout):
            assert timeout == pytest.approx(1.0)

    monkeypatch.setattr(minitouch_module.time, 'monotonic', clock.monotonic)

    with pytest.raises(_MinitouchRecoveryFailed, match='deadline exceeded'):
        Minitouch._read_humanized_minitouch_line(
            object(), SlowStream(), Client(), 1.0, 'version')


class _Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class _Response:
    status_code = 200

    def __init__(self, running):
        self.running = running

    def json(self):
        return {'running': self.running}


class _Http:
    def __init__(self, events, failure_stage):
        self.events = events
        self.failure_stage = failure_stage
        self.phase = 'initial'

    def _request(self, method, url, timeout, retry=True):
        # 模拟 TimeoutRequestsSession 对标量 timeout 的真实改写。
        if isinstance(timeout, (int, float)):
            timeout = (3.0, timeout)
        self.events.append((f'http.{method}', timeout, retry))
        if self.failure_stage == 'http-read-timeout':
            if retry:
                self.events.append('prepare-atx-agent')
                return self._request(method, url, timeout, retry=False)
            raise requests.ReadTimeout('service response unknown')
        return timeout

    def delete(self, url, timeout, retry=True):
        self._request('delete', url, timeout, retry)
        self.phase = 'stopped'
        return _Response(True)

    def post(self, url, timeout, retry=True):
        self._request('post', url, timeout, retry)
        self.phase = 'started'
        return _Response(True)

    def get(self, url, timeout, retry=True):
        self._request('get', url, timeout, retry)
        if self.failure_stage == 'stop':
            return _Response(True)
        if self.failure_stage == 'start' and self.phase == 'started':
            return _Response(False)
        return _Response(self.phase == 'started')


class _WebSocket:
    def __init__(self, failure_stage):
        self.messages = ['ready', 'ready']
        if failure_stage == 'ready-1-invalid':
            self.messages[0] = ''
        if failure_stage == 'ready-2-invalid':
            self.messages[1] = ''

    async def recv(self):
        return self.messages.pop(0)

    async def close(self):
        return None


class _HttpRecoveryHarness(Minitouch):
    def __init__(self, clock, failure_stage):
        self.events = []
        self.clock = clock
        self.failure_stage = failure_stage
        self.config = SimpleNamespace(DEVICE_OVER_HTTP=True)
        self.serial = 'http://127.0.0.1:7912'
        self.u2 = SimpleNamespace(http=_Http(self.events, failure_stage), path2url=lambda p: p)
        self.__dict__['minitouch_builder'] = _Builder(self.events)

    def sleep(self, seconds):
        self.clock.sleep(seconds)

    def _minitouch_loop_run(self, coroutine):
        return asyncio.run(coroutine)

    async def _await_humanized_minitouch_recovery(self, stage, awaitable, timeout_s):
        self.events.append((stage, timeout_s))
        if self.failure_stage == stage:
            awaitable.close()
            raise asyncio.TimeoutError
        return await awaitable


@pytest.mark.parametrize(
    'failure_stage',
    ['stop', 'start', 'http-read-timeout', 'connect', 'ready-1', 'ready-2', 'ready-1-invalid', 'ready-2-invalid'],
)
def test_http_recovery_deadline_always_takes_over(monkeypatch, failure_stage):
    clock = _Clock()
    device = _HttpRecoveryHarness(clock, failure_stage)

    async def connect(url):
        return _WebSocket(failure_stage)

    monkeypatch.setattr(minitouch_module.time, 'monotonic', clock.monotonic)
    monkeypatch.setattr(minitouch_module.websockets, 'connect', connect)

    with pytest.raises(RequestHumanTakeover):
        device.recover_humanized_minitouch_b1()

    assert device.minitouch_builder.commands == []
    http_events = [event for event in device.events if isinstance(event, tuple) and event[0].startswith('http.')]
    assert http_events
    assert all(
        isinstance(timeout, tuple)
        and len(timeout) == 2
        and all(0 < value <= 1.0 for value in timeout)
        and retry is False
        for _, timeout, retry in http_events
    )
    assert 'prepare-atx-agent' not in device.events
    if failure_stage in {'stop', 'start'}:
        assert clock.now >= 3.0
    elif failure_stage == 'http-read-timeout':
        assert [name for name, _, _ in http_events] == ['http.delete']
    elif failure_stage.endswith('invalid'):
        ready_stage = failure_stage[:-8]
        assert (ready_stage, 1.0) in device.events
    else:
        assert (failure_stage, 3.0 if failure_stage == 'connect' else 1.0) in device.events


def test_http_recovery_success_does_not_require_tcp_pid(monkeypatch):
    clock = _Clock()
    device = _HttpRecoveryHarness(clock, failure_stage=None)

    async def connect(url):
        return _WebSocket(None)

    monkeypatch.setattr(minitouch_module.time, 'monotonic', clock.monotonic)
    monkeypatch.setattr(minitouch_module.websockets, 'connect', connect)

    device.recover_humanized_minitouch_b1()

    assert device.minitouch_builder.commands == []
    assert any(event == ('ready-1', 1.0) for event in device.events)
    assert any(event == ('ready-2', 1.0) for event in device.events)


def test_http_recovery_slow_service_request_hits_wall_clock_deadline(monkeypatch):
    device = _HttpRecoveryHarness(_Clock(), failure_stage=None)

    def slow_delete(*args, **kwargs):
        # requests 的真实 read timeout 在单次响应不可用时抛出；不得靠后台
        # worker 返回来制造“墙钟超时”假象。
        assert isinstance(kwargs['timeout'], tuple)
        assert kwargs['retry'] is False
        raise requests.ReadTimeout('service response deadline exceeded')

    device.u2.http.delete = slow_delete
    monkeypatch.setattr(minitouch_module, 'MINITOUCH_RECOVERY_STOP_TIMEOUT_S', 0.01)
    with pytest.raises(RequestHumanTakeover):
        device.recover_humanized_minitouch_b1()


@pytest.mark.parametrize(
    'entry, public_name, humanized_name, args',
    [
        ('click', 'click_minitouch', '_click_minitouch_humanized_impl', (100, 200)),
        ('swipe', 'swipe_minitouch', '_swipe_minitouch_humanized_impl', (np.array([100, 100]), np.array([200, 200]))),
    ],
)
def test_control_enabled_dispatch_never_enters_public_retry(monkeypatch, entry, public_name, humanized_name, args):
    device = object.__new__(Control)
    device.config = SimpleNamespace(script=SimpleNamespace(device=SimpleNamespace(control_method='minitouch')))
    device.humanizer = SimpleNamespace(enabled=True)
    device.handle_control_check = lambda name: None
    public_retry = Mock(side_effect=AssertionError('公开 @retry 路径不得进入'))
    humanized_impl = Mock()
    monkeypatch.setattr(device, public_name, public_retry)
    monkeypatch.setattr(device, humanized_name, humanized_impl)

    if entry == 'click':
        Control.click(device, *args)
    else:
        Control.swipe(device, *args, duration=0.1, distance_check=False)

    humanized_impl.assert_called_once()
    public_retry.assert_not_called()


# ---------------------------------------------------------------------------
# 以下为 Task 16 验收补充测试：整数毫秒量化、命令形状、A 类回退、完整 TCP 恢复、
# 以及从 Control 入口注入传输异常断言 B1 处理。
# ---------------------------------------------------------------------------

def test_quantize_move_delays_keeps_total_within_1ms():
    delays = [0.010, 0.020, 0.030]
    out = minitouch_module._quantize_move_delays(delays)
    assert out == [10, 20, 30]
    # 整条量化 wait 总和必须等于 target_total_ms（误差 ≤1ms）
    assert sum(out) == int(sum(delays) * 1000 + 0.5)


def test_quantize_move_delays_zero_delay_emits_no_wait():
    out = minitouch_module._quantize_move_delays([0.0, 0.010, 0.0])
    assert out[0] == 0 and out[2] == 0
    assert out[1] >= 1


def test_quantize_move_delays_positive_at_least_1ms():
    assert minitouch_module._quantize_move_delays([0.0005, 0.0015]) == [1, 1]


def test_quantize_move_delays_largest_remainder_carry():
    # 0.0101+0.0102+0.0103 → 目标 31ms，余数最大者补齐为 [10, 10, 11]
    out = minitouch_module._quantize_move_delays([0.0101, 0.0102, 0.0103])
    assert sum(out) == 31
    assert out == [10, 10, 11]


def test_quantize_move_delays_unrepresentable_returns_none():
    # 目标总毫秒 < 正 delay 数 → 点数不可表示，禁止静默放大预算
    assert minitouch_module._quantize_move_delays([0.0001, 0.0001, 0.0001]) is None


class _CmdBuilder:
    """记录命令文本的 fake builder（humanized 命令形状测试）。"""

    def __init__(self, events):
        self.events = events
        self.commands = []
        self.delay = 0

    def down(self, x, y, contact=0, pressure=100):
        self.commands.append(f'd {x} {y}')
        return self

    def move(self, x, y, contact=0, pressure=100):
        self.commands.append(f'm {x} {y}')
        return self

    def wait(self, ms=10):
        self.commands.append(f'w {ms}')
        self.delay += ms
        return self

    def up(self, contact=0):
        self.commands.append('u')
        return self

    def commit(self):
        self.commands.append('c')
        return self

    def clear(self):
        self.events.append('builder.clear')
        self.commands.clear()
        self.delay = 0


_PLAN_SWIPE = MovePlan(points=((150, 150), (300, 400)), delays=(0.010, 0.020))
_PLAN_HOLD = MovePlan(points=((101, 201), (99, 199)), delays=(0.005, 0.005))


def _humanizer(level='light', plan=_PLAN_SWIPE, press=0.1, gap=0.052, liftoff=None,
               hold=None):
    return SimpleNamespace(
        enabled=True,
        level=level,
        gap_seconds=lambda default: gap,
        press_seconds=lambda: press,
        plan_swipe=lambda *a, **kw: plan,
        plan_touch_liftoff=lambda target: liftoff,
        plan_hold=lambda target, duration_s, **kw: hold,
    )


class _HumanizedDevice(Minitouch):
    """humanized impl 命令形状测试夹具：fake humanizer + 记录 send 的 fake builder。"""

    def __init__(self, humanizer, fail_send_number=None):
        self.humanizer = humanizer
        self.events = []
        self.builder = _CmdBuilder(self.events)
        self.__dict__['minitouch_builder'] = self.builder
        self.sent_batches = []
        self.send_count = 0
        self.fail_send_number = fail_send_number

    def minitouch_send(self, post_send_gap_s=None):
        self.send_count += 1
        payload = tuple(self.builder.commands)
        self.sent_batches.append(payload)
        self.events.append(f'send:{payload}')
        if post_send_gap_s is None:
            post_send_gap_s = getattr(self, '_humanized_minitouch_gap_s', None)
        self.events.append(f'gap:{post_send_gap_s}')
        if self.fail_send_number is not None and self.send_count == self.fail_send_number:
            raise ConnectionResetError('transport')
        self.builder.clear()


def test_humanized_swipe_three_sends_wait_before_move_int_ms():
    device = _HumanizedDevice(_humanizer())
    device._swipe_minitouch_humanized_impl((100, 100), (300, 400), duration=0.1)

    sends = [e for e in device.events if e.startswith('send:')]
    # 严格只有三个 send 批次：DOWN / 所有 MOVE / UP
    assert len(sends) == 3
    assert sends[0] == "send:('d 100 100', 'c')"
    # MOVE 批内 w → move → commit 连续追加后只调用一次 send；最后一个 wait 在终点 MOVE 前
    assert sends[1] == "send:('w 10', 'm 150 150', 'c', 'w 20', 'm 300 400', 'c')"
    # UP 批：UP 后不消费 MovePlan delay（无 wait）
    assert sends[2] == "send:('u', 'c')"
    # 所有 w 参数是整数；整条量化 wait 总和与 plan.total_seconds 误差 ≤1ms
    waits = [cmd for cmd in device.sent_batches[1] if cmd.startswith('w')]
    assert len(waits) == 2
    assert all(cmd.split()[0] == 'w' and int(cmd.split()[1]) >= 1 for cmd in waits)
    assert sum(int(cmd.split()[1]) for cmd in waits) == int(_PLAN_SWIPE.total_seconds * 1000 + 0.5)
    # 维度 I：三次 send 都带同一个 gap_seconds(0.05) 值
    assert device.events.count('gap:0.052') == 3


def test_humanized_swipe_touch_liftoff_before_up_in_move_batch():
    liftoff = TailPlan(points=((299, 399),), delays=(0.008,))
    device = _HumanizedDevice(_humanizer(liftoff=liftoff))
    device._swipe_minitouch_humanized_impl((100, 100), (300, 400), duration=0.1)

    sends = [e for e in device.events if e.startswith('send:')]
    # touch liftoff（维度 F）在 UP 前并入 MOVE 批，不增加 send 批次数
    assert len(sends) == 3
    assert sends[1] == "send:('w 10', 'm 150 150', 'c', 'w 20', 'm 300 400', 'c', 'w 8', 'm 299 399', 'c')"
    assert sends[2] == "send:('u', 'c')"


def test_humanized_click_single_batch_press_wait_int():
    device = _HumanizedDevice(_humanizer(press=0.145))
    device._click_minitouch_humanized_impl(100, 200)

    sends = [e for e in device.events if e.startswith('send:')]
    # 单批：down → wait(整数毫秒) → up → 一次 send，不为按压时长拆成两批
    assert sends == ["send:('d 100 200', 'c', 'w 145', 'u', 'c')"]
    assert device.send_count == 1
    assert device.events.count('gap:0.052') == 1


def test_humanized_swipe_a_class_plan_none_falls_back_to_legacy_once():
    device = _HumanizedDevice(_humanizer(plan=None))
    legacy = Mock(side_effect=lambda *a, **kw: device.events.append('legacy'))
    device._swipe_minitouch_legacy_impl = legacy

    device._swipe_minitouch_humanized_impl((100, 100), (300, 400), duration=0.1)

    # A 类计划回退：事件尚未发出，legacy 只调用一次，无任何 send
    legacy.assert_called_once_with((100, 100), (300, 400), duration=0.1)
    assert device.send_count == 0


def test_humanized_swipe_unrepresentable_quantization_falls_back_to_legacy():
    # 目标总毫秒 < 正 delay 数 → 量化返回 None → A 类回退 legacy（不放大预算）
    plan = MovePlan(points=((101, 101), (102, 102)), delays=(0.0001, 0.0001))
    device = _HumanizedDevice(_humanizer(plan=plan))
    legacy = Mock(side_effect=lambda *a, **kw: device.events.append('legacy'))
    device._swipe_minitouch_legacy_impl = legacy

    device._swipe_minitouch_humanized_impl((100, 100), (300, 400), duration=0.1)

    legacy.assert_called_once()
    assert device.send_count == 0


class _TcpRecoveryDevice(Minitouch):
    """完整 TCP 恢复路径测试夹具：fake adb forward/restart + fake socket 握手。"""

    def __init__(self, lines):
        self.events = []
        self.config = SimpleNamespace(DEVICE_OVER_HTTP=False, FORWARD_PORT_RANGE=(4000, 5000))
        self.adb_binary = 'adb'
        self.serial = 'emulator-5554'
        self._minitouch_pid = '101'
        self._minitouch_port = 1234
        self.max_x = 1280
        self.max_y = 720
        self.__dict__['minitouch_builder'] = _CmdBuilder(self.events)

    def sleep(self, seconds):
        self.events.append(f'sleep:{seconds}')


class _FakeSocket:
    def __init__(self, lines):
        self._lines = lines

    def settimeout(self, timeout):
        self.timeout = timeout

    def connect(self, addr):
        pass

    def makefile(self):
        return io.StringIO(self._lines)

    def close(self):
        pass


def test_tcp_first_connect_does_not_restart_atx(monkeypatch):
    """首次点击前只建立新连接，不能重启刚完成 u2 初始化的 atx-agent。"""
    clock = _Clock()
    device = _TcpRecoveryDevice('v 1\n^ 2 1280 720 50\n$ 202\n')
    fake_socket = _FakeSocket('v 1\n^ 2 1280 720 50\n$ 202\n')
    monkeypatch.setattr(minitouch_module.time, 'monotonic', clock.monotonic)
    monkeypatch.setattr(minitouch_module.socket, 'socket', lambda *a, **kw: fake_socket)
    monkeypatch.setattr(minitouch_module, 'random_port', lambda port_range: 4321)

    def run_adb(command, **kwargs):
        device.events.append(('adb', tuple(command), kwargs['timeout']))
        if command[-2:] == ['forward', '--list']:
            return SimpleNamespace(
                stdout='emulator-5554 tcp:1234 localabstract:minitouch\n'
            )
        return SimpleNamespace(stdout='')

    monkeypatch.setattr(minitouch_module.subprocess, 'run', run_adb)

    device._recover_humanized_minitouch_tcp(
        old_pid=None,
        deadline=clock.monotonic() + 3.0,
        restart_atx=False,
    )

    adb_commands = [event[1] for event in device.events if isinstance(event, tuple)]
    assert not any('/data/local/tmp/atx-agent' in command for command in adb_commands)
    assert ('adb', '-s', 'emulator-5554', 'forward', '--remove', 'tcp:1234') in adb_commands
    assert ('adb', '-s', 'emulator-5554', 'forward', 'tcp:4321', 'localabstract:minitouch') in adb_commands
    assert device._minitouch_pid == '202'
    assert device._minitouch_client is fake_socket


def test_tcp_recovery_full_handshake_new_pid(monkeypatch):
    clock = _Clock()
    device = _TcpRecoveryDevice('v 1\n^ 2 1280 720 50\n$ 202\n')
    fake_socket = _FakeSocket('v 1\n^ 2 1280 720 50\n$ 202\n')
    monkeypatch.setattr(minitouch_module.time, 'monotonic', clock.monotonic)
    monkeypatch.setattr(minitouch_module.socket, 'socket', lambda *a, **kw: fake_socket)
    monkeypatch.setattr(minitouch_module, 'random_port', lambda port_range: 4321)

    def run_adb(command, **kwargs):
        device.events.append(('adb', tuple(command), kwargs['timeout']))
        return SimpleNamespace(stdout='')

    monkeypatch.setattr(minitouch_module.subprocess, 'run', run_adb)

    device.recover_humanized_minitouch_b1()

    # 恢复证据完整：restart_atx → 移除旧 forward → 新 forward → 新 PID
    assert device._minitouch_pid == '202'
    assert device._minitouch_port == 4321
    assert device._minitouch_client is fake_socket
    adb_commands = [event[1] for event in device.events if isinstance(event, tuple) and event[0] == 'adb']
    assert ('adb', '-s', 'emulator-5554', 'shell', '/data/local/tmp/atx-agent', 'server', '--stop') in adb_commands
    assert ('adb', '-s', 'emulator-5554', 'forward', '--remove', 'tcp:1234') in adb_commands
    assert ('adb', '-s', 'emulator-5554', 'forward', 'tcp:4321', 'localabstract:minitouch') in adb_commands
    # 恢复开始时先清空本地 builder（本地卫生操作，不是成功判据本身）
    assert device.events[0] == 'builder.clear'


class _ControlHarness(Control):
    """从 Control.click() 注入传输异常的夹具：humanized click 走真实 impl。"""

    def __init__(self, humanizer, fail_send_number=1):
        self.humanizer = humanizer
        self.config = SimpleNamespace(script=SimpleNamespace(device=SimpleNamespace(control_method='minitouch')))
        self.events = []
        self.builder = _CmdBuilder(self.events)
        self.__dict__['minitouch_builder'] = self.builder
        self.send_number = 0
        self.fail_send_number = fail_send_number

    def handle_control_check(self, name):
        pass

    def minitouch_send(self, post_send_gap_s=None):
        self.send_number += 1
        payload = tuple(self.builder.commands)
        self.events.append(f'write:{payload}')
        if self.send_number == self.fail_send_number:
            raise ConnectionResetError('DOWN 写入结果未知')
        self.builder.clear()

    def recover_humanized_minitouch_b1(self):
        # 模拟真实恢复第一步（本地卫生清理），随后因证据缺失而接管
        self.builder.clear()
        raise RequestHumanTakeover


def test_control_click_b1_failure_takes_over_no_legacy_no_retry():
    device = _ControlHarness(_humanizer(press=0.1))
    # 包裹真实恢复方法：先做本地卫生清理，随后因恢复证据缺失而接管
    device.recover_humanized_minitouch_b1 = Mock(side_effect=device.recover_humanized_minitouch_b1)

    with pytest.raises(RequestHumanTakeover):
        Control.click(device, 100, 200, control_check=False)

    # B1：含 DOWN 的同包批次写入已开始但结果未知 → 没有第二个 DOWN、没有 legacy 重放
    assert device.events == [
        "write:('d 100 200', 'c', 'w 100', 'u', 'c')",
        'builder.clear',
    ]
    assert device.send_number == 1
    assert device.builder.commands == []
    device.recover_humanized_minitouch_b1.assert_called_once()


def test_control_click_b1_replays_legacy_once_after_recovery():
    device = _ControlHarness(_humanizer(press=0.1), fail_send_number=1)
    # 恢复证据完整（清空本地 builder 后正常返回）→ 允许一次无装饰 legacy 重放
    device.recover_humanized_minitouch_b1 = Mock(side_effect=lambda: device.builder.clear())

    Control.click(device, 100, 200, control_check=False)

    writes = [e for e in device.events if e.startswith('write:')]
    assert len(writes) == 2
    # humanized 一次（带 press wait）+ legacy 一次（无 wait），共两个 send
    assert writes[0] == "write:('d 100 200', 'c', 'w 100', 'u', 'c')"
    assert writes[1] == "write:('d 100 200', 'c', 'u', 'c')"
    assert device.send_number == 2


def test_humanized_long_click_plan_none_falls_back_to_legacy_once():
    # A 类计划回退：plan_hold 返回 None → 无装饰 legacy 一次调用、零 send
    # （对照 swipe 的 test_humanized_swipe_a_class_plan_none_falls_back_to_legacy_once）
    device = _HumanizedDevice(_humanizer(hold=None))
    legacy = Mock(side_effect=lambda *a, **kw: device.events.append('legacy'))
    device._long_click_minitouch_legacy_impl = legacy

    device._long_click_minitouch_humanized_impl(100, 200, 1.0)

    legacy.assert_called_once_with(100, 200, 1.0)
    assert device.send_count == 0


def test_humanized_long_click_b0_transport_failure_replays_legacy():
    # B0：事件尚未发出（send 未调用）时 prepare 阶段传输异常 → 无装饰
    # legacy 一次。hold 是单批，fail_send_number=1 构造的是 B1（send 已开始），
    # B0 用"prepare 抛传输异常"构造
    device = _HumanizedDevice(_humanizer(hold=_PLAN_HOLD))
    legacy = Mock(side_effect=lambda *a, **kw: device.events.append('legacy'))
    device._long_click_minitouch_legacy_impl = legacy

    def fail_prepare():
        raise ConnectionResetError('transport')
    device._prepare_humanized_minitouch_builder = fail_prepare

    device._long_click_minitouch_humanized_impl(100, 200, 1.0)

    legacy.assert_called_once_with(100, 200, 1.0)
    assert device.send_count == 0


def test_humanized_long_click_b1_send_failure_recovers_then_legacy():
    # B1：send 已调用（整批命令可能已发出）→ recover_humanized_minitouch_b1
    # + legacy 重放一次。hold 单批：fail_send_number=1 即 DOWN/微颤/UP 整批
    # 发送失败，恢复后完整重放长按
    device = _HumanizedDevice(_humanizer(hold=_PLAN_HOLD), fail_send_number=1)
    legacy = Mock(side_effect=lambda *a, **kw: device.events.append('legacy'))
    device._long_click_minitouch_legacy_impl = legacy
    recover_calls = []
    device.recover_humanized_minitouch_b1 = lambda: recover_calls.append(True)

    device._long_click_minitouch_humanized_impl(100, 200, 1.0)

    assert recover_calls == [True], 'B1 必须先走恢复再重放'
    legacy.assert_called_once_with(100, 200, 1.0)
