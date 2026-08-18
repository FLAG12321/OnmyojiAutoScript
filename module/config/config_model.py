# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from tasks.GuildActivityMonitor.config import GuildActivityMonitor
from typing import Dict, Any

import re
import inflection

from pydantic import Field

from module.config.utils import *
from module.logger import logger

# 导入配置的Python文件
from tasks.Component.config_base import ConfigBase
from tasks.Exploration.config import Exploration
from tasks.RyouToppa.config import RyouToppa
from tasks.Dokan.config import Dokan
from tasks.Script.config import Script
from tasks.Restart.config import Restart
from tasks.GlobalGame.config import GlobalGame
# 每日任务-----------------------------------------------------------------------------------------------------
from tasks.AreaBoss.config import AreaBoss
from tasks.ExperienceYoukai.config import ExperienceYoukai
from tasks.GoldYoukai.config import GoldYoukai
from tasks.Nian.config import Nian
from tasks.KekkaiUtilize.config import KekkaiUtilize
from tasks.KekkaiActivation.config import KekkaiActivation
from tasks.DemonEncounter.config import DemonEncounter
from tasks.DailyTrifles.config import DailyTrifles
from tasks.TalismanPass.config import TalismanPass
from tasks.Pets.config import Pets
from tasks.SoulsTidy.config import SoulsTidy
from tasks.Delegation.config import Delegation
from tasks.WantedQuests.config import WantedQuests
from tasks.Tako.config import Tako
from tasks.AutoCheckinBigGod.config import AutoCheckinBigGod
from tasks.DailyAltAcc.config import DailyAltAcc
from tasks.ActivitySignIn.config import ActivitySignIn
from tasks.MasterDisciple.config import MasterDisciple
# ----------------------------------------------------------------------------------------------------------------------
from tasks.Orochi.config import Orochi
from tasks.OrochiMoans.config import OrochiMoans
from tasks.Sougenbi.config import Sougenbi
from tasks.FallenSun.config import FallenSun
from tasks.EternitySea.config import EternitySea
from tasks.SixRealms.config import SixRealms
from tasks.RealmRaid.config import RealmRaid
from tasks.CollectiveMissions.config import CollectiveMissions
from tasks.Hunt.config import Hunt
from tasks.AbyssShadows.config import AbyssShadows
from tasks.GuildBanquet.config import GuildBanquet
from tasks.DemonRetreat.config import DemonRetreat
from tasks.GuildActivityMonitor.config import GuildActivityMonitor

# 这一部分是活动的配置-----------------------------------------------------------------------------------------------------
from tasks.ActivityShikigami.config import ActivityShikigami
from tasks.MetaDemon.config import MetaDemon
from tasks.FrogBoss.config import FrogBoss
from tasks.FloatParade.config import FloatParade
from tasks.Quiz.config import Quiz
from tasks.KittyShop.config import KittyShop
from tasks.DyeTrials.config import DyeTrials
# ----------------------------------------------------------------------------------------------------------------------

# 肝帝专属---------------------------------------------------------------------------------------------------------------
from tasks.BondlingFairyland.config import BondlingFairyland
from tasks.EvoZone.config import EvoZone
from tasks.GoryouRealm.config import GoryouRealm
from tasks.Hyakkiyakou.config import Hyakkiyakou
from tasks.HeroTest.config import HeroTest
from tasks.FindJade.config import FindJade
from tasks.MemoryScrolls.config import MemoryScrolls
from tasks.MultiDailyAltAcc.config import MultiDailyAltAcc
from tasks.MultiTasks.config import MultiTasks
from tasks.ReturnGift.config import ReturnGift
from tasks.Plotline.config import Plotline
from tasks.SearchId.config import SearchId
# ----------------------------------------------------------------------------------------------------------------------

# 每周任务---------------------------------------------------------------------------------------------------------------
from tasks.TrueOrochi.config import TrueOrochi
from tasks.RichMan.config import RichMan
from tasks.Secret.config import Secret
from tasks.WeeklyTrifles.config import WeeklyTrifles
from tasks.MysteryShop.config import MysteryShop
from tasks.Duel.config import Duel
# ----------------------------------------------------------------------------------------------------------------------

