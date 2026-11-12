"""指标定义业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "metricdef"
REQUIRED_FIELDS = ["指标编号", "指标名称", "计算公式"]
STATUS_ORDER = ["草稿", "待审核", "已生效", "已废弃"]
ACTION_RULES = {"提交审核": "待审核", "生效发布": "已生效", "废弃下线": "已废弃"}
NEGATIVE_ACTIONS = []


class MetricdefService:
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
            rows = [row for row in rows if keyword in str(row.get("指标编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        # 新增候选会改变名称/编号的歧义判断，必须推进修订号。
        store.bump_revision(MODULE)
        return entry, []

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """修改指标口径字段（如口径版本、计算公式）；任何字段变化都推进修订号。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"指标口径 {entry_id} 不存在或已归档"
        changed = False
        for field in ("指标编号", "指标名称", "计算公式", "统计粒度", "时间窗口类型", "筛选条件", "精度位数", "口径版本"):
            if field in values and str(values.get(field) or "") != str(entry.get(field) or ""):
                entry[field] = values.get(field)
                changed = True
        if changed:
            store.bump_revision(MODULE)
        return entry, "指标口径已更新" if changed else "指标口径无变化"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"指标口径 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于指标定义可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        # 生效/废弃直接改变引用可用性，推进修订号让旧解析缓存失效。
        store.bump_revision(MODULE)
        return entry, f"指标口径已{action}"
