# 桌面客户端模式测试：模式判定、Handle 定位、截图裁切、输入投递、Device 旁路
import types
from unittest.mock import MagicMock

import pytest
import win32con

from module.device.connection import Connection
from module.device.connection_attr import ConnectionAttr
from module.device.device import Device, EmulatorState
from module.device.handle import Handle
from module.exception import EmulatorNotRunningError
from module.device.method.windows_impl import Window


def _conn_with_serial(serial):
    conn = object.__new__(ConnectionAttr)
    conn.config = types.SimpleNamespace(
        script=types.SimpleNamespace(device=types.SimpleNamespace(serial=serial))
    )
    return conn


def test_is_desktop_true():
    # serial=desktop → is_desktop 为 True
    assert _conn_with_serial('desktop').is_desktop is True


def test_is_desktop_false_for_emulator():
    # 模拟器 serial → is_desktop 为 False
    assert _conn_with_serial('127.0.0.1:16384').is_desktop is False


def test_is_desktop_false_for_auto():
    # 默认 auto → is_desktop 为 False
    assert _conn_with_serial('auto').is_desktop is False


def _handle_config(serial='desktop', handle='4242'):
    return types.SimpleNamespace(
        script=types.SimpleNamespace(
            device=types.SimpleNamespace(serial=serial, handle=handle)
        )
    )


def test_find_desktop_window_by_pid_matches(monkeypatch):
    # 多个同名窗口时，按 GetWindowThreadProcessId 匹配 PID 返回正确句柄
    handle = object.__new__(Handle)
    handle.config = _handle_config()
    windows = [0x100, 0x200, 0x300]
    titles = {0x100: '其他窗口', 0x200: '阴阳师-网易游戏', 0x300: '阴阳师-网易游戏'}
    pids = {0x200: 1111, 0x300: 4242}
    monkeypatch.setattr('module.device.handle.EnumWindows', lambda cb, lst: [lst.append(h) for h in windows])
    monkeypatch.setattr('module.device.handle.GetWindowText', lambda h: titles[h])
    monkeypatch.setattr('module.device.handle.GetWindowThreadProcessId', lambda h: (0, pids[h]))
    assert handle.find_desktop_window_by_pid('4242') == 0x300


def test_find_desktop_window_by_pid_not_found_raises(monkeypatch):
    # PID 无对应窗口 → 抛 EmulatorNotRunningError
    handle = object.__new__(Handle)
    handle.config = _handle_config(handle='9999')
    monkeypatch.setattr('module.device.handle.EnumWindows', lambda cb, lst: [lst.append(0x100)])
    monkeypatch.setattr('module.device.handle.GetWindowText', lambda h: '其他窗口')
    monkeypatch.setattr('module.device.handle.GetWindowThreadProcessId', lambda h: (0, 1111))
    with pytest.raises(EmulatorNotRunningError):
        handle.find_desktop_window_by_pid('9999')


def test_handle_desktop_init_resolves_by_pid(monkeypatch):
    # Handle.__init__ 桌面分支：按 PID 定位窗口，跳过模拟器窗口树
    handle = object.__new__(Handle)
    handle.config = None
    windows = [0x200]
    monkeypatch.setattr('module.device.handle.EnumWindows', lambda cb, lst: [lst.append(h) for h in windows])
    monkeypatch.setattr('module.device.handle.GetWindowText', lambda h: '阴阳师-网易游戏')
    monkeypatch.setattr('module.device.handle.GetWindowThreadProcessId', lambda h: (0, 4242))
    Handle.__init__(handle, _handle_config())
    assert handle.is_desktop_window is True
    assert handle.root_handle_num == 0x200


