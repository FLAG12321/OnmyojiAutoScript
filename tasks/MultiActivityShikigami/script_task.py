# This Python file uses the following encoding: utf-8
from datetime import datetime
from pathlib import Path

from module.config.config import Config
from module.device.device import Device
from module.exception import (
    EmulatorNotRunningError,
    GameNotRunningError,
    RequestHumanTakeover,
    TaskEnd,
)
from module.logger import logger

from tasks.ActivityShikigami.script_task import ScriptTask as ActivityShikigamiScriptTask
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.Component.SwitchAccount.switch_account_config import AccountInfo
from tasks.GameUi.game_ui import GameUi


class _ActivityShikigamiAdapter(ActivityShikigamiScriptTask):
    """
    仅供 MultiActivityShikigami 内部使用的活动任务适配器。

    继承 ActivityShikigami.ScriptTask，直接复用其活动流程 run()，不复制任何活动逻辑。
    只覆盖 set_next_run() 以屏蔽不适用于批量执行的调度副作用：
    - ActivityShikigami: 屏蔽，避免每个账号都改动单账号活动任务的下次运行时间。
    - SoulsTidy: 屏蔽，满足多账号任务不清理御魂的要求。
    - 其他任务(如活动流程中处理悬赏邀请产生的 WantedQuests): 原样转发给父类。
    """

    def set_next_run(self, task: str, finish: bool = False,
                     success: bool = None, server: bool = True, target: datetime = None) -> None:
        # 屏蔽单账号活动任务与御魂整理的调度，其余任务完整转发
        if task in ('ActivityShikigami', 'SoulsTidy'):
            logger.info(f'[MultiActivityShikigami] 屏蔽子任务调度: {task}')
            return
        super().set_next_run(task=task, finish=finish, success=success, server=server, target=target)


