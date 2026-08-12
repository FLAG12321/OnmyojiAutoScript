# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from time import sleep
from datetime import datetime, timedelta
import cv2
import numpy as np
import random
from typing import Any
from cached_property import cached_property

from module.atom.click import RuleClick
from module.atom.ocr import RuleOcr
from module.base.protect import random_sleep
from module.base.timer import Timer
from module.exception import TaskEnd
from module.logger import logger

from tasks.base_task import BaseTask
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.ActivityShikigami.assets import ActivityShikigamiAssets
from tasks.ActivityShikigami.config import SwitchSoulConfig, GeneralBattleConfig, ActivityShikigami
from tasks.Component.BaseActivity.base_activity import BaseActivity
from tasks.Component.BaseActivity.config_activity import GeneralClimb
from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_shikigami_records
from tasks.ActivityShikigami.pass_monopoly import PassMonopolyMixin
from tasks.ActivityShikigami.season_boss.mixin import SeasonBossMixin
import tasks.Component.GeneralBattle.config_general_battle
import tasks.ActivityShikigami.page as game


def _prepare_image_for_ocr(image: np.ndarray, asset: RuleOcr) -> np.ndarray:
    image_copy = image.copy()
    x, y, w, h = asset.roi
    roi_to_process = image_copy[y:y + h, x:x + w]
    if len(roi_to_process.shape) == 3:
        gray_image = cv2.cvtColor(roi_to_process, cv2.COLOR_BGR2GRAY)
    else:
        gray_image = roi_to_process
    # 自适应二值化
    _, binary_norm = cv2.threshold(gray_image, 127, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    _, binary_inv = cv2.threshold(gray_image, 127, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    if cv2.countNonZero(binary_norm) < cv2.countNonZero(binary_inv):
        binary_correct = binary_norm
    else:
        binary_correct = binary_inv
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 1))
    dilated_image = cv2.dilate(binary_correct, kernel, iterations=1)
    # 找轮廓
    contours, _ = cv2.findContours(dilated_image, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    processed_roi_content = None
    if contours:
        all_points = np.concatenate(contours, axis=0)
        bx, by, bw, bh = cv2.boundingRect(all_points)
        processed_roi_content = binary_correct[by:by + bh, bx:bx + bw]
    centered_roi = np.full((h, w), 255, dtype=np.uint8)  # 255代表白色
    if processed_roi_content is not None:
        content_h, content_w = processed_roi_content.shape
        if content_h <= h and content_w <= w:
            # 计算居中粘贴的位置，放到中间
            start_y = (h - content_h) // 2
            start_x = (w - content_w) // 2
            paste_area = centered_roi[start_y:start_y + content_h, start_x:start_x + content_w]
            paste_area[processed_roi_content == 255] = 0
        else:
            logger.warning(f"Content for asset '{asset.name}' is larger than ROI. Skipping centering.")
            # 内容过大，直接使用原始二值图的反转作为结果
            centered_roi = cv2.bitwise_not(binary_correct)
    else:
        logger.warning(f"No content found in ROI for asset: {asset.name}. ROI will be blank.")
    processed_roi_bgr = cv2.cvtColor(centered_roi, cv2.COLOR_GRAY2BGR)
    image_copy[y:y + h, x:x + w] = processed_roi_bgr
    return image_copy


class LimitTimeOut(Exception):
    pass


class LimitCountOut(Exception):
    pass

class StateMachine(BaseTask):
    run_idx: int = 0  # 当前爬塔类型
    _count_map = None

    @cached_property
    def conf(self) -> GeneralClimb:
        return self.config.model.activity_shikigami

    @property
    def climb_type(self) -> str:
        if self.run_idx >= len(self.conf.general_climb.run_sequence_v):
            return self.conf.general_climb.run_sequence_v[-1]
        return self.conf.general_climb.run_sequence_v[self.run_idx]

    @property
    def count_map(self) -> dict[str, int]:
        """
        :return: key: climb type, value: run count
        """
        if not getattr(self, "_count_map", None):
            self._count_map = {climb_type: 0 for climb_type in self.conf.general_climb.run_sequence_v}
        return self._count_map

    # ----------------------------------------------------
    def put_status(self):
        """
        更新全局状态
        """

        def get_count(self) -> int:
            return self.count_map[self.climb_type]

        def get_limit(self) -> int:
            limit = getattr(self.conf.general_climb, f'{self.climb_type}_limit', 0)
            return 0 if not limit else limit

        # 超过运行时间
        if self.limit_time is not None and datetime.now() - self.start_time >= self.limit_time:
            logger.info(f"Climb type {self.climb_type} time out")
            raise LimitTimeOut
        # 次数达到限制
        if get_count(self) >= get_limit(self):
            logger.info(f"Climb type {self.climb_type} count limit reached")
            raise LimitCountOut

    def switch_next(self):
        """
        切换下一种爬塔类型
        :return: True 切换成功 or False
        """
        self.run_idx += 1
        if self.run_idx >= len(self.conf.general_climb.run_sequence_v):
            logger.info('All climbing activities have been completed')
            return False
        # 切换爬塔类型了, 恢复所有状态
        self.current_count = 0
        logger.hr(f'Climb switch to {self.climb_type}', 2)
        return True

class ScriptTask(StateMachine, GameUi, BaseActivity, SwitchSoul, PassMonopolyMixin, SeasonBossMixin, ActivityShikigamiAssets):
    """
    更新前请先看 ./README.md
    """
    
    def __init__(self, config, device):
        super().__init__(config, device)
        # check_tickets_enough 缓存: 避免每次循环都 OCR 剩余门票
        # 策略: pass/ap 剩余 >= 50 时 10 分钟 OCR 一次; boss 剩余 >= 10 时 10 分钟 OCR 一次
        # 缓存期内每次调用预估递减 1 (一场战斗 1 张门票)
        # 五倍消耗生效时改为递减 5, 且 OCR 间隔缩短为原来的五分之一
        self._ticket_cache: dict[str, int] = {}
        self._ticket_last_ocr: dict[str, datetime] = {}
        # 当前是否处于五倍消耗状态(游戏内五倍开关已打开且五倍券充足)
        # 仅在 pass/ap 战斗循环中维护, 供 check_tickets_enough 判断递减步长与 OCR 间隔
        self._x5_active: bool = False

    def run(self) -> None:
        self.limit_time: timedelta = self.conf.general_climb.limit_time_v
        #
        for climb_type in self.conf.general_climb.run_sequence_v:
            # 切换御魂(回到 page_main 后进入 page_shikigami_records 再切回 page_main)
            self.ui_get_current_page()
            self.switch_soul()
            # 进入到活动的主页面，不是具体的战斗页面
            self.ui_goto(game.page_climb_act)
            try:
                method_func = getattr(self, f'_run_{climb_type}')
                method_func()
            except LimitCountOut as e:
                self.ui_click(self.I_UI_BACK_YELLOW, stop=self.I_TO_BATTLE_MAIN, interval=1)
            except LimitTimeOut as e:
                break
            finally:
                # 切换下一个爬塔类型
                self.switch_next()

        # 返回庭院
        logger.hr("Exit Shikigami", 2)
        self.ui_get_current_page(False)
        self.ui_goto(game.page_main)
        if self.conf.general_climb.active_souls_clean:
            self.set_next_run(task='SoulsTidy', success=False, finish=False, target=datetime.now())
        self.set_next_run(task="ActivityShikigami", success=True)
        raise TaskEnd('ActivityShikigami')

    def _run_pass(self):
        """
            更新前请先看 ./README.md
        """
        logger.hr(f'Start run climb type PASS', 1)
        self.ui_click(self.I_TO_BATTLE_MAIN, stop=self.I_CHECK_BATTLE_MAIN, interval=1)
        self.switch_climb_mode_in_game('pass')
        # 进入 pass 模式, 重置五倍状态, 由首次 check_tickets_enough 的 OCR 重新判断
        self._x5_active = False

        ocr_limit_timer = Timer(1).start()
        click_limit_timer = Timer(4).start()
        while 1:
            self.screenshot()
            self.put_status()
            # --------------------------------------------------------------
            if (self.appear_then_click(self.I_UI_CONFIRM, interval=0.5)
                    or self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=0.5)):
                continue
            if self.ui_reward_appear_click():
                continue
            if not ocr_limit_timer.reached():
                continue
            ocr_limit_timer.reset()
            if not self.ocr_appear(self.O_FIRE):
                continue
            #  --------------------------------------------------------------
            self.lock_team(self.conf.general_battle)
            if not self.check_tickets_enough():
                logger.warning(f'No tickets left, wait for next time')
                break
            if self.conf.general_climb.random_sleep:
                random_sleep(probability=0.2)
            if self.start_battle():
                continue

        self.ui_click(self.I_UI_BACK_YELLOW, stop=self.I_TO_BATTLE_MAIN, interval=1)

    def _run_ap(self):
        """
            更新前请先看 ./README.md
        """
        logger.hr(f'Start run climb type AP')
        self.ui_clicks([self.I_TO_BATTLE_MAIN],
                       stop=self.I_CHECK_BATTLE_MAIN, interval=1)
        self.switch_climb_mode_in_game('ap')
        # 进入 ap 模式, 重置五倍状态, 由首次 check_tickets_enough 的 OCR 重新判断
        self._x5_active = False

        ocr_limit_timer = Timer(1).start()
        while 1:
            self.screenshot()
            self.put_status()
            # --------------------------------------------------------------
            if not ocr_limit_timer.reached():
                continue
            ocr_limit_timer.reset()
            if not self.ocr_appear(self.O_FIRE):
                self.appear_then_click(self.I_CHECK_BATTLE_MAIN, interval=4)
                continue
            #  --------------------------------------------------------------
            self.lock_team(self.conf.general_battle)
            if not self.check_tickets_enough():
                logger.warning(f'No tickets left, wait for next time')
                break
            if self.conf.general_climb.random_sleep:
                random_sleep(probability=0.2)
            if self.start_battle():
                continue

        self.ui_click(self.I_UI_BACK_YELLOW, stop=self.I_TO_BATTLE_MAIN, interval=1)
    def _run_boss(self):
        def start_battle():
            click_times, max_times = 0, random.randint(2, 4)
            while 1:
                self.screenshot()
                if self.is_in_battle(False):
                    break
                if click_times >= max_times:
                    logger.warning(f'Climb {self.climb_type} cannot enter, maybe already end, try next')
                    return
                if (self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=1) or
                        self.appear_then_click(self.I_UI_CONFIRM, interval=1) ):
                    continue
                if self.ocr_appear_click(self.O_FIRE2, interval=2):
                    click_times += 1
                    logger.info(f'Try click fire, remain times[{max_times - click_times}]')
                    continue
            # 运行战斗
            self.run_general_battle(config=self.get_general_battle_conf())
        """
        更新前请先看 ./README.md
        """
        logger.hr(f'Start run climb type BOSS')
        #logger.hr(f'Start run climb type AP')
        self.ui_clicks([self.I_TO_BATTLE_BOSS],
                       stop=self.I_CHECK_BATTLE_BOSS, interval=1)
        logger.hr(f'Start run climb type BOSS')
        ocr_limit_timer = Timer(1).start()
        while 1:
            self.screenshot()
            #self.put_status()
            # --------------------------------------------------------------
            if not ocr_limit_timer.reached():
                continue
            ocr_limit_timer.reset()
            if not self.ocr_appear(self.O_FIRE2):
                self.appear_then_click(self.I_CHECK_BATTLE_MAIN, interval=4)
                continue
            #  --------------------------------------------------------------
            #self.lock_team(self.conf.general_battle)
            if not self.check_tickets_enough():
                logger.warning(f'No tickets left, wait for next time')
                break
            if self.conf.general_climb.random_sleep:
                random_sleep(probability=0.2)
            if start_battle():
                continue

    def _run_ap20(self):
        """
        AP20爬塔模式：7入口遍历，每入口不限次数battle循环
        """
        logger.hr('Start run climb type AP20', 1)

        self.ui_click(self.I_TO_AP20, stop=self.I_CHECK_AP20, interval=1)

        for entry_idx in range(7):
            logger.hr(f'AP20 entry [{entry_idx}/6]', 2)

            entry_img = getattr(self, f'I_AP20_ENTRY_{entry_idx}')
            swipe_count = 0
            max_swipe = 20
            while not self.appear(entry_img):
                if swipe_count >= max_swipe:
                    logger.warning(f'Cannot find AP20 entry {entry_idx}, skip')
                    break
                logger.info(f'Swiping left to find entry {entry_idx} (attempt {swipe_count + 1})')
                self.swipe(self.S_AP20_SWIPE_LEFT, interval=2)
                sleep(2)
                self.screenshot()
                swipe_count += 1
            if swipe_count >= max_swipe:
                continue

            self.appear_then_click(entry_img, interval=2)
            self.ui_click(self.I_TO_AP20_BOSS, stop=self.I_CHECK_AP20_BOSS, interval=1)

            ocr_limit_timer = Timer(1).start()
            fire_retry = 0
            max_fire_retry = 3
            while True:
                self.screenshot()

                if not ocr_limit_timer.reached():
                    continue
                ocr_limit_timer.reset()

                if not self.ocr_appear(self.O_FIRE_AP20):
                    fire_retry += 1
                    if fire_retry >= max_fire_retry:
                        logger.info(f'AP20 entry {entry_idx} complete (no fire detected)')
                        break
                    logger.info(f'Fire not detected, retry {fire_retry}/{max_fire_retry}')
                    self.appear_then_click(self.I_CHECK_AP20_BOSS, interval=2)
                    continue
                fire_retry = 0

                # AP20 每个 entry 独立 20 次, ocr_digit_counter 返回 (current, remain, total)
                # 20/20 表示已用完, remain==0 时切换到下一个 entry
                _, remain, total = self.O_REMAIN_AP20.ocr_digit_counter(self.device.image)
                logger.info(f'AP20 entry {entry_idx} remain: {remain}/{total}')
                if total > 0 and remain <= 0:
                    logger.info(f'AP20 entry {entry_idx} exhausted, back to battle main and re-enter for next entry')
                    self.ui_click(self.I_UI_BACK_YELLOW, stop=self.I_TO_BATTLE_MAIN, interval=3)
                    self.ui_click(self.I_TO_AP20, stop=self.I_CHECK_AP20, interval=3)
                    break

                if self.conf.general_climb.random_sleep:
                    random_sleep(probability=0.2)
                self._start_battle_ap20()

        self.ui_click(self.I_UI_BACK_YELLOW, stop=self.I_TO_BATTLE_MAIN, interval=1)
        logger.info('AP20 all entries completed')

    def _start_battle_ap20(self):
        click_times, max_times = 0, random.randint(2, 4)
        while True:
            self.screenshot()
            if self.is_in_battle(False):
                break
            if click_times >= max_times:
                logger.warning(f'AP20 cannot enter battle, maybe already end')
                return
            if (self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=1) or
                    self.appear_then_click(self.I_UI_CONFIRM, interval=1)):
                continue
            if self.ocr_appear_click(self.O_FIRE_AP20, interval=2):
                click_times += 1
                logger.info(f'Try click fire AP20, remain times[{max_times - click_times}]')
                continue
        self.run_general_battle(config=self.get_general_battle_conf())

    def start_battle(self):
        click_times, max_times = 0, random.randint(2, 4)
        while 1:
            self.screenshot()
            if self.is_in_battle(False):
                break
            if click_times >= max_times:
                logger.warning(f'Climb {self.climb_type} cannot enter, maybe already end, try next')
                return
            if (self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=1) or
                    self.appear_then_click(self.I_UI_CONFIRM, interval=1) ):
                continue
            if self.ocr_appear_click(self.O_FIRE, interval=2):
                click_times += 1
                logger.info(f'Try click fire, remain times[{max_times - click_times}]')
                continue
        # 运行战斗
        self.run_general_battle(config=self.get_general_battle_conf())

    def battle_wait(self, random_click_swipt_enable: bool) -> bool:
        # 通用战斗结束判断
        self.device.stuck_record_add("BATTLE_STATUS_S")
        self.device.click_record_clear()
        logger.info(f"Start {self.climb_type} battle process")
        self.count_map[self.climb_type] = self.current_count
        for btn in (self.C_RANDOM_LEFT, self.C_RANDOM_RIGHT, self.C_RANDOM_TOP, self.C_RANDOM_BOTTOM):
            btn.name = "BATTLE_RANDOM"
        fire_ocr = {'boss': self.O_FIRE2, 'ap20': self.O_FIRE_AP20}.get(self.climb_type, self.O_FIRE)
        ok_cnt, max_retry = 0, 5
        while 1:
            sleep(random.uniform(0.5, 1.5))
            self.screenshot()
            # 达到最大重试次数则直接交给上层处理
            if ok_cnt > max_retry:
                break
            # 识别到挑战说明已经退出战斗
            if ok_cnt > 0 and self.ocr_appear(fire_ocr):
                return True
            # 战斗失败
            if self.appear(self.I_FALSE):
                logger.warning("Battle failed")
                self.ui_click_until_smt_disappear(self.random_reward_click(click_now=False), self.I_FALSE, interval=1.5)
                return False
            # 战斗成功
            if self.appear_then_click(self.I_WIN, interval=2):
                continue
            #  出现 "魂" 和 紫蛇皮
            if self.appear(self.I_REWARD) or self.appear(self.I_REWARD_PURPLE_SNAKE_SKIN)or self.appear(self.I_PURPLE_SNAKE_SKIN):
                logger.info('Win battle')
                appear_reward_purple_snake_skin = self.appear(self.I_REWARD_PURPLE_SNAKE_SKIN) or self.appear(self.I_PURPLE_SNAKE_SKIN)
                appear_reward = self.appear(self.I_REWARD)
                if appear_reward:
                    self.click(self.I_REWARD, interval=0.9)
                    ok_cnt += 1
                    continue
                if appear_reward_purple_snake_skin:
                    reward_click = random.choice(
                        [self.C_RANDOM_TOP, self.C_RANDOM_BOTTOM])
                    self.click(reward_click, interval=1.8)
                    ok_cnt += 1
                    continue
            # 已经不在战斗中了, 且奖励也识别过了, 则随机点击
            # if ok_cnt > 0 and not self.is_in_battle(False):
            #     self.random_reward_click(exclude_click=[self.C_RANDOM_BOTTOM])
            #     ok_cnt += 1
            #     continue
            # 战斗中随机滑动
            if ok_cnt == 0 and random_click_swipt_enable:
                self.random_click_swipt()
        return True

    def activity_shikigami_battle_wait(self, random_click_swipt_enable: bool) -> bool:
        """
        专为活动式神战斗设计的等待方法，只在handle_fire_scene中使用
        :param random_click_swipt_enable: 是否启用随机点击和滑动防封
        :return: 战斗结果 (True表示胜利，False表示失败)
        """
        ## 通用战斗结束判断
        self.device.stuck_record_add("BATTLE_STATUS_S")
        self.device.click_record_clear()
        logger.info(f"Start {self.climb_type} battle process")
        self.count_map[self.climb_type] = self.current_count
        for btn in (self.C_RANDOM_LEFT, self.C_RANDOM_RIGHT, self.C_RANDOM_TOP, self.C_RANDOM_BOTTOM):
            btn.name = "BATTLE_RANDOM"
        # season_boss 战斗结束回到的是修行合训主页, 没有"挑战"字样, 用主页标题「修行合训」判定退出战斗
        fire_ocr = {'boss': self.O_FIRE2, 'ap20': self.O_FIRE_AP20,
                    'season_boss': self.O_SEASON_BOSS_CHECK_MAIN}.get(self.climb_type, self.O_FIRE)
        ok_cnt, max_retry = 0, 5
        while 1:
            sleep(random.uniform(0.5, 1.5))
            self.screenshot()
            # 达到最大重试次数则直接交给上层处理
            if ok_cnt > max_retry:
                break
            # 识别到挑战说明已经退出战斗
            if ok_cnt > 0 and self.ocr_appear(fire_ocr):
                return True
            # 战斗失败
            if self.appear(self.I_FALSE):
                logger.warning("Battle failed")
                self.ui_click_until_smt_disappear(self.random_reward_click(click_now=False), self.I_FALSE, interval=1.5)
                return False
            if self.ui_reward_appear_click():
                continue
            # 战斗成功
            if self.appear_then_click(self.I_WIN, interval=2):
                return True
            # 已经不在战斗中了, 且奖励也识别过了, 则随机点击
            # if ok_cnt > 0 and not self.is_in_battle(False):
            #     self.random_reward_click(exclude_click=[self.C_RANDOM_BOTTOM])
            #     ok_cnt += 1
            #     continue
            # 战斗中随机滑动
            if ok_cnt == 0 and random_click_swipt_enable:
                self.random_click_swipt()
        return True

    def switch_soul(self):
        conf = self.conf.switch_soul_config
        conf.validate_switch_soul()
        if not getattr(conf, f"enable_switch_{self.climb_type}", False):
            return
        self.ui_goto(page_shikigami_records)
        self.run_switch_soul(getattr(conf, f"{self.climb_type}_group_team"))
        self.ui_goto(page_main)

    def switch_climb_mode_in_game(self, mode: str = 'ap'):
        map_check = {
            'ap': self.I_CLIMB_MODE_AP,
            'pass': self.I_CLIMB_MODE_PASS,
        }
        logger.info(f'Switch climb mode to {mode}')
        self.ui_click(self.I_CLIMB_MODE_SWITCH, stop=map_check[mode], interval=1.9)

    # ---------------------------------------------------------------- 五倍消耗
    def read_x5_ticket(self) -> int:
        """
        读取当前五倍券剩余数量 (O_REMAIN_X5)
        注意: O_REMAIN_X5 资产虽标记为 DigitCounter, 但五倍券界面显示的是纯数字(如 50),
        没有 "X/Y" 分隔符, 用 ocr_digit_counter 会正则匹配失败返回 0(误判券耗尽)。
        因此改用 ocr_digit 直接读纯数字, 与 O_REMAIN_PASS/O_REMAIN_AP 的读法一致。
        :return: 五倍券剩余数量, 读取失败按 0 处理(视为无券)
        """
        self.screenshot()
        return self.O_REMAIN_X5.ocr_digit(self.device.image)

    def is_x5_on(self) -> bool:
        """
        判断游戏内五倍开关是否已开启
        I_ON_X5 存在 -> 已开启; I_OFF_X5 存在 -> 未开启
        """
        return self.appear(self.I_ON_X5)

    def switch_x5_in_game(self, on: bool):
        """
        将游戏内五倍开关切换到目标状态(点击 I_ON_X5/I_OFF_X5 同一按钮切换)
        :param on: True 打开五倍, False 关闭五倍
        """
        target_on = self.is_x5_on()
        if target_on == on:
            return
        logger.info(f'Switch x5 to {"ON" if on else "OFF"}')
        # 开关按钮位置固定, 点击后状态互斥翻转; 用目标态图标作为停止条件
        stop_img = self.I_ON_X5 if on else self.I_OFF_X5
        self.ui_click(self.I_OFF_X5 if on else self.I_ON_X5, stop=stop_img, interval=1.5)

    def update_x5_state(self, remain_ticket: int) -> bool:
        """
        根据配置、五倍券数量、当前门票剩余, 决定并同步游戏内五倍开关状态。
        规则(仅 pass/ap 调用):
        - 未开启五倍消耗配置 -> 保持关闭
        - 五倍券为 0 -> 关闭五倍(用一倍打完剩余门票)
        - 剩余门票 < 5(不足一次五倍) -> 关闭五倍(用一倍打完零头)
        - 否则 -> 打开五倍
        :param remain_ticket: 当前爬塔剩余门票数
        :return: 同步后是否处于五倍状态
        """
        if not self.conf.general_climb.five_times_enable:
            self._x5_active = False
            return False
        x5_ticket = self.read_x5_ticket()
        # 五倍券耗尽或门票零头不足 5, 关闭五倍改用一倍
        want_x5 = x5_ticket > 0 and remain_ticket >= 5
        if not want_x5:
            reason = 'x5 ticket exhausted' if x5_ticket <= 0 else f'remain {remain_ticket} < 5'
            logger.info(f'Disable x5: {reason}')
        self.switch_x5_in_game(want_x5)
        self._x5_active = want_x5
        return want_x5

    def lock_team(self, battle_conf: GeneralBattleConfig):
        """
        根据配置判断当前爬塔类型是否锁定阵容, 并执行锁定或解锁
        """
        enable_preset = getattr(battle_conf, f"enable_{self.climb_type}_preset", False)
        if not enable_preset:
            logger.info(f'Lock {self.climb_type} team')
            self.ui_click(self.I_UNLOCK, stop=self.I_LOCK, interval=1.5)
            return
        logger.info(f'Unlock {self.climb_type} team')
        self.ui_click(self.I_LOCK, stop=self.I_UNLOCK, interval=1.5)

    def check_tickets_enough(self) -> bool:
        """
        判断当前爬塔门票是否足够
        :return: True 可以运行 or False

        缓存策略 (ap100 除外, 仍每次都OCR):
        - pass/ap: 剩余 < 50 时每次都 OCR; >= 50 时 10 分钟 OCR 一次
        - boss:    剩余 < 10 时每次都 OCR; >= 10 时 10 分钟 OCR 一次
        缓存命中时预估递减 1 (一场战斗 1 张门票)

        五倍消耗 (仅 pass/ap):
        - 一场战斗消耗 5 张门票, 故缓存命中时预估递减 5
        - OCR 间隔缩短为五分之一 (10 分钟 -> 2 分钟), 因门票消耗速度是原来的 5 倍
        - 每次真实 OCR 后按剩余门票同步游戏内五倍开关(update_x5_state)
        - 缓存期内一旦预估剩余不足 5, 强制下一轮重新 OCR 以便关闭五倍打零头
        """
        climb = self.climb_type
        # 一场战斗消耗的门票数: 五倍生效为 5, 否则为 1
        step = 5 if self._x5_active else 1
        # 五倍生效时门票消耗快 5 倍, OCR 间隔相应缩短为原来的五分之一
        ocr_interval = timedelta(minutes=2) if self._x5_active else timedelta(minutes=10)

        # 缓存判断 (ap100 不参与缓存)
        if climb != 'ap100':
            threshold = {'pass': 50, 'ap': 50, 'boss': 10}.get(climb, 0)
            cached = self._ticket_cache.get(climb)
            last_ocr = self._ticket_last_ocr.get(climb)
            now = datetime.now()
            need_ocr = (
                cached is None
                or cached < threshold
                or last_ocr is None
                or (now - last_ocr) >= ocr_interval
                # 五倍生效且预估剩余不足一次五倍, 需重新 OCR 以关闭五倍打零头
                or (self._x5_active and cached < 5)
            )
            if not need_ocr:
                self._ticket_cache[climb] = max(0, cached - step)
                elapsed = int((now - last_ocr).total_seconds())
                logger.info(
                    f'Skip OCR for {climb} tickets, cached={self._ticket_cache[climb]} '
                    f'(last OCR {elapsed}s ago, step={step})'
                )
                return self._ticket_cache[climb] > 0

        logger.hr(f'Check {climb} tickets')

        if climb == 'boss':
            if not self.wait_until_appear(self.O_FIRE2, wait_time=3):
                logger.warning(f'Detect fire fail, try reidentify')
                return False
        else:
            if not self.wait_until_appear(self.O_FIRE, wait_time=3):
                logger.warning(f'Detect fire fail, try reidentify')
                return False
        self.screenshot()
        remain_times = 0
        if climb == 'pass':
            remain_times = self.O_REMAIN_PASS.ocr_digit(
                _prepare_image_for_ocr(self.device.image, asset=self.O_REMAIN_PASS))
        if climb == 'ap':
            remain_times = self.O_REMAIN_AP.ocr_digit(
                _prepare_image_for_ocr(self.device.image, asset=self.O_REMAIN_AP))
        if climb == 'boss':
            _, remain_times, _ = self.O_REMAIN_BOSS.ocr_digit_counter(self.device.image)
        if climb == 'ap100':
            remain_times = self.O_REMAIN_AP100.ocr_digit(
                _prepare_image_for_ocr(self.device.image, asset=self.O_REMAIN_AP100))

        if climb != 'ap100':
            self._ticket_cache[climb] = remain_times
            self._ticket_last_ocr[climb] = datetime.now()

        # pass/ap 且门票充足时, 按真实剩余门票同步游戏内五倍开关状态
        if climb in ('pass', 'ap') and remain_times > 0:
            self.update_x5_state(remain_times)

        return remain_times > 0

    def get_general_battle_conf(self) -> tasks.Component.GeneralBattle.config_general_battle.GeneralBattleConfig:
        from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig as gbc
        self.conf.validate_switch_preset()
        enable_preset = getattr(self.conf.general_battle, f'enable_{self.climb_type}_preset', False)
        group, team = getattr(self.conf.switch_soul_config, f'{self.climb_type}_group_team').split(',')
        return gbc(lock_team_enable=not enable_preset,
                   preset_enable=enable_preset,
                   preset_group=group if enable_preset else 1,
                   preset_team=team if enable_preset else 1,
                   random_click_swipt_enable=getattr(self.conf.general_battle, f'enable_{self.climb_type}_anti_detect',
                                                     False), )

    def random_reward_click(self, exclude_click: list = None, click_now: bool = True) -> RuleClick:
        """
        随机点击
        :param exclude_click: 排除的点击位置
        :param click_now: 是否立即点击
        :return: 随机的点击位置
        """
        options = [self.C_RANDOM_TOP, self.C_RANDOM_BOTTOM]
        if exclude_click:
            options = [option for option in options if option not in exclude_click]
        target = random.choice(options)
        if click_now:
            self.click(target, interval=1.8)
        return target
if __name__ == "__main__":
    from module.config.config import Config
    from module.device.device import Device
    c = Config('OAS1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.screenshot()
    t.run()