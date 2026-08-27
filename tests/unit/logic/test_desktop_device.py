# 桌面客户端模式测试：模式判定、Handle 定位、截图裁切、输入投递、Device 旁路
import types
from unittest.mock import MagicMock

import numpy as np
import pytest
import win32con

import module.device.handle as handle_module
from module.device.connection import Connection
from module.device.connection_attr import ConnectionAttr
from module.device.device import Device, EmulatorState
from module.device.handle import Handle, DESKTOP_RESIZE_ATTEMPTS
from module.device.screenshot import Screenshot
from module.exception import (EmulatorNotRunningError, GameNotRunningError,
                              GameStuckError, RequestHumanTakeover)
from module.device.humanize import HumanizerContext
from module.device.humanize.persona import Persona
from module.device.method.windows_impl import Window


def _conn_with_serial(serial):
    conn = object.__new__(ConnectionAttr)
    conn.config = types.SimpleNamespace(
        script=types.SimpleNamespace(device=types.SimpleNamespace(serial=serial))
    )
    return conn


@pytest.fixture(autouse=True)
def _no_real_screen_wake(monkeypatch):
    """屏蔽 desktop_keep_screen_on 的真实系统调用。

    launch_desktop_client 会调它点亮显示器，里面三件事都作用于真实系统：
    向 HWND_BROADCAST 发 SC_MONITORPOWER、模拟 VK_F15 键盘事件、
    SetCursorPos 移动真实鼠标。测试不该动用户的屏幕与鼠标；此前
    SendMessageW 的同步广播还让整个测试进程挂死过 26 分钟
    （已改用 SendMessageTimeoutW，但测试仍应隔离，不依赖那个超时兜底）。
    """
    monkeypatch.setattr(handle_module, 'desktop_keep_screen_on', lambda enable: None)


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


def test_desktop_trace_vertical_does_not_crash():
    # 纯垂直移动（x 相同）：贝塞尔 x 方向参数化退化会产生 NaN 崩溃，应退化为垂直直线轨迹
    w = object.__new__(Window)
    trace = w.desktop_trace((1145, 670), (1145, 601))
    assert trace
    assert all(p[0] == 1145 for p in trace)
    # 终点精确落在目标点，中间点位于起点与终点之间且单调
    assert trace[-1] == [1145, 601]
    ys = [p[1] for p in trace]
    assert all(601 <= y <= 670 for y in ys)
    assert ys == sorted(ys, reverse=True)


def test_desktop_trace_vertical_midpoints_on_line():
    # 长距离纯垂直移动：中间点全在垂直线上且单调递增，不产生 NaN
    w = object.__new__(Window)
    trace = w.desktop_trace((100, 100), (100, 600), interval=10)
    assert len(trace) >= 5
    assert all(p[0] == 100 for p in trace)
    ys = [p[1] for p in trace]
    assert ys == sorted(ys)
    assert trace[-1] == [100, 600]


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


def test_move_desktop_window_message_caps_points(monkeypatch):
    # 跨屏移动的消息数受 DESKTOP_MOVE_MAX_POINTS 限制，耗时不随距离线性增长
    w = _desktop_window()
    w._desktop_cursor = (0, 0)
    calls = []
    monkeypatch.setattr('module.device.method.windows_impl.PostMessage',
                        lambda hwnd, msg, wp, lp: calls.append((msg, lp)))
    w.move_desktop_window_message(1280, 720)
    # 轨迹点被抽稀到上限，再加终点补发一次
    assert len(calls) <= w.DESKTOP_MOVE_MAX_POINTS + 1
    assert w._desktop_cursor == (1280, 720)


def test_desktop_light_move_wall_clock_scales_with_points():
    """桌面 light 移动墙钟最低估算按「距离 → 点数 → 每点请求 → 逐点地板」四步推导
    （Spec §7.3.2）：点数越多估算越高，只有 720px 满载 12 点才走到 35~47ms 上界，
    典型 100~400px 预定位落在 17~35ms——禁止对所有移动套用同一个 35~47ms 区间。

    2.9ms 只是本机最低估算地板，不是所有机器的通用上界；目标机中位数/P95 必须
    用 Spec §11 的校准表单独记录，不能外推到其它机器。
    """
    def estimate(dist):
        # 距离 → 点数：DESKTOP_MOVE_STEP=60 每 60px 一点，DESKTOP_MOVE_MAX_POINTS 截断
        points = min(int(dist / Window.DESKTOP_MOVE_STEP),
                     Window.DESKTOP_MOVE_MAX_POINTS)
        # 每点请求：light 预算公式 _desktop_move_budget_ms（15~30ms 区间）
        budget_ms = min(max(dist * 0.12, 15.0), 30.0)
        per_point_s = budget_ms / points / 1000.0
        # 逐点地板：墙钟最低估算 = sum(max(每点请求, 2.9ms))
        return points, sum(max(per_point_s, 0.0029) for _ in range(points))

    pts_240, est_240 = estimate(240.0)
    pts_720, est_720 = estimate(720.0)
    assert (pts_240, pts_720) == (4, 12), '距离 → 点数推导：240px→4 点、720px→12 点'
    assert est_240 < est_720, '墙钟与点数强相关，不是固定区间'
    assert 0.017 <= est_240 <= 0.035, f'典型预定位应落 17~35ms，实际 {est_240 * 1000:.2f}ms'
    assert 0.034 <= est_720 <= 0.047, f'满载 12 点应落 35~47ms，实际 {est_720 * 1000:.2f}ms'


def test_screenshot_desktop_bitblt_raises_when_minimized(monkeypatch):
    # 窗口最小化且还原失败（客户区仍 0x0）→ 应给出可读错误而非 reshape 崩溃
    w = object.__new__(Window)
    w.is_desktop_window = True
    w.root_handle_num = 0x200
    monkeypatch.setattr(Window, 'desktop_window_restore_if_minimized', lambda self: False)
    monkeypatch.setattr('module.device.method.windows_impl.GetClientRect', lambda h: (0, 0, 0, 0))
    with pytest.raises(RequestHumanTakeover):
        w.screenshot_desktop_bitblt()


def test_screenshot_desktop_bitblt_restores_before_capture(monkeypatch):
    # 截图前会先尝试还原最小化窗口；还原成功后正常截取
    w = object.__new__(Window)
    w.is_desktop_window = True
    w.root_handle_num = 0x200
    w.screenshot_size = (1280, 720)
    restore_calls = []
    monkeypatch.setattr(Window, 'desktop_window_restore_if_minimized',
                        lambda self: restore_calls.append(True) or True)
    fake_save_dc = MagicMock()
    fake_mfc_dc = MagicMock()
    fake_mfc_dc.CreateCompatibleDC.return_value = fake_save_dc
    fake_bitmap = MagicMock()
    fake_bitmap.GetBitmapBits.return_value = b'\x00' * (1280 * 720 * 4)
    fake_bitmap.CreateCompatibleBitmap = lambda dc, w, h: None
    fake_bitmap.GetHandle = lambda: 1
    fake_mfc_dc.CreateCompatibleBitmap.return_value = fake_bitmap
    fake_save_dc.BitBlt = lambda *a, **k: None
    monkeypatch.setattr('module.device.method.windows_impl.GetDC', lambda h: 1)
    monkeypatch.setattr('module.device.method.windows_impl.GetClientRect', lambda h: (0, 0, 1280, 720))
    monkeypatch.setattr('module.device.method.windows_impl.CreateDCFromHandle', lambda dc: fake_mfc_dc)
    monkeypatch.setattr('module.device.method.windows_impl.CreateBitmap', lambda: fake_bitmap)
    monkeypatch.setattr('module.device.method.windows_impl.DeleteObject', lambda h: None)
    img = w.screenshot_desktop_bitblt()
    assert restore_calls == [True]
    assert img.shape == (720, 1280, 3)


class _IntervalTimer:
    def __init__(self):
        self.limit = None


def _interval_screenshot(is_desktop, value, combat_value, method='printwindow'):
    from module.device.screenshot import Screenshot
    s = object.__new__(Screenshot)
    s.is_desktop = is_desktop
    opt = types.SimpleNamespace(screenshot_interval=value, combat_screenshot_interval=combat_value)
    s.config = types.SimpleNamespace(
        script=types.SimpleNamespace(
            optimization=opt,
            device=types.SimpleNamespace(screenshot_method=method),
        ),
        Emulator_ScreenshotMethod=method,
    )
    s._screenshot_interval = _IntervalTimer()
    return s, opt


def test_screenshot_interval_desktop_allows_lower_floor():
    # 桌面 BitBlt 便宜，间隔下限放宽到 0.05 / 战斗 0.1，且不改写用户配置
    from module.device.screenshot import Screenshot
    s, opt = _interval_screenshot(True, 0.05, 0.1)
    Screenshot.screenshot_interval_set(s, None)
    assert s._screenshot_interval.limit == 0.05
    Screenshot.screenshot_interval_set(s, 'combat')
    assert s._screenshot_interval.limit == 0.1
    # 原配置值未被非桌面区间夹取写回
    assert opt.screenshot_interval == 0.05
    assert opt.combat_screenshot_interval == 0.1


def test_screenshot_interval_emulator_unchanged():
    # 非桌面路径行为逐值不变：0.1~0.3 / 战斗 0.3~1.0
    from module.device.screenshot import Screenshot
    s, _ = _interval_screenshot(False, 0.05, 0.05, method='adb')
    Screenshot.screenshot_interval_set(s, None)
    assert s._screenshot_interval.limit == 0.1
    Screenshot.screenshot_interval_set(s, 'combat')
    assert s._screenshot_interval.limit == 0.3


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
    if config is None:
        config = types.SimpleNamespace(
            script=types.SimpleNamespace(
                device=types.SimpleNamespace(
                    serial='desktop', handle='4242', screenshot_method='auto', control_method='minitouch',
                )
            ),
        )

        def startup_normalize(updates):
            # 模拟 session.startup_normalize：只把声明路径写回 device 对象
            for path, value in updates.items():
                setattr(config.script.device, path[-1], value)

        config.startup_normalize = startup_normalize
    dev.config = config
    return dev


def test_init_desktop_forces_methods_and_healthy():
    # 桌面初始化：screenshot=window_background(BitBlt)、control=window_message、状态置 HEALTHY
    dev = _desktop_device()
    dev.config.save = lambda: None
    transitions = []
    dev._transition_to = lambda target: transitions.append(target)
    dev.screenshot_interval_set = lambda: None
    dev._desktop_ensure_launched = lambda: True
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
    dev._desktop_ensure_launched = lambda: True
    dev.desktop_window_set_size = lambda: False
    Device._init_desktop(dev)
    assert dev.config.script.device.screenshot_method == 'window_background'


def test_app_is_running_desktop_requires_login_done():
    # 桌面 app_is_running：窗口存在 且 登录完成才判定运行中（OAS 自动启动的客户端在登录页）
    dev = _desktop_device()
    dev.desktop_window_exists = lambda: True
    dev._desktop_login_done = False
    assert dev.app_is_running() is False
    dev._desktop_login_done = True
    assert dev.app_is_running() is True


def test_app_is_running_desktop_defaults_logged_in():
    # 默认登录态 True：用户已开着的客户端不强制登录
    dev = _desktop_device()
    dev.desktop_window_exists = lambda: True
    assert dev.app_is_running() is True


def test_desktop_mark_logged_out_makes_app_not_running():
    """发现 MPay 后复位登录标记，让任务前置检查直接判定「游戏未运行」。

    运行期客户端掉回 MPay 登录界面时窗口仍在，标记不复位的话 app_is_running() 会一直
    说游戏在跑，任务要白启动一次才在 ui_get_current_page 里发现问题。
    """
    dev = _desktop_device()
    dev.desktop_window_exists = lambda: True
    # 上一次登录成功留下的 True
    dev.desktop_mark_logged_in()
    assert dev.app_is_running() is True
    dev.desktop_mark_logged_out()
    assert dev.app_is_running() is False


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


def test_app_start_desktop_skips_when_window_exists():
    # 窗口在 → app_start 不自动启动，不调用 ADB
    dev = _desktop_device()
    dev.config.script.error = types.SimpleNamespace(handle_error=True)
    dev.desktop_window_exists = lambda: True
    launched = []
    dev.launch_desktop_client = lambda: launched.append(True) or True
    Device.app_start(dev)
    assert launched == []


def test_app_start_desktop_launches_when_window_missing():
    # 窗口缺失 → app_start 自动启动并绑定 PID（空闲关闭后由 Restart 链路拉起）
    dev = _desktop_device()
    dev.config.script.error = types.SimpleNamespace(handle_error=True)
    dev.desktop_window_exists = lambda: False
    launched = []
    dev.launch_desktop_client = lambda: launched.append(True) or True
    Device.app_start(dev)
    assert launched == [True]


def test_app_stop_desktop_calls_stop_client():
    # 桌面 app_stop 走 desktop_stop_client 关闭客户端，不调用 ADB
    dev = _desktop_device()
    dev.config.script.error = types.SimpleNamespace(handle_error=True)
    stopped = []

    def stop():
        stopped.append(True)
        return True

    dev.desktop_stop_client = stop
    Device.app_stop(dev)
    assert stopped == [True]


def test_desktop_ensure_launched_window_present_no_launch():
    # 窗口可用 → 不启动
    dev = _desktop_device()
    dev.desktop_window_exists = lambda: True
    launched = []
    dev.launch_desktop_client = lambda: launched.append(True) or True
    assert dev._desktop_ensure_launched() is True
    assert launched == []


def test_desktop_ensure_launched_stale_pid_restarts():
    # 配置里的 PID 已失效（对应窗口不存在）→ 自动重启客户端并重新绑定
    dev = _desktop_device()  # handle='4242' 已失效
    dev.desktop_window_exists = lambda: False
    launched = []
    dev.launch_desktop_client = lambda: launched.append(True) or True
    assert dev._desktop_ensure_launched() is True
    assert launched == [True]


def test_desktop_ensure_launched_empty_handle_launches():
    # PID 未绑定（handle 空）→ 直接启动，不依赖窗口存在性探测
    dev = _desktop_device()
    dev.config.script.device.handle = ''
    dev.launch_desktop_client = lambda: True
    assert dev._desktop_ensure_launched() is True


