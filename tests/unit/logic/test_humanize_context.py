# -*- coding: utf-8 -*-
"""HumanizerContext 门面与 ContextVar 绑定测试（Plan Task 11、Task 12）。

Task 11：门面 10 个方法（from_config + 9 个策略入口）的旁路 / 允许集 / 端点回退 /
H 合并 / 动态降点 / ContextVar 嵌套恢复。
Task 12：Device._ensure_humanizer_context 的五条初始化路径 + 配置字段，全部用
动态构造与调用证明，不以 inspect.getsource 作为唯一证据。
"""
import threading
import types

import numpy as np
import pytest

import module.device.humanize as hum
from module.device.humanize import (
    HumanizerContext,
    bind_humanizer,
    get_current_humanizer,
    set_current_humanizer,
)
from module.device.humanize import timing
from module.device.humanize.persona import Persona
from module.device.humanize.plan import MovePlan
from module.device.humanize.timing import PROFILE_MIN_DELAY_S, PROFILE_MAX_POINTS

from module.device.device import Device
from module.exception import EmulatorNotRunningError

pytestmark = pytest.mark.unit

SEED = 20260825
PERSONA = Persona.generate(SEED)
CANVAS = (1280, 720)


def rng(seed: int = 7) -> np.random.Generator:
    return np.random.Generator(np.random.PCG64(seed))


def make_context(level='heavy', *, persona=PERSONA, seed=7, canvas=CANVAS):
    """按档位构造门面；off 时 persona/rng 为 None，与 from_config 的 off 语义一致。"""
    return HumanizerContext(
        enabled=level != 'off',
        level=level,
        persona=None if level == 'off' else persona,
        rng=None if level == 'off' else rng(seed),
        canvas_size=canvas,
    )


@pytest.fixture(autouse=True)
def _isolate_humanizer_state():
    """清理模块级 ContextVar 与越界 warning 去重表，避免用例间串扰。"""
    hum._current_humanizer.set(None)
    hum._OOB_WARNED_TYPES.clear()
    yield
    hum._current_humanizer.set(None)
    hum._OOB_WARNED_TYPES.clear()


class TestFromConfig:
    """from_config 的档位解析与 off 零 I/O 契约。"""

    def test_off_level_produces_no_persona_no_rng(self):
        ctx = HumanizerContext.from_config(_config(level='off'))
        assert ctx.enabled is False
        assert ctx.persona is None
        assert ctx.rng is None
        assert ctx.level == 'off'

    def test_off_does_not_touch_persona_file_or_urandom(self, monkeypatch):
        # off 路径任何一步都不许碰人格文件 / os.urandom：把两者都炸掉来证明
        def boom(*a, **k):
            raise AssertionError('off 不得访问人格文件或 os.urandom')
        monkeypatch.setattr('module.device.humanize.persona.os.urandom', boom)
        monkeypatch.setattr('module.device.humanize.persona.PersonaStore', boom)
        ctx = HumanizerContext.from_config(_config(level='off'))
        assert ctx.enabled is False

    def test_unknown_level_rejected(self):
        with pytest.raises(ValueError, match='humanize_level'):
            HumanizerContext.from_config(_config(level='bogus'))

    def test_enabled_loads_persona(self, monkeypatch):
        fixed = Persona.generate(12345)

        class FakeStore:
            def __init__(self, config_name, base_dir='config/tasks_config'):
                self.config_name = config_name

            def load_or_create(self):
                return fixed

        monkeypatch.setattr('module.device.humanize.persona.PersonaStore', FakeStore)
        ctx = HumanizerContext.from_config(_config(level='light'))
        assert ctx.enabled is True
        assert ctx.level == 'light'
        assert ctx.persona is fixed
        assert ctx.rng is not None


class TestOffBypass:
    """off 档所有策略入口返回 None，且不消费 RNG。"""

    def test_off_entries_return_none_without_consuming_rng(self):
        r = rng(7)
        state_before = dict(r.bit_generator.state)
        ctx = HumanizerContext(enabled=False, level='off', persona=None, rng=r,
                               canvas_size=CANVAS)
        assert ctx.sample_point((0, 0, 100, 100)) is None
        assert ctx.press_seconds() is None
        assert ctx.plan_move((10, 10), (200, 200), gesture_kind='swipe', budget_ms=50) is None
        assert ctx.plan_swipe((10, 10), (200, 200), base_delay_s=0.01) is None
        assert ctx.plan_dwell((100, 100)) is None
        assert ctx.plan_pointer_tail((100, 100)) is None
        assert ctx.plan_touch_liftoff((100, 100)) is None
        assert ctx.gap_seconds(0.05) is None
        assert ctx.plan_idle(3.0, (100, 100)) is None
        # 零 RNG 消费：即使是带 rng 的 off 上下文，策略入口也不许碰它
        assert r.bit_generator.state == state_before


