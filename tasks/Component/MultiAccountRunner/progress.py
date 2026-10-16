# This Python file uses the following encoding: utf-8
"""多账号任务「每账号 × 每子任务」进度持久化（通用版）。

用独立文件记录各账号的执行状态，使多账号任务中断或异常后能够精确接续：
已完成的账号与子任务直接跳过，计数型子任务从已记计数继续。
本模块只做文件读写，不依赖 device / Config，便于单元测试。

由 tasks/Component/MultiAccountRunner/progress.py 提供通用实现，各任务经
tasks/MultiDailyAltAcc/progress.py 等薄封装固定自己的 task_name 后使用：
进度文件名 {task_name}_progress_{config_name}.json，进度快照
{task_name}_progress_{config_name}.json.bak，异常归档文件
{task_name}_errors_{config_name}.json，每个配置实例一份，互不干扰。
"""
import copy
import json
import os
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

from filelock import FileLock

from module.exception import RequestHumanTakeover
from module.logger import logger
from tasks.Component.SwitchAccount.switch_account_config import clean_text

# 第二版把实例身份和实际执行参数与轮次标识分开，避免改一个角色重跑整轮。
SCHEMA_VERSION = 2


class ProgressReviewRequired(RequestHumanTakeover):
    """无法证明进度归属或兼容性时保留原文件，停止自动执行等待核对。"""

# 子任务状态：pending 未跑或中断（可重跑）、done 正常完成、failed 抛过异常（跳过）、
# skipped 连续多次显式返回 False 后放行（跳过，本阶段不再重试）
STATUS_PENDING = 'pending'
STATUS_DONE = 'done'
STATUS_FAILED = 'failed'
STATUS_SKIPPED = 'skipped'

# 显式返回 False 的累计上限：达到即迁移 skipped。
# False 语义过载（UI 卡顿该重试 / 本来就无事可做该放行），截图层无法区分，
# 若无上限，无奖可领的庭院、空邮箱会让账号永远无法收尾，任务每 10 分钟无限重调度。
FALSE_LIMIT = 2

# 仅旧工具接口沿用陈旧阈值；有代际的新进度只按日期/阶段切换，不因停机清零。
STALE_HOURS = 18


def acc_key(account: str, character: str, svr, apple_or_android=None) -> str:
    """完整身份包含平台；省略平台只供旧数据索引兼容，不应用于新执行队列。"""
    svr_name = getattr(svr, 'name', None) or str(svr)
    values = [clean_text(str(value)) for value in (account, character, svr_name)]
    # 转义分隔符，避免字段自身含竖线时两个不同身份得到相同字符串。
    key = '|'.join(value.replace('%', '%25').replace('|', '%7C') for value in values)
    if apple_or_android is None:
        return key
    if not isinstance(apple_or_android, bool):
        raise ValueError('进度账号平台必须是明确的布尔值')
    return f'{key}|{"android" if apple_or_android else "ios"}'


def execution_parameters(value):
    """稳定序列化实际执行参数，排除调度、登录时间等运行时字段。"""
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    if isinstance(value, dict):
        return {str(key): execution_parameters(item) for key, item in value.items()
                if key not in ('scheduler', 'next_run', 'last_complete_time',
                               'last_login_time', 'need_login_time')}
    if isinstance(value, (tuple, list)):
        return [execution_parameters(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime,)):
        return value.isoformat()
    return value


def phase_flags_match(stored, current: dict) -> bool:
    """阶段开关快照是否可视为「同一阶段」。

    只要求快照里存在的键在当前快照中取值一致：升级后新增的阶段开关（老快照
    里没有该键）不参与失效判定，否则历史进度会被整份作废、已完成账号全部重跑。
    同一开关取值变化（轮转安排下一阶段）仍然判定为不一致，照常重建。
    快照不是字典（文件被手动改坏）一律按不一致处理。
    """
    if not isinstance(stored, dict):
        return False
    return all(
        name in current and current[name] == value
        for name, value in stored.items()
    )


