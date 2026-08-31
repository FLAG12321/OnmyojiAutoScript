# This Python file uses the following encoding: utf-8
"""协作汇总推送测试。

覆盖：
1. 协作识别事件结构化（现世勾协/普通勾协/现世体协/普通体协/狗粮/猫粮/金币）
2. ProgressStore coop 持久化（立即保存/接续保留/clear 清除/新阶段不继承/重建前归档/三配置隔离）
3. 汇总文本格式（7 类顺序/空分类隐藏/同角色同类 ×2/区服/去重角色数/事件总数/空协作）
4. 推送时机（整轮成功 1 次/phase_failed 0 次/中途异常 0 次/接续不推半份/notifier 失败不炸）
5. 其他通知保持（mshop 仍推/neterror 不推/cooperation 不即时推）
6. 登录页局部修复（进程存在+登录页→重试；进程不存在→仍 GameNotRunning；SwitchAccount/GameUi 未改）
"""
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from tasks.DailyAltAcc.config import MSGType
from tasks.MultiDailyAltAcc.progress import ProgressStore, phase_flags_of
from tasks.MultiDailyAltAcc.script_task import ScriptTask

FLAGS_A = {'total_mail_enable': True, 'total_alliedteam_battle_enable': False}
FLAGS_B = {'total_mail_enable': False, 'total_alliedteam_battle_enable': True}

# 测试 config_name 默认 'oas1'，_notify_daily_completion 拼接完整标题后为 'oas1｜多账号日常完成'
SUMMARY_TITLE = 'oas1｜多账号日常完成'


def _make_task():
    """绕过 __init__ 构造 ScriptTask，便于注入最小依赖。"""
    return object.__new__(ScriptTask)


def _account(account='mail@x.com', character='小号一', svr='两情相悦'):
    return SimpleNamespace(account=account, character=character, svr=svr, apple_or_android=True)


class _FakeNotifier:
    """记录 push 调用；可切换失败模式。"""

    def __init__(self, call_log=None):
        self.pushes = []
        self.fail = False
        self._call_log = call_log

    def push(self, **kwargs):
        if self.fail:
            raise RuntimeError('push failed')
        self.pushes.append(kwargs)
        if self._call_log is not None:
            self._call_log.append(('push',))
        return True


def _fake_config(notifier=None, **extra):
    base = dict(notifier=notifier or _FakeNotifier(), config_name='oas1')
    base.update(extra)
    return SimpleNamespace(**base)


def _task_with_progress(tmp_path, notifier=None, config_name='oas1'):
    """构造带真实 ProgressStore（tmp 隔离）的任务。"""
    task = _make_task()
    task.config = _fake_config(notifier, config_name=config_name)
    task._progress = ProgressStore(config_name, base_dir=tmp_path)
    task._progress.ensure_phase(FLAGS_A, '20260817-0605')
    return task


