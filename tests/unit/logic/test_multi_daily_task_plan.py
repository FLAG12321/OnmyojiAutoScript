"""MultiDailyAltAcc 模块级 task_plan 的定向测试。"""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest

from tasks.MultiDailyAltAcc.task_plan import (
    DEFAULT_TASK_PLAN,
    TASK_KEYS,
    TaskPlanError,
    load_task_plan,
    parse_task_plan,
)


def _write_plan(path, plan):
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.mark.unit
def test_missing_plan_generates_default_once_and_existing_file_stays_unchanged(tmp_path):
    path = tmp_path / "task_plan.json"
    plan = load_task_plan(path)
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8")) == DEFAULT_TASK_PLAN
    before = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns
    assert load_task_plan(path) == plan
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == before_mtime


@pytest.mark.unit
def test_concurrent_first_loaders_publish_one_complete_default(tmp_path, monkeypatch):
    """两个 loader 同时首次启动时，只会读到完整的原子发布文件。"""
    from tasks.MultiDailyAltAcc import task_plan

    path = tmp_path / "task_plan.json"
    start = threading.Barrier(2)
    publish = threading.Barrier(2)
    real_link = task_plan.os.link

    def synchronized_link(source, destination):
        # 强制两个 loader 都完成临时文件写入，再竞争最终名称发布。
        publish.wait(timeout=5)
        return real_link(source, destination)

    monkeypatch.setattr(task_plan.os, "link", synchronized_link)

    def load_concurrently():
        start.wait(timeout=5)
        return load_task_plan(path)

    with ThreadPoolExecutor(max_workers=2) as executor:
        plans = list(executor.map(lambda _: load_concurrently(), range(2)))

    assert plans[0] == plans[1]
    assert json.loads(path.read_text(encoding="utf-8")) == DEFAULT_TASK_PLAN
    assert list(tmp_path.glob(".task_plan.json.*.tmp")) == []


@pytest.mark.unit
def test_concurrent_loaders_do_not_overwrite_existing_user_plan(tmp_path):
    path = tmp_path / "task_plan.json"
    raw = deepcopy(DEFAULT_TASK_PLAN)
    raw["schedule"]["morning_time"] = "06:30"
    _write_plan(path, raw)
    before = path.read_bytes()
    start = threading.Barrier(2)

    def load_concurrently():
        start.wait(timeout=5)
        return load_task_plan(path)

    with ThreadPoolExecutor(max_workers=2) as executor:
        plans = list(executor.map(lambda _: load_concurrently(), range(2)))

    assert all(plan.morning_time.hour == 6 and plan.morning_time.minute == 30 for plan in plans)
    assert path.read_bytes() == before
    assert list(tmp_path.glob(".task_plan.json.*.tmp")) == []


@pytest.mark.unit
@pytest.mark.parametrize("mutate", [
    lambda plan: plan["schedule"].__setitem__("morning_time", "6:05"),
    lambda plan: plan["schedule"].__setitem__("random_delay_minutes", -1),
    lambda plan: plan["schedule"].__setitem__("random_delay_minutes", True),
    lambda plan: plan["morning"].__setitem__("unknown", True),
    lambda plan: plan["afternoon"].__setitem__("mail", "true"),
])
def test_invalid_existing_plan_raises_without_overwriting(tmp_path, mutate):
    path = tmp_path / "task_plan.json"
    raw = deepcopy(DEFAULT_TASK_PLAN)
    mutate(raw)
    _write_plan(path, raw)
    before = path.read_bytes()
    with pytest.raises(TaskPlanError):
        load_task_plan(path)
    assert path.read_bytes() == before


