"""只读扫描已打开的模拟器，供模拟器设置页选择并确认保存设备信息。"""
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

from pydantic import BaseModel, Field

from module.device.platform2.emulator_base import verify_mumu_alias


class EmulatorSelection(BaseModel):
    """前端确认保存时提交的设备字段，不接受任意配置覆盖。"""
    serial: str = Field(min_length=1)
    handle: str = Field(min_length=1)
    emulatorinfo_name: str = Field(min_length=1)
    emulatorinfo_path: str = Field(min_length=1)
    emulatorinfo_type: str = 'auto'


def _window_info(hwnd):
    """读取真实窗口标题；窗口在扫描期间关闭时交由调用方跳过。"""
    import win32gui
    import win32process
    import pywintypes

    try:
        if not win32gui.IsWindow(hwnd):
            return None
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return None
        return {'hwnd': hwnd, 'pid': win32process.GetWindowThreadProcessId(hwnd)[1], 'title': title}
    except pywintypes.error:
        return None  # 查询期间退出的窗口不作为可保存的设备。


class _ScanAdbClient:
    """扫描复用固定的运行连接 ADB 程序及服务端口，不依赖全局 adb_path 补丁。"""
    def __init__(self):
        from module.device.connection_attr import ConnectionAttr

        # 仅读取程序选择规则，跳过 ConnectionAttr 初始化和设备连接。
        self.binary = object.__new__(ConnectionAttr).adb_binary
        try:
            self.port = int(os.environ.get('ANDROID_ADB_SERVER_PORT', 5037))
        except ValueError:
            self.port = 5037  # 与运行连接对非法端口配置的处理保持一致。

    def _query(self, command, timeout=2):
        # 每次查询指定同一主机、端口和程序，其他连接不能改变本次扫描的 ADB 服务。
        try:
            result = subprocess.run(
                [self.binary, '-H', '127.0.0.1', '-P', str(self.port), *command],
                capture_output=True, encoding='utf-8', errors='replace', timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except subprocess.SubprocessError as error:
            raise OSError(str(error)) from error
        if result.returncode:
            raise OSError(result.stderr.strip() or 'ADB 查询失败')
        return result.stdout

    def device_list(self):
        # 只暴露在线入口，offline/unauthorized 都不满足别名归属验证条件。
        return [SimpleNamespace(serial=fields[0], status='device')
                for line in self._query(['devices']).splitlines()
                if len(fields := line.split()) == 2 and fields[1] == 'device']

    def device(self, serial):
        # shell 仍经过同一个固定程序和服务端口，并保留调用方的时间预算。
        return SimpleNamespace(serial=serial, shell=lambda command, timeout=2:
                               self._query(['-s', serial, 'shell', command], timeout))


def _mumu_devices(path, warnings, adb_client=None):
    """以 MuMu 官方只读查询关联窗口、桥接/NAT 地址和多开实例。"""
    from module.device.platform2.emulator_windows import Emulator, EmulatorInstance

    console = Emulator.single_to_console(path)
    try:
        result = subprocess.run(
            [console, 'info', '-v', 'all'], capture_output=True,
            encoding='utf-8', errors='replace', timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            raise ValueError(result.stderr.strip() or result.stdout.strip() or '查询失败')
        info = json.loads(result.stdout)
        # 单实例查询和全部实例查询的结构不同，这里只消费全部实例映射。
        if not isinstance(info, dict):
            raise ValueError('模拟器返回的实例列表格式不正确')
        instances = {}
        vm_root = Path(path).parent.parent / 'vms'
        for folder in vm_root.iterdir():
            matched = re.fullmatch(r'MuMuPlayer(?:Global)?-\d+\.\d+-(\d+)', folder.name)
            if folder.is_dir() and matched:
                instances[int(matched.group(1))] = folder.name
        devices = []
        for index, state in info.items():
            if not isinstance(state, dict) or not state.get('is_process_started'):
                continue
            try:
                # 官方输出的窗口句柄为十六进制字符串，不能按十进制解释。
                raw_hwnd = state.get('main_wnd', '')
                hwnd = int(raw_hwnd, 16) if isinstance(raw_hwnd, str) else int(raw_hwnd)
                window = _window_info(hwnd)
                name = instances.get(int(state.get('index', index)))
                host, port = state.get('adb_host_ip'), state.get('adb_port')
                if not window or not name or not host or not port:
                    warnings.append(f"{state.get('name', index)}：窗口或设备信息未就绪，请稍后刷新")
                    continue
                if state.get('pid') and window['pid'] != int(state['pid']):
                    warnings.append(f"{window['title']}：窗口已变化，请重新扫描")
                    continue
                device = {
                    'id': f"{path}:{hwnd}", 'display_name': window['title'],
                    'serial': f'{host}:{port}', 'handle': window['title'],
                    'emulatorinfo_name': name, 'emulatorinfo_path': path,
                    'emulatorinfo_type': Emulator.MuMuPlayer12,
                    '_hwnd': hwnd,
                }
                devices.append(device)
                instance = EmulatorInstance(serial=device['serial'], name=name, path=path)
                # 官方主地址始终保留；EX 只生成候选，同服务在线且 boot_id 一致才增加选项。
                for alias in instance.mumu_serials[1:]:
                    if adb_client is not None and verify_mumu_alias(adb_client, device['serial'], alias):
                        devices.append({**device, 'id': f"{path}:{hwnd}:{alias}", 'serial': alias})
            except (ValueError, TypeError, OSError) as error:
                warnings.append(f"{state.get('name', index)}：读取模拟器信息失败（{error}）")
        return devices
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        warnings.append(f'MuMu 模拟器扫描失败：{error}')
        return []


def _other_devices(path, processes, warnings):
    """复用已有实例解析，仅返回能与当前窗口明确关联的实例。"""
    import win32gui
    import pywintypes
    from module.device.platform2.emulator_windows import Emulator

    windows = []
    def collect(hwnd, _):
        # 非 MuMu 的实例通过窗口所属进程关联，忽略无标题辅助窗口。
        try:
            window = _window_info(hwnd)
            if window and window['pid'] in processes and win32gui.IsWindowVisible(hwnd):
                windows.append(window)
        except (OSError, pywintypes.error):
            pass  # 枚举期间关闭的窗口不属于本次结果。
        return True

    win32gui.EnumWindows(collect, None)
    instances = list(Emulator(path).iter_instances())
    devices = []
    for window in windows:
        cmdline = processes[window['pid']]
        candidates = [instance for instance in instances if instance.name in cmdline]
        if not candidates and len(instances) == 1:
            candidates = instances
        if len(candidates) != 1 or not candidates[0].serial or not candidates[0].name:
            warnings.append(f"{window['title']}：无法确定多开实例，请手动配置")
            continue
        instance = candidates[0]
        devices.append({
            'id': f"{path}:{window['hwnd']}", 'display_name': window['title'],
            'serial': instance.serial, 'handle': window['title'],
            'emulatorinfo_name': instance.name, 'emulatorinfo_path': path,
            'emulatorinfo_type': instance.type, '_hwnd': window['hwnd'],
        })
    return devices


def scan_emulators(adb_client=None):
    """扫描不启动模拟器、不连接游戏，也不修改现有配置。"""
    if sys.platform != 'win32':
        return {'devices': [], 'warnings': ['模拟器窗口扫描目前仅支持 Windows']}
    import psutil
    import pywintypes
    from module.device.platform2.emulator_windows import Emulator

    installations = {}
    warnings = []
    for process in psutil.process_iter(['pid', 'exe', 'cmdline']):
        try:
            path = process.info['exe']
            if path and Path(path).name.lower() == 'mumunxdevice.exe':
                # MuMu 新版把实例窗口放在 nx_device，关闭管理器后也能由实例进程找到主程序。
                main = Path(path).parent.parent.parent.parent / 'nx_main' / 'MuMuNxMain.exe'
                path = str(main) if main.is_file() else None
            if path and Emulator.is_emulator(path):
                path = str(Path(path)).replace('\\', '/')
                installations.setdefault(path, {})[process.pid] = process.info['cmdline'] or []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue  # 系统进程和刚退出的进程不影响其余模拟器的扫描。
    devices = []
    # ADB 不可用只影响别名确认，不能阻断已由官方查询确认的主端口选项。
    adb_client_attempted = adb_client is not None
    for path, processes in installations.items():
        if Emulator.path_to_type(path) == Emulator.MuMuPlayer12:
            # 整次扫描只创建一个客户端，也允许运行连接直接注入自己的同服务客户端。
            if not adb_client_attempted:
                adb_client_attempted = True
                from module.exception import RequestHumanTakeover
                try:
                    adb_client = _ScanAdbClient()
                except (OSError, RequestHumanTakeover) as error:
                    warnings.append(f'ADB 别名暂时无法确认：{error}')
            devices.extend(_mumu_devices(path, warnings, adb_client))
        else:
            try:
                devices.extend(_other_devices(path, processes, warnings))
            except (OSError, pywintypes.error) as error:
                warnings.append(f'{Path(path).name}：扫描失败（{error}）')
    # 同一窗口的主地址与别名是两个选项，只有不同窗口重名才需要保存数值句柄。
    titles = defaultdict(set)
    for device in devices:
        titles[device['display_name']].add(device['_hwnd'])
    for device in devices:
        hwnd = device.pop('_hwnd')
        if len(titles[device['display_name']]) > 1:
            device['handle'] = str(hwnd)
    devices.sort(key=lambda device: (device['display_name'], device['emulatorinfo_name']))
    return {'devices': devices, 'warnings': list(dict.fromkeys(warnings))}
