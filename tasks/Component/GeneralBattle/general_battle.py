# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time
import math
import random
from time import sleep

import cv2
from module.base.timer import Timer

from module.base.utils import get_color, color_similar
from tasks.base_task import BaseTask
from tasks.Component.GeneralInvite.assets import GeneralInviteAssets
from tasks.Component.GeneralBattle.config_general_battle import GreenMarkType, GeneralBattleConfig
from tasks.Component.GeneralBattle.assets import GeneralBattleAssets
from tasks.Component.GeneralBattle.reward_frame import (
    safe_click_rules, weighted_choice, FORBIDDEN_DEFAULT,
    get_detector, FrozenRowsDetector, locate_rule, shift_down_to_safe,
    MULTI_CLICK_SIZES, MULTI_CLICK_WEIGHTS, MULTI_CLICK_GAP_S,
    MULTI_CLICK_MAX_S, MULTI_CLICK_JITTER_PROB, MULTI_CLICK_JITTER_RANGE,
    SETTLEMENT_REUSE_PROB, SETTLEMENT_REUSE_EXACT,
    SETTLEMENT_REUSE_RADIUS, SETTLEMENT_REUSE_TTL_S)
from tasks.Component.GeneralBattle.config_general_battle import GreenMarkType, GeneralBattleConfig
from tasks.Component.GeneralBuff.config_buff import BuffClass
from tasks.Component.GeneralBuff.general_buff import GeneralBuff

from module.logger import logger


