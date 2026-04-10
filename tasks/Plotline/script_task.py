import importlib
from datetime import datetime, timedelta
import os
import time
import random
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
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.GameUi.page import page_main
from module.base.timer import Timer
from time import sleep
from enum import Enum

from script import Script 
from cached_property import cached_property


class PlotlineScene(Enum):
    PLOTLINE_SCENE_MAIN = 0
    PLOTLINE_SCENE_EXPLORATION = 1
    PLOTLINE_SCENE_SUMMON = 2
    PLOTLINE_SCENE_MAP = 3
    PLOTLINE_SCENE_BATTLE = 4
    PLOTLINE_SCENE_PRIVILEGES = 5
    PLOTLINE_SCENE_UNKNOWN = 99

class ScriptTask(GameUi, PlotlineAssets,GeneralBattle):
    plotline_conf: Plotline = None
    privileges_flag: bool = False
    level_low: bool = False
    exploration_flag: bool = False
    def run(self):
        self.plotline_conf = self.config.plotline 
        logger.info('Start plotline')
        """ while True:
            self.click_dialogue()
            #self.run_page_summon() """
            
        while 1:
            try:
                self.screenshot()
                current_scene = self.get_current_scene()
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
        
        while time.time() - start_time < 10:
            
            # 检查各个场景，按优先级排序
            self.screenshot()
            if self.appear_rgb(self.I_PAGE_SUMMON) or self.appear(self.I_PAGE_SUMMON_2):
                return PlotlineScene.PLOTLINE_SCENE_SUMMON
            elif self.appear(ExplorationAssets.I_NORMAL_BATTLE_BUTTON, interval=1) or self.appear(ExplorationAssets.I_BOSS_BATTLE_BUTTON, interval=1) or self.appear(self.I_CLICK_TO_AUTO, interval=1) or self.appear(self.I_PREPARE_HIGHLIGHT, interval=0.8):
                return PlotlineScene.PLOTLINE_SCENE_BATTLE
            elif self.appear(self.I_PAGE_MAIN, interval=1):
                return PlotlineScene.PLOTLINE_SCENE_MAIN
            elif  self.exploration_flag and (self.appear(ExplorationAssets.I_E_EXPLORATION_CLICK, interval=1) or self.appear(GameUiAssets.I_CHECK_EXPLORATION, interval=1)):
                return PlotlineScene.PLOTLINE_SCENE_EXPLORATION
            elif self.appear(self.I_PAGE_PRIVILEGES, interval=1):
                return PlotlineScene.PLOTLINE_SCENE_PRIVILEGES
            # 小延时避免CPU占用过高
            self.screenshot()
            self.click_dialogue()
            time.sleep(0.1)
        return PlotlineScene.PLOTLINE_SCENE_UNKNOWN

    def handle_scene(self, scene: PlotlineScene) -> None:
        """ 根据当前场景执行对应的操作 """
        scene_handlers = {
            PlotlineScene.PLOTLINE_SCENE_MAIN: self.handle_main_scene,
            PlotlineScene.PLOTLINE_SCENE_EXPLORATION: self.handle_exploration_scene,
            PlotlineScene.PLOTLINE_SCENE_SUMMON: self.handle_summon_scene,
            PlotlineScene.PLOTLINE_SCENE_MAP: self.handle_map_scene,
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
        while time.time()-start_time<5:
            #logger.info(f"{start_time}")
            self.screenshot()
            self.device.click_record_clear()
            if self.appear(GameUiAssets.I_CHECK_EXPLORATION, interval=1) or self.appear(ExplorationAssets.I_E_EXPLORATION_CLICK):
                return
            if self.appear(self.I_PAGE_PRIVILEGES):
                return
            if not self.privileges_flag and self.appear_rgb(self.I_CLICK_TO_PRIVILEGES):
                if self.level_low == False:
                    self.appear_then_click(self.I_CLICK_TO_PRIVILEGES,interval=1)
                    logger.info("前往新手特权")
                    start_time = time.time()
                    continue
            if self.appear_rgb(self.I_CLICK_LV):
                if self.appear_then_click(self.I_CLICK_LV,interval=1):
                    self.exploration_flag =True
                logger.info("等级不够")
                start_time = time.time()
                continue
            if self.appear_then_click(self.I_CLICK_TO_EXPLORATION, interval=1):
                logger.info("点击前往探索按钮")
                start_time = time.time()
                continue
            if self.appear(self.I_CLICK_CURSOR, interval=1):
                current_image=self.device.image
                click_cursor=self.I_CLICK_CURSOR.match_all_any(current_image)
                if len(click_cursor) ==1:
                    self.C_CLICK_CURSOR.roi_back=(click_cursor[0][1]-43,click_cursor[0][2]-65,20,20)
                    self.C_CLICK_CURSOR.roi_front=(click_cursor[0][1]-43,click_cursor[0][2]-65,20,20)
                    self.click(self.C_CLICK_CURSOR)
                start_time=time.time()
                continue
        self.level_low = False
        self.screenshot()
        self.click_dialogue()


    def handle_exploration_scene(self) -> None:
        """ 处理探索场景 """
        logger.info("当前在探索场景，启动Exploration的run_solo方法")
        
        # 创建SoloExploration实例
        solo_exploration = SoloExploration(self.config, self.device)
        
        # 获取当前最高的可探索章节
        max_available_chapter = self.find_max_available_chapter(solo_exploration)
        
        if max_available_chapter:
            # 更新配置中的探索章节
            solo_exploration.config.model.exploration.exploration_config.exploration_level = max_available_chapter
            logger.info(f"设置探索章节为: {max_available_chapter}")
        
        # 设置为True表示执行解锁操作（即不锁定队伍）
        solo_exploration._config.general_battle_config.lock_team_enable = False
        solo_exploration._config.exploration_config.minions_cnt=15
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
            raise TaskEnd
        finally:
            # 恢复原始的battle_wait和battle_before方法
            solo_exploration.battle_wait = original_battle_wait
            solo_exploration.battle_before = original_battle_before
            self.exploration_flag = False
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

    def handle_map_scene(self) -> None:
        """ 处理地图场景 """
        logger.info("当前在地图场景")
        self.screenshot()

    def handle_battle_scene(self) -> None:
        """ 处理战斗场景 """
        logger.info("当前在战斗场景")
        start_time = time.time()
        while time.time()-start_time<5:
            self.screenshot()
            if self.appear(self.I_CLICK_TO_AUTO, interval=1) or self.appear(self.I_PREPARE_HIGHLIGHT, interval=0.8):
                break
            if self.appear_then_click(ExplorationAssets.I_NORMAL_BATTLE_BUTTON, interval=1) or self.appear_then_click(ExplorationAssets.I_BOSS_BATTLE_BUTTON,interval=1):
                start_time=time.time()
        self.screenshot()
        self.run_general_battle()

    def handle_privileges_scene(self) -> None:
        """ 处理特权/选项场景 """
        logger.info("当前在特权/选项场景")
        start_time=time.time()
        while time.time()-start_time<5:
            self.screenshot()
            if (self.privileges_flag == True or self.level_low ==True) and self.appear_then_click(self.I_UI_BACK_YELLOW, interval=3):
                logger.info("点击黄色返回按钮")
                break
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
            self.swipe(ExplorationAssets.S_SWIPE_BACKGROUND_LEFT)
            logger.info("滑动")


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
            
    def click_dialogue(self):
        import time
        start_time=time.time()
        while time.time()-start_time<3:
            self.screenshot()
            self.device.click_record_clear()
            if self.appear_then_click(self.I_PAGE_CLICK_ANY, interval=1):
                start_time=time.time()
                continue
            if self.appear_then_click(self.I_CLICK_DIALOGUE_2,interval=1.5):
                start_time=time.time()
                continue
            if self.appear_then_click(self.I_CLICK_DIALOGUE_1, interval=1.5): 
                start_time=time.time()
                continue
            if self.appear_then_click(self.I_CLICK_EYE, interval=1) or self.appear_then_click(self.I_CLICK_EYE_2, interval=1):
                start_time=time.time()
                continue
            if self.appear_then_click(self.I_CLICK_JUMP, interval=1):
                start_time=time.time()
                continue
            if self.appear(self.I_CLICK_SPEED_X1, interval=1):
                start_time=time.time()
                continue 
            if self.appear_then_click(self.I_CLICK_SPEED_X2, interval=1):
                start_time=time.time()
                continue
            if self.appear_then_click(self.I_CLICK_CV, interval=1):
                start_time=time.time()
                continue
            if self.appear(self.I_PAGE_SKIP, interval=1):
                start_time=time.time()
                while time.time()-start_time<3:
                    self.screenshot()
                    if self.appear(self.I_CHECK_TICK, interval=1)and self.appear_then_click(self.I_PAGE_SKIP, interval=1):
                        self.config.notifier.push(content=f'已跳过剧情', title='Plotline')
                        raise TaskEnd
                    if self.appear_then_click(self.I_CHECK_UNTICK, interval=1):
                        start_time=time.time()
                        continue
                continue
            # 绑定手机号弹窗
            if self.appear_then_click(RestartAssets.I_LOGIN_LOGIN_GOTO_BIND_PHONE, interval=1):
                while 1:
                    self.screenshot()
                    if self.appear_then_click(RestartAssets.I_LOGIN_LOGIN_CANCEL_BIND_PHONE):
                        logger.info("Close bind phone")
                        break
                continue
            if self.appear_then_click(GameUiAssets.I_DLC_CLOSE, interval=5):
                start_time=time.time()
                continue
            if self.appear_then_click(self.I_CLICK_REFUSE, interval=5):
                start_time=time.time()
                continue
            if self.appear(self.I_CLICK_CURSOR, interval=1):
                current_image=self.device.image
                click_cursor=self.I_CLICK_CURSOR.match_all_any(current_image)
                if len(click_cursor) ==1:
                    self.C_CLICK_CURSOR.roi_back=(click_cursor[0][1]-43,click_cursor[0][2]-65,20,20)
                    self.C_CLICK_CURSOR.roi_front=(click_cursor[0][1]-43,click_cursor[0][2]-65,20,20)
                    self.click(self.C_CLICK_CURSOR)
                start_time=time.time()
                continue
        
    from tasks.Component.GeneralBuff.config_buff import BuffClass
    def battle_before(self, buff: BuffClass | list[BuffClass], config: GeneralBattleConfig, timeout: float = 10) -> bool:
        """战斗前设置
        :return: True:进入战斗或点击了准备按钮且识别不到准备按钮了 False:超过timeout s还没有进入战斗且没有点击过准备
        """
        timeout_timer = Timer(timeout).start()
        confed = False
        while not timeout_timer.reached():
            self.screenshot()
            if self.is_in_real_battle(False):  # 战斗阶段
                return True
            if self.appear_then_click(self.I_DISABLE_7DAYS_DIFF_SOUL, interval=0.6):  # 关闭御魂不一致提示
                continue
            if self.appear_then_click(self.I_CONFIRM_CLOSE_DIFF_SOUL, interval=0.6):  # 确认关闭御魂不一致提示
                continue
            if self.is_in_prepare(False):  # 战斗准备阶段
                if not getattr(config, 'lock_team_enable', False):  # 没有锁定阵容
                    if self.current_count == 1 and not confed:  # 第一次战斗且是本次第一次配置
                        self.switch_preset_team(config.preset_enable, config.preset_group, config.preset_team)
                        self.check_and_open_buff(buff)
                        confed = True
                # 点击准备(锁定阵容自动点准备,不锁定阵容前面也已经配置完毕需要点准备)
                if self.privileges_flag == True and not self.appear(self.I_FLAG_HELP, interval=0.8) :
                    if not self.appear(self.I_FLAG_CHANGE, interval=0.8):
                        self.click(self.C_CLICK_CHANGE)
                        sleep(2)
                        continue
                    self.swipe(self.S_SWIPE_SHIKIGAMI,2)
                    continue
                if self.appear_then_click(self.I_PREPARE_HIGHLIGHT, interval=0.8):
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
            
        click_cnt = 0   
        while 1:
            self.screenshot()
            self.device.click_record_clear()
            if click_cnt > 10:
                click_cnt = 0
                self.click(self.C_CLICK_RANDOM_3)
                self.click(self.C_CLICK_RANDOM_2)
                self.click(self.C_CLICK_RANDOM_1)
                self.swipe(self.S_SWIPE_SUMMON,2)
            if attack_flag and self.appear_then_click(self.I_CLICK_ATTACK, interval=0.3):
                click_cnt+=1
                continue
            if attack_flag and self.appear_then_click(self.I_CLICK_SKILL, interval=1):
                click_cnt+=1
                continue
            # 如果出现赢 就点击, 第二个是针对封魔的图片
            if self.appear(self.I_WIN, threshold=0.8) or self.appear(self.I_DE_WIN):
                logger.info("Battle result is win")
                if self.appear(self.I_DE_WIN):
                    self.ui_click_until_disappear(self.I_DE_WIN)
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

        # 再次确认战斗结果
        logger.info("Reconfirm the results of the battle")
        while 1:
            self.screenshot()
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
            action_click = random.choice([self.C_REWARD_1, self.C_REWARD_2, self.C_REWARD_3])
            if (self.appear_then_click(self.I_REWARD, action=action_click, interval=1.5) or
                self.appear_then_click(self.I_REWARD_GOLD, action=action_click, interval=1.5)#  or
                # self.appear_then_click(self.I_REWARD_STATISTICS, action=action_click, interval=1.5) or
                # self.appear_then_click(self.I_REWARD_PURPLE_SNAKE_SKIN, action=action_click, interval=1.5) or
                # self.appear_then_click(self.I_REWARD_GOLD_SNAKE_SKIN, action=action_click, interval=1.5) or
                # self.appear_then_click(self.I_REWARD_EXP_SOUL_4, action=action_click, interval=1.5) or
                # self.appear_then_click(self.I_REWARD_SOUL_5, action=action_click, interval=1.5) or
                # self.appear_then_click(self.I_REWARD_SOUL_6, action=action_click, interval=1.5)
                ):
                continue
            if (not self.appear(self.I_REWARD) and
                not self.appear(self.I_REWARD_GOLD)#  and
                # not self.appear(self.I_REWARD_STATISTICS) and
                # not self.appear(self.I_REWARD_PURPLE_SNAKE_SKIN) and
                # not self.appear(self.I_REWARD_GOLD_SNAKE_SKIN) and
                # not self.appear(self.I_REWARD_EXP_SOUL_4) and
                # not self.appear(self.I_REWARD_SOUL_5) and
                # not self.appear(self.I_REWARD_SOUL_6)
                ):
                break

        return win            
    
    


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    
    # from mypatch import SimplePatch

    # SimplePatch.patch()

    c = Config('OAS3')
    d = Device(c)
    t = ScriptTask(c, d)
    t.screenshot()
    t.run()
    t.swipe(t.S_SWIPE_SHIKIGAMI,2)
    
     