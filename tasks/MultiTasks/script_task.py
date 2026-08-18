# This Python file uses the following encoding: utf-8
"""多账号通用任务：按选定的账号来源轮转切号，为每个账号执行选定的单账号任务。

子任务参数一律读单账号任务自身的配置，本任务不持有第二份副本。
"""
from collections import defaultdict

from module.exception import TaskEnd
from module.logger import logger
from tasks.Component.MultiAccountRunner import MultiAccountRunner
from tasks.Component.MultiAccountRunner.progress import ProgressStore, acc_key
from tasks.GameUi.game_ui import GameUi
from tasks.MultiTasks.runners import ADAPTERS, SUB_TASKS
from tasks.MultiTasks.sources import ACCOUNT_SOURCES


class _MultiTasksRunner(MultiAccountRunner):
    """只改排序：按邮箱分组使同邮箱角色连续，组间与组内保持首次出现顺序。

    不按 last_complete_time 排序 —— MultiTasks 不写该字段（账号完成状态由
    ProgressStore 驱动），它由各来源实例自行维护，拿它排序会让执行顺序随
    其他实例的运行状态漂移，不可预测。保留邮箱分组是因为切邮箱比同邮箱
    换角色慢，这个收益真实存在。
    """

    def get_sorted_accounts(self) -> list:
        if not self.account_list:
            return []

        filtered = [
            account for account in self.account_list
            if account is not None and self.should_process_account(account)
        ]
        if not filtered:
            logger.info(f'[{self.task_name}] 过滤后没有需要处理的账号')
            return []

        # dict 保序：分组键按邮箱首次出现顺序排列，组内按原始位置排列
        groups: dict[str, list] = defaultdict(list)
        for account in filtered:
            groups[account.account].append(account)

        result = []
        for accounts in groups.values():
            result.extend(accounts)

        logger.info(f'[{self.task_name}] 执行顺序（按邮箱分组）:')
        for account in result:
            logger.info(f'  {account.account} {account.character} {account.svr}')
        return result


class ScriptTask(GameUi):
    # 账号级续做进度，run() 中按配置实例创建；中断后接续时已完成账号直接跳过
    _progress: ProgressStore = None
    _runner: _MultiTasksRunner = None
    # {acc_key: 来源配置名}，仅供日志使用（Runner 的 account_list 不带来源信息）
    _source_names: dict = None

    def _notify_warnings(self, warnings: list) -> None:
        """来源提醒（如未匹配的角色名）汇总后只推送一次，本身不判失败。"""
        if not warnings:
            return
        logger.warning(f'[MultiTasks] 未匹配角色名: {warnings}')
        self.config.notifier.push(
            title='多账号任务配置未找到',
            content='以下角色名未匹配到任何账号：' + '、'.join(warnings),
        )

    def _switch_and_run(self, account) -> bool:
        """切换到指定账号并执行选定的子任务。

        @return: True 表示当前账号成功，False 表示可隔离的账号级失败。
                 必须向上抛出的设备级异常不在此处吞掉（由 Runner 穿透）。
        """
        sub_task = self.config.multi_tasks.multi_tasks_config.sub_task
        spec = SUB_TASKS[sub_task]
        source_name = (self._source_names or {}).get(
            acc_key(account.account, account.character, account.svr), '?')

        logger.hr(f'MultiTasks {spec.task_end_name}: {account.character}/{account.svr}', 2)
        logger.info(
            f'[MultiTasks] 切换账号: config={source_name}, '
            f'character={account.character}, server={account.svr}'
        )
        if not self._runner.switch_to_account(account):
            logger.error(
                f'[MultiTasks] 切换账号失败: '
                f'{source_name}/{account.character}/{account.svr}'
            )
            return False

        # 切号成功后创建全新的子任务适配器，确保可变状态不跨账号共享
        adapter = ADAPTERS[sub_task](self.config, self.device)
        try:
            adapter.run()
        except TaskEnd as e:
            # 仅当 TaskEnd 表示本子任务才视为当前账号正常完成；其他 TaskEnd 上抛
            if e.args and e.args[0] == spec.task_end_name:
                logger.info(
                    f'[MultiTasks] 账号完成: {source_name}/{account.character}/{account.svr}'
                )
                return True
            raise
        # 子任务未抛 TaskEnd 属于异常情况，视为当前账号成功但记录警告
        logger.warning(f'[MultiTasks] 子任务未抛出 TaskEnd({spec.task_end_name})')
        return True

    def run(self):
        cfg = self.config.multi_tasks.multi_tasks_config

        # 1. 按选定来源加载账号
        items, warnings, load_failure = ACCOUNT_SOURCES[cfg.account_source](self.config)

        # 2. 来源提醒只推一次（未匹配本身不判失败）
        self._notify_warnings(warnings)

        # 3. 空账号集合属配置错误：判失败，按失败间隔重调度
        if not items:
            logger.error(
                f'[MultiTasks] 未加载到有效账号: source={cfg.account_source.value}'
            )
            self.set_next_run('MultiTasks', finish=True, success=False, server=False)
            raise TaskEnd('MultiTasks')

        self._source_names = {
            acc_key(account.account, account.character, account.svr): source_name
            for source_name, account in items
        }
        accounts = [account for _source_name, account in items]

        # 4. 建立账号级续做进度。阶段标识含子任务 + 来源方式 + 账号集合 + 自然日：
        #    改子任务或改来源都必须重建（绝不能沿用另一组合的完成标记）；
        #    success_interval 为 1 天，跨天必须重做；失败重调度不改标识，接续时
        #    已完成账号直接跳过，避免重复领奖或重复消耗体力。
        self._progress = ProgressStore('multi_tasks', self.config.config_name)
        self._progress.ensure_phase(
            {
                'sub_task': cfg.sub_task.value,
                'account_source': cfg.account_source.value,
                'accounts': [acc_key(a.account, a.character, a.svr) for a in accounts],
                'day': self.start_time.strftime('%Y-%m-%d'),
            },
            self.start_time.strftime('%Y%m%d-%H%M'),
        )

        # 5. 轮转执行。update_login_history_func / save_config_func 传 no-op：
        #    Runner 不触发它们，账号完成状态完全由 progress 驱动。
        self._runner = _MultiTasksRunner(
            task_name='MultiTasks',
            config=self.config,
            device=self.device,
            account_list=accounts,
            need_login=False,
            login_time=self.start_time,
            update_login_history_func=lambda account: None,
            save_config_func=lambda: None,
            on_account_error=self._on_account_error,
            progress=self._progress,
        )
        success = self._runner.run(process_func=self._switch_and_run)

        # 6. 汇总结果，只更新 MultiTasks 自身调度
        self.set_next_run(
            'MultiTasks',
            finish=True,
            success=success and not load_failure,
            server=False,
        )
        # 7. 全部成功收尾后才清进度：先调度后清，顺序不可颠倒（否则有「调度已改、
        #    进度已删」的窗口导致整轮重跑）；load_failure 时账号集合不完整，同样保留
        if success and not load_failure:
            self._progress.clear()
        raise TaskEnd('MultiTasks')

    def _on_account_error(self, account, error) -> None:
        """账号级异常回调：只负责通知留痕，续接由进度文件驱动。"""
        sub_task = self.config.multi_tasks.multi_tasks_config.sub_task
        self.config.notifier.push(
            title='ERROR',
            content=(f'{account.character}-{account.svr} '
                     f'{SUB_TASKS[sub_task].task_end_name} 执行错误\nError: {error}'),
        )


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas')
    device = Device(config)
    task = ScriptTask(config, device)
    task.run()
