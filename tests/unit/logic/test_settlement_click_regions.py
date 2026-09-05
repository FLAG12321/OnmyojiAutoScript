# -*- coding: utf-8 -*-
"""战斗结算落点安全区域：全屏候选挖掉常驻禁点区域与检测出的奖励行。

背景：结算阶段原先是「固定区域」点击——胜利画面固定右侧 C_WIN_3，
奖励页在 [C_REWARD_1, C_REWARD_3] 里随机二选一，其中右侧竖条 y 201~515
横穿三行奖励框，正是误点奖励的来源。

现行行为（2026-09-05 起）：reward_click_actions() 返回「全屏挖掉禁点区域后
的安全子区域」，胜利画面与奖励页共用；禁点区域 = 任务预设的常驻禁点矩形
（默认：顶部整条 + 左下角；突破/探索：顶左条 + 顶右条 + 左下角）
+ 检测出的奖励行（首行到末行整块，含行间间隙）。落点按「面积×密度场」
加权挑选，块内由密度场两轴加权采样。

结算点击（战斗结束胜利画面 + 领取奖励）的连点与落点参数由真人实采数据校准
（1053 次点击 / 451 个簇 / 33.8 分钟，见 log/click_monitor 的采集与分析脚本）：
- 连点次数按真人簇长直方图抽样，**在 4 点封顶**：首击点掉奖励页后剩余追加击
  会落到新界面上，长簇会误触；封顶保留 88.9% 的真人簇（含多点簇峰值 4 点），
  最长暴露窗口 2.20s → 0.66s；
- 追加击间隔 150~220ms（真人簇内中位 181ms），总时长受 MULTI_CLICK_MAX_S 预算
  约束，超预算立即收尾而不补完剩余次数；
- 追加击默认复用首击坐标（真人簇内 86% 落在同一像素），仅 14% 概率移动，
  移动量取真人非零位移量级而非持续微抖；
- 同一场战斗内落点互相参考（奖励页参考胜利画面那一次）：真人场次内相邻
  点击事件 31.4% 完全同坐标、43.8% 在 30px 内；跨场次（>10s）与自由取点
  不可区分，故复用带 TTL 自动失效；
- 复用坐标被新出现的奖励行覆盖时保持 x 沿 y 下移到安全区域。
device.click 对追加击传 pace=False 绕过操作节奏 CD。

本文件验证：
- 实例级：默认 reward_click_actions() 恰好挖掉默认预设的两块禁区；
- 行为级：连点概率分布、间隔范围、簇长封顶与时长预算、坐标复用比例、
  追加击绕过节奏、场次内复用与 TTL 失效、被覆盖时的向下平移；
- 源码契约级：各任务私有副本（battle_wait 复制体）不再出现旧的
  上/左随机列表与固定 C_WIN_3 落点，结算点全部走 settlement_click 连点入口。
"""
import math
import statistics
import pytest
from types import SimpleNamespace

from tasks.Component.GeneralBattle.general_battle import GeneralBattle

# 旧的胜利画面随机三选一必须全部移除（组件本体 + 各任务私有副本）
WIN_FILES = [
    'tasks/Component/GeneralBattle/general_battle.py',
    'tasks/Orochi/script_task.py',
    'tasks/FallenSun/script_task.py',
    'tasks/MasterDisciple/script_task.py',
    'tasks/Plotline/script_task.py',
]

# 旧的奖励页随机列表不得包含左侧区域（C_REWARD_2）
REWARD_FILES = [
    'tasks/Component/GeneralBattle/general_battle.py',
    'tasks/Orochi/script_task.py',
    'tasks/FallenSun/script_task.py',
    'tasks/BondlingFairyland/battle.py',
]

# 覆盖 reward_forbidden 的任务必须返回突破预设（顶左条 + 顶右条 + 左下角）
KEKKAI_PRESET_FILES = [
    'tasks/RealmRaid/script_task.py',
    'tasks/RyouToppa/script_task.py',
    'tasks/Exploration/base.py',
    'tasks/Plotline/script_task.py',
]

# 结算连点接线文件：胜利画面 + 领取奖励的结算点都走 settlement_click/gesture
MULTI_CLICK_FILES = WIN_FILES + [
    'tasks/BondlingFairyland/battle.py',
]


