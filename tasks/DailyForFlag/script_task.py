# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import re
import time
from cached_property import cached_property
from datetime import timedelta, datetime
from typing import List
from module.base.timer import Timer
from module.atom.image_grid import ImageGrid
from module.logger import logger
from module.exception import TaskEnd

from tasks.GameUi.game_ui import GameUi
from tasks.DailyForFlag.assets import DailyForFlagAssets
from tasks.DailyForFlag.config import DailyForFlag
from tasks.GameUi.page import page_main, page_guild , page_team,page_mall
from tasks.KekkaiUtilize.assets import KekkaiUtilizeAssets
from tasks.Restart.login import LoginHandler
from tasks.WantedQuests.config import CooperationType
from tasks.WantedQuests.assets import WantedQuestsAssets
from tasks.GameUi.assets import GameUiAssets
from tasks.GlobalGame.assets import GlobalGameAssets
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.RichMan.guild import Guild
from tasks.RichMan.mall.mall import Mall
from module.base.utils import point2str
from tasks.RichMan.config import GuildStore,Consignment
from tasks.WeeklyTrifles.config import Trifles
from tasks.WeeklyTrifles.assets import WeeklyTriflesAssets
from tasks.WeeklyTrifles.script_task import ScriptTask as WeeklyTrifles
import random




