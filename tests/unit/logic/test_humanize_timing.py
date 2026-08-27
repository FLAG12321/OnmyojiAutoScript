"""维度 B/I/D/E/H 的时间层测试（Plan Task 5、6、7）。

时间层的共同硬要求：给定 RNG 与 option 必须完全可复现，且输出恒在声明边界内。
"边界内"不是防御性编程——45ms 下界正是维度 B 存在的理由：今天桌面 fast 点击
只按 10~25ms，那是真人手做不出来的机器指纹。
"""
import math

import numpy as np
import pytest

from module.device.humanize.persona import Persona
from module.device.humanize.plan import DwellPlan, _SwipeTail
from module.device.humanize.timing import (
    DWELL_CLIP_S,
    GAP_CLIP_FACTOR,
    HESITATE_RANGE_S,
    PRESS_FAST_MEDIAN_SCALE,
    PRESS_MAX_S,
    PRESS_MIN_S,
    PROFILE_MAX_POINTS,
    PROFILE_MIN_DELAY_S,
    RANDOM_TAIL_COUNT,
    RANDOM_TAIL_DELAY_S,
    SPEED_OPTIONS,
    gap_seconds,
    legacy_move_delays,
    plan_dwell,
    press_seconds,
    profiled_move_delays,
    segment_distances,
    swipe_tail,
    time_param_map,
)

pytestmark = pytest.mark.unit

SEED = 20260825
PERSONA = Persona.generate(SEED)

TARGET = (640, 360)


def rng(seed: int = 7) -> np.random.Generator:
    return np.random.Generator(np.random.PCG64(seed))


class TestPressSeconds:
    @pytest.mark.parametrize('option', ['lognormal', 'bimodal', 'gamma'])
    def test_reproducible_given_rng(self, option):
        a = press_seconds(rng(), PERSONA, option=option)
        b = press_seconds(rng(), PERSONA, option=option)
        assert a == b

    @pytest.mark.parametrize('option', ['lognormal', 'bimodal', 'gamma'])
    def test_within_bounds_large_sample(self, option):
        r = rng()
        vals = [press_seconds(r, PERSONA, option=option) for _ in range(2000)]
        assert min(vals) >= PRESS_MIN_S
        assert max(vals) <= PRESS_MAX_S

    @pytest.mark.parametrize('option', ['lognormal', 'bimodal', 'gamma'])
    def test_fast_is_shorter_in_median_but_not_below_human_floor(self, option):
        """fast 只缩放中位数，下界仍是 45ms——不为 fast 保留最强的那条指纹。"""
        r1, r2 = rng(11), rng(11)
        normal = sorted(press_seconds(r1, PERSONA, option=option) for _ in range(800))
        fast = sorted(press_seconds(r2, PERSONA, option=option, fast=True) for _ in range(800))
        assert fast[400] < normal[400]
        assert min(fast) >= PRESS_MIN_S

    def test_fast_scale_constant_is_declared(self):
        assert PRESS_FAST_MEDIAN_SCALE == pytest.approx(0.65)

    def test_no_touch_parameter(self):
        """按压分布不按协议类型分叉（Spec §5 B）。"""
        import inspect
        params = inspect.signature(press_seconds).parameters
        assert 'touch' not in params
        assert 'enabled' not in params

    def test_illegal_option_raises(self):
        """契约 12：非法 option 抛错而不静默回退，否则会藏起档位过滤的 bug。"""
        with pytest.raises(ValueError, match='option'):
            press_seconds(rng(), PERSONA, option='uniform')

    def test_bimodal_has_distracted_tail(self):
        """bimodal 的 15% 分神分支应产生明显长于 lognormal 中位的样本。"""
        r = rng(3)
        vals = [press_seconds(r, PERSONA, option='bimodal') for _ in range(3000)]
        assert sum(1 for v in vals if v >= 0.25) / len(vals) > 0.10


