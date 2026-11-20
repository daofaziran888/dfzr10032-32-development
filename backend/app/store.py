"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

from typing import Any

from app.seed import SEED_ROWS


def _copy_row(row: dict[str, Any]) -> dict[str, Any]:
    """示例数据里的列表字段（日志组、内部日志 id）也要复制，避免多个 Store 之间串数据。"""
    copied: dict[str, Any] = {}
    for key, value in row.items():
        copied[key] = list(value) if isinstance(value, list) else value
    return copied


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [_copy_row(row) for row in rows] for name, rows in SEED_ROWS.items()
        }

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def ungrouped_logs(self) -> int:
        """尚未归并进任何故障的原始日志条数，用来提示是否需要先做一次故障归并。"""
        faults = self.rows("log_faults")
        grouped = set()
        for fault in faults:
            grouped.update(int(item) for item in fault.get("_日志ids") or [])
        return sum(1 for row in self.rows("log_viewer") if int(row.get("id", 0)) not in grouped)

    def overview(self) -> dict[str, object]:
        # 采集故障与告警是日志/监控的派生数据，待处理与异常量并回各自所属模块，不再单列。
        derived = {"log_faults": "log_viewer", "monitor_alerts": "monitor"}
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            if name in derived:
                continue
            rows = self.rows(name)
            pending = sum(1 for row in rows if row.get("pending"))
            abnormal = sum(1 for row in rows if row.get("abnormal"))
            for derived_name, owner in derived.items():
                if owner != name:
                    continue
                derived_rows = self.rows(derived_name)
                pending += sum(1 for row in derived_rows if row.get("pending"))
                abnormal += sum(1 for row in derived_rows if row.get("abnormal"))
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": pending,
                "abnormal": abnormal,
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
