# This Python file uses the following encoding: utf-8
import time
from module.base.timer import Timer
from module.exception import TaskEnd
from module.logger import logger
from tasks.ActivitySignIn.assets import ActivitySignInAssets
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main


class ScriptTask(GameUi, ActivitySignInAssets):
    """单账号活动签到：进入式神奖励主页，领取最左侧可领取的奖励。

    从旧 MultiAccountSignIn 剥离而来，只保留单账号签到流程；
    多账号轮转由 MultiTasks 负责，本任务不感知账号切换。
    """

    def _click_leftmost_reward(self) -> bool:
        """识别所有可领取奖励，并点击横坐标最小的一个。"""
        matches = self.I_GET_SHI.match_all_any(self.device.image)
        if not matches:
            logger.warning('[ActivitySignIn] 未识别到可领取奖励')
            return False

        _, x, y, width, height = min(matches, key=lambda match: match[1])
        self.device.click(
            x=x + width // 2,
            y=y + height // 2,
            control_name='I_GET_SHI_LEFTMOST',
        )
        return True

    def _wait_reward_result(self, timeout: float = 8) -> bool:
        """等待领取结果；识别到成功页面时返回 True。"""
        timeout_timer = Timer(timeout).start()
        while not timeout_timer.reached():
            self.screenshot()
            if self.appear(self.I_GET_SHI_SUCCESS):
                return True
            # 转场期间可能出现通用确认或返回按钮，优先关闭后继续检测。
            if self.appear(self.I_GET_SHI_OVER) and self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                return False
        return False

    def _return_to_reward_page(self, timeout: float = 15) -> bool:
        """关闭领取结果页及附加弹窗，直到重新回到奖励主页。"""
        timeout_timer = Timer(timeout).start()
        wait_timer = Timer(3).start()
        while not timeout_timer.reached():
            self.screenshot()
            if self.appear(self.I_PAGE_GET_SHI):
                return True
            if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                timeout_timer.reset()
                wait_timer.reset()
                continue
            if self.appear_then_click(self.I_GET_SHI_SUCCESS, interval=1):
                timeout_timer.reset()
                wait_timer.reset()
                continue
            if self.appear_then_click(self.I_ANIMATION_JUMP, interval=1):
                timeout_timer.reset()
                wait_timer.reset()
                continue
            if self.appear_then_click(self.I_DLC_EXIT, interval=1):
                timeout_timer.reset()
                wait_timer.reset()
                continue
            if wait_timer.reached():
                self.click(self.I_ANIMATION_JUMP, interval=1)
                wait_timer.reset()
                continue
        logger.warning('[ActivitySignIn] 返回奖励主页超时')
        return False

    def _goto_reward_page(self, timeout: float = 30) -> bool:
        """从庭院进入式神奖励主页。"""
        timeout_timer = Timer(timeout).start()
        while not timeout_timer.reached():
            self.screenshot()
            if self.appear(self.I_PAGE_GET_SHI):
                return True
            if self.appear(self.I_TO_PAGE_SHI):
                self.click(self.I_TO_PAGE_SHI, interval=1.5)
                continue
            if self.appear_then_click(self.I_CHANGE_ITEM, interval=1, repeat_exempt=True):
                continue
        logger.warning('[ActivitySignIn] 进入式神奖励主页超时')
        return False
    def _goto_reward_page_a(self, timeout: float = 30) -> bool:
            """从庭院进入式神奖励主页。"""
            timeout_timer = Timer(timeout).start()
            while not timeout_timer.reached():
                self.screenshot()
                if self.appear(self.I_A_MAIN):
                    return True
                if self.appear(self.I_A_FINISH):
                    return False
                if self.appear(self.I_A_SKIP):
                    self.click(self.I_A_SKIP, interval=1.5)
                    continue
                if self.appear(self.I_A_TO_MAIN):
                    self.click(self.I_A_TO_MAIN, interval=1.5)
                    continue
                if self.appear_then_click(self.I_CHANGE_ITEM, interval=1, repeat_exempt=True):
                    continue
            logger.warning('[ActivitySignIn] 进入式神奖励主页超时')
            return False
    def _goto_reward_page_b(self, timeout: float = 30) -> bool:
            """从庭院进入式神奖励主页。"""
            timeout_timer = Timer(timeout).start()
            while not timeout_timer.reached():
                self.screenshot()
                if self.appear(self.I_B_MAIN):
                    return True
                if self.appear(self.I_B_FINISH):
                    return False
                if self.appear(self.I_B_TO_MAIN):
                    self.click(self.I_B_TO_MAIN, interval=1.5)
                    continue
                if self.appear(self.I_B_TO_MAIN_2):
                    self.click(self.I_B_TO_MAIN_2, interval=1.5)
                    continue
                if self.appear(self.I_B_SELECT_POOL_2) and self.appear(self.I_B_ENSURE):
                    self.click(self.I_B_ENSURE, interval=1.5)
                    continue
                if self.appear(self.I_B_SELECT_POOL):
                    self.click(self.C_B_SELECT_POOL, interval=1.5)
                    continue
                if self.appear_then_click(self.I_CHANGE_ITEM, interval=1, repeat_exempt=True):
                    continue
            logger.warning('[ActivitySignIn] 进入式神奖励主页超时')
            return False

    def _run_sign_in_a(self) -> bool:
            """
            执行当前账号的签到。
    
            @return: True 表示已处理完毕（领取成功，或本就无可领/已领），
                     False 表示导航失败等可重试问题
            """
            try:
                self.screenshot()
                if self.ui_get_current_page() != page_main and not self.ui_goto(page_main):
                    logger.warning('[ActivitySignIn] 无法返回庭院主页面，放弃本次签到')
                    return False
                if not self._goto_reward_page_a():
                    return False
                from tasks.Plotline.assets import PlotlineAssets
                start_time=time.time()
                exit_flag=False
                while time.time()-start_time<20:
                    self.screenshot()
                    if not self.appear(self.I_A_SKIP_3) and exit_flag==True:
                        break
                    if self.appear(self.I_A_FINISH):
                        return True
                    if self.appear(self.I_A_SKIP_2):
                        self.click(self.I_A_SKIP_2, interval=1.5)
                        start_time=time.time()
                        continue
                    if self.appear(self.I_A_SKIP_3):
                        self.click(self.I_A_SKIP_2, interval=1.5)
                        exit_flag=True
                        start_time=time.time()
                        continue
                    if self.appear(self.I_A_MAIN):
                        self.swipe(PlotlineAssets.S_SWIPE_SUMMON, interval=1.5)
                        start_time=time.time()
                        continue
                    if not self.appear(self.I_A_MAIN):
                        self.click(self.I_A_SKIP_2, interval=1.5)
                        continue
            finally:
                # 无论领取结果如何都返回主页面：MultiTasks 复用本任务时，
                # 下一账号的切号流程要求从稳定页面开始。
                self.ui_get_current_page(skip_first_screenshot=False)
                if not self.ui_goto(page_main, skip_first_screenshot=False):
                    logger.warning('[ActivitySignIn] 结束后返回主页面失败')

    def _run_sign_in_b(self) -> bool:
            """
            执行当前账号的签到。
    
            @return: True 表示已处理完毕（领取成功，或本就无可领/已领），
                        False 表示导航失败等可重试问题
            """
            from tasks.Plotline.assets import PlotlineAssets
            def _run_page_summon() -> bool:
                start_time=time.time()
                while time.time()-start_time<20:
                    self.screenshot()
                    if self.appear(self.I_B_MAIN) or  self.appear(self.I_B_ENSURE_2) or self.appear(self.I_B_SUMMON) :
                        break
                    if not self.appear(self.I_B_ENSURE_2) and self.appear(self.I_B_FINISH):
                        break
                    if self.appear_then_click(self.I_B_BACK_RED, interval=1.5):
                        start_time=time.time()
                        continue
                    # 十连召唤金按钮每出一抽结果重现一次，连点约 10 次是流程的正常
                    # 推进方式；repeat_exempt 豁免拟人化同一资源连点退避，否则第 6 次
                    # 起退避爬到 10/16s，十连会被拖成分钟级
                    if self.appear_then_click(self.I_B_SUMMON_GOLD, interval=1.5, repeat_exempt=True):
                        start_time=time.time()
                        continue
                    if self.appear(self.I_B_CANCEL):
                        self.click(self.I_B_CANCEL, interval=1.5)
                        start_time=time.time()
                        continue
                    if self.appear_then_click(self.I_B_SUMMON_CHIP_GET, interval=1.5):
                        start_time=time.time()
                        continue
                    if self.appear_then_click(self.I_B_SUMMON_FLAG,action=self.C_B_SUMMON_FLAG, interval=1.5):
                        start_time=time.time()
                        continue
                    if self.appear_then_click(self.I_B_SKIP, interval=1.5):
                        start_time=time.time()
                        continue
                    if self.appear(self.I_B_PAGE_SUMMON):
                        self.swipe(PlotlineAssets.S_SWIPE_SUMMON, interval=1.5)
                        start_time=time.time()
                        continue


            try:
                self.screenshot()
                if self.ui_get_current_page() != page_main and not self.ui_goto(page_main):
                    logger.warning('[ActivitySignIn] 无法返回庭院主页面，放弃本次签到')
                    return False
                if not self._goto_reward_page_b():
                    return False
                start_time=time.time()
                exit_flag=False
                while time.time()-start_time<20:
                    self.screenshot()
                    if exit_flag==True and self.appear(self.I_B_MAIN) and not self.appear(self.I_B_ENSURE_2):
                        break
                    if self.appear(self.I_B_BACK_RED):
                        self.click(self.I_B_BACK_RED, interval=1.5)
                        start_time=time.time()
                        continue
                    if not self.appear(self.I_B_ENSURE_2) and self.appear(self.I_B_FINISH):
                        time.sleep(2)
                        self.screenshot()
                        if self.appear(self.I_B_ENSURE_2):
                            self.click(self.I_B_ENSURE_2, interval=1.5)
                        if self.appear(self.I_B_FINISH) :
                            break
                        start_time=time.time()
                    if self.appear(self.I_B_PAGE_SUMMON):
                        _run_page_summon()
                        start_time=time.time()
                        continue
                    if self.appear(self.I_B_ENSURE_2):
                        time.sleep(2)
                        self.screenshot()
                        if self.appear(self.I_B_ENSURE_2):
                            self.click(self.I_B_ENSURE_2, interval=1.5)
                            exit_flag=True
                        start_time=time.time()
                        continue
                    if self.appear(self.I_B_SUMMON):
                        self.click(self.I_B_SUMMON, interval=1.5)
                        start_time=time.time()
                        continue
            finally:
                # 无论领取结果如何都返回主页面：MultiTasks 复用本任务时，
                # 下一账号的切号流程要求从稳定页面开始。
                self.ui_get_current_page(skip_first_screenshot=False)
                if not self.ui_goto(page_main, skip_first_screenshot=False):
                    logger.warning('[ActivitySignIn] 结束后返回主页面失败')
    def _run_sign_in(self) -> bool:
        """
        执行当前账号的签到。

        @return: True 表示已处理完毕（领取成功，或本就无可领/已领），
                 False 表示导航失败等可重试问题
        """
        try:
            self.screenshot()
            if self.ui_get_current_page() != page_main and not self.ui_goto(page_main):
                logger.warning('[ActivitySignIn] 无法返回庭院主页面，放弃本次签到')
                return False
            if not self._goto_reward_page():
                return False

            for attempt in range(1, 4):
                self.screenshot()
                if not self._click_leftmost_reward():
                    # 无可领奖励（通常为已领），视为已处理：不会重复领取
                    logger.info('[ActivitySignIn] 无可领奖励，视为已签到')
                    return True
                logger.info(f'[ActivitySignIn] 第 {attempt}/3 次点击最左侧奖励')
                if not self._wait_reward_result():
                    logger.warning(f'[ActivitySignIn] 第 {attempt}/3 次未进入领取成功页面')
                    continue
                logger.info('[ActivitySignIn] 奖励领取成功')
                self._return_to_reward_page()
                return True

            logger.info('[ActivitySignIn] 三次尝试后未领取成功，本次签到结束')
            return True
        finally:
            # 无论领取结果如何都返回主页面：MultiTasks 复用本任务时，
            # 下一账号的切号流程要求从稳定页面开始。
            self.ui_get_current_page(skip_first_screenshot=False)
            if not self.ui_goto(page_main, skip_first_screenshot=False):
                logger.warning('[ActivitySignIn] 结束后返回主页面失败')

    def run(self):
        self._run_sign_in_a()
        self._run_sign_in_b()
        self.set_next_run('ActivitySignIn', finish=True, success=True, server=False)
        raise TaskEnd('ActivitySignIn')


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas')
    device = Device(config)
    task = ScriptTask(config, device)
    task.run()
