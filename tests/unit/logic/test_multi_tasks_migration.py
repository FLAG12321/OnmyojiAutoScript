# This Python file uses the following encoding: utf-8
"""三个旧多账号任务合并进 multi_tasks 的 legacy 迁移测试。

覆盖：三组参数搬入、空条目滤除并重编号、三节点全缺时建出完整节点、
幂等、scheduler 不继承（enable 保持 False）。
"""
import copy

import pytest

from module.config.config_validation import (
    DEFAULT_CONFIG_PROFILE,
    normalize_legacy_config,
    validate_persisted_config,
)


def _legacy_raw():
    """构造一份含三个旧任务节点的 raw（只放迁移关心的字段）。"""
    return {
        'multi_acc_exp': {
            'scheduler': {'enable': True, 'priority': 3},
            'multi_acc_exp_config': {
                'sup_account_count': 3,
                'total_exp_farming_enable': True,
                'total_buff_exp_50_click': True,
                'total_buff_exp_100_click': False,
                'need_login': True,
                'need_login_time': '2023-01-01 00:00:00',
            },
            # 「有效、空、有效」夹心：迁移必须滤掉空条目并重新编号
            'sup_account_list_1': {
                'character': '甲', 'svr': 's1', 'account': 'a@x.com',
                'account_alias': '', 'apple_or_android': True,
                'last_complete_time': '2023-01-01 00:00:00',
                'exp_farming_enable': True,
                'buff_exp_50_click': True,
                'buff_exp_100_click': True,
            },
            'sup_account_list_2': {
                'character': '', 'svr': '', 'account': '',
                'account_alias': '', 'apple_or_android': True,
                'last_complete_time': '2023-01-01 00:00:00',
                'exp_farming_enable': True,
                'buff_exp_50_click': False,
                'buff_exp_100_click': False,
            },
            'sup_account_list_3': {
                'character': '乙', 'svr': 's2', 'account': 'b@x.com',
                'account_alias': 'b#bb', 'apple_or_android': False,
                'last_complete_time': '2024-05-06 07:08:09',
                'exp_farming_enable': False,
                'buff_exp_50_click': False,
                'buff_exp_100_click': True,
            },
        },
        'multi_account_sign_in': {
            'scheduler': {'enable': True},
            'account_config_selection': {
                'config_e4ba066e5f51a6ac': True,
                'config_8c63c49c56eaea33': False,
            },
        },
        'multi_activity_shikigami': {
            'scheduler': {'enable': True},
            'multi_activity_shikigami_config': {'account_characters': 'js1瑶光,js2瑶光'},
        },
    }


@pytest.mark.unit
def test_migration_moves_all_three_param_groups():
    result = normalize_legacy_config(_legacy_raw(), 'oas1')

    # 三个旧节点整体消失
    assert 'multi_acc_exp' not in result
    assert 'multi_account_sign_in' not in result
    assert 'multi_activity_shikigami' not in result

    task = result['multi_tasks']
    # 账号表：只留 character 非空的两条，重新编号 1..2，且只保留 AccountInfo 字段
    assert task['sup_account_list_1']['character'] == '甲'
    assert task['sup_account_list_2']['character'] == '乙'
    assert 'sup_account_list_3' not in task
    assert 'exp_farming_enable' not in task['sup_account_list_1']
    assert 'buff_exp_50_click' not in task['sup_account_list_1']
    assert task['sup_account_list_2']['account_alias'] == 'b#bb'
    assert task['sup_account_list_2']['apple_or_android'] is False
    # count 由实际搬入条目数决定，不沿用旧的 3
    assert task['multi_tasks_config']['sup_account_count'] == 2
    # 勾选项整块搬入，哈希键不变
    assert task['account_config_selection'] == {
        'config_e4ba066e5f51a6ac': True,
        'config_8c63c49c56eaea33': False,
    }
    # 角色名串搬入
    assert task['multi_tasks_config']['account_characters'] == 'js1瑶光,js2瑶光'
    # scheduler 不继承：迁移不写 enable，留给模型默认 False
    assert 'enable' not in task.get('scheduler', {})


