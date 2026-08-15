# This Python file uses the following encoding: utf-8
# 集成测试：完整配置生命周期（migration → create/delete/rename → 工作区真实配置不变）
import copy
import hashlib
import json
from pathlib import Path

import pytest

from module.config.config_generation import (
    ConfigGenerationError,
    GenerationManager,
    GenerationRecord,
)
from module.config.config_store import ConfigNotFoundError, ConfigStore
from module.config.config_validation import ConfigValidationError
from tests.unit.logic.test_config_generation import (
    legacy_canonical,
    valid_canonical,
    write_config,
)


def _real_config_digest(root: Path) -> dict:
    """真实 config 树摘要：覆盖 config/*.json 与 .generations（不含 .lock 文件）。"""
    protected = list(root.glob("*.json"))
    generations = root / ".generations"
    if generations.exists():
        protected.extend(path for path in generations.rglob("*") if path.is_file())
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(protected)
        if path.suffix != ".lock"
    }


@pytest.fixture(scope="session", autouse=True)
def _assert_real_config_tree_unchanged():
    """整个测试会话前后断言工作区真实 config/*.json 与 .generations 逐字节不变。"""
    root = Path.cwd() / "config"
    before = _real_config_digest(root)
    yield
    assert _real_config_digest(root) == before


def test_initialized_store_recovers_committed_rename_before_patch(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    manager = GenerationManager(root)
    manager.create_config("src", valid_canonical("src"))
    store = ConfigStore(root)
    store.initialize()

    source_generation = manager.read_active_generation("src").generation
    source_digest = manager._config_digest("src")
    target_generation = "target-generation"
    target_raw = valid_canonical("dst")
    target_bytes = json.dumps(
        target_raw, indent=2, ensure_ascii=False, sort_keys=False, default=str
    ).encode("utf-8")
    target_digest = hashlib.sha256(target_bytes).hexdigest()
    manager._write_sidecar(
        "dst", GenerationRecord(target_generation, "creating", target_digest)
    )
    write_config(root, "dst", target_raw)
    txid = "00000000-0000-0000-0000-000000000030"
    manager._write_journal(txid, {
        "transaction_id": txid,
        "operation": "rename",
        "state": "committed",
        "source": {"name": "src", "generation": source_generation, "digest": source_digest},
        "target": {"name": "dst", "generation": target_generation, "digest": target_digest},
    })

    with pytest.raises(ConfigNotFoundError):
        store.patch_user_field("src", ("running_task",), "must-not-write")

    assert store.load("dst").canonical["config_name"] == "dst"
    assert not (root / "src.json").exists()
    assert not (manager.transactions_dir / f"{txid}.json").exists()


def test_migration_then_lifecycle_chain(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    write_config(root, "oas-a", legacy_canonical("oas-a"))
    manager = GenerationManager(root)
    manager.ensure_migration_complete()
    manager.recover_lifecycle_transactions()
    manager.validate_active_identities()
    assert manager.read_active_generation("oas-a").state == "active"

    manager.rename_config("oas-a", "oas-b")
    assert manager.read_active_generation("oas-a").state == "tombstone"
    assert not (root / "oas-a.json").exists()
    assert manager.load_canonical("oas-b")["config_name"] == "oas-b"

    manager.delete_config("oas-b")
    assert manager.load_canonical("oas-b") is None


def test_one_invalid_config_does_not_block_store_initialize(tmp_path):
    """一份配置内容非法只隔离该实例，不阻断 Store 初始化与其余健康实例。

    原实现让 initialize() → validate_active_identities() 对任一内容级校验失败整体抛错，
    而它是 server on_startup 的第一条无保护语句：一个历史遗留字段就会让整个 Web 服务
    起不来（含完全健康的实例），且用户在 OASX 里无处可修。
    """
    root = tmp_path / "config"
    root.mkdir()
    bad = valid_canonical("oas-bad")
    bad["orochi"]["orochi_config"]["legacy_removed_field"] = 1
    write_config(root, "oas-bad", bad)
    bad_bytes = (root / "oas-bad.json").read_bytes()
    write_config(root, "oas-good", valid_canonical("oas-good"))

    store = ConfigStore(root)
    store.initialize()

    # 坏配置被隔离并可上报原因，原字节一字不改
    assert set(store.quarantined_identities) == {"oas-bad"}
    assert "legacy_removed_field" in str(store.quarantined_identities["oas-bad"])
    assert (root / "oas-bad.json").read_bytes() == bad_bytes

    # 健康配置照常枚举与读写；坏配置自身仍 fail closed
    assert store.active_config_names() == ["oas-good"]
    assert store.load("oas-good").canonical["config_name"] == "oas-good"
    with pytest.raises(ConfigValidationError):
        store.load("oas-bad")
    store.patch_user_field("oas-good", ("running_task",), "ok")
    assert store.load("oas-good").canonical["running_task"] == "ok"


def test_structural_identity_corruption_still_fails_closed(tmp_path):
    """结构级身份损坏（迁移完成后凭空多出无 sidecar 的配置）不在隔离范围内，必须继续 fail closed。"""
    root = tmp_path / "config"
    root.mkdir()
    write_config(root, "oas-x", valid_canonical("oas-x"))
    # 先完成一次正常初始化，写入 .initialized marker
    ConfigStore(root).initialize()
    # marker 已存在，此时多出的配置不会再被 migration 收养，属于身份不变量破坏
    write_config(root, "oas-orphan", valid_canonical("oas-orphan"))

    with pytest.raises(ConfigGenerationError):
        ConfigStore(root).initialize()


def test_enumeration_takes_constant_locks_per_identity(tmp_path):
    """枚举必须整轮只取一次全局身份锁，每个名称只取自己那把 lifecycle 锁。

    原实现每个名称各走一遍「validation 锁 + 全量 recover（逐身份再取锁）」，
    把枚举放大成 O(N²) 次 FileLock：本机 7 份配置时 OASX 打开
    MultiAccountSignIn 参数页最坏要取 150 次文件锁。
    """
    import filelock

    root = tmp_path / "config"
    root.mkdir()
    manager = GenerationManager(root)
    names = [f"oas{i}" for i in range(1, 6)]
    for name in names:
        manager.create_config(name, valid_canonical(name))
    store = ConfigStore(root)
    store.initialize()

    acquired = []
    original = filelock.BaseFileLock.acquire

    def counted(self, *args, **kwargs):
        acquired.append(Path(self.lock_file).name)
        return original(self, *args, **kwargs)

    filelock.BaseFileLock.acquire = counted
    try:
        assert store.active_config_names() == names
    finally:
        filelock.BaseFileLock.acquire = original

    # 1 把 identity.lock + 每个身份 1 把 lifecycle lock
    assert acquired.count("identity.lock") == 1
    assert len(acquired) == 1 + len(names)

    # 单次 load 恒定 2 把锁，不随配置数量增长
    acquired.clear()
    filelock.BaseFileLock.acquire = counted
    try:
        store.load("oas1")
    finally:
        filelock.BaseFileLock.acquire = original
    assert acquired == ["identity.lock", "oas1.lock"]

    manager.create_config("oas-c", valid_canonical("oas-c"))
    manager.validate_active_identities()


def test_migration_raise_rule_preserves_extra_accounts(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    raw = valid_canonical("oas-raise")
    fj = raw["find_jade"]
    fj["find_jade_config"]["invite_info_count"] = 1
    fj["invite_info_list_2"] = copy.deepcopy(fj["invite_info_list_1"])
    fj["find_jade_config"]["sup_account_count"] = 1
    fj["sup_account_list_2"] = copy.deepcopy(fj["sup_account_list_1"])
    write_config(root, "oas-raise", raw)
    manager = GenerationManager(root)
    manager.ensure_migration_complete()
    canonical = manager.load_canonical("oas-raise")
    assert canonical["find_jade"]["find_jade_config"]["invite_info_count"] == 2
    assert "invite_info_list_2" in canonical["find_jade"]
    assert canonical["find_jade"]["find_jade_config"]["sup_account_count"] == 2
    assert "sup_account_list_2" in canonical["find_jade"]


def test_migration_single_rule_keeps_only_first(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    raw = valid_canonical("oas-bondling")
    bf = raw["bondling_fairyland"]
    bf["switch_account_list_2"] = copy.deepcopy(bf["switch_account_list_1"])
    write_config(root, "oas-bondling", raw)
    manager = GenerationManager(root)
    manager.ensure_migration_complete()
    canonical = manager.load_canonical("oas-bondling")
    assert "switch_account_list_1" in canonical["bondling_fairyland"]
    assert "switch_account_list_2" not in canonical["bondling_fairyland"]


def test_meta_demon_count_zero_default_placeholder_is_deleted(tmp_path):
    """记忆覆盖项：MetaDemon count=0 且 _1 占位存在时，migration 必须删除 _1。"""
    root = tmp_path / "config"
    root.mkdir()
    write_config(root, "oas-md", legacy_canonical("oas-md"))
    manager = GenerationManager(root)
    manager.ensure_migration_complete()
    canonical = manager.load_canonical("oas-md")
    md = canonical["meta_demon"]
    assert md["meta_demon_config"]["md_strategy_count"] == 0
    assert not any(k.startswith("md_strategies_") for k in md)


def test_repeated_recovery_does_not_overwrite_original_backup(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    original = legacy_canonical("oas-mig")
    write_config(root, "oas-mig", original)
    manager = GenerationManager(root)
    manager.ensure_migration_complete()
    backup = list((root / ".generations" / "backups").glob("oas-mig.*.json"))[0]
    first_bytes = backup.read_bytes()
    # 模拟进程崩溃后再次运行完整恢复流程：备份内容必须保持原字节
    manager2 = GenerationManager(root)
    manager2.ensure_migration_complete()
    manager2.recover_lifecycle_transactions()
    manager2.validate_active_identities()
    backups = list((root / ".generations" / "backups").glob("oas-mig.*.json"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == first_bytes
    assert first_bytes == json.dumps(original, indent=2, ensure_ascii=False).encode("utf-8")
