# This Python file uses the following encoding: utf-8
# 测试 config_generation：unlocked 原语、generation 身份、迁移归一化规则与全 fault-point 崩溃恢复
import json
import multiprocessing
import os
from pathlib import Path

import pytest

from module.config.config_generation import (
    CREATE_FAULT_POINTS,
    DELETE_FAULT_POINTS,
    MIGRATION_FAULT_POINTS,
    RENAME_FAULT_POINTS,
    ConfigGenerationError,
    ConfigIdentityNameError,
    GenerationManager,
    GenerationRecord,
    _normalize_dynamic_lists,
)
from module.config.config_validation import (
    ConfigValidationError,
    DynamicPathSet,
    ValidationProfile,
)
from tests.conftest import _protected_config_digest

TEMPLATE_PATH = Path.cwd() / "config" / "template.json"


# ---------- 测试数据辅助 ----------

def valid_canonical(name: str) -> dict:
    """从真实模板构造严格合法的 canonical（去掉 MetaDemon count=0 的历史 _1 占位）。"""
    raw = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    raw["meta_demon"].pop("md_strategies_1", None)
    raw["config_name"] = name
    return raw


def legacy_canonical(name: str) -> dict:
    """构造迁移前的 legacy 形状：保留 MetaDemon count=0 的 _1 占位。"""
    raw = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    raw["config_name"] = name
    return raw


def write_config(root: Path, name: str, raw: dict) -> None:
    # 用 write_bytes 精确落盘，避免 Windows 文本模式把 \n 转成 \r\n 导致字节不一致
    (root / f"{name}.json").write_bytes(
        json.dumps(raw, indent=2, ensure_ascii=False).encode("utf-8"))


def _rule_profile(key: str, member_path: tuple, count_path: tuple = None, mode: str = "counted"):
    """构造只含单个动态注册项的 profile，隔离测试各归一化规则。"""
    return ValidationProfile(object, (), (DynamicPathSet(key, member_path, count_path, mode),))


# ---------- Windows spawn 崩溃注入辅助 ----------

class ExitFaultInjector:
    """测试 fault injector：命中命名落盘点立即终止进程（Windows spawn 子进程专用）。"""

    def __init__(self, target: str):
        self.target = target

    def hit(self, point: str) -> None:
        if point == self.target:
            os._exit(97)


def run_crashing_create(root: str, point: str, name: str, raw: dict) -> None:
    manager = GenerationManager(Path(root), fault_injector=ExitFaultInjector(point))
    manager.create_config(name, raw)


def run_crashing_delete(root: str, point: str, name: str) -> None:
    manager = GenerationManager(Path(root), fault_injector=ExitFaultInjector(point))
    manager.delete_config(name)


def run_crashing_rename(root: str, point: str, source: str, destination: str) -> None:
    manager = GenerationManager(Path(root), fault_injector=ExitFaultInjector(point))
    manager.rename_config(source, destination)


def run_crashing_migration(root: str, point: str) -> None:
    manager = GenerationManager(Path(root), fault_injector=ExitFaultInjector(point))
    manager.ensure_migration_complete()


def spawn_fault(target, root: Path, point: str, *args):
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=target, args=(str(root), point, *args))
    process.start()
    return process


def assert_process_crashed(process) -> None:
    process.join(20)
    assert process.exitcode == 97


# ---------- 公共读写原语只取一次锁 ----------

def test_public_read_write_take_exactly_one_lock(tmp_path, monkeypatch):
    import module.config.utils as utils

    real_lock = utils.FileLock
    created = []

    class CountingLock(real_lock):
        def __init__(self, *args, **kwargs):
            created.append(args)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(utils, "FileLock", CountingLock)
    target = tmp_path / "probe.json"
    utils.write_file(str(target), {"a": 1})
    assert len(created) == 1
    created.clear()
    assert utils.read_file(str(target)) == {"a": 1}
    assert len(created) == 1


# ---------- GenerationRecord 身份不变量 ----------

