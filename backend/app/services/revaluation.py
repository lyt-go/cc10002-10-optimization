"""分区重评业务规则：把策略版本变化落成可提交的批量重评结果。

设计取舍（两条实现路线中的选择）：
- 不引入消息队列，采用“持久化重评批次”这一同步路线：策略启用时按当前物理分区
  计算一版待提交结果，人工提交后才一次性更新分区方案、分区结论，并写审计。
- 批次结果是分区列表、分区详情与成本详情的唯一数据源；历史成本账单保持只读，
  预计释放量只在读取时由同一份批次结果拼出来。

状态流转全部收在本服务，路由层不判断业务。
"""
from __future__ import annotations

import copy
from datetime import date, datetime
from typing import Any

from app.store import store

MODULE = "revaluation"
POLICY_MODULE = "lifecycle"
PLAN_MODULE = "partition"
ITEM_MODULE = "partitionitem"
COST_MODULE = "cost"
AUDIT_MODULE = "auditlog"

PENDING = "待提交"
SUBMITTED = "已提交"

VERDICT_ARCHIVE = "待归档"
VERDICT_COLD = "转冷"
VERDICT_KEEP = "保留"
VERDICT_ARCHIVED = "已归档"
VERDICT_DELETED = "已删除"

ONLINE_STATUS = "在线"


