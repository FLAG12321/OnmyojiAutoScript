"""维度 B（按压时长）、I（动作间隔）、D（速度剖面）、E/H（停顿与末段）。

本模块只做"给定 RNG 与已选定的 option，算出秒数"。方案选择、档位允许集求交、
旁路判断全部归 facade（Plan 契约 12）——所以这里没有任何 option=None 的隐式
分支，也没有 enabled 参数。
"""
from __future__ import annotations

import math

import numpy as np

from module.device.humanize import HumanizeLevel
from module.device.humanize.persona import Persona
from module.device.humanize.plan import DwellPlan, Point, _SwipeTail

# ---------------------------------------------------------------- 维度 B 常量

# 人类按压时长的物理边界（Spec §5 B）。45ms 下界是本维度的核心价值：
# 今天 minitouch/scrcpy 的 0ms 按压和桌面 fast 的 10~25ms 都在这条线以下
PRESS_MIN_S = 0.045
PRESS_MAX_S = 0.600

# fast 只缩放中位数，不下移边界（Spec §5 B「fast 的语义」）。
# 结果是桌面 fast 点击从 10~25ms 升到约 45~85ms —— 有意的行为变更
PRESS_FAST_MEDIAN_SCALE = 0.65

# bimodal 的"分神"分支占比与区间（Spec §5 B）
PRESS_BIMODAL_DISTRACTED_P = 0.15
PRESS_BIMODAL_DISTRACTED_S = (0.250, 0.500)

PRESS_OPTIONS = ('lognormal', 'bimodal', 'gamma')

# ---------------------------------------------------------------- 维度 I 常量

GAP_SIGMA = 0.22
GAP_CLIP_FACTOR = (0.5, 2.2)
GAP_OPTIONS = ('fixed', 'jitter')

# ---------------------------------------------------------------- 全操作共享间隔常量

# 输入操作间最小间隔的上下限（秒）：人类在两个独立操作之间至少需要"反应 +
# 视线/手指移动"时间，0.5~1.5 覆盖从熟练到从容的区间（2026-08-28 应用户
# 要求从 0.3~1.0 抬高：0.3s 仍偏机器节奏）
INTER_CLICK_MIN_S = 0.5
INTER_CLICK_MAX_S = 1.5
# 自适应基准 base 每次操作的调整步长（秒）：小步长体现"慢慢变高/变短"的动态
# 平衡；区间从 0.3~1.0 扩到 0.5~1.5 后步长等比放大（保持约 5 步爬满区间）
INTER_CLICK_STEP_S = 0.2
# 原始间隔统计窗口大小：最近几次间隔的均值参与判定，平滑单次抖动
# （脚本偶发一次连击不应立刻把要求抬满）
INTER_CLICK_WINDOW = 5
# 本次目标间隔的抽样形态：右偏 lognormal（人类反应时间的典型形态——多数操作
# 偏快、偶尔拖沓），拒绝均匀分布的"有规律"指纹。中位数取 base 的
# MEDIAN_RATIO 倍，长尾被 base 截断、短尾被 MIN_S 托底
INTER_CLICK_SIGMA = 0.45
INTER_CLICK_MEDIAN_RATIO = 0.55

# ---------------------------------------------------------------- 同一资源重复点击退避常量

# 连续点击同一资源（控件名相同 / 坐标半径内）的退避标称序列（秒）：
# 第 2 次 2s、第 3 次 3s、第 4 次 4s、第 5 次 10s、第 6 次起封顶 16s
# （2026-08-28 应用户要求修订：前段缓升 2→3→4，随后跳 10、封顶 16——
# 比纯指数翻倍更贴近"先耐心重试、确认无响应后明显迟疑"的人类节奏）。
# 人类对"点了没反应"的目标会越来越迟疑地重试，机械等间隔连点是明显脚本指纹
REPEAT_BACKOFF_NOMINAL_S = (2.0, 3.0, 4.0, 10.0, 16.0)
# 同一资源判定半径（像素）：按钮级区域（720p 下常见按钮 100+px 宽）。
# 仅作无名点击的兜底——有控件名时按名判重（2026-08-28 修订）
REPEAT_BACKOFF_RADIUS_PX = 50
# 标称值上下的随机浮动区间：退避不取精确的标称值，在 [0.8×, 1.2×] 内随机
REPEAT_BACKOFF_JITTER = (0.8, 1.2)

