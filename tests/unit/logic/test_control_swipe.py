# -*- coding: utf-8 -*-
"""滑动动作不参与连续点击保护的回归测试。"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from module.device.control import Control


pytestmark = pytest.mark.unit


def test_swipe_skips_click_record_protection():
    """连续滑动时不调用 handle_control_check，避免触发点击次数限制。"""
    device = SimpleNamespace(
        config=SimpleNamespace(
            script=SimpleNamespace(
                device=SimpleNamespace(control_method='window_message'),
            ),
        ),
        handle_control_check=Mock(),
        handle_swipe_control_check=Mock(),
        _pace_action_before=Mock(),
        _dispatch_humanized_swipe=Mock(return_value=False),
        _pace_action_after=Mock(),
        swipe_window_message=Mock(),
    )

    Control.swipe(device, (100, 100), (400, 400), duration=0.1,
                  control_name='sa_svr_swipe_left')

    device.handle_control_check.assert_not_called()
    device.handle_swipe_control_check.assert_called_once_with()
    device.swipe_window_message.assert_called_once_with([100, 100], [400, 400])
