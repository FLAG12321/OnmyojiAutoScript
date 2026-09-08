# -*- coding: utf-8 -*-
"""ActivityShikigami 配置裁剪测试：ap100 模式删除（字段与合法值）、
ap20/大富翁/修行合训前端隐藏（dynamic_hide 哨兵 + 组级 pop），及落盘值不受隐藏影响。"""
import pytest

from tasks.ActivityShikigami.config import (ActivityShikigami, SwitchSoulConfig,
                                            GeneralBattleConfig as ClimbBattleConfig)
from tasks.Component.BaseActivity.config_activity import GeneralClimb

# ap100 已删除的键（模型层不再存在）
AP100_GONE_FIELDS = {
    GeneralClimb: ('ap100_limit',),
    SwitchSoulConfig: ('enable_switch_ap100', 'ap100_group_team'),
    ClimbBattleConfig: ('enable_ap100_preset', 'enable_ap100_anti_detect'),
}

# 前端隐藏的键（模型保留，hide 上下文序列化为哨兵值）
HIDDEN_FIELDS = {
    GeneralClimb: ('ap20_limit', 'pass_monopoly_limit', 'season_boss_limit'),
    SwitchSoulConfig: ('enable_switch_ap20', 'ap20_group_team',
                       'enable_switch_pass_monopoly', 'pass_monopoly_group_team'),
    ClimbBattleConfig: ('enable_ap20_preset', 'enable_ap20_anti_detect',
                        'enable_pass_monopoly_preset', 'enable_pass_monopoly_anti_detect'),
}

# 当前活动存在的三种模式：配置必须保留可见
VISIBLE_FIELDS = {
    GeneralClimb: ('pass_limit', 'ap_limit', 'boss_limit'),
    SwitchSoulConfig: ('enable_switch_pass', 'enable_switch_ap', 'enable_switch_boss'),
    ClimbBattleConfig: ('enable_pass_preset', 'enable_ap_preset', 'enable_boss_preset'),
}


@pytest.mark.unit
@pytest.mark.parametrize('model_cls,fields', AP100_GONE_FIELDS.items())
def test_ap100_fields_removed(model_cls, fields):
    """ap100 未实现已删除：模型上不再有对应字段。"""
    for field in fields:
        assert field not in model_cls.model_fields


@pytest.mark.unit
def test_ap100_not_valid_run_sequence():
    """ap100 不再是 run_sequence 合法值（校验会拒绝，存量需迁移）。"""
    with pytest.raises(ValueError):
        GeneralClimb(run_sequence='pass,ap100,ap').run_sequence_v


@pytest.mark.unit
def test_run_sequence_default_without_ap100():
    """默认序列只含当前活动存在的三种入口。"""
    assert GeneralClimb().run_sequence_v == ['pass', 'boss', 'ap']


@pytest.mark.unit
@pytest.mark.parametrize('model_cls,fields', HIDDEN_FIELDS.items())
def test_hidden_fields_serve_sentinel(model_cls, fields):
    """隐藏字段在 hide 上下文（下发前端）序列化为哨兵值 0xABCDEF。"""
    dumped = model_cls().model_dump(context={'hide': True})
    for field in fields:
        assert dumped[field] == 0xABCDEF


@pytest.mark.unit
@pytest.mark.parametrize('model_cls,fields', HIDDEN_FIELDS.items())
def test_hidden_fields_keep_real_value_on_disk(model_cls, fields):
    """落盘序列化（无 hide 上下文）不受隐藏影响，用户存量值原样保留。"""
    dumped = model_cls().model_dump()
    for field in fields:
        assert dumped[field] != 0xABCDEF


@pytest.mark.unit
@pytest.mark.parametrize('model_cls,fields', VISIBLE_FIELDS.items())
def test_visible_fields_not_hidden(model_cls, fields):
    """pass/ap/boss 三种现存模式的配置不被误伤。"""
    dumped = model_cls().model_dump(context={'hide': True})
    for field in fields:
        assert dumped[field] != 0xABCDEF


@pytest.mark.unit
def test_script_task_drops_hidden_groups_and_fields():
    """script_task() 下发前端的结构：season_boss 整组摘掉、隐藏字段不出现、
    现存模式字段保留。"""
    from module.config.config_model import ConfigModel
    result = ConfigModel().script_task('ActivityShikigami')
    # 修行合训组整体隐藏
    assert 'season_boss' not in result
    # 隐藏字段与已删字段一律不出现
    for group, names in {
        'general_climb': {'ap100_limit', 'ap20_limit', 'pass_monopoly_limit', 'season_boss_limit'},
        'switch_soul_config': {'enable_switch_ap100', 'ap100_group_team', 'enable_switch_ap20',
                               'ap20_group_team', 'enable_switch_pass_monopoly', 'pass_monopoly_group_team'},
        'general_battle': {'enable_ap100_preset', 'enable_ap100_anti_detect', 'enable_ap20_preset',
                           'enable_ap20_anti_detect', 'enable_pass_monopoly_preset',
                           'enable_pass_monopoly_anti_detect'},
    }.items():
        got = {item['name'] for item in result[group]}
        assert not (got & names), f'{group} 泄漏了隐藏字段: {got & names}'
    # 现存模式的字段仍在（含 run_sequence 与自动段字段）
    climb_names = {item['name'] for item in result['general_climb']}
    assert {'pass_limit', 'ap_limit', 'boss_limit', 'run_sequence'} <= climb_names
    battle_names = {item['name'] for item in result['general_battle']}
    assert {'auto_battle_enable', 'auto_segment_count', 'auto_total_count'} <= battle_names


@pytest.mark.unit
def test_season_boss_config_still_usable():
    """修行合训配置类保留（仅前端隐藏）：run_sequence 手写 season_boss 仍合法可跑。"""
    assert GeneralClimb(run_sequence='season_boss').run_sequence_v == ['season_boss']
