# This Python file uses the following encoding: utf-8
# @author 
# github 
import random
import time
from time import sleep
from datetime import datetime, timedelta, time as dtime

from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.Component.GeneralInvite.general_invite import GeneralInvite, RoomType
from tasks.Component.GeneralInvite.config_invite import InviteConfig, InviteNumber, FindMode
from tasks.BondlingFairyland.assets import BondlingFairylandAssets
from tasks.Component.GeneralRoom.general_room import GeneralRoom
from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_team, page_shikigami_records, page_exploration,page_youki, page_mall
from tasks.MasterDisciple.assets import MasterDiscipleAssets
from tasks.MasterDisciple.config import MasterDisciple, MasterDiscipleMode
from tasks.Exploration.solo import SoloExploration
from tasks.Exploration.config import ExplorationLevel, UpType
from tasks.Plotline.assets import PlotlineAssets
from tasks.ExperienceYoukai.assets import ExperienceYoukaiAssets
from tasks.GoldYoukai.assets import GoldYoukaiAssets
from tasks.Restart.assets import RestartAssets
from tasks.DailyTrifles.assets import DailyTriflesAssets
from tasks.RichMan.assets import RichManAssets
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.Component.MultiAccountRunner.progress import ProgressStore, acc_key
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Component.GeneralBuff.config_buff import BuffClass
from tasks.Component.config_base import Time

from module.logger import logger
from module.exception import TaskEnd, RequestHumanTakeover, GameNotRunningError
from module.base.timer import Timer


