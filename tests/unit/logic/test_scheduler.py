import pytest
from datetime import datetime, timedelta
from module.config.scheduler import TaskScheduler
from module.config.config import Function
from tasks.Script.config_optimization import ScheduleRule


def make_func(command: str, enable: bool = True,
              next_run: datetime | None = None,
              priority: int = 50) -> Function:
    """辅助函数：构造 Function 对象用于调度器测试。
    command 必须是 ConfigModel 中的 snake_case 字段名（如 orochi, tako, nian）。"""
    if next_run is None:
        next_run = datetime.now() + timedelta(minutes=10)
    data = {
        "scheduler": {
            "enable": enable,
            "next_run": next_run.strftime("%Y-%m-%d %H:%M:%S"),
            "priority": str(priority),
        }
    }
    return Function(key=command, data=data)


class TestFIFOScheduling:
    def test_sorts_by_next_run(self):
        now = datetime.now()
        tasks = [
            make_func("orochi", next_run=now + timedelta(minutes=30)),
            make_func("tako", next_run=now + timedelta(minutes=5)),
            make_func("nian", next_run=now + timedelta(minutes=15)),
        ]
        result = TaskScheduler.fifo(tasks)
        commands = [t.command for t in result]
        assert commands[0] == "Tako"
        assert commands[1] == "Nian"
        assert commands[2] == "Orochi"

    def test_restart_always_first(self):
        now = datetime.now()
        tasks = [
            make_func("orochi", next_run=now + timedelta(minutes=5)),
            make_func("restart", next_run=now + timedelta(minutes=60)),
            make_func("tako", next_run=now + timedelta(minutes=10)),
        ]
        result = TaskScheduler.fifo(tasks)
        assert result[0].command == "Restart"


class TestScheduleDispatch:
    def test_schedule_fifo_rule(self):
        now = datetime.now()
        tasks = [
            make_func("nian", next_run=now + timedelta(minutes=20)),
            make_func("tako", next_run=now + timedelta(minutes=5)),
        ]
        result = TaskScheduler.schedule(ScheduleRule.FIFO, tasks)
        assert len(result) == 2

    def test_schedule_priority_rule(self):
        now = datetime.now()
        tasks = [
            make_func("pets", priority=100, next_run=now),
            make_func("quiz", priority=10, next_run=now),
        ]
        result = TaskScheduler.schedule(ScheduleRule.PRIORITY, tasks)
        assert len(result) == 2
