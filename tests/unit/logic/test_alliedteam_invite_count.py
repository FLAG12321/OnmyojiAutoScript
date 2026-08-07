# This Python file uses the following encoding: utf-8
"""同心队邀请人数阈值配置化测试：字段默认/约束与账号级拷贝链路完整。"""
from pathlib import Path

import pytest
from pydantic import ValidationError

from tasks.DailyAltAcc.config import DailyAltAccConfig
from tasks.MultiDailyAltAcc.config import ExtendedAccountInfo


@pytest.mark.unit
def test_invite_count_default_and_constraints():
    """阈值默认 2，且仅接受 1/2，防止填 3 后永远等不到人。"""
    assert DailyAltAccConfig().alliedteam_invite_count == 2
    DailyAltAccConfig(alliedteam_invite_count=1)
    DailyAltAccConfig(alliedteam_invite_count=2)
    with pytest.raises(ValidationError):
        DailyAltAccConfig(alliedteam_invite_count=0)
    with pytest.raises(ValidationError):
        DailyAltAccConfig(alliedteam_invite_count=3)


@pytest.mark.unit
def test_multi_account_invite_count_field_exists():
    """多账号模式的账号级配置也要有该字段，否则每个小号无法独立控制。"""
    assert ExtendedAccountInfo.model_fields['alliedteam_invite_count'].default == 2
    ExtendedAccountInfo(alliedteam_invite_count=1)


@pytest.mark.unit
def test_invite_count_copy_chain_complete():
    """阈值必须走完整的账号级→任务级拷贝链路，漏一环会静默退回默认值。"""
    source = Path('tasks/MultiDailyAltAcc/DailyAltAccEx.py').read_text(encoding='utf-8')
    assert 'config.daily_alt_acc_config.alliedteam_invite_count = self.account_info.alliedteam_invite_count' in source

    script = Path('tasks/MultiDailyAltAcc/script_task.py').read_text(encoding='utf-8')
    assert 'config.alliedteam_invite_count = account_info.alliedteam_invite_count' in script


@pytest.mark.unit
def test_alliedteam_uses_config_threshold():
    """邀请循环必须用配置值替换硬编码 2，而不是沿用魔法数字。"""
    source = Path('tasks/DailyAltAcc/alliedteam.py').read_text(encoding='utf-8')
    assert 'alliedteam_invite_count' in source
    assert 'match_all_any(self.device.image)) < alliedteam_invite_count' in source
