"""采集日志业务规则：状态流转、字段校验、筛选口径与故障归并都收在这里。

一条原始日志只会被归并一次：归并后写入 ``故障编号``/``故障id``，后续再次归并会直接跳过，
因此重复次数不会被重复累加。归并键是「关联任务 + 错误堆栈」，不同任务即使堆栈文本相同也
各自成为独立故障；已经处置完的故障不会被重新打开，而是新开一条故障记录，并用
``上次故障编号`` 串起前后两次故障。
"""
from __future__ import annotations

from typing import Any

from app.services import faults
from app.store import store

MODULE = "log_viewer"
FAULT_MODULE = faults.FAULT_MODULE
REQUIRED_FIELDS = ["日志编号", "关联任务", "日志级别"]
STATUS_ORDER = ["未处理", "处理中", "已处理", "已忽略"]
ACTION_RULES = {"开始处理": "处理中", "标记已处理": "已处理", "忽略日志": "已忽略"}
NEGATIVE_ACTIONS = ["忽略日志"]
OPTIONAL_FIELDS = ["记录时间", "日志内容", "错误堆栈", "处理状态", "处理人"]

FAULT_STATUS_ORDER = ["待处理", "处理中", "已处理", "已关闭", "已忽略"]
FAULT_ACTION_RULES = {
    "开始处理": "处理中",
    "标记已处理": "已处理",
    "关闭故障": "已关闭",
    "忽略故障": "已忽略",
}
FAULT_NEGATIVE_ACTIONS = ["忽略故障", "关闭故障"]

# 外部调用方可能用英文键提交，这里统一映射到平台约定的中文字段，避免字段丢失。
FIELD_ALIASES: dict[str, str] = {
    "log_code": "日志编号",
    "code": "日志编号",
    "log_id": "日志编号",
    "task": "关联任务",
    "task_name": "关联任务",
    "related_task": "关联任务",
    "level": "日志级别",
    "log_level": "日志级别",
    "severity": "日志级别",
    "time": "记录时间",
    "timestamp": "记录时间",
    "occurred_at": "记录时间",
    "logged_at": "记录时间",
    "message": "日志内容",
    "content": "日志内容",
    "log_content": "日志内容",
    "stack": "错误堆栈",
    "error_stack": "错误堆栈",
    "traceback": "错误堆栈",
    "exception": "错误堆栈",
    "status": "处理状态",
    "operator": "处理人",
    "handler": "处理人",
}