class TestGapSeconds:
    def test_fixed_returns_default_exactly(self):
        """fixed 是"今天"方案，只在 off 旁路语义下出现，必须逐字节等于原常量。"""
        assert gap_seconds(rng(), PERSONA, 0.05, option='fixed') == 0.05

    def test_jitter_within_clip_factor(self):
        r = rng()
        lo, hi = GAP_CLIP_FACTOR
        vals = [gap_seconds(r, PERSONA, 0.05, option='jitter') for _ in range(3000)]
        assert min(vals) >= 0.05 * lo
        assert max(vals) <= 0.05 * hi

    def test_jitter_mean_preserves_default(self):
        """维度 I 的承诺是"同均值"——零额外耗时，只打散常量指纹。"""
        r = rng()
        vals = [gap_seconds(r, PERSONA, 0.05, option='jitter') for _ in range(20000)]
        assert sum(vals) / len(vals) == pytest.approx(0.05, rel=0.06)

    def test_reproducible(self):
        assert gap_seconds(rng(), PERSONA, 0.05, option='jitter') == \
               gap_seconds(rng(), PERSONA, 0.05, option='jitter')

    @pytest.mark.parametrize('bad', [-0.01, float('nan'), math.inf])
    def test_illegal_default_raises(self, bad):
        with pytest.raises(ValueError):
            gap_seconds(rng(), PERSONA, bad, option='jitter')

    def test_zero_default_returns_zero(self):
        """零间隔不该被 jitter 放大成正数——那会给不存在等待的路径凭空加耗时。"""
        assert gap_seconds(rng(), PERSONA, 0.0, option='jitter') == 0.0


class TestLegacyMoveDelays:
    def test_length_matches_count_exactly(self):
        for count in (1, 2, 5, 12):
            assert len(legacy_move_delays(rng(), count, 0.01)) == count

    def test_jitter_is_correlated_not_white_noise(self):
        """禁止每点独立白噪声（Spec §5 D / 复审 4.2 第 5 条）。

        相邻点的一阶自相关必须显著为正；白噪声的自相关期望是 0。
        """
        vals = legacy_move_delays(rng(), 400, 0.01)
        arr = np.asarray(vals) - np.mean(vals)
        autocorr = float(np.sum(arr[:-1] * arr[1:]) / np.sum(arr * arr))
        assert autocorr > 0.3, f'自相关 {autocorr:.3f} 过低，疑似白噪声'

    def test_normalizes_to_budget(self):
        out = legacy_move_delays(rng(), 8, 0.01, total_budget_s=0.2)
        assert sum(out) == pytest.approx(0.2)

    def test_no_budget_keeps_base_scale(self):
        out = legacy_move_delays(rng(), 200, 0.01)
        assert sum(out) / len(out) == pytest.approx(0.01, rel=0.15)

    def test_all_non_negative(self):
        out = legacy_move_delays(rng(), 200, 0.002)
        assert min(out) >= 0.0

    def test_negative_budget_rejected(self):
        with pytest.raises(ValueError):
            legacy_move_delays(rng(), 4, 0.01, total_budget_s=-0.1)

    def test_zero_count_rejected(self):
        with pytest.raises(ValueError):
            legacy_move_delays(rng(), 0, 0.01)

    def test_does_not_read_legacy_point_spacing(self):
        """仅供 legacy 点位使用，但不得读取或推导点距——那会二次编码速度。"""
        import inspect
        params = inspect.signature(legacy_move_delays).parameters
        assert 'points' not in params
        assert 'distances' not in params


class TestSegmentDistances:
    def test_computes_real_arc_segments(self):
        d = segment_distances((0, 0), [(3, 4), (3, 4), (6, 8)])
        assert d == pytest.approx([5.0, 0.0, 5.0])


