from enum import Enum


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
