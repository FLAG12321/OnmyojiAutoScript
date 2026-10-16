"""角色成果文件名：以完整身份区分同名角色，保持同角色最新截图覆盖语义。"""

import hashlib
import json
import re
import unicodedata


def role_artifact_stem(task) -> str:
    """保留可读角色/区服与稳定身份摘要，替换非法字符后也不会使不同身份碰撞。"""
    context = getattr(task, '_stat_ctx', None) or {}
    identity = getattr(task, '_account_identity', None)
    if isinstance(identity, (tuple, list)) and len(identity) == 4:
        account, character, server, android = identity
        system = 'android' if android is True else 'ios' if android is False else ''
    else:
        account, character, server = (context.get(key, '') for key in ('acc', 'char', 'svr'))
        system = context.get('sys', '')
    values = [unicodedata.normalize('NFC', str(value or '')) for value in (account, character, server, system)]
    # 单实例直跑缺角色身份时按实例隔离，不能猜测角色库或使用全局默认文件名。
    if not all(values):
        values.append(str(task.config.config_name))
    digest = hashlib.sha256(json.dumps(values, ensure_ascii=False).encode('utf-8')).hexdigest()[:16]
    labels = [values[1] or task.config.config_name, values[2], values[3]]
    safe = [re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', str(value)).strip(' .')[:24] for value in labels]
    return '_'.join(value for value in safe if value) + '_' + digest
