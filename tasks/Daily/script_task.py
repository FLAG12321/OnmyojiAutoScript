import importlib
from datetime import datetime, timedelta
import os
import threading
import json
from pathlib import Path

from module.exception import TaskEnd, RequestHumanTakeover,GameNotRunningError
from module.logger import logger
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.Daily import DailyForFlagEx 
from tasks.Daily.assets import DailyAssets
from tasks.Daily.config import AccountInfo, Daily, ExtendedAccountInfo
from tasks.GameUi.game_ui import GameUi
from tasks.DailyForFlag.config import MSGType
from script import Script 


class ScriptTask(GameUi, DailyAssets):
    daily_conf: Daily = None
    # 添加一个类级别的锁，用于同步关机操作
    _shutdown_lock = threading.Lock()

    def run(self):
        import os
        pid = os.getpid()
        config_name = self.config.config_name  # 获取配置名称，例如oas1, oas2等
        logger.info(f"Starting script task with PID {pid} for config {config_name}")
        # 开始执行任务时，在进度文件中标记当前进程
        self._mark_task_start(config_name, pid)
        
        try:
            self.daily_conf = self.config.daily 
            login_time = self.daily_conf.daily_config.need_login_time
            sup_account_list = self._get_sorted_accounts(login_time)
            
            for accountInfo in sup_account_list:
                    
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
                        raise   GameNotRunningError("Game Not Running")
                    except Exception as e:
                        logger.error(f"Error processing account {accountInfo.character}: {e}")
                        self.config.notifier.push(
                            content=f"{accountInfo.character}-{accountInfo.svr} 任务执行错误\nError: {e}",  
                            title="ERROR"
                        )
                        self.daily_conf.daily_config.need_login = False
                        if not self.daily_conf.daily_config.need_login_time == self.start_time:   
                            self.daily_conf.daily_config.need_login_time = login_time
                        self.save_config()
                        self.next_run("Daily", success=False)
                        Script.save_error_log(self) 
                        if e.__class__.__name__ == "RequestHumanTakeover": 
                            raise RequestHumanTakeover("RequestHumanTakeover")
            self._notify_daily_completion()
            # 检查是否需要关机
            if self.daily_conf.daily_config.shutdown_after_finish and self.daily_conf.daily_config.total_tongxin_battle_enable:
                self._coordinated_shutdown_system(config_name)
            self.next_run("Daily", success=True)
        finally:
            # 无论任务是否成功完成，都要标记为完成
            self._mark_task_completed(config_name)
        
        raise TaskEnd("Daily")

    def _mark_task_start(self, config_name, pid):
        """
        标记进程开始执行任务，基于配置名称而非PID
        """
        progress_file = Path('./logs/daily_progress.json')
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        
        with self._shutdown_lock:
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
        progress_file = Path('./logs/daily_progress.json')
        
        with self._shutdown_lock:
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

    def _coordinated_shutdown_system(self,config_name):
        """
        协调多个进程的关机操作
        使用文件标记来跟踪完成的进程数
        """
        import os
        import time
        
        pid = os.getpid()
        progress_file = Path('./logs/daily_progress.json')
        
        # 标记当前进程已完成
        self._mark_task_completed(config_name)
        
        # 等待一小段时间，让其他进程也有机会更新状态
        time.sleep(5)
        
        # 检查是否所有进程都完成了
        with self._shutdown_lock:
            # 重新读取进度信息
            if progress_file.exists():
                try:
                    with open(progress_file, 'r', encoding='utf-8') as f:
                        progress_data = json.load(f)
                except (json.JSONDecodeError, FileNotFoundError):
                    progress_data = {}
                    
                # 检查是否所有标记的进程都完成了
                all_completed = all(
                    v.get('completed', False) for v in progress_data.values()
                )
                
                if all_completed and len(progress_data) > 0:
                    logger.info(f"All {len(progress_data)} processes completed, executing shutdown")
                    self._execute_shutdown()
                    
                    # 清理进度文件
                    try:
                        progress_file.unlink()
                    except:
                        pass
                else:
                    remaining = sum(1 for v in progress_data.values() if not v.get('completed', False))
                    logger.info(f"Not all processes completed yet, {remaining} remaining")
    
    def _execute_shutdown(self):
        """实际执行系统关机操作"""
        import platform
        import subprocess
        
        system = platform.system()
        try:
            if system == "Windows":
                logger.info("正在关闭系统...")
                # Windows 关机命令，/s 表示关机，/t 30 表示30秒后关机
                subprocess.run(["shutdown", "/s", "/t", "30"], check=True)
                self.config.notifier.push(
                    content="系统将在30秒后关机，请及时保存工作",
                    title="系统关机提醒"
                )
            elif system == "Linux" or system == "Darwin":  # Darwin 是 macOS
                logger.info("正在关闭系统...")
                # Linux/macOS 关机命令
                subprocess.run(["sudo", "shutdown", "-h", "now"], check=True)
                self.config.notifier.push(
                    content="系统即将关机",
                    title="系统关机提醒"
                )
            else:
                logger.warning(f"不支持的操作系统: {system}，无法执行关机操作")
        except subprocess.CalledProcessError as e:
            logger.error(f"关机命令执行失败: {e}")
            self.config.notifier.push(
                content=f"关机失败: {e}",
                title="关机错误"
            )
        except Exception as e:
            logger.error(f"执行关机时发生错误: {e}")

    def _shutdown_system(self):
        """执行系统关机操作（旧版本，保持向后兼容）"""
        self._execute_shutdown()

    def _get_sorted_accounts(self, login_time):
        """获取按最后完成时间排序的账号列表，先剔除不需要处理的账号，再按账号分组排序"""
        if not self.daily_conf.sup_account_list:
            return []
        
        from collections import defaultdict
        from datetime import datetime
        
        # 第一步：剔除不需要处理的账号（need_login为False时，已完成的不需要再登录）
        filtered_accounts = []
        for account_info in self.daily_conf.sup_account_list:
            if not self._should_process_account(account_info, login_time):
                logger.info(f"Filtering out account {account_info.character} (already completed)")
                continue
            filtered_accounts.append(account_info)
        
        if not filtered_accounts:
            logger.info("No accounts need to be processed after filtering")
            return []
        
        # 第二步：按账号（account）分组，使同一邮箱下的角色连续排列
        account_groups = defaultdict(list)
        for account_info in filtered_accounts:
            account_groups[account_info.account].append(account_info)
        
        # 计算每个账号的最后完成时间的最大值（即最晚完成的那个），用于排序整个账号组
        account_times = {}
        for account, account_list in account_groups.items():
            # 计算该账号下所有角色的最后完成时间的最大值（最晚完成的那个）
            latest_completion_time = max([acc.last_complete_time for acc in account_list])
            account_times[account] = latest_completion_time
        
        # 按账号的最晚完成时间排序（从大到小，即最晚完成的账号在前）
        sorted_accounts_by_time = sorted(
            account_groups.keys(),
            key=lambda acc: account_times[acc],
            reverse=True  # 从大到小排序
        )
        
        # 按账号排序后，对每个账号内的角色也进行排序
        result = []
        for account in sorted_accounts_by_time:
            # 对同一账号内的角色按最后完成时间排序（最新完成的在前）
            sorted_account_chars = sorted(
                account_groups[account],
                key=lambda x: x.last_complete_time,
                reverse=True
            )
            result.extend(sorted_account_chars)
        
        # 打印排序后的结果
        logger.info("_get_sorted_accounts result: account character last_complete_time")
        for account_info in result:
            logger.info(f"{account_info.account} {account_info.character} {account_info.last_complete_time}")
        
        return result

    def _should_process_account(self, account_info, login_time):
        """判断是否应该处理该账号"""
        logger.info(f"account {account_info.character} need_login: {self.daily_conf.daily_config.need_login}")
        #logger.info(f"need_login: {self.daily_conf.daily_config.need_login}")
        if not self.daily_conf.daily_config.need_login and not self.is_need_login(account_info, login_time):
            logger.warning(f"{account_info.character} Skipped last Login Time: {account_info.last_complete_time}")
            return False
        return True

    def _process_single_account(self, account_info):
        """处理单个账号的逻辑"""
        # 创建配置对象
        config = self._create_account_config(account_info)
        # 如果没有任何任务被启用，跳过该账号
        if not ( 
            config.tongxin_battle_enable or config.tongxin_ap_enable or \
            config.mail_enable or config.juangou_enable or  \
            config.tingyuan_enable or config.xiezuo_enable or   \
            config.huili_enable or config.weekaward_enable or   \
            config.mysteryshop_enable or config.kekkaiActivation_enable or  \
            config.KekkaiUtilize_enable or config.tree_planting_enable > 0 \
            ):
            logger.info(f"Skipping account {account_info.character} - No tasks enabled")
            return True
         
        logger.info("Start processing %s-%s", account_info.character, account_info.svr) 
        
        # 切换账号
        if not self._switch_to_account(account_info):
            return False
            
        # 执行任务
        return self._execute_daily_tasks(config, account_info)

    def _create_account_config(self, account_info):
        """创建针对特定账号的配置"""
        config = ExtendedAccountInfo()
        
        # 全局配置
        base_config = self.daily_conf.daily_config
        config.tongxin_battle_enable = base_config.total_tongxin_battle_enable and account_info.tongxin_battle_enable
        config.tongxin_ap_enable = base_config.total_tongxin_ap_enable and account_info.tongxin_ap_enable
        config.mail_enable = base_config.total_mail_enable and account_info.mail_enable
        config.juangou_enable = base_config.total_juangou_enable and account_info.juangou_enable
        config.tingyuan_enable = base_config.total_tingyuan_enable and account_info.tingyuan_enable
        config.xiezuo_enable = base_config.total_xiezuo_enable and account_info.xiezuo_enable
        config.huili_enable = base_config.total_huili_enable and account_info.huili_enable
        config.weekaward_enable = base_config.total_weekaward_enable and account_info.weekaward_enable
        config.mysteryshop_enable = base_config.total_mysteryshop_enable and account_info.mysteryshop_enable
        config.kekkaiActivation_enable = base_config.total_kekkaiActivation_enable and account_info.kekkaiActivation_enable
        config.KekkaiUtilize_enable = base_config.total_KekkaiUtilize_enable and account_info.KekkaiUtilize_enable
        config.tree_planting_enable = min(base_config.total_tree_planting_enable, account_info.tree_planting_enable)
        # 账号特定配置
        config.isflower = account_info.isflower
        config.tongxin_limit_count = account_info.tongxin_limit_count

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

    def _execute_daily_tasks(self, config, account_info):
        """执行日常任务"""
        # 创建子任务实例
        dff = self._create_task_instance(config)
        
        try:
            dff.run()
            return True
        except TaskEnd as msg:
            return self._handle_task_end(msg, account_info)
        except RequestHumanTakeover:
            Script.save_error_log(self)
            raise RequestHumanTakeover
        except Exception as e:
            logger.error(f"Error in daily tasks for {account_info.character}: {e}")
            Script.save_error_log(self)
            return False

    def CreatObjectFromModule(self, task_name: str, **kwargs):
        module_name = 'script_task'
        from pathlib import Path
        module_path = str(Path.cwd() / 'tasks' / task_name / (module_name + '.py'))

        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        WQEX = type("WQEX", (module.ScriptTask,), {
            "get_config": DailyForFlagEx.get_config
        })
        wq = WQEX(**kwargs)
        return wq
    def _create_task_instance(self, config):
        """创建任务实例"""
        dff = self.CreatObjectFromModule("DailyForFlag", config=self.config, device=self.device)
        dff.daily_conf = self.daily_conf
        dff.account_info = config
        return dff
    def _handle_task_end(self, msg, account_info):
        """处理任务结束消息"""
        logger.info(f"TaskEnd received: {msg.args}")
        
        if msg.args and msg.args != []:
            logger.info(f"Message count: {len(msg.args)}")
            for item in msg.args[0]:
                logger.info(f"Processing item: {item}")
                if len(item) >= 2:
                    msg_type = item[0]  # MSGType
                    msg_content = item[1]  # Content
                    
                    if self._process_message_type(msg_type, msg_content, account_info):
                        return False  # 如果是网络错误，需要重试
                        
        # 更新账号登录历史
        # 更新配置文件中的时间
        self.daily_conf.update_account_login_history(account_info)
        # 将修改后的 daily_conf 同步回主配置模型,确保保存时包含最新数据
        self.config.model.daily = self.daily_conf
        self.daily_conf.daily_config.need_login_time = self.start_time
        self.save_config()
        return True

    def _process_message_type(self, msg_type, msg_content, account_info):
        """处理不同类型的消息"""
        should_retry = False
        
        match msg_type:
            case MSGType.xiezuo:
                self.config.notifier.push(
                    content=self._build_notify_content(account_info),
                    title=self._build_notify_title(msg_content, "协作任务提醒"),
                )
            case MSGType.mshop:
                self.config.notifier.push(
                    content=self._build_notify_content(account_info),
                    title=self._build_notify_title(msg_content, "神秘商店提醒"),
                )
            case MSGType.Utilize:
                logger.info("由于未找到寄养卡,已将所有账号的KekkaiUtilize_enable设置为False")
                self.daily_conf.daily_config.total_KekkaiUtilize_enable = False
            case MSGType.neterror:
                logger.info("网络错误,准备重试")
                should_retry = True
            case _:
                logger.info(f"未知消息类型: {msg_type}, 内容: {msg_content}")
                
        return should_retry

    @staticmethod
    def _build_notify_title(msg_content, fallback_title):
        clean_content = str(msg_content).strip()
        return clean_content if clean_content else fallback_title

    @staticmethod
    def _build_notify_content(account_info):
        device_type = "android" if account_info.apple_or_android else "ios"
        return "\n".join([
            f"角色：{account_info.character}",
            f"客户端：{device_type}",
            f"账号：{account_info.account}",
        ])

    def _notify_daily_completion(self):
        """通知日常任务完成"""
        for info in self.daily_conf.sup_account_list:
            logger.info(f"Account: {info.character}, Last time: {info.last_complete_time}")
            
        #self.config.notifier.push(content="Daily任务执行完毕", title="任务提醒")

    def is_need_login(self, item: AccountInfo, last_complete_time: datetime):
        """
        根据上次登陆时间判断是否需要登录查找
        @param item: 账号信息
        @param last_complete_time: 需要比较的时间
        """
        #logger.info(f"Account: {item.character}, Last completion time: {item.last_complete_time}, Login time: {last_complete_time}")
        last_time = item.last_complete_time
        return last_complete_time > last_time

    def save_config(self):
        """保存配置"""
        self.config.save()

    def next_run(self, task: str, finish: bool = False,
                 success: bool = None, server: bool = True, target: datetime = None) -> None:
        """设置下一次运行时间"""
        start_time = self.start_time  # 使用任务开始时间而不是当前时间
        if success:
            if 5 <= start_time.hour < 18:
                # 工作时间段：18:05执行
                self._schedule_normal_day(start_time)
            elif start_time.hour < 5:
                # 凌晨时段：6:05执行
                self._schedule_after_midnight(start_time)
            elif 18 <= start_time.hour <= 23:
                # 晚上时段：次日00:20执行
                self._schedule_evening(start_time)
            else:
                # 异常情况：第二天6:05执行
                self.set_next_run(task, target=start_time.replace(hour=6, minute=5) + timedelta(days=1))
        else:
            # 失败情况：10分钟后重试
            self.set_next_run(task, target=datetime.now() + timedelta(minutes=10))

    def _schedule_normal_day(self, start_time: datetime):
        """安排白天的运行时间"""
        # 周三或周六开启神秘商店
        if start_time.weekday() == 2 or start_time.weekday() == 5:
            self.daily_conf.daily_config.total_mysteryshop_enable = True
        # 周一开启周奖励
        if start_time.weekday() == 0:
            self.daily_conf.daily_config.total_weekaward_enable = True
            
        self.daily_conf.daily_config.total_tongxin_battle_enable = False
        self.daily_conf.daily_config.total_tongxin_ap_enable = False
        self.daily_conf.daily_config.total_huili_enable = False
        self.daily_conf.daily_config.total_tingyuan_enable = True
        self.daily_conf.daily_config.total_mail_enable = True
        self.daily_conf.daily_config.total_xiezuo_enable = True
        self.daily_conf.daily_config.need_login = True
        self.config.model.daily = self.daily_conf
        
        self.set_next_run("Daily", target=start_time.replace(hour=18, minute=5))
        self.save_config()

    def _schedule_after_midnight(self, start_time: datetime):
        """安排凌晨的运行时间"""
        self.set_next_run("Daily", target=start_time.replace(hour=6, minute=5))

        # 如果开启了同心战斗，则调整设置
        if self.daily_conf.daily_config.total_tongxin_battle_enable:
            self.daily_conf.daily_config.total_tongxin_battle_enable = False
            self.daily_conf.daily_config.total_tongxin_ap_enable = True
            self.daily_conf.daily_config.total_tingyuan_enable = False
            self.daily_conf.daily_config.total_mail_enable = True
            self.daily_conf.daily_config.total_xiezuo_enable = True
            self.daily_conf.daily_config.need_login = True
            self.config.model.daily = self.daily_conf
            
            self.set_next_run("Daily", target=start_time.replace(hour=6, minute=5))
            self.save_config()
        elif self.daily_conf.daily_config.total_huili_enable:
            # 如果开启了回礼功能
            self.daily_conf.daily_config.total_tongxin_battle_enable = True
            self.daily_conf.daily_config.total_tongxin_ap_enable = False
            self.daily_conf.daily_config.total_huili_enable = False
            self.daily_conf.daily_config.total_tingyuan_enable = False
            self.daily_conf.daily_config.total_mail_enable = False
            self.daily_conf.daily_config.total_xiezuo_enable = False
            self.daily_conf.daily_config.need_login = True
            self.config.model.daily = self.daily_conf
            
            self.set_next_run("Daily", target=datetime.now() + timedelta(minutes=20))
            self.save_config()

    def _schedule_evening(self, start_time: datetime):
        """安排晚上的运行时间"""
        self.daily_conf.daily_config.total_weekaward_enable = False
        self.daily_conf.daily_config.total_mysteryshop_enable = False
        self.daily_conf.daily_config.total_tongxin_battle_enable = False
        self.daily_conf.daily_config.total_tongxin_ap_enable = False
        self.daily_conf.daily_config.total_huili_enable = True
        self.daily_conf.daily_config.total_tingyuan_enable = False
        self.daily_conf.daily_config.total_mail_enable = False
        self.daily_conf.daily_config.total_xiezuo_enable = False
        self.daily_conf.daily_config.need_login = True
        self.config.model.daily = self.daily_conf
        self.set_next_run("Daily", target=start_time.replace(hour=0, minute=20) + timedelta(days=1))
        self.save_config()
        
if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    # from mypatch import SimplePatch

    # SimplePatch.patch()

    c = Config('QMUMU4')
    d = Device(c)
    t = ScriptTask(c, d)
    """ t.daily_conf = t.config.daily 
    sup_account_list = t._get_sorted_accounts() """
    t.run()
