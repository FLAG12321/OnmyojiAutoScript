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


def test_multi_stats_from_stat_lines(tmp_path, monkeypatch):
    from module.server import log_stats

    log_root = tmp_path / "log"
    log_root.mkdir()
    log_file = log_root / "2026-06-20_oas1.txt"
    log_file.write_text(
        "\n".join([
            '2026-06-20 06:00:00.000 | script_task.py:0001 |     INFO | [STAT] {"ev":"acc_start","acc":"mail@example.com","char":"角色A","svr":"一区","tasks":["cooperation","alliedteam"]}',
            '2026-06-20 06:00:01.000 | script_task.py:0002 |     INFO | [STAT] {"ev":"switch","acc":"mail@example.com","char":"角色A","svr":"一区","ok":true}',
            '2026-06-20 06:00:02.000 | script_task.py:0003 |     INFO | [STAT] {"ev":"task_start","acc":"mail@example.com","char":"角色A","svr":"一区","task":"cooperation"}',
            '2026-06-20 06:00:05.000 | cooperation.py:0100 |     INFO | [STAT] {"ev":"coop","acc":"mail@example.com","char":"角色A","svr":"一区","ctype":"jade","real":true,"total":1}',
            '2026-06-20 06:00:06.000 | script_task.py:0004 |     INFO | [STAT] {"ev":"task_end","acc":"mail@example.com","char":"角色A","svr":"一区","task":"cooperation","ok":true,"dur":4.0}',
            '2026-06-20 06:00:07.000 | script_task.py:0005 |     INFO | [STAT] {"ev":"battle","acc":"mail@example.com","char":"角色A","svr":"一区","count":3}',
            '2026-06-20 06:00:08.000 | mshop.py:0158 |     INFO | [STAT] {"ev":"mshop","acc":"mail@example.com","char":"角色A","svr":"一区","goods":"shepi","price":88}',
            '2026-06-20 06:00:10.000 | script_task.py:0006 |     INFO | [STAT] {"ev":"acc_end","acc":"mail@example.com","char":"角色A","svr":"一区","err_count":0}',
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    result = log_stats.LogStatsService().build_stats("oas1", date(2026, 6, 20))

    assert result["multi"] is not None
    account = result["multi"]["accounts"][0]
    assert account["account"] == "mail@example.com"
    assert account["character"] == "角色A"
    assert account["svr"] == "一区"
    assert account["switch_ok"] is True
    assert account["duration_seconds"] == 10.0
    assert account["error_count"] == 0
    assert account["battle_count"] == 3
    assert account["coop_total"] == 1
    assert account["tasks"][0] == {"task": "cooperation", "ok": True, "duration_seconds": 4.0}
    assert account["coops"] == [{"ctype": "jade", "real": True}]
    assert account["mshops"] == [{"goods": "shepi", "price": 88}]


def test_multi_stats_without_stat_lines_is_none(tmp_path, monkeypatch):
    from module.server import log_stats

    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "2026-06-20_oas1.txt").write_text(
        "2026-06-20 06:00:00.000 | script.py:0001 |     INFO | ordinary line\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    result = log_stats.LogStatsService().build_stats("oas1", date(2026, 6, 20))

    assert result["multi"] is None


def test_multi_stats_unclosed_account_uses_last_stat_timestamp(tmp_path, monkeypatch):
    from module.server import log_stats

    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "2026-06-20_oas1.txt").write_text(
        "\n".join([
            '2026-06-20 06:00:00.000 | script_task.py:0001 |     INFO | [STAT] {"ev":"acc_start","acc":"a","char":"c","svr":"s","tasks":["mail"]}',
            '2026-06-20 06:00:03.000 | script_task.py:0002 |     INFO | [STAT] {"ev":"task_end","acc":"a","char":"c","svr":"s","task":"mail","ok":true,"dur":3.0}',
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    result = log_stats.LogStatsService().build_stats("oas1", date(2026, 6, 20))

    account = result["multi"]["accounts"][0]
    assert account["duration_seconds"] == 3.0
    assert account["tasks"][0]["task"] == "mail"
