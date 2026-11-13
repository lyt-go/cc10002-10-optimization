"""存储成本接口：维护成本账单，覆盖生成账单、核对账单、归档账单等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.cost import CostService

router = APIRouter(prefix="/api/cost", tags=["存储成本"])

service = CostService()

LIST_FIELDS = ["账单编号", "数据表名", "存储大小GB", "每日扫描量GB", "查询次数", "存储成本", "计算成本", "统计月份"]
STATUSES = ["草稿", "已生成", "已核对", "已归档"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按账单编号检索"),
    status: str | None = Query(default=None, description="草稿、已生成、已核对、已归档"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按账单编号与状态过滤存储成本列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条成本账单明细；附注归档后预计减少量，历史账单字段保持原值。"""
    entry = service.get_detail(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"成本账单 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条成本账单，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="成本账单已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条成本账单执行生成账单、核对账单、归档账单；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出存储成本清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "cost", "total": total, "items": items}
