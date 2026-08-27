# -*- coding: utf-8 -*-
"""拟人化消费点共享断言（Plan Task 15、20）。

Task 15：模拟器 window_message 只消费 B（press_seconds）与 F（plan_touch_liftoff）——
不接 plan_move/plan_dwell，medium/heavy 对该点击动作没有新增维度，行为与耗时模型
均同 light（契约 #10：不得虚构点击前 C、E 或 G 的耗时）。滑动走 plan_swipe。

Task 20：桌面指针语义的维度 G 空闲——move_desktop_plan 把 plan_idle 的 MovePlan
逐点投递为 WM_MOUSEMOVE，不产生 DOWN/UP，因此不会触发 click / screenshot /
control check；空闲阈值边界（恰好 2s / 30s）不产生游移。
"""
import inspect
import threading
import types

import numpy as np
import pytest
import win32con

import module.device.method.adb as adb_mod
from module.atom.click import RuleClick
from module.atom.gif import RuleGif
from module.atom.image import RuleImage
from module.atom.ocr import RuleOcr
from module.atom.swipe import RuleSwipe
from module.device.handle import EmulatorFamily
from module.device.humanize import HumanizerContext, bind_humanizer
from module.device.humanize.gesture import plan_idle as _plan_idle
from module.device.humanize.persona import Persona
from module.device.humanize.plan import MovePlan, TailPlan
from module.device.method.windows_impl import Window
from module.device.method.adb import Adb
from module.device.control import Control
from module.device.device import Device

pytestmark = pytest.mark.unit

_SEED = 20260825
_PERSONA = Persona.generate(_SEED)


def _humanizer(level, seed):
    """直接构造拟人化门面（复用固定人格/种子约定），不经过 Device.__init__。"""
    return HumanizerContext(
        enabled=True, level=level, persona=_PERSONA,
        rng=np.random.Generator(np.random.PCG64(seed)), canvas_size=(1280, 720))


def _rng(seed=1):
    return np.random.Generator(np.random.PCG64(seed))


def _emu_window(handles=(0x101, 0x102), family=None, scale=1.0, humanizer=None):
    """构造模拟器 window_message 桩：mumu 默认两句柄，可覆盖 family/缩放。"""
    w = object.__new__(Window)
    w.is_desktop_window = False
    w.window_scale_rate = scale
    w.control_handle_list = list(handles)
    w.root_handle_num = handles[0]
    w.screenshot_size = (1280, 720)
    w.emulator_family = family or EmulatorFamily.FAMILY_MUMU
    if humanizer is not None:
        w.humanizer = humanizer
    return w


def _desktop_window(humanizer, cursor=(100, 100)):
    """构造桌面 window_message 桩：1280x720 截图空间、100% DPI、已记录光标。"""
    w = object.__new__(Window)
    w.is_desktop_window = True
    w.window_scale_rate = 1.0
    w.control_handle_list = []
    w.root_handle_num = 0x400
    w.screenshot_size = (1280, 720)
    w.desktop_client_size = lambda: (1280, 720)
    w.desktop_client_size_virtual = lambda: (1280, 720)
    w.desktop_window_restore_if_minimized = lambda: False
    w._desktop_cursor = cursor
    w.humanizer = humanizer
    return w


def _record(monkeypatch, fn):
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


def _msgs(events):
    """事件列表 → 与事件对齐的消息序列（sleep 事件映射为 None，保证索引对齐）。"""
    return [e[2] if len(e) >= 3 else None for e in events]


# ---------------- Task 15：模拟器 window_message 点击只消费 B/F ----------------

def test_emu_click_consumes_only_press_and_liftoff(monkeypatch):
    """模拟器点击：DOWN 前无任何移动（不接 plan_move/plan_dwell），UP 是最后一条消息。"""
    w = _emu_window(humanizer=_humanizer('medium', seed=1))
    events = _record(monkeypatch, lambda: w.click_window_message(640, 360, fast=False))
    msgs = _msgs(events)
    down = msgs.index(win32con.WM_LBUTTONDOWN)
    up = msgs.index(win32con.WM_LBUTTONUP)
    # 契约 #10：模拟器点击不接 plan_move/plan_dwell，DOWN 前只有 WM_ACTIVATE，没有移动
    assert not any(m == win32con.WM_MOUSEMOVE for m in msgs[:down]), \
        '模拟器点击 DOWN 前不得出现 plan_move/plan_dwell 产生的移动'
    # 没有 after-UP 事件（模拟器路径原本没有任何收尾事件，只有 before-UP 微位移）
    assert up == len(msgs) - 1, f'UP 后不应有事件: {msgs[up + 1:]}'
    # 按压时长来自维度 B：不在原 randint(100,200) 矩形区间
    press_sleeps = [e[1] for e in events[down:up] if e[0] == 'sleep']
    assert any(not (0.100 <= s <= 0.200) for s in press_sleeps), \
        f'按压不应落在 legacy 矩形区间: {press_sleeps}'


