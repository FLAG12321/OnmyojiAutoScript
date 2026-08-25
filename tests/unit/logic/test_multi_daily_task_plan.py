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
@pytest.mark.parametrize("phase", ["morning", "afternoon"])
def test_all_seven_task_keys_gate_only_normal_phase(phase, tmp_path):
    from tasks.MultiDailyAltAcc.script_task import ScriptTask

    raw = deepcopy(DEFAULT_TASK_PLAN)
    raw[phase] = {key: False for key in TASK_KEYS}
    path = tmp_path / "task_plan.json"
    _write_plan(path, raw)
    task = object.__new__(ScriptTask)
    task.daily_conf = SimpleNamespace(multi_daily_alt_acc_config=_base())
    task._task_plan = load_task_plan(path)
    task._normal_plan_phase = phase
    config = task._create_account_config(_account())
    assert config.courtyard_enable is False
    assert config.mail_enable is False
    assert config.cooperation_enable is False
    assert config.donatejade_enable is False
    assert config.alliedteam_ap_enable is False
    assert config.kekkaiActivation_enable is False
    assert config.KekkaiUtilize_enable is False


@pytest.mark.unit
def test_account_and_total_switches_still_gate_plan_true(tmp_path):
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
def test_special_phase_does_not_apply_donate_or_kekkai_plan(tmp_path):
    from tasks.MultiDailyAltAcc.script_task import ScriptTask

    raw = deepcopy(DEFAULT_TASK_PLAN)
    raw["morning"]["donatejade"] = False
    raw["morning"]["kekkaiActivation"] = False
    path = tmp_path / "task_plan.json"
    _write_plan(path, raw)
    task = object.__new__(ScriptTask)
    task.daily_conf = SimpleNamespace(multi_daily_alt_acc_config=_base(total_returngift_enable=True))
    task._task_plan = load_task_plan(path)
    task._normal_plan_phase = None
    config = task._create_account_config(_account())
    assert config.donatejade_enable is True
    assert config.kekkaiActivation_enable is True


@pytest.mark.unit
def test_default_ap_and_independent_kekkai_stage_switches(tmp_path):
    from tasks.MultiDailyAltAcc.script_task import ScriptTask

    raw = deepcopy(DEFAULT_TASK_PLAN)
    raw["morning"]["kekkaiActivation"] = False
    raw["morning"]["KekkaiUtilize"] = True
    path = tmp_path / "task_plan.json"
    _write_plan(path, raw)
    task = object.__new__(ScriptTask)
    task.daily_conf = SimpleNamespace(multi_daily_alt_acc_config=_base())
    task._task_plan = load_task_plan(path)

    task._normal_plan_phase = "morning"
    morning = task._create_account_config(_account())
    assert morning.alliedteam_ap_enable is True
    assert morning.kekkaiActivation_enable is False
    assert morning.KekkaiUtilize_enable is True

    task._normal_plan_phase = "afternoon"
    afternoon = task._create_account_config(_account())
    assert afternoon.alliedteam_ap_enable is False


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
    """早晨庭院由 task_plan 的阶段开关决定，调度层保留总开关基线。"""
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
    assert saved.total_courtyard_enable is True


@pytest.mark.unit
def test_afternoon_courtyard_base_enabled():
    """下午庭院同样由 task_plan 决定，调度层保留总开关基线。"""
    task = _make_multi_task()
    config = _ReloadingConfig(_base(total_courtyard_enable=False))
    task.config = config
    task.daily_conf = config.model.multi_daily_alt_acc
    task.start_time = datetime(2026, 8, 17, 6, 5)
    task._schedule_normal_day(task.start_time)
    saved = config.disk.multi_daily_alt_acc.multi_daily_alt_acc_config
    assert saved.total_courtyard_enable is True


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