def test_desktop_window_exists(monkeypatch):
    # 窗口在 → True
    handle = object.__new__(Handle)
    handle.config = _handle_config()
    monkeypatch.setattr('module.device.handle.EnumWindows', lambda cb, lst: [lst.append(0x200)])
    monkeypatch.setattr('module.device.handle.GetWindowText', lambda h: '阴阳师-网易游戏')
    monkeypatch.setattr('module.device.handle.GetWindowThreadProcessId', lambda h: (0, 4242))
    assert handle.desktop_window_exists() is True
    # 窗口不在 → False
    handle2 = object.__new__(Handle)
    handle2.config = _handle_config(handle='9999')
    monkeypatch.setattr('module.device.handle.GetWindowText', lambda h: '其他窗口')
    monkeypatch.setattr('module.device.handle.GetWindowThreadProcessId', lambda h: (0, 1111))
    assert handle2.desktop_window_exists() is False


def test_desktop_screenshot_handle_num_returns_root():
    # 桌面截图句柄 = 根窗口
    handle = object.__new__(Handle)
    handle.is_desktop_window = True
    handle.root_handle_num = 0x200
    assert handle.screenshot_handle_num == 0x200


def test_desktop_screenshot_size_is_physical_target():
    # 桌面截图目标尺寸 = 物理 1280x720（与资产 1:1），位图由截图方法从逻辑缩放
    handle = object.__new__(Handle)
    handle.is_desktop_window = True
    handle.root_handle_num = 0x200
    assert handle.screenshot_size == (1280, 720)


def test_desktop_client_offset(monkeypatch):
    # 客户区在窗口 DC 内的偏移 = ClientToScreen - GetWindowRect（实际标题栏 39px）
    handle = object.__new__(Handle)
    handle.is_desktop_window = True
    handle.root_handle_num = 0x200
    monkeypatch.setattr('module.device.handle.GetWindowRect', lambda h: (0, 0, 1280, 759))
    monkeypatch.setattr('module.device.handle.ClientToScreen', lambda h, p: (0, 39))
    assert handle.desktop_client_offset() == (0, 39)


def test_control_handle_list_desktop_returns_root():
    # 桌面控制句柄 = 根窗口本身（无子窗口树）
    w = object.__new__(Window)
    w.is_desktop_window = True
    w.root_handle_num = 0x200
    assert w.control_handle_list == [0x200]


def _desktop_window(scale=1.25):
    """构造桌面 Window 桩：截图空间 1280x720，消息空间按 scale 缩小（模拟 DPI 虚拟化）。"""
    w = object.__new__(Window)
    w.is_desktop_window = True
    w.root_handle_num = 0x200
    w.screenshot_size = (1280, 720)
    w.desktop_client_size = lambda: (1280, 720)
    w.desktop_client_size_virtual = lambda: (int(1280 / scale), int(720 / scale))
    return w


def test_desktop_message_coord_scales_to_virtual_space():
    # 资产坐标（截图 1280x720）→ 消息坐标（虚拟 1024x576），比例 1/1.25
    w = _desktop_window()
    assert w.desktop_message_coord(0, 0) == (0, 0)
    assert w.desktop_message_coord(1280, 720) == (1024, 576)
    assert w.desktop_message_coord(640, 360) == (512, 288)


def test_desktop_message_coord_identity_without_scaling():
    # 100% 缩放（无虚拟化）时换算为恒等，不引入偏移
    w = _desktop_window(scale=1.0)
    assert w.desktop_message_coord(1128, 650) == (1128, 650)


def test_click_desktop_window_message_posts_to_root(monkeypatch):
    # 点击全量消息都投给根窗口，且坐标已换算到消息空间
    w = _desktop_window()
    calls = []
    monkeypatch.setattr('module.device.method.windows_impl.PostMessage',
                        lambda hwnd, msg, wp, lp: calls.append((hwnd, msg, lp)))
    monkeypatch.setattr('module.device.method.windows_impl.SendMessage',
                        lambda hwnd, msg, wp, lp: calls.append((hwnd, msg, lp)))
    monkeypatch.setattr('module.device.method.windows_impl.time.sleep', lambda s: None)
    w.click_desktop_window_message(1280, 720, fast=True)
    msgs = [m for _, m, _ in calls]
    assert win32con.WM_MOUSEMOVE in msgs
    assert win32con.WM_LBUTTONDOWN in msgs
    assert win32con.WM_LBUTTONUP in msgs
    assert win32con.WM_CAPTURECHANGED in msgs
    assert all(hwnd == 0x200 for hwnd, _, _ in calls)
    # 按下消息的 lParam 应为换算后的 (1024, 576)，而非原始资产坐标
    down_lp = [lp for _, m, lp in calls if m == win32con.WM_LBUTTONDOWN][0]
    assert (down_lp & 0xFFFF, down_lp >> 16) == (1024, 576)


