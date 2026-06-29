# This Python file uses the following encoding: utf-8
# @author 
# github 
from typing import Dict, Any
from pydantic import BaseModel, Field, model_validator, model_serializer, ValidationError
from enum import Enum

from tasks.Component.config_scheduler import Scheduler
from tasks.Component.config_base import ConfigBase, Time
from tasks.Component.SwitchAccount.switch_account_config import AccountInfo


class MasterDiscipleMode(str, Enum):
    MASTER = 'master'
    DISCIPLE = 'disciple'

class MasterBattleMode(str, Enum):
    """师父金币/经验妖怪战斗模式"""
    BATTLE_THEN_EXIT = 'battle_then_exit'    # 进入后退出（默认）
    NORMAL_BATTLE = 'normal_battle'          # 正常完成战斗

class MasterPresetConfig(BaseModel):
    """师父模式单组御魂预设配置，任务开始前一次性切换"""
    # 是否启用该组预设
    enable: bool = Field(default=False, description='master_preset_enable_help')
    # 御魂预设组 (1-7)
    preset_group: int = Field(default=1, description='preset_group_help', ge=1, le=7)
    # 御魂预设队伍 (1-4)
    preset_team: int = Field(default=1, description='preset_team_help', ge=1, le=4)

class MasterDiscipleConfig(ConfigBase):
    # 模式选择
    mode: MasterDiscipleMode = Field(default=MasterDiscipleMode.MASTER, description='master_disciple_mode_help')
    # 师父角色名称
    master_name: str = Field(default='', description='master_name_help')
    # 限制时间
    limit_time: Time = Field(default=Time(minute=30), description='limit_time_help')
    # 限制次数
    limit_count: int = Field(default=30, description='limit_count_help')
    # 是否执行探索任务
    run_exploration: bool = Field(default=False, description='run_exploration_help')
    # 是否执行经验妖怪任务
    run_exp_monster: bool = Field(default=False, description='run_exp_monster_help')
    # 是否执行石距任务
    run_stone_ju: bool = Field(default=False, description='run_stone_ju_help')
    # 是否执行金币妖怪任务
    run_coin_monster: bool = Field(default=False, description='run_coin_monster_help')
    # 是否执行守护历练任务
    run_guard: bool = Field(default=False, description='run_guard_help')
    # 守护历练战斗次数
    guard_battle_count: int = Field(default=30, description='guard_battle_count_help')
    # 是否自动切换账号
    auto_switch_account: bool = Field(default=False, description='auto_switch_account_help')
    # 是否轮询所有徒弟账号（False=只执行第一个账号，True=依次轮询所有账号）
    cycle_all_disciples: bool = Field(default=False, description='cycle_all_disciples_help')
    # 邀请师父超时时间（秒）
    invite_timeout: int = Field(default=60, description='invite_timeout_help')
    # 师父金币/经验妖怪战斗模式（进入后退出 or 正常完成战斗）
    master_battle_mode: MasterBattleMode = Field(
        default=MasterBattleMode.BATTLE_THEN_EXIT,
        description='master_battle_mode_help'
    )
    # 是否在任务开始时检测并购买体力
    buy_ap_when_low: bool = Field(default=False, description='buy_ap_when_low_help')
    # 体力阈值，低于等于此值时购买一次体力
    ap_threshold: int = Field(default=200, description='ap_threshold_help')
    # 是否执行百鬼夜行任务
    run_hyakkiyakou: bool = Field(default=False, description='run_hyakkiyakou_help')


class MasterDisciple(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    master_disciple_config: MasterDiscipleConfig = Field(default_factory=MasterDiscipleConfig)
    # 师父模式三组御魂预设配置
    master_preset_1: MasterPresetConfig = Field(default_factory=MasterPresetConfig, description='master_preset_1_help')
    master_preset_2: MasterPresetConfig = Field(default_factory=MasterPresetConfig, description='master_preset_2_help')
    master_preset_3: MasterPresetConfig = Field(default_factory=MasterPresetConfig, description='master_preset_3_help')
    # 徒弟账号列表
    disciple_account_list: list[AccountInfo] = None

    @model_validator(mode='before')
    @classmethod
    def validator_all(cls, v: dict) -> Any:
        list_size = 1

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

        validator_list('disciple_account_list', v, AccountInfo, list_size)

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
