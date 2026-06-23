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
    # Bug B 断言：count=0 不应清零战斗次数，应保留最后有效值 2
    assert account["battle_count"] == 2
    assert account["battle_avg_duration_seconds"] == pytest.approx(7.5)

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


def test_multi_stats_splits_sessions_by_gap(tmp_path, monkeypatch):
    """change-B 需求2：相邻事件间隔超过阈值应切分为多次 MultiAcc 运行会话。"""
    from module.server import log_stats

    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "2026-06-20_oas1.txt").write_text(
        "\n".join([
            # 会话 0：账号 A
            '2026-06-20 06:00:00.000 | script_task.py:0001 |     INFO | [STAT] {"ev":"acc_start","acc":"a@x.com","char":"角色A","svr":"一区","tasks":["mail"]}',
            '2026-06-20 06:05:00.000 | script_task.py:0002 |     INFO | [STAT] {"ev":"acc_end","acc":"a@x.com","char":"角色A","svr":"一区","err_count":0}',
            # 会话 1：间隔 20 分钟后再次运行
            '2026-06-20 06:25:00.000 | script_task.py:0003 |     INFO | [STAT] {"ev":"acc_start","acc":"a@x.com","char":"角色A","svr":"一区","tasks":["mail"]}',
            '2026-06-20 06:30:00.000 | script_task.py:0004 |     INFO | [STAT] {"ev":"acc_end","acc":"a@x.com","char":"角色A","svr":"一区","err_count":0}',
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(log_stats, "LOG_ROOT", log_root)

    result = log_stats.LogStatsService().build_stats("oas1", date(2026, 6, 20))
    sessions = result["multi"]["sessions"]

    assert len(sessions) == 2
    assert sessions[0]["index"] == 0
    assert sessions[0]["start_time"] == "2026-06-20 06:00:00.000"
    assert sessions[0]["end_time"] == "2026-06-20 06:05:00.000"
    assert sessions[0]["duration_seconds"] == pytest.approx(300.0)
    assert sessions[0]["account_count"] == 1
    assert sessions[1]["index"] == 1
    assert sessions[1]["start_time"] == "2026-06-20 06:25:00.000"
    assert sessions[1]["account_count"] == 1
