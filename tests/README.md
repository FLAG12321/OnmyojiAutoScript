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
