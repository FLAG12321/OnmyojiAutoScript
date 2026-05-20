# OAS 自动化测试框架 — 设计说明书

日期: 2026-05-19
状态: 已确认

---

## 1. 概述

为 OAS (OnmyojiAutoScript) 建立四层自动化测试体系。原项目 tests/ 目录为空，需从零搭建。
核心原则：**零侵入原有代码**，不修改 device.py / task 代码 / 项目结构。

---

## 2. 四层测试架构

| 层 | 名称 | 依赖 | 速度 | 覆盖内容 |
|----|------|------|------|----------|
| ① | 纯逻辑测试 | 无 | 毫秒级 | Config 模型 / 工具函数 / 调度器逻辑 |
| ② | Atom 层单元测试 | MockDevice | 毫秒级 | 图像匹配 / 点击坐标解析 / OCR 结果解析 / 滑动 |
| ③ | 任务流程测试 | MockDevice + 假截图 | 毫秒级 | Restart 正常流程 + 异常恢复 |
| ④ | 真实环境集成测试 | 真实模拟器 + 游戏 | 分钟级 | Restart 全流程录制-回放 |

---

## 3. 目录结构

```
tests/
├── conftest.py              # 全局 fixture：config, mock_device
├── pytest.ini               # pytest 配置
├── fixtures/
│   ├── screenshots/         # 假截图（按任务分类）
│   ├── ocr_results/         # 假 OCR 识别结果
│   └── recorded/            # 录制数据（第④层）
│       └── Restart/
│           ├── 0001_xxx.png
│           └── actions.jsonl
├── unit/
│   ├── logic/               # 第①层
│   │   ├── test_config_model.py
│   │   ├── test_scheduler.py
│   │   └── test_utils.py
│   └── atom/                # 第②层
│       ├── test_image_match.py
│       ├── test_ocr.py
│       └── test_click.py
├── integration/             # 第③层
│   └── tasks/
│       └── test_restart.py
└── e2e/                     # 第④层
    ├── conftest.py
    └── test_restart_flow.py
```

---

## 4. MockDevice 设计

MockDevice 继承 Device，不调用 `super().__init__`，零侵入原代码。

```
MockDevice(Device)
├── __init__(): 跳过父类初始化，初始化 self.image, self.clicks, self._screenshot_queue
├── screenshot(): 从 _screenshot_queue 弹出下一张假截图返回
├── click(x, y): 记录坐标到 self.clicks，不执行真实点击
├── swipe(p1, p2): 记录滑动轨迹到 self.swipes
├── add_screenshot(label, image): 注入假截图到队列
├── add_ocr_result(label, texts): 注入假 OCR 结果
├── app_start(): no-op
├── app_stop(): no-op
└── 其他 device 方法: no-op
```

Fixture 使用：

```python
@pytest.fixture
def mock_device():
    device = MockDevice()
    device.add_screenshot("main_page", np.array(...))
    device.add_ocr_result("main_page", ["探索", "式神", "町中"])
    return device

def test_restart_login(mock_device):
    task = ScriptTask(config=config, device=mock_device)
    task.run()
    assert mock_device.clicks[-1] == (640, 360)
```

---

## 5. 录制-回放机制（第④层）

### RecordingDevice

包装真实 Device，每次操作记录到 `actions.jsonl`：

```jsonl
{"seq": 1, "action": "screenshot", "hash": "a1b2c3"}
{"seq": 2, "action": "click", "x": 640, "y": 360, "target": "C_LOGIN_BUTTON"}
{"seq": 3, "action": "screenshot", "hash": "d4e5f6"}
{"seq": 4, "action": "ocr", "texts": ["探索", "式神"], "roi": [0, 0, 1280, 200]}
```

### ReplayDevice

回放时对比：
- SSIM 相似度 > 0.95 为通过
- 坐标偏差 > 5px 告警
- OCR 结果不一致告警

```bash
./toolkit/python.exe -m pytest tests/e2e/ -m e2e --record  # 录制
./toolkit/python.exe -m pytest tests/e2e/ -m e2e --replay  # 回放
```

---

## 6. Restart 集成测试覆盖

```python
class TestRestartNormalFlow:
    def test_full_restart_normal(mock_device):
        """完整正常流程"""

class TestRestartErrorRecovery:
    def test_emulator_not_running_then_ok(mock_device):
        """模拟器启动失败 → 重试后成功"""
    def test_black_screenshot_then_ok(mock_device):
        """纯黑截图 → 恢复正常"""
    def test_wrong_resolution_then_ok(mock_device):
        """分辨率异常 → 恢复正常"""
    def test_game_stuck_on_loading(mock_device):
        """卡加载页 → GameStuckError → 重启恢复"""
    def test_login_page_unexpected(mock_device):
        """非预期登录页 → 重试 → 正常"""
```

---

## 7. pytest 配置 (pytest.ini)

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

---

## 8. 依赖

```
pytest>=8.0
pytest-cov>=5.0
pytest-xdist>=3.0
pytest-timeout>=2.0
scikit-image>=0.21
```

---

## 9. 分阶段实施

阶段一：安装依赖，创建目录结构 + conftest.py + MockDevice 基础版本 + 3-5 个纯逻辑测试
阶段二：config 模型测试 + utils 工具函数测试 + 调度器逻辑测试
阶段三：Atom 层测试 + MockDevice 完善 + Restart 流程测试
阶段四：RecordingDevice + ReplayDevice + Restart 录制-回放

---

## 10. 设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| 测试框架 | pytest | Python 生态主流，fixture 适合 mock 注入|
| Mock 策略 | 继承重写 | 零侵入原代码，测试代码干净 |
| Config 来源 | 真实配置文件 | 测试数据接近生产环境 |
| 配置文件 | pytest.ini | 更直接，不需要 pyproject.toml |
| 目录分离 | unit/logic + unit/atom | 两类测试依赖差异大 |
| 录制目标 | 仅 Restart | 首批简化，后续扩展 |

---

## 11. 关键约束

- **零侵入原项目代码**，不修改 module/ 和 tasks/ 下的任何源文件
- Config 从 config/ 目录加载真实 JSON 配置
- 录制数据存储在 tests/fixtures/recorded/
- 所有测试标记 unit / integration / e2e / slow