def test_generation_record_invariants():
    assert GenerationRecord("g1", "creating", "digest").digest == "digest"
    assert GenerationRecord("g2", "active", None).digest is None
    assert GenerationRecord("g3", "tombstone", None).digest is None
    with pytest.raises(ValueError):
        GenerationRecord("g4", "creating", None)
    with pytest.raises(ValueError):
        GenerationRecord("g5", "active", "digest")
    with pytest.raises(ValueError):
        GenerationRecord("g6", "bogus", None)


# ---------- legacy 归一化规则（纯函数） ----------

def test_exact_rule_deletes_meta_demon_count_zero_placeholder():
    profile = _rule_profile(
        "meta_demon.md_strategies",
        ("meta_demon", "md_strategies"),
        ("meta_demon", "meta_demon_config", "md_strategy_count"),
    )
    raw = {
        "meta_demon": {
            "meta_demon_config": {"md_strategy_count": 0},
            "md_strategies_1": {"x": 1},
        }
    }
    _normalize_dynamic_lists(raw, profile)
    assert not any(k.startswith("md_strategies_") for k in raw["meta_demon"])


def test_exact_rule_truncates_extra_members_to_count():
    profile = _rule_profile(
        "meta_demon.md_strategies",
        ("meta_demon", "md_strategies"),
        ("meta_demon", "meta_demon_config", "md_strategy_count"),
    )
    raw = {
        "meta_demon": {
            "meta_demon_config": {"md_strategy_count": 1},
            "md_strategies_1": {"x": 1},
            "md_strategies_2": {"x": 2},
            "md_strategies_3": {"x": 3},
        }
    }
    _normalize_dynamic_lists(raw, profile)
    assert sorted(k for k in raw["meta_demon"] if k.startswith("md_strategies_")) == ["md_strategies_1"]


def test_exact_rule_fails_closed_on_missing_members():
    profile = _rule_profile(
        "meta_demon.md_strategies",
        ("meta_demon", "md_strategies"),
        ("meta_demon", "meta_demon_config", "md_strategy_count"),
    )
    raw = {
        "meta_demon": {
            "meta_demon_config": {"md_strategy_count": 3},
            "md_strategies_1": {"x": 1},
        }
    }
    with pytest.raises(ConfigValidationError):
        _normalize_dynamic_lists(raw, profile)


def test_raise_rule_bumps_count_to_member_count():
    profile = _rule_profile(
        "find_jade.invite_info_list",
        ("find_jade", "invite_info_list"),
        ("find_jade", "find_jade_config", "invite_info_count"),
    )
    raw = {
        "find_jade": {
            "find_jade_config": {"invite_info_count": 1},
            "invite_info_list_1": {"name": "a"},
            "invite_info_list_2": {"name": "b"},
        }
    }
    _normalize_dynamic_lists(raw, profile)
    assert raw["find_jade"]["find_jade_config"]["invite_info_count"] == 2


def test_raise_rule_fails_closed_on_missing_members():
    profile = _rule_profile(
        "find_jade.invite_info_list",
        ("find_jade", "invite_info_list"),
        ("find_jade", "find_jade_config", "invite_info_count"),
    )
    raw = {
        "find_jade": {
            "find_jade_config": {"invite_info_count": 3},
            "invite_info_list_1": {"name": "a"},
        }
    }
    with pytest.raises(ConfigValidationError):
        _normalize_dynamic_lists(raw, profile)


def test_raise_rule_fails_closed_on_gapped_indexes():
    profile = _rule_profile(
        "find_jade.invite_info_list",
        ("find_jade", "invite_info_list"),
        ("find_jade", "find_jade_config", "invite_info_count"),
    )
    raw = {
        "find_jade": {
            "find_jade_config": {"invite_info_count": 1},
            "invite_info_list_1": {"name": "a"},
            "invite_info_list_3": {"name": "c"},
        }
    }
    with pytest.raises(ConfigValidationError):
        _normalize_dynamic_lists(raw, profile)


def test_single_rule_keeps_only_first():
    profile = _rule_profile(
        "bondling_fairyland.switch_account_list",
        ("bondling_fairyland", "switch_account_list"),
        mode="single",
    )
    raw = {
        "bondling_fairyland": {
            "switch_account_list_1": {"account": "a"},
            "switch_account_list_2": {"account": "b"},
        }
    }
    _normalize_dynamic_lists(raw, profile)
    assert sorted(raw["bondling_fairyland"]) == ["switch_account_list_1"]


