"""全操作共享间隔（2026-08-27）的测试：纯函数 / 门面 / Control 接线。

预付制：操作结束挂起下一次操作的间隔要求，由下一次**截图入口**（pace_view）
等满——等待全部发生在「看」之前，动作一旦决定立即执行（反应慢、动作快）。
修复背景：旧模型把等待插在「决策→执行」之间，appear_then_click 的决策截图
与执行之间的画面有效期窗口被打破（接受邀请弹窗过期、结算画面关闭后误点
庭院——2026-08-27 两个线上现象）。

硬要求：
- off 档零消费：不 sleep、不动状态、不消费 RNG（零回归契约）；
- 窗口只记意图间隔（自然节奏，机制等待由 _mech_wait 记账扣除），防压-松振荡；
- 首次点击某资源不预付退避（下次换目标概率不低，全额预付会灾难性拖慢
  正常任务）；已确认重复（count>=2）才预付 4/8/16s 退避；
- pace_view 是唯一的主消费点，pace_execute 仅兜底且封顶 EXECUTE_PACE_MAX_S。
"""
import time as _time

import numpy as np
import pytest

from module.device.humanize import HumanizerContext
from module.device.humanize.persona import Persona
from module.device.humanize.timing import (
    EXECUTE_PACE_MAX_S,
    INTER_CLICK_MAX_S,
    INTER_CLICK_MIN_S,
    INTER_CLICK_STEP_S,
    INTER_CLICK_WINDOW,
    REPEAT_BACKOFF_JITTER,
    REPEAT_BACKOFF_NOMINAL_S,
    REPEAT_BACKOFF_RADIUS_PX,
    next_action_requirement,
    repeat_backoff_seconds,
)

pytestmark = pytest.mark.unit

SEED = 20260827
PERSONA = Persona.generate(SEED)


def rng(seed: int = 7) -> np.random.Generator:
    return np.random.Generator(np.random.PCG64(seed))


# ---------------------------------------------------------------- 纯函数


class TestNextActionRequirement:
    def test_fast_rhythm_raises_base(self):
        # 窗口均值 0.05 < base 下限：节奏快 → base 抬一步
        require, base = next_action_requirement(
            rng(), [0.05] * INTER_CLICK_WINDOW, INTER_CLICK_MIN_S, 0)
        assert base == pytest.approx(INTER_CLICK_MIN_S + INTER_CLICK_STEP_S)

    def test_slow_rhythm_lowers_base(self):
        # 窗口均值 2.0 >= base：节奏慢 → base 降一步
        require, base = next_action_requirement(
            rng(), [2.0] * INTER_CLICK_WINDOW, INTER_CLICK_MAX_S - INTER_CLICK_STEP_S, 0)
        assert base == pytest.approx(INTER_CLICK_MAX_S - 2 * INTER_CLICK_STEP_S)

    def test_base_clamped_both_sides(self):
        _, base = next_action_requirement(rng(), [0.01] * INTER_CLICK_WINDOW, INTER_CLICK_MAX_S, 0)
        assert base == INTER_CLICK_MAX_S
        _, base = next_action_requirement(rng(), [9.0] * INTER_CLICK_WINDOW, INTER_CLICK_MIN_S, 0)
        assert base == INTER_CLICK_MIN_S

    def test_normal_require_within_bounds_large_sample(self):
        r = rng()
        for _ in range(2000):
            mid = (INTER_CLICK_MIN_S + INTER_CLICK_MAX_S) / 2
            require, new_base = next_action_requirement(r, [0.5] * 3, mid, 0)
            # 常规要求（repeat_count=0）截断在 [MIN, 调整后 base] 内
            assert INTER_CLICK_MIN_S <= require <= new_base

    def test_no_backoff_on_first_click(self):
        # 首次点击该资源（repeat_count=1）：不预付退避——每次点击都白等 2s
        # 起步会把正常任务的换目标节奏灾难性拖慢
        r = rng()
        for _ in range(200):
            require, _ = next_action_requirement(r, [0.5] * 3, INTER_CLICK_MIN_S, 1)
            assert require <= INTER_CLICK_MAX_S  # 只有常规量级，无 2s 退避

    def test_backoff_prepaid_on_confirmed_repeat(self):
        # 已确认重复（count>=2）：下次要求并入退避（count+1 档）
        lo, hi = REPEAT_BACKOFF_JITTER
        r = rng()
        for count, nominal in [(2, 3.0), (3, 4.0), (4, 10.0), (9, 16.0)]:
            require, _ = next_action_requirement(r, [0.5] * 3, INTER_CLICK_MIN_S, count)
            assert nominal * lo <= require <= nominal * hi, f'count={count}'

    def test_fast_cadence_climbs_to_max_monotonically(self):
        # 序列仿真：意图节奏恒 0.01s → base 单调爬到 1.0 封顶
        r = rng()
        base = INTER_CLICK_MIN_S
        seen = []
        for _ in range(20):
            _, base = next_action_requirement(r, [0.01] * INTER_CLICK_WINDOW, base, 0)
            seen.append(base)
        assert seen == sorted(seen)
        assert base == INTER_CLICK_MAX_S

    def test_slow_cadence_falls_back_to_min(self):
        r = rng()
        base = INTER_CLICK_MAX_S
        for _ in range(12):
            _, base = next_action_requirement(r, [5.0] * INTER_CLICK_WINDOW, base, 0)
        assert base == INTER_CLICK_MIN_S

    def test_invalid_base_raises(self):
        with pytest.raises(ValueError):
            next_action_requirement(rng(), [0.1], INTER_CLICK_MIN_S - 0.1, 0)
        with pytest.raises(ValueError):
            next_action_requirement(rng(), [0.1], INTER_CLICK_MAX_S + 0.1, 0)


