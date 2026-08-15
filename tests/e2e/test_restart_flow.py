import pytest
from pathlib import Path

RECORD_DIR = Path(__file__).parent.parent / "fixtures" / "recorded" / "Restart"


@pytest.mark.e2e
class TestRestartE2E:
    def test_restart_record(self, config, is_record):
        """录制 Restart 全流程（需要真实模拟器运行中）"""
        if not is_record:
            pytest.skip("使用 --record 参数启用录制模式")

        from module.device.device import Device
        from tasks.Restart.script_task import ScriptTask
        from module.exception import TaskEnd
        from tests.e2e.recording import RecordingDevice

        # Device 构造前后必须划定 COLD 启动边界，否则 serial_check 里的内部归一化
        # （中文冒号 serial / benchmark / emulatorinfo 回写）会因缺少 provisional
        # 快照直接抛 RuntimeError。
        config.begin_device_initialization()
        real_device = Device(config=config)
        config.freeze_startup_device_snapshot()
        rec_device = RecordingDevice(real_device, RECORD_DIR)

        task = ScriptTask(config=config, device=rec_device)
        try:
            task.run()
        except TaskEnd:
            pass
        finally:
            rec_device.save_actions()

        assert rec_device.actions, "录制数据不应为空"

    def test_restart_replay_structure(self, is_replay):
        """验证录制数据的结构完整性（回放前置检查）"""
        if not is_replay:
            pytest.skip("使用 --replay 参数启用回放模式")

        from tests.e2e.replay import ReplayAssertion

        assertion = ReplayAssertion(RECORD_DIR)
        assert len(assertion.actions) > 0, "录制数据为空"

        actions_seen = set()
        for action in assertion.actions:
            assert "seq" in action
            assert "action" in action
            actions_seen.add(action["action"])
        assert "screenshot" in actions_seen, "至少应有一次截图记录"
