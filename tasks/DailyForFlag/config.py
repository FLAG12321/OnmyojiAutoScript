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
from tasks.KekkaiActivation.config import KekkaiActivation as KekkaiActivationConfig
from tasks.KekkaiUtilize.config import KekkaiUtilize as KekkaiUtilizeConfig
class DailyForFlagConfig(BaseModel):

  tongxin_battle_enable: bool = Field(default=False, description='是否开启同心队战斗')
  tongxin_limit_count: int = Field(default=30, description='战斗次数')
  tongxin_ap_enable: bool = Field(default=False, description='是否开启补充体力')
  juangou_enable: bool = Field(default=True, description='是否开启捐勾')
  tingyuan_enable: bool = Field(default=True, description='是否开启庭院事务')
  mail_enable: bool = Field(default=True, description='是否开启领取邮件')
  xiezuo_enable: bool = Field(default=True, description='是否开启寻找协作')
  huili_enable: bool = Field(default=False, description='是否开启回礼')
  weekaward_enable: bool = Field(default=False, description='领取周奖励')
  mysteryshop_enable: bool = Field(default=False, description='是否开启神秘商店')
  isflower: bool = Field(default=False, description='是否二花')
  kekkaiActivation_enable: bool = Field(default=False, description='是否挂卡')
  KekkaiUtilize_enable: bool = Field(default=False, description='是否蹭卡')
  tree_planting_enable: int  = Field(default=2, description='0不运行 1买花 2买花捐树')
  trialbattle_enable: bool = Field(default=True, description='是否开启试炼战斗')
class DailyForFlag(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    daily_for_flag_config: DailyForFlagConfig  = Field(default_factory=DailyForFlagConfig)
    general_battle_config: GeneralBattleConfig = Field(default_factory=GeneralBattleConfig)
class GoodsType(Enum):
    shepi = 0
    fmpi = 1
    heisui = 2

class CoinType(Enum):
    jade = 0
    gold = 1
    unknow = 2
class MSGType(Enum):
    none = 0
    xiezuo = 1
    mshop = 2
    Utilize = 3
    neterror = 4
