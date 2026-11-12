"""聚合查询接口：维护聚合规则，覆盖提交校验、启用规则、停用规则等动作。

除原有 CRUD/动作外，新增跨模块依赖能力：
- GET /candidates：拉取指标定义、维度管理的候选清单（含状态、版本、不可用原因）。
- POST /preview：对一份配置（可未保存）做依赖校验预览，返回解析结论、快照版本与错误。
- GET /duplicates：按解析后的真实指标/维度识别同口径重复规则。
- PUT /{id}：更新规则文字配置，仍兼容历史纯文字读取。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.aggregation import AggregationService
from app.services.dependencies import SNAPSHOT_MODULES, resolver
from app.store import store

router = APIRouter(prefix="/api/aggregation", tags=["聚合查询"])

service = AggregationService()

LIST_FIELDS = ["规则编号", "聚合函数", "目标指标", "分组维度", "时间范围", "筛选条件", "采样比例", "缓存秒数"]
STATUSES = ["草稿", "待校验", "已启用", "已停用"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按规则编号检索"),
    status: str | None = Query(default=None, description="草稿、待校验、已启用、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按规则编号与状态过滤聚合查询列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/candidates")
def candidates() -> dict[str, Any]:
    """跨模块候选：指标定义 + 维度管理，带版本、状态与不能使用的原因。"""
    return {
        "metrics": resolver.metric_candidates(),
        "dimensions": resolver.dimension_candidates(),
        "snapshotRevisions": resolver_snapshot_revisions(),
    }


@router.post("/preview")
def preview(payload: EntryPayload) -> dict[str, Any]:
    """校验预览：不写库，只解析配置中的指标/维度文字引用并给出快照结论。

    入参支持两种形态：已保存规则的 {entryId} 或直接提交的 {values: {...}}。
    歧义引用不会被自动选中，失效依赖会逐条给出原因。
    """
    values = dict(payload.values)
    entry_id = values.pop("entryId", None)
    if entry_id is not None:
        try:
            entry_id_int = int(entry_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="entryId 必须是数字编号")
        entry = service.get_entry(entry_id_int)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"聚合规则 {entry_id_int} 不存在或已归档")
        # 以已存规则为底，提交值覆盖对应字段，支持「改一点就预览」。
        merged = {**entry, **values}
        values = merged
    return resolver.preview_aggregation(values)


@router.get("/duplicates")
def duplicates() -> dict[str, Any]:
    """同口径重复预览：解析后指向相同指标、维度与聚合函数的规则归为一组。"""
    groups = resolver.duplicate_groups()
    return {"groups": groups, "total": len(groups)}


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出聚合查询清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "aggregation", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条聚合规则明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"聚合规则 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条聚合规则，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="聚合规则已登记", entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """更新聚合规则文字配置；兼容历史纯文字字段，不强制结构化。"""
    entry, message = service.update_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条聚合规则执行提交校验、启用规则、停用规则；不允许的动作会被拦下并说明原因。

    提交校验与启用前会强制跑依赖校验，无效/歧义依赖直接阻断，不能绕过。
    """
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


def resolver_snapshot_revisions() -> dict[str, int]:
    return store.revisions(SNAPSHOT_MODULES)
