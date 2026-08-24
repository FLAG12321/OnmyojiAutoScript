# This Python file uses the following encoding: utf-8
import re
import time
from typing import List
from module.atom.ocr import RuleOcr
from module.logger import logger
from tasks.GameUi.page import page_main, page_guild
from tasks.DailyAltAcc.utils import DailyAltAccBase
from tasks.WantedQuests.assets import WantedQuestsAssets
from tasks.WantedQuests.config import CooperationType
from tasks.DailyAltAcc.config import MSGType
from tasks.DailyAltAcc.stat_log import StatEvent


def _parse_cooperation_monster(raw_text: str, prefix: str) -> str:
    """从一行协作目标 OCR 文本中提取怪物名称。"""
    text = re.sub(r"\s+", "", str(raw_text or ""))
    text = re.sub(r"\d+[/／]\d+$", "", text)
    if text.startswith(prefix):
        text = text[len(prefix):]
    return text.strip()


def _parse_real_cooperation_monster(raw_text: str) -> str:
    """从现世协作的“击败N个XXX”目标中提取怪物名称。"""
    text = re.sub(r"\s+", "", str(raw_text or ""))
    text = re.sub(r"\d+[/／]\d+$", "", text)
    match = re.fullmatch(r"击败\d+个(.+)", text)
    return match.group(1).strip() if match else ""


# RuleImage.match() 会将成功匹配的实际位置写回 roi_front。普通邀请按钮的
# 初始 roi_front 即其基准位置，须在第一次匹配前保存，避免后续匹配覆盖基准。
NORMAL_COOPERATION_ANCHOR_REFERENCE = {
    index: tuple(getattr(WantedQuestsAssets, f"I_WQ_INVITE_{index + 1}").roi_front[:2])
    for index in range(3)
}

# 现世“享”标记在旧资源中仅 roi_back 是可靠搜索区域；这里保存与前两张
# 现世体协卡位对齐的实际匹配左上角，均由真实截图校准。
REAL_COOPERATION_ANCHOR_REFERENCE = {
    0: (159, 293),
    1: (458, 293),
}


