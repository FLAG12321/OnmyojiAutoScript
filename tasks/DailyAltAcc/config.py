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
class DailyAltAccConfig(BaseModel):

  alliedteam_battle_enable: bool = Field(default=False, description='是否开启同心队战斗')
  alliedteam_limit_count: int = Field(default=30, description='战斗次数')
  alliedteam_ap_enable: bool = Field(default=False, description='是否开启补充体力')
  donatejade_enable: bool = Field(default=True, description='是否开启捐勾')
  courtyard_enable: bool = Field(default=True, description='是否开启庭院事务')
  mail_enable: bool = Field(default=True, description='是否开启领取邮件')
  cooperation_enable: bool = Field(default=True, description='是否开启寻找协作')
  returngift_enable: bool = Field(default=False, description='是否开启回礼')
  weekaward_enable: bool = Field(default=False, description='领取周奖励')
  mysteryshop_enable: bool = Field(default=False, description='是否开启神秘商店')
  isflower: bool = Field(default=False, description='是否二花')
  kekkaiActivation_enable: bool = Field(default=False, description='是否挂卡')
  KekkaiUtilize_enable: bool = Field(default=False, description='是否蹭卡')
  tree_planting_enable: int  = Field(default=2, description='0不运行 1买花 2买花捐树')
  trialbattle_enable: bool = Field(default=True, description='是否开启试炼战斗')
  summon_up_enable: bool = Field(default=True, description='是否开启UP召唤领取礼包')
class DailyAltAcc(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    daily_alt_acc_config: DailyAltAccConfig  = Field(default_factory=DailyAltAccConfig)
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
    cooperation = 1
    mshop = 2
    Utilize = 3
    neterror = 4
