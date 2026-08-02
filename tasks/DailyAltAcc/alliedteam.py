# This Python file uses the following encoding: utf-8
import re
import time
import random
from datetime import datetime
from pathlib import Path
from time import sleep
from module.base.timer import Timer
from module.base.utils import save_image
from module.logger import logger
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.page import page_main, page_team, page_friends
from tasks.DailyAltAcc.utils import DailyAltAccBase
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Component.GeneralBuff.config_buff import BuffClass
from tasks.Component.GeneralRoom.general_room import GeneralRoom
from tasks.DailyAltAcc.stat_log import StatEvent
from tasks.Plotline.assets import PlotlineAssets
from tasks.MasterDisciple.assets import MasterDiscipleAssets


class Alliedteam(GeneralBattle, GeneralRoom, DailyAltAccBase):
    # 仅 alliedteam_limit_count==13 的账号启用「准备界面切换援助式神」战斗准备流程
    _need_switch_help_shikigami: bool = False
    # 援助式神切换标记，仅首场战斗切换一次，切换后置 False（后续场次直接点准备）
    _help_shikigami_detect: bool = True

    def _restore_battle_count(self) -> int:
        """从进度文件恢复已完成场次到 current_count。

        必须在捕获 before_count 之前调用，否则战斗统计的「本次新增场数」会把
        历史场次算进去。恢复后 run_alone 的次数上限判断天然只打剩余场次。
        """
        progress = getattr(self, '_progress', None)
        key = getattr(self, '_progress_key', None)
        if progress is None or not key:
            return 0
        try:
            done = progress.get_battle_count(key)
        except Exception:
            logger.exception('读取同心战斗场次失败，从 0 开始')
            return 0
        if done > 0:
            logger.info(f'同心战斗接续：已完成 {done} 场')
            self.current_count = done
        return done

    def _persist_battle_count(self) -> None:
        """每完成一场立刻回写，保证中断后能接续剩余场次。"""
        progress = getattr(self, '_progress', None)
        key = getattr(self, '_progress_key', None)
        if progress is None or not key:
            return
        try:
            progress.add_battle_count(key, 1)
        except Exception:
            logger.exception('回写同心战斗场次失败')

    def run_alliedteam(self, battle_enable, ap_enable):
        self.screenshot()
        if self.ui_get_current_page() != page_main:
            self.ui_goto(page_main)
        self.ui_goto(page_team)
        # 先恢复历史场次，再捕获基线，确保统计的是本次新增场数
        self._restore_battle_count()
        before_count = getattr(self, "current_count", 0)
        battle_result = None
        if ap_enable:
            self.run_alliedteam_ap()
        if battle_enable:
            battle_result = self.run_alliedteam_battle()
            # 只统计本次同心战斗新增场数，不统计胜负结果。
            emit_stat = getattr(self, "emit_stat", None)
            if emit_stat:
                emit_stat(StatEvent.BATTLE, count=getattr(self, "current_count", 0) - before_count)
            self.return_to_main()
        elif ap_enable:
            emit_stat = getattr(self, "emit_stat", None)
            if emit_stat:
                emit_stat(StatEvent.BATTLE, count=0)
        # 选关失败/邀请超时时一场未打：透传 False 走 False 计数通道（保持
        # pending 可重跑、两次后 skipped），不能靠默认返回 None 被标成 done
        if battle_result is False:
            return False

    def run_alliedteam_ap(self):    
        logger.info('开始执行补体力任务')
        start_time = time.time()
        count_i_ensure_ap=0
        while time.time()-start_time < 5:
            self.screenshot()
            if self.appear_then_click(self.I_TO_TEAM, interval=1):
                start_time = time.time()
                continue
            if self.appear_then_click(self.I_TO_AP, interval=1):
                start_time = time.time()
                continue
            if self.appear(self.I_ENSURE_AP):
                self.ui_click_until_disappear(self.I_ENSURE_AP, interval=1)
                count_i_ensure_ap+=1
                start_time = time.time()
                if count_i_ensure_ap>1:
                    break
                continue
            if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                start_time = time.time()
                continue
            if self.appear_then_click(self.I_LIST_MEMBER, interval=1):
                start_time = time.time()
                continue
            if self.appear_then_click(self.I_SAVE_ALL, interval=1):
                start_time = time.time()
                continue
    
        while 1:
            self.screenshot()
            if self.appear(GameUiAssets.I_CHECK_MAIN) or self.appear(self.I_M_MAIN_TO_MAIL):
                break
            if self.appear_then_click(self.I_UI_BACK_YELLOW, interval=1):
                continue
            if self.appear_then_click(self.I_BACK_BLACK, interval=1):
                continue

    def is_select_level(self):
        if not self.O_SELECT_LEVEL.ocr(self.device.image)=="觉醒业火轮壹层":
            if not self.check_zones('觉醒业火轮'):
                return False
            while 1:
                self.screenshot()
                if self.O_SELECT_LEVEL.ocr(self.device.image)=="觉醒业火轮壹层":
                    break
                logger.info(f"当前选择关卡:{list(self.O_FLAG_LEVEL.ocr(self.device.image))}")
                if not list(self.O_FLAG_LEVEL.ocr(self.device.image))==[0,0,0,0]:
                    logger.info(f"当前选择关卡:{self.O_SELECT_LEVEL.ocr(self.device.image)}")
                else :
                    self.appear_then_click(self.I_CLICK_EVOZONE, interval=1)
                    continue
                if self.appear_then_click(self.I_CLICK_LEVEL, interval=1):
                    continue
                self.swipe(self.S_SELECT_LEVEL,3)
        return True           

    def run_alliedteam_battle(self):    
        logger.info('开始执行战斗任务')
        if not self.is_select_level():
            return False
        start_time = time.time()
        while time.time()-start_time < 5:
            self.screenshot()
            if self.appear(self.I_PAGE_INVITE, interval=1):
                start_time = time.time()
                break
            if self.appear_then_click(self.I_TO_TEAM, interval=1):
                start_time = time.time()
                continue
            
            if self.appear_then_click(self.I_INVITE, interval=1):
                start_time = time.time()
                continue
        if time.time()-start_time >= 5:
            return False
        while time.time()-start_time < 5:
            self.screenshot()
            if self.appear(self.I_FORM_OVER):
                start_time = time.time()
                logger.info("邀请完成")
                break
            if len(self.I_INVITE_FRIEND_OVER.match_all_any(self.device.image))<2:
                logger.info("邀请好友")
                if self.appear(self.I_INVITE_FRIEND, interval=1):
                    self.I_INVITE_FRIEND.roi_front[2]=13
                    self.I_INVITE_FRIEND.roi_front[3]=12
                    self.click(self.I_INVITE_FRIEND)
                    time.sleep(1)
                start_time = time.time()
                continue
                
            if self.appear_then_click(self.I_FORM, interval=1):
                start_time = time.time()
                continue
                   
        while 1:
            self.screenshot()
            if self.appear(self.I_BATTLE, interval=1):  
                break
            if self.appear_then_click(self.I_CREATE_AGAIN, interval=1):
                continue
            if self.appear_then_click(self.I_CREATE_TEAM, interval=1):
                continue
            if self.appear_then_click(self.I_SELECT_LEVEL, interval=1):
                    continue
        self.run_alone()
        return True

    def return_to_main(self):   
        while 1:
            self.screenshot()
            if self.appear(GameUiAssets.I_CHECK_MAIN) or self.appear(self.I_M_MAIN_TO_MAIL):
                    break
            if self.appear_then_click(self.I_EXIT_2, interval=1):
                continue
            if self.appear_then_click(self.I_ENSURE_EXIT, interval=1):
                continue
            if self.appear_then_click(self.I_UI_BACK_YELLOW,interval=1):
                continue
            if self.appear_then_click(self.I_BACK_BLACK,interval=1):
                continue
            if self.appear_then_click(self.I_EXIT3, interval=1):
                continue
            if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                continue    
        self.ui_goto(page_main)
        if self.get_config().daily_alt_acc_config.alliedteam_limit_count == 13:
            self.screenshot()
            self.ui_goto(page_friends)
            while 1:    
                self.screenshot()
                if self.appear(self.I_FRIEND_HELP_FLAG, interval=1):
                    break
                if self.appear_then_click(self.I_FRIEND_HELP,action=self.C_FRIEND_HELP_CLICK, interval=1):
                    continue
                
            now=datetime.now()
            # 角色名优先取多账号运行注入的统计上下文(_stat_ctx)，单实例运行时退化为配置实例名
            char_name = (getattr(self, '_stat_ctx', None) or {}).get('char') or self.config.config_name
            # 替换 Windows 文件名非法字符，避免保存失败
            char_name = re.sub(r'[\\/:*?"<>|]', '_', str(char_name))
            save_dir = Path(f'screenshots/Battle_Screenshots_{now.year}_{now.month:02d}_{now.day:02d}')
            save_dir.mkdir(parents=True, exist_ok=True)
            # 同一角色同一天重复运行时直接覆盖，只保留最新一张
            save_path = save_dir / f'{char_name}.png'
            save_image(self.screenshot(), str(save_path))
            logger.info(f'同心协战次数截图已保存: {save_path}')
            run_timer=Timer(5)
            run_timer.start()
            while 1:    
                self.screenshot()
                if run_timer.reached():
                    break
                if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                    break
            self.screenshot()
            if self.ui_get_current_page() != page_main:
                self.ui_goto(page_main)
            

    def check_lock(self, lock: bool = True) -> bool:
        """
        检查是否锁定阵容, 要求在觉醒界面
        :param lock:
        :return:
        """
        logger.info('Check lock: %s', lock)
        if lock:
            while 1:
                self.screenshot()
                if self.appear(self.I_LOCK):
                    return True
                if self.appear_then_click(self.I_UNLOCK, interval=1):
                    continue
        else:
            while 1:
                self.screenshot()
                if self.appear(self.I_UNLOCK):
                    return True
                if self.appear_then_click(self.I_LOCK, interval=1):
                    continue

    def run_alone(self):
        def is_in_evozone(screenshot=False) -> bool:
            if screenshot:
                self.screenshot()
            return self.appear(self.I_BATTLE)
        logger.info('Start run alone')
        alliedteam_limit_count=self.get_config().daily_alt_acc_config.alliedteam_limit_count
        logger.info(f' alliedteam_limit_count: {alliedteam_limit_count}')
        # 次数为 13 的账号：改为在准备界面切换援助式神，因此此处必须先解锁阵容
        # （若保持锁定状态，准备界面将无法切换援助式神）。其余次数维持原锁定阵容流程。
        if alliedteam_limit_count == 13:
            self.check_lock(False)
            self._need_switch_help_shikigami = True
            self._help_shikigami_detect = True
        else:
            self.check_lock(True)
        while 1:
            self.screenshot()
            if self.current_count >= alliedteam_limit_count:
                logger.info('Orochi count limit out')
                break
            if not is_in_evozone():
                continue
            # 点击挑战
            while 1:
                self.screenshot()
                if self.appear_then_click(self.I_BATTLE, interval=1):
                    pass
                if not self.appear(self.I_BATTLE):
                    self.run_general_battle(config=self.config.daily_alt_acc.general_battle_config)
                    # 本场结束立刻落盘，中断后可从这里接续
                    self._persist_battle_count()
                    break

    def battle_before(self, buff: BuffClass | list[BuffClass], config: GeneralBattleConfig, timeout: float = 5) -> bool:
        """战斗前设置
        次数为 13 的账号走「切换援助式神 → 准备」流程（参照 MasterDisciple 的
        _disciple_exploration_battle_before）；其余账号沿用父类原有的战斗准备逻辑。
        :return: True:进入战斗或点击了准备按钮且识别不到准备按钮了 False:超过timeout还没进入战斗且没点过准备
        """
        # 非 13 账号：维持原有通用战斗准备流程
        if not self._need_switch_help_shikigami:
            return super().battle_before(buff, config, timeout)

        # 13 账号：在准备界面切换援助式神后再点准备（不锁定阵容，仅首场切换）
        timeout_timer = Timer(timeout).start()
        while not timeout_timer.reached():
            self.screenshot()
            if self.is_in_real_battle(False):  # 已进入战斗阶段
                return True
            if self.appear_then_click(self.I_DISABLE_7DAYS_DIFF_SOUL, interval=0.6):  # 关闭御魂不一致提示
                continue
            if self.appear_then_click(self.I_CONFIRM_CLOSE_DIFF_SOUL, interval=0.6):  # 确认关闭御魂不一致提示
                continue
            if self.is_in_prepare(False):  # 战斗准备阶段
                timeout_timer.reset()
                # 切换援助式神逻辑（参照 Plotline / MasterDisciple）
                if self._help_shikigami_detect:
                    if not self.appear(PlotlineAssets.I_FLAG_CHANGE):
                        # 未展开助战列表，先点击切换按钮
                        self.click(PlotlineAssets.C_CLICK_CHANGE)
                        sleep(2)
                        continue
                    else:
                        # 已展开助战列表，OCR 定位助战位并确保援助式神上场
                        self.screenshot()
                        roi = list(MasterDiscipleAssets.O_FIND_SHIKIGAMI_HELP.ocr(self.device.image))
                        if not roi == [0, 0, 0, 0]:
                            PlotlineAssets.I_FLAG_ON_FIELD.roi_back = (
                                roi[0] + roi[2] - 81, roi[1] + roi[3] - 160, 130, 160
                            )
                            logger.info(f"I_FLAG_ON_FIELD.roi_back ={PlotlineAssets.I_FLAG_ON_FIELD.roi_back}")
                            if not self.appear(PlotlineAssets.I_FLAG_ON_FIELD):
                                # 援助式神未上场，滑动将其拖入出战位
                                PlotlineAssets.S_SWIPE_SHIKIGAMI.roi_front = (
                                    roi[0], roi[1], roi[2], roi[3]
                                )
                                self.swipe(PlotlineAssets.S_SWIPE_SHIKIGAMI, 4)
                                sleep(2)
                                continue
                # 点击准备，切换完成后置标记为 False（后续场次不再切换）
                if self.appear_then_click(self.I_PREPARE_HIGHLIGHT, interval=0.8):
                    self._help_shikigami_detect = False
                    continue
                continue
            logger.info('Wait for preparation page')
            sleep(random.uniform(0.4, 0.8))
        return False


if __name__ == "__main__":
    from module.config.config import Config
    from module.device.device import Device
    c = Config('QMUMU2')
    d = Device(c)
    self = Alliedteam(c, d)
    self.screenshot()
    self.run_alliedteam_ap()
    #self.run_alliedteam(False, True)