class TestProfiledMoveDelays:
    @pytest.mark.parametrize('profile', SPEED_OPTIONS)
    def test_normalizes_to_budget(self, profile):
        d = [10.0, 30.0, 50.0, 30.0, 10.0]
        out = profiled_move_delays(rng(), d, 0.12, profile)
        assert len(out) == len(d)
        assert sum(out) == pytest.approx(0.12)

    def test_min_jerk_speed_curve_is_bell_shaped(self):
        """必须用实际速度 distance/delay 断言形状，而不是只断言 delay 本身。

        这是复审 3.2 的核心要求：delay 形状能被"点距已编码速度"的情况骗过，
        速度形状不能。
        """
        # 等距分段：此时速度形状完全由 delay 决定，钟形应清晰可见
        d = [10.0] * 11
        out = profiled_move_delays(rng(), d, 0.2, 'min_jerk')
        speeds = [dist / dt for dist, dt in zip(d, out)]
        peak = speeds.index(max(speeds))
        assert 3 <= peak <= 7, f'峰值在第 {peak} 段，不在中段'
        assert speeds[0] < speeds[peak] and speeds[-1] < speeds[peak]
        assert speeds[0] == pytest.approx(speeds[-1], rel=0.35)

    def test_ease_out_speed_is_monotone_decreasing(self):
        d = [10.0] * 10
        out = profiled_move_delays(rng(), d, 0.2, 'ease_out')
        speeds = [dist / dt for dist, dt in zip(d, out)]
        # 允许相关抖动带来的局部起伏，用首尾三段均值比较趋势
        assert sum(speeds[:3]) / 3 > sum(speeds[-3:]) / 3

    def test_uses_real_arc_length_not_index(self):
        """不允许用 i/n 替代累计弧长（Spec §5 D）。

        构造极不均匀的分段：若按索引参数化，第 0 段（占全长 90%）会拿到
        τ≈0.05 的极低速度；按真实弧长它的中点 τ≈0.45，接近峰值。
        """
        d = [900.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0]
        out = profiled_move_delays(rng(), d, 0.2, 'min_jerk')
        speeds = [dist / dt for dist, dt in zip(d, out)]
        # 长段落在弧长中部，速度应接近全局最大而不是最小
        assert speeds[0] > max(speeds[1:]) * 0.5

    def test_zero_total_distance_does_not_divide_by_zero(self):
        out = profiled_move_delays(rng(), [0.0, 0.0, 0.0], 0.09, 'min_jerk')
        assert sum(out) == pytest.approx(0.09)
        assert out == pytest.approx([0.03, 0.03, 0.03])

    def test_zero_distance_and_zero_budget_returns_zeros(self):
        assert profiled_move_delays(rng(), [0.0, 0.0], 0.0, 'min_jerk') == [0.0, 0.0]

    def test_empty_distances_returns_empty(self):
        assert profiled_move_delays(rng(), [], 0.1, 'min_jerk') == []

    def test_negative_budget_rejected(self):
        with pytest.raises(ValueError):
            profiled_move_delays(rng(), [10.0], -0.1, 'min_jerk')

    def test_negative_distance_rejected(self):
        with pytest.raises(ValueError):
            profiled_move_delays(rng(), [10.0, -1.0], 0.1, 'min_jerk')

    def test_unknown_profile_rejected(self):
        with pytest.raises(ValueError, match='profile'):
            profiled_move_delays(rng(), [10.0], 0.1, 'linear')

    def test_reproducible(self):
        d = [10.0, 20.0, 30.0]
        assert profiled_move_delays(rng(), d, 0.1, 'min_jerk') == \
               profiled_move_delays(rng(), d, 0.1, 'min_jerk')


class TestNoAverageBudgetFormula:
    def test_point_reduction_uses_actual_min_delay(self):
        """禁止 int(total_budget_s / PROFILE_MIN_DELAY_S) 这个平均值公式。

        min-jerk 的 delay 极不均匀：40ms 预算按平均值算能塞 8 点，
        实际生成后 min(delay) 远低于 5ms，只有约 3 点可信。
        """
        budget = 0.040
        naive = int(budget / PROFILE_MIN_DELAY_S)
        assert naive == 8

        accepted = None
        for count in range(PROFILE_MAX_POINTS, 1, -1):
            d = [10.0] * count
            out = profiled_move_delays(rng(), d, budget, 'min_jerk')
            if min(out) >= PROFILE_MIN_DELAY_S:
                accepted = count
                break
        assert accepted is not None
        assert accepted < naive, (
            f'实际可接受 {accepted} 点，平均值公式给出 {naive} 点——'
            f'差距正是禁用该公式的理由')


