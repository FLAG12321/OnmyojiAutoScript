"""运行总表共用协议：跨进程加锁、原子更新，并按完整实例名删除槽位。"""

from copy import deepcopy
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Callable
import uuid

from filelock import FileLock
import psutil


class RuntimeRegistryError(RuntimeError):
    """已有运行总表无法安全读取，必须保留原文件供检查。"""


class RuntimeShutdownPending(RuntimeRegistryError):
    """关机已进入提交阶段，禁止新进程或新任务继续启动。"""


# 同一调度器的嵌套调用沿用外层任务身份，不提前把 worker 标为空闲。
_task_context = ContextVar('oas_runtime_task', default=None)


def _read_unlocked(path: Path) -> dict:
    """缺失表示尚未登记；损坏不能当空表，否则会覆盖其他实例状态。"""
    try:
        with path.open('r', encoding='utf-8') as stream:
            data = json.load(stream)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, UnicodeError) as error:
        raise RuntimeRegistryError(f'运行总表无法读取，保留原文件: {path}') from error
    if not isinstance(data, dict):
        raise RuntimeRegistryError(f'运行总表顶层必须是对象，保留原文件: {path}')
    return data


def _write_unlocked(path: Path, data: dict) -> None:
    """写完并落盘后才替换原文件；失败只删除本次临时文件。"""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', dir=path.parent,
            prefix=f'.{path.name}.', suffix='.tmp', delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _registry_lock(path: Path) -> FileLock:
    """所有读写者共用此锁；持锁期间只操作总表，不进入实例生命周期锁。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(str(path) + '.registry.lock', timeout=10)


def read_runtime_registry(path) -> dict:
    """读取锁内快照；调用方不得将此快照直接写回覆盖其他进程的新状态。"""
    path = Path(path)
    with _registry_lock(path):
        return _read_unlocked(path)


def update_runtime_registry(path, updater: Callable[[dict], None]) -> dict:
    """锁内读改写；回调只修改传入字典，不申请 activity 或 ConfigStore 锁。"""
    path = Path(path)
    with _registry_lock(path):
        data = _read_unlocked(path)
        before = deepcopy(data)
        updater(data)
        if data != before:
            _write_unlocked(path, data)
        return data


def remove_runtime_registry_config(path, config_name: str) -> bool:
    """只删除 config_<完整实例名>，不影响前缀相同或其他大小写的实例。"""
    if not isinstance(config_name, str) or not config_name:
        raise ValueError('实例名不能为空')
    key = f'config_{config_name}'
    removed = False

    def remove(data: dict) -> None:
        # 不创建缺失的数据文件，也不重写已经清理过的总表。
        nonlocal removed
        if key in data:
            del data[key]
            removed = True

    update_runtime_registry(path, remove)
    return removed


def _runtime_directory(config_root) -> Path:
    """共享元数据不放在 sidecar 顶层，也不归任何临时实例独占。"""
    return Path(config_root) / '.generations' / 'runtime'


def _worker_path(config_root) -> Path:
    """受管 worker 表独立于各业务任务的兼容运行总表。"""
    return _runtime_directory(config_root) / 'workers.json'


def process_identity(pid: int = None) -> dict:
    """PID 与启动时间一起识别进程，避免 PID 复用误认仍然存活。"""
    pid = os.getpid() if pid is None else pid
    return {'pid': pid, 'create_time': psutil.Process(pid).create_time()}


def process_liveness(entry: dict):
    """返回存活、确认死亡或未知；缺身份/权限错误均不能证明死亡。"""
    if not isinstance(entry, dict):
        return None
    pid = entry.get('pid')
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    try:
        process = psutil.Process(pid)
        actual_time = process.create_time()
        expected_time = entry.get('create_time')
        if (not isinstance(expected_time, (int, float))
                or isinstance(expected_time, bool) or not math.isfinite(expected_time)):
            return None
        if abs(actual_time - expected_time) > 0.01:
            return False
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except (psutil.Error, OSError, ValueError):
        return None


def _identity_matches(config_root, name: str, generation: str) -> bool:
    """总表锁外读取实例身份，不触发恢复或把损坏身份当作已删除。"""
    from module.config.config_generation import GenerationManager
    manager = GenerationManager(Path(config_root))
    with manager._validation_lock():
        record = manager.read_active_generation(name)
        return bool(record and record.state == 'active'
                    and record.generation == generation
                    and manager._config_path(name).is_file())


def effective_runtime_registry(path, config_root) -> dict:
    """剔除确认死亡的旧槽；未知和活进程身份冲突保留供调用方阻断。"""
    snapshot = read_runtime_registry(path)
    removed = {}
    result = {}
    for key, entry in snapshot.items():
        alive = process_liveness(entry)
        if alive is False:
            removed[key] = entry
            continue
        current = deepcopy(entry) if isinstance(entry, dict) else {'invalid': entry}
        # 身份读取在 registry 锁外，锁序不会反转为 registry → ConfigStore。
        if alive is True:
            try:
                generation = entry.get('generation')
                if (not key.startswith('config_') or not generation
                        or not _identity_matches(config_root, key[7:], generation)):
                    alive = None
            except (OSError, ValueError, RuntimeError):
                alive = None
        current['_liveness'] = alive
        result[key] = current
    if removed:
        def prune(current):
            # 查询期间同名实例可能已经开始新轮，只有未改变的死亡槽可删除。
            for key, entry in removed.items():
                if current.get(key) == entry:
                    current.pop(key, None)
        update_runtime_registry(path, prune)
    return result


def _check_shutdown_marker(config_root) -> None:
    """关机提交跨进程持久可见；只有系统已重启才自动解除旧门禁。"""
    path = _runtime_directory(config_root) / 'shutdown.json'
    marker = _read_unlocked(path)
    if not marker:
        return
    boot_time = marker.get('boot_time')
    if (not isinstance(boot_time, (int, float)) or isinstance(boot_time, bool)
            or not math.isfinite(boot_time)):
        raise RuntimeRegistryError('关机门禁身份损坏，拒绝自动放行')
    if abs(psutil.boot_time() - boot_time) <= 3:
        raise RuntimeShutdownPending('系统关机已提交，拒绝开始新一轮任务')
    path.unlink()


@contextmanager
def runtime_start_gate(config_root):
    """spawn、任务开始和关机共用门禁；不覆盖 child handshake 等待。"""
    directory = _runtime_directory(config_root)
    directory.mkdir(parents=True, exist_ok=True)
    with FileLock(str(directory / 'start.lock'), timeout=10):
        _check_shutdown_marker(config_root)
        yield


def register_runtime_worker(config_root, name, generation, pid, worker_id, state='idle') -> dict:
    """调用方持门禁登记真实 worker；仍活着的其他进程不得被同名覆盖。"""
    token = dict(process_identity(pid), generation=generation, run_id=worker_id)
    entry = dict(token, state=state, task_name=None, task_run_id=None)
    def register(data):
        old = data.get(f'config_{name}')
        if old and old != entry and not _owns_slot(old, token):
            if process_liveness(old) is not False:
                raise RuntimeRegistryError(f'{name}: 同名 worker 尚未确认退出')
        data[f'config_{name}'] = entry
    update_runtime_registry(_worker_path(config_root), register)
    return token


def _owns_slot(entry, token) -> bool:
    """代际、轮次和进程身份四项完全匹配才允许更新。"""
    return (isinstance(entry, dict) and isinstance(token, dict)
            and all(entry.get(key) == token.get(key) and token.get(key) is not None
                    for key in ('generation', 'run_id', 'pid', 'create_time')))


def release_runtime_worker(config_root, name, token) -> None:
    """退出通知以身份 CAS 回收，迟到的旧 child 不能删除新 worker。"""
    def release(data):
        key = f'config_{name}'
        if _owns_slot(data.get(key), token):
            data.pop(key)
    update_runtime_registry(_worker_path(config_root), release)


def register_runtime_helper(config_root, pid: int, role: str) -> None:
    """启动方在门禁内登记明确的辅助进程，不能按所有 spawn 一概放行。"""
    entry = dict(process_identity(pid), role=role)
    update_runtime_registry(_runtime_directory(config_root) / 'helpers.json',
                            lambda data: data.update({str(pid): entry}))


@contextmanager
def task_execution_guard(config, command):
    """从首个设备动作到收尾登记 busy；嵌套任务沿用外层执行身份。"""
    root = config.store.config_root
    name, generation = config.config_name, config.generation
    current = _task_context.get()
    if current is not None:
        if current['name'] != name or current['generation'] != generation:
            raise RuntimeRegistryError('嵌套任务的实例身份发生变化')
        yield
        return
    with runtime_start_gate(root):
        if not _identity_matches(root, name, generation):
            raise RuntimeRegistryError(f'{name}: 实例代际失效，拒绝开始任务')
        worker = read_runtime_registry(_worker_path(root)).get(f'config_{name}')
        identity = process_identity()
        if (not isinstance(worker, dict) or worker.get('generation') != generation
                or any(worker.get(key) != value for key, value in identity.items())):
            worker = register_runtime_worker(root, name, generation, os.getpid(), uuid.uuid4().hex)
        token = dict(worker, name=name, task_run_id=uuid.uuid4().hex)
        def begin(data):
            entry = data.get(f'config_{name}')
            if not _owns_slot(entry, token):
                raise RuntimeRegistryError(f'{name}: worker 身份已变化')
            entry.update(state='busy', task_name=command, task_run_id=token['task_run_id'])
        update_runtime_registry(_worker_path(root), begin)
    context_token = _task_context.set(token)
    try:
        yield
    finally:
        _task_context.reset(context_token)
        def finish(data):
            entry = data.get(f'config_{name}')
            if (_owns_slot(entry, token)
                    and entry.get('task_run_id') == token['task_run_id']):
                entry.update(state='idle', task_name=None, task_run_id=None)
        update_runtime_registry(_worker_path(root), finish)


def start_runtime_slot(path, config, task_name: str, pid: int, **fields) -> dict:
    """新轮创建独立 token；业务兼容字段保留，身份字段不能由调用方覆盖。"""
    root, name = config.store.config_root, config.config_name
    with runtime_start_gate(root):
        if not _identity_matches(root, name, config.generation):
            raise RuntimeRegistryError(f'{name}: 实例代际失效，拒绝登记运行槽')
        context = _task_context.get()
        token = dict(process_identity(pid), generation=config.generation, run_id=uuid.uuid4().hex)
        entry = dict(fields, **token, status='running', completed=False,
                     task_name=task_name, start_time=datetime.now().isoformat(),
                     worker_task_run_id=context['task_run_id'] if context else None)
        def begin(data):
            old = data.get(f'config_{name}')
            # 同进程连续轮允许替换；另一个尚未确认退出的进程不能抢占。
            if (isinstance(old, dict) and old.get('status') == 'running'
                    and (old.get('pid'), old.get('create_time')) != (token['pid'], token['create_time'])
                    and process_liveness(old) is not False):
                raise RuntimeRegistryError(f'{name}: 旧运行槽尚未确认退出')
            data[f'config_{name}'] = entry
        update_runtime_registry(path, begin)
    return token


def update_runtime_slot(path, name: str, token, **fields) -> bool:
    """心跳、回礼开关和完成通知仅更新自己持有的 token，不复活删除槽。"""
    changed = False
    if any(key in fields for key in ('generation', 'run_id', 'pid', 'create_time')):
        raise ValueError('运行身份不可在更新时改写')
    def update(data):
        nonlocal changed
        entry = data.get(f'config_{name}')
        if _owns_slot(entry, token):
            entry.update(fields)
            changed = True
    update_runtime_registry(path, update)
    return changed


def _unregistered_workers(config_root, known_pids: set) -> list:
    """核对旧协议或手工入口的实际进程，不能把缺登记解释成没有 worker。"""
    project = Path(config_root).resolve().parent
    unknown = []
    # 只有本项目启动入口记录的精确辅助身份可排除，例如 multiprocessing.Manager。
    helpers = read_runtime_registry(_runtime_directory(config_root) / 'helpers.json')
    for helper in helpers.values():
        if (isinstance(helper, dict) and helper.get('role') == 'state_manager'
                and process_liveness(helper) is True):
            known_pids.add(helper['pid'])
    for process in psutil.process_iter(['pid', 'name']):
        if process.pid in known_pids:
            continue
        # 此项目的任务运行在 Python 中；其他系统进程无需查询命令行权限。
        if 'python' not in (process.info.get('name') or '').casefold():
            continue
        try:
            args = process.cmdline()
            command = ' '.join(args).casefold()
            looks_like_worker = ('spawn_main' in command or '--multiprocessing-fork' in command
                                 or any(Path(arg).name.casefold() == 'script.py' for arg in args)
                                 or any(Path(arg).name.casefold() == 'script_task.py' for arg in args)
                                 or any(arg.startswith('tasks.') for arg in args))
            if looks_like_worker and Path(process.cwd()).resolve() == project:
                unknown.append(process.pid)
        except psutil.NoSuchProcess:
            continue
        except (psutil.Error, OSError):
            # 无权核对的 Python 进程可能就是旧 worker，保守阻止自动关机。
            unknown.append(process.pid)
    return unknown


def request_runtime_shutdown(path, config, token, execute: Callable[[], bool]) -> bool:
    """门禁内核对有效槽与真实 worker，先落盘关机意图再调用系统命令。"""
    root, name = config.store.config_root, config.config_name
    with runtime_start_gate(root):
        slots = effective_runtime_registry(path, root)
        own = slots.get(f'config_{name}')
        if (not _owns_slot(own, token) or own.get('_liveness') is not True
                or not own.get('completed')):
            return False
        # 未知、尚在运行或历史无身份记录都不能参与“全部完成”的判断。
        if any(entry.get('_liveness') is not True or not entry.get('completed')
               for entry in slots.values()):
            return False
        workers = effective_runtime_registry(_worker_path(root), root)
        known_pids = set()
        for key, worker in workers.items():
            if worker.get('_liveness') is not True:
                return False
            known_pids.add(worker['pid'])
            if worker.get('state') == 'idle':
                continue
            slot = slots.get(key)
            # 仅申请者可在成功收尾中仍为 busy；其他 worker 可能正在处理异常/恢复设备。
            if (key != f'config_{name}' or worker.get('state') != 'busy' or not slot
                    or slot.get('worker_task_run_id') != worker.get('task_run_id')
                    or not worker.get('task_run_id')
                    or slot.get('task_name') != worker.get('task_name')
                    or slot.get('pid') != worker.get('pid')
                    or slot.get('create_time') != worker.get('create_time')):
                return False
        if token['pid'] not in known_pids or _unregistered_workers(root, known_pids):
            return False
        marker_path = _runtime_directory(root) / 'shutdown.json'
        marker = dict(token, boot_time=psutil.boot_time(), state='pending')
        _write_unlocked(marker_path, marker)
        try:
            succeeded = execute() is True
        except Exception:
            marker_path.unlink(missing_ok=True)
            raise
        if not succeeded:
            marker_path.unlink(missing_ok=True)
            return False
        # 成功后保留 pending 门禁即可；即使命令后进程被杀，新任务仍不能启动。
        return True


def cancel_runtime_shutdown(config_root) -> bool:
    """显式恢复入口：先确认系统取消关机成功，才解除同一启动门禁。"""
    import platform
    import subprocess
    directory = _runtime_directory(config_root)
    directory.mkdir(parents=True, exist_ok=True)
    with FileLock(str(directory / 'start.lock'), timeout=10):
        marker = directory / 'shutdown.json'
        if not _read_unlocked(marker):
            return False
        system = platform.system()
        if system == 'Windows':
            result = subprocess.run(['shutdown', '/a'], capture_output=True, text=True)
            # 1116 是 Windows 明确报告“当前没有关机计划”，兼容用户已手工取消的情形。
            accepted = result.returncode in (0, 1116)
        elif system == 'Linux':
            result = subprocess.run(['sudo', 'shutdown', '-c'], capture_output=True, text=True)
            accepted = result.returncode == 0
        else:
            raise RuntimeRegistryError(f'{system}: 无法确认取消关机，保留门禁')
        if not accepted:
            raise RuntimeRegistryError(f'取消关机未获系统确认，保留门禁（退出码 {result.returncode}）')
        marker.unlink()
        return True
