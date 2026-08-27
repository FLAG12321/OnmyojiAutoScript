# -*- coding: utf-8 -*-
"""拟人化 off 档黄金基线的测试夹具：事件记录器 + 全局 RNG 指纹。

Plan Task 1 要求从改造前 HEAD 用 fake backend / fake sleep / fake shell /
fake clock 采集逐后端完整事件序列，并断言 off 旁路前后的全局随机序列逐位一致。
本模块提供三类能力：

1. Recorder：有序事件列表，各 fake 把原生调用追加成 (tag, *args) 元组；
2. seed_all() / rng_snapshot()：全局 numpy 与 stdlib random 双生成器的重置与
   "后续 16 次抽取"指纹。MT19937 的状态完全决定后续序列，两份状态只要消费轨迹
   不同，后续 16 次抽取几乎必然不同——比存 624 个 uint32 的裸状态字面量可读
   得多，而位级验证强度等价；
3. CASES：本文件是所有用例的唯一事实源。采集脚本与测试文件都从这里导入，
   保证"固化基线时用的 fn"与"回归断言时用的 fn"是同一份，不会各写一份而错位。
"""
import contextlib
import types

import numpy as np
import random as _random

import module.device.method.windows_impl as windows_impl
import module.device.method.minitouch as minitouch_mod
import module.device.method.adb as adb_mod
import module.device.method.utils as utils_mod
from module.device.control import Control
from module.device.method.adb import Adb
from module.device.method.minitouch import Minitouch, CommandBuilder
from module.device.method.uiautomator_2 import Uiautomator2
from module.device.method.windows_impl import Window
from module.device.handle import EmulatorFamily
from module.exception import RequestHumanTakeover
from module.atom.click import RuleClick
from module.atom.swipe import RuleSwipe
from module.atom.image import RuleImage
from module.atom.ocr import RuleOcr
from module.atom.gif import RuleGif

# 快照抽取次数。16 个 float64 足以把 MT19937 状态指纹到可忽略的碰撞概率
_SNAPSHOT_DRAWS = 16


class Recorder:
    """把 fake 原生调用记成 (tag, *args) 元组列表，元素全部归一为原生类型。"""

    def __init__(self):
        self.events = []

    def record(self, tag, *args):
        self.events.append((tag,) + tuple(args))
        return None

    def __len__(self):
        return len(self.events)


def seed_all(seed: int) -> None:
    """同时重置 numpy 与 stdlib random 的全局生成器。

    window_message 的贝塞尔轨迹内部走 stdlib random（module/base/cBezier.py），
    落点/按压时长/速度型走 numpy；其余 backend 只吃 numpy。统一都种，保证任意
    后端都确定性。
    """
    np.random.seed(seed)
    _random.seed(seed)


def rng_snapshot():
    """操作结束后的随机指纹：两路各取后续 16 次抽取的精确值。

    快照抽取本身也是消耗 RNG，但每次都在操作结束后、且每次测试先 seed 再操作，
    所以指纹是操作的确定性函数。两份状态只要消费轨迹不同，指纹几乎必然不同。
    """
    return (
        tuple(float(np.random.random()) for _ in range(_SNAPSHOT_DRAWS)),
        tuple(float(_random.random()) for _ in range(_SNAPSHOT_DRAWS)),
    )


def capture(seed: int, fn):
    """seed 双生成器 → 运行 fn(recorder) → 返回 (events, rng_snapshot)。"""
    rec = Recorder()
    seed_all(seed)
    fn(rec)
    return rec.events, rng_snapshot()


@contextlib.contextmanager
def patch(module, name, value):
    """上下文内替换 module 的全局 name，退出时恢复。

    各用例的 fn 会 patch 被测模块的 SendMessage/PostMessage/time 等全局，
    失败或异常也必须恢复，避免一个用例污染后续用例。
    """
    original = getattr(module, name)
    setattr(module, name, value)
    try:
        yield
    finally:
        setattr(module, name, original)


def _fake_time(rec, time=None):
    """替换模块级 time 引用的假对象：只暴露被测代码用到的属性。"""
    ns = types.SimpleNamespace()
    ns.sleep = lambda s: rec.record('sleep', s)
    if time is not None:
        ns.time = time
    return ns


# ---------------------------------------------------------------- 通用桩

def _ctrl(method, stub_backends):
    """构造裸 Control：config 只填 control_method，backend 方法全部换成记录桩。"""
    c = object.__new__(Control)
    c.config = types.SimpleNamespace(
        script=types.SimpleNamespace(
            device=types.SimpleNamespace(control_method=method)))
    for name, fn in stub_backends.items():
        setattr(c, name, fn)
    return c


