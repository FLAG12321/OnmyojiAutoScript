"""维度 F 收尾与维度 G 空闲的测试（Plan Task 10）。

F 拆成两个函数而不是一个带 touch 布尔参数的函数，因为两者的**事件位置相反**：
指针语义在 UP 之后漂移，触摸语义在 UP 之前微位移。用 touch=True/False 表达
会让 backend 把漂移发到错误的一侧。
"""
import numpy as np
import pytest

from module.device.humanize.gesture import (
    HOLD_JITTER_WALK_MAX,
    IDLE_DRIFT_PX,
    IDLE_DRIFT_THRESHOLD_S,
    IDLE_PARK_THRESHOLD_S,
    LIFTOFF_DRIFT_PX,
    MICRO_DRIFT_COUNT,
    MICRO_DRIFT_GAP_S,
    SLIDE_AWAY_PX,
    plan_hold_jitter,
    plan_idle,
    plan_pointer_tail,
    plan_touch_liftoff,
)
from module.device.humanize.persona import Persona
from module.device.humanize.plan import MovePlan, TailPlan

pytestmark = pytest.mark.unit

SEED = 20260825
PERSONA = Persona.generate(SEED)
TARGET = (640, 360)
CANVAS = (1280, 720)


def rng(seed: int = 7) -> np.random.Generator:
    return np.random.Generator(np.random.PCG64(seed))


class TestPointerTail:
    def test_micro_drift_point_count_and_amplitude(self):
        r = rng()
        for _ in range(600):
            tail = plan_pointer_tail(r, TARGET, PERSONA, option='micro_drift',
                                     level='light', canvas_size=CANVAS)
            assert isinstance(tail, TailPlan)
            assert MICRO_DRIFT_COUNT[0] <= len(tail.points) <= MICRO_DRIFT_COUNT[1]
            for px, py in tail.points:
                assert abs(px - TARGET[0]) <= 8 and abs(py - TARGET[1]) <= 8
            for d in tail.delays:
                assert MICRO_DRIFT_GAP_S[0] <= d <= MICRO_DRIFT_GAP_S[1]

    def test_never_returns_none(self):
        """指针语义至少保留一条收尾移动以刷新 hover 状态——不提供"完全不补"的方案。"""
        for option in ('micro_drift', 'slide_away'):
            for level in ('light', 'medium', 'heavy'):
                tail = plan_pointer_tail(rng(), TARGET, PERSONA, option=option,
                                         level=level, canvas_size=CANVAS)
                assert tail is not None and len(tail.points) >= 1

    def test_slide_away_only_in_heavy(self):
        """slide_away 仅 heavy；其他档位必须退化为 micro_drift 的幅度。"""
        r = rng()
        for level in ('light', 'medium'):
            for _ in range(300):
                tail = plan_pointer_tail(r, TARGET, PERSONA, option='slide_away',
                                         level=level, canvas_size=CANVAS)
                far = max(abs(px - TARGET[0]) + abs(py - TARGET[1])
                          for px, py in tail.points)
                assert far <= 16, f'{level} 出现了 heavy 专属的远距漂移 {far}px'

    def test_slide_away_drifts_far_in_heavy(self):
        r = rng()
        seen_far = False
        for _ in range(300):
            tail = plan_pointer_tail(r, TARGET, PERSONA, option='slide_away',
                                     level='heavy', canvas_size=CANVAS)
            far = max(((px - TARGET[0]) ** 2 + (py - TARGET[1]) ** 2) ** 0.5
                      for px, py in tail.points)
            if far >= SLIDE_AWAY_PX[0]:
                seen_far = True
            assert far <= SLIDE_AWAY_PX[1] + 10
        assert seen_far

    def test_all_points_within_canvas(self):
        """漂移不得走出画布——越界坐标会被 backend 直接投递。"""
        r = rng()
        for corner in ((2, 2), (1277, 717), (2, 717), (1277, 2)):
            for _ in range(300):
                tail = plan_pointer_tail(r, corner, PERSONA, option='slide_away',
                                         level='heavy', canvas_size=CANVAS)
                for px, py in tail.points:
                    assert 0 <= px <= CANVAS[0] - 1 and 0 <= py <= CANVAS[1] - 1

    def test_same_point_rejected(self):
        """same_point 是"今天"方案，只在 off 旁路出现。"""
        with pytest.raises(ValueError, match='option'):
            plan_pointer_tail(rng(), TARGET, PERSONA, option='same_point',
                              level='light', canvas_size=CANVAS)

    def test_reproducible(self):
        a = plan_pointer_tail(rng(), TARGET, PERSONA, option='micro_drift',
                              level='light', canvas_size=CANVAS)
        b = plan_pointer_tail(rng(), TARGET, PERSONA, option='micro_drift',
                              level='light', canvas_size=CANVAS)
        assert a == b


