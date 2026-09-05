# -*- coding: utf-8 -*-
"""click_log_extract 提取器单元测试。

喂样例日志行，断言 jsonl 事件输出（时间戳还原、坐标提取、归一化、非 Click 行忽略）。
"""
import pytest

from dev_tools.click_log_extract import parse_line, extract_file

# 与 control.py 实际输出一致的样例行（point2str 4 位右对齐）
CLICK_LINE = '2026-08-20 00:10:02.628 |           control.py:0075 |     INFO | Click ( 646,  600) @ LOGIN_ENTER_GAME'
# 千位数坐标：填充 0 空格（point2str length=4 时 1201 已满 4 位）
CLICK_LINE_WIDE = '2026-08-20 00:10:17.860 |           control.py:0075 |     INFO | Click (1201,  657) @ LOGIN_LOGIN_SCROOLL_CLOSE'


class TestParseLine:
    def test_click_line_parsed(self):
        """Click 行应解析出毫秒时间戳、坐标、控件名"""
        result = parse_line(CLICK_LINE)
        assert result is not None
        ts, x, y, name = result
        assert (x, y, name) == (646, 600, 'LOGIN_ENTER_GAME')
        # 2026-08-20 00:10:02.628 本地时间的毫秒时间戳（跨时区按本地解析，只断言秒内偏移）
        assert ts % 1000 == 628

    def test_wide_coord_parsed(self):
        """千位数坐标（无空格填充）也要能提取"""
        result = parse_line(CLICK_LINE_WIDE)
        assert result is not None
        assert (result[1], result[2]) == (1201, 657)

    def test_non_click_line_ignored(self):
        """Swipe / long_click / 普通日志行不应被提取"""
        lines = [
            '2026-08-20 00:10:02.628 | control.py:0075 | INFO | minitouch Swipe ( 100,  200) -> ( 300,  400), 0.5',
            # 真实 long_click 行（log/2026-08-26 实样）：名后跟时长数字
            '2026-08-26 09:45:06.805 | control.py:0214 | INFO | Click ( 100,  200) @ LongClick 0.8',
            '2026-08-20 00:10:02.628 | ocr.py:0042 | INFO | OCR result: xxx',
        ]
        for line in lines:
            assert parse_line(line) is None, '不该提取: %r' % line

    def test_line_without_timestamp_ignored(self):
        """无时间戳前缀的行（如续行）忽略"""
        assert parse_line('some continuation line Click ( 1,  2) @ X') is None


class TestExtractFile:
    def test_extract_and_normalize(self, tmp_path):
        """提取应写事件列表，540p 档位坐标 ×4/3 归一化到 720p"""
        log = tmp_path / 'fake.txt'
        log.write_text(CLICK_LINE + '\n' + CLICK_LINE_WIDE + '\n', encoding='utf-8')
        events = extract_file(log, resolution='720p')
        assert len(events) == 2
        assert events[0]['source'] == 'script'
        assert (events[0]['x'], events[0]['y']) == (646, 600)
        assert events[0]['window'] == 'LOGIN_ENTER_GAME'

        events_540 = extract_file(log, resolution='540p')
        # 646 * 4/3 ≈ 861.33 -> 861；1201 * 4/3 ≈ 1601.33 -> 1601
        assert (events_540[0]['x'], events_540[1]['x']) == (861, 1601)
        assert events_540[0]['source'] == 'script'

    def test_task_filter(self, tmp_path):
        """--task 只提取 Start/End 任务块内的 Click，块外与嵌套他任务的都不算"""
        log = tmp_path / 'task.txt'
        log.write_text('\n'.join([
            # 块外点击：不提取
            '2026-08-20 00:00:01.000 | control.py:0075 | INFO | Click (  1,   1) @ OUTSIDE',
            '2026-08-20 00:00:02.000 | script.py:0707 | INFO | Scheduler: Start task `RyouToppa`',
            '2026-08-20 00:00:03.000 | control.py:0075 | INFO | Click ( 10,  20) @ IN_TASK',
            '2026-08-20 00:00:04.000 | control.py:0075 | INFO | Click ( 30,  40) @ IN_TASK2',
            '2026-08-20 00:00:05.000 | script.py:0712 | INFO | Scheduler: End task `RyouToppa`',
            # 块结束后：不提取
            '2026-08-20 00:00:06.000 | control.py:0075 | INFO | Click (  2,   2) @ AFTER',
        ]) + '\n', encoding='utf-8')
        events = extract_file(log, resolution='720p', task='RyouToppa')
        assert [e['window'] for e in events] == ['IN_TASK', 'IN_TASK2']
        # 无 task 过滤时全部提取
        events_all = extract_file(log, resolution='720p')
        assert len(events_all) == 4
