"""MultiDailyAltAcc 模块级 morning / afternoon 任务计划。"""
from __future__ import annotations

import json
import os
import random
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Mapping


TASK_PLAN_PATH = Path(__file__).with_name("task_plan.json")
TASK_KEYS = (
    "courtyard",
    "mail",
    "cooperation",
    "donatejade",
    "alliedteam_ap",
    "kekkaiActivation",
    "KekkaiUtilize",
)

DEFAULT_TASK_PLAN = {
    "schedule": {
        "morning_time": "06:05",
        "afternoon_time": "18:05",
        "random_delay_minutes": 30,
    },
    "morning": {
        "courtyard": False,
        "mail": True,
        "cooperation": True,
        "donatejade": True,
        "alliedteam_ap": True,
        "kekkaiActivation": True,
        "KekkaiUtilize": True,
    },
    "afternoon": {
        "courtyard": True,
        "mail": True,
        "cooperation": True,
        "donatejade": True,
        "alliedteam_ap": False,
        "kekkaiActivation": True,
        "KekkaiUtilize": True,
    },
}


class TaskPlanError(ValueError):
    """task_plan.json 不合法；调用方必须保留原文件并明确失败。"""


@dataclass(frozen=True)
class ScheduledTarget:
    base_time: datetime
    delay_minutes: int
    target: datetime


@dataclass(frozen=True)
class TaskPlan:
    morning_time: time
    afternoon_time: time
    random_delay_minutes: int
    morning: Mapping[str, bool]
    afternoon: Mapping[str, bool]

    def enabled(self, phase: str, task: str) -> bool:
        if task not in TASK_KEYS:
            raise KeyError(f"Unknown MultiDaily task-plan key: {task}")
        if phase == "morning":
            return self.morning[task]
        if phase == "afternoon":
            return self.afternoon[task]
        raise ValueError(f"Unknown MultiDaily task-plan phase: {phase}")

    def schedule_target(self, phase: str, start_time: datetime) -> ScheduledTarget:
        if phase == "morning":
            planned_time = self.morning_time
        elif phase == "afternoon":
            planned_time = self.afternoon_time
        else:
            raise ValueError(f"Unknown MultiDaily task-plan phase: {phase}")

        base_time = start_time.replace(
            hour=planned_time.hour,
            minute=planned_time.minute,
            second=0,
            microsecond=0,
        )
        delay_minutes = random.randint(0, self.random_delay_minutes)
        return ScheduledTarget(
            base_time=base_time,
            delay_minutes=delay_minutes,
            target=base_time + timedelta(minutes=delay_minutes),
        )


def _parse_time(value: object, field: str) -> time:
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value):
        raise TaskPlanError(f"{field} must be a HH:MM string")
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise TaskPlanError(f"{field} is not a valid 24-hour time: {value!r}") from exc


def _parse_phase(raw: object, phase: str) -> dict[str, bool]:
    if not isinstance(raw, dict):
        raise TaskPlanError(f"{phase} must be an object")
    actual = set(raw)
    expected = set(TASK_KEYS)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details = []
        if missing:
            details.append(f"missing={missing}")
        if unknown:
            details.append(f"unknown={unknown}")
        raise TaskPlanError(f"{phase} task keys invalid ({', '.join(details)})")
    if any(type(raw[key]) is not bool for key in TASK_KEYS):
        raise TaskPlanError(f"{phase} task values must all be boolean")
    return {key: raw[key] for key in TASK_KEYS}


def _validate_schedule(morning_time: time, afternoon_time: time, delay_minutes: int) -> None:
    morning_minutes = morning_time.hour * 60 + morning_time.minute
    afternoon_minutes = afternoon_time.hour * 60 + afternoon_time.minute
    if not 5 * 60 <= morning_minutes < 18 * 60:
        raise TaskPlanError("schedule.morning_time must stay between 05:00 and 17:59")
    if not 18 * 60 <= afternoon_minutes < 24 * 60:
        raise TaskPlanError("schedule.afternoon_time must stay between 18:00 and 23:59")
    if morning_minutes + delay_minutes >= 18 * 60:
        raise TaskPlanError("morning_time + random_delay_minutes must stay before 18:00")
    if afternoon_minutes + delay_minutes >= 24 * 60:
        raise TaskPlanError("afternoon_time + random_delay_minutes must stay before midnight")


def parse_task_plan(raw: object) -> TaskPlan:
    if not isinstance(raw, dict):
        raise TaskPlanError("task_plan root must be an object")
    expected = {"schedule", "morning", "afternoon"}
    if set(raw) != expected:
        raise TaskPlanError("task_plan must contain only schedule, morning, and afternoon")
    schedule = raw["schedule"]
    required_schedule = {"morning_time", "afternoon_time", "random_delay_minutes"}
    if not isinstance(schedule, dict) or set(schedule) != required_schedule:
        raise TaskPlanError("schedule must contain only morning_time, afternoon_time, and random_delay_minutes")
    morning_time = _parse_time(schedule["morning_time"], "schedule.morning_time")
    afternoon_time = _parse_time(schedule["afternoon_time"], "schedule.afternoon_time")
    delay_minutes = schedule["random_delay_minutes"]
    if type(delay_minutes) is not int or delay_minutes < 0:
        raise TaskPlanError("schedule.random_delay_minutes must be a non-negative integer")
    _validate_schedule(morning_time, afternoon_time, delay_minutes)
    return TaskPlan(
        morning_time=morning_time,
        afternoon_time=afternoon_time,
        random_delay_minutes=delay_minutes,
        morning=_parse_phase(raw["morning"], "morning"),
        afternoon=_parse_phase(raw["afternoon"], "afternoon"),
    )


def _write_default(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temp_path = Path(file.name)
            json.dump(DEFAULT_TASK_PLAN, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())

        # os.link() publishes the fully written temp file under the final name only
        # when it does not already exist. This is atomic on the same filesystem and
        # never replaces a user-created task_plan.json.
        os.link(temp_path, path)
    except FileExistsError:
        # 另一个 MultiDaily 进程已先发布完整文件；随后只读取它的内容。
        return
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                # The published task_plan.json remains valid; a later startup can
                # remove an antivirus-locked temporary file if necessary.
                pass


def load_task_plan(path: Path | None = None) -> TaskPlan:
    """首次缺失时生成默认文件；已有文件永远只读且严格校验。"""
    path = path or TASK_PLAN_PATH
    if not path.exists():
        _write_default(path)
    if not path.is_file():
        raise TaskPlanError(f"task_plan path is not a file: {path}")
    try:
        with path.open("r", encoding="utf-8") as file:
            raw = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise TaskPlanError(f"Cannot read task_plan {path}: {exc}") from exc
    return parse_task_plan(raw)
