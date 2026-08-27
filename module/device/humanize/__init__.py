"""拟人化输入策略层。

档位枚举放在包的 __init__ 是刻意的：timing / geometry / gesture 都要按档位
分叉，而门面 HumanizerContext（Task 11）又要 import 这三个模块——把 HumanizeLevel
下沉到子模块会形成循环 import。

本文件追加 HumanizerContext 门面与 ContextVar 绑定 API（Plan Task 11）：
- 门面的方法名与模块函数同名是刻意的，但签名差别就是门面的职责——门面**不收
  option / rng / persona**（它自己持有），**可返回 None**（off 旁路契约）。
- `_choose()` 是唯一读取 persona.weights 并按允许集挑选方案的地方（Plan 契约 12），
  策略函数永远只接收已选定的 option。
- 模块级策略函数一律经由 `timing.xxx` / `geometry.xxx` / `gesture.xxx` 调用，
  避免门面方法被同名函数遮蔽（不能 `from module.device.humanize import press_seconds`）。
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal

from collections import deque
import math
import time

import numpy as np

from module.device.humanize.plan import DwellPlan, MovePlan, Point, TailPlan
from module.logger import logger

# 四档：off 全旁路，light/medium/heavy 逐级放开维度（Spec §7.2）。
# 必须先于子模块 import 定义：timing/gesture 都会 `from module.device.humanize
# import HumanizeLevel`，此时本模块若未定义该名字会触发循环 import 报错。
HumanizeLevel = Literal['off', 'light', 'medium', 'heavy']

LEVELS: tuple[str, ...] = ('off', 'light', 'medium', 'heavy')

# 门面对外类型别名（Spec §4.5）
GestureKind = Literal['pointer_move', 'swipe', 'drag', 'idle']
TimingMode = Literal['python_sleep', 'device_wait']
_TIMING_MODES = ('python_sleep', 'device_wait')

# 策略模块经 `from module.device.humanize import xxx` 引入：门面方法名与模块函数
# 同名，必须经由模块引用调用（如 timing.press_seconds），避免被同名方法遮蔽。
from module.device.humanize import geometry
from module.device.humanize import gesture
from module.device.humanize import persona
from module.device.humanize import plan
from module.device.humanize import timing
# 模块级别名：_overshoot_track_fits 的包含校验用原始函数对象（测试 monkeypatch
# geometry.shape_points 时不受影响），真实计划生成仍走 geometry.shape_points 属性
from module.device.humanize.geometry import shape_points as _geometry_shape_points

# light 档 plan_move 的近恒定延迟基准（秒）。桌面指针移动的预算 15~30ms 由 backend
# 通过 budget_ms 传入；本常量只在预算缺失时兜底（12 点约 60ms，属可接受回退）
_LIGHT_MOVE_BASE_DELAY_S = 0.005

# plan_swipe 的逐点基准 delay（秒）。minitouch 显式传 base_delay_s=0.010；
# 此处是预算缺失时的默认值，也是 medium/heavy 预算的推导基准（base × PROFILE_MAX_POINTS）
_SWIPE_BASE_DELAY_S = 0.010

# medium/heavy plan_move 未传 budget_ms 时的默认预算（秒）。桌面 C 档请求 40~120ms，
# 取中值 60ms 作回退
_MOVE_BUDGET_DEFAULT_S = 0.060

# 端点越界 warning 每进程每类型只记一次，避免刷屏（Spec §4.11）。类型 = 门面方法名。
_OOB_WARNED_TYPES: set[str] = set()

# ContextVar：让无 device 引用的 Rule 层读取当前 context（Plan 契约 2）。
# 每个 Device 持有一个独立 HumanizerContext；正式运行时不变量是"同一执行 Context
# 同时最多一个活动 Device"——ContextVar 提供隔离机制，但不自动证明该不变量。
_current_humanizer: ContextVar['HumanizerContext | None'] = ContextVar(
    'current_humanizer', default=None)


def set_current_humanizer(context: 'HumanizerContext') -> None:
    """把 context 绑到当前执行上下文（Device.__init__ 的 _ensure_humanizer_context 调用）。"""
    _current_humanizer.set(context)


def get_current_humanizer() -> 'HumanizerContext | None':
    """读取当前执行上下文绑定的 context；无绑定返回 None（Rule 层走原始均匀采样）。"""
    return _current_humanizer.get()


@contextmanager
def bind_humanizer(context: 'HumanizerContext | None'):
    """测试 / 明确嵌套覆盖用：进入时绑定、退出时用 token 恢复。

    生产代码用 set_current_humanizer，不通过 reset 掩盖错误绑定（Plan 契约 2）。
    """
    token = _current_humanizer.set(context)
    try:
        yield
    finally:
        _current_humanizer.reset(token)


class HumanizerContext:
    """每个 Device 一份的拟人化门面。

    enabled/off 时所有策略入口返回 None 且不消费 RNG；开档时内部持有
    persona + rng，按权重在允许集内挑选方案后委托给模块级策略函数。
    """

    enabled: bool
    level: HumanizeLevel
    persona: 'persona.Persona | None'
    rng: 'np.random.Generator | None'
    canvas_size: tuple[int, int]

    def __init__(
        self,
        *,
        enabled: bool,
        level: HumanizeLevel,
        persona: 'persona.Persona | None',
        rng: 'np.random.Generator | None',
        canvas_size: tuple[int, int] = (1280, 720),
    ) -> None:
        self.enabled = enabled
        self.level = level
        self.persona = persona
        self.rng = rng
        self.canvas_size = canvas_size
        # 全操作共享间隔（2026-08-27 新增）状态：click/long_click/swipe/drag
        # 共用同一份 CD。这是门面首个有状态维度——窗口与基准跟随 Device 生命周期。
        # 预付制：操作结束时计算下一次操作的间隔要求（_pending_require），
        # 由下一次截图入口（pace_view）等满——等待全部发生在「看」之前，
        # 动作一旦决定立即执行（反应慢、动作快，人类模型）
        self._gap_last_ts: float | None = None
        self._gap_window: deque = deque(maxlen=timing.INTER_CLICK_WINDOW)
        self._gap_base: float = timing.INTER_CLICK_MIN_S
        self._pending_require: float = 0.0
        # 自上次操作以来机制注入的等待总量：record 时从间隔里扣除得到
        # 「意图节奏」，防止控制器被自己制造的慢"欺骗"而压-松振荡
        self._mech_wait: float = 0.0
        # 同一资源重复点击的指数退避状态：判定键优先用点击控件名（不同按钮
        # 即便相邻也不会误判；同名模板即同一资源），无名点击退回坐标半径兜底
        self._repeat_point: 'tuple[int, int] | None' = None
        self._repeat_name: str | None = None
        self._repeat_count: int = 0

    # ---------------------------------------------------------------- 构造

    @classmethod
    def from_config(
        cls,
        config,
        *,
        canvas_size: tuple[int, int] = (1280, 720),
    ) -> 'HumanizerContext':
        """按配置档位构造门面。

        off 时不加载/生成人格、不访问人格文件、不调用 os.urandom（零回归）——
        这既保证 off 路径零 I/O，也让默认档位的初始化不依赖 persona 存储。
        """
        level = getattr(config.script.device, 'humanize_level', 'off')
        if level not in LEVELS:
            raise ValueError(f'未知 humanize_level {level!r}，可选 {LEVELS}')
        if level == 'off':
            return cls(enabled=False, level='off', persona=None, rng=None,
                       canvas_size=canvas_size)
        config_name = getattr(config, 'config_name', None) or 'default'
        p = persona.PersonaStore(config_name).load_or_create()
        # 同一人格固定 seed 派生 RNG：同一个"人"的重启行为可复现
        rng = np.random.Generator(np.random.PCG64(p.seed))
        return cls(enabled=True, level=level, persona=p, rng=rng,
                   canvas_size=canvas_size)

    # ---------------------------------------------------------------- 内部

    def _endpoint_ok(self, p) -> bool:
        """端点必须在画布闭区间内；越界走整体回退（Spec §4.11），绝不修改端点。"""
        w, h = self.canvas_size
        return (
            isinstance(p, (tuple, list)) and len(p) == 2
            and 0 <= p[0] <= w - 1 and 0 <= p[1] <= h - 1
        )

    def _warn_endpoint_oob(self, method: str, start, end) -> None:
        """端点越界 warning，每进程每类型只记一次（Spec §4.11 防刷屏）。"""
        if method in _OOB_WARNED_TYPES:
            return
        _OOB_WARNED_TYPES.add(method)
        logger.warning(
            f'拟人化 {method} 跳过：端点越界 start={start} end={end} canvas={self.canvas_size}')

    def _choose(self, dim: str, allowed) -> str | None:
        """唯一读取 persona.weights 并按允许集抽一个方案的地方（Plan 契约 12）。

        在 allowed ∩ 权重表内归一化后抽样；交集为空时返回 None（调用方整体回退）。
        按权重表自身的 key 顺序迭代，保证同 seed 结果可复现。
        """
        weights = self.persona.weights[dim]
        allowed_set = set(allowed)
        total = 0.0
        for key in weights:
            if key in allowed_set:
                total += weights[key]
        if total <= 0:
            return None
        r = self.rng.random() * total
        acc = 0.0
        for key in weights:
            if key in allowed_set:
                acc += weights[key]
                if r < acc:
                    return key
        return None

    def _choose_shape_option(self, gesture_kind: str, safe_region, exclude=None,
                             dist: float | None = None) -> str | None:
        """按 gesture_kind 允许集过滤形状方案（Plan 契约 8）。

        pointer_move 永禁 two_phase（停顿会叠加在预算之上，破坏 §4.7 请求值守恒，
        启用需要独立的预算交互设计）；overshoot 满足其一即进入候选：
        - 显式 safe_region（调用方给出控件边界，终端段校验可执行）；
        - 距离门控（2026-08-26 调研吸收）：dist ≥ CORRECTIVE_MIN_DIST_PX 的
          长距离移动——真人长距离弹道常冲过目标再修正（ballistic+corrective
          子动作结构），越界风险由几何层 _clip_control 画布裁剪兜底。
        exclude 用于 overshoot 包含校验失败后剔除该候选重选。
        """
        if gesture_kind == 'pointer_move':
            allowed = {'bezier', 's_curve', 'jitter_line', 'arc'}
            if safe_region is not None or (
                    dist is not None and dist >= geometry.CORRECTIVE_MIN_DIST_PX):
                allowed.add('overshoot')
        elif gesture_kind == 'swipe':
            # two_phase / overshoot 对普通滑动默认禁用
            allowed = {'bezier', 's_curve', 'arc'}
        elif gesture_kind == 'drag':
            # 契约 8：two_phase 需要"容差足够"判据，API 暂无容差参数，默认不进入候选
            allowed = {'bezier', 's_curve', 'arc'}
        elif gesture_kind == 'idle':
            allowed = {'jitter_line'}
        else:
            raise ValueError(f'plan_move: 未知 gesture_kind {gesture_kind!r}')
        if exclude:
            allowed = allowed - set(exclude)
        return self._choose('shape', allowed)

    def _geometry_seed(self) -> int:
        # 固定几何 seed：派生自人格 seed，同一人格的几何可复现，且不消费父 RNG（契约 6）
        return self.persona.seed + 0x5EED

    def _overshoot_track_fits(self, start: Point, end: Point, safe_region) -> bool:
        """契约 8（2026-08-26 语义修订）：overshoot 的**终端段**必须落在 safe_region 内。

        终端段 = 过冲顶点（主段末点）+ 修正段（2~3 点，rng.integers 半开区间），即返回
        points 的末 n_correct+1 ≤ 4 个点；弹道主段允许越出 safe_region——真人弹道本来就会
        扫过目标区域外，"先过冲再修正回控件内"正是 ballistic+corrective 子动作
        结构。safe_region 为 None 时恒通过（距离门控在 _choose_shape_option
        已放行，画布内越界由几何层 _clip_control 兜底）。用固定 geometry seed
        生成（不消费父 RNG），与 _downscale 的同 seed 生成保持一致。走模块级
        别名以绕过测试对 geometry.shape_points 的 monkeypatch——这里是校验
        不是真实计划生成。
        """
        if safe_region is None:
            return True
        g = np.random.Generator(np.random.PCG64(self._geometry_seed()))
        result = _geometry_shape_points(
            g, start, end, option='overshoot', max_points=timing.PROFILE_MAX_POINTS,
            persona=self.persona, canvas_size=self.canvas_size)
        if result is None:
            return False
        points, _extra = result
        sx, sy, sw, sh = safe_region
        # 顶点 = 距 start 最远的点（过冲主段末点），终端段 = 顶点起的修正段；
        # 修正段点数随机（2~4），用最远点定位对点数稳健，不硬编码窗口长度
        apex_idx = max(range(len(points)),
                       key=lambda i: (points[i][0] - start[0]) ** 2 + (points[i][1] - start[1]) ** 2)
        return all(sx <= px <= sx + sw and sy <= py <= sy + sh
                   for px, py in points[apex_idx:])

    def _downscale(
        self,
        start: Point,
        end: Point,
        shape_option: str,
        speed_option: str,
        budget_s: float,
        timing_mode: str,
        max_points: int | None = None,
        t_map=None,
        interval_s: float | None = None,
    ):
        """先生成点、算真实段长、生成 delay、按实际 delay 验证，失败后降点。

        逐字实现 Task 6「下游契约 ①」的 6 步，**禁止**平均值公式
        int(total_budget / PROFILE_MIN_DELAY_S)：
        1. 固定 geometry_seed 生成点（不重复消耗父 RNG，每次尝试可复现）；
        2. distances = segment_distances(start, points)；
        3. delays = profiled_move_delays(rng_for_timing, distances, T, profile)；
        4. python_sleep：min(delays) < PROFILE_MIN_DELAY_S 则降点重试；
        5. device_wait：不检查 Python 地板，改做整数毫秒可表示性检查
           （目标总毫秒 < 正 delay 数则降点）；
        6. count=2 仍失败时退化为 legacy_move_delays 的近恒定 delays，
           并显式记录未启用可信 profile。

        两个滑动加密扩展（等时间采样）：
        - max_points：点数上限，默认 PROFILE_MAX_POINTS；swipe 由恒定回报率
          模型（预算 × 回报率）计算后传入；
        - t_map + interval_s：等时间映射与恒定回报率间隔。传入时速度编码进
          点密度（慢速区密集），delays = 1/rate ± 1ms 调度抖动——真实 USB/
          触摸上报的间隔围绕采样周期波动而非完美恒定；抖动零均值、不归一化
          （保留自然方差）。不传 t_map 走原 profiled delay 路径。

        返回 (points, delays, extra) 或 None（端点越界，几何生成失败）。
        """
        geometry_seed = self._geometry_seed()
        cap = timing.PROFILE_MAX_POINTS if max_points is None else max_points
        points: list[Point] | None = None
        extra: dict[int, float] = {}
        delays: list[float] | None = None
        profile_untrusted = False
        for count in range(cap, 1, -1):
            g = np.random.Generator(np.random.PCG64(geometry_seed))
            result = geometry.shape_points(
                g, start, end, option=shape_option, max_points=count,
                persona=self.persona, canvas_size=self.canvas_size, t_map=t_map)
            if result is None:
                # 几何层端点越界（理论上 facade 已提前拦截），整体回退
                return None
            points, extra = result
            if t_map is None:
                distances = timing.segment_distances(start, points)
                delays = timing.profiled_move_delays(self.rng, distances, budget_s, speed_option)
            else:
                # 恒定回报率 + 调度抖动：每点间隔 = 1/rate ± 1ms（零均值，不归一化）。
                # python_sleep 的下限是可信门槛地板；device_wait 下限 1ms（w 的量化粒度）
                floor = (timing.PROFILE_MIN_DELAY_S if timing_mode == 'python_sleep'
                         else 0.001)
                delays = [
                    max(interval_s + float(self.rng.uniform(-0.001, 0.001)), floor)
                    for _ in range(len(points))
                ]
            if timing_mode == 'python_sleep':
                if min(delays) >= timing.PROFILE_MIN_DELAY_S:
                    break
            else:  # device_wait：整数毫秒可表示性检查（契约 #6 step 5）
                # 目标总毫秒 = floor(sum * 1000 + 0.5)，与 Task 16 的量化口径一致
                total_ms = int(sum(delays) * 1000 + 0.5)
                positive = sum(1 for d in delays if d > 0)
                if total_ms >= positive:
                    break
        else:
            # count=2 仍失败：退化近恒定 delay，并显式记录未启用可信 profile
            profile_untrusted = True
        if points is None:
            return None
        if profile_untrusted:
            delays = timing.legacy_move_delays(
                self.rng, len(points), _LIGHT_MOVE_BASE_DELAY_S, total_budget_s=budget_s)
            logger.warning('拟人化 profile 未达可信门槛（含降点后），退化为近恒定 delay')
        return points, delays, extra

    def _apply_swipe_tail(self, start: Point, end: Point, points, base_delays):
        """维度 H 在 facade 内合并末段：替换而非叠加，合并后 total_seconds 即为真值。

        _SwipeTail 只在门面内部出现，绝不向 backend 暴露（Plan 契约 9）。
        """
        tail_option = self._choose('swipe_tail', timing.SWIPE_TAIL_OPTIONS)
        tail = timing.swipe_tail(self.rng, base_delays, option=tail_option, level=self.level)
        if tail is not None:
            base_delays = list(base_delays)
            base_delays[-tail.count:] = list(tail.delays)
        return plan.MovePlan(points=tuple(points), delays=tuple(base_delays))

    # ---------------------------------------------------------------- 公开方法

    def sample_point(self, roi, prev: Point | None = None) -> Point | None:
        """维度 A 落点。只使用调用方显式传入的 prev，不在 ContextVar/模块级保存历史。"""
        if not self.enabled:
            return None
        option = self._choose('point', geometry.POINT_OPTIONS)
        return geometry.sample_point(self.rng, roi, self.persona, option=option, prev=prev)

    def press_seconds(self, *, fast: bool = False) -> float | None:
        """维度 B 按压时长（秒）。"""
        if not self.enabled:
            return None
        option = self._choose('press', timing.PRESS_OPTIONS)
        return timing.press_seconds(self.rng, self.persona, option=option, fast=fast)

    def plan_move(
        self,
        start: Point,
        end: Point,
        *,
        gesture_kind: str,
        budget_ms: float | None = None,
        safe_region=None,
        legacy_points: list[Point] | None = None,
    ) -> MovePlan | None:
        """一次移动/点击前定位的完整计划；失败（off/越界/无候选/几何失败）返回 None。"""
        if not self.enabled:
            return None
        if not self._endpoint_ok(start) or not self._endpoint_ok(end):
            self._warn_endpoint_oob('plan_move', start, end)
            return None
        if self.level == 'light':
            return self._plan_move_light(start, end, budget_ms, legacy_points)
        return self._plan_move_profiled(start, end, gesture_kind, budget_ms, safe_region)

    def _plan_move_light(self, start, end, budget_ms, legacy_points):
        """light：保留 legacy 点位，只剥离与 start 相等的首点，用近恒定间隔补时。

        MovePlan.points 恒不含起点（全局契约 4）；剥离后为空、末项不等于 end 或
        没有 legacy_points 时返回 None。
        """
        if not legacy_points:
            return None
        points = list(legacy_points)
        if points and points[0] == start:
            points.pop(0)
        if not points or points[-1] != end:
            return None
        budget = None if budget_ms is None else budget_ms / 1000.0
        delays = timing.legacy_move_delays(
            self.rng, len(points), _LIGHT_MOVE_BASE_DELAY_S, total_budget_s=budget)
        return plan.MovePlan(points=tuple(points), delays=tuple(delays))

    def _plan_move_profiled(self, start, end, gesture_kind, budget_ms, safe_region):
        """medium/heavy：新二维几何 + 真实段长 profile + 动态降点，two_phase 停顿并入 delay。"""
        # 距离门控：长距离指针移动才允许纠正性子动作（overshoot）进入候选
        dist = math.hypot(float(end[0] - start[0]), float(end[1] - start[1]))
        shape_option = self._choose_shape_option(gesture_kind, safe_region, dist=dist)
        if shape_option is None:
            return None
        if shape_option == 'overshoot' and not self._overshoot_track_fits(start, end, safe_region):
            # 契约 8：终端段必须落在 safe_region 内才启用；越界时剔除候选重选
            shape_option = self._choose_shape_option(
                gesture_kind, safe_region, exclude={'overshoot'}, dist=dist)
            if shape_option is None:
                return None
        speed_option = self._choose('speed', timing.SPEED_OPTIONS)
        budget_s = (budget_ms / 1000.0) if budget_ms is not None else _MOVE_BUDGET_DEFAULT_S
        result = self._downscale(start, end, shape_option, speed_option, budget_s,
                                 timing_mode='python_sleep')
        if result is None:
            return None
        points, delays, extra = result
        # two_phase 的停顿（点索引 → 秒）加到对应 delay 上再构造 MovePlan
        for idx, sec in extra.items():
            delays[idx] += sec
        return plan.MovePlan(points=tuple(points), delays=tuple(delays))

    def plan_swipe(
        self,
        start: Point,
        end: Point,
        *,
        base_delay_s: float | None = None,
        timing_mode: TimingMode = 'python_sleep',
        safe_region=None,
        legacy_points: list[Point] | None = None,
        legacy_delays: list[float] | None = None,
        mouse: bool = False,
        point_cap: int | None = None,
    ) -> MovePlan | None:
        """一次滑动的完整计划。

        timing_mode 只允许 python_sleep（默认，逐点 Python sleep）与 device_wait
        （仅 minitouch 使用）；非法值立即拒绝，不静默回退。
        mouse=True 用鼠标回报率区间（桌面窗口拖拽语义），默认触摸面板区间。
        point_cap 是调用方的通道上限（如 u2 逐点 HTTP RPC 不能承载高回报率）。
        """
        if timing_mode not in _TIMING_MODES:
            raise ValueError(
                f'plan_swipe: 未知 timing_mode {timing_mode!r}，可选 {_TIMING_MODES}')
        if not self.enabled:
            return None
        if not self._endpoint_ok(start) or not self._endpoint_ok(end):
            self._warn_endpoint_oob('plan_swipe', start, end)
            return None
        if self.level == 'light':
            return self._plan_swipe_light(
                start, end, base_delay_s, timing_mode, legacy_points, legacy_delays)
        return self._plan_swipe_profiled(
            start, end, base_delay_s, timing_mode, safe_region, mouse, point_cap)

    def _plan_swipe_light(self, start, end, base_delay_s, timing_mode, legacy_points, legacy_delays):
        """light：保留 legacy 点位，不启用 C 几何。

        python_sleep 下 legacy_delays 逐项原样作为基础 delay（只允许 H 替换末段），
        否则按 base_delay_s 生成近恒定间隔；device_wait 下按真实段长做设备端
        profile（预算 = sum(legacy_delays) 或 base_delay_s * len(points)）。
        """
        if not legacy_points:
            return None
        points = list(legacy_points)
        delays = list(legacy_delays) if legacy_delays is not None else None
        # 全局契约 4：MovePlan.points 恒不含起点。若 backend 传入的首点仍是 start，
        # 连同其同索引 delay 一起剥离（backend 通常已剥离，这里是结构性兜底）
        if points and points[0] == start:
            points.pop(0)
            if delays:
                delays.pop(0)
        if not points or points[-1] != end:
            return None

        if timing_mode == 'python_sleep':
            if delays is not None:
                base_delays = delays
            else:
                base = base_delay_s if base_delay_s is not None else _SWIPE_BASE_DELAY_S
                base_delays = timing.legacy_move_delays(self.rng, len(points), base)
        else:  # device_wait：minitouch 专用，不启用 C 几何
            if delays is not None:
                budget = float(sum(delays))
            else:
                base = base_delay_s if base_delay_s is not None else _SWIPE_BASE_DELAY_S
                budget = base * len(points)
            speed_option = self._choose('speed', timing.SPEED_OPTIONS)
            distances = timing.segment_distances(start, points)
            base_delays = timing.profiled_move_delays(
                self.rng, distances, budget, speed_option)
            # 整数毫秒可表示性检查（契约 #6 step 5）：legacy 点数固定不可降，退化为近恒定
            total_ms = int(sum(base_delays) * 1000 + 0.5)
            positive = sum(1 for d in base_delays if d > 0)
            if total_ms < positive:
                base = base_delay_s if base_delay_s is not None else _SWIPE_BASE_DELAY_S
                base_delays = timing.legacy_move_delays(
                    self.rng, len(points), base, total_budget_s=budget)
                logger.warning(
                    '拟人化 light+device_wait 预算不足 1ms/点（目标 %d ms / %d 正 delay）：'
                    'legacy 点数固定不可降，近恒定退化仍无法满足 minitouch 整数毫秒量化'
                    '（契约 #6 step 5/6）；维持近恒定 delay，交由 Task 16 的量化与 §11 校准兜底',
                    total_ms, positive)
        return self._apply_swipe_tail(start, end, points, base_delays)

    def _plan_swipe_profiled(self, start, end, base_delay_s, timing_mode, safe_region,
                             mouse=False, point_cap=None):
        """medium/heavy：恒定回报率设备仿真——间隔严格相等的点流。

        真实输入设备按固定采样率（回报率）上报事件：每个事件的时间间隔相同、
        速度编码在位置增量里。回报率来自人格分位数映射到真实设备区间（触摸
        面板 100~240Hz / 鼠标 125~1000Hz），同人格固定；点数 = 预算 × 回报率
        （floor），位置由路径弧长按速度剖面分布（t_map 等时间采样）。
        预算 = base_delay_s × PROFILE_MAX_POINTS（调用方把总时长换算成 base 传入）。

        通道适配：device_wait（minitouch w 由设备端执行）用整毫秒间隔，回报率
        直接可达；python_sleep 受 Windows sleep 地板限制，回报率 clamp 到
        1/PROFILE_MIN_DELAY_S（200Hz），且调用方可传 point_cap 表达通道上限
        （u2 逐点 HTTP RPC）。
        """
        shape_option = self._choose_shape_option('swipe', safe_region)
        if shape_option is None:
            return None
        speed_option = self._choose('speed', timing.SPEED_OPTIONS)
        base = base_delay_s if base_delay_s is not None else _SWIPE_BASE_DELAY_S
        budget_s = base * timing.PROFILE_MAX_POINTS
        rate = timing.report_rate_hz(self.persona.report_rate_q, mouse=mouse)
        if timing_mode == 'device_wait':
            # 整毫秒间隔：w 只能整 ms，恒定回报率下全部取同一整数值，
            # 有效回报率 = 1000/interval_ms 的离散档位（与真实硬件档位同构）
            interval_ms = max(1, int(1000.0 / rate + 0.5))
            interval_s = interval_ms / 1000.0
            count = int(budget_s * 1000.0 + 0.5) // interval_ms
        else:
            # python_sleep：Windows sleep 精度 ~2.9ms、可信门槛 5ms，
            # 高于此的回报率物理上投递不出来，clamp 到门槛
            rate = min(rate, 1.0 / timing.PROFILE_MIN_DELAY_S)
            interval_s = 1.0 / rate
            count = int(budget_s / interval_s)
        cap = timing.SWIPE_MAX_POINTS_CAP if point_cap is None else point_cap
        count = max(timing.SWIPE_MIN_POINTS, min(count, cap))
        t_map = timing.time_param_map(speed_option)
        result = self._downscale(start, end, shape_option, speed_option, budget_s, timing_mode,
                                 max_points=count, t_map=t_map, interval_s=interval_s)
        if result is None:
            return None
        points, delays, extra = result
        for idx, sec in extra.items():
            delays[idx] += sec
        # H（滑动末段迟疑）在恒定回报率模型下不再替换末段 delay：速度已编码进
        # 点密度（末段减速 = 末段点距变小、间隔恒定），random_tail 的大 delay
        # 会让事件间隔突增到 50~130ms——USB/触摸上报不会这样，间隔方差本身就是
        # 指纹。"到达后停顿再抬起"由维度 F 的 touch_liftoff 表达（UP 前小步
        # 微位移 + 恒定间隔）。light 档保留 H：legacy 点距均匀，末段加大 delay
        # 是真实的末端迟疑（见 _plan_swipe_light 的 _apply_swipe_tail）
        return plan.MovePlan(points=tuple(points), delays=tuple(delays))

    def plan_dwell(self, target: Point) -> DwellPlan | None:
        """维度 E 到位停顿。target 作为业务端点先校验，越界返回 None 且不调用模块函数。"""
        if not self.enabled:
            return None
        if not self._endpoint_ok(target):
            self._warn_endpoint_oob('plan_dwell', target, None)
            return None
        option = self._choose('dwell', timing.DWELL_OPTIONS)
        return timing.plan_dwell(
            self.rng, target, self.persona, option=option, level=self.level,
            canvas_size=self.canvas_size)

    def plan_pointer_tail(self, target: Point) -> TailPlan | None:
        """维度 F（指针语义）UP 后漂移。target 同样按业务端点校验。"""
        if not self.enabled:
            return None
        if not self._endpoint_ok(target):
            self._warn_endpoint_oob('plan_pointer_tail', target, None)
            return None
        option = self._choose('pointer_tail', gesture.POINTER_TAIL_OPTIONS)
        return gesture.plan_pointer_tail(
            self.rng, target, self.persona, option=option, level=self.level,
            canvas_size=self.canvas_size)

    def plan_touch_liftoff(self, target: Point) -> TailPlan | None:
        """维度 F（触摸语义）UP 前微位移。None 可能是 off 旁路或策略 none（20% 人类方差）。"""
        if not self.enabled:
            return None
        if not self._endpoint_ok(target):
            self._warn_endpoint_oob('plan_touch_liftoff', target, None)
            return None
        option = self._choose('touch_liftoff', gesture.TOUCH_LIFTOFF_OPTIONS)
        return gesture.plan_touch_liftoff(
            self.rng, target, self.persona, option=option, level=self.level,
            canvas_size=self.canvas_size)

    def plan_hold(
        self,
        target: Point,
        duration_s: float,
        *,
        timing_mode: TimingMode = 'python_sleep',
        mouse: bool = False,
        point_cap: int | None = None,
    ) -> MovePlan | None:
        """维度 J：长按 hold 期间的微颤事件流（2026-08-26 调研对标新增）。

        平台长按识别器留 8~10px 移动容差（iOS allowableMovement / Android
        touch slop）正是因为真人按住期间手指持续微动；旧长按 hold 期间零事件
        是整秒级的事件流死寂指纹。本方法把死寂替换为恒定回报率的微颤
        MOVE 流：间隔 = 1/回报率 ± 1ms 调度抖动（与 swipe 的恒定回报率模型
        同构），位置是围绕 target 的 ±1~3px 随机游走（远离平台容差，
        不会取消长按）。duration_s 是业务时长（预算），点数 = 预算 × 回报率。
        通道适配语义与 plan_swipe 完全一致：device_wait 整毫秒档位、
        python_sleep clamp 200Hz、point_cap 表达通道上限。预算守恒精度：
        通道上限命中时 sum(delays) 精确等于 duration（间隔摊为 预算/点数）；
        未命中时 count 向下取整，sum(delays) = duration − (duration mod 间隔)
        ——UP 最多提前一个回报率间隔（4~10ms），对秒级长按可忽略。

        返回 None 表示本次不做微颤（off 或 'none' 策略，约 20% 人类方差：
        真人偶发的"按得很稳"），调用方回退纯 sleep。
        """
        if not self.enabled:
            return None
        if not self._endpoint_ok(target):
            self._warn_endpoint_oob('plan_hold', target, None)
            return None
        if isinstance(duration_s, bool) or not isinstance(duration_s, (int, float)):
            raise ValueError(f'plan_hold: duration_s 必须是数值，收到 {duration_s!r}')
        if not math.isfinite(duration_s) or duration_s < 0:
            raise ValueError(f'plan_hold: duration_s 必须是有限非负数，收到 {duration_s}')
        option = self._choose('hold', gesture.HOLD_OPTIONS)
        if option == 'none':
            return None
        # 恒定回报率：与 _plan_swipe_profiled 相同的通道适配推导
        rate = timing.report_rate_hz(self.persona.report_rate_q, mouse=mouse)
        if timing_mode == 'device_wait':
            interval_ms = max(1, int(1000.0 / rate + 0.5))
            interval_s = interval_ms / 1000.0
            count = int(duration_s * 1000.0 + 0.5) // interval_ms
        else:
            rate = min(rate, 1.0 / timing.PROFILE_MIN_DELAY_S)
            interval_s = 1.0 / rate
            count = int(duration_s / interval_s)
        cap = timing.SWIPE_MAX_POINTS_CAP if point_cap is None else point_cap
        if count > cap:
            # 通道上限命中：点数钉在 cap、间隔拉长为 预算/点数——有效回报率
            # 降为 cap/预算，但 sum(delays) 恒等于 duration_s（长按时长是业务
            # 参数，UP 不得提前；截断点数会让 hold 短掉 (1-cap/natural)×预算）
            count = cap
            interval_s = duration_s / count
        if count <= 0:
            # 预算短于一个回报率间隔：物理上放不下任何微颤事件，回退纯 sleep
            return None
        points = gesture.plan_hold_jitter(
            self.rng, target, count, canvas_size=self.canvas_size)
        if not points:
            return None
        # 间隔抖动与 _downscale 的恒定回报率分支同式：±1ms 零均值不归一化
        floor = (timing.PROFILE_MIN_DELAY_S if timing_mode == 'python_sleep'
                 else 0.001)
        delays = [
            max(interval_s + float(self.rng.uniform(-0.001, 0.001)), floor)
            for _ in range(len(points))
        ]
        return plan.MovePlan(points=tuple(points), delays=tuple(delays))

    def gap_seconds(self, default: float) -> float | None:
        """维度 I 动作间隔：把固定常量换成同均值抖动。

        维度 I 没有独立权重维（DEFAULT_WEIGHTS 无 'gap'），恒走 jitter 打散常量
        指纹；'fixed' 等价于 off 的原值，开档没必要再选它。
        """
        if not self.enabled:
            return None
        return timing.gap_seconds(self.rng, self.persona, default, option='jitter')

    def pace_view(self) -> float:
        """预付制等待的主消费点：在下一次**截图**之前等满操作间隔要求。

        由 Device.screenshot 入口调用。截图是 appear_then_click 等决策模式
        的依据——等待发生在「看」之前保证决策画面新鲜：目标仍在 → 动作
        立即执行（执行前零等待）；目标已消失（弹窗过期、结算画面自动关闭）
        → 识别自然失败、不会产生按旧目标点击的过期点击。这是 2026-08-27
        修复两个线上现象（接受邀请过期没进房、结算关闭后误点庭院）的核心：
        旧模型把等待插在「决策→执行」之间，执行时画面早已切换。

        Returns:
            本次等待的秒数（off / 无要求 / 已自然满足返回 0）。
        """
        if not self.enabled:
            return 0.0
        if self._gap_last_ts is None or self._pending_require <= 0:
            return 0.0
        elapsed = time.time() - self._gap_last_ts
        wait = self._pending_require - elapsed
        if wait <= 0:
            self._pending_require = 0.0
            return 0.0
        self._pending_require = 0.0
        time.sleep(wait)
        # 机制等待记账：record 时从间隔里扣除，窗口只统计意图节奏
        self._mech_wait += wait
        return wait

    def pace_execute(self) -> float:
        """执行前兜底等待：仅覆盖「无截图背靠背操作」的罕见场景。

        正常流程的操作间隔要求已由 pace_view（截图入口）等满，这里恒为 0。
        只有两次操作之间没有任何截图时（如 multi_click 循环），要求未被
        消费才在这里补——封顶 EXECUTE_PACE_MAX_S（保护决策-执行的画面
        有效期窗口），剩余要求留给下一次 pace_view / pace_execute。
        """
        if not self.enabled:
            return 0.0
        if self._gap_last_ts is None or self._pending_require <= 0:
            return 0.0
        elapsed = time.time() - self._gap_last_ts
        wait = min(max(0.0, self._pending_require - elapsed),
                   timing.EXECUTE_PACE_MAX_S)
        if wait > 0:
            time.sleep(wait)
            self._mech_wait += wait
        # 扣除已流逝/已等待的部分，剩余要求留给后续消费点
        self._pending_require = max(
            0.0, self._pending_require - (elapsed + wait))
        return wait

    def record_action(self, target=None, name=None) -> None:
        """操作结束打点：更新节奏统计 + 同一资源重复判定 + 计算下次要求。

        由 Control 在每次输入操作（click/long_click/swipe/drag）完成后调用。
        - 意图间隔 = (本次操作时刻 - 上次操作结束时刻) - 期间机制等待：
          窗口只统计任务层的自然节奏，防止控制器被自己制造的慢"欺骗"；
        - 同一资源判定（优先级从高到低）：
          ① name（点击控件名，如 GB_DE_WIN）：同名模板即同一资源——不同按钮
            即便坐标相邻（结算画面的多个奖励区域）也不会误判；
          ② target 坐标半径（REPEAT_BACKOFF_RADIUS_PX）：无名点击（直接
            device.click 未传控件名）的兜底；
          ③ 都没有（swipe/drag）：重置计数。
          判定命中 → 连续计数 +1，下次要求并入指数退避（连续第 2/3/4/5+
          次 2/4/8/16s 封顶）；换资源重新从 1 计；
        - 下次要求由 timing.next_action_requirement 计算（动态平衡基准 +
          右偏 lognormal 常规要求 + 退避取 max），挂起待 pace_view 消费。

        off 档无副作用。
        """
        if not self.enabled:
            return
        now = time.time()
        if self._gap_last_ts is not None:
            # 意图间隔：扣除机制注入的等待（pace_view/pace_execute 的 sleep），
            # 窗口记录的是任务层想多快，而不是被我们拖慢后的实际节奏
            intent = max(0.0, (now - self._gap_last_ts) - self._mech_wait)
            self._gap_window.append(intent)
        self._gap_last_ts = now
        self._mech_wait = 0.0
        # 同一资源判定：控件名优先，坐标半径兜底，swipe/drag 重置
        if name is not None and name not in ('Click', 'LongClick', 'SWIPE', 'DRAG'):
            # 有真实控件名的点击按名判重（泛称视同无名，走坐标兜底）
            if self._repeat_name == name:
                self._repeat_count += 1
            else:
                self._repeat_count = 1
            self._repeat_name = name
            self._repeat_point = None  # 名称判定后坐标兜底不再参与
        elif target is not None:
            if (self._repeat_point is not None
                    and math.hypot(target[0] - self._repeat_point[0],
                                   target[1] - self._repeat_point[1])
                    <= timing.REPEAT_BACKOFF_RADIUS_PX):
                self._repeat_count += 1
            else:
                self._repeat_count = 1
            self._repeat_point = (int(target[0]), int(target[1]))
            self._repeat_name = None
        else:
            self._repeat_count = 0
            self._repeat_name = None
            self._repeat_point = None
        self._pending_require, self._gap_base = timing.next_action_requirement(
            self.rng, self._gap_window, self._gap_base, self._repeat_count)

    def plan_idle(self, since_last_s: float, cursor: Point | None) -> MovePlan | None:
        """维度 G 点击间空闲。cursor 未知或未达阈值时返回 None（策略层语义）。"""
        if not self.enabled:
            return None
        if cursor is not None and not self._endpoint_ok(cursor):
            # cursor 是既有业务状态而非策略新增点，越界时必须整体回退，不能静默裁剪。
            self._warn_endpoint_oob('plan_idle', cursor, None)
            return None
        option = self._choose('idle', gesture.IDLE_OPTIONS)
        return gesture.plan_idle(
            self.rng, since_last_s, cursor, self.persona, option=option,
            level=self.level, canvas_size=self.canvas_size)


__all__ = [
    'HumanizeLevel',
    'LEVELS',
    'GestureKind',
    'TimingMode',
    'HumanizerContext',
    '_current_humanizer',
    'set_current_humanizer',
    'get_current_humanizer',
    'bind_humanizer',
]
