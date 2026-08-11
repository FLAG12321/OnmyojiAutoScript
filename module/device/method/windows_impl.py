# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import cv2
from numpy import frombuffer, uint8, array, random
import numpy as np
import time
from contextlib import nullcontext
from ctypes import windll

from math import dist
from cached_property import cached_property
from win32gui import (GetWindowText, EnumWindows, FindWindow, FindWindowEx,
                      IsWindow, GetWindowRect, GetWindowDC, DeleteObject,
                      SetForegroundWindow, IsWindowVisible, GetDC, GetParent,
                      EnumChildWindows, SetForegroundWindow, GetClientRect)
from win32con import (SRCCOPY, DESKTOPHORZRES, DESKTOPVERTRES, WM_LBUTTONUP,
                      WM_LBUTTONDOWN, WM_ACTIVATE, WA_ACTIVE, MK_LBUTTON,
                      WM_NCHITTEST, WM_SETCURSOR, HTCLIENT, WM_MOUSEMOVE,
                      WM_PARENTNOTIFY, WM_MOUSEACTIVATE, WM_MOUSEWHEEL,
                      WM_SETFOCUS, WM_CAPTURECHANGED)
from win32ui import CreateDCFromHandle, CreateBitmap
from win32api import GetSystemMetrics, SendMessage, MAKELONG, PostMessage
from win32con import SRCCOPY


from module.base.cBezier import BezierTrajectory
from module.exception import RequestHumanTakeover, ScriptError
from module.base.decorator import Config
from module.base.timer import timer
from module.logger import logger
from module.device.handle import Handle, window_scale_rate, EmulatorFamily, dpi_awareness




