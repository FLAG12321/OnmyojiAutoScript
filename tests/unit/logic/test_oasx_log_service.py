from pathlib import Path

import pytest


def test_log_service_reads_latest_window(tmp_path, monkeypatch):
    from module.server import log_service

    # 构造临时日志文件，验证服务只返回最新窗口内容。
    log_root = tmp_path / "log"
    log_root.mkdir()
    log_file = log_root / "2026-06-08_oas1.txt"
    log_file.write_text("line 1\nline 2\nline 3\n", encoding="utf-8")
    monkeypatch.setattr(log_service, "LOG_ROOT", log_root)

    result = log_service.LogBrowserService().read_window(
        script_name="oas1",
        limit_lines=2,
        limit_bytes=4096,
    )

    assert result["script_name"] == "oas1"
    assert [line["text"] for line in result["lines"]] == ["line 2", "line 3"]
    assert result["live_cursor"]


def test_log_service_rejects_unsafe_error_image_path(tmp_path, monkeypatch):
    from module.server import log_service

    # 构造错误日志图片目录，验证路径穿越会被拒绝。
    error_root = tmp_path / "log" / "error"
    error_dir = error_root / "1234567890"
    error_dir.mkdir(parents=True)
    (error_dir / "screen.png").write_bytes(b"png")
    monkeypatch.setattr(log_service, "ERROR_LOG_ROOT", error_root)

    service = log_service.LogBrowserService()
    with pytest.raises(log_service.LogServiceError):
        service.get_error_image_path("1234567890", "../secret.png")


# ======================================================================================================================
#            历史日志格式转换：file_formatter -> flutter_formatter
# ======================================================================================================================
#
# 磁盘日志由 file_formatter 写出（module/logger.py:114）：
#     '%(asctime)s.%(msecs)03d | %(filename)20s:%(lineno)04d | %(levelname)8s | %(message)s'
#   -> '2026-08-23 07:03:33.836 |            logger.py:0453 |     INFO | 正文'
#
# 但前端只认 flutter_formatter 的形状（module/logger.py:118，实时 WebSocket 用的就是它）：
#     '%(levelname)-8s|%(asctime)s.%(msecs)03d| %(message)s'
#   -> 'INFO    |07:03:33.836| 正文'
#
# 前端 log_widget.dart 的 _trimMillis 正则硬锚定 `^(.{8}\|\d{2}:\d{2}:\d{2}\.\d)\d{2}\|`，
# 文件格式一个字符都匹配不上 —— 历史日志会带着完整日期、源码位置列和右对齐级别原样
# 落进列表，与实时日志错列。所以读取时必须转成 flutter 形状。
#
# 契约细节见 [OASX] test/component/log/log_line_width_test.dart 的「行首格式契约」组。


# 取自真实日志文件 log/2026-08-23_server.txt，包含 rich 补到固定宽度的尾部空格。
REAL_FILE_LINE = (
    "2026-08-23 07:03:33.836 |            logger.py:0453 |     INFO | "
    "<<< LAUNCHER CONFIG >>>                                        "
)


def test_format_client_log_text_converts_to_flutter_shape():
    """文件格式必须转成前端认的 flutter 形状：级别左对齐 8 列在行首。"""
    from module.server.log_service import LogBrowserService

    got = LogBrowserService._format_client_log_text(REAL_FILE_LINE)

    # 级别定宽 8 列（INFO 补 4 空格）+ `|` + 时间戳 + `|` + 空格 + 正文
    assert got == "INFO    |07:03:33.836| <<< LAUNCHER CONFIG >>>"
    # 行首恰好 23 列，与 flutter_formatter 一致
    assert len("INFO    |07:03:33.836| ") == 23
    assert got[:23] == "INFO    |07:03:33.836| "


@pytest.mark.parametrize(
    "level, expect_head",
    [
        ("    INFO", "INFO    |"),
        (" WARNING", "WARNING |"),
        ("   ERROR", "ERROR   |"),
        ("CRITICAL", "CRITICAL|"),
        ("   DEBUG", "DEBUG   |"),
    ],
)
def test_format_client_log_text_level_is_left_aligned_8_columns(level, expect_head):
    """级别从文件的右对齐 8 列转成左对齐 8 列。

    CRITICAL 恰好 8 字符、后面不留空格直接接 `|`，这是列宽相等的关键：
    若按「级别 + 空格」拼，CRITICAL 会占 9 列，整片日志错列。
    """
    from module.server.log_service import LogBrowserService

    line = f"2026-08-23 07:03:33.836 |            logger.py:0453 | {level} | 正文"
    got = LogBrowserService._format_client_log_text(line)

    assert got.startswith(expect_head)
    # 无论级别多长，`|` 前的级别段恒为 8 列
    assert got.index("|") == 8


