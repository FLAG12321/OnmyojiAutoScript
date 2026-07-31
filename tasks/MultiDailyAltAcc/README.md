# MultiDailyAltAcc - 多账号小号每日任务

## 概述

`MultiDailyAltAcc` 是多账号小号每日任务的调度器，负责在多个小号之间轮转切换并执行每日例行操作。每个小号的具体日常逻辑由 `DailyAltAcc` 任务完成。

## 功能

对配置的小号列表依次执行以下可选子任务（通过 `DailyAltAcc` 实现）：

| 子任务 | 配置字段 | 说明 |
|--------|----------|------|
| 庭院事务 | `courtyard_enable` | 签到、领取庭院奖励 |
| 邮件 | `mail_enable` | 领取邮件附件 |
| 协作 | `cooperation_enable` | 寻找协作任务 |
| 捐勾玉 | `donatejade_enable` | 寮捐勾玉 |
| 回礼 | `returngift_enable` | 好友回礼 |
| 周奖励 | `weekaward_enable` | 领取寮周奖励、商店 |
| 神秘商店 | `mysteryshop_enable` | 刷新并购买神秘商店 |
| 种树 | `tree_planting_enable` | 0=不运行 1=买花 2=买花捐树 |
| 试炼战斗 | `trialbattle_enable` | 每日试炼 |
| UP召唤 | `summon_up_enable` | 领取UP召唤礼包 |
| 挂卡 | `kekkaiActivation_enable` | 结界挂卡 |
| 蹭卡 | `KekkaiUtilize_enable` | 结界蹭卡 |
| 同心队 | `alliedteam_battle_enable` | 同心队战斗 |
| 补体力 | `alliedteam_ap_enable` | 同心队补充体力 |

## 架构

```
MultiDailyAltAcc/          # 多账号调度层
├── script_task.py         # 主调度器：账号排序、切换、重试、进度追踪
├── config.py              # 配置模型（MultiDailyAltAcc, DailyConfig, ExtendedAccountInfo）
├── assets.py              # 资源定义
├── DailyAltAccEx.py       # 桥接模块：为 DailyAltAcc 注入账号级配置
└── README.md

DailyAltAcc/               # 单账号执行层
├── script_task.py         # 单账号日常任务入口
├── config.py              # 单账号配置（DailyAltAccConfig）
├── courtyard.py           # 庭院事务
├── mail.py                # 邮件
├── cooperation.py         # 协作
├── donatejade.py          # 捐勾玉
├── returngift.py          # 回礼
├── mshop.py               # 神秘商店
├── tree.py                # 种树
├── trialbattle.py         # 试炼战斗
├── summon_up.py           # UP召唤
├── alliedteam.py          # 同心队
└── utils.py               # 公共基类 DailyAltAccBase
```

## 配置

### 全局配置（DailyConfig）

在 GUI 中配置，控制所有小号的全局开关：

- `sup_account_count`: 小号数量
- `total_*_enable`: 各子任务的全局开关
- `need_login` / `need_login_time`: 已弃用（见下方「子任务进度持久化」），仅为兼容保留
- `shutdown_after_finish`: 完成后是否关机

### 账号配置（ExtendedAccountInfo）

每个小号可单独覆盖全局开关，支持精细化控制。

## 执行流程

1. 读取配置，获取小号列表
2. 按邮箱分组排序（减少邮箱切换次数），按完成时间排序
3. 过滤已完成的账号（基于进度文件 `logs/multi_daily_progress_<配置名>.json`）
4. 依次切换到每个小号，调用 `DailyAltAcc` 执行日常任务
5. 每个账号最多重试 3 次
6. 全部完成后根据配置决定是否关机

## 注意事项

1. 需要额外开启一个模拟器用于切换登录小号
2. 小号配置相关信息见 [SwitchAccount](../Component/SwitchAccount/README.md)
3. 要求被邀请对象处于登录状态，与小号是好友关系
4. 多开实例的运行状态追踪基于文件（`./logs/multidailyaltacc_progress.json`），支持多进程并行

## 子任务进度持久化与异常恢复

进度文件：`logs/multi_daily_progress_<配置名>.json`，每个实例一份。

- 账号与子任务的完成状态都记在这里，任务中断（崩溃、断电、失败重调度）后
  自动接续：已完成的账号整个跳过，已完成的子任务跳过，同心战斗从已记场次
  继续打剩余次数。
- 子任务抛业务异常时标记为 `failed`，推送一封「子任务异常已跳过」通知，
  本轮后续接续不再重试该子任务；**该跳过只对当前角色生效**，其他角色的
  同名子任务照常执行。
- 子任务连续 2 次显式返回未完成（如庭院当轮无奖可领、邮箱为空）会被标记
  为 `skipped` 放行，只记日志不发通知，避免账号因「永远无法完成」的子任务
  无限重试。
- 每条 `failed` / `skipped` 迁移会同步追加到异常归档文件
  `logs/multi_daily_errors_<配置名>.json`（按天分组，写入时自动只保留今天和昨天
  两组），进度文件被清空或重建后仍可在此回查当天所有没有正常结束的子任务；
  `failed` 记录含异常类型与消息，`skipped` 记录含累计 False 次数，同心战斗
  附带已打场次。归档写入失败只记日志，不影响任务执行。
- 进度在任务成功收尾、安排下一调度阶段时自动清除。若想强制全量重跑，
  删除该进度文件即可（异常通知里附了文件路径）。
- 进度超过 18 小时视为过期：某阶段连续失败重调度超过该时长后会全量重建，
  已完成的账号与同心战斗场次会重新执行（宁多跑不漏跑的兜底）。
- `need_login` / `need_login_time` 已弃用，不再影响任何判定。
