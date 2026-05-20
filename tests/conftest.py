import collections
import numpy as np
import pytest

from module.config.config import Config
from module.device.device import Device


class MockDevice(Device):
    """Mock Device，继承 Device 但重写所有与外界交互的方法。零侵入原有代码。"""

    def __init__(self):
        # 不调用 super().__init__，跳过模拟器连接
        self.image: np.ndarray | None = None
        self.clicks: list[tuple[int, int]] = []
        self.swipes: list[tuple[tuple, tuple]] = []
        self._screenshot_queue: collections.deque = collections.deque()
        self._ocr_results: dict[str, list[str]] = {}
        self._current_label: str = ""

        # 模拟必要的属性（通常由 Platform/Screenshot 父类设置）
        self.stuck_record_check = lambda: False
        self.click_record_check = lambda: False
        self.handle_control_check = lambda button: None

    def screenshot(self) -> np.ndarray:
        if self._screenshot_queue:
            self.image = self._screenshot_queue.popleft()
        return self.image

    def click(self, x: int, y: int, control_check=True, control_name='Click') -> None:
        self.clicks.append((int(x), int(y)))

    def swipe(self, p1, p2, duration=(0.1, 0.2), control_name='SWIPE', distance_check=True):
        self.swipes.append((tuple(p1), tuple(p2)))

    def long_click(self, x, y, duration=(0.5, 2), control_name='LongClick') -> None:
        self.clicks.append((int(x), int(y)))

    def app_start(self):
        pass

    def app_stop(self):
        pass

    def add_screenshot(self, label: str, image: np.ndarray) -> None:
        """注入假截图到队列"""
        self._screenshot_queue.append(image.copy())

    def add_screenshots(self, screenshots: list[np.ndarray]) -> None:
        """批量注入假截图"""
        for img in screenshots:
            self._screenshot_queue.append(img.copy())

    def add_ocr_result(self, label: str, texts: list[str]) -> None:
        """注入假 OCR 结果"""
        self._ocr_results[label] = texts

    def reset(self) -> None:
        """重置所有状态，用于测试间隔离"""
        self.clicks.clear()
        self.swipes.clear()
        self._screenshot_queue.clear()
        self._ocr_results.clear()

    @property
    def click_count(self) -> int:
        return len(self.clicks)

    @property
    def last_click(self) -> tuple[int, int] | None:
        return self.clicks[-1] if self.clicks else None


@pytest.fixture(scope="function")
def mock_device() -> MockDevice:
    """创建干净的 MockDevice 实例。每个测试函数独立使用。"""
    return MockDevice()


@pytest.fixture(scope="session")
def config() -> Config:
    """加载真实配置文件。会话级复用，避免重复加载。"""
    return Config(config_name="oas1")
