"""采集日志接口：维护日志条目，并按任务与错误堆栈归并出采集故障。

- ``/api/log_viewer``           原始日志条目
- ``/api/log_viewer/faults``    采集故障明细（供采集监控登记告警）
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.log_viewer import FAULT_STATUS_ORDER, LogViewerService

router = APIRouter(prefix="/api/log_viewer", tags=["采集日志"])

service = LogViewerService()

LIST_FIELDS = ["日志编号", "关联任务", "日志级别", "记录时间", "日志内容", "错误堆栈", "处理状态", "处理人"]
STATUSES = ["未处理", "处理中", "已处理", "已忽略"]
FAULT_LIST_FIELDS = ["故障编号", "关联任务", "故障摘要", "首次出现时间", "重复次数", "当前级别", "处理状态"]
FAULT_STATUSES = FAULT_STATUS_ORDER


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出采集日志清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "log_viewer", "total": total, "items": items}


@router.get("/faults/export")
def export_faults() -> dict[str, Any]:
    """导出采集故障明细：按任务与堆栈归并后的全量数据。"""
    items, total = service.list_faults(page=1, size=10000)
    return {"module": "log_faults", "total": total, "items": items}


@router.get("/faults", response_model=PageResult[dict])
def list_faults(
    request: Request,
    keyword: str | None = Query(default=None, description="按故障编号、关联任务或错误堆栈检索"),
    status: str | None = Query(default=None, description="待处理、处理中、已处理、已关闭、已忽略"),
    task: str | None = Query(default=None, description="按关联任务精确过滤"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按任务、状态过滤采集故障明细；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    extra = {
        k: v for k, v in request.query_params.items()
        if k not in ("keyword", "status", "task", "page", "size") and v.strip()
    }
    items, total = service.list_faults(
        keyword=keyword, status=status, task=task, page=page, size=size, extra=extra
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/faults/group", response_model=ActionResult)
def group_faults() -> ActionResult:
    """把尚未归并的原始日志按「任务 + 错误堆栈」归并；已归并的日志不会重复计数。"""
    result = service.group_logs()
    return ActionResult(
        ok=True,
        message=(
            f"归并完成：新建 {result['created_faults']} 条故障，"
            f"更新 {result['updated_faults']} 条，本次计入 {result['linked_logs']} 条日志，"
            f"跳过已归并 {result['skipped_logs']} 条"
        ),
        entry=result,
    )


@router.get("/faults/{fault_id}", response_model=dict)
def get_fault(fault_id: int) -> dict:
    """读取单条采集故障明细，含它归并的原始日志；不存在时给出可读的错误说明。"""
    fault = service.get_fault(fault_id)
    if fault is None:
        raise HTTPException(status_code=404, detail=f"采集故障 {fault_id} 不存在或已归档")
    return fault


@router.get("/faults/{fault_id}/logs", response_model=dict)
def get_fault_logs(fault_id: int) -> dict[str, Any]:
    """反查一条故障归并了哪些原始日志，用来确认每条日志只计一次。"""
    logs = service.fault_logs(fault_id)
    if logs is None:
        raise HTTPException(status_code=404, detail=f"采集故障 {fault_id} 不存在或已归档")
    return {"fault_id": fault_id, "total": len(logs), "items": logs}


@router.post("/faults/{fault_id}/actions", response_model=ActionResult)
def run_fault_action(fault_id: int, payload: EntryPayload) -> ActionResult:
    """对单条采集故障执行开始处理、标记已处理、关闭故障、忽略故障。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_fault_action(fault_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按日志编号或关联任务检索"),
    status: str | None = Query(default=None, description="未处理、处理中、已处理、已忽略"),
    fault_id: int | None = Query(default=None, description="只看归并到某条故障的原始日志"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按日志编号与状态过滤采集日志列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    extra = {
        k: v for k, v in request.query_params.items()
        if k not in ("keyword", "status", "fault_id", "page", "size") and v.strip()
    }
    items, total = service.list_entries(
        keyword=keyword, status=status, fault_id=fault_id, page=page, size=size, extra=extra
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条日志条目明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"日志条目 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条日志条目，缺字段时说明原因而不是静默丢弃；登记后自动参与故障归并。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    message = "日志条目已登记并归并"
    if entry and entry.get("登记结果") == "日志编号已存在，未重复登记":
        message = "日志编号已存在，未重复登记，也不会重复计入故障次数"
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条日志条目执行开始处理、标记已处理、忽略日志；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
