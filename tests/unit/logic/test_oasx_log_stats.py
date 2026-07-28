from datetime import date

import pytest


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
    assert account["tasks"][0] == {"task": "cooperation", "ok": True, "start_time": "2026-06-20 06:00:02.000", "duration_seconds": 4.0, "battle_count": 0, "battle_total_duration_seconds": 0.0, "battle_avg_duration_seconds": 0.0}
    assert account["coops"] == [{"ctype": "jade", "real": True, "time": "2026-06-20 06:00:05.000"}]
    assert account["mshops"] == [{"goods": "shepi", "price": 88, "time": "2026-06-20 06:00:08.000"}]
    # change-B 需求6：账号耗时按运行段累加，acc_start→acc_end 单段 = 10 秒
    assert account["segments"] == [
        {"start_time": "2026-06-20 06:00:00.000", "end_time": "2026-06-20 06:00:10.000", "duration_seconds": 10.0, "session": 0}
    ]
    # change-B 需求6：全天总耗时由后端汇总，前端不再自行 fold 累加
    assert result["multi"]["total_duration_seconds"] == 10.0
    # change-B 需求2：单次运行汇总为 1 个会话
    assert result["multi"]["sessions"] == [
        {"index": 0, "start_time": "2026-06-20 06:00:00.000", "end_time": "2026-06-20 06:00:10.000", "duration_seconds": 10.0, "account_count": 1}
    ]


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


