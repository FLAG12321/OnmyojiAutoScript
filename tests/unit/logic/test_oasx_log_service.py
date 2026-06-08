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
        limit_bytes=1024,
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
