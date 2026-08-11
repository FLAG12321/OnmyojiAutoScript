# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import re
import ctypes
from ctypes import c_long, byref, POINTER, Structure, wintypes

from contextlib import contextmanager
from enum import Enum
from time import sleep
from cached_property import cached_property
from anytree import NodeMixin, RenderTree, PreOrderIter
from win32api import GetSystemMetrics, SendMessage, MAKELONG, PostMessage
from win32print import GetDeviceCaps
from win32process import GetWindowThreadProcessId
from win32gui import (GetWindowText, EnumWindows, FindWindow, FindWindowEx,
                      IsWindow, GetWindowRect, GetWindowDC, DeleteObject,
                      SetForegroundWindow, IsWindowVisible, GetDC, GetParent,
                      EnumChildWindows, GetClientRect, ClientToScreen,
                      GetWindowLong, SetWindowPos)
from win32con import (SRCCOPY, DESKTOPHORZRES, DESKTOPVERTRES, WM_LBUTTONUP,
                      WM_LBUTTONDOWN, WM_ACTIVATE, WA_ACTIVE, MK_LBUTTON,
                      WM_NCHITTEST, WM_SETCURSOR, HTCLIENT, WM_MOUSEMOVE,
                      GWL_STYLE, GWL_EXSTYLE, HWND_TOP, SWP_NOMOVE, SWP_SHOWWINDOW)
from module.config.config import Config
from module.logger import logger
from module.exception import *

# 桌面客户端窗口标题（官方桌面版，多开共用同一标题）
DESKTOP_WINDOW_TITLES = ('阴阳师-网易游戏', '阴阳师-MuMu模拟器专版')

# DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2，SetThreadDpiAwarenessContext 的入参
_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)


@contextmanager
def dpi_awareness():
    """临时把当前线程切到 Per-Monitor V2 DPI 感知，退出时恢复原上下文。

    OAS 进程自身未声明 DPI 感知，GDI 调用默认被系统虚拟化成逻辑像素：125% 缩放下
    物理 1280x720 的客户区只报 1024x576，BitBlt 也只能取到画面左上角那一块（右侧
    侧边栏与底部菜单栏直接丢失）。桌面客户端进程是 Per-Monitor DPI 感知、按物理
    像素原生渲染，因此桌面分支的窗口测量与截图必须在感知上下文里做才能拿到完整的
    物理像素画面。用线程级而非进程级，是为了不影响模拟器直控路径既有的逻辑像素假设。
    """
    user32 = ctypes.windll.user32
    previous = None
    try:
        previous = user32.SetThreadDpiAwarenessContext(_PER_MONITOR_AWARE_V2)
    except AttributeError:
        # Windows 10 1607 以前没有该 API，退化为原有逻辑像素行为
        pass
    try:
        yield
    finally:
        if previous:
            user32.SetThreadDpiAwarenessContext(previous)


def _window_total_size(width: int, height: int, style: int, ex_style: int) -> tuple:
    """用 user32.AdjustWindowRectEx 计算包含标题栏/边框的窗口总尺寸 (w, h)。

    pywin32 未导出 AdjustWindowRectEx，故用 ctypes 调用。
    """
    user32 = ctypes.windll.user32

    class RECT(Structure):
        _fields_ = [('left', c_long), ('top', c_long), ('right', c_long), ('bottom', c_long)]

    user32.AdjustWindowRectEx.argtypes = [POINTER(RECT), c_long, c_long, c_long]
    user32.AdjustWindowRectEx.restype = wintypes.BOOL
    rect = RECT(0, 0, width, height)
    user32.AdjustWindowRectEx(byref(rect), style, False, ex_style)
    return rect.right - rect.left, rect.bottom - rect.top


def handle_title2num(title: str) -> int:
    """
    从标题到句柄号
    :param title:
    :return:  如果没有找到就是返回零
    """
    return FindWindow(None, title)


def handle_num2title(num: int) -> str:
    """
    通过句柄号返回窗口的标题，如果传入句柄号不合法则返回None
    :param num:
    :return:
    """
    return None if num is None or num == 0 or num == '' else GetWindowText(num)


