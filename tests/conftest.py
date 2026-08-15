import collections
import hashlib
import shutil
from pathlib import Path

import numpy as np
import pytest

from module.config.config import Config
from module.config.config_store import ConfigStore
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


def _protected_config_digest(root: Path) -> dict[str, str]:
    """真实 config 树摘要：覆盖 config/*.json 与 .generations 全部文件（不含 .lock）。"""
    protected = list(root.glob("*.json"))
    generations = root / ".generations"
    if generations.exists():
        protected.extend(path for path in generations.rglob("*") if path.is_file())
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(protected)
        if path.suffix != ".lock"
    }


@pytest.fixture(scope="session", autouse=True)
def assert_real_config_tree_unchanged():
    """整个测试会话前后断言工作区真实 config/*.json 与 .generations 逐字节不变。"""
    root = Path.cwd() / "config"
    before = _protected_config_digest(root)
    yield
    assert _protected_config_digest(root) == before


@pytest.fixture(scope="session")
def isolated_config_root(tmp_path_factory):
    """隔离的配置根：复制真实 template/oas1 到 tmp，所有读写只发生在隔离目录。"""
    root = tmp_path_factory.mktemp("config-root") / "config"
    root.mkdir()
    shutil.copy2(Path.cwd() / "config" / "template.json", root / "template.json")
    shutil.copy2(Path.cwd() / "config" / "oas1.json", root / "oas1.json")
    return root


@pytest.fixture(scope="session")
def config(isolated_config_root):
    """注入隔离 Store 的 Config session，避免测试读写工作区真实配置。"""
    store = ConfigStore(config_root=isolated_config_root)
    return Config(config_name="oas1", store=store)


@pytest.fixture
def store(tmp_path):
    """每个测试独立的隔离 Store，预置严格合法的 template 与 oas1 配置。"""
    import json

    raw = json.loads((Path.cwd() / "config" / "template.json").read_text(encoding="utf-8"))
    raw["meta_demon"].pop("md_strategies_1", None)
    s = ConfigStore(config_root=tmp_path / "config")
    s.create_from_template("template", raw)
    s.create_from_template("oas1", raw)
    return s


@pytest.fixture(scope="function")
def mock_device() -> MockDevice:
    """创建干净的 MockDevice 实例。每个测试函数独立使用。"""
    return MockDevice()
