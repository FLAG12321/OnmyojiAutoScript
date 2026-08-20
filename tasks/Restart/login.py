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

# 庭院稳定确认的延迟。登录弹窗是随机弹出的，点掉一个到下一个冒出来之间会露出一段真实的
# 干净庭院画面，只看一瞬间的截图无法区分「真的进庭院了」和「两个弹窗之间的中间态」。
# 因此首次看到庭院只启动计时、不作数，等这段时间过完再判一次，两次都是干净庭院才认定
# 登录完成；期间弹窗照常处理，中途识别到弹窗就停止计时，等下次看到庭院重新开始。
#
# 这个值必须大于 _login_popup_blocking 里所有弹窗分支的 interval（当前最大 1.6s，红/黄
# 关闭）。弹窗分支的 interval 是点击节流，静默期内 appear 直接返回 False，那些帧会放行
# 庭院判定；只要确认延迟比最大 interval 长，弹窗真还在的话计时必然在走满之前被下一次点击
# 清掉，所以不会误判。调小这个值或调大某个弹窗的 interval 都会破坏该保证，
# test_courtyard_confirm_delay_exceeds_popup_intervals 会拦住。
#
# LOGIN_CHECK 在 Device.stuck_long_wait_list 里，登录的卡死预算是 stuck_timer_long 的
# 300s，这点延迟占不到 1%，不会把正常登录推成误报卡死。
LOGIN_COURTYARD_CONFIRM_DELAY = 2.5

# 勾选标记与目标文本的 y 容差。必须小于半个【条目】间距（实测条目间距约 123px，半间距
# 61px），而不是半个文本行间距——每个条目是「角色名 / 区服名」两行结构，行内间距只有约
# 37px，而勾选标记落在这两行之间，与角色名差约 20px、与区服名差约 17px。所以容差需要
# 大于 20（否则匹到区服名那一行时判不成立）且小于 61（否则勾选在上一条目、目标在下一
# 条目时也会判定成立，登进邻号且日志毫无异常，是静默失效）。
SELECT_CHARACTER_Y_TOLERANCE = 30

# 点击目标后验证选中态的重试次数。每轮先验证再点击，所以「进来时本就已选中」（默认高亮
# 恰好是目标）会零点击直接通过。
SELECT_CHARACTER_CLICK_RETRY = 3

# 角色列表滑动后的等待时间。列表有惯性动画，滑完立刻截图会拍到运动残影导致 OCR 拉花，
# 与 login_account.py 的 switch_character 同口径。
CHARACTER_LIST_SWIPE_DELAY = 1.5

# 查找目标角色时容许的滑动轮数上限。正常情况靠「两轮 OCR 结果相同即到底」收敛，这个上限
# 只防列表因动画未停等原因每轮结果都有细微抖动、导致收敛判定永不成立的死循环。
CHARACTER_LIST_SCROLL_LIMIT = 15


def _normalize_svr(text: str) -> str:
    """统一异体字：游戏内显示「瑤/別」而配置里通常写「瑶/别」。

    角色名的归一已内建在 LoginAccount._is_character_name 内部（对 ocr_text 与目标名
    双向归一），所以这个函数只服务区服名比对，不要再套到角色名上重复处理。
    """
    return text.replace('瑤', '瑶').replace('別', '别')