class ScriptTask(GameUi):
    """
    多账号活动任务：按用户指定的角色名，从全部配置实例中检索切号资料，
    依次切换账号并运行现有 ActivityShikigami 流程。所有账号共用当前实例的
    activity_shikigami 配置，不复制第二套活动参数。
    """

    @staticmethod
    def parse_account_characters(raw: str) -> list[str]:
        """
        解析角色名字符串为有序去重列表。

        规则：仅按英文逗号分割 -> 去除首尾空白 -> 忽略空项 -> 重复名按首次出现去重。
        空字符串、纯空白或仅含逗号时返回空列表。
        """
        if not raw:
            return []
        result: list[str] = []
        seen: set[str] = set()
        for part in raw.split(','):
            name = part.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            result.append(name)
        return result

    def _load_execution_items(
        self, characters: list[str]
    ) -> tuple[list[tuple[str, AccountInfo]], list[str], bool]:
        """
        实时扫描全部 config\\*.json，按 AccountInfo.character 精确匹配账号。

        返回:
        - execution_items: 有序执行项 list[(source_name, account)]，先遵循用户输入的角色名顺序，
          同名多项按配置文件名和账号原始位置排序；同一物理账号(角色名+账号+服务器相同)只保留首次出现。
        - unmatched: 未匹配角色名，顺序与输入一致。
        - load_failure: 是否有配置加载失败(计入任务失败结果)。
        """
        # 第一步：建立 character -> [(source_name, account), ...] 索引
        # 配置文件按文件名稳定排序，账号按原始列表位置保序，保证同名项内部顺序稳定
        index: dict[str, list[tuple[str, AccountInfo]]] = {}
        load_failure = False
        for config_file in sorted((Path.cwd() / 'config').glob('*.json')):
            source_name = config_file.stem
            try:
                source_config = Config(source_name)
                source_accounts = source_config.multi_daily_alt_acc.sup_account_list or []
            except Exception as e:
                # 单个配置加载失败：记录不含敏感数据的错误日志并继续扫描其他配置
                load_failure = True
                logger.error(f'[MultiActivityShikigami] 加载配置失败: config={source_name}, error={type(e).__name__}')
                continue
            for account in source_accounts:
                # 只有具备有效切号资料的账号才可加入索引
                if not (account.character and account.svr and account.account
                        and account.apple_or_android is not None):
                    continue
                index.setdefault(account.character, []).append((source_name, account))

        # 第二步：按输入角色名顺序展开索引
        # 同名多项按配置文件名和账号原始位置排列；同一物理账号只执行一次，
        # 去重键为 (角色名, 账号, 服务器)，避免同一账号在多个配置实例中重复登记导致重复刷取。
        execution_items: list[tuple[str, AccountInfo]] = []
        unmatched: list[str] = []
        seen: set[tuple[str, str, str]] = set()
        for character in characters:
            matches = index.get(character)
            if not matches:
                unmatched.append(character)
                continue
            for source_name, account in matches:
                dedup_key = (account.character, account.account, account.svr)
                if dedup_key in seen:
                    logger.info(
                        f'[MultiActivityShikigami] 跳过重复账号: config={source_name}, '
                        f'character={account.character}, server={account.svr}'
                    )
                    continue
                seen.add(dedup_key)
                execution_items.append((source_name, account))
        return execution_items, unmatched, load_failure

    def _notify_unmatched(self, unmatched: list[str]) -> None:
        """未匹配名称汇总后，通过当前运行实例只发送一次提醒。"""
        if not unmatched:
            return
        logger.warning(f'[MultiActivityShikigami] 未匹配角色名: {unmatched}')
        self.config.notifier.push(
            title='多账号活动配置未找到',
            content='以下角色名未匹配到任何账号：' + '、'.join(unmatched),
        )

    def _run_one_account(self, source_name: str, account: AccountInfo) -> bool:
        """
        切换到单个账号并运行活动适配器。

        返回 True 表示当前账号成功，False 表示可隔离的账号级失败。
        必须向上抛出的全局异常(设备/游戏环境级)不在此处吞掉。
        """
        logger.hr(f'MultiActivityShikigami account: {account.character}/{account.svr}', 2)
        logger.info(
            f'[MultiActivityShikigami] 切换账号: config={source_name}, '
            f'character={account.character}, server={account.svr}'
        )
        # 使用当前任务的 Config、Device 和匹配到的 AccountInfo 创建 SwitchAccount
        if not SwitchAccount(self.config, self.device, account).switchAccount():
            logger.error(
                f'[MultiActivityShikigami] 切换账号失败: '
                f'{source_name}/{account.character}/{account.svr}'
            )
            return False

        # 切号成功后创建全新的活动适配器，确保可变状态不会跨账号共享
        adapter = _ActivityShikigamiAdapter(self.config, self.device)
        try:
            adapter.run()
        except TaskEnd as e:
            # 仅当 TaskEnd 表示 ActivityShikigami 才视为当前账号正常完成；其他 TaskEnd 上抛
            if e.args and e.args[0] == 'ActivityShikigami':
                logger.info(
                    f'[MultiActivityShikigami] 账号完成: {source_name}/{account.character}/{account.svr}'
                )
                return True
            raise
        # 活动适配器未抛出 TaskEnd 属于异常情况，视为当前账号成功但记录警告
        logger.warning('[MultiActivityShikigami] 活动适配器未抛出 TaskEnd(ActivityShikigami)')
        return True

    def run(self) -> None:
        # 1. 解析角色名
        characters = self.parse_account_characters(
            self.config.multi_activity_shikigami.multi_activity_shikigami_config.account_characters
        )

        # 空输入：配置错误，判失败，按失败间隔调度
        if not characters:
            logger.error('[MultiActivityShikigami] account_characters 为空，请填写要执行的角色名')
            self.set_next_run('MultiActivityShikigami', finish=True, success=False, server=False)
            raise TaskEnd('MultiActivityShikigami')

        # 2. 加载并匹配账号
        execution_items, unmatched, load_failure = self._load_execution_items(characters)

        # 3. 发送未匹配名称通知(只发一次，本身不判失败)
        self._notify_unmatched(unmatched)

        # 4. 按顺序切换账号并运行活动适配器
        has_execution_failure = load_failure
        for source_name, account in execution_items:
            try:
                if not self._run_one_account(source_name, account):
                    has_execution_failure = True
            except (GameNotRunningError, EmulatorNotRunningError, RequestHumanTakeover):
                # 设备/游戏环境级异常：交给主调度器现有恢复逻辑，不继续盲目切换后续账号
                raise
            except Exception as e:
                # 可隔离的账号级异常：记录失败并继续后续账号，日志不含登录账号
                has_execution_failure = True
                logger.error(
                    f'[MultiActivityShikigami] 账号执行异常: config={source_name}, '
                    f'character={account.character}, server={account.svr}, error={type(e).__name__}'
                )

        # 5 & 6. 汇总结果，只更新 MultiActivityShikigami 自身调度
        self.set_next_run(
            'MultiActivityShikigami',
            finish=True,
            success=not has_execution_failure,
            server=False,
        )
        # 7. 结束任务
        raise TaskEnd('MultiActivityShikigami')


if __name__ == '__main__':
    config = Config('oas')
    device = Device(config)
    task = ScriptTask(config, device)
    task.run()
