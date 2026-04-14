# This Python file uses the following encoding: utf-8
import importlib
from datetime import datetime, timedelta
import os
import threading
import json
from pathlib import Path

from module.exception import TaskEnd, RequestHumanTakeover, GameNotRunningError
from module.logger import logger
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.GameUi.game_ui import GameUi
from tasks.MultiAccExp.assets import MultiAccExpAssets
from tasks.MultiAccExp.config import MultiAccExp
from script import Script


class ScriptTask(GameUi, MultiAccExpAssets):
    multi_acc_conf: MultiAccExp = None
    # 添加一个类级别的锁，用于同步操作
    _process_lock = threading.Lock()

    def run(self):
        import os
        pid = os.getpid()
        config_name = self.config.config_name  # 获取配置名称，例如oas1, oas2等
        logger.info(f"Starting Multi-Account Experience Youkai task with PID {pid} for config {config_name}")
        
        # 开始执行任务时，在进度文件中标记当前进程
        self._mark_task_start(config_name, pid)
        
        try:
            self.multi_acc_conf = self.config.multi_acc_exp 
            # 检查multi_acc_exp_config是否存在
            if self.multi_acc_conf is None or self.multi_acc_conf.multi_acc_exp_config is None:
                logger.error("MultiAccExp configuration is not set, exiting task")
                self.config.notifier.push(
                    content="MultiAccExp配置未设置",
                    title="错误"
                )
                self.set_next_run("MultiAccExp", success=False)
                return

            sup_account_list = self._get_sorted_accounts()
            
            login_time = self.multi_acc_conf.multi_acc_exp_config.need_login_time
            
            for accountInfo in sup_account_list:
                # 检查accountInfo是否为None
                if accountInfo is None:
                    logger.warning("Skipping None account in account list")
                    continue
                    
                if not self._should_process_account(accountInfo, login_time):
                    continue
                    
                max_retries = 3
                retry_count = 0
                
                while retry_count < max_retries:
                    try:
                        if self._process_single_account(accountInfo):
                            # 成功处理账号，跳出重试循环
                            break
                        else:
                            retry_count += 1
                            if retry_count < max_retries:
                                logger.info(f"Account {accountInfo.character} failed, retrying ({retry_count}/{max_retries})...")
                            else:
                                logger.error(f"Failed to process account {accountInfo.character} after {max_retries} attempts")            
                    except GameNotRunningError:
                        raise GameNotRunningError("Game Not Running")
                    except Exception as e:
                        logger.error(f"Error processing account {accountInfo.character}: {e}")
                        self.config.notifier.push(
                            content=f"{accountInfo.character}-{accountInfo.svr} 经验妖怪任务执行错误\nError: {e}",  
                            title="ERROR"
                        )
                        self.multi_acc_conf.multi_acc_exp_config.need_login = False
                        self.multi_acc_conf.multi_acc_exp_config.need_login_time = login_time
                        self.save_config()
                        self.set_next_run("MultiAccExp", success=False)
                        Script.save_error_log(self)
                                
            self._notify_completion()
            self.set_next_run("MultiAccExp", success=True)
        finally:
            # 无论任务是否成功完成，都要标记为完成
            self._mark_task_completed(config_name)
        
        raise TaskEnd("MultiAccExp")

    def _mark_task_start(self, config_name, pid):
        """
        标记进程开始执行任务，基于配置名称而非PID
        """
        progress_file = Path('./logs/multi_acc_exp_progress.json')
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        
        with self._process_lock:
            # 读取现有进度信息
            progress_data = {}
            if progress_file.exists():
                try:
                    with open(progress_file, 'r', encoding='utf-8') as f:
                        progress_data = json.load(f)
                except (json.JSONDecodeError, FileNotFoundError):
                    progress_data = {}
            
            # 使用配置名称标记当前进程为运行中，同时记录PID以便追踪
            progress_data[f'config_{config_name}'] = {
                'status': 'running',
                'start_time': datetime.now().isoformat(),
                'completed': False,
                'pid': pid  # 记录当前PID
            }
            
            # 保存更新后的进度
            with open(progress_file, 'w', encoding='utf-8') as f:
                json.dump(progress_data, f, ensure_ascii=False, indent=2)
    
    def _mark_task_completed(self, config_name):
        """
        标记进程任务已完成，基于配置名称而非PID
        """
        progress_file = Path('./logs/multi_acc_exp_progress.json')
        
        with self._process_lock:
            # 读取现有进度信息
            progress_data = {}
            if progress_file.exists():
                try:
                    with open(progress_file, 'r', encoding='utf-8') as f:
                        progress_data = json.load(f)
                except (json.JSONDecodeError, FileNotFoundError):
                    progress_data = {}
                    
            # 标记当前配置为已完成
            if f'config_{config_name}' in progress_data:
                progress_data[f'config_{config_name}'].update({
                    'status': 'completed',
                    'completed': True,
                    'completed_time': datetime.now().isoformat()
                })
            
            # 保存更新后的进度
            with open(progress_file, 'w', encoding='utf-8') as f:
                json.dump(progress_data, f, ensure_ascii=False, indent=2)

    def _notify_completion(self):
        """通知所有账号的经验妖怪任务已完成"""
        logger.info("All accounts have completed Experience Youkai tasks")
        self.config.notifier.push(
            content="Multi-Account Experience Youkai任务已完成",  
            title="多账号经验妖怪完成"
        )

    def _should_process_account(self, account_info, login_time: datetime):
        """判断是否应该处理该账号"""
        # 检查是否需要登录
        if not self.multi_acc_conf.multi_acc_exp_config.need_login:
            # 检查上次登录时间
            if account_info.last_complete_time < login_time:
                logger.warning(f"{account_info.character} Skipped last Login Time:{account_info.last_complete_time}")
                return False
        return True

    def _get_sorted_accounts(self):
        """获取按最后完成时间排序的账号列表"""
        if not self.multi_acc_conf.sup_account_list:
            return []
        
        from datetime import datetime
        
        # 按最后完成时间排序（从大到小，即最晚完成的在前）
        sorted_accounts = sorted(
            self.multi_acc_conf.sup_account_list,
            key=lambda x: x.last_complete_time,
            reverse=True
        )
        
        # 打印排序后的结果
        logger.info("_get_sorted_accounts result: account character last_complete_time")
        for account_info in sorted_accounts:
            logger.info(f"{account_info.account} {account_info.character} {account_info.last_complete_time}")
        
        return sorted_accounts

    def _process_single_account(self, account_info):
        """处理单个账号的经验妖怪任务"""
        # 创建配置对象
        config = self._create_account_config(account_info)
        
        # 检查该账号是否启用了经验妖怪任务
        if not config.exp_farming_enable:
            logger.info(f"Skip account {account_info.character} - Experience farming disabled")
            return True
            
        logger.info("Start processing %s-%s", account_info.character, account_info.svr) 
        
        # 切换账号
        if not self._switch_to_account(account_info):
            return False
            
        # 执行经验妖怪任务
        return self._execute_experience_youkai_task(account_info, config)

    def _create_account_config(self, account_info):
        """创建针对特定账号的配置"""
        from tasks.MultiAccExp.config import ExtendedAccountInfo
        config = ExtendedAccountInfo()  # 创建ExtendedAccountInfo实例
        
        # 全局配置
        base_config = self.multi_acc_conf.multi_acc_exp_config
        config.exp_farming_enable = base_config.total_exp_farming_enable and account_info.exp_farming_enable
        config.buff_exp_50_click = base_config.total_buff_exp_50_click and account_info.buff_exp_50_click
        config.buff_exp_100_click = base_config.total_buff_exp_100_click and account_info.buff_exp_100_click

        return config

    def _switch_to_account(self, account_info):
        """切换到指定账号"""
        # 在切换账号前，重置检测记录，避免影响后续账号
        self.device.stuck_record_clear()
        success = SwitchAccount(self.config, self.device, account_info).switchAccount()
        if not success:
            logger.warning("Switch to %s-%s Failed", account_info.character, account_info.svr)
            self.config.notifier.push(
                content=f"Switch to {account_info.character}-{account_info.svr} Failed, account info: {account_info.account}",  
                title="未找到账号"
            )
        return success

    def _execute_experience_youkai_task(self, account_info, config):
        """执行经验妖怪任务"""
        try:
            # 导入ExperienceYoukai任务
            from tasks.ExperienceYoukai.script_task import ScriptTask as ExpScriptTask
            
            # 创建经验妖怪任务实例
            exp_task = ExpScriptTask(config=self.config, device=self.device)
            
            # 运行经验妖怪任务
            exp_task.run()
            
            logger.info(f"Experience task completed for account {account_info.character}")
            # 更新账号最后完成时间
            self.multi_acc_conf.update_account_login_history(account_info)
            self.save_config()
            return True
        except TaskEnd as msg:
            logger.info(f"Experience task ended for account {account_info.character}: {msg}")
            # 更新账号最后完成时间
            self.multi_acc_conf.update_account_login_history(account_info)
            self.save_config()
            return True
        except RequestHumanTakeover:
            Script.save_error_log(self)
            raise
        except Exception as e:
            logger.error(f"Error in experience task for account {account_info.character}: {e}")
            Script.save_error_log(self)
            return False

    def save_config(self):
        """保存配置"""
        self.config.multi_acc_exp = self.multi_acc_conf
        self.config.save()


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    
    # 使用默认配置名称
    c = Config('oas3')
    d = Device(c)
    t = ScriptTask(c, d)
    
    print("Testing MultiAccExp task...")
    try:
        t.run()
    except Exception as e:
        print(f"Error occurred during test: {e}")
        import traceback
        traceback.print_exc()