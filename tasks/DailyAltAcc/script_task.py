# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time

from module.logger import logger
from module.exception import TaskEnd

from tasks.GameUi.page import page_main
from tasks.GameUi.assets import GameUiAssets
from tasks.DailyAltAcc.config import MSGType
from tasks.WantedQuests.assets import WantedQuestsAssets
from tasks.RichMan.guild import Guild
from tasks.RichMan.config import GuildStore
from tasks.WeeklyTrifles.script_task import ScriptTask as WeeklyTrifles
from tasks.KekkaiActivation.script_task import ScriptTask as KekkaiActivation
from tasks.KekkaiUtilize.script_task import ScriptTask as KekkaiUtilize
from tasks.Utils.config_enum import ShikigamiClass
from tasks.KekkaiActivation.config import CardType
from tasks.KekkaiUtilize.config import UtilizeRule, SelectFriendList

# Sub-tasks
from tasks.DailyAltAcc.courtyard import Courtyard
from tasks.DailyAltAcc.mail import Mail
from tasks.DailyAltAcc.donatejade import Donatejade
from tasks.DailyAltAcc.cooperation import Cooperation
from tasks.DailyAltAcc.returngift import Returngift
from tasks.DailyAltAcc.alliedteam import Alliedteam
from tasks.DailyAltAcc.mshop import Mshop
from tasks.DailyAltAcc.tree import Tree
from tasks.DailyAltAcc.summon_up import SummonUp
from tasks.DailyAltAcc.trialbattle import Trialbattle


