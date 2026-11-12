"""维度管理业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "dimension"
REQUIRED_FIELDS = ["维度编号", "维度名称", "数据来源表"]
STATUS_ORDER = ["草稿", "待审核", "已生效", "已废弃"]
ACTION_RULES = {"提交审核": "待审核", "生效启用": "已生效", "废弃下线": "已废弃"}
NEGATIVE_ACTIONS = []


class DimensionService:
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
            rows = [row for row in rows if keyword in str(row.get("维度编号", ""))]
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
        store.bump_revision(MODULE)
        return entry, []

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """修改维度字段（如维度键列、拉链策略）；字段变化即推进修订号，旧版本缓存失效。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"分析维度 {entry_id} 不存在或已归档"
        changed = False
        for field in ("维度编号", "维度名称", "数据来源表", "维度键列", "层级关系", "历史拉链策略", "刷新周期", "默认取值"):
            if field in values and str(values.get(field) or "") != str(entry.get(field) or ""):
                entry[field] = values.get(field)
                changed = True
        if changed:
            store.bump_revision(MODULE)
        return entry, "分析维度已更新" if changed else "分析维度无变化"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"分析维度 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于维度管理可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        store.bump_revision(MODULE)
        return entry, f"分析维度已{action}"