def test_swipe_desktop_window_message_posts_trajectory(monkeypatch):
    # 滑动：先 move 到起点 → 按下 → 逐点 move → 释放 → 捕获结束
    w = _desktop_window()
    calls = []
    monkeypatch.setattr('module.device.method.windows_impl.PostMessage',
                        lambda hwnd, msg, wp, lp: calls.append((msg, wp, lp)))
    monkeypatch.setattr('module.device.method.windows_impl.SendMessage',
                        lambda hwnd, msg, wp, lp: calls.append((msg, wp, lp)))
    monkeypatch.setattr('module.device.method.windows_impl.time.sleep', lambda s: None)
    w.swipe_desktop_window_message((100, 100), (300, 400))
    msgs = [m for m, _, _ in calls]
    assert msgs[0] == win32con.WM_MOUSEMOVE
    assert win32con.WM_LBUTTONDOWN in msgs
    assert win32con.WM_LBUTTONUP in msgs
    assert win32con.WM_CAPTURECHANGED in msgs
    # 滑动结束后光标位置记录为终点，供下次操作起点使用
    assert w._desktop_cursor == (300, 400)


def test_desktop_trace_is_curved_not_straight():
    # 贝塞尔轨迹：点数足够且不全落在起终点连线上（直线插值会全部落在线上）
    w = object.__new__(Window)
    trace = w.desktop_trace((100, 100), (600, 500))
    assert len(trace) > 5
    # 叉积为 0 表示点在直线上；拟人轨迹应存在偏离直线的点
    def cross(p):
        return (600 - 100) * (p[1] - 100) - (500 - 100) * (p[0] - 100)
    assert any(cross(p) != 0 for p in trace)


def test_move_desktop_window_message_first_call_jumps(monkeypatch):
    # 首次移动没有历史位置 → 只发一次 WM_MOUSEMOVE 直达目标，并记录光标
    w = _desktop_window()
    calls = []
    monkeypatch.setattr('module.device.method.windows_impl.PostMessage',
                        lambda hwnd, msg, wp, lp: calls.append((msg, lp)))
    monkeypatch.setattr('module.device.method.windows_impl.time.sleep', lambda s: None)
    w.move_desktop_window_message(640, 360)
    assert len(calls) == 1
    assert calls[0][0] == win32con.WM_MOUSEMOVE
    assert w._desktop_cursor == (640, 360)


def test_move_desktop_window_message_traces_from_last_point(monkeypatch):
    # 已有历史位置 → 沿轨迹逐点移动（多次 WM_MOUSEMOVE），终点更新光标
    w = _desktop_window()
    w._desktop_cursor = (100, 100)
    calls = []
    monkeypatch.setattr('module.device.method.windows_impl.PostMessage',
                        lambda hwnd, msg, wp, lp: calls.append((msg, lp)))
    monkeypatch.setattr('module.device.method.windows_impl.time.sleep', lambda s: None)
    w.move_desktop_window_message(600, 500)
    assert len(calls) > 5
    assert all(m == win32con.WM_MOUSEMOVE for m, _ in calls)
    assert w._desktop_cursor == (600, 500)


