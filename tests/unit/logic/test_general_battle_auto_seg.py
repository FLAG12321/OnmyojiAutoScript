# -*- coding: utf-8 -*-
"""GeneralBattle 随机自动战斗段（spec 2026-09-08）的单元测试。

不触碰真实设备：flags/帧脚本驱动分支，复用 test_general_battle_reward_loop
的构造模式（object.__new__ 绕过 __init__ + monkeypatch 识别原语）。
"""
import pytest

from tasks.Component.GeneralBattle.config_general_battle import (
    GeneralBattleConfig)

from unittest.mock import MagicMock

from tasks.Component.GeneralBattle.general_battle import GeneralBattle, auto_ocr_running


@pytest.mark.unit
def test_auto_battle_config_defaults():
    """默认全关：总开关 False，段长 M=2，总数 T=4。"""
    cfg = GeneralBattleConfig()
    assert cfg.auto_battle_enable is False
    assert cfg.auto_segment_count == 2
    assert cfg.auto_total_count == 4


@pytest.mark.unit
def test_auto_battle_config_validation():
    """段长/总数的边界校验：超界拒绝。"""
    with pytest.raises(Exception):
        GeneralBattleConfig(auto_segment_count=0)   # M 下界 1
    with pytest.raises(Exception):
        GeneralBattleConfig(auto_segment_count=51)  # M 上界 50（与 T 对齐，原 10）
    with pytest.raises(Exception):
        GeneralBattleConfig(auto_total_count=0)     # T 下界 1
    with pytest.raises(Exception):
        GeneralBattleConfig(auto_total_count=201)   # T 上界 200（2026-09-09 放宽，原 50）


@pytest.mark.unit
def test_general_battle_config_embeds_auto_battle():
    """GeneralBattleConfig 自带默认关闭的自动段字段，存量配置零迁移。"""
    cfg = GeneralBattleConfig()
    assert cfg.auto_battle_enable is False


@pytest.mark.unit
def test_auto_ocr_running_semantics():
    """资产注释语义：纯数字 0-200 运行中；倍速字样/空白/越界为未运行。"""
    assert auto_ocr_running('30') is True
    assert auto_ocr_running('0') is True
    assert auto_ocr_running('200') is True
    assert auto_ocr_running(' 15 ') is True      # Single 模式可能带空白
    assert auto_ocr_running('×2') is False
    assert auto_ocr_running('X2') is False
    assert auto_ocr_running('x2') is False
    assert auto_ocr_running('201') is False      # 越界视为误读
    assert auto_ocr_running('') is False
    assert auto_ocr_running(None) is False
    assert auto_ocr_running('ab') is False


def _make_judge_battle(monkeypatch):
    """构造只够 is_auto_battle_page 用的裸实例：flags 驱动 appear，OCR 可注入。

    注意 RuleImage.name 由文件名推导（如 I_PAPER_TOSTART.name 是
    'GB_PAPER_TOSTART'），flags 键一律用资产真实 name，不硬编码。
    """
    b = object.__new__(GeneralBattle)
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
    """OR 语义（真机修订）：按钮消失即自动；按钮在时靠 OCR 复核。

    真机事故：结算页渐入期 OCR 短暂读空，AND 语义 3 帧失配误判中断，
    导致段退出后脚本在游戏仍自动时点结算——按钮消失必须单独构成"自动中"。
    """
    b = _make_judge_battle(monkeypatch)
    # 按钮在 + OCR 倍速字样：明确手动
    assert b.is_auto_battle_page() is False
    # 按钮在 + OCR 运行数字：OCR 直接证据优先，仍视为自动
    b.ocr_text = '30'
    assert b.is_auto_battle_page() is True
    # 按钮消失：主判据成立即自动中（结算页/遮挡期 OCR 读什么都一样）
    b.flags[b.I_PAPER_TOSTART.name] = False
    b.ocr_text = '30'
    assert b.is_auto_battle_page() is True
    b.ocr_text = '×2'
    assert b.is_auto_battle_page() is True
    b.ocr_text = ''
    assert b.is_auto_battle_page() is True


