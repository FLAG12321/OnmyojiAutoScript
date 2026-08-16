# This Python file uses the following encoding: utf-8
from types import SimpleNamespace

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
            {'name': 'leader_instance', 'type': 'string', 'value': value},
            {'name': 'epoch', 'type': 'string', 'value': ''},
        ],
    }


def test_leader_instance_injects_active_instances_as_enum():
    owner = SimpleNamespace(store=FakeStore(['OAS1', 'OAS2', 'OAS3']), config_name='OAS1')
    result = leader_item('OAS2')

    Config._inject_orochi_leader_options(owner, result)

    item = result['orochi_config'][1]
    assert item['type'] == 'enum'
    assert item['enumEnum'] == ['', 'OAS2', 'OAS3']
    # Epoch 不参与动态注入，前端仍按普通文本输入框渲染。
    assert result['orochi_config'][2]['type'] == 'string'


def test_leader_instance_keeps_saved_missing_instance_visible():
    owner = SimpleNamespace(store=FakeStore(['OAS1', 'OAS3']), config_name='OAS1')
    result = leader_item('OAS2')

    Config._inject_orochi_leader_options(owner, result)

    assert result['orochi_config'][1]['enumEnum'] == ['', 'OAS3', 'OAS2']

