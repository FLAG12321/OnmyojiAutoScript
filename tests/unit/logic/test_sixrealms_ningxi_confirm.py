# This Python file uses the following encoding: utf-8
"""SixRealms 月之海「宁息之屿」万相铃不足确认框处理单元测试。

修复目标：点击宁息之屿后若弹出「是否仍要进入？」确认框（左取消/右进入），
必须点右侧「进入」——用 SixRealms 专用模板 I_NINGXI_INSUFFICIENT_ENTER，
否则 enter_island 的 island_list 首位 I_UI_CANCEL 会反复点「取消」形成死循环
（A_NINGXI→取消→A_NINGXI→取消）。
本测试验证：确认框时点专用进入/不点取消；无确认框时原路径（含 I_UI_CANCEL）不受影响。
"""
from types import SimpleNamespace

import pytest

from tasks.SixRealms.moon_sea.map import MoonSeaMap


def _new_map(monkeypatch):
    """构造 MoonSeaMap 最小实例，注入可记录的 appear/点击行为。"""
    m = object.__new__(MoonSeaMap)
    m.cnt_skill101 = 0
    m.cnt_skillpower = 0
    # _conf 是 property，注入其依赖的 config
    m.config = SimpleNamespace(
        model=SimpleNamespace(
            six_realms=SimpleNamespace(
                six_realms_gate=SimpleNamespace(power_enhance_level=3))))
    monkeypatch.setattr(m, 'screenshot', lambda *a, **k: None)
    return m


@pytest.mark.unit
def test_popup_confirms_enter_and_no_cancel(monkeypatch):
    """万相铃不足确认框出现：点专用「进入」，不点取消。"""
    m = _new_map(monkeypatch)
    clicked = []
    monkeypatch.setattr(m, 'appear',
                        lambda b, *a, **k: id(b) == id(m.I_NINGXI_INSUFFICIENT_ENTER))
    monkeypatch.setattr(m, 'ui_click_until_disappear',
                        lambda b, **k: clicked.append(b))
    monkeypatch.setattr(m, 'appear_then_click', lambda *a, **k: False)

    assert m.enter_island() is True
    assert clicked == [m.I_NINGXI_INSUFFICIENT_ENTER]  # 只点专用「进入」
    assert m.I_UI_CANCEL not in clicked                # 绝未点「取消」


@pytest.mark.unit
def test_no_popup_normal_path_unchanged(monkeypatch):
    """无确认框：走原 island_list 遍历（无目标岛可点时返回 False，未点进入）。"""
    m = _new_map(monkeypatch)
    clicked = []
    monkeypatch.setattr(m, 'appear', lambda b, *a, **k: False)
    monkeypatch.setattr(m, 'ui_click_until_disappear',
                        lambda b, **k: clicked.append(b))
    monkeypatch.setattr(m, 'appear_then_click', lambda *a, **k: False)

    assert m.enter_island() is False
    assert clicked == []


@pytest.mark.unit
def test_no_cancel_dead_loop_when_popup_present(monkeypatch):
    """防死循环：确认框存在时，本轮绝不点击 I_UI_CANCEL。"""
    m = _new_map(monkeypatch)
    clicked = []
    monkeypatch.setattr(m, 'appear',
                        lambda b, *a, **k: id(b) == id(m.I_NINGXI_INSUFFICIENT_ENTER))
    monkeypatch.setattr(m, 'ui_click_until_disappear',
                        lambda b, **k: clicked.append(b))
    monkeypatch.setattr(m, 'appear_then_click',
                        lambda b, *a, **k: clicked.append(b) or False)

    assert m.enter_island() is True
    assert m.I_UI_CANCEL not in clicked
    assert m.I_NINGXI_INSUFFICIENT_ENTER in clicked


@pytest.mark.unit
def test_cancel_still_used_when_no_popup(monkeypatch):
    """无确认框时，island_list 首位 I_UI_CANCEL 的取消逻辑保持不变。"""
    m = _new_map(monkeypatch)
    clicked = []
    monkeypatch.setattr(m, 'appear', lambda b, *a, **k: False)
    monkeypatch.setattr(m, 'ui_click_until_disappear',
                        lambda b, **k: clicked.append(b))
    # 遍历顺序 priority_queue[0]=[0,3,1,5,4,2]，首位 island_list[0]=I_UI_CANCEL
    monkeypatch.setattr(m, 'appear_then_click',
                        lambda b, *a, **k: clicked.append(b) or True)

    m.enter_island()
    # 取消按钮仍可被点击（其他场景 / 其他岛屿的取消行为不受影响）
    assert m.I_UI_CANCEL in clicked
    assert m.I_NINGXI_INSUFFICIENT_ENTER not in clicked
