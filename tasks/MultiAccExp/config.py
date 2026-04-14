# This Python file uses the following encoding: utf-8
from pydantic import BaseModel, Field, model_validator, model_serializer
from datetime import datetime
from typing import Dict, Any
from pydantic import ValidationError
from tasks.Component.config_scheduler import Scheduler
from tasks.Component.config_base import ConfigBase, DateTime
from tasks.Component.SwitchAccount.switch_account_config import AccountInfo


class ExtendedAccountInfo(AccountInfo):
    # 继承所有AccountInfo的属性，并添加经验妖怪相关的配置
    exp_farming_enable: bool = Field(default=True, description='是否开启经验妖怪刷取')
    buff_exp_50_click: bool = Field(default=False, description='是否开启50%经验加成')
    buff_exp_100_click: bool = Field(default=False, description='是否开启100%经验加成')


class MultiAccExpConfig(ConfigBase):
    # 小号数
    sup_account_count: int = Field(default=1, ge=1, description='sup_account_count_help')
    # 全局经验妖怪设置
    total_exp_farming_enable: bool = Field(default=True, description='是否开启全账户经验妖怪')
    total_buff_exp_50_click: bool = Field(default=False, description='是否开启50%经验加成')
    total_buff_exp_100_click: bool = Field(default=False, description='是否开启100%经验加成')
    need_login: bool = Field(default=True, description='无视时间登录')
    need_login_time: DateTime = Field(default=DateTime.fromisoformat("2023-01-01 00:00:00"), description='需要登录时间点')


class MultiAccExp(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    multi_acc_exp_config: MultiAccExpConfig = Field(default_factory=MultiAccExpConfig)
    # 小号信息
    sup_account_list: list[ExtendedAccountInfo] = None

    def update_account_login_history(self, account: ExtendedAccountInfo):
        accountInfoList = self.sup_account_list
        for info in accountInfoList:
            if info.character != account.character or info.svr != account.svr:
                continue
            info.last_complete_time = datetime.now()
            break

    @model_validator(mode='before')
    @classmethod
    def validator_all(cls, v: dict) -> Any:
        sup_account_count = v.get('multi_acc_exp_config', {}).get('sup_account_count', 1)

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
                from module.logger import logger
                logger.error(e)
                return v

        for key, value in properties.items():
            if isinstance(value, list):
                for index, v in enumerate(value):
                    data[f'{key}_{index + 1}'] = v_dump(v)
            else:
                data[key] = v_dump(value)
        return data