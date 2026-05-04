import importlib
from datetime import datetime, timedelta, time as dtime
import os
import time
import random
import numpy as np
import cv2
from module.exception import TaskEnd, RequestHumanTakeover,GameNotRunningError, GameStuckError
from module.logger import logger
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
    def run(self):
        self.plotline_conf = self.config.plotline 
        self.privileges_flag=not self.plotline_conf.plotline_config.switch_system_shikigami
        self.plotline_shikigami_switch = not self.privileges_flag  # 需要借用时才需要切换
        self.exploration_shikigami_switch = not self.privileges_flag
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

    def handle_main_scene(self) -> None:
        """ 处理主界面场景 """
        logger.info("当前在主线剧情主界面场景")
        import time
        start_time = time.time()
        if not self.privileges_flag and (self.get_character_level_with_multiple_attempts() >= 7):
            self.level_low=False
            while time.time()-start_time<3:
                self.screenshot()
                if self.appear_then_click(self.I_CLICK_TO_PRIVILEGES,interval=0.7):
                    return
        if self.experience_youkai_battle and (self.get_character_level_with_multiple_attempts() >= 15):
            self.screenshot()
            if  self.appear(RestartAssets.I_LOGIN_COURTYARD, interval=0.2) or \
                self.appear(RestartAssets.I_LOGIN_COURTYARD2, interval=0.2) or\
                self.appear(RestartAssets.I_LOGIN_SCROOLL_CLOSE, interval=0.2):
                if self.click(RestartAssets.C_LOGIN_SCROLL_CLOSE_AREA, interval=2):
                    logger.info('Click scroll close area because courtyard appears')
                    self.screenshot()  # 点击后立即获取最新截图，确保后续状态检查准确
                    return
            if self.mail_flag:
                from tasks.DailyForFlag.script_task import ScriptTask as DailyForFlagScriptTask
                daily_for_flag_task=DailyForFlagScriptTask(self.config, self.device)
                if  daily_for_flag_task.harvest_mail():
                    self.mail_flag=False
                sleep(1)
            self.screenshot()
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
                    return
            # 调用经验妖怪任务
            from tasks.ExperienceYoukai.script_task import ScriptTask as ExperienceYoukaiScriptTask
            experience_youkai_task = ExperienceYoukaiScriptTask(self.config, self.device)
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
        start_time = time.time()
        while time.time()-start_time<3:
            #logger.info(f"{start_time}")
            self.screenshot()
            self.device.click_record_clear()
            if  self.appear(RestartAssets.I_LOGIN_COURTYARD, interval=0.2) or \
                self.appear(RestartAssets.I_LOGIN_COURTYARD2, interval=0.2) or\
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
                self.page_main_timeout=0
                return
        self.page_main_timeout+=1
        if self.page_main_timeout/5==0:
            self.click(self.C_CLICK_TIMEOUT)
            sleep(1)
            if self.page_main_timeout >15:
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
        
        # 获取当前最高的可探索章节
        max_available_chapter = self.find_max_available_chapter(solo_exploration)
        
        if max_available_chapter:
            # 更新配置中的探索章节
            solo_exploration.config.model.exploration.exploration_config.exploration_level = max_available_chapter
            logger.info(f"设置探索章节为: {max_available_chapter}")
        
        solo_exploration._config.general_battle_config.lock_team_enable = self.plotline_conf.plotline_config.exploration_battle_lock
        solo_exploration._config.exploration_config.minions_cnt=5
        solo_exploration._config.exploration_config.limit_time = dtime(0, 10, 0)  # 10分钟上限兜底
        solo_exploration._config.exploration_config.up_type=UpType.ALL
        solo_exploration._config.scrolls.scrolls_enable=False
        logger.info("已取消探索任务中的队伍锁定")
        
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
                if self.appear(self.I_PAGE_MAIN):
                    from tasks.DailyForFlag.script_task import ScriptTask as DailyForFlagScriptTask
                    daily_for_flag_task=DailyForFlagScriptTask(self.config, self.device)
                    daily_for_flag_task.harvest_mail()
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
        

    def find_max_available_chapter(self, solo_exploration):
        """ 查找当前可进行的最高章节 """
        # 定义章节列表，从最高到最低
        chapters = [
            ExplorationLevel.EXPLORATION_28, ExplorationLevel.EXPLORATION_27,
            ExplorationLevel.EXPLORATION_26, ExplorationLevel.EXPLORATION_25,
            ExplorationLevel.EXPLORATION_24, ExplorationLevel.EXPLORATION_23,
            ExplorationLevel.EXPLORATION_22, ExplorationLevel.EXPLORATION_21,
            ExplorationLevel.EXPLORATION_20, ExplorationLevel.EXPLORATION_19,
            ExplorationLevel.EXPLORATION_18, ExplorationLevel.EXPLORATION_17,
            ExplorationLevel.EXPLORATION_16, ExplorationLevel.EXPLORATION_15,
            ExplorationLevel.EXPLORATION_14, ExplorationLevel.EXPLORATION_13,
            ExplorationLevel.EXPLORATION_12, ExplorationLevel.EXPLORATION_11,
            ExplorationLevel.EXPLORATION_10, ExplorationLevel.EXPLORATION_9,
            ExplorationLevel.EXPLORATION_8, ExplorationLevel.EXPLORATION_7,
            ExplorationLevel.EXPLORATION_6, ExplorationLevel.EXPLORATION_5,
            ExplorationLevel.EXPLORATION_4, ExplorationLevel.EXPLORATION_3,
            ExplorationLevel.EXPLORATION_2, ExplorationLevel.EXPLORATION_1
        ]

        # 进入探索章节选择界面
        from tasks.GameUi.page import page_exploration
        solo_exploration.ui_get_current_page()
        solo_exploration.ui_goto(page_exploration)

        # 记录上次找到的最高章节
        previous_highest_chapter = None
        consecutive_same_count = 0  # 连续相同章节计数
        max_checks = 5  # 最大检测次数
        
        # 查找当前可访问的最高章节
        while consecutive_same_count < 2 and max_checks > 0:
            # 从当前界面开始，一次性获取所有可见章节
            current_highest_chapter = None
            
            # 多次截图以提高OCR准确性
            for attempt in range(3):
                solo_exploration.screenshot()
                # 检测当前屏幕上的所有章节文本
                results = solo_exploration.O_E_EXPLORATION_LEVEL_NUMBER.detect_and_ocr(solo_exploration.device.image)
                current_chapters = [result.ocr_text for result in results]
                
                # 检查当前屏幕中是否有我们在找的章节，并找出其中最高的
                for chapter in chapters:
                    if chapter.value in current_chapters:
                        # 发现当前屏幕中存在该章节，这就是当前可访问的最高章节
                        current_highest_chapter = chapter
                        break  # 找到最高章节后跳出循环
                
                if current_highest_chapter:
                    break
                sleep(0.5)  # 短暂等待后重试
            
            # 如果没有找到任何章节，稍微向下滑动再试
            if not current_highest_chapter:
                solo_exploration.swipe(solo_exploration.S_SWIPE_LEVEL_DOWN, interval=1)
                solo_exploration.screenshot()
                results = solo_exploration.O_E_EXPLORATION_LEVEL_NUMBER.detect_and_ocr(solo_exploration.device.image)
                current_chapters = [result.ocr_text for result in results]
                
                # 再次检查当前屏幕中是否有我们在找的章节，并找出其中最高的
                for chapter in chapters:
                    if chapter.value in current_chapters:
                        current_highest_chapter = chapter
                        break

            # 检查本次找到的最高章节是否与上次相同
            if previous_highest_chapter is not None and current_highest_chapter == previous_highest_chapter:
                consecutive_same_count += 1
                logger.info(f"连续第{consecutive_same_count}次检测到相同最高章节: {current_highest_chapter}")
            elif current_highest_chapter is not None:
                consecutive_same_count = 1  # 重置计数
                logger.info(f"本次检测到最高章节: {current_highest_chapter}")
            else:
                logger.info("本次未检测到任何可访问章节")
            
            previous_highest_chapter = current_highest_chapter
            max_checks -= 1
            
            # 如果连续两次检测到相同的章节，确认为最大章节
            if consecutive_same_count >= 2:
                logger.info(f"连续两次检测到相同章节，确认最大可访问章节为: {current_highest_chapter}")
                break

        # 如果没有找到任何可访问的章节，默认返回第一章
        if previous_highest_chapter is None:
            previous_highest_chapter = ExplorationLevel.EXPLORATION_1
            logger.warning("未能找到可进行的章节，默认使用第一章")
        else:
            logger.info(f"最终确定可进行的最高章节: {previous_highest_chapter}")
        
        # 确保返回到剧情界面 - 使用正确的返回按钮
        #solo_exploration.ui_click(solo_exploration.I_UI_BACK_YELLOW, stop=solo_exploration.I_CHECK_EXPLORATION, interval=1)
        
        return previous_highest_chapter

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
                if len(click_cursor) ==1:
                    self.C_CLICK_CURSOR.roi_back=(click_cursor[0][1]-43,click_cursor[0][2]-65,20,20)
                    self.C_CLICK_CURSOR.roi_front=(click_cursor[0][1]-43,click_cursor[0][2]-65,20,20)
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
                    self.privileges_flag = True
                    continue
                if self.appear(self.I_PAGE_PRIVILEGES_2):
                    self.level_low = True
                    while time.time()-start_time<5:
                        self.screenshot()
                        if self.appear_then_click(self.I_CLICK_PRIVILEGES_SUBPAGE_2, interval=1):
                            logger.info("点击式神借用按钮")
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
                self.C_CLICK_CURSOR.roi_back=(click_cursor[0][1]-43,click_cursor[0][2]-65,20,20)
                self.C_CLICK_CURSOR.roi_front=(click_cursor[0][1]-43,click_cursor[0][2]-65,20,20)
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
            # 切换式神成功后锁定探索队伍，防止后续战斗替换借用式神
            if self.privileges_flag and self._solo_exploration is not None:
                self._solo_exploration._config.general_battle_config.lock_team_enable = True
                logger.info("借用式神切换成功，锁定探索队伍")
        self.system_shikigami_detect = False

    def battle_before(self, buff: BuffClass | list[BuffClass], config: GeneralBattleConfig, timeout: float = 10) -> bool:
        """战斗前设置
        :return: True:进入战斗或点击了准备按钮且识别不到准备按钮了 False:超过timeout s还没有进入战斗且没有点击过准备
        """
        timeout_timer = Timer(timeout).start()
        confed = False
        while not timeout_timer.reached():
            self.screenshot()
            if self.is_in_real_battle(False) or self.appear_rgb(self.I_CLICK_ATTACK) or self.appear_rgb(self.I_CLICK_SKILL):  # 战斗阶段
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
                if  self.appear_then_click(self.I_CLICK_ATTACK, interval=0.3):
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
                # 点击赢了
                action_click = random.choice([self.C_WIN_1, self.C_WIN_2, self.C_WIN_3])
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
            action_click = random.choice([self.C_REWARD_1, self.C_REWARD_2, self.C_REWARD_3])
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

    c = Config('OAS2')
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
    
     