def test_desktop_window_set_size_noop_when_match(monkeypatch):
    # 物理客户区已是 1280x720 → 返回 False，不调 SetWindowPos
    handle = object.__new__(Handle)
    handle.is_desktop_window = True
    handle.root_handle_num = 0x200
    monkeypatch.setattr('module.device.handle.IsWindow', lambda h: True)
    monkeypatch.setattr('module.device.handle.GetClientRect', lambda h: (0, 0, 1280, 720))
    called = []
    monkeypatch.setattr('module.device.handle.SetWindowPos', lambda *a: called.append(a))
    # 尺寸本来就匹配属于「已得出结论」，不得被当成句柄失效而进入重绑重试
    def must_not_rebind():
        raise AssertionError('尺寸已匹配时不应重绑窗口')
    handle._desktop_rebind_window = must_not_rebind
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
    monkeypatch.setattr('module.device.handle.IsWindow', lambda h: True)
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
    monkeypatch.setattr('module.device.handle.IsWindow', lambda h: True)
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
    monkeypatch.setattr('module.device.handle.IsWindow', lambda h: True)
    monkeypatch.setattr('module.device.handle.GetWindowLong', lambda h, flag: 0)
    monkeypatch.setattr('module.device.handle._window_total_size', lambda w, h, s, e: (1298, 767))

    def boom(*a):
        raise Exception("(5, 'SetWindowPos', '拒绝访问。')")
    monkeypatch.setattr('module.device.handle.SetWindowPos', boom)
    assert handle.desktop_window_set_size() is False


def test_desktop_window_set_size_invalid_handle_gives_up_after_retries(monkeypatch):
    """句柄始终失效 → 重绑重试耗尽后返回 False，全程不调 GetClientRect、不抛异常。

    回归：客户端被 Close emulator during wait 关掉后 hwnd 立即失效，若直接
    GetClientRect 会抛 (1400, '无效的窗口句柄') 把整个 oas 进程搞崩。
    """
    handle = object.__new__(Handle)
    handle.is_desktop_window = True
    handle.root_handle_num = 0x200
    monkeypatch.setattr('module.device.handle.IsWindow', lambda h: False)
    monkeypatch.setattr('module.device.handle.time.sleep', lambda s: None)

    def must_not_call(*a):
        raise AssertionError('句柄失效时不应调用 GetClientRect')
    monkeypatch.setattr('module.device.handle.GetClientRect', must_not_call)
    rebinds = []
    handle._desktop_rebind_window = lambda: rebinds.append(1) or False

    assert handle.desktop_window_set_size() is False
    # 最后一轮不再重绑（已无重试机会），因此重绑次数比尝试轮数少 1
    assert len(rebinds) == DESKTOP_RESIZE_ATTEMPTS - 1
    # hwnd 为 0（已被 desktop_stop_client 清零）同样安全返回
    handle.root_handle_num = 0
    assert handle.desktop_window_set_size() is False


def test_desktop_window_set_size_rebinds_rebuilt_window(monkeypatch):
    """窗口重建：首轮句柄失效，重绑到新 hwnd 后调整成功。

    回归 (1400, 'GetClientRect')：客户端确认登录弹窗后销毁登录界面、重建游戏主窗口，
    resize 中途旧 hwnd 失效。进程还活着，正确处理是按 PID 重绑而非重拉客户端。
    """
    handle = object.__new__(Handle)
    handle.is_desktop_window = True
    handle.root_handle_num = 0x200
    handle.root_handle = '4242'
    state = {'size': (800, 600)}
    # 只有新 hwnd 有效，旧 hwnd 已随窗口销毁失效
    monkeypatch.setattr('module.device.handle.IsWindow', lambda h: h == 0x300)
    monkeypatch.setattr('module.device.handle.GetClientRect',
                        lambda h: (0, 0, state['size'][0], state['size'][1]))
    monkeypatch.setattr('module.device.handle.GetWindowLong', lambda h, flag: 0)
    monkeypatch.setattr('module.device.handle._window_total_size', lambda w, h, s, e: (1298, 767))
    monkeypatch.setattr('module.device.handle.time.sleep', lambda s: None)
    monkeypatch.setattr('module.device.handle.SetWindowPos',
                        lambda *a: state.update(size=(1280, 720)))
    # 重建后的新窗口
    monkeypatch.setattr(Handle, 'find_desktop_window_by_pid', lambda self, pid: 0x300)
    cleared = []
    handle._desktop_clear_handle_cache = lambda: cleared.append(1)

    assert handle.desktop_window_set_size() is True
    assert handle.root_handle_num == 0x300
    # 换 hwnd 必须同步失效截图句柄缓存
    assert cleared == [1]


def test_desktop_bind_pid_clears_screenshot_handle_cache(monkeypatch):
    """绑定新 PID/HWND 必须失效截图句柄缓存。

    回归：screenshot_handle_num 是 cached_property，桌面模式下返回 root_handle_num。
    重拉客户端后 root_handle_num 已是新 hwnd，缓存仍指向上一个客户端的废句柄，
    截图时 GetClientRect 拿废句柄抛 (1400)。
    """
    handle = object.__new__(Handle)
    handle.config = _handle_config()
    handle.root_handle_num = 0x200
    handle.is_desktop_window = True
    handle._desktop_login_done = True
    # 预热缓存，模拟上一个客户端期间已取过截图句柄
    handle.__dict__['screenshot_handle_num'] = 0x200
    handle.__dict__['screenshot_size'] = (1280, 720)

    handle.desktop_bind_pid(4242, hwnd=0x300)

    assert handle.root_handle_num == 0x300
    assert 'screenshot_handle_num' not in handle.__dict__
    assert 'screenshot_size' not in handle.__dict__
    # 新客户端未登录，必须走 restart 登录流程
    assert handle._desktop_login_done is False


def test_desktop_stop_client_clears_stale_hwnd(monkeypatch):
    """关闭客户端后必须清零 root_handle_num，否则留下失效句柄。

    回归：同一 device 对象生命周期内被唤醒的任务会跳过 Handle.__init__，
    直接拿这个废句柄做 Win32 调用而崩溃。
    """
    w = object.__new__(Window)
    w.config = _handle_config()
    w.root_handle_num = 0x200
    w._desktop_login_done = True
    monkeypatch.setattr('module.device.handle.IsWindow', lambda h: True)
    monkeypatch.setattr('module.device.handle.time.sleep', lambda s: None)
    w.desktop_force_kill = lambda: None
    w._desktop_close_wait_seconds = lambda: 1
    w._desktop_clear_handle_cache = lambda: None
    # 窗口消失 + 进程退出 = 确认释放
    w.desktop_window_exists = lambda: False
    w._desktop_pid_alive = lambda pid: False
    assert w.desktop_stop_client() is True
    assert w.root_handle_num == 0
    assert w._desktop_login_done is False


def test_init_desktop_does_not_relaunch_client():
    """_init_desktop 不负责拉客户端：窗口存在性已由 __init__ 保证。

    启动客户端是 Restart 任务的职责，重拉逻辑只应存在一处。这里若重复确保，
    启动逻辑就又散回了 device 初始化路径。
    """
    dev = _desktop_device()
    dev.config.save = lambda: None
    dev._transition_to = lambda target: None
    dev.screenshot_interval_set = lambda: None
    dev._desktop_ensure_launched = lambda: (_ for _ in ()).throw(
        AssertionError('_init_desktop 不应拉起客户端'))
    resized = []
    dev.desktop_window_set_size = lambda: resized.append(1) or False
    Device._init_desktop(dev)
    assert resized == [1]


def test_screenshot_raises_game_not_running_when_window_missing():
    """截图前窗口缺失 → 抛 GameNotRunningError，交由 Restart 拉客户端。

    回归：script.py 每个任务启动前都调 device.screenshot()。空闲期客户端被
    「Close emulator during wait」关掉后句柄失效，旧代码直接崩在 GetClientRect
    (1400, '无效的窗口句柄')。现在只报告事实，script.py 接住后 task_call('Restart')，
    由 Restart 走 app_start 完成启动客户端 + 等登录弹窗 + 进游戏整套流程。
    """
    dev = _desktop_device()
    dev.stuck_record_check = lambda: None
    dev.desktop_window_exists = lambda: False
    # 截图方法自己绝不拉客户端，也不 resize
    dev._desktop_ensure_launched = lambda: (_ for _ in ()).throw(
        AssertionError('screenshot 不应拉起客户端'))
    dev.desktop_window_set_size = lambda: (_ for _ in ()).throw(
        AssertionError('screenshot 不应调整窗口'))
    with pytest.raises(GameNotRunningError):
        Device.screenshot(dev)


def test_screenshot_skips_relaunch_when_window_valid(monkeypatch):
    """窗口有效时不做任何多余动作，保持原有截图路径。"""
    dev = _desktop_device()
    dev.stuck_record_check = lambda: None
    dev.desktop_window_exists = lambda: True
    dev._desktop_ensure_launched = lambda: (_ for _ in ()).throw(
        AssertionError('窗口有效时不应重拉'))
    dev.handle_night_commission = lambda: False
    dev.image = 'IMG'
    shots = []
    monkeypatch.setattr(Screenshot, 'screenshot', lambda self: shots.append(1))
    assert Device.screenshot(dev) == 'IMG'
    assert shots == [1]


def test_skip_app_check_tasks_skip_startup_screenshot(monkeypatch):
    """SKIP_APP_CHECK_TASKS 的任务必须跳过启动前截图，否则 Restart 起不来。

    回归 bootstrap 死锁：桌面模式客户端未启动时 screenshot 抛 GameNotRunningError，
    而 Restart 正是负责启动客户端的任务。若它也被挡在启动前截图这一步，就永远进不到
    app_start，形成「要启动客户端必须先有客户端」的死锁。
    """
    from script import Script

    script = object.__new__(Script)
    calls = []
    # 客户端未启动：截图必抛，app_is_running 也是 False
    script.device = types.SimpleNamespace(
        screenshot=lambda: calls.append('shot') or (_ for _ in ()).throw(
            GameNotRunningError('Desktop client window not found')),
        app_is_running=lambda: calls.append('check') or False,
    )
    # 在 load_module 处让流程正常收尾（返回 TaskEnd），避免真加载任务模块
    monkeypatch.setattr('script.load_module', lambda name, path: _EndImmediately())
    script.config = types.SimpleNamespace(
        notifier=types.SimpleNamespace(push=lambda **kw: None))
    script._resolve_task_end_name = lambda command, error: command
    script._should_notify_task_end = lambda task_name: False

    assert 'Restart' in Script.SKIP_APP_CHECK_TASKS
    assert Script.run(script, 'Restart') is True
    # 关键断言：既没截图也没做运行检查，直接进到任务
    assert calls == []


class _EndImmediately:
    """测试用任务模块替身：进入即以 TaskEnd 正常收尾。"""

    def ScriptTask(self, *args, **kwargs):
        from module.exception import TaskEnd

        class _T:
            def run(self):
                raise TaskEnd('Restart')
        return _T()


def _restart_task(start_results, exc=None, stop_ok=True):
    """构造一个只填了桌面重建路径所需属性的 Restart ScriptTask。

    start_results: 每轮 app_start+登录的结果，'fail' 表示抛异常。
    exc: 失败时抛的异常类，默认 GameNotRunningError（另一类是登录卡死 GameStuckError）。
    stop_ok: desktop_stop_client 的返回值，False 模拟客户端关不掉。
    """
    from tasks.Restart.script_task import ScriptTask

    task = object.__new__(ScriptTask)
    trace = []
    results = list(start_results)
    error = exc or GameNotRunningError

    def app_start():
        trace.append('start')

    def handle_login():
        outcome = results.pop(0) if results else 'ok'
        if outcome == 'fail':
            raise error('Desktop client window not found')
        trace.append('login')

    def stop_client():
        trace.append('stop')
        return stop_ok

    task.device = types.SimpleNamespace(
        is_desktop=True,
        app_start=app_start,
        desktop_stop_client=stop_client,
    )
    task.app_handle_login = handle_login
    return task, trace


def test_restart_intercepts_start_failure_and_rebuilds():
    """Restart 内部拦下客户端启动失败并重建，不把异常抛给调度器。

    回归无限重启循环：若 GameNotRunningError 抛出去，script.py 接住后
    task_call('Restart') 又打回这里，每轮日志都「正常」却永不收敛。
    """
    task, trace = _restart_task(['fail', 'ok'])
    from tasks.Restart.script_task import ScriptTask

    ScriptTask._desktop_start_and_login(task)
    # 首轮登录失败 → 清残留 → 重建成功，全程不抛
    assert trace == ['start', 'stop', 'start', 'login']


def test_restart_intercepts_login_stuck():
    """登录卡死抛的 GameStuckError 也必须就地消化，不能放跑给调度器。

    回归：script.py 对 GameStuckError 同样是 task_call('Restart')，打回这里
    形成无限重启循环。登录界面卡住是桌面端的常见失败形态，必须和「客户端没起来」
    走同一条重建路径。
    """
    from tasks.Restart.script_task import ScriptTask
    from module.exception import GameStuckError

    task, trace = _restart_task(['fail', 'ok'], exc=GameStuckError)
    ScriptTask._desktop_start_and_login(task)
    assert trace == ['start', 'stop', 'start', 'login']


def test_restart_releases_client_on_final_attempt():
    """连续失败耗尽重建轮数 → RequestHumanTakeover，且最后一轮也必须释放客户端。

    回归客户端泄漏：旧代码在最后一轮 `break` 跳过 desktop_stop_client，紧接着
    RequestHumanTakeover 让进程退出，那个客户端就再没有任何代码知道它的存在
    （实测泄漏过一个 PID）。守护进程重启实例后又会新起一个，客户端越积越多。
    """
    from tasks.Restart.script_task import ScriptTask, DESKTOP_RESTART_ATTEMPTS

    task, trace = _restart_task(['fail'] * DESKTOP_RESTART_ATTEMPTS)
    with pytest.raises(RequestHumanTakeover):
        ScriptTask._desktop_start_and_login(task)
    # 每轮都尝试启动，且每轮失败都清理——包括放弃前的最后一轮
    assert trace.count('start') == DESKTOP_RESTART_ATTEMPTS
    assert trace.count('stop') == DESKTOP_RESTART_ATTEMPTS
    # 放弃前的最后一个动作必须是释放客户端
    assert trace[-1] == 'stop'


