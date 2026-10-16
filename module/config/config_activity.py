"""实例运行锁：删除临时文件前确认任务已退出，并阻止新进程同时进入。"""

from pathlib import Path

from filelock import FileLock

from module.config.config_generation import GenerationManager


def config_activity_lock(config_root, name: str) -> FileLock:
    """进程运行和外部清理共用非阻塞锁；操作系统会在进程退出时释放占用。

    锁覆盖整个任务周期，区别于只保护配置写入的 lifecycle 锁。调用方始终先取
    activity 锁，再进入 ConfigStore，避免清理与启动互相等待。
    """
    name = GenerationManager._validate_name(name)
    directory = Path(config_root) / '.generations' / 'running'
    directory.mkdir(parents=True, exist_ok=True)
    # 管家按大小写不敏感识别临时实例；运行锁使用同一名称规范。
    return FileLock(str(directory / f'{name.casefold()}.lock'), timeout=0)
