# This Python file uses the following encoding: utf-8
from types import SimpleNamespace

from tasks.Orochi.config import Orochi, TeamMode
from module.config.config import Config


class FakeStore:
    """只提供动态下拉需要的有效实例枚举。"""

    def __init__(self, names):
        self.names = names

    def active_config_names(self):
        return list(self.names)


def leader_item(value='') -> dict:
    return {
        'orochi_config': [
            {'name': 'user_status', 'type': 'enum'},
        ],
        'team_config': [
            {'name': 'team_mode', 'type': 'enum', 'value': 'team'},
            {'name': 'leader_instance', 'type': 'string', 'value': value},
            {'name': 'epoch', 'type': 'string', 'value': ''},
        ],
    }


def test_leader_instance_injects_active_instances_as_enum():
    owner = SimpleNamespace(store=FakeStore(['OAS1', 'OAS2', 'OAS3']), config_name='OAS1')
    result = leader_item('OAS2')

    Config._inject_orochi_leader_options(owner, result)

    item = result['team_config'][1]
    assert item['type'] == 'enum'
    assert item['enumEnum'] == ['', 'OAS2', 'OAS3']
    # Epoch 不参与动态注入，前端仍按普通文本输入框渲染。
    assert result['team_config'][2]['type'] == 'string'


def test_leader_instance_keeps_saved_missing_instance_visible():
    owner = SimpleNamespace(store=FakeStore(['OAS1', 'OAS3']), config_name='OAS1')
    result = leader_item('OAS2')

    Config._inject_orochi_leader_options(owner, result)

    assert result['team_config'][1]['enumEnum'] == ['', 'OAS3', 'OAS2']


def test_legacy_team_fields_migrate_to_team_config():
    """旧版本把组队字段放在 orochi_config，加载时自动迁移到 team_config。"""
    model = Orochi.model_validate({
        'orochi_config': {
            'user_status': 'member',
            'leader_instance': 'OAS2',
            'epoch': 'abc',
            'limit_time': '00:30:00',
            'limit_count': 30,
            'total_limit_time': '04:00:00',
            'total_limit_count': 200,
        },
    })

    # 旧队长/队员身份默认视为脚本组队流程，身份仍留在副本配置
    assert model.team_config.team_mode == TeamMode.TEAM
    assert model.orochi_config.user_status.value == 'member'
    # 组队新增字段迁入独立配置，单轮限制保留在副本设置
    assert model.team_config.leader_instance == 'OAS2'
    assert model.team_config.epoch == 'abc'
    assert model.team_config.total_limit_count == 200
    assert model.orochi_config.limit_count == 30


def test_alone_role_stays_solo_mode():
    """旧 alone 身份迁移后不启用脚本组队流程。"""
    model = Orochi.model_validate({
        'orochi_config': {'user_status': 'alone'},
    })

    assert model.team_config.team_mode == TeamMode.ALONE
    assert model.orochi_config.user_status.value == 'alone'


def test_legacy_enable_team_bool_migrates_to_team_mode():
    """旧版 enable_team 布尔开关迁移为 team_mode 下拉框值。"""
    model = Orochi.model_validate({
        'team_config': {'enable_team': True},
    })

    assert model.team_config.team_mode == TeamMode.TEAM


def test_solo_mode_hides_team_config_except_mode():
    """单人模式只保留组队模式下拉框，其余组队配置不下发前端。"""
    owner = SimpleNamespace(store=FakeStore(['OAS1', 'OAS2']), config_name='OAS1')
    result = {
        'team_config': [
            {'name': 'team_mode', 'type': 'enum', 'value': 'alone'},
            {'name': 'leader_instance', 'type': 'string', 'value': 'OAS2'},
            {'name': 'total_limit_count', 'type': 'integer', 'value': 300},
        ],
    }

    Config._hide_orochi_team_config(owner, result)

    assert [item['name'] for item in result['team_config']] == ['team_mode']


def test_team_mode_keeps_full_team_config():
    """组队模式保留全部组队配置。"""
    owner = SimpleNamespace(store=FakeStore(['OAS1', 'OAS2']), config_name='OAS1')
    result = {
        'team_config': [
            {'name': 'team_mode', 'type': 'enum', 'value': 'team'},
            {'name': 'leader_instance', 'type': 'string', 'value': 'OAS2'},
            {'name': 'total_limit_count', 'type': 'integer', 'value': 300},
        ],
    }

    Config._hide_orochi_team_config(owner, result)

    assert len(result['team_config']) == 3
