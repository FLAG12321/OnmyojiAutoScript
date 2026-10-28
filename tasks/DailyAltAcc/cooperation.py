# This Python file uses the following encoding: utf-8
import re
import time
from copy import deepcopy
from datetime import datetime
from typing import List

from module.atom.image import RuleImage
from module.atom.ocr import RuleOcr
from module.exception import TaskEnd
from module.logger import logger
from tasks.GameUi.page import page_main, page_guild
from tasks.DailyAltAcc.assets import DailyAltAccAssets
from tasks.DailyAltAcc.utils import DailyAltAccBase
from tasks.WantedQuests.assets import WantedQuestsAssets
from tasks.WantedQuests.config import CooperationType
from tasks.DailyAltAcc.config import MSGType
from tasks.DailyAltAcc.stat_log import StatEvent
from tasks.Utils.coop_records import record_cooperation


# 协作事件使用统一时间格式，便于日志、统计和持久记录对照。
COOP_TS_FORMAT = '%Y-%m-%d %H:%M:%S'
# 页面确认后，协作组件连续未命中的网络恢复时限（秒）。
NET_RETRY_TIMEOUT = 30
# 网络异常期间重新检查组件的间隔（秒）。
NET_RECHECK_INTERVAL = 7


def wait_cooperation_prompt_clear(task) -> None:
    """精灵或拓出现就逐帧等待，两者消失后识别协/享；单独友牌不等待。"""
    prompts = tuple(deepcopy(rule) for rule in (
        DailyAltAccAssets.I_COOPERATION_PROMPT_SPIRIT,
        DailyAltAccAssets.I_COOPERATION_PROMPT_EXPLORE,
    ))
    waiting = False
    while any(task.appear(prompt) for prompt in prompts):
        if not waiting:
            logger.info('悬赏页出现精灵或拓提示，等待两者消失')
            waiting = True
        time.sleep(0.5)
        # 每次等待后重新截图，不能沿用发现提示时的旧图继续判断协作类型。
        task.screenshot()
    if waiting:
        logger.info('精灵和拓提示已消失，重新采样悬赏协作')


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


# ---- 协作板单区域批量识别常量 ----
# 新版悬赏板的四张竖卡共用搜索区域，包含右上协作标记、左侧邀请和底部奖励。
COOP_CARD_BAND = (88, 110, 1104, 528)
# 标记在卡片右上、邀请在左侧，统一按卡片中心归槽，避免两种锚点串到相邻卡。
COOP_ANCHOR_SLOT_X = (218, 498, 780, 1062)
# 每张卡片宽约 260；间隔部分不归入任何槽位。
COOP_SLOT_TOLERANCE = 130
# 锚点优先级：协/享 不随邀请消失，比邀请按钮可靠，故优先于邀请按钮
COOP_ANCHOR_PRIORITY = {'协': 0, '享': 1, '邀请': 2}
# 类型优先级：食物协作里也有金币奖励，故金币排在最后（沿用改造前的判定顺序）
COOP_TYPE_PRIORITY = {'jade': 0, 'dog_food': 1, 'cat_food': 2, 'sushi': 3, 'gold': 4}
# 协作双目标位于插画下方两行，ROI 取第一列，再按列间距平移。
COOP_DISCOVERER_BASE = (100, 437, 230, 30)
COOP_FRIEND_BASE = (100, 482, 230, 30)
# 现世体协只有一行目标，读取两条普通目标之间的文字带。
COOP_REAL_MONSTER_BASE = (100, 457, 230, 40)
# 奖励类型图标按卡片锚点位置限定搜索区域，避免整板相似奖励串到其他卡片。
COOP_TYPE_BASE = (195, 505, 180, 90)
COOP_TARGET_SLOT_PITCH = 282
# 协/享共用资产标注的右上角坐标；没有协/享标记时用 I_COOPERATION_INVITE 的资产坐标。
COOP_ANCHOR_ORIGIN = {'协': (309, 123), '享': (309, 123), '邀请': (105, 402)}
COOP_ANCHOR_PITCH = 282


