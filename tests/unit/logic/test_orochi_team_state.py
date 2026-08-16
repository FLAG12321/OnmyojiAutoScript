# This Python file uses the following encoding: utf-8
from datetime import datetime, timedelta

import pytest

from tasks.Orochi.team_state import (
    LEADER_FRESH_SECONDS,
    PHASE_FINISHED,
    PHASE_INVITING,
    PHASE_PREPARING,
    PHASE_WAIT_MEMBER,
    PHASE_WAIT_NEXT_ROUND,
    RoundNotDueError,
    StaleSessionError,
    TeamSession,
    TeamStateStore,
)


NOW = datetime(2026, 8, 16, 10, 0, 0)


def open_round(store: TeamStateStore, **overrides) -> dict:
    """用稳定默认值发布一轮，单测只覆盖与断言相关的字段。"""
    values = {
        'round_limit_count': 30,
        'round_limit_seconds': 1800,
        'total_limit_count': 100,
        'total_limit_seconds': 7200,
        'soul_buff_enable': True,
        'enable_realm_raid_chain': True,
        'now': NOW,
    }
    values.update(overrides)
    return store.open_round(**values)


def prepare_round(store: TeamStateStore, state: dict) -> TeamSession:
    """完成配对与双方准备，返回后状态应允许队长邀请。"""
    joined = store.try_join_member('OAS1', now=NOW)
    assert joined is not None
    session = TeamSession.from_state(joined)
    store.mark_ready(session, 'leader', 'soul')
    store.mark_ready(session, 'member', 'soul')
    store.mark_ready(session, 'leader', 'buff')
    store.mark_ready(session, 'member', 'buff')
    return session


def test_member_only_joins_fresh_wait_member(tmp_path):
    store = TeamStateStore('OAS2', base_dir=tmp_path)
    state = open_round(store)
    assert state['phase'] == PHASE_WAIT_MEMBER

    stale_time = NOW + timedelta(seconds=LEADER_FRESH_SECONDS + 1)
    assert store.try_join_member('OAS1', now=stale_time) is None
    # 加入失败是纯读取判断，不得改变旧场次或产生伪成员绑定。
    unchanged = store.read()
    assert unchanged['phase'] == PHASE_WAIT_MEMBER
    assert unchanged['member_instance'] == ''


def test_ready_barrier_blocks_inviting_until_both_buffs_ready(tmp_path):
    store = TeamStateStore('OAS2', base_dir=tmp_path)
    state = open_round(store)
    joined = store.try_join_member('OAS1', now=NOW)
    session = TeamSession.from_state(joined)
    assert joined['phase'] == PHASE_PREPARING

    store.mark_ready(session, 'leader', 'soul')
    store.mark_ready(session, 'member', 'soul')
    store.mark_ready(session, 'leader', 'buff')
    with pytest.raises(StaleSessionError, match='TEAM_NOT_READY'):
        store.start_inviting(session, now=NOW)

    store.mark_ready(session, 'member', 'buff')
    inviting = store.start_inviting(session, now=NOW)
    assert inviting['phase'] == PHASE_INVITING
    assert store.can_start_battle(session) is True


def test_reset_generates_new_epoch_and_rejects_old_session_writes(tmp_path):
    store = TeamStateStore('OAS2', base_dir=tmp_path)
    state = open_round(store)
    session = prepare_round(store, state)
    old_epoch = session.progress_epoch

    reset_state = store.reset('OAS1')
    assert reset_state['progress_epoch'] != old_epoch
    assert reset_state['total_count'] == 0
    assert reset_state['round_id'] == 0
    assert reset_state['member_instance'] == ''
    assert reset_state['leader_buff_ready'] is False
    assert datetime.fromisoformat(reset_state['next_orochi_at']) > datetime.now()

    with pytest.raises(StaleSessionError, match='STALE_SESSION'):
        store.update_progress(session, 5)


def test_leader_progress_and_next_round_time_are_authoritative(tmp_path):
    store = TeamStateStore('OAS2', base_dir=tmp_path)
    state = open_round(store)
    session = prepare_round(store, state)
    store.start_inviting(session, now=NOW)
    store.mark_running(session)
    store.update_progress(session, 5)

    next_orochi = NOW + timedelta(minutes=10)
    next_realm_raid = NOW + timedelta(minutes=1)
    finished = store.finish_round(
        session,
        success=True,
        next_orochi_at=next_orochi,
        next_realm_raid_at=next_realm_raid,
        now=NOW + timedelta(minutes=2),
    )
    assert finished['phase'] == PHASE_WAIT_NEXT_ROUND
    assert finished['total_count'] == 5
    assert finished['total_elapsed_seconds'] == 120
    assert datetime.fromisoformat(finished['next_orochi_at']) == next_orochi
    assert datetime.fromisoformat(finished['next_realm_raid_at']) == next_realm_raid

    # 任务结束后 heartbeat 是只读 no-op，且重复收尾不能重复累计时间。
    assert store.heartbeat(session, 'member', now=NOW + timedelta(minutes=3)) == finished
    assert store.finish_round(
        session,
        success=True,
        next_orochi_at=next_orochi,
        next_realm_raid_at=next_realm_raid,
        now=NOW + timedelta(minutes=3),
    )['total_elapsed_seconds'] == 120

    with pytest.raises(RoundNotDueError) as exc_info:
        open_round(store, now=next_orochi - timedelta(seconds=1))
    assert exc_info.value.next_orochi_at == next_orochi

    next_state = open_round(store, now=next_orochi)
    assert next_state['round_id'] == 2
    assert next_state['total_count'] == 5
    assert next_state['effective_limit_count'] == 30


def test_total_limit_finishes_without_scheduling_another_orochi_round(tmp_path):
    store = TeamStateStore('OAS2', base_dir=tmp_path)
    state = open_round(store, total_limit_count=5)
    session = prepare_round(store, state)
    store.start_inviting(session, now=NOW)
    store.update_progress(session, 5)

    finished = store.finish_round(
        session,
        success=True,
        next_orochi_at=NOW + timedelta(minutes=10),
        next_realm_raid_at=NOW + timedelta(minutes=1),
        now=NOW + timedelta(minutes=1),
    )
    assert finished['phase'] == PHASE_FINISHED
    assert finished['next_orochi_at'] is None
    # 达到总限制后仍保留本轮突破时间，完成御魂-突破的最后一组。
    assert finished['next_realm_raid_at'] is not None


def test_state_file_name_uses_leader_instance_case_insensitively(tmp_path):
    upper = TeamStateStore('OAS2', base_dir=tmp_path)
    lower = TeamStateStore('oas2', base_dir=tmp_path)
    assert upper.path == lower.path