def test_multi_stats_without_acc_start_and_identity_is_none(tmp_path, monkeypatch):
    from module.server import log_stats

    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "2026-06-20_oas1.txt").write_text(
        "\n".join([
            '2026-06-20 06:00:01.000 | script_task.py:0002 |     INFO | [STAT] {"ev":"switch","ok":false}',
            '2026-06-20 06:00:02.000 | script_task.py:0003 |     INFO | [STAT] {"ev":"task_end","task":"mail","ok":false,"dur":0.0}',
            '2026-06-20 06:00:03.000 | script_task.py:0004 |     INFO | [STAT] {"ev":"error","task":"mail","etype":"RuntimeError","emsg":"boom"}',
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    result = log_stats.LogStatsService().build_stats("oas1", date(2026, 6, 20))

    assert result["multi"] is None


def test_multi_stats_with_partial_identity_promotes_pending_account(tmp_path, monkeypatch):
    from module.server import log_stats

    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "2026-06-20_oas1.txt").write_text(
        "\n".join([
            '2026-06-20 06:00:01.000 | script_task.py:0002 |     INFO | [STAT] {"ev":"switch","ok":true}',
            '2026-06-20 06:00:02.000 | script_task.py:0003 |     INFO | [STAT] {"ev":"task_end","acc":"mail@example.com","char":"角色A","svr":"一区","task":"mail","ok":true,"dur":2.0}',
            '2026-06-20 06:00:04.000 | script_task.py:0004 |     INFO | [STAT] {"ev":"acc_end","acc":"mail@example.com","char":"角色A","svr":"一区"}',
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    result = log_stats.LogStatsService().build_stats("oas1", date(2026, 6, 20))

    assert result["multi"] is not None
    accounts = result["multi"]["accounts"]
    assert len(accounts) == 1
    account = accounts[0]


def test_multi_stats_identity_key_uses_char_acc_svr(tmp_path, monkeypatch):
    from module.server import log_stats

    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "2026-06-20_oas1.txt").write_text(
        "\n".join([
            '2026-06-20 06:00:00.000 | script_task.py:0001 |     INFO | [STAT] {"ev":"acc_start","acc":"acc-a@example.com","char":"角色A","svr":"一区"}',
            '2026-06-20 06:00:01.000 | script_task.py:0002 |     INFO | [STAT] {"ev":"acc_end","acc":"acc-a@example.com","char":"角色A","svr":"一区"}',
            '2026-06-20 06:00:02.000 | script_task.py:0003 |     INFO | [STAT] {"ev":"acc_start","acc":"acc-b@example.com","char":"角色A","svr":"一区"}',
            '2026-06-20 06:00:03.000 | script_task.py:0004 |     INFO | [STAT] {"ev":"acc_end","acc":"acc-b@example.com","char":"角色A","svr":"一区"}',
            '2026-06-20 06:00:04.000 | script_task.py:0005 |     INFO | [STAT] {"ev":"acc_start","acc":"acc-b@example.com","char":"角色A","svr":"二区"}',
            '2026-06-20 06:00:05.000 | script_task.py:0006 |     INFO | [STAT] {"ev":"acc_end","acc":"acc-b@example.com","char":"角色A","svr":"二区"}',
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    result = log_stats.LogStatsService().build_stats("oas1", date(2026, 6, 20))

    assert result["multi"] is not None
    accounts = result["multi"]["accounts"]
    assert len(accounts) == 3
    assert [
        (item["character"], item["account"], item["svr"])
        for item in accounts
    ] == [
        ("角色A", "acc-a@example.com", "一区"),
        ("角色A", "acc-b@example.com", "一区"),
        ("角色A", "acc-b@example.com", "二区"),
    ]


def test_multi_stats_tracks_battle_duration_by_account_and_task(tmp_path, monkeypatch):
    """多号统计应从战斗边界计算真实战斗耗时，并同时输出到账号和子任务两级。"""
    from module.server import log_stats

    log_root = tmp_path / "log"
    log_root.mkdir()
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    log_file = log_root / "2026-06-22_QMUMU1.txt"
    log_file.write_text(
        "\n".join(
            [
                '2026-06-22 10:00:00.000 | script_task.py:0001 |     INFO | [STAT] {"ev":"acc_start","acc":"a@example.com","char":"角色A","svr":"区服A","tasks":["mail"]}',
                '2026-06-22 10:00:01.000 | script_task.py:0002 |     INFO | [STAT] {"ev":"task_start","acc":"a@example.com","char":"角色A","svr":"区服A","task":"mail"}',
                "───────────────────────────── GENERAL BATTLE START ─────────────────────────────",
                '2026-06-22 10:00:02.000 | coop.py:0100 |     INFO | [STAT] {"ev":"coop","acc":"a@example.com","char":"角色A","svr":"区服A","ctype":"jade","real":true,"total":1}',
                '2026-06-22 10:00:07.000 | battle.py:0001 |     INFO | [STAT] {"ev":"battle","acc":"a@example.com","char":"角色A","svr":"区服A","count":1}',
                "───────────────────────────── GENERAL BATTLE START ─────────────────────────────",
                '2026-06-22 10:00:10.000 | script_task.py:0005 |     INFO | [STAT] {"ev":"task_end","acc":"a@example.com","char":"角色A","svr":"区服A","task":"mail","ok":true,"dur":9.0}',
                '2026-06-22 10:00:11.000 | script_task.py:0006 |     INFO | [STAT] {"ev":"acc_end","acc":"a@example.com","char":"角色A","svr":"区服A","err_count":0}',
            ]
        ),
        encoding="utf-8",
    )

    stats = log_stats.LogStatsService().build_stats("QMUMU1", date(2026, 6, 22))
    account = stats["multi"]["accounts"][0]
    task = account["tasks"][0]

    # 战斗边界计时：首条时间戳行 (10:00:02) 到第二条边界前末行 (10:00:07)，耗时 5 秒。
    assert account["battle_count"] == 1
    assert account["battle_total_duration_seconds"] == pytest.approx(5.0)
    assert account["battle_avg_duration_seconds"] == pytest.approx(5.0)
    assert task["battle_count"] == 1
    assert task["battle_total_duration_seconds"] == pytest.approx(5.0)
    assert task["battle_avg_duration_seconds"] == pytest.approx(5.0)


def test_multi_stats_handles_rich_split_stat_lines(tmp_path, monkeypatch):
    """RichHandler 会把 [STAT] 前缀与 JSON 拆成相邻两行，必须合并解析。"""
    from module.server import log_stats

    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "2026-06-20_oas1.txt").write_text(
        "\n".join([
            '2026-06-20 06:00:00.000 | stat_log.py:0034 |     INFO | [STAT]',
            '{"ev":"acc_start","acc":"a@x.com","char":"角色A","svr":"一区","tasks":["cooperation"]}',
            '2026-06-20 06:00:01.000 | cooperation.py:0100 |     INFO | [STAT]',
            '{"ev":"coop","acc":"a@x.com","char":"角色A","svr":"一区","ctype":"jade","real":true,"total":1}',
            '2026-06-20 06:00:05.000 | script_task.py:0004 |     INFO | [STAT]',
            '{"ev":"task_end","acc":"a@x.com","char":"角色A","svr":"一区","task":"cooperation","ok":true,"dur":4.0}',
            '2026-06-20 06:00:06.000 | script_task.py:0006 |     INFO | [STAT]',
            '{"ev":"acc_end","acc":"a@x.com","char":"角色A","svr":"一区","err_count":0}',
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    result = log_stats.LogStatsService().build_stats("oas1", date(2026, 6, 20))

    assert result["multi"] is not None
    accounts = result["multi"]["accounts"]
    assert len(accounts) == 1
    account = accounts[0]
    # 跨行后耗时、协作、子任务均须正确入账
    assert account["coop_total"] == 1
    assert account["coops"] == [{"ctype": "jade", "real": True, "time": "2026-06-20 06:00:01.000"}]
    assert account["tasks"] == [
        {"task": "cooperation", "ok": True, "start_time": None, "duration_seconds": 4.0, "battle_count": 0, "battle_total_duration_seconds": 0.0, "battle_avg_duration_seconds": 0.0}
    ]
    assert account["duration_seconds"] > 0


def test_multi_stats_battle_duration_survives_repeated_acc_start(tmp_path, monkeypatch):
    """回归测试：重复 acc_start（模拟切任务类型）不清零已累加的战斗耗时和战斗次数。

    - Bug A：第二次 acc_start 覆盖已有账号状态，battle_total_duration_seconds 被清零
    - Bug B：count=0 的 battle 清理事件将 battle_count 清零，导致平均耗时除零

    场景：同一账号，两次 acc_start（第一次 mail 任务，第二次 explore 任务），
    每轮各有一个战斗边界段，最后紧跟 count=0 清理事件。
    """
    from module.server import log_stats

    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "2026-06-20_oas1.txt").write_text(
        "\n".join([
            # 第一轮：mail 任务
            '2026-06-20 06:00:00.000 | script_task.py:0001 |     INFO | [STAT] {"ev":"acc_start","acc":"a@x.com","char":"角色A","svr":"一区","tasks":["mail"]}',
            '2026-06-20 06:00:01.000 | script_task.py:0002 |     INFO | [STAT] {"ev":"task_start","acc":"a@x.com","char":"角色A","svr":"一区","task":"mail"}',
            "───────────────────────────── GENERAL BATTLE START ─────────────────────────────",
            '2026-06-20 06:00:02.000 | battle.py:0100 |     INFO | inside battle 1',
            '2026-06-20 06:00:07.000 | battle.py:0100 |     INFO | [STAT] {"ev":"battle","acc":"a@x.com","char":"角色A","svr":"一区","count":1}',
            "───────────────────────────── GENERAL BATTLE START ─────────────────────────────",
            '2026-06-20 06:00:08.000 | script_task.py:0003 |     INFO | [STAT] {"ev":"task_end","acc":"a@x.com","char":"角色A","svr":"一区","task":"mail","ok":true,"dur":7.0}',
            # 第二轮：切换任务类型，同一账号再次 acc_start
            '2026-06-20 06:00:10.000 | script_task.py:0004 |     INFO | [STAT] {"ev":"acc_start","acc":"a@x.com","char":"角色A","svr":"一区","tasks":["explore"]}',
            '2026-06-20 06:00:11.000 | script_task.py:0005 |     INFO | [STAT] {"ev":"task_start","acc":"a@x.com","char":"角色A","svr":"一区","task":"explore"}',
            "───────────────────────────── GENERAL BATTLE START ─────────────────────────────",
            '2026-06-20 06:00:12.000 | explore.py:0100 |     INFO | inside battle 2',
            '2026-06-20 06:00:22.000 | explore.py:0100 |     INFO | [STAT] {"ev":"battle","acc":"a@x.com","char":"角色A","svr":"一区","count":2}',
            "───────────────────────────── GENERAL BATTLE START ─────────────────────────────",
            '2026-06-20 06:00:23.000 | script_task.py:0006 |     INFO | [STAT] {"ev":"task_end","acc":"a@x.com","char":"角色A","svr":"一区","task":"explore","ok":true,"dur":12.0}',
            # count=0 清理事件 —— 不应清零 battle_count
            '2026-06-20 06:00:25.000 | script_task.py:0007 |     INFO | [STAT] {"ev":"battle","acc":"a@x.com","char":"角色A","svr":"一区","count":0}',
            '2026-06-20 06:00:26.000 | script_task.py:0008 |     INFO | [STAT] {"ev":"acc_end","acc":"a@x.com","char":"角色A","svr":"一区","err_count":0}',
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    result = log_stats.LogStatsService().build_stats("oas1", date(2026, 6, 20))

    assert result["multi"] is not None
    accounts = result["multi"]["accounts"]
    assert len(accounts) == 1
    account = accounts[0]

    # Bug A 断言：战斗总耗时应为两轮战斗之和（5 + 10 = 15），而非第二轮单独的值
    assert account["battle_total_duration_seconds"] == pytest.approx(15.0)
    # 修复1：battle 事件按增量累加（1 + 2 = 3），count=0 清理事件累加 0 无影响
    assert account["battle_count"] == 3
    assert account["battle_avg_duration_seconds"] == pytest.approx(5.0)

    # start_time 应保留首次 acc_start 的时间，delta = 26 秒
    assert account["duration_seconds"] == pytest.approx(26.0)

    # 子任务级别校验
    assert len(account["tasks"]) == 2
    mail_task = account["tasks"][0]
    assert mail_task["task"] == "mail"
    assert mail_task["battle_count"] == 1
    assert mail_task["battle_total_duration_seconds"] == pytest.approx(5.0)

    explore_task = account["tasks"][1]
    assert explore_task["task"] == "explore"
    assert explore_task["battle_count"] == 1
    assert explore_task["battle_total_duration_seconds"] == pytest.approx(10.0)


def test_multi_stats_battle_duration_attributed_to_correct_account(tmp_path, monkeypatch):
    """回归测试：战斗耗时必须归属到战斗开始时的账号，而非战斗结束时被 acc_start 切换后的账号。

    场景：账号 A 的战斗段中途发生账号 B 的 acc_start，_active_key 被切换到 B。
    战斗关闭时耗时归属应使用战斗开始时的账号 A，而非 _active_key 指向的 B。
    """
    from module.server import log_stats

    log_root = tmp_path / "log"
    log_root.mkdir()
    log_file = log_root / "2026-06-22_QMUMU2.txt"
    log_file.write_text(
        "\n".join([
            # 账号 A 启动并开始子任务
            '2026-06-22 10:00:00.000 | script_task.py:0001 |     INFO | [STAT] {"ev":"acc_start","acc":"a@x.com","char":"角色A","svr":"一区","tasks":["mail"]}',
            '2026-06-22 10:00:01.000 | script_task.py:0002 |     INFO | [STAT] {"ev":"task_start","acc":"a@x.com","char":"角色A","svr":"一区","task":"mail"}',
            # 战斗段开始（属于账号 A 的战斗）
            "───────────────────────────── GENERAL BATTLE START ─────────────────────────────",
            '2026-06-22 10:00:02.000 | battle.py:0100 |     INFO | inside battle',
            # 战斗进行中，账号 B 启动 —— 触发 _active_key 切换到 B
            '2026-06-22 10:00:05.000 | script_task.py:0003 |     INFO | [STAT] {"ev":"acc_start","acc":"b@x.com","char":"角色B","svr":"二区","tasks":["explore"]}',
            '2026-06-22 10:00:06.000 | battle.py:0100 |     INFO | still in battle',
            # 战斗段结束 —— 此时 _active_key 是 B，但耗时应归属到 A
            "───────────────────────────── GENERAL BATTLE START ─────────────────────────────",
            '2026-06-22 10:00:07.000 | script_task.py:0004 |     INFO | [STAT] {"ev":"task_end","acc":"a@x.com","char":"角色A","svr":"一区","task":"mail","ok":true,"dur":7.0}',
            '2026-06-22 10:00:08.000 | script_task.py:0005 |     INFO | [STAT] {"ev":"acc_end","acc":"a@x.com","char":"角色A","svr":"一区","err_count":0}',
            '2026-06-22 10:00:09.000 | script_task.py:0006 |     INFO | [STAT] {"ev":"task_start","acc":"b@x.com","char":"角色B","svr":"二区","task":"explore"}',
            "───────────────────────────── GENERAL BATTLE START ─────────────────────────────",
            '2026-06-22 10:00:10.000 | explore.py:0100 |     INFO | B battle',
            "───────────────────────────── GENERAL BATTLE START ─────────────────────────────",
            '2026-06-22 10:00:11.000 | script_task.py:0007 |     INFO | [STAT] {"ev":"task_end","acc":"b@x.com","char":"角色B","svr":"二区","task":"explore","ok":true,"dur":2.0}',
            '2026-06-22 10:00:12.000 | script_task.py:0008 |     INFO | [STAT] {"ev":"acc_end","acc":"b@x.com","char":"角色B","svr":"二区","err_count":0}',
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    stats = log_stats.LogStatsService().build_stats("QMUMU2", date(2026, 6, 22))
    accounts = stats["multi"]["accounts"]
    assert len(accounts) == 2

    account_a = next(ac for ac in accounts if ac["character"] == "角色A")
    account_b = next(ac for ac in accounts if ac["character"] == "角色B")

    # 账号 A 的战斗耗时：10:00:02 → 10:00:06 = 4 秒
    # 当前 bug（修复前）：耗时被错误归属到 B（战斗关闭时 _active_key 指向 B）
    assert account_a["battle_total_duration_seconds"] == pytest.approx(4.0)
    assert account_a["battle_count"] == 1

    # 账号 B 的战斗耗时：10:00:10 → 边界关闭（无中间时间戳）= 0 秒（未计入）
    assert account_b["battle_total_duration_seconds"] == pytest.approx(0.0)
    assert account_b["battle_count"] == 0

    # A 的子任务 mail 应含边界计时战斗数据
    mail_task = account_a["tasks"][0]
    assert mail_task["task"] == "mail"
    assert mail_task["battle_count"] == 1
    assert mail_task["battle_total_duration_seconds"] == pytest.approx(4.0)


def test_multi_stats_duration_sums_segments_across_separate_runs(tmp_path, monkeypatch):
    """change-B 需求6：同一账号一天内多次独立运行，总耗时应为各运行段之和，
    而非首末事件的单一跨度（避免跨空闲间隙重复计算）。"""
    from module.server import log_stats

    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "2026-06-20_oas1.txt").write_text(
        "\n".join([
            # 第一次运行 06:00:00 → 06:10:00（10 分钟）
            '2026-06-20 06:00:00.000 | script_task.py:0001 |     INFO | [STAT] {"ev":"acc_start","acc":"a@x.com","char":"角色A","svr":"一区","tasks":["mail"]}',
            '2026-06-20 06:10:00.000 | script_task.py:0002 |     INFO | [STAT] {"ev":"acc_end","acc":"a@x.com","char":"角色A","svr":"一区","err_count":0}',
            # 第二次运行 14:00:00 → 14:10:00（10 分钟），间隔远超会话阈值
            '2026-06-20 14:00:00.000 | script_task.py:0003 |     INFO | [STAT] {"ev":"acc_start","acc":"a@x.com","char":"角色A","svr":"一区","tasks":["mail"]}',
            '2026-06-20 14:10:00.000 | script_task.py:0004 |     INFO | [STAT] {"ev":"acc_end","acc":"a@x.com","char":"角色A","svr":"一区","err_count":0}',
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    result = log_stats.LogStatsService().build_stats("oas1", date(2026, 6, 20))
    account = result["multi"]["accounts"][0]

    # 旧口径（首末跨度）会是 8 小时 = 28800 秒；新口径应为两段各 600 秒 = 1200 秒
    assert account["duration_seconds"] == pytest.approx(1200.0)
    assert len(account["segments"]) == 2
    assert account["segments"][0]["duration_seconds"] == pytest.approx(600.0)
    assert account["segments"][1]["duration_seconds"] == pytest.approx(600.0)
    # 总耗时同样为两段之和，而非 8 小时
    assert result["multi"]["total_duration_seconds"] == pytest.approx(1200.0)


def test_multi_stats_cross_account_preempt_closes_prior_segment(tmp_path, monkeypatch):
    """change-B 需求6：上一账号未发 acc_end 时，下一账号 acc_start 应以本次起点
    闭合上一账号的运行段。"""
    from module.server import log_stats

    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "2026-06-20_oas1.txt").write_text(
        "\n".join([
            '2026-06-20 06:00:00.000 | script_task.py:0001 |     INFO | [STAT] {"ev":"acc_start","acc":"a@x.com","char":"角色A","svr":"一区","tasks":["mail"]}',
            '2026-06-20 06:00:05.000 | script_task.py:0002 |     INFO | [STAT] {"ev":"task_end","acc":"a@x.com","char":"角色A","svr":"一区","task":"mail","ok":true,"dur":5.0}',
            # 账号 A 未发 acc_end，账号 B 直接开始 —— 应在 06:00:10 闭合 A 的段
            '2026-06-20 06:00:10.000 | script_task.py:0003 |     INFO | [STAT] {"ev":"acc_start","acc":"b@x.com","char":"角色B","svr":"一区","tasks":["mail"]}',
            '2026-06-20 06:00:20.000 | script_task.py:0004 |     INFO | [STAT] {"ev":"acc_end","acc":"b@x.com","char":"角色B","svr":"一区","err_count":0}',
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    result = log_stats.LogStatsService().build_stats("oas1", date(2026, 6, 20))
    accounts = result["multi"]["accounts"]
    account_a = next(ac for ac in accounts if ac["character"] == "角色A")
    account_b = next(ac for ac in accounts if ac["character"] == "角色B")

    # A 的段在 B 开始时刻（06:00:10）闭合：00:00 → 00:10 = 10 秒
    assert account_a["duration_seconds"] == pytest.approx(10.0)
    assert len(account_a["segments"]) == 1
    # B 的段正常 acc_end 闭合：10 秒
    assert account_b["duration_seconds"] == pytest.approx(10.0)
    # 两账号顺序执行，总会话耗时 = 20 秒
    assert result["multi"]["total_duration_seconds"] == pytest.approx(20.0)


def test_multi_stats_splits_sessions_by_run_start(tmp_path, monkeypatch):
    """需求1：会话按 run_start 事件切分，与时间间隔无关——5 分钟内的两次调度也是两个会话。"""
    from module.server import log_stats

    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "2026-06-20_oas1.txt").write_text(
        "\n".join([
            # 会话 0：第一次调度（如凌晨只回礼）
            '2026-06-20 06:00:00.000 | script_task.py:0001 |     INFO | [STAT] {"ev":"run_start"}',
            '2026-06-20 06:00:01.000 | script_task.py:0002 |     INFO | [STAT] {"ev":"acc_start","acc":"a@x.com","char":"角色A","svr":"一区","tasks":["returngift"]}',
            '2026-06-20 06:02:00.000 | script_task.py:0003 |     INFO | [STAT] {"ev":"acc_end","acc":"a@x.com","char":"角色A","svr":"一区","err_count":0}',
            # 会话 1：仅 3 分钟后的第二次调度（如同心战斗），旧 gap 口径会被错误合并
            '2026-06-20 06:05:00.000 | script_task.py:0004 |     INFO | [STAT] {"ev":"run_start"}',
            '2026-06-20 06:05:01.000 | script_task.py:0005 |     INFO | [STAT] {"ev":"acc_start","acc":"a@x.com","char":"角色A","svr":"一区","tasks":["alliedteam"]}',
            '2026-06-20 06:07:00.000 | script_task.py:0006 |     INFO | [STAT] {"ev":"acc_end","acc":"a@x.com","char":"角色A","svr":"一区","err_count":0}',
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    result = log_stats.LogStatsService().build_stats("oas1", date(2026, 6, 20))
    sessions = result["multi"]["sessions"]

    assert len(sessions) == 2
    assert sessions[0]["index"] == 0
    assert sessions[0]["start_time"] == "2026-06-20 06:00:01.000"
    assert sessions[1]["index"] == 1
    assert sessions[1]["start_time"] == "2026-06-20 06:05:01.000"
    account = result["multi"]["accounts"][0]
    assert [seg["session"] for seg in account["segments"]] == [0, 1]


def test_multi_stats_run_end_closes_open_segment(tmp_path, monkeypatch):
    """修复5：run_end 以事件时刻闭合未闭合运行段（覆盖正常结束与异常上抛路径）。"""
    from module.server import log_stats

    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "2026-06-20_oas1.txt").write_text(
        "\n".join([
            '2026-06-20 06:00:00.000 | script_task.py:0001 |     INFO | [STAT] {"ev":"run_start"}',
            '2026-06-20 06:00:01.000 | script_task.py:0002 |     INFO | [STAT] {"ev":"acc_start","acc":"a@x.com","char":"角色A","svr":"一区","tasks":["mail"]}',
            # 账号未发 acc_end（异常上抛），run_end 应以 06:01:00 闭合运行段
            '2026-06-20 06:00:30.000 | script_task.py:0003 |     INFO | [STAT] {"ev":"task_end","acc":"a@x.com","char":"角色A","svr":"一区","task":"mail","ok":false,"dur":29.0}',
            '2026-06-20 06:01:00.000 | script_task.py:0004 |     INFO | [STAT] {"ev":"run_end"}',
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    result = log_stats.LogStatsService().build_stats("oas1", date(2026, 6, 20))
    account = result["multi"]["accounts"][0]

    assert len(account["segments"]) == 1
    assert account["segments"][0]["end_time"] == "2026-06-20 06:01:00.000"
    assert account["duration_seconds"] == pytest.approx(59.0)


def test_multi_stats_new_run_closes_leftover_segment_at_last_event(tmp_path, monkeypatch):
    """需求1：硬中断（无 run_end）后重跑，遗留段以其最后事件时刻闭合，不跨会话计时。"""
    from module.server import log_stats

    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "2026-06-20_oas1.txt").write_text(
        "\n".join([
            '2026-06-20 06:00:00.000 | script_task.py:0001 |     INFO | [STAT] {"ev":"run_start"}',
            '2026-06-20 06:00:01.000 | script_task.py:0002 |     INFO | [STAT] {"ev":"acc_start","acc":"a@x.com","char":"角色A","svr":"一区","tasks":["mail"]}',
            '2026-06-20 06:00:30.000 | script_task.py:0003 |     INFO | [STAT] {"ev":"task_end","acc":"a@x.com","char":"角色A","svr":"一区","task":"mail","ok":true,"dur":29.0}',
            # 进程被杀，无 acc_end / run_end；10 分钟后手动重跑
            '2026-06-20 06:10:00.000 | script_task.py:0004 |     INFO | [STAT] {"ev":"run_start"}',
            '2026-06-20 06:10:01.000 | script_task.py:0005 |     INFO | [STAT] {"ev":"acc_start","acc":"a@x.com","char":"角色A","svr":"一区","tasks":["mail"]}',
            '2026-06-20 06:12:00.000 | script_task.py:0006 |     INFO | [STAT] {"ev":"acc_end","acc":"a@x.com","char":"角色A","svr":"一区","err_count":0}',
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    result = log_stats.LogStatsService().build_stats("oas1", date(2026, 6, 20))
    account = result["multi"]["accounts"][0]

    assert len(account["segments"]) == 2
    # 遗留段以最后事件时刻（06:00:30）闭合，属会话 0
    assert account["segments"][0]["end_time"] == "2026-06-20 06:00:30.000"
    assert account["segments"][0]["session"] == 0
    assert account["segments"][1]["session"] == 1
    assert account["duration_seconds"] == pytest.approx(29.0 + 119.0)


def test_multi_stats_battles_after_run_end_not_attributed(tmp_path, monkeypatch):
    """审查发现：run_end 后其他任务（如 Orochi）的战斗边界不得归属到上一会话的账号。

    场景：MultiAcc 最后一个子任务 task_end 丢失（异常上抛），run_end 之后
    同一日志文件里出现其他任务的 GENERAL BATTLE START 边界。
    """
    from module.server import log_stats

    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "2026-06-20_oas1.txt").write_text(
        "\n".join([
            '2026-06-20 06:00:00.000 | script_task.py:0001 |     INFO | [STAT] {"ev":"run_start"}',
            '2026-06-20 06:00:01.000 | script_task.py:0002 |     INFO | [STAT] {"ev":"acc_start","acc":"a@x.com","char":"角色A","svr":"一区","tasks":["mail"]}',
            '2026-06-20 06:00:02.000 | script_task.py:0003 |     INFO | [STAT] {"ev":"task_start","acc":"a@x.com","char":"角色A","svr":"一区","task":"mail"}',
            # mail 的 task_end 丢失，任务异常上抛后 run_end
            '2026-06-20 06:00:30.000 | script_task.py:0004 |     INFO | [STAT] {"ev":"run_end"}',
            # 之后是其他任务（如 Orochi）的战斗日志
            "───────────────────────────── GENERAL BATTLE START ─────────────────────────────",
            '2026-06-20 06:01:00.000 | battle.py:0100 |     INFO | orochi battle line',
            '2026-06-20 06:02:00.000 | battle.py:0101 |     INFO | orochi battle line 2',
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    result = log_stats.LogStatsService().build_stats("oas1", date(2026, 6, 20))
    account = result["multi"]["accounts"][0]

    # 未修复时：陈旧的 _current_task_name 使战斗边界被武装，60 秒战斗错误累加到账号 A
    assert account["battle_count"] == 0
    assert account["battle_total_duration_seconds"] == pytest.approx(0.0)


def test_multi_stats_switch_start_opens_segment_before_acc_start(tmp_path, monkeypatch):
    """需求2：账号运行段从 switch_start（切号起点）起算，而非 acc_start（切号完成后）。"""
    from module.server import log_stats

    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "2026-06-20_oas1.txt").write_text(
        "\n".join([
            '2026-06-20 06:00:00.000 | script_task.py:0001 |     INFO | [STAT] {"ev":"run_start"}',
            '2026-06-20 06:00:01.000 | script_task.py:0002 |     INFO | [STAT] {"ev":"switch_start","acc":"a@x.com","char":"角色A","svr":"一区"}',
            # 切号耗时 40 秒
            '2026-06-20 06:00:41.000 | script_task.py:0003 |     INFO | [STAT] {"ev":"switch","acc":"a@x.com","char":"角色A","svr":"一区","ok":true}',
            '2026-06-20 06:00:42.000 | script_task.py:0004 |     INFO | [STAT] {"ev":"acc_start","acc":"a@x.com","char":"角色A","svr":"一区","tasks":["mail"]}',
            '2026-06-20 06:01:42.000 | script_task.py:0005 |     INFO | [STAT] {"ev":"acc_end","acc":"a@x.com","char":"角色A","svr":"一区","err_count":0}',
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    result = log_stats.LogStatsService().build_stats("oas1", date(2026, 6, 20))
    account = result["multi"]["accounts"][0]

    # 单一运行段：acc_start 不得关闭重开 switch_start 已开启的段
    assert len(account["segments"]) == 1
    assert account["segments"][0]["start_time"] == "2026-06-20 06:00:01.000"
    assert account["segments"][0]["end_time"] == "2026-06-20 06:01:42.000"
    # 总耗时 101 秒 = 切号 41 秒 + 任务 60 秒
    assert account["duration_seconds"] == pytest.approx(101.0)


def test_multi_stats_failed_switch_retries_accumulate_to_target_account(tmp_path, monkeypatch):
    """需求2：切号失败与重试的耗时也归属目标账号，多段累加。"""
    from module.server import log_stats

    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "2026-06-20_oas1.txt").write_text(
        "\n".join([
            '2026-06-20 06:00:00.000 | script_task.py:0001 |     INFO | [STAT] {"ev":"run_start"}',
            # 第一次切号失败（耗时 70 秒后重试）
            '2026-06-20 06:00:10.000 | script_task.py:0002 |     INFO | [STAT] {"ev":"switch_start","acc":"a@x.com","char":"角色A","svr":"一区"}',
            '2026-06-20 06:01:00.000 | script_task.py:0003 |     INFO | [STAT] {"ev":"switch","acc":"a@x.com","char":"角色A","svr":"一区","ok":false}',
            # 重试：第二个 switch_start 以自身时刻闭合上一段并开启新段
            '2026-06-20 06:01:20.000 | script_task.py:0004 |     INFO | [STAT] {"ev":"switch_start","acc":"a@x.com","char":"角色A","svr":"一区"}',
            '2026-06-20 06:01:50.000 | script_task.py:0005 |     INFO | [STAT] {"ev":"switch","acc":"a@x.com","char":"角色A","svr":"一区","ok":true}',
            '2026-06-20 06:01:51.000 | script_task.py:0006 |     INFO | [STAT] {"ev":"acc_start","acc":"a@x.com","char":"角色A","svr":"一区","tasks":["mail"]}',
            '2026-06-20 06:03:00.000 | script_task.py:0007 |     INFO | [STAT] {"ev":"acc_end","acc":"a@x.com","char":"角色A","svr":"一区","err_count":0}',
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    result = log_stats.LogStatsService().build_stats("oas1", date(2026, 6, 20))
    account = result["multi"]["accounts"][0]

    assert len(account["segments"]) == 2
    # 第一段 06:00:10 → 06:01:20（70 秒），第二段 06:01:20 → 06:03:00（100 秒）
    assert account["segments"][0]["duration_seconds"] == pytest.approx(70.0)
    assert account["segments"][1]["duration_seconds"] == pytest.approx(100.0)
    assert account["duration_seconds"] == pytest.approx(170.0)


def test_multi_stats_battle_events_accumulate(tmp_path, monkeypatch):
    """修复1：battle 事件的 count 是增量语义（发出侧为本次新增场数），聚合器应累加而非覆盖。"""
    from module.server import log_stats

    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "2026-06-20_oas1.txt").write_text(
        "\n".join([
            '2026-06-20 06:00:00.000 | script_task.py:0001 |     INFO | [STAT] {"ev":"acc_start","acc":"a@x.com","char":"角色A","svr":"一区","tasks":["alliedteam"]}',
            '2026-06-20 06:00:10.000 | alliedteam.py:0041 |     INFO | [STAT] {"ev":"battle","acc":"a@x.com","char":"角色A","svr":"一区","count":3}',
            '2026-06-20 06:00:20.000 | alliedteam.py:0041 |     INFO | [STAT] {"ev":"battle","acc":"a@x.com","char":"角色A","svr":"一区","count":2}',
            '2026-06-20 06:00:30.000 | script_task.py:0002 |     INFO | [STAT] {"ev":"acc_end","acc":"a@x.com","char":"角色A","svr":"一区","err_count":0}',
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    result = log_stats.LogStatsService().build_stats("oas1", date(2026, 6, 20))
    account = result["multi"]["accounts"][0]

    # 3 + 2 = 5，覆盖语义会错误地得到 2
    assert account["battle_count"] == 5


def test_multi_stats_error_records_include_time(tmp_path, monkeypatch):
    """修复2：error 记录带事件时刻，供前端按会话筛选（与 coop/mshop 同款）。"""
    from module.server import log_stats

    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "2026-06-20_oas1.txt").write_text(
        "\n".join([
            '2026-06-20 06:00:00.000 | script_task.py:0001 |     INFO | [STAT] {"ev":"acc_start","acc":"a@x.com","char":"角色A","svr":"一区","tasks":["mail"]}',
            '2026-06-20 06:00:05.000 | script_task.py:0002 |     INFO | [STAT] {"ev":"error","acc":"a@x.com","char":"角色A","svr":"一区","task":"mail","etype":"RuntimeError","emsg":"boom"}',
            '2026-06-20 06:00:10.000 | script_task.py:0003 |     INFO | [STAT] {"ev":"acc_end","acc":"a@x.com","char":"角色A","svr":"一区","err_count":1}',
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    result = log_stats.LogStatsService().build_stats("oas1", date(2026, 6, 20))
    account = result["multi"]["accounts"][0]

    assert account["errors"] == [
        {"task": "mail", "etype": "RuntimeError", "emsg": "boom", "time": "2026-06-20 06:00:05.000"}
    ]


def test_multi_stats_battle_ends_at_result_line(tmp_path, monkeypatch):
    """修复3：Battle result is 行闭合战斗计时，战后领奖等动作不再计入战斗时长。"""
    from module.server import log_stats

    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "2026-06-20_oas1.txt").write_text(
        "\n".join([
            '2026-06-20 06:00:00.000 | script_task.py:0001 |     INFO | [STAT] {"ev":"acc_start","acc":"a@x.com","char":"角色A","svr":"一区","tasks":["alliedteam"]}',
            '2026-06-20 06:00:01.000 | script_task.py:0002 |     INFO | [STAT] {"ev":"task_start","acc":"a@x.com","char":"角色A","svr":"一区","task":"alliedteam"}',
            "───────────────────────────── GENERAL BATTLE START ─────────────────────────────",
            '2026-06-20 06:00:10.000 | battle.py:0100 |     INFO | inside battle',
            # 战斗在 06:00:20 出结果，此后到 task_end 的 35 秒是战后动作，不应计入战斗时长
            '2026-06-20 06:00:20.000 | general_battle.py:0182 |     INFO | Battle result is win',
            '2026-06-20 06:00:50.000 | battle.py:0101 |     INFO | post battle actions',
            '2026-06-20 06:00:55.000 | script_task.py:0003 |     INFO | [STAT] {"ev":"task_end","acc":"a@x.com","char":"角色A","svr":"一区","task":"alliedteam","ok":true,"dur":54.0}',
            '2026-06-20 06:01:00.000 | script_task.py:0004 |     INFO | [STAT] {"ev":"acc_end","acc":"a@x.com","char":"角色A","svr":"一区","err_count":0}',
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    result = log_stats.LogStatsService().build_stats("oas1", date(2026, 6, 20))
    account = result["multi"]["accounts"][0]
    task = account["tasks"][0]

    # 战斗时长 = 06:00:10 → 06:00:20 = 10 秒（未修复时延伸到 task_end 行时刻 06:00:55，得 45 秒）
    assert task["battle_count"] == 1
    assert task["battle_total_duration_seconds"] == pytest.approx(10.0)


def test_task_stats_battle_ends_at_result_line(tmp_path, monkeypatch):
    """修复3：单任务统计（LogStatsParser）同样以 Battle result is 行闭合战斗。"""
    from module.server import log_stats

    eq_line = "═" * 30
    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "2026-06-20_oas1.txt").write_text(
        "\n".join([
            eq_line,
            "─────────── Orochi ───────────",
            eq_line,
            '2026-06-20 06:00:00.000 | script.py:0001 |     INFO | task begin',
            "───────────────────────────── GENERAL BATTLE START ─────────────────────────────",
            '2026-06-20 06:00:10.000 | battle.py:0100 |     INFO | inside battle',
            '2026-06-20 06:00:20.000 | general_battle.py:0182 |     INFO | Battle result is win',
            '2026-06-20 06:00:50.000 | battle.py:0101 |     INFO | post battle actions',
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    result = log_stats.LogStatsService().build_stats("oas1", date(2026, 6, 20))
    battle = result["tasks"]["Orochi"]["battle"]

    assert battle["count"] == 1
    # 平均时长 = 单场 10 秒（06:00:10 → 06:00:20）
    assert battle["avg_duration_seconds"] == pytest.approx(10.0)


def test_multi_stats_battle_ends_at_reconfirm_line(tmp_path, monkeypatch):
    """修复3：领奖路径（I_REWARD/I_REWARD_GOLD）不输出 Battle result is，
    由四条退出路径共同经过的 Reconfirm the results of the battle 行闭合战斗。"""
    from module.server import log_stats

    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "2026-06-20_oas1.txt").write_text(
        "\n".join([
            '2026-06-20 06:00:00.000 | script_task.py:0001 |     INFO | [STAT] {"ev":"acc_start","acc":"a@x.com","char":"角色A","svr":"一区","tasks":["alliedteam"]}',
            '2026-06-20 06:00:01.000 | script_task.py:0002 |     INFO | [STAT] {"ev":"task_start","acc":"a@x.com","char":"角色A","svr":"一区","task":"alliedteam"}',
            "───────────────────────────── GENERAL BATTLE START ─────────────────────────────",
            '2026-06-20 06:00:10.000 | battle.py:0100 |     INFO | inside battle',
            # 领奖路径：无 Battle result is 行，靠 Reconfirm 行闭合
            '2026-06-20 06:00:20.000 | general_battle.py:0208 |     INFO | Reconfirm the results of the battle',
            '2026-06-20 06:00:50.000 | battle.py:0101 |     INFO | post battle actions',
            '2026-06-20 06:00:55.000 | script_task.py:0003 |     INFO | [STAT] {"ev":"task_end","acc":"a@x.com","char":"角色A","svr":"一区","task":"alliedteam","ok":true,"dur":54.0}',
            '2026-06-20 06:01:00.000 | script_task.py:0004 |     INFO | [STAT] {"ev":"acc_end","acc":"a@x.com","char":"角色A","svr":"一区","err_count":0}',
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    result = log_stats.LogStatsService().build_stats("oas1", date(2026, 6, 20))
    task = result["multi"]["accounts"][0]["tasks"][0]

    assert task["battle_count"] == 1
    assert task["battle_total_duration_seconds"] == pytest.approx(10.0)


def test_multi_stats_unclosed_battle_not_leaked_to_next_task(tmp_path, monkeypatch):
    """修复4：上一子任务 task_end 丢失时，其未闭合战斗不得错误归属到下一子任务。"""
    from module.server import log_stats

    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "2026-06-20_oas1.txt").write_text(
        "\n".join([
            '2026-06-20 06:00:00.000 | script_task.py:0001 |     INFO | [STAT] {"ev":"acc_start","acc":"a@x.com","char":"角色A","svr":"一区","tasks":["mail","alliedteam"]}',
            '2026-06-20 06:00:01.000 | script_task.py:0002 |     INFO | [STAT] {"ev":"task_start","acc":"a@x.com","char":"角色A","svr":"一区","task":"mail"}',
            "───────────────────────────── GENERAL BATTLE START ─────────────────────────────",
            '2026-06-20 06:00:10.000 | battle.py:0100 |     INFO | inside battle',
            # mail 的 task_end 丢失，直接开始下一个子任务
            '2026-06-20 06:00:30.000 | script_task.py:0003 |     INFO | [STAT] {"ev":"task_start","acc":"a@x.com","char":"角色A","svr":"一区","task":"alliedteam"}',
            '2026-06-20 06:00:40.000 | script_task.py:0004 |     INFO | [STAT] {"ev":"task_end","acc":"a@x.com","char":"角色A","svr":"一区","task":"alliedteam","ok":true,"dur":10.0}',
            '2026-06-20 06:00:50.000 | script_task.py:0005 |     INFO | [STAT] {"ev":"acc_end","acc":"a@x.com","char":"角色A","svr":"一区","err_count":0}',
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    result = log_stats.LogStatsService().build_stats("oas1", date(2026, 6, 20))
    account = result["multi"]["accounts"][0]
    alliedteam_task = account["tasks"][0]

    # alliedteam 自身无战斗，mail 的遗留战斗不得计入
    assert alliedteam_task["task"] == "alliedteam"
    assert alliedteam_task["battle_count"] == 0
