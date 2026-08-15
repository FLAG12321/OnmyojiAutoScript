import asyncio
import json
from pathlib import Path

import pytest


def test_config_name_rejects_path_traversal():
    from module.server.config_manager import ConfigManager, ConfigNameError

    # 配置名不能包含路径穿越片段，避免读写到配置目录外。
    with pytest.raises(ConfigNameError):
        ConfigManager.validate_config_name("../oas1")


def test_config_import_rejects_duplicate(tmp_path):
    from module.config.config_store import ConfigStore
    from module.server.config_manager import ConfigAlreadyExistsError, ConfigManager

    # 已存在同名配置时，导入流程必须先拒绝，不能覆盖文件。
    raw = json.loads((Path.cwd() / "config" / "template.json").read_text(encoding="utf-8"))
    raw["meta_demon"].pop("md_strategies_1", None)
    manager = ConfigManager(store=ConfigStore(config_root=tmp_path / "config"))
    manager.store.create_from_template("oas1", raw)

    with pytest.raises(ConfigAlreadyExistsError):
        manager.import_config("oas1", raw)


def test_config_copy_propagates_generation_error(tmp_path, monkeypatch):
    """ConfigManager.copy 不得吞身份损坏异常并让上层误报成功。"""
    from module.config.config_generation import ConfigGenerationError
    from module.config.config_store import ConfigStore
    from module.server.config_manager import ConfigManager

    manager = ConfigManager(store=ConfigStore(config_root=tmp_path / "config"))

    def fail_load(*_args, **_kwargs):
        raise ConfigGenerationError("injected corrupt identity")

    monkeypatch.setattr(manager.store, "load", fail_load)
    with pytest.raises(ConfigGenerationError, match="corrupt identity"):
        manager.copy("copied", "template")


def test_validate_config_model_wraps_non_dict_task_node():
    from module.server.config_manager import ConfigManager, ConfigValidationError

    # 兼容导入边界必须把任务节点形状错误转换为统一的 400 错误类型。
    with pytest.raises(ConfigValidationError) as error:
        ConfigManager._validate_config_model("oas-test", {"find_jade": 1})
    assert error.value.fields


def test_validate_config_model_wraps_non_dict_nested_group_node():
    from module.server.config_manager import ConfigManager, ConfigValidationError

    with pytest.raises(ConfigValidationError) as error:
        ConfigManager._validate_config_model("oas-test", {"find_jade": {"find_jade_config": 1}})
    assert error.value.fields


def test_validate_task_value_wraps_non_dict_nested_group_node():
    from module.server.config_manager import ConfigManager, ConfigValidationError

    with pytest.raises(ConfigValidationError) as error:
        ConfigManager.validate_task_value("find_jade", {"find_jade_config": 1})
    assert error.value.fields[0]["field"] == "find_jade"


def test_validate_task_value_keeps_legal_find_jade_import():
    from module.server.config_manager import ConfigManager

    validated = ConfigManager.validate_task_value("find_jade", {
        "find_jade_config": {"invite_info_count": 1, "sup_account_count": 1},
        "invite_info_list_1": {"name": "ONE"},
        "sup_account_list_1": {"character": "ALT"},
    })

    assert validated["invite_info_list_1"]["name"] == "ONE"
    assert validated["sup_account_list_1"]["character"] == "ALT"


def test_task_import_route_returns_400_and_keeps_disk(tmp_path):
    from fastapi import HTTPException
    from module.config.config_store import ConfigStore
    from module.server.main_manager import mm
    from module.server.script_router import config_task_import

    raw = json.loads((Path.cwd() / "config" / "template.json").read_text(encoding="utf-8"))
    raw["meta_demon"].pop("md_strategies_1", None)
    old_store = mm.store
    mm.store = ConfigStore(config_root=tmp_path / "config")
    try:
        mm.store.create_from_template("oas1", raw)
        before = mm.store.load_canonical_snapshot("oas1")

        with pytest.raises(HTTPException) as error:
            asyncio.run(config_task_import(
                config_name="oas1",
                task_name="FindJade",
                json_text=json.dumps({"find_jade": {"find_jade_config": 1}}),
                file=None,
            ))

        assert error.value.status_code == 400
        assert error.value.detail["fields"]
        # 校验失败必须发生在写盘前，现有配置内容保持逐字节不变。
        assert mm.store.load_canonical_snapshot("oas1") == before
    finally:
        mm.store = old_store


def test_task_import_rejects_range_fallback_and_keeps_disk(tmp_path):
    from fastapi import HTTPException
    from module.config.config_store import ConfigStore
    from module.server.main_manager import mm
    from module.server.script_router import config_task_import

    raw = json.loads((Path.cwd() / "config" / "template.json").read_text(encoding="utf-8"))
    raw["meta_demon"].pop("md_strategies_1", None)
    old_store = mm.store
    mm.store = ConfigStore(config_root=tmp_path / "config")
    try:
        mm.store.create_from_template("oas1", raw)
        mm.store.patch_user_argument(
            "oas1",
            "FindJade",
            "findJadeConfig",
            "inviteInfoCount",
            2,
        )
        before = mm.store.load("oas1")
        invalid = {
            "find_jade": {
                "find_jade_config": {
                    "invite_info_count": 0,
                    "sup_account_count": 1,
                },
            },
        }

        with pytest.raises(HTTPException) as error:
            asyncio.run(config_task_import(
                config_name="oas1",
                task_name="FindJade",
                json_text=json.dumps(invalid),
                file=None,
            ))

        assert error.value.status_code == 400
        after = mm.store.load("oas1")
        assert after.canonical == before.canonical
        assert after.mtime_ns == before.mtime_ns
    finally:
        mm.store = old_store


def test_parse_task_json_source_requires_exactly_one_source():
    from module.server.config_manager import ConfigJsonError, ConfigManager

    # 任务导入必须在文本和文件中二选一，避免前端提交来源不明确。
    with pytest.raises(ConfigJsonError):
        ConfigManager.parse_task_json_source(json_text=None, file_content=None)
    with pytest.raises(ConfigJsonError):
        ConfigManager.parse_task_json_source(json_text="{}", file_content=b"{}")
