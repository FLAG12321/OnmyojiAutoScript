# This Python file uses the following encoding: utf-8
from module.config.config import Config
from module.device.device import Device
from module.logger import logger
from tasks.GameUi.game_ui import GameUi
from tasks.DailyAltAcc.assets import DailyAltAccAssets
from tasks.Plotline.assets import PlotlineAssets


class DailyAltAccBase(GameUi, DailyAltAccAssets):
    """所有子任务的公共基类，提供通用方法和资源"""
    config: Config
    device: Device
    msg: list

    def __init__(self, config: Config, device: Device) -> None:
        super().__init__(config, device)
        self.msg = []

    def get_config(self):
        return self.config.daily_alt_acc

    def _is_exp_extract_dialog(self) -> bool:
        """判断当前弹窗是否为「结界经验提取」弹窗（正文含「提取」）。

        这里刻意不用 ocr_appear：RuleOcr 的 FULL 模式走 base_ocr.filter()，
        整串关键词匹配失败时会降级成「keyword 里任一字符出现即算命中」，
        而「取消」本身就含「取」字，必然误判。改为直接取 OCR 原始文本自行判断
        （与 Restart/login.py 处理区服名的做法一致），语义完全可控。
        """
        try:
            text = self.O_EXP_DAILOG.detect_text(self.device.image)
        except Exception as e:
            # OCR 异常不能影响主流程：识别不出就按原有逻辑点【取消】
            logger.warning(f'经验提取弹窗 OCR 失败，按普通弹窗处理: {e}')
            return False
        if self.O_EXP_DAILOG.keyword in text:
            logger.info(f'检测到结界经验提取弹窗，点击确认提取: [{text}]')
            return True
        return False

    def get_award_daliy(self) -> bool:
        """
        处理日常弹窗奖励（悬赏、协战、签到等）
        """
        self.screenshot()
        if self.appear_then_click(self.I_M_AWARD, action=self.C_MS_REFRESH_ACTION, interval=1):
            return True
        elif self.appear(self.I_M_PICTURE_REFUSE):
            # 【取消】按钮同时出现在「获得插画」与「结界经验提取」两种弹窗上，
            # 且 I_M_PICTURE_REFUSE 的模板就是「取消」二字、ROI 也完全重合，
            # 光靠图片匹配无法区分。因此命中后先用 OCR 读弹窗正文再决定点哪个按钮：
            # 是经验提取弹窗就点【确认】提取（否则点【取消】会让弹窗反复出现，
            # 与庭院事务的【一键完成】形成互点死循环并触发 GameTooManyClickError）。
            if self._is_exp_extract_dialog():
                # O_EXP_DAILOG 的 area 即【确认】按钮区域，click(RuleOcr) 取的是 area
                self.click(self.O_EXP_DAILOG, interval=1)
                return True
            self.appear_then_click(self.I_M_PICTURE_REFUSE, interval=1)
            return True
        elif self.appear_then_click(self.I_M_PICTURE, self.C_MS_REFRESH_ACTION, interval=1):
            return True
        elif self.appear_then_click(self.I_CORD_EXIT, interval=1):
            return True
        elif self.appear_rgb(self.I_CORD_BACK_RED):
            self.appear_then_click(self.I_CORD_BACK_RED, interval=1)
            return True
        elif self.appear_rgb(self.I_M_FRAME_BACK_RED):
            self.appear_then_click(self.I_M_FRAME_BACK_RED, interval=1)
            return True
        elif self.appear_rgb(self.I_T_BACK_RED_SIGN):
            self.appear_then_click(self.I_T_BACK_RED_SIGN, interval=1)
            return True
        elif self.appear_then_click(self.I_T_SIGN_FLAG, action=self.C_T_EXIT_SIGN, interval=1) or \
                self.appear_then_click(self.I_T_SIGN_FLAG2, action=self.C_T_EXIT_SIGN, interval=1):
            return True
        elif self.appear_then_click(PlotlineAssets.I_CLICK_CURSOR, action=self.C_MS_REFRESH_ACTION, interval=1):
            return True
        elif self.appear_then_click(PlotlineAssets.I_PAGE_CLICK_ANY, action=self.C_MS_REFRESH_ACTION, interval=1):
            return True
        else:
            return False


if __name__ == "__main__":
    c = Config('oas3')
    d = Device(c)
    self = DailyAltAccBase(c, d)
