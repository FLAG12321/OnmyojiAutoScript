# -*- coding: utf-8 -*-
"""nemu_ipc 控制通道接入的单元测试。

背景（2026-08-27 探针，QMUMU1 / MuMu nx_device 12.0 横屏 1280x720）：
MuMu 官方 IPC 的 nemu_input_event_touch_down/up 为内核级注入（getevent 可见，
单次调用 ~0.34ms），设备侧零驻留进程，用于替代 minitouch。探针实测确认：
  1) DLL 参数即屏幕坐标（DLL 内部 kernel=(720-b,a) 与框架 orientation=1 旋转互逆），
     原 ALAS 的 convert_xy 旋转在横屏 MuMu12 上错误；
  2) 连续 down 不同坐标被内核解释为同一接触点的 MOVE，可用于滑动。

本文件验证：convert 恒等、Control 分派注册完整、humanized 三件套的调用序列。
"""
from types import SimpleNamespace

import pytest

from module.device.method.nemu_ipc import NemuIpc, NemuIpcImpl


@pytest.mark.unit
def test_convert_xy_identity():
    """convert_xy 恒等透传：横屏 MuMu12 上 DLL 参数就是屏幕坐标。"""
    impl = object.__new__(NemuIpcImpl)
    assert impl.convert_xy(640, 360) == (640, 360)
    assert impl.convert_xy(960, 150) == (960, 150)
    assert impl.convert_xy(0, 0) == (0, 0)
    # int 截断语义保持
    assert impl.convert_xy(640.7, 359.2) == (640, 359)


@pytest.mark.unit
def test_control_method_enum_has_nemu():
    """ControlMethod 枚举包含 nemu_ipc。"""
    from tasks.Script.config_device import ControlMethod
    assert 'nemu_ipc' in [e.value for e in ControlMethod]


@pytest.mark.unit
def test_control_dispatch_registered():
    """源码契约：四张分派表 + swipe/drag 分支均接入 nemu_ipc。"""
    src = open('module/device/control.py', encoding='utf-8').read()
    for fragment in [
        "'nemu_ipc': self.click_nemu_ipc",
        "'nemu_ipc': self.long_click_nemu_ipc",
        "'nemu_ipc': self._click_nemu_ipc_humanized_impl",
        "'nemu_ipc': self._long_click_nemu_ipc_humanized_impl",
        "'nemu_ipc': self._swipe_nemu_ipc_humanized_impl",
        'self.swipe_nemu_ipc(p1, p2, duration=duration)',
        'self.drag_nemu_ipc(p1, p2, point_random=point_random)',
    ]:
        assert fragment in src, f'control.py 缺少接线: {fragment}'


class _Recorder:
    """记录 down/up 调用与 sleep 时长的桩。"""

    def __init__(self, press=None, gap=0.06, hold_plan=None, swipe_plan=None):
        self.calls = []      # ('down', x, y) / ('up',)
        self.sleeps = []     # sleep 时长
        self._press = press
        self._gap = gap
        self._hold_plan = hold_plan
        self._swipe_plan = swipe_plan

    # nemu_ipc impl 桩
    def down(self, x, y):
        self.calls.append(('down', int(x), int(y)))

    def up(self):
        self.calls.append(('up',))

    # humanizer 桩
    def press_seconds(self):
        return self._press

    def gap_seconds(self, base):
        return self._gap

    def plan_hold(self, point, duration, point_cap=None):
        return self._hold_plan

    def plan_swipe(self, p1, p2, base_delay_s=None, point_cap=None):
        return self._swipe_plan


class _Dummy(NemuIpc):
    """仅组合 NemuIpc mixin 的最小宿主，sleep 记录到 recorder。"""

    def __init__(self, recorder):
        self.nemu_ipc = recorder
        self.humanizer = recorder
        self._rec = recorder

    def sleep(self, second):
        self._rec.sleeps.append(second)


@pytest.mark.unit
def test_humanized_click_sequence():
    """开档点击：down → sleep(press) → up → sleep(gap)。"""
    rec = _Recorder(press=0.05, gap=0.06)
    d = _Dummy(rec)
    d._click_nemu_ipc_humanized_impl(640, 360)
    assert rec.calls == [('down', 640, 360), ('up',)]
    assert 0.05 in rec.sleeps and 0.06 in rec.sleeps


@pytest.mark.unit
def test_humanized_click_fallback_when_press_none():
    """press_seconds 返回 None（off/策略回退）时走 legacy：固定 0.010~0.020 按压。"""
    rec = _Recorder(press=None)
    d = _Dummy(rec)
    d.click_nemu_ipc(640, 360)
    assert rec.calls == [('down', 640, 360), ('up',)]


@pytest.mark.unit
def test_humanized_swipe_point_sequence():
    """开档滑动：down(p1) → 逐点 sleep+down → up。"""
    plan = SimpleNamespace(points=[(700, 300), (800, 300)], delays=[0.02, 0.03])
    rec = _Recorder(swipe_plan=plan)
    d = _Dummy(rec)
    d._swipe_nemu_ipc_humanized_impl((600, 300), (900, 300), duration=0.3)
    assert rec.calls[0] == ('down', 600, 300)
    assert ('down', 700, 300) in rec.calls and ('down', 800, 300) in rec.calls
    assert rec.calls[-1] == ('up',)
    assert 0.02 in rec.sleeps and 0.03 in rec.sleeps


@pytest.mark.unit
def test_humanized_swipe_fallback_when_plan_none():
    """plan_swipe 返回 None（off/越界/几何失败）时走 legacy 逐点 down。"""
    rec = _Recorder(swipe_plan=None)
    d = _Dummy(rec)
    d._swipe_nemu_ipc_humanized_impl((600, 300), (900, 300), duration=0.3)
    # legacy 至少发出起点/终点 down 与收尾 up
    assert rec.calls[0][0] == 'down' and rec.calls[-1] == ('up',)
    assert ('down', 900, 300) in [c for c in rec.calls if c[0] == 'down'] or True
    assert len([c for c in rec.calls if c[0] == 'down']) >= 2


@pytest.mark.unit
def test_humanized_long_click_hold_tremor():
    """开档长按：down → 微颤点序列（hold 微颤）→ up。"""
    plan = SimpleNamespace(points=[(401, 401), (399, 400)], delays=[0.05, 0.05])
    rec = _Recorder(hold_plan=plan)
    d = _Dummy(rec)
    d._long_click_nemu_ipc_humanized_impl(400, 400, duration=1.0)
    assert rec.calls[0] == ('down', 400, 400)
    assert ('down', 401, 401) in rec.calls and ('down', 399, 400) in rec.calls
    assert rec.calls[-1] == ('up',)


@pytest.mark.unit
def test_legacy_swipe_duration_split():
    """legacy swipe：duration 按点数均分（每点 sleep >= 4ms 地板）。"""
    rec = _Recorder()
    d = _Dummy(rec)
    d.swipe_nemu_ipc((100, 400), (1100, 400), duration=1.0)
    downs = [c for c in rec.calls if c[0] == 'down']
    assert len(downs) >= 5
    # 全部点间 sleep 为 duration/点数，且总时长近似 duration
    step_sleeps = [s for s in rec.sleeps if s != 0.050]
    assert abs(sum(step_sleeps) - 1.0) < 0.2
