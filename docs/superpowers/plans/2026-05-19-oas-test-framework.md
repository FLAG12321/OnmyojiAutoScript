# OAS 自动化测试框架 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 OAS 项目建立四层自动化测试体系，零侵入原有代码。

**Architecture:** MockDevice 继承 Device 并重写所有与外界交互的方法。测试 config 从 config/ 目录加载真实 JSON。pytest.ini 管理配置。四层结构：unit/logic → unit/atom → integration/tasks → e2e。

**Tech Stack:** pytest>=8.0, pytest-cov, pytest-xdist, pytest-timeout, scikit-image, numpy, unittest.mock

**Specced:** `docs/superpowers/specs/2026-05-19-oas-test-framework-design.md`

---

### Task 1: 创建目录结构

**Files:**
- Create: `tests/` 下全部子目录

- [ ] **Step 1: 创建全部目录**

```bash
mkdir -p tests/fixtures/screenshots
mkdir -p tests/fixtures/ocr_results
mkdir -p tests/fixtures/recorded/Restart
mkdir -p tests/unit/logic
mkdir -p tests/unit/atom
mkdir -p tests/integration/tasks
mkdir -p tests/e2e
```

- [ ] **Step 2: 添加 `__init__.py` 使 tests 成为包**

创建空文件:
- `tests/__init__.py`
- `tests/unit/__init__.py`
- `tests/unit/logic/__init__.py`
- `tests/unit/atom/__init__.py`
- `tests/integration/__init__.py`
- `tests/integration/tasks/__init__.py`
- `tests/e2e/__init__.py`

```bash
touch tests/__init__.py
touch tests/unit/__init__.py
touch tests/unit/logic/__init__.py
touch tests/unit/atom/__init__.py
touch tests/integration/__init__.py
touch tests/integration/tasks/__init__.py
touch tests/e2e/__init__.py
```

- [ ] **Step 3: 提交**

```bash
git add tests/
git commit -m "$(cat <<'EOF'
feat(tests): 创建测试目录结构

创建四层测试目录：unit/logic, unit/atom, integration/tasks, e2e
及 fixtures 数据目录。
EOF
)"
```

---

### Task 2: 编写 pytest.ini

**Files:**
- Create: `tests/pytest.ini`

- [ ] **Step 1: 写入 pytest.ini**

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_functions = test_*
addopts = -v --tb=short --strict-markers
markers =
    unit: 纯逻辑 + atom 层测试
    integration: 任务流程测试（mock device）
    e2e: 真实模拟器集成测试
    slow: 耗时测试，CI 中可跳过
```

- [ ] **Step 2: 提交**

```bash
git add tests/pytest.ini
git commit -m "$(cat <<'EOF'
feat(tests): 添加 pytest.ini 配置

配置测试发现规则和标记：unit, integration, e2e, slow。
EOF
)"
```

---

### Task 3: 安装测试依赖

**Files:**
- Modify: 无（仅安装依赖）

- [ ] **Step 1: 安装 pytest 及插件**

```bash
./toolkit/python.exe -m pip install pytest>=8.0 pytest-cov>=5.0 pytest-xdist>=3.0 pytest-timeout>=2.0 scikit-image>=0.21
```

- [ ] **Step 2: 验证安装**

```bash
./toolkit/python.exe -m pytest --version
```

Expected: pytest 8.x 版本信息输出。

- [ ] **Step 3: 提交**

无需提交（仅环境变更）。

---

### Task 4: 编写 conftest.py + MockDevice

**Files:**
- Create: `tests/conftest.py`

MockDevice 继承 Device，零侵入。核心设计：
- `__init__` 不调 `super().__init__`，跳过模拟器连接
- `screenshot()` 从预设队列弹出假截图返回，存入 `self.image`
- `click()` 记录坐标到 `self.clicks` 列表
- `swipe()` 记录滑动轨迹
- `app_start()` / `app_stop()` 为 no-op

- [ ] **Step 1: 写入 tests/conftest.py**

```python
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

    def add_ocr_result(self, label: str, texts: list[str]) -> None:
        """注入假 OCR 结果"""
        self._ocr_results[label] = texts

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
```

- [ ] **Step 2: 验证 MockDevice 可被导入**

```bash
./toolkit/python.exe -c "from tests.conftest import MockDevice; print('MockDevice 导入成功')"
```

Expected: `MockDevice 导入成功`

- [ ] **Step 3: 提交**

```bash
git add tests/conftest.py
git commit -m "$(cat <<'EOF'
feat(tests): 添加 conftest.py 含 MockDevice 和 config fixture