def test_restart_executes_login_on_final_attempt():
    """第三轮必须完整调用登录，不能启动后直接判定失败。"""
    from tasks.Restart.script_task import ScriptTask, DESKTOP_RESTART_ATTEMPTS

    task = object.__new__(ScriptTask)
    trace = []

    def app_start():
        trace.append('start')

    def handle_login():
        trace.append('login')
        raise GameStuckError('login timeout')

    def stop_client():
        trace.append('stop')
        return True

    task.device = types.SimpleNamespace(
        is_desktop=True,
        app_start=app_start,
        desktop_stop_client=stop_client,
    )
    task.app_handle_login = handle_login

    with pytest.raises(RequestHumanTakeover):
        ScriptTask._desktop_start_and_login(task)

    # 每一轮都必须按「启动 → 登录 → 清理」完整执行，包含最后一轮。
    assert trace == ['start', 'login', 'stop'] * DESKTOP_RESTART_ATTEMPTS


def test_restart_stops_rebuilding_when_client_cannot_be_closed():
    """客户端关不掉时立刻交人工，不再重建。

    残留窗口会让下一轮的新窗口识别绑到错误句柄，越试越乱；而且每轮重建都新起一个
    客户端，关不掉的那个会一直累积。所以 desktop_stop_client 返回 False 就必须停手。
    """
    from tasks.Restart.script_task import ScriptTask

    task, trace = _restart_task(['fail', 'ok'], stop_ok=False)
    with pytest.raises(RequestHumanTakeover):
        ScriptTask._desktop_start_and_login(task)
    # 首轮失败 → 清理失败 → 立刻放弃，不进第二轮 app_start
    assert trace == ['start', 'stop']


def _stuck_device(window_alive, login_done):
    """构造一个卡死计时器已到期的桌面 Device，用于判活分支测试。"""
    dev = _desktop_device()
    dev.stuck_timer = types.SimpleNamespace(reached=lambda: True, reset=lambda: None)
    dev.stuck_timer_long = types.SimpleNamespace(reached=lambda: True, reset=lambda: None)
    dev.detect_record = {'LOGIN_CHECK'}
    dev.desktop_window_exists = lambda: window_alive
    dev._desktop_login_done = login_done
    return dev


def test_stuck_during_login_reports_stuck_not_died():
    """登录界面卡死时窗口还活着 → 必须报 GameStuckError，不能报「客户端死了」。

    回归误判：app_is_running() 在桌面模式下是「窗口存在 且 已登录」，而登录流程中
    _desktop_login_done 恒为 False，于是判活恒假。实测登录卡满 5 分钟被报成
    Game died，Restart 白杀一个其实还活着的客户端再重开，连续两轮后交人工接管。
    卡死判定只该关心客户端窗口是否还在。
    """
    from module.exception import GameStuckError

    dev = _stuck_device(window_alive=True, login_done=False)
    # 前提确认：此时 app_is_running() 确实是 False，直接用它就会误判
    assert Device.app_is_running(dev) is False
    with pytest.raises(GameStuckError):
        Device.stuck_record_check(dev)


def test_stuck_with_window_gone_reports_game_died():
    """窗口真的没了 → 仍报 GameNotRunningError，保留原有重拉语义。"""
    dev = _stuck_device(window_alive=False, login_done=False)
    with pytest.raises(GameNotRunningError):
        Device.stuck_record_check(dev)


def test_launch_desktop_client_records_launcher_pid_for_cleanup(monkeypatch):
    """启动成功后必须留档本轮拉起的全部 PID，供关闭时清理启动器。

    回归启动器泄漏：onmyoji.exe 是启动器，Popen 得到的是它的 PID（8932），它随后派生
    真正的游戏窗口进程（6816）并自己继续存活、无窗口。OAS 靠枚举游戏窗口只能绑到
    6816，旧代码在启动成功时丢掉了 spawned 集合，于是没有任何地方知道 8932 的存在，
    关闭时它必然残留。
    """
    w = object.__new__(Window)
    w.config = _handle_config(handle='4242')
    monkeypatch.setattr(Window, 'desktop_resolve_install_root', lambda self: 'C:/Games/Onmyoji')
    monkeypatch.setattr(Window, 'desktop_game_exe', lambda self, root: 'C:/Games/Onmyoji/bin/onmyoji.exe')
    # Popen 拿到的是启动器 PID 8932
    monkeypatch.setattr('module.device.handle.subprocess.Popen',
                        lambda *a, **k: types.SimpleNamespace(pid=8932))
    # 枚举到的新窗口属于派生出的窗口进程 6816——与启动器 PID 不同，这是真机行为。
    # 首次采样是启动前快照（before_pids），新窗口须在其后才出现，否则会被当成旧窗口排除
    state = {'n': 0}

    def fake_list():
        state['n'] += 1
        if state['n'] == 1:
            return [{'pid': 4242, 'title': '阴阳师-网易游戏', 'x': 0, 'y': 0}]
        return [{'pid': 4242, 'title': '阴阳师-网易游戏', 'x': 0, 'y': 0},
                {'pid': 6816, 'title': '阴阳师-网易游戏', 'x': 100, 'y': 0, 'hwnd': 0x300}]

    monkeypatch.setattr('module.device.handle.list_desktop_windows', fake_list)
    monkeypatch.setattr('module.device.handle.time.sleep', lambda s: None)
    bound = []
    w.desktop_bind_pid = lambda pid, hwnd=0: bound.append(pid)
    w.desktop_window_exists = lambda: True

    assert w.launch_desktop_client(timeout=5) is True
    # 绑定的是窗口进程
    assert bound == [6816]
    # 但启动器 PID 必须被记下来，否则关闭时无从得知它的存在
    assert 8932 in w._desktop_spawned_pids


def test_desktop_stop_client_kills_spawned_launcher_leftover(monkeypatch):
    """关闭时必须连自己拉起的启动器一起收，不能只杀绑定的窗口进程。

    回归启动器泄漏：onmyoji.exe 是启动器，Popen 起来后它派生真正的游戏窗口进程、
    自己继续存活且没有窗口（实测启动器 8932 派生窗口进程 6816，89 线程 42s CPU、
    主窗口句柄为 0）。OAS 靠枚举游戏窗口只绑到 6816，旧代码杀完 6816 就报
    released——验证本身没错，但验证对象漏了启动器，8932 成了无主残留。
    """
    w = object.__new__(Window)
    w.is_desktop_window = True
    w.root_handle = '6816'
    w.root_handle_num = 0x200
    w.config = _handle_config(handle='6816')
    w._desktop_close_wait_seconds = lambda: 1
    w._desktop_clear_handle_cache = lambda: None
    # 启动成功时留档的两个 PID：启动器 8932 + 绑定的窗口进程 6816
    w._desktop_spawned_pids = {8932, 6816}
    monkeypatch.setattr('module.device.handle.IsWindow', lambda h: True)
    monkeypatch.setattr('module.device.handle.time.sleep', lambda s: None)

    alive = {6816: True, 8932: True}
    w.desktop_window_exists = lambda: alive[6816]
    w._desktop_pid_alive = lambda pid: alive.get(int(pid), False)
    w.desktop_force_kill = lambda: alive.__setitem__(6816, False)
    killed = []

    def kill_pids(pids):
        killed.extend(sorted(pids))
        for p in pids:
            alive[int(p)] = False

    w._desktop_kill_pids = kill_pids

    assert w.desktop_stop_client() is True
    # 只补杀启动器，绑定的窗口进程由主流程负责，不重复杀
    assert killed == [8932]
    assert alive[8932] is False
    # 留档已清空，避免下次关闭去杀早已复用的 PID
    assert w._desktop_spawned_pids == set()


def test_desktop_stop_client_reports_false_when_launcher_survives(monkeypatch):
    """启动器杀不掉 → 返回 False，不能因为窗口进程已退出就报成功。"""
    w = object.__new__(Window)
    w.is_desktop_window = True
    w.root_handle = '6816'
    w.root_handle_num = 0x200
    w.config = _handle_config(handle='6816')
    w._desktop_close_wait_seconds = lambda: 1
    w._desktop_clear_handle_cache = lambda: None
    w._desktop_spawned_pids = {8932}
    monkeypatch.setattr('module.device.handle.IsWindow', lambda h: True)
    monkeypatch.setattr('module.device.handle.time.sleep', lambda s: None)

    alive = {6816: True, 8932: True}
    w.desktop_window_exists = lambda: alive[6816]
    w._desktop_pid_alive = lambda pid: alive.get(int(pid), False)
    w.desktop_force_kill = lambda: alive.__setitem__(6816, False)
    # 启动器杀不掉（权限被拒等）
    w._desktop_kill_pids = lambda pids: None

    assert w.desktop_stop_client() is False


def test_desktop_stop_client_no_spawned_record_is_noop(monkeypatch):
    """没有启动记录时（如接管用户手开的客户端）不做任何补杀。"""
    w = object.__new__(Window)
    w.is_desktop_window = True
    w.root_handle = '6816'
    w.root_handle_num = 0x200
    w.config = _handle_config(handle='6816')
    w._desktop_close_wait_seconds = lambda: 1
    w._desktop_clear_handle_cache = lambda: None
    monkeypatch.setattr('module.device.handle.IsWindow', lambda h: True)
    monkeypatch.setattr('module.device.handle.time.sleep', lambda s: None)
    w.desktop_window_exists = lambda: False
    w._desktop_pid_alive = lambda pid: False
    w.desktop_force_kill = lambda: None
    w._desktop_kill_pids = lambda pids: pytest.fail('无启动记录时不应补杀任何进程')
    assert w.desktop_stop_client() is True


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


def _desktop_input_window(monkeypatch):
    """构造 input_text_desktop 桩：捕获 SendMessage 序列，跳过 sleep。"""
    w = object.__new__(Window)
    w.root_handle_num = 0x200
    w.DESKTOP_CLEAR_BACKSPACE = 20
    calls = []
    monkeypatch.setattr('module.device.method.windows_impl.SendMessage',
                        lambda h, m, wp, lp=0: calls.append((h, m, wp)) or 0)
    monkeypatch.setattr('module.device.method.windows_impl.time.sleep', lambda s: None)
    return w, calls


def test_input_text_desktop_ascii_and_chinese(monkeypatch):
    # 中英文统一走 WM_CHAR：ASCII 与中文码点同路径，不补 WM_KEYDOWN
    w, calls = _desktop_input_window(monkeypatch)
    w.input_text_desktop('测试a')
    assert calls == [
        (0x200, win32con.WM_CHAR, 0x6D4B),  # 测
        (0x200, win32con.WM_CHAR, 0x8BD5),  # 试
        (0x200, win32con.WM_CHAR, 0x61),    # a
    ]


def test_input_text_desktop_clear_sends_backspace(monkeypatch):
    # clear=True 发 DESKTOP_CLEAR_BACKSPACE 次退格，且不产生字符消息
    w, calls = _desktop_input_window(monkeypatch)
    w.input_text_desktop('', clear=True)
    assert len(calls) == 20
    assert all(msg == win32con.WM_CHAR and wp == 0x08 for _, msg, wp in calls)


def test_input_text_desktop_newline_uses_vk_return(monkeypatch):
    # 换行发 VK_RETURN 的 keydown/keyup，其余仍走 WM_CHAR
    w, calls = _desktop_input_window(monkeypatch)
    w.input_text_desktop('\nX')
    assert calls == [
        (0x200, win32con.WM_KEYDOWN, win32con.VK_RETURN),
        (0x200, win32con.WM_KEYUP, win32con.VK_RETURN),
        (0x200, win32con.WM_CHAR, 0x58),  # X
    ]


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


# ---------------- 桌面模式 handle 下拉：窗口枚举 / 候选注入 / 写回解析 ----------------

def _fake_desktop_windows():
    # 模拟两个桌面客户端窗口，位于不同屏幕坐标（多开）
    return [
        {'pid': 27272, 'title': '阴阳师-网易游戏', 'x': 154, 'y': 38},
        {'pid': 31844, 'title': '阴阳师-网易游戏', 'x': 1280, 'y': 0},
    ]


def test_desktop_window_option_formats_display_text():
    # 下拉项文本：PID 打头、后随窗口左上角坐标，供多开时按屏幕位置辨识；
    # 不含窗口标题（多开标题相同无辨识价值，且避免界面出现中文字样）
    from module.device.handle import desktop_window_option
    assert desktop_window_option({'pid': 27272, 'title': '阴阳师-网易游戏', 'x': 154, 'y': 38}) \
        == '27272 (154,38)'


def test_desktop_option2pid_extracts_leading_number():
    # 界面回传的是展示串，落盘前要剥回纯 PID；纯数字文本原样通过
    from module.device.handle import desktop_option2pid
    assert desktop_option2pid('27272 (154,38)') == '27272'
    assert desktop_option2pid('27272 | 阴阳师-网易游戏 (154,38)') == '27272'
    assert desktop_option2pid('27272') == '27272'
    assert desktop_option2pid('') == ''
    assert desktop_option2pid('auto') == ''


def _config_model(serial, handle):
    """构造纯默认 ConfigModel 并设定 serial/handle，不读写磁盘上的用户配置。

    __setattr__ 会触发自动保存，这里连同 save 一并拦掉，避免单测落盘。
    """
    from module.config.config_model import ConfigModel
    config = ConfigModel()
    object.__setattr__(config, 'save', lambda: None)
    config.script.device.serial = serial
    config.script.device.handle = handle
    return config


def test_script_task_desktop_injects_handle_options(monkeypatch):
    # 桌面模式下 handle 变下拉：type=enum、候选含空项(解除绑定)与枚举到的窗口，
    # 且已存 PID 的 value 对齐到同款展示串（否则界面选不中当前项会显示空白）
    monkeypatch.setattr('module.device.handle.list_desktop_windows', _fake_desktop_windows)
    config = _config_model('desktop', '27272')
    item = next(i for i in config.script_task('Script')['device'] if i['name'] == 'handle')
    assert item['type'] == 'enum'
    assert item['enumEnum'][0] == ''
    assert '27272 (154,38)' in item['enumEnum']
    assert '31844 (1280,0)' in item['enumEnum']
    # value 对齐到当前 PID 的展示串
    assert item['value'] == '27272 (154,38)'


