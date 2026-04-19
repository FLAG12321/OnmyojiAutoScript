# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import os
import re
import time
import cv2
import numpy as np
from PIL import Image
from cached_property import cached_property
from datetime import timedelta, datetime
from typing import List
from module.base.timer import Timer
from module.logger import logger
from module.exception import TaskEnd

from module.atom.image import RuleImage
from tasks.GameUi.game_ui import GameUi
from tasks.DailyForFlag.assets import DailyForFlagAssets
from tasks.DailyForFlag.config import GoodsType,CoinType,MSGType
from tasks.GameUi.page import page_main, page_guild , page_team,page_mall,page_friends
from tasks.GameUi.assets import GameUiAssets
from tasks.KekkaiUtilize.assets import KekkaiUtilizeAssets
from tasks.Restart.login import LoginHandler
from tasks.WantedQuests.config import CooperationType
from tasks.WantedQuests.assets import WantedQuestsAssets
from tasks.GlobalGame.assets import GlobalGameAssets
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.RichMan.guild import Guild
from tasks.RichMan.mall.mall import Mall
from tasks.RichMan.config import GuildStore,Consignment
from tasks.WeeklyTrifles.script_task import ScriptTask as WeeklyTrifles
from tasks.MysteryShop.assets import MysteryShopAssets
from tasks.KekkaiActivation.script_task import ScriptTask as KekkaiActivation
from tasks.KekkaiUtilize.script_task import ScriptTask as KekkaiUtilize
from tasks.Utils.config_enum import ShikigamiClass
from tasks.KekkaiActivation.config import CardType
from tasks.KekkaiUtilize.config import UtilizeRule, SelectFriendList
from tasks.Plotline.assets import PlotlineAssets
from tasks.Component.GeneralRoom.general_room import GeneralRoom
class ScriptTask(GeneralBattle,GeneralRoom,Guild,WeeklyTrifles,Mall,GameUi,LoginHandler,WantedQuestsAssets,GlobalGameAssets,DailyForFlagAssets,):
    account_info: dict= None
    msg: list = []
    
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

        if con.daily_for_flag_config.tingyuan_enable:
            if not self.run_tingyuan():
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
        if con.daily_for_flag_config.mail_enable:
            self.run_mail()
            delay_time += 5
        if con.daily_for_flag_config.xiezuo_enable:
            self.run_xiezuo()
            delay_time += 3
        if con.daily_for_flag_config.juangou_enable:
            self.run_juangou()
            delay_time += 10
        if con.daily_for_flag_config.huili_enable:
            if delay_time < 10:
                time.sleep(10-delay_time)
            self.run_huili()
        if con.daily_for_flag_config.weekaward_enable:
            xzconfig= GuildStore(enable=True,mystery_amulet=True,black_daruma_scrap=False,skin_ticket=0)
            self.execute_guild(xzconfig)
            self.execute_mall()
            self._share_collect()
        if con.daily_for_flag_config.mysteryshop_enable:
            self.run_mysteryshop()
            # 执行挂卡（只执行核心逻辑，避免TaskEnd）
        if con.daily_for_flag_config.tree_planting_enable > 0:
            self.run_tree_planting()

        if con.daily_for_flag_config.kekkaiActivation_enable:
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
        if con.daily_for_flag_config.KekkaiUtilize_enable:    
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
        if con.daily_for_flag_config.tongxin_battle_enable or con.daily_for_flag_config.tongxin_ap_enable:
            self.run_tongxing(con.daily_for_flag_config.tongxin_battle_enable,con.daily_for_flag_config.tongxin_ap_enable)

        self.set_next_run(task='DailyForFlag', finish=True, success=True)
        logger.info(self.msg)
        raise TaskEnd (self.msg)
    def run_juangou(self):
        self.screenshot()
        if self.ui_get_current_page() != page_main:
            self.ui_goto(page_main)
        self.ui_goto(page_guild)
        self.goto_realm()
        self.juangou()
        self.back_guild()
        self.screenshot()
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
        self.screenshot()
        if self.ui_get_current_page() != page_main:
            self.ui_goto(page_main)
        start_time = time.time()
        while time.time() - start_time < 5:
            self.screenshot()
            if self.appear_rgb(self.I_FINISH):
                self.appear_then_click(self.I_FINISH, interval=1)
                start_time = time.time()
                break
            if self.appear_then_click(self.I_MIAN_TO_TASK, interval=1):
                start_time = time.time()
                continue
            if self.appear_then_click(self.I_T_SPECIAL_FLAG,action=self.C_T_TONORMAL, interval=1):
                start_time = time.time()
                continue
        if time.time() - start_time>=5:
            return False
        click_count = 0
        while 1:
            self.screenshot()
            if self.appear(GameUiAssets.I_CHECK_MAIN) or self.appear(self.I_M_MAIN_TO_MAIL):
                break
            if click_count >= 3:
                if self.appear_then_click(self.I_TASK_TO_MAIN, interval=1):
                    time.sleep(1)
                    continue
            if self.get_award_daliy():
                click_count = 0
                continue
            if self.appear_then_click(self.I_SUCCESS,action=self.C_T_EXIT_SUCCESS, interval=1):
                click_count = 0
                continue
            if self.appear_rgb(self.I_FINISH):
                if self.appear_then_click(self.I_FINISH, interval=1):
                    click_count += 1
                    continue
        time.sleep(1)
        self.screenshot()
        if self.ui_get_current_page() != page_main:
            self.ui_goto(page_main)
    def harvest_mail(self) -> bool:
        logger.info('Harvest mail')

        start_time = time.time()
        while time.time()-start_time < 3:
            self.screenshot()
            if self.appear(self.I_M_PAGE_MAIL):
                start_time = time.time()
                break
            if self.appear_then_click(self.I_M_MAIN_TO_MAIL, interval=1.5):
                start_time = time.time()
                continue
        if time.time()-start_time >= 5:
            return False

        logger.info('Exec harvest mail')
        start_time = time.time()
        while time.time()-start_time < 3:
            self.screenshot()
            if self.appear_then_click(self.I_M_ENSURE_GET, interval=0.8):
                time.sleep(1)
                start_time = time.time()
                logger.info('I_M_ENSURE_GET success')
                break
            if self.appear_rgb(self.I_HARVEST_MAIL_ALL):
                if self.appear_then_click(self.I_HARVEST_MAIL_ALL, interval=1.5):
                    logger.info('I_HARVEST_MAIL_ALL success')
                start_time = time.time()
                continue
        if time.time()-start_time >= 3:
            self.screenshot()
            self.appear_then_click(self.I_M_BACK_RED, interval=1.5)
            return True
        logger.info('harvest mail get award')
        while 1:
            self.screenshot()
            if self.appear(GameUiAssets.I_CHECK_MAIN) or self.appear(self.I_M_MAIN_TO_MAIL):
                break
            if self.get_award_daliy():
                continue
            if self.appear_then_click(self.I_M_ENSURE_GET, interval=1):
                continue
            if self.appear_then_click(self.I_M_BACK_RED, interval=1.5):
                continue 
        return True
    def run_mail(self):
        self.screenshot()
        if self.ui_get_current_page() != page_main:
            self.ui_goto(page_main)
        result=self.harvest_mail()
        time.sleep(1)
        self.screenshot()
        if self.ui_get_current_page() != page_main:
            self.ui_goto(page_main)
        return result

    def run_tongxing(self, battle_enable, ap_enable):
        self.screenshot()
        if self.ui_get_current_page() != page_main:
            self.ui_goto(page_main)
        self.ui_goto(page_team)
        if ap_enable:
            self.run_tongxing_ap()
        if battle_enable:
            self.run_tongxing_battle()
            self.return_to_main()
    def run_tongxing_ap(self):    
        logger.info('开始执行补体力任务')
        start_time = time.time()
        while time.time()-start_time < 5:
            self.screenshot()
            if self.appear_then_click(self.I_TO_TEAM, interval=1):
                start_time = time.time()
                continue
            if self.appear_then_click(self.I_TO_AP, interval=1):
                start_time = time.time()
                continue
            if self.appear(self.I_ENSURE_AP):
                self.ui_click_until_disappear(self.I_ENSURE_AP, interval=1)
                start_time = time.time()
                break
            if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                start_time = time.time()
                continue
            if self.appear_then_click(self.I_LIST_MEMBER, interval=1):
                start_time = time.time()
                continue
            if self.appear_then_click(self.I_SAVE_ALL, interval=1):
                start_time = time.time()
                continue
    
        while 1:
            self.screenshot()
            if self.appear(GameUiAssets.I_CHECK_MAIN) or self.appear(self.I_M_MAIN_TO_MAIL):
                break
            if self.appear_then_click(self.I_UI_BACK_YELLOW, interval=1):
                continue
            if self.appear_then_click(self.I_BACK_BLACK, interval=1):
                continue
    def is_select_level(self):
        if not self.O_SELECT_LEVEL.ocr(self.device.image)=="觉醒业火轮壹层":
            if not self.check_zones('觉醒业火轮'):
                return False
            while 1:
                self.screenshot()
                if self.O_SELECT_LEVEL.ocr(self.device.image)=="觉醒业火轮壹层":
                    break
                logger.info(f"当前选择关卡:{list(self.O_FLAG_LEVEL.ocr(self.device.image))}")
                if not list(self.O_FLAG_LEVEL.ocr(self.device.image))==[0,0,0,0]:
                    logger.info(f"当前选择关卡:{self.O_SELECT_LEVEL.ocr(self.device.image)}")
                else :
                    self.appear_then_click(self.I_CLICK_EVOZONE, interval=1)
                    continue
                if self.appear_then_click(self.I_CLICK_LEVEL, interval=1):
                    continue
                self.swipe(self.S_SELECT_LEVEL,3)
        return True           
    def run_tongxing_battle(self):    
        logger.info('开始执行战斗任务')
        if not self.is_select_level():
            return False
        start_time = time.time()
        while time.time()-start_time < 5:
            self.screenshot()
            if self.appear(self.I_PAGE_INVITE, interval=1):
                start_time = time.time()
                break
            if self.appear_then_click(self.I_TO_TEAM, interval=1):
                start_time = time.time()
                continue
            
            if self.appear_then_click(self.I_INVITE, interval=1):
                start_time = time.time()
                continue
        if time.time()-start_time >= 5:
            return False
        while time.time()-start_time < 5:
            self.screenshot()
            if self.appear(self.I_FORM_OVER):
                start_time = time.time()
                logger.info("邀请完成")
                break
            if len(self.I_INVITE_FRIEND_OVER.match_all_any(self.device.image))<2:
                if self.appear_then_click(self.I_INVITE_FRIEND, interval=1):
                    time.sleep(1)
                start_time = time.time()
                continue
                
            if self.appear_then_click(self.I_FORM, interval=1):
                start_time = time.time()
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
        return True
    def return_to_main(self):   
        while 1:
            self.screenshot()
            if self.appear(GameUiAssets.I_CHECK_MAIN) or self.appear(self.I_M_MAIN_TO_MAIL):
                    break
            if self.appear_then_click(self.I_EXIT_2, interval=1):
                continue
            if self.appear_then_click(self.I_ENSURE_EXIT, interval=1):
                continue
            if self.appear_then_click(self.I_UI_BACK_YELLOW,interval=1):
                continue
            if self.appear_then_click(self.I_BACK_BLACK,interval=1):
                continue
            if self.appear_then_click(self.I_EXIT3, interval=1):
                continue
            if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                continue    
        self.ui_goto(page_main)
        if self.get_config().daily_for_flag_config.tongxin_limit_count == 13:
            self.screenshot()
            self.ui_goto(page_friends)
            while 1:    
                self.screenshot()
                if self.appear(self.I_FRIEND_HELP_FLAG, interval=1):
                    break
                if self.appear_then_click(self.I_FRIEND_HELP,self.C_FRIEND_HELP_CLICK, interval=1):
                    continue
                
            from module.base.utils import save_image
            now=datetime.now()
            folder_name = f'{now.year}_{now.month:02d}_{now.day:02d}'
            if not os.path.exists( f'./{folder_name}'):
                os.mkdir(f'./{folder_name}')
            folder = f'./{folder_name}'
            save_image(self.screenshot(), f'{folder}/{now.hour:02d}-{now.minute:02d}-{now.second:02d}.png')
            run_timer=Timer(5)
            run_timer.start()
            while 1:    
                self.screenshot()
                if run_timer.reached():
                    break
                if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                    break
            self.screenshot()
            if self.ui_get_current_page() != page_main:
                self.ui_goto(page_main)
            


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
        tongxin_limit_count=self.get_config().daily_for_flag_config.tongxin_limit_count
        logger.info(f' tongxin_limit_count: {tongxin_limit_count}')
        self.check_lock(True)
        while 1:
            self.screenshot()

            if not is_in_evozone():
                continue
            if self.current_count >= tongxin_limit_count:
                logger.info('Orochi count limit out')
                break
            # 点击挑战
            while 1:
                self.screenshot()
                if self.appear_then_click(self.I_BATTLE, interval=1):
                    pass
                if not self.appear(self.I_BATTLE):
                    self.run_general_battle(config=self.config.daily_for_flag.general_battle_config)
                    break
        
    def run_xiezuo(self):   
        #self.account_info =[] #self.get_account_info()
        # 打开悬赏封印 界面
        self.screenshot()
        if self.ui_get_current_page() != page_main:
            self.ui_goto(page_main)
        self.screenshot()
        retry_count = 0
        while 1:    
            self.screenshot()
            if  retry_count >3:
                self.screenshot()
                self.ui_goto(page_guild)
                time.sleep(1)
                self.screenshot()
                if self.ui_get_current_page() != page_main:
                    self.ui_goto(page_main)
            if self.appear(WantedQuestsAssets.I_WQ_SEAL,interval=1) or self.appear(WantedQuestsAssets.I_WQ_DONE,interval=1):
                break
            retry_count += 1
            time.sleep(1)
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
        if self.appear(self.I_UI_BACK_RED):
            self.click(self.I_UI_BACK_RED)
            time.sleep(1)
        self.screenshot()
        if self.ui_get_current_page() != page_main:
            self.ui_goto(page_main)

    def get_account_info(self):
        self.screenshot()
        if self.ui_get_current_page() != page_main:
            self.ui_goto(page_main)
        retry_count = 0
        while 1:
            self.screenshot()
            if retry_count > 5:
                logger.info("get account info failed")
                return None
            if self.appear(self.I_PAGE_ACCOUNT):
                account_info = self.O_ACC_NAME.ocr(self.device.image)
                logger.info(f"get account info : {account_info}")
                break
            else:
                self.click(self.C_TO_ACCOUNT)
                retry_count += 1
                time.sleep(1.5)
                continue
        retry_count = 0
        while 1:
            self.screenshot()
            if retry_count > 5:
                self.ui_goto(page_main)
                break
            if self.appear_then_click(self.I_UI_BACK_RED, interval=2):
                retry_count += 1
                continue
            if self.ui_get_current_page()==page_main:
                break
            if not self.appear(self.I_UI_BACK_RED):
                self.screenshot()
                if self.ui_get_current_page()==page_main:
                    self.ui_goto(page_main)
                break
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
                    self.push_notify(content=f"    发现现世勾协", title="协作任务提醒")
                    self.msg.append([MSGType.xiezuo,"发现现世勾协"])
                else:
                    logger.info(f"find  jade cooperation ")
                    self.push_notify(content=f"    发现普通勾协", title="协作任务提醒")
                    self.msg.append([MSGType.xiezuo,"发现普通勾协"])
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
                    self.msg.append([MSGType.xiezuo,"发现现世体协"])
                    self.push_notify(content=f"    发现现世体协", title="协作任务提醒")
                else:
                    logger.info(f"find  sushi cooperation ")
                    self.msg.append([MSGType.xiezuo,"发现普通体协"])
                    self.push_notify(content=f"    发现普通体协", title="协作任务提醒")
                continue
            # NOTE 因为食物协作里面也有金币奖励 ,所以判断金币协作放在最后面
            if self.appear(self.__getattribute__("I_WQ_COOPERATION_TYPE_GOLD_" + str(index + 1))):
                retList.append({'type': CooperationType.Gold, 'inviteBtn': btn})
                logger.info(f"find gold cooperation ")
                continue
        logger.info(f"get cooperation size {len(retList)}")
        return retList

    def run_huili(self):
        self.screenshot()
        if self.ui_get_current_page() != page_main:
            self.ui_goto(page_main)
        self.ui_goto(page_guild)
        retry_count = 0
        while 1:
            self.screenshot()
            if retry_count >= 3:
                self.appear_then_click(self.I_TO_THINK, threshold=0.9, interval=1)
                break
            if self.appear_then_click(self.I_SUDDEN,action=self.C_BACK_RED, interval=1):
                logger.info(f"I_SUDDEN found ")
                continue
            """ if self.appear_then_click(self.I_H_BACK_RED, interval=1):
                logger.info(f"I_BACK_RED found ")
                continue """
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
            if self.appear_then_click(self.I_SUDDEN,action=self.C_BACK_RED, interval=1):
                continue
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_H_BACK_RED2, interval=1):
                continue
            if self.appear_then_click(self.I_BACK_YELLOW, interval=1):
                continue
            if self.appear_then_click(self.I_SUDDEN,action=self.C_BACK_RED, interval=1):
                logger.info(f"I_SUDDEN found ")
                continue
            if self.ui_get_current_page() == page_main:
                break
            else:
                self.ui_goto(page_main)

    def execute_mall(self):
            logger.hr('Mall', 1)
            self.screenshot()
            self.ui_get_current_page()
            self.ui_goto(page_mall, confirm_wait=2.5)
            # 寄售屋
            config = Consignment(enable=True,buy_sale_ticket=True)
            self.execute_consignment(config)
            
            # 退出
            start_time = time.time()
            while time.time()>start_time-5:
                self.screenshot()
                if self.ui_get_current_page() == page_main:
                    break
                if self.appear_then_click(self.I_BACK_YELLOW, interval=1):
                    start_time = time.time()
                    continue
            if self.ui_get_current_page() != page_main:
                self.ui_goto(page_main)
    def run_mysteryshop(self):
        #self.account_info = self.get_account_info()
        self.screenshot()
        if self.ui_get_current_page()!=page_main:
            self.ui_goto(page_main)
        self.ui_goto(page_mall)
        self.ui_click(MysteryShopAssets.I_ME_ENTER, MysteryShopAssets.I_MS_SHARE)
        logger.info('Mysteryshop')

        if not self.MsFind():
            retry_count = 0
            while 1:    
                self.screenshot()
                if retry_count >= 3:
                    break
                if self.appear_then_click(self.I_MS_ENSURE,interval=1):
                    self.screenshot()
                    if not self.appear(self.I_MS_ENSURE):
                        break
                if self.appear_rgb(self.I_MS_REFRESH):
                    self.screenshot()
                    self.appear_then_click(self.I_MS_REFRESH,action=self.C_MS_REFRESH_ACTION,interval=1)
                    continue
                if self.appear(MysteryShopAssets.I_MS_SHARE,interval=2):
                    retry_count += 1
            self.MsFind()
        retry_count = 0
        while 1:
            self.screenshot()
            if retry_count >3:
                break
            if self.appear_then_click(self.I_BACK_Y,interval=2):
                retry_count +=1
                continue
            if not self.appear(self.I_BACK_Y):
                break
    def MsFind(self):
        while self.buy_mall_one(buy_button=MysteryShopAssets.I_MS_TAIKO_OFF_4, buy_check=MysteryShopAssets.I_MS_CHECK_TAIKO_4,
                                money_ocr=self.O_MALL_RESOURCE_5, buy_money=80):
            pass
        while self.buy_mall_one(buy_button=MysteryShopAssets.I_MS_TAIKO_4, buy_check=MysteryShopAssets.I_MS_CHECK_TAIKO_4,
                                money_ocr=self.O_MALL_RESOURCE_5, buy_money=80):
            pass
        while self.buy_mall_one(buy_button=MysteryShopAssets.I_MS_TAIKO_OFF_3, buy_check=MysteryShopAssets.I_MS_CHECK_TAIKO_3,
                                    money_ocr=self.O_MALL_RESOURCE_5, buy_money=45):
            pass
        while self.buy_mall_one(buy_button=MysteryShopAssets.I_MS_TAIKO_3, buy_check=MysteryShopAssets.I_MS_CHECK_TAIKO_3,
                                    money_ocr=self.O_MALL_RESOURCE_5, buy_money=45):
            pass
        
        all_info_list = []
        cointype_and_coinNum_list=self.FindCoinTypeAndCoinNum()
        self.screenshot()
        if self.appear(self.I_MS_ALL_SHEPI):
            logger.info(f"appear: self.I_MS_ALL_SHEPI{all_info_list}")
            info_list=self.FindGoodsType(GoodsType.shepi,cointype_and_coinNum_list)
            if len(info_list) > 0:
                all_info_list.extend(info_list)
        if self.appear(self.I_MS_ALL_FMPI):
            logger.info(f"appear: self.I_MS_ALL_SHEPI{all_info_list}")
            info_list=self.FindGoodsType(GoodsType.fmpi,cointype_and_coinNum_list)
            if len(info_list) > 0:
                all_info_list.extend(info_list)
        if self.get_config().daily_for_flag_config.isflower and self.appear(self.I_MS_ALL_HEISUI):
            logger.info(f"appear I_MS_ALL_HEISUI: {all_info_list}")
            info_list=self.FindGoodsType(GoodsType.heisui,cointype_and_coinNum_list)
            if len(info_list) > 0:
                all_info_list.extend(info_list)
        logger.info(f"FindGoodsType 返回的商品信息: {all_info_list}")
        if len(all_info_list) > 0 and self.InfoFilter(all_info_list):  
            return True
        else:
            logger.info('没有找到物品')
            return False
    def FindGoodsType(self, goodstype: GoodsType,cointype_and_coinNum_list:list):
        all_info_list=[]
        for index in range(8):
            if goodstype == GoodsType.shepi:
                appear_goodstype = getattr(self, f'I_MS_GOODS_SHEPI_{index+1}')
                #logger.info(f"使用蛇皮商品图像文件: {appear_goodstype} {index+1}")
            elif goodstype == GoodsType.fmpi:
                appear_goodstype = getattr(self, f'I_MS_GOODS_FMPI_{index+1}')  # 注意：这里可能需要修正为GOLD
                #logger.info(f"使用逢魔商品图像文件: {appear_goodstype} {index+1}")
            elif goodstype == GoodsType.heisui:
                appear_goodstype = getattr(self, f'I_MS_GOODS_HEISUI_{index+1}')
                #logger.info(f"使用黑碎商品图像文件: {appear_goodstype} {index+1}")
            if self.appear(appear_goodstype):
                #logger.info(f"appear_goodstype: {cointype_and_coinNum_list}")
                all_info_list.append([goodstype,cointype_and_coinNum_list[index][0],cointype_and_coinNum_list[index][1]])
            logger.info(f'FindGoodsType :{all_info_list}')
        return all_info_list    
        pass
    def FindCoinTypeAndCoinNum(self):
        info_list=[ ]
        for index in range(8):
            logger.debug(f"检查第 {index + 1} 个商品位置")
            appear_coin_jade = getattr(self, f'I_MS_PRICE_{index+1}')
            appear_coin_gold = getattr(self, f'I_MS_PRICES_{index+1}')
            appear_coin_num = self.__getattribute__("O_MS_PRICENUM_" + str(index + 1))
            ocr_results = appear_coin_num.detect_and_ocr(self.device.image)
            if ocr_results:
                coin_num = int(ocr_results[0].ocr_text)
            else:
                logger.warning(f"无法识别第 {index + 1} 个位置的数量，使用默认值 0")
                coin_num = 0  # 或者其他合适的默认值
            logger.info(f"数量: {coin_num}")
            if self.appear(appear_coin_jade):
                info_list.append([CoinType.jade,coin_num])
            elif self.appear(appear_coin_gold):
                info_list.append([CoinType.gold,coin_num])
            else:
                info_list.append([CoinType.unknow,coin_num])
        logger.info(f"FindCoinTypeAndCoinNum :{info_list} ")
        return  info_list
    def InfoFilter(self,info_list:list):
        flag :bool= False
        for info in info_list:
            if info[1]==CoinType.gold:
                if info[0]==GoodsType.shepi or info[0]==GoodsType.fmpi:
                    flag=True
                    logger.info(f"GoodsType:{info[0]} CoinType:{info[1]} CoinNum:{info[2]}")
                    self.msg.append([MSGType.mshop,f"发现{info[2]}金币 {info[0]}"])
                    #self.config.notifier.push(content=f"   {self.account_info} 发现{info[2]}金币 {info[0]}", title="神秘商店提醒")
            if info[1]==CoinType.jade: 
                 if info[0]==GoodsType.heisui: 
                    if 0<info[2]<45 or 70<info[2]<96 or info[2]>=120:
                        flag=True
                        logger.info(f"GoodsType:{info[0]} CoinType:{info[1]} CoinNum:{info[2]}")
                        #self.config.notifier.push(content=f"   {self.account_info} 发现{info[2]}勾黑碎", title="神秘商店提醒")
                        self.msg.append([MSGType.mshop,f"发现{info[2]}勾黑碎"])
                        self.push_notify(content=f" 发现{info[2]}勾黑碎", title="协作任务提醒")
        return flag
    def get_award_daliy (self):
        self.screenshot() 
        if self.appear_then_click(self.I_M_FRAME_BACK_RED, interval=1):
            return True
        elif self.appear_then_click(self.I_M_AWARD,action=self.C_MS_REFRESH_ACTION ,interval=1):
            return True
        elif self.appear_then_click(target=self.I_M_PICTURE,action=self.C_MS_REFRESH_ACTION, interval=1):
            return True
        elif self.appear_then_click(self.I_M_PICTURE_REFUSE, interval=1):
            return True
        elif self.appear_then_click(self.I_CORD_EXIT, interval=1):
            return True
        elif self.appear_then_click(self.I_CORD_BACK_RED, interval=1):
            return True
        elif self.appear_then_click(self.I_T_BACK_RED_SIGN, interval=1):
            return True
        elif self.appear_then_click(self.I_T_SIGN_FLAG,action=self.C_T_EXIT_SIGN, interval=1) or self.appear_then_click(self.I_T_SIGN_FLAG2,action=self.C_T_EXIT_SIGN, interval=1):
            return True
        elif self.appear_then_click(PlotlineAssets.I_CLICK_CURSOR,action=self.C_MS_REFRESH_ACTION, interval=1):
            return True
        elif self.appear_then_click(PlotlineAssets.I_PAGE_CLICK_ANY,action=self.C_MS_REFRESH_ACTION, interval=1):
            return True
        else:
            return False
    def get_award_daliy_2(self):
        retry_count = 0
        while 1:
            self.screenshot() 
            if retry_count>=6:
                break
            if self.appear_then_click(self.I_M_FRAME_BACK_RED, interval=1):
                retry_count=0
                continue
            if self.appear_then_click(self.I_M_AWARD,action=self.C_MS_REFRESH_ACTION ,interval=1):
                retry_count=0
                continue
            if self.appear_then_click(target=self.I_M_PICTURE,action=self.C_MS_REFRESH_ACTION, interval=1):
                retry_count=0
                continue
            if self.appear_then_click(self.I_M_PICTURE_REFUSE, interval=1):
                retry_count=0
                continue
            if self.appear_then_click(self.I_CORD_EXIT, interval=1):
                retry_count=0
                continue
            if self.appear_then_click(self.I_CORD_BACK_RED, interval=1):
                retry_count=0
                continue
            if self.appear_then_click(self.I_T_BACK_RED_SIGN, interval=1):
                retry_count=0
                continue
            if self.appear_then_click(self.I_T_SIGN_FLAG,action=self.C_T_EXIT_SIGN, interval=1) or self.appear_then_click(self.I_T_SIGN_FLAG2,action=self.C_T_EXIT_SIGN, interval=1):
                retry_count=0
                continue
            time.sleep(0.5)
            retry_count+=1
            #logger.info('get_award_daliy: 找到图片222')
        pass
    def get_config(self):
        return self.config.daily_for_flag
    def run_tree_planting(self):
        def buy_flower():
            logger.info("买花") 
            buy_count=0
            start_time = time.time()
            while time.time()-start_time<5:
                self.screenshot()
                if buy_count>=4:
                    break
                if self.appear_then_click(self.I_TREE_AWARD,interval=1):
                    start_time = time.time()
                    break
                if self.appear(self.I_BUY_FLOWER):
                    buy_flower_image = self.I_BUY_FLOWER.match_all_any(self.device.image)
                    if len(buy_flower_image) ==1:
                        self.I_TO_BUY.roi_back=(buy_flower_image[0][1],buy_flower_image[0][2],652,108)
                        self.I_TO_BUY_DISABLE.roi_back=(buy_flower_image[0][1],buy_flower_image[0][2],652,108)
                        if self.appear_rgb(self.I_TO_BUY):
                            self.appear_then_click(self.I_TO_BUY)
                            buy_count+=1
                            continue
                        elif self.appear_rgb(self.I_TO_BUY_DISABLE):
                            break
                    start_time=time.time()
                    continue
                if self.appear_then_click(self.I_CLICK_MAX, interval=1):
                    start_time = time.time()
                    time.sleep(1)
                    self.screenshot()
                    if self.appear_then_click(self.I_CLICK_BUY, interval=1):
                        start_time = time.time()
                        continue
                if self.appear_then_click(self.I_FLOWER, interval=1):
                    start_time = time.time()
                    
            start_time = time.time()
            while time.time()-start_time<5:
                self.screenshot()
                if self.appear_then_click(self.I_H_BACK_RED, interval=1):
                    start_time = time.time()
                    continue
                if self.appear_then_click(self.I_TREE_BACK_Y, interval=1):
                    start_time = time.time()
                    continue
                if self.appear_rgb(self.I_TO_TREE):
                    start_time = time.time()
                    break
            
        if self.ui_get_current_page() != page_guild:
            self.ui_goto(page_guild)
        start_time = time.time()
        while time.time()-start_time < 5:
            self.screenshot()
            if self.appear(self.I_GET_FLOWER, interval=1):
                start_time = time.time()
                break
            if self.appear(self.I_BUY_FLOWER):
                logger.info("没有花,直接买")
                buy_flower()
                start_time = time.time()
                continue
            if self.appear_then_click(self.I_TO_TREE):
                start_time = time.time()
                continue
        logger.info("开始买花")
        while time.time()-start_time < 5:
            self.screenshot()
            if self.appear(self.I_BUY_FLOWER):
                buy_flower()
                start_time = time.time()
                break  
            if self.appear_then_click(self.I_GET_FLOWER, interval=1):
                continue
             
        logger.info("开始捐赠") 
        if self.get_config().daily_for_flag_config.tree_planting_enable < 2:
            logger.info("种树配置为仅买花，跳过捐赠")
            self.appear_then_click(self.I_BACK_Y, interval=1)
            self.ui_goto(page_main)
            return
        start_time = time.time()
        while time.time()-start_time < 5:
            self.screenshot()
            if self.appear_then_click(self.I_TREE_AWARD, interval=1):
                start_time = time.time()
                break
            if self.appear_then_click(self.I_DONATE, interval=1):
                start_time = time.time()
                break
            if self.appear_then_click(self.I_PLANTING, interval=1):
                start_time = time.time()
                continue
            if not self.appear(self.I_PLANTING) and self.appear_then_click(self.I_TO_PLANTING, interval=1):
                start_time = time.time()
                continue
        time.sleep(1)
        self.screenshot()
        self.appear_then_click(self.I_TREE_AWARD)       
        self.appear_then_click(self.I_BACK_Y, interval=1)
        self.ui_goto(page_main)
        
        


