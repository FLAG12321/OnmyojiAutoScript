# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from datetime import datetime

from tasks.Restart.config_scheduler import Scheduler
from tasks.Restart.login import LoginHandler
from tasks.Restart.assets import RestartAssets
from tasks.base_task import BaseTask, Time
from datetime import datetime, time

from module.logger import logger
from module.exception import TaskEnd, RequestHumanTakeover, GameNotRunningError

# 桌面模式客户端启动失败后的重建轮数。Restart 必须自己扛住启动失败，
# 抛给调度器会被 task_call('Restart') 打回这里形成无限循环
DESKTOP_RESTART_ATTEMPTS = 2


class ScriptTask(LoginHandler):

    def run(self) -> None:
        """
        主要就是登录的模块
        :return:
        """
        if not self.delay_pending_tasks():
            self.app_restart()
        raise TaskEnd('ScriptTask end')

    def app_stop(self):
        logger.hr('App stop')
        self.device.app_stop()

    def app_start(self):
        logger.hr('App start')
        self.device.app_start()
        self.app_handle_login()
        # self.ensure_no_unfinished_campaign()

    def app_restart(self):
        logger.hr('App restart')
        # 桌面分支：客户端可能刚被 OAS 自动启动（已在登录页），直接停掉会白关一次再重开，
        # 只需确保客户端运行并走登录；交互与模拟器不同，隔离在桌面分支
        if not self.device.is_desktop:
            self.device.app_stop()
        if self.device.is_desktop:
            self._desktop_start_and_login()
        else:
            self.device.app_start()
            self.app_handle_login()

        # self.config.task_delay(server_update=True)
        self.set_next_run(task='Restart', success=True, finish=True, server=True)
        # 如果启用了定时领体力（每天 12-14、20-22 时内各有 20 体力）
        if self.config.restart.harvest_config.enable_ap:
            now = datetime.now()
            # 如果时间在00:00-12:00之间则设定时间为当日 12 时
            if now.time() < time(12, 0):
                self.custom_next_run(task='Restart', custom_time=Time(12, 0), time_delta=0)
            # 如果时间在12:00-20:00之间则设定时间为当日 20 时
            elif now.time() >= time(12, 0) and now.time() < time(20, 0):
                self.custom_next_run(task='Restart', custom_time=Time(20, 0), time_delta=0)
            # 如果时间在20:00-23:59之间则设定时间为次日 12 时
            else:
                self.custom_next_run(task='Restart', custom_time=Time(12, 0), time_delta=1)

    def _desktop_start_and_login(self) -> None:
        """桌面模式：启动客户端并登录，客户端没起来就重建，不把异常抛给调度器。

        Restart 是负责启动客户端的那个任务，所以它必须自己扛住客户端起不来的情况：
        若把 GameNotRunningError 抛出去，script.py 接住后又会 task_call('Restart')
        重新进到这里，形成无限重启循环——每轮日志都「正常」，比直接崩更难排查。
        因此这里就地重试：杀掉残留进程后重新走一遍启动+登录，连续失败才交人工。
        """
        for attempt in range(1, DESKTOP_RESTART_ATTEMPTS + 1):
            try:
                self.device.app_start()
                self.app_handle_login()
                return
            except GameNotRunningError as e:
                logger.warning(f'桌面客户端启动后仍未就绪（第 {attempt}/{DESKTOP_RESTART_ATTEMPTS} 轮）: {e}')
                if attempt >= DESKTOP_RESTART_ATTEMPTS:
                    break
                # 本轮可能留下半死的客户端进程，先清掉再重建，避免新窗口识别撞上残留窗口
                logger.info('清理残留客户端后重建')
                self.device.desktop_stop_client()
        logger.critical(f'桌面客户端连续 {DESKTOP_RESTART_ATTEMPTS} 轮启动失败，请检查客户端与机器状态')
        raise RequestHumanTakeover

    def delay_pending_tasks(self) -> bool:
        """
        周三更新游戏的时候延迟
        @return:
        """
        datetime_now = datetime.now()
        if not (datetime_now.weekday() == 2 and 6 <= datetime_now.hour <= 8):
            return False
        logger.info("The game server is updating, delay the pending tasks to 9:00")
        logger.warning('Delay pending tasks')
        # running 中的必然是 Restart
        for task in self.config.pending_task:
            print(task.command)
            self.set_next_run(task=task.command, target=datetime_now.replace(hour=9, minute=0, second=0, microsecond=0))
        self.set_next_run(task='Restart', success=True, finish=True, server=True)
        return True


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    task = ScriptTask(config, device)
    task.app_restart()
    # task.config.update_scheduler()
    # task.delay_pending_tasks()









