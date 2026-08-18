# This Python file uses the following encoding: utf-8
from pydantic import Field

from tasks.Component.config_base import ConfigBase, TimeDelta
from tasks.Component.config_scheduler import Scheduler


class ActivitySignInScheduler(Scheduler):
    # 单账号活动签到：每日一次，失败 2 小时后重试（沿用旧 MultiAccountSignIn 的节奏）
    priority: int = Field(default=5, description='priority_help')
    success_interval: TimeDelta = Field(default=TimeDelta(days=1), description='success_interval_help')
    failure_interval: TimeDelta = Field(default=TimeDelta(hours=2), description='failure_interval_help')


class ActivitySignIn(ConfigBase):
    scheduler: ActivitySignInScheduler = Field(default_factory=ActivitySignInScheduler)