def test_script_task_emulator_handle_stays_text(monkeypatch):
    # 模拟器模式下 handle 输出与改动前一致：仍是 string 文本输入，不注入任何候选项
    monkeypatch.setattr('module.device.handle.list_desktop_windows', _fake_desktop_windows)
    config = _config_model('127.0.0.1:16384', 'MuMuPlayer')
    item = next(i for i in config.script_task('Script')['device'] if i['name'] == 'handle')
    assert item['type'] == 'string'
    assert 'enumEnum' not in item


def test_script_task_desktop_keeps_unlisted_current_value(monkeypatch):
    # 已存 PID 此刻不在枚举结果里（客户端重启换了 PID）：原值仍进候选且 value 显示它，
    # 界面不会把配置显示成空白
    monkeypatch.setattr('module.device.handle.list_desktop_windows',
                        lambda: [{'pid': 31844, 'title': '阴阳师-网易游戏', 'x': 0, 'y': 0}])
    config = _config_model('desktop', '27272')
    item = next(i for i in config.script_task('Script')['device'] if i['name'] == 'handle')
    assert item['value'] == '27272'
    assert '27272' in item['enumEnum']
    assert item['value'] in item['enumEnum']


def test_script_task_desktop_enum_failure_falls_back_to_text(monkeypatch):
    # 枚举窗口异常（依赖 win32 只服务展示）时退回文本框，不能拖垮设置页
    monkeypatch.setattr('module.device.handle.list_desktop_windows',
                        lambda: (_ for _ in ()).throw(RuntimeError('win32 unavailable')))
    config = _config_model('desktop', '27272')
    item = next(i for i in config.script_task('Script')['device'] if i['name'] == 'handle')
    assert item['type'] == 'string'
    assert 'enumEnum' not in item


def test_script_set_arg_desktop_handle_stores_pure_pid(store):
    # 写回：桌面模式下选中展示串，落盘必须是纯 PID（Handle 按数字消费）
    store.patch_user_field("oas1", ("script", "device", "serial"), "desktop")
    result = store.patch_user_argument("oas1", "Script", "device", "handle", "27272 (154,38)")
    assert result.success is True
    assert store.load("oas1").canonical["script"]["device"]["handle"] == "27272"


def test_script_set_arg_emulator_handle_keeps_title_text(store):
    # 写回：模拟器模式下 handle 允许窗口标题等非数字文本，不能走桌面解析被剥空
    store.patch_user_field("oas1", ("script", "device", "serial"), "127.0.0.1:16384")
    result = store.patch_user_argument("oas1", "Script", "device", "handle", "MuMuPlayer")
    assert result.success is True
    assert store.load("oas1").canonical["script"]["device"]["handle"] == "MuMuPlayer"


# ---------------- 桌面客户端自动生命周期：窗口存在性 / 启动绑定 / 关闭 / 最小化还原 ----------------

def test_desktop_window_exists_prefers_instance_root_handle(monkeypatch):
    # 实例 root_handle（运行时重新绑定后的新 PID）优先于配置 handle
    handle = object.__new__(Handle)
    handle.config = _handle_config(handle='4242')
    handle.root_handle = '9999'
    called = []
    def fake_find(self, pid):
        called.append(pid)
        return 0x200
    monkeypatch.setattr(Handle, 'find_desktop_window_by_pid', fake_find)
    assert handle.desktop_window_exists() is True
    assert called == ['9999']


def test_desktop_window_exists_falls_back_to_config_handle(monkeypatch):
    # root_handle 未设置（Handle 尚未按 PID 定位）→ 用配置 handle
    handle = object.__new__(Handle)
    handle.config = _handle_config(handle='4242')
    called = []
    def fake_find(self, pid):
        called.append(pid)
        return 0x200
    monkeypatch.setattr(Handle, 'find_desktop_window_by_pid', fake_find)
    assert handle.desktop_window_exists() is True
    assert called == ['4242']


def test_desktop_resolve_install_root_from_config(tmp_path):
    # 配置填安装目录 → 解析成功
    (tmp_path / 'bin').mkdir()
    (tmp_path / 'bin' / 'onmyoji.exe').touch()
    w = object.__new__(Window)
    w.config = types.SimpleNamespace(script=types.SimpleNamespace(
        device=types.SimpleNamespace(desktop_game_path=str(tmp_path), handle='4242')))
    assert w.desktop_resolve_install_root() == str(tmp_path.resolve())


def test_desktop_resolve_install_root_accepts_exe_path(tmp_path):
    # 配置直接填 bin\\onmyoji.exe 完整路径 → 归一到安装目录
    (tmp_path / 'bin').mkdir()
    (tmp_path / 'bin' / 'onmyoji.exe').touch()
    w = object.__new__(Window)
    w.config = types.SimpleNamespace(script=types.SimpleNamespace(
        device=types.SimpleNamespace(desktop_game_path=str(tmp_path / 'bin' / 'onmyoji.exe'), handle='4242')))
    assert w.desktop_resolve_install_root() == str(tmp_path.resolve())


def test_desktop_resolve_install_root_invalid_falls_back(tmp_path, monkeypatch):
    # 配置路径无效 → 回退自动发现
    w = object.__new__(Window)
    w.config = types.SimpleNamespace(script=types.SimpleNamespace(
        device=types.SimpleNamespace(desktop_game_path=str(tmp_path / 'nonexistent'), handle='4242')))
    monkeypatch.setattr(Window, '_desktop_discover_install_root', lambda self: tmp_path.resolve())
    assert w.desktop_resolve_install_root() == str(tmp_path.resolve())


def test_desktop_discover_install_root_programfiles(tmp_path, monkeypatch):
    # 自动发现：%ProgramFiles%\\Onmyoji\\bin\\onmyoji.exe 存在即命中
    root = tmp_path / 'PF' / 'Onmyoji'
    (root / 'bin').mkdir(parents=True)
    (root / 'bin' / 'onmyoji.exe').touch()
    monkeypatch.setenv('ProgramFiles', str(tmp_path / 'PF'))
    monkeypatch.setenv('ProgramFiles(x86)', str(tmp_path / 'PFX86'))
    w = object.__new__(Window)
    assert w._desktop_discover_install_root() == root.resolve()


def test_launch_desktop_client_binds_new_window(monkeypatch):
    # 启动：只认新出现的桌面窗口，连续两次采样确认后绑定 PID/HWND 即成功
    w = object.__new__(Window)
    w.config = _handle_config(handle='4242')
    monkeypatch.setattr(Window, 'desktop_resolve_install_root', lambda self: 'C:/Games/Onmyoji')
    monkeypatch.setattr(Window, 'desktop_game_exe', lambda self, root: 'C:/Games/Onmyoji/bin/onmyoji.exe')
    launched = []
    monkeypatch.setattr('module.device.handle.subprocess.Popen',
                        lambda *a, **k: launched.append(a[0][0]) or types.SimpleNamespace(pid=9999))
    state = {'n': 0}
    def fake_list():
        # 第一次采样时新窗口还没出现，之后出现
        state['n'] += 1
        if state['n'] == 1:
            return [{'pid': 4242, 'title': '阴阳师-网易游戏', 'x': 0, 'y': 0}]
        return [{'pid': 4242, 'title': '阴阳师-网易游戏', 'x': 0, 'y': 0},
                {'pid': 9999, 'title': '阴阳师-网易游戏', 'x': 100, 'y': 0, 'hwnd': 0x300}]
    monkeypatch.setattr('module.device.handle.list_desktop_windows', fake_list)
    monkeypatch.setattr('module.device.handle.time.sleep', lambda s: None)
    bound = []
    w.desktop_bind_pid = lambda pid, hwnd=0: bound.append((pid, hwnd))
    w.desktop_window_exists = lambda: True
    # 启动侧不碰登录弹窗：那是 Restart 登录流程的职责，两处都做是串行叠加的重复工作
    w.find_desktop_login_popup = lambda: (_ for _ in ()).throw(
        AssertionError('启动侧不应探测登录弹窗'))
    w.desktop_confirm_login_popup = lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError('启动侧不应确认登录弹窗'))
    assert w.launch_desktop_client(timeout=5) is True
    assert launched == ['C:/Games/Onmyoji/bin/onmyoji.exe']
    assert bound == [(9999, 0x300)]


def test_launch_desktop_client_no_window_retries_once(monkeypatch):
    # 第 1 轮 timeout 内没等到新窗口 → 清理本轮进程后整轮重跑；第 2 轮出现窗口 → 成功
    w = object.__new__(Window)
    w.config = _handle_config(handle='4242')
    monkeypatch.setattr(Window, 'desktop_resolve_install_root', lambda self: 'C:/Games/Onmyoji')
    monkeypatch.setattr(Window, 'desktop_game_exe', lambda self, root: 'C:/Games/Onmyoji/bin/onmyoji.exe')
    attempt = {'n': 0}
    def fake_popen(*a, **k):
        attempt['n'] += 1
        return types.SimpleNamespace(pid=9000 + attempt['n'])
    monkeypatch.setattr('module.device.handle.subprocess.Popen', fake_popen)
    # 第 1 轮始终没有新窗口（只有基线窗口），第 2 轮出现
    monkeypatch.setattr(
        'module.device.handle.list_desktop_windows',
        lambda: ([{'pid': 4242, 'title': '阴阳师-网易游戏', 'hwnd': 0x100}]
                 if attempt['n'] < 2
                 else [{'pid': 4242, 'title': '阴阳师-网易游戏', 'hwnd': 0x100},
                       {'pid': 9002, 'title': '阴阳师-网易游戏', 'hwnd': 0x302}]))
    monkeypatch.setattr('module.device.handle.time.sleep', lambda s: None)
    # timeout 用完就返回：让 time.time 单调前进以跳出等待循环
    clock = {'t': 0.0}
    monkeypatch.setattr('module.device.handle.time.time',
                        lambda: clock.__setitem__('t', clock['t'] + 1.0) or clock['t'])
    bound = []
    w.desktop_bind_pid = lambda pid, hwnd=0: bound.append(pid)
    w.desktop_window_exists = lambda: True
    killed = []
    w._desktop_kill_pids = lambda pids: killed.append(set(pids))
    assert w.launch_desktop_client(timeout=5) is True
    # 恰好启动两轮，且第 1 轮失败后清理了本轮启动的进程
    assert attempt['n'] == 2
    assert len(killed) == 1
    assert 9001 in killed[0]


def test_launch_desktop_client_two_rounds_fail_returns_false(monkeypatch):
    # 两轮都等不到新窗口 → 返回 False（上层据此走 RequestHumanTakeover），且不再第三轮
    w = object.__new__(Window)
    w.config = _handle_config(handle='4242')
    monkeypatch.setattr(Window, 'desktop_resolve_install_root', lambda self: 'C:/Games/Onmyoji')
    monkeypatch.setattr(Window, 'desktop_game_exe', lambda self, root: 'C:/Games/Onmyoji/bin/onmyoji.exe')
    attempt = {'n': 0}
    def fake_popen(*a, **k):
        attempt['n'] += 1
        return types.SimpleNamespace(pid=9000 + attempt['n'])
    monkeypatch.setattr('module.device.handle.subprocess.Popen', fake_popen)
    # 始终只有基线窗口，没有新窗口出现
    monkeypatch.setattr('module.device.handle.list_desktop_windows',
                        lambda: [{'pid': 4242, 'title': '阴阳师-网易游戏', 'hwnd': 0x100}])
    monkeypatch.setattr('module.device.handle.time.sleep', lambda s: None)
    clock = {'t': 0.0}
    monkeypatch.setattr('module.device.handle.time.time',
                        lambda: clock.__setitem__('t', clock['t'] + 1.0) or clock['t'])
    w.desktop_bind_pid = lambda pid, hwnd=0: None
    w.desktop_window_exists = lambda: True
    w._desktop_kill_pids = lambda pids: None
    assert w.launch_desktop_client(timeout=3) is False
    # 重试恰好一次，不做第三轮
    assert attempt['n'] == 2


def test_app_start_resizes_window_before_login():
    """app_start 必须在返回前把窗口校准到 1280x720。

    登录流程（app_handle_login）的 OCR 与点击都基于 1280x720 坐标，窗口未校准时
    坐标全错。运行期重拉客户端时 Device 对象是复用的、_init_desktop 不会再跑，
    所以校准不能只挂在初始化路径上，必须挂在拉起客户端的这个唯一入口。
    """
    dev = _desktop_device()
    order = []
    dev._desktop_ensure_launched = lambda: order.append('launch') or True
    dev.desktop_window_set_size = lambda: order.append('resize') or True
    Device.app_start(dev)
    # 先确保客户端在跑，再校准尺寸；登录流程由调用方在此之后进行
    assert order == ['launch', 'resize']


def test_app_start_raises_when_launch_fails():
    """客户端拉不起来 → 抛 GameNotRunningError，不吞掉失败继续登录。

    原先只记 error 就返回，登录流程会对着不存在的窗口空转。现在交由 Restart 的
    重建轮次处理（_desktop_start_and_login）。
    """
    dev = _desktop_device()
    dev._desktop_ensure_launched = lambda: False
    dev.desktop_window_set_size = lambda: (_ for _ in ()).throw(
        AssertionError('客户端拉不起来时不应校准窗口'))
    with pytest.raises(GameNotRunningError):
        Device.app_start(dev)


def test_desktop_bind_pid_init_uses_startup_normalize():
    # 设备初始化阶段：走 startup_normalize 持久化（同步 provisional 快照）
    w = object.__new__(Window)
    w.root_handle = ''
    w.root_handle_num = 0
    w.is_desktop_window = False
    w.config = _handle_config(handle='')
    calls = []
    w.config.startup_normalize = lambda updates: calls.append(updates)
    w.desktop_bind_pid('1234', 0x100)
    assert calls == [{("script", "device", "handle"): '1234'}]
    assert w.root_handle == '1234'
    assert w.root_handle_num == 0x100
    assert w.is_desktop_window is True
    # 新绑定的客户端刚启动，登录态必须重置为 False（需先走 restart 登录流程）
    assert w._desktop_login_done is False


