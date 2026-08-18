# -*- coding: utf-8 -*-
"""GeneralBattle.battle_wait 奖励循环对「是否邀请队友」弹窗（I_GI_SURE）的退出行为。

背景：永生之海等队长战斗胜利 → 点击 GB_REWARD（领取奖励）后，游戏弹出
「是否邀请队友继续进行战斗？」确认框（确定按钮 = GeneralInviteAssets.I_GI_SURE）。
此前奖励循环没有把该弹窗视为奖励阶段结束态，且结算左上角 EXTRA_INFO 图标在弹窗
上仍命中（实测 score≈0.94），导致循环持续 appear_then_click(EXTRA_INFO) →
最终 GameTooManyClickError，battle_wait 永不返回，外层 check_and_invite 永远轮不到。

修复：仅在奖励循环顶部增加 I_GI_SURE 出现即 break 的判断。
本文件只验证“退出奖励循环”这一状态机行为：
- 不点击“确定”、不调用 check_and_invite（仍由任务层接管）；
- I_GI_SURE 不出现时，原有 I_REWARD / I_REWARD_GOLD / I_EXTRA_INFO 逻辑保持不变。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tasks.Component.GeneralBattle.general_battle import GeneralBattle


def _make_battle(monkeypatch, flags, after_reward_click=None):
    """构造 GeneralBattle 最小实例：以 RuleImage.name 为键的可变 appear 状态。

    flags: dict[str, bool]，各识别对象初始是否“出现”。
    after_reward_click: 点击后的回调 (clicks, flags)，用于模拟“点完 GB_REWARD 弹出弹窗”。
    返回 (battle, clicks, flags)。
    """
    b = object.__new__(GeneralBattle)
    b.device = MagicMock()
    b.interval_timer = {}
    clicks = []
    monkeypatch.setattr(b, 'screenshot', lambda *a, **k: None)
    monkeypatch.setattr(b, 'random_click_swipt', lambda *a, **k: None)
    monkeypatch.setattr(b, 'ui_click_until_disappear', lambda *a, **k: None)
    monkeypatch.setattr(b, 'reward_click_actions',
                        lambda: [SimpleNamespace(coord=lambda: (0, 0))])
    monkeypatch.setattr(b, 'wait_until_appear',
                        lambda target, **kw: flags.get(target.name, False))

    def _appear(target, *a, **kw):
        return flags.get(target.name, False)

    def _appear_then_click(target, *a, **kw):
        if flags.get(target.name, False):
            clicks.append(target.name)
            flags[target.name] = False
            if after_reward_click:
                after_reward_click(clicks, flags)
            return True
        return False

    monkeypatch.setattr(b, 'appear', _appear)
    monkeypatch.setattr(b, 'appear_then_click', _appear_then_click)
    monkeypatch.setattr('tasks.Component.GeneralBattle.general_battle.sleep',
                        lambda *a, **k: None)
    return b, clicks, flags


def _reward_click_pops_invite_dialog(clicks, flags):
    """模拟：点完 GB_REWARD 后弹出「是否邀请队友」弹窗。

    弹窗出现后结算左上角 EXTRA_INFO 仍可见（复现卡死前提），同时 I_GI_SURE 出现。
    """
    if clicks and clicks[-1] == 'GB_REWARD':
        flags['EXTRA_INFO'] = True
        flags['GI_GI_SURE'] = True


@pytest.mark.unit
def test_gi_sure_detected_exits_reward_loop(monkeypatch):
    """Case 1：奖励循环中 I_GI_SURE 出现 → 立即退出，不点击 EXTRA_INFO，battle_wait 正常返回。"""
    flags = {'GB_WIN': True, 'GB_REWARD': True,
             'EXTRA_INFO': False, 'GI_GI_SURE': False}
    b, clicks, _ = _make_battle(monkeypatch, flags,
                                after_reward_click=_reward_click_pops_invite_dialog)

    assert b.battle_wait(False) is True
    # GB_WIN（复确认）→ GB_REWARD（领奖励）→ 弹窗出现即 break，绝不点 EXTRA_INFO
    assert clicks == ['GB_WIN', 'GB_REWARD']
    assert 'EXTRA_INFO' not in clicks


@pytest.mark.unit
def test_no_gi_sure_normal_path_unchanged(monkeypatch):
    """Case 2：I_GI_SURE 不出现 → 原退出条件（I_REWARD/REWARD_GOLD/EXTRA_INFO 全消失）保持不变。"""
    flags = {'GB_WIN': True, 'GB_REWARD': True,
             'EXTRA_INFO': False, 'GI_GI_SURE': False}
    b, clicks, _ = _make_battle(monkeypatch, flags)

    assert b.battle_wait(False) is True
    # 无弹窗：点完奖励后按原条件退出（这里 EXTRA_INFO 未出现）
    assert clicks == ['GB_WIN', 'GB_REWARD']


@pytest.mark.unit
def test_extra_info_still_clicked_without_dialog(monkeypatch):
    """Case 2b：无弹窗且 EXTRA_INFO 出现时，原有 EXTRA_INFO 点击路径保持不变（不被误删）。"""
    flags = {'GB_WIN': True, 'GB_REWARD': True, 'EXTRA_INFO': True, 'GI_GI_SURE': False}
    b, clicks, _ = _make_battle(monkeypatch, flags)

    assert b.battle_wait(False) is True
    assert 'EXTRA_INFO' in clicks          # 原有 EXTRA_INFO 处理仍生效
    assert 'GI_GI_SURE' not in clicks      # 弹窗不存在时不产生任何动作
    assert clicks == ['GB_WIN', 'GB_REWARD', 'EXTRA_INFO']


@pytest.mark.unit
def test_fix_guard_placed_before_extra_info_branch():
    """修复位置回归：I_GI_SURE 退出判断必须位于 EXTRA_INFO 点击分支之前。

    若顺序被破坏（判断放在 EXTRA_INFO 之后），弹窗上 EXTRA_INFO 仍会先被点击，修复失效。
    """
    src = open('tasks/Component/GeneralBattle/general_battle.py', encoding='utf-8').read()
    assert 'Invite teammate dialog detected, exit reward loop' in src
    guard = src.index('self.appear(GeneralInviteAssets.I_GI_SURE)')
    extra_branch = src.index('self.appear_then_click(self.I_EXTRA_INFO, action=action_click, interval=1.5)')
    assert guard < extra_branch


@pytest.mark.unit
def test_eternity_sea_leader_handles_dialog_after_battle_wait():
    """Case 3（源码契约级）：battle_wait 返回后，EternitySea 外层仍走 check_and_invite。

    完整跨组件集成需 mock 整个 run_leader 的导航/建房/邀请流程，成本过高；
    这里用源码契约验证：run_leader 的 while 循环顶部先调用 check_and_invite，
    且 run_general_battle 在循环内被调用 —— battle_wait 一返回即可被 check_and_invite 接管。
    """
    src = open('tasks/EternitySea/script_task.py', encoding='utf-8').read()
    run_leader = src[src.index('def run_leader'):]
    assert 'check_and_invite' in run_leader
    assert 'run_general_battle' in run_leader
    # 循环顶部先处理弹窗，再进入下一次战斗
    assert run_leader.index('check_and_invite') < run_leader.index('run_general_battle')
