# This Python file uses the following encoding: utf-8
"""MultiTasks 三种账号来源的纯逻辑测试。

来源函数只读配置、不碰 device，因此可以完全脱离模拟器运行。
覆盖：角色名解析、三种来源的正常路径、跨实例去重、未匹配角色名、
单配置加载失败不中断扫描。
"""
from types import SimpleNamespace

import pytest

from tasks.MultiTasks.sources import (
    ACCOUNT_SOURCES,
    load_characters,
    load_config_selection,
    load_own_list,
    parse_account_characters,
)


def _account(character, svr='s1', account=None, alias='', android=True):
    """构造一个具备完整切号资料的账号（字段名与 AccountInfo 对齐）。"""
    return SimpleNamespace(
        character=character,
        svr=svr,
        account=account if account is not None else f'{character}@x.com',
        account_alias=alias,
        apple_or_android=android,
    )


def _store(snapshots=None, names=None, loads=None):
    """打桩 ConfigStore：只需要 active_config_names / active_canonical_snapshots。"""
    snapshots = snapshots or {}
    return SimpleNamespace(
        active_config_names=lambda: list(names if names is not None else snapshots.keys()),
        active_canonical_snapshots=lambda: dict(snapshots),
    )


def _config(store=None, config_name='runner', sup_account_list=None,
            selection=None, characters=''):
    """打桩 Config：只提供来源函数会读的字段。"""
    return SimpleNamespace(
        config_name=config_name,
        store=store or _store(),
        multi_tasks=SimpleNamespace(
            multi_tasks_config=SimpleNamespace(account_characters=characters),
            account_config_selection=SimpleNamespace(**(selection or {})),
            sup_account_list=sup_account_list,
        ),
    )


@pytest.mark.unit
@pytest.mark.parametrize('raw,expected', [
    ('', []),
    ('   ', []),
    (',,,', []),
    ('甲', ['甲']),
    (' 甲 , 乙 ', ['甲', '乙']),
    ('甲,乙,甲', ['甲', '乙']),
])
def test_parse_account_characters(raw, expected):
    assert parse_account_characters(raw) == expected


@pytest.mark.unit
def test_load_own_list_uses_current_instance_name():
    config = _config(sup_account_list=[_account('甲'), _account('乙')])

    items, warnings, load_failure = load_own_list(config)

    assert [(name, acc.character) for name, acc in items] == [('runner', '甲'), ('runner', '乙')]
    assert warnings == []
    # 本实例配置已在内存中，不存在加载失败
    assert load_failure is False


@pytest.mark.unit
def test_load_own_list_drops_incomplete_accounts():
    config = _config(sup_account_list=[
        _account('甲'),
        _account('', svr='s1'),          # 无角色名
        _account('丙', svr=''),           # 无服务器
        _account('丁', account=''),       # 无账号
    ])

    items, _warnings, _fail = load_own_list(config)

    assert [acc.character for _name, acc in items] == ['甲']


@pytest.mark.unit
def test_load_own_list_handles_none_list():
    """sup_account_list 默认为 None，不能炸。"""
    items, warnings, load_failure = load_own_list(_config(sup_account_list=None))
    assert items == []
    assert warnings == []
    assert load_failure is False


@pytest.mark.unit
def test_load_config_selection_reads_checked_instances(monkeypatch):
    import tasks.MultiTasks.sources as mod

    monkeypatch.setattr(mod, 'active_account_configs',
                        lambda store: {'config_aaa': ('source_a', 1),
                                       'config_bbb': ('source_b', 1)})
    monkeypatch.setattr(mod, '_load_source_accounts', lambda store, name: {
        'source_a': [_account('甲')],
        'source_b': [_account('乙')],
    }[name])

    config = _config(selection={'config_aaa': True, 'config_bbb': False})
    items, warnings, load_failure = load_config_selection(config)

    # 只读被勾选的实例
    assert [(name, acc.character) for name, acc in items] == [('source_a', '甲')]
    assert warnings == []
    assert load_failure is False