# ---------------------------------------------------------------- 欠账模型常量

# 执行前等待上限（秒）。任务层的主流点击是 appear_then_click 模式：appear 基于
# 缓存截图决策、click 立即执行——决策与执行之间的画面有效期窗口原本几乎为零。
# 拟人等待若全额插在这里，执行时画面可能已经切换（弹窗过期、结算自动关闭），
# 点击会落在过期目标上（2026-08-27 实测：结算画面关闭后仍点在庭院功能区）。
# 因此执行前最多等这么久，剩余等待作为欠账在下一次截图入口偿还——截图才是
# 决策依据，等待发生在「截图→识别」之前既安全又保住节奏语义
EXECUTE_PACE_MAX_S = 0.3


def _require_finite_non_negative(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} 必须是数值，收到 {value!r}')
    if not math.isfinite(value):
        raise ValueError(f'{name} 必须有限，收到 {value!r}')
    if value < 0:
        raise ValueError(f'{name} 不能为负，收到 {value!r}')
    return float(value)


def press_seconds(
    rng: np.random.Generator,
    persona: Persona,
    *,
    option: str,
    fast: bool = False,
) -> float:
    """维度 B：按压时长（秒）。

    按压分布**不按协议类型分叉**——本函数不接收也不推断 touch 类型。桌面与触摸
    的差别只体现在调用方是否传 fast，而 fast 只缩放中位数。
    """
    if option not in PRESS_OPTIONS:
        raise ValueError(f'press_seconds: 未知 option {option!r}，可选 {PRESS_OPTIONS}')

    median_ms = persona.press_median * (PRESS_FAST_MEDIAN_SCALE if fast else 1.0)

    if option == 'lognormal':
        ms = rng.lognormal(math.log(median_ms), persona.press_sigma)
    elif option == 'bimodal':
        # 85% 走 lognormal，15% 走"分神"长按（Spec §5 B）
        if rng.random() < PRESS_BIMODAL_DISTRACTED_P:
            return float(np.clip(
                rng.uniform(*PRESS_BIMODAL_DISTRACTED_S), PRESS_MIN_S, PRESS_MAX_S))
        ms = rng.lognormal(math.log(median_ms), persona.press_sigma)
    else:  # gamma
        ms = rng.gamma(persona.press_shape, median_ms / persona.press_shape)

    return float(np.clip(ms / 1000.0, PRESS_MIN_S, PRESS_MAX_S))


def gap_seconds(
    rng: np.random.Generator,
    persona: Persona,
    default: float,
    *,
    option: str,
) -> float:
    """维度 I：把代码里的固定间隔常量换成同均值分布。

    "同均值"是本维度的承诺：零额外耗时，只打散"每次都恰好 50.0ms"这个指纹。
    default 为 0 时原样返回 0——给本来不存在等待的路径凭空加耗时是回归。
    """
    if option not in GAP_OPTIONS:
        raise ValueError(f'gap_seconds: 未知 option {option!r}，可选 {GAP_OPTIONS}')
    default = _require_finite_non_negative(default, 'gap_seconds.default')
    if option == 'fixed' or default == 0.0:
        return default

    lo, hi = GAP_CLIP_FACTOR
    # lognormal 的中位数是 exp(mu)，取 mu = ln(default) 让中位数落在原常量上；
    # sigma=0.22 时均值约为 default*1.025，配合对称裁剪后偏差在 1% 量级
    value = rng.lognormal(math.log(default), GAP_SIGMA)
    return float(np.clip(value, default * lo, default * hi))


