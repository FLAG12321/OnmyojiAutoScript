# This Python file uses the following encoding: utf-8
"""derive_hot_paths 的收录与排除规则测试。

HOT 白名单不再是手写常量，而是从 ConfigModel schema 派生：所有 scalar/Enum/单值时间
叶子字段均可中途生效。本文件同时用合成模型验证遍历规则、用真实 ConfigModel 做端到端抽查，
避免只靠合成模型通过而真实 schema 出现意外收录/漏收。
"""
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Optional

import pytest
from pydantic import BaseModel, Field

from module.config.config_reload import (
    COLD,
    HOT,
    HOT_DENY_PATHS,
    WARM,
    derive_hot_paths,
    default_reload_policy,
)


class _Color(str, Enum):
    RED = 'red'
    BLUE = 'blue'


class _Nested(BaseModel):
    depth_scalar: int = 1


class _Scheduler(BaseModel):
    """字段名 scheduler 的子模型：整棵子树不得进入白名单。"""
    enable: bool = True
    next_run: datetime = datetime(2026, 1, 1)


class _DynamicGroup(BaseModel, extra='allow'):
    """extra='allow'：运行期可出现未声明字段，整棵子树不可枚举成 exact path。"""
    declared: int = 0


class _Device(BaseModel):
    serial: str = 'emulator'


class _Script(BaseModel):
    device: _Device = Field(default_factory=_Device)
    other_scalar: int = 3


class _Leaves(BaseModel):
    an_int: int = 1
    a_float: float = 1.5
    a_str: str = 'x'
    a_bool: bool = True
    an_enum: _Color = _Color.RED
    a_time: time = time(1, 2, 3)
    a_date: date = date(2026, 1, 1)
    a_datetime: datetime = datetime(2026, 1, 1)
    a_timedelta: timedelta = timedelta(minutes=5)
    a_list: list[int] = Field(default_factory=list)
    a_dict: dict[str, int] = Field(default_factory=dict)
    an_optional_model: Optional[_Nested] = None


class _SyntheticModel(BaseModel):
    leaves: _Leaves = Field(default_factory=_Leaves)
    nested: _Nested = Field(default_factory=_Nested)
    scheduler: _Scheduler = Field(default_factory=_Scheduler)
    dynamic: _DynamicGroup = Field(default_factory=_DynamicGroup)
    script: _Script = Field(default_factory=_Script)
    top_scalar: int = 7


@pytest.fixture(scope='module')
def synthetic_paths() -> frozenset:
    return derive_hot_paths(_SyntheticModel)


@pytest.mark.unit
@pytest.mark.parametrize('leaf', (
    'an_int', 'a_float', 'a_str', 'a_bool',
    'an_enum', 'a_time', 'a_date', 'a_datetime', 'a_timedelta',
))
def test_single_value_leaves_are_collected(synthetic_paths, leaf):
    """int/float/str/bool、Enum 与全部单值时间类型均收录。"""
    assert ('leaves', leaf) in synthetic_paths


@pytest.mark.unit
def test_top_level_and_deep_scalars_collected(synthetic_paths):
    # 顶层标量与嵌套一层的标量都要收录，路径按 canonical tuple 展开
    assert ('top_scalar',) in synthetic_paths
    assert ('nested', 'depth_scalar') in synthetic_paths
    assert ('script', 'other_scalar') in synthetic_paths


@pytest.mark.unit
@pytest.mark.parametrize(('path', 'reason'), (
    (('leaves', 'a_list'), 'list 容器不是单值字段'),
    (('leaves', 'a_dict'), 'dict 容器不是单值字段'),
    (('leaves', 'an_optional_model'), 'Optional[BaseModel] 是结构性字段'),
    (('scheduler', 'enable'), 'scheduler 子树由调度器管理'),
    (('scheduler', 'next_run'), 'scheduler 子树由调度器管理'),
    (('dynamic', 'declared'), "extra='allow' 子树无法预先枚举"),
    (('script', 'device', 'serial'), 'script.device 是 COLD 子树'),
))
def test_structural_exclusions(synthetic_paths, path, reason):
    assert path not in synthetic_paths, reason


@pytest.mark.unit
def test_deny_paths_are_subtracted():
    """HOT_DENY_PATHS 是中央撤回钩子：命中的路径即使类型合格也不得收录。"""
    import module.config.config_reload as reload_mod

    original = reload_mod.HOT_DENY_PATHS
    try:
        reload_mod.HOT_DENY_PATHS = frozenset({('top_scalar',)})
        paths = derive_hot_paths(_SyntheticModel)
        assert ('top_scalar',) not in paths
        # 其余同级字段不受影响，证明扣除是精确的而非整棵子树
        assert ('nested', 'depth_scalar') in paths
    finally:
        reload_mod.HOT_DENY_PATHS = original


# ---------- 真实 ConfigModel 端到端抽查 ----------

@pytest.mark.unit
def test_real_config_model_collects_user_tunables():
    """真实 schema 里典型用户可调字段必须开放中途生效。"""
    policy = default_reload_policy()
    assert policy.classify(('orochi', 'orochi_config', 'limit_count')) == HOT
    assert policy.classify(('orochi', 'orochi_config', 'limit_time')) == HOT
    assert policy.classify(('orochi', 'orochi_config', 'layer')) == HOT
    assert policy.classify(('orochi', 'orochi_config', 'soul_buff_enable')) == HOT


@pytest.mark.unit
def test_real_config_model_excludes_structural_and_bookkeeping():
    """真实 schema 的结构性字段与运行期簿记字段不得开放。"""
    policy = default_reload_policy()
    # COLD 优先级最高
    assert policy.classify(('script', 'device', 'serial')) == COLD
    # 结构性排除
    assert policy.classify(('orochi', 'scheduler', 'next_run')) == WARM
    assert policy.classify(('multi_daily_alt_acc', 'sup_account_list')) == WARM
    assert policy.classify(('find_jade', 'invite_info_list_1', 'name')) == WARM
    # 运行期簿记字段由 manager/调度器写入，不是用户设置
    assert policy.classify(('running_task',)) == WARM
    assert policy.classify(('config_name',)) == WARM


@pytest.mark.unit
def test_deny_paths_cover_runtime_bookkeeping():
    """簿记字段的排除来自 HOT_DENY_PATHS，而非碰巧类型不合格。"""
    assert ('running_task',) in HOT_DENY_PATHS
    assert ('config_name',) in HOT_DENY_PATHS


@pytest.mark.unit
def test_policy_is_cached_and_nonempty():
    """派生结果按进程缓存一次，且确实开放了字段（防止规则写错退化成空集）。"""
    first = default_reload_policy()
    second = default_reload_policy()
    assert first is second
    assert len(first.hot_paths) > 100