class TestTouchLiftoff:
    def test_none_returns_none(self):
        """touch_liftoff.none 保留 0.2 权重，是"约 20% 触摸动作不漂移"的人类方差，
        也是"今天方案默认不进 enabled 权重"的唯一显式例外。"""
        assert plan_touch_liftoff(rng(), TARGET, PERSONA,
                                  option='none', level='medium') is None

    def test_liftoff_drift_exactly_one_point(self):
        """真机验证前只放开到 1 个点（Spec §5 F / §4.10）——
        多出的 before-UP 位移可能把短按判成拖拽。"""
        r = rng()
        for _ in range(800):
            tail = plan_touch_liftoff(r, TARGET, PERSONA,
                                      option='liftoff_drift', level='medium')
            assert isinstance(tail, TailPlan)
            assert len(tail.points) == 1, '真机验证通过前不得放开到 2 个点'

    def test_liftoff_amplitude_and_gap(self):
        r = rng()
        lo, hi = LIFTOFF_DRIFT_PX
        for _ in range(800):
            tail = plan_touch_liftoff(r, TARGET, PERSONA,
                                      option='liftoff_drift', level='medium')
            (px, py), = tail.points
            dx, dy = abs(px - TARGET[0]), abs(py - TARGET[1])
            assert 0 < dx + dy <= hi * 2
            assert dx <= hi and dy <= hi
            assert 0.010 <= tail.delays[0] <= 0.030

    def test_liftoff_stays_in_canvas_at_corners(self):
        for target in ((0, 0), (1279, 0), (0, 719), (1279, 719)):
            for _ in range(300):
                tail = plan_touch_liftoff(rng(), target, PERSONA,
                                          option='liftoff_drift', level='medium', canvas_size=CANVAS)
                for px, py in tail.points:
                    assert 0 <= px < CANVAS[0] and 0 <= py < CANVAS[1]

    def test_unknown_option_rejected(self):
        with pytest.raises(ValueError, match='option'):
            plan_touch_liftoff(rng(), TARGET, PERSONA,
                               option='micro_drift', level='medium')


