# This Python file uses the following encoding: utf-8
"""同心战斗「弹回组队页」的恢复测试：不触碰真实设备，只驱动分支。

背景：队伍打满一轮/队友退出后游戏回到组队页（"请在左侧选择目标副本"），
I_BATTLE 消失。原来 run_alone 只能空转到 60s stuck 抛 GameStuckError，
剩余场次全丢；现在复用建队链路推回挑战界面，恢复不了则按「未打满」收尾。
"""
from pathlib import Path

import pytest


class FakeStore:
    def __init__(self):
        self.count = 0

    def get_battle_count(self, key, task='alliedteam'):
        return self.count

    def add_battle_count(self, key, n=1, task='alliedteam'):
        self.count += n
        return self.count


def _make(limit=3, auto=False):
    """构造绕过 __init__ 的裸 Alliedteam，装配 run_alone 所需的最小依赖。"""
    from types import SimpleNamespace

    from tasks.DailyAltAcc.alliedteam import Alliedteam

    obj = object.__new__(Alliedteam)
    obj._progress = FakeStore()
    obj._progress_key = 'a@b.com|小号一|两情相悦'
    obj.current_count = 0
    obj.screenshots = 0
    # limit != 13 走 check_lock(True) 分支；auto=False 保持手动路径语义
    obj.get_config = lambda: SimpleNamespace(
        daily_alt_acc_config=SimpleNamespace(
            alliedteam_limit_count=limit,
            alliedteam_auto_battle_enable=auto,
        )
    )
    obj.config = SimpleNamespace(
        daily_alt_acc=SimpleNamespace(general_battle_config=None)
    )
    obj.screenshot = lambda: setattr(obj, 'screenshots', obj.screenshots + 1)
    obj.check_lock = lambda lock=True: True
    return obj


class _AutoScene:
    """逐帧脚本化的自动战斗场景，驱动 _auto_battle_loop 的分支。

    每帧是一份界面状态字典：battle=战斗画面、challenge=房间（挑战按钮
    I_BATTLE 可见）、countdown=顶部倒数 OCR 文本、auto_off=关态自动开关可见。
    每调一次 screenshot() 前进一帧；帧耗尽后停在最后一帧（用例需保证终帧触发收尾）。
    """

    def __init__(self, obj, frames):
        from types import SimpleNamespace

        self.obj = obj
        self.frames = frames
        self.i = 0
        self.clicks = []
        self.exited = 0
        obj.device = SimpleNamespace(stuck_record_add=lambda *a, **k: None)
        obj.screenshot = lambda: setattr(self, 'i', min(self.i + 1, len(frames) - 1))
        obj.is_in_real_battle = lambda screenshot=False: self.frame.get('battle', False)
        obj.appear = lambda target, *a, **k: self.frame.get('challenge', False)
        obj._read_room_countdown = lambda: self.frame.get('countdown', '')
        obj.click = lambda target, interval=None: self.clicks.append(target)
        obj.exit_battle = lambda skip_first=False: setattr(self, 'exited', self.exited + 1) or True

        obj_ref = obj

        def appear_then_click(target, *a, **k):
            if target is obj_ref.I_AUTO_OFF:
                return bool(self.frame.get('auto_off'))
            return False

        obj.appear_then_click = appear_then_click

    @property
    def frame(self):
        return self.frames[self.i]


@pytest.mark.unit
def test_auto_battle_loop_counts_and_closes(monkeypatch):
    """正常路径：房间开自动 → 两场各自计数落盘 → 打满后关自动并读到分钟级倒数。"""
    import tasks.DailyAltAcc.alliedteam as alliedteam_mod
    monkeypatch.setattr(alliedteam_mod.time, 'sleep', lambda s: None)

    obj = _make(limit=2, auto=True)
    scene = _AutoScene(obj, [
        {'challenge': True, 'countdown': '01分30', 'auto_off': True},  # 房间点开自动
        {'challenge': True, 'countdown': '00分0'},                     # 确认已开
        {'battle': True},                                              # 第 1 场（游戏自动点挑战）
        {'battle': True},
        {'challenge': True, 'countdown': '00分0'},                     # 场间自动流转
        {'battle': True},                                              # 第 2 场（打满）
        {'battle': True},
        {'challenge': True, 'countdown': '00分0'},                     # 还开着 → 点关闭
        {'challenge': True, 'countdown': '01分30'},                    # 恢复分钟级 → 确认关闭
    ])

    assert obj._auto_battle_loop(2) is True
    assert obj.current_count == 2, '进入战斗的上升沿应各计一场'
    assert obj._progress.count == 2, '每场都必须落盘'
    assert scene.exited == 0, '正常路径不应退出战斗'
    # 挑战按钮必须由游戏点击，脚本全程不点 I_BATTLE（frames 中无任何脚本点挑战路径）