def next_action_requirement(
    rng: np.random.Generator,
    recent_gaps,
    base_s: float,
    repeat_count: int,
) -> tuple[float, float]:
    """操作结束时计算**下一次**操作的间隔要求（预付制纯函数）。

    预付制（2026-08-27 二次修订）：等待全部发生在下一次**截图**之前
    （pace_view），操作执行前零等待——截图是 appear_then_click 决策的依据，
    等在「看」之前保证决策画面新鲜；动作一旦决定立即执行（反应慢、动作快，
    正是人类模型）。旧模型把等待插在执行前，实测导致决策-执行之间画面
    过期（接受邀请弹窗过期、结算画面关闭后误点庭院）。

    逻辑：
    - 窗口均值 < base（近期节奏偏快）→ base 抬高一步，强制间隔慢慢变高；
    - 窗口均值 >= base（近期节奏偏慢，含几秒级识别等待）→ base 回落一步；
    - 常规要求 target 按右偏 lognormal 抽样（中位数 = MEDIAN_RATIO×base，
      多数偏快、偶尔拖沓），截断在 [MIN, base]；
    - repeat_count >= 2（本次已是同一资源第 2+ 次连续点击，下次大概率还是
      它）：与退避查表取 max——下次（连续第 repeat_count+1 次）要求
      backoff(repeat_count+1)（3/4/10/16s...）。repeat_count == 1（首次点该
      资源）**不**预付退避：下次换目标的概率不低（结算画面交替点击不同
      奖励区域、点完接受弹窗进房间），全额预付 backoff(2) 会让每一次点击
      都白等 2s 起步——代价是第 2 次同资源点击的间隔只有常规量级
      （0.5~1.5s），从第 3 次起进入 3/4/10/16s 退避；
    - 预付的退避在下次实际换目标时成为白等——拟人上可解释（愣神/视线
      转移），且目标消失时识别自然失败、不会产生过期点击。

    窗口必须记**意图间隔**（自然节奏，不含机制注入的等待，调用方负责
    扣除），否则控制器会被自己制造的慢"欺骗"而放松，形成压-松振荡。

    Args:
        rng: 人格派生的随机源（persona-seeded）
        recent_gaps: 最近 INTER_CLICK_WINDOW 次意图间隔（秒）
        base_s: 当前的最小间隔基准（秒），必须在 [MIN, MAX] 内
        repeat_count: 本次操作的同一资源连续次数（1 = 首次点该资源）
    Returns:
        (require_s, new_base_s)：require 是对下一次操作的间隔要求，
        new_base 是调整后的基准。
    """
    base_s = _require_finite_non_negative(base_s, 'next_action_requirement.base_s')
    if not INTER_CLICK_MIN_S <= base_s <= INTER_CLICK_MAX_S:
        raise ValueError(
            f'next_action_requirement: base_s 必须在 [{INTER_CLICK_MIN_S}, '
            f'{INTER_CLICK_MAX_S}] 内，收到 {base_s}')
    gaps = [float(g) for g in recent_gaps]
    # 用窗口均值判定节奏方向：快了抬要求、慢了降要求，小步长动态平衡
    if gaps:
        mean_gap = sum(gaps) / len(gaps)
        if mean_gap < base_s:
            base_s = min(INTER_CLICK_MAX_S, base_s + INTER_CLICK_STEP_S)
        else:
            base_s = max(INTER_CLICK_MIN_S, base_s - INTER_CLICK_STEP_S)
    # 常规要求：右偏 lognormal（不是均匀分布——均匀在区间内等概率取值
    # 本身就是规律）；中位数 = MEDIAN_RATIO×base，长尾截断到 base、短尾托底 MIN
    require = float(rng.lognormal(
        math.log(base_s * INTER_CLICK_MEDIAN_RATIO), INTER_CLICK_SIGMA))
    require = min(max(require, INTER_CLICK_MIN_S), base_s)
    # 同一资源重复点击退避：只有已确认重复（第 2+ 次连续点击）才预付退避，
    # 首次点击（repeat_count==1）下次换目标概率不低，全额预付 backoff(2)
    # 会让每次点击都白等 2s 起步（正常任务换目标节奏被灾难性拖慢）
    if repeat_count >= 2:
        backoff = repeat_backoff_seconds(rng, repeat_count + 1)
        require = max(require, backoff)
    return require, base_s


def repeat_backoff_seconds(rng: np.random.Generator, count: int) -> float:
    """同一资源连续第 count 次点击的退避目标间隔（秒，纯函数）。

    count 是含首次点击的连续次数（控件名相同 / 坐标半径内的连续点击）：
    - count < 2（首次 / 非重复）：不退避，返回 0.0 且不消费 RNG；
    - count = 2/3/4/5/6+：标称 = 查表 (2, 3, 4, 10, 16)s，末档封顶 16s；
    - 在标称 × JITTER 区间内均匀随机浮动，避免精确的标称规律值。

    调用方把返回值与常规动态平衡要求取 max——退避是"点了没反应越来越迟疑"，
    常规要求是"两次独立操作的节奏"，两者独立成立。
    """
    if count < 2:
        return 0.0
    nominal = REPEAT_BACKOFF_NOMINAL_S[
        min(count - 2, len(REPEAT_BACKOFF_NOMINAL_S) - 1)]
    lo, hi = REPEAT_BACKOFF_JITTER
    return float(rng.uniform(nominal * lo, nominal * hi))


