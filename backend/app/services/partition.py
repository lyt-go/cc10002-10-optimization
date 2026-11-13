"""分区治理业务规则：状态流转、字段校验与筛选口径都收在这里。

分区详情与方案明细读取同一份已提交重评结果；成本页与分区页通过该结果联动。
"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.services.revaluation import RevaluationService

MODULE = "partition"
ITEM_MODULE = "partitionitem"
REQUIRED_FIELDS = ["方案编号", "数据表名", "分区字段"]
STATUS_ORDER = ["草稿", "方案审批中", "已启用", "已停用"]
ACTION_RULES = {"提交方案审批": "方案审批中", "启用方案": "已启用", "停用方案": "已停用"}
NEGATIVE_ACTIONS = ["停用方案"]

revaluation = RevaluationService()


class PartitionService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("方案编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def list_snapshots(
        self,
        *,
        keyword: str | None = None,
        table: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """列表只拼批次摘要，不重算分区；口径与分区详情一致。"""
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("方案编号", ""))]
        if table:
            rows = [row for row in rows if str(row.get("数据表名") or "") == table]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        enriched = []
        for row in rows[start:start + size]:
            item = dict(row)
            pending = revaluation.pending_for_plan(str(row.get("方案编号") or ""))
            item["待提交批次"] = pending.get("批次编号") if pending else ""
            item["新增到期分区数"] = (
                pending.get("新增到期分区数") if pending is not None else 0
            )
            item["新增预计释放存储GB"] = (
                pending.get("新增预计释放存储GB") if pending is not None else 0.0
            )
            enriched.append(item)
        return enriched, total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def get_detail(self, entry_id: int) -> dict[str, Any] | None:
        """方案详情与成本详情读同一份重评结果，page 之间不再各算一遍。"""
        entry = self.get_entry(entry_id)
        if entry is None:
            return None
        detail = dict(entry)
        snapshot = revaluation.plan_snapshot(entry)
        detail["重评结果"] = {
            key: value
            for key, value in snapshot.items()
            if key != "分区明细"
        }
        detail["分区明细"] = snapshot["分区明细"]
        detail["成本预计"] = revaluation.cost_impact(str(entry.get("数据表名") or ""))
        return detail

    def list_partitions(
        self,
        *,
        plan_no: str | None = None,
        verdict: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(ITEM_MODULE)
        if plan_no:
            rows = [row for row in rows if str(row.get("方案编号") or "") == plan_no]
        if verdict:
            rows = [row for row in rows if str(row.get("重评结论") or "") == verdict]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("分区键", ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_partition(self, entry_id: int) -> dict[str, Any] | None:
        item = store.find(ITEM_MODULE, entry_id)
        if item is None:
            return None
        detail = dict(item)
        plan = next(
            (
                row
                for row in store.rows(MODULE)
                if str(row.get("方案编号") or "") == str(item.get("方案编号") or "")
            ),
            None,
        )
        snapshot = revaluation.plan_snapshot(plan) if plan is not None else None
        detail["重评结果"] = (
            {
                key: value
                for key, value in snapshot.items()
                if key != "分区明细"
            }
            if snapshot is not None
            else {"结果来源": "分区方案不存在"}
        )
        return detail

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in ["分区粒度", "保留天数", "冷热阈值", "预建天数", "归档路径"]:
            if values.get(field) is not None:
                entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["方案版本"] = 1
        entry["当前重评批次"] = ""
        entry["最近重评时间"] = ""
        entry["待归档分区数"] = 0
        entry["预计释放存储GB"] = 0.0
        entry["预计减少每日扫描GB"] = 0.0
        entry["重评状态"] = "尚未重评"
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"分区方案 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于分区治理可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"分区方案已{action}"
