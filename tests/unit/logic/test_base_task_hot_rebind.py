# This Python file uses the following encoding: utf-8
"""BaseTask.prepare_config_reload 捕获标量重绑的三重限定测试。

HOT 提交原地改模型，捕获「子模型对象」的读法自动生效；但 run() 开头
`self.limit_count = <配置值>` 这类拷到实例属性的标量追不回来，由 BaseTask 的
基类级 prepare hook 按 _HOT_SCALAR_REBIND 规则表同步。

三重限定各自防的是不同的错，因此逐条独立测：
1. 任务子树限定 —— 防止改 A 任务配置影响正在跑的 B 任务
2. 出处校验 —— 防止覆盖业务运行期改写过的值（BondlingFairyland 的 //= 2）
3. 类型转换 —— limit_time 配置里是 Time、任务里是 timedelta
"""
from datetime import time, timedelta
from types import SimpleNamespace

import pytest

from tasks.base_task import BaseTask, _HOT_SCALAR_REBIND


def _make_task(task_name: str, model, candidate, **attrs):
    """构造只带 prepare hook 所需依赖的 BaseTask 替身，绕开设备/资源初始化。"""
    task = object.__new__(BaseTask)

    def model_get(m, path):
        node = m
        for key in path:
            node = getattr(node, key)
        return node

    task.config = SimpleNamespace(
        task=task_name,
        model=model,
        _model_get=staticmethod(model_get).__func__,
    )
    for name, value in attrs.items():
        setattr(task, name, value)
    return task, candidate


def _orochi_model(limit_count=30, limit_time=time(0, 30, 0)):
    return SimpleNamespace(
        orochi=SimpleNamespace(
            orochi_config=SimpleNamespace(limit_count=limit_count, limit_time=limit_time)
        )
    )


LIMIT_COUNT_PATH = ('orochi', 'orochi_config', 'limit_count')
LIMIT_TIME_PATH = ('orochi', 'orochi_config', 'limit_time')


@pytest.mark.unit
def test_rebinds_captured_limit_count():
    """基础通路：属性值等于旧模型值 → 认定为拷贝 → 重绑为新值。"""
    task, candidate = _make_task(
        'Orochi', _orochi_model(limit_count=30), _orochi_model(limit_count=50),
        limit_count=30,
    )
    assert task.prepare_config_reload(candidate, [LIMIT_COUNT_PATH]) == {'limit_count': 50}


@pytest.mark.unit
def test_limit_time_converted_to_timedelta():
    """配置里是 Time、任务里是 timedelta：比较与回写都必须按同一换算。"""
    task, candidate = _make_task(
        'Orochi',
        _orochi_model(limit_time=time(0, 30, 0)),
        _orochi_model(limit_time=time(1, 15, 0)),
        limit_time=timedelta(minutes=30),
    )
    prepared = task.prepare_config_reload(candidate, [LIMIT_TIME_PATH])
    # 返回值必须已是 timedelta，否则后续 `datetime.now() - start >= limit_time` 抛 TypeError
    assert prepared == {'limit_time': timedelta(hours=1, minutes=15)}
    assert isinstance(prepared['limit_time'], timedelta)


@pytest.mark.unit
def test_other_task_subtree_is_ignored():
    """任务子树限定：改 Orochi 的字段不得影响正在跑的 EvoZone。"""
    model = SimpleNamespace(
        orochi=SimpleNamespace(orochi_config=SimpleNamespace(limit_count=30)),
        evo_zone=SimpleNamespace(evo_zone_config=SimpleNamespace(limit_count=30)),
    )
    candidate = SimpleNamespace(
        orochi=SimpleNamespace(orochi_config=SimpleNamespace(limit_count=999)),
        evo_zone=SimpleNamespace(evo_zone_config=SimpleNamespace(limit_count=30)),
    )
    # 当前跑的是 EvoZone，两个任务的 limit_count 恰好同值（出处校验单独挡不住）
    task, _ = _make_task('EvoZone', model, candidate, limit_count=30)
    assert task.prepare_config_reload(candidate, [LIMIT_COUNT_PATH]) == {}


@pytest.mark.unit
def test_runtime_modified_value_is_not_overwritten():
    """出处校验：业务运行期改写过的值不得被覆盖回配置原值。

    对应 BondlingFairyland 的 `self.limit_count //= 2`（handoff 模式两人各刷一半）：
    折半后 15 != 配置的 30，说明它不是原样拷贝，重绑必须跳过。
    """
    task, candidate = _make_task(
        'Orochi', _orochi_model(limit_count=30), _orochi_model(limit_count=50),
        limit_count=15,
    )
    assert task.prepare_config_reload(candidate, [LIMIT_COUNT_PATH]) == {}


@pytest.mark.unit
def test_unmapped_leaf_is_ignored():
    """规则表外的字段不参与重绑：捕获标量只有 limit_count/limit_time 两族。"""
    model = SimpleNamespace(orochi=SimpleNamespace(orochi_config=SimpleNamespace(layer=1)))
    candidate = SimpleNamespace(orochi=SimpleNamespace(orochi_config=SimpleNamespace(layer=2)))
    task, _ = _make_task('Orochi', model, candidate, layer=1)
    assert task.prepare_config_reload(candidate, [('orochi', 'orochi_config', 'layer')]) == {}


@pytest.mark.unit
def test_no_running_task_returns_empty():
    """调度器尚未设置 config.task 时不猜测子树，直接放弃重绑。"""
    task, candidate = _make_task(
        None, _orochi_model(), _orochi_model(limit_count=50), limit_count=30,
    )
    assert task.prepare_config_reload(candidate, [LIMIT_COUNT_PATH]) == {}


@pytest.mark.unit
def test_hyakkiyakou_alias_maps_to_limit_count():
    """Hyakkiyakou 源字段名是 hya_limit_count，仍要落到 limit_count 属性。"""
    model = SimpleNamespace(
        hyakkiyakou=SimpleNamespace(hyakkiyakou_config=SimpleNamespace(hya_limit_count=10))
    )
    candidate = SimpleNamespace(
        hyakkiyakou=SimpleNamespace(hyakkiyakou_config=SimpleNamespace(hya_limit_count=20))
    )
    task, _ = _make_task('Hyakkiyakou', model, candidate, limit_count=10)
    path = ('hyakkiyakou', 'hyakkiyakou_config', 'hya_limit_count')
    assert task.prepare_config_reload(candidate, [path]) == {'limit_count': 20}


@pytest.mark.unit
def test_declared_fields_cover_rule_table_targets():
    """规则表的目标属性必须全部在 HOT_RELOAD_DERIVED_FIELDS 里声明。

    框架会整体拒绝返回未声明字段的 prepare 结果（规格 §11.1），
    漏声明会让重绑静默退化成「每次都转 WARM deferred」。
    """
    targets = {attr for attr, _ in _HOT_SCALAR_REBIND.values()}
    assert targets <= BaseTask.HOT_RELOAD_DERIVED_FIELDS
