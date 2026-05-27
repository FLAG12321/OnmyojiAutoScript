"""
FullReset: 模拟器完整重置流程。

按 Layer 3 → 2 → 1 → 0 顺序拆除资源：
- Layer 3: 截图通道（nemu_ipc dll handle, scrcpy server, droidcast）
- Layer 2: ADB 连接
- Layer 1: 模拟器进程（4 档分级 kill）
- Layer 0: Device 实例级 cached_property（**不动**类属性）

类属性 detect_record / click_record / stuck_timer / stuck_timer_long 承载
跨 reset 累积的卡死检测语义，FullReset 永不重置（D10）。
"""

import subprocess
import time
from typing import TYPE_CHECKING

import psutil

from module.base.decorator import del_cached_property
from module.device.platform2.emulator_windows import Emulator
from module.logger import logger

if TYPE_CHECKING:
    from module.device.device import Device


class FullReset:
    """
    模拟器完整重置 — 4 层串行拆除。

    Layer 3-2-0 是 best-effort；Layer 1（进程）是关键路径，4 档分级 kill
    任何一档成功即返回 True。
    """

    def __init__(self, device: 'Device'):
        self.device = device

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def execute(self) -> bool:
        """按 Layer 3 → 2 → 1 → 0 顺序拆除。返回 Layer 1 是否成功。"""
        logger.hr('FullReset start', level=2)
        self._teardown_layer3_screenshot_channels()
        self._teardown_layer2_adb()
        layer1_ok = self._teardown_layer1_process()
        self._teardown_layer0_cached_properties()
        logger.hr(f'FullReset done (layer1_ok={layer1_ok})', level=2)
        return layer1_ok

    # ------------------------------------------------------------------
    # Layer 3: 截图通道
    # ------------------------------------------------------------------

    def _teardown_layer3_screenshot_channels(self) -> None:
        """释放截图通道（NemuIpc dll、scrcpy server、droidcast 等）。"""
        logger.info('FullReset Layer 3: screenshot channels')

        if 'nemu_ipc' in self.device.__dict__:
            try:
                self.device.nemu_ipc.__exit__(None, None, None)
                logger.info('  - nemu_ipc.__exit__() done')
            except Exception as e:
                logger.warning(f'  - nemu_ipc.__exit__() failed: {e}')
            del_cached_property(self.device, 'nemu_ipc')
            logger.info('  - nemu_ipc cached_property cleared')

        if hasattr(self.device, '_scrcpy_server_stop'):
            try:
                self.device._scrcpy_server_stop()
                logger.info('  - scrcpy server stopped')
            except Exception as e:
                logger.warning(f'  - scrcpy stop failed: {e}')

    # ------------------------------------------------------------------
    # Layer 2: ADB
    # ------------------------------------------------------------------

    def _teardown_layer2_adb(self) -> None:
        """断开 ADB 连接。"""
        logger.info('FullReset Layer 2: ADB')
        try:
            serial = self.device.serial
            self.device.adb_client.disconnect(serial)
            logger.info(f'  - adb disconnected {serial}')
        except Exception as e:
            logger.warning(f'  - adb disconnect failed: {e}')

    # ------------------------------------------------------------------
    # Layer 1: 进程（4 档分级 kill）
    # ------------------------------------------------------------------

    def _process_alive(self) -> bool:
        """检测目标 instance 进程是否还在 — 复用 health._process_check。"""
        ok, _reason = self.device.health._process_check()
        return ok

    def _wait_process_dead(self, timeout: float) -> bool:
        """轮询等待进程消失，每 0.5s 查一次。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self._process_alive():
                return True
            time.sleep(0.5)
        return not self._process_alive()

    def _teardown_layer1_process_graceful(self) -> bool:
        """档 1: MuMuManager shutdown_player（≤10s graceful）。"""
        logger.info('  Kill tier 1: MuMuManager shutdown_player')
        instance = self.device.emulator_instance
        if instance is None or instance != Emulator.MuMuPlayer12 or instance.MuMuPlayer12_id is None:
            return False
        exe = instance.emulator.path
        console = Emulator.single_to_console(exe)
        cmd = f'"{console}" api -v {instance.MuMuPlayer12_id} shutdown_player'
        try:
            subprocess.run(cmd, capture_output=True, timeout=10, shell=True)
        except Exception as e:
            logger.warning(f'    shutdown_player command failed: {e}')
            return False
        return self._wait_process_dead(timeout=10)

    def _teardown_layer1_process_force_stop(self) -> bool:
        """档 2: MuMuManager control force_stop（≤5s）。"""
        logger.info('  Kill tier 2: MuMuManager control force_stop')
        instance = self.device.emulator_instance
        if instance is None or instance != Emulator.MuMuPlayer12 or instance.MuMuPlayer12_id is None:
            return False
        exe = instance.emulator.path
        console = Emulator.single_to_console(exe)
        cmd = f'"{console}" control -v {instance.MuMuPlayer12_id} force_stop'
        try:
            subprocess.run(cmd, capture_output=True, timeout=10, shell=True)
        except Exception as e:
            logger.warning(f'    force_stop command failed: {e}')
            return False
        return self._wait_process_dead(timeout=5)

    def _teardown_layer1_process_psutil_kill(self) -> bool:
        """
        档 3: psutil 精准 kill MuMuVMMHeadless.exe --comment {instance.name}。
        psutil.AccessDenied 时退化为仅匹配进程名（D14）。
        """
        logger.info('  Kill tier 3: psutil precise kill')
        instance = self.device.emulator_instance
        if instance is None:
            return False
        target_name = getattr(instance, 'name', None)
        if not target_name:
            return False

        target_pids = []
        warned_unreadable = False
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if proc.info['name'] != 'MuMuVMMHeadless.exe':
                    continue
                cmdline_str = ''
                cmdline_unreadable = False
                try:
                    cmdline_str = ' '.join(proc.info['cmdline'] or [])
                except psutil.AccessDenied:
                    cmdline_unreadable = True
                if f'--comment {target_name}' in cmdline_str:
                    target_pids.append(proc.info['pid'])
                elif cmdline_unreadable:
                    if not warned_unreadable:
                        logger.warning(
                            '    psutil cmdline unavailable, falling back to name-only match'
                        )
                        warned_unreadable = True
                    target_pids.append(proc.info['pid'])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if not target_pids:
            logger.info('    no matching process found, treating as already dead')
            return True

        for pid in target_pids:
            try:
                psutil.Process(pid).kill()
                logger.info(f'    killed pid {pid}')
            except psutil.NoSuchProcess:
                pass
            except Exception as e:
                logger.warning(f'    kill pid {pid} failed: {e}')

        return self._wait_process_dead(timeout=5)

    def _teardown_layer1_process_taskkill(self) -> bool:
        """档 4: Windows taskkill /F /PID 兜底。"""
        logger.info('  Kill tier 4: taskkill /F /PID')
        instance = self.device.emulator_instance
        if instance is None:
            return False
        target_name = getattr(instance, 'name', '')

        target_pids = []
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if proc.info['name'] != 'MuMuVMMHeadless.exe':
                    continue
                cmdline_str = ' '.join(proc.info.get('cmdline') or [])
                if target_name and f'--comment {target_name}' in cmdline_str:
                    target_pids.append(proc.info['pid'])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if not target_pids:
            return True

        for pid in target_pids:
            try:
                subprocess.run(
                    ['taskkill', '/F', '/PID', str(pid)],
                    capture_output=True, timeout=5, shell=False
                )
                logger.info(f'    taskkill /F /PID {pid} issued')
            except Exception as e:
                logger.warning(f'    taskkill pid {pid} failed: {e}')

        return self._wait_process_dead(timeout=5)

    def _teardown_layer1_process(self) -> bool:
        """Layer 1: 进程拆除，MuMu12 用分级 kill，其他模拟器用通用停止。"""
        logger.info('FullReset Layer 1: process')

        if not self._process_alive():
            logger.info('  - process already dead, skip')
            return True

        if self.device.emulator_instance != Emulator.MuMuPlayer12:
            logger.info('  Kill generic emulator via _emulator_stop')
            if not self.device._emulator_function_wrapper(self.device._emulator_stop):
                return False
            return self._wait_process_dead(timeout=10)

        tiers = [
            ('graceful', self._teardown_layer1_process_graceful),
            ('force_stop', self._teardown_layer1_process_force_stop),
            ('psutil_kill', self._teardown_layer1_process_psutil_kill),
            ('taskkill', self._teardown_layer1_process_taskkill),
        ]
        for name, tier in tiers:
            if tier():
                logger.info(f'  - tier {name} succeeded')
                return True
            logger.warning(f'  - tier {name} failed, falling back to next')
        logger.error('FullReset Layer 1: all 4 tiers failed')
        return False

    # ------------------------------------------------------------------
    # Layer 0: Device 实例级 cached_property
    # ------------------------------------------------------------------

    def _teardown_layer0_cached_properties(self) -> None:
        """
        Device 实例级 cached_property 失效（硬件标识符）。

        注意：class-level detect_record / click_record / stuck_timer /
        stuck_timer_long 一律不动（承载跨 reset 累积的卡死检测语义，D10）。
        """
        logger.info('FullReset Layer 0: instance cached_property')
        targets = [
            'emulator_instance',
            'nemu_ipc',  # 即使 Layer 3 已清，幂等
            'root_handle_num',
            'screenshot_handle_num',
            'screenshot_size',
        ]
        for prop in targets:
            if prop in self.device.__dict__:
                del_cached_property(self.device, prop)
                logger.info(f'  - cleared {prop}')


if __name__ == '__main__':
    # Manual REPL smoke test. Usage: ./toolkit/python.exe -m module.device.emulator_reset [config_name]
    # WARNING: This will actually kill the running emulator. Use with care.
    import sys
    from module.config.config import Config
    from module.device.device import Device

    config_name = sys.argv[1] if len(sys.argv) > 1 else 'oas1'
    config = Config(config_name)
    device = Device(config)
    reset = FullReset(device)
    print('Calling FullReset.execute() — this will tear down the emulator!')
    ok = reset.execute()
    print(f'Result: layer1_ok={ok}')