class TestChoose:
    """_choose 是唯一读取权重并挑选方案的地方，只允许集内挑选 + 归一化。"""

    def test_choose_filters_to_allowed_and_normalizes(self):
        ctx = make_context('heavy', seed=16)
        out = set()
        for _ in range(300):
            out.add(ctx._choose('shape', ('arc', 'jitter_line')))
        assert out <= {'arc', 'jitter_line'}
        # 两个方案权重都 > 0，300 次内都应出现
        assert 'arc' in out and 'jitter_line' in out

    def test_choose_empty_intersection_returns_none(self):
        ctx = make_context('heavy')
        assert ctx._choose('shape', ('no_such_option',)) is None

    def test_choose_reproducible_given_seed(self):
        a = make_context('heavy', seed=9)
        b = make_context('heavy', seed=9)
        for _ in range(50):
            assert a._choose('press', ('lognormal', 'bimodal', 'gamma')) == \
                   b._choose('press', ('lognormal', 'bimodal', 'gamma'))


class TestPlanMoveLight:
    """light：保留 legacy 点位、剥离与 start 相等的首点、近恒定间隔。"""

    def test_light_strips_start_and_keeps_order(self):
        ctx = make_context('light', seed=1)
        plan = ctx.plan_move((100, 100), (300, 300), gesture_kind='pointer_move',
                             legacy_points=[(100, 100), (200, 200), (300, 300)])
        assert plan is not None
        assert plan.points == ((200, 200), (300, 300))
        assert plan.points[0] != (100, 100)
        assert plan.points[-1] == (300, 300)
        assert len(plan.delays) == 2

    def test_light_budget_normalizes_delays(self):
        ctx = make_context('light', seed=2)
        plan = ctx.plan_move((100, 100), (600, 600), gesture_kind='swipe', budget_ms=20,
                             legacy_points=[(150, 150), (300, 300), (450, 450), (600, 600)])
        assert plan is not None
        assert sum(plan.delays) == pytest.approx(0.02)

    def test_light_no_legacy_points_returns_none(self):
        ctx = make_context('light')
        assert ctx.plan_move((0, 0), (10, 10), gesture_kind='swipe') is None

    def test_light_last_not_end_returns_none(self):
        ctx = make_context('light')
        assert ctx.plan_move((100, 100), (400, 400), gesture_kind='swipe',
                             legacy_points=[(150, 150), (200, 200)]) is None

    def test_light_empty_after_strip_returns_none(self):
        ctx = make_context('light')
        assert ctx.plan_move((100, 100), (100, 100), gesture_kind='swipe',
                             legacy_points=[(100, 100)]) is None


class TestPlanMoveProfiled:
    """medium/heavy：新几何 + 动态降点（契约 ① 的 6 步，非平均值公式）。"""

    def _force_speed(self, monkeypatch, speed='min_jerk'):
        orig = HumanizerContext._choose

        def forced(self, dim, allowed):
            if dim == 'speed':
                return speed
            return orig(self, dim, allowed)
        monkeypatch.setattr(HumanizerContext, '_choose', forced)

    def test_medium_sum_matches_budget_and_min_above_floor(self):
        ctx = make_context('medium', seed=4)
        plan = ctx.plan_move((100, 100), (700, 600), gesture_kind='swipe', budget_ms=80)
        assert plan is not None
        assert plan.points[-1] == (700, 600)
        assert sum(plan.delays) == pytest.approx(0.08)
        assert min(plan.delays) >= PROFILE_MIN_DELAY_S

    def test_medium_downscales_below_naive_formula(self, monkeypatch):
        """禁止平均值公式 int(total_budget / PROFILE_MIN_DELAY_S)（Task 6 契约 ①）。"""
        self._force_speed(monkeypatch)
        ctx = make_context('medium', seed=4)
        plan = ctx.plan_move((100, 100), (700, 600), gesture_kind='swipe', budget_ms=30)
        assert plan is not None
        naive = int(30 / (PROFILE_MIN_DELAY_S * 1000))
        assert naive == 6
        assert len(plan.points) < naive
        assert min(plan.delays) >= PROFILE_MIN_DELAY_S
        assert sum(plan.delays) == pytest.approx(0.03)

    def test_geometry_none_returns_none(self, monkeypatch):
        ctx = make_context('medium', seed=7)
        monkeypatch.setattr('module.device.humanize.geometry.shape_points',
                            lambda *a, **k: None)
        assert ctx.plan_move((100, 100), (500, 500), gesture_kind='swipe', budget_ms=80) is None

    def test_two_phase_extra_added_to_delay(self, monkeypatch):
        """two_phase 的停顿（点索引 → 秒）必须并入对应 delay，不能丢。"""
        self._force_speed(monkeypatch)
        ctx = make_context('medium', seed=6)

        def fake_shape(rng, start, end, *, option, max_points, persona, canvas_size, t_map=None):
            n = max_points
            pts = [(round(start[0] + (end[0] - start[0]) * (i + 1) / (n + 1)),
                    round(start[1] + (end[1] - start[1]) * (i + 1) / (n + 1)))
                   for i in range(n)]
            pts[-1] = end
            # 模拟 two_phase 的停顿：点索引 1 上额外 0.1s
            return pts, {1: 0.1} if n > 1 else {}

        monkeypatch.setattr('module.device.humanize.geometry.shape_points', fake_shape)
        plan = ctx.plan_move((100, 100), (700, 600), gesture_kind='drag', budget_ms=80)
        assert plan is not None
        # 预算 0.08 + 停顿 0.1 = 0.18；extra 必须真实累加到 delay 而非丢失
        assert sum(plan.delays) == pytest.approx(0.18, abs=0.005)


