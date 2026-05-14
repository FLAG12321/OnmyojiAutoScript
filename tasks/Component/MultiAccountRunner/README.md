# MultiAccountRunner - 多账号轮转执行器

## 概述

`MultiAccountRunner` 是一个通用的多账号任务执行框架，封装了多账号轮转场景下的公共逻辑，供所有需要多账号切换执行的任务复用。

## 解决的问题

在 Daily、MultiAccExp、FindJade 等多账号任务中，以下逻辑被反复实现：
- 账号过滤（根据 `need_login` 和 `login_time` 判断是否需要处理）
- 账号排序（按邮箱分组、按完成时间排序）
- 账号切换（调用 `SwitchAccount`）
- 重试机制（失败后重试）
- 进度追踪（基于文件的多进程协调）
- 错误处理（异常时恢复 `need_login_time`）

这些逻辑原本分散在各任务的 `script_task.py` 中，代码高度重复且容易因修改不同步而产生 bug（如排序逻辑漏洞）。

## 核心设计

### 账号排序逻辑

**先剔除，再排序**，避免同一邮箱下已完成账号影响未完成账号的排序：

1. **过滤**：当 `need_login=False` 时，剔除 `last_complete_time >= login_time` 的账号
2. **分组**：按邮箱（account）分组，使同一邮箱下的角色连续排列（减少切换邮箱的次数）
3. **组间排序**：按每个邮箱分组的最晚完成时间排序（最晚完成的邮箱在前）
4. **组内排序**：同一邮箱内，按角色的完成时间排序（最晚完成的在前）

### login_time 参数

`login_time` 必须在任务开始时从配置中读取并传入 `MultiAccountRunner`，而不是在函数内部实时读取。因为 `need_login_time` 的值会在运行过程中变化，排序和过滤需要基于任务开始时的快照值。

### 进度追踪

基于 JSON 文件的多进程进度追踪，每个任务有独立的进度文件（`./logs/{task_name}_progress.json`），用于协调多个配置实例的并行执行。

## 使用方式

### 基本用法

```python
from tasks.Component.MultiAccountRunner.multi_account_runner import MultiAccountRunner

# 在 script_task.py 的 run() 方法中
def run(self):
    self.daily_conf = self.config.daily
    login_time = self.daily_conf.daily_config.need_login_time

    runner = MultiAccountRunner(
        task_name="Daily",
        config=self.config,
        device=self.device,
        account_list=self.daily_conf.sup_account_list,
        need_login=self.daily_conf.daily_config.need_login,
        login_time=login_time,
        update_login_history_func=self.daily_conf.update_account_login_history,
        save_config_func=self.save_config,
    )

    # 传入自定义的账号处理函数
    runner.run(process_func=self._process_single_account)
```

### 自定义处理函数

`process_func` 接收一个 `AccountInfo` 参数，返回 `True`（成功）或 `False`（失败）：

```python
def _process_single_account(self, account_info):
    """处理单个账号"""
    # 1. 切换账号
    if not self.runner.switch_to_account(account_info):
        return False

    # 2. 执行任务逻辑
    try:
        # ... 你的任务逻辑 ...
        return True
    except Exception as e:
        logger.error(f"Error: {e}")
        return False
```

### 错误回调

可通过 `on_account_error` 参数注册错误回调，用于在账号处理异常时执行特定逻辑（如更新配置、发送通知等）：

```python
def _on_account_error(self, account_info, error):
    self.multi_acc_conf.multi_acc_exp_config.need_login = False
    self.multi_acc_conf.multi_acc_exp_config.need_login_time = self.runner.login_time
    self.save_config()

runner = MultiAccountRunner(
    ...,
    on_account_error=self._on_account_error,
)
```

### 单独使用排序/过滤功能

如果只需要排序功能而不需要完整的执行流程：

```python
runner = MultiAccountRunner(
    task_name="MyTask",
    config=self.config,
    device=self.device,
    account_list=my_account_list,
    need_login=False,
    login_time=my_login_time,
    update_login_history_func=my_update_func,
    save_config_func=my_save_func,
)

sorted_accounts = runner.get_sorted_accounts()
for account in sorted_accounts:
    runner.switch_to_account(account)
    # ... 自定义逻辑 ...
```

### 自定义过滤逻辑

如果某个任务的过滤逻辑不同于默认实现，可以继承 `MultiAccountRunner` 并重写 `should_process_account`：

```python
class FindJadeRunner(MultiAccountRunner):
    def should_process_account(self, account_info: AccountInfo) -> bool:
        """FindJade 使用基于时间跨度的判断逻辑"""
        now = datetime.now()
        last_time = account_info.last_complete_time
        if now - last_time > timedelta(hours=13):
            return True
        if (last_time.hour >= 18 or last_time.hour < 5) and (18 > now.hour >= 5):
            return True
        if (5 <= last_time.hour < 18) and now.hour >= 18:
            return True
        return False
```

## API 参考

### MultiAccountRunner 构造参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `task_name` | `str` | 任务名称，用于日志和进度文件 |
| `config` | `Config` | 配置实例 |
| `device` | `Device` | 设备实例 |
| `account_list` | `List[AccountInfo]` | 账号列表 |
| `need_login` | `bool` | 是否强制登录 |
| `login_time` | `datetime` | 登录时间基准（任务开始时的快照） |
| `update_login_history_func` | `Callable` | 更新账号登录历史的回调 |
| `save_config_func` | `Callable` | 保存配置的回调 |
| `max_retries` | `int` | 最大重试次数，默认 3 |
| `on_account_error` | `Optional[Callable]` | 账号异常回调，可选 |

### 主要方法

| 方法 | 说明 |
|------|------|
| `run(process_func)` | 执行多账号轮转任务的主入口 |
| `get_sorted_accounts()` | 获取排序后的账号列表（已过滤） |
| `should_process_account(account_info)` | 判断是否需要处理该账号 |
| `switch_to_account(account_info)` | 切换到指定账号 |