class GeneralBattle(GeneralBuff, GeneralBattleAssets):
    """
    使用这个通用的战斗必须要求这个任务的config有config_general_battle
    """

    def run_general_battle(self, config: GeneralBattleConfig = None, buff: BuffClass or list[BuffClass] = None) -> bool:
        """
        运行脚本
        :return:
        """
        logger.hr("General battle start", 2)
        if config is None:
            config = GeneralBattleConfig()
        # 本人选择的策略是只要进来了就算一次，不管是不是打完了
        # 战斗统计
        self.current_count += 1
        logger.info(f"Current count: {self.current_count}")
        # 战前设置
        self.battle_before(buff, config)
        # 绿标
        if self.is_in_battle(False):
            self.green_mark(config.green_enable, config.green_mark)
        # 战中设置
        win = self.battle_wait(config.random_click_swipt_enable)
        if win:
            return True
        else:
            return False

    def battle_before(self, buff: BuffClass | list[BuffClass], config: GeneralBattleConfig, timeout: float = 5) -> bool:
        """战斗前设置
        :return: True:进入战斗或点击了准备按钮且识别不到准备按钮了 False:超过timeout s还没有进入战斗且没有点击过准备
        """
        timeout_timer = Timer(timeout).start()
        confed = False
        while not timeout_timer.reached():
            self.screenshot()
            if self.is_in_real_battle(False):  # 战斗阶段
                return True
            if self.appear_then_click(self.I_DISABLE_7DAYS_DIFF_SOUL, interval=0.6):  # 关闭御魂不一致提示
                continue
            if self.appear_then_click(self.I_CONFIRM_CLOSE_DIFF_SOUL, interval=0.6):  # 确认关闭御魂不一致提示
                continue
            if self.is_in_prepare(False):  # 战斗准备阶段
                if not getattr(config, 'lock_team_enable', False):  # 没有锁定阵容
                    if self.current_count == 1 and not confed:  # 第一次战斗且是本次第一次配置
                        self.switch_preset_team(config.preset_enable, config.preset_group, config.preset_team)
                        self.check_and_open_buff(buff)
                        confed = True
                # 点击准备(锁定阵容自动点准备,不锁定阵容前面也已经配置完毕需要点准备)
                if self.appear_then_click(self.I_PREPARE_HIGHLIGHT, interval=0.8):
                    continue
                continue
            # 未知界面, 既不是准备界面也不是战斗界面
            # logger.info('Wait for preparation page')  # 这玩意刷屏
            sleep(random.uniform(0.4, 0.8))
        return False

    def run_general_battle_back(self, config: GeneralBattleConfig = None, exit_four: bool = False) -> bool:
        """
        进入挑战然后直接返回
        :param config:
        :return:
        """
        # 如果没有锁定队伍那么在点击准备后才退出的,退四的话就直接退出
        if not config.lock_team_enable and not exit_four:
            # 点击准备按钮
            self.wait_until_appear(self.I_PREPARE_HIGHLIGHT)
            while 1:
                self.screenshot()
                if self.appear_then_click(self.I_PREPARE_HIGHLIGHT, interval=1.5):
                    continue
                if not (self.appear(self.I_PRESET) or self.appear(self.I_PRESET_WIT_NUMBER)):
                    break
            logger.info(f"Click {self.I_PREPARE_HIGHLIGHT.name}")

        # 点击返回
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_EXIT, interval=1.5):
                continue
            if self.appear(self.I_EXIT_ENSURE):
                break
        logger.info(f"Click {self.I_EXIT.name}")

        # 点击返回确认
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_EXIT_ENSURE, interval=1.5):
                continue
            if self.appear(self.I_FALSE):
                break
        logger.info(f"Click {self.I_EXIT_ENSURE.name}")

        # 点击失败确认
        self.wait_until_appear(self.I_FALSE)
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_FALSE, interval=1.5):
                continue
            if not self.appear(self.I_FALSE):
                break
        logger.info(f"Click {self.I_FALSE.name}")

        return True

    def exit_battle(self, skip_first: bool = False) -> bool:
        """
        在战斗的时候强制退出战斗
        :return:
        """
        if skip_first:
            self.screenshot()

        if not self.appear(self.I_EXIT):
            return False

        # 点击返回
        logger.info(f"Click {self.I_EXIT.name}")
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_EXIT, interval=1.5):
                continue
            if self.appear(self.I_EXIT_ENSURE):
                break

        # 点击返回确认
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_EXIT_ENSURE, interval=1.5):
                continue
            if self.appear_then_click(self.I_FALSE, interval=1.5):
                continue
            if not self.appear(self.I_EXIT):
                break

        return True

    def reward_forbidden(self) -> tuple:
        """本任务的常驻禁止区域预设（720p），与奖励检测无关、永远不点的地方。

        基类返回御魂本/活动本/其他本的默认预设；结界突破、寮突破、探索
        这三类任务的界面布局不同，覆盖本方法换成 FORBIDDEN_KEKKAI。
        """
        return FORBIDDEN_DEFAULT

    def screenshot(self):
        """截图入口：每取到新的一帧就作废奖励检测缓存。

        奖励框是结算动画里逐行出现的——上一帧算出的安全区域，到这一帧可能
        已经压在新出现的那一行上。缓存只活一帧，保证「点击落点按当前页面算」；
        同一帧内的多次调用（算落点 + 奖励框判据 + 退出条件）仍只检测一次。
        """
        image = super().screenshot()
        self._reward_safe_rules = None
        return image

    def reward_click_actions(self):
        """结算奖励与战斗胜利画面的落点：全屏候选挖掉常驻禁点区域与检测出的奖励行。

        战斗胜利画面（I_WIN 出现时）没有奖励框，检测出的禁点行自然为空，
        所以两个画面共用同一套安全区域即可；画面切换到奖励页后奖励行会被
        检测出来并从落点里挖掉。

        检测一次约 60~150ms。缓存粒度是「一帧」（截图入口作废，见 screenshot）：
        同一帧内重复调用复用结果，换帧必重新检测——奖励框逐行出现，
        用上一帧的禁区去点当前帧就可能正好点在刚出现的那一行上。
        检测异常或安全区域被挖空时回退到 C_REWARD_1：它在奖励网格下方（y 623 > 网格底 554），
        不依赖检测就一定安全。

        同一帧只检测一次：检测出的奖励行既用来挖禁区，也作为「仍在奖励页」的
        第二判据缓存下来（见 reward_grid_appear）。
        """
        if getattr(self, '_reward_safe_rules', None) is not None:
            return self._reward_safe_rules

        try:
            rows = get_detector().detect(self.device.image)
            rules = safe_click_rules(self.device.image,
                                     forbidden_preset=self.reward_forbidden(),
                                     detector=FrozenRowsDetector(rows))
        except Exception as e:
            # 模板缺失、截图异常等都不该让整个战斗任务挂掉，回退到恒安全的底部区域
            logger.warning(f'Reward frame detect failed, fallback to C_REWARD_1: {e}')
            rows, rules = [], []
        if not rules:
            rules = [self.C_REWARD_1]

        self._reward_safe_rules = rules
        self._reward_grid_found = bool(rows)
        return rules

    def reward_grid_appear(self, interval: float = None) -> bool:
        """奖励框检测作为「仍在奖励页」的第二判据，与 I_REWARD 系模板并列。

        I_REWARD / I_REWARD_GOLD 认的是奖励页上具体某个图案，遇到没收录过的
        奖励底色或结算动画中间帧会失配——此时页面明明还停在奖励页，却既没人
        点击也会被判成结算结束。奖励框检测认的是网格本身（6 种边框模板 × 3 行
        相位锁定），只要页面上还有奖励框就成立，与奖励内容无关。

        复用 reward_click_actions 的同一份检测结果（同帧只检测一次、换帧必重检，
        见 screenshot），interval 语义与 appear() 一致。
        """
        name = 'REWARD_GRID'
        if interval:
            if name in self.interval_timer:
                if self.interval_timer[name].limit != interval:
                    self.interval_timer[name] = Timer(interval)
            else:
                self.interval_timer[name] = Timer(interval)
            if not self.interval_timer[name].reached():
                return False
        # 顺带保证落点区域与本次判据出自同一份检测结果
        self.reward_click_actions()
        appear = bool(getattr(self, '_reward_grid_found', False))
        if appear and interval:
            self.interval_timer[name].reset()
        return appear

    def settlement_click_grid(self, action, interval: float = None) -> bool:
        """检测到奖励框就点击：I_REWARD 系模板全部失配时的兜底触发。

        落点仍是安全区域（已挖掉奖励行与常驻禁区），不点奖励框本身；
        控件名单列 REWARD_GRID，与 I_REWARD 的连点计数/退避互不干扰。
        """
        if not self.reward_grid_appear(interval=interval):
            return False
        self.settlement_gesture(action, control_name='REWARD_GRID')
        return True

    def settlement_click(self, target, action, interval=None, threshold=None) -> bool:
        """结算专用「出现即点击」：目标出现就在安全区域落点点击，并按概率连点。

        appear_then_click 的结算限定版，两者语义一致（interval 计时器照常管理），
        差别只在点击动作换成 settlement_gesture——按 60/35/5 概率追加双击/三击。
        **连点只允许用在战斗结束（胜利画面）与领取奖励两个场景**，其余点击
        一律继续走 appear_then_click，保持单击语义。
        """
        if not self.appear(target, interval=interval, threshold=threshold):
            return False
        self.settlement_gesture(action, control_name=target.name)
        return True

    def settlement_gesture(self, action, control_name='Reward') -> None:
        """执行一次结算点击手势：首击走正常节奏，其后按概率追加快速连击。

        首击与普通点击完全一致（节奏已在截图入口等满、按压时长/轨迹等拟人化
        维度照常），追加击由 _settlement_extra_clicks 负责。结算循环里不方便
        appear_then_click 的场景（如贪吃鬼连点）可直接调本方法。

        首击落点由 _settlement_point 决定：同一场战斗内会参考上一次结算落点
        （奖励页参考胜利画面那一次），跨场次则回到自由取点。
        """
        x, y, rule = self._settlement_point(action)
        first_ts = time.time()          # 首击发起时刻，作为连点节拍的起点
        self.device.click(x, y, control_name=control_name)
        self._settlement_extra_clicks(rule, x, y, control_name, first_ts)

    def _settlement_point(self, action):
        """算本次结算首击的落点，返回 (x, y, 该落点所在的安全区域)。

        同一场战斗内的落点互相参考：真人按场次切分后，场次内相邻点击事件有
        31.4% 落在完全相同的坐标、43.8% 在 30px 内（中位 38.6px），而跨场次
        （>10s）与重新自由取点不可区分（距离比 1.01）。故复用带 TTL，超时自动
        失效，无需在战斗流程里显式重置。

        胜利画面无奖励框、奖励页有，复用的坐标可能正好被新出现的奖励行覆盖：
        此时保持 x 不变沿 y 向下挪到最近的安全区域（热区本就锚在禁区下方），
        挪不动才回退自由取点。

        只使用本帧已有的安全区域缓存，不额外触发奖励框检测——调用方在
        weighted_choice 时已经算过，这里复用同一份结果。
        """
        rules = getattr(self, '_reward_safe_rules', None)
        last = getattr(self, '_settlement_last', None)
        if (rules and last is not None
                and time.time() - last[2] <= SETTLEMENT_REUSE_TTL_S
                and random.random() < SETTLEMENT_REUSE_PROB):
            point = self._settlement_reuse(rules, last[0], last[1])
            if point is not None:
                x, y, rule = point
                self._settlement_last = (x, y, time.time())
                return x, y, rule
        x, y = action.coord()
        self._settlement_last = (x, y, time.time())
        return x, y, action

    def _settlement_reuse(self, rules, x, y):
        """把上次落点适配到本帧，返回 (x, y, rule)；无法适配返回 None。"""
        rule = locate_rule(rules, x, y)
        if rule is None:
            # 被新出现的奖励行盖住了：保持 x，沿 y 往下挪到最近的安全区域
            shifted = shift_down_to_safe(rules, x, y)
            if shifted is None:
                return None
            y, rule = shifted
            logger.info(f'Settlement point shifted down to ({x}, {y}) by forbidden area')
            return x, y, rule
        # 仍然安全：多数情况用完全相同的坐标，其余在小半径内微调
        if random.random() < SETTLEMENT_REUSE_EXACT:
            return x, y, rule
        d = random.uniform(1.0, SETTLEMENT_REUSE_RADIUS)
        a = random.uniform(0, 2 * math.pi)
        nx = x + int(round(d * math.cos(a)))
        ny = y + int(round(d * math.sin(a)))
        moved = locate_rule(rules, nx, ny)
        # 微调后越界到禁区就放弃微调，退回原坐标（原坐标已确认安全）
        return (nx, ny, moved) if moved is not None else (x, y, rule)

    def _settlement_extra_clicks(self, action, x, y, control_name,
                                 first_ts: float = None) -> None:
        """按真人簇长分布在首击后追加快速连击，对齐真人结算行为。

        追加击的三个特征（MULTI_CLICK_* 常量在 reward_frame.py，取值由真人实采校准）：
        - 次数按真人连击簇长直方图抽样，**在 4 点封顶**：首击点掉奖励页后，
          剩余追加击会落到新出现的界面上（安全区域是按奖励页算的，在新界面
          上那个坐标可能是「再来一局」之类的按钮），所以真人尾部 5~11 点的
          长簇不采用，把最长暴露窗口从 2.20s 压到 0.66s。多点簇内部还把权重
          从 4 点挪向 2/3 点——误触窗口与簇长成正比，而单击占比与连点触发率
          保持真人值不变；
        - 间隔按**节拍补偿**对齐到目标值：device.click 自身要花约 165ms
          （按下-移动-抬起 + 拟人化按压时长 + 轨迹），直接 sleep(gap) 会叠加
          在它上面，实测相邻击间隔 334~381ms，是设定值 150~220ms 的两倍
          （QMUMU1/2/3 日志实测）。这里改为「距上一击已过多久，只补足差额」，
          实测间隔才真正等于真人的 150~220ms；
        - device.click 传 pace=False 绕过操作节奏 CD——否则节奏模型的兜底
          等待会把连点拖成秒级间隔；节奏与同资源退避只在首击记账，整次手势
          视作一个意图；
        - 追加击总时长受 MULTI_CLICK_MAX_S 预算约束，**按 wall clock 计**而非
          累加自己 sleep 了多久——click 本身的耗时不进 sleep 的账，只记 sleep
          会让预算形同虚设（4 点手势预算内 0.66s、实测 1.28s）。超预算立即
          收尾、不补完剩余次数；
        - **落点默认复用首击坐标**（真人簇内 86% 的相邻点击落在同一像素），
          仅 MULTI_CLICK_JITTER_PROB 概率偏移，且偏移量取真人非零位移的量级
          （中位约 11px）而非 0~3px 的持续微抖——「每次都抖一点点」正是真人
          最罕见、脚本最典型的模式；偏移后钳回首击所在的安全矩形，
          绝不因微动越界点进禁点区域；
        - 按压时长、按压轨迹等拟人化维度不受影响，追加击走正常 backend 链路。

        :param first_ts: 首击的发起时刻。节拍以「点击发起」为基准而非「点击返回」，
            否则补偿不掉 click 自身的耗时——那正是实测间隔翻倍的原因。
            缺省取当前时刻（首个间隔会偏长约一次 click 的耗时）。
        """
        n = random.choices(MULTI_CLICK_SIZES, weights=MULTI_CLICK_WEIGHTS)[0]
        if n == 1:
            return
        rx, ry, rw, rh = action.roi_front
        px, py = x, y
        last = first_ts if first_ts is not None else time.time()   # 上一击的发起时刻
        start = last                                               # 整次手势的起点
        for _ in range(n - 1):
            gap = random.uniform(*MULTI_CLICK_GAP_S)
            now = time.time()
            # 下一击最早能发出的时刻：理想节拍点；若上一击本身就耗时超过 gap，
            # 已经追不上节拍，就立刻发出（max 保证不往回等）
            nxt = max(last + gap, now)
            if nxt - start > MULTI_CLICK_MAX_S:
                # 预算按 wall clock 判定，不够再点一击就立即收尾，缩短误触窗口
                break
            if nxt > now:
                sleep(nxt - now)
            last = time.time()          # 本击的发起时刻，作为下一次补偿的基准
            # 默认复用上一击坐标（真人手按住不动）；小概率发生一次真实移动，
            # 移动后作为新的落点继续连点，与真人「点着点着挪了一下」一致
            if random.random() < MULTI_CLICK_JITTER_PROB:
                d = random.uniform(*MULTI_CLICK_JITTER_RANGE)
                a = random.uniform(0, 2 * math.pi)
                px = px + int(round(d * math.cos(a)))
                py = py + int(round(d * math.sin(a)))
                # 贴块边时钳回安全矩形，保证偏移不会越界点进禁点区域
                px = min(max(px, rx), rx + rw - 1)
                py = min(max(py, ry), ry + rh - 1)
            self.device.click(px, py, control_name=control_name, pace=False)

    def battle_wait(self, random_click_swipt_enable: bool) -> bool:
        """
        等待战斗结束 ！！！
        很重要 这个函数是原先写的， 优化版本在tasks/Secret/script_task下。本着不改动原先的代码的原则，所以就不改了
        :param random_click_swipt_enable:
        :return:
        """
        # 有的时候是长战斗，需要在设置stuck检测为长战斗
        # 但是无需取消设置，因为如果有点击或者滑动的话 handle_control_check会自行取消掉
        self.device.stuck_record_add('BATTLE_STATUS_S')
        self.device.click_record_clear()
        # 战斗过程 随机点击和滑动 防封
        logger.info("Start battle process")
        win: bool = False
        while 1:
            self.screenshot()
            # 如果出现赢 就点击, 第二个是针对封魔的图片
            if self.appear(self.I_WIN, threshold=0.8) or self.appear(self.I_DE_WIN):
                logger.info("Battle result is win")
                if self.appear(self.I_DE_WIN):
                    self.ui_click_until_disappear(self.I_DE_WIN)
                win = True
                break

            # 如果出现失败 就点击，返回False
            if self.appear(self.I_FALSE, threshold=0.8):
                logger.info("Battle result is false")
                win = False
                break

            # 如果领奖励
            if self.appear(self.I_REWARD, threshold=0.6):
                win = True
                break

            # 如果领奖励出现金币
            if self.appear(self.I_REWARD_GOLD, threshold=0.8):
                win = True
                break
            # 如果开启战斗过程随机滑动
            if random_click_swipt_enable:
                self.random_click_swipt()

        # 再次确认战斗结果
        logger.info("Reconfirm the results of the battle")
        while 1:
            self.screenshot()
            if win:
                # 点击赢了：全屏减去常驻禁点区域（胜利画面无奖励框，与奖励页共用安全区域），
                # 落点按「面积×人类落点密度」加权挑选、区域内采样由拟人化层完成；
                # 结算场景按概率连点（双击/三击），见 settlement_click
                action_click = weighted_choice(self.reward_click_actions())
                if self.settlement_click(self.I_WIN, action_click, interval=0.5):
                    continue
                if not self.appear(self.I_WIN):
                    break
            else:
                # 如果失败且 点击失败后
                if self.appear_then_click(self.I_FALSE, threshold=0.6):
                    continue
                if not self.appear(self.I_FALSE, threshold=0.6):
                    return False
        # 最后保证能点击 获得奖励
        self.screenshot()
        if not  self.wait_until_appear(self.I_EXTRA_INFO,wait_time=3):
            if not self.wait_until_appear(self.I_REWARD): 
                # 有些的战斗没有下面的奖励，所以直接返回
                logger.info("There is no reward, Exit battle")
                return win
        logger.info("Get reward")
        while 1:
            self.screenshot()
            # 战斗胜利后队长弹出「是否邀请队友继续进行战斗」确认框：
            # 奖励阶段已结束，退出循环交给任务层 check_and_invite 处理，
            # 避免结算左上角 EXTRA_INFO 在弹窗上仍命中导致持续误点（Too many click）
            if self.appear(GeneralInviteAssets.I_GI_SURE):
                logger.info("Invite teammate dialog detected, exit reward loop")
                break
            # 如果出现领奖励；落点按「面积×人类落点密度」加权挑选（热区更容易被选中），
            # 结算场景按概率连点（双击/三击），见 settlement_click
            action_click = weighted_choice(self.reward_click_actions())
            if (self.settlement_click(self.I_REWARD, action_click, interval=1.5) or
                self.settlement_click(self.I_REWARD_GOLD, action_click, interval=1.5) or
                # I_REWARD 系模板失配时的兜底：只要还检测到奖励框就照样点安全区域
                self.settlement_click_grid(action_click, interval=1.5)  #  or
                # self.settlement_click(self.I_REWARD_STATISTICS, action_click, interval=1.5) or
                # self.settlement_click(self.I_REWARD_PURPLE_SNAKE_SKIN, action_click, interval=1.5) or
                # self.settlement_click(self.I_REWARD_GOLD_SNAKE_SKIN, action_click, interval=1.5) or
                # self.settlement_click(self.I_REWARD_EXP_SOUL_4, action_click, interval=1.5) or
                # self.settlement_click(self.I_REWARD_SOUL_5, action_click, interval=1.5) or
                # self.settlement_click(self.I_REWARD_SOUL_6, action_click, interval=1.5)
                ):
                continue
            if self.settlement_click(self.I_EXTRA_INFO, action_click, interval=1.5):
                logger.info(f"Click self.I_EXTRA_INFO.name")
                sleep(1.5)
                continue
            # 未知结算弹窗（皮肤碎片等）：点一下空白区域尝试跳过。
            # 检测到奖励框说明还在奖励页（I_REWARD 只是失配），不能走这条盲点分支
            if self.appear(self.I_STATISTICS) and not self.appear(self.I_REWARD)and not self.appear(self.I_WIN) and not self.appear(GeneralInviteAssets.I_GI_SURE) and not self.reward_grid_appear():
                self.click(self.C_RANDOM_CLICK)  #碎片
                self.appear_then_click(self.I_CONFIRM_CLOSE_DIFF_SOUL) #整个皮肤
                continue
            if (not self.appear(self.I_REWARD) and
                not self.appear(self.I_REWARD_GOLD) and
                not self.appear(self.I_EXTRA_INFO) and
                # 奖励框还在就不算结算结束（与上面的兜底点击同一判据）
                not self.reward_grid_appear()#  and
                # not self.appear(self.I_REWARD_STATISTICS) and
                # not self.appear(self.I_REWARD_PURPLE_SNAKE_SKIN) and
                # not self.appear(self.I_REWARD_GOLD_SNAKE_SKIN) and
                # not self.appear(self.I_REWARD_EXP_SOUL_4) and
                # not self.appear(self.I_REWARD_SOUL_5) and
                # not self.appear(self.I_REWARD_SOUL_6)
                ):
                logger.info(f"break reward loop")
                break

        return win

    def _hook_special_reward(self) -> bool:
        """
        For overwrite https://github.com/runhey/OnmyojiAutoScript/issues/1580
        """
        return False

    def green_mark(self, enable: bool = False, mark_mode: GreenMarkType = GreenMarkType.GREEN_MAIN):
        """
        绿标， 如果不使能就直接返回
        :param enable:
        :param mark_mode:
        :return:
        """
        if enable:
            logger.info("Green is enable")
            x, y = None, None
            match mark_mode:
                case GreenMarkType.GREEN_LEFT1:
                    x, y = self.C_GREEN_LEFT_1.coord()
                    logger.info("Green left 1")
                case GreenMarkType.GREEN_LEFT2:
                    x, y = self.C_GREEN_LEFT_2.coord()
                    logger.info("Green left 2")
                case GreenMarkType.GREEN_LEFT3:
                    x, y = self.C_GREEN_LEFT_3.coord()
                    logger.info("Green left 3")
                case GreenMarkType.GREEN_LEFT4:
                    x, y = self.C_GREEN_LEFT_4.coord()
                    logger.info("Green left 4")
                case GreenMarkType.GREEN_LEFT5:
                    x, y = self.C_GREEN_LEFT_5.coord()
                    logger.info("Green left 5")
                case GreenMarkType.GREEN_MAIN:
                    x, y = self.C_GREEN_MAIN.coord()
                    logger.info("Green main")

            # 等待那个准备的消失
            while 1:
                self.screenshot()
                if not self.appear(self.I_PREPARE_HIGHLIGHT):
                    break

            # 判断有无坐标的偏移
            self.appear_then_click(self.I_LOCAL)
            time.sleep(0.3)
            # 点击绿标
            self.device.click(x, y)

    def switch_preset_team(self, enable: bool = False, preset_group: int = 1, preset_team: int = 1):
        """
        切换预设的队伍， 要求是在不锁定队伍时的情况下
        :param enable:
        :param preset_group:
        :param preset_team:
        :return:
        """
        if not enable:
            logger.info("Preset is disable")
            return None

        logger.info("Preset is enable")
        # 点击预设按钮
        while 1:
            self.screenshot()

            if self.appear(self.I_PRESET_ENSURE):
                break
            # 首个队伍没有满足5个式神，未出现预设按钮的情况下跳出循环
            if self.appear(self.I_PRESENT_LESS_THAN_5):
                break
            if self.appear_then_click(self.I_PRESET, threshold=0.8, interval=1):
                continue
            if self.appear_then_click(self.I_PRESET_WIT_NUMBER, threshold=0.8, interval=1):
                continue
            if self.ocr_appear(self.O_PRESET):
                self.click(self.O_PRESET, interval=1)
                continue
            if self.ocr_appear(self.O_PRESET_FULL):
                self.click(self.O_PRESET_FULL, interval=1)
                continue
        logger.info("Click preset button")

        def get_unselect_color(tmp1, tmp2, tmp3, size):
            # 获取未选择分组的颜色，3组之中必定存在两个颜色相似
            # area 参数格式是（x1,y1,x2,y2）
            color_1 = get_color(self.device.image,
                                (tmp1.roi_back[0], tmp1.roi_back[1],
                                 tmp1.roi_back[0] + size[0], tmp1.roi_back[1] + size[1]))
            color_2 = get_color(self.device.image,
                                (tmp2.roi_back[0], tmp2.roi_back[1],
                                 tmp2.roi_back[0] + size[0], tmp2.roi_back[1] + size[1]))
            color_3 = get_color(self.device.image,
                                (tmp3.roi_back[0], tmp3.roi_back[1],
                                 tmp3.roi_back[0] + size[0], tmp3.roi_back[1] + size[1]))

            if color_similar(color_1, color_2):
                return color_1
            if color_similar(color_2, color_3):
                return color_2
            return color_3

        # 选择预设组
        tmp = self.__getattribute__("C_PRESET_GROUP_" + str(preset_group))
        if tmp is None:
            tmp = self.C_PRESET_GROUP_1
        color_size = [self.C_PRESET_GROUP_1.roi_back[2],
                      self.C_PRESET_GROUP_1.roi_back[3]]
        # unselected_color = get_unselect_color(self.C_PRESET_GROUP_1, self.C_PRESET_GROUP_2, self.C_PRESET_GROUP_3, size=color_size)
        # 考虑到有些预设组没有预设，所以这里取一个比较固定的颜色
        unselected_color = (224.9, 208.3, 187.4)
        while True:
            self.screenshot()
            color_tmp = get_color(self.device.image,
                                  (tmp.roi_back[0], tmp.roi_back[1], tmp.roi_back[0] + color_size[0],
                                   tmp.roi_back[1] + color_size[1]))
            if color_similar(color_tmp, unselected_color):
                self.click(tmp, interval=0.2)
                continue
            break

        logger.info("Select preset group")

        # 选择预设的队伍
        time.sleep(0.5)
        tmp = self.__getattribute__("C_PRESET_TEAM_" + str(preset_team))
        if tmp is None:
            tmp = self.C_PRESET_TEAM_1
        color_size = [5, 5]
        # unselected_color = get_unselect_color(self.C_PRESET_TEAM_1, self.C_PRESET_TEAM_2, self.C_PRESET_TEAM_3, size=color_size )
        unselected_color = (216.8, 185.0, 146.8)
        while True:
            self.screenshot()
            color_tmp = get_color(self.device.image,
                                  (tmp.roi_back[0], tmp.roi_back[1], tmp.roi_back[0] + color_size[0],
                                   tmp.roi_back[1] + color_size[1]))
            if color_similar(color_tmp, unselected_color):
                self.click(tmp, interval=0.2)
                continue
            break

        self.click(tmp)
        logger.info("Select preset team")

        # 点击预设确认
        self.wait_until_appear(self.I_PRESET_ENSURE, wait_time=1)
        while 1:
            self.screenshot()
            if not self.appear(self.I_PRESET_ENSURE):
                break
            if self.appear_then_click(self.I_PRESET_ENSURE, threshold=0.8, interval=0.2):
                continue
        logger.info("Click preset ensure")

    def random_click_swipt(self):
        if 0 <= random.randint(0, 500) <= 3:  # 百分之4的概率
            rand_type = random.randint(0, 2)
            match rand_type:
                case 0:
                    self.click(self.C_RANDOM_CLICK, interval=20)
                case 1:
                    self.swipe(self.S_BATTLE_RANDOM_LEFT, interval=20)
                case 2:
                    self.swipe(self.S_BATTLE_RANDOM_RIGHT, interval=20)
            # 重新设置为长战斗
            # self.device.stuck_record_add('BATTLE_STATUS_S')
        else:
            time.sleep(0.4)  # 这样的好像不对

    # 判断是否在战斗中
    def is_in_battle(self, is_screenshot: bool = True) -> bool:
        """
        判断是否在战斗中
        tip: 因为有friends判别, 所以即使在准备界面也会识别在战斗中
        :return:
        """
        if is_screenshot:
            self.screenshot()
        if self.appear(self.I_BATTLE_INFO) or \
                self.appear(self.I_FRIENDS) or \
                self.appear(self.I_WIN) or \
                self.appear(self.I_FALSE) or \
                self.appear(self.I_REWARD):
            return True
        else:
            return False

    def is_in_real_battle(self, is_screenshot: bool = True):
        """
        判断是否在真正的战斗中(不是战斗准备界面也不是战斗结束界面)
        :param is_screenshot:
        :return:
        """
        if is_screenshot:
            self.screenshot()
        return self.appear(self.I_BATTLE_INFO)

    def is_in_prepare(self, is_screenshot: bool = True) -> bool:
        """
        判断是否在准备中
        :return:
        """
        if is_screenshot:
            self.screenshot()
        if self.appear(self.I_BUFF):
            return True
        elif self.appear(self.I_PREPARE_HIGHLIGHT):
            return True
        elif self.appear(self.I_PREPARE_DARK):
            return True
        elif self.appear(self.I_PRESET) or self.appear(self.I_PRESET_WIT_NUMBER):
            return True
        else:
            return False

    def check_take_over_battle(self, is_screenshot: bool, config: GeneralBattleConfig) -> bool or None:
        """
        中途接入战斗，并且接管
        :return:  赢了返回True， 输了返回False, 不是在战斗中返回None
        """
        if is_screenshot:
            self.screenshot()
        if not self.is_in_battle():
            return None

        return self.run_general_battle(config=config)

    def check_lock(self, enable: bool, lock_image, unlock_image):
        """
        检测是否锁定队伍，
        :param enable:
        :param lock_image:
        :param unlock_image:
        :return:
        """
        if enable:
            logger.info("Lock team")
            while 1:
                self.screenshot()
                if self.appear(lock_image):
                    logger.info("Lock team")
                    break
                if self.appear_then_click(unlock_image, interval=1):
                    continue
        else:
            logger.info("Unlock team")
            while 1:
                self.screenshot()
                if self.appear(unlock_image):
                    break
                if self.appear_then_click(lock_image, interval=1):
                    continue

    def check_and_open_buff(self, buff: BuffClass or list[BuffClass] = None):
        """
        检测是否开启buff
        :param buff:
        :return:
        """
        if not buff:
            return
        logger.info(f'Open buff {buff}')
        self.ui_click(self.I_BUFF, self.I_CLOUD, interval=2)
        if isinstance(buff, BuffClass):
            buff = [buff]
        match_method = {
            BuffClass.AWAKE: (self.awake, True),
            BuffClass.SOUL: (self.soul, True),
            BuffClass.GOLD_50: (self.gold_50, True),
            BuffClass.GOLD_100: (self.gold_100, True),
            BuffClass.EXP_50: (self.exp_50, True),
            BuffClass.EXP_100: (self.exp_100, True),
            BuffClass.AWAKE_CLOSE: (self.awake, False),
            BuffClass.SOUL_CLOSE: (self.soul, False),
            BuffClass.GOLD_50_CLOSE: (self.gold_50, False),
            BuffClass.GOLD_100_CLOSE: (self.gold_100, False),
            BuffClass.EXP_50_CLOSE: (self.exp_50, False),
            BuffClass.EXP_100_CLOSE: (self.exp_100, False),
        }
        for b in buff:
            func, is_open = match_method[b]
            func(is_open)
            time.sleep(0.1)
        logger.info(f'Open buff success')
        while 1:
            self.screenshot()
            if not self.appear(self.I_CLOUD):
                break
            if self.appear_then_click(self.I_BUFF, interval=1):
                continue

    def boss_mark(self, enable=True) -> bool:
        if not enable or self._boss_mark_flag:
            return False
        if self.ocr_appear(self.O_BOSS_MARK):
            self.screenshot()
            if self.ocr_appear(self.O_BOSS_MARK):
                self._boss_mark_flag = True
                logger.info('Boss marked')
                self.device.stuck_record_add('BATTLE_STATUS_S')
                return True
        if self.device.click_record.count(str(self.O_BOSS_MARK)) >= 3:
            self._boss_mark_flag = True
            logger.info('Boss mark skipped due to maybe no boss')
            self.device.stuck_record_add('BATTLE_STATUS_S')
            return False
        if self.click(self.O_BOSS_MARK, interval=1.8):
            return False
        return False

    def boss_mark_reset(self):
        self._boss_mark_flag = False


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = GeneralBattle(c, d)
    self = t
    # t.check_buff([BuffClass.EXP_50, BuffClass.GOLD_50])

    img = cv2.imread(r"E:\preset3.png")
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    self.device.image = img


    def get_unselect_color(tmp1, tmp2, tmp3, size):
        # 获取未选择分组的颜色，3组之中必定存在两个颜色相似
        # area 参数格式是（x1,y1,x2,y2）
        color_1 = get_color(self.device.image,
                            (tmp1.roi_back[0], tmp1.roi_back[1],
                             tmp1.roi_back[0] + size[0], tmp1.roi_back[1] + size[1]))
        color_2 = get_color(self.device.image,
                            (tmp2.roi_back[0], tmp2.roi_back[1],
                             tmp2.roi_back[0] + size[0], tmp2.roi_back[1] + size[1]))
        color_3 = get_color(self.device.image,
                            (tmp3.roi_back[0], tmp3.roi_back[1],
                             tmp3.roi_back[0] + size[0], tmp3.roi_back[1] + size[1]))

        if color_similar(color_1, color_2):
            return color_1
        if color_similar(color_2, color_3):
            return color_2
        return color_3


    color_size = [self.C_PRESET_GROUP_1.roi_back[2],
                  self.C_PRESET_GROUP_1.roi_back[3]]
    unselected_color = get_unselect_color(self.C_PRESET_GROUP_1, self.C_PRESET_GROUP_2, self.C_PRESET_GROUP_3,
                                          size=color_size)
    print("")
    color_size = [5, 5]
    unselected_color = get_unselect_color(self.C_PRESET_TEAM_1, self.C_PRESET_TEAM_2, self.C_PRESET_TEAM_3,
                                          size=color_size
                                          )
    print("")