def test_click_desktop_moves_before_press(monkeypatch):
    # 点击顺序：先 WM_MOUSEMOVE 到位，再 WM_LBUTTONDOWN（缺 hover 会被客户端忽略）
    w = _desktop_window()
    w._desktop_cursor = (100, 100)
    calls = []
    monkeypatch.setattr('module.device.method.windows_impl.PostMessage',
                        lambda hwnd, msg, wp, lp: calls.append(msg))
    monkeypatch.setattr('module.device.method.windows_impl.SendMessage',
                        lambda hwnd, msg, wp, lp: calls.append(msg))
    monkeypatch.setattr('module.device.method.windows_impl.time.sleep', lambda s: None)
    w.click_desktop_window_message(640, 360, fast=True)
    down_index = calls.index(win32con.WM_LBUTTONDOWN)
    assert win32con.WM_MOUSEMOVE in calls[:down_index]
    # 抬起后补一次移动刷新悬停状态
    assert calls[-1] == win32con.WM_MOUSEMOVE
    assert w._desktop_cursor == (640, 360)


def test_screenshot_window_background_desktop_crops(monkeypatch):
    # 桌面 BitBlt 走客户区 DC，源偏移恒为 (0,0)，位图即物理客户区 1280x720，无需缩放
    w = object.__new__(Window)
    w.is_desktop_window = True
    w.root_handle_num = 0x200
    w.screenshot_size = (1280, 720)
    fake_save_dc = MagicMock()
    fake_mfc_dc = MagicMock()
    fake_mfc_dc.CreateCompatibleDC.return_value = fake_save_dc
    fake_bitmap = MagicMock()
    fake_bitmap.GetBitmapBits.return_value = b'\x00' * (1280 * 720 * 4)
    fake_bitmap.CreateCompatibleBitmap = lambda dc, w, h: None
    fake_bitmap.GetHandle = lambda: 1
    fake_mfc_dc.CreateCompatibleBitmap.return_value = fake_bitmap
    captured = {}
    fake_save_dc.BitBlt = lambda dest, size, src_dc, src, rop: captured.update(src=src, size=size)
    monkeypatch.setattr('module.device.method.windows_impl.GetDC', lambda h: 1)
    monkeypatch.setattr('module.device.method.windows_impl.GetClientRect', lambda h: (0, 0, 1280, 720))
    monkeypatch.setattr('module.device.method.windows_impl.CreateDCFromHandle', lambda dc: fake_mfc_dc)
    monkeypatch.setattr('module.device.method.windows_impl.CreateBitmap', lambda: fake_bitmap)
    monkeypatch.setattr('module.device.method.windows_impl.DeleteObject', lambda h: None)
    img = w.screenshot_window_background()
    assert captured['src'] == (0, 0)
    assert captured['size'] == (1280, 720)
    assert img.shape == (720, 1280, 3)


def test_screenshot_desktop_bitblt_resizes_when_size_mismatch(monkeypatch):
    # 窗口未能校准到目标（如客户端锁定大小）时，位图兜底缩放到 1280x720
    w = object.__new__(Window)
    w.is_desktop_window = True
    w.root_handle_num = 0x200
    w.screenshot_size = (1280, 720)
    fake_save_dc = MagicMock()
    fake_mfc_dc = MagicMock()
    fake_mfc_dc.CreateCompatibleDC.return_value = fake_save_dc
    fake_bitmap = MagicMock()
    fake_bitmap.GetBitmapBits.return_value = b'\x00' * (1281 * 721 * 4)
    fake_bitmap.CreateCompatibleBitmap = lambda dc, w, h: None
    fake_bitmap.GetHandle = lambda: 1
    fake_mfc_dc.CreateCompatibleBitmap.return_value = fake_bitmap
    fake_save_dc.BitBlt = lambda *a, **k: None
    monkeypatch.setattr('module.device.method.windows_impl.GetDC', lambda h: 1)
    monkeypatch.setattr('module.device.method.windows_impl.GetClientRect', lambda h: (0, 0, 1281, 721))
    monkeypatch.setattr('module.device.method.windows_impl.CreateDCFromHandle', lambda dc: fake_mfc_dc)
    monkeypatch.setattr('module.device.method.windows_impl.CreateBitmap', lambda: fake_bitmap)
    monkeypatch.setattr('module.device.method.windows_impl.DeleteObject', lambda h: None)
    img = w.screenshot_desktop_bitblt()
    assert img.shape == (720, 1280, 3)