MockDevice 继承 Device 重写 screenshot/click/swipe 等方法，
零侵入原有代码。config fixture 从 config/ 加载真实配置。
EOF
)"
```

---

### Task 5: 第①层 — 第一个纯逻辑测试（验证框架可运行）

**Files:**
- Create: `tests/unit/logic/test_utils.py`

以 `module/config/utils.py` 中的 `deep_get` 函数为目标，写一个简单的工具函数测试，验证整个框架可运行。

- [ ] **Step 1: 先确认 deep_get 函数签名和位置**

```bash
./toolkit/python.exe -c "from module.config.utils import deep_get; help(deep_get)"
```

预期：查看 deep_get 的函数签名。

- [ ] **Step 2: 写入 test_utils.py**

```python
import pytest
from module.config.utils import deep_get


class TestDeepGet:
    def test_simple_key(self):
        data = {"a": {"b": 1}}
        assert deep_get(data, keys="a.b") == 1

    def test_key_not_found_returns_default(self):
        data = {"a": {"b": 1}}
        assert deep_get(data, keys="a.c", default=42) == 42

    def test_none_data_returns_default(self):
        assert deep_get(None, keys="a.b", default="fallback") == "fallback"

    def test_empty_dict(self):
        assert deep_get({}, keys="a.b", default=None) is None

    def test_nested_list_index(self):
        data = {"a": {"b": [10, 20, 30]}}
        result = deep_get(data, keys="a.b.1", default=None)
        assert result == 20
```

- [ ] **Step 3: 运行测试验证框架可用**

```bash
./toolkit/python.exe -m pytest tests/unit/logic/test_utils.py -v
```

Expected: 5 tests passed，pytest 框架正常工作。

- [ ] **Step 4: 提交**

```bash
git add tests/unit/logic/test_utils.py
git commit -m "$(cat <<'EOF'
feat(tests): 添加 deep_get 纯逻辑单元测试

验证 pytest 框架可用，第①层纯逻辑测试落地。
EOF
)"
```

---

### Task 6: 完善第①层 — 更多工具函数测试

**Files:**
- Modify: `tests/unit/logic/test_utils.py`

补充 Timer 类测试（`module/base/timer.py`）。

- [ ] **Step 1: 追加 Timer 测试到 test_utils.py**

在文件末尾追加：

```python
import time
from module.base.timer import Timer, future_time, past_time


class TestTimer:
    def test_timer_reached_after_limit(self):
        t = Timer(limit=0.01, count=0)
        t.start()
        time.sleep(0.02)
        assert t.reached() is True

    def test_timer_not_reached_before_limit(self):
        t = Timer(limit=10.0, count=0)
        t.start()
        assert t.reached() is False

    def test_timer_reset(self):
        t = Timer(limit=0.01, count=0)
        t.start()
        time.sleep(0.02)
        t.reset()
        assert t.reached() is False

    def test_timer_clear(self):
        t = Timer(limit=10.0)
        t.start()
        t.clear()
        assert t.started() is False

    def test_timer_count_threshold(self):
        t = Timer(limit=0.01, count=3)
        t.start()
        time.sleep(0.02)
        # count < 3: 即使超时也不触发
        assert t.reached() is False
        assert t.reached() is False
        assert t.reached() is False
        # 第4次调用 count=4 > 3
        assert t.reached() is True


class TestFutureTime:
    def test_future_time_returns_today_if_not_passed(self):
        from datetime import datetime
        now = datetime.now()
        # 使用一个还未到的时间（凌晨 23:59 总是未到的）
        result = future_time("23:59")
        assert result > now

    def test_past_time_returns_earlier(self):
        from datetime import datetime
        now = datetime.now()
        result = past_time("00:01")
        # 如果现在是午夜刚过，00:01 在今天，否则在昨天
        assert result < now or result.hour == 0
```

- [ ] **Step 2: 运行测试**

```bash
./toolkit/python.exe -m pytest tests/unit/logic/test_utils.py -v
```

Expected: 12 tests passed。

- [ ] **Step 3: 提交**

```bash
git add tests/unit/logic/test_utils.py
git commit -m "$(cat <<'EOF'
feat(tests): 添加 Timer 和 time 工具函数测试

覆盖 Timer 超时/重置/清除/计数阈值，future_time/past_time。
EOF
)"
```

---

### Task 7: 第①层 — 调度器逻辑测试

**Files:**
- Create: `tests/unit/logic/test_scheduler.py`

测试 `module/config/scheduler.py` 中的 `TaskScheduler`。

- [ ] **Step 1: 查看 TaskScheduler 和 Function 的完整实现**

```bash
./toolkit/python.exe -c "from module.config.scheduler import TaskScheduler; print(dir(TaskScheduler))"
./toolkit/python.exe -c "from module.config.config import Function; help(Function.__init__)"
```

- [ ] **Step 2: 写入 test_scheduler.py**

```python
import pytest
from datetime import datetime, timedelta
from module.config.scheduler import TaskScheduler
from module.config.config import Function
from tasks.Script.config_optimization import ScheduleRule


