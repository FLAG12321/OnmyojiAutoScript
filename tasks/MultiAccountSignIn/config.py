# This Python file uses the following encoding: utf-8
import hashlib

from pydantic import Field

from tasks.Component.config_base import ConfigBase, TimeDelta
from tasks.Component.config_scheduler import Scheduler


def account_config_field_name(config_name: str) -> str:
    """使用配置名稳定生成动态开关字段，避免规范化名称发生碰撞。"""
    digest = hashlib.sha256(config_name.encode('utf-8')).hexdigest()[:16]
    return f'config_{digest}'


def _count_multi_daily_accounts(data: dict) -> int:
    """统计 canonical 配置中 MultiDailyAltAcc 的有效账号条目数。"""
    task_data = data.get('multi_daily_alt_acc', {})
    return sum(
        1
        for key, value in task_data.items()
        if key.startswith('sup_account_list_')
        and isinstance(value, dict)
        and value.get('character')
    )


def active_account_configs(store) -> dict[str, tuple[str, int]]:
    """通过已初始化 Store 枚举当前 active 且含有效账号的配置。

    用 active_canonical_snapshots 一次拿齐 canonical：枚举后再逐个 load 会让每份配置
    多走一遍身份锁与严格校验（OASX 打开本任务参数页时最坏取 150 次文件锁）。
    锁超时仍由 Store 向上传播，任务不会把「暂时无法读取」误判为「没有选中账号」。
    """
    discovered = {}
    for config_name, canonical in store.active_canonical_snapshots().items():
        account_count = _count_multi_daily_accounts(canonical)
        if account_count == 0:
            continue
        discovered[account_config_field_name(config_name)] = (
            config_name,
            account_count,
        )
    return discovered


class AccountConfigSelection(ConfigBase, extra='allow'):
    # 动态 config_<sha256> 布尔字段由严格持久化边界校验，静态模型只负责保留 canonical 值。
    pass


class MultiAccountSignInScheduler(Scheduler):
    priority: int = Field(default=5, description='priority_help')
    success_interval: TimeDelta = Field(default=TimeDelta(days=1), description='success_interval_help')
    failure_interval: TimeDelta = Field(default=TimeDelta(hours=2), description='failure_interval_help')


class MultiAccountSignIn(ConfigBase):
    scheduler: MultiAccountSignInScheduler = Field(default_factory=MultiAccountSignInScheduler)
    account_config_selection: AccountConfigSelection = Field(default_factory=AccountConfigSelection)
