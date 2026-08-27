# -*- coding: utf-8 -*-
"""Task 21：请求预算、墙钟估算与耗时交叉验收矩阵。

预算分成三类，不能混成一个断言：

1. 请求值门禁：sum(plan.delays) 等于请求预算（误差 ≤1ms）；plan.total_seconds 等于
   最终计划 delays 之和；H 替换末段后只统计最终 delays。
2. minitouch 设备 wait 门禁：`w` 只出现整数毫秒；零 delay 不产生 wait（量化结果为 0），
   正 delay 至少 1ms；整条整数 wait 总和与 floor(sum(delays)*1000+0.5) 误差 ≤1ms
   （累计余量结转，不逐点比较）；DOWN/MOVE/UP 恰好三批，禁止用增加 send 批次修复量化误差。
3. 墙钟最低估算：注入 estimate_sleep_wall_time(s) = max(s, 0.0029)。桌面 light 移动
   按最多 12 点计算 35~47ms 的最低估算区间；minitouch 滑动单独估算"量化后设备端 wait
   总和 + 3×DEFAULT_DELAY"（host sleep 入参另记录但不重复计入），不套用 Python 逐点
   sleep 地板、不漏约 150ms 固定批次开销。scrcpy 已整体移出实施范围（Task 17），不列入。

动态降点门禁：短预算 + 固定 seed。Python backend 断言 min(delay) >= PROFILE_MIN_DELAY_S
才接受 profile；minitouch 改为断言整数 wait 可表示（target_ms >= 正 delay 数）。
禁止用 int(total_budget / PROFILE_MIN_DELAY_S) 反推点数；count=2 仍失败时标记近恒定退化。

耗时交叉验收矩阵：矩阵中的 0 必须断言"最终计划与墙钟模型同上一档"，不能仅以"没有抛
异常"代替；所有非零项指回 Spec §4.7 的具体消费 API。
"""
import inspect
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

import module.device.humanize as hum
import module.device.method.adb as adb_mod
import module.device.method.minitouch as minitouch_mod
import module.device.method.uiautomator_2 as u2_module
import module.device.method.windows_impl as windows_impl
from module.device.humanize import HumanizerContext
from module.device.humanize.gesture import plan_pointer_tail
from module.device.humanize.persona import Persona
from module.device.humanize.plan import DwellPlan, MovePlan, TailPlan
from module.exception import RequestHumanTakeover
from module.device.humanize.timing import (
    PROFILE_MAX_POINTS,
    PROFILE_MIN_DELAY_S,
    plan_dwell,
)
from module.device.method.adb import Adb
from module.device.method.minitouch import CommandBuilder, Minitouch
from module.device.method.uiautomator_2 import (
    U2_ACTION_DOWN,
    U2_ACTION_MOVE,
    U2_ACTION_UP,
    Uiautomator2,
)
from module.device.method.windows_impl import Window
from module.device.handle import EmulatorFamily

pytestmark = pytest.mark.unit

SEED = 20260825
PERSONA = Persona.generate(SEED)
CANVAS = (1280, 720)

# Windows Python 逐点 sleep 的墙钟最低估算地板（Spec §7.3.1 本机实测中位 2.92ms 的下取整）。
# 注意：这只是最低估算，不是所有机器的通用上界——受系统定时器精度、timeBeginPeriod 与负载
# 影响，目标机的实际中位数/P95 必须用 §11 的校准表单独记录，不能外推到其它机器。
ESTIMATE_SLEEP_FLOOR_S = 0.0029


def estimate_sleep_wall_time(seconds):
    """注入的墙钟最低估算：请求 sleep 时长经系统地板放大后至少 2.9ms。"""
    return max(float(seconds), ESTIMATE_SLEEP_FLOOR_S)


def rng(seed=7):
    return np.random.Generator(np.random.PCG64(seed))


def humanizer(level, seed):
    """构造门面：固定人格 + 固定 seed 的 RNG，保证确定性。"""
    return HumanizerContext(
        enabled=True, level=level, persona=PERSONA,
        rng=rng(seed), canvas_size=CANVAS)


def _force_option(monkeypatch, dim, option):
    """强制门面 _choose 在指定维度返回 option（其余维走原逻辑）。"""
    orig = HumanizerContext._choose

    def forced(self, d, allowed):
        return option if d == dim else orig(self, d, allowed)
    monkeypatch.setattr(HumanizerContext, '_choose', forced)


def _capture_warning(monkeypatch):
    """捕获门面内 logger.warning 的消息列表（断言 near-constant 退化被显式记录）。"""
    warnings = []
    monkeypatch.setattr(hum.logger, 'warning',
                        lambda *a, **k: warnings.append(a[0] if a else ''))
    return warnings


def _capture_swipe_plans(ctx):
    """包装 ctx.plan_swipe，记录每次调用返回的计划与入参（不改变真实行为）。"""
    plans = []
    real = ctx.plan_swipe

    def wrap(*a, **kw):
        p = real(*a, **kw)
        plans.append((p, kw))
        return p
    ctx.plan_swipe = wrap
    return plans


def _record(monkeypatch, fn):
    """运行 fn，把 PostMessage/SendMessage/time.sleep 记成事件序列。"""
    calls = []
    monkeypatch.setattr(windows_impl, 'PostMessage',
                        lambda hwnd, msg, wp, lp: calls.append(('Post', hwnd, msg, wp, lp)))
    monkeypatch.setattr(windows_impl, 'SendMessage',
                        lambda hwnd, msg, wp, lp: calls.append(('Send', hwnd, msg, wp, lp)))
    monkeypatch.setattr(windows_impl.time, 'sleep',
                        lambda s: calls.append(('sleep', s)))
    fn()
    return calls


def _emu_window(humanizer, handles=(0x101, 0x102), scale=1.0):
    """构造模拟器 window_message 桩（mumu 两句柄）。

    scale 模拟 window_scale_rate（Windows DPI 缩放比，125%/150% 是高分屏
    常见默认值）——坐标换算类 bug 只在 scale≠1.0 下可见。
    """
    w = object.__new__(Window)
    w.is_desktop_window = False
    w.window_scale_rate = scale
    w.control_handle_list = list(handles)
    w.root_handle_num = handles[0]
    w.screenshot_size = (1280, 720)
    w.emulator_family = EmulatorFamily.FAMILY_MUMU
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


# ============================================================
# 1. 请求值门禁
# ============================================================

def test_request_gate_plan_swipe_sum_matches_budget(monkeypatch):
    """medium 滑动：强制 natural 让 H 不替换末段，sum(plan.delays) 必须等于请求预算
    base × PROFILE_MAX_POINTS（±1ms 抖动带宽内，误差 ≤1ms/点 × 点数级）；
    total_seconds 等于最终 delays 之和。恒定回报率模型下 delays = 1/rate ± 1ms，
    sum 的期望就是预算（抖动零均值），断言放宽到抖动带宽。"""
    _force_option(monkeypatch, 'swipe_tail', 'natural')
    ctx = humanizer('medium', seed=1)
    plan = ctx.plan_swipe((100, 100), (300, 400), base_delay_s=0.010,
                          timing_mode='python_sleep')
    assert plan is not None
    budget = 0.010 * PROFILE_MAX_POINTS  # 120ms
    n = len(plan.delays)
    assert abs(sum(plan.delays) - budget) <= 0.001 * n + 0.001, \
        f'sum(plan.delays)={sum(plan.delays)} 应在抖动带宽内等于请求预算 {budget}'
    assert plan.total_seconds == sum(plan.delays), 'total_seconds 应等于最终 delays 之和'


