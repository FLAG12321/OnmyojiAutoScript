import pytest

from tasks.DailyAltAcc import publish_sr
from tasks.DailyAltAcc.publish_sr import PublishSr


@pytest.mark.unit
def test_sr_cnt_queue_read_write_uses_file_lock(tmp_path, monkeypatch):
    """sr_cnt.json 的读写应使用同一把文件锁，避免多实例并发读写损坏文件。"""
    events = []

    class RecordingFileLock:
        """记录锁的进入和退出，用于验证读写都经过文件锁。"""

        def __init__(self, path):
            self.path = path

        def __enter__(self):
            events.append(('enter', self.path))
            return self

        def __exit__(self, exc_type, exc, tb):
            events.append(('exit', self.path))
            return False

    monkeypatch.setattr(publish_sr, 'FileLock', RecordingFileLock, raising=False)

    task = PublishSr.__new__(PublishSr)
    task.SR_CNT_FILE = tmp_path / 'sr_cnt.json'
    task.SR_CNT_LOCK_FILE = tmp_path / 'sr_cnt.json.lock'
    queue = [{'name': 'I_SR_16', 'count': 1}]

    task._write_queue(queue)
    assert task._read_queue() == queue

    lock_path = str(task.SR_CNT_LOCK_FILE)
    assert events == [
        ('enter', lock_path),
        ('exit', lock_path),
        ('enter', lock_path),
        ('exit', lock_path),
    ]
