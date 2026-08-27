import importlib
from datetime import datetime, timedelta, time as dtime
import os
import time
import random
import numpy as np
import cv2
from module.exception import TaskEnd, RequestHumanTakeover,GameNotRunningError, GameStuckError
from module.logger import logger
from tasks.Component.SchedulingShield import shield_scheduling
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.Plotline.assets import PlotlineAssets
from tasks.Exploration.assets import ExplorationAssets
from tasks.Exploration.solo import SoloExploration
from tasks.Exploration.config import ExplorationLevel,UpType
from tasks.Plotline.config import  Plotline
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.assets import GameUiAssets 
from tasks.Restart.assets import RestartAssets
from tasks.ExperienceYoukai.assets import ExperienceYoukaiAssets
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.GameUi.page import page_main,page_team,page_exploration
from module.base.timer import Timer
from module.atom.ocr import RuleOcr
from module.atom.swipe import RuleSwipe
from module.atom.image import RuleImage
from time import sleep
from enum import Enum

from script import Script 
from cached_property import cached_property

class PlotlineScene(Enum):
    PLOTLINE_SCENE_MAIN = 0
    PLOTLINE_SCENE_EXPLORATION = 1
    PLOTLINE_SCENE_SUMMON = 2
    PLOTLINE_SCENE_TEAM = 3
    PLOTLINE_SCENE_BATTLE = 4
    PLOTLINE_SCENE_PRIVILEGES = 5
    PLOTLINE_SCENE_UNKNOWN = 99