def test_desktop_bind_pid_runtime_falls_back_to_save():
    # 运行期（快照已冻结，startup_normalize 抛 RuntimeError）→ 写运行模型并 save()
    w = object.__new__(Window)
    w.root_handle = 'old'
    w.root_handle_num = 0
    w.is_desktop_window = False
    w.config = _handle_config(handle='old')
    w.config.startup_normalize = lambda updates: (_ for _ in ()).throw(RuntimeError('frozen'))
    saved = []
    w.config.save = lambda: saved.append(True)
    w.desktop_bind_pid('9999', 0x300)
    assert w.root_handle == '9999'
    assert w.root_handle_num == 0x300
    assert w.is_desktop_window is True
    assert w.config.script.device.handle == '9999'
    assert saved == [True]


def _desktop_restore_stub(monkeypatch):
    """构造 desktop_window_restore_if_minimized 桩：屏蔽真实 win32，控制 IsIconic/GetClientRect。"""
    w = object.__new__(Window)
    w.is_desktop_window = True
    w.root_handle_num = 0x200
    w.desktop_window_set_size = lambda: False
    monkeypatch.setattr('module.device.handle.time.sleep', lambda s: None)
    return w


def test_desktop_restore_noop_when_not_iconic(monkeypatch):
    # 非最小化 → 不还原，不调 ShowWindow
    w = _desktop_restore_stub(monkeypatch)
    monkeypatch.setattr('module.device.handle.IsIconic', lambda h: False)
    calls = []
    monkeypatch.setattr('module.device.handle.ShowWindow', lambda h, cmd: calls.append(cmd))
    assert w.desktop_window_restore_if_minimized() is False
    assert calls == []


def test_desktop_restore_when_iconic(monkeypatch):
    # 最小化 → ShowWindow(SW_RESTORE)，客户区恢复后返回 True 并重校准尺寸
    w = _desktop_restore_stub(monkeypatch)
    monkeypatch.setattr('module.device.handle.IsIconic', lambda h: True)
    poll = {'n': 0}
    def fake_client_rect(h):
        poll['n'] += 1
        return (0, 0, 0, 0) if poll['n'] == 1 else (0, 0, 1280, 720)
    monkeypatch.setattr('module.device.handle.GetClientRect', fake_client_rect)
    calls = []
    monkeypatch.setattr('module.device.handle.ShowWindow', lambda h, cmd: calls.append(cmd))
    calibrated = []
    w.desktop_window_set_size = lambda: calibrated.append(True) or True
    assert w.desktop_window_restore_if_minimized() is True
    assert calls == [win32con.SW_RESTORE]
    assert calibrated == [True]


def test_desktop_restore_returns_false_when_still_minimized(monkeypatch):
    # 还原后客户区仍 0x0（客户端未恢复）→ 返回 False，不重校准
    w = _desktop_restore_stub(monkeypatch)
    monkeypatch.setattr('module.device.handle.IsIconic', lambda h: True)
    monkeypatch.setattr('module.device.handle.GetClientRect', lambda h: (0, 0, 0, 0))
    calls = []
    monkeypatch.setattr('module.device.handle.ShowWindow', lambda h, cmd: calls.append(cmd))
    w.desktop_window_set_size = lambda: (_ for _ in ()).throw(AssertionError('不应重校准'))
    assert w.desktop_window_restore_if_minimized(wait=0.2) is False
    assert calls == [win32con.SW_RESTORE]


def test_desktop_stop_client_force_kills_directly(monkeypatch):
    # 关闭客户端：不发任何窗口消息（退出确认框回车无效），直接强杀，确认释放即返回
    w = object.__new__(Window)
    w.is_desktop_window = True
    w.root_handle_num = 0x200
    w.config = _handle_config()
    w._desktop_close_wait_seconds = lambda: 10
    w._desktop_clear_handle_cache = lambda: None
    monkeypatch.setattr('module.device.handle.IsWindow', lambda h: True)
    monkeypatch.setattr('module.device.handle.PostMessage',
                        lambda *a: (_ for _ in ()).throw(AssertionError('关闭客户端不应发窗口消息')))
    monkeypatch.setattr('module.device.handle.SendMessage',
                        lambda *a: (_ for _ in ()).throw(AssertionError('关闭客户端不应发回车')))
    w.desktop_window_exists = lambda: False
    w._desktop_pid_alive = lambda pid: False
    killed = []
    w.desktop_force_kill = lambda: killed.append(True)
    assert w.desktop_stop_client() is True
    # 一轮就确认释放，不该重复强杀
    assert killed == [True]
    # 关闭客户端后登录态重置，下次启动需重新走登录流程
    assert w._desktop_login_done is False


def test_desktop_stop_client_skips_when_window_gone(monkeypatch):
    # 窗口已不存在 且 进程已退出 → 直接跳过，不强杀
    w = object.__new__(Window)
    w.is_desktop_window = True
    w.root_handle_num = 0
    w.config = _handle_config()
    w._desktop_clear_handle_cache = lambda: None
    w._desktop_pid_alive = lambda pid: False
    killed = []
    w.desktop_force_kill = lambda: killed.append(True)
    assert w.desktop_stop_client() is True
    assert killed == []


def test_desktop_stop_client_kills_when_window_gone_but_process_alive(monkeypatch):
    """窗口没了但进程还活着 → 必须照样强杀，不能当成已关闭。

    回归假关闭：旧代码只看 `IsWindow(root_handle_num)` 就决定跳过，而强杀被拒
    （实测本机出现过 (5, '拒绝访问。')）时窗口可能已销毁、进程仍在跑，于是
    「skip stop」直接放过一个残留进程。
    """
    w = object.__new__(Window)
    w.is_desktop_window = True
    w.root_handle_num = 0
    w.config = _handle_config()
    w._desktop_close_wait_seconds = lambda: 1
    w._desktop_clear_handle_cache = lambda: None
    monkeypatch.setattr('module.device.handle.time.sleep', lambda s: None)
    w.desktop_window_exists = lambda: False
    alive = [True]
    killed = []

    def force_kill():
        killed.append(True)
        alive[0] = False

    w.desktop_force_kill = force_kill
    w._desktop_pid_alive = lambda pid: alive[0]
    assert w.desktop_stop_client() is True
    assert killed == [True]


def test_desktop_stop_client_retries_then_reports_failure(monkeypatch):
    """强杀后仍未释放 → 用尽轮数并返回 False，把失败如实报给调用方。

    回归「发完 kill 就当成功」：旧代码只 logger.warning 后返回 None，调用方无从得知
    客户端其实还在，接着去重建就会撞上残留窗口、绑错句柄。
    """
    from module.device.handle import DESKTOP_KILL_ATTEMPTS

    w = object.__new__(Window)
    w.is_desktop_window = True
    w.root_handle_num = 0x200
    w.config = _handle_config()
    w._desktop_close_wait_seconds = lambda: 1
    w._desktop_clear_handle_cache = lambda: None
    monkeypatch.setattr('module.device.handle.IsWindow', lambda h: True)
    monkeypatch.setattr('module.device.handle.time.sleep', lambda s: None)
    # 杀不掉：窗口和进程都一直在
    w.desktop_window_exists = lambda: True
    w._desktop_pid_alive = lambda pid: True
    killed = []
    w.desktop_force_kill = lambda: killed.append(True)
    assert w.desktop_stop_client() is False
    # 每轮都真的重试过强杀，不是只试一次
    assert len(killed) == DESKTOP_KILL_ATTEMPTS
    # 句柄仍被清零：它已不可信，留着会让后续 Win32 调用崩在废句柄上
    assert w.root_handle_num == 0


def test_desktop_pid_alive_uses_exit_code(monkeypatch):
    """进程存活判定必须查退出码，不能只看 OpenProcess 是否成功。

    已退出的进程只要还有内核对象引用，OpenProcess 照样返回句柄；只看它会把
    已退出的进程误判为存活，导致关闭流程白等满 close_game_wait_duration。
    """
    w = object.__new__(Window)
    calls = []

    class _K:
        @staticmethod
        def OpenProcess(access, inherit, pid):
            calls.append(('open', access, pid))
            return 0x99

        @staticmethod
        def GetExitCodeProcess(handle, out_ref):
            out_ref._obj.value = exit_code[0]
            return 1

        @staticmethod
        def CloseHandle(handle):
            calls.append(('close', handle))
            return 1

    monkeypatch.setattr('ctypes.windll.kernel32', _K, raising=False)
    exit_code = [259]  # STILL_ACTIVE
    assert w._desktop_pid_alive(1234) is True
    exit_code = [0]  # 已正常退出
    assert w._desktop_pid_alive(1234) is False
    # 句柄必须归还，否则每次判活泄漏一个内核句柄
    assert [c for c in calls if c[0] == 'close'] == [('close', 0x99), ('close', 0x99)]


def _popup_handle(monkeypatch, windows, pid=4242):
    """构造带 MPay 弹窗枚举桩的 Handle：windows 为 [(hwnd, class, visible, pid)]。"""
    w = object.__new__(Window)
    w.is_desktop_window = True
    w.root_handle = str(pid)
    w.config = _handle_config(handle=str(pid))
    monkeypatch.setattr('module.device.handle.EnumWindows',
                        lambda cb, lst: [lst.append(h[0]) for h in windows])
    monkeypatch.setattr('module.device.handle.GetClassName',
                        lambda h: dict((x[0], x[1]) for x in windows)[h])
    monkeypatch.setattr('module.device.handle.IsWindowVisible',
                        lambda h: dict((x[0], x[2]) for x in windows)[h])
    monkeypatch.setattr('module.device.handle.GetWindowThreadProcessId',
                        lambda h: (0, dict((x[0], x[3]) for x in windows)[h]))
    return w


def test_find_desktop_login_popup_matches_class_and_pid(monkeypatch):
    # 按类名 MPAY_LOGIN + 同 PID 定位弹窗（主窗口/适龄提示/其他实例的弹窗都不能命中）
    windows = [
        (0x100, 'Win32Window', True, 4242),
        (0x200, 'MPAY_AGE_TIPS', True, 4242),
        (0x300, 'MPAY_LOGIN', True, 9999),  # 其他实例的弹窗
        (0x400, 'MPAY_LOGIN', True, 4242),
    ]
    w = _popup_handle(monkeypatch, windows)
    assert w.find_desktop_login_popup() == 0x400


def test_find_desktop_login_popup_keeps_invisible_window(monkeypatch):
    # owner 弹窗可能在桌面合成重绘期间短暂不可见，只要 HWND 存活就仍算未登录
    windows = [(0x400, 'MPAY_LOGIN', False, 4242)]
    w = _popup_handle(monkeypatch, windows)
    assert w.find_desktop_login_popup() == 0x400


def test_find_desktop_login_popup_none_when_absent(monkeypatch):
    # 没有弹窗 → 返回 0
    windows = [(0x100, 'Win32Window', True, 4242)]
    w = _popup_handle(monkeypatch, windows)
    assert w.find_desktop_login_popup() == 0


def test_desktop_confirm_login_popup_sends_enter_until_gone(monkeypatch):
    # 发现弹窗 → 发回车（鼠标消息对 DirectUI 弹窗无效）→ 弹窗消失返回 True
    import time as _time
    _real_sleep = _time.sleep
    w = object.__new__(Window)
    keys = []
    monkeypatch.setattr('module.device.handle.SendMessage',
                        lambda hwnd, msg, wp, lp: keys.append((hwnd, msg, wp)))
    monkeypatch.setattr('module.device.handle.time.sleep', lambda s: _real_sleep(0.01))
    found = [0x400, 0x400, 0]  # 第一次探测有、发回车后再探测仍有、第三次消失
    w.find_desktop_login_popup = lambda: found.pop(0) if found else 0
    assert w.desktop_confirm_login_popup(wait=5) is True
    assert [msg for _, msg, _ in keys] == [win32con.WM_KEYDOWN, win32con.WM_KEYUP]
    assert all(hwnd == 0x400 for hwnd, _, _ in keys)


def test_desktop_confirm_login_popup_no_popup_returns_false(monkeypatch):
    # 没有弹窗 → 不发任何消息，返回 False（调用方无需重新截图）
    w = object.__new__(Window)
    monkeypatch.setattr('module.device.handle.SendMessage',
                        lambda *a: (_ for _ in ()).throw(AssertionError('无弹窗不应发消息')))
    w.find_desktop_login_popup = lambda: 0
    assert w.desktop_confirm_login_popup() is False


def test_desktop_confirm_login_popup_timeout_returns_false(monkeypatch):
    # 回车后弹窗始终不消失 → 超时返回 False，不能把仍存活的 HWND 谎报为已关闭
    import time as _time
    _real_sleep = _time.sleep
    w = object.__new__(Window)
    monkeypatch.setattr('module.device.handle.SendMessage', lambda *a: None)
    monkeypatch.setattr('module.device.handle.time.sleep', lambda s: _real_sleep(0.01))
    w.find_desktop_login_popup = lambda: 0x400
    assert w.desktop_confirm_login_popup(wait=0.1) is False


def test_desktop_send_enter_ignores_dead_window(monkeypatch):
    # 回车发送过程中窗口被销毁（回车已生效）→ 吞掉异常，不影响后续流程
    w = object.__new__(Window)
    monkeypatch.setattr('module.device.handle.SendMessage',
                        lambda *a: (_ for _ in ()).throw(Exception('invalid window handle')))
    w._desktop_send_enter(0x400)


def test_desktop_pid_prefers_instance_root_handle():
    # PID 优先取实例 root_handle（运行期重新绑定的新 PID），未设置时回退配置
    w = object.__new__(Window)
    w.config = _handle_config(handle='4242')
    w.root_handle = '9999'
    assert w.desktop_pid() == 9999
    w2 = object.__new__(Window)
    w2.config = _handle_config(handle='4242')
    assert w2.desktop_pid() == 4242
    w3 = object.__new__(Window)
    w3.config = _handle_config(handle='not-a-pid')
    assert w3.desktop_pid() is None


def test_desktop_close_wait_seconds_reads_config():
    # 关闭游戏等待时长换算成秒（时*3600+分*60+秒）
    w = object.__new__(Window)
    opt = types.SimpleNamespace(close_game_wait_duration=types.SimpleNamespace(hour=0, minute=10, second=0))
    w.config = types.SimpleNamespace(script=types.SimpleNamespace(optimization=opt))
    assert w._desktop_close_wait_seconds() == 600


