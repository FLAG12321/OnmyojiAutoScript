import pytest

from module.exception import GameNotRunningError
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_login


@pytest.mark.unit
def test_page_login_interrupts_current_task_for_restart(monkeypatch):
    ui = object.__new__(GameUi)
    ui.ui_current = None
    ui.maybe_screenshot = lambda skip_first_screenshot=True: None
    ui.ui_page_appear = lambda page, interval=None: page == page_login

    with pytest.raises(GameNotRunningError, match="Login page detected"):
        ui.ui_get_current_page()


@pytest.mark.unit
def test_page_login_accept_login_returns_page():
    """切号场景：accept_login=True 时登录页作为合法当前页返回，不抛异常。"""
    ui = object.__new__(GameUi)
    ui.ui_current = None
    ui.maybe_screenshot = lambda skip_first_screenshot=True: None
    ui.ui_page_appear = lambda page, interval=None: page == page_login

    result = ui.ui_get_current_page(accept_login=True)
    assert result == page_login
    assert ui.ui_current == page_login
