# This Python file uses the following encoding: utf-8
# @brief    任务开始前切号功能的纯逻辑单元测试
# @note     覆盖 SwitchAccountOnStart mixin 逻辑、两个任务的账号列表配置序列化往返

from types import SimpleNamespace
from unittest import mock

from tasks.Component.SwitchAccount.switch_account import SwitchAccountOnStart
from tasks.Component.SwitchAccount.switch_account_config import AccountInfo
from tasks.AbyssShadows.config import AbyssShadows
from tasks.BondlingFairyland.config import BondlingFairyland


class FakeTask(SwitchAccountOnStart):
    def __init__(self, config, device):
        self.config = config
        self.device = device


class TestSwitchAccountOnStart:
    def test_disabled_returns_true(self):
        # 未启用切号，直接通过
        task = FakeTask(None, None)
        assert task.switch_account_on_start(SimpleNamespace(enable=False), []) is True

    def test_enabled_invalid_account_returns_false(self):
        # 账号没填 character，is_valid() 为 False，中止
        task = FakeTask(None, None)
        assert task.switch_account_on_start(SimpleNamespace(enable=True), [AccountInfo()]) is False

    def test_switch_failure_returns_false(self):
        with mock.patch('tasks.Component.SwitchAccount.switch_account.SwitchAccount') as SA:
            SA.return_value.switchAccount.return_value = False
            task = FakeTask(None, None)
            acc = AccountInfo(character='测试号', svr='立秋夕烛')
            assert task.switch_account_on_start(SimpleNamespace(enable=True), [acc]) is False

    def test_switch_success_returns_true(self):
        with mock.patch('tasks.Component.SwitchAccount.switch_account.SwitchAccount') as SA:
            SA.return_value.switchAccount.return_value = True
            task = FakeTask(None, None)
            acc = AccountInfo(character='测试号', svr='立秋夕烛')
            assert task.switch_account_on_start(SimpleNamespace(enable=True), [acc]) is True
            # 用任务的 config/device 构造 SwitchAccount
            SA.assert_called_once_with(None, None, acc)


class TestAbyssShadowsSwitchAccountConfig:
    def test_default_disabled(self):
        model = AbyssShadows()
        assert model.switch_account_config.enable is False
        # 校验器补齐一个空账号占位
        assert len(model.switch_account_list) == 1

    def test_load_flat_key_rebuilds_list(self):
        model = AbyssShadows(**{
            'switch_account_config': {'enable': True},
            'switch_account_list_1': {
                'character': '测试号', 'svr': '立秋夕烛',
                'account': 'test@163.com', 'account_alias': '',
                'apple_or_android': True,
            },
        })
        assert model.switch_account_config.enable is True
        assert len(model.switch_account_list) == 1
        assert model.switch_account_list[0].character == '测试号'
        assert model.switch_account_list[0].svr == '立秋夕烛'

    def test_dump_flattens_back_to_single(self):
        model = AbyssShadows(**{
            'switch_account_list_1': {'character': '测试号', 'svr': '服', 'account': 'a@b.com'},
        })
        dumped = model.model_dump()
        assert dumped['switch_account_list_1']['character'] == '测试号'
        assert 'switch_account_list' not in dumped
        assert 'switch_account_list_2' not in dumped

    def test_extra_account_truncated_to_first(self):
        model = AbyssShadows(**{
            'switch_account_list_1': {'character': '第一个号', 'svr': '服1'},
            'switch_account_list_2': {'character': '第二个号', 'svr': '服2'},
        })
        assert len(model.switch_account_list) == 1
        assert model.switch_account_list[0].character == '第一个号'


class TestBondlingFairylandSwitchAccountConfig:
    def test_default_disabled(self):
        model = BondlingFairyland()
        assert model.switch_account_config.enable is False
        assert len(model.switch_account_list) == 1

    def test_round_trip(self):
        model = BondlingFairyland(**{
            'switch_account_list_1': {'character': '测试号', 'svr': '服', 'account': 'a@b.com'},
        })
        assert model.switch_account_list[0].character == '测试号'
        dumped = model.model_dump()
        assert dumped['switch_account_list_1']['character'] == '测试号'

    def test_dynamic_hide_fields_stay_hidden_under_hide_context(self):
        # wrap 序列化器必须保留 hide 上下文，battle_config 的隐藏字段不能外泄
        model = BondlingFairyland()
        dumped = model.model_dump(context={'hide': True})
        battle = dumped['battle_config']
        for field in ('lock_team_enable', 'preset_enable', 'preset_group', 'preset_team'):
            assert battle.get(field) == 0xABCDEF


def test_abyss_shadows_task_inherits_mixin():
    from tasks.AbyssShadows.script_task import ScriptTask as AbyssTask
    assert issubclass(AbyssTask, SwitchAccountOnStart)


def test_bondling_fairyland_task_inherits_mixin():
    from tasks.BondlingFairyland.script_task import ScriptTask as BondTask
    assert issubclass(BondTask, SwitchAccountOnStart)