@pytest.mark.unit
def test_plan_random_start_range(monkeypatch):
    """随机起点区间：[0, remaining-M]；seg_len=min(M, T, remaining-1)。"""
    b = object.__new__(GeneralBattle)
    b._auto_seg = None  # 显式表示未初始化（_seg_state 用 getattr 兜底）
    calls = {}

    def _fake_randint(lo, hi):
        # 只记录第一次调用的区间（setdefault 返回元组本身，不能拿来做 or 短路）
        calls.setdefault('range', (lo, hi))
        return 3

    monkeypatch.setattr('tasks.Component.GeneralBattle.general_battle.random.randint',
                        _fake_randint)
    cfg = GeneralBattleConfig()
    cfg.auto_battle_enable = True
    cfg.auto_segment_count = 2   # M
    cfg.auto_total_count = 4     # T
    # remaining=10：起点区间 [0, 10-2]=[0,8]，seg_len=min(2, 4, 9)=2
    b.auto_battle_plan(cfg, 10)
    seg = b._seg_state()
    assert calls['range'] == (0, 8)
    assert seg['enabled'] is True and seg['planned'] is True
    assert seg['start_offset'] == 3 and seg['seg_len'] == 2
    assert seg['total_left'] == 4


@pytest.mark.unit
def test_plan_skips_when_insufficient():
    """约束矩阵：remaining < M+1 不规划；T 耗尽不再规划；未传 R/配置关直接跳过。"""
    cfg = GeneralBattleConfig()
    cfg.auto_battle_enable = True
    cfg.auto_segment_count = 2

    b = object.__new__(GeneralBattle)      # 裸实例无 _auto_seg 属性
    b.auto_battle_plan(cfg, 2)          # remaining=2 < M+1=3
    assert b._seg_state()['planned'] is False

    b2 = object.__new__(GeneralBattle)
    b2.auto_battle_plan(cfg, 10)
    b2._auto_seg['total_left'] = 0      # 手动耗尽 T
    b2._auto_seg['planned'] = False
    b2.auto_battle_plan(cfg, 10)
    assert b2._seg_state()['planned'] is False

    # 配置关：enabled 都不置位
    b3 = object.__new__(GeneralBattle)
    b3.auto_battle_plan(GeneralBattleConfig(), 10)
    assert b3._seg_state()['enabled'] is False
    # 未传 R：同上
    b4 = object.__new__(GeneralBattle)
    cfg_on = GeneralBattleConfig()
    cfg_on.auto_battle_enable = True
    b4.auto_battle_plan(cfg_on, None)
    assert b4._seg_state()['enabled'] is False


@pytest.mark.unit
def test_plan_offset_decrement_and_reached():
    """offset 随手动场递减；减到 0 的那场 _auto_seg_reached 为 True。"""
    cfg = GeneralBattleConfig()
    cfg.auto_battle_enable = True
    b = object.__new__(GeneralBattle)
    seg = b._seg_state()
    seg.update({'enabled': True, 'total_left': 4, 'planned': True,
                'start_offset': 2, 'seg_len': 2})
    # 第 1 场（offset 2→1）：未到起点
    b.auto_battle_plan(cfg, 10)
    assert seg['start_offset'] == 1 and b._auto_seg_reached() is False
    # 第 2 场（1→0）：本场即起点
    b.auto_battle_plan(cfg, 10)
    assert seg['start_offset'] == 0 and b._auto_seg_reached() is True


@pytest.mark.unit
def test_seg_reached_safe_without_state():
    """DemonRetreat 直接调 battle_wait 的路径：无 _auto_seg 时恒 False，行为不变。"""
    b = object.__new__(GeneralBattle)
    assert b._auto_seg_reached() is False
    # total_left 耗尽也不触发
    b._auto_seg = {'enabled': True, 'total_left': 0, 'planned': True,
                   'start_offset': 0, 'seg_len': 2}
    assert b._auto_seg_reached() is False


@pytest.mark.unit
def test_count_step_single_sync_point():
    """跨场计数单点同步：current_count +1、total_left -1、重挂 stuck、调钩子。"""
    b = object.__new__(GeneralBattle)
    b.device = MagicMock()
    b.current_count = 5
    seg = b._seg_state()
    seg.update({'enabled': True, 'total_left': 3})
    hook_calls = []
    b.auto_battle_count_hook = lambda: hook_calls.append(1)
    b.auto_battle_count_step()
    assert b.current_count == 6
    assert seg['total_left'] == 2
    b.device.stuck_record_add.assert_called_once_with('BATTLE_STATUS_S')
    assert hook_calls == [1]


@pytest.mark.unit
def test_remaining_now_default_none():
    """基类 remaining_now 返回 None：不知道任务限制就不做段内截断。"""
    b = object.__new__(GeneralBattle)
    assert b.auto_battle_remaining_now() is None
    b.auto_battle_count_hook()   # 基类空实现：不抛错即可


