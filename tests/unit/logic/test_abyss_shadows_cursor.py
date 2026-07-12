# This Python file uses the following encoding: utf-8
# @brief    AbyssShadows 游标式进度逻辑的纯逻辑单元测试
# @note     只测 build_linear_sequence / get_resume_index / get_snake_done_from_cursor
#           这些不依赖设备的方法，绕过重量级 __init__

import pytest
from types import SimpleNamespace

from tasks.AbyssShadows.script_task import ScriptTask
from tasks.AbyssShadows.config import EnemyType


def make_task(attack_order: str, enable_snake: bool = False, snake_battle_count: int = 20,
              progress_cursor: str = ''):
    """ 构造一个仅带必要 config 字段的 ScriptTask，绕过 __init__ """
    task = ScriptTask.__new__(ScriptTask)
    pm = SimpleNamespace(
        attack_order=attack_order,
        enable_snake=enable_snake,
        snake_battle_count=snake_battle_count,
    )
    abyss = SimpleNamespace(process_manage=pm)
    model = SimpleNamespace(abyss_shadows=abyss)
    task.config = SimpleNamespace(model=model)
    task.progress_cursor = progress_cursor
    return task


class TestBuildLinearSequence:
    def test_without_snake(self):
        # attack_order=A -> 展开为 A-4,A-5,A-6,A-2,A-3,A-1（区内固定顺序）
        task = make_task('A')
        seq = task.build_linear_sequence()
        values = [s if isinstance(s, str) else s.value for s in seq]
        assert values == ['A-4', 'A-5', 'A-6', 'A-2', 'A-3', 'A-1']

    def test_with_snake_prepends_markers(self):
        # 启用小蛇：序列前面拼 N 个 'SNAKE' 标记
        task = make_task('A', enable_snake=True, snake_battle_count=3)
        seq = task.build_linear_sequence()
        assert seq[:3] == ['SNAKE', 'SNAKE', 'SNAKE']
        values = [s if isinstance(s, str) else s.value for s in seq[3:]]
        assert values == ['A-4', 'A-5', 'A-6', 'A-2', 'A-3', 'A-1']

    def test_multi_area_order(self):
        # attack_order=A;C -> A 段在前，C 段在后
        task = make_task('A;C')
        seq = task.build_linear_sequence()
        values = [s.value for s in seq]
        assert values[:6] == ['A-4', 'A-5', 'A-6', 'A-2', 'A-3', 'A-1']
        assert values[6:] == ['C-4', 'C-5', 'C-6', 'C-2', 'C-3', 'C-1']


class TestGetResumeIndex:
    def test_empty_cursor_starts_at_zero(self):
        task = make_task('A')
        seq = task.build_linear_sequence()
        assert task.get_resume_index(seq) == 0

    def test_snake_cursor(self):
        # 小蛇20次，游标 SNAKE-15 -> 从下标15（第16项）续跑
        task = make_task('A', enable_snake=True, snake_battle_count=20,
                         progress_cursor='SNAKE-15')
        seq = task.build_linear_sequence()
        assert task.get_resume_index(seq) == 15
        # 下标15仍在 SNAKE 段（0..19 是小蛇）
        assert seq[15] == 'SNAKE'

    def test_snake_done_moves_to_first_mob(self):
        # 小蛇打满20次，游标 SNAKE-20 -> 下标20，即第一个普通怪 A-4
        task = make_task('A', enable_snake=True, snake_battle_count=20,
                         progress_cursor='SNAKE-20')
        seq = task.build_linear_sequence()
        idx = task.get_resume_index(seq)
        assert idx == 20
        assert seq[idx].value == 'A-4'

    def test_normal_cursor_resumes_after(self):
        # 游标 A-5 -> 从 A-5 的下一项 A-6 续跑
        task = make_task('A', progress_cursor='A-5')
        seq = task.build_linear_sequence()
        idx = task.get_resume_index(seq)
        assert seq[idx].value == 'A-6'

    def test_cursor_before_area_means_prior_done(self):
        # 用户规则：游标 C-1 意味着 A（排在 C 前）与 C 都完成
        # attack_order=A;C，游标 C-4（C 段第一项）-> 续跑到 C-5，A 段已被越过
        task = make_task('A;C', progress_cursor='C-4')
        seq = task.build_linear_sequence()
        idx = task.get_resume_index(seq)
        assert seq[idx].value == 'C-5'
        # A 段整体在 idx 之前
        assert all(seq[i].value.startswith('A-') is False or i < idx
                   for i in range(idx, len(seq)))

    def test_cursor_not_in_sequence_restarts_from_zero(self):
        # 游标指向不在当前 attack_order 的怪（如 attack_order 被改小）
        # -> 从头重跑，避免漏打（已死的怪 execute 会跳过）
        task = make_task('A', progress_cursor='D-1')
        seq = task.build_linear_sequence()
        assert task.get_resume_index(seq) == 0


class TestGetSnakeDoneFromCursor:
    def test_empty_cursor(self):
        task = make_task('A', enable_snake=True, snake_battle_count=20)
        assert task.get_snake_done_from_cursor() == 0

    def test_snake_cursor(self):
        task = make_task('A', enable_snake=True, snake_battle_count=20,
                         progress_cursor='SNAKE-7')
        assert task.get_snake_done_from_cursor() == 7

    def test_normal_cursor_means_snake_all_done(self):
        # 游标已到普通怪，小蛇段必然完成 -> 返回 snake_battle_count
        task = make_task('A', enable_snake=True, snake_battle_count=20,
                         progress_cursor='A-4')
        assert task.get_snake_done_from_cursor() == 20


class TestGetCompletionTarget:
    """ 补全阶段 get_completion_target 的 tried 记忆逻辑（修复漏补/重复刷 bug） """

    def _make(self, boss=0, general=0, elite=0):
        task = make_task('A')
        task.done_boss = boss
        task.done_general = general
        task.done_elite = elite
        return task

    def test_returns_first_needed_type(self):
        # 缺 boss（min_count BOSS=2），返回第一个 boss 节点 A-1
        task = self._make(boss=0, general=4, elite=6)
        target = task.get_completion_target(set())
        assert target.value == 'A-1'  # BOSS 对应序号 1

    def test_skips_tried_nodes(self):
        # A-1 已尝试过 -> 跳过，返回下一个 boss 节点 B-1
        task = self._make(boss=0, general=4, elite=6)
        target = task.get_completion_target({'A-1'})
        assert target.value == 'B-1'

    def test_returns_none_when_all_satisfied(self):
        # 三类都已满足 min_count(2/4/6) -> None
        task = self._make(boss=2, general=4, elite=6)
        assert task.get_completion_target(set()) is None

    def test_returns_none_when_all_candidates_tried(self):
        # 缺 boss 但四区 boss 节点(A-1,B-1,C-1,D-1)都尝试过 -> None，不再死循环
        task = self._make(boss=0, general=4, elite=6)
        target = task.get_completion_target({'A-1', 'B-1', 'C-1', 'D-1'})
        assert target is None