def test_emu_click_medium_does_not_invent_ceg_timing(monkeypatch):
    """契约 #10：medium 点击不得虚构 C/E/G——事件类型只可能是 ACTIVATE/DOWN/MOVE/UP，
    DOWN 前没有 sleep（无到位停顿 E）、没有移动（无 plan_move C/D），UP 后为空。"""
    w = _emu_window(humanizer=_humanizer('medium', seed=2))
    events = _record(monkeypatch, lambda: w.click_window_message(640, 360, fast=False))
    msgs = _msgs(events)
    down = msgs.index(win32con.WM_LBUTTONDOWN)
    # 消息类型集合只可能是 ACTIVATE / DOWN / MOVE(liftoff) / UP
    allowed = {win32con.WM_ACTIVATE, win32con.WM_LBUTTONDOWN,
               win32con.WM_MOUSEMOVE, win32con.WM_LBUTTONUP}
    assert set(m for m in msgs if m is not None) <= allowed, f'出现不该有的消息类型: {msgs}'
    # DOWN 前没有 sleep：模拟器点击没有到位停顿（E 不属模拟器）
    assert not any(e[0] == 'sleep' for e in events[:down]), '模拟器点击 DOWN 前不应有 E 停顿 sleep'
    # UP 后为空：没有 after-UP 指针尾（F 在这里是 before-UP liftoff）
    up = msgs.index(win32con.WM_LBUTTONUP)
    assert up == len(msgs) - 1


@pytest.mark.parametrize('target', [(0, 0), (1279, 0), (0, 719), (1279, 719)])
def test_emu_click_scaled_liftoff_stays_in_message_canvas(monkeypatch, target):
    """缩放场景先在 authoring 画布规划 liftoff，再换算到实际消息空间。"""
    planned_targets = []

    def plan_liftoff(authoring_target):
        planned_targets.append(authoring_target)
        return TailPlan(points=(authoring_target,), delays=(0.0,))

    humanizer = types.SimpleNamespace(
        press_seconds=lambda fast=False: 0.05,
        plan_touch_liftoff=plan_liftoff,
    )
    w = _emu_window(scale=1.25, humanizer=humanizer)
    events = _record(monkeypatch, lambda: w.click_window_message(*target, fast=False))
    moves = [event for event in events if len(event) >= 3 and event[2] == win32con.WM_MOUSEMOVE]

    assert planned_targets == [target]
    assert len(moves) == 1
    x = moves[0][4] & 0xFFFF
    y = (moves[0][4] >> 16) & 0xFFFF
    assert 0 <= x <= int(1279 / 1.25)
    assert 0 <= y <= int(719 / 1.25)


def test_emu_click_light_and_medium_message_types_match(monkeypatch):
    """契约 #10：medium 相对 light 无新增维度——去掉 liftoff 的随机 MOVE 后，
    两条路径的消息类型序列必须一致（medium 绝不新增移动或停顿）。"""
    def run(level, seed):
        w = _emu_window(humanizer=_humanizer(level, seed))
        events = _record(monkeypatch, lambda: w.click_window_message(640, 360, fast=False))
        return [m for m in _msgs(events) if m is not None]

    light = run('light', 3)
    medium = run('medium', 4)
    # 去掉可有可无的 liftoff MOVE（touch_liftoff.none 保留 20% 人类方差）后逐位一致
    light_core = [m for m in light if m != win32con.WM_MOUSEMOVE]
    medium_core = [m for m in medium if m != win32con.WM_MOUSEMOVE]
    assert light_core == medium_core, f'light={light} medium={medium}'


# ---------------- Task 15：模拟器 window_message 滑动走 plan_swipe ----------------

def test_emu_swipe_humanized_plan_swipe_with_sleeps(monkeypatch):
    """模拟器滑动：DOWN 后逐点 MOVE 且每个 MOVE 前有 sleep（delays 发点前消费），
    UP 是最后一条消息。"""
    w = _emu_window(humanizer=_humanizer('medium', seed=5))
    events = _record(monkeypatch, lambda: w.swipe_window_message([100, 100], [300, 400]))
    msgs = _msgs(events)
    down = msgs.index(win32con.WM_LBUTTONDOWN)
    up = msgs.index(win32con.WM_LBUTTONUP)
    body = events[down:up]
    assert any(e[2] == win32con.WM_MOUSEMOVE for e in body if len(e) >= 3), '滑动主体应有 MOVE'
    # delays[i] 在 points[i] 前消费：滑动主体里 MOVE 与 sleep 交错
    assert any(e[0] == 'sleep' for e in body), '滑动主体应有逐点 sleep'
    assert up == len(msgs) - 1, '滑动 UP 后不应有事件'


def test_emu_swipe_light_keeps_legacy_delays(monkeypatch):
    """Task 15：light 滑动 D no-op——plan_swipe 保留既有逐点 sleep 结构（近恒定），
    只由 facade 应用 H 末段替换；消息类型序列仍是 NCHITTEST/SETCURSOR/DOWN/MOVE/UP。"""
    w = _emu_window(humanizer=_humanizer('light', seed=6))
    events = _record(monkeypatch, lambda: w.swipe_window_message([100, 100], [300, 400]))
    msgs = _msgs(events)
    down = msgs.index(win32con.WM_LBUTTONDOWN)
    up = msgs.index(win32con.WM_LBUTTONUP)
    assert any(e[0] == 'sleep' for e in events[down:up]), 'light 滑动保留逐点 sleep'
    assert up == len(msgs) - 1


