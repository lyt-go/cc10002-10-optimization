"""数据生命周期接口：维护生命周期策略，覆盖提交策略评审、启用策略、停用策略等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.lifecycle import LifecycleService

router = APIRouter(prefix="/api/lifecycle", tags=["数据生命周期"])

service = LifecycleService()

LIST_FIELDS = ["策略编号", "数据表名", "保留天数", "冷数据阈值", "归档格式", "删除确认", "归档路径", "最后执行时间", "策略版本", "生效版本"]
STATUSES = ["草稿", "策略评审中", "已启用", "已停用"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按策略编号检索"),
    status: str | None = Query(default=None, description="草稿、策略评审中、已启用、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按策略编号与状态过滤数据生命周期列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/{entry_id}/adjust", response_model=ActionResult)
def adjust_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """调整保留天数/冷热阈值/归档路径；带 策略版本 时做乐观锁校验。"""
    entry, message = service.run_action(entry_id, "调整策略参数", payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条生命周期策略明细；附带待提交与最新已提交重评批次。"""
    entry = service.get_detail(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"生命周期策略 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条生命周期策略，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="生命周期策略已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """提交评审、启用策略、停用策略，或调整策略参数；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出数据生命周期清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "lifecycle", "total": total, "items": items}
