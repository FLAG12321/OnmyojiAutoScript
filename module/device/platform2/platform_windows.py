import ctypes
import json
import os
import re
import subprocess
import psutil
from adbutils import AdbDevice, AdbClient

from deploy.utils import DataProcessInfo
from module.base.decorator import run_once
from module.base.timer import Timer
from module.device.handle import Handle
from module.device.platform2.platform_base import PlatformBase
from module.device.platform2.emulator_windows import Emulator, EmulatorInstance, EmulatorManager
from module.logger import logger

import ctypes
from ctypes import wintypes


class EmulatorUnknown(Exception):
    pass

def minimize_by_name(window_name, convert_hidden=True):
    """
    按名称处理窗口状态
    Args:
        window_name (str): 窗口名称（支持部分匹配）
        convert_hidden (bool): 是否将隐藏窗口改为最小化
    """
    def callback(hwnd, lParam):
        title = get_window_title(hwnd)
        if window_name.lower() in title.lower():
            # 检查窗口当前状态
            is_visible = ctypes.windll.user32.IsWindowVisible(hwnd)
            
            if is_visible:
                # 可见窗口 → 最小化
                minimize_window(hwnd)
                logger.info(f'最小化可见窗口: {title}')
            elif convert_hidden:
                # 隐藏窗口 → 改为最小化不激活
                ctypes.windll.user32.ShowWindow(hwnd, 6)  # SW_SHOWMINNOACTIVE
                logger.info(f'隐藏窗口改为最小化: {title}')
        return True
    
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, ctypes.POINTER(ctypes.c_int))
    ctypes.windll.user32.EnumWindows(WNDENUMPROC(callback), None)

def find_hwnd_by_name(window_name):
    """
    枚举所有窗口，返回第一个匹配名称的 hwnd
    """
    target = None
    def callback(hwnd, lParam):
        title = get_window_title(hwnd)
        if window_name.lower() in title.lower():
            nonlocal target
            target = hwnd
            return False  # 停止枚举
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, ctypes.POINTER(ctypes.c_int))
    ctypes.windll.user32.EnumWindows(WNDENUMPROC(callback), None)
    return target
def show_window_by_name(window_name):
    """
    显示指定名称的窗口
    Args:
        window_name (str): 窗口名称（支持部分匹配）
    """
    hwnd = find_hwnd_by_name(window_name)
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW
        set_focus_window(hwnd)
        logger.info(f'显示窗口: {window_name}')
    else:
        logger.info(f'没有找到窗口: {window_name}')

def get_focused_window():
    return ctypes.windll.user32.GetForegroundWindow()


def set_focus_window(hwnd):
    ctypes.windll.user32.SetForegroundWindow(hwnd)


def minimize_window(hwnd):
    ctypes.windll.user32.ShowWindow(hwnd, 6)


def get_window_title(hwnd):
    """Returns the window title as a string."""
    text_len_in_characters = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    string_buffer = ctypes.create_unicode_buffer(
        text_len_in_characters + 1)  # +1 for the \0 at the end of the null-terminated string.
    ctypes.windll.user32.GetWindowTextW(hwnd, string_buffer, text_len_in_characters + 1)
    return string_buffer.value


def flash_window(hwnd, flash=True):
    ctypes.windll.user32.FlashWindow(hwnd, flash)


class AdbDeviceWithStatus(AdbDevice):
    def __init__(self, client: AdbClient, serial: str, status: str):
        self.status = status
        super().__init__(client, serial)

    def __str__(self):
        return f'AdbDevice({self.serial}, {self.status})'

    __repr__ = __str__

    def __bool__(self):
        return True