class Window(Handle):

    # 桌面模式鼠标移动：每 N 像素取一个轨迹点，并限制单次移动的最大点数。
    # 移动仅用于让客户端更新悬停状态，点越少越快；上限保证跨屏移动也不会拖慢。
    DESKTOP_MOVE_STEP = 60
    DESKTOP_MOVE_MAX_POINTS = 12

    def __init__(self, *args, **kwargs):
        logger.info("Window init")
        super().__init__(*args, **kwargs)

    def screenshot_window_background(self):
        """
        后台截屏
        :return:
        """
        if getattr(self, 'is_desktop_window', False):
            return self.screenshot_desktop_bitblt()
        src_x, src_y = 0, 0
        widthScreen, heightScreen = self.screenshot_size
        # 返回句柄窗口的设备环境，覆盖整个窗口，包括非客户区，标题栏，菜单，边框
        hwndDc = GetWindowDC(self.screenshot_handle_num)
        # 创建设备描述表
        mfcDc = CreateDCFromHandle(hwndDc)
        # 创建内存设备描述表
        saveDc = mfcDc.CreateCompatibleDC()
        # 创建位图对象准备保存图片
        saveBitMap = CreateBitmap()
        # 为bitmap开辟存储空间
        saveBitMap.CreateCompatibleBitmap(mfcDc, widthScreen, heightScreen)
        # 将截图保存到saveBitMap中
        saveDc.SelectObject(saveBitMap)
        # 保存bitmap到内存设备描述表
        saveDc.BitBlt((0, 0), (widthScreen, heightScreen), mfcDc, (src_x, src_y), SRCCOPY)

        # 保存图像
        signedIntsArray = saveBitMap.GetBitmapBits(True)
        imgSrceen = frombuffer(signedIntsArray, dtype='uint8')
        imgSrceen.shape = (heightScreen, widthScreen, 4)
        # 这点很重要 在alas中图片以np.ndarray（RGB）的顺序存储。但是opencv是以BGR
        imgSrceen = cv2.cvtColor(imgSrceen, cv2.COLOR_BGR2RGB)
        # imgSrceen = cv2.resize(imgSrceen, (win_size[0], win_size[1]))
        # 很奇怪的

        # # 测试显示截图图片
        # cv2.namedWindow('imgSrceen')  # 命名窗口
        # cv2.imshow("imgSrceen", imgSrceen)  # 显示
        # cv2.waitKey(0)
        # cv2.destroyAllWindows()

        # 内存释放
        DeleteObject(saveBitMap.GetHandle())
        saveDc.DeleteDC()
        mfcDc.DeleteDC()
        return imgSrceen

    def screenshot_desktop_bitblt(self):
        """桌面客户端后台截图：DPI 感知上下文内对客户区 DC 做 BitBlt。

        客户端进程按物理像素原生渲染，感知上下文里 GetDC 拿到的就是物理客户区
        （1280x720），BitBlt 出的位图与资产 1:1，无需任何缩放。客户区 DC 原点即
        画面左上角，不含标题栏，因此不需要偏移。PrintWindow 对该 DirectX 渲染窗口
        全 flag 返回纯黑，故桌面模式统一走 BitBlt。
        """
        with dpi_awareness():
            client_rect = GetClientRect(self.screenshot_handle_num)
            widthScreen = client_rect[2] - client_rect[0]
            heightScreen = client_rect[3] - client_rect[1]
            if widthScreen <= 0 or heightScreen <= 0:
                # 窗口最小化时客户区为 0x0，BitBlt 取不到任何内容。桌面模式允许窗口
                # 被其他窗口遮挡，但不能最小化（PrintWindow 虽支持最小化，却对该
                # DirectX 客户端返回纯黑，无法替代）。
                raise RequestHumanTakeover(
                    'Desktop client window is minimized, screenshot unavailable. '
                    '请恢复游戏窗口（可被其他窗口遮挡，但不能最小化）'
                )
            hwndDc = GetDC(self.screenshot_handle_num)
            mfcDc = CreateDCFromHandle(hwndDc)
            saveDc = mfcDc.CreateCompatibleDC()
            saveBitMap = CreateBitmap()
            saveBitMap.CreateCompatibleBitmap(mfcDc, widthScreen, heightScreen)
            saveDc.SelectObject(saveBitMap)
            saveDc.BitBlt((0, 0), (widthScreen, heightScreen), mfcDc, (0, 0), SRCCOPY)
            signedIntsArray = saveBitMap.GetBitmapBits(True)
            imgSrceen = frombuffer(signedIntsArray, dtype='uint8')
            imgSrceen.shape = (heightScreen, widthScreen, 4)
            imgSrceen = cv2.cvtColor(imgSrceen, cv2.COLOR_BGR2RGB)
            DeleteObject(saveBitMap.GetHandle())
            saveDc.DeleteDC()
            mfcDc.DeleteDC()
        # 窗口尺寸未能精确校准到目标时（如客户端锁定大小）兜底缩放，保证与资产同尺寸
        target_w, target_h = self.screenshot_size
        if (widthScreen, heightScreen) != (target_w, target_h):
            imgSrceen = cv2.resize(imgSrceen, (target_w, target_h))
        return imgSrceen

    def screenshot_printwindow(self):
        """PrintWindow 后台截图：遮挡/最小化也能捕获窗口内容，返回客户区画面。

        注意：对阴阳师桌面客户端这类 DirectX 渲染窗口，PrintWindow 全 flag 返回纯黑，
        桌面模式请用 screenshot_window_background（BitBlt）。
        """
        target_w, target_h = self.screenshot_size
        is_desktop = getattr(self, 'is_desktop_window', False)
        with dpi_awareness() if is_desktop else nullcontext():
            widthScreen, heightScreen = target_w, target_h
            if is_desktop:
                # 感知上下文内 GetClientRect 返回物理像素，位图按实际客户区开
                client_rect = GetClientRect(self.screenshot_handle_num)
                widthScreen, heightScreen = client_rect[2] - client_rect[0], client_rect[3] - client_rect[1]
            hwndDc = GetDC(self.screenshot_handle_num)
            mfcDc = CreateDCFromHandle(hwndDc)
            saveDc = mfcDc.CreateCompatibleDC()
            saveBitMap = CreateBitmap()
            saveBitMap.CreateCompatibleBitmap(mfcDc, widthScreen, heightScreen)
            saveDc.SelectObject(saveBitMap)
            # PW_CLIENTONLY(1)|PW_RENDERFULLCONTENT(2)=3：只渲染客户区且强制重绘
            windll.user32.PrintWindow(self.screenshot_handle_num, saveDc.GetSafeHdc(), 3)
            signedIntsArray = saveBitMap.GetBitmapBits(True)
            imgSrceen = frombuffer(signedIntsArray, dtype='uint8')
            imgSrceen.shape = (heightScreen, widthScreen, 4)
            imgSrceen = cv2.cvtColor(imgSrceen, cv2.COLOR_BGR2RGB)
            DeleteObject(saveBitMap.GetHandle())
            saveDc.DeleteDC()
            mfcDc.DeleteDC()
        if (widthScreen, heightScreen) != (target_w, target_h):
            imgSrceen = cv2.resize(imgSrceen, (target_w, target_h))
        return imgSrceen

    @cached_property
    def control_handle_list(self) -> list:
        """
        不同的模拟器需要的子句柄不同
        mumu模拟器的控制需要 根窗口和第一个子窗口
        雷电模拟器只需要TheRender窗口
        夜神模拟器
        :return:
        """
        result = []
        if getattr(self, 'is_desktop_window', False):
            # 桌面客户端：控制句柄即根窗口本身，无子渲染窗口
            return [self.root_handle_num]
        if self.emulator_family == EmulatorFamily.FAMILY_MUMU:
            result.append(self.root_node.num)
            result.append(self.root_node.children[0].num)
            return result
        elif self.emulator_family == EmulatorFamily.FAMILY_NOX:
            result.append(self.root_node.num)
            try:
                result.append(self.root_node.children[1].num)
                result.append(self.root_node.children[1].children[1].num)
                result.append(self.root_node.children[1].children[1].children[0].num)
            except:
                result.append(self.root_node.children[2].num)
                result.append(self.root_node.children[2].children[1].num)
                result.append(self.root_node.children[2].children[1].children[0].num)
            return result
        elif self.emulator_family == EmulatorFamily.FAMILY_LD:
            result.append(self.root_node.children[0].num)
            return result
        elif self.emulator_family == EmulatorFamily.FAMILY_MEMU:
            pass
        elif self.emulator_family == EmulatorFamily.FAMILY_BLUESTACKS:
            pass

    @cached_property
    def mumu_head_height(self):
        """
        不同mumu模拟器的头部高度不同
        :return:
        """
        father_win_Rect = GetWindowRect(self.control_handle_list[0])
        father_height: int = father_win_Rect[3] - father_win_Rect[1]  # 下y - 上y 计算高度
        children_win_Rect = GetWindowRect(self.control_handle_list[1])
        children_height: int = children_win_Rect[3] - children_win_Rect[1]  # 下y - 上y 计算高度
        height = father_height - children_height
        if int(height * self.window_scale_rate) == 37:
            # 说明是mumu模拟器 不做处理
            pass
        if int(height * self.window_scale_rate) == 45:
            # 说明是mumu12模拟器 不做处理
            pass
        logger.info(f"Mumu emulator head height: {height}")
        return height

    def click_window_message(self, x: int, y: int, fast: bool = False):
        """

        :param x:
        :param y:
        :param fast:
        :return:
        """
        if getattr(self, 'is_desktop_window', False):
            return self.click_desktop_window_message(x, y, fast)
        # 我不知道为什么的使用的pywin32==306的版本会导致获取的图片的是(1024, 576)
        # 所有我在点击的时候会除以这个缩放比例
        # 但是后面发现又不是影响的很奇怪

        x = int(x / self.window_scale_rate)
        y = int(y / self.window_scale_rate)
        if fast:
            press_time: float = (random.randint(10, 40)) / 1000.0
        else:
            press_time: float = (random.randint(100, 200)) / 1000.0
        emulator_type = len(self.control_handle_list)
        if emulator_type == 2:  # mumu模拟器
            SendMessage(self.control_handle_list[0], WM_ACTIVATE, WA_ACTIVE, 0)  # 激活窗口
            # SendMessage(self.control_handle_list[1], WM_ACTIVATE, WA_ACTIVE, 0)  # 激活窗口
            # SendMessage(self.control_handle_list[0], WM_LBUTTONDOWN, 0, MAKELONG(x, y+self.mumu_head_height))  # 模拟鼠标按下 先是父窗口 上面的框高度是57
            # mumu12模拟器 V3.5.16 之后后可以用下面的方式
            SendMessage(self.control_handle_list[1], WM_LBUTTONDOWN, 0, MAKELONG(x, y))
            time.sleep(press_time)
            SendMessage(self.control_handle_list[1], WM_LBUTTONUP, 0, MAKELONG(x, y))  # 模拟鼠标弹起 后是子窗口
        elif emulator_type > 2:  # 夜神模拟器
            SendMessage(self.control_handle_list[0], WM_LBUTTONDOWN, 0, MAKELONG(x, y))  # 模拟鼠标按下 先是父窗口 上面的框高度是57
            SendMessage(self.control_handle_list[1], WM_LBUTTONDOWN, 0, MAKELONG(x, y))
            SendMessage(self.control_handle_list[2], WM_LBUTTONDOWN, 0, MAKELONG(x, y))
            SendMessage(self.control_handle_list[3], WM_LBUTTONDOWN, 0, MAKELONG(x, y))
            time.sleep(press_time)
            SendMessage(self.control_handle_list[3], WM_LBUTTONUP, 0, MAKELONG(x, y))  # 模拟鼠标弹起 后是子窗口
        elif emulator_type == 1:  # 雷电模拟器
            clickPos = MAKELONG(x, y)
            SendMessage(self.control_handle_list[0], WM_LBUTTONDOWN, 0, clickPos)
            time.sleep(press_time)
            SendMessage(self.control_handle_list[0], WM_LBUTTONUP, 0, clickPos)

    def desktop_message_coord(self, x, y) -> tuple:
        """把资产坐标（截图空间 1280x720）换算成窗口消息坐标。

        截图在 DPI 感知上下文内取得，是物理像素；而 PostMessage 由 DPI-unaware 的
        OAS 进程发出，lParam 会被系统按虚拟化（逻辑）空间解释后再交给目标窗口。
        两者相差系统缩放比，不换算会导致点击位置整体外扩（离左上角越远偏移越大）。
        """
        target_w, target_h = self.screenshot_size
        virtual_w, virtual_h = self.desktop_client_size_virtual()
        return int(round(x * virtual_w / target_w)), int(round(y * virtual_h / target_h))

    def click_desktop_window_message(self, x: int, y: int, fast: bool = False):
        """桌面客户端后台点击：先把鼠标沿轨迹移到目标点再按下，坐标为客户区（1280x720）。

        桌面客户端是鼠标语义（不同于模拟器的触摸协议），控件普遍依赖 hover 状态，
        直接在目标点发 down/up 会被部分控件忽略，因此必须带鼠标移动过程。
        """
        hwnd = self.root_handle_num
        # 后台消息点击由客户端按 down/up 事件判定，无需模拟真人按压时长；
        # 保留小幅随机抖动避免固定间隔特征
        if fast:
            press_time: float = (random.randint(10, 25)) / 1000.0
        else:
            press_time: float = (random.randint(30, 60)) / 1000.0
        self.move_desktop_window_message(x, y)
        mx, my = self.desktop_message_coord(x, y)
        lparam = MAKELONG(mx, my)
        PostMessage(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
        time.sleep(press_time)
        PostMessage(hwnd, WM_LBUTTONUP, 0, lparam)
        SendMessage(hwnd, WM_CAPTURECHANGED, 0, 0)
        # 抬起后补一次移动，让客户端刷新悬停状态
        PostMessage(hwnd, WM_MOUSEMOVE, 0, lparam)

    def move_desktop_window_message(self, x: int, y: int) -> None:
        """桌面客户端后台鼠标移动：从上一落点沿贝塞尔轨迹移动到 (x, y)。

        入参为资产坐标；维护 _desktop_cursor 记录当前鼠标位置（同为资产坐标），
        使连续操作之间的移动连贯；轨迹与模拟器路径同源（BezierTrajectory），
        避免直线等距的机器特征。发消息前统一换算到窗口消息坐标空间。

        移动只为让客户端更新悬停状态，无需逐像素铺满：轨迹按 DESKTOP_MOVE_STEP
        像素取点并整体限制在 DESKTOP_MOVE_MAX_POINTS 个以内，长距离移动的耗时
        因此与距离解耦，不会随屏幕跨度线性增长。
        """
        hwnd = self.root_handle_num
        start = getattr(self, '_desktop_cursor', None)
        target = (int(x), int(y))

        def post_move(px, py):
            mx, my = self.desktop_message_coord(px, py)
            PostMessage(hwnd, WM_MOUSEMOVE, 0, MAKELONG(mx, my))

        if start is None:
            # 首次操作没有历史位置，直接跳到目标点
            post_move(target[0], target[1])
            self._desktop_cursor = target
            return
        if start == target:
            post_move(target[0], target[1])
            return
        trace = self.desktop_trace(start, target, interval=self.DESKTOP_MOVE_STEP)
        # 点数超限时等间隔抽稀，保留轨迹形状但把消息量压到上限内
        if len(trace) > self.DESKTOP_MOVE_MAX_POINTS:
            step = len(trace) / self.DESKTOP_MOVE_MAX_POINTS
            trace = [trace[int(i * step)] for i in range(self.DESKTOP_MOVE_MAX_POINTS)]
        for px, py in trace:
            post_move(px, py)
        post_move(target[0], target[1])
        self._desktop_cursor = target

    @staticmethod
    def desktop_trace(start_pos, end_pos, interval: int = 10) -> list:
        """生成 start_pos → end_pos 的贝塞尔轨迹点，与模拟器滑动同一套拟人参数。"""
        number_list: int = int(dist(start_pos, end_pos) / (1 * interval))
        if number_list < 1:
            return [tuple(end_pos)]
        le = random.randint(2, 4)
        deviation = random.randint(20, 40)
        # 0.8 概率先快中间慢后面快，0.1 先快后慢，0.1 先慢后快
        obbs_type = random.random()
        if 0 < obbs_type <= 0.8:
            b_type = 3
        elif obbs_type < 0.9:
            b_type = 2
        else:
            b_type = 1
        return BezierTrajectory.trackArray(start=list(start_pos), end=list(end_pos), numberList=number_list,
                                           le=le, deviation=deviation, bias=0.5, type=b_type, cbb=0, yhh=20)

    def long_click_window_message(self, x: int, y: int, duration: float):
        """

        :param x:
        :param y:
        :param duration: 持续时间 单位秒
        :return:
        """
        if getattr(self, 'is_desktop_window', False):
            return self.long_click_desktop_window_message(x, y, duration)
        # 我不知道为什么的使用的pywin32==306的版本会导致获取的图片的是(1024, 576)
        # 所有我在点击的时候会除以这个缩放比例
        x = int(x / self.window_scale_rate)
        y = int(y / self.window_scale_rate)

        emulator_type = len(self.control_handle_list)
        if self.emulator_family == EmulatorFamily.FAMILY_MUMU:  # mumu模拟器
            SendMessage(self.control_handle_list[1], WM_ACTIVATE, WA_ACTIVE, 0)  # 激活窗口
            # SendMessage(self.control_handle_list[0], WM_LBUTTONDOWN, 0, MAKELONG(x, y+self.mumu_head_height))  # 模拟鼠标按下 先是父窗口 上面的框高度是57
            SendMessage(self.control_handle_list[1], WM_LBUTTONDOWN, 0, MAKELONG(x, y))
            time.sleep(duration)  # 长按时间1000ms-1500ms
            SendMessage(self.control_handle_list[1], WM_LBUTTONUP, 0, MAKELONG(x, y))  # 模拟鼠标弹起 后是子窗口
        elif emulator_type > 2:  # 夜神模拟器
            SendMessage(self.control_handle_list[0], WM_LBUTTONDOWN, 0, MAKELONG(x, y))  # 模拟鼠标按下 先是父窗口 上面的框高度是57
            SendMessage(self.control_handle_list[1], WM_LBUTTONDOWN, 0, MAKELONG(x, y))
            SendMessage(self.control_handle_list[2], WM_LBUTTONDOWN, 0, MAKELONG(x, y))
            SendMessage(self.control_handle_list[3], WM_LBUTTONDOWN, 0, MAKELONG(x, y))
            time.sleep(duration)  # 长按时间1000ms-1500ms
            SendMessage(self.control_handle_list[3], WM_LBUTTONUP, 0, MAKELONG(x, y))  # 模拟鼠标弹起 后是子窗口
        elif emulator_type == 1:  # 雷电模拟器
            clickPos = MAKELONG(x, y)
            SendMessage(self.control_handle_list[0], WM_LBUTTONDOWN, 0, clickPos)  # 模拟鼠标按下
            time.sleep(duration)  # 长按时间1000ms-1500ms
            SendMessage(self.control_handle_list[0], WM_LBUTTONUP, 0, clickPos)  # 模拟鼠标弹起

    def long_click_desktop_window_message(self, x: int, y: int, duration: float):
        """桌面客户端后台长按：先沿轨迹移到目标点，按下保持 duration 秒后释放。"""
        hwnd = self.root_handle_num
        self.move_desktop_window_message(x, y)
        lparam = MAKELONG(*self.desktop_message_coord(x, y))
        PostMessage(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
        time.sleep(duration)
        PostMessage(hwnd, WM_LBUTTONUP, 0, lparam)
        SendMessage(hwnd, WM_CAPTURECHANGED, 0, 0)
        PostMessage(hwnd, WM_MOUSEMOVE, 0, lparam)

    # @timer
    def swipe_window_message(self, startPos: list, endPos: list) -> None:
        """
        后台滑动
        :param startPos:
        :param endPos:
        :return:
        """
        if getattr(self, 'is_desktop_window', False):
            return self.swipe_desktop_window_message(startPos, endPos)
        # 生成的坐标点列表
        interval: int = 10  # 每次移动的间隔时间
        numberList: int = int(dist(startPos, endPos) / (1 * interval))  # 表示每毫秒移动1.5个像素点， 总的时间除以每个点10ms就得到总的点的个数
        le = random.randint(2, 4)  #
        deviation = random.randint(20, 40)  # 幅度
        _type: int = 3
        obbsType = random.random()  # 0.8的概率是先快中间慢后面快， 0.1概率是先快后慢， 0.1概率先慢后快
        if 0 < obbsType <= 0.8:
            _type = 3
        elif obbsType < 0.9:
            _type = 2
        else:
            _type = 1
        trace: list = BezierTrajectory.trackArray(start=startPos, end=endPos, numberList=numberList, le=le,
                                                  deviation=deviation, bias=0.5, type=_type, cbb=0, yhh=20)

        # 使用生成的点列表进行拖拽
        handleNum = None
        if self.emulator_family == EmulatorFamily.FAMILY_MUMU:  # mumu模拟器
            handleNum = self.control_handle_list[1]
        elif self.emulator_family == EmulatorFamily.FAMILY_NOX:  # 夜神模拟器
            handleNum = self.control_handle_list[3]
        elif self.emulator_family == EmulatorFamily.FAMILY_LD:  # 雷电模拟器
            handleNum = self.control_handle_list[0]

        # 激活窗口
        # SendMessage(self.control_handle_list[0], WM_PARENTNOTIFY, WM_LBUTTONDOWN, tmpPos)
        # SendMessage(self.control_handle_list[0], WM_MOUSEACTIVATE, WM_LBUTTONDOWN, tmpPos)
        # PostMessage(handleNum, WM_ACTIVATE, WA_ACTIVE, 0)

        # 先移动到第一个点
        tmpPos = MAKELONG(trace[0][0], trace[0][1])
        SendMessage(handleNum, WM_NCHITTEST, 0, tmpPos)
        SendMessage(handleNum, WM_SETCURSOR, handleNum, MAKELONG(HTCLIENT, WM_LBUTTONDOWN))
        PostMessage(handleNum, WM_LBUTTONDOWN, 0, tmpPos)

        # 一点一点移动鼠标
        manual_control: int = 3  # 手动控制最后几个点的数量
        total_len: int = len(trace)
        for index, pos in enumerate(trace):
            lparam = MAKELONG(pos[0], pos[1])
            PostMessage(handleNum, WM_MOUSEMOVE, MK_LBUTTON, lparam)
            if manual_control >= total_len - index:
                time.sleep(0.08)
            else:
                time.sleep((interval + random.randint(-2, 2)) / 1000.0)

        # 最后释放鼠标
        time.sleep(0.05)
        end_lparam = MAKELONG(trace[-1][0], trace[-1][1])
        PostMessage(handleNum, WM_LBUTTONUP, 0, end_lparam)

    def swipe_desktop_window_message(self, startPos: list, endPos: list) -> None:
        """桌面客户端后台滑动：贝塞尔轨迹 PostMessage，与模拟器路径同一套拟人参数。"""
        hwnd = self.root_handle_num
        interval: int = 10
        trace = self.desktop_trace(startPos, endPos, interval=interval)
        # 先把鼠标移到起点，再按下，避免客户端因缺少 hover 忽略按下事件
        self.move_desktop_window_message(startPos[0], startPos[1])
        start_lparam = MAKELONG(*self.desktop_message_coord(startPos[0], startPos[1]))
        PostMessage(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, start_lparam)
        # 一点一点移动鼠标，最后几个点放慢，让客户端识别为拖拽而非瞬移
        manual_control: int = 3
        total_len: int = len(trace)
        for index, pos in enumerate(trace):
            lparam = MAKELONG(*self.desktop_message_coord(pos[0], pos[1]))
            PostMessage(hwnd, WM_MOUSEMOVE, MK_LBUTTON, lparam)
            if manual_control >= total_len - index:
                time.sleep(0.08)
            else:
                time.sleep((interval + random.randint(-2, 2)) / 1000.0)
        time.sleep(0.05)
        end_lparam = MAKELONG(*self.desktop_message_coord(endPos[0], endPos[1]))
        PostMessage(hwnd, WM_LBUTTONUP, 0, end_lparam)
        SendMessage(hwnd, WM_CAPTURECHANGED, 0, 0)
        self._desktop_cursor = (int(endPos[0]), int(endPos[1]))

    def swipe_vector_window_message2(self, startPos: list, endPos: list) -> None:
        """
        后台滑动, 直线滑动
        :param startPos:
        :param endPos:
        :return:
        """
        # 生成的坐标点列表
        interval: int = 8  # 每次移动的间隔时间
        numberList: int = int(dist(startPos, endPos) / (1.5 * interval))  # 表示每毫秒移动1.5个像素点， 总的时间除以每个点10ms就得到总的点的个数

        def generate_points(start_pos: list, end_pos: list, number: int) -> list:
            # 确定两点之间的步长
            step_x = (end_pos[0] - start_pos[0]) / (number + 1)
            step_y = (end_pos[1] - start_pos[1]) / (number + 1)
            # 生成中间点坐标列表
            points = []
            for i in range(number):
                x = start_pos[0] + step_x * (i + 1)
                y = start_pos[1] + step_y * (i + 1)
                points.append((x, y))
            # 添加起始点和最终点到列表中
            points.insert(0, tuple(start_pos))
            points.append(tuple(end_pos))
            return points

        trace: list = generate_points(start_pos=startPos, end_pos=endPos, number=numberList)

        # 使用生成的点列表进行拖拽
        emulator_type = len(self.control_handle_list)
        if emulator_type == 1:  # 雷电模拟器
            handleNum = self.control_handle_list[0]
        elif emulator_type == 2:  # mumu模拟器
            handleNum = self.control_handle_list[1]
        elif emulator_type > 2:  # 夜神模拟器
            handleNum = self.control_handle_list[3]
        PostMessage(handleNum, WM_ACTIVATE, WA_ACTIVE, 0)  # 激活窗口
        # 先移动到第一个点
        tmpPos = MAKELONG(trace[0][0], trace[0][1])
        SendMessage(handleNum, WM_NCHITTEST, 0, tmpPos)
        SendMessage(handleNum, WM_SETCURSOR, handleNum, MAKELONG(HTCLIENT, WM_LBUTTONDOWN))
        PostMessage(handleNum, WM_LBUTTONDOWN, MK_LBUTTON, tmpPos)
        # 一点一点移动鼠标
        for pos in trace:
            tmpPos = MAKELONG(pos[0], pos[1])
            PostMessage(handleNum, WM_MOUSEMOVE, MK_LBUTTON, tmpPos)
            time.sleep((interval + random.randint(-2, 2)) / 1000.0)
        # 最后释放鼠标
        tmpPos = MAKELONG(trace[-1][0], trace[-1][1])
        PostMessage(handleNum, WM_LBUTTONUP, 0, tmpPos)

    def scroll_window_message(self, x: int, y: int, delta: int=-120) -> None:
        """
        弃置
        https://github.com/runhey/OnmyojiAutoScript/issues/43
        :param x:
        :param y:
        :param delta:
        :return:
        """
        wparam = MAKELONG(0, delta)
        lparam = MAKELONG(x, y)
        handle_num = None
        if self.emulator_family == EmulatorFamily.FAMILY_MUMU:  # mumu模拟器
            handle_num = self.control_handle_list[1]

        elif self.emulator_family == EmulatorFamily.FAMILY_NOX:  # 夜神模拟器
            handle_num = self.control_handle_list[3]
        elif self.emulator_family == EmulatorFamily.FAMILY_LD:  # 雷电模拟器
            handle_num = self.control_handle_list[0]

        SetForegroundWindow(handle_num)
        time.sleep(2)
        # SendMessage(handle_num, WM_SETFOCUS, 0, 0)
        # SendMessage(handle_num, WM_ACTIVATE, WA_ACTIVE, 0)
        # SendMessage(handle_num, WM_MOUSEACTIVATE, WM_LBUTTONDOWN, lparam)


        SendMessage(handle_num, WM_NCHITTEST, 0, lparam)
        SendMessage(handle_num, WM_SETCURSOR, handle_num, lparam)
        PostMessage(handle_num, WM_MOUSEMOVE, 0, lparam)
        self.click_window_message(x, y)

        for i in range(5):
            PostMessage(handle_num, WM_SETCURSOR, handle_num, lparam)
            PostMessage(handle_num, WM_MOUSEMOVE, 0, lparam)
            PostMessage(handle_num, WM_MOUSEWHEEL, wparam, lparam)
            time.sleep(0.5)
        # 抄网上的
        # SendMessage(handle_num, WM_NCHITTEST, 0, lparam)





if __name__ == "__main__":
    w = Window(config='oas1')
    # img = w.screenshot_window_background()
    # handle = 459852
    # wparam = MAKELONG(0, -120)
    # lparam = MAKELONG(300, 300)
    # PostMessage(handle, WM_MOUSEWHEEL, wparam, lparam)

    w.long_click_window_message(142, 310, 1.5)


