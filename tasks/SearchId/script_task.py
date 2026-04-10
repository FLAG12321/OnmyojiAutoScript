from module.config.config import Config
from module.device.device import Device
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main,page_friends
from tasks.SearchId.assets import SearchIdAssets
import time
import csv
import os
from pathlib import Path
from deploy.logger import logger
from time import sleep

class ScriptTask(GameUi,SearchIdAssets):

    def __init__(self, config: Config, device: Device):
        super().__init__(config, device)

    def run(self):
        self.prepare_run()
        csv_file_path = str(Path(__file__).parent / 'data.csv')
        self.batch_search_from_csv(csv_file_path)
        """ # 检查是否启用了批量搜索模式
        if hasattr(self.config.search_id, 'batch_mode_enabled') and self.config.search_id.batch_mode_enabled:
            csv_file_path = str(Path(__file__).parent / 'data.csv')
            self.batch_search_from_csv(csv_file_path)
        else:
            # 使用原来的逻辑，从配置中获取ID
            id_to_search = getattr(self.config.search_id, 'target_id', "#500449712")
            self.character_search(id_to_search) """
            
    def batch_search_from_csv(self, csv_file_path):
        """
        从CSV文件批量搜索角色并截图保存
        :param csv_file_path: CSV文件路径
        """
        try:
            # 确保screenshots目录存在
            screenshots_dir = Path("screenshots")
            screenshots_dir.mkdir(exist_ok=True)
            
            with open(csv_file_path, 'r', encoding='utf-8') as csvfile:
                csv_reader = csv.reader(csvfile)
                for idx, row in enumerate(csv_reader, 1):
                    try:
                        if len(row) != 3:  # 确保有三个字段：ID, 寮名称, 角色名称
                            logger.warning(f"第{idx}行格式错误，跳过处理: {row}")
                            continue
                        
                        id_value, character_name, guild_name = [item.strip() for item in row]
                        
                        # 清理文件名中的非法字符
                        character_name = "".join(c for c in  character_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
                        guild_name = "".join(c for c in guild_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
                        
                        logger.info(f"开始处理第{idx}行: ID={id_value}, 角色={character_name}, 寮={guild_name}")
                        
                        # 检查截图是否已存在
                        screenshot_filename = f"#{id_value}-{character_name}-{guild_name}.png"
                        screenshot_path = screenshots_dir / screenshot_filename
                        
                        if screenshot_path.exists():
                            logger.info(f"截图已存在，跳过搜索: {screenshot_filename}")
                            continue
                        
                        # 带重试机制的搜索
                        success = self.safe_character_search_with_retry(int(id_value))
                        if success:
                            if not self.to_space():
                                self.config.notifier.push(title=f'Search ID', content=f"#{id_value}-{guild_name}-{character_name} 未找到")
                            # 截图并保存
                            # 确保截图前画面稳定
                            time.sleep(0.5)
                            # 保存截图
                            self.search_save_image(screenshot_filename)
                            self.space_return()
                        else:
                            logger.error(f"搜索失败，ID: {id_value}")
                            
                        # 在处理下一个ID前稍作延时
                        time.sleep(1)
                    
                    except Exception as e:
                        logger.error(f"处理第{idx}行时发生错误: {str(e)}")
                        continue
        
        except FileNotFoundError:
            logger.error(f"CSV文件不存在: {csv_file_path}")
        except Exception as e:
            logger.error(f"批量搜索过程中发生错误: {str(e)}")

    def safe_character_search_with_retry(self, id_value:int, max_retries=5):
        """
        带重试机制的安全角色搜索函数
        :param id_value: 要搜索的角色ID
        :param max_retries: 最大重试次数
        :return: 搜索是否成功
        """
        for attempt in range(max_retries):
            result = self.character_search(id_value)
            if result:
                logger.info(f"角色搜索成功，ID: {id_value}")
                return True
            
            if attempt < max_retries - 1:  # 不是最后一次尝试
                logger.warning(f"角色搜索失败，ID: {id_value}，正在重试 ({attempt + 1}/{max_retries})")
            else:
                logger.error(f"角色搜索失败，已达到最大重试次数，ID: {id_value}")
        
        return False
        

    def to_space(self):
        start_time = time.time()
        while time.time()-start_time<5:
            self.screenshot()
            if self.appear(self.I_PAGE_SPACE):
                start_time = time.time()
                return True
            if self.appear_then_click(self.I_TO_SPACE,interval=1):
                start_time = time.time()
                continue
            if self.is_exist_character():
                self.click(self.C_CLICK_CHARACTER)
                sleep(1)
                start_time = time.time()
                continue
            
        return False
    def space_return(self):
        start_time = time.time()
        while time.time()-start_time<5:
            self.screenshot()
            if self.appear(self.I_SERVER_ALL,interval=1):
                return True
            if self.appear_then_click(self.I_PAGE_SPACE,action=self.C_CLICK_SPACE_EXIT,interval=1):
                start_time = time.time()
                continue   
        return False 
    def character_search(self, number:int):
        start_time = time.time()
        while time.time()-start_time<5:
            self.screenshot()
            if self.appear_then_click(self.I_SEARCH_ENSURE,interval=1):
                time.sleep(3)
                self.screenshot()
                if self.appear(self.I_SEARCH_RESULT):
                    return True
                start_time = time.time()
                continue
            if not self.appear(self.I_SEARCH_FLAG,interval=1):
                start_time = time.time()
                continue
            self.ui_click(self.C_CLICK_INPUT,stop=self.I_SEARCH_ENSURE,interval=1)
            self.input_text("#")
            self.input_number(number)
            time.sleep(1)
            start_time = time.time()
        return False
    def search_save_image(self,name):
        sleep(1)
        self.screenshot()
        from module.base.utils import save_image
        folder_name = f'screenshots'
        if not os.path.exists( f'./{folder_name}'):
            os.mkdir(f'./{folder_name}')
        folder = f'./{folder_name}'
        save_image(self.screenshot(), f'{folder}/{name}')
    def is_exist_character(self):
        return self.O_LEVEL.ocr(self.device.image)>0
    def prepare_run(self):
        if self.ui_get_current_page()!=page_friends:
            self.ui_goto(page_friends)
        start_time = time.time()
        while time.time()-start_time<5:
            self.screenshot()
            if self.appear(self.I_SERVER_ALL,interval=1):
                return True
            if self.appear_rgb(self.I_TO_SEARCH):
                self.appear_then_click(self.I_TO_SEARCH,interval=1)
                start_time = time.time()
                continue
            if self.appear_then_click(self.I_SERVER_ALL_SELECT,interval=1):
                start_time = time.time()
                continue
            if self.appear_then_click(self.I_SEARCH_FLAG,action=self.C_CLICK_SERVER,interval=1):
                start_time = time.time()
        return False

if __name__ == "__main__":
    from module.config.config import Config
    from module.device.device import Device

    config = Config('QMUMU1')  
    device = Device(config)
    task = ScriptTask(config, device)
    task.run()