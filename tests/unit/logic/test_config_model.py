import pytest
from module.config.config import Config, Function


class TestConfigLoading:
    def test_load_oas_config(self, config):
        """config fixture 来自 conftest.py，从真实配置加载"""
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
