# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import re
import time
import cv2
import numpy as np
from PIL import Image
from cached_property import cached_property
from datetime import timedelta, datetime
from typing import List
from module.base.timer import Timer
from module.atom.image_grid import ImageGrid
from module.logger import logger
from module.exception import TaskEnd

from module.atom.image import RuleImage
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_guild , page_team,page_mall
from tasks.ReturnGift.assets import ReturnGiftAssets
from tasks.ReturnGift.config import ReturnGiftConfig
import random
from module.base.utils import point2str

class ScriptTask(GameUi,ReturnGiftAssets):

    def run(self):
        con = self.config.return_gift
        
        timeout_duration = timedelta(hours=con.return_gift_config.return_gift_timeout.hour,
                                    minutes=con.return_gift_config.return_gift_timeout.minute,
                                    seconds=con.return_gift_config.return_gift_timeout.second)
        retry_count = 0
        timeout=datetime.now()
        self.screenshot()
        if self.ui_get_current_page() != page_guild:
            self.ui_goto(page_guild)
        while 1:
            self.screenshot()
            if retry_count >= 4:
                retry_count=0
                if self.ui_get_current_page() != page_guild:
                    self.ui_goto(page_main)
                    self.ui_goto(page_guild)
                    continue
            if datetime.now() - self.start_time >= timeout_duration  or datetime.now() > timeout + timedelta(minutes=3):
                break
            if self.appear(self.I_R_SEND_FLAG):
                sendtimeout=self.send_gift()
                if sendtimeout:
                    timeout=sendtimeout
                receivetimeout=self.receive_gift()
                if receivetimeout:
                    timeout=receivetimeout
                while 1:
                    self.screenshot()
                    if self.appear_then_click(self.I_R_BACK_Y, interval=1):
                        continue
                    if self.ui_get_current_page() == page_guild:
                        break
                continue
            if self.appear_then_click(self.I_R_PAGE_GUILD,action=self.C_R_TOSEND_CLICK,interval=2):
                self.device.click_record_clear()
                time.sleep(1)
                continue
            retry_count += 1
        now = datetime.now()
 
        next_run_time = now.replace(hour=0, minute=19, second=30, microsecond=0) + timedelta(days=1)
        self.set_next_run(task='ReturnGift', target=next_run_time)
        raise TaskEnd('ReturnGift')
    def send_gift(self):
        send_time=False
        retry_count = 0
        swipe_count = 0
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_R_AWARD,action=self.C_R_AWARD_CLICK,interval=0.5):
                continue
            if self.appear_then_click(self.I_R_SEND_BTN, interval=0.5):
                send_time=datetime.now()
                continue
            retry_count +=1
            if retry_count > 5:
                if  swipe_count >=2:
                    break
                retry_count = 0
                duration = 2
                safe_pos_x = random.randint(980, 1080)
                safe_pos_y = random.randint(500, 520)
                p1 = (safe_pos_x, safe_pos_y)
                p2 = (safe_pos_x, safe_pos_y - 300)
                logger.info('Swipe %s -> %s, %sS ' % (point2str(*p1), point2str(*p2), duration))
                self.device.swipe_adb(p1, p2, duration=duration)
                swipe_count += 1
       
        return send_time
    def receive_gift(self):
        send_time=False
        retry_count = 0
        while 1:
            self.screenshot()
            if retry_count > 5:
                break
            if self.appear_then_click(self.I_R_AWARD,action=self.C_R_AWARD_CLICK, interval=0.5):
                time.sleep(1)
                self.screenshot()
                self.appear_then_click(self.I_R_AWARD,action=self.C_R_AWARD_CLICK, interval=0.5)
                break
            if self.appear_then_click(self.I_R_RECEIVE_ENSURE, interval=0.5):
                retry_count = 0
                continue
            if self.appear_then_click(self.I_R_RECEIVE_BTN, interval=0.5):
                send_time=datetime.now()
                retry_count = 0
                continue
            if self.appear_then_click(self.I_R_TORECEIVE_FLAG2,action=self.C_R_TORECEIVE_FLAG2_CLICK, interval=0.5):
                retry_count = 0
                continue
            if not self.appear(self.I_R_TORECEIVE_FLAG2) and self.appear_then_click(self.I_R_TORECEIVE_FLAG,action=self.C_R_TORECEIVE_FLAG_CLICK, interval=1.5):
                retry_count = 0
                continue
            retry_count += 1
        while 1:
            self.screenshot()
            if retry_count > 5:
                self.ui_goto(page_guild)
                break
            if self.appear_then_click(self.I_R_BACK_RED, interval=0.5):
                retry_count = 0
                continue
            if self.appear(self.I_R_SEND_FLAG, interval=0.5):
                break
            retry_count += 1
        return send_time
            
            


if __name__ == "__main__":
    from module.config.config import Config
    from module.device.device import Device
    c = Config('OAS1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.run()