# ---------------------------------------------------------------- 维度 D 常量

# 速度下界，只用于防止 profile(τ)→0 时 distance/v 溢出；不是可调参数
SPEED_V_FLOOR = 1e-6

# Python 侧逐点 sleep 的可信 profile 门槛（Spec §5 D）。
# 只约束 Python backend：minitouch 的 `w` 由设备端执行，不受 Windows sleep 地板影响
PROFILE_MIN_DELAY_S = 0.005

# 与 windows_impl.py 的 DESKTOP_MOVE_MAX_POINTS 对齐
PROFILE_MAX_POINTS = 12

# 滑动（swipe）专属点数下限：恒定回报率下 count=budget×rate 可能低到 0~1，
# 至少 2 点才构成轨迹
SWIPE_MIN_POINTS = 2

# 恒定回报率模型（真实设备按固定采样率上报事件流）：触摸面板与鼠标的
# 真实回报率区间。分位数来自人格（设备回报率是硬件属性，同人格固定），
# 由 report_rate_hz() 映射到对应区间——不写死单一数值
TOUCH_REPORT_RATE_HZ = (60.0, 167.0)   # 触摸面板采样率区间（覆盖老款 60Hz 到主流 120Hz 量化档）
MOUSE_REPORT_RATE_HZ = (125.0, 1000.0)  # 鼠标回报率区间（125/250/500/1000 常见档）

# 点数硬上限：设备端单批 MOVE 命令量的安全上限（触摸 240Hz × 2s ≈ 480 点）
SWIPE_MAX_POINTS_CAP = 500

SPEED_OPTIONS = ('min_jerk', 'sigmoid', 'ease_out')


def report_rate_hz(quantile: float, *, mouse: bool = False) -> float:
    """把人格的回报率分位数映射到真实设备区间（触摸面板 / 鼠标）。

    设备回报率是离散硬件档位，但采样分位数映射到连续区间已足够——
    minitouch 的 w 量化到整毫秒后，有效回报率本来就是 1000/interval_ms
    的离散值，连续取值经量化自然落到可达档位。
    """
    if not 0.0 <= quantile <= 1.0:
        raise ValueError(f'report_rate_hz: quantile 必须在 [0,1]，收到 {quantile!r}')
    lo, hi = MOUSE_REPORT_RATE_HZ if mouse else TOUCH_REPORT_RATE_HZ
    return lo + (hi - lo) * float(quantile)

SPEED_OPTIONS = ('min_jerk', 'sigmoid', 'ease_out')

# legacy 点位上的相关抖动强度。刻意小：light 档的承诺是"近恒定"，
# 不是在 legacy 点距上叠加第二层速度编码
LEGACY_JITTER_SIGMA = 0.15

# 一阶 AR 系数。真人手的速度波动是低频的；每点独立白噪声反而是一种新指纹
_JITTER_PHI = 0.7

# profile 上的相关抖动，让剖面不是完美数学曲线
_PROFILE_JITTER_SIGMA = 0.06


def _correlated_jitter(rng: np.random.Generator, count: int, sigma: float) -> list[float]:
    """低频相关抖动（一阶 AR 过程），均值 0。

    禁止每点独立白噪声：真人手的速度波动是低频的，逐点白噪声本身就是可识别
    特征（复审 4.2 第 5 条）。乘 sqrt(1-phi²) 让稳态方差保持 sigma²。
    """
    out: list[float] = []
    prev = 0.0
    scale = math.sqrt(1.0 - _JITTER_PHI ** 2)
    for _ in range(count):
        prev = _JITTER_PHI * prev + float(rng.normal(0.0, sigma)) * scale
        out.append(prev)
    return out