def is_handle_valid(num: int) -> bool:
    """
    输入一个句柄号，如果还在返回True
    :param num:
    :return:
    """
    return IsWindow(num)


def handle_num2pid(num: int) -> int:
    """
    通过句柄号获取句柄进程id，如果句柄号非法则返回0
    :param num:
    :return:
    """
    return 0 if num is None or num == 0 or num == '' else GetWindowThreadProcessId(num)[1]


def window_scale_rate() -> float:
    """
    获取window的系统缩放 一遍是1
    :return:
    """
    hDC = GetDC(0)
    # 物理上（真实的）的 横纵向分辨率
    wReal = GetDeviceCaps(hDC, DESKTOPHORZRES)
    hReal = GetDeviceCaps(hDC, DESKTOPVERTRES)
    # 缩放后的 分辨率
    wAfter = GetSystemMetrics(0)
    hAfter = GetSystemMetrics(1)
    # print(wReal, wAfter)
    return round(wReal / wAfter, 2)


class WindowNode(NodeMixin):
    def __init__(self, name, num, parent=None):
        super().__init__()
        self.name = name
        self.num = num
        self.parent = parent

    @classmethod
    def get_tree_depth(cls, root_node: 'WindowNode'):
        if not root_node.children:
            return 1 if root_node else 0
        return max(node.depth for node in root_node.descendants) + 1


class EmulatorFamily(Enum):
    FAMILY_MUMU = 10  # mumu模拟器
    FAMILY_NOX = 20  # 夜神模拟器
    FAMILY_LD = 30  # 雷电模拟器
    FAMILY_MEMU = 40  # 逍遥模拟器
    FAMILY_BLUESTACKS = 50  # 蓝叠模拟器
    FAMILY_OTHER = 60  # 其他模拟器 待定


# 各个模拟器的句柄树*******************************************************************************************************
""""
<MuMu>系列
模拟器的窗口名字
----MuMuPlayer      (!如果是mumu12是MuMuPlayer, 否则是NemuPlayer)
--------nemudisplay

<雷电模拟器系列>
雷电模拟器的窗口名字
----TheRender
--------sub

<夜神模拟器系列>  =====> 这个模拟器窗口很复杂，而且有的时候还会变化
夜神模拟器的窗口名字
----Nox
----Nox
--------toolbar_nox
--------Nox
------------Nox
----------------sub
---Nox
--------Nox
--------Nox    ==> 妈的太多了自己用spy++看吧

<蓝叠模拟器>
蓝叠模拟器的窗口名字
----HD-Player
--------_ctl.W
"""""
# **********************************************************************************************************************

