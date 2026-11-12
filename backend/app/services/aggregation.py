"""聚合查询业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.dependencies import resolver
from app.store import store

MODULE = "aggregation"
REQUIRED_FIELDS = ["规则编号", "聚合函数", "目标指标"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用规则": "已启用", "停用规则": "已停用"}
NEGATIVE_ACTIONS = ["停用规则"]
# 启用前必须通过依赖校验的动作；停用不限制（停用本身是在收敛风险）。
DEPENDENCY_GUARDED_ACTIONS = {"提交校验", "启用规则"}
EDITABLE_FIELDS = ["规则编号", "聚合函数", "目标指标", "分组维度", "时间范围", "筛选条件", "采样比例", "缓存秒数"]


class AggregationService:
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
            rows = [row for row in rows if keyword in str(row.get("规则编号", ""))]
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
        entry.update({field: values.get(field) for field in EDITABLE_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        store.bump_revision(MODULE)
        return entry, []

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """修改聚合规则配置（兼容历史文字配置，不强制改为结构化引用）。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"聚合规则 {entry_id} 不存在或已归档"
        changed = False
        for field in EDITABLE_FIELDS:
            if field in values and str(values.get(field) or "") != str(entry.get(field) or ""):
                entry[field] = values.get(field)
                changed = True
        if changed:
            store.bump_revision(MODULE)
        return entry, "聚合规则已更新" if changed else "聚合规则无变化"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"聚合规则 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于聚合查询可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        # 报告与规则都不能绕过无效依赖：提交校验/启用前先跑同一套解析快照。
        if action in DEPENDENCY_GUARDED_ACTIONS:
            preview = resolver.preview_aggregation(entry, use_cache=False)
            if not preview["ok"]:
                detail = "；".join(preview["errors"])
                return None, f"依赖校验未通过，不能{action}：{detail}"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        store.bump_revision(MODULE)
        return entry, f"聚合规则已{action}"