@pytest.mark.parametrize(
    'handles,family',
    [
        ((0x101, 0x102), EmulatorFamily.FAMILY_MUMU),
        ((0x101, 0x102, 0x103, 0x104), EmulatorFamily.FAMILY_NOX),
        ((0x101,), EmulatorFamily.FAMILY_LD),
    ],
)
def test_emu_swipe_scales_all_touch_coordinates(monkeypatch, handles, family):
    """模拟器滑动的 DOWN/MOVE/UP 都从 authoring 坐标换到消息坐标。"""
    scale = 1.25
    w = _emu_window(
        handles=handles,
        family=family,
        scale=scale,
        humanizer=_humanizer('medium', seed=15),
    )
    events = _record(monkeypatch, lambda: w.swipe_window_message([100, 100], [300, 400]))
    touch_events = [
        event for event in events
        if len(event) >= 5 and event[2] in (
            win32con.WM_LBUTTONDOWN, win32con.WM_MOUSEMOVE, win32con.WM_LBUTTONUP)
    ]
    coords = [(event[4] & 0xFFFF, (event[4] >> 16) & 0xFFFF) for event in touch_events]

    assert coords[0] == (80, 80)
    assert coords[-1] == (240, 320)
    assert all(0 <= x <= int(1279 / scale) and 0 <= y <= int(719 / scale)
               for x, y in coords)


def test_emu_swipe_off_scale_keeps_legacy_event_coordinates(monkeypatch):
    """off 档缩放不得改变 legacy DOWN/MOVE/UP 的原始事件坐标。"""
    import random as _stdlib_random

    def run(scale):
        _stdlib_random.seed(20260826)
        np.random.seed(20260826)
        w = _emu_window(scale=scale)
        return _record(monkeypatch, lambda: w.swipe_window_message([100, 100], [300, 400]))

    assert run(1.25) == run(1.0)


def test_emu_swipe_enabled_oob_fallback_keeps_legacy_event_coordinates(monkeypatch):
    """enabled 但端点越界时回退 legacy，DPI scale 不得改写原始事件坐标。"""
    import random as _stdlib_random

    def run(scale):
        _stdlib_random.seed(20260826)
        np.random.seed(20260826)
        w = _emu_window(scale=scale, humanizer=_humanizer('medium', seed=15))
        return _record(monkeypatch, lambda: w.swipe_window_message([-1, 100], [300, 400]))

    assert run(1.25) == run(1.0)


def test_emu_swipe_enabled_oob_fallback_restores_legacy_rng(monkeypatch):
    """策略失败时预生成 delay 不得污染 legacy 循环的随机序列。"""
    import random as _stdlib_random

    def run(humanizer):
        _stdlib_random.seed(20260826)
        np.random.seed(20260826)
        w = _emu_window(humanizer=humanizer)
        return _record(monkeypatch, lambda: w.swipe_window_message([-1, 100], [300, 400]))

    assert run(_humanizer('medium', seed=15)) == run(None)


# ---------------- Task 20：维度 G 空闲（桌面指针语义） ----------------

def test_desktop_idle_plan_delivery_only_moves(monkeypatch):
    """move_desktop_plan 把 plan_idle 的 MovePlan 逐点投递为 WM_MOUSEMOVE：
    绝不产生 DOWN/UP（不会触发点击/control check），delays 在发点前消费，
    光标更新到计划末点。"""
    ctx = _humanizer('medium', seed=7)
    w = _desktop_window(ctx)
    plan = ctx.plan_idle(2.5, (100, 100))
    assert isinstance(plan, MovePlan), '超过 2s 阈值应产生空闲游移计划'
    events = _record(monkeypatch, lambda: w.move_desktop_plan(plan))
    msgs = _msgs(events)
    # 只发 MOUSEMOVE（None 是 sleep 事件），没有 DOWN/UP → 不会触发 click
    assert all(m is None or m == win32con.WM_MOUSEMOVE for m in msgs), f'出现 DOWN/UP: {msgs}'
    # delays[i] 在 points[i] 前消费：sleep 数与点数一致
    sleeps = [e[1] for e in events if e[0] == 'sleep']
    assert len(sleeps) == len(plan.points)
    assert sum(sleeps) == pytest.approx(plan.total_seconds)
    assert w._desktop_cursor == (int(plan.points[-1][0]), int(plan.points[-1][1]))


def test_idle_boundary_2s_and_30s_no_drift():
    """Task 20：空闲阈值边界——恰好 2s（idle_drift）与 30s（park）时不得产生游移。"""
    ctx = _humanizer('medium', seed=8)
    cursor = (100, 100)
    # 恰好 2s：idle_drift 的判定是 <= 阈值即返回 None，与抽到哪个 option 无关
    assert ctx.plan_idle(2.0, cursor) is None
    # 恰好 30s：显式 park 语义 <= 阈值返回 None（不放手）
    assert _plan_idle(_rng(1), 30.0, cursor, _PERSONA, option='park',
                      level='medium', canvas_size=(1280, 720)) is None
    # 刚超过 2s：idle_drift 给出短距游移计划
    plan = _plan_idle(_rng(2), 2.05, cursor, _PERSONA, option='idle_drift',
                      level='medium', canvas_size=(1280, 720))
    assert isinstance(plan, MovePlan)
    assert 0.0 <= plan.total_seconds  # 计划合法（非负、有限）


def test_idle_oob_cursor_returns_none_without_clipping():
    """cursor 是既有动作起点，越界时整体回退，不能裁剪后继续游移。"""
    cursor = (1280, 720)
    ctx = _humanizer('medium', seed=31)

    assert ctx.plan_idle(5.0, cursor) is None
    assert _plan_idle(
        _rng(31), 5.0, cursor, _PERSONA, option='idle_drift',
        level='medium', canvas_size=(1280, 720),
    ) is None