def _src(path: str) -> str:
    with open(path, encoding='utf-8') as f:
        return f.read()


@pytest.mark.unit
def test_reward_click_actions_default_regions():
    """默认 reward_click_actions() 返回全屏挖掉默认预设（顶部整条 + 左下角）后的安全区域。

    image=None 模拟胜利画面（无奖励行），禁点只剩默认预设。
    分块为右下优先贪心极大矩形：先取锚在右下的最大连续块，再收左上余料。
    """
    b = object.__new__(GeneralBattle)
    b.device = SimpleNamespace(image=None)
    rois = [r.roi_front for r in b.reward_click_actions()]
    assert rois == [(112, 171, 1168, 549), (0, 171, 112, 344)]


@pytest.mark.unit
def test_win_click_regions_right_only():
    """胜利画面不得再出现上/左区域的随机选择列表与固定右侧落点。"""
    for path in WIN_FILES:
        src = _src(path)
        assert 'random.choice([self.C_WIN_1' not in src, \
            f'{path} 胜利画面仍存在上/左随机区域'
        assert 'action_click = self.C_WIN_3' not in src, \
            f'{path} 胜利画面仍固定右侧区域'


@pytest.mark.unit
def test_reward_click_regions_exclude_left():
    """奖励页随机列表不得包含左侧区域（C_REWARD_2）。"""
    for path in REWARD_FILES:
        assert 'self.C_REWARD_1, self.C_REWARD_2' not in _src(path), \
            f'{path} 奖励页随机列表仍包含左侧区域'


@pytest.mark.unit
def test_kekkai_tasks_use_kekkai_preset():
    """结界突破/寮突破/探索/Plotline探索分支使用突破预设的常驻禁点区域。"""
    for path in KEKKAI_PRESET_FILES:
        assert 'FORBIDDEN_KEKKAI' in _src(path), \
            f'{path} 未使用 FORBIDDEN_KEKKAI 预设'


@pytest.mark.unit
def test_settlement_sites_use_multi_click():
    """战斗结束/领取奖励的结算点全部走 settlement_click/settlement_gesture 连点入口。

    连点只允许这两个场景；旧 appear_then_click(action=...) 形式不得残留。
    Plotline 的 click_dialogue_high（剧情对话加速）是明确保留的旧式分支，不在本契约内。
    """
    for path in MULTI_CLICK_FILES:
        src = _src(path)
        assert 'settlement_click(' in src or 'settlement_gesture(' in src, \
            f'{path} 结算点未走连点入口'
        if path == 'tasks/Plotline/script_task.py':
            continue  # click_dialogue_high 剧情分支保留旧式，见上
        assert 'action=action_click' not in src, \
            f'{path} 结算点仍走普通 appear_then_click（未连点）'


@pytest.mark.unit
def test_human_rect_weight_preferences():
    """密度场形态：峰值在热区中心；同距离下右>左、下>上；远角趋近 0。"""
    from tasks.Component.GeneralBattle.reward_frame import (
        field_density, human_rect_weight, HOT_X, HOT_Y_BASE, HOT_H,
        HOT_Y_PEAK_RATIO, HOT_Y_UP_RATIO, HOT_Y_DOWN_RATIO)
    cx = HOT_X[0] + HOT_X[1] / 2          # x 轴峰值
    # 可用高度取热区带高与「锚点到屏幕底」的较小值，与 _gauss_y 保持一致
    strip = min(HOT_H, 720 - HOT_Y_BASE)
    cy = HOT_Y_BASE + HOT_Y_PEAK_RATIO * strip   # y 轴峰值（按百分比分配）
    # 峰值处密度为 1.0（split-normal 单峰，无平台）
    assert field_density(cx, cy) == 1.0
    # 热区左上角（双侧快衰减）应明显低于峰值
    assert field_density(HOT_X[0], HOT_Y_BASE) < 0.6
    # 各向异性：距峰值同距离时，右侧衰减慢于左侧、下方衰减慢于上方
    assert field_density(cx + 200, cy) > field_density(cx - 200, cy)
    assert field_density(cx, cy + 60) > field_density(cx, cy - 60)
    # 远离热区的左上角密度应趋近 0
    assert field_density(10, 10) < 0.1
    # 覆盖峰值的矩形密度均值显著高于远离热区的矩形
    core = (HOT_X[0], cy - HOT_Y_UP_RATIO * strip,
            HOT_X[1], (HOT_Y_UP_RATIO + HOT_Y_DOWN_RATIO) * strip)
    assert human_rect_weight(*core) > 2 * human_rect_weight(0, 0, 160, 120)