class LoginHandler(BaseTask, RestartAssets, GameUiAssets):
    character: str
    svr: str
    skip_onmyoji_genie: bool = False

    def __init__(self, *wargs, **kwargs):
        super().__init__(*wargs, **kwargs)
        self.character = self.config.restart.login_character_config.character
        # 独立启动路径只有一个配置字段，角色名与区服名填同一个值。config.py 里该字段的注释
        # 写的是「角色名/服务器名」，双值匹配下用户填哪个都能命中。
        self.svr = self.character
        self.O_LOGIN_SPECIFIC_SERVE.keyword = self.character
        # self.specific_usr = kwargs['config'].

    def _login_popup_blocking(self) -> bool:
        """处理登录期间的弹窗：识别到一个就点掉并立即返回，让调用方重新截图再判。

        登录循环的判定顺序是「先处理弹窗，一个都没有才判庭院」。弹窗背后就是庭院，庭院标识
        照样能匹配到，所以只要还有弹窗在处理，这一轮就不能碰庭院判定。

        注意各分支的 interval 是点击节流：静默期内 appear 会在模板匹配之前直接返回 False，
        于是「弹窗还在、但本轮不点」的帧会让本函数返回 False、放行庭院判定。这不会造成误判，
        因为庭院二次确认的延迟比所有弹窗 interval 都长（见 LOGIN_COURTYARD_CONFIRM_DELAY），
        弹窗真在的话计时必然在走满之前被下一次点击清掉。
        :return: 处理了弹窗返回 True
        """
        # 网络异常
        # if self.ocr_appear(self.O_LOGIN_NETWORK):
        #     logger.error('Network error')
        #     raise RequestHumanTakeover('Network error')

        # 跳过观看视频
        # if self.ocr_appear_click(self.O_LOGIN_SKIP_1, interval=1):
        #     return False
        # 领取抵扣券
        if self.appear_then_click(self.I_OFF_TICKET, interval=1):
            return True
        #领取抵扣券
        if self.appear_then_click(self.I_LOGIN_GET_COUPON, interval=1):
            return True
        # 下载插画
        if self.appear_then_click(self.I_LOGIN_LOAD_DOWN, interval=1):
            logger.info('Download inbetweening')
            return True
        # 不观看视频
        if self.appear_then_click(self.I_WATCH_VIDEO_CANCEL, interval=0.6):
            logger.info('Close video')
            return True
        # 右上角的红色的关闭
        if self.appear_then_click(self.I_LOGIN_RED_CLOSE, interval=1.6):
            logger.info('Close red close')
            return True
        # 左上角的黄色关闭
        if self.appear_then_click(self.I_LOGIN_YELLOW_CLOSE, interval=1.6):
            logger.info('Close yellow close')
            return True
        # 绑定手机号弹窗
        if self.appear_then_click(self.I_LOGIN_LOGIN_GOTO_BIND_PHONE):
            while 1:
                self.screenshot()
                if self.appear_then_click(self.I_LOGIN_LOGIN_CANCEL_BIND_PHONE):
                    logger.info("Close bind phone")
                    break
            return True
        # 关闭各种邀请弹窗(主要时结界卡寄养邀请)
        from tasks.Component.GeneralInvite.assets import GeneralInviteAssets as gia
        if self.appear_then_click(gia.I_I_REJECT, interval=0.8):
            logger.info("reject invites")
            return True
        # 关闭阴阳师精灵提示
        if not self.skip_onmyoji_genie and self.appear_then_click(self.I_LOGIN_LOGIN_ONMYOJI_GENIE):
            logger.info("click onmyoji genie")
            return True
        return False

    def _app_handle_login(self) -> bool:
        """
        最终是在庭院界面
        :return:
        """
        logger.hr('App login')
        self.device.stuck_record_add('LOGIN_CHECK')

        # 庭院二次确认计时器。首次看到干净庭院时才 start，到点后再判一次才认定登录完成；
        # 中途画面不是干净庭院就 clear，等下次重新看到庭院从头计时。
        courtyard_timer = Timer(LOGIN_COURTYARD_CONFIRM_DELAY)
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
            # ── 先处理弹窗，识别到一个就点掉并重来，本轮不碰庭院判定 ──
            # 弹窗背后就是庭院，庭院标识照样能匹配到，所以还有弹窗要处理时判庭院不可信。
            if self._login_popup_blocking():
                # 本轮处理了弹窗，已经开始的二次确认作废，等下次看到庭院重新计时
                courtyard_timer.clear()
                continue
            # ── 所有弹窗都不在，才判断是不是庭院 ──
            # 这些标识出现都意味着已经在庭院里：式神录按钮和展开的卷轴要卷轴展开后才有，
            # 卷轴收起图标与闲庭图标则是卷轴没展开时唯一的证据，少算哪个都会让庭院里的
            # 正常画面被判成「不是庭院」，计时被自己的卷轴动作反复清掉，确认永远走不完。
            # 一律不传 interval：判定的是画面状态而非点击节流，带上会让静默期内的每一帧
            # 都误判成非庭院；桌面端截图间隔可低到 0.05s，这种漏判会让确认永远走不完。
            courtyard_mark = (self.appear(self.I_MAIN_GOTO_SHIKIGAMI_RECORDS)
                              or self.appear(self.I_LOGIN_SCROOLL_OPEN)
                              or self.appear(self.I_LOGIN_SCROOLL_CLOSE, threshold=0.9)
                              or self.appear(self.I_LOGIN_COURTYARD)
                              or self.appear(self.I_LOGIN_COURTYARD2))
            # OCR 比模板匹配贵得多，只在所有图像标识都没命中时才兜底跑一次，结果下面复用
            courtyard_ocr = False if courtyard_mark else self.ocr_appear(self.O_LOGIN_COURTYARD)
            courtyard = courtyard_mark or courtyard_ocr
            # 已看到庭院即可停掉屏幕旋转检测，不必等二次确认通过
            if courtyard:
                login_success = True
            if not courtyard:
                # 还没进庭院，之前的观察作废重新等
                courtyard_timer.clear()
            elif not courtyard_timer.started():
                # 首次看到庭院不能立刻认定：可能只是点掉一个弹窗、下一个还没弹出来的中间态
                courtyard_timer.start()
                logger.info(f'Courtyard appears, confirm again after {LOGIN_COURTYARD_CONFIRM_DELAY}s')
            elif courtyard_timer.reached():
                # 隔了一段时间后再次看到干净庭院，才认定这是稳定的庭院画面
                logger.info('Login to main confirm (courtyard stable)')
                break

            # ── 庭院里该点的东西。触发条件不能换成 courtyard：式神录按钮已经露出来时
            # 再点卷轴区域会把卷轴收回去。这些都是庭院内的动作，不影响已开始的计时。──
            # 确认进入庭院
            if self.appear_then_click(self.I_LOGIN_SCROOLL_CLOSE, interval=2, threshold=0.9):
                logger.info('Open scroll')
                continue
            # 确认进入庭院(优化：当出现闲庭图片时，点击卷轴关闭区域，然后判断式神录按钮出现就代表登录成功)
            if (self.appear(self.I_LOGIN_COURTYARD, interval=0.2)
                    or self.appear(self.I_LOGIN_COURTYARD2, interval=0.2)
                    or courtyard_ocr):
                if self.click(self.C_LOGIN_SCROLL_CLOSE_AREA, interval=2):
                    logger.info('Click scroll close area because courtyard appears')
                    self.screenshot()  # 点击后立即获取最新截图，确保后续状态检查准确
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
                self._select_login_character()
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

    def set_specific_usr(self, character: str, svr: str = None):
        """设置要登录的目标角色名与区服名，两者任一被 OCR 命中即选中该条目。

        @param character: 角色名
        @param svr: 区服名，不传则与角色名同值（兼容只知道其中一个的调用方）
        """
        self.character = character
        self.svr = svr if svr else character
        self.O_LOGIN_SPECIFIC_SERVE.keyword = character

    # ------------------------------------------------------------------
    # 选角：双值匹配 + 向上滑动 + 勾选态验证
    # 判据全部抽成不依赖 device 的静态方法，查找循环只负责截图与调用，便于单测。
    # ------------------------------------------------------------------

    @staticmethod
    def _match_character_index(texts: list, character: str, svr: str) -> int:
        """在一屏 OCR 文本里找目标条目，返回下标；没找到返回 -1。

        角色名与区服名同时参与比对，任一命中即算命中，取第一个命中的（列表从上到下）。
        两者的判据不同：
          - 角色名走 LoginAccount._is_character_name 的宽松匹配，因为等级徽章的数字会被
            OCR 读进同一个文本框（如 '60js15瑶光'）；
          - 区服名是独立一行、左边没有徽章，异体字归一后严格相等即可。
        """
        # Restart 与 SwitchAccount 互相依赖（switch_account.py 顶层已 import LoginHandler），
        # 顶层反向导入会成环，跟随本文件既有惯例做函数内延迟导入。
        from tasks.Component.SwitchAccount.login_account import LoginAccount

        svr_normalized = _normalize_svr(svr) if svr else ''
        for index, text in enumerate(texts):
            if character and LoginAccount._is_character_name(text, character):
                logger.info('Match character name %s at index %d', text, index)
                return index
            if svr_normalized and _normalize_svr(text) == svr_normalized:
                logger.info('Match svr name %s at index %d', text, index)
                return index
        return -1

    @staticmethod
    def _ocr_box_to_roi(ocr_roi: tuple, box) -> list:
        """把 detect_and_ocr 返回的 box（相对 OCR ROI 的四点坐标）换算成设备空间的 roi。

        @return: [x, y, w, h]
        """
        return [
            ocr_roi[0] + box[0][0],
            ocr_roi[1] + box[0][1],
            box[1][0] - box[0][0],
            box[2][1] - box[1][1],
        ]

    @staticmethod
    def _is_select_mark_aligned(mark_y: int, target_y: int) -> bool:
        """勾选标记的中心 y 与目标文本的中心 y 是否落在同一个条目内。"""
        return abs(mark_y - target_y) <= SELECT_CHARACTER_Y_TOLERANCE

    def _select_login_character(self) -> None:
        """在角色列表里选中目标角色并确认登录。

        三个阶段：查找（一轮一次 OCR，未命中则向上滑动重试）→ 选中（点击并用勾选标记验证）
        → 确认（点确认按钮直到列表界面消失）。

        所有失败路径都只记 error 不抛异常，最终一定会走到确认阶段，避免卡在选角界面。
        """
        target_click, target_y = self._find_login_character()

        if target_click is not None:
            self._ensure_character_selected(target_click, target_y)

        # 确认登录。目标为空（未指定角色 / 一条都没识别到）时也走这里，登默认高亮的角色，
        # 与改动前「keyword 为空恒不匹配」的行为一致。
        while True:
            self.screenshot()
            if self.appear(self.I_LOGIN_SPECIFIC_SERVE):
                self.click(self.C_LOGIN_ENSURE_LOGIN_CHARACTER_IN_SAME_SVR, interval=2)
                continue
            break

    def _find_login_character(self):
        """查找目标角色，返回 (点击区域, 目标文本中心 y)；无目标可点时返回 (None, None)。

        一轮只 OCR 一次，用同一份结果比对角色名与区服名；未命中才向上滑动并重新 OCR。
        收敛条件是「两轮 OCR 结果相同即到底」，与 login_account.py 的 switch_character 同口径。
        """
        # 未指定角色和区服：不 OCR、不滑动，直接登默认第一个角色（保持原有行为）
        if not self.character and not self.svr:
            logger.info('No specific character or svr configured, login default one')
            return None, None

        ocr_roi = self.O_LOGIN_SPECIFIC_SERVE.roi
        last_texts = None  # 初值不能是 []，否则首轮空屏会与初值相等而误判到底
        for _ in range(CHARACTER_LIST_SCROLL_LIMIT):
            self.screenshot()
            ocr_res = self.O_LOGIN_SPECIFIC_SERVE.detect_and_ocr(self.device.image)
            texts = [item.ocr_text for item in ocr_res]

            # 空屏保护必须排在收敛判定之前：这一屏没有任何文本时兜底取首条会越界，
            # 而且首轮空屏与初值比较若判成「到底」会直接崩在下标访问上。
            if not texts:
                logger.error('No character text recognized in character list')
                return None, None

            index = self._match_character_index(texts, self.character, self.svr)
            if index < 0:
                if texts == last_texts:
                    # 两轮结果一致说明已滑到底，取当前屏第一条兜底，不中断登录流程
                    logger.error('Character %s / svr %s not found after scrolling to end, '
                                 'fallback to the first one: %s',
                                 self.character, self.svr, texts[0])
                    index = 0
                else:
                    last_texts = texts
                    self.swipe(self.S_LOGIN_CHARACTER_LIST_UP)
                    time.sleep(CHARACTER_LIST_SWIPE_DELAY)
                    continue

            box = ocr_res[index].box
            roi = self._ocr_box_to_roi(ocr_roi, box)
            # 文本框中心 y（设备空间），用于和勾选标记的中心 y 比对
            target_y = ocr_roi[1] + (box[0][1] + box[2][1]) // 2
            return RuleClick(roi, roi, 'character select'), target_y

        logger.error('Character %s / svr %s not found within %d swipes',
                     self.character, self.svr, CHARACTER_LIST_SCROLL_LIMIT)
        return None, None

    def _ensure_character_selected(self, target_click: RuleClick, target_y: int) -> bool:
        """点击目标条目并用勾选标记验证选中态，最多重试 SELECT_CHARACTER_CLICK_RETRY 次。

        每轮先验证再点击，所以默认高亮恰好就是目标时会零点击直接通过。
        验证不通过也只记 error，让调用方继续走确认流程。
        """
        for _ in range(SELECT_CHARACTER_CLICK_RETRY):
            self.screenshot()
            # RuleImage.match 命中时会把位置回写进 roi_front（module/atom/image.py:166），
            # 所以必须在 appear 返回 True 的同一轮里立刻读，不能跨轮缓存。
            if self.appear(self.I_SELECT_CHARACTER):
                mark = self.I_SELECT_CHARACTER.roi_front
                mark_y = mark[1] + mark[3] // 2
                logger.info('Select mark y=%d, target y=%d, delta=%d (tolerance=%d)',
                            mark_y, target_y, abs(mark_y - target_y), SELECT_CHARACTER_Y_TOLERANCE)
                if self._is_select_mark_aligned(mark_y, target_y):
                    logger.info('Target character selected')
                    return True
            self.click(target_click)
            time.sleep(1)

        logger.error('Select mark not aligned with target after %d clicks',
                     SELECT_CHARACTER_CLICK_RETRY)
        return False

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