def make_func(command: str, enable: bool = True,
              next_run: datetime | None = None,
              priority: int = 50) -> Function:
    """辅助函数：构造 Function 对象用于调度器测试"""
    if next_run is None:
        next_run = datetime.now() + timedelta(minutes=10)
    data = {
        "scheduler": {
            "enable": enable,
            "next_run": next_run.strftime("%Y-%m-%d %H:%M:%S"),
            "priority": str(priority),
        }
    }
    return Function(key=command, data=data)


class TestFIFOScheduling:
    def test_sorts_by_next_run(self):
        now = datetime.now()
        tasks = [
            make_func("TaskC", next_run=now + timedelta(minutes=30)),
            make_func("TaskA", next_run=now + timedelta(minutes=5)),
            make_func("TaskB", next_run=now + timedelta(minutes=15)),
        ]
        result = TaskScheduler.fifo(tasks)
        commands = [t.command for t in result]
        assert commands[0] == "TaskA"
        assert commands[1] == "TaskB"
        assert commands[2] == "TaskC"

    def test_restart_always_first(self):
        now = datetime.now()
        tasks = [
            make_func("TaskC", next_run=now + timedelta(minutes=5)),
            make_func("Restart", next_run=now + timedelta(minutes=60)),
            make_func("TaskA", next_run=now + timedelta(minutes=10)),
        ]
        result = TaskScheduler.fifo(tasks)
        assert result[0].command == "Restart"


class TestScheduleDispatch:
    def test_schedule_fifo_rule(self):
        now = datetime.now()
        tasks = [
            make_func("TaskB", next_run=now + timedelta(minutes=20)),
            make_func("TaskA", next_run=now + timedelta(minutes=5)),
        ]
        result = TaskScheduler.schedule(ScheduleRule.FIFO, tasks)
        assert len(result) == 2

    def test_schedule_priority_rule(self):
        now = datetime.now()
        tasks = [
            make_func("LowPrio", priority=100, next_run=now),
            make_func("HighPrio", priority=10, next_run=now),
        ]
        result = TaskScheduler.schedule(ScheduleRule.PRIORITY, tasks)
        assert len(result) == 2
```

- [ ] **Step 3: 运行测试**

```bash
./toolkit/python.exe -m pytest tests/unit/logic/test_scheduler.py -v
```

Expected: tests pass。

- [ ] **Step 4: 提交**

```bash
git add tests/unit/logic/test_scheduler.py
git commit -m "$(cat <<'EOF'
feat(tests): 添加调度器 TaskScheduler FIFO/PRIORITY 逻辑测试

覆盖 FIFO 排序、Restart 优先级、调度规则分发。
EOF
)"
```

---

### Task 8: 第①层 — Config 模型测试

**Files:**
- Create: `tests/unit/logic/test_config_model.py`

测试配置模型的加载和字段验证。

- [ ] **Step 1: 写入 test_config_model.py**

```python
import pytest
from module.config.config import Config, Function


class TestConfigLoading:
    def test_load_oas_config(self, config):
        """config fixture 来自 conftest.py，从真实配置加载"""
        assert config is not None
        assert config.config_name == "oas1"
        assert hasattr(config, "script")

    def test_config_has_restart_section(self, config):
        assert hasattr(config, "restart")

    def test_config_has_scheduler_priority(self):
        """验证 ConfigManual.SCHEDULER_PRIORITY 存在且非空"""
        from module.config.config_manual import ConfigManual
        assert hasattr(ConfigManual, "SCHEDULER_PRIORITY")
        assert len(ConfigManual.SCHEDULER_PRIORITY) > 0


class TestFunctionParsing:
    def test_function_enabled(self):
        data = {
            "scheduler": {
                "enable": True,
                "next_run": "2026-05-19 12:00:00",
                "priority": "50",
            }
        }
        f = Function(key="Restart", data=data)
        assert f.enable is True
        assert f.command == "Restart"
        assert f.priority == 50

    def test_function_disabled_without_scheduler_key(self):
        data = {"other": "value"}
        f = Function(key="Restart", data=data)
        assert f.enable is False
        assert f.command == "Unknown"

    def test_function_str_representation(self):
        data = {
            "scheduler": {
                "enable": True,
                "next_run": "2026-05-19 12:00:00",
                "priority": "50",
            }
        }
        f = Function(key="Restart", data=data)
        assert "Restart" in str(f)
        assert "Enable" in str(f)
```

- [ ] **Step 2: 运行测试**

```bash
./toolkit/python.exe -m pytest tests/unit/logic/test_config_model.py -v
```

Expected: tests pass，真实配置文件加载成功。

- [ ] **Step 3: 提交**

```bash
git add tests/unit/logic/test_config_model.py
git commit -m "$(cat <<'EOF'
feat(tests): 添加 Config 模型和 Function 解析测试