class Handle:
    emulator_list = ['MuMu12',
                     'MuMu',
                     '雷电',
                     '夜神',
                     '蓝叠',
                     '逍遥',
                     '模拟器']  # 最后一个我又不知道还有哪些模拟器
    emulator_handle = {
        # 夜神
        'nox_player': ['root_handle_title', 'Nox'],
        'nox_player_64': ['root_handle_title', 'Nox'],
        'nox_player_family': ['root_handle_title', 'Nox'],
        # 雷电
        'ld_player': ['TheRender'],
        'ld_player_4': ['TheRender'],
        'ld_player_9': ['TheRender'],
        'ld_player_family': ['TheRender'],
        # 逍遥
        'memu_player': ['root_handle_title'],
        'memu_player_family': ['root_handle_title'],
        # mumu
        'mumu_player': ['root_handle_title', 'NemuPlayer'],
        'mumu_player_12': ['root_handle_title', 'MuMuPlayer'],
        'mumu_player_family': ['root_handle_title', 'MuMuPlayer'],
        # 蓝叠
        'bluestacks_5': ['root_handle_title'],
        'bluestacks_family': ['root_handle_title']
    }
    config: Config = None
    is_desktop_window: bool = False
    """是否为桌面客户端模式（serial='desktop'），桌面分支统一用此标志隔离"""

    def __init__(self, config) -> None:
        """

        :param config:
        """
        logger.hr('Handle')
        if self.config is None:
            if isinstance(config, str):
                self.config = Config(config, task=None)
            else:
                self.config = config
        if not self.config.script.device.handle or self.config.script.device.handle == '':
            logger.info('Handle is empty, oas not use handle')
            return

        # 桌面客户端模式：按 PID 定位窗口，跳过模拟器窗口树逻辑
        if self.config.script.device.serial == 'desktop':
            self.root_handle_title = ''
            self.root_handle_num = 0
            self.root_handle = self.config.script.device.handle
            logger.info(f'Desktop handle PID is {self.root_handle}')
            self.root_handle_num = self.find_desktop_window_by_pid(self.root_handle)
            self.root_handle_title = DESKTOP_WINDOW_TITLES[0]
            self.is_desktop_window = True
            logger.info(f'Desktop client window found: title={self.root_handle_title}, hwnd={self.root_handle_num}')
            return

        # 获取根的句柄
        self.root_handle_title = ''
        self.root_handle_num = 0
        self.root_handle = self.config.script.device.handle
        logger.info(f'Handle is {self.root_handle}')
        if self.root_handle == "auto":
            logger.info('Handle is auto. oas will find window emulator')
            window_list = Handle.all_windows()
            self.root_handle_title = self.auto_handle_title(window_list)
            if not self.root_handle_title:
                logger.error('Auto handle failed, no emulator window found')
                raise EmulatorNotRunningError
            self.root_handle_num = handle_title2num(self.root_handle_title)
        if isinstance(self.root_handle, str):
            try:
                self.root_handle_num = int(self.root_handle)
                logger.info('Handle is a number, using it as root handle num')
                if is_handle_valid(self.root_handle_num):
                    logger.info(f'Handle number {self.root_handle_num} is valid')
                    self.root_handle_title = handle_num2title(self.root_handle_num)
            except ValueError:
                logger.info('Handle is a string, looking up window by title')
                if handle_title2num(self.root_handle) != 0:
                    self.root_handle_num = handle_title2num(self.root_handle)
                    self.root_handle_title = self.root_handle
                else:
                    logger.error(f'Handle title "{self.root_handle}" not found, emulator may not be running')
                    raise EmulatorNotRunningError
        logger.info(f'The root handle title is {self.root_handle_title} and num is {self.root_handle_num}')

        # 获取句柄树（加重试，等待子窗口渲染就绪）
        self.root_node = WindowNode(name=self.root_handle_title, num=self.root_handle_num)
        Handle.handle_tree(self.root_handle_num, self.root_node)
        if not self.root_node.children:
            logger.info('Window child tree not ready, waiting for emulator to finish initializing')
            for i in range(9):
                sleep(1)
                self.root_node = WindowNode(name=self.root_handle_title, num=self.root_handle_num)
                Handle.handle_tree(self.root_handle_num, self.root_node)
                if self.root_node.children:
                    logger.info(f'Window child tree ready after {i + 2} attempts')
                    break
            else:
                logger.warning('Window child tree still not ready after 10 attempts, will use title-based fallback')

        logger.info('Emulator handle structure:')
        for pre, fill, node in RenderTree(self.root_node):
            pass
            #logger.info("%s%s" % (pre, node.name))
        for pre, fill, node in RenderTree(self.root_node):
            pass
            #logger.info("%s%s" % (pre, node.num))

        # 判断是哪一个模拟器 通过句柄树结构
        logger.info(f'Emulator family: {self.emulator_family}')
        # window系统的缩放
        logger.info(f'Window screen scale rate: {window_scale_rate()}')
        # screenshot_handle_num 和 screenshot_size 延迟到首次截屏时按需求值，不在初始化时预计算

    def find_desktop_window_by_pid(self, pid) -> int:
        """按 PID 查找桌面客户端窗口，返回窗口句柄；未找到抛 EmulatorNotRunningError。"""
        try:
            pid_int = int(str(pid))
        except (TypeError, ValueError):
            logger.error(f'Invalid desktop PID: {pid}')
            raise EmulatorNotRunningError(f'Invalid desktop client PID: {pid}')

        def enum_cb(hwnd, param):
            param.append(hwnd)
            return True

        windows = []
        EnumWindows(enum_cb, windows)
        for hwnd in windows:
            if GetWindowText(hwnd) in DESKTOP_WINDOW_TITLES:
                _, win_pid = GetWindowThreadProcessId(hwnd)
                if win_pid == pid_int:
                    return hwnd
        logger.error(f'Desktop client window not found, PID={pid}')
        raise EmulatorNotRunningError(f'Desktop client window not found, PID={pid}')

    def desktop_window_exists(self) -> bool:
        """桌面模式：目标 PID 对应窗口仍存在即视为客户端运行中。"""
        try:
            self.find_desktop_window_by_pid(self.config.script.device.handle)
            return True
        except EmulatorNotRunningError:
            return False

    def desktop_client_offset(self) -> tuple:
        """返回客户区在窗口 DC 内的偏移 (x, y)，用于 BitBlt 扣除标题栏/边框。"""
        with dpi_awareness():
            window_rect = GetWindowRect(self.screenshot_handle_num)
            client_origin = ClientToScreen(self.screenshot_handle_num, (0, 0))
            return client_origin[0] - window_rect[0], client_origin[1] - window_rect[1]

    def desktop_client_size(self) -> tuple:
        """返回桌面客户端窗口客户区的物理像素尺寸 (width, height)。"""
        with dpi_awareness():
            rect = GetClientRect(self.screenshot_handle_num)
            return rect[2] - rect[0], rect[3] - rect[1]

    def desktop_client_size_virtual(self) -> tuple:
        """返回客户区在 DPI 虚拟化空间下的尺寸 (width, height)。

        OAS 进程未声明 DPI 感知，PostMessage 的 lParam 会被系统按该空间解释，
        因此后台输入的坐标必须换算到这里，而不是截图所用的物理空间。
        """
        rect = GetClientRect(self.screenshot_handle_num)
        return rect[2] - rect[0], rect[3] - rect[1]

    def desktop_window_set_size(self, width: int = 1280, height: int = 720) -> bool:
        """桌面模式：检测窗口客户区尺寸，非目标大小时用 SetWindowPos 调整到 width×height。

        窗口位置保持不变；返回是否执行了调整。全过程在 DPI 感知上下文内完成，
        GetClientRect/SetWindowPos 处理的都是物理像素，因此目标尺寸无需按缩放比换算，
        调整后客户区物理尺寸恰为 width×height，游戏画面与资产 1:1 对应。
        """
        if not getattr(self, 'is_desktop_window', False):
            return False
        hwnd = self.root_handle_num
        with dpi_awareness():
            client_rect = GetClientRect(hwnd)
            client_w = client_rect[2] - client_rect[0]
            client_h = client_rect[3] - client_rect[1]
            logger.info(f'Desktop client size: {client_w}x{client_h} (physical), target {width}x{height}')
            if client_w == width and client_h == height:
                logger.info('Desktop client size already matches target')
                return False
            # 用 AdjustWindowRectEx 精确计算含标题栏/边框的窗口总尺寸
            style = GetWindowLong(hwnd, GWL_STYLE)
            ex_style = GetWindowLong(hwnd, GWL_EXSTYLE)
            total_w, total_h = _window_total_size(width, height, style, ex_style)
            logger.info(f'Resize desktop window to {total_w}x{total_h} to get client {width}x{height}')
            try:
                SetWindowPos(hwnd, HWND_TOP, 0, 0, total_w, total_h, SWP_NOMOVE | SWP_SHOWWINDOW)
            except Exception as e:
                # 拒绝访问通常是目标窗口权限更高（游戏以管理员运行）或客户端锁定窗口大小
                logger.error(f'SetWindowPos failed: {e}. '
                             f'请以管理员身份运行 OAS，或手动把游戏窗口设为 1280x720')
                return False
            # 校准：SetWindowPos 后的实际客户区可能与目标差几像素，按差值持续修正
            for _ in range(5):
                cr = GetClientRect(hwnd)
                cw, ch = cr[2] - cr[0], cr[3] - cr[1]
                if cw == width and ch == height:
                    return True
                total_w += width - cw
                total_h += height - ch
                logger.info(f'Calibrate desktop window to {total_w}x{total_h}, current client {cw}x{ch}')
                SetWindowPos(hwnd, HWND_TOP, 0, 0, total_w, total_h, SWP_NOMOVE | SWP_SHOWWINDOW)
            return True

    @staticmethod
    def all_windows() -> list:
        """
        获取桌面上的所有窗体

        :return:  类似这样['MuMu模拟器']
        """

        def enum_windows_callback(hwnd, windows):
            window_text = GetWindowText(hwnd)
            windows.append(window_text)

        windows = []
        EnumWindows(enum_windows_callback, windows)
        return windows

    @classmethod
    def auto_handle_title(cls, windows: list) -> str:
        """
        返回第一个找到的有模拟器的标题
        :param windows:
        :return:
        """
        if windows is None:
            logger.error("auto_handle_title: windows list is None")

        emu_list = []
        for window_title in windows:
            for item in Handle.emulator_list:
                if window_title.find(item) != -1:
                    emu_list.append(window_title)

        if not len(emu_list):
            logger.error('Can not find emulator handle, please check your emulator is running')
            return None

        emulator_title = ''
        # 测试mumu12的时候发现 获取的全部的窗体标题有这样的: 'MuMuPlayer', 'MuMuPlayer', 'MuMuPlayer', 'MuMu模拟器12'
        # 事实上 我们只需要最后一个 'MuMu模拟器12'，其他的不重要
        if 'MuMu模拟器12' in emu_list and 'MuMuPlayer' in emu_list:
            emulator_title = 'MuMu模拟器12'
        
        # MuMu5.0更新，窗体标题改动: 'MuMu模拟器','MuMuNxDevice','MuMu安卓设备'
        # 如果没有匹配上旧版本，尝试匹配MuMu5.0窗口名                                                                  
        if emulator_title == '' and 'MuMu安卓设备' in emu_list:
            emulator_title = 'MuMu安卓设备'

        if len(emu_list) > 1 and emulator_title == '':
            logger.warning(f'Find more than one emulator handle, oas will use the first one {emu_list[0]}')
            emulator_title = emu_list[0]

        if len(emu_list) == 1:
            emulator_title = emu_list[0]

        logger.info(f'Auto-detected emulator window: {emulator_title}')
        return emulator_title

    @staticmethod
    def handle_tree(hwnd, node: WindowNode, level: int = 0) -> None:
        """
        生成一个窗口的句柄树
        :param hwnd:
        :param node:
        :param level:
        :return:
        """
        child_windows = []
        EnumChildWindows(hwnd, lambda hwnd, param: param.append(hwnd), child_windows)

        if not child_windows:
            return
        for child_hwnd in child_windows:
            if GetParent(child_hwnd) == hwnd:
                child_text = GetWindowText(child_hwnd)
                child_node = WindowNode(name=child_text, num=child_hwnd, parent=node)

                # 递归遍历子窗体的子窗体
                Handle.handle_tree(child_hwnd, child_node, level + 1)
    @cached_property
    def emulator_family(self) -> EmulatorFamily:
        """
        通过句柄树来判断这个是那个模拟器大类
        :return:
        """
        children_num = len(self.root_node.children)
        if children_num == 1:  #
            if len(self.root_node.children) == 0:
                logger.error('No children found in root node for emulator detection')
                return EmulatorFamily.FAMILY_OTHER
            name = self.root_node.children[0].name
            if name == 'MuMuPlayer':
                return EmulatorFamily.FAMILY_MUMU
            elif name == 'NemuPlayer':
                return EmulatorFamily.FAMILY_MUMU
            elif name == 'TheRender':
                return EmulatorFamily.FAMILY_LD
            elif name == 'HD-Player':
                return EmulatorFamily.FAMILY_BLUESTACKS
        elif children_num >= 3:
            if len(self.root_node.children) == 0:
                logger.error('No children found in root node for emulator detection')
                return EmulatorFamily.FAMILY_OTHER
            name = self.root_node.children[0].name
            if name == 'Nox':
                return EmulatorFamily.FAMILY_NOX

        # 基于句柄标题的判定
        for emu in Handle.emulator_list:
            if self.root_handle_title.find(emu) != -1:
                if emu == 'MuMu':
                    return EmulatorFamily.FAMILY_MUMU
                elif emu == '雷电':
                    return EmulatorFamily.FAMILY_LD
                elif emu == '夜神':
                    return EmulatorFamily.FAMILY_NOX
                elif emu == '蓝叠':
                    return EmulatorFamily.FAMILY_BLUESTACKS
                elif emu == '逍遥':
                    return EmulatorFamily.FAMILY_MEMU
        return EmulatorFamily.FAMILY_OTHER
    """ @cached_property
    def emulator_family(self) -> EmulatorFamily:
        
        通过句柄树来判断这个是那个模拟器大类
        :return:
        
        children_num = len(self.root_node.children)
        if children_num == 1:  #
            name = self.root_node.children[0].name
            if name == 'MuMuPlayer':
                return EmulatorFamily.FAMILY_MUMU
            elif name == 'MuMuNxDevice':
                return EmulatorFamily.FAMILY_MUMU
            elif name == 'NemuPlayer':
                return EmulatorFamily.FAMILY_MUMU
            elif name == 'TheRender':
                return EmulatorFamily.FAMILY_LD
            elif name == 'HD-Player':
                return EmulatorFamily.FAMILY_BLUESTACKS
        elif children_num >= 3:
            name = self.root_node.children[0].name
            if name == 'Nox':
                return EmulatorFamily.FAMILY_NOX

        # 基于句柄标题的判定
        for emu in Handle.emulator_list:
            if self.root_handle_title.find(emu) != -1:
                if emu == 'MuMu':
                    return EmulatorFamily.FAMILY_MUMU
                elif emu == '雷电':
                    return EmulatorFamily.FAMILY_LD
                elif emu == '夜神':
                    return EmulatorFamily.FAMILY_NOX
                elif emu == '蓝叠':
                    return EmulatorFamily.FAMILY_BLUESTACKS
                elif emu == '逍遥':
                    return EmulatorFamily.FAMILY_MEMU
        return EmulatorFamily.FAMILY_OTHER """

    @cached_property
    def screenshot_handle_num(self) -> int:
        """
        截屏的句柄其实并不是根句柄
        :return:  出错返回None
        """
        if getattr(self, 'is_desktop_window', False):
            return self.root_handle_num
        if self.emulator_family == EmulatorFamily.FAMILY_MUMU:
            # 使用正则匹配12 来判定是不是mumu12这并不是一个好的方法
            if len(self.root_node.children) == 0:
                logger.error('No children found in root node for MuMu emulator')
                return self.root_node.num
            name = self.root_node.children[0].name
            num = self.root_node.children[0].num
            if name == 'MuMuPlayer':
                logger.info('The emulator is MuMu模拟器12')
                return num
            elif name == 'NemuPlayer':
                logger.info('The emulator is MuMu模拟器')
                return num
        # 夜神
        elif self.emulator_family == EmulatorFamily.FAMILY_NOX:
            try:
                if len(self.root_node.children) < 2 or len(self.root_node.children[1].children) < 2:
                    logger.error('Insufficient children nodes for Nox emulator')
                    return self.root_node.num
                return self.root_node.children[1].children[1].num
            except:
                if len(self.root_node.children) < 3 or len(self.root_node.children[2].children) < 2:
                    logger.error('Insufficient children nodes for Nox emulator')
                    return self.root_node.num
                return self.root_node.children[2].children[1].num

        elif self.emulator_family == EmulatorFamily.FAMILY_LD:
            for node in PreOrderIter(self.root_node):
                if node.name == Handle.emulator_handle['ld_player_family'][0]:
                    return node.num

        elif self.emulator_family == EmulatorFamily.FAMILY_MEMU:
            for node in PreOrderIter(self.root_node):
                if node.name == Handle.emulator_handle['memu_player_family']:
                    return node.num

        elif self.emulator_family == EmulatorFamily.FAMILY_BLUESTACKS:
            for node in PreOrderIter(self.root_node):
                if node.name == Handle.emulator_handle['bluestacks_family']:
                    return node.num
        return self.root_node.num
    """  @cached_property
    def screenshot_handle_num(self) -> int:
        
        截屏的句柄其实并不是根句柄
        :return:  出错返回None
        
        if self.emulator_family == EmulatorFamily.FAMILY_MUMU:
            # 使用正则匹配12 来判定是不是mumu12这并不是一个好的方法
            name = self.root_node.children[0].name
            num = self.root_node.children[0].num
            if name == 'MuMuPlayer':
                logger.info('The emulator is MuMu模拟器12')
                return num
            elif name == 'NemuPlayer':
                logger.info('The emulator is MuMu模拟器')
                return num
            elif name == 'MuMuNxDevice':
                logger.info('The emulator is MuMu模拟器5.0')
                return num
        # 夜神
        elif self.emulator_family == EmulatorFamily.FAMILY_NOX:
            try:
                return self.root_node.children[1].children[1].num
            except:
                return self.root_node.children[2].children[1].num

        elif self.emulator_family == EmulatorFamily.FAMILY_LD:
            for node in PreOrderIter(self.root_node):
                if node.name == Handle.emulator_handle['ld_player_family'][0]:
                    return node.num

        elif self.emulator_family == EmulatorFamily.FAMILY_MEMU:
            for node in PreOrderIter(self.root_node):
                if node.name == Handle.emulator_handle['memu_player_family']:
                    return node.num

        elif self.emulator_family == EmulatorFamily.FAMILY_BLUESTACKS:
            for node in PreOrderIter(self.root_node):
                if node.name == Handle.emulator_handle['bluestacks_family']:
                    return node.num
        return self.root_node.num """

    @cached_property
    def screenshot_size(self) -> tuple or None:
        """
        第一个是width 第二个是heigth
        2023.7.1 在高缩放的设备上应该输出1280X720
        :return:
        """
        if getattr(self, 'is_desktop_window', False):
            # 桌面模式固定输出 1280x720（物理目标，与资产 1:1）；
            # 实际位图由截图方法截取逻辑客户区后 resize 到该尺寸
            return 1280, 720
        winRect = GetWindowRect(self.screenshot_handle_num)
        scale_rate = window_scale_rate()
        width_before: int = winRect[2] - winRect[0]  # 右x-左x
        height_before: int = winRect[3] - winRect[1]  # 下y - 上y 计算高度
        width, height = width_before, height_before
        if abs((width_before * scale_rate) - 1280) < 5:
            width = 1280
        if abs((height_before * scale_rate) - 720) < 5:
            height = 720
        if width is None or height is None:
            logger.error(f'Get screenshot size error, width={width}, height={height}')
            return None
        return width, height

    @cached_property
    def window_scale_rate(self) -> float:
        """
        获取window的系统缩放 一般是1
        :return:
        """
        hDC = GetDC(0)
        # 物理上（真实的）的 横纵向分辨率
        wReal = GetDeviceCaps(hDC, DESKTOPHORZRES)
        hReal = GetDeviceCaps(hDC, DESKTOPVERTRES)
        # 缩放后的 分辨率
        wAfter = GetSystemMetrics(0)
        hAfter = GetSystemMetrics(1)
        # print(wReal, wAfter)
        return round(wReal / wAfter, 2)


    @classmethod
    def handle_has_children(cls, hwnd: int, name: str = 'MuMuPlayer12') -> bool:
        root_node = WindowNode(name=name, num=hwnd)
        Handle.handle_tree(hwnd=hwnd, node=root_node)
        handle_depth = WindowNode.get_tree_depth(root_node)
        if handle_depth > 1:
            logger.info(f'Window handle [{hwnd}] depth: {handle_depth}')
            return True
        return False


if __name__ == '__main__':
    h = Handle(config='oas1')
    # logger.info(h.auto_handle_title(h.all_windows()))
    # logger.info(h.root_handle_num)
    # logger.info(h.emulator_family)