class TestPlanIdle:
    CURSOR = (300, 200)

    def test_below_drift_threshold_returns_none(self):
        for s in (0.0, 1.0, IDLE_DRIFT_THRESHOLD_S - 0.01):
            assert plan_idle(rng(), s, self.CURSOR, PERSONA, option='idle_drift',
                             level='medium', canvas_size=CANVAS) is None

    def test_at_drift_threshold_produces_plan(self):
        plan = plan_idle(rng(), IDLE_DRIFT_THRESHOLD_S + 0.01, self.CURSOR, PERSONA,
                         option='idle_drift', level='medium', canvas_size=CANVAS)
        assert isinstance(plan, MovePlan)

    def test_drift_distance_within_range(self):
        r = rng()
        lo, hi = IDLE_DRIFT_PX
        for _ in range(400):
            plan = plan_idle(r, 5.0, self.CURSOR, PERSONA, option='idle_drift',
                             level='medium', canvas_size=CANVAS)
            far = max(((px - self.CURSOR[0]) ** 2 + (py - self.CURSOR[1]) ** 2) ** 0.5
                      for px, py in plan.points)
            assert far <= hi + 5

    def test_park_needs_30s(self):
        assert plan_idle(rng(), 10.0, self.CURSOR, PERSONA, option='park',
                         level='medium', canvas_size=CANVAS) is None
        plan = plan_idle(rng(), IDLE_PARK_THRESHOLD_S + 0.01, self.CURSOR, PERSONA,
                         option='park', level='medium', canvas_size=CANVAS)
        assert isinstance(plan, MovePlan)

    def test_park_targets_canvas_edge(self):
        r = rng()
        for _ in range(300):
            plan = plan_idle(r, 60.0, self.CURSOR, PERSONA, option='park',
                             level='medium', canvas_size=CANVAS)
            ex, ey = plan.points[-1]
            on_edge = ex in (0, CANVAS[0] - 1) or ey in (0, CANVAS[1] - 1)
            assert on_edge, f'park 终点 {(ex, ey)} 不在画布边缘'

    def test_no_cursor_returns_none(self):
        """光标位置未知时不能凭空移动——那会把光标从未知处拽到某个坐标。"""
        assert plan_idle(rng(), 60.0, None, PERSONA, option='park',
                         level='medium', canvas_size=CANVAS) is None

    def test_all_points_within_canvas(self):
        r = rng()
        for cursor in ((1, 1), (1278, 718)):
            for option, since in (('idle_drift', 5.0), ('park', 60.0)):
                for _ in range(200):
                    plan = plan_idle(r, since, cursor, PERSONA, option=option,
                                     level='medium', canvas_size=CANVAS)
                    if plan is None:
                        continue
                    for px, py in plan.points:
                        assert 0 <= px <= CANVAS[0] - 1 and 0 <= py <= CANVAS[1] - 1

    def test_none_option_rejected(self):
        with pytest.raises(ValueError, match='option'):
            plan_idle(rng(), 60.0, self.CURSOR, PERSONA, option='none',
                      level='medium', canvas_size=CANVAS)

    def test_negative_since_rejected(self):
        with pytest.raises(ValueError):
            plan_idle(rng(), -1.0, self.CURSOR, PERSONA, option='idle_drift',
                      level='medium', canvas_size=CANVAS)


class TestHoldJitter:
    """维度 J（长按 hold 微颤随机游走，2026-08-26 调研对标新增）。"""

    def test_count_respected(self):
        assert len(plan_hold_jitter(rng(), TARGET, 0)) == 0
        assert len(plan_hold_jitter(rng(), TARGET, 5)) == 5
        assert len(plan_hold_jitter(rng(), TARGET, 200)) == 200

    def test_walk_within_amplitude_cap(self):
        # 随机游走距 target 不超过 HOLD_JITTER_WALK_MAX（远离平台 8~10px 长按容差）
        r = rng()
        for _ in range(300):
            for px, py in plan_hold_jitter(r, TARGET, 10):
                assert abs(px - TARGET[0]) <= HOLD_JITTER_WALK_MAX
                assert abs(py - TARGET[1]) <= HOLD_JITTER_WALK_MAX

    def test_all_points_within_canvas_at_corners(self):
        r = rng()
        for target in ((0, 0), (1279, 719), (1, 718), (1278, 1)):
            for _ in range(50):
                for px, py in plan_hold_jitter(r, target, 8, canvas_size=CANVAS):
                    assert 0 <= px <= CANVAS[0] - 1 and 0 <= py <= CANVAS[1] - 1

    def test_reproducible(self):
        a = plan_hold_jitter(rng(42), TARGET, 20)
        b = plan_hold_jitter(rng(42), TARGET, 20)
        assert a == b

    def test_invalid_count_rejected(self):
        with pytest.raises(ValueError):
            plan_hold_jitter(rng(), TARGET, -1)
        with pytest.raises(ValueError):
            plan_hold_jitter(rng(), TARGET, 1.5)

    def test_points_are_int_tuples(self):
        for px, py in plan_hold_jitter(rng(), TARGET, 10):
            assert isinstance(px, int) and isinstance(py, int)
