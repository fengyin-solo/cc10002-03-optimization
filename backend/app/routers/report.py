"""分析报告接口：维护报告模板，覆盖提交审核、生效发布、停用模板等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.dependencies import resolver
from app.services.report import ReportService

router = APIRouter(prefix="/api/report", tags=["分析报告"])

service = ReportService()

LIST_FIELDS = ["模板编号", "模板名称", "包含指标", "报告周期", "图表模板", "导出格式", "推送渠道", "接收人列表"]
STATUSES = ["草稿", "待审核", "已生效", "已停用"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按模板编号检索"),
    status: str | None = Query(default=None, description="草稿、待审核、已生效、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按模板编号与状态过滤分析报告列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/dependency-preview")
def dependency_preview(payload: EntryPayload) -> dict[str, Any]:
    """报告选取聚合规则后的依赖解释：实际采用的指标/维度版本与快照判断。

    与聚合查询预览共用同一解析器与同一份依赖快照，结论保持一致；
    无效或歧义依赖会直接给出阻断原因，报告不能绕过。
    """
    rule_text = payload.values.get("聚合规则")
    if rule_text is None:
        entry_id = payload.values.get("entryId")
        if entry_id is not None:
            entry = service.get_entry(int(entry_id))
            if entry is None:
                raise HTTPException(status_code=404, detail=f"报告模板 {entry_id} 不存在或已归档")
            rule_text = entry.get("聚合规则") or ""
    return resolver.preview_report_rules(rule_text)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出分析报告清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "report", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条报告模板明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"报告模板 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条报告模板，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="报告模板已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条报告模板执行提交审核、生效发布、停用模板；不允许的动作会被拦下并说明原因。

    提交审核与生效发布前强制校验聚合规则依赖，无效依赖阻断，报告无法绕过。
    """
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """更新报告模板（含聚合规则引用）；是否可发布由依赖校验决定。"""
    entry, message = service.update_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
