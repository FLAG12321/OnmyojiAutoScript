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
    REALM_RAID_PENDING_EXPIRE_SECONDS,
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
        'leader_realm_raid': True,
        'now': NOW,
    }
    values.update(overrides)
    return store.open_round(**values)


def prepare_round(store: TeamStateStore, state: dict, member_realm_raid: bool = False,
                  now: datetime = NOW) -> TeamSession:
    """完成配对与双方准备，返回后状态应允许队长邀请。

    now 必须与该轮 open_round 的时间一致，否则会被队长新鲜度检查正确拒绝。
    """
    joined = store.try_join_member('OAS1', member_realm_raid=member_realm_raid, now=now)
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
    assert store.try_join_member('OAS1', member_realm_raid=False, now=stale_time) is None
    # 加入失败是纯读取判断，不得改变旧场次或产生伪成员绑定。
    unchanged = store.read()
    assert unchanged['phase'] == PHASE_WAIT_MEMBER
    assert unchanged['member_instance'] == ''


def test_ready_barrier_blocks_inviting_until_both_buffs_ready(tmp_path):
    store = TeamStateStore('OAS2', base_dir=tmp_path)
    state = open_round(store)
    joined = store.try_join_member('OAS1', member_realm_raid=False, now=NOW)
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

    next_orochi = NOW + timedelta(minutes=1)
    finished = store.finish_round(
        session,
        success=True,
        next_orochi_at=next_orochi,
        now=NOW + timedelta(minutes=2),
    )
    assert finished['phase'] == PHASE_WAIT_NEXT_ROUND
    assert finished['total_count'] == 5
    assert finished['total_elapsed_seconds'] == 120
    assert datetime.fromisoformat(finished['next_orochi_at']) == next_orochi

    # 任务结束后 heartbeat 是只读 no-op，且重复收尾不能重复累计时间。
    assert store.heartbeat(session, 'member', now=NOW + timedelta(minutes=3)) == finished
    assert store.finish_round(
        session,
        success=True,
        next_orochi_at=next_orochi,
        now=NOW + timedelta(minutes=3),
    )['total_elapsed_seconds'] == 120

    with pytest.raises(RoundNotDueError) as exc_info:
        open_round(store, now=next_orochi - timedelta(seconds=1))
    assert exc_info.value.next_orochi_at == next_orochi

    next_state = open_round(store, now=next_orochi)
    assert next_state['round_id'] == 2
    assert next_state['total_count'] == 5
    # 队长开着突破，本轮仍按单轮配置切分
    session2 = prepare_round(store, next_state, now=next_orochi)
    assert store.start_inviting(session2, now=next_orochi)['effective_limit_count'] == 30


def test_total_limit_finishes_without_scheduling_another_orochi_round(tmp_path):
    store = TeamStateStore('OAS2', base_dir=tmp_path)
    state = open_round(store, total_limit_count=5)
    session = prepare_round(store, state)
    store.start_inviting(session, now=NOW)
    store.update_progress(session, 5)

    finished = store.finish_round(
        session,
        success=True,
        next_orochi_at=NOW + timedelta(minutes=1),
        now=NOW + timedelta(minutes=1),
    )
    assert finished['phase'] == PHASE_FINISHED
    assert finished['next_orochi_at'] is None
    # 总量已完成不会再配对，等待标记必须清掉
    assert finished['pairing_needs_realm_raid'] is False


def test_no_realm_raid_on_either_side_runs_total_in_one_round(tmp_path):
    """双方都不打突破：单轮限制放宽到总量剩余，一次打完不分轮。"""
    store = TeamStateStore('OAS2', base_dir=tmp_path)
    state = open_round(store, leader_realm_raid=False, total_limit_count=100,
                       total_limit_seconds=7200)
    # 发布阶段还不知道队员是否开突破，先按总量剩余登记
    assert state['effective_limit_count'] == 100
    session = prepare_round(store, state, member_realm_raid=False)

    inviting = store.start_inviting(session, now=NOW)
    assert inviting['needs_realm_raid'] is False
    # 单轮配置是 30/1800，但无人打突破，直接放宽到总量剩余
    assert inviting['effective_limit_count'] == 100
    assert inviting['effective_limit_seconds'] == 7200

    store.update_progress(session, 100)
    finished = store.finish_round(
        session,
        success=True,
        next_orochi_at=NOW + timedelta(minutes=1),
        now=NOW + timedelta(minutes=30),
    )
    # 一次打完总量，不进入 WAIT_NEXT_ROUND
    assert finished['phase'] == PHASE_FINISHED
    assert finished['pairing_needs_realm_raid'] is False


