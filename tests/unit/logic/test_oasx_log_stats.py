from datetime import date


def test_stats_dates_from_log_files(tmp_path, monkeypatch):
    from module.server import log_stats

    # 构造多日期日志文件，验证日期列表按新到旧排序。
    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "2026-06-08_oas1.txt").write_text("", encoding="utf-8")
    (log_root / "2026-06-07_oas1.txt").write_text("", encoding="utf-8")
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    result = log_stats.LogStatsService().list_available_dates("oas1")

    assert result["script_name"] == "oas1"
    assert result["dates"] == ["2026-06-08", "2026-06-07"]


def test_stats_missing_log_returns_zero(tmp_path, monkeypatch):
    from module.server import log_stats

    # 缺少日志文件时，统计接口应返回零值结构而不是抛错。
    log_root = tmp_path / "log"
    log_root.mkdir()
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    result = log_stats.LogStatsService().build_stats("oas1", date(2026, 6, 8))

    assert result["total_runtime_seconds"] == 0
    assert result["total_task_run_count"] == 0
    assert result["total_battle_count"] == 0
    assert result["tasks"] == {}