class PlatformWindows(PlatformBase, EmulatorManager):
    @classmethod
    def execute(cls, command, show_window=True):
        """
        Args:
            command (str):

        Returns:
            subprocess.Popen:
        """

        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        if not show_window:
            startupinfo.wShowWindow = 0  # SW_MINIMIZE - 不显示窗口
        else:
            startupinfo.wShowWindow = 1  # SW_SHOWNORMAL - 正常显示


        command = command.replace(r"\\", "/").replace("\\", "/").replace('"', '"')
        logger.info(f'Execute: {command}')
        return subprocess.Popen(
        command,
        close_fds=True,
        startupinfo=startupinfo
        )
        #return subprocess.Popen(command, close_fds=True)  # only work on Windows

    @classmethod
    def kill_process_by_regex(cls, regex: str) -> int:
        """
        Kill processes with cmdline match the given regex.

        Args:
            regex:

        Returns:
            int: Number of processes killed
        """
        count = 0

        for proc in psutil.process_iter():
            cmdline = DataProcessInfo(proc=proc, pid=proc.pid).cmdline
            if re.search(regex, cmdline):
                logger.info(f'Kill emulator: {cmdline}')
                proc.kill()
                count += 1

        return count

    def _emulator_start(self, instance: EmulatorInstance):
        """
        Start a emulator without error handling
        """
        show_window=not self.config.script.device.emulator_window_minimize and not self.config.script.device.run_background_only
        exe: str = instance.emulator.path
        if instance == Emulator.MuMuPlayer:
            # NemuPlayer.exe
            self.execute(exe, show_window=show_window)
        elif instance == Emulator.MuMuPlayerX:
            # NemuPlayer.exe -m nemu-12.0-x64-default
            self.execute(f'"{exe}" -m {instance.name}', show_window=show_window)
        elif instance == Emulator.MuMuPlayer12:
            # MuMuPlayer.exe -v 0
            if instance.MuMuPlayer12_id is None:
                logger.warning(f'Cannot get MuMu instance index from name {instance.name}')
            self.execute(f'"{exe}" -v {instance.MuMuPlayer12_id}', show_window=show_window)
        elif instance == Emulator.LDPlayerFamily:
            # ldconsole.exe launch --index 0
            self.execute(f'"{Emulator.single_to_console(exe)}" launch --index {instance.LDPlayer_id}', show_window=show_window)
        elif instance == Emulator.NoxPlayerFamily:
            # Nox.exe -clone:Nox_1
            self.execute(f'"{exe}" -clone:{instance.name}', show_window=show_window)
        elif instance == Emulator.BlueStacks5:
            # HD-Player.exe --instance Pie64
            self.execute(f'"{exe}" --instance {instance.name}', show_window=show_window)
        elif instance == Emulator.BlueStacks4:
            # Bluestacks.exe -vmname Android_1
            self.execute(f'"{exe}" -vmname {instance.name}', show_window=show_window)
        elif instance == Emulator.MEmuPlayer:
            # MEmu.exe MEmu_0
            self.execute(f'"{exe}" {instance.name}', show_window=show_window)
        else:
            raise EmulatorUnknown(f'Cannot start an unknown emulator instance: {instance}')

    def _emulator_stop(self, instance: EmulatorInstance):
        """
        Stop a emulator without error handling
        """
        exe: str = instance.emulator.path
        if instance == Emulator.MuMuPlayer:
            # MuMu6 does not have multi instance, kill one means kill all
            # Has 4 processes
            # "C:\Program Files\NemuVbox\Hypervisor\NemuHeadless.exe" --comment nemu-6.0-x64-default --startvm
            # "E:\ProgramFiles\MuMu\emulator\nemu\EmulatorShell\NemuPlayer.exe"
            # E:\ProgramFiles\MuMu\emulator\nemu\EmulatorShell\NemuService.exe
            # "C:\Program Files\NemuVbox\Hypervisor\NemuSVC.exe" -Embedding
            self.kill_process_by_regex(
                rf'('
                rf'NemuHeadless.exe'
                rf'|NemuPlayer.exe\"'
                rf'|NemuPlayer.exe$'
                rf'|NemuService.exe'
                rf'|NemuSVC.exe'
                rf')'
            )
        elif instance == Emulator.MuMuPlayerX:
            # MuMu X has 3 processes
            # "E:\ProgramFiles\MuMu9\emulator\nemu9\EmulatorShell\NemuPlayer.exe" -m nemu-12.0-x64-default -s 0 -l
            # "C:\Program Files\Muvm6Vbox\Hypervisor\Muvm6Headless.exe" --comment nemu-12.0-x64-default --startvm xxx
            # "C:\Program Files\Muvm6Vbox\Hypervisor\Muvm6SVC.exe" --Embedding
            self.kill_process_by_regex(
                rf'('
                rf'NemuPlayer.exe.*-m {instance.name}'
                rf'|Muvm6Headless.exe'
                rf'|Muvm6SVC.exe'
                rf')'
            )
        elif instance == Emulator.MuMuPlayer12:
            # MuMuManager.exe api -v 1 shutdown_player
            if instance.MuMuPlayer12_id is None:
                logger.warning(f'Cannot get MuMu instance index from name {instance.name}')
            self.execute(f'"{Emulator.single_to_console(exe)}" api -v {instance.MuMuPlayer12_id} shutdown_player')
        elif instance == Emulator.LDPlayerFamily:
            # ldconsole.exe quit --index 0
            self.execute(f'"{Emulator.single_to_console(exe)}" quit --index {instance.LDPlayer_id}')
        elif instance == Emulator.NoxPlayerFamily:
            # Nox.exe -clone:Nox_1 -quit
            self.execute(f'"{exe}" -clone:{instance.name} -quit')
        elif instance == Emulator.BlueStacks5:
            # BlueStack has 2 processes
            # C:\Program Files\BlueStacks_nxt_cn\HD-Player.exe --instance Pie64
            # C:\Program Files\BlueStacks_nxt_cn\BstkSVC.exe -Embedding
            self.kill_process_by_regex(
                rf'('
                rf'HD-Player.exe.*"--instance" "{instance.name}"'
                rf')'
            )
        elif instance == Emulator.BlueStacks4:
            # E:\Program Files (x86)\BluestacksCN\bsconsole.exe quit --name Android
            self.execute(f'"{Emulator.single_to_console(exe)}" quit --name {instance.name}')
        elif instance == Emulator.MEmuPlayer:
            # F:\Program Files\Microvirt\MEmu\memuc.exe stop -n MEmu_0
            self.execute(f'"{Emulator.single_to_console(exe)}" stop -n {instance.name}')
        else:
            raise EmulatorUnknown(f'Cannot stop an unknown emulator instance: {instance}')

    def _emulator_function_wrapper(self, func: callable):
        """
        Args:
            func (callable): _emulator_start or _emulator_stop

        Returns:
            bool: If success
        """
        try:
            func(self.emulator_instance)
            return True
        except OSError as e:
            msg = str(e)
            # OSError: [WinError 740] 请求的操作需要提升。
            if 'WinError 740' in msg:
                logger.error('To start/stop MumuAppPlayer, ALAS needs to be run as administrator')
        except EmulatorUnknown as e:
            logger.error(e)
        except Exception as e:
            logger.exception(e)

        logger.error(f'Emulator function {func.__name__}() failed')
        return False

    def _is_emulator_process_alive(self) -> bool:
        """
        Generic check: is the emulator process still running?
        Uses psutil to find a process matching the emulator executable.
        """
        instance = self.emulator_instance
        if instance is None:
            return False

        exe = instance.emulator.path
        if not exe:
            return False

        exe_name = os.path.basename(exe).lower()

        for proc in psutil.process_iter(['name', 'pid']):
            try:
                if proc.info['name'] and proc.info['name'].lower() == exe_name:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        return False

    def _query_mumu12_state(self):
        """
        Query MuMu12 emulator instance state via MuMuManager.exe info.

        Returns:
            dict: Parsed JSON info, or None if query failed or not MuMu12
        """
        instance = self.emulator_instance
        if instance is None or instance != Emulator.MuMuPlayer12:
            return None
        if instance.MuMuPlayer12_id is None:
            return None
        exe = instance.emulator.path
        console = Emulator.single_to_console(exe)
        cmd = f'"{console}" info -v {instance.MuMuPlayer12_id}'
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10, shell=True
            )
            if result.returncode == 0 and result.stdout.strip():
                return json.loads(result.stdout.strip())
        except Exception:
            pass
        return None

    def emulator_start_watch(self):
        """
        Wait for emulator to fully start up.
        Checks: ADB connection → shell response → game package → window structure.

        Returns:
            bool: True if startup completed, False if timeout
        """
        logger.hr('Emulator start watch', level=2)
        current_window = get_focused_window()
        serial = self.serial
        logger.info(f'Watching serial: {serial}')

        @run_once
        def show_online(m):
            logger.info(f'Emulator online: {m}')

        @run_once
        def show_ping(m):
            logger.info(f'Command ping: {m}')

        @run_once
        def show_package(m):
            logger.info(f'Found packages: {m}')

        interval = Timer(1).start()
        timeout = Timer(180).start()
        struct_window = Timer(10)
        state_check_timer = Timer(15).start()
        # 阶段超时分配 (总预算 180s):
        #   startup_grace:    模拟器进程启动 (从开始算, 90s)
        #   stuck_grace:      ADB 就绪后等待 player_state → start_finished (仅 MuMu12, 45s)
        #   packages_timeout: state finished 后游戏包出现 (MuMu12) / ADB 就绪后游戏包出现 (其他, 30s)
        #   struct_window:    包出现后窗口结构稳定 (10s)
        # MuMuManager 状态可能滞后；ADB、包和窗口已可用时允许先通过实际可用性验证。
        # 时序 (MuMu12): startup(90s) → ADB → [state finished 或实际可用性验证] → window(10s)
        # 时序 (其他):   startup(90s) → ADB → packages(30s) → window(10s)
        # 最坏情况由外层 timeout(180s) 截断。
        startup_grace = Timer(90).start()
        packages_timeout = Timer(30)
        stuck_grace = Timer(0)

        new_window = 0
        adb_connected = False
        state_finished_seen = False  # Bug 1 fix: packages_timeout only starts after state==start_finished

        while 1:
            interval.wait()
            interval.reset()
            mumu_state_stuck = False
            if timeout.reached():
                logger.warning('Emulator start timeout')
                return False

            # Periodically check emulator state via MuMuManager
            if state_check_timer.reached():
                state_check_timer.reset()
                state = self._query_mumu12_state()
                if state is not None:
                    player_state = state.get('player_state', '')
                    is_started = state.get('is_process_started', False)
                    if not is_started:
                        if not startup_grace.reached():
                            logger.info(f'Emulator process not started yet (player_state={player_state}), keep waiting')
                            continue
                        logger.warning(f'Emulator process not started (player_state={player_state}), aborting watch')
                        return False
                    if adb_connected and player_state != 'start_finished':
                        if not stuck_grace.started():
                            stuck_grace = Timer(45).start()
                            # MuMuManager 状态偶发滞后；继续走 ADB/包/窗口验证，避免可用模拟器被误判超时。
                            logger.info(f'Emulator state is {player_state}, probing ADB readiness while waiting up to 45s for start_finished')
                        elif not stuck_grace.reached():
                            logger.info(f'Emulator state is {player_state}, probing ADB readiness while waiting for start_finished [{stuck_grace.remain():.0f}s]')
                        else:
                            # 状态已超时也先跑完本轮实际可用性检查，避免 ready 边界被误杀。
                            mumu_state_stuck = True
                            logger.warning(f'Emulator state stuck (player_state={player_state}), probing readiness before abort')
                    else:
                        stuck_grace = Timer(0)
                        if adb_connected and player_state == 'start_finished' and not state_finished_seen:
                            state_finished_seen = True
                            packages_timeout.start()
                            logger.info(f'Emulator state reached start_finished, packages_timeout started ({packages_timeout.limit}s)')

            # Detect new emulator window and restore focus
            if current_window != 0 and new_window == 0:
                new_window = get_focused_window()
                if current_window != new_window and not self.config.script.device.emulator_window_minimize and not self.config.script.device.run_background_only:
                    logger.info(f'New window showing up: {new_window}, focus back')
                    set_focus_window(current_window)
                else:
                    new_window = 0

            logger.info(f'Waiting for emulator, remain[{timeout.remain():.1f}s]')

            # Step 1: Check ADB device status
            try:
                devices = self.list_device().select(serial=serial)
            except Exception as e:
                logger.info(f'list_device error (transient): {e}')
                continue

            if not devices:
                if mumu_state_stuck:
                    logger.warning('Emulator stuck and ADB device not visible, aborting watch')
                    return False
                # Device not visible yet, try adb connect
                try:
                    self.adb_client.connect(serial)
                except Exception:
                    pass
                continue

            device: AdbDeviceWithStatus = devices.first_or_none()
            if device.status == 'offline':
                if mumu_state_stuck:
                    logger.warning('Emulator stuck and ADB device is offline, aborting watch')
                    return False
                self.adb_client.disconnect(serial)
                try:
                    self.adb_client.connect(serial)
                except Exception:
                    pass
                continue
            if device.status != 'device':
                if mumu_state_stuck:
                    logger.warning(f'Emulator stuck and ADB status is {device.status}, aborting watch')
                    return False
                continue

            show_online(device)
            if not adb_connected:
                adb_connected = True
                # Bug 1 fix: for MuMu12, packages_timeout deferred to state==start_finished;
                # for non-MuMu12 (state unqueryable), start now (legacy behavior).
                is_mumu12 = self.emulator_instance == Emulator.MuMuPlayer12 if self.emulator_instance else False
                if not is_mumu12:
                    state_finished_seen = True
                    packages_timeout.start()
                    logger.info(f'Non-MuMu12 emulator detected, packages_timeout started immediately ({packages_timeout.limit}s)')

            # Step 2: Verify shell is responsive
            try:
                pong = self.adb_shell(['echo', 'pong'])
            except Exception as e:
                if mumu_state_stuck:
                    logger.warning(f'Emulator stuck and shell not ready: {e}')
                    return False
                logger.info(f'Shell not ready: {e}')
                continue
            show_ping(pong)

            # Step 3: Verify game package exists
            try:
                packages = self.list_app_packages(show_log=False)
            except Exception as e:
                if mumu_state_stuck:
                    logger.warning(f'Emulator stuck and package query failed: {e}')
                    return False
                continue
            if not packages:
                if mumu_state_stuck:
                    logger.warning('Emulator stuck and game packages not found, aborting watch')
                    return False
                if state_finished_seen and packages_timeout.reached():
                    logger.warning(f'Game packages not found within {packages_timeout.limit}s after state_finished, emulator likely stuck')
                    return False
                continue
            show_package(packages)

            # Step 4: Wait for window structure to stabilize
            if not struct_window.started():
                struct_window.start()
            elif struct_window.reached():
                break
            if new_window == 0:
                if mumu_state_stuck:
                    logger.warning('Emulator stuck and no new window detected, aborting watch')
                    return False
                continue
            if not Handle.handle_has_children(hwnd=new_window):
                if mumu_state_stuck:
                    logger.warning('Emulator stuck and window structure not ready, aborting watch')
                    return False
                continue

            break

        emulator_window_minimize = self.config.script.device.emulator_window_minimize
        if (emulator_window_minimize): logger.info(f'Minimize new emulator window: {emulator_window_minimize}')
        if (self.config.script.device.run_background_only):
            logger.info(f'run background only: {self.config.script.device.run_background_only}')
            logger.warning('run_background_only will not show any UI, emulator will run background only')
        if emulator_window_minimize and not self.config.script.device.run_background_only:
            # 直接使用窗口名称最小化
            sleep_time = 3
            logger.info(f'Waiting {sleep_time} seconds before minimizing window')
            Timer(sleep_time).wait()
            target_window_name = self.config.script.device.handle  # 在这里输入你的具体窗口名称
            minimize_by_name(target_window_name)
            logger.info(f'最小化窗口: {target_window_name}')
            # if current_window:
            #     logger.info(f'De-flash current window: {current_window}')
            #     flash_window(current_window, flash=False)

        logger.info('Emulator start completed')
        return True


    def emulator_start(self):
        """Start emulator with watch. Used by game stuck recovery."""
        logger.hr('Emulator start', level=1)
        for trial in range(3):
            if not self._emulator_function_wrapper(self._emulator_start):
                logger.warning(f'Failed to start emulator (attempt {trial + 1}/3)')
                continue

            if self.emulator_start_watch():
                return True
            logger.warning(f'Emulator start watch failed (attempt {trial + 1}/3)')
            self.reset.execute()

        logger.error('Failed to start emulator after 3 attempts')
        return False

    def emulator_stop(self):
        logger.hr('Emulator stop', level=1)
        for trial in range(3):
            if self._emulator_function_wrapper(self._emulator_stop):
                return True
            logger.warning(f'Failed to stop emulator (attempt {trial + 1}/3)')

        logger.error('Failed to stop emulator after 3 attempts')
        return False


if __name__ == '__main__':
    self = PlatformWindows()
    d = self.emulator_instance
    print(d)
