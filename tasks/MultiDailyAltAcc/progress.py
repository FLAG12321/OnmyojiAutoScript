# This Python file uses the following encoding: utf-8
"""MultiDailyAltAcc 子任务进度持久化。

用独立文件记录「每账号 × 每子任务」的执行状态，使任务中断或异常后能够精确接续：
已完成的账号与子任务直接跳过，同心战斗这类计数子任务从已记场次继续。
本模块只做文件读写，不依赖 device / Config，便于单元测试。
"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from module.logger import logger

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

# 进度陈旧阈值（小时）。正常相邻调度阶段间隔不超过该值，
# 超过则认为是脚本长期停机后的残留文件，直接作废重建。
STALE_HOURS = 18


def acc_key(account: str, character: str, svr) -> str:
    """账号在进度文件中的唯一标识。svr 可能是枚举，统一转字符串。"""
    svr_name = getattr(svr, 'name', None) or str(svr)
    return f'{account}|{character}|{svr_name}'


# 参与阶段判定的开关白名单：只收 _schedule_* 会改写的键。
# 显式白名单而非 startswith('total_')，因为 total_KekkaiUtilize_enable 会在
# 运行期被 MSGType.Utilize（未找到寄养卡）改写并落盘——若纳入快照，另一账号
# 失败重调度时会被误判成新阶段，重建进度导致已完成账号全部重跑、重复领奖。
# total_donatejade_enable / total_kekkaiActivation_enable 是静态用户配置，
# 不随阶段变化，同样排除。
PHASE_FLAG_KEYS = (
    'total_alliedteam_battle_enable',
    'total_alliedteam_ap_enable',
    'total_returngift_enable',
    'total_courtyard_enable',
    'total_mail_enable',
    'total_cooperation_enable',
    'total_weekaward_enable',
    'total_mysteryshop_enable',
    # 单次运行标志：由 _reset_one_shot_flags 在成功收尾时清零，属于阶段语义
    'total_tree_planting_enable',
    'total_trialbattle_enable',
    'total_summon_up_enable',
    'total_publish_sr_enable',
)


def phase_flags_of(base_config) -> dict:
    """提取决定「本轮做什么」的全局开关快照，用于判断调度阶段是否延续。

    这些键由 _schedule_normal_day / _schedule_evening / _schedule_after_midnight /
    _schedule_alliedteam_after_returngift 在成功收尾时改写，因此快照变化恰好
    等价于「任务已分配下一次要做什么」，正是进度失效的边界。
    need_login / need_login_time 不纳入：失败分支会改写 need_login，
    若纳入会把失败重试误判成新阶段。
    """
    return {name: getattr(base_config, name, None) for name in PHASE_FLAG_KEYS}


def phase_id_of(start_time) -> str:
    """生成人类可读的阶段标签（仅用于日志排查，不参与阶段判定）。"""
    return start_time.strftime('%Y%m%d-%H%M')


def _write_json_atomic(path: Path, data: dict) -> None:
    """tmp 文件 + os.replace 原子写 JSON；异常由调用方按各自语义处理。

    进度文件与异常归档文件共用：直接覆写时进程被杀会留下截断 JSON，
    os.replace 在 Windows 上对已存在目标是原子替换，可消除该窗口。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix('.json.tmp')
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