class ScriptTask(GeneralBattle, GeneralInvite, GeneralRoom, SwitchSoul, GameUi, MasterDiscipleAssets,
                 ExperienceYoukaiAssets, GoldYoukaiAssets, BondlingFairylandAssets):
    # 探索任务中切换援助式神相关标记
    help_shikigami_detect: bool = True
    coin_buff: bool =False
    # 徒弟轮询的账号级续做进度，run_as_disciple 中创建；中断后接续时已完成徒弟直接跳过
    _progress: ProgressStore = None
    def run(self) -> bool:
        """
        师徒任务主入口
        """
        # 御魂切换：MasterDisciple不再暴露switch_soul配置，但保留师父模式的御魂预设切换
        # 预设切换在run_as_master中处理

        limit_count = self.config.master_disciple.master_disciple_config.limit_count
        limit_time = self.config.master_disciple.master_disciple_config.limit_time
        self.current_count = 0
        self.limit_count: int = limit_count
        self.limit_time: timedelta = timedelta(
            hours=limit_time.hour,
            minutes=limit_time.minute,
            seconds=limit_time.second
        )
        self.screenshot()
        self.ui_get_current_page()
        self.ui_goto(page_main)

        config: MasterDisciple = self.config.master_disciple

        success = True
        try:
            match config.master_disciple_config.mode:
                case MasterDiscipleMode.MASTER:
                    success = self.run_as_master()
                case MasterDiscipleMode.DISCIPLE:
                    success = self.run_as_disciple()
                case _:
                    logger.error('Unknown master-disciple mode')
        except Exception as e:
            # 异常上抛前必须置 success=False，否则 finally 会误判为成功并按成功间隔调度
            success = False
            raise e
        finally:
            # 下一次运行时间
            if success:
                self.set_next_run('MasterDisciple', finish=True, success=True)
                if config.master_disciple_config.run_exploration:
                    self.set_next_run('Exploration', success=True)
                if config.master_disciple_config.run_exp_monster:
                    self.set_next_run('ExperienceYoukai', success=True)
                if config.master_disciple_config.run_stone_ju:
                    self.set_next_run('Tako', success=True)
                if config.master_disciple_config.run_coin_monster:
                    self.set_next_run('GoldYoukai', success=True)
                if config.master_disciple_config.run_guard:
                    pass
                # 徒弟轮询全部成功收尾后清进度：先调度后清，顺序不可颠倒
                if self._progress is not None:
                    self._progress.clear()
            else:
                self.set_next_run('MasterDisciple', finish=False, success=False)

        raise TaskEnd

    # ======================== 徒弟模式 ========================

    def _run_task_with_retry(self, task_func, task_name: str, max_retries: int = 3) -> bool:
        """
        带重试机制执行小任务，捕获异常后重试，超过重试次数则推送通知并跳过

        :param task_func: 要执行的任务函数
        :param task_name: 任务名称（用于日志和通知）
        :param max_retries: 最大重试次数
        :return: 是否成功
        """
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"[{task_name}] 第{attempt}次执行")
                self.device.stuck_record_clear()
                task_func()
                logger.info(f"[{task_name}] 第{attempt}次执行成功")
                return True
            except GameNotRunningError:
                raise
            except RequestHumanTakeover:
                raise
            except TaskEnd:
                break
            except Exception as e:
                logger.error(f"[{task_name}] 第{attempt}次执行异常: {e}")
                if attempt < max_retries:
                    logger.info(f"[{task_name}] 准备重试 ({attempt}/{max_retries})...")
                    # 异常恢复：回到庭院，清理可能残留的状态
                    try:
                        self.device.stuck_record_clear()
                        self.screenshot()
                        self.ui_get_current_page()
                        self.ui_goto(page_main)
                    except Exception:
                        logger.warning(f"[{task_name}] 恢复到庭院失败，继续重试")
                else:
                    logger.error(f"[{task_name}] 已重试{max_retries}次，仍然失败，跳过该任务")
                    self.config.notifier.push(
                        content=f"{task_name}任务异常，已重试{max_retries}次仍失败，已跳过\nError: {e}",
                        title=f"{task_name}任务失败"
                    )
        return False

    def run_as_disciple(self):
        """
        以徒弟身份运行
        支持 cycle_all_disciples 配置：
        - False: 只切换到第一个徒弟账号执行任务
        - True: 轮询所有徒弟账号，依次切换并执行任务
        """
        logger.info("Running as disciple")

        account_list = self.config.master_disciple.disciple_account_list
        cycle_all = self.config.master_disciple.master_disciple_config.cycle_all_disciples
        auto_switch = self.config.master_disciple.master_disciple_config.auto_switch_account

        if not auto_switch or not account_list:
            # 不切换账号或没有账号列表，直接在当前账号执行任务
            self._execute_disciple_tasks()
            return True

        if not cycle_all:
            # 只执行第一个徒弟账号
            logger.info("Cycle all disciples is disabled, switching to first disciple account only")
            if not self.switch_to_disciple_account(account_list[0]):
                return False
            self._execute_disciple_tasks()
            return True

        # 轮询所有徒弟账号：建账号级续做进度，阶段标识 = 徒弟账号集合 + 自然日
        logger.info(f"Cycle all disciples enabled, total {len(account_list)} account(s) to process")
        self._progress = ProgressStore('master_disciple', self.config.config_name)
        self._progress.ensure_phase(
            {'disciples': [acc_key(a.account, a.character, a.svr) for a in account_list],
             'day': self.start_time.strftime('%Y-%m-%d')},
            self.start_time.strftime('%Y%m%d-%H%M'),
        )
        all_success = True
        for index, account_info in enumerate(account_list):
            key = acc_key(account_info.account, account_info.character, account_info.svr)
            if self._progress.is_account_done(key):
                logger.info(f"Disciple {account_info.character}-{account_info.svr} already done, skipping")
                continue
            logger.info(f"Processing disciple account {index + 1}/{len(account_list)}: {account_info.character}-{account_info.svr}")
            if not self.switch_to_disciple_account(account_info):
                logger.warning(f"Failed to switch to disciple account {account_info.character}-{account_info.svr}, skipping")
                all_success = False
                continue
            try:
                self._execute_disciple_tasks()
                # 徒弟任务正常完成：即时落盘，中断后接续时整个跳过
                self._progress.mark_account_done(key)
            except TaskEnd:
                raise
            except RequestHumanTakeover:
                raise
            except GameNotRunningError:
                raise
            except Exception as e:
                logger.error(f"Error executing tasks for disciple {account_info.character}-{account_info.svr}: {e}")
                all_success = False
                # 异常恢复：回到庭院
                try:
                    self.device.stuck_record_clear()
                    self.screenshot()
                    self.ui_get_current_page()
                    self.ui_goto(page_main)
                except Exception:
                    logger.warning("Failed to recover to main page after error")

        return all_success

    def _check_and_buy_ap(self):
        """
        徒弟模式：在庭院检测体力，不足则进商店购买
        流程：庭院OCR读取体力(O_SUSHI_NUM) → 计算需购买次数 → 进商店购买 → 回庭院
        """
        import math
        ap_threshold = self.config.master_disciple.master_disciple_config.ap_threshold
        logger.info(f"[体力购买] 目标体力: {ap_threshold}")

        try:
            # 确保在庭院
            self.screenshot()
            self.ui_get_current_page()
            self.ui_goto(page_main)

            # 在庭院OCR读取当前体力
            self.screenshot()
            current_ap = self.O_SUSHI_NUM.ocr_digit(self.device.image)
            logger.info(f"[体力购买] 当前体力: {current_ap}, 目标: {ap_threshold}")

            # 体力已足够，不需要购买
            if current_ap >= ap_threshold:
                logger.info(f"[体力购买] 当前体力 {current_ap} >= {ap_threshold}，无需购买")
                return

            # 计算需要购买的次数，每次购买获得100体力
            need_ap = ap_threshold - current_ap
            buy_count = math.ceil(need_ap / 100)
            logger.info(f"[体力购买] 还需 {need_ap} 体力，需购买 {buy_count} 次")

            # 导航到商店
            self.ui_goto(page_mall, confirm_wait=3)

            # 进入Special页面（带超时保护）
            enter_timer = Timer(30)
            enter_timer.start()
            while 1:
                self.screenshot()
                if self.appear(RichManAssets.I_SIDE_CHECK_SPECIAL):
                    break
                if enter_timer.reached():
                    logger.warning("[体力购买] 进入Special页面超时，跳过")
                    return
                if self.appear_then_click(RichManAssets.I_SIDE_SURE_SPECIAL, interval=1):
                    continue
                if self.appear_then_click(RichManAssets.I_MALL_SUNDRY, interval=1):
                    continue

            # 循环购买指定次数的体力
            bought = 0
            buy_loop_timer = Timer(60)
            buy_loop_timer.start()
            while bought < buy_count and not buy_loop_timer.reached():
                self.screenshot()

                # 执行一次购买
                logger.info(f"[体力购买] 正在购买第 {bought + 1}/{buy_count} 次")
                if self.appear(DailyTriflesAssets.I_STORE_COST_TYPE_JADE):
                    self.ui_click_until_disappear(DailyTriflesAssets.I_STORE_COST_TYPE_JADE, interval=2)
                    bought += 1
                    logger.info(f"[体力购买] 第 {bought} 次购买成功")
                elif self.appear(DailyTriflesAssets.I_SPECIAL_SUSHI):
                    self.ui_click(DailyTriflesAssets.I_SPECIAL_SUSHI, stop=DailyTriflesAssets.I_STORE_COST_TYPE_JADE, interval=2)
                    self.screenshot()
                    if self.appear(DailyTriflesAssets.I_STORE_COST_TYPE_JADE):
                        self.ui_click_until_disappear(DailyTriflesAssets.I_STORE_COST_TYPE_JADE, interval=2)
                        bought += 1
                        need_harvest_mail = True
                        logger.info(f"[体力购买] 第 {bought} 次购买成功")
                else:
                    logger.warning("[体力购买] 未找到体力购买项，跳过")
                    return
                # 购买后等待页面刷新
                sleep(1)

            if bought >= buy_count:
                logger.info(f"[体力购买] 完成，共购买 {bought} 次，获得 {bought * 100} 体力")
            else:
                logger.warning(f"[体力购买] 购买循环超时，已购买 {bought} 次")

        except Exception as e:
            logger.warning(f"[体力购买] 执行异常: {e}，跳过购买")
        finally:
            # 确保回到庭院
            try:
                self.screenshot()
                self.ui_get_current_page()
                self.ui_goto(page_main)
                logger.info("[体力购买] 已回到庭院，开始领取一次邮件")
                self._harvest_mail_after_buy_ap()
                self.screenshot()
                self.ui_get_current_page()
                self.ui_goto(page_main)
            except Exception:
                logger.warning("[体力购买] 返回庭院或领取邮件失败")

    def _harvest_mail_after_buy_ap(self) -> bool:
        """
        体力检测流程结束后领取一次邮件
        该方法由体力检测流程在回到庭院后调用，复用DailyAltAcc的邮件领取流程
        """
        logger.info("[体力购买] 体力检测流程结束后领取一次邮件")
        from tasks.DailyAltAcc.mail import Mail
        return Mail(self.config, self.device).run_mail()

    def _execute_disciple_tasks(self):
        """
        在当前徒弟账号上执行所有已启用的任务
        """
        # 体力检测与购买
        if self.config.master_disciple.master_disciple_config.buy_ap_when_low:
            self._run_task_with_retry(self._check_and_buy_ap, "体力检测购买")
        # 执行守护历练任务
        if self.config.master_disciple.master_disciple_config.run_guard:
            self._run_task_with_retry(self.run_guard_as_disciple, "守护历练")
        # 执行石距任务
        if self.config.master_disciple.master_disciple_config.run_stone_ju:
            self._run_task_with_retry(self.run_stone_ju_as_disciple, "石距")
        # 执行金币妖怪任务
        if self.config.master_disciple.master_disciple_config.run_coin_monster:
            self._run_task_with_retry(self.run_coin_monster_as_disciple, "金币妖怪")

        # 执行经验妖怪任务
        if self.config.master_disciple.master_disciple_config.run_exp_monster:
            self._run_task_with_retry(self.run_exp_monster_as_disciple, "经验妖怪")

        # 执行探索任务
        if self.config.master_disciple.master_disciple_config.run_exploration:
            self._run_task_with_retry(self.run_exploration_as_disciple, "探索")

    def switch_to_disciple_account(self, account_info=None):
        """
        切换到徒弟账号

        :param account_info: 要切换的账号信息，若为None则从列表取第一个账号
        """
        logger.info("Switching to disciple account")

        if account_info is None:
            account_list = self.config.master_disciple.disciple_account_list
            if not account_list:
                logger.warning("Disciple account list is empty, cannot switch")
                return False
            account_info = account_list[0]

        # 重置检测记录，避免影响后续操作
        self.device.stuck_record_clear()

        success = SwitchAccount(self.config, self.device, account_info).switchAccount()
        if not success:
            logger.warning(f"Switch to disciple account failed: {account_info.character}-{account_info.svr}")
            self.config.notifier.push(
                content=f"Switch to {account_info.character}-{account_info.svr} Failed, account info: {account_info.account}",
                title="未找到账号"
            )
        else:
            logger.info(f"Successfully switched to disciple account: {account_info.character}-{account_info.svr}")

        return success
    def _get_add_count(self, consecutive_count: int = 2) -> int:
        """
        连续采集加号图标数量，直到连续 consecutive_count 次结果相同时返回该数量

        :param consecutive_count: 需要连续多少次采集结果相同才返回，默认 3
        :return: 稳定的加号图标数量
        """
        self.device.stuck_record_add('BATTLE_STATUS_S')

        def reject_invite():
            from tasks.Component.GeneralInvite.assets import GeneralInviteAssets as gia
            while 1:
                self.screenshot()
                if not (self.appear(gia.I_I_REJECT_1) or self.appear(gia.I_I_REJECT_2) or self.appear(gia.I_I_REJECT_3) or self.appear(gia.I_I_REJECT_4)):
                    break
                if self.appear(gia.I_I_REJECT_4):
                    self.click(gia.I_I_REJECT_4, 1)
                    continue
                if self.appear(gia.I_I_REJECT_1):
                    self.click(gia.I_I_REJECT_1, 1)
                    continue
                if self.appear(gia.I_I_REJECT_3):
                    self.click(gia.I_I_REJECT_3, 1)
                    continue
                if self.appear(gia.I_I_REJECT_2):
                    self.click(gia.I_I_REJECT_2, 1)
                    continue
                if self.appear(gia.I_I_REJECT_1):
                    self.click(gia.I_I_REJECT_1, 1)
                    continue
            return True

        list_add_count = [99] * consecutive_count
        index = 0
        while 1:
            self.screenshot()
            # 所有采集值相等且不为初始值 99 时，认为已稳定，返回该数量
            if len(set(list_add_count)) == 1 and list_add_count[0] != 99:
                logger.info(f'获取加号数量:[{list_add_count[0]}]')
                return list_add_count[0]
            if index >= consecutive_count:
                index = 0
            reject_invite()
            list_add_count[index] = len(self.I_CLICK_INVITE_ADD.match_all_any(self.device.image))
            index += 1
            time.sleep(0.5)

    def _create_room_and_invite(self, task_name: str, room_type: RoomType = RoomType.NORMAL_5,
                                 navigate_and_create_func=None, invite_timeout: int = None,
                                 wait_for_others: bool = True) -> bool:
        """
        创建房间并邀请师父/好友的通用流程
        通用流程：导航并创建房间 → 等待进入房间 → 记录加号状态 → 邀请师父 → 等待师父进入

        :param task_name: 任务名称，如 '金币妖怪'、'守护历练'（用于日志和通知）
        :param room_type: 房间类型，决定加号图标和邀请逻辑
        :param navigate_and_create_func: 导航并创建房间的函数，无参数，返回bool（True=成功进入房间）
            若为None，则使用默认流程：ui_goto(page_team) → check_zones(task_name) → create_room → ensure_private → create_ensure
        :param invite_timeout: 等待师父进入房间的超时时间（秒），None时使用全局配置 invite_timeout
        :param wait_for_others: 师父进入后是否公开房间并等待其他人（默认True保持现有行为）
        :return: True 师父已进入房间，False 邀请失败或超时
        """
        master_name = self.config.master_disciple.master_disciple_config.master_name
        # 未指定超时时回退到全局配置，保证守护历练等既有调用行为不变
        if invite_timeout is None:
            invite_timeout = self.config.master_disciple.master_disciple_config.invite_timeout

        if navigate_and_create_func is not None:
            # 使用自定义的导航+创建房间函数
            if not navigate_and_create_func():
                logger.warning(f"[{task_name}] Failed to navigate and create room")
                return False
        else:
            # 默认流程：导航到组队页面 → 创建私人房间
            sleep(2)
            self.screenshot()
            self.ui_get_current_page()
            self.ui_goto(page_team)
            self.check_zones(task_name)

            if not self.create_room():
                logger.warning(f"[{task_name}] Failed to create room")
                return False

            self.ensure_private()
            self.create_ensure()

        # 等待进入房间
        wait_enter = Timer(10)
        wait_enter.start()
        while 1:
            self.screenshot()
            if self.is_in_room():
                break
            if wait_enter.reached():
                logger.warning(f"[{task_name}] Failed to enter room")
                return False

        # 根据房间类型选择需要检测的加号图标数量
        add_num = self._get_add_icons(room_type)
        # wait_for_others=False 时跳过公开房间和等待他人步骤
        if add_num == 4 and wait_for_others:
            add_other=True
        else:
            add_other=False

        # 等待师父进入房间，每15秒重新邀请一次
        self.device.stuck_record_clear()
        self.device.stuck_record_add('BATTLE_STATUS_S')
        wait_timer = Timer(invite_timeout)
        wait_timer.start()
        reinvite_timer = Timer(15)
        reinvite_timer.start()
        # 首次邀请师父（即使失败也不退出，继续等待重试）
        self._invite_by_room_type(master_name, room_type)

        while 1:
            self.screenshot()

            if not self.is_in_room():
                continue

            if wait_timer.reached():
                logger.warning(f"[{task_name}] Master did not accept invite within {invite_timeout}s")
                self.config.notifier.push(
                    content=f"师父{master_name}在{invite_timeout}秒内未接受邀请，任务：{task_name}已跳过",
                    title="邀请师父超时"
                )
                self.exit_room()
                return False
            # 检查是否有人进入（某个加号从有变为无，表示有人进了该位置）
            logger.info(f"add_num:[{add_num}]")
            if  add_num>self._get_add_count():
                if add_other:
                    while 1:
                        self.screenshot()
                        if  self.appear(self.I_ENSURE_SWITCH):
                            break
                        self.appear_then_click(self.I_TO_SWITCH, interval=1)
                    while 1:
                        self.screenshot()
                        if "所有人"in self.O_ADD_ALL.ocr(self.device.image):
                            break
                        if self.ui_click(self.I_SWITCH_ALL,stop=self.I_SWITCH_ALL_OVER, interval=1):
                            if self.appear_then_click(self.I_ENSURE_SWITCH,interval=1):
                                continue
                    add_num-=2
                    add_other=False
                    continue
                logger.info(f"return True")
                return True
            if add_num!=self._get_add_icons(room_type):
                reinvite_timer.reset()
            # 每15秒重新邀请师父
            if reinvite_timer.reached():
                logger.info(f"[{task_name}] Re-inviting master: {master_name}")
                reinvite_timer.reset()
                self._invite_by_room_type(master_name, room_type)

        return False

    def _get_add_icons(self, room_type: RoomType) -> int:
        """
        根据房间类型获取需要监测的加号图标个数

        :param room_type: 房间类型
        :return: 加号图标个数
        """
        if room_type == RoomType.NORMAL_2 or room_type == RoomType.ETERNITY_SEA:
            return 1
        elif room_type == RoomType.NORMAL_3:
            return 2
        else:
            return 4
    def _goto_invite(self):
        while 1:
            self.screenshot() 
            if self.appear(self.I_LOAD_FRIEND):
                break
            if self.appear(self.I_INVITE_ENSURE):
                break
            if self.appear_then_click(self.I_CLICK_INVITE_ADD,interval=1):
                continue
    def _invite_by_room_type(self, name: str, room_type: RoomType) -> bool:
        """
        根据房间类型分派邀请逻辑

        :param name: 被邀请人名字
        :param room_type: 房间类型
        :return: 是否邀请成功
        """
        # 邀请前先关闭可能遮挡好友名字的拒绝按钮，金币妖怪房间中偶发出现
        from tasks.Component.GeneralInvite.assets import GeneralInviteAssets as gia
        reject_timer = Timer(3).start()
        while not reject_timer.reached():
            self.screenshot()
            if self.appear_then_click(gia.I_I_REJECT_4, interval=0.5):
                continue
        if room_type == RoomType.NORMAL_2 :
            return self._guard_invite_friend_no_tab(name)
        elif room_type == RoomType.NORMAL_3:
            return self._invite_friend_3room(name)
        else:
            return self.invite_friend(name, FindMode.AUTO_FIND)

    def _invite_friend_3room(self, name: str = None) -> bool:
        """
        3人房邀请好友（石距等），只有好友/跨区两个标签
        参照BondlingFairyland的invite_friend实现
        :param name: 好友名字
        :return: 是否邀请成功
        """
        logger.info('Click add to invite friend (3-room)')
        # 点击＋号（3人房用I_ADD_1和I_ADD_2）
        self._goto_invite()

        # 识别好友标签（只有好友和跨区）
        friend_class = []
        list_1 = self.O_FRIEND.ocr(self.device.image)
        list_2 = self.O_KUAQU.ocr(self.device.image)
        list_1 = list_1.replace(' ', '').replace('、', '')
        list_2 = list_2.replace(' ', '').replace('、', '')
        if list_1 is not None and list_1 != '' and list_1 in self.friend_class:
            friend_class.append(list_1)
        if list_2 is not None and list_2 != '' and list_2 in self.friend_class:
            friend_class.append(list_2)
        for i in range(len(friend_class)):
            if friend_class[i] == '蔡友':
                friend_class[i] = '寮友'
            elif friend_class[i] == '路区':
                friend_class[i] = '跨区'
            elif friend_class[i] == '察友':
                friend_class[i] = '寮友'
            elif friend_class[i] == '区':
                friend_class[i] = '跨区'
        logger.info(f'Friend class: {friend_class}')

        is_select: bool = False

        for index in range(len(friend_class)):
            if is_select:
                continue
            # 切换到对应的好友标签
            while index == 0:
                self.screenshot()
                if self.appear(self.I_SELECT_FRIEND_ON):
                    break
                if self.appear_then_click(self.I_SELECT_FRIEND_OFF, interval=1):
                    continue
            while index == 1:
                self.screenshot()
                if self.appear(self.I_SELECT_KUAQU_ON):
                    break
                if self.appear_then_click(self.I_SELECT_KUAQU_OFF, interval=1):
                    continue

            # 等待好友列表加载后搜索
            logger.info(f'Now find friend in {friend_class[index]}')
            sleep(1)
            if not is_select:
                if self.detect_select(name):
                    is_select = True
            sleep(1)
            if not is_select:
                if self.detect_select(name):
                    is_select = True

        # 点击确定
        logger.info('Click invite ensure')
        if not self.appear(self.I_INVITE_ENSURE):
            logger.warning('No appear invite ensure while invite friend')
        while 1:
            self.screenshot()
            if not self.appear(self.I_INVITE_ENSURE):
                break
            if self.appear_then_click(self.I_INVITE_ENSURE):
                continue
        # 没有找到好友也点击确认以退出好友列表
        if not is_select:
            logger.warning('No find friend')
            logger.info('Task failed')
            return False

        return True

    def _run_battle_with_invite(self, zones_name: str, battle_count: int = 2,
                                 buff_open_func=None, buff_close_func=None,
                                 battle_wait_func=None,
                                 room_type: RoomType = RoomType.NORMAL_5,
                                 wait_for_others: bool = True) -> bool:
        """
        徒弟模式下带师父邀请的战斗通用流程

        :param zones_name: 副本名称
        :param battle_count: 战斗次数
        :param buff_open_func: 开buff的函数
        :param buff_close_func: 关buff的函数
        :param battle_wait_func: 战斗等待函数（默认使用 run_general_battle）
        :param room_type: 房间类型，5人房(NORMAL_5)或3人房(NORMAL_3)
        :param wait_for_others: 是否在师父进入后公开房间等待其他人（默认True）
        :return: 是否成功完成
        """
        
        start_time =time.time() 
        while  time.time() - start_time< 5:
            self.screenshot()
            if self.appear_then_click(self.I_UI_BACK_YELLOW,interval=1):
                start_time =time.time() 
                continue
            if self.appear(PlotlineAssets.I_PAGE_MAIN,interval=1):
                start_time =time.time() 
                break
        # 开启加成
        if buff_open_func:
            self.screenshot()
            self.ui_get_current_page()
            self.ui_goto(page_main)
            #buff_open_func()

        count = 0
        while count < battle_count:
            self.device.stuck_record_clear()
            # 创建私人房间并邀请师父（石距/金币/经验固定等待4分钟）
            if not self._create_room_and_invite(zones_name, room_type=room_type, invite_timeout=240, wait_for_others=wait_for_others):
                # 邀请失败，跳过该任务
                logger.warning(f"Skip {zones_name} due to invite failure")
                break
            logger.info(f"click_fire")
            # 师父已进入房间，点击挑战
            self.click_fire()
            count += 1

            # 执行战斗（battle_before内会点击准备按钮）
            if battle_wait_func:
                # 使用通用battle_before处理准备阶段，再用自定义battle_wait处理结算
                battle_config = GeneralBattleConfig(lock_team_enable=True)
                self.battle_before(buff=None, config=battle_config)
                battle_wait_func()
            else:
                self.run_general_battle(config=GeneralBattleConfig(lock_team_enable=True))

            # 战斗结束后处理可能出现的弹窗（绑定手机、活动弹窗等）
            self._handle_post_battle_popup()

            # 战斗结束后检查是否需要再次邀请（默认邀请）
            self.device.stuck_record_add('BATTLE_STATUS_S')
            self.check_and_invite(default_invite=True)

        # 关闭加成
        if buff_close_func:
            self.screenshot()
            self.ui_get_current_page()
            self.ui_goto(page_main)
            #buff_close_func()

        return count > 0

    def _handle_popup(self):
        """
        处理战斗胜利后可能出现的弹窗（绑定手机、活动弹窗等）
        在 battle_wait 循环内和战斗结束后调用
        """
        # 绑定手机弹窗：先检测"前往绑定"，再点击"取消绑定"
        if self.appear(RestartAssets.I_LOGIN_LOGIN_GOTO_BIND_PHONE):
            logger.info("Detected bind phone popup, closing it")
            if self.appear_then_click(RestartAssets.I_LOGIN_LOGIN_CANCEL_BIND_PHONE, interval=1):
                logger.info("Closed bind phone popup")
                return True
        if self.appear_then_click(PlotlineAssets.I_PAGE_CLICK_ANY, interval=1):
            logger.info("Closed bind phone popup")
            return True
        # 通用确认弹窗
        if self.appear_then_click(self.I_UI_CONFIRM, interval=1):
            logger.info("Closed UI confirm popup")
            return True
        if self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=1):
            logger.info("Closed small UI confirm popup")
            return True
        # "知道了"弹窗
        if self.appear_then_click(self.I_UI_GOTIT_SMALL, interval=1):
            logger.info("Closed 'got it' popup")
            return True
        # 取消按钮弹窗
        if self.appear_then_click(self.I_UI_CANCEL, interval=1):
            logger.info("Closed UI cancel popup")
            return True
        if self.appear_then_click(self.I_UI_CANCEL_SAMLL, interval=1):
            logger.info("Closed small UI cancel popup")
            return True
        return False

    def _exp_youkai_battle_wait(self):
        """
        经验妖怪战斗结算（参照ExperienceYoukai.battle_wait）
        检测 I_DE_WIN 或 I_EXP_WIN，处理胜利后弹窗
        """
        self.device.stuck_record_add('BATTLE_STATUS_S')
        self.device.click_record_clear()
        logger.info("Start exp youkai battle process")
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_PREPARE_HIGHLIGHT, interval=1):
                logger.info('click prepare')
            # 处理弹窗（绑定手机、活动弹窗等）
            self._handle_popup()
            if self.appear(self.I_DE_WIN):
                logger.info('Win battle (DE_WIN)')
                self.ui_click_until_disappear(self.I_DE_WIN)
                # 胜利后可能出现弹窗，循环处理直到回到组队/主界面
                self._handle_post_battle_popup()
                return True
            if self.appear(self.I_EXP_WIN):
                logger.info('Win battle (EXP_WIN)')
                self.ui_click_until_disappear(self.I_EXP_WIN)
                self._handle_post_battle_popup()
                return True
            if self.appear(self.I_FALSE):
                logger.warning('False battle')
                self.ui_click_until_disappear(self.I_FALSE)
                return False

    def _handle_post_battle_popup(self, timeout: float = 5):
        """
        处理战斗胜利点击后可能出现的弹窗（绑定手机、活动提示等）
        最多等待timeout秒，确保弹窗被关闭
        """
        start = time.time()
        while time.time() - start < timeout:
            self.screenshot()
            if self._handle_popup():
                # 处理了一个弹窗，重置计时
                start = time.time()
                continue
            # 没有弹窗了，退出
            break

    def _gold_youkai_battle_wait(self):
        """
        金币妖怪战斗结算（参照GoldYoukai.battle_wait）
        检测 I_DE_WIN 或 I_GOLD_WIN，处理胜利后弹窗
        """
        self.device.stuck_record_add('BATTLE_STATUS_S')
        self.device.click_record_clear()
        logger.info("Start gold youkai battle process")
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_PREPARE_HIGHLIGHT, interval=1):
                logger.info('click prepare')
            # 处理弹窗
            self._handle_popup()
            if self.appear(self.I_DE_WIN):
                logger.info('Win battle (DE_WIN)')
                self.ui_click_until_disappear(self.I_DE_WIN)
                self._handle_post_battle_popup()
                return True
            if self.appear(self.I_GOLD_WIN):
                logger.info('Win battle (GOLD_WIN)')
                self.ui_click_until_disappear(self.I_GOLD_WIN)
                self._handle_post_battle_popup()
                return True
            if self.appear(self.I_FALSE):
                logger.warning('False battle')
                self.ui_click_until_disappear(self.I_FALSE)
                return False

    def _tako_battle_wait(self):
        """
        石距战斗结算（参照Tako.battle_wait）
        检测 I_WIN 或 I_REWARD，再点击领奖直到回到主界面/组队
        """
        self.device.stuck_record_add('BATTLE_STATUS_S')
        self.device.click_record_clear()
        logger.info("Start tako battle process")
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_PREPARE_HIGHLIGHT, interval=1):
                logger.info('click prepare')
            # 处理弹窗
            self._handle_popup()
            if self.appear(self.I_WIN) or self.appear(self.I_REWARD):
                logger.info('Win battle')
                self.ui_click_until_disappear(self.I_WIN)
                while 1:
                    self.screenshot()
                    # 处理弹窗
                    self._handle_popup()
                    if self.appear(self.I_CHECK_MAIN) or self.appear(self.I_CHECK_TEAM):
                        break
                    if self.click(self.C_REWARD_2, interval=2):
                        continue
                return True
            if self.appear(self.I_FALSE):
                logger.warning('False battle')
                self.ui_click_until_disappear(self.I_FALSE)
                return False

    def run_coin_monster_as_disciple(self):
        """
        徒弟模式 - 金币妖怪（2次，邀请师父，5人房）
        战斗结算：检测 I_DE_WIN 或 I_GOLD_WIN（参照GoldYoukai）
        """
        logger.info("Running coin monster as disciple")

        # 金币妖怪师父准备后退出时，徒弟公开房间等待其他人补位
        wait = self.config.master_disciple.master_disciple_config.master_coin_exit_after_prepare
        self._run_battle_with_invite(
            zones_name='金币妖怪',
            battle_count=2,
            battle_wait_func=self._gold_youkai_battle_wait,
            room_type=RoomType.NORMAL_5,
            wait_for_others=wait
        )

    def run_exp_monster_as_disciple(self):
        """
        徒弟模式 - 经验妖怪（2次，邀请师父，5人房）
        战斗结算：检测 I_DE_WIN 或 I_EXP_WIN（参照ExperienceYoukai）
        写死开启50%和100%经验加成
        """
        logger.info("Running experience monster as disciple")

        def open_buff():
            self.open_buff()
            self.exp_50()
            self.exp_100()
            self.close_buff()

        def close_buff():
            self.open_buff()
            self.exp_50(False)
            self.exp_100(False)
            self.close_buff()

        # 经验妖怪师父准备后退出时，徒弟公开房间等待其他人补位
        wait = self.config.master_disciple.master_disciple_config.master_exp_exit_after_prepare
        self._run_battle_with_invite(
            zones_name='经验妖怪',
            battle_count=2,
            buff_open_func=open_buff,
            buff_close_func=close_buff,
            battle_wait_func=self._exp_youkai_battle_wait,
            room_type=RoomType.NORMAL_5,
            wait_for_others=wait
        )

    def run_stone_ju_as_disciple(self):
        """
        徒弟模式 - 石距（周一到周五2次，周六周日1次，邀请师父，3人房；超时退出）
        战斗结算：检测 I_WIN 或 I_REWARD，再点击领奖（参照Tako）
        """
        logger.info("Running stone ju (tako) as disciple")

        # 石距每周五六是愤怒的石距
        if 5 <= self.start_time.weekday() <= 6:
            zones_name = '愤怒的石距'
            battle_count = 1
        else:
            zones_name = '石距'
            battle_count = 2

        def open_buff():
            self.open_buff()
            self.exp_50()
            self.exp_100()
            self.close_buff()

        def close_buff():
            self.open_buff()
            self.exp_50(False)
            self.exp_100(False)
            self.close_buff()

        self._run_battle_with_invite(
            zones_name=zones_name,
            battle_count=battle_count,
            buff_open_func=open_buff,
            buff_close_func=close_buff,
            battle_wait_func=self._tako_battle_wait,
            room_type=RoomType.NORMAL_3
        )

    # ======================== 守护历练 Guard ========================

    def _guard_goto_team(self) -> bool:
        """
        从庭院导航到守护历练组队房间
        流程：庭院 → 旅途中 → 任务页 → 找到守护历练 → 进入组队
        :return: 是否成功进入组队房间
        """
        logger.info("Guard: navigating to team room")

        # 导航到旅途中
        self.screenshot()
        self.ui_get_current_page()
        self.ui_goto(page_main)
        self.ui_goto(page_youki)

        # 等待任务页面出现
        """ while 1:
            self.screenshot()
            if self.appear(self.I_PAGE_TASK):
                logger.info('Guard: task page appeared')
                break
            if self.appear_then_click(self.I_TO_TASK, interval=1):
                continue """

        # 在任务列表中找到"守护历练"并点击
        """ start_time = time.time()
        swipe_count = 0
        while time.time() - start_time < 30:
            self.screenshot()
            if self.appear(self.I_PAGE_BATTLE_GUARD):
                break
            roi = list(self.O_FLAG_TASK_GUARD.ocr(self.device.image))
            if roi != [0, 0, 0, 0]:
                roi[2] = 424
                roi[3] = 77
                self.I_TO_BATTLE_GUARD.roi_back = roi
                if self.appear_then_click(self.I_TO_BATTLE_GUARD, interval=1):
                    start_time = time.time()
                    continue
            if time.time() - start_time > (5 + swipe_count * 2):
                self.swipe(self.S_FIND_TASK_GUARD, 2)
                sleep(1)
                swipe_count += 1

        if not self.appear(self.I_PAGE_BATTLE_GUARD):
            logger.warning('Guard: failed to find battle guard page')
            return False """

        start_time = time.time()
        to_team_click_cnt = 0
        while time.time() - start_time < 15:
            self.screenshot()
            if self.appear(self.I_PAGE_TEAM):
                logger.info('Guard: entered room')
                return True
            if self.appear_then_click(self.I_TO_TEAM, interval=1):
                to_team_click_cnt += 1
                if to_team_click_cnt >=5:
                    logger.warning('Guard: clicked I_TO_TEAM over 5 times, guard daily limit may be exhausted')
                    raise  TaskEnd('Guard daily limit may be exhausted')
                start_time = time.time()
                continue
            """ if self.appear(self.I_PAGE_BATTLE_GUARD) and not self.appear(self.I_TO_TEAM):
                start_time = time.time()
                logger.info('Guard: swiping to find team button')
                self.swipe(self.S_TO_BATTLE_SWIPE, 5)
                continue """

        logger.warning('Guard: failed to enter team room')
        return False

    def _guard_invite_friend_no_tab(self, name: str) -> bool:
        """
        Guard房间2人房邀请好友（没有好友/跨区标签的邀请界面）
        流程：点击+号 → 等待好友列表加载 → OCR搜索好友 → 选中 → 确定
        :param name: 好友名字
        :return: 是否邀请成功
        """
        logger.info(f'Guard: inviting friend [{name}]')

        # 点击+号邀请
        self._goto_invite()
        # 等待好友列表加载
        sleep(1.5)

        # OCR搜索并选中好友（guard邀请没有标签，直接在列表中搜索）
        is_select = False
        self.O_FRIEND_NAME_1.keyword = name
        self.O_FRIEND_NAME_2.keyword = name

        for _ in range(3):
            if is_select:
                break
            sleep(1)
            if self.detect_select(name):
                is_select = True
            sleep(1)
            if not is_select:
                if self.detect_select(name):
                    is_select = True

        # 点击确定
        logger.info('Guard: click invite ensure')
        while 1:
            self.screenshot()
            if not self.appear(self.I_INVITE_ENSURE):
                break
            if self.appear_then_click(self.I_INVITE_ENSURE):
                continue

        if not is_select:
            logger.warning(f'Guard: friend [{name}] not found')
            return False

        return True

    def _guard_battle_wait(self, random_click_swipt_enable: bool) -> bool:
        """
        Guard专用战斗等待：战斗胜利时邀请弹窗（I_GI_SURE）在I_WIN上层，
        必须先处理邀请弹窗才能点击I_WIN
        """
        self.device.stuck_record_add('BATTLE_STATUS_S')
        self.device.click_record_clear()
        logger.info("Guard: start battle process")
        win: bool = False

        # 阶段1：等待战斗结束
        while 1:
            self.screenshot()
            if self.appear(self.I_WIN, threshold=0.8) or self.appear(self.I_DE_WIN):
                logger.info("Guard: battle result is win")
                win = True
                break
            if self.appear(self.I_FALSE, threshold=0.8):
                logger.info("Guard: battle result is false")
                win = False
                break
            if self.appear(self.I_REWARD, threshold=0.6):
                win = True
                break
            if self.appear(self.I_REWARD_GOLD, threshold=0.8):
                win = True
                break
            if random_click_swipt_enable:
                self.random_click_swipt()

        if not win:
            while 1:
                self.screenshot()
                if self.appear_then_click(self.I_FALSE, threshold=0.6):
                    continue
                if not self.appear(self.I_FALSE, threshold=0.6):
                    break
            return False

        # 阶段2：胜利 — 先处理邀请弹窗（在I_WIN上层），再点I_WIN
        logger.info("Guard: handling invite dialog on top of WIN")
        self.I_REWARD.roi_back=[0,0,1280,720]
        while 1:
            self.screenshot()
            # 优先处理邀请弹窗
            if self.appear(self.I_GI_SURE):
                if self.appear(self.I_I_NO_DEFAULT):
                    self.appear_then_click(self.I_I_NO_DEFAULT, interval=0.5)
                    continue
                if self.appear_then_click(self.I_GI_SURE, interval=0.5):
                    continue
            # 邀请弹窗消失后，点击胜利
            if self.appear(self.I_WIN, threshold=0.8):
                action_click = random.choice([self.C_WIN_1, self.C_WIN_2, self.C_WIN_3])
                self.appear_then_click(self.I_WIN, action=action_click, interval=0.5)
                sleep(2)
                continue
            if self.appear_multi_scale(self.I_REWARD):
                self.ui_click_until_smt_disappear(self.I_REWARD, self.I_REWARD, interval=1.5)
                continue
            if self.appear_multi_scale(self.I_REWARD_GOLD):
                self.ui_click_until_smt_disappear(self.I_REWARD_GOLD, self.I_REWARD_GOLD, interval=1.5)
                continue
            # I_WIN和邀请弹窗都消失了，进入奖励阶段
            if not self.appear(self.I_WIN, threshold=0.8) and not self.appear(self.I_REWARD) and not self.appear(self.I_GI_SURE):
                break
                

        return True

    def _guard_run_battle(self, config: GeneralBattleConfig):
        """
        Guard房间战斗流程：使用自定义_guard_battle_wait处理邀请弹窗
        """
        logger.hr("Guard battle start", 2)
        self.current_count += 1
        logger.info(f"Current count: {self.current_count}")
        self.battle_before(None, config)
        if self.is_in_battle(False):
            self.green_mark(config.green_enable, config.green_mark)
        win = self._guard_battle_wait(config.random_click_swipt_enable)
        return win

    def run_guard_as_disciple(self):
        """
        徒弟模式 - 守护历练任务
        流程：
        1. 使用_create_room_and_invite导航到守护历练组队页面并邀请师父
        2. 等待师父进入 → 开战
        3. 战斗胜利 → 自动处理邀请弹窗 → 等师父再进入 → 循环
        """
        logger.info("Running guard as disciple")

        guard_count = self.config.master_disciple.master_disciple_config.guard_battle_count
        master_name = self.config.master_disciple.master_disciple_config.master_name
        invite_timeout = self.config.master_disciple.master_disciple_config.invite_timeout

        # 使用通用函数导航并创建房间、邀请师父
        # Guard的特殊导航通过navigate_and_create_func传入
        if not self._create_room_and_invite(
            task_name='守护历练',
            room_type=RoomType.NORMAL_2,
            navigate_and_create_func=self._guard_goto_team
        ):
            logger.warning("Guard: failed to create room and invite master")
            return

        # 确认房间类型（2人房）
        self.room_type = RoomType.NORMAL_2
        logger.info('Guard: room type is NORMAL_2')

        # 初始化战斗配置
        battle_config = GeneralBattleConfig(lock_team_enable=True)

        count = 0

        while count < guard_count:
            # 等待师父进入房间（I_ADD_2_1消失表示有人进入）
            self.device.stuck_record_clear()
            self.device.stuck_record_add('BATTLE_STATUS_S')
            self.screenshot()
            wait_timer = Timer(invite_timeout)
            wait_timer.start()
            reinvite_timer = Timer(15)
            reinvite_timer.start()

            while 1:
                self.screenshot()
                if not self.is_in_room():
                    continue

                # 2人房：I_ADD_2_1消失表示有人进入
                if self._get_add_count()==0:
                    break

                if wait_timer.reached():
                    logger.warning(f'Guard: master did not enter within {invite_timeout}s')
                    raise Exception("wait timeout")

                if reinvite_timer.reached():
                    logger.info('Guard: re-inviting master')
                    reinvite_timer.reset()
                    self._invite_by_room_type(master_name,RoomType.NORMAL_2)

            # 师父已进入，点击挑战
            self.click_fire()
            count += 1
            # _guard_run_battle 内部在战斗胜利时会自动处理邀请弹窗并点击确认
            # 确认后师父会自动收到邀请，下一轮等师父进入即可
            win = self._guard_run_battle(battle_config)
            if  self.appear(self.I_TO_TEAM):
                start_time = time.time()
                while time.time()-start_time<5:
                    self.screenshot()
                    if self.appear(PlotlineAssets.I_PAGE_MAIN):
                        raise TaskEnd ("Guard: returned to main page, ending task")
                    if self.appear_then_click(self.I_BACK_YELLOW, interval=1):
                        continue
                self.screenshot()
                self.ui_get_current_page()
                self.ui_goto(page_main) 
                raise TaskEnd ("Guard: returned to main page, ending task")
            if not win:
                logger.warning('Guard: battle failed')
                break

        # 退出房间
        if self.exit_room():
            pass
        # 退出组队界面
        if self.exit_team():
            pass
        start_time=time.time()
        while time.time()>start_time-5:
            self.screenshot()
            if self.appear(self.I_CHECK_MAIN):
                break
            if self.appear_then_click(self.I_BACK_YELLOW, interval=1):
                start_time = time.time()
                continue
        self.screenshot()
        if self.ui_get_current_page()!=page_main:
            self.ui_goto(page_main) 
        logger.info(f'Guard: completed {count} battles')
        raise TaskEnd ("Guard: completed")

    def run_exploration_as_disciple(self):
        """
        徒弟模式 - 探索任务
        参照Plotline流程：自动寻找最高章节 → 执行15次战斗 → 切换援助式神 → 锁定队伍
        配置不暴露给用户，全部在代码中初始化
        """
        logger.info("Running exploration as disciple")

        # 创建SoloExploration实例
        solo_exploration = SoloExploration(self.config, self.device)

        solo_exploration.config.model.exploration.exploration_config.exploration_level = ExplorationLevel.AUTO
        logger.info("Set exploration chapter to: AUTO")

        # 在代码中初始化探索配置，不暴露给用户
        solo_exploration._config.general_battle_config.lock_team_enable = False
        solo_exploration._config.exploration_config.minions_cnt =14
        solo_exploration._config.exploration_config.limit_time = dtime(0, 15, 0)  # 15分钟上限兜底
        solo_exploration._config.exploration_config.up_type = UpType.ALL
        solo_exploration._config.scrolls.scrolls_enable = False

        solo_exploration.ui_get_current_page()
        solo_exploration.ui_goto(page_exploration)

        # 切换援助式神相关标记
        self.help_shikigami_detect = True

        # 临时替换battle_wait和battle_before方法
        original_battle_wait = solo_exploration.battle_wait
        original_battle_before = solo_exploration.battle_before
        solo_exploration.battle_wait = self.battle_wait
        solo_exploration.battle_before = self._disciple_exploration_battle_before

        try:
            solo_exploration.run_solo()
        except Exception as e:
            logger.error(f"Exploration task error: {e}")
            self.config.notifier.push(content=f'探索任务异常: {e}', title='MasterDisciple')
        finally:
            # 恢复原始方法
            solo_exploration.battle_wait = original_battle_wait
            solo_exploration.battle_before = original_battle_before

    def _disciple_exploration_battle_before(self, buff: BuffClass | list[BuffClass], 
                                              config: GeneralBattleConfig, timeout: float = 10) -> bool:
        """
        徒弟探索战斗前：切换援助式神 → 锁定队伍 → 准备
        参照Plotline的battle_before实现
        """
        timeout_timer = Timer(timeout).start()
        confed = False
        while not timeout_timer.reached():
            self.screenshot()
            if self.is_in_real_battle(False) :
                return True
            if self.appear_then_click(self.I_DISABLE_7DAYS_DIFF_SOUL, interval=0.6):
                continue
            if self.appear_then_click(self.I_CONFIRM_CLOSE_DIFF_SOUL, interval=0.6):
                continue
            if self.is_in_prepare(False):
                timeout_timer.reset()
                # 切换援助式神逻辑（参照Plotline）
                if self.help_shikigami_detect:
                    if not self.appear(PlotlineAssets.I_FLAG_CHANGE):
                        self.click(PlotlineAssets.C_CLICK_CHANGE)
                        sleep(2)
                        continue
                    else:
                        self.screenshot()
                        roi=list(self.O_FIND_SHIKIGAMI_HELP.ocr(self.device.image))
                        if not roi==[0,0,0,0]:
                            PlotlineAssets.I_FLAG_ON_FIELD.roi_back = (
                                                    roi[0] + roi[2]-81, roi[1] + roi[3]-160,130,160
                                                )
                            logger.info(f"I_FLAG_ON_FIELD.roi_back ={PlotlineAssets.I_FLAG_ON_FIELD.roi_back}")
                            if not self.appear(PlotlineAssets.I_FLAG_ON_FIELD):
                                PlotlineAssets.S_SWIPE_SHIKIGAMI.roi_front = (
                                    roi[0], roi[1], 
                                    roi[2], roi[3]
                                )
                                self.swipe(PlotlineAssets.S_SWIPE_SHIKIGAMI, 4)
                                sleep(2)
                                continue

                if self.appear_then_click(self.I_PREPARE_HIGHLIGHT, interval=0.8):
                    self.help_shikigami_detect = False
                    continue
                continue

            logger.info('Wait for preparation page')
            sleep(random.uniform(0.4, 0.8))
        return False


    # ======================== 师父模式 ========================

    def run_as_master(self):
        """
        以师父身份运行：
        1. 任务开始前，去式神录一次性切换三组御魂预设（类似SixRealms）
        2. 回到庭院被动等待徒弟邀请
        3. 接受邀请 → 等待开战 → 战斗 → 循环
        """
        logger.info("Running as master")

        try:
            # 任务开始前切换三组御魂预设
            self._master_switch_presets()

            # 确保在庭院等待
            self.screenshot()
            self.ui_get_current_page()
            self.ui_goto(page_main)

            # 进入被动等待循环
            self.master_battle_flow()

        except GameNotRunningError:
            raise
        except RequestHumanTakeover:
            raise
        except TaskEnd:
            raise
        except Exception as e:
            logger.error(f"师父模式执行异常: {e}")
            self.config.notifier.push(
                content=f"师父模式任务异常\nError: {e}",
                title="师父模式任务失败"
            )
            return False

    def _master_switch_presets(self):
        """
        任务开始前，去式神录一次性切换三组御魂预设
        类似SixRealms的switch_soul模式，依次切换1→2→3组
        """
        config = self.config.master_disciple
        switch_targets = []
        for preset_cfg in [config.master_preset_1, config.master_preset_2, config.master_preset_3]:
            if preset_cfg.enable:
                switch_targets.append((preset_cfg.preset_group, preset_cfg.preset_team))
                logger.info(f"Master preset enabled: group={preset_cfg.preset_group}, team={preset_cfg.preset_team}")

        if not switch_targets:
            logger.info("No master preset enabled, skip switching")
            return

        logger.info(f"Master switching {len(switch_targets)} preset(s) before battle")

        # 导航到式神录
        self.screenshot()
        self.ui_get_current_page()
        self.ui_goto(page_shikigami_records)

        # 依次切换每组预设
        self.run_switch_soul(switch_targets)

        logger.info("Master preset switching completed")

    def master_battle_flow(self):
        """
        师父的战斗流程：
        在庭院被动等待邀请 → check_then_accept → wait_battle → run_general_battle → 循环
        参照Orochi的run_member模式实现
        配置不暴露给用户，在代码中初始化
        """
        def check_then_accept() -> int :
            """
            队员接受邀请
            :return:
            """
            battle_type = 0
            if not self.appear(self.I_ACCEPT):
                return battle_type
            logger.info('Click accept')
            start_time = time.time()
            while time.time()-start_time < 30:
                self.screenshot()
                if self.is_in_room():
                    return battle_type
                # 被秒开
                # https://github.com/runhey/OnmyojiAutoScript/issues/230
                if self.appear(self.I_EXIT):
                    return battle_type
                if self.appear(self.I_ACCEPT, interval=1):
                    self.O_ACCEPT_NAME.roi=[self.I_ACCEPT.roi_front[0]+167,self.I_ACCEPT.roi_front[1]+25,180,47]
                    text=self.O_ACCEPT_NAME.ocr(self.device.image)
                    logger.info(f"text={text}")
                    if "金币"in text :
                        if not self.coin_buff:
                            self.open_buff()
                            self.gold_50(True)
                            self.gold_100(True)
                            self.close_buff()
                            self.coin_buff=True
                            continue
                        battle_type= 5
                    elif "经验"in text:
                        battle_type= 6
                    elif "石距"in text:
                        battle_type= 7
                    elif "守护"in text:
                        battle_type= 8
                    else:
                        continue
                    self.click(self.I_I_ACCEPT,interval=2)
            return battle_type 
                    
        logger.info("Master battle flow started, waiting in courtyard for invitations")
        self.device.stuck_record_clear()
        self.device.stuck_record_add('BATTLE_STATUS_S')
        wait_out=Timer(180).start()
        exp_battle_count = 0  # 经验妖怪战斗计数
        coin_battle_count =0
        while not wait_out.reached():
            self.screenshot()
            battle_type = check_then_accept()
            # 1. 检查并接受邀请
            if battle_type ==0:
                logger.info('Master accepted invitation')
                continue
            self.device.stuck_record_add('BATTLE_STATUS_S')
            # 2. 如果已经在房间内，等待队长（徒弟）开战
            if self.is_in_room():
                self.device.stuck_record_clear()
                self.device.stuck_record_add('BATTLE_STATUS_S')
                if self.wait_battle(wait_time=dtime(minute=2)):
                      
                    # 进入战斗后根据金币/经验独立开关决定是否准备后退出
                    master_config = self.config.master_disciple.master_disciple_config
                    if battle_type == 8:
                        # 守护历练：始终正常完成战斗
                        self.run_general_battle(config=GeneralBattleConfig())
                    elif battle_type == 5:
                        # 金币妖怪：根据独立开关决定正常完成或准备后退出
                        if not master_config.master_coin_exit_after_prepare:
                            # 正常完成战斗
                            battle_config = GeneralBattleConfig(lock_team_enable=True)
                            self.battle_before(buff=None, config=battle_config)
                            self._gold_youkai_battle_wait()
                        else:
                            # 进入后退出
                            self.master_run_battle_back(config=GeneralBattleConfig())
                    elif battle_type == 6:
                        # 经验妖怪：根据独立开关决定正常完成或准备后退出
                        if not master_config.master_exp_exit_after_prepare:
                            # 正常完成战斗
                            battle_config = GeneralBattleConfig(lock_team_enable=True)
                            self.battle_before(buff=None, config=battle_config)
                            self._exp_youkai_battle_wait()
                        else:
                            # 进入战斗后等待OCR数字达到24再退出
                            self.master_run_exp_battle_back(config=GeneralBattleConfig())
                    elif battle_type == 7:
                        # 石距：始终进入后退出
                        self.master_run_battle_back_stone(config=GeneralBattleConfig())
                    else:
                        # 未知类型：进入后退出
                        self.master_run_battle_back(config=GeneralBattleConfig())
                    # 经验妖怪(battle_type==6)退出后计数，达到2次则结束任务
                    if battle_type == 6:
                        exp_battle_count += 1
                        logger.info(f'Exp battle count: {exp_battle_count}/2')
                        if exp_battle_count >= 2:
                            logger.info('Master has exited 2 exp battles, ending task')
                            self.screenshot()
                            self.ui_get_current_page()
                            self.ui_goto(page_main)
                            raise TaskEnd
                    if battle_type == 5:
                        coin_battle_count += 1
                        if coin_battle_count >= 2:
                            self.screenshot()
                            self.ui_get_current_page()
                            self.ui_goto(page_main)
                            if self.coin_buff:
                                self.open_buff()
                                self.gold_50(False)
                                self.gold_100(False)
                                self.close_buff()
                                self.coin_buff=False
                    wait_out.reset()
                    self.device.stuck_record_clear()
                    self.device.stuck_record_add('BATTLE_STATUS_S')
                    sleep(2)
                    self.screenshot()

            # 3. 如果不在房间也不在战斗，确保回到庭院
            if self.ui_get_current_page() != page_main:
                self.ui_get_current_page()
                self.ui_goto(page_main) 
                continue
        raise TaskEnd

    def master_run_exp_battle_back(self, config: GeneralBattleConfig = None, exit_four: bool = False) -> bool:
        """
        经验妖怪进入战斗后先打开退出确认框，等待O_KILL_CNT识别数字达到30再确认退出
        :param config:
        :return:
        """
        # 先点击准备进入战斗，保持师父模式原有“进入后退出”前置行为
        self.wait_until_appear_then_click(self.I_PREPARE_HIGHLIGHT)
        prepare_timeout = Timer(5).start()
        while 1:
            self.screenshot()
            if prepare_timeout.reached():
                logger.warning(f"Timeout while waiting for {self.I_PREPARE_HIGHLIGHT.name}")
                break
            if self.appear_then_click(self.I_PREPARE_HIGHLIGHT, interval=1):
                continue
        logger.info(f"Click {self.I_PREPARE_HIGHLIGHT.name}")
        # 进入真实战斗后先点击退出键，让界面停在退出确认框，等击杀数达标后立刻确认退出
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_EXIT, interval=1.5):
                continue
            if self.appear(self.I_EXIT_ENSURE):
                break
        logger.info(f"Click {self.I_EXIT.name}")

        # 使用O_KILL_CNT识别经验妖怪战斗中的击杀数量，达到30后点击确认退出
        wait_ocr_timer = Timer(120).start()
        while not wait_ocr_timer.reached():
            self.screenshot()
            try:
                value = self.O_KILL_CNT.ocr_digit(self.device.image)
            except Exception as e:
                logger.warning(f"[经验妖怪] O_KILL_CNT识别异常: {e}")
                value = 0
            logger.info(f"[经验妖怪] 击杀数量: {value}/30")
            if value >= 30:
                logger.info("[经验妖怪] 击杀数量已达到30，确认退出战斗")
                self.appear_then_click(self.I_EXIT_ENSURE, interval=1.5)
                break
            sleep(1)
        else:
            logger.warning("[经验妖怪] 等待OCR数字达到30超时，确认退出战斗")
            self.appear_then_click(self.I_EXIT_ENSURE, interval=1.5)

        # 等待退出结果，并处理失败确认
        while 1:
            self.screenshot()
            if self.appear(self.I_CHECK_MAIN):
                return True
            if self.appear_then_click(self.I_EXIT_ENSURE, interval=1.5):
                continue
            if self.appear(self.I_FALSE):
                break
        logger.info(f"Click {self.I_EXIT_ENSURE.name}")

        # 点击失败确认
        self.wait_until_appear(self.I_FALSE)
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_FALSE, interval=1.5):
                continue
            if not self.appear(self.I_FALSE):
                break
        logger.info(f"Click {self.I_FALSE.name}")

        return True

    def master_run_battle_back_stone(self, config: GeneralBattleConfig = None, exit_four: bool = False) -> bool:
        """
        进入挑战然后直接返回
        :param config:
        :return:
        """
        # 如果没有锁定队伍那么在点击准备后才退出的,退四的话就直接退出
        #if not config.lock_team_enable and not exit_four:
        # 点击准备按钮
        self.wait_until_appear(self.I_PREPARE_HIGHLIGHT)
        # 点击返回
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_EXIT, interval=1.5):
                continue
            if self.appear(self.I_EXIT_ENSURE):
                break
        logger.info(f"Click {self.I_EXIT.name}")

        # 点击返回确认
        while 1:
            self.screenshot()
            if self.appear(self.I_CHECK_MAIN):
                return True
            if self.appear_then_click(self.I_EXIT_ENSURE, interval=1.5):
                continue
            if self.appear(self.I_FALSE):
                break
        logger.info(f"Click {self.I_EXIT_ENSURE.name}")

        # 点击失败确认
        self.wait_until_appear(self.I_FALSE)
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_FALSE, interval=1.5):
                continue
            if not self.appear(self.I_FALSE):
                break
        logger.info(f"Click {self.I_FALSE.name}")

        return True
    def master_run_battle_back(self, config: GeneralBattleConfig = None, exit_four: bool = False) -> bool:
        """
        进入挑战然后直接返回
        :param config:
        :return:
        """
        # 如果没有锁定队伍那么在点击准备后才退出的,退四的话就直接退出
        #if not config.lock_team_enable and not exit_four:
        # 点击准备按钮
        sleep(5)
        self.wait_until_appear_then_click(self.I_PREPARE_HIGHLIGHT)
        self.click(self.I_PREPARE_HIGHLIGHT)
        logger.info(f"Click {self.I_PREPARE_HIGHLIGHT.name}")
        # 点击返回
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_EXIT, interval=1.5):
                continue
            if self.appear(self.I_EXIT_ENSURE):
                break
        logger.info(f"Click {self.I_EXIT.name}")

        # 点击返回确认
        while 1:
            self.screenshot()
            if self.appear(self.I_CHECK_MAIN):
                return True
            if self.appear_then_click(self.I_EXIT_ENSURE, interval=1.5):
                continue
            if self.appear(self.I_FALSE):
                break
        logger.info(f"Click {self.I_EXIT_ENSURE.name}")

        # 点击失败确认
        self.wait_until_appear(self.I_FALSE)
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_FALSE, interval=1.5):
                continue
            if not self.appear(self.I_FALSE):
                break
        logger.info(f"Click {self.I_FALSE.name}")

        return True
