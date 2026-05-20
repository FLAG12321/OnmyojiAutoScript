import pytest
from tests.conftest import MockDevice


class TestRuleClick:
    def test_click_records_coordinates(self):
        """验证 MockDevice.click() 记录坐标"""
        device = MockDevice()
        device.click(640, 360)
        assert device.last_click == (640, 360)
        assert device.click_count == 1

    def test_multiple_clicks_appended(self):
        device = MockDevice()
        device.click(100, 200)
        device.click(300, 400)
        device.click(500, 600)
        assert device.click_count == 3
        assert device.clicks[0] == (100, 200)
        assert device.clicks[2] == (500, 600)

    def test_click_coordinates_are_ints(self):
        device = MockDevice()
        device.click(640.7, 360.3)
        x, y = device.last_click
        assert isinstance(x, int)
        assert isinstance(y, int)
        assert x == 640
        assert y == 360

    def test_swipe_records_trajectory(self):
        device = MockDevice()
        device.swipe((100, 200), (300, 400))
        assert len(device.swipes) == 1
        assert device.swipes[0] == ((100, 200), (300, 400))

    def test_long_click_also_recorded(self):
        device = MockDevice()
        device.long_click(500, 500, duration=1.0)
        assert device.click_count == 1
        assert device.last_click == (500, 500)
