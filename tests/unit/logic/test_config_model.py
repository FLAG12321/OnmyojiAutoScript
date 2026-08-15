import pytest
from module.config.config import Config, Function
from module.config.config_model import ConfigModel


class TestConfigLoading:
    def test_load_oas_config(self, config):
        """config fixture 来自 conftest.py，从隔离配置加载"""
        assert config is not None
        assert config.config_name == "oas1"
        assert hasattr(config, "script")

    def test_config_has_restart_section(self, config):
        assert hasattr(config, "restart")

    def test_config_has_scheduler_priority(self):
        """验证 ConfigManual.SCHEDULER_PRIORITY 存在且非空"""
        from module.config.config_manual import ConfigManual
        assert hasattr(ConfigManual, "SCHEDULER_PRIORITY")
        assert len(ConfigManual.SCHEDULER_PRIORITY) > 0


class TestModelValidateCompat:
    def test_model_validate_accepts_canonical_data_without_file_io(self):
        raw = ConfigModel().model_dump(mode="json")
        raw["config_name"] = "oas-test"
        model = ConfigModel.model_validate(raw)
        assert model.config_name == "oas-test"

    def test_config_compat_loader_still_reads_named_file(self, tmp_path):
        # Config 由注入 Store 加载：oas-test 必须真实存在于隔离配置根
        from module.config.config_store import ConfigStore

        raw = ConfigModel().model_dump(mode="json")
        raw["config_name"] = "oas-test"
        raw["meta_demon"].pop("md_strategies_1", None)
        store = ConfigStore(config_root=tmp_path / "config")
        store.create_from_template("oas-test", raw)
        config = Config("oas-test", store=store)
        assert config.config_name == "oas-test"


class TestFunctionParsing:
    def test_function_enabled(self):
        data = {
            "scheduler": {
                "enable": True,
                "next_run": "2026-05-19 12:00:00",
                "priority": "50",
            }
        }
        f = Function(key="restart", data=data)
        assert f.enable is True
        assert f.command == "Restart"
        assert f.priority == 50

    def test_function_disabled_without_scheduler_key(self):
        data = {"other": "value"}
        f = Function(key="restart", data=data)
        assert f.enable is False
        assert f.command == "Unknown"

    def test_function_str_representation(self):
        data = {
            "scheduler": {
                "enable": True,
                "next_run": "2026-05-19 12:00:00",
                "priority": "50",
            }
        }
        f = Function(key="restart", data=data)
        assert f.command in str(f)
        assert "Enable" in str(f)
