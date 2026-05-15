# This Python file uses the following encoding: utf-8
import os
import time
from datetime import datetime
from module.base.timer import Timer
from module.base.utils import save_image
from module.logger import logger
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.page import page_main, page_team, page_friends
from tasks.DailyAltAcc.utils import DailyAltAccBase
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.Component.GeneralRoom.general_room import GeneralRoom


class Alliedteam(GeneralBattle, GeneralRoom, DailyAltAccBase):
    def run_alliedteam(self, battle_enable, ap_enable):
        self.screenshot()
        if self.ui_get_current_page() != page_main:
            self.ui_goto(page_main)
        self.ui_goto(page_team)
        if ap_enable:
            self.run_alliedteam_ap()
        if battle_enable:
            self.run_alliedteam_battle()
            self.return_to_main()

    def run_alliedteam_ap(self):    
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

    def run_alliedteam_battle(self):    
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
                logger.info("邀请好友")
                if self.appear(self.I_INVITE_FRIEND, interval=1):
                    self.I_INVITE_FRIEND.roi_front[2]=13
                    self.I_INVITE_FRIEND.roi_front[3]=12
                    self.click(self.I_INVITE_FRIEND)
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
        if self.get_config().daily_alt_acc_config.alliedteam_limit_count == 13:
            self.screenshot()
            self.ui_goto(page_friends)
            while 1:    
                self.screenshot()
                if self.appear(self.I_FRIEND_HELP_FLAG, interval=1):
                    break
                if self.appear_then_click(self.I_FRIEND_HELP,action=self.C_FRIEND_HELP_CLICK, interval=1):
                    continue
                
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
        alliedteam_limit_count=self.get_config().daily_alt_acc_config.alliedteam_limit_count
        logger.info(f' alliedteam_limit_count: {alliedteam_limit_count}')
        self.check_lock(True)
        while 1:
            self.screenshot()
            if self.current_count >= alliedteam_limit_count:
                logger.info('Orochi count limit out')
                break
            if not is_in_evozone():
                continue
            # 点击挑战
            while 1:
                self.screenshot()
                if self.appear_then_click(self.I_BATTLE, interval=1):
                    pass
                if not self.appear(self.I_BATTLE):
                    self.run_general_battle(config=self.config.daily_alt_acc.general_battle_config)
                    break


if __name__ == "__main__":
    from module.config.config import Config
    from module.device.device import Device
    c = Config('QMUMU2')
    d = Device(c)
    self = Alliedteam(c, d)
    self.screenshot()
    self.run_alliedteam_ap()
    #self.run_alliedteam(False, True)