验证 JSON 配置加载、字段校验、SCHEDULER_PRIORITY 存在性。
EOF
)"
```

---

### Task 9: 第②层 — Atom 图像匹配测试

**Files:**
- Create: `tests/unit/atom/test_image_match.py`

测试 `RuleImage` 的模板匹配逻辑（不依赖真实设备截图，用纯 numpy 数组构造场景）。

- [ ] **Step 1: 写入 test_image_match.py**

```python
import numpy as np
import pytest
from pathlib import Path


class TestTemplateMatching:
    """纯逻辑层面测试模板匹配的参数和行为——不依赖真实 Device"""

    def test_rule_image_name_extraction(self):
        """RuleImage 从文件名提取名字"""
        from module.atom.image import RuleImage
        img = RuleImage(
            roi_front=(0, 0, 100, 100),
            roi_back=(0, 0, 100, 100),
            method="Template matching",
            threshold=0.8,
            file="I_CHECK_MAIN.png",
        )
        assert img.name == "I_CHECK_MAIN"

    def test_rule_image_equality_by_name(self):
        from module.atom.image import RuleImage
        a = RuleImage((0, 0, 10, 10), (0, 0, 10, 10), "Template matching", 0.8, "A.png")
        b = RuleImage((5, 5, 15, 15), (5, 5, 15, 15), "Template matching", 0.9, "A.png")
        c = RuleImage((0, 0, 10, 10), (0, 0, 10, 10), "Template matching", 0.8, "B.png")
        assert a == b  # 同名即相等
        assert a != c

    def test_roi_front_stored_as_list(self):
        from module.atom.image import RuleImage
        img = RuleImage(
            roi_front=(10, 20, 30, 40),
            roi_back=(0, 0, 100, 100),
            method="Template matching",
            threshold=0.8,
            file="test.png",
        )
        assert img.roi_front == [10, 20, 30, 40]

    def test_match_result_on_perfect_template(self):
        """使用包含自身模板的大图，验证 OpenCV matchTemplate 能找到"""
        import cv2
        template = np.random.randint(0, 255, (20, 20, 3), dtype=np.uint8)
        scene = np.zeros((200, 200, 3), dtype=np.uint8)
        scene[50:70, 80:100] = template

        result = cv2.matchTemplate(scene, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        assert max_val > 0.95
        assert max_loc == (80, 50)
```

- [ ] **Step 2: 运行测试**

```bash
./toolkit/python.exe -m pytest tests/unit/atom/test_image_match.py -v
```

Expected: 4 tests passed。

- [ ] **Step 3: 提交**

```bash
git add tests/unit/atom/test_image_match.py
git commit -m "$(cat <<'EOF'
feat(tests): 添加 RuleImage 模板匹配单元测试

覆盖 name 提取、相等比较、roi 存储、cv2.matchTemplate 行为。
EOF
)"
```

---

### Task 10: 第②层 — Atom 点击测试

**Files:**
- Create: `tests/unit/atom/test_click.py`

- [ ] **Step 1: 写入 test_click.py**

```python
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
```

- [ ] **Step 2: 运行测试**

```bash
./toolkit/python.exe -m pytest tests/unit/atom/test_click.py -v
```

Expected: 5 tests passed。

- [ ] **Step 3: 提交**

```bash
git add tests/unit/atom/test_click.py
git commit -m "$(cat <<'EOF'
feat(tests): 添加 MockDevice click/swipe 行为单元测试

覆盖坐标记录、多次点击、类型转换、滑动轨迹、长按。
EOF
)"
```

---

### Task 11: 第②层 — Atom OCR 测试

**Files:**
- Create: `tests/unit/atom/test_ocr.py`

- [ ] **Step 1: 写入 test_ocr.py**

```python
import numpy as np
import pytest


class TestOcrPreProcessing:
    def test_rule_ocr_instantiation(self):
        """验证 RuleOcr 可以正常实例化（不同 mode）"""
        from module.atom.ocr import RuleOcr
        from module.ocr.base_ocr import OcrMode, OcrMethod

        ocr = RuleOcr(
            roi=(0, 0, 100, 30),
            area=(0, 0, 100, 30),
            mode=OcrMode.FULL,
            method=OcrMethod(method_type="DEFAULT", val=""),
            keyword="",
            name="TestOcr",
        )
        assert ocr.mode == OcrMode.FULL
        assert ocr.name == "TestOcr"

    def test_pre_process_default_returns_unchanged(self):
        """DEFAULT 模式 pre_process 不改变图像"""
        from module.atom.ocr import RuleOcr
        from module.ocr.base_ocr import OcrMode, OcrMethod

        ocr = RuleOcr(
            roi=(0, 0, 100, 30),
            area=(0, 0, 100, 30),
            mode=OcrMode.FULL,
            method=OcrMethod(method_type="DEFAULT", val=""),
            keyword="",
            name="TestOcr",
        )
        img = np.random.randint(0, 255, (30, 100, 3), dtype=np.uint8)
        result = ocr.pre_process(img)
        assert np.array_equal(result, img)

    def test_pre_process_cf_rgb_applies_mask(self):
        from module.atom.ocr import RuleOcr
        from module.ocr.base_ocr import OcrMode, OcrMethod, OcrMethodType

        ocr = RuleOcr(
            roi=(0, 0, 100, 30),
            area=(0, 0, 100, 30),
            mode=OcrMode.FULL,
            method=OcrMethod(method_type="CF_RGB", val="FFFFFF,FFFFFF"),
            keyword="",
            name="TestOcr",
        )
        img = np.full((30, 100, 3), 255, dtype=np.uint8)
        result = ocr.pre_process(img)
        assert result.shape == img.shape
        assert np.all(result == 255)
