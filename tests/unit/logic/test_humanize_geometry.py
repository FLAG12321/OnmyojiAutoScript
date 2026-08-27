"""维度 A 落点与维度 C 形状的几何层测试（Plan Task 8、9）。

几何层的两条硬要求：
1. 结果恒在 ROI / 画布闭区间内——越界坐标会被 backend 直接投递出去；
2. 起点与终点**永不被修改**。裁剪中间控制点是安全的，裁剪端点等于偷偷改变
   业务目标，那是语义变化而不是拟人化（复审 3.9）。
"""
import numpy as np
import pytest

from module.device.humanize.geometry import (
    EDGE_AVOID_INSET,
    OVERSHOOT_MAX_PX,
    POINT_OPTIONS,
    SHAPE_OPTIONS,
    sample_point,
    shape_points,
)
from module.device.humanize.persona import Persona

pytestmark = pytest.mark.unit

SEED = 20260825
PERSONA = Persona.generate(SEED)
ROI = (100, 200, 80, 40)   # x, y, w, h
CANVAS = (1280, 720)


def rng(seed: int = 7) -> np.random.Generator:
    return np.random.Generator(np.random.PCG64(seed))


class TestSamplePoint:
    @pytest.mark.parametrize('option', POINT_OPTIONS)
    def test_always_inside_roi_closed_interval(self, option):
        r = rng()
        x, y, w, h = ROI
        for _ in range(5000):
            px, py = sample_point(r, ROI, PERSONA, option=option, prev=(0, 0))
            assert x <= px <= x + w - 1, f'{option}: x={px} 越界'
            assert y <= py <= y + h - 1, f'{option}: y={py} 越界'

    @pytest.mark.parametrize('option', POINT_OPTIONS)
    def test_returns_int_tuple(self, option):
        p = sample_point(rng(), ROI, PERSONA, option=option, prev=(0, 0))
        assert isinstance(p, tuple) and len(p) == 2
        assert all(isinstance(v, int) and not isinstance(v, bool) for v in p)

    def test_center_gauss_is_center_biased(self):
        """大样本验证中心偏置，不用单次随机值断言分布。"""
        r = rng()
        x, y, w, h = ROI
        cx, cy = x + w / 2, y + h / 2
        pts = [sample_point(r, ROI, PERSONA, option='center_gauss') for _ in range(6000)]
        mx = sum(p[0] for p in pts) / len(pts)
        my = sum(p[1] for p in pts) / len(pts)
        assert abs(mx - cx) < w * 0.05
        assert abs(my - cy) < h * 0.05
        # 中心区（半宽内）的命中率应显著高于均匀分布的 50%
        inner = sum(1 for px, _ in pts if abs(px - cx) <= w / 4)
        assert inner / len(pts) > 0.60

    def test_offset_gauss_follows_persona_aim_bias(self):
        """同一个人的握姿偏心是固定的，所以偏移方向必须跟随人格而非每次随机。"""
        r = rng()
        x, y, w, h = ROI
        pts = [sample_point(r, ROI, PERSONA, option='offset_gauss') for _ in range(6000)]
        mx = sum(p[0] for p in pts) / len(pts)
        expected = x + w * (0.5 + PERSONA.aim_bias[0])
        assert abs(mx - expected) < w * 0.06

    def test_edge_avoid_stays_within_inset(self):
        r = rng()
        x, y, w, h = ROI
        ix, iy = w * EDGE_AVOID_INSET, h * EDGE_AVOID_INSET
        for _ in range(3000):
            px, py = sample_point(r, ROI, PERSONA, option='edge_avoid')
            assert x + ix - 1 <= px <= x + w - ix
            assert y + iy - 1 <= py <= y + h - iy

    def test_prev_biased_shifts_toward_prev(self):
        r = rng()
        x, y, w, h = ROI
        far = (x - 400, y)
        near_pts = [sample_point(r, ROI, PERSONA, option='prev_biased', prev=far)
                    for _ in range(4000)]
        plain = [sample_point(r, ROI, PERSONA, option='center_gauss') for _ in range(4000)]
        # prev 在左侧远处时，落点均值应比纯 center_gauss 更偏左（朝 prev 方向）
        assert sum(p[0] for p in near_pts) / len(near_pts) < \
               sum(p[0] for p in plain) / len(plain)

    def test_prev_biased_without_prev_falls_back_to_center_gauss(self):
        a = sample_point(rng(), ROI, PERSONA, option='prev_biased', prev=None)
        b = sample_point(rng(), ROI, PERSONA, option='center_gauss')
        assert a == b

    def test_one_pixel_roi(self):
        assert sample_point(rng(), (5, 6, 1, 1), PERSONA, option='center_gauss') == (5, 6)

    @pytest.mark.parametrize('bad', [
        (0, 0, 0, 10), (0, 0, 10, 0), (0, 0, -5, 10), (0, 0, 10, -5),
    ])
    def test_empty_or_negative_roi_raises(self, bad):
        with pytest.raises(ValueError, match='roi'):
            sample_point(rng(), bad, PERSONA, option='center_gauss')

    def test_malformed_roi_raises(self):
        with pytest.raises(ValueError, match='roi'):
            sample_point(rng(), (0, 0, 10), PERSONA, option='center_gauss')

    def test_uniform_option_rejected(self):
        """uniform 是"今天"方案，不进权重也不该被传进来。"""
        with pytest.raises(ValueError, match='option'):
            sample_point(rng(), ROI, PERSONA, option='uniform')

    def test_does_not_touch_global_rng(self):
        np.random.seed(999)
        before = np.random.random()
        np.random.seed(999)
        sample_point(rng(), ROI, PERSONA, option='center_gauss')
        after = np.random.random()
        assert before == after

    def test_reproducible(self):
        assert sample_point(rng(), ROI, PERSONA, option='offset_gauss') == \
               sample_point(rng(), ROI, PERSONA, option='offset_gauss')


