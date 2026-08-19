# This Python file uses the following encoding: utf-8
import time
from module.base.timer import Timer
from module.logger import logger
from tasks.GameUi.page import page_main, page_mall
from tasks.DailyAltAcc.utils import DailyAltAccBase
from tasks.RichMan.mall.mall import Mall
from tasks.RichMan.config import Consignment, MedalRoom
from tasks.MysteryShop.assets import MysteryShopAssets
from tasks.DailyAltAcc.config import GoodsType, CoinType, MSGType
from tasks.DailyAltAcc.stat_log import StatEvent
from tasks.DailyAltAcc.mshop_grid import (
    COIN_NAMES,
    GOODS_NAMES,
    GOODS_TEMPLATES,
    SlotItem,
    coin_of,
    goods_rule,
    locate_slot,
)


class Mshop(Mall, DailyAltAccBase):
    def execute_mall(self):
            logger.hr('Mall', 1)
            self.screenshot()
            self.ui_get_current_page()
            self.ui_goto(page_mall, confirm_wait=2.5)

            # 勋章屋黑蛋：必须放在寄售屋之前。勋章页不是 page_main，也没有
            # 黄色返回可点，下面的退出循环在那里两个分支都不成立会死循环；
            # 而寄售屋兑换子页有黄色返回，退出循环能正常走完。
            self._buy_medal_black_daruma()

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

    def _back_to_mall_root(self, timeout: float = 15) -> bool:
        """退回商城根页面（底部导航栏可见的那一层）。

        必须是有界循环：商城内各屋的入口按钮都在底部导航栏上，而 MallNavbar
        里的 ui_click 没有超时，一旦起始页面不对就会永久自旋，_run_with_stat
        也救不回来。所以这里超时就返回 False 让调用方跳过。

        :return: True 已在商城根页面
        """
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            if self.appear(self.I_CHECK_MALL):
                return True
            if timer.reached():
                return False
            if self.appear_then_click(self.I_UI_BACK_YELLOW, interval=1):
                continue

    def _buy_medal_black_daruma(self):
        """勋章屋购买黑蛋（480 勋章，每周限购一颗）。

        买不到的情况（按钮未出现/本周剩余为 0/勋章不足）由 buy_mall_one
        内部判断并跳过，这里不用重复检查。
        买完停在勋章页，必须退回商城根页面，否则后续寄售屋点不到底部导航栏
        的入口，_enter_consignment 的 ui_click 无超时会死循环。
        """
        logger.hr('Medal black daruma', 2)
        self.execute_medal(MedalRoom(enable=True, black_daruma=True))
        if not self._back_to_mall_root():
            logger.warning('勋章屋后回到商城根页面失败')

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

    def _enabled_goods(self) -> list[GoodsType]:
        """按配置决定要扫哪几类货：黑碎只在二花账号扫。

        保留原 MsFind 里 isflower 门控黑碎的行为，把开关收敛到清单构造处，
        而不是散落进扫描或判定逻辑里。
        """
        goods = [GoodsType.shepi, GoodsType.fmpi]
        if self.get_config().daily_alt_acc_config.isflower:
            goods.append(GoodsType.heisui)
        return goods

    def _ocr_price(self, slot: int) -> int:
        """读取指定格位的价格数字，返回 0 表示识别失败。

        必须显式兜底 detect_and_ocr：Digit 模式下 after_process 把空串转成 int 0
        （module/ocr/sub_ocr.py:121），而 ocr_single 的判空是 `!= ""`
        （module/ocr/sub_ocr.py:86），`0 != ""` 恒真，导致 RuleOcr.ocr 内部那段
        竖排 / detect_and_ocr 兜底对 Digit 模式是死代码。
        """
        rule = getattr(self, f'O_MS_PRICENUM_{slot}')
        price = rule.ocr(self.device.image)
        if price:
            return int(price)
        results = rule.detect_and_ocr(self.device.image)
        if results:
            return int(results[0].ocr_text)
        return 0

    def _scan_slots(self, goods_types: list[GoodsType]) -> list[SlotItem]:
        """扫描当前货架，返回识别到的商品。要求已在神秘商店页面且已截图。

        每类货物在总区域做一次多点匹配（NMS 去重叠）→ 命中框中心映射到格位
        → 同格冲突取匹配得分高者 → 只对有命中的格位 OCR 价格。
        价格 OCR 失败的格位直接丢弃：价格未知无法判定，宁漏不错。

        :param goods_types: 要扫描的货物类型，由调用方按配置挑选
        :return: 按格位号升序的商品列表
        """
        image = self.device.image
        # 格位号 -> (货物类型, 匹配得分)，同格只保留得分最高的一条
        best: dict[int, tuple[GoodsType, float]] = {}
        for goods in goods_types:
            matches = goods_rule(GOODS_TEMPLATES[goods]).match_all_any(image)
            for score, x, y, w, h in matches:
                cx, cy = x + w // 2, y + h // 2
                slot = locate_slot(cx, cy)
                if slot is None:
                    logger.warning(f'命中中心 ({cx},{cy}) 落在格位网格外，跳过')
                    continue
                exist = best.get(slot)
                if exist is None:
                    best[slot] = (goods, score)
                    continue
                # 同格命中两种货物，必有一个是误匹配，留日志便于回溯
                keep = goods if score > exist[1] else exist[0]
                logger.warning(f'格位 {slot} 同时命中 {exist[0].name} 与 {goods.name}，'
                               f'保留得分高的 {keep.name}')
                if keep is goods:
                    best[slot] = (goods, score)

        items: list[SlotItem] = []
        for slot in sorted(best):
            goods, score = best[slot]
            price = self._ocr_price(slot)
            if price <= 0:
                logger.warning(f'格位 {slot}（{goods.name}）价格 OCR 失败，跳过该格')
                continue
            items.append(SlotItem(slot=slot, goods=goods, price=price,
                                  coin=coin_of(price), score=score))
        return items

    def MsFind(self) -> bool:
        """扫描当前神秘商店货架，播报规则判定要买的商品。

        :return: True 表示有要买的商品；run_mysteryshop 据此决定是否刷新商店。
            占位 _should_buy 恒返回 False，所以规则填好前每次都会触发刷新。
        """
        self.screenshot()
        items = self._scan_slots(self._enabled_goods())
        logger.info(f'神秘商店扫描到 {len(items)} 件商品: '
                    f'{[(i.slot, i.goods.name, i.coin.name, i.price) for i in items]}')
        found = False
        for item in items:
            if not self._should_buy(item.goods, item.coin, item.price, item.slot):
                continue
            found = True
            self._notify_item(item)
            # TODO 购买：点 O_MS_PRICENUM_<slot> 的 ROI（价格条就是购买按钮），
            #      确认弹窗用 I_MS_ENSURE（货物无关的「确定」按钮），
            #      每格一件走 Buy.buy_one 而非 buy_more
        if not found:
            logger.info('没有找到物品')
        return found

    def _should_buy(self, goods: GoodsType, coin: CoinType, price: int, slot: int) -> bool:
        """判定是否购买该格位商品。规则待填。

        :param goods: 货物类型
        :param coin: 币种，由价格数值判定（>10000 金币，否则勾玉）
        :param price: 价格数值，调用方保证 > 0
        :param slot: 格位号 1..8
        :return: True 表示要买
        """
        return False

    def _notify_item(self, item: SlotItem) -> None:
        """播报一件命中商品，msg / push_notify / emit_stat 三个通道全发。

        原实现按货物类型分通道，分支依据已移入 _should_buy，通道层不再区分类型。
        emit_stat 用 getattr 守卫：Mshop 不一定混入了 StatLogMixin。
        """
        content = f'发现{item.price}{COIN_NAMES[item.coin]}{GOODS_NAMES[item.goods]}'
        logger.info(f'格位 {item.slot} {content}')
        self.msg.append([MSGType.mshop, content])
        self.push_notify(content=f' {content}', title='神秘商店提醒')
        emit_stat = getattr(self, 'emit_stat', None)
        if emit_stat:
            emit_stat(StatEvent.MSHOP, goods=item.goods.name, price=item.price)


if __name__ == "__main__":
    from module.config.config import Config
    from module.device.device import Device
    c = Config('oas3')
    d = Device(c)
    self = Mshop(c, d)
    self.screenshot()
    self.run_mysteryshop()
