# This Python file uses the following encoding: utf-8
from module.config.config import Config
from module.base.timer import Timer
from module.exception import TaskEnd
from module.logger import logger
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.Component.SwitchAccount.switch_account_config import AccountInfo
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main
from tasks.MultiAccountSignIn.assets import MultiAccountSignInAssets
from tasks.MultiAccountSignIn.config import ACCOUNT_CONFIGS


class ScriptTask(GameUi, MultiAccountSignInAssets):

    def _load_accounts(self) -> list[tuple[str, AccountInfo]]:
        """从用户勾选的配置实例实时加载 MultiDailyAltAcc 账号列表。"""
        selection = self.config.multi_account_sign_in.account_config_selection
        source_names = [
            config_name
            for field_name, (config_name, _) in ACCOUNT_CONFIGS.items()
            if getattr(selection, field_name, False)
        ]
        accounts = []
        for source_name in source_names:
            source_config = Config(source_name)
            source_accounts = source_config.multi_daily_alt_acc.sup_account_list or []
            # 读取完整切号资料；账号别名允许为空，SwitchAccount 会优先匹配原账号。
            accounts.extend(
                (source_name, account)
                for account in source_accounts
                if account.character
                and account.svr
                and account.account
                and account.apple_or_android is not None
            )
        return accounts

    def _click_leftmost_reward(self) -> bool:
        """识别所有可领取奖励，并点击横坐标最小的一个。"""
        matches = self.I_GET_SHI.match_all_any(self.device.image)
        if not matches:
            logger.warning('[MultiAccountSignIn] 未识别到可领取奖励')
            return False

        _, x, y, width, height = min(matches, key=lambda match: match[1])
        self.device.click(
            x=x + width // 2,
            y=y + height // 2,
            control_name='I_GET_SHI_LEFTMOST',
        )
        return True

    def _wait_reward_result(self, timeout: float = 8) -> bool:
        """等待领取结果；识别到成功页面时返回 True。"""
        timeout_timer = Timer(timeout).start()
        while not timeout_timer.reached():
            self.screenshot()
            if self.appear(self.I_GET_SHI_SUCCESS):
                return True
            
            # 转场期间可能出现通用确认或返回按钮，优先关闭后继续检测。
            if self.appear(self.I_GET_SHI_OVER) and self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                return False
        return False

    def _return_to_reward_page(self, timeout: float = 15) -> bool:
        """关闭领取结果页及附加弹窗，直到重新回到奖励主页。"""
        timeout_timer = Timer(timeout).start()
        wait_timer = Timer(3).start()
        while not timeout_timer.reached():
            self.screenshot()
            if self.appear(self.I_PAGE_GET_SHI):
                return True
            if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                timeout_timer.reset()
                wait_timer.reset()
                continue
            if self.appear_then_click(self.I_GET_SHI_SUCCESS,interval=1):
                timeout_timer.reset()
                wait_timer.reset()
                continue
            if self.appear_then_click(self.I_ANIMATION_JUMP, interval=1):
                timeout_timer.reset()
                wait_timer.reset()
                continue
            if wait_timer.reached():
                self.click(self.I_ANIMATION_JUMP, interval=1)
                wait_timer.reset()
                continue
        logger.warning('[MultiAccountSignIn] 返回奖励主页超时')
        return False

    def _goto_reward_page(self, timeout: float = 30) -> bool:
        """从庭院进入式神奖励主页。"""
        timeout_timer = Timer(timeout).start()
        while not timeout_timer.reached():
            self.screenshot()
            if self.appear(self.I_PAGE_GET_SHI):
                return True
            if self.appear(self.I_TO_PAGE_SHI):
                self.click(self.I_TO_PAGE_SHI, interval=1.5)
                continue
            if self.appear_then_click(self.I_CHANGE_ITEM, interval=1):
                continue
        logger.warning('[MultiAccountSignIn] 进入式神奖励主页超时')
        return False

    def _run_sign_in(self, source_name: str, account: AccountInfo) -> None:
        """执行单个账号的签到功能。"""
        logger.info(
            f'[MultiAccountSignIn] 开始签到: config={source_name}, '
            f'character={account.character}, server={account.svr}'
        )
        try:
            self.screenshot()
            if self.ui_get_current_page() != page_main and not self.ui_goto(page_main):
                logger.warning('[MultiAccountSignIn] 无法返回庭院主页面，跳过当前账号')
                return
            if not self._goto_reward_page():
                return

            for attempt in range(1, 4):
                self.screenshot()
                if not self._click_leftmost_reward():
                    break
                logger.info(f'[MultiAccountSignIn] 第 {attempt}/3 次点击最左侧奖励')
                if not self._wait_reward_result():
                    logger.warning(f'[MultiAccountSignIn] 第 {attempt}/3 次未进入领取成功页面')
                    continue
                logger.info('[MultiAccountSignIn] 奖励领取成功')
                self._return_to_reward_page()
                return

            logger.info('[MultiAccountSignIn] 三次尝试后未领取成功，当前账号签到结束')
        finally:
            # 无论领取结果如何，都返回主页面，保证下一账号从稳定状态开始切换。
            self.ui_get_current_page(skip_first_screenshot=False)
            if not self.ui_goto(page_main, skip_first_screenshot=False):
                logger.warning('[MultiAccountSignIn] 当前账号结束后返回主页面失败')

    def run(self):
        accounts = self._load_accounts()
        if not accounts:
            logger.warning('[MultiAccountSignIn] 未从来源配置中加载到有效账号')
            self.set_next_run('MultiAccountSignIn', finish=True, success=False, server=False)
            raise TaskEnd('MultiAccountSignIn')

        has_switch_failure = False
        for source_name, account in accounts:
            logger.info(
                f'[MultiAccountSignIn] 切换账号: config={source_name}, '
                f'character={account.character}, server={account.svr}'
            )
            if not SwitchAccount(self.config, self.device, account).switchAccount():
                has_switch_failure = True
                logger.error(
                    f'[MultiAccountSignIn] 切换账号失败: '
                    f'{source_name}/{account.character}/{account.svr}'
                )
                continue
            self._run_sign_in(source_name, account)

        self.set_next_run(
            'MultiAccountSignIn',
            finish=True,
            success=not has_switch_failure,
            server=False,
        )
        raise TaskEnd('MultiAccountSignIn')


if __name__ == '__main__':
    from module.device.device import Device

    config = Config('QMUMU3')
    device = Device(config)
    task = ScriptTask(config, device)
    task.run()
