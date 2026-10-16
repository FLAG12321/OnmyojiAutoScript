"""
Copy from https://github.com/LmeSzinc/AzurLaneAutoScript
"""
import multiprocessing
import threading
from multiprocessing.managers import SyncManager
from typing import TYPE_CHECKING, Callable, Generic, TypeVar

if TYPE_CHECKING:
    from module.config.config_updater import ConfigUpdater
    from module.server.config import DeployConfig

T = TypeVar("T")


class cached_class_property(Generic[T]):
    """
    Code from https://github.com/dssg/dickens
    Add typing support

    Descriptor decorator implementing a class-level, read-only
    property, which caches its results on the class(es) on which it
    operates.
    Inheritance is supported, insofar as the descriptor is never hidden
    by its cache; rather, it stores values under its access name with
    added underscores. For example, when wrapping getters named
    "choices", "choices_" or "_choices", each class's result is stored
    on the class at "_choices_"; decoration of a getter named
    "_choices_" would raise an exception.
    """

    class AliasConflict(ValueError):
        pass

    def __init__(self, func: Callable[..., T]):
        self.__func__ = func
        self.__cache_name__ = '_{}_'.format(func.__name__.strip('_'))
        if self.__cache_name__ == func.__name__:
            raise self.AliasConflict(self.__cache_name__)

    def __get__(self, instance, cls=None) -> T:
        if cls is None:
            cls = type(instance)

        try:
            return vars(cls)[self.__cache_name__]
        except KeyError:
            result = self.__func__(cls)
            setattr(cls, self.__cache_name__, result)
            return result


class State:
    """
    Shared settings
    """

    _init = False
    _clearup = False

    restart_event: threading.Event = None
    manager: SyncManager = None
    server = None  # 由 server.py 注入 uvicorn.Server 实例，供 kill_server 触发整个服务优雅退出

    @classmethod
    def init(cls):
        from pathlib import Path
        from module.config.runtime_registry import register_runtime_helper, runtime_start_gate
        # Manager 与脚本 worker 都使用 spawn；登记精确辅助身份避免关机误判。
        root = Path.cwd() / 'config'
        with runtime_start_gate(root):
            cls.manager = multiprocessing.Manager()
            try:
                register_runtime_helper(root, cls.manager._process.pid, 'state_manager')
            except BaseException:
                # 登记失败即回收本次辅助进程，不留下无法辨认的活跃 spawn。
                cls.manager.shutdown()
                raise
        cls._init = True

    @classmethod
    def clearup(cls):
        cls.manager.shutdown()
        cls._clearup = True

    @cached_class_property
    def deploy_config(self) -> "DeployConfig":
        """
        Returns:
            DeployConfig：
        """
        from module.server.config import DeployConfig

        return DeployConfig()

    @cached_class_property
    def config_updater(self) -> "ConfigUpdater":
        """
        Returns:
            ConfigUpdater：
        """
        from module.config.config_updater import ConfigUpdater

        return ConfigUpdater()