def _speed(profile: str, tau: float) -> float:
    """profile 的速度值（相对量纲，只有比值有意义）。"""
    if profile == 'min_jerk':
        # 最小抖动位移 s(t)=10t³-15t⁴+6t⁵ 的导数：v(t)=30t²(1-t)²
        return 30.0 * tau ** 2 * (1.0 - tau) ** 2
    if profile == 'sigmoid':
        # logistic 位移对应的钟形速度，比 min_jerk 更"肩宽"
        return math.exp(-((tau - 0.5) / 0.25) ** 2 / 2.0)
    # ease_out：单调减速，只用于 overshoot 的修正段
    return (1.0 - tau) ** 2 + 0.05


# 等时间映射的密度地板：v 以峰值速度的 10% 为下限。min_jerk 两端 v→0，
# 若不设地板，1/v 的奇点会把几乎全部采样点塌缩到端点同一像素
# （SPEED_V_FLOOR=1e-6 只防除零，这里需要的是有意义的密度界）
T_PARAM_V_FLOOR_RATIO = 0.10

# 逆时间分布查表缓存：key 为 profile。v 加地板后严格为正 → F 严格递增，np.interp 可用
_T_PARAM_CACHE: dict[str, tuple[np.ndarray, np.ndarray]] = {}


def time_param_map(profile: str):
    """把等间隔 u∈[0,1] 映射到"等时间到达"的曲线参数 t，返回可调用映射。

    F(t) = ∫₀ᵗ dτ / max(v(τ), 峰值×10%) 是到达 t 所需时间的比例；返回
    u ↦ F⁻¹(u) 后，按 u 等间隔采样等价于按时间等间隔采样——点距正比局部
    速度，慢速区自然密集。真实输入设备按固定采样率上报事件：时间戳近似
    均匀、速度编码在位置增量里；与之配套的 delay 也应近恒定（设备采样间隔），
    不能再叠 profiled delay——点密度已编码速度，叠加会得到 v² 双重编码。
    """
    if profile not in SPEED_OPTIONS:
        raise ValueError(f'time_param_map: 未知 profile {profile!r}，可选 {SPEED_OPTIONS}')
    cached = _T_PARAM_CACHE.get(profile)
    if cached is None:
        steps = 256
        t_grid = np.linspace(0.0, 1.0, steps + 1)
        v_grid = np.array([_speed(profile, float(t)) for t in t_grid])
        # 密度地板相对峰值：保证两端最多 10 倍于中段的点密度
        v_grid = np.maximum(v_grid, T_PARAM_V_FLOOR_RATIO * float(v_grid.max()))
        # 累积时间比例 F(t) = ∫₀ᵗ dτ/v(τ)（梯形积分）。注意积分对象是 1/v：
        # 积分 v 得到的是弧长（等弧长采样=空间均匀，恰好与慢速区密集相反）
        inv_v = 1.0 / v_grid
        f_grid = np.concatenate(
            ([0.0], np.cumsum((inv_v[:-1] + inv_v[1:]) * 0.5 * np.diff(t_grid))))
        f_grid /= f_grid[-1]
        cached = (f_grid, t_grid)
        _T_PARAM_CACHE[profile] = cached
    f_grid, t_grid = cached

    def mapping(u: float) -> float:
        # u 裁剪进 [0,1]：F 网格只覆盖该区间，越界查询会外推
        clipped = min(max(float(u), 0.0), 1.0)
        return float(np.interp(clipped, f_grid, t_grid))

    return mapping


def segment_distances(start, points) -> list[float]:
    """start + points 的逐段欧氏距离。

    这是 profiled_move_delays 的正确输入：**真实段长**，不是 i/n。
    """
    out: list[float] = []
    prev = start
    for p in points:
        out.append(math.hypot(float(p[0] - prev[0]), float(p[1] - prev[1])))
        prev = p
    return out


def legacy_move_delays(
    rng: np.random.Generator,
    count: int,
    base_delay_s: float,
    total_budget_s: float | None = None,
) -> list[float]:
    """light 档用：在既有点位上生成近恒定间隔。

    **刻意不接受 points/distances**：legacy 的点距本身已编码速度（Spec §2.5），
    在其上再套 dt ∝ 1/v 会得到约 v² 的二次编码。本函数因此只知道"有几个点"，
    无法二次编码——这是用签名把错误变成不可能，而不是靠注释提醒。
    """
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ValueError(f'legacy_move_delays: count 必须是正整数，收到 {count!r}')
    base_delay_s = _require_finite_non_negative(base_delay_s, 'base_delay_s')
    if total_budget_s is not None:
        total_budget_s = _require_finite_non_negative(total_budget_s, 'total_budget_s')

    noise = _correlated_jitter(rng, count, LEGACY_JITTER_SIGMA)
    delays = [max(0.0, base_delay_s * (1.0 + n)) for n in noise]

    if total_budget_s is None:
        return delays
    s = sum(delays)
    if s <= 0:
        # base_delay_s 为 0 时均分预算，避免 0/0
        return [total_budget_s / count] * count
    return [d / s * total_budget_s for d in delays]