@pytest.mark.unit
def test_schedule_target_is_one_draw_and_zero_delay_uses_exact_base(monkeypatch, tmp_path):
    path = tmp_path / "task_plan.json"
    raw = deepcopy(DEFAULT_TASK_PLAN)
    raw["schedule"].update(morning_time="06:30", afternoon_time="18:20", random_delay_minutes=0)
    _write_plan(path, raw)
    plan = load_task_plan(path)
    target = plan.schedule_target("morning", datetime(2026, 8, 25, 0, 20, 59))
    assert target.delay_minutes == 0
    assert target.target == datetime(2026, 8, 25, 6, 30)

    raw["schedule"]["random_delay_minutes"] = 30
    _write_plan(path, raw)
    plan = load_task_plan(path)
    draws = iter([15, 7])
    monkeypatch.setattr("tasks.MultiDailyAltAcc.task_plan.random.randint", lambda low, high: next(draws))
    morning = plan.schedule_target("morning", datetime(2026, 8, 25, 0, 20))
    afternoon = plan.schedule_target("afternoon", datetime(2026, 8, 25, 6, 20))
    assert (morning.delay_minutes, morning.target) == (15, datetime(2026, 8, 25, 6, 45))
    assert (afternoon.delay_minutes, afternoon.target) == (7, datetime(2026, 8, 25, 18, 27))


@pytest.mark.unit
def test_plan_rejects_delay_that_crosses_stage_boundary(tmp_path):
    path = tmp_path / "task_plan.json"
    raw = deepcopy(DEFAULT_TASK_PLAN)
    raw["schedule"].update(morning_time="17:59", random_delay_minutes=1)
    _write_plan(path, raw)
    with pytest.raises(TaskPlanError, match="before 18:00"):
        load_task_plan(path)


