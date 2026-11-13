"""存储成本业务规则：状态流转、字段校验与筛选口径都收在这里。

成本详情里的“归档后预计减少”来自与分区详情同一份已提交重评批次，只作为
附注展示；账单本身的存储量、扫描量与金额保持历史口径，不因策略调整而改写。
"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.services.revaluation import RevaluationService

MODULE = "cost"
REQUIRED_FIELDS = ["账单编号", "数据表名", "存储大小GB"]
STATUS_ORDER = ["草稿", "已生成", "已核对", "已归档"]
ACTION_RULES = {"生成账单": "已生成", "核对账单": "已核对", "归档账单": "已归档"}
NEGATIVE_ACTIONS = []

revaluation = RevaluationService()


class CostService:
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
            rows = [row for row in rows if keyword in str(row.get("账单编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        items = [self._with_projection(row) for row in rows[start:start + size]]
        return items, total

    def _with_projection(self, row: dict[str, Any]) -> dict[str, Any]:
        """列表只附注归档预计，账单字段保持历史口径；跨页与分区详情使用同一批次结果。"""
        item = dict(row)
        impact = revaluation.cost_impact(str(row.get("数据表名") or ""))
        item["归档预计减少存储GB"] = impact.get("预计减少存储GB", 0.0)
        item["归档预计减少扫描GB"] = impact.get("预计减少每日扫描GB", 0.0)
        item["归档预计来源批次"] = impact.get("批次编号") or ""
        return item

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def get_detail(self, entry_id: int) -> dict[str, Any] | None:
        """成本详情附注归档预计，与分区详情读同一份重评结果。"""
        entry = self.get_entry(entry_id)
        if entry is None:
            return None
        detail = dict(entry)
        detail["归档预计"] = revaluation.cost_impact(str(entry.get("数据表名") or ""))
        return detail

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in ["每日扫描量GB", "查询次数", "存储成本", "计算成本", "统计月份"]:
            if values.get(field) is not None:
                entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"成本账单 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于存储成本可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"成本账单已{action}"
