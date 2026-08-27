# -*- coding: utf-8 -*-
"""uiautomator2 拟人化接入测试（Plan Task 18）。

覆盖 Plan 契约 11 的 B 类投递异常收口：
- B0（首个原生 RPC 尚未开始）异常 → 最多一次无装饰 legacy；
- B1（单次 injectInputEvent RPC 已发出但结果未知）→ 一律 RequestHumanTakeover，
  不重放 legacy、不发送第二个 DOWN、不经过会自动 reset/re-send 的 u2 wrapper；
- 开档输入只经 _u2_single_input_rpc 直达 /jsonrpc/0（二元 timeout + retry=False）；
- Control.click/swipe enabled 时直达无装饰 humanized impl，绝不进入公开 @retry。
"""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
import requests

from module.device.control import Control
from module.device.method import uiautomator_2 as u2_module
from module.device.method.uiautomator_2 import (
    U2_ACTION_DOWN,
    U2_ACTION_MOVE,
    U2_ACTION_UP,
    Uiautomator2,
    _HumanizedU2ProtocolError,
)
from module.exception import RequestHumanTakeover

pytestmark = pytest.mark.unit


class _U2B1Harness:
    def __init__(self):
        self.events = []


class _JsonRpcResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class _RetryingJsonRpcHttp:
    def __init__(self, owner, fail_action):
        self.owner = owner
        self.fail_action = fail_action
        self.calls = []

    def post(self, url, *, headers, data, timeout, retry=True):
        # 模拟 uiautomator2：标量会变成 (3, scalar)，默认 retry 会准备 ATX 后重发。
        if isinstance(timeout, (int, float)):
            timeout = (3.0, timeout)
        payload = json.loads(data)
        self.calls.append((url, payload, timeout, retry))
        action = payload['params'][0]
        self.owner.events.append(('input-requested', action))
        if action == self.fail_action:
            self.owner.events.append(('response-unknown', action))
            if retry:
                self.owner.prepare_atx_agent()
                return self.post(url, headers=headers, data=data, timeout=timeout, retry=False)
            raise requests.ReadTimeout('input request may already be delivered')
        return _JsonRpcResponse({
            'jsonrpc': '2.0',
            'id': payload['id'],
            'result': True,
        })


class _SingleRpcHarness:
    _run_humanized_uiautomator2 = Uiautomator2._run_humanized_uiautomator2
    _u2_single_input_rpc = Uiautomator2._u2_single_input_rpc
    _click_uiautomator2_humanized_impl = Uiautomator2._click_uiautomator2_humanized_impl
    _swipe_uiautomator2_humanized_impl = Uiautomator2._swipe_uiautomator2_humanized_impl
    _drag_along_impl = Uiautomator2._drag_along_impl

    def __init__(self, fail_action):
        self.events = []
        self.sleeps = []
        self.prepare_atx_agent = Mock()
        self.reset_uiautomator = Mock()
        self.http = _RetryingJsonRpcHttp(self, fail_action)
        self.pos_rel2abs = Mock(side_effect=AssertionError('拟人化像素坐标不得进入第三方相对坐标转换'))
        self.u2 = SimpleNamespace(
            http=self.http,
            pos_rel2abs=self.pos_rel2abs,
            _jsonrpc_id=lambda method: 'single-input-id',
            jsonrpc=Mock(side_effect=AssertionError('拟人化路径不得使用 jsonrpc wrapper')),
            reset_uiautomator=self.reset_uiautomator,
            touch=SimpleNamespace(down=Mock(), move=Mock(), up=Mock()),
        )
        self.humanizer = SimpleNamespace(
            press_seconds=lambda: 0.07,
            plan_swipe=lambda *args, **kwargs: SimpleNamespace(
                points=[(30, 30)], delays=[0.1]
            ),
            # 全操作共享 CD（2026-08-27）入口：本文件只测 u2 分派拓扑，
            # 节奏等待与打点用 no-op（返回 0 即不 sleep，不影响事件断言）
            pace_execute=lambda: 0.0,
            record_action=lambda target=None, name=None: None,
        )

    def sleep(self, seconds):
        self.sleeps.append(seconds)

    def _click_uiautomator2_legacy_impl(self, x, y):
        self.events.append(('legacy-click', x, y))

    def _swipe_uiautomator2_legacy_impl(self, p1, p2, duration=0.1):
        self.events.append(('legacy-swipe', p1, p2, duration))


