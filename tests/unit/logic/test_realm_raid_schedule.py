# This Python file uses the following encoding: utf-8
import ast
from pathlib import Path


def test_realm_raid_does_not_schedule_orochi():
    """RealmRaid 只能安排自身下次运行，Orochi 时间由组队状态唯一控制。"""
    source = Path('tasks/RealmRaid/script_task.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    scheduled_tasks = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != 'set_next_run':
            continue
        task_value = None
        if node.args and isinstance(node.args[0], ast.Constant):
            task_value = node.args[0].value
        for keyword in node.keywords:
            if keyword.arg == 'task' and isinstance(keyword.value, ast.Constant):
                task_value = keyword.value.value
        scheduled_tasks.append(task_value)

    assert 'Orochi' not in scheduled_tasks
    assert 'RealmRaid' in scheduled_tasks

