"""生命周期重评接口：策略启用生成待提交批次、多条方案逐条结果、一次提交生效。

批次是分区治理、生命周期与存储成本之间的串联对象：
- 生成阶段只读策略生效快照与物理分区，不改任何计划；
- 提交阶段一次性更新分区方案与分区结论，并同步写变更审计；
- 分区详情与成本详情通过批次读取同一份结果，不走各自的计算口径。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.revaluation import RevaluationService

router = APIRouter(prefix="/api/revaluation", tags=["生命周期重评"])

service = RevaluationService()

STATUSES = ["待提交", "已提交", "已失效"]


@router.get("", response_model=PageResult[dict])
def list_batches(
    keyword: str | None = Query(default=None, description="按批次编号检索"),
    status: str | None = Query(default=None, description="待提交、已提交、已失效"),
    policy_no: str | None = Query(default=None, description="按策略编号过滤"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按批次编号、状态与策略编号过滤重评批次；没有数据时返回空页。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_batches(
        keyword=keyword, status=status, policy_no=policy_no, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{batch_id}", response_model=dict)
def get_batch(batch_id: int) -> dict:
    """读取单个批次：包含逐条方案结果、受影响分区与沿用结论统计。"""
    batch = service.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"重评批次 {batch_id} 不存在")
    return batch


@router.post("/{batch_id}/submit", response_model=ActionResult)
def submit_batch(batch_id: int, payload: EntryPayload) -> ActionResult:
    """一次提交才更新当前计划；重复提交同一批次不会第二次生效。"""
    operator = str(payload.values.get("操作人") or "值班管理员").strip()
    batch, message = service.submit_batch(batch_id, operator)
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=batch)


@router.get("/export")
def export_batches() -> dict[str, Any]:
    """导出重评批次清单：含逐条方案结果，便于离线核对。"""
    items, total = service.list_batches(page=1, size=10000)
    return {"module": "revaluation", "total": total, "items": items}
