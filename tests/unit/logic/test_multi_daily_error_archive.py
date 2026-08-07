# This Python file uses the following encoding: utf-8
"""异常归档（multi_daily_errors_*.json）单元测试：纯文件读写，不依赖设备。"""
import json
from datetime import datetime, timedelta

import pytest

from tasks.MultiDailyAltAcc.progress import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_SKIPPED,
    ProgressStore,
)

FLAGS = {'total_mail_enable': True}
KEY = 'mail@x.com|小号一|两情相悦'


def _fresh_store(tmp_path):
    """创建一个已初始化阶段的 store，归档测试共用。"""
    store = ProgressStore('oas1', base_dir=tmp_path)
    store.ensure_phase(FLAGS, '20260731-0020')
    return store


def _read_errors(store):
    return json.loads(store.error_path.read_text(encoding='utf-8'))


@pytest.mark.unit
def test_error_path_per_instance(tmp_path):
    # 每个配置实例一份归档文件，与进度文件命名风格一致
    store = ProgressStore('oas1', base_dir=tmp_path)
    assert store.error_path.name == 'multi_daily_errors_oas1.json'


@pytest.mark.unit
def test_first_failed_transition_archives_full_record(tmp_path):
    store = _fresh_store(tmp_path)
    store.mark_task(KEY, 'courtyard', STATUS_FAILED, etype='ValueError', emsg='ocr timeout')
    data = _read_errors(store)
    today = datetime.now().strftime('%Y-%m-%d')
    assert list(data.keys()) == [today]
    (record,) = data[today]
    # 公共字段 + failed 附加字段必须完整，供人工排查与程序消费
    assert record['phase_id'] == '20260731-0020'
    assert record['account'] == KEY
    assert record['task'] == 'courtyard'
    assert record['status'] == STATUS_FAILED
    assert record['etype'] == 'ValueError'
    assert record['emsg'] == 'ocr timeout'
    # time 为秒级本地时间字符串
    datetime.strptime(record['time'], '%Y-%m-%d %H:%M:%S')
    # 无已打场次时不附带 battle_count
    assert 'battle_count' not in record


@pytest.mark.unit
def test_repeated_failed_does_not_duplicate(tmp_path):
    store = _fresh_store(tmp_path)
    store.mark_task(KEY, 'courtyard', STATUS_FAILED, etype='ValueError', emsg='x')
    # 重复标 failed 不是首次迁移，不追加第二条（去重跟随邮件语义）
    store.mark_task(KEY, 'courtyard', STATUS_FAILED, etype='ValueError', emsg='x')
    today = datetime.now().strftime('%Y-%m-%d')
    assert len(_read_errors(store)[today]) == 1


@pytest.mark.unit
def test_done_to_failed_not_archived(tmp_path):
    store = _fresh_store(tmp_path)
    store.mark_task(KEY, 'mail', STATUS_DONE)
    # 从已了结状态改写为 failed 不算迁移，与不发邮件的语义一致
    store.mark_task(KEY, 'mail', STATUS_FAILED, etype='ValueError', emsg='x')
    assert not store.error_path.exists()


@pytest.mark.unit
def test_failed_with_battle_count_attached(tmp_path):
    store = _fresh_store(tmp_path)
    for _ in range(10):
        store.add_battle_count(KEY)
    store.mark_task(KEY, 'alliedteam', STATUS_FAILED, etype='GameStuckError', emsg='Wait too long')
    today = datetime.now().strftime('%Y-%m-%d')
    (record,) = _read_errors(store)[today]
    # 报告「已打 10 场后中断」，排查时能对上进度
    assert record['battle_count'] == 10


@pytest.mark.unit
def test_retention_keeps_today_and_yesterday(tmp_path):
    store = _fresh_store(tmp_path)
    today = datetime.now().strftime('%Y-%m-%d')
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    day_before = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d')
    store.error_path.parent.mkdir(parents=True, exist_ok=True)
    store.error_path.write_text(json.dumps({
        day_before: [{'task': 'old'}],
        yesterday: [{'task': 'kept'}],
    }), encoding='utf-8')
    store.mark_task(KEY, 'mail', STATUS_FAILED, etype='ValueError', emsg='x')
    data = _read_errors(store)
    # 写入时清掉今昨以外的日期组：昨天保留、前天删除
    assert set(data.keys()) == {yesterday, today}
    assert data[yesterday] == [{'task': 'kept'}]
    assert len(data[today]) == 1


@pytest.mark.unit
def test_corrupted_archive_rebuilt_without_crash(tmp_path):
    store = _fresh_store(tmp_path)
    store.error_path.parent.mkdir(parents=True, exist_ok=True)
    store.error_path.write_text('{not json', encoding='utf-8')
    # 归档文件损坏时按空重建（丢历史但不崩），迁移判定不受影响
    assert store.mark_task(KEY, 'mail', STATUS_FAILED, etype='ValueError', emsg='x') is True
    today = datetime.now().strftime('%Y-%m-%d')
    assert len(_read_errors(store)[today]) == 1


@pytest.mark.unit
def test_non_dict_archive_toplevel_rebuilt(tmp_path):
    store = _fresh_store(tmp_path)
    store.error_path.parent.mkdir(parents=True, exist_ok=True)
    # 顶层被手动改成合法 JSON 但非 dict（如列表）：同样按空重建，不崩
    store.error_path.write_text(json.dumps([1, 2]), encoding='utf-8')
    assert store.mark_task(KEY, 'mail', STATUS_FAILED, etype='ValueError', emsg='x') is True
    today = datetime.now().strftime('%Y-%m-%d')
    assert len(_read_errors(store)[today]) == 1