def profiled_move_delays(
    rng: np.random.Generator,
    distances: list[float],
    total_budget_s: float,
    profile: str,
) -> list[float]:
    """medium/heavy 用：按**真实段长**做时间参数化。

    τ 取每段的累计弧长中点比例，**不是 i/n**（Spec §5 D）。用索引参数化会在
    分段极不均匀时把长段误判成低速段，几何与时间就对不上了。

    不在本函数做弧长重采样：几何层负责点位，时间层负责真实段长，两层分离。
    """
    if profile not in SPEED_OPTIONS:
        raise ValueError(f'profiled_move_delays: 未知 profile {profile!r}，可选 {SPEED_OPTIONS}')
    total_budget_s = _require_finite_non_negative(total_budget_s, 'total_budget_s')
    n = len(distances)
    if n == 0:
        return []
    for i, d in enumerate(distances):
        _require_finite_non_negative(d, f'distances[{i}]')

    total = float(sum(distances))
    if total <= 0.0:
        # 零距离动作不得除零：预算为 0 返回全 0，否则均分。
        # 是否直接回退该动作由 facade 决定——时间层不做动作级判断
        return [0.0] * n if total_budget_s == 0.0 else [total_budget_s / n] * n

    cumulative = 0.0
    raw: list[float] = []
    for d in distances:
        tau = (cumulative + d / 2.0) / total
        v = max(_speed(profile, tau), SPEED_V_FLOOR)
        raw.append(d / v)
        cumulative += d

    # 相关抖动：让剖面不是完美数学曲线，同时保持低频特性
    noise = _correlated_jitter(rng, n, _PROFILE_JITTER_SIGMA)
    raw = [max(r * (1.0 + e), 0.0) for r, e in zip(raw, noise)]

    s = sum(raw)
    if s <= 0:
        return [total_budget_s / n] * n
    return [r / s * total_budget_s for r in raw]


# ---------------------------------------------------------------- 维度 E 常量

DWELL_CLIP_S = (0.020, 0.250)
DWELL_SIGMA_RATIO = 0.35          # σ = dwell_mu * 0.35（Spec §5 E）
HESITATE_RANGE_S = (0.300, 0.800)
SETTLE_SEGMENTS = (2, 3)
SETTLE_JITTER_PX = 2              # 段间位置更新幅度 ±1~2px
DWELL_OPTIONS = ('gauss', 'settle', 'hesitate')

# ---------------------------------------------------------------- 维度 H 常量

RANDOM_TAIL_COUNT = (2, 5)
RANDOM_TAIL_DELAY_S = (0.050, 0.130)
# light 的 natural 用主体 delay 中位数做基准，抖动系数刻意小（近恒定）
NATURAL_LIGHT_JITTER = 0.35
SWIPE_TAIL_OPTIONS = ('random_tail', 'natural')


def _dwell_gauss_seconds(rng: np.random.Generator, persona: Persona) -> float:
    """gauss 停顿时长，裁剪到 [20, 250] ms。"""
    ms = rng.normal(persona.dwell_mu, persona.dwell_mu * DWELL_SIGMA_RATIO)
    return float(np.clip(ms / 1000.0, *DWELL_CLIP_S))


def _clip_strategy_point(p: Point, canvas_size: tuple[int, int]) -> Point:
    """裁剪策略生成的附加点，不处理调用方传入的业务端点。"""
    return (
        min(max(p[0], 0), canvas_size[0] - 1),
        min(max(p[1], 0), canvas_size[1] - 1),
    )