# 协作 UI 入口：此类打开悬赏板、识别协作卡片与奖励，并产生日常协作事件。
class Cooperation(DailyAltAccBase):
    # 多账号任务注入同一持久表、账号身份和轮次；单任务无上下文时只产出日志/消息。
    _coop_store = None
    _account_identity = None
    _coop_round_id = None
    # 本次「找协作」取帧的时刻，由 get_cooperation_info 在 screenshot() 之后立刻盖下。
    # 协作的归属窗口是 05:00 / 18:00 两档刷新，窗口由**识别帧**决定而不是落盘时刻：
    # 从识别到落盘隔着点返回与 ui_goto，有 1~5 秒，恰好跨过刷新点时按落盘时刻取
    # 会把一条版面已经刷掉的协作归进下一个窗口，它于是在下游待办里活满 11 小时。
    # None 表示没有取帧来源（单测、手工构造），_coop_emit 退化为当下时刻。
    _coop_scan_at: datetime = None

    # 每种模板在四张卡片上统一批量搜索，随后按卡片中心归槽。
    COOP_ANCHOR_RULES = {
        '协': DailyAltAccAssets.I_NORMAL_FLAG,
        '享': DailyAltAccAssets.I_REAL_FLAG,
        '邀请': DailyAltAccAssets.I_COOPERATION_INVITE,
    }
    COOP_TYPE_RULES = {
        'jade': WantedQuestsAssets.I_WQ_COOPERATION_TYPE_JADE,
        'dog_food': WantedQuestsAssets.I_WQ_COOPERATION_TYPE_DOG_FOOD,
        'cat_food': WantedQuestsAssets.I_WQ_COOPERATION_TYPE_CAT_FOOD,
        'sushi': WantedQuestsAssets.I_WQ_COOPERATION_TYPE_SUSHI,
        'gold': WantedQuestsAssets.I_WQ_COOPERATION_TYPE_GOLD,
    }

    def wait_for_cooperation_network_normal(self) -> bool | None:
        """页面未确认时返回 None；协作页内轮询组件，30 秒未恢复则通知父任务重登。"""
        while True:
            network_failed_at = getattr(self, '_cooperation_network_failed_at', None)
            if (network_failed_at is not None
                    and time.monotonic() - network_failed_at >= NET_RETRY_TIMEOUT):
                logger.warning(f'悬赏协作页超过 {NET_RETRY_TIMEOUT} 秒未恢复，判为账号级网络故障')
                self.msg.append([MSGType.neterror, '网络错误'])
                raise TaskEnd(self.msg)

            if not self.appear(deepcopy(DailyAltAccAssets.I_PAGE_COOPERATION)):
                return None

            components = (
                DailyAltAccAssets.I_NET_NORMAL_FLAG_1,
                DailyAltAccAssets.I_NET_NORMAL_FLAG_2,
                *self.COOP_ANCHOR_RULES.values(),
                *self.COOP_TYPE_RULES.values(),
            )
            if any(self.appear(deepcopy(component)) for component in components):
                self._cooperation_network_failed_at = None
                return True

            if network_failed_at is None:
                network_failed_at = time.monotonic()
                self._cooperation_network_failed_at = network_failed_at
            remaining = NET_RETRY_TIMEOUT - (time.monotonic() - network_failed_at)
            time.sleep(min(NET_RECHECK_INTERVAL, max(0, remaining)))
            self.screenshot()

    def wait_cooperation_board_ready(self) -> None:
        """统一清理残留页面并检测协作板；协作页内的网络检测统一等待最多 30 秒。"""
        while True:
            self.screenshot()
            # 使用悬赏标题确认已经进入任务板。
            if self.appear(deepcopy(DailyAltAccAssets.I_PAGE_COOPERATION)):
                if self.wait_for_cooperation_network_normal() is None:
                    continue
                self.device.click_record_clear()
                self.ui_click_until_disappear(self.I_UI_BACK_RED, interval=4)
                return

            if self.appear_then_click(WantedQuestsAssets.I_WQ_SEAL, interval=1) or \
                    self.appear_then_click(WantedQuestsAssets.I_WQ_DONE, interval=1):
                continue

            if self.appear(self.I_UI_BACK_RED):
                self.device.click_record_clear()
                self.ui_click_until_disappear(self.I_UI_BACK_RED, interval=4)

    # 类型信号的输出规格。日志文案与标签沿用改造前的逐字表述（含 "find  jade
    # cooperation " 里的双空格），前端展示与推送依赖这些字符串，不得改动。
    # 狗粮/猫粮/金币改造前硬编码 real=False、标签只有一份；改为忠实反映锚点后
    # 「享」也会出现在这三类上（实机样本里撞到 5 次现世金币），故补上「现世」
    # 标签，汇总侧的分类表同步扩到 10 类（见 tasks/Utils/coop_summary.coop_category_order）。
    COOP_TYPE_SPEC = {
        'jade': {
            'type': CooperationType.Jade, 'event_type': 'jade', 'notify': True,
            'log': 'find  jade cooperation ', 'log_real': 'find real jade cooperation ',
            'label': '普通勾协', 'label_real': '现世勾协',
        },
        'dog_food': {
            'type': CooperationType.Food, 'event_type': 'food', 'notify': False,
            'log': 'find dog food cooperation ', 'log_real': None,
            'label': '狗粮协作', 'label_real': '现世狗粮协作', 'food_kind': 'dog',
        },
        'cat_food': {
            'type': CooperationType.Food, 'event_type': 'food', 'notify': False,
            'log': 'find cat food cooperation ', 'log_real': None,
            'label': '猫粮协作', 'label_real': '现世猫粮协作', 'food_kind': 'cat',
        },
        'sushi': {
            'type': CooperationType.Sushi, 'event_type': 'sushi', 'notify': True,
            'log': 'find  sushi cooperation ', 'log_real': 'find real sushi cooperation ',
            'label': '普通体协', 'label_real': '现世体协',
        },
        'gold': {
            'type': CooperationType.Gold, 'event_type': 'gold', 'notify': False,
            'log': 'find gold cooperation ', 'log_real': None,
            'label': '金币协作', 'label_real': '现世金币协作',
        },
    }

    # 目标文本读法：'normal' 读双行（自己击败X / 好友击败Y），'real' 读单行
    # （击败N个X）。键为 (类型信号, 是否现世)，缺省即不读该组合的目标文本。
    # 沿用改造前行为：勾协只在普通时读、体协现世读单行普通读双行、其余类型不读
    # （读目标要走 OCR，非必要不付出这个开销）。
    COOP_TARGET_READ = {
        ('jade', False): 'normal',
        ('sushi', True): 'real',
        ('sushi', False): 'normal',
    }

    @staticmethod
    def _shift_roi(roi: tuple | list, delta: tuple[int, int]) -> tuple[int, int, int, int]:
        x, y, width, height = roi
        dx, dy = delta
        return int(x + dx), int(y + dy), int(width), int(height)

    @staticmethod
    def _coop_target_shift(anchor, slot: int) -> tuple[int, int]:
        """从协/享或邀请锚点的实测位置反推该槽资源 ROI 的平移量。

        @param anchor: 该槽胜出的锚点 (信号名, 得分, x, y)
        @return: (dx, dy)，用于平移目标文本与奖励图标的基准区域

        以锚点在第一张卡上的标称位置为基准，槽间距按 282 计算，剩余差值来自
        本帧命中的实际偏移。现世「享」与普通「协」共用同一标称位置。
        """
        anchor_name, _score, anchor_x, anchor_y = anchor
        dx, dy = COOP_TARGET_SLOT_PITCH * slot, 0
        origin = COOP_ANCHOR_ORIGIN.get(anchor_name)
        if origin is not None:
            dx += anchor_x - origin[0] - COOP_ANCHOR_PITCH * slot
            dy = anchor_y - origin[1]
        return int(dx), int(dy)

    @classmethod
    def _coop_collect_types(cls, image, rules: dict, anchor_slots: dict) -> dict:
        """按每张卡的锚点偏移搜索奖励图标，并返回各槽优先级最高的类型。"""
        winners = {}
        for slot, anchor in anchor_slots.items():
            roi = cls._shift_roi(COOP_TYPE_BASE, cls._coop_target_shift(anchor, slot))
            for name, rule in rules.items():
                # 每次使用独立模板和卡片局部区域，避免改写共享资源的搜索范围。
                search_rule = RuleImage(
                    roi_front=rule.roi_front, roi_back=roi,
                    method=rule.method, threshold=rule.threshold, file=rule.file,
                )
                matches = search_rule.match_all_any(image, nms_threshold=0.3)
                for score, x, y, _width, _height in matches:
                    current = winners.get(slot)
                    if current is None or COOP_TYPE_PRIORITY[name] < COOP_TYPE_PRIORITY[current[0]]:
                        winners[slot] = (name, score, x, y)
        return winners

    @staticmethod
    def _coop_slot_of(x: int, basis: tuple) -> int | None:
        """按 |x - 基准| 把命中归位到最近的槽位；超出容差返回 None。"""
        slot, best = None, None
        for index, base_x in enumerate(basis):
            distance = abs(x - base_x)
            if best is None or distance < best:
                slot, best = index, distance
        return slot if best is not None and best <= COOP_SLOT_TOLERANCE else None

    @classmethod
    def _coop_collect(cls, image, rules: dict, basis: tuple, priority: dict) -> tuple[dict, dict]:
        """在同一区域内批量匹配一组模板，并把命中按 x 归位到卡片槽位。

        每个模板在整块区域里一次找出全部命中，再归位到四张卡片。

        @param rules: {信号名: RuleImage}
        @param basis: 四张卡片的中心 x
        @param priority: 同槽位多命中时的优先级，数值小者胜出
        @return: ({槽位: (信号名, 得分, x, y)}, {信号名: [(得分, x, y, 槽位), ...]})
                 槽位为 None 表示该命中离所有基准都超过容差、已丢弃。
        """
        winners, hits = {}, {}
        for name, rule in rules.items():
            # 只在本次调用传入整板区域，不让共享模板保留本任务的搜索范围。
            # 必须用 match_all_any：match_all 返回所有 ≥阈值的像素点，一个图标就是
            # 几百个候选框；match_all_any 内部走 cv2.dnn.NMSBoxes 去重。
            search_rule = RuleImage(
                roi_front=rule.roi_front, roi_back=COOP_CARD_BAND,
                method=rule.method, threshold=rule.threshold, file=rule.file,
            )
            matches = search_rule.match_all_any(image, nms_threshold=0.3)
            hits[name] = [
                (score, x, y, cls._coop_slot_of(x, basis))
                for score, x, y, _width, _height in matches
            ]
            for score, x, y, slot in hits[name]:
                if slot is None:
                    continue
                current = winners.get(slot)
                if current is None or priority[name] < priority[current[0]]:
                    winners[slot] = (name, score, x, y)
        return winners, hits

    def _coop_read_targets(self, type_name: str, real: bool, slot: int, anchor) -> dict:
        """按类型与现世标记读取该槽的协作目标文本；不需要读的组合直接返回空。"""
        read_mode = self.COOP_TARGET_READ.get((type_name, real))
        if read_mode is None:
            return {}
        if read_mode == 'real':
            return self._read_real_sushi_target(slot, anchor)
        return self._read_normal_cooperation_targets(slot, anchor)

    def _coop_invite_button(self, slot: int, invite_hits: list):
        """按该槽实测到的邀请按钮位置构造返回用的 RuleImage。

        改造前直接返回固定槽位邀请实例（appear() 已把实测位置写回 roi_front）；
        改成单实例批量识别后不再有「每槽一个固定条目」，故按命中位置就地构造。
        本任务不消费该字段，仅为保持 @return 契约。卡片已邀请时按钮不存在，
        退回该槽的锚点位置。
        """
        source = DailyAltAccAssets.I_COOPERATION_INVITE
        hit = next((item for item in invite_hits if item[3] == slot), None)
        # 已邀请时保留该列邀请区域，不能把右上角的协作标记当作邀请点击位置。
        origin_x, origin_y = COOP_ANCHOR_ORIGIN['邀请']
        x, y = (hit[1], hit[2]) if hit else (
            origin_x + COOP_TARGET_SLOT_PITCH * slot, origin_y)
        return RuleImage(
            roi_front=(x, y, source.roi_front[2], source.roi_front[3]),
            roi_back=COOP_CARD_BAND,
            method=source.method,
            threshold=source.threshold,
            file=source.file,
        )

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

    def _read_normal_cooperation_targets(self, slot: int, anchor) -> dict:
        """读取普通协作的双目标（自己击败X / 好友击败Y）。

        区域由「槽位偏移 + 标记抖动」一次算出。改造前是先试固定 ROI、失败再按
        邀请锚点平移重试一次；锚点位置本身已是实测值，固定 ROI 那一步是多余的，
        故这里只保留一次带偏移的读取，不再需要二次 fallback。
        """
        image = getattr(getattr(self, "device", None), "image", None)
        if image is None:
            return {}
        shift = self._coop_target_shift(anchor, slot)
        try:
            result = self._normal_target_result(
                self._ocr_single(
                    image,
                    self._shift_roi(COOP_DISCOVERER_BASE, shift),
                    f"wq_cooperation_discoverer_slot_{slot + 1}",
                ),
                self._ocr_single(
                    image,
                    self._shift_roi(COOP_FRIEND_BASE, shift),
                    f"wq_cooperation_friend_slot_{slot + 1}",
                ),
            )
        except Exception as exc:
            logger.warning(f"普通协作目标 OCR 失败(slot={slot + 1}): {exc}")
            result = {}
        logger.info(
            f"normal cooperation slot={slot + 1} shift={shift} "
            f"discoverer={result.get('discoverer_monster')!r} "
            f"friend={result.get('friend_monster')!r}"
        )
        return result

    def _read_real_sushi_target(self, slot: int, anchor) -> dict:
        """读取现世体协的单目标（击败N个X）。

        四列都按协/享或邀请锚点位置平移同一文字区域，不依赖逐列 OCR 资产。
        """
        image = getattr(getattr(self, "device", None), "image", None)
        if image is None:
            return {}
        shift = self._coop_target_shift(anchor, slot)
        try:
            monster = _parse_real_cooperation_monster(self._ocr_single(
                image,
                self._shift_roi(COOP_REAL_MONSTER_BASE, shift),
                f"wq_real_cooperation_monster_slot_{slot + 1}",
            ))
        except Exception as exc:
            logger.warning(f"现世体协目标 OCR 失败(slot={slot + 1}): {exc}")
            monster = ""
        logger.info(
            f"real sushi cooperation slot={slot + 1} shift={shift} monster={monster!r}"
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
            # 确认处于协作页面且至少一个协作组件命中后，才开始批量读取卡片。
            if self.wait_for_cooperation_network_normal() is True:
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

    def _coop_found_at(self) -> str:
        """本次识别帧的时刻字符串，见 ``_coop_scan_at`` 的说明。

        退化分支只在没走 get_cooperation_info 的调用里会走到（单测、手工构造）：
        拿不出取帧时刻时，当下是最接近的估计——比「不写时间」好得多，下游把没有
        采集时间的协作判为不可判定、排除出供需，等于白识别一场。
        """
        return (self._coop_scan_at or datetime.now()).strftime(COOP_TS_FORMAT)

    def _coop_emit(self, type_name: str, real: bool, cooperation: dict, total=None) -> None:
        """产出该槽的日志、推送与归档事件。

        日志文案与推送文案沿用改造前的逐字表述，前端展示与推送逻辑依赖它们；
        推送范围也保持原样——只有勾协与体协推送，其余类型只归档。

        事件里带 ``found_at``：识别帧时刻随事件一起走，持久表与 STAT 不再
        各自记时。表内同时保存轮次和卡槽快照，窗口归属只有一个来源。
        """
        spec = self.COOP_TYPE_SPEC[type_name]
        label = spec['label_real'] if real else spec['label']
        logger.info(spec['log_real'] if (real and spec['log_real']) else spec['log'])
        event = {"type": spec['event_type'], "real": real, "label": label,
                 "found_at": self._coop_found_at()}
        if 'slot' in cooperation:
            # 卡槽只用于保存本轮逐卡推送快照，不改变管家已有的协作去重 ID。
            event['slot'] = cooperation['slot']
        if 'food_kind' in spec:
            event["food_kind"] = spec['food_kind']
        event.update({
            key: cooperation[key]
            for key in ("discoverer_monster", "friend_monster", "monster_text")
            if cooperation.get(key)
        })
        self.msg.append([MSGType.cooperation, event])
        emit_stat = getattr(self, 'emit_stat', None)
        if emit_stat and total is not None:
            # 日志、持久表和推送用同一事件；先写日志，持久化失败时仍有原始证据。
            emit_stat(
                StatEvent.COOP, ctype=event['type'], real=event['real'], total=total,
                found_at=event['found_at'], food_kind=event.get('food_kind'),
                discoverer_monster=event.get('discoverer_monster', ''),
                friend_monster=event.get('friend_monster', ''),
                slot=event.get('slot'),
            )
        record_cooperation(event, self._coop_store, self._account_identity,
                           round_id=self._coop_round_id)
        if spec['notify']:
            self.push_notify(content=f"    发现{label}", title="协作任务提醒")

    def get_cooperation_info(self) -> List:
        """
            获取协作任务详情
        @return: 协作任务类型与邀请按钮
        """
        self.screenshot()
        image = getattr(getattr(self, "device", None), "image", None)
        if image is not None:
            wait_cooperation_prompt_clear(self)
            image = self.device.image
        # 提示消失后取帧即记时刻：下面只做模板匹配与 OCR，不再截图，所有协作
        # 共用最终识别帧的时间，避免等待跨过 05:00 / 18:00 时归入旧刷新窗口。
        self._coop_scan_at = datetime.now()
        retList = []
        if image is None:
            logger.warning("协作识别取帧失败，本屏按没有协作任务处理")
            logger.info(f"get cooperation size {len(retList)}")
            return retList

        # ① 先在整块协作板中匹配三种锚点，命中坐标均为屏幕绝对值。
        anchor_slots, anchor_hits = self._coop_collect(
            image, self.COOP_ANCHOR_RULES, COOP_ANCHOR_SLOT_X, COOP_ANCHOR_PRIORITY)
        logger.info(f"[协作锚点] 协={len(anchor_hits['协'])} 享={len(anchor_hits['享'])}"
                    f" 邀请={len(anchor_hits['邀请'])}")
        # 整板无锚点 = 本屏没有协作任务。实测 103 张样本里 58 张如此，是多数情况
        # 而不是异常，故用 info 记录（设计文档原写 warning，按实测数据下调）。
        if not anchor_slots:
            logger.info("本屏无协/享/邀请标记，判定为没有协作任务")
            logger.info(f"get cooperation size {len(retList)}")
            return retList
        type_slots = self._coop_collect_types(image, self.COOP_TYPE_RULES, anchor_slots)
        # 有锚点且有类型的槽位才会产出事件；每条 STAT 沿用本屏总数口径。
        total = len(anchor_slots.keys() & type_slots.keys())

        # ② 逐槽产出。类型结果只在同时存在锚点的槽位被采纳 —— 类型模板本身不能
        # 证明「这是协作卡」：实测 sushi 在非协作卡的橙色鱼籽奖励上能得 0.9735，
        # 越过 0.8 阈值；锚点先行是唯一可靠的过滤（103 张样本上拦下 252 次误命中）。
        # 另：改造前的判据是「邀请按钮 或 享字」，一张已邀请的普通协作卡会让循环
        # break、同屏其后的卡片被一起跳过；现在只跳过没有锚点的槽位，不再提前终止。
        for slot in sorted(anchor_slots):
            anchor_name, anchor_score, anchor_x, anchor_y = anchor_slots[slot]
            logger.info(f"[协作锚点] slot={slot} {anchor_name} "
                        f"score={anchor_score:.4f} @({anchor_x},{anchor_y})")
            typed = type_slots.get(slot)
            if typed is None:
                logger.warning(f"槽{slot + 1} 有协作标记({anchor_name})但类型模板全部失配，跳过该槽")
                continue
            type_name, type_score, type_x, type_y = typed
            logger.info(f"[协作类型] slot={slot} {type_name} "
                        f"score={type_score:.4f} @({type_x},{type_y})")
            # 现世标记直接由胜出的锚点决定：协→普通、享→现世；走到邀请说明
            # 协/享 都没匹配上，按普通处理
            real = anchor_name == '享'
            cooperation = {
                'type': self.COOP_TYPE_SPEC[type_name]['type'],
                'inviteBtn': self._coop_invite_button(slot, anchor_hits['邀请']),
                'real': real,
                'slot': slot,
            }
            cooperation.update(
                self._coop_read_targets(type_name, real, slot, anchor_slots[slot]))
            retList.append(cooperation)
            self._coop_emit(type_name, real, cooperation, total=total)

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