def _full_phase_config(**overrides):
    """覆盖 PHASE_FLAG_KEYS 全部键的 phase 配置，保证 phase_flags_of 快照完整。"""
    defaults = {
        'total_alliedteam_battle_enable': False,
        'total_alliedteam_ap_enable': False,
        'total_returngift_enable': False,
        'total_courtyard_enable': False,
        'total_mail_enable': True,
        'total_cooperation_enable': True,
        'total_weekaward_enable': False,
        'total_mysteryshop_enable': False,
        'total_tree_planting_enable': 2,
        'total_trialbattle_enable': False,
        'total_summon_up_enable': False,
        'total_publish_sr_enable': False,
        # run() 第 147 行直接访问该字段（成功分支关机判断）
        'shutdown_after_finish': False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_run_task(tmp_path, monkeypatch, notifier, phase_overrides=None, seed_coops=()):
    """构造可直接执行 run() 的最小任务；run() 内部重建的 ProgressStore 指向 tmp。

    - run() 会用 phase_flags_of(base_config) 重新 ensure_phase；预置阶段必须用同一
      快照，才能「接续」而非「重建」，从而验证 coop 在重启/接续后保留。
    - monkeypatch 模块级 ProgressStore 使 run() 内部新建的 store 落在 tmp，避免污染
      工作区 config/tasks_config。
    """
    import tasks.MultiDailyAltAcc.script_task as st_mod

    phase_cfg = _full_phase_config(**(phase_overrides or {}))
    task = _make_task()
    # run() 第 70 行会从 config 重新读取 multi_daily_alt_acc，因此 config 必须提供它
    task.config = _fake_config(notifier,
                               multi_daily_alt_acc=SimpleNamespace(multi_daily_alt_acc_config=phase_cfg))
    task.daily_conf = task.config.multi_daily_alt_acc
    task.start_time = datetime(2026, 8, 17, 6, 5)

    seed = ProgressStore('oas1', base_dir=tmp_path)
    seed.ensure_phase(phase_flags_of(phase_cfg), '20260817-0605')
    for c in seed_coops:
        seed.append_coop(c)

    monkeypatch.setattr(st_mod, 'ProgressStore',
                        lambda name: ProgressStore(name, base_dir=tmp_path))
    return task


# =====================================================================
# 1. 协作识别事件结构化（tasks/DailyAltAcc/cooperation.py）
# =====================================================================


def _coop_obj():
    from tasks.DailyAltAcc.cooperation import Cooperation

    coop = object.__new__(Cooperation)
    coop.msg = []
    return coop


def _activate(monkeypatch, *buttons):
    """让 appear 只对给定资产对象返回 True；screenshot 变 no-op。"""
    from tasks.DailyAltAcc.cooperation import Cooperation

    active = {id(b) for b in buttons}
    monkeypatch.setattr(
        Cooperation, 'appear', lambda self, button, interval=None: id(button) in active)
    monkeypatch.setattr(Cooperation, 'screenshot', lambda self, *a, **k: None)


def _detect(monkeypatch, *active):
    coop = _coop_obj()
    _activate(monkeypatch, *active)
    coop.get_cooperation_info()
    return [item[1] for item in coop.msg if item[0] == MSGType.cooperation]


def _wq(name):
    from tasks.WantedQuests.assets import WantedQuestsAssets
    return getattr(WantedQuestsAssets, name)


def _real_flag(index):
    from tasks.DailyAltAcc.assets import DailyAltAccAssets
    return getattr(DailyAltAccAssets, f'I_REAL_FLAG_{index}')


@pytest.mark.unit
def test_coop_detect_real_jade(monkeypatch):
    events = _detect(monkeypatch, _wq('I_WQ_INVITE_1'), _wq('I_WQ_COOPERATION_TYPE_JADE_1'), _real_flag(1))
    assert events == [{'type': 'jade', 'real': True, 'label': '现世勾协'}]


@pytest.mark.unit
def test_coop_detect_normal_jade(monkeypatch):
    events = _detect(monkeypatch, _wq('I_WQ_INVITE_1'), _wq('I_WQ_COOPERATION_TYPE_JADE_1'))
    assert events == [{'type': 'jade', 'real': False, 'label': '普通勾协'}]


@pytest.mark.unit
def test_coop_detect_real_sushi(monkeypatch):
    events = _detect(monkeypatch, _wq('I_WQ_INVITE_1'), _wq('I_WQ_COOPERATION_TYPE_SUSHI_1'), _real_flag(1))
    assert events == [{'type': 'sushi', 'real': True, 'label': '现世体协'}]


@pytest.mark.unit
def test_coop_detect_normal_sushi(monkeypatch):
    events = _detect(monkeypatch, _wq('I_WQ_INVITE_1'), _wq('I_WQ_COOPERATION_TYPE_SUSHI_1'))
    assert events == [{'type': 'sushi', 'real': False, 'label': '普通体协'}]


@pytest.mark.unit
def test_coop_detect_dog_food_maps_to_dog(monkeypatch):
    """DOG_FOOD 模板 → food_kind=dog → 狗粮协作（人工确认未标反）。"""
    events = _detect(monkeypatch, _wq('I_WQ_INVITE_1'), _wq('I_WQ_COOPERATION_TYPE_DOG_FOOD_1'))
    assert events == [{'type': 'food', 'real': False, 'food_kind': 'dog', 'label': '狗粮协作'}]


@pytest.mark.unit
def test_coop_detect_cat_food_maps_to_cat(monkeypatch):
    """CAT_FOOD 模板 → food_kind=cat → 猫粮协作（人工确认未标反）。"""
    events = _detect(monkeypatch, _wq('I_WQ_INVITE_1'), _wq('I_WQ_COOPERATION_TYPE_CAT_FOOD_1'))
    assert events == [{'type': 'food', 'real': False, 'food_kind': 'cat', 'label': '猫粮协作'}]


@pytest.mark.unit
def test_coop_detect_gold(monkeypatch):
    events = _detect(monkeypatch, _wq('I_WQ_INVITE_1'), _wq('I_WQ_COOPERATION_TYPE_GOLD_1'))
    assert events == [{'type': 'gold', 'real': False, 'label': '金币协作'}]


# =====================================================================
# 2. ProgressStore coop 持久化（tasks/MultiDailyAltAcc/progress.py）
# =====================================================================

_COOP_A = {'account': 'a@x.com', 'character': '角色A', 'svr': '两情相悦', 'type': 'jade', 'real': True, 'label': '现世勾协'}
_COOP_B = {'account': 'b@x.com', 'character': '角色B', 'svr': '两情相悦', 'type': 'food', 'real': False, 'food_kind': 'dog', 'label': '狗粮协作'}


def _store(tmp_path, config_name='oas1'):
    return ProgressStore(config_name, base_dir=tmp_path)


@pytest.mark.unit
def test_append_coop_saves_immediately(tmp_path):
    store = _store(tmp_path)
    store.ensure_phase(FLAGS_A, '20260817-0605')
    store.append_coop(_COOP_A)
    # 重新实例化（模拟 OAS/Python 重启）后仍能读到
    reloaded = _store(tmp_path)
    reloaded.ensure_phase(FLAGS_A, '20260817-0605')
    assert reloaded.load_coops() == [_COOP_A]


@pytest.mark.unit
def test_load_coops_empty_by_default(tmp_path):
    store = _store(tmp_path)
    store.ensure_phase(FLAGS_A, '20260817-0605')
    assert store.load_coops() == []


@pytest.mark.unit
def test_coop_survives_phase_resume(tmp_path):
    """phase_failed / 10 分钟接续：同一 phase_flags 接续，coop 保留。"""
    store = _store(tmp_path)
    store.ensure_phase(FLAGS_A, '20260817-0605')
    store.append_coop(_COOP_A)
    resumed = _store(tmp_path)
    assert resumed.ensure_phase(FLAGS_A, '20260817-0605') is False  # 接续
    assert resumed.load_coops() == [_COOP_A]


@pytest.mark.unit
def test_coop_cleared_on_clear(tmp_path):
    store = _store(tmp_path)
    store.ensure_phase(FLAGS_A, '20260817-0605')
    store.append_coop(_COOP_A)
    store.clear()
    assert store.load_coops() == []
    # 成功后下一轮是全新文件，coop 为空
    nxt = _store(tmp_path)
    nxt.ensure_phase(FLAGS_A, '20260817-1805')
    assert nxt.load_coops() == []


@pytest.mark.unit
def test_coop_not_inherited_new_phase(tmp_path):
    """新阶段（phase_flags 变化 → 重建）不继承旧 coop。"""
    store = _store(tmp_path)
    store.ensure_phase(FLAGS_A, '20260817-0605')
    store.append_coop(_COOP_A)
    nxt = _store(tmp_path)
    assert nxt.ensure_phase(FLAGS_B, '20260817-1805') is True  # 重建
    assert nxt.load_coops() == []


@pytest.mark.unit
def test_old_coop_archived_on_rebuild(tmp_path):
    """重建前兜底：旧 coop 被归档到独立文件，避免覆盖丢失。"""
    import json

    store = _store(tmp_path)
    store.ensure_phase(FLAGS_A, '20260817-0605')
    store.append_coop(_COOP_A)
    store.append_coop(_COOP_B)
    nxt = _store(tmp_path)
    assert nxt.ensure_phase(FLAGS_B, '20260817-1805') is True
    archive = tmp_path / 'multi_daily_coop_archive_oas1.json'
    assert archive.exists()
    records = json.loads(archive.read_text(encoding='utf-8'))
    assert isinstance(records, list) and len(records) == 1
    assert records[0]['coops'] == [_COOP_A, _COOP_B]


@pytest.mark.unit
def test_no_archive_when_coop_empty_on_rebuild(tmp_path):
    store = _store(tmp_path)
    store.ensure_phase(FLAGS_A, '20260817-0605')
    nxt = _store(tmp_path)
    nxt.ensure_phase(FLAGS_B, '20260817-1805')
    assert not (tmp_path / 'multi_daily_coop_archive_oas1.json').exists()


@pytest.mark.unit
def test_three_configs_coop_isolated(tmp_path):
    """小号1/2/3 三份 progress 互不污染。"""
    for name in ('小号1', '小号2', '小号3'):
        _store(tmp_path, name).ensure_phase(FLAGS_A, '20260817-0605')

    s1 = _store(tmp_path, '小号1')
    s1.ensure_phase(FLAGS_A, '20260817-0605')
    s1.append_coop({**_COOP_A, 'character': '小1角色'})
    s2 = _store(tmp_path, '小号2')
    s2.ensure_phase(FLAGS_A, '20260817-0605')
    s2.append_coop({**_COOP_B, 'character': '小2角色'})

    r1 = _store(tmp_path, '小号1')
    r1.ensure_phase(FLAGS_A, '20260817-0605')
    assert r1.load_coops()[0]['character'] == '小1角色'
    r2 = _store(tmp_path, '小号2')
    r2.ensure_phase(FLAGS_A, '20260817-0605')
    assert r2.load_coops()[0]['character'] == '小2角色'
    r3 = _store(tmp_path, '小号3')
    r3.ensure_phase(FLAGS_A, '20260817-0605')
    assert r3.load_coops() == []
    # 文件名天然隔离
    assert (tmp_path / 'multi_daily_progress_小号1.json').exists()
    assert (tmp_path / 'multi_daily_progress_小号2.json').exists()
    assert (tmp_path / 'multi_daily_progress_小号3.json').exists()


# =====================================================================
# 3. 汇总文本格式（ScriptTask._build_summary_content）
# =====================================================================

def _sum(coops, at=None):
    return ScriptTask._build_summary_content(coops, at or datetime(2026, 8, 17, 20, 30))


@pytest.mark.unit
def test_summary_seven_categories_in_order():
    coops = [
        {'account': 'a', 'character': '角色A', 'svr': '常世之国', 'type': 'jade', 'real': True},
        {'account': 'b', 'character': '角色B', 'svr': '常世之国', 'type': 'sushi', 'real': True},
        {'account': 'c', 'character': '角色C', 'svr': '常世之国', 'type': 'jade', 'real': False},
        {'account': 'd', 'character': '角色D', 'svr': '常世之国', 'type': 'sushi', 'real': False},
        {'account': 'e', 'character': '角色E', 'svr': '常世之国', 'type': 'food', 'food_kind': 'dog'},
        {'account': 'f', 'character': '角色F', 'svr': '常世之国', 'type': 'food', 'food_kind': 'cat'},
        {'account': 'g', 'character': '角色G', 'svr': '常世之国', 'type': 'gold'},
    ]
    text = _sum(coops)
    assert text.index('现世勾协') < text.index('现世体协') < text.index('普通勾协') \
        < text.index('普通体协') < text.index('狗粮协作') < text.index('猫粮协作') < text.index('金币协作')
    assert '发现协作角色：7' in text
    assert '协作任务数量：7' in text


@pytest.mark.unit
def test_summary_empty_category_hidden():
    coops = [
        {'account': 'a', 'character': '角色A', 'svr': '常世之国', 'type': 'gold'},
    ]
    text = _sum(coops)
    assert '现世勾协' not in text
    assert '金币协作' in text


@pytest.mark.unit
def test_summary_same_role_same_category_x2():
    coops = [
        {'account': 'a', 'character': '角色A', 'svr': '常世之国', 'type': 'jade', 'real': False},
        {'account': 'a', 'character': '角色A', 'svr': '常世之国', 'type': 'jade', 'real': False},
    ]
    text = _sum(coops)
    assert '• 角色A（常世之国） ×2' in text
    assert text.count('角色A') == 1  # 不出现两行


@pytest.mark.unit
def test_summary_same_role_diff_category():
    coops = [
        {'account': 'a', 'character': '角色A', 'svr': '常世之国', 'type': 'jade', 'real': False},
        {'account': 'a', 'character': '角色A', 'svr': '常世之国', 'type': 'food', 'food_kind': 'cat'},
    ]
    text = _sum(coops)
    assert '普通勾协（1）' in text
    assert '猫粮协作（1）' in text
    assert text.count('角色A') == 2  # 不同类别分别进入


@pytest.mark.unit
def test_summary_svr_included_and_role_count_dedup():
    coops = [
        {'account': 'a', 'character': '角色A', 'svr': '常世之国', 'type': 'jade', 'real': False},
        {'account': 'a', 'character': '角色A', 'svr': '常世之国', 'type': 'jade', 'real': False},
        {'account': 'b', 'character': '角色B', 'svr': '两情相悦', 'type': 'gold'},
    ]
    text = _sum(coops)
    # 角色去重：A（+区服）、B
    assert '发现协作角色：2' in text
    assert '协作任务数量：3' in text
    assert '（常世之国）' in text
    assert '（两情相悦）' in text


@pytest.mark.unit
def test_summary_empty_round():
    text = _sum([])
    assert '发现协作角色：0' in text
    assert '协作任务数量：0' in text
    assert '本轮未发现协作任务。' in text


@pytest.mark.unit
def test_summary_roles_without_coop_hidden():
    """只显示有协作的角色（coops 里根本没有的角色不会出现）。"""
    coops = [{'account': 'a', 'character': '角色A', 'svr': '常世之国', 'type': 'gold'}]
    text = _sum(coops)
    assert '角色B' not in text


# =====================================================================
# 4. 推送时机
# =====================================================================

@pytest.mark.unit
def test_process_message_cooperation_persists_without_push(tmp_path):
    task = _task_with_progress(tmp_path)
    event = {'type': 'jade', 'real': True, 'label': '现世勾协'}
    ret = task._process_message_type(MSGType.cooperation, event, _account())
    assert ret is False
    assert task.config.notifier.pushes == []  # 不再立即推送
    coops = task._progress.load_coops()
    assert len(coops) == 1
    assert coops[0]['character'] == '小号一'
    assert coops[0]['svr'] == '两情相悦'
    assert coops[0]['type'] == 'jade'
    assert coops[0]['real'] is True


@pytest.mark.unit
def test_process_message_legacy_string_coop_ignored(tmp_path):
    task = _task_with_progress(tmp_path)
    ret = task._process_message_type(MSGType.cooperation, '发现现世勾协', _account())
    assert ret is False
    assert task.config.notifier.pushes == []
    assert task._progress.load_coops() == []


@pytest.mark.unit
def test_notify_success_pushes_once(tmp_path):
    task = _task_with_progress(tmp_path)
    task._progress.append_coop(_COOP_A)
    task._progress.append_coop(_COOP_B)
    task._notify_daily_completion()
    assert len(task.config.notifier.pushes) == 1
    content = task.config.notifier.pushes[0]['content']
    assert '狗粮协作' in content


@pytest.mark.unit
def test_notify_empty_round_pushes_once(tmp_path):
    task = _task_with_progress(tmp_path)
    task._notify_daily_completion()
    assert len(task.config.notifier.pushes) == 1
    assert '本轮未发现协作任务。' in task.config.notifier.pushes[0]['content']


@pytest.mark.unit
def test_notify_failure_does_not_break(tmp_path):
    notifier = _FakeNotifier()
    notifier.fail = True
    task = _task_with_progress(tmp_path, notifier=notifier)
    task._progress.append_coop(_COOP_A)
    # 通知失败只记日志，不抛异常、不影响整轮收尾
    task._notify_daily_completion()
    assert notifier.pushes == []


# ---- coop_notified：本轮已通知持久化标记（消除「push 成功、clear 前崩溃」重复窗口） ----

@pytest.mark.unit
def test_notify_first_completion_pushes_once_and_marks(tmp_path):
    """首次整轮完成：push 1 次，成功后 coop_notified=true。"""
    task = _task_with_progress(tmp_path)
    task._progress.append_coop(_COOP_A)
    task._notify_daily_completion()
    assert len(task.config.notifier.pushes) == 1
    assert task._progress.is_coop_notified() is True


@pytest.mark.unit
def test_notify_skips_when_already_notified(tmp_path):
    """coop_notified=true 后再次进入完成分支（如崩溃重启接续）：push 0 次。"""
    task = _task_with_progress(tmp_path)
    task._progress.append_coop(_COOP_A)
    task._notify_daily_completion()
    assert len(task.config.notifier.pushes) == 1
    # 模拟重启后同一 progress 再次进入完成分支
    task._notify_daily_completion()
    assert len(task.config.notifier.pushes) == 1  # 不重复推送


@pytest.mark.unit
def test_notify_failure_does_not_mark_but_keeps_round(tmp_path):
    """PushPlus 失败：不写 coop_notified，但不抛异常、主任务仍可继续收尾。"""
    notifier = _FakeNotifier()
    notifier.fail = True
    task = _task_with_progress(tmp_path, notifier=notifier)
    task._progress.append_coop(_COOP_A)
    task._notify_daily_completion()
    assert task._progress.is_coop_notified() is False  # 未标记
    # 主任务收尾（next_run/clear）不受影响：clear 仍可正常执行
    task._progress.clear()
    assert not task._progress.path.exists()


@pytest.mark.unit
def test_notified_marker_cleared_on_clear(tmp_path):
    """clear 后标记自然消失，下一轮重新 ensure_phase 无 notified 标记。"""
    task = _task_with_progress(tmp_path)
    task._progress.append_coop(_COOP_A)
    task._notify_daily_completion()
    assert task._progress.is_coop_notified() is True
    task._progress.clear()
    nxt = ProgressStore('oas1', base_dir=tmp_path)
    nxt.ensure_phase(FLAGS_A, '20260817-1805')
    assert nxt.is_coop_notified() is False
    assert nxt.load_coops() == []


@pytest.mark.unit
def test_run_success_pushes_once_before_next_run(tmp_path, monkeypatch):
    """整轮成功：推送 1 条协作汇总，且推送发生在 next_run(success=True) 之前。"""
    from module.exception import TaskEnd

    calls = []
    notifier = _FakeNotifier(call_log=calls)
    task = _make_run_task(tmp_path, monkeypatch, notifier, seed_coops=[_COOP_A])
    monkeypatch.setattr(task, '_get_sorted_accounts', lambda: [])
    monkeypatch.setattr(task, '_mark_task_start', lambda *a, **k: None)
    monkeypatch.setattr(task, '_mark_task_completed', lambda *a, **k: None)
    monkeypatch.setattr(task, '_update_task_returngift_enable', lambda *a, **k: None)
    monkeypatch.setattr(task, 'emit_stat', lambda *a, **k: None)
    monkeypatch.setattr(task, 'next_run', lambda task_name, **kw: calls.append(('next_run', kw.get('success'))))
    monkeypatch.setattr(task, '_coordinated_shutdown_system', lambda *a, **k: calls.append(('shutdown',)))

    with pytest.raises(TaskEnd):
        task.run()

    # 推送 1 条协作汇总；顺序：push 在 next_run 之前；shutdown 未触发
    assert len(notifier.pushes) == 1
    assert calls == [('push',), ('next_run', True)]


@pytest.mark.unit
def test_run_phase_failed_pushes_zero(tmp_path, monkeypatch):
    """phase_failed=True：绝不推送协作汇总。"""
    from module.exception import TaskEnd

    notifier = _FakeNotifier()
    task = _make_run_task(tmp_path, monkeypatch, notifier, seed_coops=[_COOP_A])
    calls = []
    acc = _account()
    monkeypatch.setattr(task, '_get_sorted_accounts', lambda: [acc])
    monkeypatch.setattr(task, '_process_single_account', lambda ai: False)  # 连续失败
    monkeypatch.setattr(task, '_mark_task_start', lambda *a, **k: None)
    monkeypatch.setattr(task, '_mark_task_completed', lambda *a, **k: None)
    monkeypatch.setattr(task, '_update_task_returngift_enable', lambda *a, **k: None)
    monkeypatch.setattr(task, 'emit_stat', lambda *a, **k: None)
    monkeypatch.setattr(task, 'next_run', lambda task_name, **kw: calls.append(('next_run', kw.get('success'))))
    monkeypatch.setattr(task, '_coordinated_shutdown_system', lambda *a, **k: calls.append(('shutdown',)))

    with pytest.raises(TaskEnd):
        task.run()

    assert notifier.pushes == []
    assert calls == [('next_run', False)]


@pytest.mark.unit
def test_run_mid_round_exception_pushes_zero_summary(tmp_path, monkeypatch):
    """中途账号异常：不发送协作汇总（其余 ERROR 通知行为保持）。"""
    from module.exception import TaskEnd

    notifier = _FakeNotifier()
    task = _make_run_task(tmp_path, monkeypatch, notifier, seed_coops=[_COOP_A])
    calls = []
    acc = _account()

    def boom(ai):
        raise RuntimeError('boom')

    monkeypatch.setattr(task, '_get_sorted_accounts', lambda: [acc])
    monkeypatch.setattr(task, '_process_single_account', boom)
    monkeypatch.setattr(task, '_mark_task_start', lambda *a, **k: None)
    monkeypatch.setattr(task, '_mark_task_completed', lambda *a, **k: None)
    monkeypatch.setattr(task, '_update_task_returngift_enable', lambda *a, **k: None)
    monkeypatch.setattr(task, 'emit_stat', lambda *a, **k: None)
    monkeypatch.setattr(task, 'next_run', lambda task_name, **kw: calls.append(('next_run', kw.get('success'))))
    monkeypatch.setattr(task, '_coordinated_shutdown_system', lambda *a, **k: calls.append(('shutdown',)))
    import script
    monkeypatch.setattr(script.Script, 'save_error_log', lambda *a, **k: None)

    with pytest.raises(TaskEnd):
        task.run()

    # 没有任何协作汇总推送
    assert not any(p.get('title') == SUMMARY_TITLE for p in notifier.pushes)
    # 真正的错误通知仍发送（run() 的 except Exception 分支，title=ERROR）
    assert any(p.get('title') == 'ERROR' for p in notifier.pushes)
    assert calls == [('next_run', False)]


@pytest.mark.unit
def test_resume_round_does_not_push_half(tmp_path):
    """模拟 OAS 重启后接续：只收集落盘，不触发汇总（不推半份）。"""
    notifier = _FakeNotifier()
    task = _task_with_progress(tmp_path, notifier=notifier)
    # 模拟重启后重新实例化并从磁盘恢复
    task._progress = ProgressStore('oas1', base_dir=tmp_path)
    task._progress.ensure_phase(FLAGS_A, '20260817-0605')
    # 继续收集（十分钟接续/OAS 重启）
    task._process_message_type(MSGType.cooperation, {'type': 'gold', 'real': False, 'label': '金币协作'}, _account())
    assert task._progress.load_coops()
    # 未走整轮完成，不得推送
    assert notifier.pushes == []


# =====================================================================
# 5. 其他通知保持
# =====================================================================

@pytest.mark.unit
def test_mshop_persists_instead_of_pushing(tmp_path):
    """神秘商店改与协作同策：落盘到本轮进度，不再「发现一条立即推送」。

    即时推送在多账号场景下会按账号数量刷屏；现在统一进整轮汇总一条。
    """
    task = _task_with_progress(tmp_path)
    ret = task._process_message_type(MSGType.mshop, {
        'goods': '大蛇的逆鳞', 'coin': '金币', 'price': 82500,
        'slot': 2, 'label': '发现82500金币大蛇的逆鳞',
    }, _account())
    assert ret is False
    assert task.config.notifier.pushes == []
    saved = task._progress.load_mshops()
    assert len(saved) == 1
    assert saved[0]['goods'] == '大蛇的逆鳞'
    assert saved[0]['price'] == 82500
    assert saved[0]['coin'] == '金币'


@pytest.mark.unit
def test_mshop_accepts_legacy_string_event(tmp_path):
    """旧格式纯字符串事件也要落盘（整串进 label），跨版本不丢记录。"""
    task = _task_with_progress(tmp_path)
    ret = task._process_message_type(MSGType.mshop, '发现1000金币 勾玉', _account())
    assert ret is False
    assert task.config.notifier.pushes == []
    saved = task._progress.load_mshops()
    assert len(saved) == 1
    assert saved[0]['label'] == '发现1000金币 勾玉'
    assert saved[0]['price'] is None


@pytest.mark.unit
def test_neterror_returns_true_and_no_push(tmp_path):
    task = _task_with_progress(tmp_path)
    ret = task._process_message_type(MSGType.neterror, '网络错误', _account())
    assert ret is True
    assert task.config.notifier.pushes == []


# =====================================================================
# 7. Notifier 标题前缀（module/notify/notify.py）
# =====================================================================

@pytest.mark.unit
def test_notifier_default_prefix_keeps_config_space(monkeypatch):
    """默认（不传 skip_config_prefix）：标题 = config_name + 空格 + title，全局行为不变。"""
    from types import SimpleNamespace

    from module.notify.notify import Notifier

    sent = []
    fake = SimpleNamespace(params={"required": []})

    def fake_notify(**kw):
        sent.append(kw)
        return SimpleNamespace(status_code=200)

    fake.notify = fake_notify
    monkeypatch.setattr("module.notify.notify.get_notifier", lambda name: fake)

    n = Notifier("provider: pushplus", enable=True)
    n.config_name = "小号1"
    assert n.push(title="ERROR", content="x") is True
    assert sent[0]["title"] == "小号1 ERROR"  # 带空格，与既有行为一致


@pytest.mark.unit
def test_notifier_skip_config_prefix_keeps_raw_title(monkeypatch):
    """skip_config_prefix=True：标题原样（协作汇总期望「小号1｜多账号日常完成」）。"""
    from types import SimpleNamespace

    from module.notify.notify import Notifier

    sent = []
    fake = SimpleNamespace(params={"required": []})

    def fake_notify(**kw):
        sent.append(kw)
        return SimpleNamespace(status_code=200)

    fake.notify = fake_notify
    monkeypatch.setattr("module.notify.notify.get_notifier", lambda name: fake)

    n = Notifier("provider: pushplus", enable=True)
    n.config_name = "小号1"
    assert n.push(title="小号1｜多账号日常完成", content="x", skip_config_prefix=True) is True
    assert sent[0]["title"] == "小号1｜多账号日常完成"
    # skip_config_prefix 是控制参数，不应透传给 provider
    assert "skip_config_prefix" not in sent[0]


# =====================================================================
# 8. 平台/账号展示 + TaskEnd「任务提醒」抑制
# =====================================================================

@pytest.mark.unit
def test_should_notify_task_end_suppresses_multidaily():
    """MultiDailyAltAcc 已从 TASK_END_NOTIFY_LIST 移除：通用「任务提醒」不再推，
    完成通知完全由任务内 _notify_daily_completion 负责；其他任务保留。"""
    from script import Script

    s = object.__new__(Script)
    assert Script._should_notify_task_end(s, 'MultiDailyAltAcc') is False
    # 其他在通知列表的任务仍保留完成提醒
    assert Script._should_notify_task_end(s, 'Orochi') is True


def _task_with_coop_cfg(tmp_path, coop_enable=True, notifier=None, config_name='oas1'):
    """构造带 total_cooperation_enable 开关配置 + 真实 ProgressStore 的任务。"""
    cfg = SimpleNamespace(total_cooperation_enable=coop_enable)
    task = _make_task()
    task.config = _fake_config(notifier, config_name=config_name,
                               multi_daily_alt_acc=SimpleNamespace(multi_daily_alt_acc_config=cfg))
    task.daily_conf = task.config.multi_daily_alt_acc
    task._progress = ProgressStore(config_name, base_dir=tmp_path)
    task._progress.ensure_phase(FLAGS_A, '20260817-0605')
    return task


def _task_with_toggles(tmp_path, coop_enable=True, mshop_enable=True,
                       notifier=None, config_name='oas1'):
    """构造同时带协作与神秘商店总开关的任务（真实 ProgressStore）。"""
    cfg = SimpleNamespace(total_cooperation_enable=coop_enable,
                          total_mysteryshop_enable=mshop_enable)
    task = _make_task()
    task.config = _fake_config(notifier, config_name=config_name,
                               multi_daily_alt_acc=SimpleNamespace(multi_daily_alt_acc_config=cfg))
    task.daily_conf = task.config.multi_daily_alt_acc
    task._progress = ProgressStore(config_name, base_dir=tmp_path)
    task._progress.ensure_phase(FLAGS_A, '20260817-0605')
    return task


_MSHOP_A = {
    'account': 'a@example.com', 'character': '角色A', 'svr': '一区',
    'apple_or_android': True, 'goods': '大蛇的逆鳞', 'coin': '金币',
    'price': 82500, 'label': '发现82500金币大蛇的逆鳞',
}


@pytest.mark.unit
def test_summary_appends_mshop_section(tmp_path):
    """协作与商店都有记录：一条汇总里同时出现协作类别与神秘商店段落。"""
    task = _task_with_toggles(tmp_path)
    task._progress.append_coop(_COOP_A)
    task._progress.append_mshop(_MSHOP_A)
    task._notify_daily_completion()
    assert len(task.config.notifier.pushes) == 1
    content = task.config.notifier.pushes[0]['content']
    assert '神秘商店（1）' in content
    assert '大蛇的逆鳞 82500金币' in content
    assert '协作任务数量：1' in content
    assert task._progress.is_coop_notified() is True


@pytest.mark.unit
def test_summary_sends_mshop_only_when_coop_toggle_off(tmp_path):
    """协作关 + 商店有记录：仍发汇总，但不出协作类别段落。"""
    task = _task_with_toggles(tmp_path, coop_enable=False)
    task._progress.append_coop(_COOP_A)
    task._progress.append_mshop(_MSHOP_A)
    task._notify_daily_completion()
    assert len(task.config.notifier.pushes) == 1
    content = task.config.notifier.pushes[0]['content']
    assert '神秘商店（1）' in content
    # 协作关闭 -> 计数归零且不出类别段落
    assert '协作任务数量：0' in content
    assert '现世勾协' not in content


@pytest.mark.unit
def test_summary_skipped_when_coop_off_and_no_mshop(tmp_path):
    """协作关 + 商店无记录：不发汇总，改发普通完成推送（含本轮执行项目）。"""
    task = _task_with_toggles(tmp_path, coop_enable=False)
    task._progress.append_coop(_COOP_A)
    task._notify_daily_completion()
    assert len(task.config.notifier.pushes) == 1
    push = task.config.notifier.pushes[0]
    assert push['title'] == SUMMARY_TITLE
    assert '本轮执行项目' in push['content']
    assert '本轮未发现协作任务' not in push['content']
    assert task._progress.is_coop_notified() is True


@pytest.mark.unit
def test_summary_omits_mshop_when_toggle_off(tmp_path):
    """商店总开关关闭：即便有落盘记录也不出商店段落。"""
    task = _task_with_toggles(tmp_path, mshop_enable=False)
    task._progress.append_mshop(_MSHOP_A)
    task._notify_daily_completion()
    assert len(task.config.notifier.pushes) == 1
    content = task.config.notifier.pushes[0]['content']
    assert '神秘商店' not in content
    assert '本轮未发现协作任务。' in content


@pytest.mark.unit
def test_summary_unchanged_when_no_mshop(tmp_path):
    """商店无记录时，汇总输出必须与改造前逐字一致（协作行为零回归）。"""
    from tasks.MultiDailyAltAcc.script_task import ScriptTask
    at = datetime(2026, 8, 20, 3, 12, 5)
    coops = [_COOP_A, _COOP_B]
    with_empty = ScriptTask._build_summary_content(coops, completed_at=at, mshops=[])
    without_arg = ScriptTask._build_summary_content(coops, completed_at=at)
    assert with_empty == without_arg
    assert '神秘商店' not in with_empty


@pytest.mark.unit
def test_archive_covers_mshop_records(tmp_path):
    """重建前归档要带上商店记录，否则换阶段时商店命中会静默丢失。"""
    store = ProgressStore('oas1', base_dir=tmp_path)
    store.ensure_phase(FLAGS_A, '20260817-0605')
    store.append_mshop(_MSHOP_A)
    n = store.archive_pending_coops()
    assert n == 1
    archive = tmp_path / 'multi_daily_coop_archive_oas1.json'
    assert archive.exists()
    data = json.loads(archive.read_text(encoding='utf-8'))
    assert data[-1]['mshops'][0]['goods'] == '大蛇的逆鳞'


@pytest.mark.unit
def test_notify_skipped_when_coop_toggle_off(tmp_path):
    """寻找协作关闭：不发协作汇总，改发普通完成推送并标记已通知。
    该构造器只带协作/商店两个开关，其余 total_* 缺失 → 项目行为空。"""
    task = _task_with_coop_cfg(tmp_path, coop_enable=False)
    task._progress.append_coop(_COOP_A)
    task._notify_daily_completion()
    assert len(task.config.notifier.pushes) == 1
    push = task.config.notifier.pushes[0]
    assert push['title'] == SUMMARY_TITLE
    # 无任何 total_* 开关可列 → 不出项目行，但完成通知本身不丢
    assert '本轮执行项目' not in push['content']
    assert '本轮未发现协作任务' not in push['content']
    assert task._progress.is_coop_notified() is True


@pytest.mark.unit
def test_notify_empty_round_when_toggle_on(tmp_path):
    """寻找协作开启 + coop=0：仍发送 0 角色/0 任务的空轮汇总。"""
    task = _task_with_coop_cfg(tmp_path, coop_enable=True)
    task._notify_daily_completion()
    assert len(task.config.notifier.pushes) == 1
    content = task.config.notifier.pushes[0]['content']
    assert '发现协作角色：0' in content
    assert '协作任务数量：0' in content
    assert '本轮未发现协作任务。' in content


@pytest.mark.unit
def test_notify_with_coop_when_toggle_on(tmp_path):
    """寻找协作开启 + coop>0：正常发送新协作汇总并标记已通知。"""
    task = _task_with_coop_cfg(tmp_path, coop_enable=True)
    task._progress.append_coop(_COOP_B)
    task._notify_daily_completion()
    assert len(task.config.notifier.pushes) == 1
    assert '协作任务数量：1' in task.config.notifier.pushes[0]['content']
    assert task._progress.is_coop_notified() is True


@pytest.mark.unit
def test_task_end_notify_list_excludes_multidaily():
    """MultiDailyAltAcc 不在 TASK_END_NOTIFY_LIST：完成通知由任务内统一负责，
    通用「任务提醒」不因寻找协作开关变化而改变（原 _multidaily_coop_notify_enabled
    依赖收尾时已被改写的开关，存在同心轮两头皆空的时序漏洞，已删除）。"""
    from script import Script

    assert 'MultiDailyAltAcc' not in Script.TASK_END_NOTIFY_LIST
    # 其余任务保持原有完成提醒
    assert 'Orochi' in Script.TASK_END_NOTIFY_LIST
    assert not hasattr(Script, '_multidaily_coop_notify_enabled')


@pytest.mark.unit
def test_task_end_toggle_three_configs_isolated(tmp_path):
    """三个 config 的寻找协作开关相互独立：开发汇总，关发普通完成推送。"""
    on = _task_with_coop_cfg(tmp_path, coop_enable=True, config_name='小号1')
    off1 = _task_with_coop_cfg(tmp_path, coop_enable=False, config_name='小号2')
    off2 = _task_with_coop_cfg(tmp_path, coop_enable=False, config_name='小号3')
    for t in (on, off1, off2):
        t._progress.append_coop(_COOP_A)
    on._notify_daily_completion()
    off1._notify_daily_completion()
    off2._notify_daily_completion()
    assert len(on.config.notifier.pushes) == 1
    assert '协作任务数量：1' in on.config.notifier.pushes[0]['content']
    # 协作关的两个实例改发普通完成推送，各一条（该构造器无其他 total_* 开关，
    # 项目行为空，验证标题即可）
    assert len(off1.config.notifier.pushes) == 1
    assert off1.config.notifier.pushes[0]['title'] == '小号2｜多账号日常完成'
    assert len(off2.config.notifier.pushes) == 1
    assert off2.config.notifier.pushes[0]['title'] == '小号3｜多账号日常完成'


@pytest.mark.unit
def test_run_coop_off_error_still_notifies(tmp_path, monkeypatch):
    """寻找协作关闭 + 账号异常：不发协作汇总，原 ERROR 通知正常。"""
    from module.exception import TaskEnd

    notifier = _FakeNotifier()
    task = _make_run_task(tmp_path, monkeypatch, notifier,
                          phase_overrides={'total_cooperation_enable': False},
                          seed_coops=[_COOP_A])
    calls = []
    acc = _account()

    def boom(ai):
        raise RuntimeError('boom')

    monkeypatch.setattr(task, '_get_sorted_accounts', lambda: [acc])
    monkeypatch.setattr(task, '_process_single_account', boom)
    monkeypatch.setattr(task, '_mark_task_start', lambda *a, **k: None)
    monkeypatch.setattr(task, '_mark_task_completed', lambda *a, **k: None)
    monkeypatch.setattr(task, '_update_task_returngift_enable', lambda *a, **k: None)
    monkeypatch.setattr(task, 'emit_stat', lambda *a, **k: None)
    monkeypatch.setattr(task, 'next_run', lambda task_name, **kw: calls.append(('next_run', kw.get('success'))))
    monkeypatch.setattr(task, '_coordinated_shutdown_system', lambda *a, **k: calls.append(('shutdown',)))
    import script
    monkeypatch.setattr(script.Script, 'save_error_log', lambda *a, **k: None)

    with pytest.raises(TaskEnd):
        task.run()

    # 寻找协作关闭：无任何协作汇总
    assert not any(p.get('title') == SUMMARY_TITLE for p in notifier.pushes)
    # 原 ERROR 通知不受开关影响
    assert any(p.get('title') == 'ERROR' for p in notifier.pushes)
    assert calls == [('next_run', False)]


@pytest.mark.unit
def test_summary_platform_android_and_ios():
    coops = [
        {'type': 'jade', 'real': False, 'character': '角色A', 'svr': '常世之国', 'apple_or_android': True},
        {'type': 'jade', 'real': False, 'character': '角色B', 'svr': '常世之国', 'apple_or_android': False},
    ]
    text = ScriptTask._build_summary_content(coops, datetime(2026, 8, 17, 20, 30))
    assert '• 角色A（常世之国｜安卓）' in text
    assert '• 角色B（常世之国｜iOS）' in text


@pytest.mark.unit
def test_summary_platform_missing_not_shown():
    """旧记录无 apple_or_android 字段时不显示平台。"""
    coops = [{'type': 'gold', 'character': '角色A', 'svr': '常世之国'}]
    text = ScriptTask._build_summary_content(coops, datetime(2026, 8, 17, 20, 30))
    assert '• 角色A（常世之国）' in text
    assert '安卓' not in text
    assert 'iOS' not in text


@pytest.mark.unit
def test_summary_show_account_switch():
    """show_account 开关：关闭不显示账号，开启显示 account 原值。"""
    coops = [{'type': 'gold', 'character': '角色A', 'svr': '常世之国', 'apple_or_android': True,
              'account': 'user@163.com'}]
    text_off = ScriptTask._build_summary_content(coops, datetime(2026, 8, 17, 20, 30))
    text_on = ScriptTask._build_summary_content(
        coops, datetime(2026, 8, 17, 20, 30), show_account=True)
    assert 'user@163.com' not in text_off
    assert '• 角色A（常世之国｜安卓｜user@163.com）' in text_on


# =====================================================================
# 8.5 普通完成推送（协作关且无商店记录时，列出本轮执行项目）
# =====================================================================

def _plain_task(tmp_path, coop_enable=False, **flag_overrides):
    """构造带完整 total_* 开关的任务（真实 ProgressStore），供普通推送测试。

    tmp_path 传子目录可实现多实例隔离（coop_notified 标记按配置名落盘）。"""
    flags = {'total_cooperation_enable': coop_enable,
             'total_mysteryshop_enable': False}
    flags.update(flag_overrides)
    cfg = SimpleNamespace(**flags)
    task = _make_task()
    task.config = _fake_config(_FakeNotifier(), config_name='oas1',
                               multi_daily_alt_acc=SimpleNamespace(multi_daily_alt_acc_config=cfg))
    task.daily_conf = task.config.multi_daily_alt_acc
    task._progress = ProgressStore('oas1', base_dir=tmp_path)
    task._progress.ensure_phase(FLAGS_A, '20260817-0605')
    return task


@pytest.mark.unit
def test_plain_push_lists_enabled_tasks(tmp_path):
    """普通推送列出 total_* 开启的项目（回礼轮不受 plan 过滤）。"""
    task = _plain_task(tmp_path, total_returngift_enable=True, total_mail_enable=True)
    # 回礼轮：phase 为 None，全部按开关列出
    task._normal_plan_phase = None
    task._notify_daily_completion()
    assert len(task.config.notifier.pushes) == 1
    push = task.config.notifier.pushes[0]
    assert push['title'] == SUMMARY_TITLE
    assert push.get('skip_config_prefix') is True
    assert '本轮执行项目：邮件、回礼' in push['content']
    assert task._progress.is_coop_notified() is True


@pytest.mark.unit
def test_plain_push_respects_alliedteam_round(tmp_path):
    """同心战斗轮：只列同心战斗相关项（协作/邮件等开关均关）。"""
    task = _plain_task(tmp_path,
                       total_alliedteam_battle_enable=True,
                       total_alliedteam_ap_enable=True,
                       total_mail_enable=False, total_courtyard_enable=False)
    task._normal_plan_phase = None
    task._notify_daily_completion()
    content = task.config.notifier.pushes[0]['content']
    assert '本轮执行项目：同心战斗、同心体力' in content


@pytest.mark.unit
def test_plain_push_morning_plan_filters_items(tmp_path):
    """普通早轮：7 个 plan 键按 task_plan 阶段过滤（默认早晨 courtyard 关）。"""
    task = _plain_task(tmp_path, total_courtyard_enable=True, total_mail_enable=True,
                       total_kekkaiActivation_enable=True, total_KekkaiUtilize_enable=True,
                       total_donatejade_enable=True)
    task._normal_plan_phase = 'morning'
    # 未预载 plan 时 _get_task_plan 会 load 默认文件，早晨 courtyard=False
    task._notify_daily_completion()
    content = task.config.notifier.pushes[0]['content']
    assert '庭院事务' not in content
    for label in ('邮件', '捐勾', '挂卡', '蹭卡'):
        assert label in content


@pytest.mark.unit
def test_plain_push_afternoon_plan_filters_items(tmp_path):
    """普通下午轮：默认 afternoon 同心体力关，不列入。"""
    task = _plain_task(tmp_path, total_alliedteam_ap_enable=True, total_mail_enable=True)
    task._normal_plan_phase = 'afternoon'
    task._notify_daily_completion()
    content = task.config.notifier.pushes[0]['content']
    assert '同心体力' not in content
    assert '邮件' in content


@pytest.mark.unit
def test_plain_push_tree_planting_labels(tmp_path):
    """种树三值开关：1=买花、2=买花捐树、0=不列。两个实例各自独立 ProgressStore。"""
    t1 = _plain_task(tmp_path / 'a', total_tree_planting_enable=1)
    t1._normal_plan_phase = None
    t1._notify_daily_completion()
    assert '本轮执行项目：买花' in t1.config.notifier.pushes[0]['content']

    t2 = _plain_task(tmp_path / 'b', total_tree_planting_enable=2)
    t2._normal_plan_phase = None
    t2._notify_daily_completion()
    assert '本轮执行项目：买花捐树' in t2.config.notifier.pushes[0]['content']


@pytest.mark.unit
def test_plain_push_idempotent_with_coop_notified(tmp_path):
    """普通推送与汇总共用 coop_notified 标记：已通知后再次进入完成分支不重发。"""
    task = _plain_task(tmp_path, total_mail_enable=True)
    task._normal_plan_phase = None
    task._notify_daily_completion()
    assert len(task.config.notifier.pushes) == 1
    # 模拟崩溃重启接续再次进入完成分支
    task._notify_daily_completion()
    assert len(task.config.notifier.pushes) == 1


@pytest.mark.unit
def test_plain_push_failure_does_not_mark(tmp_path):
    """普通推送失败：不写 coop_notified、不抛异常，整轮仍可收尾。"""
    notifier = _FakeNotifier()
    notifier.fail = True
    cfg = SimpleNamespace(total_cooperation_enable=False, total_mysteryshop_enable=False)
    task = _make_task()
    task.config = _fake_config(notifier, config_name='oas1',
                               multi_daily_alt_acc=SimpleNamespace(multi_daily_alt_acc_config=cfg))
    task.daily_conf = task.config.multi_daily_alt_acc
    task._progress = ProgressStore('oas1', base_dir=tmp_path)
    task._progress.ensure_phase(FLAGS_A, '20260817-0605')
    task._notify_daily_completion()
    assert notifier.pushes == []
    assert task._progress.is_coop_notified() is False


# =====================================================================
# 9. 推送显示系统/账号开关（四种组合、空值、默认值、UI schema、错误链路）
# =====================================================================

_COOP_REC = {'type': 'gold', 'character': '角色A', 'svr': '常世之国',
             'apple_or_android': True, 'account': 'example@qq.com'}


@pytest.mark.unit
def test_summary_four_switch_combinations():
    """系统/账号两开关四种组合独立生效。"""
    dt = datetime(2026, 8, 17, 20, 30)
    c = [dict(_COOP_REC)]
    t_off_off = ScriptTask._build_summary_content(c, dt, show_account=False, show_system=False)
    t_sys = ScriptTask._build_summary_content(c, dt, show_account=False, show_system=True)
    t_acc = ScriptTask._build_summary_content(c, dt, show_account=True, show_system=False)
    t_both = ScriptTask._build_summary_content(c, dt, show_account=True, show_system=True)
    assert '• 角色A（常世之国）' in t_off_off
    assert '• 角色A（常世之国｜安卓）' in t_sys
    assert '• 角色A（常世之国｜example@qq.com）' in t_acc
    assert '• 角色A（常世之国｜安卓｜example@qq.com）' in t_both
    assert 'example@qq.com' not in t_off_off
    assert 'example@qq.com' not in t_sys


@pytest.mark.unit
def test_summary_empty_fields_no_empty_separator():
    """svr/account/platform 任一为空都不产生空分隔符（（｜）、｜｜、（）等）。"""
    dt = datetime(2026, 8, 17, 20, 30)
    # account 为空
    t1 = ScriptTask._build_summary_content(
        [dict(_COOP_REC, account='')], dt, show_account=True, show_system=True)
    assert '• 角色A（常世之国｜安卓）' in t1
    # svr 为空
    t2 = ScriptTask._build_summary_content(
        [dict(_COOP_REC, svr='')], dt, show_account=True, show_system=True)
    assert '• 角色A（安卓｜example@qq.com）' in t2
    # platform 缺失 + svr/account 都有
    t3 = ScriptTask._build_summary_content(
        [dict(_COOP_REC, apple_or_android=None)], dt, show_account=True, show_system=True)
    assert '• 角色A（常世之国｜example@qq.com）' in t3
    # 全部为空 → 无括号
    t4 = ScriptTask._build_summary_content(
        [dict(_COOP_REC, svr='', apple_or_android=None, account='')],
        dt, show_account=True, show_system=True)
    assert '• 角色A' in t4
    for t in (t1, t2, t3, t4):
        assert '（｜' not in t and '｜）' not in t and '｜｜' not in t and '（）' not in t


@pytest.mark.unit
def test_summary_platform_hidden_when_show_system_false():
    """show_system=False 时有平台字段也不显示。"""
    dt = datetime(2026, 8, 17, 20, 30)
    text = ScriptTask._build_summary_content(
        [dict(_COOP_REC)], dt, show_account=False, show_system=False)
    assert '• 角色A（常世之国）' in text
    assert '安卓' not in text
    assert 'iOS' not in text


@pytest.mark.unit
def test_coop_notify_switch_defaults():
    """coop_notify_show_system 默认 True，coop_notify_show_account 默认 False。"""
    from tasks.MultiDailyAltAcc.config import MultiDailyAltAccConfig

    cfg = MultiDailyAltAccConfig()
    assert cfg.coop_notify_show_system is True
    assert cfg.coop_notify_show_account is False


@pytest.mark.unit
def test_coop_notify_switch_ui_schema_text():
    """UI/schema 中文文案与默认值（config_model.script_task 以此生成 UI）。"""
    from tasks.MultiDailyAltAcc.config import MultiDailyAltAccConfig

    props = MultiDailyAltAccConfig.model_json_schema()['properties']
    assert props['coop_notify_show_system']['title'] == '推送显示系统'
    assert props['coop_notify_show_system']['description'] == '推送显示系统'
    assert props['coop_notify_show_system']['default'] is True
    assert props['coop_notify_show_account']['title'] == '推送显示账号'
    assert props['coop_notify_show_account']['description'] == '推送显示账号'
    assert props['coop_notify_show_account']['default'] is False


@pytest.mark.unit
def test_switch_account_failure_still_pushes(tmp_path, monkeypatch):
    """切号失败通知保持（title=未找到账号）。"""
    import tasks.MultiDailyAltAcc.script_task as st_mod
    from types import SimpleNamespace

    task = _task_with_progress(tmp_path)
    monkeypatch.setattr(task, 'emit_stat', lambda *a, **k: None)
    device = SimpleNamespace()
    device.stuck_record_clear = lambda: None
    task.device = device
    fake_sw = SimpleNamespace()
    fake_sw.switchAccount = lambda: False
    monkeypatch.setattr(st_mod, 'SwitchAccount', lambda *a, **k: fake_sw)

    assert task._switch_to_account(_account()) is False
    assert any(p.get('title') == '未找到账号' for p in task.config.notifier.pushes)


@pytest.mark.unit
def test_run_device_level_error_raises(tmp_path, monkeypatch):
    """设备级异常必须穿透 raise（原处理链不变），不产生协作汇总。"""
    from module.exception import GameStuckError

    notifier = _FakeNotifier()
    task = _make_run_task(tmp_path, monkeypatch, notifier, seed_coops=[_COOP_A])
    calls = []
    acc = _account()

    def boom(ai):
        raise GameStuckError('stuck')

    monkeypatch.setattr(task, '_get_sorted_accounts', lambda: [acc])
    monkeypatch.setattr(task, '_process_single_account', boom)
    monkeypatch.setattr(task, '_mark_task_start', lambda *a, **k: None)
    monkeypatch.setattr(task, '_mark_task_completed', lambda *a, **k: None)
    monkeypatch.setattr(task, '_update_task_returngift_enable', lambda *a, **k: None)
    monkeypatch.setattr(task, 'emit_stat', lambda *a, **k: None)
    monkeypatch.setattr(task, 'next_run', lambda task_name, **kw: calls.append(('next_run', kw.get('success'))))
    monkeypatch.setattr(task, '_coordinated_shutdown_system', lambda *a, **k: calls.append(('shutdown',)))
    import script
    monkeypatch.setattr(script.Script, 'save_error_log', lambda *a, **k: None)

    with pytest.raises(GameStuckError):
        task.run()

    # 设备级异常不产生协作汇总
    assert not any(p.get('title') == SUMMARY_TITLE for p in notifier.pushes)
    assert calls == [('next_run', False)]


@pytest.mark.unit
def test_coop_notify_switch_i18n_keys_present():
    """i18n 翻译 key 存在，前端 .tr(name) 才能显示中文文案而非英文字段名。"""
    import json
    from pathlib import Path

    data = json.loads(
        (Path.cwd() / 'assets' / 'i18n' / 'zh-CN.json').read_text(encoding='utf-8'))
    assert data.get('coop_notify_show_system') == '推送显示系统'
    assert data.get('coop_notify_show_account') == '推送显示账号'