class Cooperation(DailyAltAccBase):
    @staticmethod
    def _shift_roi(roi: tuple | list, delta: tuple[int, int]) -> tuple[int, int, int, int]:
        x, y, width, height = roi
        dx, dy = delta
        return int(x + dx), int(y + dy), int(width), int(height)

    @staticmethod
    def _anchor_delta(anchor, reference: tuple[int, int]) -> tuple[int, int]:
        """以同一锚点的实际匹配左上角相对基准左上角计算平移。"""
        actual_x, actual_y = anchor.roi_front[:2]
        reference_x, reference_y = reference
        return int(actual_x - reference_x), int(actual_y - reference_y)

    @staticmethod
    def _ocr_single(image, roi: tuple[int, int, int, int], name: str) -> str:
        return RuleOcr(
            mode="Single", roi=roi, area=roi, method="Default", keyword="", name=name,
        ).ocr(image)

    @staticmethod
    def _normal_target_result(discoverer_raw: str, friend_raw: str) -> dict:
        discoverer = _parse_cooperation_monster(discoverer_raw, "自己击败")
        friend = _parse_cooperation_monster(friend_raw, "好友击败")
        result = {}
        if discoverer:
            result["discoverer_monster"] = discoverer
        if friend:
            result["friend_monster"] = friend
        if discoverer or friend:
            result["monster_text"] = "&".join(
                monster for monster in (discoverer, friend) if monster
            )
        return result

    def _read_normal_cooperation_targets(self, index: int, anchor) -> dict:
        """读取普通协作的双目标；固定 ROI 失败时按邀请锚点平移后重试一次。"""
        image = getattr(getattr(self, "device", None), "image", None)
        if image is None:
            return {}
        discoverer_ocr = getattr(
            WantedQuestsAssets, f"O_WQ_COOPERATION_DISCOVERER_{index + 1}",
        )
        friend_ocr = getattr(
            WantedQuestsAssets, f"O_WQ_COOPERATION_FRIEND_{index + 1}",
        )
        try:
            fast_result = self._normal_target_result(
                discoverer_ocr.ocr(image), friend_ocr.ocr(image),
            )
        except Exception as exc:
            logger.warning(f"普通协作目标 OCR 失败(index={index}): {exc}")
            fast_result = {}

        if fast_result.get("discoverer_monster") and fast_result.get("friend_monster"):
            return fast_result

        delta = self._anchor_delta(anchor, NORMAL_COOPERATION_ANCHOR_REFERENCE[index])
        try:
            fallback_result = self._normal_target_result(
                self._ocr_single(
                    image,
                    self._shift_roi(discoverer_ocr.roi, delta),
                    f"wq_cooperation_discoverer_fallback_{index + 1}",
                ),
                self._ocr_single(
                    image,
                    self._shift_roi(friend_ocr.roi, delta),
                    f"wq_cooperation_friend_fallback_{index + 1}",
                ),
            )
        except Exception as exc:
            logger.warning(f"普通协作目标 fallback OCR 失败(index={index}): {exc}")
            fallback_result = {}

        result = fallback_result or fast_result
        logger.info(
            f"normal cooperation index={index} fallback_delta={delta} "
            f"discoverer={result.get('discoverer_monster')!r} "
            f"friend={result.get('friend_monster')!r}"
        )
        return result

    def _read_real_sushi_target(self, index: int, anchor) -> dict:
        """读取现世体协单目标；固定 ROI 失败时按“享”标记平移后重试一次。"""
        image = getattr(getattr(self, "device", None), "image", None)
        if image is None:
            return {}
        ocr_item = getattr(WantedQuestsAssets, f"O_WQ_REAL_COOPERATION_MONSTER_{index + 1}")
        try:
            monster = _parse_real_cooperation_monster(ocr_item.ocr(image))
        except Exception as exc:
            logger.warning(f"现世体协目标 OCR 失败(index={index}): {exc}")
            monster = ""
        if monster:
            return {"monster_text": monster}

        delta = self._anchor_delta(anchor, REAL_COOPERATION_ANCHOR_REFERENCE[index])
        try:
            monster = _parse_real_cooperation_monster(self._ocr_single(
                image,
                self._shift_roi(ocr_item.roi, delta),
                f"wq_real_cooperation_monster_fallback_{index + 1}",
            ))
        except Exception as exc:
            logger.warning(f"现世体协目标 fallback OCR 失败(index={index}): {exc}")
            monster = ""
        logger.info(
            f"real sushi cooperation index={index} fallback_delta={delta} monster={monster!r}"
        )
        return {"monster_text": monster} if monster else {}

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
                cooperation = {
                    'type': CooperationType.Jade,
                    'inviteBtn': btn,
                    'real': real_flag,
                }
                if not real_flag:
                    cooperation.update(self._read_normal_cooperation_targets(index, btn))
                retList.append(cooperation)
                if real_flag:
                    logger.info(f"find real jade cooperation ")
                    self.push_notify(content=f"    发现现世勾协", title="协作任务提醒")
                    self.msg.append([MSGType.cooperation,
                                     {"type": "jade", "real": True, "label": "现世勾协"}])
                else:
                    logger.info(f"find  jade cooperation ")
                    self.push_notify(content=f"    发现普通勾协", title="协作任务提醒")
                    event = {"type": "jade", "real": False, "label": "普通勾协"}
                    event.update({
                        key: cooperation[key]
                        for key in ("discoverer_monster", "friend_monster", "monster_text")
                        if cooperation.get(key)
                    })
                    self.msg.append([MSGType.cooperation, event])
                continue
            if self.appear(getattr(WantedQuestsAssets, "I_WQ_COOPERATION_TYPE_DOG_FOOD_" + str(index + 1))):
                retList.append({'type': CooperationType.Food, 'inviteBtn': btn, 'real': real_flag})
                logger.info(f"find dog food cooperation ")
                # 狗粮协作：不区分现世/普通，food_kind 固定为 dog（模板已人工确认为狗粮）
                self.msg.append([MSGType.cooperation,
                                 {"type": "food", "real": False, "food_kind": "dog", "label": "狗粮协作"}])
                continue
            if self.appear(getattr(WantedQuestsAssets, "I_WQ_COOPERATION_TYPE_CAT_FOOD_" + str(index + 1))):
                retList.append({'type': CooperationType.Food, 'inviteBtn': btn, 'real': real_flag})
                logger.info(f"find cat food cooperation ")
                # 猫粮协作：不区分现世/普通，food_kind 固定为 cat（模板已人工确认为猫粮）
                self.msg.append([MSGType.cooperation,
                                 {"type": "food", "real": False, "food_kind": "cat", "label": "猫粮协作"}])
                continue
            if self.appear(getattr(WantedQuestsAssets, "I_WQ_COOPERATION_TYPE_SUSHI_" + str(index + 1))):
                cooperation = {
                    'type': CooperationType.Sushi,
                    'inviteBtn': btn,
                    'real': real_flag,
                }
                if real_flag and index < 2:
                    cooperation.update(self._read_real_sushi_target(index, btn2))
                else:
                    if not real_flag:
                        cooperation.update(self._read_normal_cooperation_targets(index, btn))
                retList.append(cooperation)
                if real_flag:
                    logger.info(f"find real sushi cooperation ")
                    event = {"type": "sushi", "real": True, "label": "现世体协"}
                    self.push_notify(content=f"    发现现世体协", title="协作任务提醒")
                else:
                    logger.info(f"find  sushi cooperation ")
                    event = {"type": "sushi", "real": False, "label": "普通体协"}
                    self.push_notify(content=f"    发现普通体协", title="协作任务提醒")
                event.update({
                    key: cooperation[key]
                    for key in ("discoverer_monster", "friend_monster", "monster_text")
                    if cooperation.get(key)
                })
                self.msg.append([MSGType.cooperation, event])
                continue
            # NOTE 因为食物协作里面也有金币奖励 ,所以判断金币协作放在最后面
            if self.appear(getattr(WantedQuestsAssets, "I_WQ_COOPERATION_TYPE_GOLD_" + str(index + 1))):
                retList.append({'type': CooperationType.Gold, 'inviteBtn': btn, 'real': real_flag})
                logger.info(f"find gold cooperation ")
                # 金币协作：不区分现世/普通
                self.msg.append([MSGType.cooperation,
                                 {"type": "gold", "real": False, "label": "金币协作"}])
                continue
        logger.info(f"get cooperation size {len(retList)}")
        # 将本轮识别到的协作按明细写入 STAT，便于前端区分类型和现世标记。
        emit_stat = getattr(self, "emit_stat", None)
        total = len(retList)
        if emit_stat:
            for item in retList:
                emit_stat(
                    StatEvent.COOP,
                    ctype=item["type"].name.lower(),
                    real=bool(item.get("real", False)),
                    total=total,
                )
        return retList


if __name__ == "__main__":
    from module.config.config import Config
    from module.device.device import Device
    c = Config('oas2')
    d = Device(c)
    self = Cooperation(c, d)
    self.screenshot()
    self.run_cooperation()
