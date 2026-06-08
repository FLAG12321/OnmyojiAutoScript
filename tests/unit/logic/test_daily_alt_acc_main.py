from pathlib import Path

import pytest


@pytest.mark.unit
def test_daily_alt_acc_main_runs_oas2_sr16_publish_probe():
    """DailyAltAcc 的 main 入口应作为 oas2 的 SR16 发布流程测试入口。"""
    source = Path('tasks/DailyAltAcc/script_task.py').read_text(encoding='utf-8')

    assert "Config('oas2')" in source
    assert "result = self._do_publish_sr('I_SR_16')" in source
    assert "_do_publish_sr(I_SR_16) result: {result}" in source