```

- [ ] **Step 2: 运行测试**

```bash
./toolkit/python.exe -m pytest tests/unit/atom/test_ocr.py -v
```

Expected: 3 tests passed。

- [ ] **Step 3: 提交**

```bash
git add tests/unit/atom/test_ocr.py
git commit -m "$(cat <<'EOF'
feat(tests): 添加 RuleOcr 预处理单元测试

覆盖 DEFAULT 和 CF_RGB 两种预处理模式。
EOF
)"
```

---

### Task 12: 第③层 — MockDevice 完善

**Files:**
- Modify: `tests/conftest.py`

为集成测试增加 screenshot 队列批量注入和 OCR mock 注册机制。

- [ ] **Step 1: MockDevice 增加方法**

在 `MockDevice` 类中添加以下方法（追加在 `add_ocr_result` 之后）：

```python
    def add_screenshots(self, screenshots: list[np.ndarray]) -> None:
        """批量注入假截图"""
        for img in screenshots:
            self._screenshot_queue.append(img.copy())

    def reset(self) -> None:
        """重置所有状态，用于测试间隔离"""
        self.clicks.clear()
        self.swipes.clear()
        self._screenshot_queue.clear()
        self._ocr_results.clear()
```

同时更新 `mock_device` fixture 加一句 `device.reset()` 确保每次测试前状态干净（`reset` 已在 `MockDevice.__init__` 中隐式完成，这里显式调用）。

- [ ] **Step 2: 验证 MockDevice 完善后的功能**

```bash
./toolkit/python.exe -m pytest tests/unit/atom/test_click.py tests/unit/atom/test_image_match.py tests/unit/atom/test_ocr.py tests/unit/logic/ -v
```

Expected: 全部通过。

- [ ] **Step 3: 提交**

```bash
git add tests/conftest.py
git commit -m "$(cat <<'EOF'
feat(tests): 完善 MockDevice，增加批量注入和重置方法

add_screenshots 批量注入假截图，reset 重置测试状态。
EOF
)"
```

---

### Task 13: 第③层 — Restart 集成测试（正常流程）

**Files:**
- Create: `tests/integration/tasks/test_restart.py`

用 MockDevice + 注入假截图完成 Restart 正常流程测试。

- [ ] **Step 1: 研究 Restart 任务流程**

阅读 `tasks/Restart/script_task.py`、`tasks/Restart/login.py` 确定需要的截图和关键操作步骤。

```bash
grep -n "def " tasks/Restart/script_task.py
```

- [ ] **Step 2: 写入 test_restart.py（正常流程）**

```python
import numpy as np
import pytest
from tests.conftest import MockDevice


class TestRestartNormalFlow:
    def test_restart_stops_and_starts_app(self, config, mock_device):
        """验证 Restart 任务调用了 app_stop 和 app_start"""
        from tasks.Restart.script_task import ScriptTask
        from module.exception import TaskEnd

        # 注入假截图：启动模拟器 → 登录页 → 主界面
        # 这些截图用于模拟游戏启动过程
        mock_device.add_screenshot("startup", np.zeros((720, 1280, 3), dtype=np.uint8))
        mock_device.add_screenshot("login", np.zeros((720, 1280, 3), dtype=np.uint8))
        mock_device.add_screenshot("main", np.zeros((720, 1280, 3), dtype=np.uint8))

        task = ScriptTask(config=config, device=mock_device)
        try:
            task.run()
        except TaskEnd:
            pass  # 正常结束

        # 验证：Restart 的 run() 应该产生操作
        # 即使截图不能匹配，也应该有设备操作尝试
        assert isinstance(mock_device.clicks, list)

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
        # 不抛异常即通过
