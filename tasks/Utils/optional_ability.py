# This Python file uses the following encoding: utf-8
from pathlib import Path


def run_friend_interact(task) -> bool:
    module_path = Path.cwd() / 'tasks' / 'FriendInteract' / 'friend_interact.py'
    if not module_path.exists():
        return False
    from tasks.FriendInteract.friend_interact import FriendInteract
    FriendInteract(task.config, task.device).run()
    return True