def _account(**overrides):
    values = dict(
        alliedteam_battle_enable=True, alliedteam_ap_enable=True,
        mail_enable=True, donatejade_enable=True, courtyard_enable=True,
        cooperation_enable=True, returngift_enable=True, weekaward_enable=True,
        mysteryshop_enable=True, kekkaiActivation_enable=True, KekkaiUtilize_enable=True,
        tree_planting_enable=2, trialbattle_enable=True, summon_up_enable=True,
        publish_sr_enable=True, isflower=0, alliedteam_limit_count=30,
        alliedteam_invite_count=2,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _base(**overrides):
    values = dict(
        total_alliedteam_battle_enable=False, total_alliedteam_ap_enable=True,
        total_mail_enable=True, total_donatejade_enable=True, total_courtyard_enable=True,
        total_cooperation_enable=True, total_returngift_enable=False,
        total_weekaward_enable=False, total_mysteryshop_enable=False,
        total_kekkaiActivation_enable=True, total_KekkaiUtilize_enable=True,
        total_tree_planting_enable=0, total_trialbattle_enable=False,
        total_summon_up_enable=False, total_publish_sr_enable=False,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.unit
@pytest.mark.parametrize("phase", ["morning", "afternoon", None])
def test_runtime_ignores_plan_phase_total_only(phase, tmp_path):
    """运行时过滤已拆除：只看 total AND account，plan 阶段勾选不参与。

    即使 plan 里全部 False（或 phase 为 None），total=True+account=True 就执行
    ——plan 的意志已由排程物化进 total_* 落盘表达。
    """
    from tasks.MultiDailyAltAcc.script_task import ScriptTask

    raw = deepcopy(DEFAULT_TASK_PLAN)
    for p in ("morning", "afternoon"):
        raw[p] = {key: False for key in TASK_KEYS}
    path = tmp_path / "task_plan.json"
    _write_plan(path, raw)
    task = object.__new__(ScriptTask)
    task.daily_conf = SimpleNamespace(multi_daily_alt_acc_config=_base())
    task._task_plan = load_task_plan(path)
    task._normal_plan_phase = phase
    config = task._create_account_config(_account())
    assert config.courtyard_enable is True
    assert config.mail_enable is True
    assert config.donatejade_enable is True
    assert config.alliedteam_ap_enable is True
    assert config.kekkaiActivation_enable is True
    assert config.KekkaiUtilize_enable is True


@pytest.mark.unit
def test_account_and_total_switches_gate_runtime(tmp_path):
    """运行时 total AND account 两层仍然有效：任一为 False 即不执行。"""
    from tasks.MultiDailyAltAcc.script_task import ScriptTask

    path = tmp_path / "task_plan.json"
    _write_plan(path, DEFAULT_TASK_PLAN)
    task = object.__new__(ScriptTask)
    task.daily_conf = SimpleNamespace(multi_daily_alt_acc_config=_base(total_mail_enable=False))
    task._task_plan = load_task_plan(path)
    task._normal_plan_phase = "morning"
    config = task._create_account_config(_account(courtyard_enable=False))
    assert config.mail_enable is False
    assert config.courtyard_enable is False


@pytest.mark.unit
def test_manual_total_override_survives_one_round(tmp_path):
    """用户在轮次间隙手动开 total_*（如捐勾）时照常执行一轮——
    与试炼战斗等一次性任务同款行为；下次排程物化重新接管。"""
    from tasks.MultiDailyAltAcc.script_task import ScriptTask

    raw = deepcopy(DEFAULT_TASK_PLAN)
    for p in ("morning", "afternoon"):
        raw[p]["donatejade"] = False
    path = tmp_path / "task_plan.json"
    _write_plan(path, raw)
    task = object.__new__(ScriptTask)
    task.daily_conf = SimpleNamespace(
        multi_daily_alt_acc_config=_base(total_donatejade_enable=True))
    task._task_plan = load_task_plan(path)
    task._normal_plan_phase = "morning"
    config = task._create_account_config(_account())
    assert config.donatejade_enable is True


@pytest.mark.unit
def test_special_schedule_paths_never_call_plan_random(monkeypatch):
    """00:20、回礼后/失败后 3 分钟均不能触发 task_plan 的随机抽取。"""
    from tasks.MultiDailyAltAcc.script_task import ScriptTask

    task = object.__new__(ScriptTask)
    task._schedule_plan_phase = lambda *args: (_ for _ in ()).throw(AssertionError("plan schedule used"))
    task.daily_conf = SimpleNamespace(multi_daily_alt_acc_config=_base())
    task.config = SimpleNamespace(
        model=SimpleNamespace(multi_daily_alt_acc=task.daily_conf),
        save=lambda: None,
    )
    task.start_time = datetime(2026, 8, 25, 18, 5)
    captured = []
    task.set_next_run = lambda *args, **kwargs: captured.append(kwargs["target"])

    task._schedule_evening(task.start_time)
    assert captured[-1].hour == 0 and captured[-1].minute == 20

    task._schedule_alliedteam_after_returngift()
    assert 179 <= (captured[-1] - datetime.now()).total_seconds() <= 180

    task.next_run("MultiDailyAltAcc", success=False)
    assert len(captured) == 3
    assert 179 <= (captured[-1] - datetime.now()).total_seconds() <= 180


class _ReloadingConfig:
    """模拟 task_delay 先从磁盘重载，再保存 scheduler 的真实行为。"""

    def __init__(self, phase):
        self.disk = SimpleNamespace(
            multi_daily_alt_acc=SimpleNamespace(
                multi_daily_alt_acc_config=deepcopy(phase),
                scheduler=SimpleNamespace(next_run=None),
            )
        )
        self.model = deepcopy(self.disk)
        self.task_delay_calls = []
        self.save_calls = 0

    def task_delay(self, task, **kwargs):
        self.task_delay_calls.append(kwargs["target"])
        self.model = deepcopy(self.disk)
        self.model.multi_daily_alt_acc.scheduler.next_run = kwargs["target"]
        if kwargs.get("persist", True):
            self.save()

    def save(self):
        self.save_calls += 1
        self.disk = deepcopy(self.model)


def _make_multi_task():
    from tasks.MultiDailyAltAcc.script_task import ScriptTask
    from tasks.MultiDailyAltAcc.task_plan import parse_task_plan

    task = object.__new__(ScriptTask)
    task._task_plan = parse_task_plan(DEFAULT_TASK_PLAN)
    return task


@pytest.mark.unit
def test_morning_courtyard_base_enabled_after_midnight():
    """早晨庭院由 plan 物化决定：默认 plan.morning.courtyard=False，排程落盘即 False。"""
    task = _make_multi_task()
    config = _ReloadingConfig(_base(
        total_alliedteam_battle_enable=True,
        total_courtyard_enable=False,
    ))
    task.config = config
    task.daily_conf = config.model.multi_daily_alt_acc
    task.start_time = datetime(2026, 8, 17, 0, 23)
    task._schedule_after_midnight(task.start_time)
    saved = config.disk.multi_daily_alt_acc.multi_daily_alt_acc_config
    assert saved.total_courtyard_enable is False
    assert saved.total_mail_enable is True
    assert saved.total_alliedteam_ap_enable is True


@pytest.mark.unit
def test_afternoon_courtyard_base_enabled():
    """下午庭院由 plan 物化决定：默认 plan.afternoon.courtyard=True，排程落盘即 True。"""
    task = _make_multi_task()
    config = _ReloadingConfig(_base(total_courtyard_enable=False))
    task.config = config
    task.daily_conf = config.model.multi_daily_alt_acc
    task.start_time = datetime(2026, 8, 17, 6, 5)
    task._schedule_normal_day(task.start_time)
    saved = config.disk.multi_daily_alt_acc.multi_daily_alt_acc_config
    assert saved.total_courtyard_enable is True
    assert saved.total_alliedteam_ap_enable is False


@pytest.mark.unit
def test_morning_and_afternoon_are_distinct_task_plan_phases():
    from tasks.MultiDailyAltAcc.progress import phase_flags_of

    morning = _base(total_courtyard_enable=True, total_mail_enable=True,
                    total_cooperation_enable=True)
    afternoon = _base(total_courtyard_enable=True, total_mail_enable=True,
                      total_cooperation_enable=True)
    assert phase_flags_of(morning, "morning") != phase_flags_of(afternoon, "afternoon")


@pytest.mark.unit
def test_daily_alt_acc_does_not_import_multi_daily_task_plan():
    from pathlib import Path

    source = Path("tasks/DailyAltAcc/script_task.py").read_text(encoding="utf-8")
    assert "MultiDailyAltAcc.task_plan" not in source


# ---------------------------------------------------------------------------
# 单用途轮运行前过滤 & 回礼轮 plan 控制勾协/商店
# ---------------------------------------------------------------------------

def _filter_task(**total_overrides):
    """构造可直接调 _apply_single_purpose_filter 的 task。"""
    task = _make_multi_task()
    task.config = SimpleNamespace(
        model=SimpleNamespace(multi_daily_alt_acc=SimpleNamespace(
            multi_daily_alt_acc_config=_base(**total_overrides)))
    )
    task.daily_conf = task.config.model.multi_daily_alt_acc
    return task


@pytest.mark.unit
def test_returngift_round_masks_manual_switches():
    """回礼轮：手动开的其他任务全屏蔽，只留回礼。"""
    task = _filter_task(total_returngift_enable=True, total_donatejade_enable=True,
                        total_courtyard_enable=True, total_kekkaiActivation_enable=True)
    task._apply_single_purpose_filter(task.daily_conf.multi_daily_alt_acc_config)
    cfg = task.daily_conf.multi_daily_alt_acc_config
    assert cfg.total_returngift_enable is True
    assert cfg.total_donatejade_enable is False
    assert cfg.total_courtyard_enable is False
    assert cfg.total_kekkaiActivation_enable is False


@pytest.mark.unit
def test_returngift_round_allows_plan_coop_and_mshop():
    """回礼轮：勾协/商店均为排程决策类（照单执行，total=True 就保留，
    过滤不做二次判定——晚轮排程已按 plan.returngift 写好开关）。"""
    task = _filter_task(total_returngift_enable=True,
                        total_cooperation_enable=True, total_mysteryshop_enable=True)
    task._apply_single_purpose_filter(task.daily_conf.multi_daily_alt_acc_config)
    cfg = task.daily_conf.multi_daily_alt_acc_config
    assert cfg.total_cooperation_enable is True   # 排程决策结果照单执行
    assert cfg.total_mysteryshop_enable is True   # 排程决策结果照单执行


@pytest.mark.unit
def test_evening_schedules_coop_for_returngift_round():
    """晚轮排回礼轮时做勾协决策：默认 returngift.cooperation=False → 不带；
    勾选 → 回礼轮带勾协。"""
    # 默认 plan：returngift.cooperation=False → 不带
    task = _make_multi_task()
    config = _ReloadingConfig(_base())
    task.config = config
    task.daily_conf = config.model.multi_daily_alt_acc
    task.start_time = datetime(2026, 9, 11, 18, 5)  # 周五晚
    task._schedule_evening(task.start_time)
    saved = config.disk.multi_daily_alt_acc.multi_daily_alt_acc_config
    assert saved.total_returngift_enable is True
    assert saved.total_cooperation_enable is False  # 默认不勾

    # 自定义 plan：returngift.cooperation=True → 带勾协
    raw = deepcopy(DEFAULT_TASK_PLAN)
    raw["returngift"]["cooperation"] = True
    task2 = _make_multi_task()
    task2._task_plan = parse_task_plan(raw)
    config2 = _ReloadingConfig(_base())
    task2.config = config2
    task2.daily_conf = config2.model.multi_daily_alt_acc
    task2.start_time = datetime(2026, 9, 11, 18, 5)
    task2._schedule_evening(task2.start_time)
    saved2 = config2.disk.multi_daily_alt_acc.multi_daily_alt_acc_config
    assert saved2.total_cooperation_enable is True


def _write_plan_returned(tmp_path, raw):
    path = tmp_path / "task_plan.json"
    _write_plan(path, raw)
    return path


@pytest.mark.unit
def test_alliedteam_round_masks_everything_except_battle():
    """同心战斗轮：只留同心战斗，连 AP 也屏蔽。"""
    task = _filter_task(total_alliedteam_battle_enable=True, total_alliedteam_ap_enable=True,
                        total_mail_enable=True, total_trialbattle_enable=True,
                        total_tree_planting_enable=2)
    task._apply_single_purpose_filter(task.daily_conf.multi_daily_alt_acc_config)
    cfg = task.daily_conf.multi_daily_alt_acc_config
    assert cfg.total_alliedteam_battle_enable is True
    assert cfg.total_alliedteam_ap_enable is False
    assert cfg.total_mail_enable is False
    assert cfg.total_trialbattle_enable is False
    assert cfg.total_tree_planting_enable == 0  # 三值开关屏蔽成 0


@pytest.mark.unit
def test_normal_round_not_filtered():
    """普通轮：排程物化已决定内容，运行前过滤不动作。"""
    task = _filter_task(total_mail_enable=True, total_donatejade_enable=True)
    task._apply_single_purpose_filter(task.daily_conf.multi_daily_alt_acc_config)
    cfg = task.daily_conf.multi_daily_alt_acc_config
    assert cfg.total_mail_enable is True
    assert cfg.total_donatejade_enable is True


@pytest.mark.unit
def test_legacy_plan_without_returngift_phase_uses_default():
    """旧版 task_plan.json（无 returngift 段）解析不报错，回礼段用默认值。"""
    raw = deepcopy(DEFAULT_TASK_PLAN)
    del raw["returngift"]
    plan = parse_task_plan(raw)
    assert plan.returngift == {"cooperation": False, "mysteryshop": False}


# ---------------------------------------------------------------------------
# 周奖励/神秘商店星期计划（schedule.weekaward_weekdays / mysteryshop_weekdays）
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_weekdays_parsed_from_plan_and_defaults():
    """显式列表解析成功；缺省字段用默认值（周一/周三周六）。"""
    raw = deepcopy(DEFAULT_TASK_PLAN)
    raw["schedule"]["weekaward_weekdays"] = [0, 3]
    plan = parse_task_plan(raw)
    assert plan.weekaward_weekdays == (0, 3)
    assert plan.mysteryshop_weekdays == (2, 5)  # 缺省默认

    # 旧版文件（无星期字段）按默认解析
    legacy = deepcopy(DEFAULT_TASK_PLAN)
    del legacy["schedule"]["weekaward_weekdays"]
    del legacy["schedule"]["mysteryshop_weekdays"]
    plan2 = parse_task_plan(legacy)
    assert plan2.weekaward_weekdays == (0,)
    assert plan2.mysteryshop_weekdays == (2, 5)


@pytest.mark.unit
@pytest.mark.parametrize("bad", [
    [7],           # 越界
    [-1],          # 负数
    ["mon"],       # 非整数
    3,             # 不是列表
])
def test_weekdays_invalid_values_rejected(bad):
    """星期列表非法值必须明确报错，不静默回落。"""
    raw = deepcopy(DEFAULT_TASK_PLAN)
    raw["schedule"]["mysteryshop_weekdays"] = bad
    with pytest.raises(TaskPlanError):
        parse_task_plan(raw)


@pytest.mark.unit
def test_after_midnight_opens_weekaward_by_plan_weekday():
    """星期规则从 plan 消费：周二（weekday=1）不在默认表 → 两开关都不开。"""
    task = _make_multi_task()
    config = _ReloadingConfig(_base(total_alliedteam_battle_enable=True))
    task.config = config
    task.daily_conf = config.model.multi_daily_alt_acc
    task.start_time = datetime(2026, 9, 8, 0, 23)  # 周二
    task._schedule_after_midnight(task.start_time)
    saved = config.disk.multi_daily_alt_acc.multi_daily_alt_acc_config
    assert saved.total_weekaward_enable is False
    assert saved.total_mysteryshop_enable is False


@pytest.mark.unit
def test_after_midnight_opens_weekaward_on_configured_day():
    """plan 配置周一领周奖励 → 周一（weekday=0）排程落盘开启。"""
    task = _make_multi_task()
    config = _ReloadingConfig(_base(total_alliedteam_battle_enable=True))
    task.config = config
    task.daily_conf = config.model.multi_daily_alt_acc
    task.start_time = datetime(2026, 9, 7, 0, 23)  # 周一
    task._schedule_after_midnight(task.start_time)
    saved = config.disk.multi_daily_alt_acc.multi_daily_alt_acc_config
    assert saved.total_weekaward_enable is True
    assert saved.total_mysteryshop_enable is False  # 周一不在 [2,5]


@pytest.mark.unit
def test_after_midnight_opens_mysteryshop_by_plan_weekday():
    """早轮物化带星期门控（执行日语义）：周六（weekday=5）∈ [2,5] →
    默认表 morning.mysteryshop=True → 带商店；改为 False 时星期命中也不带。"""
    # 默认 plan（morning.mysteryshop=True）→ 周六早轮带商店
    task = _make_multi_task()
    config = _ReloadingConfig(_base(total_alliedteam_battle_enable=True))
    task.config = config
    task.daily_conf = config.model.multi_daily_alt_acc
    task.start_time = datetime(2026, 9, 12, 0, 23)  # 周六
    task._schedule_after_midnight(task.start_time)
    saved = config.disk.multi_daily_alt_acc.multi_daily_alt_acc_config
    assert saved.total_mysteryshop_enable is True
    assert saved.total_weekaward_enable is False  # 周六不在 weekaward_days

    # 自定义 plan：morning.mysteryshop=False → 周六早轮也不带
    raw = deepcopy(DEFAULT_TASK_PLAN)
    raw["morning"]["mysteryshop"] = False
    task2 = _make_multi_task()
    task2._task_plan = parse_task_plan(raw)
    config2 = _ReloadingConfig(_base(total_alliedteam_battle_enable=True))
    task2.config = config2
    task2.daily_conf = config2.model.multi_daily_alt_acc
    task2.start_time = datetime(2026, 9, 12, 0, 23)
    task2._schedule_after_midnight(task2.start_time)
    saved2 = config2.disk.multi_daily_alt_acc.multi_daily_alt_acc_config
    assert saved2.total_mysteryshop_enable is False


@pytest.mark.unit
def test_evening_schedules_mysteryshop_for_returngift_round():
    """晚轮排回礼轮时按执行日判次日：默认 returngift.mysteryshop=False → 不带；
    勾选且次日周六∈[2,5] → 带；周四晚（次日周五∉[2,5]）→ 不带。"""
    # 默认 plan：returngift.mysteryshop=False → 周五晚排的回礼轮不带商店
    task = _make_multi_task()
    config = _ReloadingConfig(_base())
    task.config = config
    task.daily_conf = config.model.multi_daily_alt_acc
    task.start_time = datetime(2026, 9, 11, 18, 5)  # 周五晚
    task._schedule_evening(task.start_time)
    saved = config.disk.multi_daily_alt_acc.multi_daily_alt_acc_config
    assert saved.total_returngift_enable is True
    assert saved.total_mysteryshop_enable is False  # 默认不勾

    # 自定义 plan：returngift.mysteryshop=True → 次日周六∈[2,5] 带商店
    raw = deepcopy(DEFAULT_TASK_PLAN)
    raw["returngift"]["mysteryshop"] = True
    task2 = _make_multi_task()
    task2._task_plan = parse_task_plan(raw)
    config2 = _ReloadingConfig(_base())
    task2.config = config2
    task2.daily_conf = config2.model.multi_daily_alt_acc
    task2.start_time = datetime(2026, 9, 11, 18, 5)  # 周五晚
    task2._schedule_evening(task2.start_time)
    saved2 = config2.disk.multi_daily_alt_acc.multi_daily_alt_acc_config
    assert saved2.total_mysteryshop_enable is True   # 次日周六是执行日

    # 自定义 plan + 周四晚：次日周五 ∉ [2,5] → 不带
    task3 = _make_multi_task()
    task3._task_plan = parse_task_plan(raw)
    config3 = _ReloadingConfig(_base())
    task3.config = config3
    task3.daily_conf = config3.model.multi_daily_alt_acc
    task3.start_time = datetime(2026, 9, 10, 18, 5)  # 周四晚
    task3._schedule_evening(task3.start_time)
    saved3 = config3.disk.multi_daily_alt_acc.multi_daily_alt_acc_config
    assert saved3.total_mysteryshop_enable is False


@pytest.mark.unit
def test_evening_respects_returngift_mshop_disabled():
    """plan.returngift.mysteryshop=False（不想回礼轮翻商店）时，即使次日执行日命中也不带。"""
    raw = deepcopy(DEFAULT_TASK_PLAN)
    raw["returngift"]["mysteryshop"] = False
    task = _make_multi_task()
    task._task_plan = parse_task_plan(raw)
    config = _ReloadingConfig(_base())
    task.config = config
    task.daily_conf = config.model.multi_daily_alt_acc
    task.start_time = datetime(2026, 9, 11, 18, 5)  # 周五晚，次日周六是执行日
    task._schedule_evening(task.start_time)
    saved = config.disk.multi_daily_alt_acc.multi_daily_alt_acc_config
    assert saved.total_mysteryshop_enable is False


# ---------------------------------------------------------------------------
# 单用途阶段开关（single_purpose）：同心/回礼的运行时过滤与空跑
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_single_purpose_plan_gates_runtime_not_total(tmp_path):
    """single_purpose 段在运行时过滤：total 不变（轮次身份判定不动），
    plan 关闭 → 账号配置里该任务 False（整轮空跑，轮转照常）。"""
    from tasks.MultiDailyAltAcc.script_task import ScriptTask

    # plan 关闭回礼阶段
    raw = deepcopy(DEFAULT_TASK_PLAN)
    raw["single_purpose"] = {"returngift": False, "alliedteam_battle": True}
    task = object.__new__(ScriptTask)
    task.daily_conf = SimpleNamespace(multi_daily_alt_acc_config=_base(total_returngift_enable=True))
    task._task_plan = parse_task_plan(raw)
    config = task._create_account_config(_account())
    # total 仍 True（轮次身份判定依赖它），但账号配置里被 plan 关掉 → 全账号 skip
    assert task.daily_conf.multi_daily_alt_acc_config.total_returngift_enable is True
    assert config.returngift_enable is False

    # plan 关闭同心战斗阶段
    raw2 = deepcopy(DEFAULT_TASK_PLAN)
    raw2["single_purpose"] = {"returngift": True, "alliedteam_battle": False}
    task2 = object.__new__(ScriptTask)
    task2.daily_conf = SimpleNamespace(multi_daily_alt_acc_config=_base(total_alliedteam_battle_enable=True))
    task2._task_plan = parse_task_plan(raw2)
    config2 = task2._create_account_config(_account())
    assert task2.daily_conf.multi_daily_alt_acc_config.total_alliedteam_battle_enable is True
    assert config2.alliedteam_battle_enable is False


@pytest.mark.unit
def test_single_purpose_default_keeps_stages_on():
    """默认 single_purpose 全 true：行为与无该段的旧文件一致。"""
    plan = parse_task_plan(DEFAULT_TASK_PLAN)
    assert plan.enabled("single_purpose", "returngift") is True
    assert plan.enabled("single_purpose", "alliedteam_battle") is True


@pytest.mark.unit
def test_legacy_plan_without_single_purpose_uses_default():
    """旧版文件（无 single_purpose 段）解析不报错，按默认全 true 兜底。"""
    raw = deepcopy(DEFAULT_TASK_PLAN)
    del raw["single_purpose"]
    plan = parse_task_plan(raw)
    assert plan.single_purpose == {"returngift": True, "alliedteam_battle": True}