def plan_dwell(
    rng: np.random.Generator,
    target: Point,
    persona: Persona,
    *,
    option: str,
    level: HumanizeLevel,
    canvas_size: tuple[int, int] = (1280, 720),
) -> DwellPlan:
    """维度 E：到位停顿。仅桌面指针语义消费（Spec §5 E）。

    DwellPlan 的语义写死为"先发点、再等待"，backend 不猜顺序。
    """
    if option not in DWELL_OPTIONS:
        raise ValueError(f'plan_dwell: 未知 option {option!r}，可选 {DWELL_OPTIONS}')

    if option == 'hesitate':
        # 长尾只属 heavy；medium 传进来也退化为 gauss，不靠调用方自律
        if level == 'heavy' and rng.random() < persona.hesitate_p:
            return DwellPlan(segments=((None, float(rng.uniform(*HESITATE_RANGE_S))),))
        return DwellPlan(segments=((None, _dwell_gauss_seconds(rng, persona)),))

    if option == 'gauss':
        return DwellPlan(segments=((None, _dwell_gauss_seconds(rng, persona)),))

    # settle：把 gauss 时长拆成 2~3 段，段间发 ±(1~2)px 的位置更新。
    # 真人手在"停住"时仍有微动，绝对静止本身就是特征
    total = _dwell_gauss_seconds(rng, persona)
    count = int(rng.integers(SETTLE_SEGMENTS[0], SETTLE_SEGMENTS[1] + 1))
    # Dirichlet 保证各段为正且和恰为 total，避免逐段减法的累计误差
    parts = sorted(float(p) for p in rng.dirichlet([3.0] * count) * total)
    # 浮点残差修正：Dirichlet 各段乘 total 后，逐段浮点求和可能比 total 偏 1~2 ulp，
    # total 恰好是 0.020/0.250 边界时 sum 会越界，settle 总时长因此误报出界。
    # 把最小的一段让给"最后一段 = total - 前几段之和"：其余各段和 ≥ total/2
    # （count≥2 恒成立），Sterbenz 引理保证该减法精确，最终 sum(segments) 精确等于 total
    head = parts[1:]
    last = total - float(sum(head))
    part_values = head + [last]
    segments: list[tuple[Point | None, float]] = []
    for i, sec in enumerate(part_values):
        if i == 0:
            # 第一段沿用当前落点，只等待
            segments.append((None, float(sec)))
            continue
        dx = int(rng.integers(-SETTLE_JITTER_PX, SETTLE_JITTER_PX + 1))
        dy = int(rng.integers(-SETTLE_JITTER_PX, SETTLE_JITTER_PX + 1))
        point = _clip_strategy_point((target[0] + dx, target[1] + dy), canvas_size)
        segments.append((point, float(sec)))
    return DwellPlan(segments=tuple(segments))


def swipe_tail(
    rng: np.random.Generator,
    base_delays: list[float],
    *,
    option: str,
    level: HumanizeLevel,
) -> _SwipeTail | None:
    """维度 H：滑动末段。返回 None 表示**不替换末段**。

    只在 facade 的 plan_swipe() 内调用，_SwipeTail 不出 facade。四个 backend
    各自实现"覆盖最后 N 个 delay"正是要避免的分歧来源。

    替换而非叠加：叠加会让预算失控（Spec §5 H）。
    """
    if option not in SWIPE_TAIL_OPTIONS:
        raise ValueError(f'swipe_tail: 未知 option {option!r}，可选 {SWIPE_TAIL_OPTIONS}')
    n = len(base_delays)
    if n == 0:
        return None

    if option == 'natural' and level in ('medium', 'heavy'):
        # medium/heavy 的 D 已给出自然减速，不需要也不应该再替换
        return None

    count = int(rng.integers(RANDOM_TAIL_COUNT[0], RANDOM_TAIL_COUNT[1] + 1))
    count = min(count, n)   # 点数不足时裁剪，不添加额外点

    if option == 'random_tail':
        delays = [float(rng.uniform(*RANDOM_TAIL_DELAY_S)) for _ in range(count)]
        return _SwipeTail(count=count, delays=tuple(delays))

    # option == 'natural' 且 level == 'light'：无可信 profile，用主体 delay 中位数
    # 做基准生成低频近恒定 jitter。目的是甩掉 legacy 的固定尾巴，而不是造一个新常量
    body = base_delays[:-count] or base_delays
    median = float(sorted(body)[len(body) // 2])
    noise = _correlated_jitter(rng, count, NATURAL_LIGHT_JITTER)
    delays = [max(0.0, median * (1.0 + e)) for e in noise]
    return _SwipeTail(count=count, delays=tuple(delays))