def _window(rec, is_desktop, handle_list, scale=1.0, cursor=None):
    """构造裸 Window：控制句柄、缩放、光标、桌面换算与最小化还原全部打桩。"""
    w = object.__new__(Window)
    w.is_desktop_window = is_desktop
    w.window_scale_rate = scale
    w.control_handle_list = list(handle_list)
    w.root_handle_num = 0x400 if is_desktop else handle_list[0]
    w.screenshot_size = (1280, 720)
    w.desktop_client_size_virtual = lambda: (1280, 720)
    w.desktop_window_restore_if_minimized = lambda: False
    if cursor is not None:
        w._desktop_cursor = cursor
    return w


def _patch_win_msg(rec):
    """把 windows_impl 的 SendMessage/PostMessage/time.sleep 换成记录桩。"""
    cm = contextlib.ExitStack()
    cm.enter_context(patch(
        windows_impl, 'SendMessage',
        lambda hwnd, msg, wp, lp: rec.record('SendMessage', hwnd, msg, wp, lp)))
    cm.enter_context(patch(
        windows_impl, 'PostMessage',
        lambda hwnd, msg, wp, lp: rec.record('PostMessage', hwnd, msg, wp, lp)))
    cm.enter_context(patch(windows_impl, 'time', _fake_time(rec)))
    return cm


def _minitouch(rec, orientation=0, max_x=1280, max_y=720, over_http=False):
    """构造裸 Minitouch：builder 与 socket 客户端全部打桩。"""
    m = object.__new__(Minitouch)
    m.max_x = max_x
    m.max_y = max_y
    m.orientation = orientation
    m.config = types.SimpleNamespace(DEVICE_OVER_HTTP=over_http)
    m.minitouch_builder = CommandBuilder(m)
    m._minitouch_client = types.SimpleNamespace(
        sendall=lambda data: rec.record('socket_send', data.decode('utf-8')),
        recv=lambda n: rec.record('recv', n))
    return m


def _u2(rec, u2obj):
    """构造裸 Uiautomator2：u2 换成记录桩，self.sleep 换成记录桩。"""
    u = object.__new__(Uiautomator2)
    u.u2 = u2obj
    u.sleep = lambda s: rec.record('sleep', s)
    return u


def _u2_obj(rec, click=None, swipe=None, long_click=None):
    """构造 uiautomator2 的 fake 设备对象。"""
    return types.SimpleNamespace(
        click=click or (lambda x, y: rec.record('u2.click', x, y)),
        long_click=long_click or (
            lambda x, y, duration=None: rec.record('u2.long_click', x, y, duration)),
        swipe=swipe or (lambda *a, **kw: rec.record('u2.swipe', *a, kw['duration'])),
        touch=types.SimpleNamespace(
            down=lambda x, y: rec.record('touch.down', x, y),
            move=lambda x, y: rec.record('touch.move', x, y),
            up=lambda x, y: rec.record('touch.up', x, y)),
    )


def _adb(rec, is_desktop=False):
    """构造裸 Adb：adb_shell/self.sleep/swipe_window_message/is_desktop 打桩。"""
    a = object.__new__(Adb)
    a.adb_shell = lambda cmd, *args, **kwargs: rec.record('shell', tuple(cmd))
    a.sleep = lambda s: rec.record('sleep', s)
    a.is_desktop = is_desktop
    a.swipe_window_message = lambda p1, p2: rec.record('swipe_window_message', tuple(p1), tuple(p2))
    return a


# ---------------------------------------------------------------- Control 分发

def cap_dispatch_click(method):
    def fn(rec):
        c = _ctrl(method, {
            'click_adb': lambda x, y: rec.record('click_adb', x, y),
            'click_minitouch': lambda x, y: rec.record('click_minitouch', x, y),
            'click_uiautomator2': lambda x, y: rec.record('click_uiautomator2', x, y),
            'click_window_message': lambda x, y: rec.record('click_window_message', x, y),
        })
        c.click(123, 456, control_check=False, control_name='Click')
    return fn


def cap_dispatch_long_click(method, duration):
    def fn(rec):
        c = _ctrl(method, {
            'long_click_adb': lambda x, y, d: rec.record('long_click_adb', x, y, d),
            'long_click_minitouch': lambda x, y, d: rec.record('long_click_minitouch', x, y, d),
            'long_click_uiautomator2': lambda x, y, d: rec.record('long_click_uiautomator2', x, y, d),
            'long_click_window_message': lambda x, y, d: rec.record('long_click_window_message', x, y, d),
        })
        c.long_click(100, 200, duration=duration)
    return fn