class ProgressStore:
    """进度文件的读写封装，每个配置实例（oas1/oas2）一个文件，互不干扰。"""

    def __init__(self, config_name: str, base_dir='config/tasks_config'):
        self.config_name = config_name
        self.path = Path(base_dir) / f'multi_daily_progress_{config_name}.json'
        # 异常归档文件：failed/skipped 迁移的按天留痕，独立于阶段生命周期，
        # 进度文件被 clear()/重建后仍可回查当天所有没有正常结束的子任务
        self.error_path = Path(base_dir) / f'multi_daily_errors_{config_name}.json'
        self._data: dict = {}

    # ---------- 底层读写 ----------

    def _load(self) -> dict:
        """读取进度文件；不存在或损坏都返回空字典（当作无进度处理）。"""
        if not self.path.exists():
            return {}
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.warning(f'进度文件损坏，将按无进度全量重跑: {e}')
            return {}

    def _save(self) -> None:
        """保存进度；写失败只记日志，绝不阻断任务执行。

        原子写的动机见 _write_json_atomic：同心战斗每打一场就落盘一次，
        截断 JSON 会让 _load() 按无进度全量重跑导致重复领奖。
        """
        try:
            _write_json_atomic(self.path, self._data)
        except (OSError, TypeError, ValueError) as e:
            # TypeError/ValueError：extra 混入不可序列化对象时 json.dump 抛出，
            # 同样只记日志——进度丢一次没关系，炸掉任务流程才是事故
            logger.warning(f'进度文件写入失败，本次进度未持久化: {e}')

    def _is_stale(self, data: dict) -> bool:
        """判断进度是否因长期停机而过期。时间字段异常时同样视为过期。"""
        try:
            created = datetime.fromisoformat(data.get('created_at', ''))
        except (TypeError, ValueError):
            return True
        return (datetime.now() - created).total_seconds() > STALE_HOURS * 3600

    # ---------- 异常归档 ----------

    def _load_errors(self) -> dict:
        """读取异常归档文件；不存在或损坏按空处理（丢历史但不崩，下次写入重建）。"""
        if not self.error_path.exists():
            return {}
        try:
            with open(self.error_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.warning(f'异常归档文件损坏，历史记录将被重建: {e}')
            return {}

    def _archive_error(self, key: str, task: str, status: str, **fields) -> None:
        """把一条 failed/skipped 迁移追加到异常归档文件（按天分组，只保留今昨两组）。

        必须在进度 _save() 之前调用（先归档后写进度）：极端情况下进程在两次落盘
        之间被强杀，进度仍为 pending，下轮重跑再失败会重复归档一条（轻微可接受）；
        若反序则该条归档永久丢失（进度已 failed 不再迁移）。宁可重复，不可丢失。
        每次追加前重新读取文件（read-modify-write），容忍外部手动编辑/删除。
        fields 会覆盖同名公共字段（time/phase_id/account/task/status/battle_count），
        调用方不要用这些键名传附加信息。
        """
        try:
            now = datetime.now()
            record = {
                'time': now.strftime('%Y-%m-%d %H:%M:%S'),
                'phase_id': self._data.get('phase_id'),
                'account': key,
                'task': task,
                'status': status,
            }
            # 计数型子任务（同心战斗）附带已打场次，报告「已打 N 场后中断」
            battle = self.get_battle_count(key, task)
            if battle > 0:
                record['battle_count'] = battle
            record.update(fields)

            data = self._load_errors()
            today = now.strftime('%Y-%m-%d')
            yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')
            # 保留策略：只留今昨两个日期组，写入时顺手清理更早的；
            # 组值非 list（文件被手动改坏）按损坏丢弃，防止后面 append 炸掉
            data = {day: items for day, items in data.items()
                    if day in (today, yesterday) and isinstance(items, list)}
            data.setdefault(today, []).append(record)
            _write_json_atomic(self.error_path, data)
        except Exception as e:
            # 兜底捕 Exception 而非白名单：归档是旁路留痕，手动编辑文件可造成
            # 任意形状的异常，任何一种逃逸都会炸掉 mark_task、连带进度不落盘
            logger.warning(f'异常归档写入失败，本条记录未持久化: {e}')

    # ---------- 阶段生命周期 ----------

    def ensure_phase(self, phase_flags: dict, phase_id: str) -> bool:
        """确保进度文件对应当前调度阶段。

        阶段是否延续由「当轮生效的全局开关快照」判定：失败重调度不会改开关，
        因此快照一致 → 接续；正常完成后 _schedule_* 会改写开关安排下一阶段，
        快照不一致 → 重建。
        :return: True 表示新建/重建（无可用进度），False 表示接续已有进度
        """
        data = self._load()
        resumable = (
            bool(data)
            and data.get('phase_flags') == phase_flags
            and not self._is_stale(data)
        )
        if resumable:
            self._data = data
            logger.info(f'接续已有日常进度: {self.path} phase={data.get("phase_id")}')
            return False

        self._data = {
            'phase_id': phase_id,
            'phase_flags': dict(phase_flags),
            'created_at': datetime.now().isoformat(),
            'accounts': {},
        }
        self._save()
        logger.info(f'创建新的日常进度: {self.path} phase={phase_id}')
        return True

    def clear(self) -> None:
        """删除进度文件。任务成功并安排下一阶段后调用。"""
        self._data = {}
        try:
            self.path.unlink(missing_ok=True)
        except OSError as e:
            logger.warning(f'进度文件删除失败: {e}')

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

    # ---------- 计数类子任务（同心战斗） ----------

    def get_battle_count(self, key: str, task: str = 'alliedteam') -> int:
        """已完成场次。数值异常时按 0 处理（宁可多打，不能崩）。"""
        value = self.get_task(key, task).get('battle_count', 0)
        return value if isinstance(value, int) and value >= 0 else 0

    def add_battle_count(self, key: str, n: int = 1, task: str = 'alliedteam') -> int:
        """每完成一场立刻累加并落盘，崩溃后据此接续剩余场次。

        :return: 累加后的总场次
        """
        tasks = self._account(key).setdefault('tasks', {})
        record = tasks.setdefault(task, {'status': STATUS_PENDING})
        total = self.get_battle_count(key, task) + n
        record['battle_count'] = total
        self._save()
        return total
