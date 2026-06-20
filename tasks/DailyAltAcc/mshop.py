# This Python file uses the following encoding: utf-8
import time
from module.logger import logger
from tasks.GameUi.page import page_main, page_mall
from tasks.DailyAltAcc.utils import DailyAltAccBase
from tasks.RichMan.mall.mall import Mall
from tasks.RichMan.config import Consignment
from tasks.MysteryShop.assets import MysteryShopAssets
from tasks.DailyAltAcc.config import GoodsType, CoinType, MSGType
from tasks.DailyAltAcc.stat_log import StatEvent


class Mshop(Mall, DailyAltAccBase):
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
        """ while self.buy_mall_one(buy_button=MysteryShopAssets.I_MS_TAIKO_OFF_4, buy_check=MysteryShopAssets.I_MS_CHECK_TAIKO_4,
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
            pass """
        
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
        if self.get_config().daily_alt_acc_config.isflower and self.appear(self.I_MS_ALL_HEISUI):
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
                    # 统计只关注金币蛇皮和逢魔，黑碎仍保留原通知但不进入 mshop 明细。
                    emit_stat = getattr(self, "emit_stat", None)
                    if emit_stat:
                        emit_stat(
                            StatEvent.MSHOP,
                            goods=info[0].name,
                            price=info[2],
                        )
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


if __name__ == "__main__":
    from module.config.config import Config
    from module.device.device import Device
    c = Config('oas3')
    d = Device(c)
    self = Mshop(c, d)
    self.screenshot()
    self.run_mysteryshop()
