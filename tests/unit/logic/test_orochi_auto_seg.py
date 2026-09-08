# -*- coding: utf-8 -*-
"""Orochi 自动段接线测试：remaining 口径、_battle_count_settle 抽取、钩子覆盖、
run_general_battle 分派链与五倍券禁用。"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Orochi.script_task import ScriptTask


def _make_orochi(ticket=3, five_times=True, team=False):
    """构造绕过 __init__ 的裸 ScriptTask，装配计数收尾所需最小依赖。"""
    obj = object.__new__(ScriptTask)
    obj.current_count = 1
    obj.limit_count = 30
    orochi_config = SimpleNamespace(
        five_times_enable=five_times, five_times_ticket=ticket)
    obj.config = SimpleNamespace(orochi=SimpleNamespace(
        orochi_config=orochi_config))
    obj.config.save = MagicMock()
    obj.team_store = MagicMock() if team else None
    obj.team_session = 'sess' if team else None
    obj.team_role = 'leader' if team else None
    obj._team_last_heartbeat = 0.0
    return obj


def _stub_battle_chain(monkeypatch, obj):
    """打桩通用战斗链（battle_before/绿标判定/battle_wait/段函数），
    让 ScriptTask.run_general_battle 与其 super 的真实方法可被驱动。"""
    monkeypatch.setattr(obj, 'battle_before', lambda *a, **k: True)
    monkeypatch.setattr(obj, 'is_in_battle', lambda shot=False: False)
    monkeypatch.setattr(obj, 'battle_wait', lambda *a, **k: True)


@pytest.mark.unit
def test_battle_count_settle_five_times_ticket():
    """五倍券收尾：+4 计数、扣 1 券、回写配置。"""
    obj = _make_orochi(ticket=3)
    obj._battle_count_settle()
    assert obj.current_count == 5
    assert obj.config.orochi.orochi_config.five_times_ticket == 2
    obj.config.save.assert_called_once()


@pytest.mark.unit
def test_battle_count_settle_no_ticket():
    """无券：计数不动，只走组队心跳路径（非组队则无事）。"""
    obj = _make_orochi(ticket=0)
    obj._battle_count_settle()
    assert obj.current_count == 1
    obj.config.save.assert_not_called()


@pytest.mark.unit
def test_battle_count_settle_team_leader_progress():
    """组队队长：每场同步 JSON 进度（update_progress 以 current_count 调用）。"""
    obj = _make_orochi(team=True)
    obj._battle_count_settle()
    obj.team_store.update_progress.assert_called_once_with('sess', obj.current_count)


@pytest.mark.unit
def test_auto_battle_count_hook_reuses_settle():
    """钩子覆盖：段内每场走与手动场同一份 _battle_count_settle。"""
    obj = _make_orochi(ticket=2)
    obj.auto_battle_count_hook()
    assert obj.current_count == 5
    assert obj.config.orochi.orochi_config.five_times_ticket == 1


@pytest.mark.unit
def test_remaining_now_local_scope():
    """截断口径：limit_count - current_count（五倍券的 +4 已计入）。"""
    obj = _make_orochi()
    obj.current_count = 7
    assert obj.auto_battle_remaining_now() == 23


@pytest.mark.unit
def test_run_general_battle_passes_remaining(monkeypatch):
    """Orochi 重写向 super 传 remaining = limit_count - current_count（调用前口径），
    经 auto_battle_plan 的 plan_calls 行为断言（非源码断言）。"""
    obj = _make_orochi(ticket=0, five_times=False)
    obj.device = MagicMock()
    obj.interval_timer = {}
    _stub_battle_chain(monkeypatch, obj)
    plan_calls = []
    monkeypatch.setattr(obj, 'auto_battle_plan', lambda cfg, r: plan_calls.append(r))
    monkeypatch.setattr(obj, 'auto_battle_run', lambda: None)
    cfg = GeneralBattleConfig()
    obj.run_general_battle(config=cfg)
    obj.run_general_battle(config=cfg)
    # 第一次调用前 current=1 → 29；序言 +1 后第二次调用前 current=2 → 28
    assert plan_calls == [29, 28]


@pytest.mark.unit
def test_run_general_battle_dispatches_segment(monkeypatch):
    """Orochi 分派链：经真实 ScriptTask.run_general_battle（含重写）驱动，
    到达段起点时 auto_battle_run 被分派执行——段分支挂 run_general_battle
    而非 battle_wait，重写了 battle_wait 的 Orochi 同样可达。"""
    obj = _make_orochi(ticket=0, five_times=False)
    obj.device = MagicMock()
    obj.interval_timer = {}
    _stub_battle_chain(monkeypatch, obj)
    seg_calls = []

    def _seg_run():
        # 复刻真实现入口副作用：领取段即消耗 planned（本场只进段一次）
        seg_calls.append(1)
        obj._auto_seg['planned'] = False

    monkeypatch.setattr(obj, 'auto_battle_run', _seg_run)
    # 起点抽到 0：本场即段起点（remaining = 30 - 1 = 29 足够规划 M+1）
    monkeypatch.setattr('tasks.Component.GeneralBattle.general_battle.random.randint',
                        lambda lo, hi: 0)
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
def test_five_times_ticket_disables_auto_seg(monkeypatch, caplog):
    """五倍券生效期间向规划传 None（自动段 fail-closed 禁用），告警只打一次。"""
    obj = _make_orochi(ticket=3, five_times=True)
    obj.device = MagicMock()
    obj.interval_timer = {}
    _stub_battle_chain(monkeypatch, obj)
    plan_calls = []
    monkeypatch.setattr(obj, 'auto_battle_plan', lambda cfg, r: plan_calls.append(r))
    monkeypatch.setattr(obj, 'auto_battle_run', lambda: None)
    cfg = GeneralBattleConfig()
    with caplog.at_level('WARNING'):
        obj.run_general_battle(config=cfg)
        obj.run_general_battle(config=cfg)   # 第二场仍有券（3→2）：持续禁用
    assert plan_calls == [None, None]
    warns = [r for r in caplog.records if '五倍券' in r.message]
    assert len(warns) == 1                   # 告警不刷屏


@pytest.mark.unit
def test_no_ticket_passes_remaining(monkeypatch):
    """券用完（ticket=0）后自动恢复：规划重新收到正常 remaining。"""
    obj = _make_orochi(ticket=0, five_times=True)
    obj.device = MagicMock()
    obj.interval_timer = {}
    _stub_battle_chain(monkeypatch, obj)
    plan_calls = []
    monkeypatch.setattr(obj, 'auto_battle_plan', lambda cfg, r: plan_calls.append(r))
    monkeypatch.setattr(obj, 'auto_battle_run', lambda: None)
    obj.run_general_battle(config=GeneralBattleConfig())
    assert plan_calls == [29]


@pytest.mark.unit
def test_run_finish_sweep_called():
    """run() 收尾处调 auto_battle_finish_sweep（源码级断言）。"""
    import inspect
    src = inspect.getsource(ScriptTask.run)
    assert 'auto_battle_finish_sweep' in src