class TestPlanMoveAllowedSet:
    """gesture_kind 允许集过滤（Plan 契约 8）。"""

    def _spy_shape(self, monkeypatch):
        import module.device.humanize.geometry as geom
        opts = []
        real = geom.shape_points

        def spy(rng, start, end, *, option, max_points, persona, canvas_size, t_map=None):
            opts.append(option)
            return real(rng, start, end, option=option, max_points=max_points,
                        persona=persona, canvas_size=canvas_size, t_map=t_map)
        monkeypatch.setattr(geom, 'shape_points', spy)
        return opts

    def test_pointer_move_short_dist_bans_overshoot_without_safe_region(self, monkeypatch):
        # 距离门控（2026-08-26 调研吸收）：短距离（< CORRECTIVE_MIN_DIST_PX=200）
        # 且无 safe_region 时，overshoot/two_phase 都不得进入候选——小幅移动
        # 一次弹道即精确到位，没有纠正阶段
        ctx = make_context('medium', seed=13)
        opts = self._spy_shape(monkeypatch)
        for _ in range(40):
            ctx.plan_move((100, 100), (150, 130), gesture_kind='pointer_move',
                          budget_ms=80, safe_region=None)
        assert opts
        assert set(opts) <= {'bezier', 's_curve', 'jitter_line', 'arc'}
        assert 'two_phase' not in opts
        assert 'overshoot' not in opts

    def test_pointer_move_long_dist_allows_overshoot_without_safe_region(self, monkeypatch):
        # 长距离（≈640px ≥ 200）无 safe_region：overshoot 进入候选（ballistic+
        # corrective 子动作结构），_overshoot_track_fits 对 None 恒通过。
        # 权重 0.30，固定 seed 下 60 次采样应至少命中一次
        ctx = make_context('medium', seed=13)
        opts = self._spy_shape(monkeypatch)
        for _ in range(60):
            ctx.plan_move((100, 100), (600, 500), gesture_kind='pointer_move',
                          budget_ms=80, safe_region=None)
        assert set(opts) <= {'bezier', 's_curve', 'jitter_line', 'arc', 'overshoot'}
        assert 'overshoot' in opts
        assert 'two_phase' not in opts

    def test_swipe_bans_two_phase_and_overshoot(self, monkeypatch):
        ctx = make_context('medium', seed=14)
        opts = self._spy_shape(monkeypatch)
        for _ in range(40):
            ctx.plan_move((100, 100), (600, 500), gesture_kind='swipe', budget_ms=80)
        assert set(opts) <= {'bezier', 's_curve', 'arc'}

    def test_pointer_move_allows_overshoot_when_safe_region_present(self, monkeypatch):
        ctx = make_context('medium', seed=15)
        opts = self._spy_shape(monkeypatch)
        for _ in range(60):
            ctx.plan_move((100, 100), (600, 500), gesture_kind='pointer_move',
                          budget_ms=80, safe_region=(50, 50, 700, 550))
        assert set(opts) <= {'bezier', 's_curve', 'jitter_line', 'arc', 'overshoot'}
        # overshoot 权重 0.30，固定 seed 下 60 次采样应至少命中一次
        assert 'overshoot' in opts

    def test_pointer_move_overshoot_excluded_when_track_escapes_safe_region(self, monkeypatch):
        # 契约 8：safe_region 恰好含 end 但容不下过冲顶点时，overshoot 被剔除重选。
        # 正向用例的补充，防止 allow-set 断言空转。
        ctx = make_context('medium', seed=15)
        opts = self._spy_shape(monkeypatch)
        for _ in range(60):
            ctx.plan_move((100, 100), (600, 500), gesture_kind='pointer_move',
                          budget_ms=80, safe_region=(90, 90, 520, 420))
        assert set(opts) <= {'bezier', 's_curve', 'jitter_line', 'arc'}
        assert 'overshoot' not in opts

    def test_pointer_move_overshoot_only_terminal_checked(self, monkeypatch):
        # 契约 8 语义修订（2026-08-26）：safe_region 只校验终端段（过冲顶点+
        # 修正段），弹道主段允许越出——"先过冲再修正回控件内"是 ballistic+
        # corrective 子动作结构。safe_region 紧贴目标控件（主段必然越出）时
        # overshoot 仍应启用；旧"整条轨迹在界内"语义下该用例必然失败
        ctx = make_context('medium', seed=15)
        opts = self._spy_shape(monkeypatch)
        for _ in range(60):
            ctx.plan_move((100, 100), (600, 500), gesture_kind='pointer_move',
                          budget_ms=80, safe_region=(560, 460, 80, 80))
        assert 'overshoot' in opts

    def test_unknown_gesture_kind_rejected(self):
        ctx = make_context('medium')
        with pytest.raises(ValueError, match='gesture_kind'):
            ctx.plan_move((100, 100), (500, 500), gesture_kind='teleport', budget_ms=80)