@pytest.mark.unit
def test_archive_write_failure_does_not_break_marking(tmp_path, monkeypatch):
    store = _fresh_store(tmp_path)

    # 实现已迁移到 Component 层，须打通用模块的 _write_json_atomic 才有效
    import tasks.Component.MultiAccountRunner.progress as progress_mod

    def boom(path, data):
        raise OSError('disk full')

    # 归档与进度写入双双失败也只记日志：迁移判定（发邮件依据）必须照常返回
    monkeypatch.setattr(progress_mod, '_write_json_atomic', boom)
    assert store.mark_task(KEY, 'mail', STATUS_FAILED, etype='ValueError', emsg='x') is True


@pytest.mark.unit
def test_archive_written_before_progress(tmp_path, monkeypatch):
    store = _fresh_store(tmp_path)

    # 实现已迁移到 Component 层，须打通用模块的 _write_json_atomic 才有效
    import tasks.Component.MultiAccountRunner.progress as progress_mod

    calls = []
    original = progress_mod._write_json_atomic

    def spy(path, data):
        calls.append(path)
        original(path, data)

    monkeypatch.setattr(progress_mod, '_write_json_atomic', spy)
    store.mark_task(KEY, 'mail', STATUS_FAILED, etype='ValueError', emsg='x')
    # 先归档后写进度：极端崩溃下宁可下轮重复归档一条，不可丢失
    assert calls == [store.error_path, store.path]


@pytest.mark.unit
def test_clear_and_rebuild_keep_archive(tmp_path):
    store = _fresh_store(tmp_path)
    store.mark_task(KEY, 'mail', STATUS_FAILED, etype='ValueError', emsg='x')
    before = _read_errors(store)
    # 成功收尾清空进度不影响归档
    store.clear()
    assert _read_errors(store) == before
    # 换开关快照重建阶段同样不影响归档
    rebuilt = ProgressStore('oas1', base_dir=tmp_path)
    rebuilt.ensure_phase({'total_mail_enable': False}, '20260731-0605')
    assert _read_errors(rebuilt) == before


@pytest.mark.unit
def test_malformed_day_bucket_discarded_without_crash(tmp_path):
    store = _fresh_store(tmp_path)
    today = datetime.now().strftime('%Y-%m-%d')
    store.error_path.parent.mkdir(parents=True, exist_ok=True)
    # 组值被手动改坏（合法 JSON 但非列表）：按损坏丢弃重建，绝不能让 AttributeError
    # 逃逸炸掉 mark_task（那会连带跳过进度落盘、抑制邮件、账号永不能收尾）
    store.error_path.write_text(json.dumps({today: 'oops'}), encoding='utf-8')
    assert store.mark_task(KEY, 'mail', STATUS_FAILED, etype='ValueError', emsg='x') is True
    records = _read_errors(store)[today]
    assert isinstance(records, list) and len(records) == 1


@pytest.mark.unit
def test_cross_phase_failure_archives_again(tmp_path):
    store = _fresh_store(tmp_path)
    store.mark_task(KEY, 'mail', STATUS_FAILED, etype='ValueError', emsg='x')
    # 阶段重建后同一子任务再次失败是独立事件，应追加第二条（频次即排查信息）
    rebuilt = ProgressStore('oas1', base_dir=tmp_path)
    rebuilt.ensure_phase({'total_mail_enable': False}, '20260731-0605')
    rebuilt.mark_task(KEY, 'mail', STATUS_FAILED, etype='ValueError', emsg='x')
    today = datetime.now().strftime('%Y-%m-%d')
    records = _read_errors(rebuilt)[today]
    assert [r['phase_id'] for r in records] == ['20260731-0020', '20260731-0605']


@pytest.mark.unit
def test_skipped_transition_archives_with_false_count(tmp_path):
    store = _fresh_store(tmp_path)
    store.mark_task_false(KEY, 'courtyard')
    # 第 2 次达到 FALSE_LIMIT 迁移 skipped，归档一条含 false_count
    store.mark_task_false(KEY, 'courtyard')
    today = datetime.now().strftime('%Y-%m-%d')
    (record,) = _read_errors(store)[today]
    assert record['task'] == 'courtyard'
    assert record['status'] == STATUS_SKIPPED
    assert record['false_count'] == 2
    # skipped 不是异常，没有 etype/emsg
    assert 'etype' not in record
    assert 'emsg' not in record


@pytest.mark.unit
def test_first_false_does_not_archive(tmp_path):
    store = _fresh_store(tmp_path)
    # 首次 False 保持 pending（还有重跑机会），未发生迁移不归档
    store.mark_task_false(KEY, 'courtyard')
    assert not store.error_path.exists()


@pytest.mark.unit
def test_finished_task_false_not_archived(tmp_path):
    store = _fresh_store(tmp_path)
    store.mark_task(KEY, 'mail', STATUS_DONE)
    # 已了结后再报 False 不计数、不迁移，自然也不归档
    store.mark_task_false(KEY, 'mail')
    store.mark_task_false(KEY, 'mail')
    assert not store.error_path.exists()


@pytest.mark.unit
def test_skipped_with_battle_count_attached(tmp_path):
    store = _fresh_store(tmp_path)
    for _ in range(10):
        store.add_battle_count(KEY)
    store.mark_task_false(KEY, 'alliedteam')
    store.mark_task_false(KEY, 'alliedteam')
    today = datetime.now().strftime('%Y-%m-%d')
    (record,) = _read_errors(store)[today]
    # 同心战斗被放行时归档也要报告已打场次
    assert record['status'] == STATUS_SKIPPED
    assert record['battle_count'] == 10