def test_desktop_force_kill_calls_terminate_process(monkeypatch):
    # 强杀：OpenProcess + TerminateProcess + CloseHandle
    w = object.__new__(Window)
    w.root_handle = '9999'
    kernel = MagicMock()
    kernel.OpenProcess.return_value = 0xABC
    fake_windll = MagicMock()
    fake_windll.kernel32 = kernel
    monkeypatch.setattr('module.device.handle.ctypes.windll', fake_windll)
    w.desktop_force_kill()
    kernel.OpenProcess.assert_called_once_with(0x0001, False, 9999)
    kernel.TerminateProcess.assert_called_once_with(0xABC, 0)
    kernel.CloseHandle.assert_called_once_with(0xABC)


def test_swipe_adb_desktop_routes_to_window_message():
    # 桌面模式无 adb 设备：任务直连 swipe_adb 也改走窗口消息路径（不调 adb_shell）
    dev = _desktop_device()
    swiped = []
    dev.swipe_window_message = lambda p1, p2: swiped.append((p1, p2))
    dev.adb_shell = lambda *a: (_ for _ in ()).throw(AssertionError('桌面模式不应调用 adb'))
    dev.swipe_adb((537, 527), (537, 167), duration=1.0)
    assert swiped == [((537, 527), (537, 167))]


def test_swipe_adb_emulator_keeps_adb_shell():
    # 模拟器路径字节不变：仍走 adb_shell input swipe
    dev = object.__new__(Device)
    dev.config = types.SimpleNamespace(
        script=types.SimpleNamespace(device=types.SimpleNamespace(serial='127.0.0.1:16384')))
    calls = []
    dev.adb_shell = lambda *a: calls.append(a)
    dev.swipe_adb((10, 20), (30, 40), duration=0.1)
    assert calls == [(['input', 'swipe', 10, 20, 30, 40, 100],)]


def test_desktop_mark_logged_in_sets_flag():
    # 登录成功后标记登录态，app_is_running 转 True
    dev = _desktop_device()
    dev.desktop_window_exists = lambda: True
    dev._desktop_login_done = False
    dev.desktop_mark_logged_in()
    assert dev._desktop_login_done is True
    assert dev.app_is_running() is True


def test_app_restart_desktop_skips_stop():
    # 桌面分支：客户端可能刚被 OAS 自动启动（已在登录页），不做 app_stop 避免白关一次
    from tasks.Restart.script_task import ScriptTask
    t = object.__new__(ScriptTask)
    t.device = _desktop_device()
    stopped = []
    t.device.app_stop = lambda: stopped.append(True)
    started = []
    t.device.app_start = lambda: started.append(True)
    t.app_handle_login = lambda: None
    t.set_next_run = lambda **kw: None
    t.config = types.SimpleNamespace(
        restart=types.SimpleNamespace(harvest_config=types.SimpleNamespace(enable_ap=False)))
    ScriptTask.app_restart(t)
    assert stopped == []
    assert started == [True]


def test_app_restart_emulator_stops_first():
    # 模拟器流程不变：app_restart 仍先停后开
    from tasks.Restart.script_task import ScriptTask
    t = object.__new__(ScriptTask)
    dev = object.__new__(Device)
    dev.config = types.SimpleNamespace(
        script=types.SimpleNamespace(device=types.SimpleNamespace(serial='127.0.0.1:16384')))
    t.device = dev
    stopped = []
    t.device.app_stop = lambda: stopped.append(True)
    started = []
    t.device.app_start = lambda: started.append(True)
    t.app_handle_login = lambda: None
    t.set_next_run = lambda **kw: None
    t.config = types.SimpleNamespace(
        restart=types.SimpleNamespace(harvest_config=types.SimpleNamespace(enable_ap=False)))
    ScriptTask.app_restart(t)
    assert stopped == [True]
    assert started == [True]


def test_app_handle_login_marks_desktop_logged_in():
    # 桌面登录成功后标记登录态，使 app_is_running 判定为已在游戏中
    from tasks.Restart.login import LoginHandler
    t = object.__new__(LoginHandler)
    t.device = _desktop_device()
    t.device.stuck_record_clear = lambda: None
    t.device.click_record_clear = lambda: None
    t.device._desktop_login_done = False
    t._app_handle_login = lambda: True
    t.config = types.SimpleNamespace(
        restart=types.SimpleNamespace(harvest_config=types.SimpleNamespace(enable=False)))
    assert t.app_handle_login() is True
    assert t.device._desktop_login_done is True


def test_app_handle_login_emulator_not_marked():
    # 模拟器登录不触发桌面登录态标记
    from tasks.Restart.login import LoginHandler
    t = object.__new__(LoginHandler)
    dev = object.__new__(Device)
    dev.config = types.SimpleNamespace(
        script=types.SimpleNamespace(device=types.SimpleNamespace(serial='127.0.0.1:16384')))
    dev.stuck_record_clear = lambda: None
    dev.click_record_clear = lambda: None
    t.device = dev
    t._app_handle_login = lambda: True
    t.config = types.SimpleNamespace(
        restart=types.SimpleNamespace(harvest_config=types.SimpleNamespace(enable=False)))
    assert t.app_handle_login() is True
    # 默认登录态仍为 True（类属性），未被桌面标记逻辑改动
    assert dev._desktop_login_done is True


class _AlwaysReachedTimer:
    """登录循环用的 Timer 桩：所有计时器立即到点，让循环一轮内跑完判定"""

    def __init__(self, limit, count=0):
        pass

    def start(self):
        return self

    def started(self):
        # 恒为「已启动」，配合 reached 恒真让庭院二次确认在一轮内通过
        return True

    def reached(self):
        return True

    def reset(self):
        return self

    def clear(self):
        return self


class _TickTimer:
    """按调用次数驱动的 Timer 桩，语义与真实 Timer 一致但不依赖真实时间。

    start 之后 reached() 被调用满 TICKS 次才算到点，用来在测试里精确控制
    「庭院二次确认」隔了几轮才通过，避免 sleep 让测试变慢变脆。
    """
    TICKS = 2

    def __init__(self, limit, count=0):
        self.limit = limit
        self._started = False
        self._calls = 0

    def start(self):
        if not self._started:
            self._started = True
            self._calls = 0
        return self

    def started(self):
        return self._started

    def reached(self):
        self._calls += 1
        return self._started and self._calls > self.TICKS

    def reset(self):
        self._calls = 0
        return self

    def clear(self):
        self._started = False
        self._calls = 0
        return self


def _login_handler_for_popup(monkeypatch, device):
    """构造 LoginHandler 桩：只让"式神录按钮出现"判定为真，一轮循环即跳出。"""
    from tasks.Restart.login import LoginHandler
    monkeypatch.setattr('tasks.Restart.login.Timer', _AlwaysReachedTimer)
    t = object.__new__(LoginHandler)
    t.device = device
    t.device.stuck_record_add = lambda name: None
    t.device.get_orientation = lambda: None
    t.screenshot = lambda: None
    t.skip_onmyoji_genie = True
    # 除"式神录按钮出现"外的图像判定统一为假，让循环只走弹窗分支与跳出判定
    t.appear_then_click = lambda *a, **kw: False
    t.click = lambda *a, **kw: False
    t.ocr_appear = lambda *a, **kw: False
    t.ocr_appear_click = lambda *a, **kw: False
    t.appear = lambda rule, **kw: rule is LoginHandler.I_MAIN_GOTO_SHIKIGAMI_RECORDS
    return t


def test_app_handle_login_desktop_confirms_popup_before_loop(monkeypatch):
    # 桌面分支：登录循环每轮都检查 MPay，关闭后重新开始本轮识别
    dev = _desktop_device()
    popup_states = [0x400, 0x400, 0, 0]
    calls = []

    def confirm(*a, **kw):
        calls.append(True)
        return True

    dev.find_desktop_login_popup = lambda: popup_states.pop(0) if popup_states else 0
    dev.desktop_confirm_login_popup = confirm
    t = _login_handler_for_popup(monkeypatch, dev)
    t._app_handle_login()
    # 前两轮均检测到弹窗并发送回车，第三轮才进入正常页面识别
    assert len(calls) == 2


def test_app_handle_login_popup_close_failure_stops_login(monkeypatch):
    # MPay 回车后仍存活时必须中止登录，交给 Restart 清理客户端后重建
    dev = _desktop_device()
    dev.find_desktop_login_popup = lambda: 0x400
    dev.desktop_confirm_login_popup = lambda *a, **kw: False
    t = _login_handler_for_popup(monkeypatch, dev)

    with pytest.raises(GameStuckError, match='MPay login popup cannot be closed'):
        t._app_handle_login()


def test_app_handle_login_popup_reappear_is_bounded(monkeypatch):
    """MPay 每次都关得掉却反复重弹时必须封顶，否则登录循环会永久挂住。

    弹窗分支 continue 回循环顶部，绕过了 self.screenshot() 里的 stuck_record_check，
    所以这条路径没有超时兜底，只能靠自带的次数上限抛异常交给 Restart 重建客户端。
    """
    from tasks.Restart.login import DESKTOP_MPAY_CLOSE_LIMIT

    dev = _desktop_device()
    calls = []
    # 弹窗每次都在，且每次都"成功关闭"——模拟关掉又立刻重弹
    dev.find_desktop_login_popup = lambda: 0x400

    def confirm(*a, **kw):
        calls.append(True)
        return True

    dev.desktop_confirm_login_popup = confirm
    t = _login_handler_for_popup(monkeypatch, dev)

    with pytest.raises(GameStuckError, match='reappeared more than'):
        t._app_handle_login()
    # 上限内的每一次都真的尝试过回车，超出后才抛异常（不再多发一次）
    assert len(calls) == DESKTOP_MPAY_CLOSE_LIMIT


def test_app_handle_login_emulator_never_touches_popup(monkeypatch):
    # 模拟器流程不受影响：完全不调用桌面弹窗确认
    dev = object.__new__(Device)
    dev.config = types.SimpleNamespace(
        script=types.SimpleNamespace(device=types.SimpleNamespace(serial='127.0.0.1:16384')))
    dev.desktop_confirm_login_popup = lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError('模拟器流程不应确认桌面弹窗'))
    t = _login_handler_for_popup(monkeypatch, dev)
    t._app_handle_login()


def test_login_popup_handles_and_returns_immediately():
    """识别到弹窗就点掉并立即返回，不继续往下探测其它弹窗。

    一轮只处理一个弹窗，调用方随即重新截图再判——弹窗是逐个弹出的，拿同一张旧截图接着点
    下一个，只会点到已经变了的画面上。
    """
    from tasks.Restart.login import LoginHandler
    t = object.__new__(LoginHandler)
    t.skip_onmyoji_genie = True
    clicked = []
    # 第一个分支（抵扣券）就命中
    t.appear_then_click = lambda rule, **kw: clicked.append(rule.name) or True
    assert t._login_popup_blocking() is True
    assert clicked == [LoginHandler.I_OFF_TICKET.name], f'命中后应立即返回: {clicked}'


def test_login_popup_returns_false_when_nothing_present():
    # 一个弹窗都没有才返回 False，让调用方去判庭院
    from tasks.Restart.login import LoginHandler
    t = object.__new__(LoginHandler)
    t.skip_onmyoji_genie = True
    t.appear_then_click = lambda *a, **kw: False
    assert t._login_popup_blocking() is False


def test_login_popup_respects_skip_genie():
    # 阴阳师精灵提示在 skip 时不该被点掉，那是用户主动要求保留的提示
    from tasks.Restart.login import LoginHandler
    t = object.__new__(LoginHandler)
    seen = []
    t.appear_then_click = lambda rule, **kw: seen.append(rule.name) or False
    t.skip_onmyoji_genie = True
    t._login_popup_blocking()
    assert LoginHandler.I_LOGIN_LOGIN_ONMYOJI_GENIE.name not in seen
    seen.clear()
    t.skip_onmyoji_genie = False
    t._login_popup_blocking()
    assert LoginHandler.I_LOGIN_LOGIN_ONMYOJI_GENIE.name in seen


def test_courtyard_confirm_delay_exceeds_popup_intervals():
    """庭院确认延迟必须长于所有弹窗分支的 interval。

    弹窗分支的 interval 是点击节流，静默期内 appear 在模板匹配之前就返回 False，那些帧会
    放行庭院判定。只要确认延迟比最大 interval 长，弹窗真还在的话计时必然在走满之前被下一
    次点击清掉；反之（延迟 <= 最大 interval）就可能出现「整个确认窗口都落在同一个静默期
    内」，弹窗压着庭院也能走完确认，退回误判登录完成的老问题。
    """
    from tasks.Restart.login import LoginHandler, LOGIN_COURTYARD_CONFIRM_DELAY
    t = object.__new__(LoginHandler)
    t.skip_onmyoji_genie = False
    intervals = []

    def appear_then_click(rule, **kw):
        # 不传 interval 的分支每帧都真判，没有静默期，不参与这个约束
        if kw.get('interval') is not None:
            intervals.append((rule.name, kw['interval']))
        return False

    t.appear_then_click = appear_then_click
    t._login_popup_blocking()
    assert intervals, '应当探测到带 interval 的弹窗分支'
    worst = max(intervals, key=lambda kv: kv[1])
    assert LOGIN_COURTYARD_CONFIRM_DELAY > worst[1], (
        f'确认延迟 {LOGIN_COURTYARD_CONFIRM_DELAY}s 必须大于最长弹窗节流 '
        f'{worst[0]}={worst[1]}s，否则弹窗压着庭院也能走完二次确认')