class TestPlanDwell:
    def test_gauss_single_wait_only_segment(self):
        plan = plan_dwell(rng(), TARGET, PERSONA, option='gauss', level='medium')
        assert isinstance(plan, DwellPlan)
        assert len(plan.segments) == 1
        assert plan.segments[0][0] is None

    def test_gauss_within_clip(self):
        r = rng()
        for _ in range(2000):
            plan = plan_dwell(r, TARGET, PERSONA, option='gauss', level='medium')
            total = sum(sec for _, sec in plan.segments)
            assert DWELL_CLIP_S[0] <= total <= DWELL_CLIP_S[1]

    def test_settle_splits_into_2_or_3_segments_with_micro_updates(self):
        """真人手不会绝对静止：settle 在停顿中插入 1~2px 的位置更新。"""
        r = rng()
        seen_counts = set()
        for _ in range(200):
            plan = plan_dwell(r, TARGET, PERSONA, option='settle', level='medium')
            seen_counts.add(len(plan.segments))
            moved = [p for p, _ in plan.segments if p is not None]
            assert moved, 'settle 必须至少有一个位置更新'
            for p in moved:
                assert abs(p[0] - TARGET[0]) <= 2 and abs(p[1] - TARGET[1]) <= 2
        assert seen_counts <= {2, 3} and len(seen_counts) == 2

    def test_settle_total_still_within_clip(self):
        r = rng()
        for _ in range(500):
            plan = plan_dwell(r, TARGET, PERSONA, option='settle', level='medium')
            total = sum(sec for _, sec in plan.segments)
            assert DWELL_CLIP_S[0] <= total <= DWELL_CLIP_S[1]

    def test_settle_extra_points_stay_in_canvas_at_corners(self):
        canvas = (1280, 720)
        for target in ((0, 0), (1279, 0), (0, 719), (1279, 719)):
            for _ in range(300):
                plan = plan_dwell(rng(), target, PERSONA, option='settle', level='medium',
                                  canvas_size=canvas)
                for point, _ in plan.segments:
                    if point is not None:
                        assert 0 <= point[0] < canvas[0] and 0 <= point[1] < canvas[1]

    def test_hesitate_only_in_heavy(self):
        """hesitate 的 300~800ms 长尾只属 heavy；medium 必须退化为 gauss。"""
        r = rng()
        for _ in range(3000):
            plan = plan_dwell(r, TARGET, PERSONA, option='hesitate', level='medium')
            total = sum(sec for _, sec in plan.segments)
            assert total <= DWELL_CLIP_S[1], 'medium 出现了 heavy 专属长尾'

    def test_hesitate_produces_long_tail_in_heavy(self):
        r = rng()
        totals = [sum(sec for _, sec in
                      plan_dwell(r, TARGET, PERSONA, option='hesitate', level='heavy').segments)
                  for _ in range(4000)]
        long_ones = [t for t in totals if t >= HESITATE_RANGE_S[0]]
        # 概率是 persona.hesitate_p ∈ [0.02, 0.07]，4000 次至少应出现若干次
        assert long_ones, 'heavy 下 hesitate 从未触发长尾'
        assert max(long_ones) <= HESITATE_RANGE_S[1]
        assert len(long_ones) / len(totals) < 0.15

    def test_illegal_option_raises(self):
        with pytest.raises(ValueError, match='option'):
            plan_dwell(rng(), TARGET, PERSONA, option='none', level='medium')

    def test_reproducible(self):
        a = plan_dwell(rng(), TARGET, PERSONA, option='settle', level='medium')
        b = plan_dwell(rng(), TARGET, PERSONA, option='settle', level='medium')
        assert a == b