@pytest.mark.unit
def test_migration_is_idempotent():
    """LEGACY_ALIAS_MIGRATIONS 注册 3 条同一函数，normalize 会调用 3 次。"""
    once = normalize_legacy_config(_legacy_raw(), 'oas1')
    twice = normalize_legacy_config(copy.deepcopy(once), 'oas1')
    assert twice == once


@pytest.mark.unit
def test_migration_pads_one_default_entry_when_no_valid_account():
    raw = _legacy_raw()
    # 只留一个空条目：滤完为 0 条，必须补一条默认空条目并令 count=1（模型 ge=1）
    del raw['multi_acc_exp']['sup_account_list_1']
    del raw['multi_acc_exp']['sup_account_list_3']

    task = normalize_legacy_config(raw, 'oas1')['multi_tasks']
    assert task['multi_tasks_config']['sup_account_count'] == 1
    assert task['sup_account_list_1']['character'] == ''
    assert 'sup_account_list_2' not in task


@pytest.mark.unit
def test_migration_creates_account_list_even_without_multi_acc_exp():
    """只有签到节点时也必须补齐账号表不变量，否则 counted 校验会报 count 缺失。"""
    raw = _legacy_raw()
    del raw['multi_acc_exp']

    task = normalize_legacy_config(raw, 'oas1')['multi_tasks']
    assert task['multi_tasks_config']['sup_account_count'] == 1
    assert task['sup_account_list_1']['character'] == ''


@pytest.mark.unit
def test_migration_builds_full_node_when_no_legacy_node():
    """三节点全缺时必须建出完整的 multi_tasks，不能留空。

    缺 multi_tasks 节点会让严格校验报 "changed members during canonicalization"：
    expected 对缺失父节点算出 {}，而 model_validate 填默认值后 serializer 会吐出
    sup_account_list_1，两者不一致。
    """
    task = normalize_legacy_config({'config_name': 'oas1'}, 'oas1')['multi_tasks']
    assert task['multi_tasks_config']['sup_account_count'] == 1
    assert task['sup_account_list_1']['character'] == ''


@pytest.mark.unit
def test_migration_returns_early_when_already_migrated():
    """已是新形状（有 multi_tasks、无旧节点）时不得改动已搬入的账号表。"""
    migrated = normalize_legacy_config(_legacy_raw(), 'oas1')
    again = normalize_legacy_config(copy.deepcopy(migrated), 'oas1')

    assert again['multi_tasks']['sup_account_list_1']['character'] == '甲'
    assert again['multi_tasks']['multi_tasks_config']['sup_account_count'] == 2


@pytest.mark.unit
def test_migration_rules_registry_covers_multi_tasks():
    """MIGRATION_RULES 与 DYNAMIC_PATH_SET_REGISTRY 必须同步。

    漏登记不会报错，只会让首次迁移静默跳过 raise 归一化（count 不会被上调到
    成员数），因此用测试钉住。项目里没有现成的同步门禁。
    """
    from module.config.config_generation import MIGRATION_RULES

    keys = {entry.key for entry in DEFAULT_CONFIG_PROFILE.dynamic_path_sets}
    assert 'multi_tasks.sup_account_list' in keys
    assert MIGRATION_RULES['multi_tasks.sup_account_list'] == 'raise'
    assert 'multi_acc_exp.sup_account_list' not in MIGRATION_RULES
    assert 'multi_acc_exp.sup_account_list' not in keys


@pytest.mark.unit
def test_migrated_legacy_config_passes_strict_validation():
    """把真实 template 退化成旧形状后走完整严格校验，确认迁移产物是 canonical 的。"""
    import json
    from pathlib import Path

    raw = json.loads((Path.cwd() / 'config' / 'template.json').read_text(encoding='utf-8'))
    raw['meta_demon'].pop('md_strategies_1', None)
    # template 已是新形状，这里把 multi_tasks 换回三个旧节点模拟老用户配置
    raw.pop('multi_tasks', None)
    raw.update(_legacy_raw())

    model, canonical = validate_persisted_config(raw, 'oas1', DEFAULT_CONFIG_PROFILE)
    assert canonical['multi_tasks']['multi_tasks_config']['sup_account_count'] == 2
    assert model.multi_tasks.scheduler.enable is False
    assert [a.character for a in model.multi_tasks.sup_account_list] == ['甲', '乙']