class TestEndpointFallback:
    """端点越界：warn + 整体返回 None，绝不修改端点、不调用模块函数。"""

    def _spy_warnings(self, monkeypatch):
        warnings = []
        monkeypatch.setattr('module.device.humanize.logger.warning',
                            lambda msg: warnings.append(str(msg)))
        return warnings

    def test_plan_move_endpoint_oob(self, monkeypatch):
        warnings = self._spy_warnings(monkeypatch)
        ctx = make_context('medium')
        assert ctx.plan_move((0, -1), (100, 100), gesture_kind='swipe', budget_ms=50) is None
        assert ctx.plan_move((100, 100), (2000, 100), gesture_kind='swipe', budget_ms=50) is None
        assert warnings

    def test_plan_swipe_endpoint_oob(self, monkeypatch):
        warnings = self._spy_warnings(monkeypatch)
        ctx = make_context('medium')
        assert ctx.plan_swipe((100, 100), (100, 99999), base_delay_s=0.01) is None
        assert warnings

    def test_plan_dwell_oob_does_not_call_module(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError('plan_dwell 越界时门面不得调用模块函数')
        monkeypatch.setattr('module.device.humanize.timing.plan_dwell', boom)
        ctx = make_context('heavy', seed=17)
        assert ctx.plan_dwell((5000, 5000)) is None

    def test_plan_pointer_tail_oob_does_not_call_module(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError('plan_pointer_tail 越界时门面不得调用模块函数')
        monkeypatch.setattr('module.device.humanize.gesture.plan_pointer_tail', boom)
        ctx = make_context('heavy', seed=18)
        assert ctx.plan_pointer_tail((0, -5)) is None

    def test_plan_touch_liftoff_oob_does_not_call_module(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError('plan_touch_liftoff 越界时门面不得调用模块函数')
        monkeypatch.setattr('module.device.humanize.gesture.plan_touch_liftoff', boom)
        ctx = make_context('heavy', seed=19)
        assert ctx.plan_touch_liftoff((640, 36000)) is None

    def test_in_bounds_targets_still_work(self):
        ctx = make_context('heavy', seed=19)
        assert ctx.plan_dwell((640, 360)) is not None
        assert ctx.plan_pointer_tail((640, 360)) is not None


class TestPlanSwipe:
    """plan_swipe：light 保留点位 / device_wait 预算 / H 末段合并。"""

    def test_light_uses_legacy_delays_as_is(self, monkeypatch):
        ctx = make_context('light', seed=5)
        monkeypatch.setattr('module.device.humanize.timing.swipe_tail',
                            lambda rng, bd, *, option, level: None)
        plan = ctx.plan_swipe((100, 100), (400, 400),
                              legacy_points=[(200, 200), (300, 300), (400, 400)],
                              legacy_delays=[0.02, 0.03, 0.04], base_delay_s=0.01)
        assert plan is not None
        assert plan.points == ((200, 200), (300, 300), (400, 400))
        # legacy_delays 非空时逐项原样作为基础 delay（H 已被禁，这里是原样）
        assert plan.delays == (0.02, 0.03, 0.04)

    def test_light_strips_leading_start_with_same_index_delay(self, monkeypatch):
        """全局契约 4：MovePlan.points 恒不含起点；首点==start 时连同 delay 一起剥离。"""
        ctx = make_context('light', seed=5)
        monkeypatch.setattr('module.device.humanize.timing.swipe_tail',
                            lambda rng, bd, *, option, level: None)
        plan = ctx.plan_swipe((100, 100), (400, 400),
                              legacy_points=[(100, 100), (250, 250), (400, 400)],
                              legacy_delays=[0.01, 0.02, 0.03], base_delay_s=0.01)
        assert plan is not None
        assert plan.points == ((250, 250), (400, 400))
        assert plan.delays == (0.02, 0.03)
        assert plan.points[0] != (100, 100)

    def test_light_no_legacy_points_returns_none(self):
        ctx = make_context('light')
        assert ctx.plan_swipe((0, 0), (10, 10), base_delay_s=0.01) is None

    def test_light_last_not_end_returns_none(self):
        ctx = make_context('light')
        assert ctx.plan_swipe((100, 100), (400, 400), legacy_points=[(150, 150), (200, 200)],
                              legacy_delays=[0.01, 0.01]) is None

    def test_light_falls_back_to_legacy_move_delays(self, monkeypatch):
        ctx = make_context('light', seed=3)
        monkeypatch.setattr('module.device.humanize.timing.swipe_tail',
                            lambda rng, bd, *, option, level: None)
        plan = ctx.plan_swipe((100, 100), (400, 400),
                              legacy_points=[(200, 200), (300, 300), (400, 400)],
                              base_delay_s=0.01)
        assert plan is not None
        assert len(plan.delays) == 3
        assert all(d >= 0 for d in plan.delays)
        # 近恒定间隔，均值 ≈ base_delay_s
        assert sum(plan.delays) == pytest.approx(0.03, rel=0.3)

    def test_light_device_wait_profiled_by_real_segments(self, monkeypatch):
        """light + device_wait：保留点位、预算=sum(legacy_delays)、不启用 C 几何。"""
        ctx = make_context('light', seed=8)
        monkeypatch.setattr('module.device.humanize.timing.swipe_tail',
                            lambda rng, bd, *, option, level: None)
        spy_calls = []
        monkeypatch.setattr(
            'module.device.humanize.geometry.shape_points',
            lambda *a, **k: (spy_calls.append(1), None)[1])
        lp = [(200, 200), (300, 300), (400, 400), (500, 500)]
        ld = [0.01, 0.02, 0.03, 0.04]
        plan = ctx.plan_swipe((100, 100), (500, 500), timing_mode='device_wait',
                              legacy_points=lp, legacy_delays=ld)
        assert plan is not None
        assert plan.points == tuple(lp)
        assert len(plan.delays) == 4
        assert sum(plan.delays) == pytest.approx(sum(ld))
        assert not spy_calls  # device_wait 的 light 不调用几何

    def test_medium_generates_new_geometry(self, monkeypatch):
        ctx = make_context('medium', seed=10)
        monkeypatch.setattr('module.device.humanize.timing.swipe_tail',
                            lambda rng, bd, *, option, level: None)
        import module.device.humanize.geometry as geom
        opts = []
        real = geom.shape_points

        def spy(rng, start, end, *, option, max_points, persona, canvas_size, t_map=None):
            opts.append(option)
            return real(rng, start, end, option=option, max_points=max_points,
                        persona=persona, canvas_size=canvas_size, t_map=t_map)
        monkeypatch.setattr(geom, 'shape_points', spy)
        plan = ctx.plan_swipe((100, 100), (500, 500), base_delay_s=0.01)
        assert plan is not None
        assert opts
        assert set(opts) <= {'bezier', 's_curve', 'arc'}
        assert plan.points[-1] == (500, 500)
        assert min(plan.delays) >= PROFILE_MIN_DELAY_S
        # 恒定回报率：所有 delay 围绕 1/rate ± 1ms（真实设备固定采样率 + 调度抖动），
        # sum = 预算 ± 抖动带宽（budget = base_delay_s × PROFILE_MAX_POINTS）
        assert max(plan.delays) - min(plan.delays) <= 0.002 + 1e-9, \
            'delay 应为恒定回报率间隔 ±1ms 抖动'
        assert abs(sum(plan.delays) - 0.01 * PROFILE_MAX_POINTS) \
            <= len(plan.delays) * 0.001 + 0.001

    def test_rejects_invalid_timing_mode_immediately(self):
        # 非法 timing_mode 立即拒绝，off 档也不例外
        for ctx in (make_context('off'), make_context('heavy')):
            with pytest.raises(ValueError, match='timing_mode'):
                ctx.plan_swipe((0, 0), (10, 10), timing_mode='sleep_now')

    def test_h_not_applied_in_constant_rate_model(self, monkeypatch):
        """恒定回报率模型（medium/heavy profiled）不应用 H 替换：速度已编码进
        点密度，random_tail 的 0.05~0.13s 大 delay 会把末段事件间隔突增——USB/
        触摸上报的间隔方差本身就是指纹。末端迟疑由 light 路径的 H 与维度 F
        的 touch_liftoff 表达。"""
        called = []
        monkeypatch.setattr('module.device.humanize.timing.swipe_tail',
                            lambda rng, bd, *, option, level: called.append(1) or None)
        ctx = make_context('medium', seed=11)
        plan = ctx.plan_swipe((100, 100), (600, 500), base_delay_s=0.01)
        assert isinstance(plan, MovePlan)
        assert not called, 'medium/heavy 恒定回报率路径不得调用 swipe_tail'
        # 所有 delay 保持恒定回报率间隔 ±1ms（含末段）
        assert max(plan.delays) - min(plan.delays) <= 0.002 + 1e-9, \
            '末段不得被 H 的大 delay 替换'

    def test_h_none_keeps_base_delays(self, monkeypatch):
        ctx = make_context('medium', seed=12)
        monkeypatch.setattr('module.device.humanize.timing.swipe_tail',
                            lambda rng, bd, *, option, level: None)
        plan = ctx.plan_swipe((100, 100), (500, 500), base_delay_s=0.01)
        assert plan is not None
        # natural 在 medium/heavy 返回 None → 不替换，sum 仍在预算 ± 抖动带宽内
        assert abs(sum(plan.delays) - 0.01 * PROFILE_MAX_POINTS) \
            <= len(plan.delays) * 0.001 + 0.001


class TestContextVar:
    """ContextVar 绑定 API：嵌套恢复正确。"""

    def test_get_default_none(self):
        assert get_current_humanizer() is None

    def test_set_and_get_roundtrip(self):
        ctx = make_context('heavy')
        set_current_humanizer(ctx)
        assert get_current_humanizer() is ctx
        hum._current_humanizer.set(None)

    def test_bind_nesting_restores(self):
        a = make_context('heavy', seed=1)
        b = make_context('medium', seed=2)
        set_current_humanizer(a)
        try:
            assert get_current_humanizer() is a
            with bind_humanizer(b):
                assert get_current_humanizer() is b
                with bind_humanizer(a):
                    assert get_current_humanizer() is a
                assert get_current_humanizer() is b
            assert get_current_humanizer() is a
        finally:
            hum._current_humanizer.set(None)

    def test_bind_none(self):
        set_current_humanizer(make_context('heavy'))
        try:
            with bind_humanizer(None):
                assert get_current_humanizer() is None
        finally:
            hum._current_humanizer.set(None)


class TestSamplePointUsesExplicitPrev:
    """sample_point 只使用调用方显式传入的 prev，不保存历史。"""

    def test_prev_affects_result_and_not_stored(self, monkeypatch):
        # 固定点维方案为 prev_biased，才能保证 prev 一定参与采样
        orig = HumanizerContext._choose

        def force(self, dim, allowed):
            if dim == 'point':
                return 'prev_biased'
            return orig(self, dim, allowed)
        monkeypatch.setattr(HumanizerContext, '_choose', force)
        roi = (100, 100, 200, 150)
        c1 = make_context('heavy', seed=20)
        c2 = make_context('heavy', seed=20)
        with_prev1 = c1.sample_point(roi, prev=(200, 200))
        with_prev2 = c2.sample_point(roi, prev=(200, 200))
        # 同 seed 同 prev 可复现：证明 sample_point 不在 ContextVar/模块级保存跨调用历史
        assert with_prev1 == with_prev2
        c3 = make_context('heavy', seed=20)
        no_prev = c3.sample_point(roi, prev=None)
        # prev 是显式入参，缺省时退化为 center_gauss，结果不同
        assert no_prev != with_prev1


# ---------------------------------------------------------------- Task 12：Device 绑定

def _config(*, serial='127.0.0.1:5555', level='off',
            screenshot_method='adb', emulatorinfo_type='manual',
            package='com.netease.onmyoji.wyzymnqsd_cps'):
    return types.SimpleNamespace(
        config_name='test_humanize',
        script=types.SimpleNamespace(
            device=types.SimpleNamespace(
                serial=serial,
                humanize_level=level,
                screenshot_method=screenshot_method,
                emulatorinfo_type=emulatorinfo_type,
                package_name=types.SimpleNamespace(value=package),
            )
        )
    )


class _FakeHealth:
    def __init__(self, device):
        self.device = device

    def is_alive(self):
        return True

    def why_dead(self):
        return 'fake health'


class _FakeReset:
    def __init__(self, device):
        self.device = device


def _fake_full_recovery(self):
    self.recovered = True
    return True


def _apply_device_harness(monkeypatch, *, init_raises=False, desktop=False):
    """按现有 test_device_init_recovery.py 的模式，把 Device 初始化桩成可测路径。"""
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


class TestDeviceBinding:
    """Task 12：五条初始化路径全部动态构造并验证绑定先于 Rule.coord()。"""

    def test_path1_first_super_success_binds(self, monkeypatch):
        _apply_device_harness(monkeypatch)
        device = Device(_config())
        assert getattr(device, 'humanizer', None) is not None
        # 绑定已完成：此后任何 Rule.coord() 读取 get_current_humanizer 都能拿到
        assert get_current_humanizer() is device.humanizer
        assert device.humanizer.enabled is False  # 默认 off

    def test_path2_recovery_second_super_binds(self, monkeypatch):
        calls = _apply_device_harness(monkeypatch, init_raises=True)
        device = Device(_config())
        # 首次抛 EmulatorNotRunningError + 恢复后第二次 super 成功
        assert len(calls) == 2
        assert getattr(device, 'humanizer', None) is not None
        assert get_current_humanizer() is device.humanizer

    def test_path3_desktop_early_return_binds(self, monkeypatch):
        # 首次 super 就抛错，保证绑定只可能来自 desktop 分支的提前 return 前那次
        _apply_device_harness(monkeypatch, init_raises=True, desktop=True)
        device = Device(_config(serial='desktop'))
        assert getattr(device, 'humanizer', None) is not None
        assert get_current_humanizer() is device.humanizer

    def test_path4_same_thread_sequential_devices_rebind(self, monkeypatch):
        _apply_device_harness(monkeypatch)
        d1 = Device(_config(serial='127.0.0.1:1'))
        assert get_current_humanizer() is d1.humanizer
        d2 = Device(_config(serial='127.0.0.1:2'))
        # 后建 context 替换旧 context 是正确语义（Spec §4.2），不是 bug
        assert get_current_humanizer() is d2.humanizer
        hum._current_humanizer.set(None)

    def test_path5_two_threads_isolated_contexts(self, monkeypatch):
        _apply_device_harness(monkeypatch)
        seen = {}

        def worker(name, serial):
            d = Device(_config(serial=serial))
            seen[name] = (get_current_humanizer(), d.humanizer)

        t1 = threading.Thread(target=worker, args=('a', '127.0.0.1:1'))
        t2 = threading.Thread(target=worker, args=('b', '127.0.0.1:2'))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        ctx_a, dev_a = seen['a']
        ctx_b, dev_b = seen['b']
        # 线程内读到本线程构造的 context，两线程互不串扰
        assert ctx_a is dev_a
        assert ctx_b is dev_b
        assert ctx_a is not ctx_b

    def test_binding_available_before_rule_coord(self, monkeypatch):
        """coord() 前绑定：构造 Device 后 get_current_humanizer 立即可用（Task 12 验收）。"""
        _apply_device_harness(monkeypatch)
        # 用默认 off 档证明绑定本身（PersonaStore 的真实文件写入由
        # test_binding_with_real_persona 用 FakeStore 隔离，见 conftest 配置树守卫）
        device = Device(_config(level='off'))
        assert getattr(device, 'humanizer', None) is not None
        assert get_current_humanizer() is device.humanizer

    def test_binding_with_real_persona(self, monkeypatch):
        _apply_device_harness(monkeypatch)
        fixed = Persona.generate(999)

        class FakeStore:
            def __init__(self, config_name, base_dir='config/tasks_config'):
                self.config_name = config_name

            def load_or_create(self):
                return fixed

        monkeypatch.setattr('module.device.humanize.persona.PersonaStore', FakeStore)
        device = Device(_config(level='light'))
        assert device.humanizer.enabled is True
        assert device.humanizer.persona is fixed
        assert get_current_humanizer() is device.humanizer


class TestPlanHold:
    """维度 J（长按 hold 微颤事件流，2026-08-26 调研对标新增）。

    对标依据：平台长按识别器留 8~10px 移动容差（iOS allowableMovement /
    Android touch slop / Web ~10px），容差的存在证明真人按住期间手指持续微动；
    旧长按 hold 期间零事件是整秒级事件流死寂指纹。
    """

    def test_off_returns_none(self):
        assert make_context('off').plan_hold((640, 360), 1.0) is None

    def test_endpoint_oob_returns_none(self, monkeypatch):
        warnings = []
        monkeypatch.setattr('module.device.humanize.logger.warning',
                            lambda msg: warnings.append(str(msg)))
        ctx = make_context('medium')
        assert ctx.plan_hold((-1, 360), 1.0) is None
        assert ctx.plan_hold((640, 720), 1.0) is None
        assert warnings, '越界应记 warning'

    def test_invalid_duration_rejected(self):
        ctx = make_context('medium')
        with pytest.raises(ValueError):
            ctx.plan_hold((640, 360), -0.1)
        with pytest.raises(ValueError):
            ctx.plan_hold((640, 360), float('nan'))
        with pytest.raises(ValueError):
            ctx.plan_hold((640, 360), '1.0')

    def test_none_option_returns_none(self, monkeypatch):
        # 'none' 策略（约两成"按得很稳"的人类方差）→ 回退纯 sleep
        _force_hold_none(monkeypatch)
        ctx = make_context('medium')
        assert ctx.plan_hold((640, 360), 1.0) is None

    def test_budget_conserved(self, monkeypatch):
        # 预算守恒：sum(delays) ≈ duration（±1ms/点抖动带宽）——长按时长是业务
        # 参数，UP 不得提前
        _force_hold_tremor(monkeypatch)
        ctx = make_context('medium')
        for duration in (0.5, 1.0, 2.0):
            plan = ctx.plan_hold((640, 360), duration)
            assert plan is not None
            assert abs(sum(plan.delays) - duration) <= 0.001 * len(plan.delays) + 0.001
            assert plan.points[-1] != (640, 360) or True  # 末点不强制回 target

    def test_constant_rate_intervals(self, monkeypatch):
        # 恒定回报率：间隔围绕 1/rate ± 1ms（python_sleep clamp 200Hz），rate 按
        # 人格 report_rate_q 实际推导（人格间 100~200Hz 不等，不能钉死 200Hz）
        _force_hold_tremor(monkeypatch)
        ctx = make_context('medium')
        plan = ctx.plan_hold((640, 360), 1.0)
        assert plan is not None
        rate = timing.report_rate_hz(PERSONA.report_rate_q)
        rate = min(rate, 1.0 / PROFILE_MIN_DELAY_S)
        interval = 1.0 / rate
        for d in plan.delays:
            assert abs(d - interval) <= 0.001 + 1e-9, \
                f'间隔应围绕 {interval*1000:.2f}ms±1ms: {d}'
        # 1s × rate = 该人格的点数
        assert len(plan.points) == int(1.0 / interval)

    def test_point_cap_stretches_interval(self, monkeypatch):
        # 通道上限：点数钉 cap、间隔拉长为 预算/点数（预算守恒，不截断）
        _force_hold_tremor(monkeypatch)
        ctx = make_context('medium')
        plan = ctx.plan_hold((640, 360), 1.0, point_cap=50)
        assert plan is not None
        assert len(plan.points) == 50
        assert abs(sum(plan.delays) - 1.0) <= 0.001 * 50 + 0.001
        for d in plan.delays:
            assert abs(d - 0.020) <= 0.001 + 1e-9, \
                f'cap 命中时间隔应摊为 1/50=20ms±1ms: {d}'

    def test_device_wait_uses_integer_ms(self, monkeypatch):
        # device_wait：整毫秒档位，1s hold 下点数 = 1000/interval_ms
        _force_hold_tremor(monkeypatch)
        ctx = make_context('medium')
        plan = ctx.plan_hold((640, 360), 1.0, timing_mode='device_wait')
        assert plan is not None
        base = plan.delays[0]
        for d in plan.delays:
            assert abs(d - base) <= 0.002, f'device_wait 间隔应同档位 ±2ms: {d}'
        assert abs(sum(plan.delays) - 1.0) <= 0.001 * len(plan.delays) + 0.002

    def test_amplitude_far_below_platform_slop(self, monkeypatch):
        # 幅度安全：所有点距 target ≤ HOLD_JITTER_WALK_MAX=6px，
        # 远低于 iOS 10pt / Android 8dp 长按容差（不会取消长按）
        _force_hold_tremor(monkeypatch)
        ctx = make_context('medium')
        plan = ctx.plan_hold((640, 360), 1.0)
        for px, py in plan.points:
            assert abs(px - 640) <= 6 and abs(py - 360) <= 6

    def test_too_short_budget_returns_none(self, monkeypatch):
        # 预算短于一个回报率间隔：物理上放不下微颤事件，回退纯 sleep
        _force_hold_tremor(monkeypatch)
        ctx = make_context('medium')
        assert ctx.plan_hold((640, 360), 0.002) is None

    def test_zero_duration_returns_none(self, monkeypatch):
        # duration=0：count=0 → None → 回退纯 sleep（长按时长为零本身是业务
        # 异常，但策略层不该炸，应静默回退）
        _force_hold_tremor(monkeypatch)
        ctx = make_context('medium')
        assert ctx.plan_hold((640, 360), 0) is None

    def test_bool_duration_rejected(self, monkeypatch):
        # bool 是 int 子类：True 意味着 1s（合法数值）还是类型错误？设计
        # 决策是显式拒绝——1s 长按不会用 bool 表达，静默接受会掩盖调用方 bug
        _force_hold_tremor(monkeypatch)
        ctx = make_context('medium')
        with pytest.raises(ValueError):
            ctx.plan_hold((640, 360), True)
        with pytest.raises(ValueError):
            ctx.plan_hold((640, 360), False)


def _force_hold_option(monkeypatch, option):
    """把 hold 权重钉到单一 option，消除策略抽样的随机性。"""
    ctx_cls = HumanizerContext

    def _choose(self, dim, allowed):
        if dim == 'hold':
            assert option in allowed
            return option
        return _ORIG_CHOOSE(self, dim, allowed)

    monkeypatch.setattr(ctx_cls, '_choose', _choose)


_ORIG_CHOOSE = HumanizerContext._choose
_force_hold_tremor = lambda mp: _force_hold_option(mp, 'tremor')
_force_hold_none = lambda mp: _force_hold_option(mp, 'none')
