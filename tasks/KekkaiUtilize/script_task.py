# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import re
import time
from cached_property import cached_property
from datetime import timedelta, datetime

from module.base.timer import Timer
from module.atom.image_grid import ImageGrid
from module.logger import logger
from module.exception import TaskEnd

from tasks.GameUi.game_ui import GameUi
from tasks.Utils.config_enum import ShikigamiClass
from tasks.KekkaiUtilize.assets import KekkaiUtilizeAssets
from tasks.KekkaiUtilize.config import UtilizeRule, SelectFriendList
from tasks.KekkaiUtilize.utils import CardClass, target_to_card_class
from tasks.Component.ReplaceShikigami.replace_shikigami import ReplaceShikigami
from tasks.GameUi.page import page_main, page_guild
from module.base.utils import point2str
import random
from tasks.Pets.script_task import ScriptTask as Pets
""" 结界蹭卡 """


class ScriptTask(GameUi, ReplaceShikigami, KekkaiUtilizeAssets):
    # 搜索输入框未获得焦点时显示的占位文字
    PRIORITY_NAME_PLACEHOLDER = '请输入好友昵称或备注'
    # 好友列表右侧边界，动态选中标记识别区以此坐标收口
    PRIORITY_LIST_RIGHT = 640
    # 实测当前卡片中心与选中标记中心相差不超过7像素，相邻行至少相差100像素
    CARD_SELECTED_Y_THRESHOLD = 40
    # 未选中目标卡片时最多等待两秒，识别到选中标记后立即继续
    CARD_SELECTION_TIMEOUT = 2
    last_best_index = 99
    utilize_add_count = 0
    ap_max_num = 0
    jade_max_num = 0
    first_utilize = True
    # 优先搜索中未达标好友的结界卡数值记录：{好友名: {'zone': 区服, '斗鱼': 值, '太鼓': 值}}
    priority_friend_records: dict = {}
    msg: list = []
    def run(self):
        con = self.config.kekkai_utilize.utilize_config
        # 检查是否处于禁止运行时间段，命中则跳过本次运行
        self.check_forbidden_time('KekkaiUtilize', con.forbidden_time_enable, con.forbidden_time_range)
        self.msg = []
        self.ui_get_current_page()
        self.ui_goto(page_guild)
        logger.info(f'开始蹭卡{self.config.kekkai_utilize.utilize_config.utilize_rule}')
        # 进入寮结界
        self.goto_realm()
        # 育成界面去蹭卡
        if con.utilize_enable:
            self.check_utilize_add()

        # 查看育成满级
        if con.exchange_before:
            self.check_max_lv(con.shikigami_class)
        # 检查蹭卡收获
        if con.guild_ap_enable:
            self.check_utilize_harvest()
        # 收体力盒子或者是经验盒子
        self.check_box_ap_or_exp(con.box_ap_enable, con.box_exp_enable, con.box_exp_waste)
        # 收取寮资金和体力
        if con.guild_assets_enable:
            self.recive_guild_ap_or_assets(con.harvest_guild_max_times)
        if not con.utilize_enable:
            self.set_next_run(task='KekkaiUtilize', finish=True, success=True)
        if con.pets_enable:
            pets = Pets(self.config, self.device)
            pets.run()
        logger.info(self.msg)
        raise TaskEnd(self.msg)

    def recive_guild_ap_or_assets(self, max_tries: int = 3):
        for i in range(1, max_tries+1):
            self.ui_get_current_page()
            self.ui_goto(page_guild)
            # 在寮的主界面 检查是否有收取体力或者是收取寮资金
            if self.check_guild_ap_or_assets():
                logger.warning(f'第[{i}]次检查寮收获,成功')
                self.ui_goto(page_main)
                break
            else:
                logger.warning(f'第[{i}]次检查寮收获寮收获,失败')
            self.ui_goto(page_main)

    def check_utilize_add(self):
        con = self.config.kekkai_utilize.utilize_config
        while 1:
            self.utilize_add_count += 1
            if self.utilize_add_count >= 5:
                logger.warning('没有合适可以蹭的卡, 5分钟后再次执行蹭卡')
                # 添加消息到列表，以便在TaskEnd时返回
                from tasks.DailyAltAcc.config import MSGType
                self.msg.append([MSGType.Utilize, "未找到寄养卡"])
                self.push_notify(content=f"没有合适可以蹭的卡, 5分钟后再次执行蹭卡")
                
                if not self.config.kekkai_utilize.utilize_config.utilize_rule == UtilizeRule.DAILY:
                    self.config.notifier.push(content=f'没有合适可以蹭的卡, 5分钟后再次执行蹭卡', title='寄养')
                self.set_next_run(task='KekkaiUtilize', target=datetime.now() + timedelta(minutes=5))

                return

            # 无论收不收到菜，都会进入看看至少看一眼时间还剩多少
            time.sleep(0.5)
            # 进入育成界面
            self.realm_goto_grown()
            self.screenshot()

            if not self.appear(self.I_UTILIZE_ADD):
                remaining_time = self.O_UTILIZE_RES_TIME.ocr_duration(self.device.image)
                if not isinstance(remaining_time, timedelta):
                    logger.warning('Ocr remaining time error')
                logger.info(f'Utilize remaining time: {remaining_time}')
                # 已经蹭上卡了，设置下次蹭卡时间  # 减少30秒
                # remaining_time = remaining_time - timedelta(seconds=30)
                next_time = datetime.now() + remaining_time
                if not self.config.kekkai_utilize.utilize_config.utilize_rule == UtilizeRule.DAILY:
                    self.config.notifier.push(content=f'下次寄养时间: {next_time}', title='寄养')
                
                self.set_next_run(task='KekkaiUtilize', target=next_time)
                return
            if not self.grown_goto_utilize():
                logger.info('Utilize failed, exit')
                # 未进入蹭卡界面时退出本轮，避免在错误界面继续执行寄养
                return
            # 开始执行寄养
            if self.run_utilize(con.select_friend_list, con.shikigami_class, con.shikigami_order):
                # 退出寮结界
                self.back_guild()
                # 进入寮结界
                self.goto_realm()
            else:
                self.back_realm()

    def check_max_lv(self, shikigami_class: ShikigamiClass = ShikigamiClass.N):
        """
        在结界界面，进入式神育成，检查是否有满级的，如果有就换下一个
        退出的时候还是结界界面
        :return:
        """
        self.realm_goto_grown()
        if self.appear(self.I_RS_LEVEL_MAX):
            # 存在满级的式神
            logger.info('Exist max level shikigami and replace it')
            self.unset_shikigami_max_lv()
            self.switch_shikigami_class(shikigami_class)
            self.set_shikigami(shikigami_order=7, stop_image=self.I_RS_NO_ADD)
        else:
            logger.info('No max level shikigami')
        if self.detect_no_shikigami():
            logger.warning('There are no any shikigami grow room')
            self.switch_shikigami_class(shikigami_class)
            self.set_shikigami(shikigami_order=7, stop_image=self.I_RS_NO_ADD)

        # 回到结界界面
        while 1:
            self.screenshot()

            if self.appear(self.I_REALM_SHIN) and self.appear_multi_scale(self.I_SHI_GROWN):
                self.screenshot()
                if not self.appear(self.I_REALM_SHIN):
                    continue
                break
            if self.appear_then_click(self.I_UI_BACK_BLUE, interval=2.5):
                continue

    def check_guild_ap_or_assets(self, ap_enable: bool = True, assets_enable: bool = True) -> bool:
        """
        在寮的主界面 检查是否有收取体力或者是收取寮资金
        如果有就顺带收取
        :return:
        """
        timer_check = Timer(2)
        timer_check.start()
        click_ap = False
        while 1:
            self.screenshot()

            # 获得奖励
            if self.ui_reward_appear_click():
                timer_check.reset()
                continue

            if timer_check.reached():
                return False

            if click_ap and not self.appear(self.I_GUILD_AP) and not self.appear(self.I_UI_REWARD):
                return True

            # 关闭展开的寮活动横幅
            if self.appear_then_click(self.I_GUILD_EXPAND):
                timer_check.reset()
                continue

            # 资金收取确认
            if self.appear_then_click(self.I_GUILD_ASSETS_RECEIVE, interval=1):
                time.sleep(1)
                timer_check.reset()
                continue

            # 收资金
            if self.appear_then_click(self.I_GUILD_ASSETS, interval=1.5, threshold=0.6):
                timer_check.reset()
                continue

            # 收体力
            if self.appear_then_click(self.I_GUILD_AP, interval=1):
                # 等待1秒，看到获得奖励
                time.sleep(1)
                logger.info('appear_click guild_ap success')
                if self.ui_reward_appear_click(True):
                    logger.info('appear_click reward success')
                    click_ap = True
                    time.sleep(1)
                    timer_check.reset()
                continue

    def goto_realm(self):
        """
        从寮的主界面进入寮结界
        :return:
        """
        while 1:
            self.screenshot()
            if self.appear(self.I_REALM_SHIN):
                break
            if self.appear_multi_scale(self.I_SHI_DEFENSE):
                break
            if self.appear_then_click(self.I_PLANT_TREE_CLOSE):
                continue
            if self.appear_then_click(self.I_GUILD_REALM, interval=1):
                continue

    def check_box_ap_or_exp(self, ap_enable: bool = True, exp_enable: bool = True, exp_waste: bool = True) -> bool:
        """
        顺路检查盒子
        :param ap_enable:
        :param exp_enable:
        :return:
        """

        # 退出到寮结界
        def _exit_to_realm():
            # 右上方关闭红色
            while 1:
                self.screenshot()
                if self.appear(self.I_REALM_SHIN):
                    break
                if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                    continue

        # 先是体力盒子
        def _check_ap_box(appear: bool = False):
            if not appear:
                return False
            # 点击盒子
            timer_ap = Timer(6)
            timer_ap.start()
            while 1:
                self.screenshot()

                if self.appear(self.I_UI_REWARD):
                    while 1:
                        self.screenshot()
                        if not self.appear(self.I_UI_REWARD):
                            break
                        if self.appear_then_click(self.I_UI_REWARD, self.C_UI_REWARD, interval=1, threshold=0.6):
                            continue
                    logger.info('Reward box')
                    break

                if self.appear_then_click(self.I_BOX_AP, interval=1):
                    continue
                if self.appear_then_click(self.I_AP_EXTRACT, interval=2):
                    continue
                if timer_ap.reached():
                    logger.warning('Extract ap box timeout')
                    break
            logger.info('Extract AP box finished')
            _exit_to_realm()

        # 经验盒子
        def _check_exp_box(appear: bool = False):
            if not appear:
                logger.info('No exp box')
                return False

            time_exp = Timer(12)
            time_exp.start()
            while 1:
                self.screenshot()
                # 如果出现结界皮肤， 表示收取好了
                if self.appear(self.I_REALM_SHIN) and not self.appear(self.I_BOX_EXP, threshold=0.6):
                    break
                # 如果出现收取确认，表明进入到了有满级的
                if self.appear(self.I_UI_CONFIRM):
                    self.screenshot()
                    if not self.appear(self.I_UI_CANCEL):
                        logger.info('No cancel button')
                        continue
                    if exp_waste:
                        check_button = self.I_UI_CONFIRM
                    else:
                        check_button = self.I_UI_CANCEL
                    while 1:
                        self.screenshot()
                        if not self.appear(check_button):
                            break
                        if self.appear_then_click(check_button, interval=1):
                            continue
                    break

                if self.appear(self.I_EXP_EXTRACT):
                    # 如果达到今日领取的最大，就不领取了
                    cur, res, totol = self.O_BOX_EXP.ocr(self.device.image)
                    if cur == res == totol == 0:
                        continue
                    if cur == totol and cur + res == totol:
                        logger.info('Exp box reach max do not collect')
                        break
                if self.appear_then_click(self.I_BOX_EXP, threshold=0.6, interval=1):
                    continue
                if self.appear_then_click(self.I_EXP_EXTRACT, interval=1):
                    continue

                if time_exp.reached():
                    logger.warning('Extract exp box timeout')
                    break
            _exit_to_realm()

        self.screenshot()
        box_ap = self.appear(self.I_BOX_AP)
        box_exp = self.appear(self.I_BOX_EXP, threshold=0.6) or self.appear(self.I_BOX_EXP_MAX, threshold=0.6)
        if ap_enable:
            _check_ap_box(box_ap)
        if exp_enable:
            _check_exp_box(box_exp)

    def check_utilize_harvest(self) -> bool:
        """
        在寮结界界面检查是否有收获
        :return: 如果没有返回False, 如果有就收菜返回True
        """
        self.screenshot()
        appear = self.appear(self.I_UTILIZE_EXP)
        if not appear:
            logger.info('No utilize harvest')
            return False

        # 收获
        self.ui_get_reward(self.I_UTILIZE_EXP)
        return True

    def realm_goto_grown(self):
        """
        进入式神育成界面
        :return:
        """
        while 1:
            self.screenshot()

            if self.in_shikigami_growth():
                break

            if self.appear_then_click_multi_scale(self.I_SHI_GROWN, interval=1):
                continue
        logger.info('Enter shikigami grown')

    def grown_goto_utilize(self):
        """
        从式神育成界面到 蹭卡界面
        :return:
        """
        self.screenshot()
        if not self.appear(self.I_UTILIZE_ADD):
            logger.warning('No utilize add')
            return False

        while 1:
            self.screenshot()

            if self.appear(self.I_U_ENTER_REALM):
                break
            if self.appear_then_click(self.I_UTILIZE_ADD, interval=2):
                continue
        logger.info('Enter utilize')
        return True

    @staticmethod
    def parse_priority_search_names(raw_names: str) -> list[tuple[SelectFriendList, str]]:
        """解析带区服标记的优先搜索名称配置。"""
        if not raw_names or not raw_names.strip():
            return []

        zone_map = {
            '同区': SelectFriendList.SAME_SERVER,
            '同服': SelectFriendList.SAME_SERVER,
            'same_server': SelectFriendList.SAME_SERVER,
            '跨区': SelectFriendList.DIFFERENT_SERVER,
            '跨服': SelectFriendList.DIFFERENT_SERVER,
            'different_server': SelectFriendList.DIFFERENT_SERVER,
        }
        result = []
        for raw_item in re.split(r'[\n,，;；]+', raw_names):
            item = raw_item.strip()
            if not item:
                continue
            matched = re.match(
                r'^(同区|同服|跨区|跨服|same_server|different_server)\s*[:：|]\s*(.+)$',
                item,
                flags=re.IGNORECASE,
            )
            if not matched:
                logger.warning('忽略未标注同区或跨区的优先搜索名称: %s', item)
                continue
            zone_text = matched.group(1).lower()
            name = matched.group(2).strip()
            if not name:
                logger.warning('忽略角色名为空的优先搜索配置: %s', item)
                continue
            result.append((zone_map[zone_text], name))
        return result

    @staticmethod
    def _normalize_priority_name(text: str) -> str:
        """统一空白和常见异体字，降低好友名称 OCR 的比较误差。"""
        return re.sub(r'\s+', '', str(text or '')).replace('瑤', '瑶').replace('別', '别')

    @staticmethod
    def _opposite_friend_list(friend: SelectFriendList) -> SelectFriendList:
        """返回另一个好友区服，用于清空当前搜索状态。"""
        if friend == SelectFriendList.SAME_SERVER:
            return SelectFriendList.DIFFERENT_SERVER
        return SelectFriendList.SAME_SERVER

    def _reset_priority_search(self, current_friend: SelectFriendList,
                               target_friend: SelectFriendList) -> SelectFriendList:
        """通过切到另一区服重置搜索，再进入下一项目标区服。"""
        reset_friend = self._opposite_friend_list(current_friend)
        self.switch_friend_list(reset_friend)
        if reset_friend != target_friend:
            self.switch_friend_list(target_friend)
        return target_friend

    def _open_priority_name_search(self) -> bool:
        """打开好友名称搜索栏，搜索按钮出现后停止点击放大镜。"""
        timeout = Timer(8).start()
        while not timeout.reached():
            self.screenshot()
            if self.appear(self.I_SERACH_ON):
                return True
            if self.appear_then_click(self.I_SERACH_NAME, interval=0.6):
                continue
        logger.warning('打开好友名称搜索栏超时')
        return False

    def _priority_name_check_texts(self) -> list[str]:
        """读取搜索输入框中的全部 OCR 文本。"""
        results = self.O_NAME_CHECK.detect_and_ocr(self.device.image)
        return [item.ocr_text for item in results] if results else []

    def _focus_priority_name_input(self) -> bool:
        """持续点击名称输入框，直到占位文字消失。"""
        timeout = Timer(6).start()
        roi_x, roi_y, roi_w, roi_h = self.O_NAME_CHECK.roi
        click_x = int(roi_x + roi_w / 2)
        click_y = int(roi_y + roi_h / 2)
        placeholder = self._normalize_priority_name(self.PRIORITY_NAME_PLACEHOLDER)

        while not timeout.reached():
            # 每轮先点击再校验，避免首次 OCR 漏检时误判输入框已经聚焦
            self.device.click(click_x, click_y, control_name=self.O_NAME_CHECK.name)
            time.sleep(0.3)
            self.screenshot()
            current_text = ''.join(
                self._normalize_priority_name(text) for text in self._priority_name_check_texts()
            )
            if placeholder not in current_text:
                return True
        logger.warning('好友名称输入框未能获得焦点')
        return False

    def _input_priority_name(self, name: str) -> bool:
        """输入角色名，并等待输入框 OCR 确认名称已经写入。

        与 SearchId 一致采用逐字符输入，避免一次性 send_keys 输入中文昵称时
        出现乱码或内容不被游戏识别；send_keys(clear=True) 仅作兜底。
        """
        expected = self._normalize_priority_name(name)
        fallback_used = False
        for attempt in range(3):
            try:
                # 先清空输入框，避免重试时把同一个角色名重复追加
                self.device.u2.send_keys('', clear=True)
                # 逐字符输入，降低一次性输入长串中文被游戏漏识别的概率
                self.input_text_alternative(name)
            except Exception as error:
                if fallback_used:
                    logger.warning('好友名称输入失败: %s', error)
                    break
                logger.warning('逐字符输入不可用，改用清空后一次性输入: %s', error)
                try:
                    self.device.u2.send_keys(name, clear=True)
                except Exception as error2:
                    logger.warning('清空后一次性输入也不可用: %s', error2)
                fallback_used = True

            check_timer = Timer(3).start()
            while not check_timer.reached():
                time.sleep(0.3)
                self.screenshot()
                current_text = ''.join(
                    self._normalize_priority_name(text) for text in self._priority_name_check_texts()
                )
                if expected in current_text:
                    logger.info('优先搜索名称已输入: %s', name)
                    return True
            if fallback_used:
                break
            logger.warning('第%d次输入后未识别到角色名: %s', attempt + 1, name)
        return False

    def _click_priority_search(self) -> bool:
        """连续点击两到三次搜索按钮，降低单次点击丢失的概率。"""
        self.screenshot()
        if not self.appear(self.I_SERACH_ON):
            logger.warning('未识别到好友名称搜索按钮')
            return False
        click_x, click_y = self.I_SERACH_ON.front_center()
        for _ in range(random.randint(2, 3)):
            self.device.click(click_x, click_y, control_name=self.I_SERACH_ON.name)
            time.sleep(0.25)
        return True

    def _matching_priority_name_areas(self, name: str) -> list[tuple[int, int, int, int]]:
        """返回搜索结果中所有同名角色的绝对坐标，并按从上到下排序。"""
        expected = self._normalize_priority_name(name)
        results = self.O_NAME_LIST.detect_and_ocr(self.device.image)
        if not results:
            return []

        roi_x, roi_y, _, _ = self.O_NAME_LIST.roi
        areas = []
        for result in results:
            current = self._normalize_priority_name(result.ocr_text)
            if expected not in current:
                continue
            box = result.box
            # OCR 检测框可能轻微倾斜，按四点外接矩形换算为截图绝对坐标
            box_x = [point[0] for point in box]
            box_y = [point[1] for point in box]
            x = int(roi_x + min(box_x))
            y = int(roi_y + min(box_y))
            width = max(1, int(max(box_x) - min(box_x)))
            height = max(1, int(max(box_y) - min(box_y)))
            areas.append((x, y, width, height))
        return sorted(areas, key=lambda area: (area[1], area[0]))

    def _wait_priority_name_areas(self, name: str) -> list[tuple[int, int, int, int]]:
        """等待名称搜索结果加载完成。"""
        timeout = Timer(6).start()
        while not timeout.reached():
            self.screenshot()
            areas = self._matching_priority_name_areas(name)
            if areas:
                logger.info('搜索到%d个同名角色: %s', len(areas), name)
                return areas
            time.sleep(0.3)
        logger.info('未搜索到角色名: %s', name)
        return []

    @classmethod
    def _priority_selection_roi(cls, name_area: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        """按角色名纵坐标构造选中标记识别区，右边界固定为640。"""
        name_x, name_y, _, _ = name_area
        left = max(0, int(name_x))
        top = max(0, int(name_y) - 15)
        return left, top, max(1, cls.PRIORITY_LIST_RIGHT - left), 70

    def _select_priority_name_area(self, name: str,
                                   name_area: tuple[int, int, int, int]) -> bool:
        """点击指定同名角色，直到该行出现选中标记。"""
        original_roi = self.I_SELECT_REALM_ON.roi_back
        self.I_SELECT_REALM_ON.roi_back = self._priority_selection_roi(name_area)
        name_x, name_y, name_w, name_h = name_area
        click_x = int(name_x + name_w / 2)
        click_y = int(name_y + name_h / 2)
        timeout = Timer(6).start()
        try:
            while not timeout.reached():
                self.screenshot()
                if self.appear(self.I_SELECT_REALM_ON):
                    logger.info('已选中同名角色: %s @ %s', name, name_area)
                    return True
                self.device.click(click_x, click_y, control_name='priority_search_name')
                time.sleep(0.5)
        finally:
            self.I_SELECT_REALM_ON.roi_back = original_roi
        logger.warning('点击后未识别到角色选中标记: %s @ %s', name, name_area)
        return False

    @staticmethod
    def _priority_card_roi(name_area: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        """根据角色名所在行限制结界卡识别范围，避免检查到其他同名角色。"""
        _, name_y, _, name_h = name_area
        top = max(0, int(name_y) - 35)
        height = min(720 - top, max(80, int(name_h) + 60))
        return 520, top, 120, height

    @classmethod
    def _card_matches_selected_row(
            cls,
            card_area: tuple[int, int, int, int],
            selected_area: tuple[int, int, int, int],
    ) -> bool:
        """根据中心纵坐标判断结界卡是否属于当前选中好友。"""
        _, card_y, _, card_height = card_area
        _, selected_y, _, selected_height = selected_area
        card_center_y = card_y + card_height / 2
        selected_center_y = selected_y + selected_height / 2
        return abs(card_center_y - selected_center_y) <= cls.CARD_SELECTED_Y_THRESHOLD

    def _ensure_card_selected(self, card_area: tuple[int, int, int, int]) -> bool:
        """确保目标结界卡已选中，已对齐时跳过点击和固定等待。"""
        self.C_SELECT_CARD.roi_front = card_area
        if self.appear(self.I_SELECT_REALM_ON) and self._card_matches_selected_row(
                card_area, tuple(self.I_SELECT_REALM_ON.roi_front)):
            logger.info('结界卡已处于选中行，跳过重复点击: %s', card_area)
            return True

        self.click(self.C_SELECT_CARD)
        timeout = Timer(self.CARD_SELECTION_TIMEOUT).start()
        while not timeout.reached():
            self.screenshot()
            if self.appear(self.I_SELECT_REALM_ON) and self._card_matches_selected_row(
                    card_area, tuple(self.I_SELECT_REALM_ON.roi_front)):
                logger.info('结界卡点击后已选中: %s', card_area)
                return True
        logger.warning('等待结界卡选中超时: %s', card_area)
        return False

    @staticmethod
    def _meets_min_value(card_value: int, min_value: int) -> bool:
        """数值达标判断：未配置门槛（0）时保持大于 0，配置后需达到门槛值。"""
        if min_value > 0:
            return card_value >= min_value
        return card_value > 0

    def _priority_search_min_for(self, card_type: str) -> int:
        """返回指定结界卡类型配置的最低寄养门槛；斗鱼/太鼓分别取值，0 表示不限制。"""
        con = self.config.kekkai_utilize.utilize_config
        if card_type == '斗鱼':
            return con.priority_search_min_fish
        if card_type == '太鼓':
            return con.priority_search_min_taiko
        return 0

    def _select_priority_name_card(self, name: str,
                                   name_area: tuple[int, int, int, int],
                                   check_min: bool = True) -> bool:
        """检查指定角色所在行的结界卡。

        check_min=True 时需达到对应类型的最低门槛才选中，未达标的结界卡数值会被记录
        供后续最佳值兜底；check_min=False 时直接选中该行结界卡（兜底搜索路径）。
        """
        self.screenshot()
        card_roi = self._priority_card_roi(name_area)
        matches = []
        for order, target in enumerate(self.order_targets.images):
            original_roi = target.roi_back
            try:
                target_matches = target.match_all_any(
                    self.device.image,
                    threshold=target.threshold,
                    roi=card_roi,
                    nms_threshold=0.3,
                )
            finally:
                target.roi_back = original_roi
            for score, x, y, width, height in target_matches:
                matches.append((order, target, score, (x, y, width, height)))

        matches.sort(key=lambda item: (item[0], item[3][1], -item[2]))
        for _, target, _, card_area in matches:
            card_class = target_to_card_class(target)
            if card_class not in self.order_cards:
                continue
            if not self._ensure_card_selected(card_area):
                continue
            card_type, card_value = self.check_card_num()
            expected_type = '太鼓' if card_class.value.startswith('taiko') else '斗鱼'
            if card_type != expected_type:
                continue
            min_value = self._priority_search_min_for(card_type)
            if check_min and not self._meets_min_value(card_value, min_value):
                # 未达标：记录该好友结界卡数值，供最佳值匹配后直接搜索该好友
                self._record_priority_friend_value(name, card_type, card_value)
                logger.info('优先角色%s的结界卡不满足要求: %s@%s (最低值 %s)', name, card_type, card_value, min_value)
                continue
            logger.info('优先角色%s的结界卡满足要求: %s@%s (最低值 %s)', name, card_type, card_value, min_value)
            return True
        return False

    def _record_priority_friend_value(self, name: str, card_type: str, card_value: int) -> None:
        """记录优先好友未达标结界卡的数值，供最佳值匹配后直接搜索该好友。"""
        record = self.priority_friend_records.setdefault(name, {})
        record[card_type] = max(record.get(card_type, 0), card_value)

    def _priority_friend_matching(self, target_value: int, card_type: str) -> str | None:
        """返回结界卡数值不低于最佳值且最接近的优先好友名，没有则返回 None。"""
        best_name = None
        best_value = -1
        for name, record in self.priority_friend_records.items():
            value = record.get(card_type, 0)
            if value >= target_value and value > best_value:
                best_name = name
                best_value = value
        return best_name

    def _select_from_priority_names(
            self,
            priority_names: list[tuple[SelectFriendList, str]],
            current_friend: SelectFriendList,
            shikigami_class: ShikigamiClass = ShikigamiClass.N,
            shikigami_order: int = 7,
            check_min: bool = True,
    ) -> tuple[bool, SelectFriendList]:
        """依次搜索优先角色，逐个检查同名结果；寄养成功后返回，坑位被占用则跳过当前角色。"""
        # 确保实例属性存在，避免误改类属性造成跨轮污染
        if 'priority_friend_records' not in self.__dict__:
            self.priority_friend_records = {}
        for index, (target_friend, name) in enumerate(priority_names):
            # 记录该好友所在区服，供后续最佳值匹配后直接搜索该好友
            self.priority_friend_records.setdefault(name, {})['zone'] = target_friend
            if index == 0:
                self.switch_friend_list(target_friend)
            else:
                current_friend = self._reset_priority_search(current_friend, target_friend)
            current_friend = target_friend
            logger.info('开始优先搜索好友: %s [%s]', name, target_friend.value)

            if not self._open_priority_name_search():
                continue
            if not self._focus_priority_name_input():
                continue
            if not self._input_priority_name(name):
                continue
            if not self._click_priority_search():
                continue

            for name_area in self._wait_priority_name_areas(name):
                if not self._select_priority_name_area(name, name_area):
                    continue
                if not self._select_priority_name_card(name, name_area, check_min=check_min):
                    continue
                # 选中好友后进入结界寄养；坑位被占用时退回蹭卡界面并跳过当前角色
                result = self._enter_realm_and_utilize(shikigami_class, shikigami_order)
                if result == 'occupied':
                    logger.info('好友%s的结界坑位被占用，跳过该角色', name)
                    # 坑位被占用时该好友不可用，移除其结界卡数值，避免被计入最佳值
                    self.priority_friend_records.pop(name, None)
                    self._exit_friend_realm_to_utilize()
                    continue
                if result == 'ok':
                    return True, current_friend
                logger.warning('进入好友%s的结界失败，跳过该角色', name)
                self._exit_friend_realm_to_utilize()
                continue
        return False, current_friend

    def _search_priority_friend_and_utilize(self, name: str, friend: SelectFriendList,
                                            shikigami_class: ShikigamiClass,
                                            shikigami_order: int) -> bool:
        """直接搜索指定优先好友并寄养（不检查最低值），用于最佳值匹配好友时免翻列表。"""
        selected, _ = self._select_from_priority_names(
            [(friend, name)], friend, shikigami_class, shikigami_order, check_min=False)
        return selected

    def _enter_realm_and_utilize(self, shikigami_class: ShikigamiClass,
                                 shikigami_order: int) -> str:
        """进入已选好友的结界并上式神寄养。

        返回字符串状态供调用方决定后续：
        - 'ok': 寄养成功
        - 'occupied': 结界坑位被占用，未上式神
        - 'failed': 进入好友结界失败
        """
        # 找到卡后清理重试次数和原选卡流程留下的最优值
        self.utilize_add_count = 0
        self.ap_max_num, self.jade_max_num = 0, 0
        logger.info('开始执行进入结界蹭卡流程')
        self.screenshot()
        # 进入结界
        if not self.appear(self.I_U_ENTER_REALM):
            logger.warning('Cannot find enter realm button')
            # 可能是滑动的时候出错
            logger.warning('The best reason is that the swipe is wrong')
            return 'failed'
        wait_timer = Timer(20)
        wait_timer.start()
        while 1:
            self.screenshot()
            if self.appear(self.I_U_ADD_1) or self.appear(self.I_U_ADD_2):
                logger.info('Appear enter friend realm button')
                break
            if self.appear(self.I_CHECK_FRIEND_REALM_1):
                self.wait_until_stable(self.I_CHECK_FRIEND_REALM_1)
                logger.info('Appear enter friend realm button')
                break
            if self.appear(self.I_CHECK_FRIEND_REALM_3):
                self.wait_until_stable(self.I_CHECK_FRIEND_REALM_3)
                logger.info('Appear enter friend realm button')
                break
            if wait_timer.reached():
                self.save_image(wait_time=0, push_flag=False, content='进入好友结界超时', image_type='png')
                logger.warning('Appear friend realm timeout')
                return 'failed'
            if self.appear_then_click(self.I_CHECK_FRIEND_REALM_2, interval=1.5):
                logger.info('Click too fast to enter the friend\'s realm pool')
                continue
            if self.appear_then_click(self.I_U_ENTER_REALM, interval=2.5):
                time.sleep(0.5)
                continue
        logger.info('Enter friend realm')

        # 判断好友的有两个位置还是一个坑位
        stop_image = None
        self.screenshot()
        if self.appear(self.I_U_ADD_1):  # 右侧第一个有（无论左侧有没有）
            logger.info('Right side has one')
            stop_image = self.I_U_ADD_1
        elif self.appear(self.I_U_ADD_2) and not self.appear(self.I_U_ADD_1):  # 右侧第二个有 但是最左边的没有，这表示只留有一个坑位
            logger.info('Right side has two')
            stop_image = self.I_U_ADD_2
        if not stop_image:
            # 没有坑位可能是其他人的手速太快了抢占了
            self.save_image(content='没有坑位了', wait_time=0, push_flag=False, image_type='png')
            logger.warning('没有坑位可能是其他人的手速太快了抢占了')
            return 'occupied'
        # 切换式神的类型
        self.switch_shikigami_class(shikigami_class)
        # 上式神
        self.set_shikigami(shikigami_order, stop_image)
        return 'ok'

    def _exit_friend_realm_to_utilize(self) -> None:
        """从好友结界退回到蹭卡界面（好友列表），供继续搜索下一个优先角色。"""
        timeout = Timer(10).start()
        while not timeout.reached():
            self.screenshot()
            if self.appear(self.I_UTILIZE_FRIEND_GROUP) or self.appear(self.I_UTILIZE_ZONES_GROUP):
                return
            if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                continue
            if self.appear_then_click(self.I_UI_BACK_BLUE, interval=1):
                continue
        logger.warning('退出好友结界返回蹭卡界面超时')

    def switch_friend_list(self, friend: SelectFriendList = SelectFriendList.SAME_SERVER) -> bool:
        """
        切换不同的服务区
        :param friend:
        :return:
        """
        logger.info('Switch friend list to %s', friend)
        if friend == SelectFriendList.SAME_SERVER:
            check_image = self.I_UTILIZE_FRIEND_GROUP
        else:
            check_image = self.I_UTILIZE_ZONES_GROUP

        timer_click = Timer(1)
        timer_click.start()
        while 1:
            self.screenshot()
            if self.appear(check_image):
                break
            if timer_click.reached():
                timer_click.reset()
                x, y = check_image.coord()
                self.device.click(x=x, y=y, control_name=check_image.name)
        if friend == SelectFriendList.DIFFERENT_SERVER:
            time.sleep(1)
        time.sleep(0.5)
        return True

    @cached_property
    def order_targets(self) -> ImageGrid:
        rule = self.config.kekkai_utilize.utilize_config.utilize_rule
        if rule == UtilizeRule.DEFAULT:
            return ImageGrid([self.I_U_FISH_6, self.I_U_TAIKO_6, self.I_U_FISH_5, self.I_U_TAIKO_5])
        elif rule == UtilizeRule.FISH:
            return ImageGrid([self.I_U_FISH_6, self.I_U_FISH_5])
        elif rule == UtilizeRule.TAIKO:
            return ImageGrid([self.I_U_TAIKO_6, self.I_U_TAIKO_5])
        elif rule == UtilizeRule.DAILY:
            return ImageGrid([self.I_U_TAIKO_6, self.I_U_TAIKO_5, self.I_U_TAIKO_4, self.I_U_TAIKO_3])
        else:
            logger.error('Unknown utilize rule')
            raise ValueError('Unknown utilize rule')

    @cached_property
    def order_cards(self) -> list[CardClass]:
        rule = self.config.kekkai_utilize.utilize_config.utilize_rule
        result = []
        if rule == UtilizeRule.DEFAULT:
            result = [CardClass.FISH6, CardClass.TAIKO6, CardClass.FISH5, CardClass.TAIKO5,
                      CardClass.TAIKO4, CardClass.FISH4, CardClass.TAIKO3, CardClass.FISH3]
        elif rule == UtilizeRule.FISH:
            result = [CardClass.FISH6, CardClass.FISH5,
                      CardClass.TAIKO6, CardClass.TAIKO5, CardClass.FISH4, CardClass.TAIKO4, CardClass.FISH3,
                      CardClass.TAIKO3]
        elif rule == UtilizeRule.TAIKO:
            result = [CardClass.TAIKO6, CardClass.TAIKO5,
                      CardClass.TAIKO4, CardClass.TAIKO3, CardClass.FISH6, CardClass.FISH5, CardClass.FISH4,
                      CardClass.FISH3]
        elif rule == UtilizeRule.DAILY:
            result = [CardClass.TAIKO6, CardClass.TAIKO5,
                      CardClass.TAIKO4, CardClass.TAIKO3]
        else:
            logger.error('Unknown utilize rule')
            raise ValueError('Unknown utilize rule')
        return result

    def run_utilize(self, friend: SelectFriendList = SelectFriendList.SAME_SERVER,
                    shikigami_class: ShikigamiClass = ShikigamiClass.N,
                    shikigami_order: int = 7):
        """
        执行寄养
        :param shikigami_class:
        :param friend:
        :param rule:
        :return:
        """
        logger.hr('Start utilize')
        # 每轮寄养前清空优先好友记录，避免跨轮残留
        self.priority_friend_records = {}
        first_round = self.first_utilize
        if self.first_utilize:
            self.first_utilize = False
        if not first_round:
            # 非首次：搜索前切回配置的优先好友区服
            self.switch_friend_list(friend)
        # 首次搜索前不切换区服，避免干扰搜索框定位

        # --------------- 结界卡选择 ---------------
        priority_names = self.parse_priority_search_names(
            self.config.kekkai_utilize.utilize_config.priority_search_names
        )
        selected = False
        current_friend = friend
        if priority_names:
            # 优先搜索成功时已在内部进入结界并寄养
            selected, current_friend = self._select_from_priority_names(
                priority_names, current_friend, shikigami_class, shikigami_order)
        if not selected:
            if priority_names:
                # 优先名称全部失败后清空搜索，并回到配置的优先好友区服执行原流程
                self._reset_priority_search(current_friend, friend)
            # 优先搜索完成后再滑动到列表最下方，拉到底后切换一遍同区跨区刷新列表
            if first_round:
                self.swipe(self.S_U_END, interval=3)
                if friend == SelectFriendList.SAME_SERVER:
                    self.switch_friend_list(SelectFriendList.DIFFERENT_SERVER)
                    self.switch_friend_list(SelectFriendList.SAME_SERVER)
                else:
                    self.switch_friend_list(SelectFriendList.SAME_SERVER)
                    self.switch_friend_list(SelectFriendList.DIFFERENT_SERVER)
            if not self._select_optimal_resource_card(shikigami_class, shikigami_order):
                return False
            # 原选卡流程选中的卡，进入结界寄养；占用时保持原行为直接返回
            result = self._enter_realm_and_utilize(shikigami_class, shikigami_order)
            if result == 'occupied':
                logger.info('原选卡流程的结界坑位被占用')
                return True
            if result == 'failed':
                return False
        return True

    def _select_optimal_resource_card(self,
                                      shikigami_class: ShikigamiClass = ShikigamiClass.N,
                                      shikigami_order: int = 7):
        """整合后的智能选卡主逻辑（无嵌套函数版）"""
        # 类常量声明（需在类中定义）
        RESOURCE_PRESETS = {
            '斗鱼': [151, 143, 134, 126, 101, 84],
            '太鼓': [76,  76,  67,  67,  59,  50]
        }
        MAX_INDEX = 99

        def get_resource_index(resource_name, current_value, preset_values):
            """获取资源匹配的档位索引"""
            for idx, val in enumerate(preset_values):
                if current_value >= val:
                    logger.info(f'📊 {resource_name}区间匹配: {current_value} ≥ {val} (档位{idx})')
                    return idx
            logger.warning(f'⚠️ {resource_name}值[{current_value}]低于所有预设')
            return MAX_INDEX
        if self.config.kekkai_utilize.utilize_config.utilize_rule == UtilizeRule.DAILY:
            logger.info('DAILY规则：寻找任意太鼓卡')
            if self._find_any_taiko_card():
                logger.info('✅ 找到太鼓卡，立即寄养')
                return True
            else:
                logger.info('❌ 未找到合适的太鼓卡')
                return False
        while True:
            self.screenshot()

            # 第一阶段：初始记录获取
            if self.ap_max_num == 0 and self.jade_max_num == 0:
                logger.hr('第一阶段：初始记录获取', 2)
                if self._current_select_best():
                    logger.info(f'✅ 完美结界卡确认成功，重置状态')
                    self.ap_max_num, self.jade_max_num = 0, 0
                    return True
                logger.info(f'📝 记录最佳值 | 斗鱼:{self.ap_max_num} 太鼓:{self.jade_max_num}')
                if self.ap_max_num == 0 and self.jade_max_num == 0:
                    # 一整轮都没有可用结界卡，放弃
                    return False
                # 未找到达标卡，进入第二阶段用当前最佳值兜底

            logger.hr('第二阶段：资源优先级判断', 2)
            # 获取双资源档位
            ap_index = get_resource_index('斗鱼', self.ap_max_num, RESOURCE_PRESETS['斗鱼'])
            jade_index = get_resource_index('太鼓', self.jade_max_num, RESOURCE_PRESETS['太鼓'])

            # 双资源超限处理
            if ap_index == MAX_INDEX and jade_index == MAX_INDEX:
                if self.ap_max_num <= 0 and self.jade_max_num <= 0:
                    logger.warning('🔄 未记录到任何可用结界卡，重置初始记录')
                    self.ap_max_num, self.jade_max_num = 0, 0
                    return False
                # 探索未达到最低值，第二轮直接选择当前数值较高的最佳值兜底
                res_type, target = ('斗鱼', self.ap_max_num) if self.ap_max_num >= self.jade_max_num else ('太鼓', self.jade_max_num)
            else:
                # 决策优先级
                res_type, target = ('斗鱼', self.ap_max_num) if ap_index <= jade_index else ('太鼓', self.jade_max_num)
            logger.info(f'⚖️ 选择{res_type}卡 | 目标: {target}')

            # 第三阶段：执行选卡操作
            logger.hr('第三阶段：执行选卡操作', 2)
            # 若最佳值恰好来自某个优先好友的结界卡，直接搜索该好友寄养，避免再次翻列表
            friend_name = self._priority_friend_matching(target, res_type)
            if friend_name:
                friend_zone = self.priority_friend_records.get(friend_name, {}).get(
                    'zone', SelectFriendList.SAME_SERVER)
                if self._search_priority_friend_and_utilize(
                        friend_name, friend_zone, shikigami_class, shikigami_order):
                    logger.info(f'✅ 搜索优先好友{friend_name}寄养成功')
                    self.ap_max_num, self.jade_max_num = 0, 0
                    return True
                logger.warning(f'❌ 搜索优先好友{friend_name}寄养失败，回落翻列表确认')
            if self._current_select_best(res_type, target, selected_card=True):
                logger.info(f'✅ {res_type}卡确认成功，重置状态')
                self.ap_max_num, self.jade_max_num = 0, 0
                return True
            else:
                logger.warning(f'❌ {res_type}卡确认失败，重置状态')
                self.ap_max_num, self.jade_max_num = 0, 0
                return False
# ... existing code ...
    def _find_any_taiko_card(self):
        """在DAILY规则下寻找任意太鼓卡并立即使用"""
        logger.info('开始寻找任意太鼓卡...')
        RESOURCE_CONFIG = {
            '斗鱼': {'max': 151, 'record_attr': 'ap_max_num'},
            '太鼓': {'max': 76, 'record_attr': 'jade_max_num'}
        }
        MAX_SWIPES = 20  # 最大滑动次数
        CONSEC_MISS = 3  # 允许连续无卡次数
        TIMEOUT = 120  # 操作超时(秒)

        # ============== 初始化阶段 ==============#
        timer = Timer(TIMEOUT).start()
        miss_count = 0  # 连续无卡计数器

        # ============== 主滑动循环 ==============#
        for swipe_count in range(MAX_SWIPES + 1):
            # 超时检测
            if timer.reached():
                logger.warning('⏰ 操作超时，终止流程')
                return False

            # ------ 步骤1: 截图识别结界卡 ------#
            self.screenshot()
            cards = self.order_targets.find_everyone(self.device.image)

            # 处理无卡情况
            if not cards:
                miss_count += 1
                logger.info(f'第{swipe_count}次滑动 | 未检测到结界卡' if swipe_count > 0 else '初始界面 | 未检测到结界卡')
                # 连续无卡超过阈值则终止
                if miss_count > CONSEC_MISS:
                    logger.warning(f'⚠️ 连续{miss_count}次 | 未检测到结界卡, 终止流程')
                    return False
                # 执行滑动操作
                self.perform_swipe_action()
                continue

            miss_count = 0  # 重置无卡计数器

            # ------ 步骤2: 处理识别到的结界卡 ------
            cards_list = [target for target, _, _ in cards]
            logger.info((f'第{swipe_count}次滑动' if swipe_count > 0 else '初始界面') + f' | 检测到结界卡：{cards_list}')

            # 遍历所有结界卡（已按位置排序）
            for _, _, area in cards:
                # 选中目标卡片后再读取详情，已选中时无需重复点击
                if not self._ensure_card_selected(area):
                    continue

                # 解析结界卡类型和数值
                card_type, card_value = self.check_card_num()

                # 跳过无效结界卡（类型未知或数值异常）
                if card_type == 'unknown' or card_value <= 0 or card_type not in RESOURCE_CONFIG:
                    logger.info(f'⏭️ 跳过无效卡: {card_type}@{card_value}')
                    continue

                # 太鼓卡达到配置最低值后立即确认寄养，低于门槛则跳过
                if card_type == '太鼓' and self._meets_min_value(
                        card_value, self._priority_search_min_for(card_type)):
                    logger.info(f'🎉 发现太鼓卡: {card_type}@{card_value}，返回成功')
                    self.save_image(push_flag=False, wait_time=0, content=f'🎉 发现太鼓卡（{card_type}: {card_value}）')
                    return True

            # ------ 步骤3: 滑动到下一屏 ------#
            self.perform_swipe_action()

        # ============== 终止处理 ==============#
        logger.warning(f'⚠️ 已达到最大滑动次数{MAX_SWIPES}, 未找到可用太鼓卡')
        return False
# ... existing code ...
    def _current_select_best(self, best_card_type=None, best_card_num=0, selected_card=False):
        """结界卡选择核心逻辑（集成版）
        功能：滑动屏幕寻找最优资源卡，支持两种模式：
        - 探索模式：记录当前遇到的最佳结界卡数值
        - 确认模式：根据给定条件选择指定类型结界卡

        :param best_card_type: 目标卡类型('太鼓'/'斗鱼')
        :param best_card_num:  要求的最低数值
        :param selected_card:  是否处于确认选择模式
        :return: 找到符合条件返回True，否则None
        """
        # ============== 配置常量 ==============#
        RESOURCE_CONFIG = {
            '斗鱼': {'max': 151, 'record_attr': 'ap_max_num'},
            '太鼓': {'max': 76, 'record_attr': 'jade_max_num'}
        }
        MAX_SWIPES = 50  # 最大滑动次数
        CONSEC_MISS = 8  # 允许连续无卡次数
        TIMEOUT = 240  # 操作超时(秒)

        # ============== 初始化阶段 ==============#
        logger.info(f'启动{"探索模式" if not selected_card else f"确认模式 | 目标: {best_card_type} @ {best_card_num}"}')
        timer = Timer(TIMEOUT).start()
        miss_count = 0  # 连续无卡计数器

        # ============== 主滑动循环 ==============#
        for swipe_count in range(MAX_SWIPES + 1):
            # 超时检测
            if timer.reached():
                logger.warning('⏰ 操作超时，终止流程')
                return None

            # ------ 步骤1: 截图识别结界卡 ------#
            self.screenshot()
            cards = self.order_targets.find_everyone(self.device.image)

            # 处理无卡情况
            if not cards:
                miss_count += 1
                logger.info(f'第{swipe_count}次滑动 | 未检测到结界卡' if swipe_count > 0 else '初始界面 | 未检测到结界卡')
                # 连续无卡超过阈值则终止
                if miss_count > CONSEC_MISS:
                    logger.warning(f'⚠️ 连续{miss_count}次 | 未检测到结界卡, 终止流程')
                    return None
                # 执行滑动操作
                self.perform_swipe_action()
                continue

            miss_count = 0  # 重置无卡计数器

            # ------ 步骤2: 处理识别到的结界卡 ------
            cards_list = [target for target, _, _ in cards]
            logger.info((f'第{swipe_count}次滑动' if swipe_count > 0 else '初始界面') + f' | 检测到结界卡：{cards_list}')

            # 遍历所有结界卡（已按位置排序）
            for _, _, area in cards:
                # 选中目标卡片后再读取详情，已选中时无需重复点击
                if not self._ensure_card_selected(area):
                    continue

                # 解析结界卡类型和数值
                card_type, card_value = self.check_card_num()

                # 跳过无效结界卡（类型未知或数值异常）
                if card_type == 'unknown' or card_value <= 0 or card_type not in RESOURCE_CONFIG:
                    logger.info(f'⏭️ 跳过无效卡: {card_type}@{card_value}')
                    continue

                # ====== 模式分支处理 ======#
                current_max = RESOURCE_CONFIG[card_type]['max']
                record_attr = RESOURCE_CONFIG[card_type]['record_attr']
                current_record = getattr(self, record_attr, 0)
                logger.info(f'🔍 识别卡片: {card_type} | 当前值: {card_value}, 最优值: {current_record}')

                # 更新最佳记录
                if card_value > current_record:
                    logger.info(f'📈 更新记录: {card_type} | {current_record} → {card_value}')
                    setattr(self, record_attr, card_value)

                if selected_card:  # 确认选择模式
                    # 第二轮确认选择当前最佳值，不再强制达到最低值门槛
                    if (card_type == best_card_type) and (card_value >= best_card_num):
                        logger.info(f'🎉 确认蹭卡: {card_type} | 当前值: {card_value} ≥ 目标值: {best_card_num}')
                        self.save_image(push_flag=False, wait_time=0, content=f'🎉 确认蹭卡（{card_type}: {card_value}）')
                        return True
                else:  # 探索记录模式
                    # 达到配置最低值的卡直接寄养，不用继续翻找
                    min_value = self._priority_search_min_for(card_type)
                    if card_value >= current_max or (min_value > 0 and card_value >= min_value):
                        message = f'🎉 完美蹭卡 | {card_type}: {card_value}'
                        logger.info(message)
                        self.save_image(push_flag=False, wait_time=0, content=message)
                        return True

            # ------ 步骤3: 滑动到下一屏 ------#
            self.perform_swipe_action()

        # ============== 终止处理 ==============#
        logger.warning(f'⚠️ 已达到最大滑动次数{MAX_SWIPES}, 终止流程')
        return None

    def perform_swipe_action(self):
        """统一滑动操作"""
        # 缩短手势时长并保留约半行重叠，兼顾速度与列表覆盖完整性
        duration = 1.0
        safe_pos_x = random.randint(340, 600)
        safe_pos_y = random.randint(500, 565)
        p1 = (safe_pos_x, safe_pos_y)
        p2 = (safe_pos_x, safe_pos_y - 360)
        logger.info('Swipe %s -> %s, %sS ' % (point2str(*p1), point2str(*p2), duration))
        self.device.swipe_adb(p1, p2, duration=duration)

        # self.swipe(self.S_U_UP, duration=1, wait_up_time=1)
        self.device.click_record_clear()
        time.sleep(2)

    def check_card_num(self) -> tuple[str, int]:
        """优化版数值提取方法，返回结界卡类型及对应数值"""
        self.screenshot()
        # OCR识别
        raw_text = self.O_CARD_NUM.ocr(self.device.image)
        # logger.info(f'OCR原始结果: {raw_text}')

        # 判断结界卡类型
        if any(c in raw_text for c in ['体', 'カ', '力']):
            card_type = '斗鱼'
        elif any(c in raw_text for c in ['勾', '玉']):
            card_type = '太鼓'
        else:
            logger.warning(f'结界卡类型识别失败，原始内容: {raw_text}')
            # self.push_notify(content=f'结界卡类型识别失败: {raw_text}')
            return 'unknown', 0  # 未知类型返回0

        # 提取纯数字部分（兼容带+号的情况，如+100）
        cleaned = re.sub(r'[^\d+]', '', raw_text)  # 保留数字和加号
        match = re.search(r'\d+', cleaned)  # 匹配连续数字

        try:
            value = int(match.group()) if match else 0
        except ValueError:
            logger.warning(f'数值转换异常，清理后文本: {cleaned}')
            value = 0

        if value <= 0:
            self.push_notify(content=f'数值异常: {raw_text} -> 解析值: {value}')
            return card_type, 0

        # logger.info(f'识别成功: 卡类型: {card_type}, 数值: {value}')
        return card_type, value

    def back_guild(self):
        """
        回到寮的界面
        :return:
        """
        while 1:
            self.screenshot()

            if self.appear(self.I_GUILD_INFO):
                break
            if self.appear(self.I_GUILD_REALM):
                break
            if self.appear_then_click(self.I_PLANT_TREE_CLOSE):
                continue

            if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                continue
            if self.appear_then_click(self.I_UI_BACK_BLUE, interval=1):
                continue
            if self.appear_then_click(self.I_UI_BACK_YELLOW, interval=1):
                continue

    def back_realm(self):
        # 回到寮结界
        while 1:
            self.screenshot()
            if self.appear(self.I_REALM_SHIN):
                break
            if self.appear_multi_scale(self.I_SHI_DEFENSE):
                break
            if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                continue
            if self.appear_then_click(self.I_UI_BACK_BLUE, interval=1):
                continue


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