def test_screenshot_printwindow_uses_client_flag3(monkeypatch):
    # PrintWindow 带 PW_CLIENTONLY|PW_RENDERFULLCONTENT(3)，位图即物理客户区 1280x720
    w = object.__new__(Window)
    w.is_desktop_window = True
    w.root_handle_num = 0x200
    w.screenshot_size = (1280, 720)
    fake_save_dc = MagicMock()
    fake_mfc_dc = MagicMock()
    fake_mfc_dc.CreateCompatibleDC.return_value = fake_save_dc
    fake_bitmap = MagicMock()
    fake_bitmap.GetBitmapBits.return_value = b'\x00' * (1280 * 720 * 4)
    fake_bitmap.CreateCompatibleBitmap = lambda dc, w, h: None
    fake_bitmap.GetHandle = lambda: 1
    fake_mfc_dc.CreateCompatibleBitmap.return_value = fake_bitmap
    fake_windll = MagicMock()
    monkeypatch.setattr('module.device.method.windows_impl.windll', fake_windll)
    monkeypatch.setattr('module.device.method.windows_impl.GetDC', lambda h: 1)
    monkeypatch.setattr('module.device.method.windows_impl.GetClientRect', lambda h: (0, 0, 1280, 720))
    monkeypatch.setattr('module.device.method.windows_impl.CreateDCFromHandle', lambda dc: fake_mfc_dc)
    monkeypatch.setattr('module.device.method.windows_impl.CreateBitmap', lambda: fake_bitmap)
    monkeypatch.setattr('module.device.method.windows_impl.DeleteObject', lambda h: None)
    img = w.screenshot_printwindow()
    assert fake_windll.user32.PrintWindow.call_args.args[2] == 3
    assert img.shape == (720, 1280, 3)


def _desktop_device(config=None):
    dev = object.__new__(Device)
    dev.config = config or types.SimpleNamespace(
        script=types.SimpleNamespace(
            device=types.SimpleNamespace(
                serial='desktop', screenshot_method='auto', control_method='minitouch',
            )
        ),
    )
    return dev


def test_init_desktop_forces_methods_and_healthy():
    # 桌面初始化：screenshot=window_background(BitBlt)、control=window_message、状态置 HEALTHY
    dev = _desktop_device()
    dev.config.save = lambda: None
    transitions = []
    dev._transition_to = lambda target: transitions.append(target)
    dev.screenshot_interval_set = lambda: None
    dev.desktop_window_set_size = lambda: False
    Device._init_desktop(dev)
    assert transitions == [EmulatorState.HEALTHY]
    assert dev.config.script.device.screenshot_method == 'window_background'
    assert dev.config.script.device.control_method == 'window_message'


def test_init_desktop_replaces_printwindow():
    # PrintWindow 对客户端 DirectX 窗口返回纯黑，初始化时改回 BitBlt
    dev = _desktop_device()
    dev.config.script.device.screenshot_method = 'printwindow'
    dev.config.save = lambda: None
    dev._transition_to = lambda target: None
    dev.screenshot_interval_set = lambda: None
    dev.desktop_window_set_size = lambda: False
    Device._init_desktop(dev)
    assert dev.config.script.device.screenshot_method == 'window_background'


def test_app_is_running_desktop_uses_window_existence():
    # 桌面 app_is_running：窗口存在即 True
    dev = _desktop_device()
    dev.desktop_window_exists = lambda: True
    assert dev.app_is_running() is True


def test_full_recovery_desktop_window_alive():
    # 桌面 full_recovery：窗口在 → HEALTHY 并返回 True
    dev = _desktop_device()
    dev.emulator_state = EmulatorState.COLD
    dev.desktop_window_exists = lambda: True
    assert dev.full_recovery() is True
    assert dev.emulator_state == EmulatorState.HEALTHY


