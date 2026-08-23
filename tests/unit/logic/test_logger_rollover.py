"""跨天日志滚动测试。

覆盖场景：实例在零点前启动并持续运行，零点后的日志必须写入新日期的文件，
而不是继续追加到前一天的文件里（否则当天统计读不到数据、前一天统计被污染）。
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path


def run_logger_snippet(tmp_path, snippet):
    """在临时目录里以独立进程运行 logger 片段，避免污染当前进程的 handler。"""
    repo_root = Path(__file__).resolve().parents[3]
    package_dir = tmp_path / "module"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("")
    shutil.copyfile(repo_root / "module" / "logger.py", package_dir / "logger.py")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(tmp_path)
    # 子进程通过管道输出中文；显式指定 UTF-8，避免 Windows 默认代码页与父进程
    # 的 UTF-8 解码不一致，把正确的 GUI 回调文本变成替换字符。
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        # 显式 utf-8：logger 输出含中文，Windows 默认 GBK 解码会抛
        # UnicodeDecodeError 让 result.stdout 变成 None，断言拿不到真实内容
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


# 用假 date 替换 logger 模块内的 date，以便在同一进程里模拟跨天
_FAKE_DATE = '''
import datetime as _dt
import module.logger as m

class FakeDate(_dt.date):
    """可控的 date 替身，today() 返回预设值。"""
    current = _dt.date(2026, 8, 7)

    @classmethod
    def today(cls):
        return cls.current

m.date = FakeDate
'''


def test_rollover_writes_next_day_lines_to_new_file(tmp_path):
    """跨天后的日志行必须落到新日期文件，且不再写入旧文件。"""
    snippet = _FAKE_DATE + '''
import datetime as _dt
m.set_file_logger("rolltest")
m.logger.info("LINE_DAY_ONE")

# 模拟零点跨天
FakeDate.current = _dt.date(2026, 8, 8)
m.logger.info("LINE_DAY_TWO")
print("OK")
'''
    result = run_logger_snippet(tmp_path, snippet)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout

    day_one = (tmp_path / "log" / "2026-08-07_rolltest.txt").read_text(encoding="utf-8")
    day_two = (tmp_path / "log" / "2026-08-08_rolltest.txt").read_text(encoding="utf-8")

    assert "LINE_DAY_ONE" in day_one
    # 关键断言：跨天的行不能留在前一天的文件里
    assert "LINE_DAY_TWO" not in day_one
    assert "LINE_DAY_TWO" in day_two
    assert "LINE_DAY_ONE" not in day_two


def test_rollover_new_file_starts_with_start_boundary(tmp_path):
    """新文件开头须有 ═/─START─/═ 三行块，供统计解析与错误日志切片定位。"""
    snippet = _FAKE_DATE + '''
import datetime as _dt
m.set_file_logger("rolltest")
m.logger.info("LINE_DAY_ONE")
FakeDate.current = _dt.date(2026, 8, 8)
m.logger.info("LINE_DAY_TWO")
print("OK")
'''
    result = run_logger_snippet(tmp_path, snippet)
    assert result.returncode == 0, result.stderr

    lines = (tmp_path / "log" / "2026-08-08_rolltest.txt").read_text(
        encoding="utf-8"
    ).splitlines()

    assert len(lines) >= 3
    # 与正常启动时 logger.hr('Start', level=0) 产生的压缩格式一致：
    # 分隔线与 GUI 日志硬上限同为 80 列，不能只校验字符构成而放过宽度回退。
    assert len(lines[0]) == 80
    assert len(lines[1]) == 80
    assert len(lines[2]) == 80
    assert set(lines[0].strip()) == {"═"}
    assert "START" in lines[1]
    assert set(lines[2].strip()) == {"═"}
    assert "rolled over" in "\n".join(lines)


def test_no_rollover_within_same_day(tmp_path):
    """同一天内不得产生额外文件或重复 START 块（避免虚假会话边界）。"""
    snippet = _FAKE_DATE + '''
m.set_file_logger("rolltest")
for i in range(5):
    m.logger.info("SAME_DAY_%d" % i)
print("OK")
'''
    result = run_logger_snippet(tmp_path, snippet)
    assert result.returncode == 0, result.stderr

    produced = sorted(p.name for p in (tmp_path / "log").glob("*_rolltest.txt"))
    assert produced == ["2026-08-07_rolltest.txt"]

    content = (tmp_path / "log" / "2026-08-07_rolltest.txt").read_text(encoding="utf-8")
    assert "rolled over" not in content


def test_rollover_boundary_is_parseable_by_log_stats(tmp_path):
    """滚动写入的 START 块必须能被 log_stats 的边界正则识别。"""
    snippet = _FAKE_DATE + '''
import datetime as _dt
m.set_file_logger("rolltest")
m.logger.info("LINE_DAY_ONE")
FakeDate.current = _dt.date(2026, 8, 8)
m.logger.hr("SomeTask", level=0)
m.logger.info("LINE_DAY_TWO")
print("OK")
'''
    result = run_logger_snippet(tmp_path, snippet)
    assert result.returncode == 0, result.stderr

    from module.server.log_stats import LogStatsParser

    lines = (tmp_path / "log" / "2026-08-08_rolltest.txt").read_text(
        encoding="utf-8"
    ).splitlines(keepends=True)

    # 新文件首块应被识别为 START 边界
    assert LogStatsParser._is_task_boundary(lines, 0)
    assert LogStatsParser._extract_boundary_title(lines[1]).upper() == "START"

    # 整体可解析且能统计到跨天后的任务（任务键经 hr() 转大写）
    payload = LogStatsParser.parse_lines(lines, script_name="rolltest")
    assert "SOMETASK" in payload["tasks"]
