# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time

import importlib
import sys
from pathlib import Path

from datetime import datetime
from time import sleep

import random
from collections import deque
from module.atom.click import RuleClick
from module.atom.gif import RuleGif
from module.atom.image import RuleImage
from module.atom.list import RuleList
from module.atom.ocr import RuleOcr
from module.base.decorator import run_once
from module.base.timer import Timer
from module.exception import (GameNotRunningError, GamePageUnknownError)
from module.logger import logger
from tasks.Component.GeneralBattle.assets import GeneralBattleAssets
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.page import Page, PageRegistry, page_login, page_main, random_click,page_mall,page_shikigami_records,page_onmyodo,page_friends,page_guild,page_team,page_collection,page_travel,page_daily
from tasks.Restart.assets import RestartAssets
from tasks.SixRealms.assets import SixRealmsAssets
from tasks.base_task import BaseTask
from tasks.ActivityShikigami.assets import ActivityShikigamiAssets


class GameUi(BaseTask, GameUiAssets):
    ui_current: Page = None
    ui_close = [GameUiAssets.I_BACK_MALL, GeneralBattleAssets.I_CONFIRM,
                BaseTask.I_UI_BACK_RED, BaseTask.I_UI_BACK_YELLOW,
                GameUiAssets.I_BACK_FRIENDS, GameUiAssets.I_BACK_DAILY,
                GameUiAssets.I_REALM_RAID_GOTO_EXPLORATION,
                GameUiAssets.I_SIX_GATES_GOTO_EXPLORATION, SixRealmsAssets.I_EXIT_SIXREALMS,
                ActivityShikigamiAssets.I_SKIP_BUTTON, ActivityShikigamiAssets.I_RED_EXIT, BaseTask.I_UI_BACK_BLUE]

    def __init__(self, config, device):
        super().__init__(config, device)
        # 初始化时动态导入所有 page 模块
        self._import_all_pages()

    @staticmethod
    def _import_all_pages():
        """动态加载 tasks/**/page.py"""
        base_dir = Path(__file__).resolve().parent.parent  # tasks 目录
        for task_dir in base_dir.iterdir():
            if not task_dir.is_dir():
                continue
            page_file = task_dir / "page.py"
            if not page_file.exists():
                continue
            module_name = f"tasks.{task_dir.name}.page"
            # 已加载过则直接复用缓存：重复 exec 会重新执行模块级 Page() 构造，
            # 把同名页面反复注册进 PageRegistry（每轮任务 +1），
            # 页面识别时注册表越遍历越慢（实测事故中单轮从 14s 拖到 53s）
            if module_name in sys.modules:
                continue
            spec = importlib.util.spec_from_file_location(module_name, page_file)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                # 先写入 sys.modules 再 exec，保证本次加载后后续调用能命中上面的缓存检查
                sys.modules[module_name] = module
                spec.loader.exec_module(module)

    @property
    def ui_pages(self) -> list[Page]:
        return PageRegistry.all()

    def ui_page_appear(self, page: Page, skip_first_screenshot: bool = True, interval: float = None):
        """
        判断当前页面是否为page
        """
        self.maybe_screenshot(skip_first_screenshot)
        if isinstance(page.check_button, list):
            for button in page.check_button:
                if self.appear(button, interval):
                    return True
            return False
        return self.appear(page.check_button, interval)

    def ui_wait_until_appear(self, page: Page, timeout: float = 5, interval: float = 0.5,
                             skip_first_screenshot: bool = True) -> bool:
        """
        等待页面出现
        :param page: 等待的页面
        :param timeout: 超时时间
        :param interval: 检查间隔时间
        :param skip_first_screenshot:
        :return: 页面出现返回True, 否则返回False
        """
        logger.info(f'Waiting for {page}')
        timeout_timer = Timer(timeout).start()
        while not timeout_timer.reached():
            if self.ui_page_appear(page, skip_first_screenshot, interval=interval):
                return True
            skip_first_screenshot = False
        return False

    def _costume_scroll_visible(self) -> bool:
        """卷轴两个状态（展开 / 收起）的图标至少认得出一个。

        两个都认不出，就说明卷轴皮肤与当前配置不符 —— 换皮后 theme_costume_model 替换的
        正是这两张图，它们会一起失效。两张图都走资产自带阈值（收起态 0.7），与 login.py
        的 courtyard_mark 保持一致。
        """
        return bool(self.appear(RestartAssets.I_LOGIN_SCROOLL_OPEN)
                    or self.appear(RestartAssets.I_LOGIN_SCROOLL_CLOSE))

    def ui_get_current_page(self, skip_first_screenshot=True, accept_login: bool = False) -> Page:
        """
        获取当前页面
        :param skip_first_screenshot:
        :param accept_login: True 时把登录页当作合法当前页返回（切号场景需要停在登录页操作），
                             False 时检测到登录页抛 GameNotRunningError 交由 Restart 重新登录
        :return:
        """
        #logger.info("UI get current page")

        @run_once
        def app_check():
            if not self.device.app_is_running():
                raise GameNotRunningError("Game not running")

        @run_once
        def minicap_check():
            if self.config.script.device.control_method == "uiautomator2":
                self.device.uninstall_minicap()

        @run_once
        def rotation_check():
            self.device.get_orientation()

        def desktop_login_popup_check():
            """MPay 存活时游戏画面上的 Page 标志全部无效，交给 Restart 处理。"""
            if not self.device.is_desktop:
                return
            if self.device.find_desktop_login_popup():
                logger.warning('Desktop MPay login popup present, client is not logged in')
                # 客户端已掉回未登录态，复位登录标记，让 app_is_running 的任务前置检查
                # 也能直接拦下，不必等下一个任务跑起来再发现
                self.device.desktop_mark_logged_out()
                raise GameNotRunningError('Desktop MPay login popup present')

        # MPay 是独立顶层窗口，不能通过游戏截图中的 Page 素材可靠识别。
        desktop_login_popup_check()
        timeout = Timer(10, count=20).start()
        while 1:
            self.maybe_screenshot(skip_first_screenshot)
            skip_first_screenshot = False
            # 弹窗可能在页面轮询期间出现，每轮截图后重新检查 HWND 是否存活。
            desktop_login_popup_check()
            # 如果10S还没有到底，那么就抛出异常
            if timeout.reached():
                break
            # Known pages
            redetect = False
            for page in self.ui_pages:
                if not page.check_button:
                    continue
                if self.ui_page_appear(page=page, interval=None):
                    logger.attr("UI", page.name)
                    if page == page_login:
                        # 默认语义：登录页=掉线信号，抛异常让调度器走 Restart 重新登录；
                        # 仅切号流程（accept_login=True）把登录页当合法当前页，用于选账号/角色
                        if accept_login:
                            self.ui_current = page
                            return page
                        raise GameNotRunningError("Login page detected")
                    # 在庭院却认不出卷轴本身：卷轴换皮后两张 I_LOGIN_SCROOLL_* 一起失效，
                    # 而 I_CHECK_MAIN 不随卷轴变、庭院照样判得出来。不在这里补一次探测，
                    # 卷轴皮肤与配置不符就永远发现不了 —— login 那边的探测要求 courtyard_mark
                    # 为假才跑，而庭院认得出时它根本不触发。
                    # 探到就重截一帧重判，让修正后的卷轴资产立刻生效（探针自带节流与锁）。
                    # 只挂 page_main：卷轴开合不参与页面识别，庭院一律判成 page_main，
                    # page_theme 只做导航中转、永远不会由这里返回。
                    if page == page_main and not self._costume_scroll_visible():
                        if self.try_detect_costume():
                            logger.info('Costume fixed by probe, re-check current page')
                            redetect = True
                            break
                    self.ui_current = page
                    return page
            if redetect:
                continue
            # ── 全页扫描全落空：可能是庭院皮肤与配置不符，I_CHECK_MAIN 匹配不上 ──
            # 探到哪套就套用哪套（并回写配置），page_main 立刻恢复可用。
            # 位置要在下面 _try_back_main_shortcut / try_close_unknown_page 之前——
            # 那两支会重置 timeout 并可能把画面导离庭院。也不能再往前挪到页面循环之前，
            # 那会让每一轮都白跑一次探测。
            detected = self.try_detect_costume_main()
            if detected is not None:
                logger.info(f'Courtyard recognised as {detected}, recover page_main')
                self.ui_current = page_main
                return page_main
            # Try to close unknown page: 优先尝试 I_BACK_MAIN 回主页
            if self._try_back_main_shortcut(skip_first_screenshot=False):
                timeout = Timer(10, count=20).start()
            elif self.try_close_unknown_page():
                timeout = Timer(10, count=20).start()
            else:
                # entirely unknown page, click safe random area
                #self.click(random_click(), interval=4)
                pass
            # wait to ui
            sleep(0.3)
            app_check()
            minicap_check()
            rotation_check()
        # Unknown page, need manual switching
        logger.warning("Unknown ui page")
        logger.attr("EMULATOR__SCREENSHOT_METHOD", self.config.script.device.screenshot_method)
        logger.attr("EMULATOR__CONTROL_METHOD", self.config.script.device.control_method)
        logger.warning("Starting from current page is not supported")
        logger.warning(f"Supported page: {[str(page) for page in self.ui_pages]}")
        logger.warning('Supported page: Any page with a "HOME" button on the upper-right')
        logger.critical("Please switch to a supported page before starting oas")
        raise GamePageUnknownError

    def ui_button_interval_reset(self, button):
        """
        Reset interval of some button to avoid mistaken clicks

        Args:
            button (Button):
        """
        if getattr(button, 'name', None) and button.name in self.interval_timer:
            self.interval_timer[button.name].reset()

    def build_reverse_path_dict(self, destination: Page) -> dict[Page, list[Page]]:
        """
        构建从每个页面到目标页面的最短路径（反向 BFS）

        Returns:
            dict[Page, list[Page]] -> {start_page: [page1, ...destinationPage], ...}
        """
        paths = {destination: [destination]}
        queue = deque([destination])
        while queue:
            cur = queue.popleft()
            for page in self.ui_pages:
                if page not in paths and cur in page.links:
                    # page -> cur
                    paths[page] = [page] + paths[cur]
                    queue.append(page)
        return paths

    def build_reverse_paths(self, destination: Page) -> list[tuple[Page, list[Page]]]:
        """
        构建从每个页面到目标页面的最短路径（反向 BFS）
        路径从短到长排序

        Returns:
            [(start_page, [page1, ...destinationPage]), ...]
        """
        paths = self.build_reverse_path_dict(destination)
        # 转换成列表并按路径长度排序, 短到长
        sorted_paths = sorted(paths.items(), key=lambda kv: len(kv[1]))
        return sorted_paths

    def ui_goto_page(self, dest_page: Page, confirm_wait=0, skip_first_screenshot=True, timeout: int = 60) -> bool:
        """前往指定page, 自动调用获取当前页面方法, 其他参数同ui_goto
        """
        self.ui_get_current_page()
        return self.ui_goto(dest_page, confirm_wait, skip_first_screenshot, timeout)

    def _try_back_main_shortcut(self, skip_first_screenshot=True) -> bool:
        """
        尝试使用一键回主页按钮(I_BACK_MAIN)直接回到 page_main。
        当页面未识别或目标是 page_main 时调用。
        如果检测到并成功点击 I_BACK_MAIN，返回 True；否则返回 False。
        """
        self.maybe_screenshot(skip_first_screenshot)
        if self.appear(self.I_BACK_MAIN):
            logger.info(f"Found I_BACK_MAIN shortcut, clicking to go to page_main")
            if self.appear_then_click(self.I_BACK_MAIN, interval=1.0):
                # 等待 page_main 出现
                if self.ui_wait_until_appear(page_main, timeout=5, skip_first_screenshot=False):
                    logger.info("I_BACK_MAIN shortcut success, arrived at page_main")
                    self.ui_current = page_main
                    return True
        return False

    def ui_goto(self, destination: Page, confirm_wait=0, skip_first_screenshot=True, timeout: int = 60) -> bool:
        """
        Args:
            destination (Page):
            confirm_wait:
            skip_first_screenshot:
        :return: find destination page or timeout reached
        """
        logger.hr(f"UI goto {destination}")
        # 初始化
        timeout_timer = Timer(timeout).start()
        confirm_timer = Timer(confirm_wait, count=int(confirm_wait // 0.5)).start()
        close_unknown_timer = Timer(3).start()
        # 构建路径映射
        path_dict = self.build_reverse_path_dict(destination)

        found = False
        while not timeout_timer.reached():
            if found:
                confirm_timer.wait()
                return True
            confirm_timer.reset()
            # 如果目标是 page_main，先尝试一键回主页快捷方式
            if destination == page_main and self.ui_current != page_main:
                if self._try_back_main_shortcut(skip_first_screenshot=skip_first_screenshot):
                    found = True
                    continue
                skip_first_screenshot = False
            path = path_dict.get(self.ui_current, None)
            # 找不到路径时，先尝试 I_BACK_MAIN 回到 page_main，再从 page_main 出发
            if not path:
                if self.ui_current != page_main and self._try_back_main_shortcut(skip_first_screenshot=skip_first_screenshot):
                    # 成功回到 page_main，重新构建路径继续导航
                    skip_first_screenshot = False
                    continue
                self.ui_get_current_page(skip_first_screenshot)
                continue
            skip_first_screenshot = False
            logger.info(f"Current page: {self.ui_current}. Following shortest path:")
            show_paths: str = ' -> '.join([p.name for p in path])
            logger.info(f"{show_paths}")
            # 遍历路径
            found = self._execute_path(path, timeout_timer)
            if not found:
                if close_unknown_timer.reached_and_reset():
                    self.try_close_unknown_page(skip_screenshot=False)
                    self.ui_current = None
        else:
            logger.error(f'Cannot goto page[{destination}], timeout[{timeout}s] reached')
        return False

    def try_close_unknown_page(self, skip_screenshot: bool = True):
        """
        尝试关闭未知界面
        :return: 执行了关闭返回True, 否则False
        """
        self.maybe_screenshot(skip_screenshot)
        timer = Timer(None).start()
        for close in self.ui_close:
            if self.appear_then_click(close, interval=1.5):
                logger.warning('Trying to switch to supported page')
                logger.info(f'[{timer.current():.1f}s]Click {close} on {self.ui_current} success')
                return True
        return False

    def _execute_path(self, path: list, timeout_timer):
        """
        执行路径
        :param path: currentPage,page1,page2,...,destinationPage
        :param timeout_timer: 超时定时器
        :return: currentPage==destinationPage
        """
        for i, current_page in enumerate(path):
            if timeout_timer.reached():
                return False
            # 当前页不等于路径中对应页, 尝试下一页
            if self.ui_current != current_page:
                continue
            self.run_additional(current_page, interval=0.6, skip_first_screenshot=False)
            # 如果已经是最后一页，不再跳转
            if i == len(path) - 1:
                if len(path) == 1:
                    logger.info(f'Page arrived {current_page}')
                break
            next_page = path[i + 1]
            logger.info(f'Page switch: {current_page} -> {next_page}')
            # 获取页面跳转操作
            button = current_page.links.get(next_page)
            if not button:
                logger.warning(f"No link from {current_page} to {next_page}")
                continue
            # 跳转页面
            max_wait_timer = Timer(6).start()
            logger.info(f'Wait appear and operate {button} on {current_page}')
            # 提前收工判据用的按钮：列表取第一个，与上面 exec_operates[0] 的语义一致
            probe = button[0] if isinstance(button, list) else button
            while not max_wait_timer.reached():
                if timeout_timer.reached():
                    return False
                if isinstance(button, list):
                    exec_operates = [self.appear_then_operate(btn, interval=0.8, skip_first_screenshot=False)
                                     for btn in button]
                    if exec_operates[0]:  # 只要第一个成功就跳出
                        break
                if self.appear_then_operate(button, interval=0.8, skip_first_screenshot=False):
                    break
                # 跳转按钮不出现、而目标页已经可见：这一步就没有可操作的对象了，
                # 直接进入到达判定，省掉最多 6 秒空等。典型是 page_main -> page_theme：
                # 卷轴已经展开时「收起卷轴」图不匹配，但 page_theme 的展开图已命中。
                # 两个条件缺一不可：目标页判据可能与当前页重叠（卷轴展开态下
                # I_CHECK_MAIN 照样匹配），只看目标页会把该点的「收起卷轴」跳掉，
                # 卷轴就一直挂着。RuleClick 类跳转（固定点击区域）不参与本判据——
                # 它必然可点，该点就得点
                if isinstance(probe, (RuleImage, RuleGif, RuleOcr)) \
                        and not self.appear(probe) \
                        and self.ui_page_appear(next_page, skip_first_screenshot=False):
                    logger.info(f'{next_page} already appear, skip operating {probe}')
                    break
            else:
                logger.warning(f'Failed recognize {button} on {current_page}')
                self.ui_get_current_page(skip_first_screenshot=False)
                # 当前页面不是对应路径的页面, 则尝试下一个页面
                if self.ui_current != current_page:
                    continue
            max_wait_timer.reset()
            while not max_wait_timer.reached():
                if timeout_timer.reached():
                    return False
                if self.ui_wait_until_appear(next_page, timeout=2.5, skip_first_screenshot=False):
                    logger.info(f'[{max_wait_timer.current():.1f}s]Page arrived {next_page}')
                    self.ui_current = next_page
                    break
            else:
                # 重新获取当前页
                self.ui_get_current_page(skip_first_screenshot=False)
        return self.ui_current == path[-1]

    def run_additional(self, page: Page, interval: float = None, skip_first_screenshot: bool = True):
        """执行页面附加操作"""
        if not page.additional:
            return

        # 先统一解析出所有附加项: condition, action, detect_seconds
        # 格式: btn                          -> condition=btn, action=btn, detect=0 (普通点击)
        #       [target, detect]             -> condition=target, action=target, detect=detect (限时检测点击)
        #       [condition, action]          -> condition=condition, action=action, detect=0 (复合条件)
        #       [condition, action, detect]  -> condition=condition, action=action, detect=detect (限时复合条件)
        items: list = []
        for btn in page.additional:
            condition = btn
            action = btn
            detect_seconds = 0
            if isinstance(btn, (list, tuple)):
                if len(btn) == 3 and isinstance(btn[2], (int, float)):
                    condition, action, detect_seconds = btn
                elif len(btn) == 2 and isinstance(btn[1], (int, float)):
                    condition, action, detect_seconds = btn[0], btn[0], btn[1]
                elif len(btn) == 2:
                    condition, action = btn
            items.append((condition, action, detect_seconds))

        # 限时检测: 所有限时项共享同一个超时窗口(取各项峰值时长)，而不是每项各自跑满自己的时长。
        # 总耗时由 sum(detect) 降为 max(detect)；且 appear() 读的是缓存帧, 一轮只截一次图就能同时
        # 匹配全部模板（原先每项独立截图, 一帧只查一个模板）。
        # 命中一项后立刻重置计时, 剩余项重新获得一个完整窗口, 所以「关掉一个又弹出下一个」仍能依次处理。
        timed = [item for item in items if item[2] > 0]
        timed_seconds = max((item[2] for item in timed), default=0)
        timed_done = False

        # 列表顺序即优先级: 从前往后处理, 靠前的先识别, 同一帧里多个都命中时也靠前的先操作。
        # 限时项整批只在「首个限时项」的位置执行一次 —— 排在它前面的非限时项先就地处理,
        # 从而保持配置里写下的先后顺序不被改写。
        for condition, action, detect_seconds in items:
            if detect_seconds > 0:
                if not timed_done:
                    self._run_timed_additional(page, timed, timed_seconds, interval)
                    timed_done = True
                    skip_first_screenshot = True  # 批次已截过图, 后续非限时项直接复用
                continue

            # 无延时时: 原有逻辑
            if condition is not action:
                # 复合条件操作: 出现条件图片时点击另一个区域
                self.maybe_screenshot(skip_first_screenshot)
                if self.appear(condition):
                    if self.appear_then_operate(action, interval=interval, skip_first_screenshot=False):
                        logger.info(f'Page {page} additional conditional {condition} -> {action} executed')
                        skip_first_screenshot = False
            elif self.appear_then_operate(condition, interval=interval, skip_first_screenshot=skip_first_screenshot):
                logger.info(f'Page {page} additional {condition} clicked')
                skip_first_screenshot = False

    def _run_timed_additional(self, page: Page, timed: list, timeout: float, interval: float) -> None:
        """共享超时窗口处理限时项。

        所有项共用一个计时器：每轮只截一次图供全部模板复用，并按列表顺序从前往后匹配。
        命中一项立即操作并从待检列表移除，随后重置计时器，让剩余项重新获得完整窗口。
        于是总耗时是 max(各项 detect) 而非 sum，且同帧多命中时靠前的项先被操作。
        """
        detect_timer = Timer(timeout).start()
        while timed and not detect_timer.reached():
            self.screenshot()
            for i, (condition, action, _) in enumerate(timed):
                if not self.appear(condition):
                    continue
                timed.pop(i)
                # 复用刚匹配到的那一帧执行动作, 不再重复截图
                if self.appear_then_operate(action, interval=interval, skip_first_screenshot=True):
                    logger.info(f'Page {page} additional {condition} -> {action} detected within {timeout}s')
                detect_timer.reset()  # 找到一个就重置时间
                break  # 画面已变化, 跳出重新截图后再查剩余项
            else:
                sleep(0.1)
    def appear_then_operate(self, target: RuleList | RuleImage | RuleGif | RuleOcr | RuleClick,
                            interval: float = None, skip_first_screenshot: bool = True):
        """
        出现对应目标执行操作(点击图像, 滑动列表至array第一个元素并点击, 点击OCR, 点击)
        :param target: 目标
        :param interval: 间隔
        :param skip_first_screenshot: 是否跳过首次截图
        :return: 是否成功操作
        """
        self.maybe_screenshot(skip_first_screenshot)
        operated = False
        if isinstance(target, RuleList):
            operated = self.list_appear_click(target, interval=interval)
        elif isinstance(target, (RuleImage, RuleGif)):
            operated = self.appear_then_click(target, interval=interval)
        elif isinstance(target, RuleOcr):
            #logger.info(f'Trying to OCR target.area{target.area} target.roi{target.roi} for {target.name}')
            operated = self.ocr_appear_click(target, interval=interval)
        elif isinstance(target, RuleClick):
            operated = self.click(target, interval=interval)
        return operated


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    from tasks.GameUi.page import page_guild

    c = Config('OAS1')
    d = Device(c)
    game = GameUi(config=c, device=d)
    game.screenshot()
    #game.appear_then_click(game.I_DLC_EXIT)
    #logger.info(game.ui_get_current_page())
    game.screenshot()
    game.ui_goto(page_main)
    game.ui_goto(page_shikigami_records)
    game.ui_goto(page_onmyodo)
    game.ui_goto(page_friends)
    game.ui_goto(page_guild)
    game.ui_goto(page_team)
    game.ui_goto(page_collection)
    game.ui_goto(page_travel)
    game.ui_goto(page_daily)
    game.ui_goto(page_mall)
    game.ui_goto(page_main)