def test_idle_cursor_unknown_returns_none():
    """Task 20：光标位置未知时 plan_idle 返回 None——凭空移动会把光标从未知处
    拽到某坐标，那是引入新行为而不是拟人化。"""
    ctx = _humanizer('medium', seed=9)
    assert ctx.plan_idle(5.0, None) is None


def test_idle_oob_cursor_falls_back_without_clipping():
    """cursor 是既有动作起点，越界时整体回退，不能裁剪后继续游移。"""
    cursor = (1280, 720)
    ctx = _humanizer('medium', seed=31)

    assert ctx.plan_idle(5.0, cursor) is None
    assert _plan_idle(
        _rng(31), 5.0, cursor, _PERSONA, option='idle_drift',
        level='medium', canvas_size=(1280, 720),
    ) is None


# ---------------- Task 20：Control.click 接入（维度 G 空闲投递） ----------------

def _click_device(humanizer, *, cursor=(100, 100), last_ts=None):
    """构造桌面 Device 桩：is_desktop_window=True + humanizer + 记录光标。

    object.__new__(Device) 避开 Device.__init__ 的重型初始化；last_ts 为 None
    时不设置实例时间戳，模拟"首次点击"（Control.click 的 getattr 走 None 分支，
    since_last=0，不产生首击游移）。
    """
    d = object.__new__(Device)
    d.is_desktop_window = True
    d.humanizer = humanizer
    d.config = types.SimpleNamespace(
        script=types.SimpleNamespace(
            device=types.SimpleNamespace(control_method='window_message')))
    d._desktop_cursor = cursor
    if last_ts is not None:
        d._last_action_ts = last_ts
    return d


def _fake_clock(now):
    """只暴露 .time() 的假时钟：精确控制 Control.click 里 since_last 的取值，
    让 2s / 30s 边界测试不依赖真实墙钟。"""
    ns = types.SimpleNamespace()
    ns.time = lambda: now
    return ns


def _record_click_backends(d):
    """把 dispatch 可能选中的两个后端（window_message 与 ADB 兜底）换成记录桩。

    桌面 + humanizer 启用时，Control.click 的 humanized_click_methods 不含
    window_message 键，methods.get 会落到 click_adb 兜底；这里两个都桩上，
    断言"恰好一个被调用"即证明原 click 仍执行，不依赖 dispatch 的内部取舍。
    """
    clicks = []
    d.click_adb = lambda x, y: clicks.append((x, y))
    d.click_window_message = lambda x, y, fast=False: clicks.append((x, y))
    return clicks


def _force_idle_option(monkeypatch, option):
    """强制维度 idle 的选型，让 plan_idle 的 option 确定（其余维走原 _choose）。

    不强制时 idle 可能抽中 idle_drift 或 park，两个 option 的阈值不同，无法在
    Control 层把"刚好超过阈值 → 有游移"断定为确定性行为。
    """
    orig = HumanizerContext._choose

    def forced(self, dim, allowed):
        return option if dim == 'idle' else orig(self, dim, allowed)
    monkeypatch.setattr(HumanizerContext, '_choose', forced)


def test_click_delivers_idle_plan_before_backend(monkeypatch):
    """Control.click 在桌面指针语义下：超过 2s 阈值 → plan_idle 返回计划 →
    move_desktop_plan 投递游移，且原始 click 仍原样执行。"""
    ctx = _humanizer('medium', seed=20)
    d = _click_device(ctx, last_ts=100.0)
    monkeypatch.setattr('module.device.control.time', _fake_clock(102.5))  # since_last=2.5s
    _force_idle_option(monkeypatch, 'idle_drift')
    delivered = []
    monkeypatch.setattr(d, 'move_desktop_plan', lambda plan: delivered.append(plan))
    clicks = _record_click_backends(d)
    Control.click(d, 200, 200, control_check=False, control_name='Click')
    assert len(delivered) == 1
    assert isinstance(delivered[0], MovePlan)
    assert len(clicks) == 1  # 游移只投移动，不替代原 click
    assert clicks == [(200, 200)]


def test_click_boundary_exactly_2s_no_idle(monkeypatch):
    """间隔恰好 2s：plan_idle 判定 <= 阈值返回 None（与抽到哪个 option 无关），
    Control.click 不投递游移、只执行原 click，时间戳照常更新。"""
    ctx = _humanizer('medium', seed=21)
    d = _click_device(ctx, last_ts=100.0)
    monkeypatch.setattr('module.device.control.time', _fake_clock(102.0))  # since_last==2.0
    delivered = []
    monkeypatch.setattr(d, 'move_desktop_plan', lambda plan: delivered.append(plan))
    clicks = _record_click_backends(d)
    Control.click(d, 200, 200, control_check=False, control_name='Click')
    assert delivered == []
    assert len(clicks) == 1
    assert d._last_action_ts == 102.0


