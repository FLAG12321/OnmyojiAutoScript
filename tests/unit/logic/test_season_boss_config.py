"""SeasonBossConfig 配置模型校验测试"""
import pytest

from tasks.ActivityShikigami.season_boss.config import (
    SeasonBossConfig, parse_group_team, parse_monster_preset_text,
)


class TestParseGroupTeam:
    def test_valid(self):
        assert parse_group_team('3,2') == (3, 2)

    def test_minus_one(self):
        assert parse_group_team('-1,-1') is None

    def test_invalid_group(self):
        assert parse_group_team('8,1') is None

    def test_invalid_team(self):
        assert parse_group_team('1,9') is None

    def test_non_numeric(self):
        assert parse_group_team('a,b') is None

    def test_empty(self):
        assert parse_group_team('') is None


class TestParseMonsterPresetText:
    def test_multiple_lines(self):
        result = parse_monster_preset_text('雷麒麟,普通,1,1\n雷麒麟,精英,2,2')
        assert result == [
            ('雷麒麟', '普通', (1, 1), None),
            ('雷麒麟', '精英', (2, 2), None),
        ]

    def test_six_fields_with_soul(self):
        # 6段: 末两段为御魂预设
        result = parse_monster_preset_text('雷麒麟,普通,1,1,5,2')
        assert result == [('雷麒麟', '普通', (1, 1), (5, 2))]

    def test_mixed_four_and_six_fields(self):
        result = parse_monster_preset_text('雷麒麟,普通,1,1\n幽火姥姥,精英,2,2,3,4')
        assert result == [
            ('雷麒麟', '普通', (1, 1), None),
            ('幽火姥姥', '精英', (2, 2), (3, 4)),
        ]

    def test_empty_text(self):
        assert parse_monster_preset_text('') == []

    def test_blank_lines_skipped(self):
        result = parse_monster_preset_text('\n  \n雷麒麟,普通,1,1\n')
        assert result == [('雷麒麟', '普通', (1, 1), None)]

    def test_bad_format_skipped(self):
        # 只有3段, 格式错误, 跳过
        assert parse_monster_preset_text('雷麒麟,普通,1') == []

    def test_five_fields_skipped(self):
        # 5段既不是4也不是6, 跳过
        assert parse_monster_preset_text('雷麒麟,普通,1,1,5') == []

    def test_empty_monster_skipped(self):
        assert parse_monster_preset_text(',普通,1,1') == []

    def test_minus_one_preset(self):
        result = parse_monster_preset_text('雷麒麟,普通,-1,-1')
        assert result == [('雷麒麟', '普通', None, None)]

    def test_invalid_group_team(self):
        # 组8非法, 预设解析为 None, 但行本身有效(怪物名+品阶保留)
        result = parse_monster_preset_text('雷麒麟,普通,8,1')
        assert result == [('雷麒麟', '普通', None, None)]

    def test_invalid_soul_only(self):
        # 御魂段非法, 队伍预设仍保留
        result = parse_monster_preset_text('雷麒麟,普通,1,1,9,9')
        assert result == [('雷麒麟', '普通', (1, 1), None)]


class TestSeasonBossConfig:
    def test_phase_order_valid(self):
        cfg = SeasonBossConfig()
        assert cfg.phase_order_v == ['normal', 'premium']

    def test_phase_order_single(self):
        cfg = SeasonBossConfig(phase_order='premium')
        assert cfg.phase_order_v == ['premium']

    def test_phase_order_invalid(self):
        cfg = SeasonBossConfig(phase_order='boss')
        with pytest.raises(ValueError):
            cfg.phase_order_v

    def test_rank_invalid(self):
        cfg = SeasonBossConfig(monster_preset_text='雷麒麟,史诗,1,1')
        with pytest.raises(ValueError):
            cfg.valid_ranks()

    def test_rank_valid(self):
        cfg = SeasonBossConfig(monster_preset_text='雷麒麟,普通,1,1')
        cfg.valid_ranks()  # 不抛
