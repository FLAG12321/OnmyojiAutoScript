# -*- coding: utf-8 -*-
"""战斗结算随机点击区域收敛：胜利画面固定右侧，奖励页仅「底部中央 + 右侧」。

背景：原逻辑在结算阶段随机取点——胜利画面于 上(C_WIN_1)/左(C_WIN_2)/右(C_WIN_3)
三区域随机，奖励页于 下(C_REWARD_1)/左(C_REWARD_2)/右(C_REWARD_3)三区域随机，
合计覆盖 上/左/右/下 四个方位。其中上方与左侧不符合人类点击习惯（且上横条压在
结算统计图标带附近），统一禁用：胜利画面固定 C_WIN_3，奖励页保留 [C_REWARD_1,
C_REWARD_3] 二选一。落点的随机性由拟人化层在区域内采样提供。

本文件验证：
- 实例级：GeneralBattle.reward_click_actions() 默认值不含左侧区域；
- 源码契约级：各任务私有副本（battle_wait 复制体）不再出现上/左随机列表。
"""
import pytest

from tasks.Component.GeneralBattle.general_battle import GeneralBattle

# 胜利画面随机三选一必须全部移除（组件本体 + 各任务私有副本）
WIN_FILES = [
    'tasks/Component/GeneralBattle/general_battle.py',
    'tasks/Orochi/script_task.py',
    'tasks/FallenSun/script_task.py',
    'tasks/MasterDisciple/script_task.py',
    'tasks/Plotline/script_task.py',
]

# 奖励页随机列表不得包含左侧区域（C_REWARD_2）
# 注：Plotline 不在此列——其 click_dialogue_high 是剧情对话加速点击，不属于结算场景
REWARD_FILES = [
    'tasks/Component/GeneralBattle/general_battle.py',
    'tasks/Orochi/script_task.py',
    'tasks/FallenSun/script_task.py',
    'tasks/BondlingFairyland/battle.py',
]

# reward_click_actions 覆盖方法不得再返回含左侧区域的列表
OVERRIDE_FILES = [
    'tasks/Exploration/base.py',
    'tasks/Plotline/script_task.py',
]


def _src(path: str) -> str:
    with open(path, encoding='utf-8') as f:
        return f.read()


@pytest.mark.unit
def test_reward_click_actions_default_regions():
    """默认 reward_click_actions() 仅返回底部中央(reward_1)+右侧(reward_3)。"""
    b = object.__new__(GeneralBattle)
    names = [a.name for a in b.reward_click_actions()]
    assert names == ['reward_1', 'reward_3']


@pytest.mark.unit
def test_win_click_regions_right_only():
    """胜利画面不得再出现上/左区域的随机选择列表。"""
    for path in WIN_FILES:
        assert 'random.choice([self.C_WIN_1' not in _src(path), \
            f'{path} 胜利画面仍存在上/左随机区域'


@pytest.mark.unit
def test_reward_click_regions_exclude_left():
    """奖励页随机列表不得包含左侧区域（C_REWARD_2）。"""
    for path in REWARD_FILES:
        assert 'self.C_REWARD_1, self.C_REWARD_2' not in _src(path), \
            f'{path} 奖励页随机列表仍包含左侧区域'


@pytest.mark.unit
def test_override_reward_actions_exclude_left():
    """Exploration/Plotline 的 reward_click_actions 覆盖不得返回含左侧区域的列表。"""
    for path in OVERRIDE_FILES:
        assert 'return [self.C_REWARD_2' not in _src(path), \
            f'{path} 覆盖方法仍返回左侧区域'