@pytest.mark.unit
def test_auto_battle_loop_exits_battle_when_full_and_pulled_in(monkeypatch):
    """打满后未确认关闭仍被拉进战斗：退出战斗，场次已满判成功。"""
    import tasks.DailyAltAcc.alliedteam as alliedteam_mod
    monkeypatch.setattr(alliedteam_mod.time, 'sleep', lambda s: None)

    obj = _make(limit=1, auto=True)
    scene = _AutoScene(obj, [
        {'challenge': True, 'countdown': '', 'auto_off': True},
        {'challenge': True, 'countdown': '00分0'},
        {'battle': True},                          # 第 1 场（打满）
        {'battle': True},
        {'challenge': True, 'countdown': '00分0'},  # 关一次没关掉
        {'challenge': True, 'countdown': '00分0'},  # 再关仍没关掉
        {'battle': True},                          # 被拉进意外战斗 → 退出
    ])

    assert obj._auto_battle_loop(1) is True, '场次已打完，退出战斗也应判成功'
    assert scene.exited == 1, '被拉进意外战斗必须退出'
    assert obj.current_count == 1, '意外进战的那场不得计数'
    assert obj._progress.count == 1


@pytest.mark.unit
def test_run_alone_returns_true_when_limit_reached():
    """已达上限：直接 True，调用方标 done。"""
    obj = _make(limit=3)
    obj.current_count = 3
    assert obj.run_alone() is True


@pytest.mark.unit
def test_run_alone_returns_false_when_recovery_fails():
    """不在挑战界面且恢复失败：返回 False 让上层走「未打满」接续通道。"""
    obj = _make(limit=3)
    obj.current_count = 1
    obj.appear = lambda *a, **k: False          # I_BATTLE 始终不出现
    obj._ensure_battle_ready = lambda *a, **k: False

    assert obj.run_alone() is False
    assert obj.current_count == 1, '恢复失败不得改动已打场次'


@pytest.mark.unit
def test_run_alone_recovers_then_continues_battling():
    """恢复成功后继续打，直到打满上限返回 True。"""
    obj = _make(limit=2)
    # ready 表示「挑战按钮可见」。内层循环的语义是：点挑战 → 按钮消失 → 进战斗，
    # 所以 appear_then_click 要把 ready 置回 False，否则内层循环永不退出。
    state = {'ready': False, 'recovered': 0}

    def appear(*a, **k):
        return state['ready']

    def appear_then_click(*a, **k):
        if not state['ready']:
            return False
        state['ready'] = False   # 点下挑战，按钮消失
        return True

    def ensure_ready(*a, **k):
        state['recovered'] += 1
        state['ready'] = True
        return True

    def run_battle(*a, **k):
        obj.current_count += 1
        return True

    obj.appear = appear
    obj.appear_then_click = appear_then_click
    obj._ensure_battle_ready = ensure_ready
    obj.run_general_battle = run_battle

    assert obj.run_alone() is True
    assert obj.current_count == 2
    assert state['recovered'] >= 2, '每次被弹回都应触发一次恢复'
    assert obj._progress.count == 2, '每场都必须落盘'


# ---------- 源码结构约束 ----------

@pytest.mark.unit
def test_ensure_battle_ready_is_shared_by_both_call_sites():
    """建队与恢复必须共用同一方法，避免两处点击链路漂移。"""
    source = Path('tasks/DailyAltAcc/alliedteam.py').read_text(encoding='utf-8')
    assert 'def _ensure_battle_ready' in source
    # 首次建队 + run_alone 恢复两处调用
    assert source.count('self._ensure_battle_ready()') == 2


@pytest.mark.unit
def test_run_alone_no_longer_spins_on_bare_continue():
    """不在挑战界面时不得再裸 continue 空转（那是卡死 60s 的根因）。"""
    source = Path('tasks/DailyAltAcc/alliedteam.py').read_text(encoding='utf-8')
    assert 'if not is_in_evozone():' in source
    start = source.index('if not is_in_evozone():')
    # 该分支内必须先尝试恢复，而不是直接 continue
    branch = source[start:start + 400]
    assert '_ensure_battle_ready' in branch
    assert not branch.lstrip().startswith('if not is_in_evozone():\n                continue')


@pytest.mark.unit
def test_run_alliedteam_battle_propagates_run_alone_result():
    """run_alone 的未打满结论必须透传，否则会被标 done 丢掉剩余场次。"""
    source = Path('tasks/DailyAltAcc/alliedteam.py').read_text(encoding='utf-8')
    assert 'return self.run_alone()' in source
    assert 'self.run_alone()\n        return True' not in source
