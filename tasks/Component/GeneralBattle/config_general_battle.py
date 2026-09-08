# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from enum import Enum
from datetime import datetime, time
from pydantic import BaseModel, ValidationError, validator, Field

class GreenMarkType(str, Enum):
    GREEN_LEFT1 = 'green_left1'
    GREEN_LEFT2 = 'green_left2'
    GREEN_LEFT3 = 'green_left3'
    GREEN_LEFT4 = 'green_left4'
    GREEN_LEFT5 = 'green_left5'
    GREEN_MAIN = 'green_main'

class GeneralBattleConfig(BaseModel):

    # 是否锁定阵容, 有些的战斗是外边的锁定阵容甚至有些的战斗没有锁定阵容的
    lock_team_enable: bool = Field(default=False, description='lock_team_enable_help')

    # 是否启动 预设队伍
    preset_enable: bool = Field(default=False, description='preset_enable_help')
    # 选哪一个预设组
    preset_group: int = Field(default=1, description='preset_group_help', ge=1, le=7)
    # 选哪一个队伍
    preset_team: int = Field(default=1, description='preset_team_help', ge=1, le=5)
    # 是否启动开启buff
    # buff_enable: bool = Field(default=False, description='buff_enable_help')
    # 是否点击觉醒Buff
    # buff_awake_click: bool = Field(default=False, description='')
    # 是否点击御魂buff
    # buff_soul_click: bool = Field(default=False, description='')
    # 是否点击金币50buff
    # buff_gold_50_click: bool = Field(default=False, description='')
    # 是否点击金币100buff
    # buff_gold_100_click: bool = Field(default=False, description='')
    # 是否点击经验50buff
    # buff_exp_50_click: bool = Field(default=False, description='')
    # 是否点击经验100buff
    # buff_exp_100_click: bool = Field(default=False, description='')

    # 是否开启绿标
    green_enable: bool = Field(default=False, description='green_enable_help')
    # 选哪一个绿标
    green_mark: GreenMarkType = Field(default=GreenMarkType.GREEN_LEFT1, description='green_mark_help')

    # 是否启动战斗时随机点击或者随机滑动
    random_click_swipt_enable: bool = Field(default=False, description='random_click_swipt_enable_help')

    # 随机自动战斗段：战斗过程中随机插入若干场自动战斗（点开→游戏连打→点回手动）
    # 字段直接拍平在 GeneralBattleConfig 上而非嵌套子模型——script_task() 的
    # merge_value 与 OASX 前端都只支持一层 group，嵌套 BaseModel 会 KeyError。
    # 总开关：部分战斗不存在自动战斗按钮，默认关闭
    auto_battle_enable: bool = Field(default=False, description='auto_battle_enable_help')
    # 单段连续自动战斗场数 M（进入自动后连续 M 场由游戏自动完成，脚本零输入）
    auto_segment_count: int = Field(default=2, ge=1, le=10, description='auto_segment_count_help')
    # 本次任务运行内自动战斗总场数上限 T（不持久化，任务重启重新规划）
    auto_total_count: int = Field(default=4, ge=1, le=50, description='auto_total_count_help')


