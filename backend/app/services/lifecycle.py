"""数据生命周期业务规则：状态流转、版本校验与重评触发都收在这里。

版本口径：
- 策略版本：当前工作版本，任何参数调整都会 +1；
- 评审版本：最近一次“提交策略评审”对应的版本；
- 生效版本：真正驱动分区治理与成本的持久化版本。
并发修改时以 生效版本 为准：只有 评审版本 == 策略版本 的版本才能启用，
同一个评审版本只允许启用一次，避免两个并发请求各自生成一份重评结果。
"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.services.revaluation import RevaluationService, effective_snapshot

MODULE = "lifecycle"
REQUIRED_FIELDS = ["策略编号", "数据表名", "保留天数"]
NUMERIC_FIELDS = ["保留天数", "冷数据阈值"]
STATUS_ORDER = ["草稿", "策略评审中", "已启用", "已停用"]
ACTION_RULES = {"提交策略评审": "策略评审中", "启用策略": "已启用", "停用策略": "已停用"}
ADJUST_ACTION = "调整策略参数"
NEGATIVE_ACTIONS = ["停用策略"]

revaluation = RevaluationService()


def _as_int(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


class LifecycleService:
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
            rows = [row for row in rows if keyword in str(row.get("策略编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def get_detail(self, entry_id: int) -> dict[str, Any] | None:
        entry = self.get_entry(entry_id)
        if entry is None:
            return None
        detail = dict(entry)
        detail["重评批次"] = self._batch_view(entry)
        return detail

    def _batch_view(self, entry: dict[str, Any]) -> dict[str, Any]:
        policy_no = str(entry.get("策略编号") or "")
        pending = revaluation.pending_for_policy(policy_no)
        committed = revaluation.committed_for_policy(policy_no)
        return {
            "待提交批次": (
                {"批次编号": pending.get("批次编号"), "策略版本": pending.get("策略版本")}
                if pending is not None
                else None
            ),
            "最新已提交批次": (
                {"批次编号": committed.get("批次编号"), "提交时间": committed.get("提交时间")}
                if committed is not None
                else None
            ),
        }

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        for field in NUMERIC_FIELDS:
            if str(values.get(field) or "").strip() and _as_int(values.get(field)) is None:
                return None, [f"{field}（需为整数）"]
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in ["冷数据阈值", "归档格式", "删除确认", "归档路径"]:
            if values.get(field) is not None:
                entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["策略版本"] = 1
        entry["评审版本"] = None
        entry["生效版本"] = 0
        entry["生效保留天数"] = None
        entry["生效冷数据阈值"] = None
        entry["生效归档路径"] = ""
        rows.append(entry)
        return entry, []

    def adjust_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """调整策略参数：版本乐观锁 + 失效旧评审，不直接改写生效快照。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"生命周期策略 {entry_id} 不存在或已归档"
        expected = _as_int(values.get("策略版本"))
        current = int(entry.get("策略版本") or 1)
        # 未显式带版本号时按“读取时的版本”处理；显式带版本时严格比对，
        # 保证并发提交的两个版本只有一个能落地。
        if expected is not None and expected != current:
            return None, (
                f"策略版本冲突：当前为 v{current}，提交的是 v{expected}。"
                "并发修改只允许一个评审版本生效，请重新读取后再提交"
            )

        updates: dict[str, Any] = {}
        for field in NUMERIC_FIELDS:
            if str(values.get(field) or "").strip():
                parsed = _as_int(values.get(field))
                if parsed is None:
                    return None, f"{field} 需为整数"
                updates[field] = parsed
        for field in ["归档格式", "删除确认", "归档路径", "数据表名"]:
            if values.get(field) is not None:
                updates[field] = values.get(field)
        if not updates:
            return None, "没有可调整的字段；请至少提交保留天数、冷数据阈值或归档路径"

        entry.update(updates)
        entry["策略版本"] = current + 1
        entry["评审版本"] = None
        entry["pending"] = True
        # 调整参数需要重新走评审；停用状态也回到草稿，由人工重新提交评审。
        entry["status"] = "草稿"
        revaluation.invalidate_pending(
            str(entry.get("策略编号") or ""),
            f"策略参数调整为 v{entry['策略版本']}，旧参数生成的待提交批次失效",
        )
        return entry, (
            f"策略参数已调整为 v{entry['策略版本']}；"
            f"当前生效版本仍为 v{entry.get('生效版本') or 0}，需重新评审并启用后生成重评批次"
        )

    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        values = values or {}
        if action == ADJUST_ACTION:
            return self.adjust_entry(entry_id, values)
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"生命周期策略 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据生命周期可执行范围"
        if action == "启用策略":
            return self._enable(entry, values)
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        with store.lock:
            if action == "提交策略评审":
                entry["评审版本"] = int(entry.get("策略版本") or 1)
                entry["pending"] = True
            if action == "停用策略":
                # 停用后不再允许把旧参数的待提交批次提交上来。
                revaluation.invalidate_pending(
                    str(entry.get("策略编号") or ""),
                    "策略已停用，待提交重评批次失效",
                )
            entry["status"] = target
            entry["pending"] = target != STATUS_ORDER[-1]
            entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"生命周期策略已{action}"

    def _enable(self, entry: dict[str, Any], values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        with store.lock:
            current_version = int(entry.get("策略版本") or 1)
            expected = _as_int(values.get("策略版本"))
            if expected is not None and expected != current_version:
                return None, (
                    f"策略版本冲突：当前为 v{current_version}，提交的是 v{expected}；"
                    "请基于最新持久化版本重新评审"
                )
            review_version = _as_int(entry.get("评审版本"))
            effective_version = int(entry.get("生效版本") or 0)
            if effective_version == current_version and review_version in (None, current_version):
                return None, (
                    f"策略 v{current_version} 已生效，重复启用不再生成第二份重评批次；"
                    "如需重评请调整参数后重新评审"
                )
            if review_version is None or review_version != current_version:
                return None, (
                    f"策略 v{current_version} 尚未评审（评审版本 "
                    f"{review_version if review_version is not None else '无'}），"
                    "请先提交策略评审再启用"
                )
            entry["status"] = "已启用"
            entry["pending"] = False
            entry["abnormal"] = False
            previous = effective_snapshot(entry)
            entry["生效版本"] = review_version
            entry["生效保留天数"] = _as_int(entry.get("保留天数"))
            entry["生效冷数据阈值"] = _as_int(entry.get("冷数据阈值"))
            entry["生效归档路径"] = str(entry.get("归档路径") or "")
            entry["评审版本"] = None
            revaluation.create_batch(entry, previous)
            pending = revaluation.pending_for_policy(str(entry.get("策略编号") or ""))
        return entry, (
            f"生命周期策略 v{review_version} 已启用，已按批量分区生成待提交重评批次 "
            f"{pending.get('批次编号') if pending else ''}"
        )
