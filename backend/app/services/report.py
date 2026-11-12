"""分析报告业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.dependencies import resolver
from app.store import store

MODULE = "report"
REQUIRED_FIELDS = ["模板编号", "模板名称", "包含指标"]
STATUS_ORDER = ["草稿", "待审核", "已生效", "已停用"]
ACTION_RULES = {"提交审核": "待审核", "生效发布": "已生效", "停用模板": "已停用"}
NEGATIVE_ACTIONS = ["停用模板"]
# 报告生效前必须确认所选聚合规则及其指标/维度依赖全部有效，不能绕过无效依赖。
DEPENDENCY_GUARDED_ACTIONS = {"提交审核", "生效发布"}


class ReportService:
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
            rows = [row for row in rows if keyword in str(row.get("模板编号", ""))]
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
        if values.get("聚合规则") is not None:
            entry["聚合规则"] = values.get("聚合规则")
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """更新报告模板，包含聚合规则引用的调整。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"报告模板 {entry_id} 不存在或已归档"
        changed = False
        for field in ("模板编号", "模板名称", "包含指标", "报告周期", "图表模板", "导出格式", "推送渠道", "接收人列表", "聚合规则"):
            if field in values and str(values.get(field) or "") != str(entry.get(field) or ""):
                entry[field] = values.get(field)
                changed = True
        return entry, "报告模板已更新" if changed else "报告模板无变化"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"报告模板 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于分析报告可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        if action in DEPENDENCY_GUARDED_ACTIONS:
            rule_text = entry.get("聚合规则") or entry.get("包含指标") or ""
            dependency = resolver.preview_report_rules(rule_text)
            if not dependency["ok"]:
                detail = "；".join(dependency["errors"])
                return None, f"报告依赖校验未通过，不能{action}：{detail}"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"报告模板已{action}"