@pytest.mark.unit
def test_hotspot_anchors_below_reward_grid():
    """y 轴按可用高度百分比分配：锚点越低（排数越多），底部密度越高、原高位密度越低。"""
    from tasks.Component.GeneralBattle.reward_frame import field_density, HOT_X
    cx = HOT_X[0] + HOT_X[1] / 2
    # 同一个屏幕偏下位置：三排奖励（锚点 554）分布整体压缩到底条，密度更高
    assert field_density(cx, 650, hot_y0=554) > field_density(cx, 650, hot_y0=419)
    # 同一个原热区高位：三排锚点下它已远在峰值上方（快衰减带），密度更低
    assert field_density(cx, 470, hot_y0=554) < field_density(cx, 470, hot_y0=419)


@pytest.mark.unit
def test_edge_taper_zero_at_block_edges():
    """块边渐缩：块边界上密度为 0，渐缩宽度处恢复全值，中间平滑过渡。"""
    from tasks.Component.GeneralBattle.reward_frame import _edge_taper
    assert _edge_taper(0, 50) == 0.0        # 正好在块边
    assert _edge_taper(50, 50) == 1.0       # 达到渐缩宽度
    assert _edge_taper(100, 50) == 1.0      # 超过渐缩宽度
    assert 0.0 < _edge_taper(25, 50) < 1.0  # 中间平滑
    assert _edge_taper(30, 0) == 1.0        # margin<=0 时不渐缩


@pytest.mark.unit
def test_field_rule_click_no_edge_pile_and_field_bias():
    """块内采样按密度场：不贴边堆积、整体重心偏向热区（右下）。"""
    import random as _random
    from tasks.Component.GeneralBattle.reward_frame import FieldRuleClick, HOT_Y_BASE
    _random.seed(3)
    x, y, w, h = 112, 171, 1168, 549   # 默认预设胜利画面的大块
    rule = FieldRuleClick((x, y, w, h), 'test', HOT_Y_BASE)
    pts = [rule.coord() for _ in range(6000)]
    # 全部落在矩形内
    assert all(x <= px < x + w and y <= py < y + h for px, py in pts)
    # 贴边堆积应回到自然水平：旧实现（正态裁剪）约 5%，此处应 < 0.5%
    edge = sum(1 for px, py in pts
               if px <= x or px >= x + w - 1 or py <= y or py >= y + h - 1)
    assert edge / len(pts) < 0.005
    # 重心偏向热区：热区中心 (762,531) 在矩形中心 (696,445) 右下方
    mean_x = sum(px for px, _ in pts) / len(pts)
    mean_y = sum(py for _, py in pts) / len(pts)
    assert mean_x > x + w / 2, '块内重心应右偏（右边衰减慢）'
    assert mean_y > y + h / 2, '块内重心应下偏（下方衰减慢）'


@pytest.mark.unit
def test_reward_grid_appear_as_second_criterion(monkeypatch):
    """奖励框检测作为「仍在奖励页」的第二判据：I_REWARD 失配时仍能触发点击。"""
    from tasks.Component.GeneralBattle.reward_frame import CELL_H
    b = object.__new__(GeneralBattle)
    b.device = SimpleNamespace(image=None)
    b.interval_timer = {}
    clicks = []
    monkeypatch.setattr(b, 'settlement_gesture',
                        lambda action, control_name=None: clicks.append(control_name))

    # 检测不到奖励框：判据为假、不点击
    monkeypatch.setattr('tasks.Component.GeneralBattle.general_battle.get_detector',
                        lambda: SimpleNamespace(detect=lambda image: []))
    assert b.reward_grid_appear() is False
    assert b.settlement_click_grid(object()) is False
    assert clicks == []

    # 检测到奖励框：判据为真、点击一次（控件名单列 REWARD_GRID）
    rows = [{'y0': 172, 'y1': 172 + CELL_H, 'boxes': []}]
    monkeypatch.setattr('tasks.Component.GeneralBattle.general_battle.get_detector',
                        lambda: SimpleNamespace(detect=lambda image: rows))
    b._reward_safe_rules = None          # 模拟换帧：缓存被截图入口作废
    assert b.reward_grid_appear() is True
    assert b.settlement_click_grid(object()) is True
    assert clicks == ['REWARD_GRID']
    # 落点区域与判据同源：奖励行被挖掉，安全块都在奖励网格下方
    assert all(r.roi_front[1] >= 172 for r in b.reward_click_actions())