def test_format_client_log_text_strips_rich_padding_and_crlf():
    """去掉 rich 补的尾部空格，但保留行尾换行符本身。

    RichFileHandler 继承 RichHandler，用 Console 渲染时会把每行铺满终端宽度，
    留下几十个尾部空格。前端 maxLines:1 + ellipsis 下这些空格会算进行宽。
    """
    from module.server.log_service import LogBrowserService

    line = (
        "2026-08-23 07:03:33.836 |            logger.py:0453 |     INFO | 正文      \r\n"
    )
    got = LogBrowserService._format_client_log_text(line)

    assert got == "INFO    |07:03:33.836| 正文\r\n"


def test_format_client_log_text_keeps_body_intact():
    """正文一个字都不能改 —— 包括正文里长得像时间戳/源码位置的内容。"""
    from module.server.log_service import LogBrowserService

    body = "retry at 08:10:20.999 from foo.py:0012 | pipe in body"
    line = f"2026-08-23 07:03:33.836 |            logger.py:0453 |     INFO | {body}"
    got = LogBrowserService._format_client_log_text(line)

    assert got == f"INFO    |07:03:33.836| {body}"


def test_format_client_log_text_passes_through_non_log_lines():
    """分隔线、rich traceback 框等非日志行原样返回，不得被误改。"""
    from module.server.log_service import LogBrowserService

    for line in [
        "═" * 80,
        "─" * 36 + " START " + "─" * 37,
        "Traceback (most recent call last):",
        '  File "script.py", line 1, in <module>',
        "",
        "line 1",
    ]:
        assert LogBrowserService._format_client_log_text(line) == line


def test_format_client_log_text_handles_multiline_payload():
    """多行载荷逐行转换，行数与顺序不变。"""
    from module.server.log_service import LogBrowserService

    payload = (
        "2026-08-23 07:03:33.836 |            logger.py:0453 |     INFO | 第一行\n"
        "═══════\n"
        "2026-08-23 07:03:34.365 |               rpc.py:0091 |  WARNING | 第二行\n"
    )
    got = LogBrowserService._format_client_log_text(payload)

    assert got == (
        "INFO    |07:03:33.836| 第一行\n"
        "═══════\n"
        "WARNING |07:03:34.365| 第二行\n"
    )


def test_log_window_returns_flutter_shaped_lines(tmp_path, monkeypatch):
    """端到端：read_window 返回的 text 必须已是 flutter 形状。"""
    from module.server import log_service

    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "2026-08-23_oas1.txt").write_text(
        "2026-08-23 07:03:33.836 |            logger.py:0453 |     INFO | 任务开始\n"
        "2026-08-23 07:03:34.365 |               rpc.py:0091 |  WARNING | 连接超时\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(log_service, "LOG_ROOT", log_root)

    result = log_service.LogBrowserService().read_window(
        script_name="oas1",
        limit_lines=10,
        limit_bytes=4096,
    )

    assert [line["text"] for line in result["lines"]] == [
        "INFO    |07:03:33.836| 任务开始",
        "WARNING |07:03:34.365| 连接超时",
    ]


def test_log_window_byte_length_tracks_raw_not_formatted(tmp_path, monkeypatch):
    """byte_length / offset 必须按磁盘原始字节算，不能被格式转换污染。

    分页游标依赖这两个值定位文件位置；若按转换后的短文本算，回翻会错位 ——
    转换丢掉了日期与源码位置列，每行差几十字节，读 500 行就偏出去几 KB。

    用 write_bytes 而非 write_text：后者在 Windows 上会把 `\\n` 转成 `\\r\\n`，
    磁盘字节数与内存字符串长度不一致，测试就锁不住「按磁盘字节算」这个点。
    """
    from module.server import log_service

    raw_bytes = (
        "2026-08-23 07:03:33.836 |            logger.py:0453 |     INFO | 任务开始\n"
    ).encode("utf-8")
    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "2026-08-23_oas1.txt").write_bytes(raw_bytes)
    monkeypatch.setattr(log_service, "LOG_ROOT", log_root)

    result = log_service.LogBrowserService().read_window(
        script_name="oas1",
        limit_lines=10,
        limit_bytes=4096,
    )

    line = result["lines"][0]
    assert line["offset"] == 0
    # 磁盘原始字节数（含换行），而不是转换后 text 的长度
    assert line["byte_length"] == len(raw_bytes)
    assert line["byte_length"] > len(line["text"].encode("utf-8"))
    # text 已转换，与原始行不同 —— 证明上面比的确实是两个不同的量
    assert line["text"] == "INFO    |07:03:33.836| 任务开始"