def test_member_realm_raid_alone_still_splits_rounds(tmp_path):
    """只有队员开突破也要分轮，并置起跨轮的配对等待标记。"""
    store = TeamStateStore('OAS2', base_dir=tmp_path)
    state = open_round(store, leader_realm_raid=False)
    session = prepare_round(store, state, member_realm_raid=True)

    inviting = store.start_inviting(session, now=NOW)
    assert inviting['needs_realm_raid'] is True
    assert inviting['effective_limit_count'] == 30

    store.update_progress(session, 30)
    finished = store.finish_round(
        session,
        success=True,
        next_orochi_at=NOW + timedelta(minutes=1),
        now=NOW + timedelta(minutes=10),
    )
    assert finished['phase'] == PHASE_WAIT_NEXT_ROUND
    # 下一轮没突破的队长必须等队员打完突破，标记要跨轮保留
    assert finished['pairing_needs_realm_raid'] is True
    next_state = open_round(store, leader_realm_raid=False,
                            now=NOW + timedelta(minutes=11))
    assert next_state['pairing_needs_realm_raid'] is True


def test_state_file_name_uses_leader_instance_case_insensitively(tmp_path):
    upper = TeamStateStore('OAS2', base_dir=tmp_path)
    lower = TeamStateStore('oas2', base_dir=tmp_path)
    assert upper.path == lower.path


def test_realm_raid_pending_is_queryable_by_the_other_side(tmp_path):
    """打突破的一方落标记，对方能查到；它回来清除后对方立刻不再等待。"""
    store = TeamStateStore('OAS2', base_dir=tmp_path)
    open_round(store)

    assert store.realm_raid_pending(store.read(), 'leader', now=NOW) is False

    store.mark_realm_raid_pending('leader', now=NOW)
    # 队员侧据此判断队长仍在突破中
    assert store.realm_raid_pending(store.read(), 'leader', now=NOW) is True
    # 只影响落标记的那一方
    assert store.realm_raid_pending(store.read(), 'member', now=NOW) is False

    store.clear_realm_raid_pending('leader', now=NOW + timedelta(minutes=15))
    assert store.realm_raid_pending(store.read(), 'leader', now=NOW) is False


def test_realm_raid_pending_expires_so_a_dead_peer_cannot_block_forever(tmp_path):
    """对方崩溃后标记不会永久有效，超过兜底时长即视为不在突破中。"""
    store = TeamStateStore('OAS2', base_dir=tmp_path)
    open_round(store)
    store.mark_realm_raid_pending('member', now=NOW)

    state = store.read()
    inside = NOW + timedelta(seconds=REALM_RAID_PENDING_EXPIRE_SECONDS - 1)
    outside = NOW + timedelta(seconds=REALM_RAID_PENDING_EXPIRE_SECONDS + 1)
    assert store.realm_raid_pending(state, 'member', now=inside) is True
    assert store.realm_raid_pending(state, 'member', now=outside) is False


def test_realm_raid_pending_survives_next_round_publication(tmp_path):
    """队长发布新场次时不能抹掉队员的突破标记，否则会退回短超时提前放弃。"""
    store = TeamStateStore('OAS2', base_dir=tmp_path)
    state = open_round(store, leader_realm_raid=False)
    session = prepare_round(store, state, member_realm_raid=True)
    store.start_inviting(session, now=NOW)
    store.update_progress(session, 30)
    store.finish_round(
        session,
        success=True,
        next_orochi_at=NOW + timedelta(minutes=1),
        now=NOW + timedelta(minutes=10),
    )
    # 队员去打突破，队长先回来发布下一轮
    store.mark_realm_raid_pending('member', now=NOW + timedelta(minutes=10))
    next_state = open_round(store, leader_realm_raid=False,
                            now=NOW + timedelta(minutes=11))
    assert store.realm_raid_pending(next_state, 'member',
                                    now=NOW + timedelta(minutes=11)) is True