@pytest.mark.unit
def test_reward_grid_wired_as_fallback_trigger():
    """各任务奖励循环都把奖励框接成兜底触发，且退出条件同步认这一判据。"""
    for path in MULTI_CLICK_FILES:
        src = _src(path)
        if path == 'tasks/MasterDisciple/script_task.py':
            continue      # 奖励阶段走 ui_click_until_smt_disappear，不在本契约内
        assert 'settlement_click_grid(' in src, f'{path} 奖励循环缺少奖励框兜底触发'
        assert 'not self.reward_grid_appear()' in src, \
            f'{path} 退出条件未认奖励框判据（奖励框还在就会提前退出）'


@pytest.mark.unit
def test_reward_detect_cache_is_per_frame(monkeypatch):
    """一帧一缓存：同帧内复用检测结果，换帧（截图）必重新检测。

    奖励框是结算动画里逐行出现的，用上一帧的禁区去点当前帧，可能正好点在
    刚出现的那一行上——所以缓存不能跨帧。
    """
    from tasks.base_task import BaseTask
    b = object.__new__(GeneralBattle)
    b.device = SimpleNamespace(image=None)
    b.interval_timer = {}
    calls = []
    monkeypatch.setattr('tasks.Component.GeneralBattle.general_battle.get_detector',
                        lambda: SimpleNamespace(detect=lambda image: calls.append(1) or []))
    monkeypatch.setattr(BaseTask, 'screenshot', lambda self: None)

    # 同一帧：算落点 + 奖励框判据 + 退出条件共 3 次调用，只检测一次
    b.reward_click_actions()
    b.reward_click_actions()
    b.reward_grid_appear()
    assert len(calls) == 1

    # 新的一帧：截图入口作废缓存，必须重新检测
    b.screenshot()
    assert getattr(b, '_reward_safe_rules', 'unset') is None
    b.reward_click_actions()
    assert len(calls) == 2


@pytest.mark.unit
def test_multi_click_gesture_structure(monkeypatch):
    """连点手势结构：首击走正常节奏（不带 pace=False），追加击才绕过节奏。"""
    import random as _random
    import tasks.Component.GeneralBattle.general_battle as gb
    from tasks.Component.GeneralBattle.reward_frame import (
        FieldRuleClick, HOT_Y_BASE, MULTI_CLICK_SIZES, MULTI_CLICK_WEIGHTS)
    b = object.__new__(GeneralBattle)
    clicks = []
    _install_clock(monkeypatch, gb, clicks)     # 走假时钟，避免真睡拖慢测试
    b.device = SimpleNamespace(click=lambda x, y, **kw: clicks.append((x, y, kw)))
    rule = FieldRuleClick((100, 200, 300, 150), 'test', HOT_Y_BASE)
    _random.seed(1)
    extra_total = 0
    for _ in range(50):
        clicks.clear()
        b.settlement_gesture(rule, control_name='I_REWARD')
        # 首击不带 pace=False（默认 True，走正常节奏链路）
        assert clicks and clicks[0][2].get('pace', True) is True
        # 其余全部是绕过节奏等待的追加击
        assert all(kw.get('pace') is False for _, _, kw in clicks[1:])
        extra_total += len(clicks) - 1
    # 单击概率 258/401≈64%，50 次全为单击的概率 0.64^50 ≈ 1e-10，追加击必然出现过
    assert MULTI_CLICK_WEIGHTS[0] / sum(MULTI_CLICK_WEIGHTS) < 0.7
    assert extra_total > 0


