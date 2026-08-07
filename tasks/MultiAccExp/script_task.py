# This Python file uses the following encoding: utf-8
from module.exception import TaskEnd, RequestHumanTakeover
from module.logger import logger
from tasks.Component.MultiAccountRunner import MultiAccountRunner
from tasks.Component.MultiAccountRunner.progress import ProgressStore, acc_key
from tasks.GameUi.game_ui import GameUi
from tasks.MultiAccExp.assets import MultiAccExpAssets
from tasks.MultiAccExp.config import MultiAccExp
from script import Script


class ScriptTask(GameUi, MultiAccExpAssets):
    multi_acc_conf: MultiAccExp = None
    _runner: MultiAccountRunner = None
    # 账号级续做进度，run() 中按配置实例创建；中断后接续时已完成账号直接跳过
    _progress: ProgressStore = None

    def run(self):
        self.multi_acc_conf = self.config.multi_acc_exp

        if self.multi_acc_conf is None or self.multi_acc_conf.multi_acc_exp_config is None:
            logger.error("MultiAccExp configuration is not set, exiting task")
            self.config.notifier.push(content="MultiAccExp配置未设置", title="错误")
            self.set_next_run("MultiAccExp", success=False)
            return

        # 建立账号级续做进度：阶段标识 = 账号集合 + 自然日（success_interval 为 1 天，
        # 跨天必须重做）。失败重调度不改标识，接续时已完成账号直接跳过。
        self._progress = ProgressStore('multi_acc_exp', self.config.config_name)
        self._progress.ensure_phase(
            {'accounts': [acc_key(a.account, a.character, a.svr)
                          for a in (self.multi_acc_conf.sup_account_list or [])],
             'day': self.start_time.strftime('%Y-%m-%d')},
            self.start_time.strftime('%Y%m%d-%H%M'),
        )

        # need_login / need_login_time 已弃用，账号完成判定改由进度文件驱动，
        # 传入固定值使其不再参与过滤（progress 非空时 Runner 不读它们）。
        self._runner = MultiAccountRunner(
            task_name="MultiAccExp",
            config=self.config,
            device=self.device,
            account_list=self.multi_acc_conf.sup_account_list,
            need_login=False,
            login_time=self.start_time,
            update_login_history_func=self.multi_acc_conf.update_account_login_history,
            save_config_func=self._save_config,
            on_account_error=self._on_account_error,
            progress=self._progress,
        )

        success = self._runner.run(process_func=self._process_single_account)

        if success:
            logger.info("All accounts have completed Experience Youkai tasks")
            self.config.notifier.push(
                content="Multi-Account Experience Youkai任务已完成",
                title="多账号经验妖怪完成"
            )

        self.set_next_run("MultiAccExp", success=success)
        # 全部成功收尾后才清进度：先调度后清，顺序不可颠倒
        if success and self._progress is not None:
            self._progress.clear()
        raise TaskEnd("MultiAccExp")

    def _process_single_account(self, account_info):
        config = self._create_account_config(account_info)

        if not config.exp_farming_enable:
            logger.info(f"Skip account {account_info.character} - Experience farming disabled")
            return True

        logger.info("Start processing %s-%s", account_info.character, account_info.svr)

        if not self._runner.switch_to_account(account_info):
            return False

        return self._execute_experience_youkai_task(account_info, config)

    def _create_account_config(self, account_info):
        from tasks.MultiAccExp.config import ExtendedAccountInfo
        config = ExtendedAccountInfo()

        base_config = self.multi_acc_conf.multi_acc_exp_config
        config.exp_farming_enable = base_config.total_exp_farming_enable and account_info.exp_farming_enable
        config.buff_exp_50_click = base_config.total_buff_exp_50_click and account_info.buff_exp_50_click
        config.buff_exp_100_click = base_config.total_buff_exp_100_click and account_info.buff_exp_100_click

        return config

    def _execute_experience_youkai_task(self, account_info, config):
        try:
            self.config.experience_youkai.experience_youkai.buff_exp_50_click = config.buff_exp_50_click
            self.config.experience_youkai.experience_youkai.buff_exp_100_click = config.buff_exp_100_click
            logger.info(f"Buff config for {account_info.character}: 50%={config.buff_exp_50_click}, 100%={config.buff_exp_100_click}")

            from tasks.ExperienceYoukai.script_task import ScriptTask as ExpScriptTask
            exp_task = ExpScriptTask(config=self.config, device=self.device)
            exp_task.run()

            logger.info(f"Experience task completed for account {account_info.character}")
            self.multi_acc_conf.update_account_login_history(account_info)
            self._save_config()
            return True
        except TaskEnd as msg:
            logger.info(f"Experience task ended for account {account_info.character}: {msg}")
            self.multi_acc_conf.update_account_login_history(account_info)
            self._save_config()
            return True
        except RequestHumanTakeover:
            Script.save_error_log(self)
            raise
        except Exception as e:
            logger.error(f"Error in experience task for account {account_info.character}: {e}")
            Script.save_error_log(self)
            return False

    def _on_account_error(self, account_info, error):
        self.config.notifier.push(
            content=f"{account_info.character}-{account_info.svr} 经验妖怪任务执行错误\nError: {error}",
            title="ERROR"
        )
        # 不再改写 need_login / need_login_time（旧逻辑会把未跑账号误判为已完成，
        # 导致后续轮次全部账号被过滤、任务永久空转）；账号续接由进度文件驱动
        Script.save_error_log(self)

    def _save_config(self):
        self.config.model.multi_acc_exp = self.multi_acc_conf
        self.config.save()


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

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