def test_click_boundary_exactly_30s_park_no_idle(monkeypatch):
    """间隔恰好 30s 且抽中 park：park 判定 <= 阈值不放手（返回 None），
    Control.click 不投递游移、只执行原 click。"""
    ctx = _humanizer('medium', seed=22)
    d = _click_device(ctx, last_ts=100.0)
    monkeypatch.setattr('module.device.control.time', _fake_clock(130.0))  # since_last==30.0
    _force_idle_option(monkeypatch, 'park')
    delivered = []
    monkeypatch.setattr(d, 'move_desktop_plan', lambda plan: delivered.append(plan))
    clicks = _record_click_backends(d)
    Control.click(d, 200, 200, control_check=False, control_name='Click')
    assert delivered == []
    assert len(clicks) == 1
    assert d._last_action_ts == 130.0


def test_click_just_above_30s_park_delivers_idle(monkeypatch):
    """刚超过 30s 且抽中 park：park 开始"放手"（移到画布边缘）→ Control.click
    投递游移、原 click 仍执行——证明 30s 边界在 Control 层同样成立。"""
    ctx = _humanizer('medium', seed=23)
    d = _click_device(ctx, last_ts=100.0)
    monkeypatch.setattr('module.device.control.time', _fake_clock(130.5))  # since_last=30.5s
    _force_idle_option(monkeypatch, 'park')
    delivered = []
    monkeypatch.setattr(d, 'move_desktop_plan', lambda plan: delivered.append(plan))
    clicks = _record_click_backends(d)
    Control.click(d, 200, 200, control_check=False, control_name='Click')
    assert len(delivered) == 1
    assert isinstance(delivered[0], MovePlan)
    assert len(clicks) == 1


def test_click_enabled_window_message_dispatches_to_in_body_not_adb(monkeypatch):
    """Control.click 在 enabled + window_message 时必须落到 click_window_message
    的方法体内 humanized 分支（0 个 @retry），绝不能落到 click_adb——桌面客户端
    由窗口消息控制，ADB input tap 控制不了它（否则桌面开档点击静默失效）。"""
    ctx = _humanizer('medium', seed=24)
    d = _click_device(ctx)          # last_ts=None → since_last=0 → plan_idle 返回 None
    wm, adb = [], []
    d.click_window_message = lambda x, y, fast=False: wm.append((x, y))
    d.click_adb = lambda x, y: adb.append((x, y))
    monkeypatch.setattr(d, 'move_desktop_plan', lambda plan: None)  # 防 idle 误投
    Control.click(d, 200, 200, control_check=False, control_name='Click')
    assert wm == [(200, 200)] and adb == [], f'分派错误: wm={wm} adb={adb}'


def test_click_first_action_no_idle_drift(monkeypatch):
    """首次点击没有历史时间戳 → since_last=0（低于 2s 阈值）→ 不游移直接点击，
    且时间戳被初始化为当前时钟（供下一次点击做间隔基准）。"""
    ctx = _humanizer('medium', seed=24)
    d = _click_device(ctx)  # 不设置 _last_action_ts
    monkeypatch.setattr('module.device.control.time', _fake_clock(1.0))
    delivered = []
    monkeypatch.setattr(d, 'move_desktop_plan', lambda plan: delivered.append(plan))
    clicks = _record_click_backends(d)
    Control.click(d, 200, 200, control_check=False, control_name='Click')
    assert delivered == []
    assert len(clicks) == 1
    assert d._last_action_ts == 1.0


def test_click_cursor_unknown_skips_idle(monkeypatch):
    """光标未知（无 _desktop_cursor）时 plan_idle 返回 None：不能凭空把光标
    从未知处拽到某坐标，Control.click 只执行原 click。"""
    ctx = _humanizer('medium', seed=25)
    d = _click_device(ctx, cursor=None, last_ts=100.0)
    monkeypatch.setattr('module.device.control.time', _fake_clock(103.0))  # since_last 3s，超阈值
    delivered = []
    monkeypatch.setattr(d, 'move_desktop_plan', lambda plan: delivered.append(plan))
    clicks = _record_click_backends(d)
    Control.click(d, 200, 200, control_check=False, control_name='Click')
    assert delivered == []
    assert len(clicks) == 1


def test_click_off_updates_timestamp_without_idle(monkeypatch):
    """off 档：跳过 plan_idle（不消费策略 RNG、不产生游移），但仍刷新时间戳。"""
    ctx = HumanizerContext(enabled=False, level='off', persona=None,
                           rng=np.random.Generator(np.random.PCG64(7)),
                           canvas_size=(1280, 720))
    d = _click_device(ctx, last_ts=100.0)
    monkeypatch.setattr('module.device.control.time', _fake_clock(105.0))
    # 证明 off 根本不调用 plan_idle：把它换成会爆炸的实现
    monkeypatch.setattr(d.humanizer, 'plan_idle', lambda *a, **k: (_ for _ in ()).throw(
        AssertionError('off 不应调用 plan_idle')))
    state_before = dict(d.humanizer.rng.bit_generator.state)
    delivered = []
    monkeypatch.setattr(d, 'move_desktop_plan', lambda plan: delivered.append(plan))
    clicks = _record_click_backends(d)
    Control.click(d, 200, 200, control_check=False, control_name='Click')
    assert delivered == []
    assert len(clicks) == 1
    assert d._last_action_ts == 105.0
    # off 零 RNG 消费：即便给了带 rng 的 off 上下文，策略入口也不许碰它
    assert d.humanizer.rng.bit_generator.state == state_before


