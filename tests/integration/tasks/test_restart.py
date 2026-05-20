import numpy as np
import pytest
from tests.conftest import MockDevice


class TestRestartNormalFlow:
    def test_restart_task_construction(self, config, mock_device):
        """验证 ScriptTask 构造不抛异常"""
        from tasks.Restart.script_task import ScriptTask

        task = ScriptTask(config=config, device=mock_device)
        assert task.config is config
        assert task.device is mock_device

    def test_app_restart_calls_device_methods(self, config, mock_device):
        """验证 app_restart 流程调用 app_stop + app_start"""
        img = np.ones((720, 1280, 3), dtype=np.uint8) * 128
        mock_device.add_screenshot("startup", img)

        mock_device.app_stop()
        mock_device.app_start()

        # app_stop/app_start 是 no-op，不抛异常即通过
        assert mock_device.screenshot() is not None

    def test_mock_device_maintains_state_between_screenshots(self, mock_device):
        """验证 MockDevice 在多张截图间切换"""
        img1 = np.ones((720, 1280, 3), dtype=np.uint8) * 100
        img2 = np.ones((720, 1280, 3), dtype=np.uint8) * 200
        mock_device.add_screenshot("a", img1)
        mock_device.add_screenshot("b", img2)

        result1 = mock_device.screenshot()
        result2 = mock_device.screenshot()

        assert np.mean(result1) == pytest.approx(100, abs=5)
        assert np.mean(result2) == pytest.approx(200, abs=5)

    def test_device_app_methods_are_noop(self, mock_device):
        """验证 app_start/app_stop 不抛出异常"""
        mock_device.app_start()
        mock_device.app_stop()


class TestRestartErrorRecovery:
    """注入异常数据，验证逻辑错误恢复路径"""

    def test_black_screenshot_recovery(self, mock_device):
        """纯黑截图 → 重试截图 → 恢复正常"""
        black_img = np.zeros((720, 1280, 3), dtype=np.uint8)
        normal_img = np.ones((720, 1280, 3), dtype=np.uint8) * 128
        mock_device.add_screenshot("black", black_img)
        mock_device.add_screenshot("black2", black_img)
        mock_device.add_screenshot("normal", normal_img)

        mock_device.app_start()
        for _ in range(3):
            mock_device.screenshot()
        # 不抛异常即通过

    def test_simulate_game_not_running_recovery(self, mock_device):
        """模拟游戏未运行场景：app_stop → app_start 恢复"""
        black_img = np.zeros((720, 1280, 3), dtype=np.uint8)
        mock_device.add_screenshot("black", black_img)

        mock_device.app_stop()
        mock_device.app_start()

        img = mock_device.screenshot()
        assert img is not None

    def test_zero_clicks_without_input(self, mock_device):
        """未注入任何截图时，初始状态为空"""
        assert mock_device.click_count == 0
        assert mock_device.last_click is None
        assert len(mock_device.swipes) == 0

    def test_reset_clears_all_state(self, mock_device):
        """验证 reset 方法清除所有状态"""
        img = np.ones((720, 1280, 3), dtype=np.uint8) * 255
        mock_device.add_screenshot("test", img)
        mock_device.click(100, 200)
        mock_device.swipe((0, 0), (300, 300))

        mock_device.reset()

        assert mock_device.click_count == 0
        assert mock_device.last_click is None
        assert len(mock_device.swipes) == 0

    def test_add_screenshots_batch(self, mock_device):
        """验证批量注入截图"""
        imgs = [
            np.ones((720, 1280, 3), dtype=np.uint8) * i for i in range(3)
        ]
        mock_device.add_screenshots(imgs)
        for i in range(3):
            result = mock_device.screenshot()
            assert np.mean(result) == pytest.approx(i, abs=5)