def cap_dispatch_swipe(method, p1, p2, duration=(0.1, 0.2)):
    def fn(rec):
        c = _ctrl(method, {
            'swipe_adb': lambda a, b, duration: rec.record('swipe_adb', tuple(a), tuple(b), duration),
            'swipe_minitouch': lambda a, b, duration: rec.record('swipe_minitouch', tuple(a), tuple(b), duration),
            'swipe_uiautomator2': lambda a, b, duration: rec.record('swipe_uiautomator2', tuple(a), tuple(b), duration),
            'swipe_window_message': lambda a, b: rec.record('swipe_window_message', tuple(a), tuple(b)),
            'swipe_scrcpy': lambda a, b: rec.record('swipe_scrcpy', tuple(a), tuple(b)),
        })
        c.swipe(p1, p2, duration=duration, distance_check=True)
    return fn


# ---------------------------------------------------------------- window_message

def cap_emu_click(rec, handles, fast):
    with _patch_win_msg(rec):
        w = _window(rec, False, handles)
        w.click_window_message(640, 360, fast=fast)


def cap_emu_long_click(rec, family, handles):
    with _patch_win_msg(rec):
        w = _window(rec, False, handles)
        w.emulator_family = family
        w.long_click_window_message(640, 360, 1.5)


def cap_emu_swipe(rec, family, handles):
    with _patch_win_msg(rec):
        w = _window(rec, False, handles)
        w.emulator_family = family
        w.swipe_window_message([100, 100], [300, 400])


def cap_desktop_click(rec, fast, cursor):
    with _patch_win_msg(rec):
        w = _window(rec, True, [], cursor=cursor)
        w.click_desktop_window_message(640, 360, fast=fast)


def cap_desktop_long_click(rec):
    with _patch_win_msg(rec):
        w = _window(rec, True, [], cursor=(100, 100))
        w.long_click_desktop_window_message(640, 360, 0.5)


def cap_desktop_swipe(rec):
    with _patch_win_msg(rec):
        w = _window(rec, True, [], cursor=(50, 50))
        w.swipe_desktop_window_message([100, 100], [300, 400])


def cap_desktop_move(rec, start, target):
    with _patch_win_msg(rec):
        w = _window(rec, True, [], cursor=start)
        w.move_desktop_window_message(*target)


# ---------------------------------------------------------------- minitouch

def cap_minitouch_click(rec, orientation=0, max_x=1280, max_y=720):
    m = _minitouch(rec, orientation=orientation, max_x=max_x, max_y=max_y)
    with patch(minitouch_mod, 'time', _fake_time(rec)):
        m.click_minitouch(100, 200)


def cap_minitouch_long_click(rec):
    m = _minitouch(rec)
    with patch(minitouch_mod, 'time', _fake_time(rec)):
        m.long_click_minitouch(100, 200, duration=1.0)


def cap_minitouch_swipe(rec, duration):
    m = _minitouch(rec)
    with patch(minitouch_mod, 'time', _fake_time(rec)):
        m.swipe_minitouch((100, 100), (300, 400), duration=duration)


# ---------------------------------------------------------------- uiautomator2

def cap_u2_click(rec):
    u = _u2(rec, _u2_obj(rec))
    u.click_uiautomator2(100, 200)


def cap_u2_long_click(rec):
    u = _u2(rec, _u2_obj(rec))
    u.long_click_uiautomator2(100, 200, duration=(1, 1.2))


def cap_u2_swipe(rec):
    u = _u2(rec, _u2_obj(rec))
    u.swipe_uiautomator2((10, 20), (30, 40), duration=0.1)


def cap_u2_drag_along(rec):
    u = _u2(rec, _u2_obj(rec))
    u._drag_along([(10, 20, 0.2), (30, 40, 0.1), (50, 60, 0.15)])


def cap_u2_retry(rec, exc):
    obj = _u2_obj(rec, click=lambda x, y: rec.record('u2.click', x, y) or _raise(exc))
    u = _u2(rec, obj)
    with patch(utils_mod, 'time', _fake_time(rec)):
        try:
            u.click_uiautomator2(100, 200)
            rec.record('NO_EXCEPTION')
        except RequestHumanTakeover:
            rec.record('RequestHumanTakeover')


# ---------------------------------------------------------------- ADB