def test_single_rule_fails_closed_when_first_missing():
    profile = _rule_profile(
        "bondling_fairyland.switch_account_list",
        ("bondling_fairyland", "switch_account_list"),
        mode="single",
    )
    raw = {"bondling_fairyland": {"switch_account_list_2": {"account": "b"}}}
    with pytest.raises(ConfigValidationError):
        _normalize_dynamic_lists(raw, profile)


def test_contiguous_rule_keeps_all_members():
    profile = _rule_profile(
        "master_disciple.disciple_account_list",
        ("master_disciple", "disciple_account_list"),
        mode="contiguous",
    )
    raw = {
        "master_disciple": {
            "disciple_account_list_1": {"a": 1},
            "disciple_account_list_2": {"a": 2},
            "disciple_account_list_3": {"a": 3},
        }
    }
    _normalize_dynamic_lists(raw, profile)
    assert sorted(raw["master_disciple"]) == [
        "disciple_account_list_1", "disciple_account_list_2", "disciple_account_list_3"]


def test_contiguous_rule_fails_closed_on_gap():
    profile = _rule_profile(
        "master_disciple.disciple_account_list",
        ("master_disciple", "disciple_account_list"),
        mode="contiguous",
    )
    raw = {
        "master_disciple": {
            "disciple_account_list_1": {"a": 1},
            "disciple_account_list_3": {"a": 3},
        }
    }
    with pytest.raises(ConfigValidationError):
        _normalize_dynamic_lists(raw, profile)


def test_non_canonical_member_key_fails_closed():
    profile = _rule_profile(
        "find_jade.invite_info_list",
        ("find_jade", "invite_info_list"),
        ("find_jade", "find_jade_config", "invite_info_count"),
    )
    raw = {
        "find_jade": {
            "find_jade_config": {"invite_info_count": 1},
            "invite_info_list_01": {"name": "a"},
        }
    }
    with pytest.raises(ConfigValidationError):
        _normalize_dynamic_lists(raw, profile)


# ---------- migration 端到端 ----------