def _num(value: Any, default: int | None = None) -> int | None:
    if value is None or str(value).strip() == "":
        return default
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _amount(value: Any) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def _day(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _today() -> date:
    return date.today()


def _age_days(created: Any, today: date) -> int | None:
    created_on = _day(created)
    return None if created_on is None else (today - created_on).days


def _days_since(value: Any, today: date) -> int | None:
    seen = _day(value)
    return None if seen is None else (today - seen).days


def _is_archived(partition: dict[str, Any]) -> bool:
    return str(partition.get("分区状态") or "") == "已归档" or str(partition.get("重评结论") or "") == VERDICT_ARCHIVED


def _is_deleted(partition: dict[str, Any]) -> bool:
    return str(partition.get("分区状态") or "") == "已删除" or str(partition.get("重评结论") or "") == VERDICT_DELETED


def _plans_for_table(table: str) -> list[dict[str, Any]]:
    rows = store.rows(PLAN_MODULE)
    return [
        row
        for row in rows
        if str(row.get("数据表名") or "") == table and str(row.get("status") or "") == "已启用"
    ]


def _partitions_for(plan: dict[str, Any]) -> list[dict[str, Any]]:
    code = str(plan.get("方案编号") or "")
    return [row for row in store.rows(ITEM_MODULE) if str(row.get("方案编号") or "") == code]


def _next_batch_code() -> str:
    ids = [_num(row.get("id"), 0) or 0 for row in store.rows(MODULE)]
    return f"REVL-{max(ids, default=0) + 1:04d}"


def _batch_no(batch: dict[str, Any]) -> str:
    return str(batch.get("批次编号") or batch.get("id") or "")


def effective_snapshot(policy: dict[str, Any]) -> dict[str, Any]:
    """读取持久化的生效快照；没有生效版本时返回空值，代表“首次重评”。"""
    if _num(policy.get("生效版本"), 0) in (None, 0):
        return {"保留天数": None, "冷数据阈值": None, "归档路径": ""}
    return {
        "保留天数": _num(policy.get("生效保留天数")),
        "冷数据阈值": _num(policy.get("生效冷数据阈值")),
        "归档路径": str(policy.get("生效归档路径") or ""),
    }


def _new_cold(
    access_days: int | None,
    old_threshold: int | None,
    new_threshold: int | None,
    prior: str,
    expired: bool,
) -> bool:
    if expired or access_days is None or new_threshold is None:
        return False
    if access_days <= new_threshold:
        return False
    if prior in (VERDICT_COLD, VERDICT_ARCHIVE):
        return False
    if old_threshold is None:
        return True
    return access_days > old_threshold


def _build_plan_result(
    plan: dict[str, Any],
    *,
    old: dict[str, Any],
    new: dict[str, Any],
    today: date,
) -> dict[str, Any]:
    old_retention = old["保留天数"]
    new_retention = new["保留天数"]
    old_threshold = old["冷数据阈值"]
    new_threshold = new["冷数据阈值"]
    old_path = str(old["归档路径"] or "")
    new_path = str(new["归档路径"] or "")
    params_changed = (
        old_retention != new_retention
        or old_threshold != new_threshold
        or old_path != new_path
    )

    new_expired: list[dict[str, Any]] = []
    new_cold: list[dict[str, Any]] = []
    removed_expired: list[dict[str, Any]] = []
    removed_cold: list[dict[str, Any]] = []
    reused = 0
    archived_skipped = 0
    deleted_kept = 0
    projected_pending: list[dict[str, Any]] = []
    projected_cold = 0

    for partition in _partitions_for(plan):
        if _is_archived(partition):
            archived_skipped += 1
            continue
        if _is_deleted(partition):
            deleted_kept += 1
            continue
        if str(partition.get("分区状态") or "") != ONLINE_STATUS:
            continue

        prior = str(partition.get("重评结论") or "")
        age = _age_days(partition.get("创建时间"), today)
        access_days = _days_since(partition.get("最近访问时间"), today)
        expired = age is not None and new_retention is not None and age > new_retention
        cold = (
            not expired
            and new_threshold is not None
            and access_days is not None
            and access_days > new_threshold
        )
        was_expired = old_retention is not None and age is not None and age > old_retention

        snapshot = {
            "分区编号": partition.get("分区编号"),
            "分区键": partition.get("分区键"),
            "创建时间": partition.get("创建时间"),
            "最近访问时间": partition.get("最近访问时间"),
            "存储大小GB": _amount(partition.get("存储大小GB")),
            "每日扫描量GB": _amount(partition.get("每日扫描量GB")),
            "归档路径": new_path or old_path,
            "原结论": prior or VERDICT_KEEP,
            "分区年龄天": age,
            "距最近访问天": access_days,
        }

        # 只对落在“新增到期区间”或“新增转冷区间”的分区给出新结论；
        # 原本已在到期/转冷结论里的分区按上一轮结论继续复用。
        newly_expired = expired and not was_expired and prior != VERDICT_ARCHIVE
        if newly_expired:
            new_expired.append(snapshot)
            projected_pending.append(snapshot)
            continue

        newly_cold = _new_cold(access_days, old_threshold, new_threshold, prior, expired)
        if newly_cold:
            new_cold.append(snapshot)
            projected_cold += 1
            continue

        if prior == VERDICT_ARCHIVE and not expired:
            # 保留天数放宽后，原本待归档但已回到保留窗口的在线分区撤销归档提示。
            removed_expired.append(snapshot)
            reused += 1
            continue

        if prior == VERDICT_COLD and not cold and not expired:
            removed_cold.append(snapshot)
            reused += 1
            continue

        if prior == VERDICT_ARCHIVE:
            projected_pending.append(snapshot)
        elif prior == VERDICT_COLD:
            projected_cold += 1
        reused += 1

    projected_release = round(sum(item["存储大小GB"] for item in projected_pending), 2)
    projected_scan = round(sum(item["每日扫描量GB"] for item in projected_pending), 2)
    added_release = round(sum(item["存储大小GB"] for item in new_expired), 2)
    added_scan = round(sum(item["每日扫描量GB"] for item in new_expired), 2)

    if params_changed:
        threshold_note = (
            ""
            if old_threshold == new_threshold
            else f"冷热阈值 {old_threshold if old_threshold is not None else '未生效'} → {new_threshold}，"
        )
        notes = (
            f"保留天数 {old_retention if old_retention is not None else '未生效'} → {new_retention}；"
            f"{threshold_note}"
            f"仅新增到期区间命中 {len(new_expired)} 个分区，归档路径按 {new_path or old_path} 计算。"
        )
    else:
        notes = "参数未变化，全部在线分区沿用上一轮结论。"

    return {
        "方案编号": plan.get("方案编号"),
        "数据表名": plan.get("数据表名"),
        "分区字段": plan.get("分区字段"),
        "旧保留天数": old_retention,
        "新保留天数": new_retention,
        "旧冷数据阈值": old_threshold,
        "新冷数据阈值": new_threshold,
        "旧归档路径": old_path,
        "新归档路径": new_path,
        "参数变化": params_changed,
        "新增到期分区数": len(new_expired),
        "新增转冷分区数": len(new_cold),
        "移除到期分区数": len(removed_expired),
        "解除转冷分区数": len(removed_cold),
        "沿用结论分区数": reused,
        "已归档跳过数": archived_skipped,
        "已删除保留数": deleted_kept,
        "待归档分区数": len(projected_pending),
        "转冷分区数": projected_cold,
        "预计释放存储GB": projected_release,
        "预计减少每日扫描GB": projected_scan,
        "新增预计释放存储GB": added_release,
        "新增预计减少每日扫描GB": added_scan,
        "新增到期分区": new_expired,
        "新增转冷分区": new_cold,
        "移除到期分区": removed_expired,
        "解除转冷分区": removed_cold,
        "说明": notes,
    }


class RevaluationService:
    def list_batches(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        policy_no: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("批次编号", ""))]
        if status:
            rows = [row for row in rows if row.get("批次状态") == status]
        if policy_no:
            rows = [row for row in rows if str(row.get("策略编号") or "") == policy_no]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_batch(self, batch_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, batch_id)

    def pending_count(self, policy_no: str | None = None) -> int:
        rows = store.rows(MODULE)
        return sum(
            1
            for row in rows
            if str(row.get("批次状态") or "") == PENDING
            and (policy_no is None or str(row.get("策略编号") or "") == policy_no)
        )

    def pending_for_policy(self, policy_no: str) -> dict[str, Any] | None:
        for row in store.rows(MODULE):
            if str(row.get("策略编号") or "") == policy_no and str(row.get("批次状态") or "") == PENDING:
                return row
        return None

    def invalidate_pending(self, policy_no: str, reason: str) -> int:
        """参数被重新调整时，旧参数算出的待提交批次直接作废，避免拿过期结果提交。"""
        count = 0
        for row in store.rows(MODULE):
            if str(row.get("策略编号") or "") != policy_no:
                continue
            if str(row.get("批次状态") or "") != PENDING:
                continue
            row["批次状态"] = "已失效"
            row["失效原因"] = reason
            count += 1
        return count

    def committed_for_policy(self, policy_no: str) -> dict[str, Any] | None:
        candidates = [
            row
            for row in store.rows(MODULE)
            if str(row.get("策略编号") or "") == policy_no
            and str(row.get("批次状态") or "") == SUBMITTED
        ]
        return max(candidates, key=lambda row: int(row.get("id", 0)), default=None)

    def committed_for_table(self, table: str) -> dict[str, Any] | None:
        candidates = [
            row
            for row in store.rows(MODULE)
            if str(row.get("数据表名") or "") == table
            and str(row.get("批次状态") or "") == SUBMITTED
        ]
        return max(candidates, key=lambda row: int(row.get("id", 0)), default=None)

    def create_batch(
        self, policy: dict[str, Any], previous: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """按持久化策略参数与当前物理分区生成一版待提交结果，不修改任何计划。

        previous 是启用前读取到的上一版生效快照；不传时按当前持久化快照比较。
        """
        old = previous or effective_snapshot(policy)
        new = {
            "保留天数": _num(policy.get("保留天数")),
            "冷数据阈值": _num(policy.get("冷数据阈值")),
            "归档路径": str(policy.get("归档路径") or ""),
        }
        today = _today()
        results: list[dict[str, Any]] = []
        for plan in _plans_for_table(str(policy.get("数据表名") or "")):
            results.append(_build_plan_result(plan, old=old, new=new, today=today))

        batch_id = max((int(row.get("id", 0)) for row in store.rows(MODULE)), default=0) + 1
        batch = {
            "id": batch_id,
            "批次编号": _next_batch_code(),
            "策略编号": policy.get("策略编号"),
            "数据表名": policy.get("数据表名"),
            "策略版本": _num(policy.get("评审版本"), _num(policy.get("策略版本"), 1)),
            "旧保留天数": old["保留天数"],
            "新保留天数": new["保留天数"],
            "旧冷数据阈值": old["冷数据阈值"],
            "新冷数据阈值": new["冷数据阈值"],
            "旧归档路径": old["归档路径"],
            "新归档路径": new["归档路径"],
            "触发方式": "策略启用",
            "批次状态": PENDING,
            "生成时间": today.isoformat(),
            "提交时间": "",
            "提交人": "",
            "受影响方案数": len(results),
            "新增到期分区数": sum(item["新增到期分区数"] for item in results),
            "新增转冷分区数": sum(item["新增转冷分区数"] for item in results),
            "移除到期分区数": sum(item["移除到期分区数"] for item in results),
            "解除转冷分区数": sum(item["解除转冷分区数"] for item in results),
            "沿用结论分区数": sum(item["沿用结论分区数"] for item in results),
            "已归档跳过数": sum(item["已归档跳过数"] for item in results),
            "已删除保留数": sum(item["已删除保留数"] for item in results),
            "待归档分区数": sum(item["待归档分区数"] for item in results),
            "预计释放存储GB": round(sum(item["预计释放存储GB"] for item in results), 2),
            "预计减少每日扫描GB": round(sum(item["预计减少每日扫描GB"] for item in results), 2),
            "新增预计释放存储GB": round(sum(item["新增预计释放存储GB"] for item in results), 2),
            "新增预计减少每日扫描GB": round(sum(item["新增预计减少每日扫描GB"] for item in results), 2),
            "结果": results,
        }
        store.rows(MODULE).append(batch)
        return batch

    def submit_batch(self, batch_id: int, operator: str) -> tuple[dict[str, Any] | None, str]:
        """提交一版批次：一次性更新计划与分区结论，并同步写审计；重复提交不再生效。"""
        with store.lock:
            batch = store.find(MODULE, batch_id)
            if batch is None:
                return None, f"重评批次 {batch_id} 不存在"
            if str(batch.get("批次状态") or "") != PENDING:
                if str(batch.get("批次状态") or "") == SUBMITTED:
                    return None, f"重评批次 {_batch_no(batch)} 已提交，重复重评只生效一次"
                return None, (
                    f"重评批次 {_batch_no(batch)} 状态为「{batch.get('批次状态')}」，"
                    f"不再可提交：{batch.get('失效原因') or '批次已失效'}"
                )
            today = _today().isoformat()
            results = batch.get("结果") or []
            for result in results:
                self._apply_plan(result, batch, today, operator)
            batch["批次状态"] = SUBMITTED
            batch["提交时间"] = today
            batch["提交人"] = operator
        return batch, f"重评批次 {_batch_no(batch)} 已提交，分区计划与成本预计已按同一结果更新"

    def _apply_plan(self, result: dict[str, Any], batch: dict[str, Any], today: str, operator: str) -> None:
        plan = next(
            (
                row
                for row in store.rows(PLAN_MODULE)
                if str(row.get("方案编号") or "") == str(result.get("方案编号") or "")
            ),
            None,
        )
        if plan is None:
            return
        new_path = str(result.get("新归档路径") or "")
        plan["保留天数"] = result.get("新保留天数")
        plan["冷热阈值"] = result.get("新冷数据阈值")
        plan["归档路径"] = new_path
        plan["方案版本"] = int(plan.get("方案版本") or 1) + 1
        plan["当前重评批次"] = batch.get("批次编号")
        plan["最近重评时间"] = today
        plan["待归档分区数"] = int(result.get("待归档分区数") or 0)
        plan["预计释放存储GB"] = float(result.get("预计释放存储GB") or 0.0)
        plan["预计减少每日扫描GB"] = float(result.get("预计减少每日扫描GB") or 0.0)
        plan["重评状态"] = "已按新策略重评" if result.get("参数变化") else "沿用上一轮结论"

        partitions = _partitions_for(plan)
        for item in result.get("新增到期分区") or []:
            partition = self._partition(partitions, item.get("分区编号"))
            if partition is None:
                continue
            partition["重评结论"] = VERDICT_ARCHIVE
            partition["结论批次"] = batch.get("批次编号")
            partition["结论时间"] = today
            partition["归档路径"] = item.get("归档路径") or new_path
            partition["预计释放存储GB"] = item.get("存储大小GB")
            partition["预计减少每日扫描GB"] = item.get("每日扫描量GB")

        for item in result.get("新增转冷分区") or []:
            partition = self._partition(partitions, item.get("分区编号"))
            if partition is None:
                continue
            partition["重评结论"] = VERDICT_COLD
            partition["结论批次"] = batch.get("批次编号")
            partition["结论时间"] = today

        for item in result.get("移除到期分区") or []:
            partition = self._partition(partitions, item.get("分区编号"))
            if partition is None:
                continue
            partition["重评结论"] = VERDICT_KEEP
            partition["结论批次"] = batch.get("批次编号")
            partition["结论时间"] = today
            partition["预计释放存储GB"] = 0.0
            partition["预计减少每日扫描GB"] = 0.0

        for item in result.get("解除转冷分区") or []:
            partition = self._partition(partitions, item.get("分区编号"))
            if partition is None:
                continue
            partition["重评结论"] = VERDICT_KEEP
            partition["结论批次"] = batch.get("批次编号")
            partition["结论时间"] = today

        # 归档路径变化时，所有仍待归档的在线分区都换到新路径；结论批次保持不变，
        # 避免把“路径同步”误记成一次新的重评。
        if new_path:
            for partition in partitions:
                if _is_archived(partition) or _is_deleted(partition):
                    continue
                if str(partition.get("重评结论") or "") == VERDICT_ARCHIVE:
                    partition["归档路径"] = new_path

        self._write_audit(batch, result, operator, today)

    def _partition(self, partitions: list[dict[str, Any]], partition_no: Any) -> dict[str, Any] | None:
        for item in partitions:
            if str(item.get("分区编号") or "") == str(partition_no or ""):
                return item
        return None

    def _write_audit(self, batch: dict[str, Any], result: dict[str, Any], operator: str, today: str) -> None:
        rows = store.rows(AUDIT_MODULE)
        entry_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        before = (
            f"保留{result.get('旧保留天数')}天/冷阈值{result.get('旧冷数据阈值')}/"
            f"归档{result.get('旧归档路径') or '未配置'}"
        )
        after = (
            f"保留{result.get('新保留天数')}天/冷阈值{result.get('新冷数据阈值')}/"
            f"归档{result.get('新归档路径') or '未配置'}"
        )
        rows.append({
            "id": entry_id,
            "status": "已记录",
            "pending": False,
            "abnormal": False,
            "记录编号": f"AUDI-{entry_id:04d}",
            "操作表名": result.get("数据表名"),
            "变更类型": "生命周期策略重评提交",
            "变更前值": before,
            "变更后值": after,
            "操作人": operator,
            "操作时间": today,
            "变更原因": (
                f"重评批次 {batch.get('批次编号')}（策略 {batch.get('策略编号')} "
                f"v{batch.get('策略版本')}）提交：新增到期 {result.get('新增到期分区数')} 个，"
                f"新增转冷 {result.get('新增转冷分区数')} 个，沿用上一轮结论 "
                f"{result.get('沿用结论分区数')} 个。"
            ),
        })

    # ---------- 共享读取：分区与成本都从这里取同一份结果 ----------

    def plan_snapshot(self, plan: dict[str, Any]) -> dict[str, Any]:
        """返回方案对应的最新已提交批次结果；没有已提交结果时明确说明。"""
        plan_no = str(plan.get("方案编号") or "")
        batch = None
        for candidate in store.rows(MODULE):
            if str(candidate.get("批次状态") or "") != SUBMITTED:
                continue
            for result in candidate.get("结果") or []:
                if str(result.get("方案编号") or "") == plan_no:
                    if batch is None or int(candidate.get("id", 0)) > int(batch.get("id", 0)):
                        batch = candidate
                    break
        result = None
        if batch is not None:
            result = next(
                (
                    item
                    for item in batch.get("结果") or []
                    if str(item.get("方案编号") or "") == plan_no
                ),
                None,
            )
        partitions = [
            dict(item)
            for item in _partitions_for(plan)
        ]
        return {
            "方案编号": plan_no,
            "数据表名": plan.get("数据表名"),
            "当前重评批次": plan.get("当前重评批次") or "",
            "最近重评时间": plan.get("最近重评时间") or "",
            "待归档分区数": int(plan.get("待归档分区数") or 0),
            "预计释放存储GB": float(plan.get("预计释放存储GB") or 0.0),
            "预计减少每日扫描GB": float(plan.get("预计减少每日扫描GB") or 0.0),
            "重评状态": plan.get("重评状态") or "尚未重评",
            "方案版本": plan.get("方案版本"),
            "批次": copy.deepcopy(batch) if batch is not None else None,
            "批次结果": copy.deepcopy(result) if result is not None else None,
            "分区明细": partitions,
            "待提交批次": self._pending_snapshot_for_plan(plan_no),
            "结果来源": f"重评批次 {batch.get('批次编号')}" if batch is not None else "尚无已提交重评批次，沿用方案当前结论",
        }

    def plan_snapshot_by_no(self, plan_no: str) -> dict[str, Any] | None:
        plan = next(
            (row for row in store.rows(PLAN_MODULE) if str(row.get("方案编号") or "") == plan_no),
            None,
        )
        return None if plan is None else self.plan_snapshot(plan)

    def pending_for_plan(self, plan_no: str) -> dict[str, Any] | None:
        for batch in store.rows(MODULE):
            if str(batch.get("批次状态") or "") != PENDING:
                continue
            for result in batch.get("结果") or []:
                if str(result.get("方案编号") or "") == plan_no:
                    return {
                        "批次编号": batch.get("批次编号"),
                        "策略编号": batch.get("策略编号"),
                        "新增到期分区数": result.get("新增到期分区数"),
                        "新增转冷分区数": result.get("新增转冷分区数"),
                        "新增预计释放存储GB": result.get("新增预计释放存储GB"),
                        "新增预计减少每日扫描GB": result.get("新增预计减少每日扫描GB"),
                        "说明": result.get("说明"),
                    }
        return None

    def _pending_snapshot_for_plan(self, plan_no: str) -> dict[str, Any] | None:
        return self.pending_for_plan(plan_no)

    def cost_impact(self, table: str) -> dict[str, Any]:
        """成本详情与分区详情共用同一份已提交批次，不读草稿账单、不改写历史账单。"""
        batch = self.committed_for_table(table)
        if batch is None:
            return {
                "数据表名": table,
                "结果来源": "尚无已提交重评批次，暂不给出归档预计",
                "批次编号": "",
                "待归档分区数": 0,
                "预计减少存储GB": 0.0,
                "预计减少每日扫描GB": 0.0,
                "方案": [],
            }
        results = [
            item for item in batch.get("结果") or []
            if str(item.get("数据表名") or "") == table
        ]
        return {
            "数据表名": table,
            "结果来源": f"重评批次 {batch.get('批次编号')}",
            "批次编号": batch.get("批次编号"),
            "批次状态": batch.get("批次状态"),
            "提交时间": batch.get("提交时间"),
            "待归档分区数": sum(int(item.get("待归档分区数") or 0) for item in results),
            "预计减少存储GB": round(sum(float(item.get("预计释放存储GB") or 0.0) for item in results), 2),
            "预计减少每日扫描GB": round(sum(float(item.get("预计减少每日扫描GB") or 0.0) for item in results), 2),
            "方案": [
                {
                    "方案编号": item.get("方案编号"),
                    "新增到期分区数": item.get("新增到期分区数"),
                    "待归档分区数": item.get("待归档分区数"),
                    "预计释放存储GB": item.get("预计释放存储GB"),
                    "预计减少每日扫描GB": item.get("预计减少每日扫描GB"),
                    "新增到期分区": item.get("新增到期分区"),
                    "说明": item.get("说明"),
                }
                for item in results
            ],
        }
