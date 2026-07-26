# This Python file uses the following encoding: utf-8
from pydantic import Field

from tasks.Component.config_base import ConfigBase, TimeDelta
from tasks.Component.config_scheduler import Scheduler


class MultiActivityShikigamiScheduler(Scheduler):
    # 多账号活动任务的独立调度器，间隔风格参照同类多账号任务 MultiAccountSignIn
    priority: int = Field(default=5, description='priority_help')
    success_interval: TimeDelta = Field(default=TimeDelta(days=1), description='success_interval_help')
    failure_interval: TimeDelta = Field(default=TimeDelta(hours=2), description='failure_interval_help')


class MultiActivityShikigamiConfig(ConfigBase):
    # 要执行的角色名，英文逗号分隔，例如 js1瑶光,js2瑶光
    # 不持有 ActivityShikigami 配置副本，活动参数复用当前实例的 activity_shikigami
    account_characters: str = Field(default='', description='要执行的角色名，仅用英文逗号 , 分隔，例如 js1瑶光,js2瑶光')


class MultiActivityShikigami(ConfigBase):
    scheduler: MultiActivityShikigamiScheduler = Field(default_factory=MultiActivityShikigamiScheduler)
    # 用子模型承载标量参数，满足前端“分组->参数”两级结构的渲染约定
    multi_activity_shikigami_config: MultiActivityShikigamiConfig = Field(default_factory=MultiActivityShikigamiConfig)