class TestRepeatBackoff:
    def test_pure_function_sequence(self):
        # 纯函数序列：count=2/3/4/5/6+ → 标称查表 (2,3,4,10,16) 末档封顶，区间内随机
        r = rng()
        lo, hi = REPEAT_BACKOFF_JITTER
        for count, nominal in [(2, 2.0), (3, 3.0), (4, 4.0), (5, 10.0), (6, 16.0), (9, 16.0)]:
            v = repeat_backoff_seconds(r, count)
            assert nominal * lo <= v <= nominal * hi, f'count={count}'

    def test_pure_function_first_click_no_backoff_no_rng(self):
        r = rng()
        s = r.bit_generator.state
        assert repeat_backoff_seconds(r, 1) == 0.0
        assert repeat_backoff_seconds(r, 0) == 0.0
        assert r.bit_generator.state == s


# ---------------------------------------------------------------- 门面（虚拟时钟）


class _FakeClock:
    """虚拟时钟：time.sleep 直接推进时钟，等待可断言、测试零真实耗时。"""

    def __init__(self, start: float = 10000.0):
        self.now = start
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def clock(monkeypatch):
    c = _FakeClock()
    # 门面的三个等待/打点入口都用模块级 time：替换 time.time / time.sleep
    #（monkeypatch 自动还原，作用域仅本测试）
    monkeypatch.setattr(_time, 'time', c.time)
    monkeypatch.setattr(_time, 'sleep', c.sleep)
    return c


def make_context(level: str = 'light') -> HumanizerContext:
    return HumanizerContext(
        enabled=level != 'off', level=level, persona=PERSONA, rng=rng())


