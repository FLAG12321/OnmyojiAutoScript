# This Python file uses the following encoding: utf-8
"""MultiDailyAltAcc 子任务进度持久化（薄封装）。

通用实现位于 tasks/Component/MultiAccountRunner/progress.py，本模块只做两件事：
1. 固定 task_name='multi_daily'，保证进度文件名仍为 multi_daily_progress_<config>.json；
2. 提供 MultiDailyAltAcc 专属的阶段判定辅助（phase_flags_of / phase_id_of）。

本模块只做文件读写，不依赖 device / Config，便于单元测试。
"""
from datetime import datetime

from tasks.Component.MultiAccountRunner.progress import (
    # 重新导出，保持既有 import 面不变：
    # tasks/MultiDailyAltAcc/script_task.py 与 tasks/DailyAltAcc/script_task.py 均从此处取
    FALSE_LIMIT,
    STALE_HOURS,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SKIPPED,
    ProgressStore as _BaseProgressStore,
    acc_key,
)

__all__ = [
    'FALSE_LIMIT',
    'STALE_HOURS',
    'STATUS_DONE',
    'STATUS_FAILED',
    'STATUS_PENDING',
    'STATUS_SKIPPED',
    'ProgressStore',
    'acc_key',
    'phase_flags_of',
    'phase_id_of',
]


class ProgressStore(_BaseProgressStore):
    """MultiDailyAltAcc 的进度存储，固定文件名 multi_daily_progress_<config>.json。"""

    def __init__(self, config_name: str, base_dir='config/tasks_config'):
        super().__init__('multi_daily', config_name, base_dir)


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