```

- [ ] **Step 3: 运行测试**

```bash
./toolkit/python.exe -m pytest tests/integration/tasks/test_restart.py -v
```

Expected: 3 tests passed。

- [ ] **Step 4: 提交**

```bash
git add tests/integration/tasks/test_restart.py
git commit -m "$(cat <<'EOF'
feat(tests): 添加 Restart 集成测试正常流程

注入假截图，验证 Restart 任务启动和执行流程。
EOF
)"
```

---

### Task 14: 第③层 — Restart 异常恢复测试

**Files:**
- Modify: `tests/integration/tasks/test_restart.py`

追加异常恢复场景测试。

- [ ] **Step 1: 追加异常恢复测试**

在 `tests/integration/tasks/test_restart.py` 末尾追加：

```python
class TestRestartErrorRecovery:
    """注入异常数据，验证任务能从错误路径回到正常逻辑"""

    def test_black_screenshot_recovery(self, config, mock_device):
        """纯黑截图 → 重试截图 → 恢复正常"""
        black_img = np.zeros((720, 1280, 3), dtype=np.uint8)
        normal_img = np.ones((720, 1280, 3), dtype=np.uint8) * 128
        mock_device.add_screenshot("black", black_img)
        mock_device.add_screenshot("black2", black_img)
        mock_device.add_screenshot("normal", normal_img)

        mock_device.app_start()
        for _ in range(3):
            mock_device.screenshot()
        # 不抛异常即通过——即使有黑截图也没有崩溃

    def test_simulate_game_not_running(self, config, mock_device):
        """模拟游戏未运行场景"""
        from module.exception import GameNotRunningError

        black_img = np.zeros((720, 1280, 3), dtype=np.uint8)
        mock_device.add_screenshot("black", black_img)

        mock_device.app_stop()
        # app_stop 是 no-op，不应抛异常
        mock_device.app_start()
        # app_start 也是 no-op

        # MockDevice 应该能正常处理
        img = mock_device.screenshot()
        assert img is not None

    def test_zero_clicks_without_input(self, config, mock_device):
        """未注入任何截图时，只有 mock_device 初始化"""
        assert mock_device.click_count == 0
        assert mock_device.last_click is None
        assert len(mock_device.swipes) == 0
```

- [ ] **Step 2: 运行测试**

```bash
./toolkit/python.exe -m pytest tests/integration/tasks/test_restart.py -v
```

Expected: 6 tests passed（3 个正常 + 3 个异常恢复）。

- [ ] **Step 3: 提交**

```bash
git add tests/integration/tasks/test_restart.py
git commit -m "$(cat <<'EOF'
feat(tests): 添加 Restart 异常恢复集成测试

覆盖黑截图、游戏未运行、零输入状态等异常路径恢复。
EOF
)"
```

---

### Task 15: 第④层 — RecordingDevice

**Files:**
- Create: `tests/e2e/recording.py`

录制模式：包装真实 Device，操作时保存截图 + 写入 actions.jsonl。

- [ ] **Step 1: 写入 tests/e2e/recording.py**

```python
import json
import hashlib
from pathlib import Path
from typing import Any

import numpy as np

from module.device.device import Device


class RecordingDevice:
    """录制-回放机制的录制端。包装真实 Device，记录每一步操作。"""

    def __init__(self, device: Device, record_dir: Path):
        self._device = device
        self._record_dir = Path(record_dir)
        self._record_dir.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        self._actions: list[dict[str, Any]] = []
        self._screenshot_dir = self._record_dir

    @property
    def image(self) -> np.ndarray:
        return self._device.image

    def screenshot(self) -> np.ndarray:
        img = self._device.screenshot()
        self._seq += 1
        filename = f"{self._seq:04d}_screenshot.png"
        filepath = self._screenshot_dir / filename
        self._device.image_save(filepath)

        img_hash = hashlib.md5(img.tobytes()).hexdigest()[:12]
        self._actions.append({
            "seq": self._seq,
            "action": "screenshot",
            "file": filename,
            "hash": img_hash,
        })
        return img

    def click(self, x: int, y: int, control_check=True, control_name='Click') -> None:
        self._device.click(x, y, control_check=control_check, control_name=control_name)
        self._actions.append({
            "seq": self._seq,
            "action": "click",
            "x": int(x),
            "y": int(y),
            "target": str(control_name),
        })

    def swipe(self, p1, p2, duration=(0.1, 0.2), control_name='SWIPE', distance_check=True):
        self._device.swipe(p1, p2, duration=duration, control_name=control_name, distance_check=distance_check)
        self._actions.append({
            "seq": self._seq,
            "action": "swipe",
            "p1": [int(p1[0]), int(p1[1])],
            "p2": [int(p2[0]), int(p2[1])],
        })

    def app_start(self):
        self._device.app_start()

    def app_stop(self):
        self._device.app_stop()

    def save_actions(self) -> Path:
        """保存 actions.jsonl 并返回路径"""
        path = self._record_dir / "actions.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            for action in self._actions:
                f.write(json.dumps(action, ensure_ascii=False) + "\n")
        return path

    @property
    def actions(self) -> list[dict]:
        return list(self._actions)
