# This Python file uses the following encoding: utf-8
# 分级热重载策略：COLD prefix allowlist > HOT exact-path allowlist > 其余全部 WARM（default-deny）。
# 生产首版 HOT_RELOAD_PATHS 为空，只有测试专用合成 Schema 注入 ReloadPolicy 时才会命中 HOT。
from dataclasses import dataclass, field
from typing import Sequence

HOT = "hot"
WARM = "warm"
COLD = "cold"


@dataclass(frozen=True)
class ReloadPolicy:
    """把 canonical tuple path 分类为 HOT/WARM/COLD。

    分类优先级固定为 COLD prefix allowlist > HOT exact-path allowlist > WARM；
    新字段默认 WARM，禁止通过“非 scheduler 即 HOT”等规则推导。

    HOT 契约（规格 §11.1）：生产首版 hot_paths 为空，只有测试专用合成 Schema
    注入 ReloadPolicy 时才会命中 HOT；未来生产 HOT 仅支持任务显式声明的
    scalar/Enum/time 单值字段，不支持 list/dict/动态 serializer 子树/结构字段。
    """
    hot_paths: frozenset = field(default_factory=frozenset)
    cold_prefixes: Sequence[tuple[str, ...]] = (("script", "device"),)

    def classify(self, path: tuple[str, ...]) -> str:
        for prefix in self.cold_prefixes:
            if path[:len(prefix)] == prefix:
                return COLD
        if path in self.hot_paths:
            return HOT
        return WARM


# 生产默认策略：script.device 子树 COLD、无 HOT、其余 WARM。
DEFAULT_RELOAD_POLICY = ReloadPolicy()


def coerce_path(p) -> tuple:
    """防御式把事件路径转成 tuple；str 视为单段路径，避免被 tuple() 拆成字符。"""
    if isinstance(p, (tuple, list)):
        return tuple(p)
    return (p,)
