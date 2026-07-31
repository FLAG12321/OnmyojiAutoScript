# This Python file uses the following encoding: utf-8
"""同心战斗场次接续测试：只验证计数恢复/回写，不触碰真实战斗流程。"""
from pathlib import Path

import pytest

KEY = 'mail@x.com|小号一|两情相悦'


class Store:
    def __init__(self, count=0):
        self.count = count
        self.added = 0

    def get_battle_count(self, key, task='alliedteam'):
        return self.count

    def add_battle_count(self, key, n=1, task='alliedteam'):
        self.count += n
        self.added += n
        return self.count


def _make(store, count_attr=None):
    from tasks.DailyAltAcc.alliedteam import Alliedteam

    obj = object.__new__(Alliedteam)
    obj._progress = store
    obj._progress_key = KEY
    obj.current_count = count_attr if count_attr is not None else 0
    return obj


@pytest.mark.unit
def test_restore_battle_count_sets_current_count():
    obj = _make(Store(count=10))
    # 已打 10 场，恢复后 current_count 应为 10，limit 13 时只再打 3 场
    assert obj._restore_battle_count() == 10
    assert obj.current_count == 10


@pytest.mark.unit
def test_restore_without_store_keeps_zero():
    obj = _make(None)
    assert obj._restore_battle_count() == 0
    assert obj.current_count == 0


@pytest.mark.unit
def test_persist_battle_count_increments_store():
    store = Store(count=10)
    obj = _make(store)
    obj._persist_battle_count()
    assert store.count == 11
    assert store.added == 1


@pytest.mark.unit
def test_persist_without_store_is_noop():
    obj = _make(None)
    # 无 store 时不应抛异常
    obj._persist_battle_count()


@pytest.mark.unit
def test_run_alliedteam_restores_before_capturing_before_count():
    """恢复必须发生在 before_count 捕获之前，否则战斗统计的新增场数会虚高。"""
    source = Path('tasks/DailyAltAcc/alliedteam.py').read_text(encoding='utf-8')
    # 先断言存在性：index() 找不到会抛 ValueError，报错信息不如断言清晰
    assert 'self._restore_battle_count()' in source
    assert 'before_count = ' in source
    restore_pos = source.index('self._restore_battle_count()')
    before_pos = source.index('before_count = ')
    assert restore_pos < before_pos


@pytest.mark.unit
def test_run_alone_persists_after_each_battle():
    """每场战斗结束后必须立刻回写，崩溃才能接续。"""
    source = Path('tasks/DailyAltAcc/alliedteam.py').read_text(encoding='utf-8')
    # 已核对 alliedteam.py:255 的实际写法与此逐字符一致
    assert 'self.run_general_battle(config=self.config.daily_alt_acc.general_battle_config)' in source
    assert 'self._persist_battle_count()' in source
    battle_pos = source.index('self.run_general_battle(config=self.config.daily_alt_acc.general_battle_config)')
    persist_pos = source.index('self._persist_battle_count()', battle_pos)
    # 回写紧跟在战斗调用之后
    assert 0 < persist_pos - battle_pos < 200
