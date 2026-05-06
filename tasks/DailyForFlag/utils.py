# This Python file uses the following encoding: utf-8
from module.config.config import Config
from module.device.device import Device
from tasks.GameUi.game_ui import GameUi
from tasks.DailyForFlag.assets import DailyForFlagAssets
from tasks.Plotline.assets import PlotlineAssets


class DailyForFlagBase(GameUi, DailyForFlagAssets):
    """所有子任务的公共基类，提供通用方法和资源"""
    config: Config
    device: Device
    msg: list

    def __init__(self, config: Config, device: Device) -> None:
        super().__init__(config, device)
        self.msg = []

    def get_config(self):
        return self.config.daily_for_flag

    def get_award_daliy(self) -> bool:
        """
        处理日常弹窗奖励（悬赏、协战、签到等）
        """
        self.screenshot()
        if self.appear_then_click(self.I_M_AWARD, action=self.C_MS_REFRESH_ACTION, interval=1):
            return True
        elif self.appear_then_click(self.I_M_PICTURE_REFUSE, interval=1):
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
    self = DailyForFlagBase(c, d)