class ScriptTask(GameUi, PlotlineAssets,GeneralBattle):
    plotline_conf: Plotline = None
    privileges_flag: bool = False
    level_low: bool = False
    exploration_flag: bool = False
    experience_youkai_battle : bool = False
    system_shikigami_detect: bool = True
    plotline_shikigami_switch: bool = True
    exploration_shikigami_switch: bool = True
    _current_battle_type: str = 'plotline'  # 'plotline' or 'exploration'
    _solo_exploration = None
    mail_flag: bool = True
    page_main_timeout: int = 0
    unknow_cnt: int = 0
    plotline_main_flag=False

    def _reset_shikigami_switch_flags(self, switch_system_shikigami: bool):
        self.privileges_flag = not switch_system_shikigami
        # 任务启动时按 switch_system_shikigami 决定两类首次战斗是否需要借用式神上场
        self.plotline_shikigami_switch = switch_system_shikigami
        self.exploration_shikigami_switch = switch_system_shikigami

    def _enable_first_battle_shikigami_switch(self):
        # 成功开启借用后，剧情战斗和探索战斗各自的第一次战斗都需要切换借用式神
        self.privileges_flag = True
        self.plotline_shikigami_switch = True
        self.exploration_shikigami_switch = True

    def _wait_exploration_entrance_after_click(self):
        wait_timer = Timer(8).start()
        while not wait_timer.reached():
            sleep(0.5)
            self.screenshot()
            # 前往探索动画会短暂出现探索大世界标识，必须等最终章节入口出现再交给 Exploration。
            if self.appear(ExplorationAssets.I_E_ENTRANCE) and self.appear(ExplorationAssets.I_E_EXPLORATION_CLICK):
                logger.info("前往探索动画结束，已进入章节入口")
                return True
        logger.warning("前往探索后未等到章节入口，交由场景识别继续处理")
        return False

    def run(self):
        self.plotline_conf = self.config.plotline
        self._reset_shikigami_switch_flags(self.plotline_conf.plotline_config.switch_system_shikigami)
        self.experience_youkai_battle = self.plotline_conf.plotline_config.experience_youkai_battle
        logger.info(f'Start plotline{self.privileges_flag}')
        """ while True:
            self.click_dialogue()
            #self.run_page_summon() """
        while 1:
            try:
                self.screenshot()
                current_scene = self.get_current_scene()
                if current_scene == PlotlineScene.PLOTLINE_SCENE_UNKNOWN:
                   self.unknow_cnt+=1
                else:
                    self.unknow_cnt=0
                self.handle_scene(current_scene)
            except TaskEnd as e:
                self.set_next_run(task='Plotline',success=True)
                logger.info("任务结束")
                raise  e
            except GameStuckError as e:
                logger.error(f"等待超时: {e}")
                # 一分钟后再重启
                self.custom_next_run(task='Plotline', custom_time=(datetime.now() + timedelta(minutes=1)), time_delta=0)
                self.config.task_call('Restart')
                raise e
            except Exception as e:
                self.set_next_run(task='Plotline',success=False)
                raise e
            
                

    def get_current_scene(self) -> PlotlineScene:
        """ 获取当前场景，轮询最多10秒后如果仍未识别到有效场景则返回UNKNOWN """
        import time
        start_time = time.time()
        
        while time.time() - start_time < 5:
            
            # 检查各个场景，按优先级排序
            if self.click_dialogue_high():
                start_time=time.time()
                continue
            elif self.appear(self.I_PAGE_MAIN, interval=1):
                return PlotlineScene.PLOTLINE_SCENE_MAIN
            elif self.appear(self.I_PAGE_EXP_BATTLE, interval=1):
                return PlotlineScene.PLOTLINE_SCENE_TEAM
            elif  self.appear(ExplorationAssets.I_NORMAL_BATTLE_BUTTON, interval=1) or\
                  self.appear(ExplorationAssets.I_BOSS_BATTLE_BUTTON, interval=1) or \
                  self.appear(self.I_CLICK_BATTLE, interval=1) or \
                  self.appear(self.I_CLICK_TO_AUTO, interval=1) or \
                  self.appear(self.I_PREPARE_HIGHLIGHT, interval=0.8):
                return PlotlineScene.PLOTLINE_SCENE_BATTLE
            elif  self.exploration_flag and ((self.appear(ExplorationAssets.I_E_EXPLORATION_CLICK) or\
                 self.appear(self.I_CHECK_EXPLORATION))):
                return PlotlineScene.PLOTLINE_SCENE_EXPLORATION
            elif self.appear(self.I_PAGE_PRIVILEGES, interval=1):
                return PlotlineScene.PLOTLINE_SCENE_PRIVILEGES
            elif self.appear_rgb(self.I_PAGE_SUMMON) or self.appear(self.I_PAGE_SUMMON_2):
                return PlotlineScene.PLOTLINE_SCENE_SUMMON
            elif self.click_dialogue_low():
                start_time=time.time()
                continue 
            
        return PlotlineScene.PLOTLINE_SCENE_UNKNOWN

    def handle_scene(self, scene: PlotlineScene) -> None:
        """ 根据当前场景执行对应的操作 """
        scene_handlers = {
            PlotlineScene.PLOTLINE_SCENE_MAIN: self.handle_main_scene,
            PlotlineScene.PLOTLINE_SCENE_EXPLORATION: self.handle_exploration_scene,
            PlotlineScene.PLOTLINE_SCENE_SUMMON: self.handle_summon_scene,
            PlotlineScene.PLOTLINE_SCENE_TEAM: self.handle_team_scene,
            PlotlineScene.PLOTLINE_SCENE_BATTLE: self.handle_battle_scene,
            PlotlineScene.PLOTLINE_SCENE_PRIVILEGES: self.handle_privileges_scene,
            PlotlineScene.PLOTLINE_SCENE_UNKNOWN: self.handle_unknown_scene
        }

        handler = scene_handlers.get(scene, self.handle_unknown_scene)
        handler()

    def appear_then_click_leftmost(self,
                                   target: RuleImage,
                                   interval: float = None,
                                   threshold: float = None,
                                   nms_threshold: float = 0.3) -> bool:
        """
        识别 target 的所有匹配项，如果有多个则只点击最左边（x 最小）的那个。
        用于同一界面可能出现多个相同图标、需要按从左到右顺序处理的场景。
        :param target: RuleImage 对象
        :param interval: 点击间隔，复用 appear 相同的计时器逻辑，避免过于频繁地点击
        :param threshold: 匹配阈值，不传则使用 target 自身的阈值
        :param nms_threshold: NMS 去重阈值，用于剔除重叠的冗余匹配框
        :return: 成功识别并点击返回 True，否则 False
        """
        # 复用 interval 计时器逻辑，未到间隔时间直接返回
        if interval:
            if target.name in self.interval_timer:
                if self.interval_timer[target.name].limit != interval:
                    self.interval_timer[target.name] = Timer(interval)
            else:
                self.interval_timer[target.name] = Timer(interval)
            if not self.interval_timer[target.name].reached():
                return False

        # match_all_any 返回 (score, x, y, w, h) 列表，已通过 NMS 去除重叠框
        matches = target.match_all_any(self.device.image, threshold=threshold, nms_threshold=nms_threshold)
        if not matches:
            return False

        # 按 x 升序取最左边的匹配项，点击其中心坐标
        score, x, y, w, h = min(matches, key=lambda m: m[1])
        click_x = int(x + w // 2)
        click_y = int(y + h // 2)
        self.device.click(click_x, click_y, control_name=target.name)

        if interval:
            self.interval_timer[target.name].reset()
        return True

    def change_main_scene(self):
        "切换庭院场景"
        def change_main_scene():
            while 1:
                self.screenshot()
                if self.appear(self.I_FLAG_NOT_USE):
                    break
                if self.appear_then_click(self.I_MAIN_SELECT,interval=1):
                    continue
                if self.appear_then_click(self.I_TO_MAIN_CHANGE,interval=1):
                    continue
                if self.appear_then_click(self.I_TO_MAIN_CHANGE,interval=1):
                    continue
                if self.appear_then_click(self.I_TO_COLLET_2,interval=1):
                    continue
                # I_TO_COLLET 可能同时匹配到多个，只点击最左边的那个
                if self.appear_then_click_leftmost(self.I_TO_COLLET,interval=1):
                    continue
            while 1:
                self.screenshot()
                if self.appear(self.I_FLAG_USED):
                    break
                if self.appear_then_click(self.I_FLAG_NOT_USE,interval=1):
                    continue
            while 1:
                self.screenshot()
                if self.appear(page_main.check_button):
                    break
                if self.appear_then_click(self.I_UI_BACK_YELLOW,interval=1):
                    continue
        if self.appear(self.I_PLOTLINE_OLD_MAIN_CHECK):
            return True
        if self.appear(self.I_PLOTLINE_NEW_MAIN_CHECK) and (self.get_character_level_with_multiple_attempts() >= 7):
            while 1:
                self.screenshot()
                if self.appear(self.I_TO_COLLET):
                    change_main_scene()
                    return True
                if self.appear_then_click(RestartAssets.I_LOGIN_COURTYARD, action=RestartAssets.C_LOGIN_SCROLL_CLOSE_AREA,interval=2):
                    continue
                if self.appear_then_click(RestartAssets.I_LOGIN_COURTYARD2, action=RestartAssets.C_LOGIN_SCROLL_CLOSE_AREA,interval=2):
                    continue
                if self.ocr_appear_click(RestartAssets.O_LOGIN_COURTYARD, action=RestartAssets.C_LOGIN_SCROLL_CLOSE_AREA,interval=2):
                    continue
                if self.appear_then_click(RestartAssets.I_LOGIN_SCROOLL_CLOSE, action=RestartAssets.C_LOGIN_SCROLL_CLOSE_AREA,interval=2):
                    continue
        return False
        

            
    
    def handle_main_scene(self) -> None:
        """ 处理主界面场景 """
        def check_privileges():
            if not self.privileges_flag and (self.get_character_level_with_multiple_attempts() >= 7):
                self.level_low=False
                while time.time()-start_time<3:
                    self.screenshot()
                    if self.appear_then_click(self.I_CLICK_TO_PRIVILEGES,interval=0.7):
                        return True
        def check_experience_youkai_battle():
            if self.experience_youkai_battle and (self.get_character_level_with_multiple_attempts() >= 15):
                self.screenshot()
                self.ui_goto(page_main)
                self.screenshot()
                """ if  self.appear(RestartAssets.I_LOGIN_COURTYARD, interval=0.2) or \
                    self.appear(RestartAssets.I_LOGIN_COURTYARD2, interval=0.2) or\
                    self.ocr_appear(RestartAssets.O_LOGIN_COURTYARD, interval=0.2) or\
                    self.appear(RestartAssets.I_LOGIN_SCROOLL_CLOSE, interval=0.2):
                    if self.click(RestartAssets.C_LOGIN_SCROLL_CLOSE_AREA, interval=2):
                        logger.info('Click scroll close area because courtyard appears')
                        self.screenshot()  # 点击后立即获取最新截图，确保后续状态检查准确
                        return True """
                if self.mail_flag:
                    from tasks.DailyAltAcc.script_task import ScriptTask as DailyAltAccScriptTask
                    daily_alt_acc_task=DailyAltAccScriptTask(self.config, self.device)
                    if  daily_alt_acc_task.harvest_mail():
                        self.mail_flag=False
                    sleep(1)
                if self.ui_get_current_page()!=page_main:
                    self.ui_goto(page_main)
                else :
                    self.ui_goto(page_exploration)
                    self.screenshot()
                    self.ui_get_current_page()
                    self.ui_goto(page_team)
                self.screenshot()
                start_time = time.time()
                while time.time()-start_time<3:
                    self.screenshot()
                    if self.appear(self.I_PAGE_CLICK_ANY2,interval=0.7) or \
                        self.appear(self.I_CLICK_CURSOR,interval=0.7):
                        return True
                # 调用经验妖怪任务
                from tasks.ExperienceYoukai.script_task import ScriptTask as ExperienceYoukaiScriptTask
                # 屏蔽 ExperienceYoukai 的调度副作用：其 experience_exit() 会写死
                # set_next_run('ExperienceYoukai')，剧情任务内部借跑一次不应改动
                # 该单账号任务自身的下次运行时间。
                experience_youkai_task = shield_scheduling(
                    ExperienceYoukaiScriptTask, ('ExperienceYoukai',), 'Plotline'
                )(self.config, self.device)
                try:
                    self.screenshot()
                    if self.ui_get_current_page()!=page_main:
                        self.ui_goto(page_main)
                    experience_youkai_task.run()                
                except TaskEnd as e:
                    self.experience_youkai_battle=False
                    logger.info("任务结束")
                except Exception as e:
                    logger.error(f"经验妖怪任务执行异常: {e}")
                    # 继续执行剧情任务
                    
        logger.info("当前在主线剧情主界面场景")
        import time
        start_time = time.time()
        if check_privileges():
            return
        if not self.plotline_main_flag and self.change_main_scene():
            self.plotline_main_flag = True
        if check_experience_youkai_battle():
            return
        start_time = time.time()
        while time.time()-start_time<3:
            #logger.info(f"{start_time}")
            self.screenshot()
            self.device.click_record_clear()
            if  self.appear(RestartAssets.I_LOGIN_COURTYARD, interval=0.2) or \
                self.appear(RestartAssets.I_LOGIN_COURTYARD2, interval=0.2) or\
                self.ocr_appear(RestartAssets.O_LOGIN_COURTYARD, interval=0.2) or\
                self.appear(RestartAssets.I_LOGIN_SCROOLL_CLOSE, interval=0.2):
                if self.click(RestartAssets.C_LOGIN_SCROLL_CLOSE_AREA, interval=2):
                    logger.info('Click scroll close area because courtyard appears')
                    self.screenshot()  # 点击后立即获取最新截图，确保后续状态检查准确
                    self.page_main_timeout=0
                    return
            if self.appear_then_click(self.I_CLICK_LV,interval=1):
                self.exploration_flag =True
                logger.info("等级不够")
                start_time = time.time()
                self.page_main_timeout=0
                continue
            if self.appear(self.I_PAGE_MAIN) and self.appear_then_click(self.I_CLICK_DIALOGUE_1,interval=1):
                self.page_main_timeout=0
                return 
            if self.appear_then_click(self.I_CLICK_TO_EXPLORATION, interval=1):
                logger.info("点击前往探索按钮")
                self.exploration_flag = True
                # 前往探索会直接加载到章节入口，等待动画结束后再进入 Exploration 逻辑。
                self._wait_exploration_entrance_after_click()
                self.page_main_timeout=0
                return
        self.page_main_timeout+=1
        logger.info("TO I_PAGE_COLLET%02d", self.page_main_timeout)
        if self.page_main_timeout%3==0:
            logger.info("TO page_main_timeout")
            self.appear_then_click(self.I_PAGE_COLLET,interval=1)
            logger.info("TO I_PAGE_COLLET2")
            sleep(1)
            if self.page_main_timeout >9:
                raise TaskEnd(Plotline)


            
    def get_character_level_with_multiple_attempts(self) -> int:
        """ 对角色等级进行多次识别并返回最大值 """
        import time
        levels = []
        
        for i in range(3):
            # 截图并识别等级
            self.screenshot()
            try:
                level = self.O_CHARACTER_LEVEL.ocr(self.device.image)
                levels.append(level)
                logger.info(f"第{i+1}次等级识别结果: {level}")
            except Exception as e:
                logger.warning(f"第{i+1}次等级识别失败: {e}")
            # 短暂延迟，避免识别过于频繁
            time.sleep(0.2)
        
        # 返回识别结果中的最大值
        if levels:
            max_level = max(levels)
            logger.info(f"三次识别中最高等级: {max_level}")
            return max_level
        else:
            # 如果识别失败，返回默认值0
            logger.warning("等级识别全部失败，返回默认值0")
            return 0

    def handle_exploration_scene(self) -> None:
        """ 处理探索场景 """
        logger.info("当前在探索场景，启动Exploration的run_solo方法")
        self._current_battle_type = 'exploration'
        # 创建SoloExploration实例
        solo_exploration = SoloExploration(self.config, self.device)
        self._solo_exploration = solo_exploration
        
        solo_exploration.config.model.exploration.exploration_config.exploration_level = ExplorationLevel.AUTO
        logger.info("设置探索章节为: AUTO")
        
        need_switch_exploration_shikigami = self.privileges_flag and self.exploration_shikigami_switch
        # 探索首战需要切换借用式神时，先临时解锁阵容，切换完成后再恢复锁定。
        solo_exploration._config.general_battle_config.lock_team_enable = (
            self.plotline_conf.plotline_config.exploration_battle_lock and not need_switch_exploration_shikigami
        )
        if need_switch_exploration_shikigami and self.plotline_conf.plotline_config.exploration_battle_lock:
            logger.info("探索首战需要切换借用式神，战前临时解除阵容锁定")
        solo_exploration._config.exploration_config.minions_cnt=5
        solo_exploration._config.exploration_config.limit_time = dtime(0, 10, 0)  # 10分钟上限兜底
        solo_exploration._config.exploration_config.up_type=UpType.ALL
        solo_exploration._config.scrolls.scrolls_enable=False
        logger.info(f"探索队伍锁定配置: {solo_exploration._config.general_battle_config.lock_team_enable}")
        
        # 临时替换battle_wait和battle_before方法为当前类的实现
        original_battle_wait = solo_exploration.battle_wait
        original_battle_before = solo_exploration.battle_before
        solo_exploration.battle_wait = self.battle_wait
        solo_exploration.battle_before = self.battle_before
        
        try:
            # 运行探索
            solo_exploration.run_solo()
            self.plotline_conf.plotline_config.exploration_battle_lock=self.privileges_flag
        except Exception as e:
            self.config.notifier.push(content=f'探索任务异常{e}', title='Plotline')
            start_time = time.time()
            while time.time()-start_time < 5:
                self.screenshot()
                if self.appear_then_click(self.I_CLICK_BACK_RED,interval=2):
                    start_time = time.time()
                    continue
                if self.appear_then_click(self.I_UI_BACK_YELLOW,interval=2):
                    start_time = time.time()
                    continue
            logger.info(f'探索任务异常{e.args[0]}')
            if e.args[0] == 'Insufficient AP' and self.mail_flag:
                self.screenshot()
                if self.appear(self.I_PAGE_MAIN) :
                    if self.get_character_level_with_multiple_attempts() >= 15:
                        self.screenshot()
                        self.ui_goto(page_main)
                        self.screenshot()
                    from tasks.DailyAltAcc.script_task import ScriptTask as DailyAltAccScriptTask
                    daily_alt_acc_task=DailyAltAccScriptTask(self.config, self.device)
                    daily_alt_acc_task.harvest_mail()
                    self.mail_flag=False
                    sleep(1)
                    self.screenshot()
            else:
                raise TaskEnd
        finally:
            # 恢复原始的battle_wait和battle_before方法
            solo_exploration.battle_wait = original_battle_wait
            solo_exploration.battle_before = original_battle_before
            self.exploration_flag = False
            self.exploration_shikigami_switch = False
            self._solo_exploration = None
            start_time = time.time()
            while time.time()-start_time < 5:
                self.screenshot()
                if self.appear_then_click(self.I_CLICK_BACK_RED,interval=2):
                    start_time = time.time()
                    continue
                if self.appear_then_click(self.I_UI_BACK_YELLOW,interval=2):
                    start_time = time.time()
                    continue
        


    def handle_summon_scene(self) -> None:
        """ 处理召唤场景 """
        logger.info("当前在召唤场景")
        self.run_page_summon()

    def handle_team_scene(self) -> None:
        """ 处理组队场景 """
        logger.info("当前在组队场景")
        self.screenshot()
        start_time = time.time()
        while time.time()-start_time<5:
            self.screenshot()
            if self.appear(self.I_CLICK_CURSOR, interval=1):
                current_image=self.device.image
                click_cursor=self.I_CLICK_CURSOR.match_all_any(current_image)
                1125,294
                1083,213
                if len(click_cursor) ==1:
                    self.C_CLICK_CURSOR.roi_back=(click_cursor[0][1]-5,click_cursor[0][2]-5,20,20)
                    self.C_CLICK_CURSOR.roi_front=(click_cursor[0][1]-5,click_cursor[0][2]-5,20,20)
                    self.click(self.C_CLICK_CURSOR)
                start_time=time.time()
                continue
            if self.appear_then_click(self.I_PAGE_CLICK_ANY2, interval=1):
                start_time=time.time()
                continue
            if self.appear_then_click(self.I_PREPARE_HIGHLIGHT, interval=0.8):
                start_time=time.time()
                continue
            if self.appear_then_click(ExperienceYoukaiAssets.I_EXP_WIN, interval=1):
                start_time=time.time()
                continue
            if self.appear(self.I_PAGE_EXP_BATTLE):
                start_time=time.time()
                continue
            if self.appear(self.I_PAGE_MAIN):
                break

    def handle_battle_scene(self) -> None:
        """ 处理战斗场景 """
        logger.info("当前在战斗场景")
        self._current_battle_type = 'plotline'
        start_time = time.time()
        while time.time()-start_time<5:
            self.screenshot()
            if self.appear(self.I_CLICK_TO_AUTO, interval=1) or self.appear(self.I_PREPARE_HIGHLIGHT, interval=0.8):
                break
            if self.appear_then_click(ExplorationAssets.I_NORMAL_BATTLE_BUTTON, interval=1) \
                or self.appear_then_click(ExplorationAssets.I_BOSS_BATTLE_BUTTON,interval=1)\
                or self.appear_then_click(self.I_CLICK_BATTLE,interval=1):
                start_time=time.time()
        if time.time()-start_time>=5:
            return
        self.screenshot()
        self.run_general_battle()
        self.plotline_shikigami_switch = False

    def handle_privileges_scene(self) -> None:
        """ 处理特权/选项场景 """
        logger.info("当前在特权/选项场景")
        start_time=time.time()
        while time.time()-start_time<5:
            self.screenshot()
            if (self.privileges_flag == True or self.level_low ==True) and self.appear_then_click(self.I_UI_BACK_YELLOW, interval=3):
                logger.info("点击黄色返回按钮")
                break
            if not self.privileges_flag:
                if self.appear(self.I_FLAG_LEASE):
                    self._enable_first_battle_shikigami_switch()
                    continue
                if self.appear(self.I_PAGE_PRIVILEGES_2):
                    self.level_low = True
                    while time.time()-start_time<5:
                        self.screenshot()
                        if self.appear_then_click(self.I_CLICK_PRIVILEGES_SUBPAGE_2, interval=1):
                            logger.info("点击式神借用页签")
                            # 点击页签只是进入借用页面，必须识别到 I_FLAG_LEASE 才算借用成功
                            self.level_low = False
                            start_time=time.time()
                            break
                    start_time=time.time()
                    continue    
                if self.appear_then_click(self.I_PAGE_CLICK_ANY, interval=1) or self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                    start_time=time.time()
                    continue
                if self.appear_then_click(self.I_CLICK_PRIVILEGES_SUBPAGE, interval=1) :
                    logger.info("点击特权按钮")
                    start_time=time.time()
                    continue
                if self.appear_then_click(self.I_SUBPAGE_PRIVILEGES, action=self.C_CLICK_LEASE, interval=1):
                    logger.info("点击租借按钮")
                    start_time=time.time()
                    continue
            
    def handle_unknown_scene(self) -> None:
        """ 处理未知场景 """
        logger.info("当前场景未知，尝试返回主界面")
        self.screenshot()
        # 尝试返回主界面
        if not self.appear(ExplorationAssets.I_E_EXPLORATION_CLICK) and self.appear_then_click(self.I_CLICK_BACK_RED, interval=3):
                logger.info("点击红色返回按钮")
        if self.appear_then_click(self.I_UI_BACK_YELLOW, interval=3):
            logger.info("点击黄色返回按钮")
        if self.appear(self.I_BACK_BATTLE, interval=3):
            if self.unknow_cnt>=5 :
                self.appear_then_click(self.I_BACK_BATTLE, interval=3)
            else :
                self.click(self.C_CLICK_FIND_FLAG)
                sleep(3)


    def run_page_summon(self):
        
        start_time=time.time()
        while time.time()-start_time<20:
            self.screenshot()
            if self.appear_then_click(self.I_SUMMON_GOTO_MAIN, interval=3):
                break
            if self.appear_then_click(self.I_CLICK_SUMMON, interval=1):
                start_time=time.time()-15
                logger.info("点击确认按钮")
                continue
            if self.appear_then_click(self.I_PAGE_CLICK_ANY, interval=1):
                start_time=time.time()-15
                continue
            if self.appear(self.I_PAGE_SUMMON, interval=3):
                self.swipe(self.S_SWIPE_SUMMON)
                start_time=time.time()
                continue
    def click_dialogue_low(self):  
        self.screenshot()
        self.device.click_record_clear()
        if self.appear_then_click(self.I_CLICK_DIALOGUE_1, interval=1):
            pass    
        elif self.appear_then_click(self.I_CLICK_LV,interval=1):    
            self.exploration_flag =True   
        elif not self.appear(GameUiAssets.I_CHECK_TEAM) \
                and not self.appear(self.I_CHECK_EXPLORATION) \
                and self.appear_then_click(self.I_UI_BACK_YELLOW, interval=3):
            pass
        else:
            return False
        return True
                
                      
    def click_dialogue_high(self):
        import time
        action_click = random.choice([self.C_REWARD_1, self.C_REWARD_2, self.C_REWARD_3])
        start_time=time.time()
        self.screenshot()
        self.device.click_record_clear()
        if self.appear(self.I_CLICK_SPEED_X1, interval=1):
            pass
        elif self.appear(self.I_CLICK_CURSOR, interval=1):
            current_image=self.device.image
            click_cursor=self.I_CLICK_CURSOR.match_all_any(current_image)
            if len(click_cursor) ==1:
                self.C_CLICK_CURSOR.roi_back=(click_cursor[0][1]-5,click_cursor[0][2]-5,20,20)
                self.C_CLICK_CURSOR.roi_front=(click_cursor[0][1]-5,click_cursor[0][2]-5,20,20)
                self.click(self.C_CLICK_CURSOR)
        elif self.appear_then_click(self.I_PAGE_CLICK_ANY2, interval=1):
            pass
        elif self.appear_then_click(ExperienceYoukaiAssets.I_EXP_WIN, interval=1):
            pass
        elif self.appear_then_click(self.I_CLICK_EYE, interval=1) or \
            self.appear_then_click(self.I_CLICK_EYE_2, interval=1):
            pass
        elif self.appear_then_click(self.I_CLICK_JUMP, interval=1):
            pass
        elif self.appear_then_click(self.I_CLICK_JUMP2, interval=1):
            pass
        elif self.appear_then_click(self.I_CLICK_SPEED_X2, interval=1):
            pass
        elif self.appear_then_click(self.I_PAGE_CLICK_ANY, interval=1):
            pass
        elif self.appear_then_click(self.I_CLICK_DIALOGUE_2,interval=1.5):
            pass
        elif self.privileges_flag and not self.experience_youkai_battle and not self.mail_flag and self.appear_then_click(self.I_CLICK_DIALOGUE_1, interval=1):
            pass    
        elif self.privileges_flag and not self.experience_youkai_battle and not self.mail_flag and self.appear_then_click(self.I_CLICK_LV,interval=1):    
            self.exploration_flag =True 
        elif self.appear_then_click(self.I_CLICK_CV, interval=1):
            pass
        elif not self.appear(ExplorationAssets.I_E_EXPLORATION_CLICK) and self.appear_then_click(self.I_CLICK_BACK_RED, interval=3):
            pass
        elif self.appear(self.I_PAGE_SKIP, interval=1):
            start_time=time.time()
            while time.time()-start_time<3:
                self.screenshot()
                if self.appear(self.I_CHECK_TICK, interval=1)and self.appear_then_click(self.I_PAGE_SKIP, interval=1):
                    self.config.notifier.push(content=f'已跳过剧情', title='Plotline')
                    raise TaskEnd
                if self.appear_then_click(self.I_CHECK_UNTICK, interval=1):
                    start_time=time.time()
                    continue
        # 绑定手机号弹窗
        elif self.appear_then_click(RestartAssets.I_LOGIN_LOGIN_GOTO_BIND_PHONE, interval=1):
            start_time=time.time()
            while time.time() - start_time<3:
                self.screenshot()
                if self.appear_then_click(RestartAssets.I_LOGIN_LOGIN_CANCEL_BIND_PHONE):
                    logger.info("Close bind phone")
                    break
        elif self.appear_then_click(self.I_CLICK_REFUSE, interval=5):
            pass
        elif self.appear(self.I_WIN, threshold=0.8) or self.appear(self.I_DE_WIN):
                logger.info("Battle result is win")
        elif (self.appear_then_click(self.I_REWARD, action=action_click, interval=1.5) or
            self.appear_then_click(self.I_REWARD_GOLD, action=action_click, interval=1.5)
            ):
            pass
        else:
            return False
        return True

    from tasks.Component.GeneralBuff.config_buff import BuffClass
    def reward_click_actions(self):
        if self._current_battle_type == 'exploration':
            # Exploration 战斗结算禁用 reward_1（异常区域）；左侧区域不符合人类点击习惯，同样禁用。
            return [self.C_REWARD_3]
        return super().reward_click_actions()

    def _need_switch_shikigami(self) -> bool:
        """判断当前场景是否需要切换借用式神"""
        if self._current_battle_type == 'plotline':
            return self.plotline_shikigami_switch
        elif self._current_battle_type == 'exploration':
            return self.exploration_shikigami_switch
        return False

    def _mark_shikigami_switched(self):
        """标记当前场景的借用式神切换已完成"""
        if self._current_battle_type == 'plotline':
            self.plotline_shikigami_switch = False
        elif self._current_battle_type == 'exploration':
            self.exploration_shikigami_switch = False
            # 借用式神切换成功后，后续探索战斗按锁定阵容处理。
            if self.privileges_flag and self._solo_exploration is not None:
                self._solo_exploration._config.general_battle_config.lock_team_enable = True
                logger.info("借用式神切换成功，后续探索队伍保持锁定")
        self.system_shikigami_detect = False

    def battle_before(self, buff: BuffClass | list[BuffClass], config: GeneralBattleConfig, timeout: float = 10) -> bool:
        """战斗前设置
        :return: True:进入战斗或点击了准备按钮且识别不到准备按钮了 False:超过timeout s还没有进入战斗且没有点击过准备
        """
        timeout_timer = Timer(timeout).start()
        confed = False
        while not timeout_timer.reached():
            self.screenshot()
            if self.is_in_real_battle(False) or self.appear_rgb(self.I_CLICK_ATTACK) or self.appear_rgb(self.I_CLICK_ATTACK1) or self.appear_rgb(self.I_CLICK_ATTACK2) or self.appear_rgb(self.I_CLICK_ATTACK3) or self.appear_rgb(self.I_CLICK_SKILL):  # 战斗阶段
                return True
            if self.appear_then_click(self.I_DISABLE_7DAYS_DIFF_SOUL, interval=0.6):  # 关闭御魂不一致提示
                continue
            if self.appear_then_click(self.I_CONFIRM_CLOSE_DIFF_SOUL, interval=0.6):  # 确认关闭御魂不一致提示
                continue
            if self.is_in_prepare(False):  # 战斗准备阶段
                timeout_timer.reset()
                if not getattr(config, 'lock_team_enable', False):  # 没有锁定阵容
                    if self.current_count == 1 and not confed:  # 第一次战斗且是本次第一次配置
                        self.switch_preset_team(config.preset_enable, config.preset_group, config.preset_team)
                        self.check_and_open_buff(buff)
                        confed = True
                # 点击准备(锁定阵容自动点准备,不锁定阵容前面也已经配置完毕需要点准备)
                if self.appear(self.I_PAGE_EXP_BATTLE, interval=0.8):
                    pass
                elif self._need_switch_shikigami() and self.privileges_flag : 
                    if not self.appear(self.I_FLAG_CHANGE) :
                        self.click(self.C_CLICK_CHANGE)
                        sleep(2)
                        continue
                    else:

                        raw_matches =self.I_FLAG_HELP2.match_all_any(self.device.image)
                        # 直接从匹配结果中提取坐标信息并按y坐标排序
                        logger.info(f"raw_matches{raw_matches}")
                        bounty_list = sorted(
                            [[x, y, w, h] for (sc, x, y, w, h) in raw_matches],
                            key=lambda item: item[1]  # 按y坐标排序
                        )
                        logger.info(f"bounty_list{bounty_list}len(bounty_list){len(bounty_list)}")
                        if len(bounty_list)>0:
                            self.I_FLAG_ON_FIELD.roi_back=(bounty_list[0][0]+43,bounty_list[0][1]-24,79,154)
                            if not self.appear(self.I_FLAG_ON_FIELD):
                                self.S_SWIPE_SHIKIGAMI.roi_front=(bounty_list[0][0],bounty_list[0][1],bounty_list[0][2],bounty_list[0][3])
                                self.swipe(self.S_SWIPE_SHIKIGAMI,4)
                                sleep(2)
                                continue
                
                if self.appear_then_click(self.I_PREPARE_HIGHLIGHT, interval=0.8):
                    self._mark_shikigami_switched()
                    continue
                continue
            # 未知界面, 既不是准备界面也不是战斗界面
            logger.info('Wait for preparation page')
            sleep(random.uniform(0.4, 0.8))
        return False                        
    def battle_wait(self, random_click_swipt_enable: bool) -> bool:
        """
        等待战斗结束 ！！！
        很重要 这个函数是原先写的， 优化版本在tasks/Secret/script_task下。本着不改动原先的代码的原则，所以就不改了
        :param random_click_swipt_enable:
        :return:
        """
        # 有的时候是长战斗，需要在设置stuck检测为长战斗
        # 但是无需取消设置，因为如果有点击或者滑动的话 handle_control_check会自行取消掉
        self.device.stuck_record_add('BATTLE_STATUS_S')
        self.device.click_record_clear()
        # 战斗过程 随机点击和滑动 防封
        logger.info("Start battle process")
        win: bool = False
        retry_cnt: int = 0
        attack_flag: bool = True
        while 1:
            self.screenshot()
            if retry_cnt >3:
                logger.info("手动战斗")
                break 
            if self.appear(self.I_CLICK_AUTO, interval=1):
                attack_flag = False
                logger.info("自动战斗")
                break 
            if self.appear_then_click(self.I_CLICK_BATTLE_SPEED_X1, interval=1):
                pass
            if self.appear_then_click(self.I_CLICK_TO_AUTO, interval=1):
                retry_cnt +=1
                continue
            
        click_timer = Timer(5)
        swipe_timer = Timer(10)
        attack_click_timer = Timer(0.5)
        while 1:
            self.screenshot()
            self.device.click_record_clear()
            if attack_flag :
                if  click_timer.reached():
                    click_timer.reset()
                    self.click(self.C_CLICK_RANDOM_3)
                    self.click(self.C_CLICK_RANDOM_2)
                    self.click(self.C_CLICK_RANDOM_1)
                    sleep(0.5)
                    continue
                if  swipe_timer.reached():
                    swipe_timer.reset()
                    swipe_list = [self.S_SWIPE_BATTLE,\
                                self.S_SWIPE_BATTLE2,\
                                RuleSwipe(roi_front=self.S_SWIPE_BATTLE.roi_back, roi_back=self.S_SWIPE_BATTLE.roi_front, mode="default"),\
                                RuleSwipe(roi_front=self.S_SWIPE_BATTLE2.roi_back, roi_back=self.S_SWIPE_BATTLE2.roi_front, mode="default")]            
                    self.swipe(random.choice(list(swipe_list)),interval=5, duration=1.0) 
                    sleep(1.5)
                    continue
                # 四个攻击点击共用冷却，任意一个点击成功后都等待 1 秒再检查下一次攻击点击
                if attack_click_timer.reached():
                    attack_clicked = False
                    for attack_button in (
                        self.I_CLICK_ATTACK,
                        self.I_CLICK_ATTACK1,
                        self.I_CLICK_ATTACK2,
                        self.I_CLICK_ATTACK3,
                        
                    ):
                        if self.appear_then_click(attack_button):
                            attack_click_timer.reset()
                            attack_clicked = True
                            break
                    if attack_clicked:
                        continue
                if  self.appear_then_click(self.I_CLICK_SKILL, interval=1):
                    continue
            # 如果出现赢 就点击, 第二个是针对封魔的图片
            if self.appear(self.I_WIN, threshold=0.8) or self.appear(self.I_DE_WIN):
                logger.info("Battle result is win")
                win = True
                break

            # 如果出现失败 就点击，返回False
            if self.appear(self.I_FALSE, threshold=0.8):
                logger.info("Battle result is false")
                win = False
                break

            # 如果领奖励
            if self.appear(self.I_REWARD, threshold=0.6):
                win = True
                break

            # 如果领奖励出现金币
            if self.appear(self.I_REWARD_GOLD, threshold=0.8):
                win = True
                break
            # 如果开启战斗过程随机滑动
            if self.appear(self.I_CLICK_DIALOGUE_2,interval=1.5)\
                or self.appear(self.I_CLICK_DIALOGUE_1,interval=1.5)\
                or self.appear(self.I_CLICK_DIALOGUE_1,interval=1.5)\
                or self.appear(self.I_CLICK_EYE,interval=1.5)\
                or self.appear(self.I_CLICK_EYE_2,interval=1.5):
                win = True
                return win

        # 再次确认战斗结果
        logger.info("Reconfirm the results of the battle")
        while 1:
            self.screenshot()
            # 如果开启战斗过程随机滑动
            if self.appear(self.I_CLICK_DIALOGUE_2,interval=1.5)\
                or self.appear(self.I_CLICK_DIALOGUE_1,interval=1.5)\
                or self.appear(self.I_CLICK_DIALOGUE_1,interval=1.5)\
                or self.appear(self.I_CLICK_EYE,interval=1.5)\
                or self.appear(self.I_CLICK_EYE_2,interval=1.5):
                win = True
                return win
            if win:
                # 点击赢了：固定右侧区域（上/左区域不符合人类点击习惯，已禁用）
                action_click = self.C_WIN_3
                if self.appear_then_click(self.I_WIN, action=action_click, interval=0.5):
                    continue
                if not self.appear(self.I_WIN):
                    break
            else:
                # 如果失败且 点击失败后
                if self.appear_then_click(self.I_FALSE, threshold=0.6):
                    continue
                if not self.appear(self.I_FALSE, threshold=0.6):
                    return False
        # 最后保证能点击 获得奖励
        if not self.wait_until_appear(self.I_REWARD):
            # 有些的战斗没有下面的奖励，所以直接返回
            logger.info("There is no reward, Exit battle")
            return win
        logger.info("Get reward")
        while 1:
            self.screenshot()
            # 如果出现领奖励
            if self.appear(self.I_CLICK_DIALOGUE_2,interval=1.5)\
                or self.appear(self.I_CLICK_DIALOGUE_1,interval=1.5)\
                or self.appear(self.I_CLICK_DIALOGUE_1,interval=1.5)\
                or self.appear(self.I_CLICK_EYE,interval=1.5)\
                or self.appear(self.I_CLICK_EYE_2,interval=1.5):
                win = True
                return win
            action_click = random.choice(self.reward_click_actions())
            if (self.appear_then_click(self.I_REWARD, action=action_click, interval=1.5) or
                self.appear_then_click(self.I_REWARD_GOLD, action=action_click, interval=1.5)
                ):
                continue
            if (not self.appear(self.I_REWARD) and not self.appear(self.I_REWARD_GOLD)):
                break

        return win            
    def swipe(self, swipe: RuleSwipe, interval: float = None, duration: float = 0.1) -> None:
        """

        :param interval:
        :param swipe:
        :param  duration
        :param  wait_up_time
        :return:
        """
        if not isinstance(swipe, RuleSwipe):
            return

        if interval:
            if swipe.name in self.interval_timer:
                # 如果传入的限制时间不一样，则替换限制新的传入的时间
                if self.interval_timer[swipe.name].limit != interval:
                    self.interval_timer[swipe.name] = Timer(interval)
            else:
                # 如果没有限制时间，则创建限制时间
                self.interval_timer[swipe.name] = Timer(interval)
            # 如果时间还没到达，则不执行
            if not self.interval_timer[swipe.name].reached():
                return

        x1, y1, x2, y2 = swipe.coord()
        self.device.swipe(p1=(x1, y1), p2=(x2, y2), control_name=swipe.name, duration=(duration, duration + 0.1))

        # 执行后，如果有限制时间，则重置限制时间
        if interval:
            # logger.info(f'Swipe {swipe.name}')
            self.interval_timer[swipe.name].reset()   

    


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    
    # from mypatch import SimplePatch

    # SimplePatch.patch()

    c = Config('QMUMU1')
    d = Device(c)
    self = ScriptTask(c, d)
    self.screenshot()
    #Script.save_error_log(t)
    #t.O_CHARACTER_LEVEL.ocr(t.device.image)
    #t.swipe(t.S_SWIPE_BATTLE, duration = 1 , interval=1)
    logger.info(self.config.config_name)
    #self.swipe(self.S_SWIPE_FIND_FLAG,duration=1)
    """  if self.appear(self.I_FLAG_ON_FIELD):
        logger.info('flag on field')
    else:
        logger.info('flag not on field') """
    self.run()
    #t.swipe(t.S_SWIPE_SHIKIGAMI,2)
    
     