def _login_handler_for_courtyard(monkeypatch, appear, turn, popup=None):
    """构造只走庭院二次确认的 LoginHandler 桩。

    :param appear: 庭院标识判定桩，按轮次返回画面内容
    :param turn: 单元素列表，_app_handle_login 每轮自增一次，供桩判断当前轮次
    :param popup: 可选，弹窗判定桩 (rule) -> bool；返回真表示这一轮该弹窗被识别并点掉
    """
    from tasks.Restart.login import LoginHandler
    monkeypatch.setattr('tasks.Restart.login.Timer', _TickTimer)
    dev = object.__new__(Device)
    dev.config = types.SimpleNamespace(
        script=types.SimpleNamespace(device=types.SimpleNamespace(serial='127.0.0.1:16384')))
    t = object.__new__(LoginHandler)
    t.device = dev
    t.device.stuck_record_add = lambda name: None
    t.device.get_orientation = lambda: None
    t.screenshot = lambda: None
    t.skip_onmyoji_genie = True
    t.click = lambda *a, **kw: False
    t.ocr_appear = lambda *a, **kw: False
    t.ocr_appear_click = lambda *a, **kw: False
    t.appear = appear

    # I_CANCEL_BATTLE 在每轮循环里恰好被 appear_then_click 探测一次，用它数轮次
    def appear_then_click(rule, *a, **kw):
        if rule is LoginHandler.I_CANCEL_BATTLE:
            turn[0] += 1
            # 桩里的 screenshot 是 no-op，不会触发 stuck_record_check，所以判定逻辑一旦
            # 不收敛（例如漏算某个庭院标识，计时被反复清掉）测试就会挂死。这里主动封顶，
            # 让它以明确的断言失败结束而不是拖到超时。
            if turn[0] > 50:
                raise AssertionError(
                    '登录循环未收敛：庭院二次确认的计时始终走不完，检查庭院标识是否漏算')
            # 它自己命中会 continue 跳过判定，这里只用于计数，恒不命中
            return False
        # 弹窗现在由 _login_popup_blocking 用 appear_then_click 识别并点掉，
        # 由 popup 回调决定本轮哪个弹窗命中
        return bool(popup and popup(rule))

    t.appear_then_click = appear_then_click
    return t


def test_app_handle_login_courtyard_needs_second_confirm(monkeypatch):
    """首次看到庭院不能立刻认定登录完成。

    点掉一个弹窗到下一个弹窗冒出来之间会露出一段真实的干净庭院画面，单帧截图无法把它和
    「真的进庭院了」区分开。所以首次看到只启动计时，隔一段时间后再判一次才认。
    """
    from tasks.Restart.login import LoginHandler

    turn = [0]
    # 庭院全程干净可见，且从不出现任何弹窗
    t = _login_handler_for_courtyard(
        monkeypatch,
        lambda rule, **kw: rule is LoginHandler.I_MAIN_GOTO_SHIKIGAMI_RECORDS,
        turn)

    t._app_handle_login()
    # 第 1 轮只启动计时，之后每轮 reached() 各消耗一次，满 TICKS 才跳出
    assert turn[0] == _TickTimer.TICKS + 2


def test_app_handle_login_popup_midway_restarts_courtyard_timer(monkeypatch):
    """计时中途识别到弹窗要停止计时，等下一次庭院出现再从头开始计。

    这是随机弹窗的关键：若中途只是「暂停」或干脆无视，弹窗关掉后残余的计时会立刻到点，
    等于又回到看一瞬间就认定的老问题。
    """
    from tasks.Restart.login import LoginHandler

    turn = [0]
    interrupt_at = 2  # 第 2 轮冒出弹窗打断计时

    t = _login_handler_for_courtyard(
        monkeypatch,
        # 庭院标识全程可匹配（弹窗盖在庭院上时背景照样能匹配到）
        lambda rule, **kw: rule is LoginHandler.I_MAIN_GOTO_SHIKIGAMI_RECORDS,
        turn,
        popup=lambda rule: rule is LoginHandler.I_LOGIN_RED_CLOSE and turn[0] == interrupt_at)

    t._app_handle_login()
    # 第 1 轮启动计时；第 2 轮识别到弹窗、点掉并清零；第 3 轮重新启动，再满 TICKS 次才跳出。
    # 没有「识别到弹窗即清零」的话会在 TICKS+2 轮就跳出，这里必须多花被打断的那两轮。
    assert turn[0] == _TickTimer.TICKS + 4


def test_app_handle_login_popup_over_courtyard_is_not_courtyard(monkeypatch):
    """弹窗还在处理时不算庭院，二次确认那一刻也不能放行。

    弹窗背后就是庭院，卷轴与式神录按钮照样能匹配到。少了「先处理弹窗、有弹窗就重来」这层
    收口，确认到点时只要恰好压着弹窗就会误放行。
    """
    from tasks.Restart.login import LoginHandler

    turn = [0]

    t = _login_handler_for_courtyard(
        monkeypatch,
        lambda rule, **kw: rule is LoginHandler.I_MAIN_GOTO_SHIKIGAMI_RECORDS,
        turn,
        # 弹窗一直在，直到确认本该到点之后才消失
        popup=lambda rule: (rule is LoginHandler.I_LOGIN_RED_CLOSE
                            and turn[0] <= _TickTimer.TICKS + 2))

    t._app_handle_login()
    # 弹窗期间一轮都不许放行：计时只能在弹窗消失后才开始，跳出必然晚于弹窗消失那一轮
    assert turn[0] > _TickTimer.TICKS + 2


def test_app_handle_login_popup_skips_courtyard_probe_entirely(monkeypatch):
    """本轮处理了弹窗就直接重来，压根不做庭院识别。

    弹窗识别到就点掉并 continue，庭院标识只在一个弹窗都没有时才去看。这样弹窗那几帧既省掉
    庭院侧的模板匹配与 OCR，也不可能出现「弹窗还在、庭院标识却匹配到了」的中间结论。
    """
    from tasks.Restart.login import LoginHandler

    turn = [0]
    probed = []

    def appear(rule, **kw):
        if rule in (LoginHandler.I_MAIN_GOTO_SHIKIGAMI_RECORDS, LoginHandler.I_LOGIN_SCROOLL_OPEN,
                    LoginHandler.I_LOGIN_SCROOLL_CLOSE, LoginHandler.I_LOGIN_COURTYARD,
                    LoginHandler.I_LOGIN_COURTYARD2):
            probed.append(turn[0])
            return rule is LoginHandler.I_MAIN_GOTO_SHIKIGAMI_RECORDS
        return False

    t = _login_handler_for_courtyard(
        monkeypatch, appear, turn,
        # 前两轮识别到弹窗并点掉，之后干净
        popup=lambda rule: rule is LoginHandler.I_LOGIN_RED_CLOSE and turn[0] <= 2)
    ocr_calls = []
    t.ocr_appear = lambda rule, **kw: ocr_calls.append(turn[0]) or False

    t._app_handle_login()
    # 处理弹窗的那两轮完全没碰庭院标识，也没跑 OCR
    assert 1 not in probed and 2 not in probed, f'弹窗期间仍在识别庭院: {probed}'
    assert not ocr_calls, f'弹窗期间仍在跑 OCR: {ocr_calls}'
    # 弹窗结束后才开始计时，跳出必然晚于弹窗消失那一轮
    assert turn[0] > _TickTimer.TICKS + 2


def test_login_popup_covers_every_closed_popup():
    """登录期间会主动关闭的弹窗都要收在 _login_popup_blocking 里。

    漏掉一个，它出现的那些帧就会被当成「没有弹窗」而放行庭院判定——弹窗压着庭院却认成登录
    完成，正是这么来的。I_CANCEL_BATTLE 与切号相关按钮在主循环里单独处理，不属于这里。
    """
    from tasks.Restart.login import LoginHandler
    from tasks.Component.GeneralInvite.assets import GeneralInviteAssets as gia

    expected = {
        LoginHandler.I_OFF_TICKET, LoginHandler.I_LOGIN_GET_COUPON,
        LoginHandler.I_LOGIN_LOAD_DOWN, LoginHandler.I_WATCH_VIDEO_CANCEL,
        LoginHandler.I_LOGIN_RED_CLOSE, LoginHandler.I_LOGIN_YELLOW_CLOSE,
        LoginHandler.I_LOGIN_LOGIN_GOTO_BIND_PHONE, gia.I_I_REJECT,
        LoginHandler.I_LOGIN_LOGIN_ONMYOJI_GENIE,
    }
    t = object.__new__(LoginHandler)
    t.skip_onmyoji_genie = False
    seen = []
    t.appear_then_click = lambda rule, **kw: seen.append(rule.name) or False
    t._login_popup_blocking()
    missing = {r.name for r in expected} - set(seen)
    assert not missing, f'弹窗清单漏收: {missing}'


@pytest.mark.parametrize('mark', ['I_LOGIN_SCROOLL_CLOSE', 'I_LOGIN_COURTYARD',
                                  'I_LOGIN_COURTYARD2', 'I_LOGIN_SCROOLL_OPEN'])
def test_app_handle_login_courtyard_marks_all_count(monkeypatch, mark):
    """卷轴没展开时式神录按钮是看不到的，此时闲庭图标／卷轴收起图标必须照样算庭院。

    这些标识本身就出现在庭院里，点卷轴区域也是庭院内的动作。少算哪个，庭院里的正常画面
    就会被判成「不是庭院」，计时被自己的卷轴动作反复清掉，二次确认永远走不完。
    """
    from tasks.Restart.login import LoginHandler

    turn = [0]
    target = getattr(LoginHandler, mark)
    # 只有这一个标识可见，式神录按钮全程不可见（模拟卷轴没展开）
    t = _login_handler_for_courtyard(monkeypatch, lambda rule, **kw: rule is target, turn)
    # 点击一律不生效，避免点击分支的 continue 干扰轮次计数
    t.click = lambda *a, **kw: False

    t._app_handle_login()
    # 与式神录按钮可见时的节奏一致：第 1 轮启动计时，满 TICKS 次后跳出
    assert turn[0] == _TickTimer.TICKS + 2


def test_app_handle_login_courtyard_ocr_fallback_counts(monkeypatch):
    # 所有图像标识都不匹配时，OCR 兜底认出的庭院同样要算庭院并启动二次确认
    from tasks.Restart.login import LoginHandler

    turn = [0]
    t = _login_handler_for_courtyard(monkeypatch, lambda rule, **kw: False, turn)
    ocr_calls = []
    t.ocr_appear = lambda rule, **kw: ocr_calls.append(kw) or True
    t.click = lambda *a, **kw: False

    t._app_handle_login()
    assert turn[0] == _TickTimer.TICKS + 2
    # 判定链里的 OCR 不带 interval（节流会漏判），且每轮只跑一次——OCR 比模板匹配贵得多
    assert ocr_calls[0] == {}
    assert len(ocr_calls) == turn[0]


def test_emulator_stop_desktop_closes_client():
    # 空闲关闭（close_emulator_or_*）桌面分支：走 desktop_stop_client 关客户端，不碰模拟器
    dev = _desktop_device()
    stopped = []

    def stop():
        stopped.append(True)
        return True

    dev.desktop_stop_client = stop
    # 不再独立查窗口：desktop_stop_client 内部已验证「窗口消失 且 进程退出」
    dev.desktop_window_exists = lambda: (_ for _ in ()).throw(
        AssertionError('emulator_stop 应直接采用 desktop_stop_client 的结论'))
    assert dev.emulator_stop() is True
    assert stopped == [True]


def test_emulator_stop_desktop_returns_false_when_not_released():
    """客户端未确认释放 → 返回 False，供上层判断空闲关闭是否成功。

    回归：旧代码杀完再独立查一次窗口，而窗口消失并不等于进程退出，
    强杀被拒时会把「进程还在」报成关闭成功。
    """
    dev = _desktop_device()
    dev.desktop_stop_client = lambda: False
    assert dev.emulator_stop() is False


# ---------------- 拟人化桌面输入：Task 14 消费点（plan_move/dwell/press/pointer_tail） ----------------

_DESKTOP_HZ_PERSONA = Persona.generate(20260825)


def _humanized_desktop(level, seed, cursor=(100, 100), scale=1.0):
    """构造带拟人化门面的桌面 Window 桩。

    humanizer 直接构造（复用固定人格/种子约定），不经过 Device.__init__——
    这里只测 backend 消费点，不测绑定。
    """
    w = _desktop_window(scale=scale)
    w._desktop_cursor = cursor
    w.humanizer = HumanizerContext(
        enabled=True, level=level, persona=_DESKTOP_HZ_PERSONA,
        rng=np.random.Generator(np.random.PCG64(seed)), canvas_size=(1280, 720))
    return w


def _record_desktop(fn, monkeypatch):
    """运行 fn，把 PostMessage/SendMessage/time.sleep 记成事件序列。"""
    calls = []
    monkeypatch.setattr('module.device.method.windows_impl.PostMessage',
                        lambda hwnd, msg, wp, lp: calls.append(('Post', hwnd, msg, wp, lp)))
    monkeypatch.setattr('module.device.method.windows_impl.SendMessage',
                        lambda hwnd, msg, wp, lp: calls.append(('Send', hwnd, msg, wp, lp)))
    monkeypatch.setattr('module.device.method.windows_impl.time.sleep',
                        lambda s: calls.append(('sleep', s)))
    fn()
    return calls


def _desktop_msgs(events):
    """事件列表 → 与事件对齐的消息序列（sleep 事件映射为 None，保证索引对齐）。"""
    return [e[2] if len(e) >= 3 else None for e in events]


def _move_points(events):
    """从事件里取所有 WM_MOUSEMOVE 的 (x, y)（lParam 解码：MAKELONG(x, y) => y<<16|x）。"""
    return [(e[4] & 0xFFFF, (e[4] >> 16) & 0xFFFF)
            for e in events if len(e) >= 3 and e[2] == win32con.WM_MOUSEMOVE]


def test_desktop_click_humanized_medium_consumes_plan_dwell_press_tail(monkeypatch):
    # 桌面点击 medium：预定位 plan_move + 到位停顿 E + 按压 B + 抬起后指针尾 F。
    # 事件顺序：MOVE(预定位/dwell) → DOWN → sleep(按压) → UP → CAPTURECHANGED → MOVE(尾)
    w = _humanized_desktop('medium', seed=1)
    events = _record_desktop(lambda: w.click_desktop_window_message(640, 360, fast=False),
                             monkeypatch)
    msgs = _desktop_msgs(events)
    down = msgs.index(win32con.WM_LBUTTONDOWN)
    up = msgs.index(win32con.WM_LBUTTONUP)
    cap = msgs.index(win32con.WM_CAPTURECHANGED)
    # 保留语义：WM_CAPTURECHANGED 在 UP 后立即发送
    assert cap == up + 1
    # DOWN 前有预定位/到位停顿的 MOUSEMOVE（plan_move 与 settle 段）
    assert any(m == win32con.WM_MOUSEMOVE for m in msgs[:down])
    # 按压时长来自维度 B（不在原 randint(30,60) 矩形区间），位于 DOWN/UP 之间
    press_sleeps = [e[1] for e in events[down:up] if e[0] == 'sleep']
    assert press_sleeps, 'DOWN 与 UP 之间必须有按压 sleep'
    assert not any(0.030 <= s <= 0.060 for s in press_sleeps), \
        f'按压不应落在 legacy 矩形区间: {press_sleeps}'
    # UP/CAPTURECHANGED 之后仍有 after-UP 指针尾漂移（hover 刷新）
    assert any(m == win32con.WM_MOUSEMOVE for m in msgs[cap + 1:])
    assert w._desktop_cursor == _move_points(events)[-1]
    # 预定位纳入动作级预算：DOWN 前至少存在一次 sleep（plan_move 的逐点 delay 或 dwell）
    assert any(e[0] == 'sleep' for e in events[:down])


