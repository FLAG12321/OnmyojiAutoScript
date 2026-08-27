"""拟人化计划数据类的构造校验测试（Plan Task 2）。

这些数据类是策略层与 backend 之间的唯一契约载体，所以校验必须在构造期就拒绝
非法值——等到 backend 逐点投递时才发现 delays 比 points 短，手势已经发出去一半了。
"""
import math

import pytest

from module.device.humanize.plan import (
    DwellPlan,
    MovePlan,
    TailPlan,
    _SwipeTail,
)

pytestmark = pytest.mark.unit


class TestMovePlan:
    def test_total_seconds_is_sum_of_delays(self):
        plan = MovePlan(points=((10, 20), (30, 40)), delays=(0.01, 0.02))
        assert plan.total_seconds == pytest.approx(0.03)

    def test_empty_points_rejected(self):
        with pytest.raises(ValueError, match='points 不能为空'):
            MovePlan(points=(), delays=())

    def test_length_mismatch_rejected(self):
        with pytest.raises(ValueError, match='长度必须相等'):
            MovePlan(points=((1, 2), (3, 4)), delays=(0.01,))

    @pytest.mark.parametrize('bad', [float('nan'), math.inf, -math.inf, -0.001])
    def test_non_finite_or_negative_delay_rejected(self, bad):
        with pytest.raises(ValueError):
            MovePlan(points=((1, 2),), delays=(bad,))

    @pytest.mark.parametrize('bad', [
        (1, 2, 3),        # 三元组
        (1.5, 2),         # float 分量
        ('1', 2),         # 字符串分量
        (True, 2),        # bool 是 int 子类，必须显式排除
        None,
        [1, 2],           # list 不是 tuple
    ])
    def test_illegal_point_rejected(self, bad):
        with pytest.raises((ValueError, TypeError)):
            MovePlan(points=(bad,), delays=(0.01,))

    def test_list_container_rejected(self):
        """facade 负责转 tuple；这里拒绝 list 才能让 frozen 的不可变语义真实成立。"""
        with pytest.raises(TypeError, match='必须是 tuple'):
            MovePlan(points=[(1, 2)], delays=[0.01])

    def test_frozen(self):
        plan = MovePlan(points=((1, 2),), delays=(0.01,))
        with pytest.raises(Exception):
            plan.points = ((3, 4),)


class TestDwellPlan:
    def test_none_point_means_wait_only(self):
        plan = DwellPlan(segments=((None, 0.05),))
        assert plan.segments[0][0] is None

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match='segments 不能为空'):
            DwellPlan(segments=())

    def test_negative_wait_rejected(self):
        with pytest.raises(ValueError):
            DwellPlan(segments=(((1, 2), -0.01),))

    def test_malformed_segment_rejected(self):
        with pytest.raises(ValueError, match='point\\|None, second'):
            DwellPlan(segments=(((1, 2),),))


class TestTailPlan:
    def test_accepts_parallel_sequences(self):
        tail = TailPlan(points=((5, 6), (7, 8)), delays=(0.01, 0.02))
        assert len(tail.points) == len(tail.delays) == 2

    def test_empty_rejected(self):
        """指针语义至少保留一条收尾移动（Spec §5 F），空 tail 无意义。"""
        with pytest.raises(ValueError, match='points 不能为空'):
            TailPlan(points=(), delays=())


class TestSwipeTail:
    def test_count_matches_delays(self):
        tail = _SwipeTail(count=2, delays=(0.05, 0.06))
        assert tail.count == len(tail.delays)

    def test_negative_count_rejected(self):
        with pytest.raises(ValueError, match='count 不能为负'):
            _SwipeTail(count=-1, delays=())

    def test_count_delays_mismatch_rejected(self):
        with pytest.raises(ValueError, match='长度必须等于 count'):
            _SwipeTail(count=3, delays=(0.05,))