def test_request_gate_plan_move_sum_matches_budget():
    """桌面指针移动：请求预算来自 _desktop_move_budget_ms（消费点决定预算，§4.7
    move_desktop_window_message → plan_move）；medium 为 40~120ms×人格速度缩放，
    最终计划的 sum(delays) 等于该预算。"""
    ctx = humanizer('medium', seed=2)
    start, end = (0, 0), (300, 0)
    budget_ms = min(max(300.0 * 0.35, 40.0), 120.0) * ctx.persona.move_speed_scale
    plan = ctx.plan_move(start, end, gesture_kind='pointer_move', budget_ms=budget_ms)
    assert plan is not None
    assert abs(sum(plan.delays) - budget_ms / 1000.0) <= 0.001
    assert plan.total_seconds == sum(plan.delays)


def test_request_gate_after_h_only_final_delays_counted(monkeypatch):
    """H 替换末段后只统计最终 delays：total_seconds 等于替换后 delays 之和，不再等于
    替换前的基础预算（契约 #9：H 是替换不是叠加）。"""
    _force_option(monkeypatch, 'swipe_tail', 'random_tail')
    ctx = humanizer('light', seed=3)
    legacy_points = [(110, 110), (200, 200), (300, 400)]
    legacy_delays = [0.010, 0.010, 0.010]
    plan = ctx.plan_swipe((100, 100), (300, 400), base_delay_s=0.010,
                          legacy_points=legacy_points, legacy_delays=legacy_delays,
                          timing_mode='python_sleep')
    assert plan is not None
    # 最终 delays 才是唯一计数对象
    assert plan.total_seconds == sum(plan.delays)
    # 末段确实被 random_tail（0.050~0.130）替换：与传入的基础末段（0.010）不同
    assert list(plan.delays) != legacy_delays
    assert len(plan.points) == len(legacy_points)
    # 替换后总和超过基础预算（0.030），证明统计的不是替换前预算
    assert plan.total_seconds > 0.030


# ============================================================
# 2. minitouch 设备 wait 门禁
# ============================================================

class _BudgetCmdBuilder:
    """记录命令文本与 wait 累计毫秒的 fake builder（humanized 命令形状测试）。"""

    def __init__(self):
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
        self.commands.clear()
        self.delay = 0


class _BudgetMinitouchDevice(Minitouch):
    """记录 minitouch_send 的 payload、批次数与 host sleep 入参的夹具。

    minitouch_send 的真实 host sleep 入参是 (builder.delay / 1000 + gap)：
    其中 builder.delay / 1000 正是同一批 w 的主机侧等待，gap 是批次 transport guard。
    这里把两者分开记录，供墙钟估算断言"不重复计入"（Spec §4.8）。
    """

    def __init__(self, ctx):
        self.humanizer = ctx
        self.builder = _BudgetCmdBuilder()
        self.__dict__['minitouch_builder'] = self.builder
        self.sent = []          # 每个 send 批次的命令元组
        self.host_sleeps = []   # (batch_wait_s, gap_s)：minitouch_send 的真实 sleep 入参
        self.send_count = 0

    def minitouch_send(self, post_send_gap_s=None):
        self.send_count += 1
        batch_wait_s = self.builder.delay / 1000.0
        if post_send_gap_s is None:
            post_send_gap_s = getattr(self, '_humanized_minitouch_gap_s', None)
        if post_send_gap_s is None:
            post_send_gap_s = self.builder.DEFAULT_DELAY
        # 真实 host sleep：测试用 monkeypatch 捕获 minitouch_mod.time.sleep 入参，
        # 与 sent 命令解析出的设备 w 走两条观测通道对账（Spec §4.8 不重复计入）
        minitouch_mod.time.sleep(batch_wait_s + post_send_gap_s)
        self.host_sleeps.append((batch_wait_s, post_send_gap_s))
        self.sent.append(tuple(self.builder.commands))
        self.builder.clear()

    def _swipe_minitouch_legacy_impl(self, p1, p2, duration=None):
        self.sent.append(('legacy',))


def _swipe_waits(dev):
    """从所有 send 批次里取出 w 命令的整数毫秒列表。"""
    return [int(c.split()[1]) for payload in dev.sent for c in payload
            if c.startswith('w ')]


def test_minitouch_device_wait_gate_total_matches_plan(monkeypatch):
    """设备 wait 门禁：w 只出现整数毫秒、正 delay 至少 1ms；整条整数 wait 总和与
    floor(sum(plan.delays)*1000 + 0.5) 误差 ≤1ms（累计余量结转，不逐点比较）。"""
    # pin 触摸 liftoff 为 none：liftoff delay 若很小会量化成 0 不产生 wait，让
    # 下方 len(waits)==positive 的计数断言隐式依赖 RNG seed——固定分支消除脆弱性
    _force_option(monkeypatch, 'touch_liftoff', 'none')
    ctx = humanizer('medium', seed=4)
    plans = _capture_swipe_plans(ctx)
    dev = _BudgetMinitouchDevice(ctx)
    dev._swipe_minitouch_humanized_impl((100, 100), (300, 400), duration=0.1)
    plan = plans[0][0]
    waits = _swipe_waits(dev)
    # w 只出现整数毫秒（_quantize_move_delays 输出整数，builder.wait 原样入命令）
    assert all(isinstance(w, int) and w >= 1 for w in waits), \
        f'w 必须是正整数毫秒: {waits}'
    # 整条整数 wait 总和 == 目标（契约 #6 step 3 累计余量结转，误差 0，满足 ≤1ms 门禁）
    target = int(sum(plan.delays) * 1000 + 0.5)
    assert abs(sum(waits) - target) <= 1, \
        f'整数 wait 总和 {sum(waits)} 偏离目标 {target}'
    # 正 delay 数 = 生成的 wait 数（零 delay 量化结果为 0，不增加 wait 时间）
    positive = sum(1 for d in plan.delays if d > 0)
    assert len(waits) == positive


def test_minitouch_quantize_zero_delay_emits_no_wait_time():
    """零 delay 不产生 wait（量化结果为 0）；正 delay 至少 1ms（契约 #6 step 2）。"""
    from module.device.method import minitouch as minitouch_mod
    out = minitouch_mod._quantize_move_delays([0.0, 0.010, 0.0, 0.0005])
    assert out[0] == 0 and out[2] == 0
    assert out[1] >= 1 and out[3] >= 1
    # 累计余量结转：整条总和严格等于目标总毫秒
    assert sum(out) == int(sum([0.0, 0.010, 0.0, 0.0005]) * 1000 + 0.5)


def test_minitouch_swipe_exactly_three_send_batches(monkeypatch):
    """DOWN/MOVE/UP 恰好三批，禁止用增加 send 批次修复量化误差（契约 #6）：
    无论计划点数多少，MOVE 批内连续追加 wait→move→commit 后只调用一次 send。"""
    ctx = humanizer('heavy', seed=4)
    dev = _BudgetMinitouchDevice(ctx)
    dev._swipe_minitouch_humanized_impl((100, 100), (300, 400), duration=0.1)
    assert dev.send_count == 3, f'必须恰好三批，实际 {dev.send_count}'
    # 三批顺序：DOWN 批 / 所有 MOVE（含 wait）/ UP 批
    assert dev.sent[0][0].startswith('d ')
    assert all(c[0] in ('w', 'm', 'c') for c in dev.sent[1])
    assert dev.sent[2] == ('u', 'c')


