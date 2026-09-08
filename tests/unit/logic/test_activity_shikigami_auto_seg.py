# -*- coding: utf-8 -*-
"""ActivityShikigami 自动段接线测试：remaining 口径（场次/门票双约束、五倍 fail-closed、
类型过滤）、门票缓存递减钩子、gbc 透传、run_general_battle 分派链。"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tasks.ActivityShikigami.config import GeneralBattleConfig as ClimbBattleConfig
from tasks.ActivityShikigami.script_task import ScriptTask


def _make_climb(climb='pass', enable=True, seg=2, total=4, limit=50, count=1,
                ticket=None, x5=False):
    """构造绕过 __init__ 的裸 ScriptTask，装配自动段口径所需最小依赖。

    conf 走实例 __dict__ 覆盖 cached_property；climb_type 由
    run_idx + run_sequence_v 驱动（只读 property，不能直接赋值）。
    """
    obj = object.__new__(ScriptTask)
    obj.run_idx = 0
    obj.conf = SimpleNamespace(
        general_battle=ClimbBattleConfig(auto_battle_enable=enable,
                                         auto_segment_count=seg,
                                         auto_total_count=total),
        general_climb=SimpleNamespace(run_sequence_v=[climb],
                                      **{f'{climb}_limit': limit}),
        switch_soul_config=SimpleNamespace(**{f'{climb}_group_team': '1,1'}),
    )
    obj.conf.validate_switch_preset = lambda: None
    obj.current_count = count
    obj._ticket_cache = {} if ticket is None else {climb: ticket}
    obj._x5_active = x5
    return obj


def _stub_battle_chain(monkeypatch, obj):
    """打桩通用战斗链，让 run_general_battle（含爬塔重写）可被驱动。"""
    monkeypatch.setattr(obj, 'battle_before', lambda *a, **k: True)
    monkeypatch.setattr(obj, 'is_in_battle', lambda shot=False: False)
    monkeypatch.setattr(obj, 'battle_wait', lambda *a, **k: True)


@pytest.mark.unit
def test_remaining_config_disabled():
    """配置未开：None（组件层静默跳过，双保险之一）。"""
    obj = _make_climb(enable=False)
    assert obj._auto_seg_remaining() is None


@pytest.mark.unit
@pytest.mark.parametrize('climb', ['ap20', 'pass_monopoly', 'season_boss'])
def test_remaining_type_not_supported(climb):
    """不接入的爬塔类型：None 静默跳过（ap20/大富翁/修行合训）。"""
    obj = _make_climb(climb=climb)
    assert obj._auto_seg_remaining() is None


@pytest.mark.unit
def test_remaining_x5_fail_closed(caplog):
    """五倍消耗期间 fail-closed：None + 告警只打一次（场次/门票口径错配）。"""
    obj = _make_climb(x5=True, ticket=30)
    with caplog.at_level('WARNING'):
        assert obj._auto_seg_remaining() is None
        assert obj._auto_seg_remaining() is None   # 第二次仍禁用
    warns = [r for r in caplog.records if '五倍消耗' in r.message]
    assert len(warns) == 1


@pytest.mark.unit
def test_remaining_limit_zero():
    """场次上限 0（该类型未启用）：None。"""
    obj = _make_climb(limit=0)
    assert obj._auto_seg_remaining() is None


@pytest.mark.unit
def test_remaining_min_of_limit_and_ticket():
    """双约束取小：门票缓存与场次上限谁小取谁。"""
    # 场次剩余 50-10=40 > 门票 30 → 取 30（门票约束生效）
    obj = _make_climb(limit=50, count=10, ticket=30)
    assert obj._auto_seg_remaining() == 30
    # 场次剩余 40 < 门票 45 → 取 40
    obj = _make_climb(limit=50, count=10, ticket=45)
    assert obj._auto_seg_remaining() == 40
    # 无门票缓存（首场前的防御分支）：只受场次约束
    obj = _make_climb(limit=50, count=10, ticket=None)
    assert obj._auto_seg_remaining() == 40


@pytest.mark.unit
def test_remaining_ticket_zero():
    """门票缓存为 0：remaining=0，规划要求 remaining >= M+1，不会开新段。"""
    obj = _make_climb(ticket=0)
    assert obj._auto_seg_remaining() == 0


@pytest.mark.unit
def test_count_hook_decrements_ticket():
    """段内每场递减门票缓存 1：段内 fire 由游戏点，主循环不再递减。"""
    obj = _make_climb(climb='ap', ticket=3)
    obj.auto_battle_count_hook()
    assert obj._ticket_cache['ap'] == 2
    # 边界：递减到 0 不为负
    obj._ticket_cache['ap'] = 0
    obj.auto_battle_count_hook()
    assert obj._ticket_cache['ap'] == 0


@pytest.mark.unit
def test_count_hook_ignores_uncached_type():
    """无缓存的类型（ap20 不走 check_tickets_enough，缓存天然为空）：
    钩子不炸也不写入缓存。"""
    obj = _make_climb(climb='ap20', ticket=None)
    obj.auto_battle_count_hook()
    assert 'ap20' not in obj._ticket_cache


@pytest.mark.unit
def test_gbc_transfers_auto_seg_fields():
    """get_general_battle_conf 构造的组件 gbc 透传自动段三字段
    （不透传则组件侧永远是默认关闭）。"""
    obj = _make_climb(enable=True, seg=3, total=5)
    gbc = obj.get_general_battle_conf()
    assert gbc.auto_battle_enable is True
    assert gbc.auto_segment_count == 3
    assert gbc.auto_total_count == 5


@pytest.mark.unit
def test_run_general_battle_passes_remaining(monkeypatch):
    """爬塔重写向 super 传 remaining（调用前口径，与序言计数时序对齐），
    经 auto_battle_plan 的 plan_calls 行为断言。"""
    obj = _make_climb(limit=50, count=1, ticket=30)
    obj.device = MagicMock()
    obj.interval_timer = {}
    _stub_battle_chain(monkeypatch, obj)
    plan_calls = []
    monkeypatch.setattr(obj, 'auto_battle_plan', lambda cfg, r: plan_calls.append(r))
    monkeypatch.setattr(obj, 'auto_battle_run', lambda: None)
    from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
    cfg = GeneralBattleConfig()
    obj.run_general_battle(config=cfg)
    obj.run_general_battle(config=cfg)
    # 第一次调用前 count=1 → min(50-1, 30)=30；序言 +1 后第二次 → min(50-2, 30)=30
    assert plan_calls == [30, 30]
    # 门票耗到低于场次剩余后门票约束生效：29 < 48
    obj._ticket_cache['pass'] = 29
    obj.run_general_battle(config=cfg)
    assert plan_calls[-1] == 29


@pytest.mark.unit
def test_run_general_battle_dispatches_segment(monkeypatch):
    """分派链：经真实 ScriptTask.run_general_battle（含重写）驱动，到达段起点时
    auto_battle_run 被分派执行——段分支挂在组件 run_general_battle 上，重写了
    battle_wait 的爬塔同样可达。"""
    obj = _make_climb(limit=50, count=1, ticket=None)
    obj.device = MagicMock()
    obj.interval_timer = {}
    _stub_battle_chain(monkeypatch, obj)
    seg_calls = []

    def _seg_run():
        # 复刻真实现入口副作用：领取段即消耗 planned（本场只进段一次）
        seg_calls.append(1)
        obj._auto_seg['planned'] = False

    monkeypatch.setattr(obj, 'auto_battle_run', _seg_run)
    # 起点抽到 0：本场即段起点（remaining = 50 - 1 = 49 足够规划 M+1）
    monkeypatch.setattr('tasks.Component.GeneralBattle.general_battle.random.randint',
                        lambda lo, hi: 0)
    from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
    cfg = GeneralBattleConfig()
    cfg.auto_battle_enable = True
    cfg.auto_segment_count = 2
    assert obj.run_general_battle(config=cfg) is True
    assert seg_calls == [1]
    # 段状态已按规划落位（起点 0、段长 2），且领取后 planned 归 False
    seg = obj._auto_seg
    assert seg['start_offset'] == 0 and seg['seg_len'] == 2
    assert seg['planned'] is False


@pytest.mark.unit
def test_run_general_battle_x5_disables_seg(monkeypatch, caplog):
    """五倍期间 run_general_battle 分派链上规划收到 None（端到端 fail-closed）。"""
    obj = _make_climb(x5=True, ticket=30)
    obj.device = MagicMock()
    obj.interval_timer = {}
    _stub_battle_chain(monkeypatch, obj)
    plan_calls = []
    monkeypatch.setattr(obj, 'auto_battle_plan', lambda cfg, r: plan_calls.append(r))
    monkeypatch.setattr(obj, 'auto_battle_run', lambda: None)
    from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
    obj.run_general_battle(config=GeneralBattleConfig())
    assert plan_calls == [None]


@pytest.mark.unit
def test_remaining_now_reuses_scope():
    """段内截断口径复用规划口径实时计算。"""
    obj = _make_climb(limit=50, count=10, ticket=20)
    assert obj.auto_battle_remaining_now() == 20


@pytest.mark.unit
def test_run_finish_sweep_called():
    """run() 收尾处调 auto_battle_finish_sweep（源码级断言）。"""
    import inspect
    src = inspect.getsource(ScriptTask.run)
    assert 'auto_battle_finish_sweep' in src
