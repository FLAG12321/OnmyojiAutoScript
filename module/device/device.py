
from collections import deque
from datetime import datetime

# Patch pkg_resources before importing adbutils and uiautomator2
from module.device.pkg_resources import get_distribution
# Just avoid being removed by import optimization
_ = get_distribution

from module.device.env import IS_WINDOWS
from module.base.decorator import del_cached_property
from module.base.timer import Timer
from module.config.utils import get_server_next_update
from module.device.app_control import AppControl
from module.device.control import Control
from module.device.platform2 import Platform
from module.device.screenshot import Screenshot
from module.exception import (GameNotRunningError,
                              GameStuckError,
                              GameTooManyClickError,
                              RequestHumanTakeover,
                              EmulatorNotRunningError)
from module.logger import logger
import time
from enum import Enum


class EmulatorState(Enum):
    COLD = "cold"
    HEALTHY = "healthy"
    ZOMBIE = "zombie"


_VALID_TRANSITIONS = {
    EmulatorState.COLD: {EmulatorState.HEALTHY},
    EmulatorState.HEALTHY: {EmulatorState.ZOMBIE},
    EmulatorState.ZOMBIE: {EmulatorState.COLD},
}


class Device(Platform, Screenshot, Control, AppControl):
    _screen_size_checked = False
    detect_record = set()
    click_record = deque(maxlen=15)
    stuck_timer = Timer(60, count=60).start()
    stuck_timer_long = Timer(300, count=300).start()
    stuck_long_wait_list = ['BATTLE_STATUS_S', 'PAUSE', 'LOGIN_CHECK', 'PREPARE_BEFORE_BATTLE']
    retry_times :int = 0
    def __init__(self, *args, **kwargs):
        self.emulator_state = EmulatorState.COLD
        from module.device.emulator_health import EmulatorHealth
        self.health = EmulatorHealth(self)
        from module.device.emulator_reset import FullReset
        self.reset = FullReset(self)

        # Initialize mixin state. Tolerate EmulatorNotRunningError here since
        # health probe + full_recovery below handle the "emulator down" path.
        try:
            super().__init__(*args, **kwargs)
        except EmulatorNotRunningError:
            logger.warning('super().__init__ saw EmulatorNotRunningError — will probe health below')

        # Probe health first — if the user already has a working emulator,
        # don't disturb it. Only run full_recovery if unhealthy.
        if self.health.is_alive():
            self._transition_to(EmulatorState.HEALTHY)
        else:
            logger.warning(f'Initial health check failed: {self.health.why_dead()}')
            recovered = False
            for trial in range(3):
                if self.full_recovery():
                    recovered = True
                    break
                logger.warning(f'full_recovery attempt {trial + 1}/3 failed')
            if not recovered:
                logger.critical('Failed to bring emulator to HEALTHY after 3 attempts')
                raise RequestHumanTakeover from None

        # Auto-fill emulator info
        if IS_WINDOWS and self.config.script.device.emulatorinfo_type == 'auto':
            _ = self.emulator_instance

        self.screenshot_interval_set()

        # Auto-select the fastest screenshot method
        if self.config.script.device.screenshot_method == 'auto':
            self.run_simple_screenshot_benchmark()

    def _transition_to(self, target: EmulatorState) -> None:
        """Transition emulator state with validation. Same state is a no-op."""
        from module.exception import ScriptError
        if target == self.emulator_state:
            return
        if target not in _VALID_TRANSITIONS[self.emulator_state]:
            raise ScriptError(
                f'Illegal emulator state transition: {self.emulator_state.name} → {target.name}'
            )
        logger.info(f'EmulatorState: {self.emulator_state.name} → {target.name}')
        self.emulator_state = target

    def full_recovery(self) -> bool:
        """
        ZOMBIE/COLD → HEALTHY one-shot recovery.

        Sequence: FullReset.execute() → _emulator_start → emulator_start_watch
        → health.is_alive() verification. Transitions state to HEALTHY on
        success.

        NOTE: this method MUST NOT call itself recursively (D14). If
        emulator_start_watch fails, return False and let the caller decide
        whether to retry; do not invoke full_recovery again from here.
        """
        logger.hr('Device full_recovery', level=1)

        # 1. Tear down any residue (idempotent — best-effort even from COLD).
        self.reset.execute()
        if self.emulator_state != EmulatorState.COLD:
            self._transition_to(EmulatorState.COLD)

        # 2. COLD → HEALTHY: bring up the emulator.
        instance = self._resolve_emulator_instance()
        if instance is None:
            logger.error('full_recovery: emulator instance not found')
            return False

        if not self._emulator_function_wrapper(self._emulator_start):
            logger.warning('full_recovery: _emulator_start failed')
            return False

        if not self.emulator_start_watch():
            logger.warning('full_recovery: emulator_start_watch returned False')
            return False

        if not self.health.is_alive():
            logger.warning(f'full_recovery: health check failed: {self.health.why_dead()}')
            return False

        self._transition_to(EmulatorState.HEALTHY)
        logger.info('full_recovery: HEALTHY')
        return True

    def _resolve_emulator_instance(self):
        """查找模拟器实例，必要时重新发现"""
        if self.emulator_instance is not None:
            return self.emulator_instance

        logger.warning('Emulator instance not found, re-discovering...')
        del_cached_property(self, 'emulator_instance')
        del_cached_property(self, 'emulator_info')
        del_cached_property(self, 'all_emulator_instances')

        if self.emulator_instance is not None:
            logger.info(f'Re-discovered emulator instance: {self.emulator_instance}')
            return self.emulator_instance

        logger.critical(
            f'No emulator with serial "{self.serial}" found, '
            f'please set a correct serial'
        )
        return None

    def run_simple_screenshot_benchmark(self):
        """
        Perform a screenshot method benchmark, test 3 times on each method.
        The fastest one will be set into config.
        """
        logger.info('run_simple_screenshot_benchmark')
        # Check resolution first
        # self.resolution_check_uiautomator2()
        # Perform benchmark
        from module.daemon.benchmark import Benchmark
        bench = Benchmark(config=self.config, device=self)
        method = bench.run_simple_screenshot_benchmark()
        # Set
        self.config.script.device.screenshot_method = method
        self.config.save()

    def handle_night_commission(self, daily_trigger='21:00', threshold=30):
        """
        Args:
            daily_trigger (int): Time for commission refresh.
            threshold (int): Seconds around refresh time.

        Returns:
            bool: If handled.
        """
        update = get_server_next_update(daily_trigger=daily_trigger)
        now = datetime.now()
        diff = (update.timestamp() - now.timestamp()) % 86400
        if threshold < diff < 86400 - threshold:
            return False

        # if GET_MISSION.match(self.image, offset=True):
        #     logger.info('Night commission appear.')
        #     self.click(GET_MISSION)
        #     return True

        return False

    def screenshot(self):
        """
        Returns:
            np.ndarray:
        """
        self.stuck_record_check()

        try:
            super().screenshot()
        except RequestHumanTakeover as e:
            raise RequestHumanTakeover

        if self.handle_night_commission():
            super().screenshot()

        return self.image

    def release_during_wait(self):
        # Scrcpy server is still sending video stream,
        # stop it during wait
        # self.config.script.device.screenshot_method = 'scrcpy'
        if self.config.script.device.screenshot_method == 'scrcpy':
            self._scrcpy_server_stop()
        if self.config.Emulator_ScreenshotMethod == 'nemu_ipc':
            self.nemu_ipc_release()

    def stuck_record_add(self, button):
        """
        当你要设置这个时候检测为长时间的时候，你需要在这里添加
        如果取消后，需要在`stuck_record_clear`中清除
        :param button:
        :return:
        """
        self.detect_record.add(str(button))
        logger.info(f'Add stuck record: {button}')

    def stuck_record_clear(self):
        self.detect_record = set()
        self.stuck_timer.reset()
        self.stuck_timer_long.reset()

    def stuck_record_check(self):
        """
        Raises:
            GameStuckError:
        """
        reached = self.stuck_timer.reached()
        reached_long = self.stuck_timer_long.reached()

        if not reached:
            return False
        if not reached_long:
            for button in self.stuck_long_wait_list:
                if button in self.detect_record:
                    return False

        logger.warning('Wait too long')
        logger.warning(f'Waiting for {self.detect_record}')
        self.stuck_record_clear()

        if self.app_is_running():
            raise GameStuckError(f'Wait too long')
        else:
            raise GameNotRunningError('Game died')

    def handle_control_check(self, button):
        self.stuck_record_clear()
        self.click_record_add(button)
        self.click_record_check()

    def click_record_add(self, button):
        self.click_record.append(str(button))

    def click_record_clear(self):
        self.click_record.clear()

    def click_record_remove(self, button):
        """
        Remove a button from `click_record`

        Args:
            button (Button):

        Returns:
            int: Number of button removed
        """
        removed = 0
        for _ in range(self.click_record.maxlen):
            try:
                self.click_record.remove(str(button))
                removed += 1
            except ValueError:
                # Value not in queue
                break

        return removed

    def click_record_check(self):
        """
        Raises:
            GameTooManyClickError:
        """
        count = {}
        for key in self.click_record:
            count[key] = count.get(key, 0) + 1
        count = sorted(count.items(), key=lambda item: item[1], reverse=True)
        if count[0][1] >= 12:
            logger.warning(f'Too many click for a button: {count[0][0]}')
            logger.warning(f'History click: {[str(prev) for prev in self.click_record]}')
            
            # 特殊处理sa_account_list_up，这是滑动列表的操作，不应该抛出异常
            if str(count[0][0]) == "sa_account_list_up":
                self.click_record_clear()
                return
            self.click_record_clear()
            if self.retry_times >=2:
                self.retry_times = 0
                raise GameTooManyClickError(f'Too many click for a button: {count[0][0]}')
            self.retry_times += 1
            time.sleep(10)
            return
        if len(count) >= 2 and count[0][1] >= 6 and count[1][1] >= 6:
            logger.warning(f'Too many click between 2 buttons: {count[0][0]}, {count[1][0]}')
            logger.warning(f'History click: {[str(prev) for prev in self.click_record]}')
            self.click_record_clear()
            if self.retry_times >= 5:
                self.retry_times = 0
                raise GameTooManyClickError(f'Too many click for a button: {count[0][0]}')
            self.retry_times += 1
            time.sleep(10)
            return

    def disable_stuck_detection(self):
        """
        Disable stuck detection and its handler. Usually uses in semi auto and debugging.
        """
        logger.info('Disable stuck detection')

        def empty_function(*arg, **kwargs):
            return False

        self.click_record_check = empty_function
        self.stuck_record_check = empty_function

    def app_start(self):
        if not self.config.script.error.handle_error:
            logger.critical('No app stop/start, because HandleError disabled')
            logger.critical('Please enable Alas.Error.HandleError or manually login to AzurLane')
            raise RequestHumanTakeover
        super().app_start()
        self.stuck_record_clear()
        self.click_record_clear()

    def app_stop(self):
        if not self.config.script.error.handle_error:
            logger.critical('No app stop/start, because HandleError disabled')
            logger.critical('Please enable Alas.Error.HandleError or manually login to AzurLane')
            raise RequestHumanTakeover
        super().app_stop()
        self.stuck_record_clear()
        self.click_record_clear()


if __name__ == "__main__":
    device = Device(config="oas1")
    # cv2.imshow("imgSrceen", device.screenshot())  # 显示
    # cv2.waitKey(0)
    # cv2.destroyAllWindows()
