import importlib
from datetime import datetime, timedelta
import os
import threading
import json
from pathlib import Path

from module.exception import TaskEnd, RequestHumanTakeover,GameNotRunningError
from module.logger import logger
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.MultiDailyAltAcc import DailyAltAccEx
from tasks.MultiDailyAltAcc.assets import MultiDailyAltAccAssets
from tasks.MultiDailyAltAcc.config import AccountInfo, MultiDailyAltAcc, ExtendedAccountInfo
from tasks.GameUi.game_ui import GameUi
from tasks.DailyAltAcc.config import MSGType
from tasks.DailyAltAcc.stat_log import StatEvent, StatLogMixin
from script import Script


class ScriptTask(StatLogMixin, GameUi, MultiDailyAltAccAssets):
    daily_conf: MultiDailyAltAcc = None
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
            # 本次运行的开始边界：后端据此把统计切分为独立会话（一次调度 = 一个会话）；
            # 放在 try 内首行，与 finally 中的 run_end 严格成对
            self.emit_stat(StatEvent.RUN_START)
            # 加载配置，获取returngift_enable状态
            self.daily_conf = self.config.multi_daily_alt_acc
            returngift_enable = self.daily_conf.multi_daily_alt_acc_config.total_returngift_enable
            # 更新进度文件中的returngift_enable状态
            self._update_task_returngift_enable(config_name, returngift_enable)
            
            login_time = self.daily_conf.multi_daily_alt_acc_config.need_login_time
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
                    except GameNotRunningError as e:
                        if "Game crashed to desktop while switching account" not in str(e):
                            raise GameNotRunningError("Game Not Running")
                        logger.error(f"Error processing account {accountInfo.character}: {e}")
                        self.config.notifier.push(
                            content=f"{accountInfo.character}-{accountInfo.svr} 任务执行错误\nError: {e}",
                            title="ERROR"
                        )
                        # 仅切换账号测活失败按账号任务失败处理，避免影响任务开始前的全局游戏未启动检测
                        self.daily_conf.multi_daily_alt_acc_config.need_login = False
                        if not self.daily_conf.multi_daily_alt_acc_config.need_login_time == self.start_time:
                            self.daily_conf.multi_daily_alt_acc_config.need_login_time = login_time
                        self.save_config()
                        self.next_run("MultiDailyAltAcc", success=False)
                        Script.save_error_log(self)
                        break
                    except Exception as e:
                        logger.error(f"Error processing account {accountInfo.character}: {e}")
                        self.config.notifier.push(
                            content=f"{accountInfo.character}-{accountInfo.svr} 任务执行错误\nError: {e}",  
                            title="ERROR"
                        )
                        self.daily_conf.multi_daily_alt_acc_config.need_login = False
                        if not self.daily_conf.multi_daily_alt_acc_config.need_login_time == self.start_time:   
                            self.daily_conf.multi_daily_alt_acc_config.need_login_time = login_time
                        self.save_config()
                        self.next_run("MultiDailyAltAcc", success=False)
                        Script.save_error_log(self) 
                        if e.__class__.__name__ == "RequestHumanTakeover": 
                            raise RequestHumanTakeover("RequestHumanTakeover")
            self._notify_daily_completion()
            # 检查是否需要关机
            if self.daily_conf.multi_daily_alt_acc_config.shutdown_after_finish and self.daily_conf.multi_daily_alt_acc_config.total_alliedteam_battle_enable:
                self._coordinated_shutdown_system(config_name)
            self.next_run("MultiDailyAltAcc", success=True)
        finally:
            try:
                # 本次运行的结束边界：正常结束与异常上抛均会发出，供后端闭合运行段
                self.emit_stat(StatEvent.RUN_END)
            except Exception:
                # 统计埋点失败（如日志 IO 故障）不得阻断任务收尾与完成标记（审查m5）
                logger.exception("emit run_end stat failed")
            # 无论任务是否成功完成，都要标记为完成
            self._mark_task_completed(config_name)
        
        raise TaskEnd("MultiDailyAltAcc")

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
                'pid': pid,
                'returngift_enable': False  # 初始为False，后续更新
            }
            
            # 保存更新后的进度
            with open(progress_file, 'w', encoding='utf-8') as f:
                json.dump(progress_data, f, ensure_ascii=False, indent=2)

    def _update_task_returngift_enable(self, config_name, returngift_enable):
        """更新进度文件中的returngift_enable状态"""
        progress_file = Path('./logs/daily_progress.json')
        
        with self._shutdown_lock:
            if not progress_file.exists():
                return
            
            try:
                with open(progress_file, 'r', encoding='utf-8') as f:
                    progress_data = json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                return
            
            key = f'config_{config_name}'
            if key in progress_data:
                progress_data[key]['returngift_enable'] = returngift_enable
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
        logger.info(f"account {account_info.character} need_login: {self.daily_conf.multi_daily_alt_acc_config.need_login}")
        #logger.info(f"need_login: {self.daily_conf.multi_daily_alt_acc_config.need_login}")
        if not self.daily_conf.multi_daily_alt_acc_config.need_login and not self.is_need_login(account_info, login_time):
            logger.warning(f"{account_info.character} Skipped last Login Time: {account_info.last_complete_time}")
            return False
        return True

    def _process_single_account(self, account_info):
        """处理单个账号的逻辑"""
        # 创建配置对象
        config = self._create_account_config(account_info)
        # 如果没有任何任务被启用，跳过该账号
        if not ( 
            config.alliedteam_battle_enable or config.alliedteam_ap_enable or \
            config.mail_enable or config.donatejade_enable or  \
            config.courtyard_enable or config.cooperation_enable or   \
            config.returngift_enable or config.weekaward_enable or   \
            config.mysteryshop_enable or config.kekkaiActivation_enable or  \
            config.KekkaiUtilize_enable or config.tree_planting_enable > 0 or \
            config.trialbattle_enable or config.summon_up_enable or \
            config.publish_sr_enable \
            ):
            logger.info(f"Skipping account {account_info.character} - No tasks enabled")
            return True
         
        logger.info("Start processing %s-%s", account_info.character, account_info.svr) 
        
        # 切换账号
        if not self._switch_to_account(account_info):
            return False

        self.emit_stat(
            StatEvent.ACC_START,
            acc=account_info.account,
            char=account_info.character,
            svr=account_info.svr,
            tasks=self._enabled_task_keys(config),
        )

        # 执行任务
        return self._execute_daily_tasks(config, account_info)

    def _create_account_config(self, account_info):
        """创建针对特定账号的配置"""
        config = ExtendedAccountInfo()
        
        # 全局配置
        base_config = self.daily_conf.multi_daily_alt_acc_config
        config.alliedteam_battle_enable = base_config.total_alliedteam_battle_enable and account_info.alliedteam_battle_enable
        config.alliedteam_ap_enable = base_config.total_alliedteam_ap_enable and account_info.alliedteam_ap_enable
        config.mail_enable = base_config.total_mail_enable and account_info.mail_enable
        config.donatejade_enable = base_config.total_donatejade_enable and account_info.donatejade_enable
        config.courtyard_enable = base_config.total_courtyard_enable and account_info.courtyard_enable
        config.cooperation_enable = base_config.total_cooperation_enable and account_info.cooperation_enable
        config.returngift_enable = base_config.total_returngift_enable and account_info.returngift_enable
        config.weekaward_enable = base_config.total_weekaward_enable and account_info.weekaward_enable
        config.mysteryshop_enable = base_config.total_mysteryshop_enable and account_info.mysteryshop_enable
        config.kekkaiActivation_enable = base_config.total_kekkaiActivation_enable and account_info.kekkaiActivation_enable
        config.KekkaiUtilize_enable = base_config.total_KekkaiUtilize_enable and account_info.KekkaiUtilize_enable
        config.tree_planting_enable = min(base_config.total_tree_planting_enable, account_info.tree_planting_enable)
        config.trialbattle_enable = base_config.total_trialbattle_enable and account_info.trialbattle_enable
        config.summon_up_enable = base_config.total_summon_up_enable and account_info.summon_up_enable
        # 发布SR碎片：全局 AND 每个小号配置
        config.publish_sr_enable = base_config.total_publish_sr_enable and account_info.publish_sr_enable
        # 账号特定配置
        config.isflower = account_info.isflower
        config.alliedteam_limit_count = account_info.alliedteam_limit_count

        return config

    @staticmethod
    def _enabled_task_keys(config):
        """按实际开启状态返回本账号会执行的子任务键。"""
        task_flags = [
            ("courtyard", config.courtyard_enable),
            ("mail", config.mail_enable),
            ("cooperation", config.cooperation_enable),
            ("donatejade", config.donatejade_enable),
            ("returngift", config.returngift_enable),
            ("weekaward", config.weekaward_enable),
            ("mysteryshop", config.mysteryshop_enable),
            ("kekkaiActivation", config.kekkaiActivation_enable),
            ("KekkaiUtilize", config.KekkaiUtilize_enable),
            ("tree", config.tree_planting_enable > 0),
            ("trialbattle", config.trialbattle_enable),
            ("summon_up", config.summon_up_enable),
            ("publish_sr", config.publish_sr_enable),
            ("alliedteam", config.alliedteam_battle_enable or config.alliedteam_ap_enable),
        ]
        return [task for task, enabled in task_flags if enabled]

    def _emit_account_error(self, account_info, task, error):
        """记录账号级异常，保留异常类型和首行错误消息。"""
        self.emit_stat(
            StatEvent.ERROR,
            acc=account_info.account,
            char=account_info.character,
            svr=account_info.svr,
            task=task,
            etype=error.__class__.__name__,
            emsg=str(error).splitlines()[0] if str(error) else "",
        )

    def _emit_account_end(self, account_info, err_count: int):
        """记录账号运行结束，供后端计算账号总耗时。"""
        self.emit_stat(
            StatEvent.ACC_END,
            acc=account_info.account,
            char=account_info.character,
            svr=account_info.svr,
            err_count=err_count,
        )

    def _switch_to_account(self, account_info):
        """切换到指定账号"""
        # 在切换账号前，重置检测记录，避免影响后续账号
        self.device.stuck_record_clear()
        # 切号起点标记：账号耗时（含切号过程与失败重试）从此刻起算
        self.emit_stat(
            StatEvent.SWITCH_START,
            acc=account_info.account,
            char=account_info.character,
            svr=account_info.svr,
        )
        success = SwitchAccount(self.config, self.device, account_info).switchAccount()
        self.emit_stat(
            StatEvent.SWITCH,
            acc=account_info.account,
            char=account_info.character,
            svr=account_info.svr,
            ok=success,
        )
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
        dff = self._create_task_instance(config, account_info)

        try:
            dff.run()
            self._emit_account_end(account_info, err_count=0)
            return True
        except TaskEnd as msg:
            success = self._handle_task_end(msg, account_info)
            self._emit_account_end(account_info, err_count=0 if success else 1)
            return success
        except RequestHumanTakeover:
            self._emit_account_error(account_info, None, RequestHumanTakeover("RequestHumanTakeover"))
            self._emit_account_end(account_info, err_count=1)
            Script.save_error_log(self)
            raise RequestHumanTakeover
        except Exception as e:
            logger.error(f"Error in daily tasks for {account_info.character}: {e}")
            self._emit_account_error(account_info, None, e)
            self._emit_account_end(account_info, err_count=1)
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
            "get_config": DailyAltAccEx.get_config
        })
        wq = WQEX(**kwargs)
        return wq
    def _create_task_instance(self, config, source_account_info):
        """创建任务实例，并注入统计日志的账号上下文。"""
        dff = self.CreatObjectFromModule("DailyAltAcc", config=self.config, device=self.device)
        dff.daily_conf = self.daily_conf
        dff.account_info = config
        dff._stat_ctx = {
            "acc": source_account_info.account,
            "char": source_account_info.character,
            "svr": source_account_info.svr,
        }
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
        self.config.model.multi_daily_alt_acc = self.daily_conf
        self.daily_conf.multi_daily_alt_acc_config.need_login_time = self.start_time
        self.save_config()
        return True

    def _process_message_type(self, msg_type, msg_content, account_info):
        """处理不同类型的消息"""
        should_retry = False
        
        match msg_type:
            case MSGType.cooperation:
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
                self.daily_conf.multi_daily_alt_acc_config.total_KekkaiUtilize_enable = False
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
            if self.daily_conf.multi_daily_alt_acc_config.total_returngift_enable:
                self._schedule_alliedteam_after_returngift()
            elif self.daily_conf.multi_daily_alt_acc_config.total_alliedteam_battle_enable:
                # 同心战斗模式：不分时段，完成后直接走凌晨后流程（6:05执行上午任务）
                self._schedule_after_midnight(start_time)
            elif 5 <= start_time.hour < 18:
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

    def _reset_one_shot_flags(self):
        """重置单次运行标志（只由用户手动开启，运行一次后自动关闭）"""
        self.daily_conf.multi_daily_alt_acc_config.total_tree_planting_enable = 0
        self.daily_conf.multi_daily_alt_acc_config.total_trialbattle_enable = False
        self.daily_conf.multi_daily_alt_acc_config.total_summon_up_enable = False
        self.daily_conf.multi_daily_alt_acc_config.total_publish_sr_enable = False

    def _schedule_normal_day(self, start_time: datetime):
        """安排白天的运行时间"""
        # 周三或周六开启神秘商店
        if start_time.weekday() == 2 or start_time.weekday() == 5:
            self.daily_conf.multi_daily_alt_acc_config.total_mysteryshop_enable = True
        # 周一开启周奖励
        if start_time.weekday() == 0:
            self.daily_conf.multi_daily_alt_acc_config.total_weekaward_enable = True

        self.daily_conf.multi_daily_alt_acc_config.total_alliedteam_battle_enable = False
        self.daily_conf.multi_daily_alt_acc_config.total_alliedteam_ap_enable = False
        self.daily_conf.multi_daily_alt_acc_config.total_returngift_enable = False
        self.daily_conf.multi_daily_alt_acc_config.total_courtyard_enable = True
        self.daily_conf.multi_daily_alt_acc_config.total_mail_enable = True
        self.daily_conf.multi_daily_alt_acc_config.total_cooperation_enable = True
        self.daily_conf.multi_daily_alt_acc_config.need_login = True
        self._reset_one_shot_flags()
        self.config.model.multi_daily_alt_acc = self.daily_conf

        self.set_next_run("MultiDailyAltAcc", target=start_time.replace(hour=18, minute=5))
        self.save_config()

    def _schedule_after_midnight(self, start_time: datetime):
        """安排凌晨的运行时间"""
        self.set_next_run("MultiDailyAltAcc", target=start_time.replace(hour=6, minute=5))

        # 如果开启了同心战斗，则调整设置
        if self.daily_conf.multi_daily_alt_acc_config.total_alliedteam_battle_enable:
            self.daily_conf.multi_daily_alt_acc_config.total_alliedteam_battle_enable = False
            self.daily_conf.multi_daily_alt_acc_config.total_alliedteam_ap_enable = True
            self.daily_conf.multi_daily_alt_acc_config.total_courtyard_enable = False
            self.daily_conf.multi_daily_alt_acc_config.total_mail_enable = True
            self.daily_conf.multi_daily_alt_acc_config.total_cooperation_enable = True
            self.daily_conf.multi_daily_alt_acc_config.need_login = True
            self._reset_one_shot_flags()
            self.config.model.multi_daily_alt_acc = self.daily_conf

            self.set_next_run("MultiDailyAltAcc", target=start_time.replace(hour=6, minute=5))
            self.save_config()
        elif self.daily_conf.multi_daily_alt_acc_config.total_returngift_enable:
            self._schedule_alliedteam_after_returngift()

    def _schedule_alliedteam_after_returngift(self):
        self.daily_conf.multi_daily_alt_acc_config.total_alliedteam_battle_enable = True
        self.daily_conf.multi_daily_alt_acc_config.total_alliedteam_ap_enable = False
        self.daily_conf.multi_daily_alt_acc_config.total_returngift_enable = False
        self.daily_conf.multi_daily_alt_acc_config.total_courtyard_enable = False
        self.daily_conf.multi_daily_alt_acc_config.total_mail_enable = False
        self.daily_conf.multi_daily_alt_acc_config.total_cooperation_enable = False
        self.daily_conf.multi_daily_alt_acc_config.need_login = True
        self._reset_one_shot_flags()
        self.config.model.multi_daily_alt_acc = self.daily_conf

        self.set_next_run("MultiDailyAltAcc", target=datetime.now() + timedelta(minutes=3))
        self.save_config()

    def _schedule_evening(self, start_time: datetime):
        """安排晚上的运行时间"""
        self.daily_conf.multi_daily_alt_acc_config.total_weekaward_enable = False
        self.daily_conf.multi_daily_alt_acc_config.total_mysteryshop_enable = False
        self.daily_conf.multi_daily_alt_acc_config.total_alliedteam_battle_enable = False
        self.daily_conf.multi_daily_alt_acc_config.total_alliedteam_ap_enable = False
        self.daily_conf.multi_daily_alt_acc_config.total_returngift_enable = True
        self.daily_conf.multi_daily_alt_acc_config.total_courtyard_enable = False
        self.daily_conf.multi_daily_alt_acc_config.total_mail_enable = False
        self.daily_conf.multi_daily_alt_acc_config.total_cooperation_enable = False
        self.daily_conf.multi_daily_alt_acc_config.need_login = True
        self._reset_one_shot_flags()
        self.config.model.multi_daily_alt_acc = self.daily_conf
        self.set_next_run("MultiDailyAltAcc", target=start_time.replace(hour=0, minute=20) + timedelta(days=1))
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