class _Clock:
    """假时钟：sleep 与 device.click 都推进它，用来验证 wall clock 语义的节拍与预算。

    真实时钟测不出这两件事——测试里 sleep 被打桩成不真睡，经过时间恒为 0，
    「节拍补偿是否补对」和「预算是否按真实耗时计」都会假通过。
    """

    def __init__(self, t=1000.0):
        self.t = t

    def time(self):
        return self.t

    def advance(self, d):
        self.t += d


# device.click 自身的耗时（秒）：按下/移动/抬起 + 拟人化按压时长 + 轨迹。
# QMUMU1/2/3 实测「相邻击间隔 334~381ms − 设定间隔 150~220ms」反推约 165ms。
CLICK_COST_S = 0.165


def _install_clock(monkeypatch, gb, clicks):
    """给 general_battle 装上假时钟，返回 (clock, 假的 device)。"""
    clock = _Clock()

    def fake_click(x, y, **kw):
        clicks.append((x, y, kw, clock.time()))    # 记录**发起**时刻
        clock.advance(CLICK_COST_S)                # 点击动作本身要花时间

    monkeypatch.setattr(gb, 'sleep', clock.advance)
    monkeypatch.setattr(gb, 'time', SimpleNamespace(time=clock.time,
                                                    sleep=clock.advance))
    return clock, SimpleNamespace(click=fake_click)


@pytest.mark.unit
def test_multi_click_distribution_gap_and_clamp(monkeypatch):
    """连点分布：追加击总量符合真人簇长期望；间隔在人类范围；落点以复用为主且钳回安全矩形。"""
    import random as _random
    import tasks.Component.GeneralBattle.general_battle as gb
    from tasks.Component.GeneralBattle.reward_frame import (
        FieldRuleClick, HOT_Y_BASE, MULTI_CLICK_GAP_S, MULTI_CLICK_MAX_S,
        MULTI_CLICK_SIZES, MULTI_CLICK_WEIGHTS)
    b = object.__new__(GeneralBattle)
    clicks = []
    clock, b.device = _install_clock(monkeypatch, gb, clicks)
    rule = FieldRuleClick((100, 200, 300, 150), 'test', HOT_Y_BASE)
    _random.seed(11)
    per_gesture = []                # 每次手势的 (追加击数, 追加击时刻列表, 起点)
    for _ in range(3000):
        n0 = len(clicks)
        first_ts = clock.time()
        clock.advance(CLICK_COST_S)             # 模拟首击自身的耗时
        b._settlement_extra_clicks(rule, 250, 275, 'I_REWARD', first_ts)
        per_gesture.append(([c[3] for c in clicks[n0:]], first_ts))
    # 期望追加击数 = 3000 × Σ(权重×(点数-1)) / Σ权重，权重即真人簇长直方图
    total_w = sum(MULTI_CLICK_WEIGHTS)
    exp = 3000 * sum(w * (n - 1) for n, w in
                     zip(MULTI_CLICK_SIZES, MULTI_CLICK_WEIGHTS)) / total_w
    assert exp * 0.9 < len(clicks) < exp * 1.1, \
        f'追加击总数 {len(clicks)} 偏离真人簇长期望 {exp:.0f}'
    # 追加击全部绕过节奏等待
    assert all(kw.get('pace') is False for _, _, kw, _ in clicks)
    # 节拍补偿：相邻两击的**实际发起间隔**落在人类连点间隔带内。
    # 若退回「无脑 sleep(gap)」，间隔会变成 gap + CLICK_COST_S（实测 334~381ms），
    # 上界断言就会失败——这正是 QMUMU 日志暴露的缺陷。
    gaps = [b_ - a for ts, _ in per_gesture for a, b_ in zip(ts, ts[1:])]
    gaps += [ts[0] - st for ts, st in per_gesture if ts]   # 首个间隔以首击发起为基准
    assert gaps, '未产生任何追加击'
    lo = min(MULTI_CLICK_GAP_S[0], CLICK_COST_S)   # click 比 gap 还慢时追不上节拍
    assert all(lo - 1e-9 <= g <= MULTI_CLICK_GAP_S[1] + 1e-9 for g in gaps), \
        f'实际间隔 {min(gaps):.3f}~{max(gaps):.3f}s 超出人类连点间隔带 {MULTI_CLICK_GAP_S}'
    # 时长预算：任何一次手势从首击到末击都在预算内（误触暴露窗口的硬上限）
    spans = [ts[-1] - st for ts, st in per_gesture if ts]
    assert max(spans) <= MULTI_CLICK_MAX_S, \
        f'手势时长 {max(spans):.2f}s 超出预算 {MULTI_CLICK_MAX_S}s'
    # 簇长封顶：首击点掉奖励页后追加击会落到新界面上，故不采用真人的长尾簇
    assert max(len(ts) for ts, _ in per_gesture) <= max(MULTI_CLICK_SIZES) - 1
    # 落点全部钳回首击所在的安全矩形内
    rx, ry, rw, rh = rule.roi_front
    assert all(rx <= x < rx + rw and ry <= y < ry + rh for x, y, _, _ in clicks)
    # 绝大多数追加击复用首击坐标（真人簇内 86% 落在同一像素），
    # 这是与「每次微抖 1~3px」的机器特征相区分的关键指标
    same = sum(1 for x, y, _, _ in clicks if (x, y) == (250, 275))
    assert same / len(clicks) > 0.7, f'复用首击坐标的比例 {same / len(clicks):.2f} 过低'
    # 发生偏移时是一次真实移动而非微抖：非零位移应显著大于 3px
    dist = [math.hypot(x - 250, y - 275) for x, y, _, _ in clicks]
    moved = [d for d in dist if d > 0.5]
    assert moved and statistics.median(moved) > 3.0, '偏移量退化成了微抖量级'