def test_click_idle_move_does_not_trigger_extra_control_check(monkeypatch):
    """idle 游移只投 WM_MOUSEMOVE（无 DOWN/UP）：Control.click 层
    handle_control_check 恰被调用一次（仅原 click），不被游移重复触发；
    游移也不产生额外点击。"""
    ctx = _humanizer('medium', seed=26)
    d = _click_device(ctx, last_ts=100.0)
    monkeypatch.setattr('module.device.control.time', _fake_clock(102.5))  # since_last 2.5s
    _force_idle_option(monkeypatch, 'idle_drift')
    checks = []
    d.handle_control_check = lambda name: checks.append(name)
    delivered = []
    monkeypatch.setattr(d, 'move_desktop_plan', lambda plan: delivered.append(plan))
    clicks = _record_click_backends(d)
    Control.click(d, 200, 200, control_check=True, control_name='Click')
    assert len(delivered) == 1
    assert checks == ['Click']   # 仅原 click 一次 control check
    assert len(clicks) == 1      # 游移不触发点击


def test_click_last_action_ts_not_shared_across_devices(monkeypatch):
    """多 Device 时间戳互不共享：d1 的长间隔产生游移，d2 的短间隔不产生，
    且各自的时间戳独立更新。"""
    ctx = _humanizer('medium', seed=27)
    d1 = _click_device(ctx, last_ts=100.0)
    d2 = _click_device(ctx, last_ts=199.5)
    monkeypatch.setattr('module.device.control.time', _fake_clock(200.0))
    _force_idle_option(monkeypatch, 'idle_drift')
    d1_delivered, d2_delivered = [], []
    monkeypatch.setattr(d1, 'move_desktop_plan', lambda plan: d1_delivered.append(plan))
    monkeypatch.setattr(d2, 'move_desktop_plan', lambda plan: d2_delivered.append(plan))
    c1 = _record_click_backends(d1)
    c2 = _record_click_backends(d2)
    Control.click(d1, 100, 100, control_check=False, control_name='Click')
    Control.click(d2, 100, 100, control_check=False, control_name='Click')
    # d1 距上次 100s（> 阈值）→ 游移；d2 距上次 0.5s（< 阈值）→ 不游移
    assert d1_delivered and not d2_delivered
    assert len(c1) == 1 and len(c2) == 1
    # 两个实例各自把时间戳更新为当前时钟，互不影响
    assert d1._last_action_ts == 200.0
    assert d2._last_action_ts == 200.0


def _config(*, serial, level='off'):
    return types.SimpleNamespace(
        config_name='test_humanize',
        script=types.SimpleNamespace(
            device=types.SimpleNamespace(
                serial=serial,
                humanize_level=level,
                screenshot_method='adb',
                emulatorinfo_type='manual',
                package_name=types.SimpleNamespace(
                    value='com.netease.onmyoji.wyzymnqsd_cps'),
            )
        )
    )


def _init_device_harness(monkeypatch, *, desktop=False, init_raises=False):
    """把 Device.__init__ 的依赖桩成可测路径（与 test_humanize_context 同款模式）。"""
    from module.device.emulator_health import EmulatorHealth
    from module.device.emulator_reset import FullReset
    from module.exception import EmulatorNotRunningError

    class _FakeHealth:
        def __init__(self, device):
            pass

        def is_alive(self):
            return True

        def why_dead(self):
            return 'fake health'

    class _FakeReset:
        def __init__(self, device):
            pass

    def _fake_full_recovery(self):
        return True

    monkeypatch.setattr('module.device.emulator_health.EmulatorHealth', _FakeHealth)
    monkeypatch.setattr('module.device.emulator_reset.FullReset', _FakeReset)
    monkeypatch.setattr(Device, 'full_recovery', _fake_full_recovery)
    monkeypatch.setattr(Device, 'screenshot_interval_set', lambda self: None)
    monkeypatch.setattr(Device, 'run_simple_screenshot_benchmark', lambda self: None)
    if desktop:
        monkeypatch.setattr(Device, '_desktop_ensure_launched', lambda self: True)
        monkeypatch.setattr(Device, '_init_desktop', lambda self: None)
    calls = []

    def fake_platform_init(self, config):
        calls.append(config)
        self.config = config
        self.package = config.script.device.package_name.value
        if init_raises and len(calls) == 1:
            raise EmulatorNotRunningError('fake emulator not running')
    monkeypatch.setattr(Device.__mro__[1], '__init__', fake_platform_init)
    return calls


def test_device_init_sets_last_action_ts_on_all_paths(monkeypatch):
    """Device.__init__ 在四条初始化路径（含 desktop 早返回分支）都为实例初始化
    独立的 _last_action_ts（None），实例间互不共享。"""
    # 路径 1 + 4：首次 super 成功（普通初始化末尾幂等保证）
    _init_device_harness(monkeypatch, desktop=True)
    d1 = Device(_config(serial='127.0.0.1:1'))
    assert d1._last_action_ts is None
    # 路径 3：desktop 早返回分支
    d3 = Device(_config(serial='desktop'))
    assert d3._last_action_ts is None
    # 路径 2：首次 super 抛 EmulatorNotRunningError，恢复后第二次 super 成功
    calls = _init_device_harness(monkeypatch, desktop=True, init_raises=True)
    d2 = Device(_config(serial='127.0.0.1:2'))
    assert len(calls) == 2
    assert d2._last_action_ts is None
    # 实例独立：改一个不影响其他
    d1._last_action_ts = 1.0
    assert d2._last_action_ts is None
    assert d3._last_action_ts is None


