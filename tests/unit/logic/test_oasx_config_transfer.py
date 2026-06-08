import pytest


def test_config_name_rejects_path_traversal():
    from module.server.config_manager import ConfigManager, ConfigNameError

    # 配置名不能包含路径穿越片段，避免读写到配置目录外。
    with pytest.raises(ConfigNameError):
        ConfigManager.validate_config_name("../oas1")


def test_config_import_rejects_duplicate(tmp_path, monkeypatch):
    from module.server.config_manager import ConfigAlreadyExistsError, ConfigManager

    # 已存在同名配置时，导入流程必须先拒绝，不能覆盖文件。
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "oas1.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ConfigManager, "config_dir", staticmethod(lambda: config_dir))

    with pytest.raises(ConfigAlreadyExistsError):
        ConfigManager.import_config("oas1", {"script": {}})


def test_parse_task_json_source_requires_exactly_one_source():
    from module.server.config_manager import ConfigJsonError, ConfigManager

    # 任务导入必须在文本和文件中二选一，避免前端提交来源不明确。
    with pytest.raises(ConfigJsonError):
        ConfigManager.parse_task_json_source(json_text=None, file_content=None)
    with pytest.raises(ConfigJsonError):
        ConfigManager.parse_task_json_source(json_text="{}", file_content=b"{}")
