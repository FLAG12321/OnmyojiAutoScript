# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from pydantic import BaseModel, Field
from enum import Enum
from datetime import datetime, time

from tasks.Component.SwitchSoul.switch_soul_config import SwitchSoulConfig as BaseSwitchSoulConfig
from tasks.Component.config_scheduler import Scheduler
from tasks.Component.config_base import ConfigBase, Time
from tasks.Component.GeneralInvite.config_invite import InviteConfig
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig


class UserStatus(str, Enum):
    LEADER = 'leader'
    MEMBER = 'member'
    ALONE = 'alone'
    WILD = 'wild'  # 还不打算实现

class Layer(str, Enum):
    ONE = '壹层'
    TWO = '贰层'
    THREE = '叁层'
    FOUR = '肆层'
    FIVE = '伍层'
    SIX = '陆层'
    SEVEN = '柒层'
    EIGHT = '捌层'
    NINE = '玖层'
    TEN = '拾层'
    ELEVEN = '悲鸣'
    TWELVE = '神罚'
    THIRTEEN = '虚无'


class OrochiConfig(ConfigBase):
    # 身份
    user_status: UserStatus = Field(default=UserStatus.LEADER, description='user_status_help')
    # 队员通过运行中的实例列表选择队长；队长身份下可以留空
    leader_instance: str = Field(default='', description='leader_instance_help')
    # 场次 Epoch 由脚本自动回写，输入固定值 RESET 可丢弃旧场次并重新配对
    epoch: str = Field(default='', description='epoch_help')
    # 层数
    layer: Layer = Field(default=Layer.ELEVEN, description='layer_help')
    # 单轮限制时间，以队长配置为唯一来源
    limit_time: Time = Field(default=Time(minute=30), description='limit_time_help')
    # 单轮限制次数，以队长配置为唯一来源
    limit_count: int = Field(default=30, description='limit_count_help')
    # 多轮累计战斗时间，仅组队模式使用
    total_limit_time: Time = Field(default=Time(hour=4), description='total_limit_time_help')
    # 多轮累计战斗次数，仅组队模式使用
    total_limit_count: int = Field(default=300, description='total_limit_count_help')
    # 是否开启御魂加成
    soul_buff_enable: bool = Field(default=False, description='soul_buff_enable_help')
    # 是否开启五倍消耗（游戏内五倍卡需已开启，脚本只负责按五倍计数并扣减券）
    five_times_enable: bool = Field(default=False, description='five_times_enable_help')
    # 五倍消耗券剩余数量，每消耗一张会立即回写，保证下次运行读到真实剩余
    five_times_ticket: int = Field(default=0, description='five_times_ticket_help')
    # 是否在完成后拉起RealmRaid任务
    enable_realm_raid_chain: bool = Field(default=True, description='enable_realm_raid_chain_help')

class SwitchSoulConfig(BaseSwitchSoulConfig):
    enable: bool = Field(default=False)
    switch_group_team: str = Field(default='-1,-1', description='switch_group_team_help')
    enable_switch_by_name: bool = Field(default=False, description='enable_switch_by_name_help')
    group_name: str = Field(default='')
    team_name: str = Field(default='')
    auto_switch_soul: bool = Field(default=False, description='auto_switch_soul_orochi_help')
    # 十层 config
    ten_switch: str = Field(default='-1,-1', description='ten_switch_help')
    # 悲鸣 config
    eleven_switch: str = Field(default='-1,-1', description='eleven_switch_help')
    # 神罚 config
    twelve_switch: str = Field(default='-1,-1', description='twelve_switch_help')
    # 虚无 config
    thirteen_switch: str = Field(default='-1,-1', description='thirteen_switch_help')


class Orochi(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    orochi_config: OrochiConfig = Field(default_factory=OrochiConfig)
    invite_config: InviteConfig = Field(default_factory=InviteConfig)
    general_battle_config: GeneralBattleConfig = Field(default_factory=GeneralBattleConfig)
    switch_soul: SwitchSoulConfig = Field(default_factory=SwitchSoulConfig)
