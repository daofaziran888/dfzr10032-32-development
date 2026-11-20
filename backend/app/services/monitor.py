"""采集监控业务规则：监控面板、告警记录的状态流转与筛选口径都收在这里。

告警记录来自采集故障明细：一条故障对应一张告警，告警上固定保存当时的日志组与本次严重
程度。告警是独立对象，关闭一张只影响它自己关联的那条故障，不会顺手关掉同任务的其他
故障，因此低等级告警关闭后，高等级且未处置的故障仍然可以继续接单。
"""
from __future__ import annotations

from typing import Any

from app.services import faults
from app.store import store

MODULE = "monitor"
REQUIRED_FIELDS = ["面板编号", "采集源总数", "今日采集量"]
STATUS_ORDER = ["正常", "预警", "告警"]
ACTION_RULES = {"查看详情": "正常"}
NEGATIVE_ACTIONS = []

ALERT_MODULE = faults.ALERT_MODULE
ALERT_STATUS_ORDER = ["待领取", "处理中", "已关闭", "已忽略"]
ALERT_ACTION_RULES = {"领取告警": "处理中", "关闭告警": "已关闭", "忽略告警": "已忽略"}
ALERT_NEGATIVE_ACTIONS = ["忽略告警"]

FIELD_ALIASES: dict[str, str] = {
    "panel_code": "面板编号",
    "code": "面板编号",
    "source_total": "采集源总数",
    "today_total": "今日采集量",
    "success_rate": "成功率",
    "avg_latency": "平均延迟",
    "retry_count": "失败重试",
    "alert_count": "异常告警",
    "updated_at": "更新时间",
}


