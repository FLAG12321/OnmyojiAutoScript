import pytest
import types

from module.exception import GameNotRunningError
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_login


@pytest.mark.unit
def test_page_login_interrupts_current_task_for_restart(monkeypatch):
    ui = object.__new__(GameUi)
    ui.ui_current = None
    ui.device = types.SimpleNamespace(is_desktop=False)
    ui.maybe_screenshot = lambda skip_first_screenshot=True: None
    ui.ui_page_appear = lambda page, interval=None: page == page_login

    with pytest.raises(GameNotRunningError, match="Login page detected"):
        ui.ui_get_current_page()


@pytest.mark.unit
def test_page_login_accept_login_returns_page():
    """切号场景：accept_login=True 时登录页作为合法当前页返回，不抛异常。"""
    ui = object.__new__(GameUi)
    ui.ui_current = None
    ui.device = types.SimpleNamespace(is_desktop=False)
    ui.maybe_screenshot = lambda skip_first_screenshot=True: None
    ui.ui_page_appear = lambda page, interval=None: page == page_login

    result = ui.ui_get_current_page(accept_login=True)
    assert result == page_login
    assert ui.ui_current == page_login


@pytest.mark.unit
def test_desktop_mpay_popup_interrupts_page_detection():
    """MPay 是独立窗口，存在时不再遍历游戏 Page，直接交给 Restart。"""
    ui = object.__new__(GameUi)
    ui.ui_current = None
    marked = []
    ui.device = types.SimpleNamespace(
        is_desktop=True,
        find_desktop_login_popup=lambda: 0x400,
        desktop_mark_logged_out=lambda: marked.append(True),
    )
    ui.maybe_screenshot = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError('发现 MPay 后不应继续截图识别 Page'))

    with pytest.raises(GameNotRunningError, match='Desktop MPay login popup present'):
        ui.ui_get_current_page()
    # 同时复位登录标记，让 app_is_running 的任务前置检查也能拦下
    assert marked == [True]