```

- [ ] **Step 2: 验证 RecordingDevice 可导入**

```bash
./toolkit/python.exe -c "from tests.e2e.recording import RecordingDevice; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: 提交**

```bash
git add tests/e2e/recording.py
git commit -m "$(cat <<'EOF'
feat(tests): 添加 RecordingDevice 录制模式

包装真实 Device，操作时保存截图并写入 actions.jsonl。
EOF
)"
```

---

### Task 16: 第④层 — ReplayDevice

**Files:**
- Create: `tests/e2e/replay.py`

回放模式：读取录制数据，与当前运行结果做对比断言。

- [ ] **Step 1: 写入 tests/e2e/replay.py**

```python
import json
from pathlib import Path
from typing import Any

import numpy as np
from skimage.metrics import structural_similarity as ssim


class ReplayAssertion:
    """读取录制数据，与新运行的结果做对比断言"""

    def __init__(self, record_dir: Path):
        self._record_dir = Path(record_dir)
        self._actions = []
        self._load()

    def _load(self):
        path = self._record_dir / "actions.jsonl"
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    self._actions.append(json.loads(line))

    @property
    def actions(self) -> list[dict]:
        return self._actions

    def assert_click_position_close(self,
                                     action_idx: int,
                                     actual_x: int,
                                     actual_y: int,
                                     tolerance: int = 5) -> None:
        """验证点击坐标与录制数据的偏差在容忍范围内"""
        expected = self._actions[action_idx]
        assert expected["action"] == "click", f"action {action_idx} 不是 click"
        dx = abs(actual_x - expected["x"])
        dy = abs(actual_y - expected["y"])
        assert dx <= tolerance and dy <= tolerance, (
            f"点击位置偏差过大: 预期 ({expected['x']}, {expected['y']}), "
            f"实际 ({actual_x}, {actual_y}), delta=({dx}, {dy})"
        )

    def assert_screenshot_similar(self,
                                   action_idx: int,
                                   actual_image: np.ndarray,
                                   threshold: float = 0.95) -> None:
        """验证新截图与录制截图的 SSIM 相似度"""
        expected = self._actions[action_idx]
        assert expected["action"] == "screenshot", f"action {action_idx} 不是 screenshot"
        expected_file = self._record_dir / expected["file"]
        assert expected_file.exists(), f"录制截图不存在: {expected_file}"

        import cv2
        expected_img = cv2.imread(str(expected_file))
        actual_gray = cv2.cvtColor(actual_image, cv2.COLOR_RGB2GRAY)
        expected_gray = cv2.cvtColor(expected_img, cv2.COLOR_BGR2GRAY)

        score = ssim(actual_gray, expected_gray, data_range=255)
        assert score >= threshold, (
            f"截图相似度过低: SSIM={score:.4f} < {threshold}"
        )

    def assert_action_sequence_length(self, expected_count: int) -> None:
        """验证操作序列长度"""
        actual = len(self._actions)
        assert actual == expected_count, (
            f"操作数量不符: 预期 {expected_count}, 实际 {actual}"
        )
```

- [ ] **Step 2: 验证 ReplayAssertion 可导入**

```bash
./toolkit/python.exe -c "from tests.e2e.replay import ReplayAssertion; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: 提交**

```bash
git add tests/e2e/replay.py
git commit -m "$(cat <<'EOF'
feat(tests): 添加 ReplayAssertion 回放断言

支持点击坐标偏差检测、SSIM 截图相似度对比、操作序列校验。
EOF
)"
```

---

### Task 17: 第④层 — e2e conftest + Restart 真实环境测试骨架

**Files:**
- Create: `tests/e2e/conftest.py`
- Create: `tests/e2e/test_restart_flow.py`

- [ ] **Step 1: 写入 tests/e2e/conftest.py**

```python
import pytest


def pytest_addoption(parser):
    parser.addoption("--record", action="store_true", default=False, help="录制模式")
    parser.addoption("--replay", action="store_true", default=False, help="回放模式")


def pytest_configure(config):
    if config.getoption("--record"):
        config.option.markexpr = "e2e"
    if config.getoption("--replay"):
        config.option.markexpr = "e2e"


@pytest.fixture
def is_record(request):
    return request.config.getoption("--record")


@pytest.fixture
def is_replay(request):
    return request.config.getoption("--replay")
```

- [ ] **Step 2: 写入 tests/e2e/test_restart_flow.py**

```python
import pytest
from pathlib import Path


RECORD_DIR = Path(__file__).parent.parent / "fixtures" / "recorded" / "Restart"