def test_desktop_click_humanized_light_has_b_and_tail(monkeypatch):
    # 桌面点击 light：B/F/D 启用；fast 按压来自 B（下界 45ms），不在 legacy 的 10~25ms
    w = _humanized_desktop('light', seed=2)
    events = _record_desktop(lambda: w.click_desktop_window_message(640, 360, fast=True),
                             monkeypatch)
    msgs = _desktop_msgs(events)
    down = msgs.index(win32con.WM_LBUTTONDOWN)
    up = msgs.index(win32con.WM_LBUTTONUP)
    cap = msgs.index(win32con.WM_CAPTURECHANGED)
    assert cap == up + 1
    # F 在 light 也启用：CAPTURECHANGED 后仍有尾移动
    assert any(m == win32con.WM_MOUSEMOVE for m in msgs[cap + 1:])
    press_sleeps = [e[1] for e in events[down:up] if e[0] == 'sleep']
    assert press_sleeps and not any(0.010 <= s <= 0.025 for s in press_sleeps), \
        f'fast 按压不应落在 legacy 的 10~25ms: {press_sleeps}'


def test_desktop_long_click_humanized_preserves_duration(monkeypatch):
    # 桌面长按：E 停顿 + F 尾；既有长按时长是业务参数，不被维度 B 重采样。
    # hold 段守恒断言对维度 J 两个策略分支都成立：'none' 时是单次 0.5s 纯 sleep，
    # 'tremor' 时是 N 个小 sleep、sum ≈ 0.5s（±1ms/点抖动带宽）——不 pin 策略，
    # 避免 RNG 消耗顺序变化把 seed 翻到另一分支导致脆断
    w = _humanized_desktop('medium', seed=3)
    events = _record_desktop(lambda: w.long_click_desktop_window_message(640, 360, 0.5),
                             monkeypatch)
    msgs = _desktop_msgs(events)
    down = msgs.index(win32con.WM_LBUTTONDOWN)
    up = msgs.index(win32con.WM_LBUTTONUP)
    cap = msgs.index(win32con.WM_CAPTURECHANGED)
    holds = [e[1] for e in events[down + 1:up] if e[0] == 'sleep']
    assert abs(sum(holds) - 0.5) <= 0.001 * len(holds) + 0.001, \
        f'hold 段时长应守恒 0.5s: {holds}'
    assert cap == up + 1
    assert any(m == win32con.WM_MOUSEMOVE for m in msgs[cap + 1:])
    assert w._desktop_cursor == _move_points(events)[-1]


def test_desktop_move_light_strips_start_and_budget(monkeypatch):
    # 移动 light：plan_move 剥离与 start 相同的首点；首个 MOVE != start；末项仍为终点；
    # 请求预算落在 15~30ms（Spec §5 D light）
    w = _humanized_desktop('light', seed=4)
    events = _record_desktop(lambda: w.move_desktop_window_message(600, 500), monkeypatch)
    pts = _move_points(events)
    assert pts, '开档移动必须发出 MOVE'
    assert pts[0] != (100, 100), '首个 MOVE 不得等于起点（light 剥离）'
    assert pts[-1] == (600, 500), '末项必须仍为终点'
    sleeps = [e[1] for e in events if e[0] == 'sleep']
    assert 0.015 <= sum(sleeps) <= 0.030 + 1e-6, \
        f'light 移动请求预算应 15~30ms，实际 {sum(sleeps):.4f}'
    assert w._desktop_cursor == (600, 500)


def test_desktop_move_medium_caps_message_count(monkeypatch):
    # 移动 medium：消息数 ≤ DESKTOP_MOVE_MAX_POINTS，请求预算 ≥ 40ms
    w = _humanized_desktop('medium', seed=5)
    w._desktop_cursor = (0, 0)
    events = _record_desktop(lambda: w.move_desktop_window_message(1279, 719), monkeypatch)
    pts = _move_points(events)
    assert len(pts) <= w.DESKTOP_MOVE_MAX_POINTS, f'MOVE 消息数超上限: {len(pts)}'
    sleeps = [e[1] for e in events if e[0] == 'sleep']
    assert sum(sleeps) >= 0.040 - 1e-6, f'medium 请求预算应 ≥ 40ms，实际 {sum(sleeps):.4f}'
    assert w._desktop_cursor == (1279, 719)


def test_desktop_move_oob_target_falls_back_to_original(monkeypatch):
    # Spec §4.11：终点越界（(1280,720) 超出 1280×720 画布）→ plan_move 返回 None →
    # 回退原旁路。原移动循环没有逐点 sleep，因此 sleep 总数为 0，光标仍更新到目标
    w = _humanized_desktop('medium', seed=6)
    w._desktop_cursor = (0, 0)
    events = _record_desktop(lambda: w.move_desktop_window_message(1280, 720), monkeypatch)
    sleeps = [e[1] for e in events if e[0] == 'sleep']
    assert sleeps == [], '越界回退走原旁路，不应有任何逐点 sleep'
    assert w._desktop_cursor == (1280, 720)


def test_desktop_swipe_humanized_plan_swipe_and_preposition(monkeypatch):
    # 桌面滑动 medium：起点预定位（plan_move）+ 手势主体 plan_swipe + UP 前 gap（I）。
    # DOWN 前有预定位 MOUSEMOVE，DOWN 后有带按键 MOUSEMOVE，结尾 UP + CAPTURECHANGED
    w = _humanized_desktop('medium', seed=7)
    w._desktop_cursor = (50, 50)
    events = _record_desktop(lambda: w.swipe_desktop_window_message([100, 100], [300, 400]),
                             monkeypatch)
    msgs = _desktop_msgs(events)
    down = msgs.index(win32con.WM_LBUTTONDOWN)
    up = msgs.index(win32con.WM_LBUTTONUP)
    # 预定位：DOWN 前有不带按键的 MOUSEMOVE（move 消费 plan_move）
    assert any(m == win32con.WM_MOUSEMOVE for m in msgs[:down])
    # 手势主体：DOWN 后有带按键的 MOUSEMOVE
    assert any(m == win32con.WM_MOUSEMOVE for m in msgs[down:up])
    assert win32con.WM_CAPTURECHANGED in msgs[up:]
    assert w._desktop_cursor == (300, 400)


def test_desktop_swipe_budget_scales_with_legacy_duration(monkeypatch):
    """medium/heavy 桌面滑动预算 = legacy 总时长（desktop_trace 点位数随距离
    伸缩 + 3×80ms 手动收尾），不再固定 120ms：长滑动的手势主体 sleep 总量
    必须显著超过旧固定预算 + gap 的上界。强制 natural 尾段排除 H 替换干扰。"""
    from module.device.humanize import HumanizerContext
    orig_choose = HumanizerContext._choose

    def forced_natural(self, d, allowed):
        return 'natural' if d == 'swipe_tail' else orig_choose(self, d, allowed)
    monkeypatch.setattr(HumanizerContext, '_choose', forced_natural)

    w = _humanized_desktop('medium', seed=9)
    w._desktop_cursor = (50, 50)
    events = _record_desktop(lambda: w.swipe_desktop_window_message([50, 200], [1150, 500]),
                             monkeypatch)
    down_i = next(i for i, e in enumerate(events)
                  if len(e) >= 3 and e[2] == win32con.WM_LBUTTONDOWN)
    up_i = next(i for i, e in enumerate(events)
                if len(e) >= 3 and e[2] == win32con.WM_LBUTTONUP)
    body = [e[1] for e in events[down_i:up_i] if e[0] == 'sleep']
    # 旧固定预算路径主体 = 120ms + gap(≤110ms) ≈ ≤0.23s；新预算 ≥ 240ms 收尾
    # + N 点×10ms 间隔（1100px 长滑 ≥ 10 点）+ gap，必然 > 0.3s
    assert sum(body) > 0.30, \
        f'长滑动手势主体 sleep 总量 {sum(body):.3f}s 应超过旧固定 120ms 预算区间'


def test_desktop_swipe_oob_fallback_restores_legacy_rng(monkeypatch):
    """桌面策略失败时预生成 delay 不得改变 legacy 的事件与随机时序。"""
    import random as _stdlib_random

    def run(humanized):
        _stdlib_random.seed(20260826)
        np.random.seed(20260826)
        w = _humanized_desktop('medium', seed=7) if humanized else _desktop_window(scale=1.0)
        w._desktop_cursor = (100, 100)
        return _record_desktop(
            lambda: w.swipe_desktop_window_message([100, 100], [1280, 400]),
            monkeypatch,
        )

    assert run(True) == run(False)


def test_desktop_click_message_count_within_cap(monkeypatch):
    # Task 14 验收：消息点数不超过 DESKTOP_MOVE_MAX_POINTS + 收尾条数上限。
    # 跨屏移动被抽稀到 12 点，再加 dwell/尾收尾，总 MOVE 消息数仍受上限约束
    w = _humanized_desktop('medium', seed=8)
    w._desktop_cursor = (0, 0)
    events = _record_desktop(lambda: w.click_desktop_window_message(1279, 719, fast=True),
                             monkeypatch)
    moves = [e for e in events if len(e) >= 3 and e[2] == win32con.WM_MOUSEMOVE]
    assert len(moves) <= w.DESKTOP_MOVE_MAX_POINTS + 8, \
        f'MOVE 总数超上限: {len(moves)}（12 点移动 + 收尾）'


def test_desktop_long_click_hold_jitter_stream(monkeypatch):
    """维度 J（hold 微颤）：长按 hold 期间不再死寂——DOWN 与 UP 之间出现
    微颤 MOUSEMOVE 流，且总 sleep 守恒业务时长（UP 不提前）。"""
    w = _humanized_desktop('medium', seed=11)
    events = _record_desktop(lambda: w.long_click_desktop_window_message(640, 360, 1.0),
                             monkeypatch)
    msgs = _desktop_msgs(events)
    down = msgs.index(win32con.WM_LBUTTONDOWN)
    up = msgs.index(win32con.WM_LBUTTONUP)
    # hold 期间（DOWN 后 UP 前）有 MOUSEMOVE：事件流不再死寂
    hold_moves = [m for m in msgs[down + 1:up] if m == win32con.WM_MOUSEMOVE]
    assert hold_moves, 'hold 期间应有微颤 MOVE 流'
    # 微颤幅度 ≤6px（换算前是 authoring 坐标；scale=1.0 时消息坐标即 authoring）
    for e in events[down + 1:up]:
        if len(e) == 5 and e[2] == win32con.WM_MOUSEMOVE:
            lp = e[4]
            px, py = lp & 0xFFFF, (lp >> 16) & 0xFFFF
            assert abs(px - 640) <= 6 and abs(py - 360) <= 6
    # 预算守恒：hold 段 sleep 总量 ≈ 1.0s（±1ms/点抖动带宽）
    hold_sleeps = [e[1] for e in events[down + 1:up] if e[0] == 'sleep']
    assert abs(sum(hold_sleeps) - 1.0) <= 0.001 * len(hold_sleeps) + 0.001


def test_desktop_long_click_hold_jitter_none_falls_back_to_sleep(monkeypatch):
    """维度 J 'none' 策略：hold 段回退纯 sleep(duration)，行为与旧实现一致。"""
    w = _humanized_desktop('medium', seed=12)
    monkeypatch.setattr(w.humanizer, 'plan_hold',
                        lambda target, duration_s, **kw: None)
    events = _record_desktop(lambda: w.long_click_desktop_window_message(640, 360, 0.5),
                             monkeypatch)
    msgs = _desktop_msgs(events)
    down = msgs.index(win32con.WM_LBUTTONDOWN)
    up = msgs.index(win32con.WM_LBUTTONUP)
    # 回退路径：hold 段只有一个 0.5s 纯 sleep，无 MOVE
    hold_moves = [m for m in msgs[down + 1:up] if m == win32con.WM_MOUSEMOVE]
    assert hold_moves == []
    hold_sleeps = [e[1] for e in events[down + 1:up] if e[0] == 'sleep']
    assert hold_sleeps == [0.5]


def test_desktop_long_click_hold_jitter_scale_not_one(monkeypatch):
    """桌面 hold 微颤在 DPI≠100% 下不得双重缩放：微颤点 authoring 空间
    生成，desktop_message_coord 单次换算（消息空间 = authoring/scale）。
    scale=1.5 时消息空间幅度 ≤ 6/1.5 + 1（取整余量）；预算守恒不受影响。"""
    w = _humanized_desktop('medium', seed=13, scale=1.5)
    events = _record_desktop(lambda: w.long_click_desktop_window_message(640, 360, 0.5),
                             monkeypatch)
    msgs = _desktop_msgs(events)
    down = msgs.index(win32con.WM_LBUTTONDOWN)
    up = msgs.index(win32con.WM_LBUTTONUP)
    down_lp = [e for e in events if len(e) == 5 and e[2] == win32con.WM_LBUTTONDOWN][0][4]
    down_pos = (down_lp & 0xFFFF, (down_lp >> 16) & 0xFFFF)
    moved = [e for e in events[down + 1:up]
             if len(e) == 5 and e[2] == win32con.WM_MOUSEMOVE]
    assert moved, 'hold 段应有微颤 MOVE'
    limit = 6 / 1.5 + 1
    for e in moved:
        lp = e[4]
        mx, my = lp & 0xFFFF, (lp >> 16) & 0xFFFF
        assert abs(mx - down_pos[0]) <= limit, \
            f'MOVE ({mx},{my}) 距 DOWN {down_pos} 超限 {limit:.1f}px——疑似双重缩放'
        assert abs(my - down_pos[1]) <= limit
    hold_sleeps = [e[1] for e in events[down + 1:up] if e[0] == 'sleep']
    assert abs(sum(hold_sleeps) - 0.5) <= 0.001 * len(hold_sleeps) + 0.001