def test_u2_b0_calls_undecorated_legacy_once():
    device = _U2B1Harness()
    legacy = Mock(side_effect=lambda: device.events.append('legacy'))

    def fail_before_rpc(rpc):
        raise ConnectionResetError('尚未调用设备 RPC')

    Uiautomator2._run_humanized_uiautomator2(device, fail_before_rpc, legacy)

    assert device.events == ['legacy']
    legacy.assert_called_once_with()


def test_u2_down_response_unknown_takes_over_without_any_second_input():
    device = _U2B1Harness()
    legacy = Mock(side_effect=lambda: device.events.append('legacy'))

    def down_response_unknown(rpc):
        def down():
            device.events.append('DOWN:request-started')
            raise ConnectionResetError('DOWN 请求已发出，响应未知')

        rpc(down)

    with pytest.raises(RequestHumanTakeover):
        Uiautomator2._run_humanized_uiautomator2(device, down_response_unknown, legacy)

    assert device.events == ['DOWN:request-started']
    legacy.assert_not_called()


def test_u2_move_failure_takes_over_without_up_or_legacy_replay():
    device = _U2B1Harness()
    legacy = Mock(side_effect=lambda: device.events.append('legacy'))

    def move_fails_after_confirmed_down(rpc):
        rpc(lambda: device.events.append('DOWN:confirmed'))

        def move():
            device.events.append('MOVE:request-started')
            raise ConnectionResetError('MOVE 请求已发出，响应未知')

        rpc(move)
        rpc(lambda: device.events.append('UP:must-not-run'))

    with pytest.raises(RequestHumanTakeover):
        Uiautomator2._run_humanized_uiautomator2(device, move_fails_after_confirmed_down, legacy)

    assert device.events == ['DOWN:confirmed', 'MOVE:request-started']
    legacy.assert_not_called()


def test_u2_single_rpc_down_read_timeout_never_resets_or_reissues_input():
    device = _SingleRpcHarness(U2_ACTION_DOWN)

    with pytest.raises(RequestHumanTakeover):
        device._click_uiautomator2_humanized_impl(10, 20)

    assert [(url, payload['params'][0]) for url, payload, _, _ in device.http.calls] == [
        ('/jsonrpc/0', U2_ACTION_DOWN),
    ]
    assert all(
        isinstance(timeout, tuple)
        and len(timeout) == 2
        and all(value == 1.0 for value in timeout)
        and retry is False
        for _, _, timeout, retry in device.http.calls
    )
    assert device.events == [
        ('input-requested', U2_ACTION_DOWN),
        ('response-unknown', U2_ACTION_DOWN),
    ]
    device.prepare_atx_agent.assert_not_called()
    device.reset_uiautomator.assert_not_called()
    device.u2.jsonrpc.assert_not_called()
    device.u2.touch.down.assert_not_called()
    device.u2.touch.move.assert_not_called()
    device.u2.touch.up.assert_not_called()
    device.pos_rel2abs.assert_not_called()
    assert device.sleeps == []


def test_u2_single_rpc_move_read_timeout_never_replays_down_or_sends_up():
    device = _SingleRpcHarness(U2_ACTION_MOVE)

    with pytest.raises(RequestHumanTakeover):
        device._swipe_uiautomator2_humanized_impl((10, 10), (30, 30), duration=0.1)

    assert [payload['params'][0] for _, payload, _, _ in device.http.calls] == [
        U2_ACTION_DOWN,
        U2_ACTION_MOVE,
    ]
    assert sum(payload['params'][0] == U2_ACTION_DOWN for _, payload, _, _ in device.http.calls) == 1
    assert all(payload['params'][0] != U2_ACTION_UP for _, payload, _, _ in device.http.calls)
    assert all(retry is False for _, _, _, retry in device.http.calls)
    assert device.prepare_atx_agent.call_count == 0
    assert device.reset_uiautomator.call_count == 0
    assert device.u2.jsonrpc.call_count == 0
    assert device.u2.touch.up.call_count == 0
    device.pos_rel2abs.assert_not_called()
    assert not any(event[0].startswith('legacy-') for event in device.events)


