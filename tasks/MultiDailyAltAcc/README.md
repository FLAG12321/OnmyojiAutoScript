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
- `need_login`: 是否无视时间强制登录
- `need_login_time`: 登录时间基准点
- `shutdown_after_finish`: 完成后是否关机

### 账号配置（ExtendedAccountInfo）

每个小号可单独覆盖全局开关，支持精细化控制。

## 执行流程

1. 读取配置，获取小号列表
2. 按邮箱分组排序（减少邮箱切换次数），按完成时间排序
3. 过滤已完成的账号（基于 `need_login` 和 `login_time`）
4. 依次切换到每个小号，调用 `DailyAltAcc` 执行日常任务
5. 每个账号最多重试 3 次
6. 全部完成后根据配置决定是否关机

## 注意事项

1. 需要额外开启一个模拟器用于切换登录小号
2. 小号配置相关信息见 [SwitchAccount](../Component/SwitchAccount/README.md)
3. 要求被邀请对象处于登录状态，与小号是好友关系
4. 进度追踪基于文件（`./logs/daily_progress.json`），支持多进程并行
