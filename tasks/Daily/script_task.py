import importlib
from datetime import datetime, timedelta

from module.exception import TaskEnd, RequestHumanTakeover
from module.logger import logger
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.Daily import DailyForFlagEx 
from tasks.Daily.assets import DailyAssets
from tasks.Daily.config import AccountInfo, Daily, ExtendedAccountInfo
from tasks.GameUi.game_ui import GameUi
from tasks.DailyForFlag.config import  DailyForFlagConfig,GeneralBattleConfig


class ScriptTask(GameUi, DailyAssets):
    daily_conf: Daily= None

    def run(self):
        self.daily_conf = self.config.daily 
        sup_account_list=self.daily_conf.sup_account_list
        if  self.daily_conf.sup_account_list[0].last_complete_time<self.daily_conf.sup_account_list[1].last_complete_time: 
            sup_account_list=reversed(self.daily_conf.sup_account_list)
        logger.info(f"sup_account_list: {list(self.daily_conf.sup_account_list)}")
        loggin_time=self.daily_conf.daily_config.need_login_time
        
        for accountInfo in sup_account_list:
            config = ExtendedAccountInfo()
            if  self.daily_conf.daily_config.total_tongxin_battle_enable:
                if not accountInfo.tongxin_battle_enable:
                    continue
                config.tongxin_battle_enable = True
                if self.daily_conf.daily_config.total_huili_enable:
                    config.tongxin_battle_enable = False
            else:
                config.tongxin_battle_enable = False
            config.tongxin_ap_enable = self.daily_conf.daily_config.total_tongxin_ap_enable
            config.mail_enable = self.daily_conf.daily_config.total_mail_enable
            config.juangou_enable = self.daily_conf.daily_config.total_juangou_enable
            config.tingyuan_enable = self.daily_conf.daily_config.total_tingyuan_enable
            config.xiezuo_enable = self.daily_conf.daily_config.total_xiezuo_enable
            config.huili_enable = self.daily_conf.daily_config.total_huili_enable
            config.weekaward_enable = self.daily_conf.daily_config.total_weekaward_enable
            
            config.tongxin_battle_enable &= accountInfo.tongxin_battle_enable
            config.tongxin_ap_enable &= accountInfo.tongxin_ap_enable
            config.mail_enable  &= accountInfo.mail_enable
            config.juangou_enable   &= accountInfo.juangou_enable
            config.tingyuan_enable  &= accountInfo.tingyuan_enable
            config.xiezuo_enable &= accountInfo.xiezuo_enable
            config.huili_enable &= accountInfo.huili_enable
            config.weekaward_enable &= accountInfo.weekaward_enable
            config.tongxin_limit_count = accountInfo.tongxin_limit_count
            
            logger.info("start %s-%s ", accountInfo.character, accountInfo.svr) 
            if not self.daily_conf.daily_config.need_login and not self.is_need_login(accountInfo,loggin_time):
                logger.warning("%s Skipped last Login Time:%s", accountInfo.character, accountInfo.last_complete_time)
                continue 
            suc = SwitchAccount(self.config, self.device, accountInfo).switchAccount()
            if not suc:
                logger.warning("switch to %s-%s Failed", accountInfo.character, accountInfo.svr)
                self.config.notifier.push(content=f"switch to {accountInfo.character}-{accountInfo.svr} Failed,account info :{accountInfo.account}",  title="未找到账号")
                continue
            # 创建子任务实例
            dff = self.CreatObjectFromModule("DailyForFlag", config=self.config, device=self.device)
            # 根据目标模块的配置要求，正确设置配置属性
            dff.daily_conf = self.daily_conf
            dff.account_info = config
            try:
                dff.run()
            except TaskEnd as e:
                logger.warning("%s-%s TaskEnd", accountInfo.character, accountInfo.svr)
                # 更新配置文件中的时间
                self.daily_conf.update_account_login_history(accountInfo)
                # 将修改后的 daily_conf 同步回主配置模型，确保保存时包含最新数据
                self.config.model.daily = self.daily_conf
                self.daily_conf.daily_config.need_login_time = self.start_time
                self.save_config()
                
                continue
            except RequestHumanTakeover as e:
                raise
            except Exception as e:
                logger.error(e)
                self.config.notifier.push(content=f" {accountInfo.character}-{accountInfo.svr} 任务执行错误\n  error: {e}",  title="ERROR")
                self.next_run("Daily", success=False)
        for info in self.daily_conf.sup_account_list:
            logger.info(f"name:{info.character}")
            logger.info(f"time :{info.last_complete_time}")
        self.config.notifier.push(content=f"Daily任务执行完毕", title="任务提醒")
        self.next_run("Daily", success=True)
        raise TaskEnd("Daily")
        pass


    def is_need_login(self, item: AccountInfo,last_complete_time:datetime):
        """
            根据上次登陆时间 判断是否需要登录查找
        @param item:
        @type item:
        """
        lastTime = item.last_complete_time
        #now = datetime.now()
        if last_complete_time > lastTime :
            return True
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

    def save_config(self):
        self.config.save()

    def next_run(self, task: str, finish: bool = False,
                 success: bool = None, server: bool = True, target: datetime = None) -> None:
        now = datetime.now()
        if success:
            if 5 <= now.hour < 18:
                self.set_next_run(task, target=now.replace(hour=18, minute=5))
            elif now.hour < 5:
                self.set_next_run(task, target=now.replace(hour=5, minute=5))
            else:
                self.set_next_run(task, target=now.replace(hour=18, minute=5) + timedelta(days=1))
        else:
            self.set_next_run(task, target=now + timedelta(minutes=10))


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    # from mypatch import SimplePatch

    # SimplePatch.patch()

    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)

    t.run()
