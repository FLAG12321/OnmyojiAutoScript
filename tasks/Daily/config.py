from datetime import datetime, timedelta
from typing import Any, Dict

from pydantic import Field, BaseModel, model_validator, model_serializer, ValidationError

from deploy.logger import logger
from tasks.Component.SwitchAccount.switch_account_config import AccountInfo
from tasks.Component.config_base import ConfigBase, DateTime
from tasks.Component.config_scheduler import Scheduler
from tasks.WantedQuests.config import CooperationSelectMaskDescription, CooperationSelectMask, CooperationType

class ExtendedAccountInfo(AccountInfo):
    # 继承所有AccountInfo的属性，并添加新属性
    alliedteam_battle_enable: bool = Field(default=False, description='是否开启同心队战斗')
    alliedteam_limit_count: int = Field(default=30, description='limit_count_help')
    alliedteam_ap_enable: bool = Field(default=True, description='是否开启补充体力')
    donatejade_enable: bool = Field(default=True, description='是否开启捐勾')
    courtyard_enable: bool = Field(default=True, description='是否开启庭院事务')
    mail_enable: bool = Field(default=True, description='是否开启领取邮件')
    cooperation_enable: bool = Field(default=True, description='是否开启寻找协作')
    returngift_enable: bool = Field(default=True, description='是否开启回礼')
    weekaward_enable: bool = Field(default=True, description='是否领取每周奖励')
    mysteryshop_enable: bool = Field(default=True, description='是否开启神秘商店')
    isflower: bool = Field(default=False, description='是否二花')
    kekkaiActivation_enable: bool = Field(default=True, description='是否挂卡')
    KekkaiUtilize_enable: bool = Field(default=True, description='是否蹭卡')
    tree_planting_enable: int = Field(default=2, description='种树:0不运行 1买花 2买花捐树')
    trialbattle_enable: bool = Field(default=True, description='是否开启试炼战斗')
    summon_up_enable: bool = Field(default=True, description='是否开启UP召唤领取礼包')
class DailyConfig(ConfigBase):
    # 小号数
    sup_account_count: int = Field(default=1, ge=1, description='sup_account_count_help')
    total_alliedteam_battle_enable: bool = Field(default=False, description='同心模式')
    total_alliedteam_ap_enable: bool = Field(default=True, description='补充同心体力')
    total_donatejade_enable: bool = Field(default=True, description='捐勾')
    total_courtyard_enable: bool = Field(default=True, description='庭院事务')
    total_mail_enable: bool = Field(default=True, description='邮件')
    total_cooperation_enable: bool = Field(default=True, description='协作')
    total_returngift_enable: bool = Field(default=True, description='回礼')
    total_weekaward_enable: bool = Field(default=True, description='领取每周奖励')
    total_mysteryshop_enable: bool = Field(default=False, description='神秘商店')
    need_login: bool = Field(default=True, description='无视时间登录')
    need_login_time: DateTime = Field(default=DateTime.fromisoformat("2023-01-01 00:00:00"),description='需要登录时间点')
    total_kekkaiActivation_enable: bool = Field(default=False, description='是否挂卡')
    total_KekkaiUtilize_enable: bool = Field(default=False, description='是否蹭卡')
    total_tree_planting_enable: int = Field(default=0, description='种树:0不运行 1买花 2买花捐树')
    total_trialbattle_enable: bool = Field(default=False, description='试炼战斗')
    total_summon_up_enable: bool = Field(default=False, description='是否开启UP召唤领取礼包')
    shutdown_after_finish: bool = Field(default=False, description='日常任务完成后是否关机')

class Daily(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    daily_config: DailyConfig = Field(default_factory=DailyConfig)
    # 小号信息
    sup_account_list: list[ExtendedAccountInfo] = None
    def update_account_login_history(self, account: ExtendedAccountInfo):
        accountInfoList = self.sup_account_list
        for info in accountInfoList:
            if info.character != account.character or info.svr != account.svr:
                continue
            info.last_complete_time = datetime.now()
            logger.info(f"update login history name:{info.character}  time :{info.last_complete_time}")
            return  info.last_complete_time

    @model_validator(mode='before')
    @classmethod
    def validator_all(cls, v: dict) -> Any:
        sup_account_count = v.get('daily_config', {}).get('sup_account_count', 1)

        def validator_list(list_name, data, item_type=None, list_size=1):
            if list_name not in data:
                data[list_name] = []

            remove_keys = []
            for key, value in data.items():
                if list_name == key or list_name not in key:
                    continue
                try:
                    item = item_type(**value)
                    if item.is_valid():
                        data[list_name].append(item)
                    remove_keys.append(key)
                except ValidationError as e:
                    pass
                except TypeError as e:
                    pass

            for key in remove_keys:
                del data[key]

            if item_type is not None:
                if len(data[list_name]) < list_size:
                    for i in range(list_size - len(data[list_name])):
                        data[list_name].append(item_type())
        validator_list('sup_account_list', v, ExtendedAccountInfo, sup_account_count)

        return v

    @model_serializer()
    def serializer_model(self, value: Any) -> Dict[str, Any]:
        properties = self.__dict__
        data = {}

        def v_dump(v):
            try:
                return v.model_dump()
            except AttributeError as e:
                logger.error(e)
                return v

        for key, value in properties.items():
            if isinstance(value, list):
                for index, v in enumerate(value):
                    data[f'{key}_{index + 1}'] = v_dump(v)
            else:
                data[key] = v_dump(value)
        return data