def test_u2_edge_pixel_coordinates_do_not_query_window_size_or_retry():
    device = _SingleRpcHarness(fail_action=None)

    assert device._u2_single_input_rpc(U2_ACTION_DOWN, 0, 0) is True

    device.pos_rel2abs.assert_not_called()
    assert len(device.http.calls) == 1
    _, payload, timeout, retry = device.http.calls[0]
    assert payload['params'] == [U2_ACTION_DOWN, 0, 0, 0]
    assert timeout == (1.0, 1.0)
    assert retry is False
    device.prepare_atx_agent.assert_not_called()


def test_u2_usb_rpc_uses_deadline_forward_without_path2url(monkeypatch):
    device = _SingleRpcHarness(fail_action=None)
    device.serial = 'emulator-5554'
    device.adb_binary = 'adb'
    device.config = SimpleNamespace(FORWARD_PORT_RANGE=(4000, 5000))
    calls = []

    def run(command, **kwargs):
        calls.append((tuple(command), kwargs))
        return SimpleNamespace(stdout='')

    monkeypatch.setattr(u2_module.subprocess, 'run', run)
    monkeypatch.setattr(u2_module, 'random_port', lambda port_range: 4321)
    device._click_uiautomator2_humanized_impl(0, 0)

    assert device.http.calls[0][0] == 'http://127.0.0.1:4321/jsonrpc/0'
    assert device.http.calls[1][0] == 'http://127.0.0.1:4321/jsonrpc/0'
    assert calls[0][0][-3:] == ('forward', 'tcp:4321', 'tcp:7912')
    assert calls[1][0][-3:] == ('forward', '--remove', 'tcp:4321')
    assert all(kwargs['timeout'] == 1.0 for _, kwargs in calls)


def test_u2_usb_forward_failure_is_b0_legacy_once(monkeypatch):
    """临时 forward 在首个 RPC 前失败，只允许一次无装饰 legacy。"""
    device = _SingleRpcHarness(fail_action=None)
    device.serial = 'emulator-5554'
    device.adb_binary = 'adb'
    device.config = SimpleNamespace(FORWARD_PORT_RANGE=(4000, 5000))

    def fail_forward(command, **kwargs):
        raise u2_module.subprocess.TimeoutExpired(command, kwargs['timeout'])

    monkeypatch.setattr(u2_module.subprocess, 'run', fail_forward)
    device._click_uiautomator2_humanized_impl(10, 20)

    assert device.events == [('legacy-click', 10, 20)]
    assert device.http.calls == []


def test_u2_cache_miss_uses_raw_device_without_connection_attr_init(monkeypatch):
    """首次开档不得触发 connection_attr.u2 的第三方初始化链。"""
    class _RawDevice:
        def __init__(self):
            self.events = []
            self.http = _RetryingJsonRpcHttp(self, fail_action=None)

        def _jsonrpc_id(self, method):
            return 'raw-device-id'

    raw = _RawDevice()
    monkeypatch.setattr(u2_module.u2, 'Device', lambda serial: raw)
    device = object.__new__(Uiautomator2)
    device.serial = 'http://127.0.0.1:7912'
    device.humanizer = SimpleNamespace(press_seconds=lambda: 0)
    device.sleep = lambda seconds: None
    device._click_uiautomator2_legacy_impl = lambda x, y: pytest.fail('不得走 legacy')

    # 如果实现访问 self.u2，descriptor 会把这个测试变成失败。
    type(device).u2 = property(lambda self: (_ for _ in ()).throw(
        AssertionError('不得触发 connection_attr.u2 cached_property')))
    try:
        Uiautomator2._click_uiautomator2_humanized_impl(device, 10, 20)
    finally:
        delattr(type(device), 'u2')

    assert [call[1]["params"][0] for call in raw.http.calls] == [U2_ACTION_DOWN, U2_ACTION_UP]