# ============================================================
# Task 13：所有 Rule 坐标入口接入维度 A（Plan Task 13）
# ============================================================

def _gif_rule():
    """构造带落点 roi_front 的 RuleGif（coord 走 roi_front 采样）。"""
    rule = RuleGif(targets=[RuleImage(roi_front=[100, 50, 80, 40], roi_back=(200, 100, 60, 30),
                                      method='Template matching', threshold=0.8, file='x.png')])
    rule.roi_front = [100, 50, 80, 40]
    return rule


def _coord_cases():
    """7 个坐标入口 → (新建 Rule 并采样的 fn, 每个返回点对应的 ROI 列表)。"""
    click = lambda: RuleClick(roi_front=(100, 50, 80, 40), roi_back=(10, 20, 30, 60))
    image = lambda: RuleImage(roi_front=[100, 50, 80, 40], roi_back=(200, 100, 60, 30),
                              method='Template matching', threshold=0.8, file='x.png')
    return {
        'ruleclick_front': (lambda: click().coord(), [(100, 50, 80, 40)]),
        'ruleclick_back': (lambda: click().coord_more(), [(10, 20, 30, 60)]),
        'ruleswipe': (lambda: RuleSwipe(roi_front=(100, 50, 80, 40), roi_back=(200, 100, 60, 30),
                                        mode='default').coord(),
                      [(100, 50, 80, 40), (200, 100, 60, 30)]),
        'ruleimage_front': (lambda: image().coord(), [(100, 50, 80, 40)]),
        'ruleimage_back': (lambda: image().coord_more(), [(200, 100, 60, 30)]),
        'ruleocr': (lambda: RuleOcr(name='TestOcr', mode='FULL', method='DEFAULT',
                                    roi=(100, 50, 80, 40), area=(100, 50, 80, 40),
                                    keyword='').coord(), [(100, 50, 80, 40)]),
        'rulegif': (lambda: _gif_rule().coord(), [(100, 50, 80, 40)]),
    }


def _off_ctx():
    """off 档门面：enabled=False，sample_point/gap_seconds 一律返回 None。"""
    return HumanizerContext(enabled=False, level='off', persona=None,
                            rng=np.random.Generator(np.random.PCG64(0)),
                            canvas_size=(1280, 720))


def _in_roi(point, roi):
    """断言采样点在 ROI 闭区间 [x, x+w-1] × [y, y+h-1] 内。"""
    x, y, w, h = roi
    return x <= point[0] <= x + w - 1 and y <= point[1] <= y + h - 1


@pytest.mark.parametrize('name', sorted(_coord_cases()))
def test_coord_off_bound_context_byte_identical(name):
    """Task 13：绑定 off context 后坐标入口与原 np.random.randint 逐位一致——
    坐标与全局 RNG 后续抽取指纹都与无绑定完全一致（off 零回归）。"""
    fn, _rois = _coord_cases()[name]

    def run():
        np.random.seed(12345)
        return fn(), tuple(float(np.random.random()) for _ in range(16))

    result_unbound, fp_unbound = run()
    with bind_humanizer(_off_ctx()):
        result_off, fp_off = run()
    assert result_off == result_unbound
    assert fp_off == fp_unbound


@pytest.mark.parametrize('name', sorted(_coord_cases()))
def test_coord_enabled_uses_persona_rng_not_global(name):
    """Task 13：开档（light/medium/heavy）坐标入口用人格 RNG，逐位不碰全局
    np.random；结果落在 ROI 闭区间内，同 seed 可复现。"""
    fn, rois = _coord_cases()[name]
    for level in ('light', 'medium', 'heavy'):
        ctx = _humanizer(level, seed=7)
        np.random.seed(999)
        expected = tuple(float(np.random.random()) for _ in range(16))
        np.random.seed(999)
        with bind_humanizer(ctx):
            result = fn()
        actual = tuple(float(np.random.random()) for _ in range(16))
        assert actual == expected, f'{name}@{level} 消耗了全局 np.random'
        points = [result[i:i + 2] for i in range(0, len(result), 2)]
        assert len(points) == len(rois)
        for point, roi in zip(points, rois):
            assert _in_roi(point, roi), f'{name}@{level} 落点 {point} 越出 ROI {roi}'
        with bind_humanizer(_humanizer(level, seed=7)):
            assert fn() == result, f'{name}@{level} 同 seed 不可复现'


def test_coord_off_not_changed_by_other_thread_enabled():
    """Task 13 验收：线程 A 的 off（无绑定）坐标序列不被线程 B 的 heavy 绑定改变。"""
    fn, _rois = _coord_cases()['ruleclick_front']

    def worker():
        # 线程 B 绑定 heavy 并消费人格 RNG，绑定只留在线程 B 的 ContextVar 里
        ctx = _humanizer('heavy', seed=31)
        with bind_humanizer(ctx):
            ctx.sample_point((100, 50, 80, 40))

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    np.random.seed(4242)
    first = (fn(), tuple(float(np.random.random()) for _ in range(16)))
    np.random.seed(4242)
    second = (fn(), tuple(float(np.random.random()) for _ in range(16)))
    assert first == second


# ============================================================
# Task 19：ADB 接入（仅维度 A + I；维度 B 已放弃）
# ============================================================