class TestSwipeTail:
    BASE = [0.01, 0.012, 0.011, 0.013, 0.010, 0.012, 0.011, 0.014]

    def test_random_tail_count_and_delay_range(self):
        r = rng()
        for _ in range(500):
            tail = swipe_tail(r, self.BASE, option='random_tail', level='medium')
            assert isinstance(tail, _SwipeTail)
            assert RANDOM_TAIL_COUNT[0] <= tail.count <= RANDOM_TAIL_COUNT[1]
            for d in tail.delays:
                assert RANDOM_TAIL_DELAY_S[0] <= d <= RANDOM_TAIL_DELAY_S[1]

    def test_natural_returns_none_in_medium(self):
        """medium/heavy 的 natural 沿用 D 的自然减速，不替换末段。"""
        assert swipe_tail(rng(), self.BASE, option='natural', level='medium') is None
        assert swipe_tail(rng(), self.BASE, option='natural', level='heavy') is None

    def test_natural_replaces_tail_in_light(self):
        """light 无可信 profile，末段要替换成基于主体中位数的近恒定 jitter，
        否则就是继续保留 legacy 的固定尾巴。"""
        tail = swipe_tail(rng(), self.BASE, option='natural', level='light')
        assert isinstance(tail, _SwipeTail)
        assert RANDOM_TAIL_COUNT[0] <= tail.count <= RANDOM_TAIL_COUNT[1]
        median = sorted(self.BASE)[len(self.BASE) // 2]
        # 单个样本可被 max(0.0, ·) 合法压到 0（AR(1) 噪声约 -2σ 尾部），
        # 逐样本下界必然误报；均值才是稳健的近恒定判据，上界仍守住"不爆量"
        for d in tail.delays:
            assert 0.0 <= d <= 3.0 * median
        assert 0.3 * median <= sum(tail.delays) / len(tail.delays) <= 3.0 * median

    def test_tail_count_clipped_to_available_points(self):
        """点数不足时裁剪 tail count，不添加额外点、不让长度失配。"""
        for n in (1, 2, 3):
            base = [0.01] * n
            tail = swipe_tail(rng(), base, option='random_tail', level='medium')
            assert tail.count <= n
            assert len(tail.delays) == tail.count

    def test_empty_base_returns_none(self):
        assert swipe_tail(rng(), [], option='random_tail', level='medium') is None

    def test_fixed3_rejected(self):
        """fixed3 只是旧行为记录，不进人格权重，也不该被传进来。"""
        with pytest.raises(ValueError, match='option'):
            swipe_tail(rng(), self.BASE, option='fixed3', level='medium')

    def test_reproducible(self):
        a = swipe_tail(rng(), self.BASE, option='random_tail', level='medium')
        b = swipe_tail(rng(), self.BASE, option='random_tail', level='medium')
        assert a == b


class TestReportRateHz:
    """设备回报率：人格分位数映射到真实设备区间（触摸面板 / 鼠标）。"""

    def test_touch_interval_bounds(self):
        from module.device.humanize.timing import (
            MOUSE_REPORT_RATE_HZ, TOUCH_REPORT_RATE_HZ, report_rate_hz)
        for q in (0.0, 0.5, 1.0):
            assert TOUCH_REPORT_RATE_HZ[0] <= report_rate_hz(q) <= TOUCH_REPORT_RATE_HZ[1]
            assert MOUSE_REPORT_RATE_HZ[0] <= report_rate_hz(q, mouse=True) \
                <= MOUSE_REPORT_RATE_HZ[1]

    def test_quantile_maps_linearly(self):
        from module.device.humanize.timing import TOUCH_REPORT_RATE_HZ, report_rate_hz
        lo, hi = TOUCH_REPORT_RATE_HZ
        assert report_rate_hz(0.0) == pytest.approx(lo)
        assert report_rate_hz(1.0) == pytest.approx(hi)
        assert report_rate_hz(0.5) == pytest.approx((lo + hi) / 2)

    def test_mouse_interval_higher_than_touch(self):
        from module.device.humanize.timing import report_rate_hz
        assert report_rate_hz(0.5, mouse=True) > report_rate_hz(0.5)

    def test_invalid_quantile_raises(self):
        from module.device.humanize.timing import report_rate_hz
        for q in (-0.1, 1.1):
            with pytest.raises(ValueError, match='quantile'):
                report_rate_hz(q)


class TestTimeParamMap:
    """等时间映射 u↦F⁻¹(u)：慢速区点密集（HumanCursor tween 的解析等价物）。"""

    @pytest.mark.parametrize('profile', SPEED_OPTIONS)
    def test_endpoints_and_monotone(self, profile):
        m = time_param_map(profile)
        assert m(0.0) == pytest.approx(0.0, abs=1e-9)
        assert m(1.0) == pytest.approx(1.0, abs=1e-9)
        prev = -1.0
        for i in range(1, 21):
            t = m(i / 20)
            assert t > prev, 'F 严格递增 → 逆映射必须严格递增'
            prev = t

    def test_min_jerk_dense_at_both_ends(self):
        """min_jerk 对称钟形：前 20% 时间走不到 20% 参数，后 20% 时间超过 80%。"""
        m = time_param_map('min_jerk')
        assert m(0.2) < 0.2
        assert m(0.8) > 0.8

    def test_ease_out_dense_at_end(self):
        """ease_out 起步快收尾慢：同一时间比例走到更靠后的参数位置。"""
        m = time_param_map('ease_out')
        assert m(0.5) > 0.5

    def test_density_floor_bounds_end_concentration(self):
        """10% 峰值速度的密度地板：端点邻域的参数跨度不得塌缩到网格分辨率。"""
        m = time_param_map('min_jerk')
        # 前半时间的参数跨度 > 1/256 的网格步长 × 4——若忘加地板，这里会接近 0
        assert m(0.5) > 4 / 256

    def test_unknown_profile_raises(self):
        with pytest.raises(ValueError, match='profile'):
            time_param_map('warp')

    def test_u_clipped_into_unit_interval(self):
        """越界 u 裁剪进 [0,1]，不触发 np.interp 外推。"""
        m = time_param_map('min_jerk')
        assert m(-0.5) == pytest.approx(0.0, abs=1e-9)
        assert m(1.5) == pytest.approx(1.0, abs=1e-9)