class TestFacadePayForward:
    def test_off_returns_zero_and_touches_nothing(self, clock):
        ctx = make_context('off')
        assert ctx.pace_view() == 0.0
        assert ctx.pace_execute() == 0.0
        ctx.record_action(target=(500, 300))
        # off 零消费：无 sleep、无状态、无 RNG 消耗
        assert clock.sleeps == []
        assert ctx._gap_last_ts is None
        assert len(ctx._gap_window) == 0
        assert ctx._pending_require == 0.0

    def test_pace_view_consumes_pending_before_screenshot(self, clock):
        ctx = make_context()
        # 首个操作：打点挂起下次要求（常规量级，首击无退避）
        ctx.record_action(target=(500, 300))
        assert ctx._pending_require > 0.0
        # 下一次截图入口：等满挂起的要求
        w = ctx.pace_view()
        assert w == pytest.approx(ctx._pending_require + w)  # 已清零，直接比对
        assert ctx._pending_require == 0.0
        assert clock.sleeps and clock.sleeps[0] == pytest.approx(w)
        # 要求已消费：再次截图零等待（多帧识别循环不重复等）
        assert ctx.pace_view() == 0.0

    def test_pace_view_natural_elapsed_counts(self, clock):
        ctx = make_context()
        ctx.record_action(target=(500, 300))
        pending = ctx._pending_require
        # 任务层自然消耗了部分时间（识别/处理），只补差额
        clock.advance(pending * 0.4)
        w = ctx.pace_view()
        assert w == pytest.approx(pending * 0.6, abs=1e-6)

    def test_pace_execute_fallback_capped(self, clock):
        ctx = make_context()
        ctx.record_action(target=(500, 300))
        ctx.record_action(target=(500, 300))
        ctx.record_action(target=(500, 300))
        # 无截图背靠背（罕见路径）：执行前兜底封顶 EXECUTE_PACE_MAX_S
        # （count=3 → pending 含 backoff(4)≈8s，兜底最多 0.3s）
        w = ctx.pace_execute()
        assert 0 < w <= EXECUTE_PACE_MAX_S + 1e-9
        # 剩余要求留给后续消费点
        assert ctx._pending_require > 0.0

    def test_pace_execute_zero_after_view(self, clock):
        ctx = make_context()
        ctx.record_action(target=(500, 300))
        ctx.pace_view()  # 正常流程：截图入口已等满
        assert ctx.pace_execute() == 0.0

    def test_slow_cadence_never_waits(self, clock):
        ctx = make_context()
        for i in range(6):
            # 每轮换目标（避免触发同一资源退避——退避预付在慢节奏下
            # 仍会补差额，那是退避语义的正确行为，本测试只测常规节奏）
            ctx.record_action(target=(500 + i * 200, 300))
            clock.advance(3.0)  # 任务层自然间隔 3s：远慢于要求
            assert ctx.pace_view() == 0.0
        assert clock.sleeps == []

    def test_fast_cadence_pushes_base_to_max(self, clock):
        ctx = make_context()
        moments = []
        for _ in range(15):
            # 模拟真实循环：截图入口等满 → 操作（执行前零等待）→ 打点
            ctx.pace_view()
            moments.append(clock.now)
            ctx.record_action(target=(500, 300))
        assert ctx._gap_base == INTER_CLICK_MAX_S
        # 决策-执行零等待：相邻操作间隔全部由 pace_view 承担
        gaps = [b - a for a, b in zip(moments, moments[1:])]
        assert all(g >= INTER_CLICK_MIN_S - 0.05 for g in gaps)

    def test_mech_wait_not_fed_back_into_window(self, clock):
        ctx = make_context()
        ctx.record_action(target=(500, 300))
        for _ in range(4):
            ctx.pace_view()  # 机制等待（推时钟）
            ctx.record_action(target=(500, 300))
        # 窗口记录意图间隔（机制等待已扣除）：背靠背循环下 ≈ 0，
        # 绝不能混入 pace_view 的等待（否则控制器被自己制造的慢欺骗而振荡）
        assert len(ctx._gap_window) == 4
        assert all(g < 0.05 for g in ctx._gap_window)


