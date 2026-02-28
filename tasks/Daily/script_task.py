import importlib
from datetime import datetime, timedelta

from module.exception import TaskEnd, RequestHumanTakeover
from module.logger import logger
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.Daily import DailyForFlagEx 
from tasks.Daily.assets import DailyAssets
from tasks.Daily.config import AccountInfo, Daily, ExtendedAccountInfo
from tasks.GameUi.game_ui import GameUi
from tasks.DailyForFlag.config import MSGType


class ScriptTask(GameUi, DailyAssets):
    daily_conf: Daily = None

    def run(self):
        self.daily_conf = self.config.daily 
        sup_account_list = self._get_sorted_accounts()
        
        login_time = self.daily_conf.daily_config.need_login_time
        
        for accountInfo in sup_account_list:
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
                            
                except Exception as e:
                    logger.error(f"Error processing account {accountInfo.character}: {e}")
                    retry_count += 1
                    if retry_count >= max_retries:
                        # 如果多次失败，记录错误并继续下一个账号
                        self.config.notifier.push(
                            content=f"{accountInfo.character}-{accountInfo.svr} 任务执行错误\nError: {e}",  
                            title="ERROR"
                        )
                        self.daily_conf.daily_config.need_login = False
                        self.daily_conf.daily_config.need_login_time = login_time
                        self.save_config()
                        self.next_run("Daily", success=False)
                        
        self._notify_daily_completion()
        self.next_run("Daily", success=True)
        raise TaskEnd("Daily")

    def _get_sorted_accounts(self):
        """获取按最后完成时间排序的账号列表"""
        if self.daily_conf.sup_account_list[0].last_complete_time < self.daily_conf.sup_account_list[1].last_complete_time: 
            return reversed(self.daily_conf.sup_account_list)
        return self.daily_conf.sup_account_list

    def _should_process_account(self, account_info, login_time):
        """判断是否应该处理该账号"""
        if not self.daily_conf.daily_config.need_login and not self.is_need_login(account_info, login_time):
            logger.warning(f"{account_info.character} Skipped last Login Time: {account_info.last_complete_time}")
            return False
        return True

    def _process_single_account(self, account_info):
        """处理单个账号的逻辑"""
        # 创建配置对象
        config = self._create_account_config(account_info)
        
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
        
        # 账号特定配置
        config.isflower = account_info.isflower
        config.tongxin_limit_count = account_info.tongxin_limit_count

        return config

    def _switch_to_account(self, account_info):
        """切换到指定账号"""
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
            raise
        except Exception as e:
            logger.error(f"Error in daily tasks for {account_info.character}: {e}")
            return False

    def _create_task_instance(self, config):
        """创建任务实例"""
        module_name = 'script_task'
        from pathlib import Path
        module_path = str(Path.cwd() / 'tasks' / 'DailyForFlag' / (module_name + '.py'))

        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        WQEX = type("WQEX", (module.ScriptTask,), {
            "get_config": DailyForFlagEx.get_config
        })
        wq = WQEX(config=self.config, device=self.device)
        return wq

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
        self.daily_conf.update_account_login_history(account_info)
        self.config.model.daily = self.daily_conf
        self.save_config()
        return True

    def _process_message_type(self, msg_type, msg_content, account_info):
        """处理不同类型的消息"""
        should_retry = False
        
        match msg_type:
            case MSGType.xiezuo:
                device_type = "android" if account_info.apple_or_android else "ios"
                self.config.notifier.push(
                    content=f"{account_info.character} {msg_content}\n所属账号为:{account_info.account},客户端为：{device_type}",
                    title="协作任务提醒"
                )
            case MSGType.mshop:
                device_type = "android" if account_info.apple_or_android else "ios"
                self.config.notifier.push(
                    content=f"{account_info.character} {msg_content}\n所属账号为:{account_info.account},客户端为：{device_type}",
                    title="神秘商店提醒"
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

    def _notify_daily_completion(self):
        """通知日常任务完成"""
        for info in self.daily_conf.sup_account_list:
            logger.info(f"Account: {info.character}, Last completion time: {info.last_complete_time}")
            
        self.config.notifier.push(content="Daily任务执行完毕", title="任务提醒")

    def is_need_login(self, item: AccountInfo, last_complete_time: datetime):
        """
        根据上次登陆时间判断是否需要登录查找
        @param item: 账号信息
        @param last_complete_time: 需要比较的时间
        """
        last_time = item.last_complete_time
        return last_complete_time > last_time

    def save_config(self):
        """保存配置"""
        self.config.save()

    def next_run(self, task: str, finish: bool = False,
                 success: bool = None, server: bool = True, target: datetime = None) -> None:
        """设置下一次运行时间"""
        now = datetime.now()
        
        if success:
            if 5 <= now.hour < 18:
                # 工作时间段：18:05执行
                self._schedule_normal_day(now)
            elif now.hour < 5:
                # 凌晨时段：5:05执行
                self._schedule_after_midnight(now)
            elif 18 <= now.hour <= 23:
                # 晚上时段：次日00:20执行
                self._schedule_evening(now)
            else:
                # 异常情况：第二天5:05执行
                self.set_next_run(task, target=now.replace(hour=5, minute=5) + timedelta(days=1))
        else:
            # 失败情况：10分钟后重试
            self.set_next_run(task, target=now + timedelta(minutes=10))

    def _schedule_normal_day(self, now: datetime):
        """安排白天的运行时间"""
        # 周三或周六开启神秘商店
        if now.weekday() == 2 or now.weekday() == 5:
            self.daily_conf.daily_config.total_mysteryshop_enable = True
        # 周一开启周奖励
        if now.weekday() == 0:
            self.daily_conf.daily_config.total_weekaward_enable = True
            
        self.daily_conf.daily_config.total_tongxin_battle_enable = False
        self.daily_conf.daily_config.total_tongxin_ap_enable = False
        self.daily_conf.daily_config.total_huili_enable = False
        self.daily_conf.daily_config.total_tingyuan_enable = True
        self.daily_conf.daily_config.total_mail_enable = True
        self.daily_conf.daily_config.total_xiezuo_enable = True
        self.daily_conf.daily_config.need_login = True
        self.config.model.daily = self.daily_conf
        
        self.set_next_run("Daily", target=now.replace(hour=18, minute=5))
        self.save_config()

    def _schedule_after_midnight(self, now: datetime):
        """安排凌晨的运行时间"""
        self.set_next_run("Daily", target=now.replace(hour=5, minute=5))

        # 如果开启了同心战斗，则调整设置
        if self.daily_conf.daily_config.total_tongxin_battle_enable:
            self.daily_conf.daily_config.total_tongxin_battle_enable = False
            self.daily_conf.daily_config.total_tongxin_ap_enable = True
            self.daily_conf.daily_config.total_tingyuan_enable = False
            self.daily_conf.daily_config.total_mail_enable = True
            self.daily_conf.daily_config.total_xiezuo_enable = True
            self.config.model.daily = self.daily_conf
            
            self.set_next_run("Daily", target=now.replace(hour=5, minute=5))
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
            
            self.set_next_run("Daily", target=now + timedelta(minutes=20))
            self.save_config()

    def _schedule_evening(self, now: datetime):
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
        self.set_next_run("Daily", target=now.replace(hour=0, minute=20) + timedelta(days=1))
        self.save_config()
        
if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    # from mypatch import SimplePatch

    # SimplePatch.patch()

    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)

    t.run()
