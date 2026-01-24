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

class DailyForFlagConfig(BaseModel):

  tongxin_battle_enable: bool = Field(default=False, description='是否开启同心队战斗')
  tongxin_limit_count: int = Field(default=30, description='limit_count_help')
  tongxin_ap_enable: bool = Field(default=False, description='是否开启补充体力')
  juangou_enable: bool = Field(default=True, description='是否开启捐勾')
  tingyuan_enable: bool = Field(default=True, description='是否开启庭院事务')
  mail_enable: bool = Field(default=True, description='是否开启领取邮件')
  xiezuo_enable: bool = Field(default=True, description='是否开启寻找协作')


class DailyForFlag(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    daily_for_flag_config: DailyForFlagConfig  = Field(default_factory=DailyForFlagConfig)
    general_battle_config: GeneralBattleConfig = Field(default_factory=GeneralBattleConfig)

