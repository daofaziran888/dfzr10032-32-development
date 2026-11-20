"""采集监控接口：维护监控面板，并由采集故障明细生成告警记录。

- ``/api/monitor``         监控面板
- ``/api/monitor/alerts``  由采集故障归并结果生成的告警记录
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.monitor import ALERT_STATUS_ORDER, MonitorService

router = APIRouter(prefix="/api/monitor", tags=["采集监控"])

service = MonitorService()

LIST_FIELDS = ["面板编号", "采集源总数", "今日采集量", "成功率", "平均延迟", "失败重试", "异常告警", "更新时间"]
STATUSES = ["正常", "预警", "告警"]
ALERT_LIST_FIELDS = ["告警编号", "关联任务", "故障编号", "日志组", "本次严重程度", "告警状态", "领取人"]
ALERT_STATUSES = ALERT_STATUS_ORDER


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出采集监控清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "monitor", "total": total, "items": items}


@router.get("/alerts/export")
def export_alerts() -> dict[str, Any]:
    """导出采集告警清单：保留每条告警对应的日志组与本次严重程度。"""
    items, total = service.list_alerts(page=1, size=10000)
    return {"module": "monitor_alerts", "total": total, "items": items}


@router.get("/alerts", response_model=PageResult[dict])
def list_alerts(
    request: Request,
    keyword: str | None = Query(default=None, description="按告警编号、关联任务或故障编号检索"),
    status: str | None = Query(default=None, description="待领取、处理中、已关闭、已忽略"),
    task: str | None = Query(default=None, description="按关联任务精确过滤"),
    fault_id: int | None = Query(default=None, description="只看某条采集故障对应的告警"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按状态与任务过滤采集告警记录；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    extra = {
        k: v for k, v in request.query_params.items()
        if k not in ("keyword", "status", "task", "fault_id", "page", "size") and v.strip()
    }
    items, total = service.list_alerts(
        keyword=keyword, status=status, task=task, fault_id=fault_id, page=page, size=size, extra=extra
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/alerts/generate", response_model=ActionResult)
def generate_alerts() -> ActionResult:
    """按采集故障明细生成告警记录；同一故障已有未关闭告警时只同步、不重复生成。"""
    result = service.generate_alerts()
    return ActionResult(
        ok=True,
        message=(
            f"告警记录已生成：新增 {result['created']} 条，同步 {result['synced']} 条故障，"
            f"当前未关闭告警 {result['open_alerts']} 条"
        ),
        entry=result,
    )


@router.get("/alerts/{alert_id}", response_model=dict)
def get_alert(alert_id: int) -> dict:
    """读取单条告警明细，含对应日志组、本次严重程度与同任务未处置故障。"""
    alert = service.get_alert(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"告警记录 {alert_id} 不存在或已归档")
    return alert


@router.post("/alerts/{alert_id}/actions", response_model=ActionResult)
def run_alert_action(alert_id: int, payload: EntryPayload) -> ActionResult:
    """对单条告警执行领取告警、关闭告警、忽略告警；关闭只影响这张告警自己的故障。"""
    action = str(payload.values.get("action") or "").strip()
    operator = str(payload.values.get("operator") or payload.values.get("处理人") or "").strip()
    entry, message = service.run_alert_action(alert_id, action, operator=operator)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按面板编号检索"),
    status: str | None = Query(default=None, description="正常、预警、告警"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按面板编号与状态过滤采集监控列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    extra = {k: v for k, v in request.query_params.items() if k not in ("keyword", "status", "page", "size") and v.strip()}
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size, extra=extra)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条监控面板明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"监控面板 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条监控面板，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="监控面板已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条监控面板执行查看详情；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