def _adb_click_stub(rec, humanizer=None):
    """构造裸 Adb 并打桩 adb_shell/self.sleep/is_desktop，可带 humanizer。"""
    a = object.__new__(Adb)
    a.adb_shell = lambda cmd, *args, **kwargs: rec.append(('shell', tuple(cmd)))
    a.sleep = lambda s: rec.append(('sleep', s))
    a.is_desktop = False
    if humanizer is not None:
        a.humanizer = humanizer
    return a


def _fake_adb_clock(value):
    """只暴露 .time() 的假时钟：固定值让 click_adb 的耗时判定恒走 sleep 分支。"""
    ns = types.SimpleNamespace()
    ns.time = lambda: value
    return ns


def _adb_ctx(level, seed):
    """ADB 测试用门面：off 与开档分别构造（off 不加载人格/RNG）。"""
    if level == 'off':
        return _off_ctx()
    return _humanizer(level, seed)


def test_adb_input_tap_byte_identical_all_levels(monkeypatch):
    """Task 19：四档 `input tap x y` 命令逐字节相同，只有尾随 sleep 时长不同。"""
    commands = {}
    for level in ('off', 'light', 'medium', 'heavy'):
        rec = []
        a = _adb_click_stub(rec, humanizer=_adb_ctx(level, seed=11))
        monkeypatch.setattr(adb_mod, 'time', _fake_adb_clock(100.0))
        a.click_adb(100, 200)
        commands[level] = rec
    tap = ('shell', ('input', 'tap', 100, 200))
    for level, rec in commands.items():
        assert rec[0] == tap, f'{level} 的 input tap 命令偏离基线: {rec}'
        assert rec[1][0] == 'sleep'
    assert commands['off'][1] == ('sleep', 0.05), 'off 必须用旧常量 0.05'
    for level in ('light', 'medium', 'heavy'):
        s = commands[level][1][1]
        assert 0.025 <= s <= 0.11, f'{level} sleep {s} 越出 clip 区间'


def test_adb_enabled_gap_jitters_in_clip_interval(monkeypatch):
    """Task 19：开档 sleep 落在 clip(lognormal(ln(0.05), 0.22), 0.025, 0.11) 内，
    且跨 seed 产生方差（不是恒等于旧常量）。"""
    seen = set()
    for seed in range(30):
        rec = []
        a = _adb_click_stub(rec, humanizer=_humanizer('heavy', seed=1000 + seed))
        monkeypatch.setattr(adb_mod, 'time', _fake_adb_clock(100.0))
        a.click_adb(100, 200)
        s = rec[1][1]
        assert 0.025 <= s <= 0.11, f'seed={seed} sleep {s} 越出 clip 区间'
        seen.add(s)
    assert len(seen) > 1, '开档间隔应产生方差而非恒定 0.05'


def test_adb_press_seconds_never_called(monkeypatch):
    """Task 19：维度 B 已从 ADB 放弃——click_adb 全程不调用 press_seconds。"""
    ctx = _humanizer('heavy', seed=13)
    calls = []
    monkeypatch.setattr(ctx, 'press_seconds', lambda *a, **k: calls.append(1))
    rec = []
    a = _adb_click_stub(rec, humanizer=ctx)
    monkeypatch.setattr(adb_mod, 'time', _fake_adb_clock(100.0))
    a.click_adb(100, 200)
    assert calls == [], 'ADB 点击不得消费维度 B'


def test_adb_click_source_has_no_zero_distance_swipe():
    """Task 19：click_adb 源码中不存在 `input swipe x y x y` 形式的零距离点击命令
    （维度 B 放弃的静态保证，避免引入会静默失败的新命令）。"""
    source = inspect.getsource(Adb.click_adb)
    assert "input', 'swipe" not in source
    assert "input', 'tap" in source


def test_off_swipe_bound_disabled_matches_unbound(monkeypatch):
    """契约 #1/#2 关键形态：生产 off 档 Device 已绑定 disabled humanizer（契约 #2
    强制每实例绑定），swipe 必须与"完全未绑定"逐字节一致——事件序列与全局 RNG
    消耗量都不得因绑定 disabled context 而偏移。防 legacy_delays 预计算在 off 下
    偷吃全局随机数（Phase 3 复审 HIGH 的回归锁）。"""
    import random as _stdlib_random

    def run(bound):
        _stdlib_random.seed(20260825)
        np.random.seed(20260825)
        w = _emu_window(
            humanizer=types.SimpleNamespace(enabled=False) if bound else None)
        return _record(monkeypatch, lambda: w.swipe_window_message([100, 100], [300, 400]))

    unbound = run(False)
    bound = run(True)
    assert bound == unbound, f'绑定 disabled humanizer 改变了 off 滑动事件:\nunbound={unbound}\nbound={bound}'


def test_adb_swipe_command_unchanged_across_levels(monkeypatch):
    """Task 19：ADB 不接入 C/D/H——开档滑动仍发同一条 `input swipe`，只保留既有
    duration 语义，不伪造中间轨迹。"""
    def run(level):
        rec = []
        a = _adb_click_stub(rec, humanizer=_adb_ctx(level, seed=12))
        a.swipe_adb((10, 20), (30, 40), duration=0.1)
        return rec

    off = run('off')
    on = run('heavy')
    assert off == [('shell', ('input', 'swipe', 10, 20, 30, 40, 100))]
    assert on == off