if __name__ == "__main__":
    from module.config.config import Config
    from module.device.device import Device
    from tasks.Component.GeneralInvite.assets import GeneralInviteAssets as gia
    c = Config('oas3')
    d = Device(c)
    self = ScriptTask(c, d)
    self.screenshot()
    self.master_run_battle_back_stone(config=GeneralBattleConfig())
    #self.run()
    """ while 1:
        self.screenshot()
        if  self.appear(self.I_ENSURE_SWITCH):
            break
        self.appear_then_click(self.I_TO_SWITCH, interval=1)
    while 1:
        self.screenshot()
        if "所有人"in self.O_ADD_ALL.ocr(self.device.image):
            break
        if self.ui_click(self.I_SWITCH_ALL,stop=self.I_SWITCH_ALL_OVER, interval=1):
            if self.appear_then_click(self.I_ENSURE_SWITCH,interval=1):
                continue """
        
    """ click_add=self.I_CLICK_INVITE_ADD.match_all_any(self.device.image)
    logger.info (f"len(click_add{len(click_add)})") 
    self._goto_invite() """
    """ self.ui_goto(page_main)
    self.run() """
    """ roi=list(self.O_FIND_SHIKIGAMI_HELP.ocr(self.device.image))
    if not roi==[0,0,0,0]:
        PlotlineAssets.I_FLAG_ON_FIELD.roi_back = (
                                 roi[0] + roi[2]-81, roi[1] + roi[3]-160,130,160
                            )
        logger.info(f"I_FLAG_ON_FIELD.roi_back ={PlotlineAssets.I_FLAG_ON_FIELD.roi_back}")
        if not self.appear(PlotlineAssets.I_FLAG_ON_FIELD):
            PlotlineAssets.S_SWIPE_SHIKIGAMI.roi_front = (
                roi[0], roi[1], 
                roi[2], roi[3]
            )
            self.swipe(PlotlineAssets.S_SWIPE_SHIKIGAMI, 4)
            sleep(2) """

    #self.run()
        
    #self._guard_goto_team()