def test_migration_normalizes_legacy_and_persists_immutable_backup(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    original = legacy_canonical("oas-mig")
    write_config(root, "oas-mig", original)
    manager = GenerationManager(root)
    manager.ensure_migration_complete()
    manager.recover_lifecycle_transactions()
    manager.validate_active_identities()

    canonical = manager.load_canonical("oas-mig")
    assert canonical is not None
    assert canonical["meta_demon"]["meta_demon_config"]["md_strategy_count"] == 0
    assert not any(k.startswith("md_strategies_") for k in canonical["meta_demon"])
    record = manager.read_active_generation("oas-mig")
    assert record is not None and record.state == "active"
    assert (root / ".generations" / ".initialized").exists()
    backups = list((root / ".generations" / "backups").glob("oas-mig.*.json"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == json.dumps(original, indent=2, ensure_ascii=False).encode("utf-8")

    # 幂等：marker 存在后再次运行不重写 sidecar
    generation = manager.read_active_generation("oas-mig").generation
    manager.ensure_migration_complete()
    assert manager.read_active_generation("oas-mig").generation == generation


def test_migration_quarantines_gapped_dynamic_list_without_blocking_others(tmp_path):
    """内容级迁移失败只隔离该身份，不阻断整批 migration 与健康配置。

    原实现让任一份配置归一化失败就中止整批迁移且不写 marker：真实升级中一份含历史
    遗留字段的配置会导致每次启动重跑迁移并再次失败，服务永远起不来，且用户在 OASX
    里无处可修。改为隔离后坏配置原字节保持不变（备份已落盘），健康配置正常升级。
    """
    root = tmp_path / "config"
    root.mkdir()
    bad = valid_canonical("oas-bad")
    bad["find_jade"]["find_jade_config"]["invite_info_count"] = 3
    bad_bytes = json.dumps(bad, indent=2, ensure_ascii=False).encode("utf-8")
    write_config(root, "oas-bad", bad)
    write_config(root, "oas-good", legacy_canonical("oas-good"))

    manager = GenerationManager(root)
    manager.ensure_migration_complete()

    # 坏配置被隔离，原字节一字不改，且原字节备份可供人工恢复
    assert isinstance(manager.quarantined_identities["oas-bad"], ConfigValidationError)
    assert (root / "oas-bad.json").read_bytes() == bad_bytes
    backups = list((root / ".generations" / "backups").glob("oas-bad.*.json"))
    assert len(backups) == 1 and backups[0].read_bytes() == bad_bytes

    # 整批迁移完成、marker 写入，健康配置正常升级可用
    assert (root / ".generations" / ".initialized").exists()
    assert manager.load_canonical("oas-good")["config_name"] == "oas-good"

    # survey 复现同一隔离结论；严格体检仍然 fail closed
    assert set(manager.survey_active_identities()) == {"oas-bad"}
    with pytest.raises(ConfigValidationError):
        manager.validate_active_identities()


def test_marker_present_does_not_regenerate_missing_sidecar(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    write_config(root, "oas-mig", legacy_canonical("oas-mig"))
    manager = GenerationManager(root)
    manager.ensure_migration_complete()
    sidecar = root / ".generations" / "oas-mig.json"
    sidecar.unlink()
    manager.ensure_migration_complete()
    assert not sidecar.exists()
    with pytest.raises(ConfigGenerationError):
        manager.validate_active_identities()


# ---------- 身份校验 fail closed ----------

def test_validate_rejects_active_without_config(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    manager = GenerationManager(root)
    manager._write_sidecar("oas-x", GenerationRecord("g", "active", None))
    with pytest.raises(ConfigGenerationError):
        manager.validate_active_identities()


def test_validate_rejects_tombstone_with_config(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    manager = GenerationManager(root)
    manager.create_config("oas-x", valid_canonical("oas-x"))
    manager._write_sidecar("oas-x", GenerationRecord("g", "tombstone", None))
    with pytest.raises(ConfigGenerationError):
        manager.validate_active_identities()


def test_validate_rejects_config_without_sidecar(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    write_config(root, "oas-x", valid_canonical("oas-x"))
    manager = GenerationManager(root)
    with pytest.raises(ConfigGenerationError):
        manager.validate_active_identities()


def test_validate_accepts_active_and_tombstone_stable_identities(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    manager = GenerationManager(root)
    manager.create_config("oas-a", valid_canonical("oas-a"))
    manager.create_config("oas-b", valid_canonical("oas-b"))
    manager.delete_config("oas-b")
    manager.validate_active_identities()


# ---------- 生命周期 happy path ----------

def test_create_load_delete_recreate_lifecycle(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    manager = GenerationManager(root)
    first = manager.create_config("oas-x", valid_canonical("oas-x"))
    record = manager.read_active_generation("oas-x")
    assert record is not None and record.state == "active"
    assert manager.load_canonical("oas-x")["config_name"] == "oas-x"
    assert (root / "oas-x.json").exists()
    manager.validate_active_identities()

    manager.rename_config("oas-x", "oas-y")
    assert manager.read_active_generation("oas-x").state == "tombstone"
    assert not (root / "oas-x.json").exists()
    assert manager.read_active_generation("oas-y").state == "active"
    assert manager.load_canonical("oas-y")["config_name"] == "oas-y"
    manager.validate_active_identities()

    manager.delete_config("oas-y")
    assert manager.read_active_generation("oas-y").state == "tombstone"
    assert not (root / "oas-y.json").exists()
    assert manager.load_canonical("oas-y") is None
    manager.validate_active_identities()

    second = manager.create_config("oas-y", valid_canonical("oas-y"))
    assert second != first
    assert manager.read_active_generation("oas-y").generation == second
    manager.validate_active_identities()


def test_same_name_recreate_produces_new_generation(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    manager = GenerationManager(root)
    first = manager.create_config("oas-x", valid_canonical("oas-x"))
    manager.delete_config("oas-x")
    tombstone_gen = manager.read_active_generation("oas-x").generation
    second = manager.create_config("oas-x", valid_canonical("oas-x"))
    assert second != first
    assert second != tombstone_gen
    assert manager.read_active_generation("oas-x").generation == second


def test_template_reserved_identity_rejects_delete_and_rename(tmp_path):
    """template 不能删除、改名或被普通身份覆盖，失败后内容与 generation 不变。"""
    root = tmp_path / "config"
    root.mkdir()
    manager = GenerationManager(root)
    manager.create_config("template", valid_canonical("template"))
    manager.create_config("oas-x", valid_canonical("oas-x"))
    before = manager.load_canonical("template")
    before_generation = manager.read_active_generation("template").generation

    for action in (
        lambda: manager.delete_config("template"),
        lambda: manager.rename_config("template", "renamed"),
        lambda: manager.rename_config("oas-x", "template"),
    ):
        with pytest.raises(ConfigIdentityNameError):
            action()

    assert manager.load_canonical("template") == before
    assert manager.read_active_generation("template").generation == before_generation
    assert manager.read_active_generation("oas-x").state == "active"


@pytest.mark.skipif(os.name != "nt", reason="Windows case-insensitive identity only")
@pytest.mark.parametrize("alias", ("Template", "TEMPLATE"))
def test_template_reserved_identity_rejects_case_aliases_on_windows(tmp_path, alias):
    """Windows 大小写别名不得绕过 template 生命周期保护。"""
    root = tmp_path / "config"
    root.mkdir()
    manager = GenerationManager(root)
    manager.create_config("template", valid_canonical("template"))

    with pytest.raises(ConfigIdentityNameError):
        manager.delete_config(alias)
    with pytest.raises(ConfigIdentityNameError):
        manager.rename_config(alias, "renamed")
    with pytest.raises(ConfigIdentityNameError):
        manager.rename_config("other", alias)

    assert manager.read_active_generation("template").state == "active"


def test_delete_missing_raises(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    manager = GenerationManager(root)
    with pytest.raises(ConfigGenerationError):
        manager.delete_config("oas-x")


def test_create_existing_raises(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    manager = GenerationManager(root)
    manager.create_config("oas-x", valid_canonical("oas-x"))
    with pytest.raises(ConfigGenerationError):
        manager.create_config("oas-x", valid_canonical("oas-x"))


@pytest.mark.parametrize("name", ("../escaped", "a/b", "a\\b", ".", "..", ""))
def test_lifecycle_rejects_unsafe_names(tmp_path, name):
    root = tmp_path / "config"
    root.mkdir()
    manager = GenerationManager(root)

    with pytest.raises(ConfigGenerationError):
        manager.create_config(name, valid_canonical("safe"))
    assert not (tmp_path / "escaped.json").exists()


@pytest.mark.parametrize(
    "journal",
    (
        {"state": "prepared", "source": {"name": "src"}, "target": {"name": "dst"}},
        {
            "transaction_id": "wrong",
            "operation": "rename",
            "state": "committed",
            "source": {"name": "src", "generation": "g1", "digest": "d1"},
            "target": {"name": "dst", "generation": "g2", "digest": "d2"},
        },
    ),
)
def test_corrupt_rename_journal_is_fail_closed(tmp_path, journal):
    root = tmp_path / "config"
    root.mkdir()
    manager = GenerationManager(root)
    manager.create_config("dst", valid_canonical("dst"))
    txid = "00000000-0000-0000-0000-000000000010"
    manager._ensure_dirs()
    (manager.transactions_dir / f"{txid}.json").write_text(
        json.dumps(journal), encoding="utf-8")

    with pytest.raises(ConfigGenerationError):
        manager.recover_lifecycle_transactions()

    assert manager.load_canonical("dst")["config_name"] == "dst"
    assert (manager.transactions_dir / f"{txid}.json").exists()


def test_prepared_recovery_does_not_delete_new_target_generation(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    manager = GenerationManager(root)
    manager.create_config("src", valid_canonical("src"))
    src = manager.read_active_generation("src")
    new_generation = manager.create_config("dst", valid_canonical("dst"))
    txid = "00000000-0000-0000-0000-000000000001"
    manager._write_journal(txid, {
        "transaction_id": txid,
        "operation": "rename",
        "state": "prepared",
        "source": {"name": "src", "generation": src.generation,
                   "digest": manager._config_digest("src")},
        "target": {"name": "dst", "generation": "old-target-generation",
                   "digest": "0" * 64},
    })

    with pytest.raises(ConfigGenerationError):
        manager.recover_lifecycle_transactions()

    assert manager.read_active_generation("dst").generation == new_generation
    assert manager.load_canonical("dst")["config_name"] == "dst"
    assert (manager.transactions_dir / f"{txid}.json").exists()


def test_committed_recovery_does_not_delete_new_source_generation(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    manager = GenerationManager(root)
    old_source_generation = manager.create_config("src", valid_canonical("src"))
    source_digest = manager._config_digest("src")
    target_generation = manager.create_config("dst", valid_canonical("dst"))
    target_digest = manager._config_digest("dst")
    manager.delete_config("src")
    new_generation = manager.create_config("src", valid_canonical("src"))
    txid = "00000000-0000-0000-0000-000000000002"
    manager._write_journal(txid, {
        "transaction_id": txid,
        "operation": "rename",
        "state": "committed",
        "source": {"name": "src", "generation": old_source_generation,
                   "digest": source_digest},
        "target": {"name": "dst", "generation": target_generation,
                   "digest": target_digest},
    })

    with pytest.raises(ConfigGenerationError):
        manager.recover_lifecycle_transactions()

    assert manager.read_active_generation("src").generation == new_generation
    assert manager.load_canonical("src")["config_name"] == "src"
    assert (manager.transactions_dir / f"{txid}.json").exists()


def test_prepared_journal_never_rolls_back_active_target(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    manager = GenerationManager(root)
    manager.create_config("src", valid_canonical("src"))
    target_generation = manager.create_config("dst", valid_canonical("dst"))
    src = manager.read_active_generation("src")
    txid = "00000000-0000-0000-0000-000000000004"
    manager._write_journal(txid, {
        "transaction_id": txid,
        "operation": "rename",
        "state": "prepared",
        "source": {"name": "src", "generation": src.generation,
                   "digest": manager._config_digest("src")},
        "target": {"name": "dst", "generation": target_generation,
                   "digest": manager._config_digest("dst")},
    })

    with pytest.raises(ConfigGenerationError):
        manager.recover_lifecycle_transactions()

    assert manager.read_active_generation("dst").generation == target_generation
    assert manager.load_canonical("dst")["config_name"] == "dst"
    assert (manager.transactions_dir / f"{txid}.json").exists()


def test_prepared_recovery_keeps_preexisting_tombstone_target(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    manager = GenerationManager(root)
    manager.create_config("src", valid_canonical("src"))
    manager.create_config("dst", valid_canonical("dst"))
    manager.delete_config("dst")
    tombstone = manager.read_active_generation("dst")
    src = manager.read_active_generation("src")
    txid = "00000000-0000-0000-0000-000000000003"
    manager._write_journal(txid, {
        "transaction_id": txid,
        "operation": "rename",
        "state": "prepared",
        "source": {"name": "src", "generation": src.generation,
                   "digest": manager._config_digest("src")},
        "target": {"name": "dst", "generation": "new-target-generation",
                   "digest": "0" * 64},
    })

    manager.recover_lifecycle_transactions()

    assert manager.read_active_generation("dst") == tombstone
    assert not (manager.transactions_dir / f"{txid}.json").exists()


def test_rename_case_only_alias_fails_fast_on_windows(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows case-insensitive identity only")
    root = tmp_path / "config"
    root.mkdir()
    manager = GenerationManager(root, timeout=0.2)
    manager.create_config("oas", valid_canonical("oas"))

    with pytest.raises(ConfigGenerationError):
        manager.rename_config("oas", "OAS")


def test_rename_same_name_fails_fast(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    manager = GenerationManager(root)
    manager.create_config("oas-x", valid_canonical("oas-x"))
    # 同名 rename 必须在取双锁前抛 ConfigGenerationError，不得退化为 filelock.Timeout
    with pytest.raises(ConfigGenerationError):
        manager.rename_config("oas-x", "oas-x")


def test_rename_corrupt_journal_fails_fast_without_blocking_others(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    manager = GenerationManager(root)
    # source==target 的损坏 journal：恢复必须快速失败，不能被双锁 10s 阻塞
    corrupt_txid = "00000000-0000-0000-0000-000000000020"
    manager._write_journal(corrupt_txid, {
        "transaction_id": corrupt_txid,
        "operation": "rename",
        "state": "committed",
        "source": {"name": "oas-x", "generation": "g1", "digest": "d" * 64},
        "target": {"name": "oas-x", "generation": "g2", "digest": "d" * 64},
    })
    # 其后放一个可正常回滚的 prepared journal，验证损坏 journal 不阻断后续恢复
    normal_txid = "00000000-0000-0000-0000-000000000021"
    manager._write_journal(normal_txid, {
        "transaction_id": normal_txid,
        "operation": "rename",
        "state": "prepared",
        "source": {"name": "oas-src", "generation": "g1", "digest": "d" * 64},
        "target": {"name": "oas-dst", "generation": "g2", "digest": "d" * 64},
    })
    with pytest.raises(ConfigGenerationError):
        manager.recover_lifecycle_transactions()
    # 损坏 journal 未处理；清理后重跑，正常 journal 仍可恢复
    corrupt_journal = manager.transactions_dir / f"{corrupt_txid}.json"
    assert corrupt_journal.exists()
    corrupt_journal.unlink()
    manager.recover_lifecycle_transactions()
    assert not (manager.transactions_dir / f"{normal_txid}.json").exists()


def test_validate_rejects_non_string_generation_sidecar(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    manager = GenerationManager(root)
    manager.create_config("oas-x", valid_canonical("oas-x"))
    sidecar = root / ".generations" / "oas-x.json"
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["generation"] = 123
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    # generation 非 str 属于损坏 sidecar，身份校验必须 fail closed
    with pytest.raises(ConfigGenerationError):
        manager.validate_active_identities()


def test_workspace_config_tree_untouched_by_generation_manager(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    real = Path.cwd() / "config"
    # 断言的是「本次生命周期操作不触碰真实 config 树」，而不是「.generations 从不存在」：
    # 只要本机正常跑过一次 server.py/script.py，工作区就会合法地留下 .generations。
    before = _protected_config_digest(real)
    manager = GenerationManager(root)
    manager.create_config("oas-x", valid_canonical("oas-x"))
    manager.rename_config("oas-x", "oas-y")
    manager.delete_config("oas-y")
    # 目录延后到首次 I/O 前创建：构造 GenerationManager / import 主进程模块均不产生
    # .generations 目录副作用，全部事务只落在注入的隔离 root 内。
    assert _protected_config_digest(real) == before
    assert (root / ".generations").exists()


# ---------- fault point 崩溃恢复矩阵 ----------

@pytest.mark.parametrize("point", MIGRATION_FAULT_POINTS)
def test_migration_crash_recovery(tmp_path, point):
    root = tmp_path / "config"
    root.mkdir()
    original = legacy_canonical("oas-mig")
    write_config(root, "oas-mig", original)
    process = spawn_fault(run_crashing_migration, root, point)
    assert_process_crashed(process)
    manager = GenerationManager(root)
    manager.ensure_migration_complete()
    manager.recover_lifecycle_transactions()
    manager.validate_active_identities()
    record = manager.read_active_generation("oas-mig")
    assert record is not None and record.state == "active"
    assert manager.load_canonical("oas-mig") is not None
    assert (root / ".generations" / ".initialized").exists()
    backups = list((root / ".generations" / "backups").glob("oas-mig.*.json"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == json.dumps(original, indent=2, ensure_ascii=False).encode("utf-8")


@pytest.mark.parametrize("point", CREATE_FAULT_POINTS)
def test_create_crash_recovery(tmp_path, point):
    root = tmp_path / "config"
    root.mkdir()
    raw = valid_canonical("oas-crash")
    process = spawn_fault(run_crashing_create, root, point, "oas-crash", raw)
    assert_process_crashed(process)
    manager = GenerationManager(root)
    manager.recover_lifecycle_transactions()
    manager.validate_active_identities()
    record = manager.read_active_generation("oas-crash")
    if point == "create.after_creating_sidecar":
        # creating sidecar 但配置未写：恢复为带新 generation 的 tombstone，load 视为 not found
        assert record is not None and record.state == "tombstone"
        assert not (root / "oas-crash.json").exists()
        assert manager.load_canonical("oas-crash") is None
    else:
        # 配置与 digest 匹配：前滚为 active
        assert record is not None and record.state == "active"
        canonical = manager.load_canonical("oas-crash")
        assert canonical is not None and canonical["config_name"] == "oas-crash"


@pytest.mark.parametrize("point", DELETE_FAULT_POINTS)
def test_delete_crash_recovery(tmp_path, point):
    root = tmp_path / "config"
    root.mkdir()
    setup = GenerationManager(root)
    setup.create_config("oas-crash", valid_canonical("oas-crash"))
    process = spawn_fault(run_crashing_delete, root, point, "oas-crash")
    assert_process_crashed(process)
    manager = GenerationManager(root)
    manager.recover_lifecycle_transactions()
    manager.validate_active_identities()
    record = manager.read_active_generation("oas-crash")
    assert record is not None and record.state == "tombstone"
    assert not (root / "oas-crash.json").exists()
    assert manager.load_canonical("oas-crash") is None


@pytest.mark.parametrize("point", RENAME_FAULT_POINTS)
def test_rename_crash_recovery(tmp_path, point):
    root = tmp_path / "config"
    root.mkdir()
    setup = GenerationManager(root)
    setup.create_config("oas-source", valid_canonical("oas-source"))
    process = spawn_fault(run_crashing_rename, root, point, "oas-source", "oas-destination")
    assert_process_crashed(process)
    manager = GenerationManager(root)
    manager.recover_lifecycle_transactions()
    manager.validate_active_identities()
    if point in RENAME_FAULT_POINTS[:3]:
        # prepared / target creating / target config：源保持 active，目标完全回滚
        src = manager.read_active_generation("oas-source")
        assert src is not None and src.state == "active"
        assert (root / "oas-source.json").exists()
        assert manager.read_active_generation("oas-destination") is None
        assert not (root / "oas-destination.json").exists()
    else:
        # committed 及之后：目标 active、源 tombstone 且源配置不存在
        tgt = manager.read_active_generation("oas-destination")
        assert tgt is not None and tgt.state == "active"
        canonical = manager.load_canonical("oas-destination")
        assert canonical is not None and canonical["config_name"] == "oas-destination"
        src = manager.read_active_generation("oas-source")
        assert src is not None and src.state == "tombstone"
        assert not (root / "oas-source.json").exists()


# ---------- 并发竞态：生命周期操作与身份校验互斥 ----------

def test_lifecycle_ops_serialized_by_validation_lock(tmp_path):
    """并发 create/delete/rename 与身份校验由全局身份锁互斥。

    竞态根因：validate 用两次独立 glob 判断身份，期间并发 create 存在合法中间态
    （config 已写、active sidecar 未切换），会被误判为 "config exists but sidecar missing"。
    修复要求 create/delete/rename 全程持同一把 validation lock，与校验互斥。
    本测试确定性验证：持锁时生命周期操作超时阻塞，释放后正常完成。
    """
    from filelock import Timeout

    root = tmp_path / "config"
    root.mkdir()
    manager = GenerationManager(root, timeout=0.5)
    # 模拟 validate_active_identities 正在扫描：先持全局身份锁
    lock = manager._validation_lock()
    lock.acquire()
    try:
        with pytest.raises(Timeout):
            manager.create_config("oas-x", valid_canonical("oas-x"))
    finally:
        lock.release()
    gen = manager.create_config("oas-x", valid_canonical("oas-x"))
    assert manager.read_active_generation("oas-x").generation == gen

    # delete / rename 同样受全局身份锁约束
    lock.acquire()
    try:
        with pytest.raises(Timeout):
            manager.delete_config("oas-x")
        with pytest.raises(Timeout):
            manager.rename_config("oas-x", "oas-y")
    finally:
        lock.release()
    manager.rename_config("oas-x", "oas-y")
    manager.delete_config("oas-y")
    manager.validate_active_identities()
