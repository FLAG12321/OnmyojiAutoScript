# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from pydantic import BaseModel, Field
from enum import Enum
from datetime import datetime, time

from tasks.Component.config_scheduler import Scheduler
from tasks.Component.config_base import ConfigBase, TimeDelta
from tasks.Utils.config_enum import ShikigamiClass

class SelectFriendList(str, Enum):
    SAME_SERVER = 'same_server'
    DIFFERENT_SERVER = 'different_server'

class UtilizeRule(str, Enum):
    DEFAULT = 'default'  # 默认就好
    TAIKO = 'kaiko'  # 太鼓优先
    FISH = 'fish'  # 斗鱼优先
    DAILY= 'daily'
    # AUTO = 'auto'  # 自动 兼容代码罢了



class UtilizeScheduler(Scheduler):
    priority: int = Field(default=2, description='priority_help')
    success_interval: TimeDelta = Field(default=TimeDelta(hours=6), description='success_interval_help')
    failure_interval: TimeDelta = Field(default=TimeDelta(hours=6), description='failure_interval_help')

class UtilizeConfig(BaseModel):
    utilize_rule: UtilizeRule = Field(default=UtilizeRule.TAIKO, description='utilize_rule_help')
    select_friend_list: SelectFriendList = Field(default=SelectFriendList.SAME_SERVER, description='select_friend_list_help')
    # 按配置顺序优先搜索指定好友，空字符串表示关闭该流程
    priority_search_names: str = Field(default='', description='priority_search_names_help')
    # 优先搜索好友的结界卡数值最低门槛，0 表示不限制；斗鱼与太鼓分别设置
    priority_search_min_fish: int = Field(default=0, description='priority_search_min_fish_help')
    priority_search_min_taiko: int = Field(default=0, description='priority_search_min_taiko_help')
    shikigami_class: ShikigamiClass = Field(default=ShikigamiClass.N, description='shikigami_class_help')
    shikigami_order: int = Field(default=1, description='shikigami_order_help')
    harvest_guild_max_times: int = Field(default=0, description='收取寮资金或体力失败的最大尝试次数')
    # 禁止运行时间段：命中时跳过本次运行并将下次时间设为区间结束时刻
    forbidden_time_enable: bool = Field(default=False, description='是否启用禁止运行时间段')
    forbidden_time_range: str = Field(default='', description='禁止运行的时间段，24小时制精确到分钟，多个用英文逗号分隔，如 01:00-02:00,02:30-04:00，支持跨天如 23:00-01:00')
    utilize_enable: bool = Field(default=True, description='是否蹭卡，小号可以选择不蹭卡')
    pets_enable: bool = Field(default=False, description='pets_enable_help')
    guild_ap_enable: bool = Field(default=False, description='收取寄养资源')
    guild_assets_enable: bool = Field(default=False, description='guild_assets_enable_help')
    box_ap_enable: bool = Field(default=False, description='box_ap_enable_help')
    box_exp_enable: bool = Field(default=False, description='box_exp_enable_help')
    box_exp_waste: bool = Field(default=False, description='box_exp_waste_help')
    exchange_before: bool = Field(default=True, description='exchange_before_help')

class KekkaiUtilize(ConfigBase):
    scheduler: UtilizeScheduler = Field(default_factory=UtilizeScheduler)
    utilize_config: UtilizeConfig = Field(default_factory=UtilizeConfig)