def _make_click_battle(monkeypatch, scene):
    """构造开启/取消原语用的裸实例：scene 是每帧状态的可变 dict。

    scene 键：paper（I_PAPER_TOSTART 是否出现）、auto（是否自动页）。
    点击 C_PAPER_TOSTART 的效果由用例通过改写 scene 模拟。
    注意 RuleImage.name 由文件名推导（'GB_PAPER_TOSTART'），
    appear 判断用资产真实 name，点击记录的 C_PAPER_TOSTART.name 是 'paper_tostart'。
    """
    b = object.__new__(GeneralBattle)
    b.device = MagicMock()
    b.interval_timer = {}
    b.clicks = []
    b.scene = scene
    monkeypatch.setattr(b, 'screenshot', lambda *a, **k: None)
    monkeypatch.setattr(b, 'is_auto_battle_page', lambda: scene.get('auto', False))

    def _appear(target, *a, **kw):
        return scene.get('paper', False) if target.name == b.I_PAPER_TOSTART.name else False

    def _click(target, *a, **kw):
        b.clicks.append(target.name)
        return True

    b.appear = _appear
    b.click = _click
    monkeypatch.setattr('tasks.Component.GeneralBattle.general_battle.sleep',
                        lambda *a, **k: None)
    return b


@pytest.mark.unit
def test_auto_start_click_and_confirm(monkeypatch):
    """开启：按钮出现→点击→自动页确认成功。"""
    scene = {'paper': True, 'auto': False}
    b = _make_click_battle(monkeypatch, scene)
    # 模拟点击生效：下一次查询变为自动页（按钮消失）
    b.click = lambda target, *a, **kw: (b.clicks.append(target.name),
                                       scene.update({'paper': False, 'auto': True})) and True
    assert b._auto_start() is True
    assert b.clicks == ['paper_tostart']


@pytest.mark.unit
def test_auto_start_already_on(monkeypatch):
    """已在自动页（上次残留）：不点击直接视为成功。"""
    scene = {'paper': False, 'auto': True}
    b = _make_click_battle(monkeypatch, scene)
    assert b._auto_start() is True
    assert b.clicks == []


@pytest.mark.unit
def test_auto_start_fail_retries(monkeypatch, caplog):
    """开启失败：重试 2 次（共 3 次尝试）后告警返回 False。"""
    scene = {'paper': True, 'auto': False}   # 点了也永远不开
    b = _make_click_battle(monkeypatch, scene)
    with caplog.at_level('WARNING'):
        assert b._auto_start() is False
    assert b.clicks == ['paper_tostart'] * 3
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
def test_finish_sweep_skips_when_manual(monkeypatch):
    """收尾兜底：非战斗界面（主界面等）直接返回零点击；战斗界面手动态零点击；
    战斗界面自动态补一次取消。

    真机事故：OR 语义下主界面按钮天然不在被误判"自动中"，在庭院坐标连点三次
    paper_tostart——收尾必须先确认战斗界面（is_in_real_battle）再判定。
    """
    # 非战斗界面（任务收尾时已回主界面）：OR 判定不可信，直接返回
    scene = {'paper': False, 'auto': True, 'battle': False}
    b = _make_click_battle(monkeypatch, scene)
    monkeypatch.setattr(b, 'is_in_real_battle', lambda shot=False: scene['battle'])
    b.auto_battle_finish_sweep()
    assert b.clicks == []          # 连 is_auto_battle_page 都不查，零点击
    # 战斗界面 + 手动态：判定不成立，零点击
    scene.update({'battle': True, 'paper': True, 'auto': False})
    b.auto_battle_finish_sweep()
    assert b.clicks == []
    # 战斗界面 + 自动态：补一次取消
    scene.update({'paper': False, 'auto': True})
    b.click = lambda target, *a, **kw: (b.clicks.append(target.name),
                                       scene.update({'paper': True, 'auto': False})) and True
    b.auto_battle_finish_sweep()
    assert b.clicks == ['paper_tostart']


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
        """推进一帧并应用帧副作用（auto=False 的战斗界面帧会驱动防抖计时）。"""
        if self.i + 1 < len(self.frames):
            self.i += 1
        self.cur = self.frames[self.i]


