"""采集故障归并与告警记录共用的领域规则。

这里只放跨模块复用的判断：故障归并键、严重程度排序、时间比较以及编号生成。
具体状态流转仍然留在 ``services/log_viewer.py`` 与 ``services/monitor.py`` 里。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

FAULT_MODULE = "log_faults"
ALERT_MODULE = "monitor_alerts"

# 故障处于这些状态时表示这一轮已经处置完。相同任务、相同堆栈再次出现时必须另开一条
# 新故障，不能把旧记录重新打开，否则首次出现时间、重复次数和告警都会被历史数据覆盖。
FAULT_TERMINAL = {"已处理", "已关闭", "已忽略"}
FAULT_ACTIVE = {"待处理", "处理中"}
FAULT_CLOSED = {"已关闭", "已忽略"}

# 告警只有关闭后才是终态；待领取表示还需要值班员接单。
ALERT_TERMINAL = {"已关闭", "已忽略"}
ALERT_ACTIVE = {"待领取", "处理中"}

# 日志级别是自由文本，中英文都认；未知级别按 0 处理，不会压过已知的高等级。
SEVERITY_RANKS: dict[str, int] = {
    "TRACE": 5,
    "DEBUG": 10,
    "INFO": 20,
    "NOTICE": 25,
    "提示": 25,
    "WARN": 30,
    "WARNING": 30,
    "警告": 30,
    "一般": 30,
    "ERROR": 40,
    "ERR": 40,
    "错误": 40,
    "SEVERE": 40,
    "FATAL": 50,
    "CRITICAL": 50,
    "CRIT": 50,
    "严重": 50,
    "紧急": 60,
    "致命": 60,
}
UNKNOWN_LEVEL = "未知"


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def severity_rank(level: Any) -> int:
    """把日志级别折算成可比较的数字，未知级别排在已知级别之下。"""
    key = normalize_text(level).upper()
    if not key:
        return 0
    if key in SEVERITY_RANKS:
        return SEVERITY_RANKS[key]
    # 允许 “ERROR 500” 这类带后缀的写法。
    for name, rank in SEVERITY_RANKS.items():
        if key.startswith(name):
            return rank
    return 0


def highest_level(*levels: Any) -> str:
    """返回等级最高的那个级别文本；等级相同时保留先出现的写法。"""
    result = ""
    result_rank = -1
    for level in levels:
        text = normalize_text(level)
        if not text:
            continue
        rank = severity_rank(text)
        if rank > result_rank:
            result = text
            result_rank = rank
    return result or UNKNOWN_LEVEL


def parse_time(value: Any) -> datetime | None:
    """尽量宽松地解析日志时间，解析不了时返回 None 交给字符串比较兜底。"""
    text = normalize_text(value)
    if not text:
        return None
    normalized = text.replace("/", "-")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(normalized).replace(tzinfo=None)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def earlier_time(first: Any, second: Any) -> str:
    """取两个时间文本里更早的一个，空值不参与比较。"""
    left = normalize_text(first)
    right = normalize_text(second)
    if not left:
        return right
    if not right:
        return left
    left_dt = parse_time(left)
    right_dt = parse_time(right)
    if left_dt is not None and right_dt is not None:
        return left if left_dt <= right_dt else right
    return left if left <= right else right


def later_time(first: Any, second: Any) -> str:
    """取两个时间文本里更晚的一个，空值不参与比较。"""
    left = normalize_text(first)
    right = normalize_text(second)
    if not left:
        return right
    if not right:
        return left
    left_dt = parse_time(left)
    right_dt = parse_time(right)
    if left_dt is not None and right_dt is not None:
        return left if left_dt >= right_dt else right
    return left if left >= right else right


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def next_code(prefix: str, rows: list[dict[str, Any]], field: str, width: int = 4) -> str:
    """生成 FAULT-0001 / ALER-0001 这类业务编号。"""
    current = 0
    for row in rows:
        text = normalize_text(row.get(field))
        if not text.startswith(f"{prefix}-"):
            continue
        suffix = text[len(prefix) + 1:]
        if suffix.isdigit():
            current = max(current, int(suffix))
    return f"{prefix}-{current + 1:0{width}d}"


def is_terminal_fault(status: Any) -> bool:
    return normalize_text(status) in FAULT_TERMINAL


def is_active_fault(status: Any) -> bool:
    return normalize_text(status) in FAULT_ACTIVE


def public_row(row: dict[str, Any], *, list_fields: tuple[str, ...] = ()) -> dict[str, Any]:
    """复制一行数据，避免调用方改到仓库里的原对象；顺带把列表字段复制一份。"""
    copied = {key: value for key, value in row.items() if not key.startswith("_")}
    for field in list_fields:
        if isinstance(copied.get(field), list):
            copied[field] = list(copied[field])
    return copied