class TestFacadeRepeatBackoff:
    def test_same_name_counts_repeat_across_jitter(self, clock):
        ctx = make_context()
        # 同名模板即同一资源：拟人化落点抖动（坐标变化）不影响判重
        ctx.record_action(target=(500, 300), name='GB_DE_WIN')
        ctx.record_action(target=(520, 315), name='GB_DE_WIN')
        ctx.record_action(target=(480, 290), name='GB_DE_WIN')
        assert ctx._repeat_count == 3

    def test_adjacent_different_names_reset(self, clock):
        ctx = make_context()
        # 结算画面的相邻奖励区域：坐标距离 <50px 但控件名不同 → 各自独立
        # 计数（2026-08-28 修订：按名判重取代纯坐标半径，避免相邻按钮误判）
        ctx.record_action(target=(500, 300), name='C_REWARD_1')
        ctx.record_action(target=(510, 305), name='C_REWARD_3')
        assert ctx._repeat_count == 1
        assert ctx._repeat_name == 'C_REWARD_3'

    def test_generic_name_falls_back_to_radius(self, clock):
        ctx = make_context()
        # 泛称（默认 control_name='Click'）视同无名 → 坐标半径兜底
        ctx.record_action(target=(500, 300), name='Click')
        ctx.record_action(target=(510, 305), name='Click')
        assert ctx._repeat_count == 2  # 半径内 → 同一资源

    def test_same_target_backoff_grows(self, clock):
        ctx = make_context()
        moments = []
        for i in range(5):
            ctx.pace_view()
            moments.append(clock.now)
            ctx.record_action(target=(500, 300))  # 连续点同一坐标
        # 事件实际间隔（pace_view 等满）：第 2 次 = 常规量级（首击不预付退避），
        # 第 3/4/5 次 = 退避查表 3/4/10s 量级（首击不预付 → count=2 挂 backoff(3)）
        gaps = [b - a for a, b in zip(moments, moments[1:])]
        lo, _ = REPEAT_BACKOFF_JITTER
        assert gaps[0] <= INTER_CLICK_MAX_S  # 第 2 次：常规
        assert gaps[1] >= 3.0 * lo - 0.1     # 第 3 次：退避 3s
        assert gaps[2] >= 4.0 * lo - 0.1     # 第 4 次：退避 4s
        assert gaps[3] >= 10.0 * lo - 0.1    # 第 5 次：退避 10s
        assert ctx._repeat_count == 5

    def test_small_jitter_counts_same_target(self, clock):
        ctx = make_context()
        ctx.record_action(target=(500, 300))
        # 半径内小幅偏移（拟人化落点抖动 ±几十像素）：仍算同一资源
        ctx.record_action(target=(520, 315))
        assert ctx._repeat_count == 2

    def test_different_target_resets(self, clock):
        ctx = make_context()
        ctx.record_action(target=(500, 300))
        # 距离 > 半径（50px）：换目标 → 计数重置为 1
        ctx.record_action(target=(800, 300))
        assert ctx._repeat_count == 1

    def test_swipe_resets(self, clock):
        ctx = make_context()
        ctx.record_action(target=(500, 300))
        # swipe/drag 无落点：重置计数
        ctx.record_action(target=None)
        assert ctx._repeat_count == 0
        assert ctx._repeat_point is None

    def test_backoff_prepaid_only_after_confirmed_repeat(self, clock):
        ctx = make_context()
        # 首击：pending 只有常规量级（不预付退避，防换目标白等）
        ctx.record_action(target=(500, 300))
        assert ctx._pending_require <= INTER_CLICK_MAX_S
        # 已确认重复（count=2）：pending 并入退避 3s 量级（backoff(3) 查表）
        ctx.record_action(target=(500, 300))
        assert ctx._pending_require >= 3.0 * REPEAT_BACKOFF_JITTER[0]


# ---------------------------------------------------------------- Control 接线

from module.device.control import Control  # noqa: E402  Control 依赖较重，放测试尾部导入


class _StubControl:
    """只借 Control 的三个方法做接线测试，不构造完整 Device。"""

    _pace_action_before = Control._pace_action_before
    _pace_action_after = Control._pace_action_after
    _humanizer_enabled = Control._humanizer_enabled

    def __init__(self, humanizer):
        self.humanizer = humanizer


class _Recorder:
    """记录型 humanizer 替身：只关心门面两个入口是否被按对调用。"""

    enabled = True

    def __init__(self):
        self.executes: list = []
        self.records: list = []

    def pace_execute(self) -> float:
        self.executes.append(None)
        return 0.0

    def record_action(self, target=None, name=None) -> None:
        self.records.append((target, name))


class TestControlWiring:
    def test_enabled_humanizer_gets_both_calls(self):
        rec = _Recorder()
        stub = _StubControl(rec)
        stub._pace_action_before()
        stub._pace_action_after((100, 200))
        assert rec.executes == [None]
        assert rec.records == [((100, 200), None)]

    def test_disabled_humanizer_skipped(self):
        class _Off:
            enabled = False

        stub = _StubControl(_Off())
        stub._pace_action_before()
        stub._pace_action_after((100, 200))
        # off 旁路：不触达门面任何方法

    def test_missing_humanizer_skipped(self):
        # humanizer 未绑定（Device 尚未构造 humanizer 的场景）不得抛异常
        stub = _StubControl(None)
        stub._pace_action_before()
        stub._pace_action_after((100, 200))


def test_device_screenshot_wiring_source_contract():
    """源码契约：Device.screenshot 必须在入口做预付等待（pace_view）。

    截图是 appear_then_click 决策模式的依据，等待若不在截图前发生就会滑落
    到操作执行前——画面过期点击的修复点只有这一个。
    """
    import inspect
    from module.device import device as device_module
    src = inspect.getsource(device_module.Device.screenshot)
    assert 'pace_view' in src
    # 必须在截图动作（super().screenshot）之前
    assert src.index('pace_view') < src.index('super().screenshot')
