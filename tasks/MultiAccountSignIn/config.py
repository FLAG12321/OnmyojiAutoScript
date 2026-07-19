# This Python file uses the following encoding: utf-8
import hashlib
import json
from pathlib import Path

from pydantic import Field, create_model

from tasks.Component.config_base import ConfigBase, TimeDelta
from tasks.Component.config_scheduler import Scheduler


def _count_multi_daily_accounts(data: dict) -> int:
    """统计配置文件中 MultiDailyAltAcc 的账号条目数。"""
    task_data = data.get('multi_daily_alt_acc', {})
    return sum(
        1
        for key, value in task_data.items()
        if key.startswith('sup_account_list_')
        and isinstance(value, dict)
        and value.get('character')
    )


def _discover_account_configs() -> dict[str, tuple[str, int]]:
    """扫描正式配置文件，并返回“配置字段名 -> (配置名, 账号数)”映射。"""
    discovered = {}
    for config_file in sorted((Path.cwd() / 'config').glob('*.json')):
        try:
            with config_file.open('r', encoding='utf-8') as file:
                account_count = _count_multi_daily_accounts(json.load(file))
        except (OSError, json.JSONDecodeError):
            continue

        # 只列出实际包含 MultiDailyAltAcc 账号的配置，避免显示无关配置。
        if account_count == 0:
            continue
        # 使用原始配置名的稳定摘要生成字段，避免大小写或特殊字符规范化后发生碰撞。
        digest = hashlib.sha256(config_file.stem.encode('utf-8')).hexdigest()[:16]
        field_name = f'config_{digest}'
        discovered[field_name] = (config_file.stem, account_count)
    return discovered


ACCOUNT_CONFIGS = _discover_account_configs()

# 每个配置文件生成一个独立开关，标题保留文件名，说明中列出账号总数。
AccountConfigSelection = create_model(
    'AccountConfigSelection',
    __base__=ConfigBase,
    **{
        field_name: (
            bool,
            Field(
                default=False,
                title=config_name,
                description=f'{config_name} 账号总数：{account_count}',
            ),
        )
        for field_name, (config_name, account_count) in ACCOUNT_CONFIGS.items()
    },
)


class MultiAccountSignInScheduler(Scheduler):
    priority: int = Field(default=5, description='priority_help')
    success_interval: TimeDelta = Field(default=TimeDelta(days=1), description='success_interval_help')
    failure_interval: TimeDelta = Field(default=TimeDelta(hours=2), description='failure_interval_help')


class MultiAccountSignIn(ConfigBase):
    scheduler: MultiAccountSignInScheduler = Field(default_factory=MultiAccountSignInScheduler)
    account_config_selection: AccountConfigSelection = Field(default_factory=AccountConfigSelection)