@pytest.mark.unit
def test_multi_click_budget_truncates_long_cluster(monkeypatch):
    """时长预算耗尽时立即收尾，不补完剩余次数——限制误触暴露窗口。

    把间隔调大到「两次就用光预算」，验证预算真的会截断，而不是只在当前参数下
    恰好不触发（当前 4 点最长 3×0.22=0.66s 本就在 0.7s 预算内）。
    """
    import random as _random
    import tasks.Component.GeneralBattle.general_battle as gb
    from tasks.Component.GeneralBattle.reward_frame import (
        FieldRuleClick, HOT_Y_BASE, MULTI_CLICK_MAX_S)
    b = object.__new__(GeneralBattle)
    clicks = []
    clock, b.device = _install_clock(monkeypatch, gb, clicks)
    rule = FieldRuleClick((100, 200, 300, 150), 'test', HOT_Y_BASE)
    # 间隔固定为预算的 0.4 倍：第 3 击落在 1.2 倍预算处，必被截断
    gap = MULTI_CLICK_MAX_S * 0.4
    monkeypatch.setattr(gb, 'MULTI_CLICK_GAP_S', (gap, gap))
    monkeypatch.setattr(gb, 'MULTI_CLICK_SIZES', (4,))      # 恒抽 4 点（3 次追加击）
    monkeypatch.setattr(gb, 'MULTI_CLICK_WEIGHTS', (1,))
    _random.seed(7)
    for _ in range(30):
        clicks.clear()
        first_ts = clock.time()
        clock.advance(CLICK_COST_S)
        b._settlement_extra_clicks(rule, 250, 275, 'I_REWARD', first_ts)
        assert len(clicks) == 2, f'预算未截断，实际追加 {len(clicks)} 次'
        assert clicks[-1][3] - first_ts <= MULTI_CLICK_MAX_S