def test_full_recovery_desktop_window_missing():
    # 桌面 full_recovery：窗口不在 → 返回 False，不触碰客户端进程
    dev = _desktop_device()
    dev.desktop_window_exists = lambda: False
    assert dev.full_recovery() is False


def test_app_start_stop_desktop_noop():
    # 桌面 app_start/app_stop 为 no-op，不调用 ADB
    dev = _desktop_device()
    calls = {'start': 0, 'stop': 0}
    dev.config.script.error = types.SimpleNamespace(handle_error=True)

    def fake_start():
        calls['start'] += 1
    def fake_stop():
        calls['stop'] += 1
    dev.super_start = fake_start
    dev.super_stop = fake_stop
    # 直接验证桌面分支提前 return
    Device.app_start(dev)
    Device.app_stop(dev)
    assert calls['start'] == 0
    assert calls['stop'] == 0


def test_desktop_window_set_size_noop_when_match(monkeypatch):
    # 物理客户区已是 1280x720 → 返回 False，不调 SetWindowPos
    handle = object.__new__(Handle)
    handle.is_desktop_window = True
    handle.root_handle_num = 0x200
    monkeypatch.setattr('module.device.handle.GetClientRect', lambda h: (0, 0, 1280, 720))
    called = []
    monkeypatch.setattr('module.device.handle.SetWindowPos', lambda *a: called.append(a))
    assert handle.desktop_window_set_size() is False
    assert called == []


def test_desktop_window_set_size_resizes(monkeypatch):
    # 物理客户区 800x600 → 目标 1280x720，SetWindowPos 一次校准成功
    handle = object.__new__(Handle)
    handle.is_desktop_window = True
    handle.root_handle_num = 0x200
    state = {'size': (800, 600)}
    def fake_client_rect(h):
        w, hgt = state['size']
        return (0, 0, w, hgt)
    monkeypatch.setattr('module.device.handle.GetClientRect', fake_client_rect)
    monkeypatch.setattr('module.device.handle.GetWindowLong', lambda h, flag: 0)
    monkeypatch.setattr('module.device.handle._window_total_size', lambda w, h, s, e: (1298, 767))
    calls = []
    def fake_setwindowpos(hwnd, insert, x, y, w, h, flags):
        calls.append((w, h))
        # 首次 SetWindowPos 后物理客户区精确变为 1280x720，校准一次即成功
        state['size'] = (1280, 720)
    monkeypatch.setattr('module.device.handle.SetWindowPos', fake_setwindowpos)
    assert handle.desktop_window_set_size() is True
    assert calls == [(1298, 767)]


def test_desktop_window_set_size_calibrates_to_exact(monkeypatch):
    # 边框假设偏差 1px：首次 SetWindowPos 后客户区 1281x721 → 持续校准到 1280x720
    handle = object.__new__(Handle)
    handle.is_desktop_window = True
    handle.root_handle_num = 0x200
    state = {'size': (800, 600)}
    def fake_client_rect(h):
        w, hgt = state['size']
        return (0, 0, w, hgt)
    monkeypatch.setattr('module.device.handle.GetClientRect', fake_client_rect)
    monkeypatch.setattr('module.device.handle.GetWindowLong', lambda h, flag: 0)
    monkeypatch.setattr('module.device.handle._window_total_size', lambda w, h, s, e: (1298, 767))
    calls = []
    def fake_setwindowpos(hwnd, insert, x, y, w, h, flags):
        calls.append((w, h))
        # 第一次后客户区 1281x721（各差 1px），第二次后精确 1280x720
        if len(calls) == 1:
            state['size'] = (1281, 721)
        else:
            state['size'] = (1280, 720)
    monkeypatch.setattr('module.device.handle.SetWindowPos', fake_setwindowpos)
    assert handle.desktop_window_set_size() is True
    # 第二次把窗口总尺寸各减 1，让客户区 1281x721 → 1280x720
    assert calls == [(1298, 767), (1297, 766)]