def _make_seg_run_battle(monkeypatch, frames, m=2, remaining_now=None):
    """构造 auto_battle_run 用的裸实例并接管全部识别原语。

    资产名说明：RuleImage.name 由文件名推导（I_PAPER_TOSTART.name 是
    'GB_PAPER_TOSTART'，结算系模板是 'GB_WIN' 等大写名），appear 分支
    按真实资产名匹配；点击记录的 C_PAPER_TOSTART.name 是 'paper_tostart'。
    """
    b = object.__new__(GeneralBattle)
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
            # 按钮出现 = 非自动页的战斗界面
            return scene.cur.get('battle', False) and not scene.cur.get('auto', False)
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
    monkeypatch.setattr(b, 'is_auto_battle_page', lambda: scene.cur.get('auto', False))
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
    # 这里只记录点击；_auto_start/_auto_cancel 保持真实现（它们内部用的
    # appear/is_auto_battle_page 已接管，sleep 已打补丁）
    def _click(target, *a, **kw):
        scene.clicks.append(target.name)
        return True

    b.click = _click
    monkeypatch.setattr('tasks.Component.GeneralBattle.general_battle.sleep',
                        lambda *a, **k: None)
    monkeypatch.setattr('tasks.Component.GeneralBattle.general_battle.time.time',
                        lambda: 0.0)   # 防抖的累计时长分支恒为 0（不触发），只测帧数分支
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
def test_auto_battle_run_interrupted(monkeypatch, caplog):
    """中断：战斗界面帧连续 3 帧非自动页 → 告警返回，不取消（已非自动）。"""
    battle_manual = {'auto': False, 'battle': True, 'settle': False, 'prepare': False}
    frames = [battle_manual] * 8               # 全程失配
    b, scene, steps = _make_seg_run_battle(monkeypatch, frames, m=2)
    # _auto_start 在帧 1~N 上失败 3 次重试……为测"段内中断"而非"开启失败"，
    # 先手动开启：把 _auto_start 换成恒成功
    monkeypatch.setattr(b, '_auto_start', lambda retry=2: True)
    with caplog.at_level('WARNING'):
        b.auto_battle_run()
    assert steps == []                         # 没跨过边界
    assert scene.clicks == []                  # 零输入（连取消都没有）
    assert any('Auto battle interrupted' in r.message for r in caplog.records)


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
    # _auto_start 保持真实现：3 次尝试全失败
    b.auto_battle_run()
    assert steps == []
    assert scene.clicks == ['paper_tostart'] * 3
    assert b._seg_state()['total_left'] == 4    # 未消耗


@pytest.mark.unit
def test_run_general_battle_signature_passes_remaining(monkeypatch):
    """run_general_battle 接受 remaining_count 并在序言调用规划（规划效果可观察）。"""
    from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig

    b = object.__new__(GeneralBattle)
    b.device = MagicMock()
    b.interval_timer = {}
    b.current_count = 0
    plan_calls = []

    def _plan(config, remaining):
        plan_calls.append(remaining)

    monkeypatch.setattr(b, 'auto_battle_plan', _plan)
    # 战前设置与战斗全链打桩：battle_wait 直接返回 True
    monkeypatch.setattr(b, 'battle_before', lambda *a, **k: True)
    monkeypatch.setattr(b, 'is_in_battle', lambda shot=False: False)
    monkeypatch.setattr(b, 'battle_wait', lambda *a, **k: True)
    cfg = GeneralBattleConfig()
    b.run_general_battle(config=cfg, remaining_count=12)
    assert plan_calls == [12]
    assert b.current_count == 1     # 序言计数照常
    # 不传 remaining：规划收到 None（内部跳过），行为不变
    plan_calls.clear()
    b.run_general_battle(config=cfg)
    assert plan_calls == [None]


def _make_dispatch_battle(monkeypatch, seg=None):
    """构造 run_general_battle 层分派测试用的裸实例：序言真实执行，
    battle_before/绿标判定/battle_wait/段函数全打桩并记录调用顺序。"""
    b = object.__new__(GeneralBattle)
    b.device = MagicMock()
    b.interval_timer = {}
    b.current_count = 0
    if seg is not None:
        b._auto_seg = seg
    order = []
    monkeypatch.setattr(b, 'auto_battle_plan', lambda *a, **k: order.append('plan'))
    monkeypatch.setattr(b, 'battle_before', lambda *a, **k: order.append('before') or True)
    monkeypatch.setattr(b, 'is_in_battle', lambda shot=False: False)
    monkeypatch.setattr(b, 'battle_wait', lambda *a, **k: order.append('wait') or True)
    monkeypatch.setattr(b, 'auto_battle_run', lambda: order.append('seg'))
    return b, order


