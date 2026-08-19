# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import random
from module.base.timer import Timer
from module.exception import RequestHumanTakeover, GameTooManyClickError, GameStuckError
from module.logger import logger
from tasks.Restart.assets import RestartAssets
from tasks.GameUi.assets import GameUiAssets
from tasks.base_task import BaseTask
from module.atom.click import RuleClick
import time

# 单次登录流程里容许处理的 MPay 弹窗次数上限。弹窗关得掉却反复重弹，说明客户端登录态
# 没有真正推进；而弹窗分支是 continue 回循环顶部、绕过了 self.screenshot()，卡死检测
# （stuck_record_check）只在截图里跑，因此这条路径没有任何超时兜底，原地重试会把登录
# 循环永久挂住。超过上限就交给 Restart 重建客户端，而不是继续等一个不会消失的弹窗。
DESKTOP_MPAY_CLOSE_LIMIT = 5


class LoginHandler(BaseTask, RestartAssets, GameUiAssets):
    character: str
    skip_onmyoji_genie: bool = False

    def __init__(self, *wargs, **kwargs):
        super().__init__(*wargs, **kwargs)
        self.character = self.config.restart.login_character_config.character
        self.O_LOGIN_SPECIFIC_SERVE.keyword = self.character
        # self.specific_usr = kwargs['config'].

    def _app_handle_login(self) -> bool:
        """
        最终是在庭院界面
        :return:
        """
        logger.hr('App login')
        self.device.stuck_record_add('LOGIN_CHECK')

        confirm_timer = Timer(1.5, count=2).start()
        orientation_timer = Timer(10)
        login_success = False
        # 本次登录已处理过的 MPay 弹窗次数，用于给「关掉又重弹」封顶
        popup_handled = 0

        def handle_desktop_login_popup() -> bool:
            """登录期间持续处理 MPay 弹窗，返回是否刚刚关闭了弹窗。"""
            nonlocal popup_handled
            if not self.device.is_desktop or not self.device.find_desktop_login_popup():
                return False
            popup_handled += 1
            if popup_handled > DESKTOP_MPAY_CLOSE_LIMIT:
                # 前几次都成功关掉了却又弹出来，说明登录靠回车已经走不出去，
                # 交给 Restart 清掉客户端重建，而不是在这里无限关弹窗
                raise GameStuckError(
                    f'Desktop MPay login popup reappeared more than {DESKTOP_MPAY_CLOSE_LIMIT} times')
            if not self.device.desktop_confirm_login_popup():
                # 弹窗可能恰好在确认函数最后一次枚举后自行关闭，重新确认避免误判为卡死。
                if not self.device.find_desktop_login_popup():
                    return True
                # 弹窗仍存活时不能继续识别游戏页面，否则会把 MPay 误报成未知页面。
                raise GameStuckError('Desktop MPay login popup cannot be closed')
            return True

        while 1:
            # MPay 可能在启动后、重登时或登录流程中途反复出现，每一轮都必须处理。
            if handle_desktop_login_popup():
                continue
            # Watch device rotation
            if not login_success and orientation_timer.reached():
                # Screen may rotate after starting an app
                self.device.get_orientation()
                orientation_timer.reset()

            self.screenshot()
            # 截图与窗口枚举存在竞态，截图后再检查一次，避免刚出现的 MPay 进入图像识别。
            if handle_desktop_login_popup():
                continue
            # 取消继续战斗
            if self.appear_then_click(self.I_CANCEL_BATTLE, interval=0.8):
                logger.info('Cancel continue battle')
                continue
                        # 确认进入庭院
            if self.appear_then_click(self.I_LOGIN_SCROOLL_CLOSE, interval=2, threshold=0.9):
                logger.info('Open scroll')
                continue
            # 确认进入庭院(优化：当出现闲庭图片时，点击卷轴关闭区域，然后判断式神录按钮出现就代表登录成功)
            if self.appear(self.I_LOGIN_COURTYARD, interval=0.2) or self.appear(self.I_LOGIN_COURTYARD2, interval=0.2) or self.ocr_appear(self.O_LOGIN_COURTYARD, interval=0.2):
                if self.click(self.C_LOGIN_SCROLL_CLOSE_AREA, interval=2):
                    logger.info('Click scroll close area because courtyard appears')
                    self.screenshot()  # 点击后立即获取最新截图，确保后续状态检查准确
                    continue
            if self.appear(self.I_MAIN_GOTO_SHIKIGAMI_RECORDS, interval=0.2):
                if confirm_timer.reached():
                    logger.info('Login to main confirm (shikigami records button appears)')
                    break
            elif self.appear(self.I_LOGIN_SCROOLL_OPEN, interval=0.2):
                if confirm_timer.reached():
                    logger.info('Login to main confirm (scroll open)')
                    break
            else:
                confirm_timer.reset()
            # 登录成功
            if self.appear(self.I_MAIN_GOTO_SHIKIGAMI_RECORDS, interval=0.5):
                logger.info('Login success: shikigami records button appears')
                login_success = True
            elif self.appear(self.I_LOGIN_SCROOLL_OPEN, interval=0.5):
                logger.info('Login success: scroll open')
                login_success = True

            # 网络异常
            # if self.ocr_appear(self.O_LOGIN_NETWORK):
            #     logger.error('Network error')
            #     raise RequestHumanTakeover('Network error')

            # 跳过观看视频
            # if self.ocr_appear_click(self.O_LOGIN_SKIP_1, interval=1):
            #     continue
            # 领取抵扣券
            if self.appear_then_click(self.I_OFF_TICKET, interval=1):
                continue
            #领取抵扣券
            if self.appear_then_click(self.I_LOGIN_GET_COUPON, interval=1):
                continue
            # 下载插画
            if self.appear_then_click(self.I_LOGIN_LOAD_DOWN, interval=1):
                logger.info('Download inbetweening')
                continue
            # 不观看视频
            if self.appear_then_click(self.I_WATCH_VIDEO_CANCEL, interval=0.6):
                logger.info('Close video')
                continue
            # 右上角的红色的关闭
            if self.appear_then_click(self.I_LOGIN_RED_CLOSE, interval=1.6):
                logger.info('Close red close')
                continue
            # 左上角的黄色关闭
            if self.appear_then_click(self.I_LOGIN_YELLOW_CLOSE, interval=1.6):
                logger.info('Close yellow close')
                continue
            # 绑定手机号弹窗
            if self.appear_then_click(self.I_LOGIN_LOGIN_GOTO_BIND_PHONE):
                while 1:
                    self.screenshot()
                    if self.appear_then_click(self.I_LOGIN_LOGIN_CANCEL_BIND_PHONE):
                        logger.info("Close bind phone")
                        break
                continue
            # 关闭各种邀请弹窗(主要时结界卡寄养邀请)
            from tasks.Component.GeneralInvite.assets import GeneralInviteAssets as gia
            if self.appear_then_click(gia.I_I_REJECT, interval=0.8):
                logger.info("reject invites")
                continue
            # 关闭阴阳师精灵提示
            if not self.skip_onmyoji_genie and self.appear_then_click(self.I_LOGIN_LOGIN_ONMYOJI_GENIE):
                logger.info("click onmyoji genie")
                continue
            # 当账号未登录时点击登录
            from tasks.Component.SwitchAccount.assets import SwitchAccountAssets
            if self.appear_then_click(SwitchAccountAssets.I_SA_ACCOUNT_LOGIN_BTN, interval=0.8):
                logger.info("click login")
                continue
            if self.appear_then_click(SwitchAccountAssets.I_SA_LOGIN_FORM_ANDROID, interval=0.8):
                logger.info("click ANDROID")
                continue
            # 点击屏幕进入游戏
            if self.appear(self.I_LOGIN_SPECIFIC_SERVE, interval=0.6):
                for i in range(5):
                    self.screenshot()
                    ocrRes = self.O_LOGIN_SPECIFIC_SERVE.detect_and_ocr(self.device.image)
                    # 找到该账号
                    acount_click=""
                    for index, ocr_account in enumerate([ocrResItem.ocr_text for ocrResItem in ocrRes]):
                        if not self.O_LOGIN_SPECIFIC_SERVE.keyword==ocr_account:
                            continue
                        ocrResBoxList = [ocrResItem.box for ocrResItem in ocrRes]
                        
                        roi = [
                            self.O_LOGIN_SPECIFIC_SERVE.roi[0] + ocrResBoxList[index][0][
                                0],
                            self.O_LOGIN_SPECIFIC_SERVE.roi[1] + ocrResBoxList[index][0][
                                1],
                            ocrResBoxList[index][1][0] - ocrResBoxList[index][0][0],
                            ocrResBoxList[index][2][1] - ocrResBoxList[index][1][1]]
                        acount_click = RuleClick(roi,roi,"character select")
                        break
                    if not acount_click=="":
                        self.click(acount_click)
                        time.sleep(1)   
                        self.click(acount_click)
                        break
                while True:
                    self.screenshot()
                    if self.appear(self.I_LOGIN_SPECIFIC_SERVE):
                        self.click(self.C_LOGIN_ENSURE_LOGIN_CHARACTER_IN_SAME_SVR, interval=2)
                        continue
                    break
                logger.info('login specific user')
                continue
            
            # 创建角色, 误入新区直接重启
            if self.appear(self.I_CREATE_ACCOUNT):
                logger.warning('Appear create account')
                raise GameStuckError('Appear create account')

            # 点击“进入游戏”速度过快会进入区服设置，同时需在检测I_LOGIN_8之前检测，因为新服图标会让I_LOGIN_8向右偏移导致永远无法检测成功
            # 同时修复了点击位置（之前是点击I_CHARACTARS而不是左边的区域）
            if self.appear(self.I_CHARACTARS, interval=1):
                logger.info('误入区服设置')
                # https://github.com/runhey/OnmyojiAutoScript/issues/585
                self.device.click(x=106, y=535)
                
            # 点击’进入游戏‘
            if not self.appear(self.I_LOGIN_8):
                continue
            
            # 登录体验服时，点击“进入游戏”速度过快，可能会出现体验服的弹窗
            if self.appear(self.I_EARLY_SERVER):
                if self.appear_then_click(self.I_EARLY_SERVER_CANCEL):
                    logger.info('Cancel switch from early server to normal server')
                    continue
            if self.ocr_appear_click(self.O_LOGIN_ENTER_GAME, interval=3):
                self.wait_until_appear(self.I_LOGIN_SPECIFIC_SERVE, True, wait_time=5)
                continue

        return login_success

    def app_handle_login(self) -> bool:
        # 桌面客户端的启动、清理和三轮重建统一由 _desktop_start_and_login 管理。
        # 这里若再 stop/start，会在内部重试耗尽后留下一个从未验证的新进程。
        attempts = 1 if self.device.is_desktop else 2
        for _ in range(attempts):
            self.device.stuck_record_clear()
            self.device.click_record_clear()
            try:
                self._app_handle_login()
                # 桌面分支：登录成功标记登录态，使 app_is_running 判定为已在游戏中
                if self.device.is_desktop:
                    self.device.desktop_mark_logged_in()
                if self.config.restart.harvest_config.enable:
                    self.harvest()
                return True
            except (GameTooManyClickError, GameStuckError) as e:
                logger.warning(e)
                if self.device.is_desktop:
                    raise
                self.device.app_stop()
                self.device.app_start()
                continue

        logger.critical(f'Login failed after {attempts} attempts')
        logger.critical('Onmyoji server may be under maintenance, or you may lost network connection')
        raise RequestHumanTakeover

    def harvest(self):
        """
        获得奖励
        :return: 如果没有发现任何奖励后退出
        """
        logger.hr('Harvest')
        timer_harvest = Timer(5)  # 如果连续5秒没有发现任何奖励，退出
        skip_default = False
        courtyard_affairs_done = False  # 庭院事务只执行一次
        while 1:
            self.screenshot()

            # 点击'获得奖励'
            if self.ui_reward_appear_click():
                timer_harvest.reset()
                continue
            # 获得奖励
            if self.appear_then_click(self.I_UI_AWARD, interval=0.2):
                timer_harvest.reset()
                continue
            # 偶尔会打开到聊天频道
            if self.appear_then_click(self.I_HARVEST_CHAT_CLOSE, interval=1):
                timer_harvest.reset()
                continue
            # 偶尔会进入其他页面
            # 左上角的黄色关闭
            if self.appear_then_click(self.I_LOGIN_YELLOW_CLOSE, interval=0.6):
                timer_harvest.reset()
                logger.info('Close yellow close')
                continue
            # 关闭宠物小屋
            if self.appear_then_click(self.I_HARVEST_BACK_PET_HOUSE, interval=0.6):
                timer_harvest.reset()
                logger.info('Close yellow close')
                continue
            # 御魂溢确认
            if self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=2.5):
                timer_harvest.reset()
                skip_default = True
                logger.info('Soul overflow')
                continue
            # 关闭姿度出现的蒙版
            if self.appear(self.I_HARVEST_ZIDU, interval=1):
                timer_harvest.reset()
                self.I_HARVEST_ZIDU.roi_front[0] -= 200
                self.I_HARVEST_ZIDU.roi_front[1] -= 200
                if self.click(self.I_HARVEST_ZIDU, interval=2):
                    logger.info('Close zidu')
                continue

            # 庭院事务
            if self.config.restart.harvest_config.enable_courtyard_affairs and not courtyard_affairs_done:
                self.harvest_courtyard_affairs()
                timer_harvest.reset()
                courtyard_affairs_done = True
                continue
            # 勾玉
            if self.appear_then_click(self.I_HARVEST_JADE, interval=1.5):
                timer_harvest.reset()
                continue
            # 签到
            if self.appear_then_click(self.I_HARVEST_SIGN, interval=1.5):
                self.wait_until_appear(self.I_HARVEST_SIGN_2, wait_time=2)
                timer_harvest.reset()
                continue
            # 某些活动的特殊签到，有空看到就删掉
            if self.appear_then_click(self.I_HARVEST_SIGN_3, interval=0.7):
                timer_harvest.reset()
                continue
            if self.appear_then_click(self.I_HARVEST_SIGN_4, interval=1):
                timer_harvest.reset()
                continue
            if self.appear_then_click(self.I_HARVEST_SIGN_2, interval=1.5):
                self.wait_until_appear(self.I_LOGIN_RED_CLOSE, wait_time=2)
                timer_harvest.reset()
                continue
            # 999天的签到福袋
            if self.appear_then_click(self.I_HARVEST_SIGN_999, interval=1.5):
                timer_harvest.reset()
                continue
            # 判断是否勾选了收取邮件（不收取邮件可以查看每日收获）
            if not skip_default and self.config.restart.harvest_config.enable_mail and self.harvest_mail():
                timer_harvest.reset()
                continue
            if self.appear_then_click(self.I_HARVEST_AP, interval=1, threshold=0.7):
                timer_harvest.reset()
                continue
            # 御魂觉醒加成
            if self.appear_then_click(self.I_HARVEST_SOUL, interval=1):
                timer_harvest.reset()
                continue
            # 寮包
            if self.appear_then_click(self.I_HARVEST_GUILD_REWARD, interval=2):
                timer_harvest.reset()
                continue
            # 自选御魂
            if not skip_default and self.appear(self.I_HARVEST_SOUL_1):
                logger.info('Select soul 2')
                self.ui_click(self.I_HARVEST_SOUL_1, stop=self.I_HARVEST_SOUL_2)
                self.ui_click(self.I_HARVEST_SOUL_2, stop=self.I_HARVEST_SOUL_3, interval=3)
                self.ui_click_until_disappear(click=self.I_HARVEST_SOUL_3)
                timer_harvest.reset()

            # 红色的关闭
            if self.appear(self.I_LOGIN_RED_CLOSE):
                self.click(self.I_LOGIN_RED_CLOSE, interval=2)
                timer_harvest.reset()
                continue

            # 五秒内没有发现任何奖励，退出
            if not timer_harvest.started():
                timer_harvest.start()
            else:
                if timer_harvest.reached():
                    logger.info('No more reward')
                    return

    def set_specific_usr(self, character: str):
        self.character = character
        self.O_LOGIN_SPECIFIC_SERVE.keyword = character

    def harvest_mail(self) -> bool:
        if not self.appear_multi_scale(self.I_HARVEST_MAIL,scale_range=(0.8, 1.1)) and \
                not self.appear(self.I_HARVEST_MAIL_COPY):
            if not self.appear(self.I_READ_ALL_MAIL):
                return False
        logger.info('Harvest mail')
        while 1:
            self.screenshot()
            if self.appear(self.I_READ_ALL_MAIL):
                break
            if self.appear_then_click_multi_scale(self.I_HARVEST_MAIL, interval=1.5, scale_range=(0.8, 1.1)):
                continue
            if self.appear_then_click(self.I_HARVEST_MAIL_COPY, interval=1.5):
                continue
        timeout_timer = Timer(3).start()
        logger.info('Exec harvest mail')
        while 1:
            self.screenshot()
            if timeout_timer.reached():
                break
            if self.appear_then_click(self.I_HARVEST_MAIL_CONFIRM, interval=0.8):
                break

            if self.appear_then_click(self.I_READ_ALL_MAIL, interval=1.5):
                continue
            if self.appear_then_click(self.I_HARVEST_MAIL_ALL, interval=1.5):
                continue
            if self.appear_then_click(self.I_MAIL_RED_POINT, interval=4):
                continue
        self.ui_click_until_disappear(self.I_LOGIN_RED_CLOSE)
        return True
    
    def harvest_courtyard_affairs(self) -> bool:
        if not self.ui_click_multi_scale(self.I_NOTE, self.I_PAGE, timeout=3, scale_range=(0.8, 1.2)):
            logger.warning('courtyard affairs timeout!')
            return False
        count_success = 0
        while 1:
            self.screenshot()
            if self.appear(self.I_NO_TASKS):
                logger.info('courtyard affairs completed！')
                break
            # 每日六星御魂
            if self.appear_then_click(self.I_HARVEST_SOUL_2, interval=1) \
                    or self.appear_then_click(self.I_HARVEST_SOUL_3, interval=1):
                continue
            # 点击'获得奖励'
            if self.ui_reward_appear_click():
                continue
            # 获得奖励
            if self.appear_then_click(self.I_UI_AWARD, interval=0.2):
                continue
            # 式神满级，是否提取物经验？确定
            if self.appear_then_click(self.I_CONFIRM, interval=1):
                continue

            if self.appear_then_click(self.I_DAILY, interval=1):
                continue
            # 领取成功： 太傻逼了收取结界奖励游戏里面居然没有加上限制
            if self.appear_then_click(self.I_SUCCESS_CLAIMED, interval=1):
                continue
            if self.appear_then_click(self.I_SKIP):# 万花牌跳过
                continue
            if self.appear_then_click(self.I_LOGIN_RED_CLOSE, interval=1):# 万花牌X
                continue
            # 一键完成
            if count_success >= 3:
                logger.info(f'Click complete tasks {count_success} times')
                break
            if self.appear_then_click(self.I_COMPLETE_TASKS, interval=2.3):
                count_success += 1
                continue
        return True