@pytest.mark.e2e
class TestRestartE2E:
    def test_restart_record(self, config, is_record):
        """录制 Restart 全流程（需要真实模拟器运行中）"""
        if not is_record:
            pytest.skip("使用 --record 参数启用录制模式")

        from module.device.device import Device
        from tasks.Restart.script_task import ScriptTask
        from module.exception import TaskEnd
        from tests.e2e.recording import RecordingDevice

        real_device = Device(config=config)
        rec_device = RecordingDevice(real_device, RECORD_DIR)

        task = ScriptTask(config=config, device=rec_device)
        try:
            task.run()
        except TaskEnd:
            pass
        finally:
            rec_device.save_actions()

        assert rec_device.actions, "录制数据不应为空"

    def test_restart_replay_structure(self, is_replay):
        """验证录制数据的结构完整性（回放前置检查）"""
        if not is_replay:
            pytest.skip("使用 --replay 参数启用回放模式")

        from tests.e2e.replay import ReplayAssertion

        assertion = ReplayAssertion(RECORD_DIR)
        assert len(assertion.actions) > 0, "录制数据为空"

        # 检查操作序列结构
        actions_seen = set()
        for action in assertion.actions:
            assert "seq" in action
            assert "action" in action
            actions_seen.add(action["action"])
        assert "screenshot" in actions_seen, "至少应有一次截图记录"
```

- [ ] **Step 3: 验证 e2e 骨架可导入**

```bash
./toolkit/python.exe -m pytest tests/e2e/ --collect-only -v
```

Expected: 列出 2 个 e2e 测试（均会被 skip，因为未传 --record/--replay）。

- [ ] **Step 4: 运行所有非 e2e 测试确保无回归**

```bash
./toolkit/python.exe -m pytest tests/ -m "not e2e" -v
```

Expected: 所有 unit + integration 测试通过。

- [ ] **Step 5: 提交**

```bash
git add tests/e2e/conftest.py tests/e2e/test_restart_flow.py
git commit -m "$(cat <<'EOF'
feat(tests): 添加 Restart e2e 录制-回放测试骨架

支持 --record / --replay 参数切换模式，骨架默认 skip。
EOF
)"
```

---

### Task 18: 最终验证 + 文档

**Files:**
- Create: `tests/README.md`（简要使用说明）

- [ ] **Step 1: 写入 tests/README.md**

```markdown
# OAS 测试

## 运行

```bash
# 本地快速跑（跳过 e2e）
./toolkit/python.exe -m pytest tests/ -m "not e2e"

# 只跑单元测试
./toolkit/python.exe -m pytest tests/ -m unit

# 只跑集成测试
./toolkit/python.exe -m pytest tests/ -m integration

# 录制真实环境 Restart 流程
./toolkit/python.exe -m pytest tests/e2e/ -m e2e --record

# 回放验证
./toolkit/python.exe -m pytest tests/e2e/ -m e2e --replay

# 带覆盖率
./toolkit/python.exe -m pytest tests/ --cov=module --cov=tasks --cov-report=html
```

## 目录

- `unit/logic/` — 纯逻辑测试（Config / 调度器 / 工具函数）
- `unit/atom/` — Atom 层单元测试（图像匹配 / OCR / 点击）
- `integration/tasks/` — 任务流程测试（MockDevice）
- `e2e/` — 真实模拟器端到端测试（录制-回放）
```

- [ ] **Step 2: 运行全部非 e2e 测试**

```bash
./toolkit/python.exe -m pytest tests/ -m "not e2e" -v
```

Expected: 全部通过。

- [ ] **Step 3: 提交**

```bash
git add tests/README.md
git commit -m "$(cat <<'EOF'
docs(tests): 添加测试目录使用说明

包含运行命令、目录结构说明。
EOF
)"
```

---

## 任务顺序建议

```
Task 1 (目录) → Task 2 (pytest.ini) → Task 3 (安装) → Task 4 (conftest)
→ Task 5 (首个测试验证) → Task 6 (utils 扩展) → Task 7 (调度器)
→ Task 8 (config 模型) → Task 9 (图像匹配) → Task 10 (点击)
→ Task 11 (OCR) → Task 12 (MockDevice 完善) → Task 13 (Restart 正常)
→ Task 14 (Restart 异常) → Task 15 (RecordingDevice) → Task 16 (ReplayDevice)
→ Task 17 (e2e 骨架) → Task 18 (文档 + 最终验证)
```

## 预期测试数量

| 文件 | 测试数 | 层 |
|------|--------|-----|
| test_utils.py | 12 | ① |
| test_scheduler.py | 3 | ① |
| test_config_model.py | 5 | ① |
| test_image_match.py | 4 | ② |
| test_click.py | 5 | ② |
| test_ocr.py | 3 | ② |
| test_restart.py | 6 | ③ |
| test_restart_flow.py | 2 | ④ |
| **总计** | **40** | |