def test_u2_real_session_branch_uses_single_deadline_transport(monkeypatch):
    """真实 u2 session 分支使用项目侧单次传输而不调用其隐式 retry。"""
    device = _SingleRpcHarness(fail_action=None)
    device.serial = 'http://127.0.0.1:7912'
    payload = json.dumps({'jsonrpc': '2.0', 'id': 'single-input-id', 'result': True}).encode()
    calls = []

    def request(url, method, **kwargs):
        calls.append((url, method, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: json.loads(payload))

    monkeypatch.setattr(u2_module, 'humanized_http_request', request)
    monkeypatch.setattr(u2_module.u2, '_AgentRequestSession', type(device.http))
    device._click_uiautomator2_humanized_impl(1, 2)

    assert calls[0][0] == 'http://127.0.0.1:7912/jsonrpc/0'
    assert calls[0][1] == 'post'
    assert calls[0][2]['deadline'] > 0


@pytest.mark.parametrize(
    'payload',
    [
        {},
        {'jsonrpc': '2.0', 'result': True},
        {'jsonrpc': '2.0', 'id': 'wrong-id', 'result': True},
        {'jsonrpc': '1.0', 'id': 'single-input-id', 'result': True},
        {'jsonrpc': '2.0', 'id': 'single-input-id', 'result': False},
        {'jsonrpc': '2.0', 'id': 'single-input-id', 'result': None},
    ],
)
def test_u2_single_rpc_rejects_ambiguous_jsonrpc_success(payload):
    device = _SingleRpcHarness(fail_action=None)
    device.http.post = Mock(return_value=_JsonRpcResponse(payload))

    with pytest.raises(_HumanizedU2ProtocolError):
        device._u2_single_input_rpc(U2_ACTION_DOWN, 10, 20)

    device.pos_rel2abs.assert_not_called()


def test_u2_single_rpc_read_timeout_is_b1_without_background_worker():
    device = _SingleRpcHarness(fail_action=None)

    def slow_post(*args, **kwargs):
        # 单次 HTTP fake 直接模拟 requests 的读取超时；生产代码不再启动
        # 无法取消的 daemon worker，因此不会有迟到输入线程。
        raise requests.ReadTimeout('input response deadline exceeded')

    device.http.post = slow_post
    with pytest.raises(RequestHumanTakeover):
        device._click_uiautomator2_humanized_impl(10, 20)

    assert device.sleeps == []


def test_u2_transport_context_resets_after_rpc_failure():
    """RPC 异常退出后，ContextVar 不得把旧设备 URL 泄漏到下一次调用。"""
    device = _SingleRpcHarness(U2_ACTION_DOWN)
    with pytest.raises(RequestHumanTakeover):
        device._click_uiautomator2_humanized_impl(10, 20)

    device.http = _RetryingJsonRpcHttp(device, fail_action=None)
    device.u2.http = device.http
    assert device._u2_single_input_rpc(U2_ACTION_UP, 10, 20) is True
    assert device.http.calls[0][0] == '/jsonrpc/0'


@pytest.mark.parametrize(
    'entry, public_name, humanized_name, args',
    [
        ('click', 'click_uiautomator2', '_click_uiautomator2_humanized_impl', (100, 200)),
        ('swipe', 'swipe_uiautomator2', '_swipe_uiautomator2_humanized_impl', (np.array([100, 100]), np.array([200, 200]))),
    ],
)
def test_control_u2_enabled_dispatch_never_enters_public_retry(monkeypatch, entry, public_name, humanized_name, args):
    device = object.__new__(Control)
    device.config = SimpleNamespace(script=SimpleNamespace(device=SimpleNamespace(control_method='uiautomator2')))
    # 全操作共享 CD（2026-08-27）入口：本测试只关心分派拓扑，节奏入口 no-op
    device.humanizer = SimpleNamespace(
        enabled=True, pace_execute=lambda: 0.0, record_action=lambda target=None, name=None: None)
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


def test_humanized_drag_consumes_delay_before_move_and_never_after_up():
    events = []
    sleeps = []
    device = SimpleNamespace(
        u2=SimpleNamespace(
            touch=SimpleNamespace(
                down=lambda x, y: events.append(('DOWN', x, y)),
                move=lambda x, y: events.append(('MOVE', x, y)),
                up=lambda x, y: events.append(('UP', x, y)),
            )
        ),
        sleep=lambda seconds: sleeps.append(seconds),
    )
    path = [(10, 10, 0), (20, 20, 0.12), (30, 30, 0)]

    Uiautomator2._drag_along_impl(device, path, verbose=False, delay_before_move=True)

    assert events == [('DOWN', 10, 10), ('MOVE', 20, 20), ('UP', 30, 30)]
    assert sleeps == [0.12]