def cap_adb_click(rec, clock):
    a = _adb(rec)
    with patch(adb_mod, 'time', _fake_time(rec, time=clock)):
        a.click_adb(100, 200)


def cap_adb_swipe(rec, duration=0.1):
    a = _adb(rec)
    a.swipe_adb((10, 20), (30, 40), duration=duration)


def cap_adb_swipe_desktop(rec):
    a = _adb(rec, is_desktop=True)
    a.swipe_adb((10, 20), (30, 40), duration=0.1)


def cap_adb_long_click(rec, duration):
    a = _adb(rec)
    a.long_click_adb(100, 200, duration)


def cap_adb_retry(rec, exc):
    a = _adb(rec)
    a.adb_shell = lambda cmd, *args, **kwargs: rec.record('shell', tuple(cmd)) or _raise(exc)
    with patch(adb_mod, 'time', _fake_time(rec, time=_advancing_clock())):
        with patch(utils_mod, 'time', _fake_time(rec)):
            try:
                a.click_adb(100, 200)
                rec.record('NO_EXCEPTION')
            except RequestHumanTakeover:
                rec.record('RequestHumanTakeover')


# ---------------------------------------------------------------- atom coord

def cap_coord_ruleclick(rec, which):
    rule = RuleClick(roi_front=(100, 50, 80, 40), roi_back=(10, 20, 30, 60), name='click')
    rec.record('coord', *rule.coord() if which == 'front' else rule.coord_more())


def cap_coord_ruleswipe(rec):
    rule = RuleSwipe(roi_front=(100, 50, 80, 40), roi_back=(200, 100, 60, 30),
                     mode='default', name='swipe')
    rec.record('coord', *rule.coord())


def cap_coord_ruleimage(rec, which):
    rule = RuleImage(roi_front=[100, 50, 80, 40], roi_back=(200, 100, 60, 30),
                     method='Template matching', threshold=0.8, file='x.png')
    rec.record('coord', *rule.coord() if which == 'front' else rule.coord_more())


def cap_coord_ruleocr(rec):
    rule = RuleOcr(name='TestOcr', mode='FULL', method='DEFAULT',
                   roi=(100, 50, 80, 40), area=(100, 50, 80, 40), keyword='')
    rec.record('coord', *rule.coord())


def cap_coord_rulegif(rec):
    rule = RuleGif(targets=[RuleImage(roi_front=[100, 50, 80, 40], roi_back=(200, 100, 60, 30),
                                      method='Template matching', threshold=0.8, file='x.png')])
    rule.roi_front = [100, 50, 80, 40]
    rec.record('coord', *rule.coord())


def _raise(exc):
    raise exc


# ---------------------------------------------------------------- 用例登记表

