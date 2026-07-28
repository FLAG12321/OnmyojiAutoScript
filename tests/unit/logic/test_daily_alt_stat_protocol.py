import json
from enum import Enum
from types import SimpleNamespace

import pytest


class DemoEvent(Enum):
    FOO = 1


def test_stat_log_mixin_json_safe_converts_enum_values():
    from tasks.DailyAltAcc.stat_log import StatLogMixin

    # 统计日志最终会被 json.dumps 写成单行，因此 Enum 需要先转成稳定字符串。
    payload = StatLogMixin._json_safe({"event": DemoEvent.FOO, "items": [DemoEvent.FOO]})

    assert payload == {"event": "FOO", "items": ["FOO"]}


def test_multi_daily_alt_acc_enabled_task_keys_respects_config_flags():
    from tasks.MultiDailyAltAcc.config import ExtendedAccountInfo
    from tasks.MultiDailyAltAcc.script_task import ScriptTask

    # 账号级起始事件需要携带实际启用的子任务清单，方便后端按账号汇总。
    config = ExtendedAccountInfo()
    for name in [
        "alliedteam_battle_enable",
        "alliedteam_ap_enable",
        "mail_enable",
        "donatejade_enable",
        "courtyard_enable",
        "cooperation_enable",
        "returngift_enable",
        "weekaward_enable",
        "mysteryshop_enable",
        "kekkaiActivation_enable",
        "KekkaiUtilize_enable",
        "trialbattle_enable",
        "summon_up_enable",
        "publish_sr_enable",
    ]:
        setattr(config, name, False)
    config.tree_planting_enable = 0
    config.mail_enable = True
    config.cooperation_enable = True
    config.tree_planting_enable = 2
    config.alliedteam_battle_enable = True

    assert ScriptTask._enabled_task_keys(config) == [
        "mail",
        "cooperation",
        "tree",
        "alliedteam",
    ]


def _collect_stat_payloads(events):
    """从捕获的 logger.info 消息中提取 [STAT] JSON 载荷，保持顺序。"""
    payloads = []
    for message in events:
        if isinstance(message, str) and message.startswith("[STAT] "):
            payloads.append(json.loads(message[len("[STAT] "):]))
    return payloads


def test_stat_event_defines_run_and_switch_start_names():
    from tasks.DailyAltAcc.stat_log import StatEvent

    # 事件名字面值是任务端与后端解析器之间的协议，必须锁定。
    assert StatEvent.RUN_START == "run_start"
    assert StatEvent.RUN_END == "run_end"
    assert StatEvent.SWITCH_START == "switch_start"


def test_multi_switch_to_account_emits_switch_start_before_switch(monkeypatch):
    from tasks.MultiDailyAltAcc import script_task as mod

    # 捕获全部 logger.info 输出（stat_log 与 script_task 共享同一个 logger 对象）。
    events = []
    monkeypatch.setattr(mod.logger, "info", lambda msg, *args: events.append(msg))

    class _FakeSwitch:
        """替身 SwitchAccount：记录被调用的时刻并直接返回成功。"""

        def __init__(self, *_args):
            pass

        def switchAccount(self):
            events.append("SWITCH_CALLED")
            return True

    monkeypatch.setattr(mod, "SwitchAccount", _FakeSwitch)

    task = mod.ScriptTask.__new__(mod.ScriptTask)
    task.device = SimpleNamespace(stuck_record_clear=lambda: None)
    task.config = SimpleNamespace()
    account = SimpleNamespace(account="a@x.com", character="角色A", svr="一区")

    assert task._switch_to_account(account) is True

    payloads = _collect_stat_payloads(events)
    # switch_start 在前、switch 在后，且 switch_start 必须先于实际切号动作。
    assert payloads[0] == {"ev": "switch_start", "acc": "a@x.com", "char": "角色A", "svr": "一区"}
    assert payloads[1]["ev"] == "switch"
    assert payloads[1]["ok"] is True
    switch_start_index = next(
        i for i, e in enumerate(events) if isinstance(e, str) and '"switch_start"' in e
    )
    assert switch_start_index < events.index("SWITCH_CALLED")


def test_multi_run_emits_run_start_and_run_end(monkeypatch):
    from module.exception import TaskEnd
    from tasks.MultiDailyAltAcc import script_task as mod

    events = []
    monkeypatch.setattr(mod.logger, "info", lambda msg, *args: events.append(msg))

    # 用 __new__ 绕过重量级构造，再以实例属性替换 run() 依赖的内部方法。
    task = mod.ScriptTask.__new__(mod.ScriptTask)
    conf = SimpleNamespace(
        multi_daily_alt_acc_config=SimpleNamespace(
            total_returngift_enable=False,
            need_login_time=None,
            shutdown_after_finish=False,
            total_alliedteam_battle_enable=False,
        )
    )
    task.config = SimpleNamespace(config_name="oas1", multi_daily_alt_acc=conf)
    task._mark_task_start = lambda *a, **k: None
    task._update_task_returngift_enable = lambda *a, **k: None
    task._get_sorted_accounts = lambda *a, **k: []
    task._notify_daily_completion = lambda *a, **k: None
    task.next_run = lambda *a, **k: None
    task._mark_task_completed = lambda *a, **k: None

    with pytest.raises(TaskEnd):
        task.run()

    payloads = _collect_stat_payloads(events)
    # 一次运行必须以 run_start 开始、run_end 结束（finally 保证异常路径也发出）。
    assert payloads[0] == {"ev": "run_start"}
    assert payloads[-1] == {"ev": "run_end"}


def test_multi_run_end_emit_failure_does_not_block_completion_mark(monkeypatch):
    """审查m5：run_end 埋点抛异常时，任务完成标记仍必须执行，且不改变 TaskEnd 控制流。"""
    from module.exception import TaskEnd
    from tasks.MultiDailyAltAcc import script_task as mod

    monkeypatch.setattr(mod.logger, "info", lambda msg, *args: None)

    task = mod.ScriptTask.__new__(mod.ScriptTask)
    conf = SimpleNamespace(
        multi_daily_alt_acc_config=SimpleNamespace(
            total_returngift_enable=False,
            need_login_time=None,
            shutdown_after_finish=False,
            total_alliedteam_battle_enable=False,
        )
    )
    task.config = SimpleNamespace(config_name="oas1", multi_daily_alt_acc=conf)
    task._mark_task_start = lambda *a, **k: None
    task._update_task_returngift_enable = lambda *a, **k: None
    task._get_sorted_accounts = lambda *a, **k: []
    task._notify_daily_completion = lambda *a, **k: None
    task.next_run = lambda *a, **k: None
    completed = []
    task._mark_task_completed = lambda name: completed.append(name)

    def failing_emit(ev, **fields):
        # 仅让结束埋点失败，模拟日志写入 IO 故障
        if ev == "run_end":
            raise RuntimeError("log io failure")

    task.emit_stat = failing_emit

    with pytest.raises(TaskEnd):
        task.run()

    # 埋点失败不得阻断收尾：完成标记照常执行
    assert completed == ["oas1"]