def test_minitouch_swipe_duration_sets_budget_and_point_count(monkeypatch):
    """duration 是目标总时长，不得写死预算：duration=2 的滑动预算必须是 2s
    （此前 base_delay_s=0.010 写死 → 预算恒 120ms，2s 被压成 120ms）。
    恒定回报率模型：interval = round(1000/rate) ms（PERSONA 固定分位数映射
    触摸面板区间），count = 2000ms // interval，w 全部落在 interval ± 2ms
    （±1ms 调度抖动 × 量化结转）。pin 掉 liftoff/H 看手势主体真值。"""
    from module.device.humanize import timing as timing_mod
    _force_option(monkeypatch, 'touch_liftoff', 'none')
    _force_option(monkeypatch, 'swipe_tail', 'natural')
    interval_ms = max(1, int(1000.0 / timing_mod.report_rate_hz(PERSONA.report_rate_q) + 0.5))
    ctx = humanizer('medium', seed=8)
    dev = _BudgetMinitouchDevice(ctx)
    dev._swipe_minitouch_humanized_impl((100, 100), (300, 400), duration=2.0)
    waits = _swipe_waits(dev)
    expected = min(2000 // interval_ms, timing_mod.SWIPE_MAX_POINTS_CAP)
    assert len(waits) == expected, \
        f'2s/{interval_ms}ms 应摊 {expected} 点，实际 {len(waits)}'
    # 恒定回报率：每个 w 都在 interval ± 2ms 内（抖动 + 量化结转）
    assert all(abs(w - interval_ms) <= 2 for w in waits), \
        f'w 应围绕 {interval_ms}ms ±2ms，实际 {waits[:10]}'
    assert all(w >= 1 for w in waits), '量化后每点至少 1ms'


def test_minitouch_swipe_short_duration_constant_rate(monkeypatch):
    """短 duration 下点数由恒定回报率决定：count = 100ms // interval（真实设备
    0.1s @ ~112Hz 就是 ~11 个事件，不再有 30 点地板——盲目多点才是合成器
    特征）。pin 掉 liftoff/H 排除附加量。"""
    from module.device.humanize import timing as timing_mod
    _force_option(monkeypatch, 'touch_liftoff', 'none')
    _force_option(monkeypatch, 'swipe_tail', 'natural')
    interval_ms = max(1, int(1000.0 / timing_mod.report_rate_hz(PERSONA.report_rate_q) + 0.5))
    ctx = humanizer('medium', seed=8)
    dev = _BudgetMinitouchDevice(ctx)
    dev._swipe_minitouch_humanized_impl((100, 100), (300, 400), duration=0.1)
    waits = _swipe_waits(dev)
    expected = max(timing_mod.SWIPE_MIN_POINTS, min(100 // interval_ms,
                                                    timing_mod.SWIPE_MAX_POINTS_CAP))
    assert len(waits) == expected, \
        f'0.1s/{interval_ms}ms 应摊 {expected} 点，实际 {len(waits)}'
    assert abs(sum(waits) - 100) <= len(waits) * 2 + 1, '总 wait 应约等于 0.1s 预算'


def test_u2_swipe_base_scaled_from_duration():
    """u2 开档滑动把 duration 换算成 base：预算 = duration×12/12 = duration。
    此前误传 base=duration → 预算 = duration×12（2s 滑动膨胀成 24s）。"""
    dev = _U2BaseCapture()
    Uiautomator2._swipe_uiautomator2_humanized_impl(dev, (10, 10), (30, 30), duration=0.1)
    assert dev.captured_base == pytest.approx(0.1 / PROFILE_MAX_POINTS)
    dev2 = _U2BaseCapture()
    Uiautomator2._swipe_uiautomator2_humanized_impl(dev2, (10, 10), (30, 30), duration=2.0)
    assert dev2.captured_base == pytest.approx(2.0 / PROFILE_MAX_POINTS)


class _U2BaseCapture:
    """只捕获 plan_swipe 入参的最小桩：plan 返回 None 走 legacy 无害路径。"""

    def __init__(self):
        self.captured_base = None
        self.humanizer = SimpleNamespace(
            plan_swipe=lambda *a, **kw: self._capture(**kw))

    def _capture(self, **kw):
        self.captured_base = kw.get('base_delay_s')
        return None

    def _swipe_uiautomator2_legacy_impl(self, p1, p2, duration=0.1):
        return None


# ============================================================
# 3. 墙钟最低估算
# ============================================================

def test_desktop_light_move_wall_clock_minimum_band():
    """桌面 light 移动按最多 12 点计算最低估算落在 35~47ms 区间（Spec §7.3.2）。

    距离 → 点数 → 每点请求 → 逐点地板四步推导：dist ≥ 720px 走满 12 点
    （DESKTOP_MOVE_STEP=60 每 60px 一点、DESKTOP_MOVE_MAX_POINTS=12 截断），每点请求
    1.25~2.5ms 全部落到 2.9ms 地板，12×2.9=34.8ms≈35ms 是低端；47ms 是每点 2.5ms
    请求在本机实测中位（≈3.9ms/点）给出的设计上界。2.9ms 不是所有机器的通用上界——
    目标机中位数/P95 必须用 §11 校准表单独记录。
    """
    n_points = min(int(720.0 / Window.DESKTOP_MOVE_STEP), Window.DESKTOP_MOVE_MAX_POINTS)
    assert n_points == 12, '720px 应走到满 12 点'
    for budget_ms in (15.0, 30.0):
        per_point_s = budget_ms / n_points / 1000.0
        estimate = sum(estimate_sleep_wall_time(per_point_s) for _ in range(n_points))
        assert 0.034 <= estimate <= 0.047, \
            f'12 点最低估算应落在 35~47ms（实际 {estimate * 1000:.2f}ms）'


def test_desktop_light_move_actual_plan_capped_and_floor_respected(monkeypatch):
    """行为侧：真实 light 移动的最终计划点数受 DESKTOP_MOVE_MAX_POINTS 截断，每个
    delay 都被 sleep；逐点地板后的估算不越设计上界 47ms，且 sum(delays) 等于请求预算。"""
    ctx = humanizer('light', seed=5)
    w = _desktop_window(ctx, cursor=(0, 0))
    events = _record(monkeypatch, lambda: w.move_desktop_window_message(720, 0))
    sleeps = [e[1] for e in events if e[0] == 'sleep']
    assert len(sleeps) <= Window.DESKTOP_MOVE_MAX_POINTS, '点数不得超过 12'
    assert len(sleeps) > 0, 'light 移动必须走计划路径（逐点 sleep）'
    estimate = sum(estimate_sleep_wall_time(s) for s in sleeps)
    assert estimate <= 0.047, f'实际估算 {estimate * 1000:.2f}ms 不得越 12 点满载上界'
    # 请求值守恒：sleep 总和等于 _desktop_move_budget_ms 预算（30ms）
    assert abs(sum(sleeps) - 0.030) <= 0.001


def test_minitouch_swipe_wall_clock_estimate_separate_model(monkeypatch):
    """minitouch 滑动单独估算：量化后设备端 wait 总和 + 3×DEFAULT_DELAY（约 150ms
    固定批次开销）。host sleep 入参另记录但不重复计入——minitouch_send 的 host sleep
    里的 wait 分量正是同一批 w 的主机侧等待（Spec §4.8），不得把同一批 wait 相加两次。
    不套用 Python 逐点 sleep 地板。scrcpy 已移出实施范围（Task 17），不列入本门禁。"""
    # 固定每批 transport guard 为 DEFAULT_DELAY：隔离维度 I 的 jitter，只验证墙钟
    # 估算公式对真实 minitouch_send 行为成立（gap 抖动本身由 test_humanize_timing 覆盖）
    monkeypatch.setattr(HumanizerContext, 'gap_seconds',
                        lambda self, default: CommandBuilder.DEFAULT_DELAY)
    # 捕获真实 minitouch_send 的 host time.sleep 入参（独立于内部记录的第二观测通道）
    real_sleeps = []
    monkeypatch.setattr(minitouch_mod.time, 'sleep',
                        lambda s: real_sleeps.append(s))
    ctx = humanizer('medium', seed=4)
    plans = _capture_swipe_plans(ctx)
    dev = _BudgetMinitouchDevice(ctx)
    dev._swipe_minitouch_humanized_impl((100, 100), (300, 400), duration=0.1)
    plan = plans[0][0]
    waits = _swipe_waits(dev)
    device_wait_s = sum(waits) / 1000.0
    # 每批恰好一次真实 host sleep（DOWN/MOVE/UP 三批），且真实入参 == 内部记录
    # (batch_wait + gap) 逐批一致——证明 minitouch_send 确实 sleep 同一批 wait + guard
    assert len(real_sleeps) == 3, f'应恰好三次真实 sleep，实际 {len(real_sleeps)}'
    assert len(dev.host_sleeps) == 3
    for sleep_s, (batch_wait, gap) in zip(real_sleeps, dev.host_sleeps):
        assert sleep_s == pytest.approx(batch_wait + gap), \
            '真实 host sleep 入参必须等于该批设备 wait + guard'
    # 墙钟估算 = 量化设备 wait + 3 批 guard（DEFAULT_DELAY）：对真实 minitouch_send
    # 的 sleep 入参总和断言（Spec §4.8），不再是自比较
    estimate = device_wait_s + 3 * CommandBuilder.DEFAULT_DELAY
    assert sum(real_sleeps) == pytest.approx(estimate), \
        f'真实墙钟 {sum(real_sleeps)*1000:.2f}ms 应等于估算 {estimate*1000:.2f}ms'
    # 公式里不含逐点 max(s, 0.0029)：minitouch 的 w 由设备端执行，不适用 Python 地板
    # （Spec §7.3.1 的 sleep 地板只约束 Python 逐点 sleep backend）
    assert 3 * CommandBuilder.DEFAULT_DELAY == pytest.approx(0.15), \
        '约 150ms 固定批次开销不得被漏掉'
    # 设备端事件时间、socket 发送与调度误差是另一份真机校准项（Spec §11），此处只留档
    assert dev.send_count == 3


# ============================================================
# 4. 动态降点门禁
# ============================================================

def test_downscale_accepts_profile_only_above_min_delay(monkeypatch):
    """恒定回报率（Python backend）：点数 = 预算 × 回报率，每个 delay 围绕
    1/rate ± 1ms 抖动（floor clamp 到 PROFILE_MIN_DELAY_S）。40ms 预算 ×
    PERSONA 回报率 ≈ 4 点，min(delay) 自然满足 Python 地板。"""
    from module.device.humanize.timing import report_rate_hz
    _force_option(monkeypatch, 'swipe_tail', 'natural')  # 去掉 H 干扰，看真值
    rate = min(report_rate_hz(PERSONA.report_rate_q), 1.0 / PROFILE_MIN_DELAY_S)
    interval_s = 1.0 / rate
    expected_count = max(2, int(0.040 / interval_s))
    ctx = humanizer('medium', seed=7)
    plan = ctx.plan_swipe((100, 100), (300, 400),
                          base_delay_s=0.040 / PROFILE_MAX_POINTS,
                          timing_mode='python_sleep')
    assert plan is not None
    assert len(plan.points) == expected_count, \
        f'点数应 = 40ms × {rate:.1f}Hz ≈ {expected_count}，实际 {len(plan.points)}'
    assert min(plan.delays) >= PROFILE_MIN_DELAY_S, \
        f'每个 delay 必须满足 >= {PROFILE_MIN_DELAY_S}'
    # 恒定回报率：所有 delay 围绕同一间隔 ±1ms（抖动带宽）
    assert max(plan.delays) - min(plan.delays) <= 0.002 + 1e-9, \
        f'delay 应为恒定间隔 ±1ms，实际 [{min(plan.delays):.4f}, {max(plan.delays):.4f}]'
    # 总和按文档化语义对账：count = floor(预算/间隔) 向下取整，sum ≈ count×间隔 ± 逐点抖动。
    # 只有间隔整除预算时 sum 才等于预算本身（旧区间 100~240Hz 下 200Hz 恰好整除 40ms；
    # 2026-08-27 区间改为 60~167Hz 后不再整除，断言必须按 count×间隔 收敛）
    assert abs(sum(plan.delays) - expected_count * interval_s) <= expected_count * 0.001 + 0.001


def test_swipe_tiny_budget_clamps_to_min_points(monkeypatch):
    """预算 < 2×回报率间隔（6ms < 2×9ms）：count clamp 到 SWIPE_MIN_POINTS=2，
    恒定回报率间隔不变——设备最短触摸事件的物理下限允许超出请求预算，
    不再走近恒定退化路径（恒定回报率下 delay 本来就 ≥ 地板）。"""
    _force_option(monkeypatch, 'swipe_tail', 'natural')
    ctx = humanizer('medium', seed=7)
    plan = ctx.plan_swipe((100, 100), (300, 400), base_delay_s=0.0005,
                          timing_mode='python_sleep')
    assert plan is not None
    assert len(plan.points) == 2, '极小预算 clamp 到最小点数 2'
    assert min(plan.delays) >= PROFILE_MIN_DELAY_S, '间隔仍不低于 Python 地板'
    assert sum(plan.delays) >= 0.006, '不小于请求预算（设备物理下限允许超出）'


def test_downscale_no_average_budget_back_derivation():
    """禁止用 int(total_budget / PROFILE_MIN_DELAY_S) 推导点数（契约 #6）：_downscale
    的实现体（去掉 docstring）不含以预算除以单点地板来定点数的表达式——点数只能由
    调用方上限（默认 PROFILE_MAX_POINTS，swipe 传恒定回报率模型算出的 count）起
    逐级降点，不能由预算反推。"""
    source = inspect.getsource(HumanizerContext._downscale)
    # 去掉 docstring：其中对禁止公式的文字描述（"禁止平均值公式"）不算实现
    body = source.split('"""', 2)[2]
    assert 'cap = timing.PROFILE_MAX_POINTS if max_points is None else max_points' in body, \
        '点数上限必须显式来自 PROFILE_MAX_POINTS（默认）或调用方参数'
    assert 'range(cap, 1, -1)' in body, '点数必须由上限起循环降点'
    assert '/ PROFILE_MIN_DELAY_S' not in body
    assert '/ timing.PROFILE_MIN_DELAY_S' not in body
    assert 'int(total_budget' not in body


def test_downscale_minitouch_uses_integer_representability_not_python_floor(monkeypatch):
    """minitouch device_wait 的判据是整数 wait 可表示（target_ms >= 正 delay 数），
    不是 Python 的 PROFILE_MIN_DELAY_S 单点地板（契约 #6 step 5）——设备端 w
    由设备执行，不受 Windows sleep 地板约束。恒定回报率下预算 6ms < 2×间隔 →
    clamp 到 2 点且间隔不变（设备最短触摸事件的物理下限允许超出请求预算），
    整数 wait 仍可表示。"""
    from module.device.humanize.timing import report_rate_hz
    _force_option(monkeypatch, 'swipe_tail', 'natural')
    interval_ms = max(1, int(1000.0 / report_rate_hz(PERSONA.report_rate_q) + 0.5))
    ctx = humanizer('medium', seed=9)
    plan = ctx.plan_swipe((100, 100), (300, 400), base_delay_s=0.0005,
                          timing_mode='device_wait')
    assert plan is not None
    assert len(plan.points) == 2, '极小预算 clamp 到最小点数 2'
    total_ms = int(sum(plan.delays) * 1000 + 0.5)
    positive = sum(1 for d in plan.delays if d > 0)
    assert total_ms >= positive, '整数 wait 必须可表示（否则量化返回 None 整体回退）'
    # device_wait 间隔是整毫秒恒定值 ±1ms 调度抖动，与 Python sleep 地板无关
    for d in plan.delays:
        assert abs(d * 1000 - interval_ms) <= 2, \
            f'delay 应围绕 {interval_ms}ms ±2ms，实际 {d * 1000:.2f}ms'
    assert sum(plan.delays) >= 0.006, '设备物理下限允许超出极小请求预算'


def test_minitouch_light_device_wait_budget_shortage_records_calibration(monkeypatch):
    """light + device_wait 预算不足 1ms/点：目标总毫秒 < 正 delay 数 → 整数 wait 不可
    表示，代码不得静默放大预算——维持近恒定 delay 并显式记录设备 profile 校准状态
    （契约 #6 step 5/6；设备端真实呈现仍须 §11 真机校准，这里是单位置可测的记账侧）。"""
    warnings = _capture_warning(monkeypatch)
    ctx = humanizer('light', seed=13)
    # 4 个 legacy 点（末项必须等于目标 end），预算 = base × len = 0.0001 × 4 ≈ 0.4ms，
    # 远小于 4 个正 delay → 目标总毫秒 0 < 4，整数 wait 不可表示
    legacy_points = [(110, 110), (160, 160), (220, 220), (300, 400)]
    plan = ctx.plan_swipe((100, 100), (300, 400), base_delay_s=0.0001,
                          legacy_points=legacy_points, timing_mode='device_wait')
    assert plan is not None
    assert warnings, '预算不足必须记录设备 profile 校准状态 warning'
    assert any('1ms/点' in w or '整数毫秒量化' in w for w in warnings), \
        f'warning 应指明 light+device_wait 预算不足: {warnings}'


# ============================================================
# 5. 耗时交叉验收矩阵
# ============================================================

class _FixedHumanizer:
    """固定返回值的记录型 humanizer：矩阵"0"格在逐级间做确定性比较。

    每个 facade 方法返回固定的最小计划/时长并记录调用名。同一档位下返回值固定，
    因此"同上一档"可以直接比较消费方法集合与墙钟估算，不依赖随机值。persona 只
    暴露 _desktop_move_budget_ms 用到的 move_speed_scale。
    """

    def __init__(self, level):
        self.enabled = True
        self.level = level
        self.persona = SimpleNamespace(move_speed_scale=1.0)
        self.calls = []

    def _call(self, name):
        self.calls.append(name)

    def press_seconds(self, **kw):
        self._call('press_seconds')
        return 0.100

    def gap_seconds(self, default):
        self._call('gap_seconds')
        return 0.050

    def plan_touch_liftoff(self, target):
        self._call('plan_touch_liftoff')
        return TailPlan(points=((target[0] + 1, target[1] + 1),), delays=(0.020,))

    def plan_pointer_tail(self, target):
        self._call('plan_pointer_tail')
        return TailPlan(points=((target[0] + 1, target[1] + 1),), delays=(0.020,))

    def plan_dwell(self, target):
        self._call('plan_dwell')
        return DwellPlan(segments=((None, 0.050),))

    def plan_move(self, start, end, **kw):
        self._call('plan_move')
        return MovePlan(points=(tuple(end),), delays=(0.030,))

    def plan_swipe(self, start, end, **kw):
        self._call('plan_swipe')
        return MovePlan(points=(tuple(end),), delays=(0.050,))

    def plan_hold(self, target, duration_s, **kw):
        # 维度 J（长按 hold 微颤）：固定 2 点小计划，矩阵各档一致
        self._call('plan_hold')
        return MovePlan(
            points=((target[0] + 1, target[1] + 1), (target[0] - 1, target[1])),
            delays=(duration_s / 2, duration_s / 2))


class _RecordingU2:
    """记录 humanized u2 路径的 DOWN/MOVE/UP RPC 与 sleep 的夹具（不触真实 HTTP）。"""

    _run_humanized_uiautomator2 = Uiautomator2._run_humanized_uiautomator2
    _click_uiautomator2_humanized_impl = Uiautomator2._click_uiautomator2_humanized_impl
    _swipe_uiautomator2_humanized_impl = Uiautomator2._swipe_uiautomator2_humanized_impl
    _long_click_uiautomator2_humanized_impl = Uiautomator2._long_click_uiautomator2_humanized_impl
    _drag_along_impl = Uiautomator2._drag_along_impl

    def __init__(self, humanizer):
        self.humanizer = humanizer
        self.sleeps = []
        self.events = []
        self.u2 = SimpleNamespace(
            pos_rel2abs=lambda x, y: (x, y),
            _jsonrpc_id=lambda method: 'id',
            http=SimpleNamespace(post=lambda *a, **k: None),
        )

    def sleep(self, seconds):
        self.sleeps.append(seconds)

    def _u2_single_input_rpc(self, action, x, y):
        self.events.append((action, x, y))
        return True

    def _click_uiautomator2_legacy_impl(self, x, y):
        self.events.append(('legacy-click', x, y))

    def _swipe_uiautomator2_legacy_impl(self, p1, p2, duration=0.1):
        self.events.append(('legacy-swipe', p1, p2))


def _run_emu_click(humanizer, monkeypatch):
    w = _emu_window(humanizer)
    events = _record(monkeypatch, lambda: w.click_window_message(640, 360, fast=False))
    sleeps = [e[1] for e in events if e[0] == 'sleep']
    return humanizer.calls, sum(estimate_sleep_wall_time(s) for s in sleeps)


def _run_minitouch_click(humanizer, monkeypatch):
    dev = _BudgetMinitouchDevice(humanizer)
    dev._click_minitouch_humanized_impl(100, 200)
    waits = _swipe_waits(dev)
    wall = sum(waits) / 1000.0 + dev.send_count * CommandBuilder.DEFAULT_DELAY
    return humanizer.calls, wall


def _run_u2_click(humanizer, monkeypatch):
    dev = _RecordingU2(humanizer)
    dev._click_uiautomator2_humanized_impl(100, 200)
    wall = sum(estimate_sleep_wall_time(s) for s in dev.sleeps)
    return humanizer.calls, wall


def _run_adb_click(humanizer, monkeypatch):
    rec = []
    a = object.__new__(Adb)
    a.adb_shell = lambda cmd, *args, **kwargs: rec.append(('shell', tuple(cmd)))
    a.sleep = lambda s: rec.append(('sleep', s))
    a.is_desktop = False
    a.humanizer = humanizer
    ns = SimpleNamespace()
    ns.time = lambda: 100.0
    monkeypatch.setattr(adb_mod, 'time', ns)
    a.click_adb(100, 200)
    sleeps = [v for k, v in rec if k == 'sleep']
    wall = sum(estimate_sleep_wall_time(s) for s in sleeps)
    return humanizer.calls, wall


def _run_desktop_click(humanizer, monkeypatch):
    w = _desktop_window(humanizer, cursor=(100, 100))
    events = _record(monkeypatch, lambda: w.click_desktop_window_message(640, 360, fast=False))
    sleeps = [e[1] for e in events if e[0] == 'sleep']
    return humanizer.calls, sum(estimate_sleep_wall_time(s) for s in sleeps)


def _run_desktop_long_click(humanizer, monkeypatch):
    w = _desktop_window(humanizer, cursor=(100, 100))
    events = _record(monkeypatch, lambda: w.long_click_desktop_window_message(640, 360, 0.5))
    sleeps = [e[1] for e in events if e[0] == 'sleep']
    return humanizer.calls, sum(estimate_sleep_wall_time(s) for s in sleeps)


def _run_minitouch_swipe(humanizer, monkeypatch):
    dev = _BudgetMinitouchDevice(humanizer)
    dev._swipe_minitouch_humanized_impl((100, 100), (300, 400), duration=0.1)
    waits = _swipe_waits(dev)
    wall = sum(waits) / 1000.0 + dev.send_count * CommandBuilder.DEFAULT_DELAY
    return humanizer.calls, wall


# 0 格：medium 相对 light、heavy 相对 medium 都必须"同上一档"。
# 参数 expected 是 §4.7 对应消费点列出的 API 集合——消费集合恰好等于它，证明
# medium/heavy 没有虚构未消费维度的耗时。
@pytest.mark.parametrize('run_action,row,expected', [
    (_run_emu_click, '模拟器 window_message 点击',
     {'press_seconds', 'plan_touch_liftoff'}),
    (_run_minitouch_click, 'minitouch 点击',
     {'press_seconds', 'gap_seconds'}),
    (_run_u2_click, 'uiautomator2 点击',
     {'press_seconds'}),
    (_run_adb_click, 'ADB 点击',
     {'gap_seconds'}),
], ids=[
    'emu_window_message_click',
    'minitouch_click',
    'uiautomator2_click',
    'adb_click',
])
def test_matrix_click_zero_cells_same_level(run_action, row, expected, monkeypatch):
    """0 格：最终计划与墙钟模型同上一档——消费方法集合与墙钟估算（固定返回值下）
    逐级一致，且集合恰好等于 §4.7 列出的消费 API。"""
    light_calls, light_wall = run_action(_FixedHumanizer('light'), monkeypatch)
    medium_calls, medium_wall = run_action(_FixedHumanizer('medium'), monkeypatch)
    heavy_calls, heavy_wall = run_action(_FixedHumanizer('heavy'), monkeypatch)
    assert set(light_calls) == set(medium_calls) == set(heavy_calls) == expected, \
        f'{row}：消费方法集合应恰好等于 §4.7 列出的 API（{expected}）'
    # medium 相对 light、heavy 相对 medium 都是 0 格：墙钟估算同上一档
    assert medium_wall == light_wall, f'{row}：medium 墙钟模型与 light 不一致'
    assert heavy_wall == medium_wall, f'{row}：heavy 墙钟模型与 medium 不一致'


def test_matrix_adb_click_never_consumes_press_seconds(monkeypatch):
    """ADB 点击只消费 A（Rule 层）+ I（gap_seconds），维度 B 已放弃（§8.1）。
    medium/heavy 增量为 0 并不代表"没接入"：A 只改落点坐标不加等待、I 各档都已消费。"""
    for level in ('light', 'medium', 'heavy'):
        calls, _ = _run_adb_click(_FixedHumanizer(level), monkeypatch)
        assert 'press_seconds' not in calls, f'{level} ADB 点击不得消费维度 B'
        assert 'gap_seconds' in calls


def test_matrix_desktop_move_medium_adds_c_budget(monkeypatch):
    """桌面指针移动：medium 相对 light 新增 C（§4.7 move_desktop_window_message →
    plan_move，预算经 _desktop_move_budget_ms 决定）。heavy 相对 medium 为 0：
    同一预算公式。C 预算进入最终计划（请求值门禁）。"""
    light_w = _desktop_window(humanizer('light', seed=10), cursor=(0, 0))
    medium_w = _desktop_window(humanizer('medium', seed=10), cursor=(0, 0))
    heavy_w = _desktop_window(humanizer('heavy', seed=10), cursor=(0, 0))
    start, end = (0, 0), (300, 0)
    light_budget = light_w._desktop_move_budget_ms(start, end)
    medium_budget = medium_w._desktop_move_budget_ms(start, end)
    heavy_budget = heavy_w._desktop_move_budget_ms(start, end)
    # light：15~30ms；medium/heavy：40~120ms × 人格速度缩放（C 档预算）
    assert 15.0 <= light_budget <= 30.0, f'light 预算 {light_budget} 应落在 15~30ms'
    scale = medium_w.humanizer.persona.move_speed_scale
    assert 40.0 * scale <= medium_budget <= 120.0 * scale, \
        f'medium 预算 {medium_budget} 应落在 40~120ms × scale'
    # heavy 相对 medium = 0 格：同一预算公式
    assert heavy_budget == medium_budget
    # C 预算进入最终计划：sum(delays) == 预算（误差 ≤1ms）
    plan = medium_w.humanizer.plan_move(start, end, gesture_kind='pointer_move',
                                        budget_ms=medium_budget)
    assert plan is not None
    assert abs(sum(plan.delays) - medium_budget / 1000.0) <= 0.001
    assert plan.total_seconds == sum(plan.delays)


def test_matrix_desktop_click_medium_adds_plan_dwell(monkeypatch):
    """桌面点击：medium 相对 light 新增 C/E——预定位 plan_move（C，经
    move_desktop_window_message）+ plan_dwell（E）。§4.7：click_desktop_window_message
    → move_desktop_window_message / press_seconds / plan_dwell / plan_pointer_tail。"""
    light_calls, light_wall = _run_desktop_click(_FixedHumanizer('light'), monkeypatch)
    medium_calls, medium_wall = _run_desktop_click(_FixedHumanizer('medium'), monkeypatch)
    # 预定位 C 各档都有（plan_move）；E 只属 medium/heavy（plan_dwell）
    assert 'plan_move' in light_calls and 'plan_move' in medium_calls
    assert 'plan_dwell' not in light_calls and 'plan_dwell' in medium_calls
    assert medium_wall > light_wall, 'medium 墙钟应多出 dwell 时长'
    # heavy 相对 medium = 0 格（API 集合层面）：heavy 增量是方案级 hesitate/slide_away
    heavy_calls, heavy_wall = _run_desktop_click(_FixedHumanizer('heavy'), monkeypatch)
    assert heavy_calls == medium_calls
    assert heavy_wall == medium_wall


def test_matrix_desktop_long_click_medium_adds_plan_dwell(monkeypatch):
    """桌面长按同样走 C/E：预定位 plan_move + plan_dwell；既有长按时长是业务参数，
    不由维度 B 重采样（§7.3.2 桌面长按不消费 B）。"""
    light_calls, _ = _run_desktop_long_click(_FixedHumanizer('light'), monkeypatch)
    medium_calls, _ = _run_desktop_long_click(_FixedHumanizer('medium'), monkeypatch)
    assert 'plan_dwell' not in light_calls and 'plan_dwell' in medium_calls
    assert 'press_seconds' not in medium_calls, '长按时长不由 B 重采样'
    # heavy 相对 medium = 0 格（API 集合层面）
    heavy_calls, _ = _run_desktop_long_click(_FixedHumanizer('heavy'), monkeypatch)
    assert heavy_calls == medium_calls


def test_matrix_desktop_click_heavy_adds_hesitate_and_slide_away():
    """heavy 相对 medium 的 E/F 增量（方案级，§7.3.2）：E(hesitate) 只属 heavy——
    medium 的 plan_dwell(option='hesitate') 退化为 gauss（≤250ms），heavy 有机会给出
    0.3~0.8s 长尾；F(slide_away) 同样只属 heavy（追加滑离段）。§4.7 消费点：
    plan_dwell（E）/ plan_pointer_tail（F）。G(park) 由 Control.click 的 plan_idle
    消费（Task 20），桌面点击进 Control 才触发。"""
    # hesitate：用 hesitate_p=1.0 的人格让 heavy 的 rng 判据确定触发，证明档位门控
    high_p = replace(PERSONA, hesitate_p=1.0)
    medium = plan_dwell(rng(7), (100, 100), high_p, option='hesitate', level='medium')
    heavy = plan_dwell(rng(7), (100, 100), high_p, option='hesitate', level='heavy')
    assert all(0.020 <= sec <= 0.250 for _, sec in medium.segments), \
        'medium 的 hesitate 选项恒退化为 gauss'
    assert any(0.300 <= sec <= 0.800 for _, sec in heavy.segments), \
        'heavy 才有 hesitate 长尾'
    # slide_away：heavy 追加滑离点，medium 退化为纯 micro_drift
    medium_tail = plan_pointer_tail(rng(7), (100, 100), PERSONA, option='slide_away',
                                    level='medium')
    heavy_tail = plan_pointer_tail(rng(7), (100, 100), PERSONA, option='slide_away',
                                   level='heavy')
    assert len(heavy_tail.points) > len(medium_tail.points), \
        'heavy 才有 slide_away 附加段'


def test_matrix_minitouch_swipe_medium_adds_c_geometry():
    """minitouch 滑动：light 的 D 例外沿用 legacy 贝塞尔点 + device-side profile
    （§5 D），medium 新增 C（新二维几何），heavy 相对 medium 为 0（同一 profiled 路径）。
    §4.7：swipe_minitouch → plan_swipe(timing_mode='device_wait') / plan_touch_liftoff /
    gap_seconds。"""

    def run(level, seed):
        ctx = humanizer(level, seed)
        plans = _capture_swipe_plans(ctx)
        dev = _BudgetMinitouchDevice(ctx)
        dev._swipe_minitouch_humanized_impl((100, 100), (300, 400), duration=0.1)
        return plans[0][0], plans[0][1].get('legacy_points')

    light_plan, light_legacy = run('light', 11)
    medium_plan, medium_legacy = run('medium', 11)
    heavy_plan, heavy_legacy = run('heavy', 11)
    # light：计划点 == backend 传入的 legacy 贝塞尔点（D 例外，不启用 C 几何）
    assert light_legacy is not None
    assert tuple(light_legacy) == light_plan.points
    # medium/heavy：不传 legacy 点 → profiled 几何（C），与 light 的点不同
    assert medium_legacy is None and heavy_legacy is None
    assert medium_plan.points != light_plan.points
    # heavy 相对 medium = 0：同一 profiled 路径
    assert heavy_legacy is None


def test_matrix_minitouch_swipe_heavy_same_as_medium(monkeypatch):
    """minitouch 滑动 heavy 相对 medium = 0 格：消费方法集合与墙钟估算（固定返回值下）
    同上一档。"""
    medium_calls, medium_wall = _run_minitouch_swipe(_FixedHumanizer('medium'), monkeypatch)
    heavy_calls, heavy_wall = _run_minitouch_swipe(_FixedHumanizer('heavy'), monkeypatch)
    assert heavy_calls == medium_calls
    assert heavy_wall == medium_wall


def test_matrix_u2_swipe_medium_adds_c_via_drag_along_impl():
    """uiautomator2 滑动：light 无 legacy 点 → plan_swipe 返回 None → 回退 legacy
    u2.swipe；medium 新增 C 并经 _drag_along_impl 逐点投递（DOWN/MOVE/UP）。
    §4.7：swipe_uiautomator2 → plan_swipe（换 _drag_along_impl 后）。"""

    def run(level, seed):
        dev = _RecordingU2(humanizer(level, seed))
        dev._swipe_uiautomator2_humanized_impl((100, 100), (300, 400), duration=0.1)
        return dev

    light = run('light', 12)
    medium = run('medium', 12)
    heavy = run('heavy', 12)
    # light：plan 为 None → 一次 legacy 回退（u2.swipe），不消费 C
    assert any(e[0] == 'legacy-swipe' for e in light.events)
    assert not any(a == U2_ACTION_MOVE for a, *_ in light.events)
    # medium/heavy：经 _drag_along_impl 发 DOWN → MOVE×N → UP（C 轨迹），heavy == medium
    for dev in (medium, heavy):
        actions = [a for a, *_ in dev.events if a in (U2_ACTION_DOWN, U2_ACTION_MOVE, U2_ACTION_UP)]
        assert actions[0] == U2_ACTION_DOWN and actions[-1] == U2_ACTION_UP
        assert U2_ACTION_MOVE in actions
        assert len(dev.sleeps) > 0, 'delay_before_move=True：中间点 sleep 各自 delay'


def test_matrix_u2_swipe_heavy_same_as_medium(monkeypatch):
    """uiautomator2 滑动 heavy 相对 medium = 0 格：同一条 _drag_along_impl 投递路径、
    同样的 sleep 输入。"""
    medium_dev = _RecordingU2(_FixedHumanizer('medium'))
    medium_dev._swipe_uiautomator2_humanized_impl((100, 100), (300, 400), duration=0.1)
    heavy_dev = _RecordingU2(_FixedHumanizer('heavy'))
    heavy_dev._swipe_uiautomator2_humanized_impl((100, 100), (300, 400), duration=0.1)
    assert heavy_dev.sleeps == medium_dev.sleeps
    assert [a for a, *_ in heavy_dev.events] == [a for a, *_ in medium_dev.events]


def test_matrix_dimension_a_costs_zero_time():
    """A（落点采样）在本矩阵恒为 0 耗时：只改落点坐标、不加等待，且只在 Rule 层消费
    （§4.7：RuleClick/RuleSwipe/RuleImage/RuleOcr/RuleGif 的 coord），backend 控制
    方法不消费它。因此 ADB 行 medium/heavy 增量为 0 不代表"没接入"。"""
    sources = [
        inspect.getsource(Window.click_window_message),
        inspect.getsource(Window.click_desktop_window_message),
        inspect.getsource(Window.long_click_desktop_window_message),
        inspect.getsource(Window.swipe_window_message),
        inspect.getsource(Window.swipe_desktop_window_message),
        inspect.getsource(Minitouch._click_minitouch_humanized_impl),
        inspect.getsource(Minitouch._swipe_minitouch_humanized_impl),
        inspect.getsource(Uiautomator2._click_uiautomator2_humanized_impl),
        inspect.getsource(Uiautomator2._swipe_uiautomator2_humanized_impl),
        inspect.getsource(Adb.click_adb),
    ]
    for src in sources:
        assert 'sample_point' not in src, 'backend 控制方法不得消费维度 A'


# ============================================================
# 6. 长按 hold 微颤（维度 J，2026-08-26 调研对标）
# ============================================================

def _capture_hold_plans(ctx):
    """捕获 plan_hold 的返回值（与 _capture_swipe_plans 同模式）。"""
    plans = []
    orig = ctx.plan_hold

    def spy(target, duration_s, **kw):
        plan = orig(target, duration_s, **kw)
        plans.append((plan, target, duration_s, kw))
        return plan
    ctx.plan_hold = spy
    return plans


def test_minitouch_long_click_hold_jitter_single_batch(monkeypatch):
    """minitouch 长按：单批 down → (w+m)*N → up，w 总和 ≈ duration（ms）——
    hold 时长由设备端 w 执行，UP 不提前。"""
    _force_option(monkeypatch, 'hold', 'tremor')
    ctx = humanizer('medium', seed=5)
    dev = _BudgetMinitouchDevice(ctx)
    dev._long_click_minitouch_humanized_impl(640, 360, 1.0)
    # 恰好一批（与 click 的单批拓扑一致，长按不拆批）
    assert dev.send_count == 1
    payload = dev.sent[0]
    assert payload[0].startswith('d ')
    assert payload[1] == 'c'
    assert payload[-2:] == ('u', 'c')
    # hold 段：w 与 m 交替出现（wait → move），w 都是正整数毫秒
    body = payload[2:-2]
    assert all(c[0] in ('w', 'm', 'c') for c in body)
    waits = [int(c.split()[1]) for c in body if c.startswith('w ')]
    moves = [c for c in body if c.startswith('m ')]
    assert len(waits) == len(moves) > 0, 'hold 段应有微颤 MOVE 与对应 wait'
    assert all(isinstance(w, int) and w >= 1 for w in waits)
    # 设备端 hold 时长精确守恒（_quantize_move_delays 累计余量结转，契约 #6）：
    # w 总和 == floor(sum(delays)×1000)，而 plan_hold 的 cap 命中分支
    # sum(delays) ≈ duration（±1ms/点抖动）→ 总和落在 1000±容差内
    assert abs(sum(waits) - 1000) <= len(waits) + 1, \
        f'hold 时长不守恒: {sum(waits)}ms vs 1000ms'


def test_minitouch_long_click_hold_none_falls_back_legacy(monkeypatch):
    """维度 J 'none' 策略：plan_hold 返回 None → legacy 单 w 长按。"""
    _force_option(monkeypatch, 'hold', 'none')
    ctx = humanizer('medium', seed=6)
    dev = _BudgetMinitouchDevice(ctx)
    dev._long_click_minitouch_legacy_impl = lambda x, y, duration: dev.sent.append(('legacy', duration))
    dev._long_click_minitouch_humanized_impl(640, 360, 1.0)
    assert dev.sent == [('legacy', 1.0)]


def test_u2_long_click_hold_jitter_rpc_stream():
    """u2 长按：DOWN → (sleep+MOVE)*N → UP，逐点单 RPC；sleep 总和 ≈ duration。"""
    humanizer_fixed = _FixedHumanizer('medium')
    calls = _RecordingU2(humanizer_fixed)
    calls._long_click_uiautomator2_legacy_impl = \
        lambda x, y, duration: calls.events.append(('legacy-long', x, y))
    calls._long_click_uiautomator2_humanized_impl(640, 360, 0.5)
    # 事件流形状：DOWN / MOVE... / UP，无 legacy 回退
    actions = [a for a, *_ in calls.events]
    assert actions[0] == 0 and actions[-1] == 1, f'DOWN/UP 边界: {actions}'
    assert actions[1:-1] == [2] * (len(actions) - 2), f'hold 段应全为 MOVE: {actions}'
    assert 'legacy-long' not in calls.events
    # 预算守恒：_FixedHumanizer.plan_hold 返回 duration/2 × 2
    assert sum(calls.sleeps) == pytest.approx(0.5)
    # 微颤点距 target ≤6px
    for a, x, y in calls.events[1:-1]:
        assert abs(x - 640) <= 6 and abs(y - 360) <= 6


def test_u2_long_click_hold_none_falls_back_legacy():
    """维度 J 'none'：plan_hold None → 无装饰 legacy。"""
    humanizer_fixed = _FixedHumanizer('medium')
    monkeypatch_none = SimpleNamespace(plan_hold=lambda target, duration_s, **kw: None,
                                       level='medium', enabled=True)
    calls = _RecordingU2(monkeypatch_none)
    calls._long_click_uiautomator2_legacy_impl = \
        lambda x, y, duration: calls.events.append(('legacy-long', x, y))
    calls._long_click_uiautomator2_humanized_impl(640, 360, 1.0)
    assert calls.events == [('legacy-long', 640, 360)]


def test_emu_long_click_hold_jitter_no_double_scaling(monkeypatch):
    """模拟器长按 hold 微颤不得双重缩放（CRITICAL 回归锁）。

    window_scale_rate 是 Windows DPI 缩放比：authoring target 在换算前捕获
    传给 plan_hold、发送时单次换算。若把已换算的消息空间坐标再传给
    _hold_jitter_moves（旧 bug），MOVE 会落在 authoring/rate²——DPI 125% 时
    DOWN→MOVE 跳变 ~119px、150% 时 ~164px，远超平台 8~10px 长按容差，
    长按被取消。
    """
    for scale in (1.25, 1.5):
        ctx = humanizer('medium', seed=8)
        w = _emu_window(ctx, scale=scale)
        events = _record(monkeypatch,
                         lambda: w.long_click_window_message(640, 360, 1.0))
        moves = [(e[4] & 0xFFFF, (e[4] >> 16) & 0xFFFF) for e in events
                 if len(e) == 5 and e[2] == windows_impl.WM_MOUSEMOVE]
        downs = [e for e in events if len(e) == 5 and e[2] == windows_impl.WM_LBUTTONDOWN]
        assert downs, '应有 DOWN'
        down_lparam = downs[0][4]
        down_pos = (down_lparam & 0xFFFF, (down_lparam >> 16) & 0xFFFF)
        assert len(moves) > 0, f'scale={scale}: hold 段应有微颤 MOVE'
        for mx, my in moves:
            # 微颤 MOVE 与 DOWN 同簇：消息空间距离 ≤ 6px×scale + 1（取整余量）
            dx = abs(mx - down_pos[0])
            dy = abs(my - down_pos[1])
            limit = 6 * scale + 1
            assert dx <= limit and dy <= limit, \
                f'scale={scale}: MOVE ({mx},{my}) 距 DOWN {down_pos} 跳变 ' \
                f'({dx},{dy}) 超 {limit:.1f}px——疑似双重缩放'


def test_u2_long_click_b0_first_rpc_failure_falls_back_legacy(monkeypatch):
    """u2 长按 B0：首个 RPC 之前（u2 设备构造）失败 → 单次 legacy、零输入事件。"""
    humanizer_fixed = _FixedHumanizer('medium')
    calls = _RecordingU2(humanizer_fixed)
    calls._long_click_uiautomator2_legacy_impl = \
        lambda x, y, duration: calls.events.append(('legacy-long', x, y))

    def boom(device):
        raise ConnectionError('u2 init failed')

    monkeypatch.setattr(u2_module, '_humanized_u2_device', boom)
    calls._long_click_uiautomator2_humanized_impl(640, 360, 1.0)
    assert calls.events == [('legacy-long', 640, 360)]


def test_u2_long_click_b1_rpc_failure_takes_over():
    """u2 长按 B1：首个 DOWN RPC 已发出后失败 → RequestHumanTakeover，
    绝不重放 legacy（「绝不第二次 DOWN」契约——长按中途回放会造成一次
    完整 DOWN+hold+UP 重叠）。"""
    humanizer_fixed = _FixedHumanizer('medium')
    calls = _RecordingU2(humanizer_fixed)
    calls._long_click_uiautomator2_legacy_impl = \
        lambda x, y, duration: calls.events.append(('legacy-long', x, y))

    rpc_state = {'n': 0}

    def flaky_rpc(action, x, y):
        rpc_state['n'] += 1
        if rpc_state['n'] == 1:
            raise ConnectionError('rpc dropped after DOWN')
        raise AssertionError('B1 后不得再发任何 RPC')

    calls._u2_single_input_rpc = flaky_rpc
    with pytest.raises(RequestHumanTakeover):
        calls._long_click_uiautomator2_humanized_impl(640, 360, 1.0)
    # 无 legacy 重放、无更多 RPC
    assert calls.events == []


def test_u2_long_click_b1_move_failure_takes_over():
    """u2 长按 B1 变体：hold 段 MOVE RPC 失败（首 RPC 成功后）→ 接管。
    事件记录里只有那次成功的 DOWN，无 legacy。"""
    humanizer_fixed = _FixedHumanizer('medium')
    calls = _RecordingU2(humanizer_fixed)
    calls._long_click_uiautomator2_legacy_impl = \
        lambda x, y, duration: calls.events.append(('legacy-long', x, y))

    def move_fail_rpc(action, x, y):
        calls.events.append((action, x, y))
        if action == 2:  # 首个 MOVE
            raise ConnectionError('move rpc dropped')
        return True

    calls._u2_single_input_rpc = move_fail_rpc
    with pytest.raises(RequestHumanTakeover):
        calls._long_click_uiautomator2_humanized_impl(640, 360, 1.0)
    # 记录含"已发出但结果未知"的那个 MOVE（append 先于抛异常），无 legacy
    assert calls.events == [(0, 640, 360), (2, 641, 361)]
