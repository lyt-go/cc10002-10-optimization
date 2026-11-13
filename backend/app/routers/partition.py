"""分区治理接口：维护分区方案，覆盖提交方案审批、启用方案、停用方案等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.partition import PartitionService

router = APIRouter(prefix="/api/partition", tags=["分区治理"])

service = PartitionService()

LIST_FIELDS = ["方案编号", "数据表名", "分区字段", "分区粒度", "保留天数", "冷热阈值", "预建天数", "归档路径", "当前重评批次", "待归档分区数", "预计释放存储GB"]
STATUSES = ["草稿", "方案审批中", "已启用", "已停用"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按方案编号检索"),
    status: str | None = Query(default=None, description="草稿、方案审批中、已启用、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按方案编号与状态过滤分区治理列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/snapshots", response_model=PageResult[dict])
def list_snapshots(
    keyword: str | None = Query(default=None, description="按方案编号检索"),
    table: str | None = Query(default=None, description="按数据表名过滤，供成本页联动"),
    status: str | None = Query(default=None, description="草稿、方案审批中、已启用、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """分区方案列表 + 待提交批次摘要；与分区详情、成本详情同口径。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_snapshots(
        keyword=keyword, table=table, status=status, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/partitions", response_model=PageResult[dict])
def list_partitions(
    plan_no: str | None = Query(default=None, description="按方案编号过滤"),
    verdict: str | None = Query(default=None, description="重评结论：保留、转冷、待归档、已归档、已删除"),
    keyword: str | None = Query(default=None, description="按分区键检索"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """分区列表与方案详情、成本详情读取同一份重评结论。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_partitions(
        plan_no=plan_no, verdict=verdict, keyword=keyword, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/partitions/{entry_id}", response_model=dict)
def get_partition(entry_id: int) -> dict:
    """分区详情：结论批次、最近访问、归档路径与成本预计都来自同一重评结果。"""
    detail = service.get_partition(entry_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"分区 {entry_id} 不存在或已归档")
    return detail


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条分区方案明细；不存在时给出可读的错误说明。"""
    entry = service.get_detail(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"分区方案 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条分区方案，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="分区方案已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条分区方案执行提交方案审批、启用方案、停用方案；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出分区治理清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "partition", "total": total, "items": items}
