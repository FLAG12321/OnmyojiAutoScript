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
  # 邀请人数阈值：队友标记满该数量即进入下一流程，仅支持 1 或 2（默认2）
  alliedteam_invite_count: int = Field(default=2, ge=1, le=2, description='同心队需要邀请的队友人数(1或2)')
  alliedteam_ap_enable: bool = Field(default=False, description='是否开启补充体力')
  donatejade_enable: bool = Field(default=True, description='是否开启捐勾')
  courtyard_enable: bool = Field(default=True, description='是否开启庭院事务')
  mail_enable: bool = Field(default=True, description='是否开启领取邮件')
  cooperation_enable: bool = Field(default=True, description='是否开启寻找协作')
  returngift_enable: bool = Field(default=False, description='是否开启回礼')
  weekaward_enable: bool = Field(default=False, description='领取周奖励')
  mysteryshop_enable: bool = Field(default=False, description='是否开启神秘商店')
  isflower: int = Field(default=0, ge=0, le=3, description='几花账号：0零花 1一花 2二花 3三花，决定神秘商店解锁哪些货')
  kekkaiActivation_enable: bool = Field(default=False, description='是否挂卡')
  KekkaiUtilize_enable: bool = Field(default=False, description='是否蹭卡')
  tree_planting_enable: int  = Field(default=2, description='0不运行 1买花 2买花捐树')
  trialbattle_enable: bool = Field(default=True, description='是否开启试炼战斗')
  summon_up_enable: bool = Field(default=True, description='是否开启UP召唤领取礼包')
  publish_sr_enable: bool = Field(default=False, description='是否发布SR碎片')
class DailyAltAcc(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    daily_alt_acc_config: DailyAltAccConfig  = Field(default_factory=DailyAltAccConfig)
    general_battle_config: GeneralBattleConfig = Field(default_factory=GeneralBattleConfig)
class GoodsType(Enum):
    orochi_scale = 0
    demon_soul = 1
    skill_shard = 2
    mystery_amulet = 3
    black_daruma = 4

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