class LogViewerService:
    # ------------------------------------------------------------------
    # 原始日志
    # ------------------------------------------------------------------
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        fault_id: int | None = None,
        page: int = 1,
        size: int = 20,
        extra: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("日志编号", ""))
                or keyword in str(row.get("关联任务", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if fault_id is not None:
            rows = [row for row in rows if int(row.get("故障id") or 0) == fault_id]
        if extra:
            for field, val in extra.items():
                rows = [row for row in rows if val in str(row.get(field, ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._with_fault_link(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return self._with_fault_link(entry)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        values = self._normalize_values(values)
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        code = faults.normalize_text(values.get("日志编号"))
        for row in rows:
            if faults.normalize_text(row.get("日志编号")) == code:
                # 同一条原始日志重复上报时只保留一份，不重复计入故障的重复次数。
                existing = self._with_fault_link(row)
                existing["登记结果"] = "日志编号已存在，未重复登记"
                return existing, []
        entry = {"id": faults.next_id(rows)}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        # 其余可选字段（记录时间、日志内容、错误堆栈、处理人）也要保留，归并时用得到。
        for field in OPTIONAL_FIELDS:
            if field in values:
                entry[field] = values.get(field)
        entry["status"] = faults.normalize_text(entry.get("处理状态")) or STATUS_ORDER[0]
        entry["status"] = entry["status"] if entry["status"] in STATUS_ORDER else STATUS_ORDER[0]
        entry["pending"] = entry["status"] not in (STATUS_ORDER[2], STATUS_ORDER[3])
        entry["abnormal"] = False
        entry["归并状态"] = "未归并"
        entry["登记结果"] = "已登记"
        rows.append(entry)
        # 登记即归并：调用方立刻能看到这条日志落在哪个故障下。
        fault, reason = self._group_one(entry)
        if fault is None:
            entry["归并说明"] = reason
        return self._with_fault_link(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"日志条目 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于采集日志可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return self._with_fault_link(entry), f"日志条目已{action}"

    # ------------------------------------------------------------------
    # 故障归并
    # ------------------------------------------------------------------
    def list_faults(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        task: str | None = None,
        page: int = 1,
        size: int = 20,
        extra: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(FAULT_MODULE)
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("故障编号", ""))
                or keyword in str(row.get("关联任务", ""))
                or keyword in str(row.get("错误堆栈", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if task:
            rows = [row for row in rows if row.get("关联任务") == task]
        if extra:
            for field, val in extra.items():
                rows = [row for row in rows if val in str(row.get(field, ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._public_fault(row) for row in rows[start:start + size]], total

    def get_fault(self, fault_id: int) -> dict[str, Any] | None:
        fault = store.find(FAULT_MODULE, fault_id)
        if fault is None:
            return None
        detail = self._public_fault(fault)
        detail["原始日志"] = self.fault_logs(fault_id) or []
        return detail

    def fault_logs(self, fault_id: int) -> list[dict[str, Any]] | None:
        fault = store.find(FAULT_MODULE, fault_id)
        if fault is None:
            return None
        log_ids = list(fault.get("_日志ids") or [])
        return [
            self._with_fault_link(row)
            for row in store.rows(MODULE)
            if int(row.get("id", 0)) in log_ids
        ]

    def group_logs(self) -> dict[str, Any]:
        """把尚未归并的原始日志按「任务 + 堆栈」归并，已归并过的日志不会被重复计数。"""
        rows = store.rows(MODULE)
        before = len(store.rows(FAULT_MODULE))
        linked = 0
        skipped = 0
        unmatched: list[dict[str, Any]] = []
        touched: dict[int, dict[str, Any]] = {}
        for entry in sorted(rows, key=lambda item: int(item.get("id", 0))):
            if entry.get("故障id"):
                skipped += 1
                continue
            fault, reason = self._group_one(entry)
            if fault is None:
                entry["归并状态"] = "未归并"
                entry["归并说明"] = reason
                unmatched.append({"日志编号": entry.get("日志编号"), "原因": reason})
                continue
            linked += 1
            touched[int(fault["id"])] = fault
        for fault in store.rows(FAULT_MODULE):
            fault.pop("_本次新建", None)
        created = len(store.rows(FAULT_MODULE)) - before
        return {
            "created_faults": created,
            "updated_faults": len(touched) - created,
            "linked_logs": linked,
            "skipped_logs": skipped,
            "unmatched": unmatched,
            "total_faults": len(store.rows(FAULT_MODULE)),
            "total_logs": len(rows),
        }

    def run_fault_action(self, fault_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        fault = store.find(FAULT_MODULE, fault_id)
        if fault is None:
            return None, f"采集故障 {fault_id} 不存在或已归档"
        if action not in FAULT_ACTION_RULES:
            return None, f"动作「{action}」不属于采集故障可执行范围"
        target = FAULT_ACTION_RULES[action]
        if target not in FAULT_STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        if faults.is_terminal_fault(fault.get("status")):
            return None, f"故障 {fault.get('故障编号')} 已处置，不能再次流转"
        fault["status"] = target
        fault["pending"] = target in faults.FAULT_ACTIVE
        fault["abnormal"] = target in faults.FAULT_ACTIVE
        fault["处理状态"] = target
        self._sync_alert(fault)
        entry = self._public_fault(fault)
        if target in faults.FAULT_TERMINAL:
            entry["关闭说明"] = self._terminal_note(fault)
        return entry, entry.get("关闭说明") or f"{entry.get('故障编号')} 已{action}"

    def ungrouped_logs(self) -> list[dict[str, Any]]:
        return [row for row in store.rows(MODULE) if not row.get("故障id")]

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_values(values: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for key, value in values.items():
            normalized[FIELD_ALIASES.get(key, key)] = value
        return normalized

    def _group_one(self, entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        if entry.get("故障id"):
            fault = store.find(FAULT_MODULE, int(entry["故障id"]))
            return fault, "该日志已归并"
        task = faults.normalize_text(entry.get("关联任务"))
        stack = faults.normalize_text(entry.get("错误堆栈")) or faults.normalize_text(entry.get("日志内容"))
        if not task:
            return None, "缺少关联任务，无法按任务归并"
        if not stack:
            return None, "缺少错误堆栈与日志内容，无法归并"
        level = faults.highest_level(entry.get("日志级别"))
        occurred = faults.normalize_text(entry.get("记录时间")) or faults.now_text()
        key = f"{task}\u0000{stack}"

        rows = store.rows(FAULT_MODULE)
        active = None
        latest_terminal = None
        for fault in rows:
            if fault.get("_归并键") != key:
                continue
            if faults.is_active_fault(fault.get("status")):
                active = fault
            elif faults.is_terminal_fault(fault.get("status")):
                latest_terminal = fault
        fault = active
        if fault is None:
            # 新故障的起始状态跟随首条日志的处理状态，值班员已开始处理的不会被拉回待处理。
            initial = "处理中" if entry.get("status") == "处理中" else "待处理"
            fault = {
                "id": faults.next_id(rows),
                "故障编号": faults.next_code("FAULT", rows, "故障编号"),
                "关联任务": task,
                "错误堆栈": stack,
                "故障摘要": self._summary(stack),
                "首次出现时间": occurred,
                "最近出现时间": occurred,
                "重复次数": 0,
                "当前级别": level,
                "最高级别": level,
                "首次出现级别": level,
                "status": initial,
                "pending": True,
                "abnormal": True,
                "日志组": [],
                "首次日志编号": entry.get("日志编号"),
                "处理人": faults.normalize_text(entry.get("处理人")) or None,
                "上次故障编号": latest_terminal.get("故障编号") if latest_terminal else None,
                "处理状态": initial,
                "_归并键": key,
                "_日志ids": [],
                "_本次新建": True,
            }
            rows.append(fault)

        log_ids = fault.setdefault("_日志ids", [])
        entry_id = int(entry.get("id", 0))
        if entry_id in log_ids:
            # 同一条原始日志已经计过数，只补关联、不再累加重复次数。
            entry["故障id"] = fault["id"]
            entry["故障编号"] = fault["故障编号"]
            entry["归并状态"] = "已归并"
            entry["归并说明"] = f"该日志已归并到 {fault['故障编号']}"
            return fault, entry["归并说明"]
        log_ids.append(entry_id)
        group = fault.setdefault("日志组", [])
        code = entry.get("日志编号")
        if code and code not in group:
            group.append(code)
        fault["重复次数"] = len(log_ids)
        fault["原始日志数"] = len(log_ids)
        fault["首次出现时间"] = faults.earlier_time(fault.get("首次出现时间"), occurred)
        fault["最近出现时间"] = faults.later_time(fault.get("最近出现时间"), occurred)
        fault["当前级别"] = level
        fault["当前级别来源"] = entry.get("日志编号")
        fault["最高级别"] = faults.highest_level(fault.get("最高级别"), level)
        entry["故障id"] = fault["id"]
        entry["故障编号"] = fault["故障编号"]
        entry["归并状态"] = "已归并"
        entry["归并说明"] = f"已归并到 {fault['故障编号']}"
        if faults.is_active_fault(fault.get("status")):
            fault["pending"] = True
            fault["abnormal"] = True
        if not fault.get("处理人") and faults.normalize_text(entry.get("处理人")):
            fault["处理人"] = faults.normalize_text(entry.get("处理人"))
        self._sync_alert(fault)
        fault.pop("_本次新建", None)
        return fault, entry["归并说明"]

    @staticmethod
    def _summary(stack: str) -> str:
        first = stack.splitlines()[0].strip() if stack else ""
        return first[:120]

    def _pending_faults_same_task(self, fault: dict[str, Any]) -> list[dict[str, Any]]:
        result = []
        for item in store.rows(FAULT_MODULE):
            if item.get("id") == fault.get("id"):
                continue
            if item.get("关联任务") != fault.get("关联任务"):
                continue
            if not faults.is_active_fault(item.get("status")):
                continue
            alert = self._latest_alert(item)
            result.append({
                "故障编号": item.get("故障编号"),
                "当前级别": item.get("当前级别"),
                "最高级别": item.get("最高级别"),
                "严重程度值": faults.severity_rank(item.get("最高级别")),
                "status": item.get("status"),
                "重复次数": item.get("重复次数"),
                "日志组": list(item.get("日志组") or []),
                "告警编号": (alert or {}).get("告警编号"),
                "是否需接单": (alert or {}).get("status") == "待领取",
            })
        return result

    def _terminal_note(self, fault: dict[str, Any]) -> str:
        remaining = self._pending_faults_same_task(fault)
        if not remaining:
            return f"{fault.get('故障编号')} 已关闭，同任务没有其他未处置故障"
        higher = [
            item for item in remaining
            if item["严重程度值"] > faults.severity_rank(fault.get("当前级别"))
        ]
        if higher:
            names = "、".join(item["故障编号"] for item in higher)
            return f"{fault.get('故障编号')} 已关闭；同任务仍有未处置高等级故障：{names}，需继续接单"
        return f"{fault.get('故障编号')} 已关闭；同任务还有 {len(remaining)} 条未处置故障"

    @staticmethod
    def _latest_alert(fault: dict[str, Any]) -> dict[str, Any] | None:
        latest = None
        for alert in store.rows(faults.ALERT_MODULE):
            if alert.get("_故障id") == fault.get("id"):
                latest = alert
        return latest

    def _public_fault(self, fault: dict[str, Any]) -> dict[str, Any]:
        copied = faults.public_row(fault, list_fields=("日志组",))
        copied["原始日志数"] = int(fault.get("重复次数") or 0)
        copied["严重程度值"] = faults.severity_rank(fault.get("最高级别"))
        alert = self._latest_alert(fault)
        if alert is not None:
            copied["告警编号"] = alert.get("告警编号")
            copied["告警状态"] = alert.get("status")
            copied["是否需接单"] = alert.get("status") == "待领取"
        else:
            copied["告警编号"] = None
            copied["告警状态"] = None
            copied["是否需接单"] = False
        return copied

    @staticmethod
    def _with_fault_link(entry: dict[str, Any]) -> dict[str, Any]:
        copied = faults.public_row(entry)
        if not copied.get("处理状态"):
            # 列表页统一用「处理状态」展示，状态字段缺失时用流转状态补上。
            copied["处理状态"] = entry.get("status")
        fault_id = entry.get("故障id")
        if fault_id:
            fault = store.find(FAULT_MODULE, int(fault_id))
            if fault is not None:
                copied["故障编号"] = fault.get("故障编号")
                copied["故障状态"] = fault.get("status")
                copied["故障重复次数"] = fault.get("重复次数")
        return copied

    @staticmethod
    def _sync_alert(fault: dict[str, Any]) -> None:
        """故障变化后同步它自己的告警，不影响同任务的其他故障。"""
        from app.services.monitor import MonitorService

        MonitorService().sync_alert_for_fault(fault)