@pytest.mark.unit
def test_load_config_selection_dedups_same_physical_account(monkeypatch):
    """同一物理账号登记在两个实例里只执行一次（旧 MultiAccountSignIn 会签两次）。"""
    import tasks.MultiTasks.sources as mod

    monkeypatch.setattr(mod, 'active_account_configs',
                        lambda store: {'config_aaa': ('source_a', 1),
                                       'config_bbb': ('source_b', 1)})
    shared = _account('甲')
    monkeypatch.setattr(mod, '_load_source_accounts', lambda store, name: [shared])

    config = _config(selection={'config_aaa': True, 'config_bbb': True})
    items, _warnings, _fail = load_config_selection(config)

    assert [(name, acc.character) for name, acc in items] == [('source_a', '甲')]


@pytest.mark.unit
def test_load_config_selection_isolates_single_config_failure(monkeypatch):
    """一个实例加载失败只置 load_failure，继续扫描其余实例。"""
    import tasks.MultiTasks.sources as mod

    monkeypatch.setattr(mod, 'active_account_configs',
                        lambda store: {'config_aaa': ('bad', 1), 'config_bbb': ('good', 1)})

    def loader(store, name):
        if name == 'bad':
            raise RuntimeError('boom')
        return [_account('乙')]

    monkeypatch.setattr(mod, '_load_source_accounts', loader)

    config = _config(selection={'config_aaa': True, 'config_bbb': True})
    items, _warnings, load_failure = load_config_selection(config)

    assert [acc.character for _name, acc in items] == ['乙']
    assert load_failure is True


@pytest.mark.unit
def test_load_characters_matches_in_input_order(monkeypatch):
    import tasks.MultiTasks.sources as mod

    monkeypatch.setattr(mod, '_load_source_accounts', lambda store, name: {
        'source_a': [_account('乙'), _account('甲')],
        'source_b': [_account('丙')],
    }[name])

    config = _config(store=_store(names=['source_a', 'source_b']), characters='甲,丙,乙')
    items, warnings, load_failure = load_characters(config)

    # 输出顺序遵循用户输入的角色名顺序，而非配置文件里的顺序
    assert [acc.character for _name, acc in items] == ['甲', '丙', '乙']
    assert warnings == []
    assert load_failure is False


@pytest.mark.unit
def test_load_characters_reports_unmatched(monkeypatch):
    import tasks.MultiTasks.sources as mod

    monkeypatch.setattr(mod, '_load_source_accounts',
                        lambda store, name: [_account('甲')])

    config = _config(store=_store(names=['source_a']), characters='甲,不存在')
    items, warnings, load_failure = load_characters(config)

    assert [acc.character for _name, acc in items] == ['甲']
    assert warnings == ['不存在']
    # 未匹配本身不算失败
    assert load_failure is False


@pytest.mark.unit
def test_load_characters_isolates_single_config_failure(monkeypatch):
    import tasks.MultiTasks.sources as mod

    def loader(store, name):
        if name == 'bad':
            raise RuntimeError('boom')
        return [_account('甲')]

    monkeypatch.setattr(mod, '_load_source_accounts', loader)

    config = _config(store=_store(names=['bad', 'good']), characters='甲')
    items, _warnings, load_failure = load_characters(config)

    assert [acc.character for _name, acc in items] == ['甲']
    assert load_failure is True


@pytest.mark.unit
def test_account_sources_registry_covers_every_enum_member():
    from tasks.MultiTasks.config import AccountSourceType

    assert set(ACCOUNT_SOURCES) == set(AccountSourceType)


@pytest.mark.unit
def test_load_characters_scans_via_store(store):
    """真实 Store 集成：扫描不裸读配置文件，模板默认账号无切号资料全部 unmatched。

    取代 Task 1 删掉的 test_config_writers.test_multi_activity_shikigami_scans_via_store。
    """
    from module.config.config import Config

    config = Config('oas1', store=store)
    config.model.multi_tasks.multi_tasks_config.account_characters = '甲'
    items, warnings, load_failure = load_characters(config)

    assert load_failure is False
    assert warnings == ['甲']
    assert items == []