@pytest.mark.unit
def test_run_general_battle_dispatches_segment_before_wait(monkeypatch):
    """到达起点：auto_battle_run 恰好调用一次，且发生在 battle_wait 之前。

    段分支必须挂在 run_general_battle（Orochi 重写了 battle_wait，
    插在基类 battle_wait 开头对重写方不可达）——顺序用调用记录断言。
    """
    seg = {'enabled': True, 'total_left': 4, 'planned': True,
           'start_offset': 0, 'seg_len': 2}
    b, order = _make_dispatch_battle(monkeypatch, seg=seg)
    cfg = GeneralBattleConfig()
    assert b.run_general_battle(config=cfg, remaining_count=10) is True
    assert order.count('seg') == 1               # 段函数恰好一次
    assert order.index('seg') < order.index('wait')   # 且先于 battle_wait
    # 段内零输入兜底：进入段前挂一次长战斗 stuck 计时
    b.device.stuck_record_add.assert_called_once_with('BATTLE_STATUS_S')


@pytest.mark.unit
def test_run_general_battle_skips_segment_without_plan(monkeypatch):
    """未规划段（含 DemonRetreat 式无 _auto_seg 直调路径）：不碰段函数。"""
    b, order = _make_dispatch_battle(monkeypatch)      # 不设 _auto_seg 属性
    assert b.run_general_battle(config=GeneralBattleConfig(), remaining_count=10) is True
    assert 'seg' not in order
    # planned=False 的已初始化状态同样不分派
    b2, order2 = _make_dispatch_battle(
        monkeypatch, seg={'enabled': True, 'total_left': 4, 'planned': False,
                          'start_offset': 0, 'seg_len': 2})
    assert b2.run_general_battle(config=GeneralBattleConfig(), remaining_count=10) is True
    assert 'seg' not in order2


@pytest.mark.unit
def test_debounce_reset_across_settle(monkeypatch, caplog):
    """修复3：进入结算页时清零中断防抖——结算后回到战斗界面 1~2 帧 OCR 抖动
    不误判中断（若防抖残留结算帧计数，2 帧抖动就凑满 3 帧阈值）。"""
    battle_auto = {'auto': True, 'battle': True, 'settle': False, 'prepare': False}
    battle_manual = {'auto': False, 'battle': True, 'settle': False, 'prepare': False}
    settle = {'auto': False, 'battle': False, 'settle': True, 'prepare': False}
    frames = [battle_manual,
              battle_manual,                      # _auto_start 首查：点击开启
              battle_auto,                        # 确认进入自动页
              settle,                             # 第 1 场结算出现 → waiting_settle（防抖清零）
              battle_auto,                        # 回战斗界面 → count_step，seg_done=2
              battle_manual, battle_manual,       # OCR 抖动 2 帧（防抖正确时不足 3 帧阈值）
              battle_auto,                        # 自动判定恢复，防抖清零，段继续
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
def test_auto_cancel_uses_fresh_frame(monkeypatch, caplog):
    """修复4：取消确认必须基于点击后的新帧——点击效果延迟到下一次截图才可见
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
def test_auto_wait_battle_ui_hit(monkeypatch):
    """修复5：轮询等待战斗界面，任一帧命中立即返回 True。"""
    b = object.__new__(GeneralBattle)
    shots = []
    monkeypatch.setattr(b, 'screenshot', lambda *a, **k: shots.append(1))
    # 第 1 帧不在战斗界面、第 2 帧命中：验证确实在轮询而非单帧判定
    monkeypatch.setattr(b, 'is_in_real_battle', lambda shot=False: len(shots) >= 2)
    monkeypatch.setattr('tasks.Component.GeneralBattle.general_battle.sleep',
                        lambda *a, **k: None)
    assert b._auto_wait_battle_ui() is True
    assert len(shots) == 2


@pytest.mark.unit
def test_auto_wait_battle_ui_timeout(monkeypatch):
    """修复5：超时返回 False，由调用方告警兜底。"""
    b = object.__new__(GeneralBattle)
    monkeypatch.setattr(b, 'screenshot', lambda *a, **k: None)
    monkeypatch.setattr(b, 'is_in_real_battle', lambda shot=False: False)

    class _FakeTimer:
        """恒 reached 的假计时器：首轮循环判定即超时，避免测试真实等待。"""

        def __init__(self, limit, count=0):
            pass

        def start(self):
            return self

        def reached(self):
            return True

    monkeypatch.setattr('tasks.Component.GeneralBattle.general_battle.Timer', _FakeTimer)
    assert b._auto_wait_battle_ui(timeout=10.0) is False


@pytest.mark.unit
def test_segment_cancel_skipped_when_not_in_battle_ui(monkeypatch, caplog):
    """修复5 段尾：等不到战斗界面时跳过取消并告警（准备页/结算页上取消假成功）。"""
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
    """修复6：结算后页面流失去未知界面（既非战斗/准备也非结算），连续超过
    30 帧告警退出，交 battle_wait/stuck 兜底，不段内死循环。"""
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