class ConfigModel(ConfigBase):
    config_name: str = "oas"
    running_task: str = ''
    script: Script = Field(default_factory=Script)
    restart: Restart = Field(default_factory=Restart)
    global_game: GlobalGame = Field(default_factory=GlobalGame)

    # 这些是每日任务的
    area_boss: AreaBoss = Field(default_factory=AreaBoss)
    experience_youkai: ExperienceYoukai = Field(default_factory=ExperienceYoukai)
    gold_youkai: GoldYoukai = Field(default_factory=GoldYoukai)
    nian: Nian = Field(default_factory=Nian)
    realm_raid: RealmRaid = Field(default_factory=RealmRaid)
    ryou_toppa: RyouToppa = Field(default_factory=RyouToppa)
    kekkai_utilize: KekkaiUtilize = Field(default_factory=KekkaiUtilize)
    kekkai_activation: KekkaiActivation = Field(default_factory=KekkaiActivation)
    demon_encounter: DemonEncounter = Field(default_factory=DemonEncounter)
    daily_trifles: DailyTrifles = Field(default_factory=DailyTrifles)
    talisman_pass: TalismanPass = Field(default_factory=TalismanPass)
    pets: Pets = Field(default_factory=Pets)
    souls_tidy: SoulsTidy = Field(default_factory=SoulsTidy)
    delegation: Delegation = Field(default_factory=Delegation)
    exploration: Exploration = Field(default_factory=Exploration)
    wanted_quests: WantedQuests = Field(default_factory=WantedQuests)
    tako: Tako = Field(default_factory=Tako)
    auto_checkin_big_god: AutoCheckinBigGod = Field(default_factory=AutoCheckinBigGod)
    daily_alt_acc: DailyAltAcc = Field(default_factory=DailyAltAcc)
    activity_sign_in: ActivitySignIn = Field(default_factory=ActivitySignIn)
    master_disciple: MasterDisciple = Field(default_factory=MasterDisciple)

    # 这些是刷御魂的
    orochi: Orochi = Field(default_factory=Orochi)
    orochi_moans: OrochiMoans = Field(default_factory=OrochiMoans)
    sougenbi: Sougenbi = Field(default_factory=Sougenbi)
    fallen_sun: FallenSun = Field(default_factory=FallenSun)
    eternity_sea: EternitySea = Field(default_factory=EternitySea)
    six_realms: SixRealms = Field(default_factory=SixRealms)

    # 这些是活动的
    activity_shikigami: ActivityShikigami = Field(default_factory=ActivityShikigami)
    meta_demon: MetaDemon = Field(default_factory=MetaDemon)
    frog_boss: FrogBoss = Field(default_factory=FrogBoss)
    float_parade: FloatParade = Field(default_factory=FloatParade)
    quiz: Quiz = Field(default_factory=Quiz)
    kitty_shop: KittyShop = Field(default_factory=KittyShop)
    dye_trials: DyeTrials = Field(default_factory=DyeTrials)

    # 这些是肝帝专属
    bondling_fairyland: BondlingFairyland = Field(default_factory=BondlingFairyland)
    evo_zone: EvoZone = Field(default_factory=EvoZone)
    goryou_realm: GoryouRealm = Field(default_factory=GoryouRealm)
    hyakkiyakou: Hyakkiyakou = Field(default_factory=Hyakkiyakou)
    hero_test: HeroTest = Field(default_factory=HeroTest)
    find_jade: FindJade = Field(default_factory=FindJade)
    memory_scrolls: MemoryScrolls = Field(default_factory=MemoryScrolls)
    multi_daily_alt_acc: MultiDailyAltAcc = Field(default_factory=MultiDailyAltAcc)
    multi_tasks: MultiTasks = Field(default_factory=MultiTasks)
    plotline: Plotline = Field(default_factory=Plotline)
    search_id: SearchId = Field(default_factory=SearchId)
    return_gift: ReturnGift = Field(default_factory=ReturnGift)
    # 这些是每周任务
    true_orochi: TrueOrochi = Field(default_factory=TrueOrochi)
    rich_man: RichMan = Field(default_factory=RichMan)
    secret: Secret = Field(default_factory=Secret)
    weekly_trifles: WeeklyTrifles = Field(default_factory=WeeklyTrifles)
    mystery_shop: MysteryShop = Field(default_factory=MysteryShop)
    duel: Duel = Field(default_factory=Duel)

    # 阴阳寮
    collective_missions: CollectiveMissions = Field(default_factory=CollectiveMissions)
    hunt: Hunt = Field(default_factory=Hunt)
    dokan: Dokan = Field(default_factory=Dokan)
    abyss_shadows: AbyssShadows = Field(default_factory=AbyssShadows)
    guild_banquet: GuildBanquet = Field(default_factory=GuildBanquet)
    demon_retreat: DemonRetreat = Field(default_factory=DemonRetreat)
    guild_activity_monitor: GuildActivityMonitor = Field(default_factory=GuildActivityMonitor)

    # 注意：ConfigModel 只保留纯 Schema 与 UI/读取 helper。文件型 __init__、自动保存
    # __setattr__、read_json/write_json/save/script_set_arg/copy/reset 等 I/O 与业务写操作
    # 已在 Task 3 全部移入 ConfigStore；任何磁盘读写必须经 ConfigStore/公共 locked API。

    def gui_args(self, task: str) -> str:
        """
        返回提供给gui显示的参数
        :param task: 输入的是任务的名称英文 如'Script' 或者是'script'都是可以的
        :return: 返回的是pydantic给我们结构化的输出的信息, 如果不能获取就返回空的str
        """
        task = convert_to_underscore(task)
        task_gui = getattr(self, task, None)
        if task_gui is None:
            logger.warning(f'{task} is no inexistence')
            return ''

        schema2 = task_gui.schema()
        # https://github.com/pydantic/pydantic/discussions/5687
        if 'definitions' in schema2:
            if 'Scheduler' in schema2['definitions']:
                if 'properties' in schema2['definitions']['Scheduler']:
                    properties = schema2['definitions']['Scheduler']['properties']
                    if 'success_interval' in properties:
                        properties['success_interval']['type'] = 'string'
                    if 'failure_interval' in properties:
                        properties['failure_interval']['type'] = 'string'
        return json.dumps(schema2)

    def gui_task(self, task: str) -> str:
        """
        返回提供给gui显示的参数
        :param task:
        :return:
        """
        task_name = convert_to_underscore(task)
        task = getattr(self, task_name, None)
        if task is None:
            logger.warning(f'{task_name} is no inexistence')
            return ''
        return task.json()

    @staticmethod
    def type(key: str) -> str:
        """
        输入模型的键值，获取这个字段对象的类型 比如输入是orochi输出是Orochi
        :param key:
        :return:
        """
        field_type: str = str(ConfigModel.__annotations__[key])
        # return field_type
        if '.' in field_type:
            classname = field_type.split('.')[-1][:-2]
            return classname
        else:
            classname = re.findall(r"'([^']*)'", field_type)[0]
            return classname

    @staticmethod
    def deep_get(obj, keys: str, default=None):
        """
        递归获取模型的值
        :param obj:
        :param keys:
        :param default:
        :return:
        """
        if not isinstance(keys, list):
            keys = keys.split('.')
        value = obj
        try:
            for key in keys:
                value = getattr(value, key)
        except AttributeError:
            return default
        return value

    @staticmethod
    def deep_set(obj, keys: str, value) -> bool:
        if not isinstance(keys, list):
            keys = keys.split('.')
        current_obj = obj
        try:
            for key in keys[:-1]:
                current_obj = getattr(current_obj, key)
            setattr(current_obj, keys[-1], value)
            return True
        except (AttributeError, KeyError):
            return False

    # ----------------------------------- fastapi -----------------------------------
    def script_task(self, task: str) -> dict:
        """

        :param task: 同gui_args函数
        :return:
        """
        task = convert_to_underscore(task)
        task = getattr(self, task, None)
        if task is None:
            logger.warning(f'{task} is no inexistence')
            return {}

        def extract_groups(sch):
            # 从schema 中提取未解析的group的数据
            # properties = properties_groups(sch)
            results = {}
            properties = {}
            for key, value in sch["properties"].items():
                if 'items' in value:
                    properties[key] = re.search(r"/([^/]+)$", value['items']['$ref']).group(1)
                else:
                    properties[key] = re.search(r"/([^/]+)$", value['$ref']).group(1)

            for key, value in properties.items():
                results[key] = sch["$defs"][value]
            return results

        def merge_value(groups, jsons, definitions) -> list[dict]:
            # 将 groups的参数，同导出的json一起合并, 用于前端显示
            result = []
            for key, value in groups["properties"].items():
                # deal with exclude 
                if key in jsons and jsons[key] == 0xABCDEF:
                    continue

                item = {}
                item["name"] = key
                item["title"] = value["title"] if "title" in value else inflection.underscore(key)
                if "description" in value:
                    item["description"] = value["description"]
                item["default"] = value["default"]
                item["value"] = jsons[key] if key in jsons else value["default"]
                item["type"] = value["type"] if "type" in value else "enum"
                if '$ref' in value:  # list
                    enum_key = re.search(r"/([^/]+)$", value['$ref']).group(1)
                    item["enumEnum"] = definitions[enum_key]["enum"]
                # if 'allOf' in value:
                #     enum_key = re.search(r"/([^/]+)$", value['allOf'][0]['$ref']).group(1)
                #     item["enumEnum"] = definitions[enum_key]["enum"]
                result.append(item)
            return result

        schema = task.model_json_schema()
        groups = extract_groups(schema)
        groups_value = groups.copy()

        result: dict[str, list] = {}
        for key, value in task.model_dump(context={'hide': True}).items():
            if key not in groups:
                for group_name in groups.keys():
                    if group_name in key:
                        groups_value[key] = groups[group_name]
            result[key] = merge_value(groups_value[key], value, schema["$defs"])

        self._inject_desktop_handle_options(result)
        return result

    def _inject_desktop_handle_options(self, result: dict) -> None:
        """桌面模式下把 handle 就地改成"已开客户端窗口"下拉，供界面选择而非手填 PID。

        仅当 script.device.serial == 'desktop' 时注入：handle 是桌面与模拟器共用
        字段，模拟器模式下需要保留 auto/窗口标题/HWND 的自由文本输入，因此那边的
        schema 输出保持原样不动。
        字段类型仍是 str，只是在返回给界面的字典里补上候选项，pydantic 校验不受影响。
        """
        device_items = result.get('device')
        if not device_items:
            return
        if getattr(self.script.device, 'serial', '') != 'desktop':
            return
        try:
            from module.device.handle import desktop_window_option, list_desktop_windows
            windows = list_desktop_windows()
        except Exception as e:
            # 枚举依赖 win32 且只服务于界面展示，失败时退回手填输入框，不能拖垮设置页
            logger.warning(f'list desktop windows failed, handle stays a text input: {e}')
            return

        options = [desktop_window_option(w) for w in windows]
        current = ''
        for item in device_items:
            if item['name'] == 'handle':
                current = str(item.get('value') or '')
                break
        # 落盘的 handle 是纯 PID，而候选项是带标题坐标的展示串。界面靠"值等于某个
        # 候选项"来选中，所以要把当前值换成同款展示串，否则下拉会显示为空白
        display = ''
        for window, option in zip(windows, options):
            if str(window['pid']) == current:
                display = option
                break
        # 已绑定的 PID 此刻不在枚举结果里（客户端未启动，或重启后换了 PID）：
        # 仍把原值作为候选保留，让界面显示出"配置里存的是谁"而不是静默变空
        if current and not display:
            display = current
            options.append(current)
        # 空串候选用于"解除绑定"，同时保证 handle 为空时下拉有项可选中
        options.insert(0, '')

        for item in device_items:
            if item['name'] != 'handle':
                continue
            item['type'] = 'enum'
            item['enumEnum'] = options
            item['value'] = display
            break


if __name__ == "__main__":
    c = ConfigModel()
    print(c.script_task('GuildBanquet'))

