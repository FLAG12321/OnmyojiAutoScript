"""任务完成通知的持久待发箱：网络失败只重试投递，不重新执行游戏任务。"""

import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from filelock import FileLock

from module.config.history_retention import BEIJING, retention_for


class NotificationOutboxError(RuntimeError):
    """待发记录无法安全持久化时，调用方不能清除唯一的业务进度。"""


class NotificationOutbox:
    """一个实例一份待发文件；生命周期代际和轮次区分同名实例与不同任务阶段。"""

    def __init__(self, config_name, generation, base_dir='config/tasks_config'):
        self.path = Path(base_dir) / f'task_notifications_{config_name}.json'
        self.generation = str(generation)

    def _read(self):
        """缺失表示空箱，损坏则保留原文件，不能用空箱覆盖。"""
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {'version': 1, 'items': []}
        except (OSError, ValueError) as exc:
            raise NotificationOutboxError(f'通知待发箱无法读取：{self.path}') from exc
        if (not isinstance(data, dict) or data.get('version') != 1
                or not isinstance(data.get('items'), list)
                or any(not isinstance(row, dict) or not isinstance(row.get('payload'), dict)
                       or not row.get('id') or row.get('status') not in ('pending', 'sent')
                       for row in data['items'])):
            raise NotificationOutboxError(f'通知待发箱格式无效：{self.path}')
        return data

    def _save(self, data):
        """同锁内原子替换；故障时保留上一份已提交待发记录。"""
        temporary = Path(str(self.path) + '.tmp')
        try:
            with temporary.open('w', encoding='utf-8') as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except (OSError, ValueError) as exc:
            raise NotificationOutboxError(f'通知待发箱保存失败：{self.path}') from exc
        finally:
            temporary.unlink(missing_ok=True)

    def _lock(self):
        """投递回调只调用通知器，不进入 ConfigStore，避免倒置生命周期锁序。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(str(self.path) + '.cleanup.lock', timeout=10)

    def enqueue(self, phase_token, payload, *, coops=(), mshops=(), items=(), now=None):
        """先写入摘要及业务条目；时间展示变化不会让同一轮次重复排队。"""
        now = now or datetime.now(BEIJING)
        event = [self.generation, phase_token, list(coops), list(mshops), list(items)]
        key = hashlib.sha256(json.dumps(event, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
        with self._lock():
            data = self._read()
            if not any(row['id'] == key for row in data['items']):
                data['items'].append({'id': key, 'generation': self.generation, 'phase_token': phase_token,
                                      'created_at': now.isoformat(), 'status': 'pending', 'payload': dict(payload),
                                      'coops': list(coops), 'mshops': list(mshops)})
                self._save(data)
        return key

    def deliver(self, push, *, record_id=None, now=None):
        """每次至多一条，失败三分钟后再试；成功标记落盘前崩溃仍可能重复投递。"""
        if not self.path.exists():
            return False
        now = now or datetime.now(BEIJING)
        with self._lock():
            data = self._read()
            for row in data['items']:
                if row.get('generation') != self.generation or (record_id and row['id'] != record_id):
                    continue
                if row['status'] == 'sent':
                    if record_id:
                        return True
                    continue
                previous = row.get('last_attempt')
                if previous:
                    try:
                        if now < datetime.fromisoformat(previous) + timedelta(minutes=3):
                            continue
                    except (TypeError, ValueError):
                        raise NotificationOutboxError(f'通知重试时间无效：{self.path}') from None
                row['last_attempt'] = now.isoformat()
                self._save(data)
                try:
                    success = push(**dict(row['payload'])) is True
                except Exception:
                    success = False
                if success:
                    row.update(status='sent', sent_at=now.isoformat())
                    self._save(data)
                return success
            # 待发送永远不因过期丢失，已发送历史按双方共享日期策略收回。
            try:
                policy = retention_for(self.path.parent, now=now)
                kept = [row for row in data['items'] if not policy.notification_expired(row)]
            except ValueError as exc:
                raise NotificationOutboxError(f'通知保留策略无效：{self.path}') from exc
            if kept != data['items']:
                data['items'] = kept
                self._save(data)
        return False


def retry_notifications(config):
    """调度器闲时仅投递当前实例的待发记录，不创建任务或访问游戏设备。"""
    box = NotificationOutbox(config.config_name, config.generation,
                             base_dir=config.store.config_root / 'tasks_config')
    return box.deliver(config.notifier.push)