if __name__ == "__main__":
    from module.config.config import Config
    from module.device.device import Device
    c = Config('oas3')
    d = Device(c)
    self = ScriptTask(c, d)
    #t.run_mysteryshop()
    self.screenshot()
    """ buy_flower_image = self.I_BUY_FLOWER.match_all_any(self.device.image)
    logger.info(buy_flower_image) """
    self.run_tree_planting()
    """ start_time = time.time()
    #logger.info(f"开始执行{len(self.I_INVITE_FRIEND_OVER.match_all_any(self.device.image))}")
    while time.time()-start_time < 5:
        self.screenshot()
        if self.appear(self.I_FORM_OVER):
            start_time = time.time()
            logger.info("任务完成")
            break
        if len(self.I_INVITE_FRIEND_OVER.match_all_any(self.device.image))<2:
            if self.appear_then_click(self.I_INVITE_FRIEND, interval=1):
                time.sleep(1)
            start_time = time.time()
            continue
            
        if self.appear_then_click(self.I_FORM, interval=1):
            start_time = time.time()
            continue
    start_time = time.time() """
    """ if not self.O_SELECT_LEVEL.ocr(self.device.image)=="觉醒业火轮壹层":
        self.check_zones('觉醒业火轮')
        while 1:
            self.screenshot()
            if self.O_SELECT_LEVEL.ocr(self.device.image)=="觉醒业火轮壹层":
                break
            logger.info(f"当前选择关卡:{list(self.O_FLAG_LEVEL.ocr(self.device.image))}")
            if not list(self.O_FLAG_LEVEL.ocr(self.device.image))==[0,0,0,0]:
                logger.info(f"当前选择关卡:{self.O_SELECT_LEVEL.ocr(self.device.image)}")
            else :
                self.appear_then_click(self.I_CLICK_EVOZONE, interval=1)
                continue
            if self.appear_then_click(self.I_CLICK_LEVEL, interval=1):
                continue
            self.swipe(self.S_SELECT_LEVEL,3) """
    """ if self.O_SELECT_LEVEL.ocr(self.device.image)=="觉醒业火轮壹层":
        logger.info(f"当前选择关卡:{self.O_SELECT_LEVEL.ocr(self.device.image)}")
    logger.info(f"当前副本:{self.check_zones('觉醒业火轮')}")
    if self.O_SELECT_NAME.ocr(self.device.image):
        logger.info(f"当前选择副本:{self.O_SELECT_NAME.ocr(self.device.image)}")
    if self.O_LIST_FIND.ocr(self.device.image):
        logger.info(f"当前副本列表:{self.O_LIST_FIND.ocr(self.device.image)}") """
    """ self.screenshot()
    self.ui_goto(page_friends)
    while 1:    
        self.screenshot()
        if self.appear(self.I_FRIEND_HELP_FLAG, interval=1):
            break
        if self.appear_then_click(self.I_FRIEND_HELP,self.C_FRIEND_HELP_CLICK, interval=1):
            continue
        
    from module.base.utils import save_image
    now=datetime.now()
    folder_name = f'{now.year}{now.month}{now.day}'
    if not os.path.exists( f'./{folder_name}'):
        os.mkdir(f'./{folder_name}')
    folder = f'./{folder_name}'
    save_image(self.screenshot(), f'{folder}/{now.hour}:{now.minute}:{now.second}.png')
    run_timer=Timer(5)
    run_timer.start()
    while 1:    
        self.screenshot()
        if run_timer.reached():
            break
        if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
            break
    self.screenshot()
    if self.ui_get_current_page() != page_main:
        self.ui_goto(page_main) """