# 单一事实源：采集脚本与回归测试都从这里取 fn。
CASES = {
    # ---- Control 分发：click / long_click / swipe 的实际选择与参数
    'dispatch_click_adb': cap_dispatch_click('ADB'),
    'dispatch_click_minitouch': cap_dispatch_click('minitouch'),
    'dispatch_click_uiautomator2': cap_dispatch_click('uiautomator2'),
    'dispatch_click_window_message': cap_dispatch_click('window_message'),
    'dispatch_long_click_adb_fixed': cap_dispatch_long_click('ADB', 0.8),
    'dispatch_long_click_minitouch_tuple': cap_dispatch_long_click('minitouch', (0.5, 2)),
    'dispatch_swipe_adb': cap_dispatch_swipe('ADB', (100, 200), (300, 400)),
    'dispatch_swipe_adb_horizontal': cap_dispatch_swipe('ADB', (100, 200), (300, 200)),
    'dispatch_swipe_adb_vertical': cap_dispatch_swipe('ADB', (100, 200), (100, 400)),
    'dispatch_swipe_adb_drop': cap_dispatch_swipe('ADB', (100, 200), (101, 200)),
    'dispatch_swipe_minitouch': cap_dispatch_swipe('minitouch', (100, 200), (300, 400)),
    # ---- window_message 模拟器：mumu / nox / ld 三种句柄拓扑
    'emu_click_mumu': lambda rec: cap_emu_click(rec, [0x101, 0x102], False),
    'emu_click_mumu_fast': lambda rec: cap_emu_click(rec, [0x101, 0x102], True),
    'emu_click_nox': lambda rec: cap_emu_click(rec, [0x201, 0x202, 0x203, 0x204], False),
    'emu_click_ld': lambda rec: cap_emu_click(rec, [0x301], False),
    'emu_long_click_mumu': lambda rec: cap_emu_long_click(rec, EmulatorFamily.FAMILY_MUMU, [0x101, 0x102]),
    'emu_long_click_nox': lambda rec: cap_emu_long_click(rec, EmulatorFamily.FAMILY_NOX, [0x201, 0x202, 0x203, 0x204]),
    'emu_swipe_mumu': lambda rec: cap_emu_swipe(rec, EmulatorFamily.FAMILY_MUMU, [0x101, 0x102]),
    'emu_swipe_nox': lambda rec: cap_emu_swipe(rec, EmulatorFamily.FAMILY_NOX, [0x201, 0x202, 0x203, 0x204]),
    # ---- window_message 桌面：首次跳移 / 轨迹 / 长按 / 滑动 / 跨屏截断 / 垂直 / 短距
    'desktop_click_first_move': lambda rec: cap_desktop_click(rec, True, None),
    'desktop_click_trajectory': lambda rec: cap_desktop_click(rec, False, (100, 100)),
    'desktop_long_click': cap_desktop_long_click,
    'desktop_swipe': cap_desktop_swipe,
    'desktop_move_cross_screen': lambda rec: cap_desktop_move(rec, (0, 0), (1280, 720)),
    'desktop_move_vertical': lambda rec: cap_desktop_move(rec, (100, 100), (100, 600)),
    'desktop_move_short': lambda rec: cap_desktop_move(rec, (100, 100), (110, 110)),
    # ---- minitouch：orientation 0/1/2/3、缩放、长按、两条滑动路径
    'minitouch_click_ori0': lambda rec: cap_minitouch_click(rec, 0),
    'minitouch_click_ori1_scale': lambda rec: cap_minitouch_click(rec, 1, 1080, 1920),
    'minitouch_click_ori2': lambda rec: cap_minitouch_click(rec, 2),
    'minitouch_click_ori3_scale': lambda rec: cap_minitouch_click(rec, 3, 1080, 1920),
    'minitouch_long_click': cap_minitouch_long_click,
    'minitouch_swipe_insert': lambda rec: cap_minitouch_swipe(rec, None),
    'minitouch_swipe_bezier': lambda rec: cap_minitouch_swipe(rec, 0.3),
    # ---- uiautomator2：click / long_click / swipe / _drag_along / 异常回退
    'u2_click': cap_u2_click,
    'u2_long_click_tuple': cap_u2_long_click,
    'u2_swipe': cap_u2_swipe,
    'u2_drag_along': cap_u2_drag_along,
    'u2_click_retry_exception': lambda rec: cap_u2_retry(rec, KeyError('boom')),
    'u2_click_rht_propagates': lambda rec: cap_u2_retry(rec, RequestHumanTakeover('takeover')),
    # ---- ADB：快慢路径、swipe、长按三档、异常传播
    'adb_click_fast': lambda rec: cap_adb_click(rec, lambda: 100.0),
    'adb_click_slow': lambda rec: cap_adb_click(rec, _advancing_clock()),
    'adb_swipe': cap_adb_swipe,
    'adb_swipe_desktop_routes': cap_adb_swipe_desktop,
    'adb_long_click_1_5s': lambda rec: cap_adb_long_click(rec, 1.5),
    'adb_long_click_5s': lambda rec: cap_adb_long_click(rec, 5),
    'adb_long_click_sub_ms': lambda rec: cap_adb_long_click(rec, 0.0005),
    'adb_click_retry_exception': lambda rec: cap_adb_retry(rec, RuntimeError('boom')),
    'adb_click_rht_propagates': lambda rec: cap_adb_retry(rec, RequestHumanTakeover('takeover')),
    # ---- atom coord 入口：坐标结果 + 后续随机序列
    'coord_ruleclick_front': lambda rec: cap_coord_ruleclick(rec, 'front'),
    'coord_ruleclick_back': lambda rec: cap_coord_ruleclick(rec, 'back'),
    'coord_ruleswipe': cap_coord_ruleswipe,
    'coord_ruleimage_front': lambda rec: cap_coord_ruleimage(rec, 'front'),
    'coord_ruleimage_back': lambda rec: cap_coord_ruleimage(rec, 'back'),
    'coord_ruleocr': cap_coord_ruleocr,
    'coord_rulegif': cap_coord_rulegif,
}


def _advancing_clock():
    """返回一个每次调用 +0.1 的假时钟：让 click_adb 的耗时判定跨过 0.05 阈值。"""
    t = [0.0]

    def _clock():
        t[0] += 0.1
        return t[0]

    return _clock
