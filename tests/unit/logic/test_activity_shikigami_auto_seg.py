# -*- coding: utf-8 -*-
"""ActivityShikigami 自动段接线测试：remaining 口径（场次/门票双约束、五倍 fail-closed、
类型过滤）、门票缓存递减钩子、gbc 透传、run_general_battle 分派链，
以及 ScriptTask 覆盖通用组件的四个自动段方法（AND 判定/前置开启/取消/段循环）。

覆盖方法测试复用 test_general_battle_auto_seg 的帧脚本构造模式
（object.__new__ 绕过 __init__ + monkeypatch 识别原语），裸实例构造目标
改为 ScriptTask：覆盖方法解析到本任务实现，继承方法（count_step/
_auto_wait_battle_ui 等）仍走通用组件。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tasks.ActivityShikigami.config import GeneralBattleConfig as ClimbBattleConfig
from tasks.ActivityShikigami import script_task as st_mod
from tasks.ActivityShikigami.script_task import ScriptTask


def _make_climb(climb='pass', enable=True, seg=2, total=4, limit=50, count=1,
                ticket=None, x5=False):
    """构造绕过 __init__ 的裸 ScriptTask，装配自动段口径所需最小依赖。

    conf 走实例 __dict__ 覆盖 cached_property；climb_type 由
    run_idx + run_sequence_v 驱动（只读 property，不能直接赋值）。
    """
    obj = object.__new__(ScriptTask)
    obj.run_idx = 0
    obj.conf = SimpleNamespace(
        general_battle=ClimbBattleConfig(auto_battle_enable=enable,
                                         auto_segment_count=seg,
                                         auto_total_count=total),
        general_climb=SimpleNamespace(run_sequence_v=[climb],
                                      **{f'{climb}_limit': limit}),
        switch_soul_config=SimpleNamespace(**{f'{climb}_group_team': '1,1'}),
    )
    obj.conf.validate_switch_preset = lambda: None
    obj.current_count = count
    obj._ticket_cache = {} if ticket is None else {climb: ticket}
    obj._x5_active = x5
    return obj


def _stub_battle_chain(monkeypatch, obj):
    """打桩通用战斗链，让 run_general_battle（含爬塔重写）可被驱动。"""
    monkeypatch.setattr(obj, 'battle_before', lambda *a, **k: True)
    monkeypatch.setattr(obj, 'is_in_battle', lambda shot=False: False)
    monkeypatch.setattr(obj, 'battle_wait', lambda *a, **k: True)


@pytest.mark.unit
def test_remaining_config_disabled():
    """配置未开：None（组件层静默跳过，双保险之一）。"""
    obj = _make_climb(enable=False)
    assert obj._auto_seg_remaining() is None


@pytest.mark.unit
@pytest.mark.parametrize('climb', ['ap20', 'pass_monopoly', 'season_boss'])
def test_remaining_type_not_supported(climb):
    """不接入的爬塔类型：None 静默跳过（ap20/大富翁/修行合训）。"""
    obj = _make_climb(climb=climb)
    assert obj._auto_seg_remaining() is None


@pytest.mark.unit
def test_remaining_x5_fail_closed(caplog):
    """五倍消耗期间 fail-closed：None + 告警只打一次（场次/门票口径错配）。"""
    obj = _make_climb(x5=True, ticket=30)
    with caplog.at_level('WARNING'):
        assert obj._auto_seg_remaining() is None
        assert obj._auto_seg_remaining() is None   # 第二次仍禁用
    warns = [r for r in caplog.records if '五倍消耗' in r.message]
    assert len(warns) == 1


@pytest.mark.unit
def test_remaining_limit_zero():
    """场次上限 0（该类型未启用）：None。"""
    obj = _make_climb(limit=0)
    assert obj._auto_seg_remaining() is None


@pytest.mark.unit
def test_remaining_min_of_limit_and_ticket():
    """双约束取小：门票缓存与场次上限谁小取谁。"""
    # 场次剩余 50-10=40 > 门票 30 → 取 30（门票约束生效）
    obj = _make_climb(limit=50, count=10, ticket=30)
    assert obj._auto_seg_remaining() == 30
    # 场次剩余 40 < 门票 45 → 取 40
    obj = _make_climb(limit=50, count=10, ticket=45)
    assert obj._auto_seg_remaining() == 40
    # 无门票缓存（首场前的防御分支）：只受场次约束
    obj = _make_climb(limit=50, count=10, ticket=None)
    assert obj._auto_seg_remaining() == 40


@pytest.mark.unit
def test_remaining_ticket_zero():
    """门票缓存为 0：remaining=0，规划要求 remaining >= M+1，不会开新段。"""
    obj = _make_climb(ticket=0)
    assert obj._auto_seg_remaining() == 0


@pytest.mark.unit
def test_count_hook_decrements_ticket():
    """段内每场递减门票缓存 1：段内 fire 由游戏点，主循环不再递减。"""
    obj = _make_climb(climb='ap', ticket=3)
    obj.auto_battle_count_hook()
    assert obj._ticket_cache['ap'] == 2
    # 边界：递减到 0 不为负
    obj._ticket_cache['ap'] = 0
    obj.auto_battle_count_hook()
    assert obj._ticket_cache['ap'] == 0


@pytest.mark.unit
def test_count_hook_ignores_uncached_type():
    """无缓存的类型（ap20 不走 check_tickets_enough，缓存天然为空）：
    钩子不炸也不写入缓存。"""
    obj = _make_climb(climb='ap20', ticket=None)
    obj.auto_battle_count_hook()
    assert 'ap20' not in obj._ticket_cache


@pytest.mark.unit
def test_gbc_transfers_auto_seg_fields():
    """get_general_battle_conf 构造的组件 gbc 透传自动段三字段
    （不透传则组件侧永远是默认关闭）。"""
    obj = _make_climb(enable=True, seg=3, total=5)
    gbc = obj.get_general_battle_conf()
    assert gbc.auto_battle_enable is True
    assert gbc.auto_segment_count == 3
    assert gbc.auto_total_count == 5


@pytest.mark.unit
def test_run_general_battle_passes_remaining(monkeypatch):
    """爬塔重写向 super 传 remaining（调用前口径，与序言计数时序对齐），
    经 auto_battle_plan 的 plan_calls 行为断言。"""
    obj = _make_climb(limit=50, count=1, ticket=30)
    obj.device = MagicMock()
    obj.interval_timer = {}
    _stub_battle_chain(monkeypatch, obj)
    plan_calls = []
    monkeypatch.setattr(obj, 'auto_battle_plan', lambda cfg, r: plan_calls.append(r))
    monkeypatch.setattr(obj, 'auto_battle_run', lambda: None)
    from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
    cfg = GeneralBattleConfig()
    obj.run_general_battle(config=cfg)
    obj.run_general_battle(config=cfg)
    # 第一次调用前 count=1 → min(50-1, 30)=30；序言 +1 后第二次 → min(50-2, 30)=30
    assert plan_calls == [30, 30]
    # 门票耗到低于场次剩余后门票约束生效：29 < 48
    obj._ticket_cache['pass'] = 29
    obj.run_general_battle(config=cfg)
    assert plan_calls[-1] == 29


@pytest.mark.unit
def test_run_general_battle_dispatches_segment(monkeypatch):
    """分派链：经真实 ScriptTask.run_general_battle（含重写）驱动，到达段起点时
    auto_battle_run 被分派执行——段分支挂在组件 run_general_battle 上，重写了
    battle_wait 的爬塔同样可达。"""
    obj = _make_climb(limit=50, count=1, ticket=None)
    obj.device = MagicMock()
    obj.interval_timer = {}
    _stub_battle_chain(monkeypatch, obj)
    seg_calls = []

    def _seg_run():
        # 复刻真实现入口副作用：领取段即消耗 planned（本场只进段一次）
        seg_calls.append(1)
        obj._auto_seg['planned'] = False

    monkeypatch.setattr(obj, 'auto_battle_run', _seg_run)
    # 起点抽到 0：本场即段起点（remaining = 50 - 1 = 49 足够规划 M+1）
    monkeypatch.setattr('tasks.Component.GeneralBattle.general_battle.random.randint',
                        lambda lo, hi: 0)
    from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
    cfg = GeneralBattleConfig()
    cfg.auto_battle_enable = True
    cfg.auto_segment_count = 2
    assert obj.run_general_battle(config=cfg) is True
    assert seg_calls == [1]
    # 段状态已按规划落位（起点 0、段长 2），且领取后 planned 归 False
    seg = obj._auto_seg
    assert seg['start_offset'] == 0 and seg['seg_len'] == 2
    assert seg['planned'] is False


@pytest.mark.unit
def test_run_general_battle_x5_disables_seg(monkeypatch, caplog):
    """五倍期间 run_general_battle 分派链上规划收到 None（端到端 fail-closed）。"""
    obj = _make_climb(x5=True, ticket=30)
    obj.device = MagicMock()
    obj.interval_timer = {}
    _stub_battle_chain(monkeypatch, obj)
    plan_calls = []
    monkeypatch.setattr(obj, 'auto_battle_plan', lambda cfg, r: plan_calls.append(r))
    monkeypatch.setattr(obj, 'auto_battle_run', lambda: None)
    from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
    obj.run_general_battle(config=GeneralBattleConfig())
    assert plan_calls == [None]


@pytest.mark.unit
def test_remaining_now_reuses_scope():
    """段内截断口径复用规划口径实时计算。"""
    obj = _make_climb(limit=50, count=10, ticket=20)
    assert obj.auto_battle_remaining_now() == 20


@pytest.mark.unit
def test_run_finish_sweep_called():
    """run() 收尾处调 auto_battle_finish_sweep（源码级断言）。"""
    import inspect
    src = inspect.getsource(ScriptTask.run)
    assert 'auto_battle_finish_sweep' in src


# ------------------------------------------------ 覆盖方法：判定/开启/取消
def _make_judge_battle(monkeypatch):
    """构造只够 is_auto_battle_page 用的裸 ScriptTask：flags 驱动 appear，
    OCR 可注入。RuleImage.name 由文件名推导（I_PAPER_TOSTART.name 是
    'GB_PAPER_TOSTART'），flags 键一律用资产真实 name，不硬编码。"""
    b = object.__new__(ScriptTask)
    b.device = MagicMock()
    b.interval_timer = {}
    b.flags = {b.I_PAPER_TOSTART.name: True}
    b.ocr_text = '×2'

    def _appear(target, *a, **kw):
        return b.flags.get(target.name, False)

    b.appear = _appear
    monkeypatch.setattr(b.O_POINT_OR_SPEED, 'ocr',
                        lambda image, keyword=None: b.ocr_text)
    return b


@pytest.mark.unit
def test_is_auto_battle_page_matrix(monkeypatch):
    """AND 语义（2026-09-09 爬塔真机定稿）：OCR 运行数字 且 按钮消失，
    两者同时成立才自动中。

    爬塔真机教训：通用 OR 语义（按钮不在即自动）在进场动画期按钮未渲染时
    假成功，段次次虚开次次 1/M 假中断、全程手动——爬塔覆盖版只认双证据。
    """
    b = _make_judge_battle(monkeypatch)
    # 按钮在：一律手动（开启后按钮即消失），OCR 数字属 ×2 误读，不作数
    assert b.is_auto_battle_page() is False          # 按钮在 + ×2
    b.ocr_text = '30'
    assert b.is_auto_battle_page() is False          # 按钮在 + 误读数字
    # 按钮消失：还要 OCR 是运行数字才算自动中
    b.flags[b.I_PAPER_TOSTART.name] = False
    b.ocr_text = '30'
    assert b.is_auto_battle_page() is True           # 数字 + 消失：自动中
    b.ocr_text = '×2'
    assert b.is_auto_battle_page() is False          # ×2 + 消失：非自动
    b.ocr_text = ''
    assert b.is_auto_battle_page() is False          # 读空 + 消失：无证据，不算


def _make_click_battle(monkeypatch, scene):
    """构造开启/取消原语用的裸 ScriptTask：scene 是每帧状态的可变 dict。

    scene 键：paper（I_PAPER_TOSTART 是否出现）、auto（是否自动页）、
    battle（是否战斗过程界面，缺省 True）、ocr（O_POINT_OR_SPEED 读数，
    缺省按 auto/battle 推断：auto→'15' 运行数字，battle→'×2'，否则 ''）。
    点击 C_PAPER_TOSTART 的效果由用例通过改写 scene 模拟。
    is_auto_battle_page / _auto_start 走爬塔覆盖实现，判定原语全部经 mock 驱动。
    """
    b = object.__new__(ScriptTask)
    b.device = MagicMock()
    b.interval_timer = {}
    b.clicks = []
    b.scene = scene
    monkeypatch.setattr(b, 'screenshot', lambda *a, **k: None)
    monkeypatch.setattr(b, 'is_in_real_battle', lambda shot=False: scene.get('battle', True))

    def _ocr(image, keyword=None):
        if 'ocr' in scene:
            return scene['ocr']
        if scene.get('auto', False):
            return '15'
        if scene.get('battle', True):
            return '×2'
        return ''

    monkeypatch.setattr(b.O_POINT_OR_SPEED, 'ocr', _ocr)

    def _appear(target, *a, **kw):
        return scene.get('paper', False) if target.name == b.I_PAPER_TOSTART.name else False

    def _click(target, *a, **kw):
        b.clicks.append(target.name)
        return True

    b.appear = _appear
    b.click = _click
    # 覆盖方法里的 sleep 解析到爬塔模块全局，打补丁要打到该模块
    monkeypatch.setattr('tasks.ActivityShikigami.script_task.sleep',
                        lambda *a, **k: None)
    return b


@pytest.mark.unit
def test_auto_start_click_and_confirm(monkeypatch):
    """开启：按钮出现→点击→自动页（数字+按钮消失）确认成功。"""
    scene = {'paper': True, 'auto': False}
    b = _make_click_battle(monkeypatch, scene)
    # 模拟点击生效：下一次查询变为自动页（按钮消失、OCR 变运行数字）
    b.click = lambda target, *a, **kw: (b.clicks.append(target.name),
                                       scene.update({'paper': False, 'auto': True})) and True
    assert b._auto_start() is True
    assert b.clicks == ['paper_tostart']


@pytest.mark.unit
def test_auto_start_already_on(monkeypatch):
    """已在自动页（上次残留）：数字 + 按钮消失，不点击直接视为成功。"""
    scene = {'paper': False, 'auto': True}
    b = _make_click_battle(monkeypatch, scene)
    assert b._auto_start() is True
    assert b.clicks == []


@pytest.mark.unit
def test_auto_start_waits_battle_ui(monkeypatch):
    """战斗界面前置：不在战斗过程界面（准备页/结算页/过渡动画）时不点击，
    等到进入战斗界面再走开启判定——避免在错误页面上误触。"""
    scene = {'paper': True, 'auto': False, 'battle': False}   # 一直在准备页
    b = _make_click_battle(monkeypatch, scene)
    assert b._auto_start() is False
    assert b.clicks == []          # 全程不在战斗界面：零点击


@pytest.mark.unit
def test_auto_start_clicks_without_button_template(monkeypatch):
    """按钮模板失配但 OCR 读到 ×2：同样点击开启（两判据任一可触发点击）。"""
    scene = {'paper': False, 'auto': False, 'ocr': '×2'}
    b = _make_click_battle(monkeypatch, scene)
    # 点击后进入自动页（按钮继续不在，OCR 变运行数字——显式 ocr 键必须一并
    # 更新，否则残留 ×2 会让下一轮判定再次点击）
    b.click = lambda target, *a, **kw: (b.clicks.append(target.name),
                                       scene.update({'auto': True, 'ocr': '15'})) and True
    assert b._auto_start() is True
    assert b.clicks == ['paper_tostart']


@pytest.mark.unit
def test_auto_start_waits_entry_animation(monkeypatch):
    """进场动画期（按钮不在 + OCR 读空）：不点击也不误判自动，等按钮渲染
    出来再点击——2026-09-09 oas1 爬塔虚开事故（次次 1/M 假中断）的回归用例。"""
    scene = {'paper': False, 'auto': False, 'ocr': ''}   # 动画期：无任何证据
    b = _make_click_battle(monkeypatch, scene)
    shots = {'n': 0}

    def _shot(*a, **k):
        # 前 2 轮循环是进场动画（按钮/速度控件都未渲染）；第 3 轮按钮渲染
        # 出来（只渲染一次，之后场景演进交给点击回调，避免覆盖自动页状态）
        shots['n'] += 1
        if shots['n'] == 3:
            scene.update({'paper': True, 'ocr': '×2'})

    monkeypatch.setattr(b, 'screenshot', _shot)
    b.click = lambda target, *a, **kw: (b.clicks.append(target.name),
                                        scene.update({'paper': False, 'auto': True,
                                                      'ocr': '15'})) and True
    assert b._auto_start() is True
    # 动画期零点击：clicks 只有按钮出现后的那一次开启点击
    assert b.clicks == ['paper_tostart']
    assert shots['n'] >= 3           # 确实经历了动画期等待，不是首轮就点


@pytest.mark.unit
def test_auto_start_fail_retries(monkeypatch, caplog):
    """开启失败：重试 5 次（共 6 次尝试）后告警返回 False。"""
    scene = {'paper': True, 'auto': False}   # 点了也永远不开
    b = _make_click_battle(monkeypatch, scene)
    with caplog.at_level('WARNING'):
        assert b._auto_start() is False
    assert b.clicks == ['paper_tostart'] * 6
    assert any('Auto battle start failed' in r.message for r in caplog.records)


@pytest.mark.unit
def test_auto_cancel_success(monkeypatch):
    """取消：自动页→点击→按钮回归确认成功。"""
    scene = {'paper': False, 'auto': True}
    b = _make_click_battle(monkeypatch, scene)
    b.click = lambda target, *a, **kw: (b.clicks.append(target.name),
                                       scene.update({'paper': True, 'auto': False})) and True
    assert b._auto_cancel() is True
    assert b.clicks == ['paper_tostart']


@pytest.mark.unit
def test_auto_cancel_fail_retries(monkeypatch, caplog):
    """取消失败：重试 2 次后告警返回 False（调用方继续手动流程）。"""
    scene = {'paper': False, 'auto': True}   # 点了也取消不掉
    b = _make_click_battle(monkeypatch, scene)
    with caplog.at_level('WARNING'):
        assert b._auto_cancel() is False
    assert b.clicks == ['paper_tostart'] * 3
    assert any('Auto battle cancel failed' in r.message for r in caplog.records)


@pytest.mark.unit
def test_auto_cancel_uses_fresh_frame(monkeypatch, caplog):
    """取消确认必须基于点击后的新帧——点击效果延迟到下一次截图才可见
    （真机语义），若拿点击前的旧帧确认，按钮明明回归却查不到。"""
    scene = {'paper': False, 'auto': True}
    b = _make_click_battle(monkeypatch, scene)
    pending = {}

    # 点击只登记待生效状态，下一次截图才应用到当前帧——模拟 appear 基于
    # device.image、截图才刷新的时序
    def _click(target, *a, **kw):
        b.clicks.append(target.name)
        pending.update({'paper': True, 'auto': False})
        return True

    def _shot():
        if pending:
            scene.update(pending)
            pending.clear()

    b.click = _click
    monkeypatch.setattr(b, 'screenshot', _shot)
    with caplog.at_level('INFO', logger='oas'):
        assert b._auto_cancel() is True
    assert b.clicks == ['paper_tostart']       # 一击即在新帧上确认成功
    # 确认走 'canceled' 分支而非下一轮 'already manual'（旧帧确认的退化路径）
    assert any(r.message == 'Auto battle canceled' for r in caplog.records)
    assert not any('already manual' in r.message for r in caplog.records)


@pytest.mark.unit
def test_finish_sweep_skips_when_manual(monkeypatch):
    """收尾兜底（通用版 finish_sweep + 爬塔 AND 判定的组合）：非战斗界面
    直接返回零点击；战斗界面手动态零点击；战斗界面自动态补一次取消。

    真机事故：OR 语义下主界面按钮天然不在被误判"自动中"，在庭院坐标连点三次
    paper_tostart——收尾必须先确认战斗界面（is_in_real_battle）再判定。
    """
    # 非战斗界面（任务收尾时已回主界面）：判定不可信，直接返回
    scene = {'paper': False, 'auto': True, 'battle': False}
    b = _make_click_battle(monkeypatch, scene)
    monkeypatch.setattr(b, 'is_in_real_battle', lambda shot=False: scene['battle'])
    b.auto_battle_finish_sweep()
    assert b.clicks == []          # 连 is_auto_battle_page 都不查，零点击
    # 战斗界面 + 手动态：AND 判定不成立，零点击
    scene.update({'battle': True, 'paper': True, 'auto': False})
    b.auto_battle_finish_sweep()
    assert b.clicks == []
    # 战斗界面 + 自动态：补一次取消
    scene.update({'paper': False, 'auto': True})
    b.click = lambda target, *a, **kw: (b.clicks.append(target.name),
                                       scene.update({'paper': True, 'auto': False})) and True
    b.auto_battle_finish_sweep()
    assert b.clicks == ['paper_tostart']


# ------------------------------------------------ 覆盖方法：auto_battle_run 段循环
class _SegScene:
    """段函数的帧脚本：每调一次 screenshot() 前进一帧。

    每帧是 dict：auto（战斗界面上的自动状态）、settle（结算页系是否出现）、
    battle（战斗过程界面）、prepare（准备页）。段函数的识别原语全部从当前帧取值。
    注意：构造时 cur=frames[0] 供外部预置，第一次 screenshot() 推进到 frames[1]，
    即段函数的每次判断都发生在「刚推进到」的帧上。
    """

    def __init__(self, obj, frames):
        self.obj = obj
        self.frames = frames
        self.i = 0
        self.clicks = []
        self.count_steps = 0
        self.cur = frames[0] if frames else {}

    def advance(self):
        """推进一帧（帧脚本驱动段循环，段内不再识别自动状态）。"""
        if self.i + 1 < len(self.frames):
            self.i += 1
        self.cur = self.frames[self.i]


def _make_seg_run_battle(monkeypatch, frames, m=2, remaining_now=None):
    """构造 auto_battle_run（爬塔覆盖版）用的裸 ScriptTask 并接管全部识别原语。

    继承方法（count_step/_auto_wait_battle_ui）走通用组件真实现，
    覆盖方法（_auto_start/_auto_cancel/is_auto_battle_page/auto_battle_run）
    走爬塔实现——sleep/time/常量的 monkeypatch 因此要打到各自所属模块。
    资产名说明：RuleImage.name 由文件名推导（I_PAPER_TOSTART.name 是
    'GB_PAPER_TOSTART'，结算系模板是 'GB_WIN' 等大写名）。
    """
    b = object.__new__(ScriptTask)
    b.device = MagicMock()
    b.interval_timer = {}
    b.current_count = 1      # 序言已计第 1 场
    scene = _SegScene(b, frames)
    b._scene = scene

    def _shot():
        scene.advance()

    monkeypatch.setattr(b, 'screenshot', _shot)

    def _appear(target, *a, **kw):
        name = target.name
        if name == 'GB_PAPER_TOSTART':
            # 按钮出现：帧里可显式给 paper 键（进场动画期 False），
            # 缺省按"手动战斗界面"推断
            return scene.cur.get('paper', scene.cur.get('battle', False) and not scene.cur.get('auto', False))
        if name in ('GB_WIN', 'GB_WIN_2', 'GB_DE_WIN', 'GB_FALSE',
                    'GB_REWARD', 'GB_REWARD_GOLD'):
            return scene.cur.get('settle', False)
        if name == 'GB_BATTLE_INFO':
            return scene.cur.get('battle', False)
        return False

    b.appear = _appear
    monkeypatch.setattr(b, 'win_appear', lambda *a, **kw: scene.cur.get('settle', False))
    # 奖励框检测兜底判据（与 settlement_click_grid 同一检测）：帧里用 grid 键
    # 显式控制，不依赖裸实例上"检测异常回退 False"的隐式行为
    monkeypatch.setattr(b, 'reward_grid_appear', lambda *a, **kw: scene.cur.get('grid', False))
    # OCR 读数：帧里可显式给 ocr 键；缺省按帧语义推断——自动页=运行数字，
    # 手动战斗界面=×2，其余（结算/过渡）=读空
    def _ocr(image, keyword=None):
        if 'ocr' in scene.cur:
            return scene.cur['ocr']
        if scene.cur.get('auto', False):
            return '15'
        if scene.cur.get('battle', False):
            return '×2'
        return ''

    monkeypatch.setattr(b.O_POINT_OR_SPEED, 'ocr', _ocr)
    monkeypatch.setattr(b, 'is_in_real_battle', lambda shot=False: scene.cur.get('battle', False))
    monkeypatch.setattr(b, 'is_in_prepare', lambda shot=False: scene.cur.get('prepare', False))

    # steps 经计数钩子记录（count_step 走真实现以扣减 total_left，
    # 真实现内部调 device.stuck_record_add 与本钩子，device 是 MagicMock）
    steps = []
    b.auto_battle_count_hook = lambda: steps.append(1)

    def _remaining():
        return remaining_now

    monkeypatch.setattr(b, 'auto_battle_remaining_now', _remaining)

    # 开启/取消走真实原语的点击断言版：点击把帧切到开启后状态由用例排帧完成，
    # 这里只记录点击；_auto_start/_auto_cancel 保持爬塔覆盖实现（内部用的
    # appear/is_auto_battle_page 已接管，sleep 已打补丁）
    def _click(target, *a, **kw):
        scene.clicks.append(target.name)
        return True

    b.click = _click
    # 覆盖方法里的 sleep/time 解析到爬塔模块；继承的 _auto_wait_battle_ui
    # 里的 sleep/Timer 解析到通用模块——两处都要打补丁
    monkeypatch.setattr('tasks.ActivityShikigami.script_task.sleep',
                        lambda *a, **k: None)
    monkeypatch.setattr('tasks.Component.GeneralBattle.general_battle.sleep',
                        lambda *a, **k: None)
    monkeypatch.setattr('tasks.ActivityShikigami.script_task.time',
                        lambda: 0.0)
    seg = b._seg_state()
    seg.update({'enabled': True, 'total_left': 4, 'planned': True,
                'start_offset': 0, 'seg_len': m})
    return b, scene, steps


@pytest.mark.unit
def test_auto_battle_run_full_segment(monkeypatch):
    """M=2 完整段：开自动 1 击 + 跨 1 次边界（第 2 场）+ 段尾取消 1 击，无其他输入。"""
    battle_auto = {'auto': True, 'battle': True, 'settle': False, 'prepare': False}
    battle_manual = {'auto': False, 'battle': True, 'settle': False, 'prepare': False}
    settle = {'auto': False, 'battle': False, 'settle': True, 'prepare': False}
    # 帧 0 仅供构造预置；_auto_start 首次截图推进到帧 1（非自动页但按钮在 → 点击开启），
    # 第二次截图落在帧 2（自动页）确认成功——点击生效由排帧保证，点击本身只记录。
    # 段尾取消前 _auto_wait_battle_ui 先轮询截图等战斗界面，故多一帧 battle_auto
    frames = [battle_manual,
              battle_manual,                      # _auto_start 首查：点击开启
              battle_auto,                        # 确认进入自动页
              settle, settle,                     # 第 1 场结算页出现
              battle_auto,                        # 回到战斗界面（第 2 场）→ count_step，seg_done=2
              battle_auto,                        # _auto_wait_battle_ui 等到战斗界面
              battle_auto,                        # 段尾取消首查：仍自动页 → 点击
              battle_manual]                      # 按钮回归，确认取消成功
    b, scene, steps = _make_seg_run_battle(monkeypatch, frames, m=2)
    b.auto_battle_run()
    assert steps == [1]                       # 段内只补第 2 场的计数
    assert scene.clicks == ['paper_tostart', 'paper_tostart']   # 开启 + 取消
    assert b._seg_state()['planned'] is False  # 段已消耗
    assert b._seg_state()['total_left'] == 2    # 4 - 第1场1 - 第2场1


@pytest.mark.unit
def test_auto_battle_run_grid_fallback_settle(monkeypatch):
    """奖励框兜底：结算系模板全部失配（活动奖励底色变化等）时，
    reward_grid_appear 作为第二判据仍能识别结算边界——记次与段尾取消
    与模板命中时完全一致，否则段会卡死在等待结算。"""
    battle_auto = {'auto': True, 'battle': True, 'settle': False, 'prepare': False, 'grid': False}
    battle_manual = {'auto': False, 'battle': True, 'settle': False, 'prepare': False, 'grid': False}
    # 模板失配的结算帧：settle=False（win/false/reward/gold 全不匹配），仅 grid=True
    settle_grid_only = {'auto': False, 'battle': False, 'settle': False, 'prepare': False, 'grid': True}
    frames = [battle_manual,
              battle_manual,                      # _auto_start 首查：点击开启
              battle_auto,                        # 确认进入自动页
              settle_grid_only, settle_grid_only,  # 结算页（仅奖励框检测可见）
              battle_auto,                        # 回到战斗界面 → count_step，seg_done=2
              battle_auto,                        # _auto_wait_battle_ui 等到战斗界面
              battle_auto,                        # 段尾取消首查：仍自动页 → 点击
              battle_manual]                      # 按钮回归，确认取消成功
    b, scene, steps = _make_seg_run_battle(monkeypatch, frames, m=2)
    b.auto_battle_run()
    assert steps == [1]                       # 模板失配下边界照常识别，补第 2 场计数
    assert scene.clicks == ['paper_tostart', 'paper_tostart']
    assert b._seg_state()['total_left'] == 2


@pytest.mark.unit
def test_auto_battle_run_no_settle_timeout(monkeypatch, caplog):
    """单场墙钟超时：持续等不到结算页（自动失效/游戏卡死）→ 告警退出，
    不取消（页面状态未知，交 battle_wait 兜底）。"""
    battle_manual = {'auto': False, 'battle': True, 'settle': False, 'prepare': False}
    frames = [battle_manual] * 8               # 全程无结算
    b, scene, steps = _make_seg_run_battle(monkeypatch, frames, m=2)
    # _auto_start 恒成功（不耗帧），段循环立即开始；墙钟设为已超时
    monkeypatch.setattr(b, '_auto_start', lambda retry=2: True)
    monkeypatch.setattr(st_mod, 'AUTO_BATTLE_BATTLE_TIMEOUT_S', -1)
    with caplog.at_level('WARNING'):
        b.auto_battle_run()
    assert steps == []                         # 没跨过边界
    assert scene.clicks == []                  # 零输入（连取消都没有）
    assert any('no settle' in r.message for r in caplog.records)


@pytest.mark.unit
def test_auto_battle_run_stuck_refresh(monkeypatch):
    """stuck 续窗：段内零输入不能点，靠纯状态操作（clear+add）周期性重置
    stuck 计时——300s 长窗不会在段内炸（慢战斗/多场段）。

    oas1 爬塔事故回归：开启点击清空 BATTLE_STATUS_S 后无人补，首场
    60s+ 慢战斗撞上 60s stuck 窗口被误杀重启游戏。
    """
    battle_auto = {'auto': True, 'battle': True, 'settle': False, 'prepare': False}
    battle_manual = {'auto': False, 'battle': True, 'settle': False, 'prepare': False}
    settle = {'auto': False, 'battle': False, 'settle': True, 'prepare': False}
    frames = [battle_manual,
              battle_manual,                      # _auto_start 点击开启
              battle_auto,                        # 确认自动页
              settle, settle,                     # 第 1 场结算
              battle_auto,                        # 回战斗界面 → count_step
              battle_auto,                        # _auto_wait_battle_ui
              battle_auto,                        # 取消点击
              battle_manual]                      # 确认取消
    b, scene, steps = _make_seg_run_battle(monkeypatch, frames, m=2)
    # 续窗间隔设为已到期：段循环每帧都续窗（clear+add 成对）
    monkeypatch.setattr(st_mod, 'AUTO_BATTLE_STUCK_REFRESH_S', -1)
    b.auto_battle_run()
    assert steps == [1]
    # 续窗产生了 clear+add 对（device 是 MagicMock，统计调用）；
    # clicks 里只有开启/取消两次——续窗不产生任何点击
    assert scene.clicks == ['paper_tostart', 'paper_tostart']
    assert b.device.stuck_record_clear.call_count >= 1
    assert b.device.stuck_record_add.call_count >= 1


@pytest.mark.unit
def test_auto_battle_run_readds_after_start_click(monkeypatch):
    """开启后 re-add：_auto_start 的点击在真机会触发 stuck_record_clear
    （record 清空），段函数在 _auto_start 返回后必须立即补回 BATTLE_STATUS_S
    ——首个 60s 窗口内 detect_record 非空，慢战斗才按长战斗放行。

    add 的两次构成为：开启后 re-add（首次，mock_calls 可验序）+ 跨场
    count_step 内的重挂。
    """
    battle_auto = {'auto': True, 'battle': True, 'settle': False, 'prepare': False}
    battle_manual = {'auto': False, 'battle': True, 'settle': False, 'prepare': False}
    settle = {'auto': False, 'battle': False, 'settle': True, 'prepare': False}
    frames = [battle_manual,
              battle_manual,                    # _auto_start 首查：按钮在 → 点击开启
              battle_auto,                      # 确认进入自动页 → re-add 在此之后发生
              settle, settle,                   # 第 1 场结算
              battle_auto]                      # 回战斗界面 → count_step，seg_done=2 出循环
    b, scene, steps = _make_seg_run_battle(monkeypatch, frames, m=2)
    monkeypatch.setattr(b, '_auto_wait_battle_ui', lambda timeout=10.0: False)
    monkeypatch.setattr(b, '_auto_cancel', lambda retry=2: True)
    b.auto_battle_run()
    # re-add 1 次 + count_step 重挂 1 次；首次调用即开启后的 re-add
    assert b.device.stuck_record_add.call_count == 2
    assert b.device.mock_calls[0][1][0] == 'BATTLE_STATUS_S'


@pytest.mark.unit
def test_auto_battle_run_truncated(monkeypatch, caplog):
    """截断：count_step 后剩余场数不足（remaining_now <= m - seg_done）→ 立即取消。"""
    battle_auto = {'auto': True, 'battle': True, 'settle': False, 'prepare': False}
    settle = {'auto': False, 'battle': False, 'settle': True, 'prepare': False}
    battle_manual = {'auto': False, 'battle': True, 'settle': False, 'prepare': False}
    # 帧序：开启首查成功（帧 1 auto）→ 第 1 场结算 → 回战斗界面触发截断 →
    # _auto_wait_battle_ui 等到战斗界面 → 取消时仍是自动页（点击一击）→ 按钮回归手动页
    frames = [battle_auto,
              battle_auto,                      # 开启确认
              settle, settle,                   # 第 1 场结算
              battle_auto,                      # 回战斗界面 → count_step → 触发截断
              battle_auto,                      # _auto_wait_battle_ui 等到战斗界面
              battle_auto,                      # 取消时仍自动页 → 点击
              battle_manual]                    # 按钮回归确认取消成功
    b, scene, steps = _make_seg_run_battle(monkeypatch, frames, m=3, remaining_now=1)
    # m=3、seg_done=1 后 remaining_now=1 <= 3-1=2 → 截断
    with caplog.at_level('WARNING'):
        b.auto_battle_run()
    assert steps == [1]
    assert scene.clicks == ['paper_tostart']    # 只有取消一击
    assert any('truncated' in r.message for r in caplog.records)


@pytest.mark.unit
def test_auto_battle_run_start_fail_no_consume(monkeypatch):
    """开启失败：段函数直接返回，total_left 不消耗（本场手动）。"""
    frames = [{'auto': False, 'battle': True, 'settle': False, 'prepare': False}] * 8
    b, scene, steps = _make_seg_run_battle(monkeypatch, frames, m=2)
    # _auto_start 保持爬塔覆盖实现：6 次尝试（retry=5）全失败
    b.auto_battle_run()
    assert steps == []
    assert scene.clicks == ['paper_tostart'] * 6
    assert b._seg_state()['total_left'] == 4    # 未消耗


@pytest.mark.unit
def test_auto_battle_run_tolerates_manual_frames(monkeypatch, caplog):
    """手动帧容忍：段内不识别自动状态，手动帧（自动失效/被取消的表征）
    只是等——不误判退出，边界流转与计数不受影响；真正的退出出口是单场墙钟
    超时与 unknown 超时（各自有专项用例）。"""
    battle_auto = {'auto': True, 'battle': True, 'settle': False, 'prepare': False}
    battle_manual = {'auto': False, 'battle': True, 'settle': False, 'prepare': False}
    settle = {'auto': False, 'battle': False, 'settle': True, 'prepare': False}
    frames = [battle_manual,
              battle_manual,                      # _auto_start 首查：点击开启
              battle_auto,                        # 确认进入自动页
              settle,                             # 第 1 场结算出现 → waiting_settle
              battle_auto,                        # 回战斗界面 → count_step，seg_done=2
              battle_manual, battle_manual,       # OCR 抖动 2 帧（段内不判定，只等边界）
              battle_auto,                        # 段继续
              settle,                             # 第 2 场结算
              battle_auto,                        # 回战斗界面 → count_step，seg_done=3 出循环
              battle_auto,                        # _auto_wait_battle_ui 等到战斗界面
              battle_auto,                        # 取消首查 → 点击
              battle_manual]                      # 确认取消成功
    b, scene, steps = _make_seg_run_battle(monkeypatch, frames, m=3)
    with caplog.at_level('WARNING'):
        b.auto_battle_run()
    # 没有误判中断；两场计数齐全；开启 + 取消恰好两击
    assert not any('interrupted' in r.message for r in caplog.records)
    assert steps == [1, 1]
    assert scene.clicks == ['paper_tostart', 'paper_tostart']


@pytest.mark.unit
def test_segment_cancel_skipped_when_not_in_battle_ui(monkeypatch, caplog):
    """段尾：等不到战斗界面时跳过取消并告警（准备页/结算页上取消假成功）。"""
    battle_auto = {'auto': True, 'battle': True, 'settle': False, 'prepare': False}
    settle = {'auto': False, 'battle': False, 'settle': True, 'prepare': False}
    frames = [battle_auto,
              battle_auto,                      # _auto_start 首查已是自动页 → 成功
              settle, settle,                   # 第 1 场结算
              battle_auto]                      # 回战斗界面 → count_step，seg_done=2 出循环
    b, scene, steps = _make_seg_run_battle(monkeypatch, frames, m=2)
    # 等待战斗界面超时（真实 _auto_wait_battle_ui 在此场景耗 10s，桩掉只测分支）
    monkeypatch.setattr(b, '_auto_wait_battle_ui', lambda timeout=10.0: False)
    with caplog.at_level('WARNING'):
        b.auto_battle_run()
    assert any('cancel skipped' in r.message for r in caplog.records)
    assert scene.clicks == []                   # 未做取消点击
    assert steps == [1]                         # 段内计数照常


@pytest.mark.unit
def test_auto_battle_run_lost_in_unknown_ui(monkeypatch, caplog):
    """结算后页面流失去未知界面（既非战斗/准备也非结算），连续超过 30 帧
    告警退出，交 battle_wait/stuck 兜底，不段内死循环。"""
    battle_auto = {'auto': True, 'battle': True, 'settle': False, 'prepare': False}
    settle = {'auto': False, 'battle': False, 'settle': True, 'prepare': False}
    unknown = {'auto': False, 'battle': False, 'settle': False, 'prepare': False}
    frames = [battle_auto,
              battle_auto,                      # _auto_start 首查已是自动页 → 成功
              settle]                           # 第 1 场结算出现 → waiting_settle
    frames += [unknown] * 32                    # 未知界面持续 → 第 31 帧起触发退出
    b, scene, steps = _make_seg_run_battle(monkeypatch, frames, m=2)
    with caplog.at_level('WARNING'):
        b.auto_battle_run()
    assert any('unknown ui' in r.message for r in caplog.records)
    assert steps == []                          # 没跨过场次边界
    assert scene.clicks == []                   # 零输入退出