def _write_json_atomic(path: Path, data: dict) -> None:
    """tmp 文件 + os.replace 原子写 JSON；异常由调用方按各自语义处理。

    进度文件与异常归档文件共用：直接覆写时进程被杀会留下截断 JSON，
    os.replace 在 Windows 上对已存在目标是原子替换，可消除该窗口。
    再补一次 fsync：停电/断电时 NTFS 会先把元数据提交，数据块可能还在缓存里，
    只靠 replace 仍会留下「文件名对、内容截断或全零」的半成品文件。

    写失败（序列化异常、磁盘错误）时顺手删掉半截的 tmp，不在磁盘上留孤儿；
    进程被强杀留下的 tmp 由下一次写同一路径覆盖，或由 clear() 一并清理。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix('.json.tmp')
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        # 清理半截 tmp；连清理都失败也不能顶掉原始异常（调用方按各自语义处理）
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


class ProgressStore:
    """进度文件的读写封装，每个配置实例（oas1/oas2）一个文件，互不干扰。

    :param task_name: 任务标识，用于进度与归档文件名前缀（如 "multi_daily"、"find_jade"）
    :param config_name: 配置实例名（oas1/oas2），不同实例进度互不干扰
    """

    def __init__(self, task_name: str, config_name: str, base_dir='config/tasks_config',
                 *, generation: str | None = None):
        self.task_name = task_name
        self.config_name = config_name
        # 生产调用方传 Config.generation；不传仅保留旧版独立测试/工具的读取接口。
        self.generation = generation
        self.path = Path(base_dir) / f'{task_name}_progress_{config_name}.json'
        # 快照文件：主文件每次成功落盘后同步写一份，主文件损坏时回退用它。
        # 停电/强杀可能让最后一次落盘留下截断或全零的 JSON，没有快照就只剩
        # 「按无进度全量重跑」一条路，已完成的账号与同心战斗场次全部白打。
        self.backup_path = Path(f'{self.path}.bak')
        # 异常归档文件：failed/skipped 迁移的按天留痕，独立于阶段生命周期，
        # 进度文件被 clear()/重建后仍可回查当天所有没有正常结束的子任务
        self.error_path = Path(base_dir) / f'{task_name}_errors_{config_name}.json'
        self._data: dict = {}

    # ---------- 底层读写 ----------

    @staticmethod
    def _read_json(path: Path) -> dict | None:
        """读取一份 JSON 进度文件；文件不存在或不可解析都返回 None。"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.warning(f'进度文件不可解析: {path} ({e})')
            return None
        return data if isinstance(data, dict) else None

    def _load(self) -> dict:
        """读取进度文件；不存在或损坏都返回空字典（当作无进度处理）。

        主文件**存在但损坏**时先回退快照：停电/断电可能让最后一次落盘留下截断
        或全零的 JSON，此时直接按无进度重跑会把已完成的账号整个重做一遍。
        主文件**不存在**时不看快照——删除进度文件是 README 写明的「强制全量
        重跑」手法，不能被一份陈旧快照悄悄复活。
        """
        if not self.path.exists():
            return {}
        data = self._read_json(self.path)
        if data is not None:
            return data
        backup = self._read_json(self.backup_path)
        if backup is not None:
            logger.warning(f'进度文件损坏，已从快照恢复: {self.backup_path}')
            return backup
        if self.generation is not None:
            raise ProgressReviewRequired(f'进度和快照均损坏，需核对后恢复：{self.path}')
        logger.warning('进度文件损坏且快照不可用，将按无进度全量重跑')
        return {}

    def _save(self) -> None:
        """保存进度；生产实例写失败停止消耗，旧工具接口保留日志降级行为。

        原子写的动机见 _write_json_atomic：同心战斗每打一场就落盘一次，
        截断 JSON 会让 _load() 按无进度全量重跑导致重复领奖。
        主文件落盘成功后再补写一份快照，供 _load() 在下次损坏时回退。
        """
        try:
            _write_json_atomic(self.path, self._data)
        except (OSError, TypeError, ValueError) as e:
            # TypeError/ValueError 同样可能导致本次已消耗场次无法持久化。
            logger.warning(f'进度文件写入失败，本次进度未持久化: {e}')
            if self.generation is not None:
                # 生产进度不能丢一次计数后继续消耗，否则下次重试会重复执行。
                raise ProgressReviewRequired(f'进度保存失败，停止自动执行：{self.path}') from e
            return
        try:
            _write_json_atomic(self.backup_path, self._data)
        except (OSError, TypeError, ValueError) as e:
            # 主文件已经写好了，快照失败只损失损坏回退能力，不影响本轮继续
            logger.warning(f'进度快照写入失败，本次未更新快照: {e}')

    def _is_stale(self, data: dict) -> bool:
        """判断进度是否因长期停机而过期。时间字段异常时同样视为过期。"""
        try:
            created = datetime.fromisoformat(data.get('created_at', ''))
        except (TypeError, ValueError):
            return True
        return (datetime.now() - created).total_seconds() > STALE_HOURS * 3600

    # ---------- 异常归档 ----------

    def _load_errors(self) -> dict:
        """读取异常归档；坏文件交给旁路调用方报错，不能覆盖为空表。"""
        if not self.error_path.exists():
            return {}
        try:
            with open(self.error_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError('错误归档根节点不是对象')
            return data
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.warning(f'异常归档文件损坏，保留历史记录: {e}')
            raise

    def _archive_error(self, key: str, task: str, status: str, **fields) -> None:
        """与管家清理共用文件锁，读改写完整串行，防止新增错误被旧快照覆盖。"""
        try:
            self.error_path.parent.mkdir(parents=True, exist_ok=True)
            with FileLock(str(self.error_path) + '.cleanup.lock'):
                self._archive_error_locked(key, task, status, **fields)
        except Exception as e:
            # 与原有旁路归档一致：锁或目录访问失败也不能阻断主任务。
            logger.warning(f'异常归档写入失败，本条记录未持久化: {e}')

    def _archive_error_locked(self, key: str, task: str, status: str, **fields) -> None:
        """把一条 failed/skipped 迁移追加到异常归档，按统一北京时间策略保留。

        必须在进度 _save() 之前调用（先归档后写进度）：极端情况下进程在两次落盘
        之间被强杀，进度仍为 pending，下轮重跑再失败会重复归档一条（轻微可接受）；
        若反序则该条归档永久丢失（进度已 failed 不再迁移）。宁可重复，不可丢失。
        每次追加前重新读取文件（read-modify-write），容忍外部手动编辑/删除。
        fields 会覆盖同名公共字段（time/phase_id/account/task/status/battle_count），
        调用方不要用这些键名传附加信息。
        """
        try:
            now = datetime.now(timezone(timedelta(hours=8)))
            record = {
                'time': now.strftime('%Y-%m-%d %H:%M:%S'),
                'phase_id': self._data.get('phase_id'),
                'generation': self.generation,
                'account': key,
                'task': task,
                'status': status,
            }
            # 计数型子任务附带已记计数，报告「进行到 N 后中断」
            battle = self.get_battle_count(key, task)
            if battle > 0:
                record['battle_count'] = battle
            record.update(fields)

            data = self._load_errors()
            today = now.strftime('%Y-%m-%d')
            from module.config.history_retention import retention_for
            policy = retention_for(self.error_path.parent, now=now)
            # 只裁剪可证实过期的日期组，非法日期/结构保留供人工核对。
            data = {day: items for day, items in data.items()
                    if not isinstance(items, list) or not policy.expired(day)}
            if today in data and not isinstance(data[today], list):
                raise ValueError('当天错误归档结构异常，保留原文件')
            data.setdefault(today, []).append(record)
            _write_json_atomic(self.error_path, data)
        except Exception as e:
            # 兜底捕 Exception 而非白名单：归档是旁路留痕，手动编辑文件可造成
            # 任意形状的异常，任何一种逃逸都会炸掉 mark_task、连带进度不落盘
            logger.warning(f'异常归档写入失败，本条记录未持久化: {e}')

    # ---------- 阶段生命周期 ----------

    def _before_replace_phase(self, data: dict) -> None:
        """业务薄封装可在确实重建前保全未通知成果，失败时抛错阻止覆盖。"""

    def _check_identity(self, data: dict) -> None:
        """同名不是同实例；主文件或回退快照都必须匹配本会话 generation。"""
        if self.generation is None or not data:
            return
        stored = data.get('generation')
        if not stored:
            raise ProgressReviewRequired(f'旧进度缺少实例代际，需核对归属：{self.path}')
        if stored != self.generation:
            raise ProgressReviewRequired(f'进度属于其他实例代际，禁止接续或覆盖：{self.path}')
        if data.get('schema_version', 1) not in (1, SCHEMA_VERSION):
            raise ProgressReviewRequired(f'进度版本无法识别，需核对：{self.path}')
        if not isinstance(data.get('accounts', {}), dict):
            raise ProgressReviewRequired(f'账号进度结构异常，需核对：{self.path}')
        for account in data.get('accounts', {}).values():
            if not isinstance(account, dict) or not isinstance(account.get('tasks', {}), dict):
                raise ProgressReviewRequired(f'子任务进度结构异常，需核对：{self.path}')
            for record in account.get('tasks', {}).values():
                if not isinstance(record, dict):
                    raise ProgressReviewRequired(f'子任务记录损坏，需核对：{self.path}')
                count = record.get('battle_count', 0)
                if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                    raise ProgressReviewRequired(f'已完成场次无法确认，需核对：{self.path}')

    def _reconcile_plans(self, data: dict, plans: dict) -> None:
        """逐账号和子任务核对参数，只重开受影响的完成标记，保留可信场次。"""
        previous = data.get('account_plans')
        accounts = data.setdefault('accounts', {})
        if previous is None and accounts:
            # 历史完成标记没有参数证据，不能猜测兼容后跳过或清零重打。
            raise ProgressReviewRequired(f'旧进度缺少执行参数，需核对：{self.path}')
        previous = previous or {}
        if not isinstance(previous, dict):
            raise ProgressReviewRequired(f'执行参数快照损坏，需核对：{self.path}')
        if any(not isinstance(tasks, dict) or any(not isinstance(plan, dict)
               for plan in tasks.values()) for tasks in previous.values()):
            raise ProgressReviewRequired(f'子任务参数结构损坏，需核对：{self.path}')
        # 有代际和参数证据的旧三段键，只有当前平台唯一时才能迁移。
        aliases = {}
        for key in plans:
            aliases.setdefault(key.rsplit('|', 1)[0], []).append(key)
        for old_key in list(accounts):
            if old_key in plans or old_key.count('|') != 2:
                continue
            matches = aliases.get(old_key, [])
            if len(matches) != 1 or old_key not in previous:
                raise ProgressReviewRequired(f'旧账号键无法唯一匹配平台，需核对：{self.path}')
            new_key = matches[0]
            if new_key in accounts or previous[old_key] != plans[new_key]:
                raise ProgressReviewRequired(f'旧账号参数无法证明兼容，需核对：{self.path}')
            accounts[new_key] = accounts.pop(old_key)
            previous[new_key] = previous.pop(old_key)
            for task, record in accounts[new_key].get('tasks', {}).items():
                if 'counter_target' in previous[new_key].get(task, {}) and 'battle_count' not in record:
                    # 旧版只记完成未记实际场数时，兼容参数可沿用完成，但修改目标须核对。
                    record['count_unverified'] = True
        for key, task_plans in plans.items():
            old_plans = previous.get(key, {})
            account = accounts.get(key)
            if account is None:
                continue
            if old_plans != task_plans:
                account['status'] = STATUS_PENDING
            for task, current in task_plans.items():
                old = old_plans.get(task)
                record = account.setdefault('tasks', {}).get(task)
                if old == current or record is None:
                    continue
                if old is None:
                    raise ProgressReviewRequired(f'子任务缺少旧参数证据，需核对：{self.path}')
                # 仅开关变化不撤销已经完成的同一任务成果，重新启用不会重复消耗。
                before = {k: v for k, v in old.items() if k != 'enabled'}
                after = {k: v for k, v in current.items() if k != 'enabled'}
                if before == after:
                    continue
                if record.get('count_unverified'):
                    raise ProgressReviewRequired(f'旧版缺少可信实际场数，需核对：{self.path}')
                count = record.get('battle_count', 0)
                if count and old.get('counter_scope') != current.get('counter_scope'):
                    raise ProgressReviewRequired(f'已有场次的计数口径发生变化，需核对：{self.path}')
                record['status'] = STATUS_PENDING
                record.pop('false_count', None)
        # 暂时移除的账号仍保留旧计划，重新加入时能证明历史成果是否兼容。
        previous.update(copy.deepcopy(plans))
        data['account_plans'] = previous

    def ensure_phase(self, phase_flags: dict, phase_id: str, *,
                     account_plans: dict | None = None) -> bool:
        """确保进度文件对应当前调度阶段。

        phase_flags 只定义日期/轮转时段；实际执行参数放 account_plans，变化时
        逐角色逐子任务核对。旧接口仍兼容新增阶段字段；新版缺身份或参数证据则
        保留原文件并要求核对，绝不靠清零修复未知进度。
        :return: True 表示新建/重建（无可用进度），False 表示接续已有进度
        """
        data = self._load()
        self._check_identity(data)
        phase_flags = execution_parameters(phase_flags)
        plans = execution_parameters(account_plans) if account_plans is not None else None
        if self.generation is not None and data.get('accounts') and plans is not None:
            if not isinstance(data.get('account_plans'), dict):
                # 即使轮次字段恰好不同，也不能把参数来源不明的旧进度直接清零。
                raise ProgressReviewRequired(f'旧进度缺少执行参数，需核对：{self.path}')
            if not isinstance(data.get('phase_flags'), dict):
                raise ProgressReviewRequired(f'轮次标识无法确认，需核对：{self.path}')
            if not all(name in data['phase_flags'] for name in phase_flags):
                raise ProgressReviewRequired(f'旧进度缺少轮次依据，需核对：{self.path}')
        resumable = (
            bool(data)
            and phase_flags_match(data.get('phase_flags'), phase_flags)
            # 新版轮次标识含日期/时段；同轮停机超过18小时不能清零已消耗的场次。
            and (self.generation is not None or not self._is_stale(data))
        )
        if resumable:
            revised = copy.deepcopy(data)
            if plans is not None:
                self._reconcile_plans(revised, plans)
            revised.update(schema_version=SCHEMA_VERSION, generation=self.generation)
            self._data = revised
            # 老快照缺少新增开关时把当轮快照补进去并落盘：缺失键不参与比较，
            # 不补齐的话该开关之后取值变化会一直被忽略。
            if revised != data or data.get('phase_flags') != phase_flags:
                revised['phase_flags'] = dict(phase_flags)
                self._save()
            logger.info(f'接续已有进度: {self.path} phase={data.get("phase_id")}')
            return False

        self._before_replace_phase(data)
        self._data = {
            'schema_version': SCHEMA_VERSION,
            'generation': self.generation,
            'phase_id': phase_id,
            'phase_flags': dict(phase_flags),
            'created_at': datetime.now().isoformat(),
            'accounts': {},
        }
        if plans is not None:
            self._data['account_plans'] = copy.deepcopy(plans)
        self._save()
        logger.info(f'创建新的进度: {self.path} phase={phase_id}')
        return True

    @property
    def phase_token(self) -> str:
        """接续时保持轮次身份；创建时刻可区分同一分钟内新建的两个阶段。"""
        return f'{self._data.get("phase_id", "")}|{self._data.get("created_at", "")}'

    def clear(self) -> None:
        """删除进度文件、快照与残留的 tmp。任务成功并安排下一阶段后调用。

        快照必须一起删：本轮进度已作废，留着它只会让下一轮意外回退到旧进度。
        tmp 是写入中途被强杀留下的半截文件，收尾时一并清掉——注意 with_suffix
        只替换最后一段后缀，所以主文件的是 x.json.tmp、快照的是 x.json.json.tmp。
        """
        self._data = {}
        for path in (self.path, self.backup_path,
                     self.path.with_suffix('.json.tmp'),
                     self.backup_path.with_suffix('.json.tmp')):
            try:
                path.unlink(missing_ok=True)
            except OSError as e:
                logger.warning(f'进度文件删除失败: {path} ({e})')

    # ---------- 账号级 ----------

    def _account(self, key: str) -> dict:
        """取出（必要时创建）账号条目。"""
        accounts = self._data.setdefault('accounts', {})
        return accounts.setdefault(key, {'status': STATUS_PENDING, 'tasks': {}})

    def is_account_done(self, key: str) -> bool:
        """账号是否已在本阶段完整跑完。"""
        account = self._data.get('accounts', {}).get(key)
        return bool(account) and account.get('status') == STATUS_DONE

    def has_pending_tasks(self, key: str, enabled_tasks) -> bool:
        """该账号本轮启用的子任务里是否还有未了结的。

        用于避免「账号被标 done、但里面还有 pending 子任务」——例如 courtyard
        进入超时返回 False 保持 pending，若此时把账号标 done，下次接续会整个
        跳过该账号，那个 pending 子任务永远补不回来，导致漏领奖励。
        """
        return any(not self.is_task_finished(key, task) for task in enabled_tasks)

    def mark_account_done(self, key: str) -> None:
        """标记账号本阶段已完成，后续接续时整个跳过。"""
        self._account(key)['status'] = STATUS_DONE
        self._save()

    # ---------- 子任务级 ----------

    def get_task(self, key: str, task: str) -> dict:
        """返回子任务记录，没有记录则返回空字典。"""
        return self._data.get('accounts', {}).get(key, {}).get('tasks', {}).get(task, {})

    def is_task_finished(self, key: str, task: str) -> bool:
        """done / failed / skipped 都算已了结，接续时都跳过。"""
        return self.get_task(key, task).get('status') in (
            STATUS_DONE, STATUS_FAILED, STATUS_SKIPPED)

    def mark_task(self, key: str, task: str, status: str, **extra) -> bool:
        """记录子任务状态。

        :return: True 表示本次是「pending/无记录 → failed」的首次迁移，调用方据此
                 只发一封异常邮件；重复标记 failed 或从已了结状态改写为 failed
                 均返回 False，避免每轮重调度都轰炸邮箱。
        """
        tasks = self._account(key).setdefault('tasks', {})
        previous = tasks.get(task, {}).get('status')
        transitioned = status == STATUS_FAILED and previous in (None, STATUS_PENDING)
        if transitioned:
            # 首次 failed 迁移同步留痕到异常归档（先归档后写进度，见 _archive_error）
            self._archive_error(key, task, STATUS_FAILED, **extra)
        record = tasks.setdefault(task, {})
        record['status'] = status
        record.update(extra)
        self._save()
        return transitioned

    def mark_task_false(self, key: str, task: str) -> bool:
        """记录一次「显式返回 False」（业务上未完成，非异常）。

        首次保持 pending（保留一次进程内重跑机会）；累计达到 FALSE_LIMIT 迁移
        终态 skipped——False 语义过载（UI 卡顿该重试 / 本来就无事可做该放行），
        截图层无法区分，不设上限会让账号永远无法收尾、任务每 10 分钟无限重调度。
        已了结（done/failed/skipped）后再报 False 不计数、不重复迁移。
        :return: True 表示本次达到上限迁移为 skipped，调用方据此记一条 warning
        """
        if self.is_task_finished(key, task):
            return False
        tasks = self._account(key).setdefault('tasks', {})
        record = tasks.setdefault(task, {'status': STATUS_PENDING})
        count = record.get('false_count', 0)
        record['false_count'] = count + 1 if isinstance(count, int) else 1
        reached = record['false_count'] >= FALSE_LIMIT
        if reached:
            # 达到上限迁移 skipped 同步留痕到异常归档（先归档后写进度，见 _archive_error）
            self._archive_error(key, task, STATUS_SKIPPED, false_count=record['false_count'])
            record['status'] = STATUS_SKIPPED
        self._save()
        return reached

    # ---------- 计数类子任务 ----------

    def get_battle_count(self, key: str, task: str = 'alliedteam') -> int:
        """读取已完成场次；生产进度计数异常时停止，不能猜成零再打。"""
        record = self.get_task(key, task)
        if self.generation is not None and record.get('count_unverified'):
            raise ProgressReviewRequired(f'旧版缺少可信实际场数，需核对：{self.path}')
        value = record.get('battle_count', 0)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        if self.generation is not None:
            raise ProgressReviewRequired(f'已完成场次无法确认，需核对：{self.path}')
        return 0

    def add_battle_count(self, key: str, n: int = 1, task: str = 'alliedteam') -> int:
        """每完成一次立刻累加并落盘，崩溃后据此接续剩余次数。

        :return: 累加后的总次数
        """
        tasks = self._account(key).setdefault('tasks', {})
        record = tasks.setdefault(task, {'status': STATUS_PENDING})
        total = self.get_battle_count(key, task) + n
        record['battle_count'] = total
        self._save()
        return total
