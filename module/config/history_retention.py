# -*- coding: utf-8 -*-
"""两端共用的历史保留协议；此文件与 Butler bridge/history_retention.py 保持一致。

只读取共享策略，不创建实例备份。缺失策略采用北京含今天七天；损坏策略拒绝裁剪。
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BEIJING = timezone(timedelta(hours=8))
DEFAULT_RETENTION_DAYS = 7
LONGEST_RETENTION_DAYS = 3650
POLICY_VERSION = 1
POLICY_FILE = "history_retention.json"


def timestamp_day(value):
    """无时区时间按北京解释；非法、空白及非标准日期均保留待核对。"""
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or BEIJING).astimezone(BEIJING).date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        if not re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", value):
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or BEIJING).astimezone(BEIJING).date()
    except (ValueError, OverflowError):
        return None


def validate_options(options):
    """两端必须理解同一协议版本，不能在未知策略下继续清历史。"""
    if not isinstance(options, dict) or options.get("version") != POLICY_VERSION:
        raise ValueError("历史保留策略版本无效，请更新 OAS 与管家")
    days, enabled = options.get("retention_days"), options.get("enabled")
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= LONGEST_RETENTION_DAYS:
        raise ValueError("历史保留天数必须是 1 至 3650 的整数")
    if not isinstance(enabled, bool):
        raise ValueError("历史保留策略开关必须是布尔值")
    protected = options.get("protected_dates", [])
    protected_ids = options.get("protected_ids", [])
    if (not isinstance(protected, list) or any(timestamp_day(value) is None for value in protected)
            or not isinstance(protected_ids, list) or any(not isinstance(value, str) for value in protected_ids)):
        raise ValueError("历史保留保护范围无效，已保留原记录")
    return {"version": POLICY_VERSION, "retention_days": days, "enabled": enabled,
            "protected_dates": protected, "protected_ids": protected_ids}


def options_for(history_dir):
    """策略单例位于 tasks_config，不能进入会被当作实例的 config 根目录。"""
    path = Path(history_dir) / POLICY_FILE
    try:
        return validate_options(json.loads(path.read_text(encoding="utf-8-sig")))
    except FileNotFoundError:
        return validate_options({"version": POLICY_VERSION, "retention_days": DEFAULT_RETENTION_DAYS,
                                 "enabled": True})
    except (OSError, ValueError) as exc:
        raise ValueError(f"无法读取历史保留策略，已保留原记录：{exc}") from exc


class HistoryRetention:
    """所有日期、协作窗口和通知分组都通过此对象判断，未知信息永不推断。"""

    def __init__(self, retention_days=DEFAULT_RETENTION_DAYS, *, now=None, protect=(),
                 protected_ids=(), enabled=True):
        options = validate_options({"version": POLICY_VERSION, "retention_days": retention_days,
                                    "enabled": enabled})
        self.enabled = options["enabled"]
        self.retention_days = options["retention_days"]
        self.today = timestamp_day(now or datetime.now(BEIJING))
        if self.today is None:
            raise ValueError("当前日期无效，不能清理历史")
        self.cutoff = self.today - timedelta(days=self.retention_days - 1)
        self.protected = {timestamp_day(value) for value in protect}
        self.protected_ids = set(protected_ids)

    def expired(self, value):
        """截止日、未来日期和正在查看的日期都在保留范围内。"""
        day = timestamp_day(value)
        return self.enabled and day is not None and day < self.cutoff and day not in self.protected

    def cooperation_expired(self, row):
        """跨午夜窗口要求发现与失效时间都过期，引用中的完成证据也保留。"""
        return (isinstance(row, dict) and row.get("id") not in self.protected_ids
                and self.expired(row.get("found_at")) and self.expired(row.get("expires_at")))

    def shop_expired(self, row):
        """商店依采集时间清理；附带失效时间存在时同样必须过期。"""
        return (isinstance(row, dict) and self.expired(row.get("found_at"))
                and ("expires_at" not in row or self.expired(row["expires_at"])))

    def archive_expired(self, group):
        """归档日期不能替代实际业务时间；混合日期分组整体保留。"""
        if not isinstance(group, dict) or not self.expired(group.get("archived_at")):
            return False
        coops, shops = group.get("coops", []), group.get("mshops", [])
        return (isinstance(coops, list) and isinstance(shops, list)
                and all(self.cooperation_expired(row) for row in coops)
                and all(self.shop_expired(row) for row in shops))

    def notification_expired(self, row):
        """待发永久保护；已发送也须核实投递、入箱和内部实际业务时间。"""
        return (isinstance(row, dict) and row.get("status") == "sent"
                and self.expired(row.get("sent_at"))
                and self.archive_expired(dict(row, archived_at=row.get("created_at"))))


def retention_for(history_dir, *, now=None, protect=(), protected_ids=()):
    """写入端每次裁剪前重新读取共享策略，不缓存旧的保留天数。"""
    options = options_for(history_dir)
    return HistoryRetention(options["retention_days"], enabled=options["enabled"], now=now,
                            protect=tuple(protect) + tuple(options["protected_dates"]),
                            protected_ids=tuple(protected_ids) + tuple(options["protected_ids"]))