class MonitorService:
    # ------------------------------------------------------------------
    # 监控面板
    # ------------------------------------------------------------------
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        extra: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("面板编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if extra:
            for field, val in extra.items():
                rows = [row for row in rows if val in str(row.get(field, ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        values = {FIELD_ALIASES.get(key, key): value for key, value in values.items()}
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": faults.next_id(rows)}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in ("成功率", "平均延迟", "失败重试", "异常告警", "更新时间"):
            if field in values:
                entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"监控面板 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于采集监控可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"监控面板已{action}"

    # ------------------------------------------------------------------
    # 告警记录
    # ------------------------------------------------------------------
    def list_alerts(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        task: str | None = None,
        fault_id: int | None = None,
        page: int = 1,
        size: int = 20,
        extra: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(ALERT_MODULE)
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("告警编号", ""))
                or keyword in str(row.get("关联任务", ""))
                or keyword in str(row.get("故障编号", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if task:
            rows = [row for row in rows if row.get("关联任务") == task]
        if fault_id is not None:
            rows = [row for row in rows if int(row.get("_故障id") or 0) == fault_id]
        if extra:
            for field, val in extra.items():
                rows = [row for row in rows if val in str(row.get(field, ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._public_alert(row) for row in rows[start:start + size]], total

    def get_alert(self, alert_id: int) -> dict[str, Any] | None:
        alert = store.find(ALERT_MODULE, alert_id)
        if alert is None:
            return None
        return self._public_alert(alert)

    def generate_alerts(self) -> dict[str, Any]:
        """按采集故障明细补齐告警记录；已有未关闭告警的故障不会重复生成。"""
        before = len(store.rows(ALERT_MODULE))
        synced = 0
        for fault in store.rows(faults.FAULT_MODULE):
            self.sync_alert_for_fault(fault)
            synced += 1
        return {
            "created": len(store.rows(ALERT_MODULE)) - before,
            "synced": synced,
            "total_alerts": len(store.rows(ALERT_MODULE)),
            "open_alerts": sum(
                1 for alert in store.rows(ALERT_MODULE)
                if alert.get("status") not in faults.ALERT_TERMINAL
            ),
        }

    def sync_alert_for_fault(self, fault: dict[str, Any]) -> dict[str, Any] | None:
        """让告警跟随故障变化，但不反过来改故障状态；只处理这一条故障自己的告警。"""
        alert = self._open_alert(fault)
        if faults.is_terminal_fault(fault.get("status")):
            if alert is not None:
                self._close_alert(
                    alert,
                    status="已忽略" if fault.get("status") == "已忽略" else "已关闭",
                    note=f"故障{fault.get('故障编号')}已{fault.get('status')}",
                    operator="监控自动关闭",
                )
            return alert
        if alert is None:
            alert = self._ensure_alert(fault)
            if alert is None:
                return None
        alert["日志组"] = list(fault.get("日志组") or [])
        alert["原始日志数"] = int(fault.get("重复次数") or 0)
        alert["重复次数"] = int(fault.get("重复次数") or 0)
        alert["本次严重程度"] = fault.get("最高级别") or alert.get("本次严重程度")
        alert["最近发生时间"] = fault.get("最近出现时间")
        # 告警是否已被领取只由领取动作决定，故障侧的处理不会替值班员接单。
        return alert

    def run_alert_action(
        self,
        alert_id: int,
        action: str,
        *,
        operator: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        alert = store.find(ALERT_MODULE, alert_id)
        if alert is None:
            return None, f"告警记录 {alert_id} 不存在或已归档"
        if action not in ALERT_ACTION_RULES:
            return None, f"动作「{action}」不属于采集监控告警可执行范围"
        target = ALERT_ACTION_RULES[action]
        if target not in ALERT_STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        if alert.get("status") in faults.ALERT_TERMINAL:
            return None, f"告警 {alert.get('告警编号')} 已关闭，不能再次流转"
        if action == "领取告警" and alert.get("status") != "待领取":
            return None, f"告警 {alert.get('告警编号')} 已被领取，无需重复接单"

        fault = store.find(faults.FAULT_MODULE, int(alert.get("_故障id") or 0))
        who = faults.normalize_text(operator)
        alert["status"] = target
        alert["pending"] = target in faults.ALERT_ACTIVE
        alert["abnormal"] = action in ALERT_NEGATIVE_ACTIONS
        if action == "领取告警":
            alert["领取人"] = who or "值班管理员"
            alert["领取时间"] = faults.now_text()
        if target in faults.ALERT_TERMINAL:
            self._close_alert(alert, status=target, note=f"{alert.get('告警编号')} {action}", operator=who or "值班管理员")

        if fault is not None:
            # 只改这张告警自己的故障，同任务的其他故障保持原状态。
            if action == "领取告警" and faults.is_active_fault(fault.get("status")):
                fault["status"] = "处理中"
                fault["处理状态"] = "处理中"
                fault["pending"] = True
                fault["abnormal"] = True
            elif target == "已关闭" and not faults.is_terminal_fault(fault.get("status")):
                fault["status"] = "已关闭"
                fault["处理状态"] = "已关闭"
                fault["pending"] = False
                fault["abnormal"] = False
            elif target == "已忽略" and not faults.is_terminal_fault(fault.get("status")):
                fault["status"] = "已忽略"
                fault["处理状态"] = "已忽略"
                fault["pending"] = False
                fault["abnormal"] = False
        entry = self._public_alert(alert)
        if target in faults.ALERT_TERMINAL:
            entry["关闭说明"] = self._close_note(alert, fault)
        return entry, entry.get("关闭说明") or f"{entry.get('告警编号')} 已{action}"

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _open_alert(fault: dict[str, Any]) -> dict[str, Any] | None:
        latest = None
        for alert in store.rows(ALERT_MODULE):
            if alert.get("_故障id") != fault.get("id"):
                continue
            if alert.get("status") not in faults.ALERT_TERMINAL:
                latest = alert
        return latest

    @staticmethod
    def _ensure_alert(fault: dict[str, Any]) -> dict[str, Any] | None:
        if not fault:
            return None
        rows = store.rows(ALERT_MODULE)
        terminal = faults.is_terminal_fault(fault.get("status"))
        handler = faults.normalize_text(fault.get("处理人")) or None
        # 故障在归并时已经有人在处理，说明这张告警的来源处置单已经接单；没有处理人时
        # 仍然保持待领取，留给值班员按告警领取。
        in_progress = not terminal and fault.get("status") == "处理中" and handler is not None
        status = "已关闭" if terminal else ("处理中" if in_progress else "待领取")
        alert = {
            "id": faults.next_id(rows),
            "告警编号": faults.next_code("ALER", rows, "告警编号"),
            "告警标题": f"{fault.get('关联任务')} 采集故障：{fault.get('故障摘要') or fault.get('故障编号')}",
            "关联任务": fault.get("关联任务"),
            "故障id": fault.get("id"),
            "故障编号": fault.get("故障编号"),
            "日志组": list(fault.get("日志组") or []),
            "原始日志数": int(fault.get("重复次数") or 0),
            "重复次数": int(fault.get("重复次数") or 0),
            "本次严重程度": fault.get("最高级别"),
            "首次发生时间": fault.get("首次出现时间"),
            "最近发生时间": fault.get("最近出现时间"),
            "来源": "采集故障归并",
            "status": status,
            "pending": not terminal,
            "abnormal": False,
            "领取人": handler if in_progress else None,
            "领取时间": fault.get("最近出现时间") if in_progress else None,
            "关闭人": "监控自动关闭" if terminal else None,
            "关闭时间": faults.now_text() if terminal else None,
            "关闭说明": "故障已处置" if terminal else None,
            "_故障id": fault.get("id"),
        }
        rows.append(alert)
        return alert

    @staticmethod
    def _close_alert(alert: dict[str, Any], *, status: str, note: str, operator: str) -> None:
        alert["status"] = status
        alert["pending"] = False
        alert["abnormal"] = status == "已忽略"
        alert["关闭时间"] = faults.now_text()
        alert["关闭说明"] = note
        alert["关闭人"] = operator or alert.get("关闭人") or "值班管理员"

    def _close_note(self, alert: dict[str, Any], fault: dict[str, Any] | None) -> str:
        """关闭一张告警后，说明同任务还有哪些故障没处置，避免高等级故障被静默吞掉。"""
        remaining = self._task_faults(
            faults.normalize_text(alert.get("关联任务")), alert.get("_故障id")
        )
        open_ones = [item for item in remaining if item.get("是否需接单") or item.get("未处置")]
        if not open_ones:
            return f"{alert.get('告警编号')} 已关闭，同任务没有其他未处置故障"
        detail = "、".join(
            f"{item['故障编号']}({item['当前级别']}，{item['重复次数']} 次)"
            for item in open_ones
        )
        return f"{alert.get('告警编号')} 已关闭；同任务仍需接单：{detail}"

    def _public_alert(self, alert: dict[str, Any]) -> dict[str, Any]:
        copied = faults.public_row(alert, list_fields=("日志组",))
        fault = store.find(faults.FAULT_MODULE, int(alert.get("_故障id") or 0))
        copied["是否需接单"] = alert.get("status") == "待领取"
        copied["告警状态"] = alert.get("status")
        if fault is not None:
            copied["故障状态"] = fault.get("status")
            copied["日志组"] = list(fault.get("日志组") or [])
            copied["原始日志数"] = int(fault.get("重复次数") or 0)
            copied["重复次数"] = int(fault.get("重复次数") or 0)
            copied["本次严重程度"] = fault.get("最高级别")
            copied["严重程度值"] = faults.severity_rank(fault.get("最高级别"))
        copied["同任务故障"] = self._task_faults(
            faults.normalize_text(alert.get("关联任务")), alert.get("_故障id")
        )
        copied["同任务未处置"] = [
            item for item in copied["同任务故障"] if item.get("未处置")
        ]
        copied["未处置高等级故障数"] = sum(
            1 for item in copied["同任务未处置"]
            if int(item.get("严重程度值") or 0) >= int(copied.get("严重程度值") or 0)
        )
        return copied

    @staticmethod
    def _task_faults(task: str, exclude_fault_id: Any) -> list[dict[str, Any]]:
        """同任务下的全部故障（含已关闭），每条故障单独给出自己的原始日志次数。"""
        result = []
        for fault in store.rows(faults.FAULT_MODULE):
            if exclude_fault_id is not None and fault.get("id") == exclude_fault_id:
                continue
            if faults.normalize_text(fault.get("关联任务")) != task:
                continue
            alert = MonitorService._open_alert(fault)
            active = faults.is_active_fault(fault.get("status"))
            result.append({
                "故障id": fault.get("id"),
                "故障编号": fault.get("故障编号"),
                "当前级别": fault.get("当前级别"),
                "最高级别": fault.get("最高级别"),
                "严重程度值": faults.severity_rank(fault.get("最高级别")),
                "status": fault.get("status"),
                "未处置": active,
                "重复次数": int(fault.get("重复次数") or 0),
                "原始日志数": int(fault.get("重复次数") or 0),
                "日志组": list(fault.get("日志组") or []),
                "告警编号": (alert or {}).get("告警编号"),
                "告警状态": (alert or {}).get("status"),
                "是否需接单": (alert or {}).get("status") == "待领取",
            })
        return result