def test_desktop_window_set_size_handles_setwindowpos_error(monkeypatch):
    # SetWindowPos 抛异常（UIPI 权限不足拒绝访问）→ 返回 False，不崩溃
    handle = object.__new__(Handle)
    handle.is_desktop_window = True
    handle.root_handle_num = 0x200
    monkeypatch.setattr('module.device.handle.GetClientRect', lambda h: (0, 0, 800, 600))
    monkeypatch.setattr('module.device.handle.GetWindowLong', lambda h, flag: 0)
    monkeypatch.setattr('module.device.handle._window_total_size', lambda w, h, s, e: (1298, 767))

    def boom(*a):
        raise Exception("(5, 'SetWindowPos', '拒绝访问。')")
    monkeypatch.setattr('module.device.handle.SetWindowPos', boom)
    assert handle.desktop_window_set_size() is False


def test_desktop_client_size_returns_physical(monkeypatch):
    # 客户区尺寸在 DPI 感知上下文内读取，返回物理像素
    handle = object.__new__(Handle)
    handle.is_desktop_window = True
    handle.root_handle_num = 0x200
    monkeypatch.setattr('module.device.handle.GetClientRect', lambda h: (0, 0, 1280, 720))
    assert handle.desktop_client_size() == (1280, 720)


def test_dpi_awareness_restores_previous_context(monkeypatch):
    # dpi_awareness 退出时必须恢复原上下文，避免污染后续模拟器直控路径
    from module.device.handle import dpi_awareness
    calls = []
    fake_user32 = MagicMock()
    fake_user32.SetThreadDpiAwarenessContext.side_effect = lambda ctx: calls.append(ctx) or 0x1234
    fake_windll = MagicMock()
    fake_windll.user32 = fake_user32
    monkeypatch.setattr('module.device.handle.ctypes.windll', fake_windll)
    with dpi_awareness():
        pass
    # 第一次传入 PER_MONITOR_AWARE_V2，退出时传回上一次返回的上下文
    assert len(calls) == 2
    assert calls[1] == 0x1234


def test_connection_init_desktop_skips_adb(monkeypatch):
    # 桌面模式：Connection.__init__ 不调 detect_device / adb_connect / detect_package
    conn = object.__new__(Connection)
    conn.config = types.SimpleNamespace(
        script=types.SimpleNamespace(
            device=types.SimpleNamespace(
                serial='desktop',
                handle='4242',
                package_name=types.SimpleNamespace(value='com.netease.onmyoji'),
            ),
        ),
    )
    calls = []
    monkeypatch.setattr(Connection, 'detect_device', lambda self: calls.append('detect_device'))
    monkeypatch.setattr(Connection, 'adb_connect', lambda self, serial: calls.append('adb_connect'))
    monkeypatch.setattr(Connection, 'detect_package', lambda self: calls.append('detect_package'))
    monkeypatch.setattr(Connection, '_precheck_network_emulator_alive',
                        lambda self: calls.append('precheck'))
    # super() 链起点 mock 掉，避免 ConnectionAttr.__init__ 副作用（它依赖真实 Config）
    monkeypatch.setattr(ConnectionAttr, '__init__', lambda self, config: None)
    Connection.__init__(conn, conn.config)
    assert calls == []
    assert conn.package == 'com.netease.onmyoji'


def test_get_orientation_desktop_returns_normal(monkeypatch):
    # 桌面模式：get_orientation 直接返回 0（横屏 Normal），不经过 adb dumpsys
    conn = object.__new__(Connection)
    conn.config = types.SimpleNamespace(
        script=types.SimpleNamespace(
            device=types.SimpleNamespace(serial='desktop'),
        ),
    )
    called = []
    monkeypatch.setattr(Connection, 'adb_shell',
                        lambda self, cmd, *a, **k: called.append(cmd))
    assert Connection.get_orientation(conn) == 0
    assert called == []
    assert conn.orientation == 0