@pytest.mark.unit
def test_hot_anchor_moves_only_when_grid_reaches_hot_zone():
    """禁区没碰到热区时热区不动，碰到了才被往下压。

    热区是 HOT_Y_BASE 向下 HOT_H 的一条带，奖励禁区是从上方压下来的满宽块，
    「碰到」等价于禁区底边越过热区上沿。一排/两排奖励的禁区底（284 / 419）
    都够不到上沿 427，锚点必须保持基准；三排（554）压进热区才让位。

    回归点：早先直接取禁区底，浅禁区会把热区反向上提到屏幕上半部。
    """
    from tasks.Component.GeneralBattle.reward_frame import (
        safe_click_rules, FrozenRowsDetector, FORBIDDEN_DEFAULT,
        ROW_TOPS, CELL_H, ROW_MARGIN, HOT_Y_BASE)

    def anchor(n_rows):
        rows = [{'y0': t, 'y1': t + CELL_H, 'boxes': []} for t in ROW_TOPS[:n_rows]]
        rules = safe_click_rules(None, forbidden_preset=FORBIDDEN_DEFAULT,
                                 detector=FrozenRowsDetector(rows))
        # 同一帧内所有安全块共用一个锚点
        assert len({r.hot_y0 for r in rules}) == 1
        return rules[0].hot_y0

    assert anchor(0) == HOT_Y_BASE          # 胜利画面：无奖励行
    assert anchor(1) == HOT_Y_BASE          # 禁区底 284，够不到热区
    assert anchor(2) == HOT_Y_BASE          # 禁区底 419，仍够不到
    assert anchor(3) == ROW_TOPS[2] + CELL_H + ROW_MARGIN   # 554，压进热区
    assert anchor(3) > HOT_Y_BASE, '三排奖励必须把热区往下压'


@pytest.mark.unit
def test_settlement_point_reuses_last_within_battle():
    """同一场战斗内的落点复用：多数复用上次坐标，跨 TTL 后回到自由取点。"""
    import random as _random
    import time as _time
    from tasks.Component.GeneralBattle.reward_frame import (
        FieldRuleClick, HOT_Y_BASE, SETTLEMENT_REUSE_TTL_S)
    b = object.__new__(GeneralBattle)
    rule = FieldRuleClick((100, 200, 600, 400), 'safe', HOT_Y_BASE)
    b._reward_safe_rules = [rule]          # 本帧安全区域缓存（复用的前提）
    _random.seed(3)

    # 首次调用没有历史，必然自由取点并记下落点
    x0, y0, _ = b._settlement_point(rule)
    assert b._settlement_last[:2] == (x0, y0)

    # 场次内连续取点：显著一部分应落在上次坐标附近（真人 ≤30px 占 43.8%）
    near = 0
    for _ in range(400):
        prev = b._settlement_last[:2]
        x, y, _ = b._settlement_point(rule)
        if math.hypot(x - prev[0], y - prev[1]) <= 30:
            near += 1
    assert near / 400 > 0.30, f'场次内复用比例 {near / 400:.2f} 过低'

    # 上次落点超过 TTL（换了一场战斗）后不再复用：伪造一个久远的时间戳
    b._settlement_last = (400, 500, _time.time() - SETTLEMENT_REUSE_TTL_S - 1)
    fresh = [b._settlement_point(rule)[:2] for _ in range(30)]
    assert all(p != (400, 500) for p in fresh), 'TTL 过期后仍在复用旧落点'


@pytest.mark.unit
def test_settlement_point_shifts_down_when_covered():
    """上次落点被奖励行覆盖时，保持 x 沿 y 下移到安全区域，而非重新自由取点。"""
    import random as _random
    import time as _time
    from tasks.Component.GeneralBattle.reward_frame import (
        FieldRuleClick, HOT_Y_BASE, locate_rule, shift_down_to_safe)
    b = object.__new__(GeneralBattle)
    # 奖励页：只有 y>=560 的下条是安全的（上方被奖励网格占满）
    lower = FieldRuleClick((0, 560, 1280, 160), 'lower', 560)
    b._reward_safe_rules = [lower]
    # 上次落点在胜利画面取的 y=500，已被本帧奖励行覆盖
    b._settlement_last = (900, 500, _time.time())
    assert locate_rule([lower], 900, 500) is None
    _random.seed(5)
    shifted = 0
    for _ in range(300):
        b._settlement_last = (900, 500, _time.time())
        x, y, _ = b._settlement_point(lower)
        if x == 900 and y >= 560:
            shifted += 1        # x 对齐保留、y 落进下条安全区
    assert shifted > 0, '被覆盖时没有发生向下平移'
    # 平移落点应贴着安全区上沿而不是滑到屏幕最底
    ny, _ = shift_down_to_safe([lower], 900, 500)
    assert 560 <= ny <= 560 + 40, f'下移落点 {ny} 偏离安全区上沿过远'
