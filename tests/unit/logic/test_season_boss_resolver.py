"""怪物名+品阶 -> 战斗预设 查表纯函数测试"""
from tasks.ActivityShikigami.season_boss.resolver import (
    resolve_monster_preset, should_skip_soul_switch,
)

PRESET_TEXT = '雷麒麟,普通,1,1\n雷麒麟,精英,2,2\n幽火姥姥,精英,3,3'


class TestResolveMonsterPreset:
    def test_exact_match(self):
        assert resolve_monster_preset('雷麒麟', '精英', PRESET_TEXT, '-1,-1') == ((2, 2), None)

    def test_match_rank_distinct(self):
        # 同名不同品阶走不同预设
        assert resolve_monster_preset('雷麒麟', '普通', PRESET_TEXT, '-1,-1') == ((1, 1), None)

    def test_no_match_uses_default(self):
        assert resolve_monster_preset('未知怪', '普通', PRESET_TEXT, '4,1') == ((4, 1), None)

    def test_no_match_default_minus_one(self):
        assert resolve_monster_preset('未知怪', '普通', PRESET_TEXT, '-1,-1') == (None, None)

    def test_empty_text(self):
        assert resolve_monster_preset('雷麒麟', '普通', '', '3,2') == ((3, 2), None)

    def test_rank_not_in_text_uses_default(self):
        # 文本里没有 首领 品阶的行, 走兜底
        assert resolve_monster_preset('雷麒麟', '首领', PRESET_TEXT, '4,2') == ((4, 2), None)


class TestResolveSoulPreset:
    """6段格式: 怪物名,品阶,队伍组,队伍队,御魂组,御魂队"""
    SOUL_TEXT = '雷麒麟,普通,1,1,5,2\n雷麒麟,精英,2,2'

    def test_six_field_returns_soul(self):
        assert resolve_monster_preset('雷麒麟', '普通', self.SOUL_TEXT, '-1,-1') == ((1, 1), (5, 2))

    def test_four_field_soul_falls_back_to_default(self):
        # 命中行只有4段(未配御魂), 御魂回落到兜底
        assert resolve_monster_preset('雷麒麟', '精英', self.SOUL_TEXT, '-1,-1', '6,3') == ((2, 2), (6, 3))

    def test_four_field_no_default_soul_is_none(self):
        assert resolve_monster_preset('雷麒麟', '精英', self.SOUL_TEXT, '-1,-1', '-1,-1') == ((2, 2), None)

    def test_no_match_uses_both_defaults(self):
        assert resolve_monster_preset('未知怪', '普通', self.SOUL_TEXT, '4,1', '7,4') == ((4, 1), (7, 4))

    def test_invalid_soul_group_is_none(self):
        # 御魂组8越界 -> 解析为 None, 队伍预设仍有效
        assert resolve_monster_preset('鬼王', '普通', '鬼王,普通,1,1,8,1', '-1,-1') == ((1, 1), None)


class TestShouldSkipSoulSwitch:
    """御魂切换是否可跳过: ①无御魂目标 ②与上次已切一致"""

    def test_soul_none_skips(self):
        # 无可切御魂目标(队伍预设也未配, soul 仍为 None) -> 跳过
        assert should_skip_soul_switch(None, None) is True

    def test_same_group_still_switch(self):
        # 回归: 御魂与队伍同组(如 team=(2,1) soul=(2,1))也必须进式神录切御魂;
        # soul=None 时 mixin 会先让御魂跟随队伍预设, 故落到这里仍要切
        assert should_skip_soul_switch((2, 1), None) is False

    def test_same_as_last_skips(self):
        # 御魂与上次已切一致 -> 跳过, 同次任务内不重复切换
        assert should_skip_soul_switch((5, 2), (5, 2)) is True

    def test_need_switch(self):
        # 御魂与上次不同 -> 需要进式神录
        assert should_skip_soul_switch((5, 2), (6, 3)) is False

    def test_need_switch_no_last(self):
        # 上次未切过 -> 需要进式神录
        assert should_skip_soul_switch((5, 2), None) is False