class ScriptTask(GeneralBattle,Guild,WeeklyTrifles,Mall,GameUi,LoginHandler,WantedQuestsAssets,GlobalGameAssets,DailyForFlagAssets,):
    account_info: dict= None
    def run(self):
        con = self.get_config()
        
        if con.daily_for_flag_config.tingyuan_enable:
            self.run_tingyuan()
        if con.daily_for_flag_config.mail_enable:
            self.run_mail()
        if con.daily_for_flag_config.xiezuo_enable:
            self.run_xiezuo()
        if con.daily_for_flag_config.juangou_enable:
            self.run_juangou()
        if con.daily_for_flag_config.tongxin_battle_enable or con.daily_for_flag_config.tongxin_ap_enable:
            self.run_tongxing(con.daily_for_flag_config.tongxin_battle_enable,con.daily_for_flag_config.tongxin_ap_enable)
        if con.daily_for_flag_config.huili_enable:
            self.run_huili()
        if con.daily_for_flag_config.weekaward_enable:
            xzconfig= GuildStore(enable=True,mystery_amulet=True,black_daruma_scrap=False,skin_ticket=0)
            self.execute_guild(xzconfig)
            self.execute_mall()
            self._share_collect()


        self.set_next_run(task='DailyForFlag', finish=True, success=True)
        raise TaskEnd ("DailyForFlag")
    def run_juangou(self):
        if self.ui_get_current_page() != page_main:
            self.ui_goto(page_main)
        self.ui_goto(page_guild)
        self.goto_realm()
        self.juangou()
        self.back_guild()
        if self.ui_get_current_page() != page_main:
            self.ui_goto(page_main)
    
    def juangou(self):
        retry_count = 0 
        while 1:
            self.screenshot()
            if retry_count >= 3:
                break
            if self.appear_then_click(self.I_AWARD, interval=1):
                break
            if self.appear(self.I_LIMIT) and self.appear_then_click(self.I_CLICK_DONATE, interval=1):
                continue
            if not self.appear(self.I_LIMIT) and self.appear_then_click(self.I_ADD,interval=0.5):
                continue
            if not self.appear(self.I_ADD) and self.appear_then_click(self.I_DONATE, interval=1):
                retry_count += 1
                continue
            
            
    def goto_realm(self):
        """
        从寮的主界面进入寮信息界面
        :return:
        """
        while 1:
            self.screenshot()
            if self.appear(self.I_DONATE):
                break
            if self.appear_then_click(KekkaiUtilizeAssets.I_PLANT_TREE_CLOSE):
                continue
            if self.appear_then_click(KekkaiUtilizeAssets.I_GUILD_INFO, interval=1):
                continue
    def back_guild(self):
        """
        回到寮的界面
        :return:
        """
        while 1:
            self.screenshot()

            if self.appear(KekkaiUtilizeAssets.I_GUILD_INFO):
                break
            if self.appear(KekkaiUtilizeAssets.I_GUILD_REALM):
                break
            if self.appear_then_click(KekkaiUtilizeAssets.I_PLANT_TREE_CLOSE):
                continue
            if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                continue
            if self.appear_then_click(self.I_UI_BACK_BLUE, interval=1):
                continue
            if self.appear_then_click(self.I_UI_BACK_YELLOW, interval=1):
                continue
    
    def run_tingyuan(self):
        if self.ui_get_current_page() != page_main:
            self.ui_goto(page_main)
        retry_count = 0
        while 1:
            self.screenshot()
            if retry_count >= 4:
                if self.appear_then_click(self.I_TASK_TO_MAIN, interval=1):
                    break
            if self.appear_then_click(self.I_MIAN_TO_TASK):
                continue
            if self.appear_then_click(self.I_FINISH, interval=1):
                retry_count += 1
                continue
            if self.appear_then_click(self.I_NORMAL, interval=1):
                continue
            if self.appear_then_click(self.I_SUCCESS, interval=1):
                continue
            if self.ui_reward_appear_click():
                continue
            # 获得奖励
            if self.appear_then_click(self.I_UI_AWARD, interval=0.2):
                continue
            if self.appear(self.I_LOGIN_RED_CLOSE):
                self.click(self.I_LOGIN_RED_CLOSE, interval=2)
                continue
        if self.ui_get_current_page() != page_main:
            self.ui_goto(page_main)
    def harvest_mail(self) -> bool:
        logger.info('Harvest mail')
        retry_count = 0
        while 1:
            if retry_count >= 3:
                break
            self.screenshot()
            if self.appear(self.I_M_PAGE_MAIL):
                break
            if self.appear_then_click(self.I_M_MAIN_TO_MAIL, interval=1.5):
                retry_count += 1
                continue

        logger.info('Exec harvest mail')
        retry_count = 0
        while 1:
            if retry_count >= 3:
                break
            self.screenshot()
            if self.appear_then_click(self.I_HARVEST_MAIL_CONFIRM, interval=0.8):
                break
            if self.appear_then_click(self.I_READ_ALL_MAIL, interval=1.5):
                continue
            if self.appear_then_click(self.I_HARVEST_MAIL_ALL, interval=1.5):
                continue
            if self.appear_then_click(self.I_MAIL_RED_POINT, interval=4):
                continue
            if self.appear(self.I_M_PAGE_MAIL):
                retry_count += 1
                break
        retry_count = 0    
        while 1:
            self.screenshot() 
            if retry_count >= 3:
                break
            if self.appear_then_click(self.I_M_AWARD, interval=1.5):
                continue
            if self.appear_then_click(self.I_M_BACK_RED, interval=1.5):
                retry_count += 1
                break
        return True
    def run_mail(self):
        if self.ui_get_current_page() != page_main:
            self.ui_goto(page_main)
        return self.harvest_mail()

    def run_tongxing(self, battle_enable, ap_enable):
        if self.ui_get_current_page() != page_main:
            self.ui_goto(page_main)
        self.ui_goto(page_team)
        if ap_enable:
            self.run_tongxing_ap()
        if battle_enable:
            self.run_tongxing_battle()
    def run_tongxing_ap(self):    
        logger.info('开始执行补体力任务')
        retry_count = 0
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_TO_TEAM, interval=1):
                continue
            if self.appear_then_click(self.I_TO_AP, interval=1):
                continue
            if self.appear(self.I_ENSURE_AP):
                self.ui_click_until_disappear(self.I_ENSURE_AP, interval=1)
                break
            if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                continue
            if self.appear_then_click(self.I_LIST_MEMBER, interval=1):
                continue
            if self.appear_then_click(self.I_SAVE_ALL, interval=1):
                continue
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_UI_BACK_YELLOW, interval=1):
                continue
            if self.appear_then_click(self.I_BACK_BLACK, interval=1):
                continue
            if self.ui_get_current_page() == page_main:
                break
            else:
                self.ui_goto(page_main)
    def run_tongxing_battle(self):    
        logger.info('开始执行战斗任务')
        while 1:
            self.screenshot()
            if self.appear(self.I_FORM_OVER):
                break
            if self.appear_then_click(self.I_TO_TEAM, interval=1):
                continue
            if self.appear_then_click(self.I_FORM, interval=1):
                continue
            if self.appear_then_click(self.I_INVITE, interval=1):
                continue
            
        while 1:
            self.screenshot()
            if self.appear(self.I_BATTLE, interval=1):  
                break
            if self.appear_then_click(self.I_CREATE_AGAIN, interval=1):
                continue
            if self.appear_then_click(self.I_CREATE_TEAM, interval=1):
                continue
            if self.appear_then_click(self.I_SELECT_LEVEL, interval=1):
                    continue
        self.run_alone()

    def check_lock(self, lock: bool = True) -> bool:
        """
        检查是否锁定阵容, 要求在觉醒界面
        :param lock:
        :return:
        """
        logger.info('Check lock: %s', lock)
        if lock:
            while 1:
                self.screenshot()
                if self.appear(self.I_LOCK):
                    return True
                if self.appear_then_click(self.I_UNLOCK, interval=1):
                    continue
        else:
            while 1:
                self.screenshot()
                if self.appear(self.I_UNLOCK):
                    return True
                if self.appear_then_click(self.I_LOCK, interval=1):
                    continue
    def run_alone(self):
        def is_in_evozone(screenshot=False) -> bool:
            if screenshot:
                self.screenshot()
            return self.appear(self.I_BATTLE)
        logger.info('Start run alone')
        self.check_lock(True)
        while 1:
            self.screenshot()

            if not is_in_evozone():
                continue
            if self.current_count >= 30:
                logger.info('Orochi count limit out')
                break
            logger.info('Orochi count limit 111')
            # 点击挑战
            while 1:
                self.screenshot()
                if self.appear_then_click(self.I_BATTLE, interval=1):
                    pass
                if not self.appear(self.I_BATTLE):
                    self.run_general_battle(config=self.config.daily_for_flag.general_battle_config)
                    break
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_EXIT_2, interval=1):
                continue
            if self.appear_then_click(self.I_ENSURE_EXIT, interval=1):
                continue
            if self.appear_then_click(self.I_UI_BACK_YELLOW,interval=1):
                continue
            if self.appear_then_click(self.I_BACK_BLACK,interval=1):
                continue
            if self.ui_get_current_page() == page_main:
                break
            else:
                self.ui_goto(page_main)
        self.ui_goto(page_main)
    def run_xiezuo(self):   
        self.account_info = self.get_account_info()
        # 打开悬赏封印 界面
        if self.ui_get_current_page() != page_main:
            self.ui_goto(page_main)
        while 1:
            self.screenshot()
            if self.appear(WantedQuestsAssets.I_TRACE_ENABLE) or self.appear(WantedQuestsAssets.I_TRACE_DISABLE) or self.appear(self.I_UI_BACK_RED):
                break
            if self.appear_then_click(WantedQuestsAssets.I_WQ_SEAL, interval=1):
                continue
            if self.appear_then_click(WantedQuestsAssets.I_WQ_DONE, interval=1):
                continue
        self.get_cooperation_info()
        self.screenshot()
        self.appear_then_click(self.I_UI_BACK_RED)
        if self.ui_get_current_page() != page_main:
            self.ui_goto(page_main)

    def get_account_info(self):
        if self.ui_get_current_page() != page_main:
            self.ui_goto(page_main)
        self.ui_click(self.C_TO_ACCOUNT,stop=self.I_PAGE_ACCOUNT, interval=1)
        self.screenshot()
        if self.appear(self.I_PAGE_ACCOUNT):
            account_info = self.O_ACC_NAME.ocr(self.device.image)
            logger.info(f"get account info : {account_info}")
        self.ui_click_until_disappear(self.I_UI_BACK_RED, interval=1)
        return account_info
    def get_cooperation_info(self) -> List:
        """
            获取协作任务详情
        @return: 协作任务类型与邀请按钮
        """
        self.screenshot()
        retList = []
        i = 0
        for index in range(3):
            btn = self.__getattribute__("I_WQ_INVITE_" + str(index + 1))
            btn2 = self.__getattribute__("I_REAL_FLAG_" + str(index + 1))
            normal_flag=self.appear(btn)
            real_flag=self.appear(btn2)
            if not normal_flag and not real_flag:
                break
            if self.appear(self.__getattribute__("I_WQ_COOPERATION_TYPE_JADE_" + str(index + 1))):
                retList.append({'type': CooperationType.Jade, 'inviteBtn': btn})
                if real_flag:
                    logger.info(f"find real jade cooperation ")
                    self.push_notify(content=f"   {self.account_info} 发现现世勾协", title="协作任务提醒")
                    self.config.notifier.push(content=f"   {self.account_info} 发现现世勾协", title="协作任务提醒")
                else:
                    logger.info(f"find  jade cooperation ")
                    self.push_notify(content=f"   {self.account_info} 发现普通勾协", title="协作任务提醒")
                    self.config.notifier.push(content=f"   {self.account_info} 发现普通勾协", title="协作任务提醒")
                continue
            if self.appear(self.__getattribute__("I_WQ_COOPERATION_TYPE_DOG_FOOD_" + str(index + 1))):
                retList.append({'type': CooperationType.Food, 'inviteBtn': btn})
                logger.info(f"find dog food cooperation ")
                continue
            if self.appear(self.__getattribute__("I_WQ_COOPERATION_TYPE_CAT_FOOD_" + str(index + 1))):
                retList.append({'type': CooperationType.Food, 'inviteBtn': btn})
                logger.info(f"find cat food cooperation ")
                continue
            if self.appear(self.__getattribute__("I_WQ_COOPERATION_TYPE_SUSHI_" + str(index + 1))):
                retList.append({'type': CooperationType.Sushi, 'inviteBtn': btn})
                if real_flag:
                    logger.info(f"find real sushi cooperation ")
                    self.push_notify(content=f"   {self.account_info} 发现现世体协", title="协作任务提醒")
                    self.config.notifier.push(content=f"   {self.account_info} 发现现世体协", title="协作任务提醒")
                else:
                    logger.info(f"find  sushi cooperation ")
                    self.push_notify(content=f"   {self.account_info} 发现普通体协", title="协作任务提醒")
                    self.config.notifier.push(content=f"   {self.account_info} 发现普通体协", title="协作任务提醒")
                continue
            # NOTE 因为食物协作里面也有金币奖励 ,所以判断金币协作放在最后面
            if self.appear(self.__getattribute__("I_WQ_COOPERATION_TYPE_GOLD_" + str(index + 1))):
                retList.append({'type': CooperationType.Gold, 'inviteBtn': btn})
                logger.info(f"find gold cooperation ")
                continue
        logger.info(f"get cooperation size {len(retList)}")
        return retList

    def run_huili(self):
        if self.ui_get_current_page() != page_main:
            self.ui_goto(page_main)
        self.ui_goto(page_guild)
        retry_count = 0
        while 1:
            self.screenshot()
            if retry_count >= 3:
                self.appear_then_click(self.I_TO_THINK, threshold=0.9, interval=1)
                break
            if self.appear_then_click(self.I_H_BACK_RED, interval=1):
                logger.info(f"I_BACK_RED found ")
                continue
            if self.appear(self.I_TO_THINK, threshold=0.9, interval=1):
                retry_count += 1
                logger.info(f"I_TO_THINK found ")
                continue
            if self.appear_then_click(self.I_QIYUAN, interval=1):
                #self.wait_until_appear_then_click(self.I_BACK_RED,wait_time=1)
                continue
        retry_count = 0
        while 1:
            self.screenshot()
            logger.info(f"screenshot found ")
            if retry_count >= 3:
                break
            if not self.appear(self.I_FLAG_THINK) and self.appear(self.I_TO_THINK,):
                self.appear_then_click(self.I_TO_THINK, interval=3)
                continue
            if  self.appear(self.I_BTN_ENSURE):
                self.ui_click_until_disappear(self.I_BTN_ENSURE, interval=1)
                break
            if self.appear_then_click(self.I_BTN_THINK, interval=1):
                continue
            if self.appear(self.I_FLAG_THINK):
                retry_count += 1
                continue
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_H_BACK_RED2, interval=1):
                continue
            if self.appear_then_click(self.I_BACK_YELLOW, interval=1):
                continue
            if self.ui_get_current_page() == page_main:
                break
            else:
                self.ui_goto(page_main)

    def execute_mall(self):
            logger.hr('Mall', 1)
            self.ui_get_current_page()
            self.ui_goto(page_mall, confirm_wait=2.5)
            # 寄售屋
            config = Consignment(enable=True,buy_sale_ticket=True)
            self.execute_consignment(config)
            
            # 退出
            if self.ui_get_current_page() != page_main:
                self.ui_goto(page_main)    
    def get_config(self):
        return self.config.daily_for_flag




if __name__ == "__main__":
    from module.config.config import Config
    from module.device.device import Device

    c = Config('switch')
    d = Device(c)
    t = ScriptTask(c, d)
    for i in range(10):
        t.perform_swipe_action()
    t.recive_guild_ap_or_assets()
    # t.check_utilize_add()
    # t.check_card_num('勾玉', 67)
    # t.screenshot()
    # print(t.appear(t.I_BOX_EXP, threshold=0.6))
    # print(t.appear(t.I_BOX_EXP_MAX, threshold=0.6))