class TestShapePoints:
    CASES = {
        'horizontal': ((100, 360), (900, 360)),
        'vertical': ((640, 100), (640, 620)),     # legacy cBezier 在此除零
        'diagonal': ((200, 150), (1000, 600)),
        'short': ((640, 360), (652, 366)),
        'reverse': ((1000, 600), (200, 150)),
    }

    @pytest.mark.parametrize('option', SHAPE_OPTIONS)
    @pytest.mark.parametrize('name', list(CASES))
    def test_last_point_is_exactly_end(self, option, name):
        """终点永不被修改（Spec §4.11）。裁剪端点等于偷偷改业务目标。"""
        start, end = self.CASES[name]
        result = shape_points(rng(), start, end, option=option,
                              max_points=12, persona=PERSONA, canvas_size=CANVAS)
        assert result is not None, f'{option}/{name} 意外返回 None'
        points, extra = result
        assert points[-1] == end

    @pytest.mark.parametrize('option', SHAPE_OPTIONS)
    @pytest.mark.parametrize('name', list(CASES))
    def test_start_not_included_and_count_bounded(self, option, name):
        start, end = self.CASES[name]
        points, _ = shape_points(rng(), start, end, option=option,
                                 max_points=12, persona=PERSONA, canvas_size=CANVAS)
        assert points[0] != start or start == end
        assert 1 <= len(points) <= 12

    @pytest.mark.parametrize('option', SHAPE_OPTIONS)
    def test_vertical_path_no_division_by_zero(self, option):
        """legacy cBezier 用 X 做参数轴，垂直路径除零——desktop_trace 至今留着绕行分支。
        自实现用 t ∈ [0,1] 参数化，垂直路径必须正常。"""
        start, end = self.CASES['vertical']
        for _ in range(200):
            points, _ = shape_points(rng(), start, end, option=option,
                                     max_points=12, persona=PERSONA, canvas_size=CANVAS)
            assert points[-1] == end

    @pytest.mark.parametrize('option', SHAPE_OPTIONS)
    def test_all_points_int_tuples(self, option):
        start, end = self.CASES['diagonal']
        points, _ = shape_points(rng(), start, end, option=option,
                                 max_points=12, persona=PERSONA, canvas_size=CANVAS)
        for p in points:
            assert isinstance(p, tuple) and len(p) == 2
            assert all(isinstance(v, int) and not isinstance(v, bool) for v in p)

    def test_extra_only_from_two_phase(self):
        """extra 是点索引→额外停顿秒数，只有 two_phase 可以非空。"""
        start, end = self.CASES['diagonal']
        for option in SHAPE_OPTIONS:
            _, extra = shape_points(rng(), start, end, option=option,
                                    max_points=12, persona=PERSONA, canvas_size=CANVAS)
            if option == 'two_phase':
                assert extra, 'two_phase 必须记录停顿'
                assert all(0 <= k < 12 and v > 0 for k, v in extra.items())
            else:
                assert extra == {}, f'{option} 不该产生 extra'

    def test_overshoot_amplitude_capped(self):
        """过冲幅度硬上限 20px——过冲点虽在画布内，仍可能落进相邻控件。"""
        start, end = self.CASES['horizontal']
        r = rng()
        for _ in range(400):
            points, _ = shape_points(r, start, end, option='overshoot',
                                     max_points=12, persona=PERSONA, canvas_size=CANVAS)
            beyond = max(px - end[0] for px, _ in points)
            assert beyond <= OVERSHOOT_MAX_PX + 1, f'过冲 {beyond}px 超上限'

    def test_arc_side_follows_persona(self):
        """同一个人手腕转动方向一致，所以弧向由人格固定而非每次随机
        （legacy cBezier 的 random.choice([-1,1]) 正是这里要摆脱的）。"""
        start, end = self.CASES['horizontal']
        r = rng()
        sides = set()
        for _ in range(300):
            points, _ = shape_points(r, start, end, option='arc',
                                     max_points=12, persona=PERSONA, canvas_size=CANVAS)
            mid = points[len(points) // 2]
            sides.add(1 if mid[1] < start[1] else -1)
        assert len(sides) == 1, f'弧向出现两个方向 {sides}，人格未生效'

    def test_jitter_line_stays_near_straight_line(self):
        start, end = self.CASES['diagonal']
        points, _ = shape_points(rng(), start, end, option='jitter_line',
                                 max_points=12, persona=PERSONA, canvas_size=CANVAS)
        ax, ay = end[0] - start[0], end[1] - start[1]
        norm = (ax * ax + ay * ay) ** 0.5
        for px, py in points[:-1]:
            # 点到直线距离 = |叉积| / |方向|
            d = abs(ax * (py - start[1]) - ay * (px - start[0])) / norm
            assert d <= 4.0, f'偏离直线 {d:.1f}px，超出 1~3px 抖动'

    def test_jitter_line_stays_in_canvas_at_corners(self):
        # 四角出发的水平轨迹会让法向抖动直接碰到上下边界。
        for start, end in (((0, 0), (100, 0)), ((1279, 0), (1179, 0)),
                           ((0, 719), (100, 719)), ((1279, 719), (1179, 719))):
            for _ in range(300):
                points, _ = shape_points(rng(), start, end, option='jitter_line',
                                         max_points=12, persona=PERSONA, canvas_size=CANVAS)
                for px, py in points:
                    assert 0 <= px < CANVAS[0] and 0 <= py < CANVAS[1]

    @pytest.mark.parametrize('option', SHAPE_OPTIONS)
    @pytest.mark.parametrize('bad_end', [(-1, 360), (1280, 360), (640, -5), (640, 720)])
    def test_out_of_canvas_endpoint_returns_none(self, option, bad_end):
        """端点越界返回 None 回退 legacy，绝不 clip 后接回——
        那会让同一次调用在不同档位产生不同终点（复审 3.9）。"""
        assert shape_points(rng(), (640, 360), bad_end, option=option,
                            max_points=12, persona=PERSONA, canvas_size=CANVAS) is None

    @pytest.mark.parametrize('option', SHAPE_OPTIONS)
    def test_out_of_canvas_start_returns_none(self, option):
        assert shape_points(rng(), (-3, 360), (640, 360), option=option,
                            max_points=12, persona=PERSONA, canvas_size=CANVAS) is None

    def test_accepts_full_option_set(self):
        """shape_points 不做档位/gesture_kind 过滤——允许集测试放在 facade。"""
        assert set(SHAPE_OPTIONS) == {
            'bezier', 's_curve', 'overshoot', 'two_phase', 'arc', 'jitter_line'}

    def test_unknown_option_raises(self):
        with pytest.raises(ValueError, match='option'):
            shape_points(rng(), (0, 0), (10, 10), option='spiral',
                         max_points=12, persona=PERSONA, canvas_size=CANVAS)

    def test_max_points_two_still_valid(self):
        """降点到底（count=2）时几何仍必须可用，否则降点循环会无解。"""
        start, end = self.CASES['diagonal']
        for option in SHAPE_OPTIONS:
            points, _ = shape_points(rng(), start, end, option=option,
                                     max_points=2, persona=PERSONA, canvas_size=CANVAS)
            assert len(points) <= 2 and points[-1] == end

    def test_no_legacy_cbezier_dependency(self):
        """三份 legacy 贝塞尔都不得进入新几何层（Spec §5 C）。"""
        import inspect

        from module.device.humanize import geometry
        src = inspect.getsource(geometry)
        assert 'cBezier' not in src
        assert '_generate_bezier_points' not in src

    def test_reproducible(self):
        start, end = self.CASES['diagonal']
        a = shape_points(rng(), start, end, option='bezier',
                         max_points=12, persona=PERSONA, canvas_size=CANVAS)
        b = shape_points(rng(), start, end, option='bezier',
                         max_points=12, persona=PERSONA, canvas_size=CANVAS)
        assert a == b


class TestSCurveShape:
    """s_curve：控制点分居法线两侧的 S 形拐点轨迹（吸收自 HumanCursor 的
    多节点随机贝塞尔——方向修正是低频人类特征，单弧 bezier 永远做不到）。"""

    def test_inflects_across_baseline(self):
        """水平基线上，轨迹相对基线的垂直偏移必须两侧都出现（存在拐点）。"""
        start, end = (100, 360), (1100, 360)
        for seed in range(8):
            points, _ = shape_points(rng(seed), start, end, option='s_curve',
                                     max_points=24, persona=PERSONA, canvas_size=CANVAS)
            assert points[-1] == end
            sides = [1 if p[1] > 360 else (-1 if p[1] < 360 else 0) for p in points]
            nz = [s for s in sides if s != 0]
            assert 1 in nz and -1 in nz, f'seed={seed}: S 形必须两侧都出现，实际 {nz[:5]}'

    def test_points_in_canvas_and_endpoint_exact(self):
        for seed in range(5):
            points, _ = shape_points(rng(seed), (200, 200), (1000, 550),
                                     option='s_curve', max_points=20,
                                     persona=PERSONA, canvas_size=CANVAS)
            assert 1 <= len(points) <= 20
            assert points[-1] == (1000, 550)
            for px, py in points:
                assert 0 <= px <= 1279 and 0 <= py <= 719

    def test_max_points_two_still_valid(self):
        points, _ = shape_points(rng(), (100, 100), (600, 500), option='s_curve',
                                 max_points=2, persona=PERSONA, canvas_size=CANVAS)
        assert len(points) <= 2 and points[-1] == (600, 500)

    def test_reproducible(self):
        a = shape_points(rng(), (100, 100), (600, 500), option='s_curve',
                         max_points=12, persona=PERSONA, canvas_size=CANVAS)
        b = shape_points(rng(), (100, 100), (600, 500), option='s_curve',
                         max_points=12, persona=PERSONA, canvas_size=CANVAS)
        assert a == b


class TestEqualTimeSampling:
    """t_map 等时间采样：慢速区点密集（HumanCursor tween 的架构内等价物）。"""

    def test_min_jerk_dense_at_both_ends(self):
        """min_jerk 两端慢：首段/末段步距必须小于中段最大步距。"""
        from module.device.humanize.timing import time_param_map
        t_map = time_param_map('min_jerk')
        start, end = (100, 360), (1100, 360)
        points, _ = shape_points(rng(3), start, end, option='bezier',
                                 max_points=20, persona=PERSONA,
                                 canvas_size=CANVAS, t_map=t_map)
        assert points[-1] == end
        dists = []
        prev = start
        for p in points:
            dists.append(((p[0] - prev[0]) ** 2 + (p[1] - prev[1]) ** 2) ** 0.5)
            prev = p
        assert dists[0] < max(dists), '首段（起步慢）应比中段步距小'
        assert dists[-1] < max(dists), '末段（收尾慢）应比中段步距小'

    def test_ease_out_dense_at_end(self):
        """ease_out 单调减速：末段步距小于中段，且终点密集不对称。"""
        from module.device.humanize.timing import time_param_map
        t_map = time_param_map('ease_out')
        start, end = (100, 360), (1100, 360)
        points, _ = shape_points(rng(5), start, end, option='arc',
                                 max_points=20, persona=PERSONA,
                                 canvas_size=CANVAS, t_map=t_map)
        dists = []
        prev = start
        for p in points:
            dists.append(((p[0] - prev[0]) ** 2 + (p[1] - prev[1]) ** 2) ** 0.5)
            prev = p
        assert dists[-1] < max(dists), '末段（减速区）应比中段步距小'

    def test_none_keeps_uniform_sampling(self):
        """t_map=None 是恒等映射：与显式传 u→u 的结果逐点相同。"""
        a = shape_points(rng(9), (100, 100), (600, 500), option='bezier',
                         max_points=12, persona=PERSONA, canvas_size=CANVAS)
        b = shape_points(rng(9), (100, 100), (600, 500), option='bezier',
                         max_points=12, persona=PERSONA, canvas_size=CANVAS,
                         t_map=lambda u: u)
        assert a == b

    def test_endpoint_never_modified(self):
        """任何 t_map 下末项恒等于 end：终点不接受 1px 误差。"""
        from module.device.humanize.timing import time_param_map
        for profile in ('min_jerk', 'sigmoid', 'ease_out'):
            t_map = time_param_map(profile)
            points, _ = shape_points(rng(2), (50, 50), (1230, 690),
                                     option='s_curve', max_points=16,
                                     persona=PERSONA, canvas_size=CANVAS,
                                     t_map=t_map)
            assert points[-1] == (1230, 690)
