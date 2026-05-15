# This Python file uses the following encoding: utf-8
import time
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.page import page_main
from tasks.DailyAltAcc.utils import DailyAltAccBase


class Courtyard(DailyAltAccBase):
    def run_courtyard(self):
        self.screenshot()
        if self.ui_get_current_page() != page_main:
            self.ui_goto(page_main)
        start_time = time.time()
        while time.time() - start_time < 5:
            self.screenshot()
            if self.appear_rgb(self.I_FINISH):
                self.appear_then_click(self.I_FINISH, interval=1)
                start_time = time.time()
                break
            if self.appear_then_click(self.I_MIAN_TO_TASK, interval=1):
                start_time = time.time()
                continue
            if self.appear_then_click(self.I_T_SPECIAL_FLAG,action=self.C_T_TONORMAL, interval=1):
                start_time = time.time()
                continue
        if time.time() - start_time>=5:
            return False
        click_count = 0
        success_count = 0
        while 1:
            self.screenshot()
            if self.appear(GameUiAssets.I_CHECK_MAIN) or self.appear(self.I_M_MAIN_TO_MAIL):
                break
            if click_count >= 3 or success_count >= 5:
                if self.appear_then_click(self.I_TASK_TO_MAIN, interval=1):
                    time.sleep(1)
                    continue
            if self.get_award_daliy():
                click_count = 0
                continue
            if self.appear_then_click(self.I_SUCCESS,action=self.C_T_EXIT_SUCCESS, interval=1):
                click_count = 0
                success_count += 1
                continue
            if self.appear_rgb(self.I_FINISH):
                if self.appear_then_click(self.I_FINISH, interval=1):
                    click_count += 1
                    continue
        time.sleep(1)
        self.screenshot()
        if self.ui_get_current_page() != page_main:
            self.ui_goto(page_main)


if __name__ == "__main__":
    from module.config.config import Config
    from module.device.device import Device
    c = Config('oas2')
    d = Device(c)
    self = Courtyard(c, d)
    self.screenshot()
    self.run_courtyard()
