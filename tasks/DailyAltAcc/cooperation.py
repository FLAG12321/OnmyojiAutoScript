# This Python file uses the following encoding: utf-8
import time
from typing import List
from module.logger import logger
from tasks.GameUi.page import page_main, page_guild
from tasks.DailyAltAcc.utils import DailyAltAccBase
from tasks.WantedQuests.assets import WantedQuestsAssets
from tasks.WantedQuests.config import CooperationType
from tasks.DailyAltAcc.config import MSGType


class Cooperation(DailyAltAccBase):
    def run_cooperation(self):   
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
            btn = getattr(WantedQuestsAssets, "I_WQ_INVITE_" + str(index + 1))
            btn2 = self.__getattribute__("I_REAL_FLAG_" + str(index + 1))
            normal_flag=self.appear(btn)
            real_flag=self.appear(btn2)
            if not normal_flag and not real_flag:
                break
            if self.appear(getattr(WantedQuestsAssets, "I_WQ_COOPERATION_TYPE_JADE_" + str(index + 1))):
                retList.append({'type': CooperationType.Jade, 'inviteBtn': btn})
                if real_flag:
                    logger.info(f"find real jade cooperation ")
                    self.push_notify(content=f"    发现现世勾协", title="协作任务提醒")
                    self.msg.append([MSGType.cooperation,"发现现世勾协"])
                else:
                    logger.info(f"find  jade cooperation ")
                    self.push_notify(content=f"    发现普通勾协", title="协作任务提醒")
                    self.msg.append([MSGType.cooperation,"发现普通勾协"])
                continue
            if self.appear(getattr(WantedQuestsAssets, "I_WQ_COOPERATION_TYPE_DOG_FOOD_" + str(index + 1))):
                retList.append({'type': CooperationType.Food, 'inviteBtn': btn})
                logger.info(f"find dog food cooperation ")
                continue
            if self.appear(getattr(WantedQuestsAssets, "I_WQ_COOPERATION_TYPE_CAT_FOOD_" + str(index + 1))):
                retList.append({'type': CooperationType.Food, 'inviteBtn': btn})
                logger.info(f"find cat food cooperation ")
                continue
            if self.appear(getattr(WantedQuestsAssets, "I_WQ_COOPERATION_TYPE_SUSHI_" + str(index + 1))):
                retList.append({'type': CooperationType.Sushi, 'inviteBtn': btn})
                if real_flag:
                    logger.info(f"find real sushi cooperation ")
                    self.msg.append([MSGType.cooperation,"发现现世体协"])
                    self.push_notify(content=f"    发现现世体协", title="协作任务提醒")
                else:
                    logger.info(f"find  sushi cooperation ")
                    self.msg.append([MSGType.cooperation,"发现普通体协"])
                    self.push_notify(content=f"    发现普通体协", title="协作任务提醒")
                continue
            # NOTE 因为食物协作里面也有金币奖励 ,所以判断金币协作放在最后面
            if self.appear(getattr(WantedQuestsAssets, "I_WQ_COOPERATION_TYPE_GOLD_" + str(index + 1))):
                retList.append({'type': CooperationType.Gold, 'inviteBtn': btn})
                logger.info(f"find gold cooperation ")
                continue
        logger.info(f"get cooperation size {len(retList)}")
        return retList


if __name__ == "__main__":
    from module.config.config import Config
    from module.device.device import Device
    c = Config('oas2')
    d = Device(c)
    self = Cooperation(c, d)
    self.screenshot()
    self.run_cooperation()