class ScriptTask(Courtyard, Mail, Donatejade, Cooperation,
                 Returngift, Alliedteam, Mshop, Tree,
                 SummonUp, Trialbattle,
                 Guild, WeeklyTrifles):
    account_info: dict = None
    
    def run(self):
        
 
        con = self.get_config()
        self.msg = []
        net_normal_flag = False
        retry_count = 0
        while 1:    
            self.screenshot()
            if  retry_count >=10:
                self.msg.append([MSGType.neterror, "网络错误"])
                raise TaskEnd(self.msg)
            if self.appear(self.I_NET_NORMAL_FLAG,interval=1):
                net_normal_flag = True
                continue

            if self.appear_then_click(self.I_NET_CHECK,action=self.C_NET_CLICK,interval=1):
                time.sleep(5)
                retry_count += 1
                self.screenshot()

            if self.appear_then_click(WantedQuestsAssets.I_WQ_SEAL,interval=1) or self.appear_then_click(WantedQuestsAssets.I_WQ_DONE,interval=1):
                continue

            if self.appear(self.I_UI_BACK_RED):
                self.device.click_record_clear()
                self.ui_click_until_disappear(self.I_UI_BACK_RED,interval=3)
                if net_normal_flag:
                    break
                continue
        delay_time = 0
        self.screenshot()
        if self.ui_get_current_page() != page_main:
            self.ui_goto(page_main)

        if con.daily_alt_acc_config.courtyard_enable:
            if not self.run_courtyard():
                while 1:
                    self.screenshot()
                    if self.appear(GameUiAssets.I_CHECK_MAIN) or self.appear(self.I_M_MAIN_TO_MAIL):
                        break
                    if self.appear_then_click(self.I_TASK_TO_MAIN, interval=1):
                        time.sleep(1)
                        continue
                time.sleep(1)
                self.screenshot()
                if self.ui_get_current_page() != page_main:
                    self.ui_goto(page_main)
                
            delay_time += 10
        if con.daily_alt_acc_config.mail_enable:
            self.run_mail()
            delay_time += 5
        if con.daily_alt_acc_config.cooperation_enable:
            self.run_cooperation()
            delay_time += 3
        if con.daily_alt_acc_config.donatejade_enable:
            self.run_donatejade()
            delay_time += 10
        if con.daily_alt_acc_config.returngift_enable:
            if delay_time < 10:
                time.sleep(10-delay_time)
            self.run_returngift()
        if con.daily_alt_acc_config.weekaward_enable:
            xzconfig= GuildStore(enable=True,mystery_amulet=True,black_daruma_scrap=False,skin_ticket=0)
            self.execute_guild(xzconfig)
            self.execute_mall()
            self._share_collect()
        if con.daily_alt_acc_config.mysteryshop_enable:
            self.run_mysteryshop()
            # 执行挂卡（只执行核心逻辑，避免TaskEnd）
        if con.daily_alt_acc_config.tree_planting_enable > 0:
            self.run_tree_planting()
        if con.daily_alt_acc_config.trialbattle_enable:
            self.run_trialbattle()
        if con.daily_alt_acc_config.summon_up_enable:
            self.run_summon_up()

        if con.daily_alt_acc_config.kekkaiActivation_enable:
            try:
                activation_task = KekkaiActivation(self.config, self.device)
                activation_conf=activation_task.config.kekkai_activation.activation_config
                activation_conf.card_type=CardType.DAILY
                activation_conf.min_taiko_num=1
                activation_conf.exchange_before=False
                activation_conf.exchange_max=False
                activation_conf.card_not_found_count=0
                activation_conf.shikigami_class=ShikigamiClass.MATERIAL
                activation_task.run()
            except TaskEnd:
                pass  # 忽略挂卡任务的结束信号
        if con.daily_alt_acc_config.KekkaiUtilize_enable:    
            # 执行蹭卡
            try:
                utilize_task = KekkaiUtilize(self.config, self.device)
                # 确保在运行前修改配置
                utilize_task.config.kekkai_utilize.utilize_config.utilize_rule = UtilizeRule.DAILY
                # 同时也设置其他参数
                utilize_task.config.kekkai_utilize.utilize_config.select_friend_list = SelectFriendList.SAME_SERVER
                utilize_task.config.kekkai_utilize.utilize_config.shikigami_class = ShikigamiClass.MATERIAL
                utilize_task.config.kekkai_utilize.utilize_config.shikigami_order = 1
                utilize_task.config.kekkai_utilize.utilize_config.harvest_guild_max_times = 0
                utilize_task.config.kekkai_utilize.utilize_config.utilize_enable = True
                utilize_task.config.kekkai_utilize.utilize_config.guild_ap_enable = False
                utilize_task.config.kekkai_utilize.utilize_config.guild_assets_enable = False
                utilize_task.config.kekkai_utilize.utilize_config.box_ap_enable = False
                utilize_task.config.kekkai_utilize.utilize_config.box_exp_enable = False
                utilize_task.config.kekkai_utilize.utilize_config.box_exp_waste = False
                utilize_task.config.kekkai_utilize.utilize_config.exchange_before = False
                utilize_task.run()
            except TaskEnd as msg:
                # 直接将KekkaiUtilize的消息透传给Daily，不做额外处理
                if msg.args and msg.args[0]:  # 如果TaskEnd带有参数且不为空
                    for msg_item in msg.args[0]:
                        # 直接将消息添加到当前任务的消息列表中
                        self.msg.append(msg_item)
                pass  # 如果蹭卡任务也有TaskEnd，也需要处理
        if con.daily_alt_acc_config.alliedteam_battle_enable or con.daily_alt_acc_config.alliedteam_ap_enable:
            self.run_alliedteam(con.daily_alt_acc_config.alliedteam_battle_enable,con.daily_alt_acc_config.alliedteam_ap_enable)

        self.set_next_run(task='DailyAltAcc', finish=True, success=True)
        logger.info(self.msg)
        raise TaskEnd (self.msg)


if __name__ == "__main__":
    from module.config.config import Config
    from module.device.device import Device
    from module.base.utils import load_image
    c = Config('oas3')
    d = Device(c)
    self = ScriptTask(c, d)
    self.screenshot()
    self.run_summon_up()   
