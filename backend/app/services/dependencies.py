"""跨模块依赖解析：把聚合规则里的文字引用解析成指标定义/维度管理的具体记录。

设计要点（对应聚合查询配置的可用性与重复校验诉求）：

1. 引用只是文字，解析时不猜：按编号精确匹配，其次按名称匹配；名称命中多条
   （同名不同编号）一律判为歧义，列出全部候选让用户显式选择，不自行挑一个。
2. 每条解析结果都带「能否使用」和原因：已废弃、非生效状态、记录缺失都有可读说明，
   并回传指标口径版本、维度键列等实际采用的版本信息。
3. 双层缓存，且都不靠 TTL 续命：
   - 文字引用解析缓存：按 token 缓存，缓存项带依赖表修订号；指标废弃/维度变更推进
     修订号后旧条目自然失效，频繁改筛选条件不会重复解析没变过的指标/维度。
   - 整份配置预览缓存：按「配置指纹 + 依赖快照修订号」缓存，单次预览内拿到的依赖
     快照一致；配置一改指纹就变，绝不会拿旧成功结果掩盖本次失败。
4. 失败结果只在当前修订号下短期保留（同样带修订号），修订号一变立刻重算，
   因此错误不会污染下一次有效预览。
"""
from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass, field
from typing import Any

from app.store import store

METRIC_MODULE = "metricdef"
DIMENSION_MODULE = "dimension"
AGGREGATION_MODULE = "aggregation"
SNAPSHOT_MODULES = [METRIC_MODULE, DIMENSION_MODULE, AGGREGATION_MODULE]

# 只有「已生效」的指标/维度可以被聚合规则引用；其他状态都给出明确原因。
USABLE_STATUS = "已生效"
# 文字配置里可能出现的分隔符：兼容历史上手填的逗号、顿号、分号、空白。
_TOKEN_SPLIT_RE = re.compile(r"[,，;；、\s]+")

# 缓存上限：只做容量保护，淘汰不影响正确性（丢了重算即可），不承担失效职责。
_TOKEN_CACHE_LIMIT = 1024
_PREVIEW_CACHE_LIMIT = 256


def split_refs(text: Any) -> list[str]:
    """把一段文字引用拆成去重后的 token 列表；空文字返回空列表。"""
    if text is None:
        return []
    parts = [part.strip() for part in _TOKEN_SPLIT_RE.split(str(text))]
    seen: set[str] = set()
    tokens: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.add(part)
            tokens.append(part)
    return tokens


def config_fingerprint(values: dict[str, Any]) -> str:
    """对聚合配置做稳定指纹：字段顺序不影响，只由实际内容决定。

    频繁编辑筛选条件时指纹随之变化，新配置不会命中旧预览；未变的依赖 token
    仍可命中解析缓存，避免重复解析。
    """
    normalized = {
        key: str(values.get(key) or "").strip()
        for key in ("规则编号", "聚合函数", "目标指标", "分组维度", "时间范围", "筛选条件", "采样比例")
    }
    payload = "|".join(f"{key}={normalized[key]}" for key in sorted(normalized))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class ResolvedRef:
    """单个文字引用的解析结论。"""

    token: str
    kind: str  # metric / dimension
    status: str  # resolved / ambiguous / not_found / unusable
    usable: bool
    reason: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
    selected: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "kind": self.kind,
            "status": self.status,
            "usable": self.usable,
            "reason": self.reason,
            "candidates": self.candidates,
            "selected": self.selected,
        }


@dataclass
class DependencySnapshot:
    """一次预览/报告引用所依据的依赖快照。"""

    revisions: dict[str, int]
    fingerprint: str
    metrics: list[dict[str, Any]]
    dimensions: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "revisions": self.revisions,
            "fingerprint": self.fingerprint,
            "metrics": self.metrics,
            "dimensions": self.dimensions,
        }


class DependencyResolver:
    """指标/维度引用解析器，进程内单例，线程安全（FastAPI 可能并发处理预览）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # token 解析缓存：(kind, token) -> (依赖表修订号, ResolvedRef)
        self._token_cache: dict[tuple[str, str], tuple[int, ResolvedRef]] = {}
        # 整份配置预览缓存：指纹 -> (快照修订号元组, 结果 dict)
        self._preview_cache: dict[str, tuple[tuple[int, ...], dict[str, Any]]] = {}
        # 观测计数，便于在界面/测试里确认复用是否发生。
        self.stats = {"token_hits": 0, "token_misses": 0, "preview_hits": 0, "preview_misses": 0}

    # ------------------------------------------------------------------
    # 候选数据
    # ------------------------------------------------------------------
    def metric_candidates(self) -> list[dict[str, Any]]:
        return [self._metric_brief(row) for row in store.rows(METRIC_MODULE)]

    def dimension_candidates(self) -> list[dict[str, Any]]:
        return [self._dimension_brief(row) for row in store.rows(DIMENSION_MODULE)]

    @staticmethod
    def _metric_brief(row: dict[str, Any]) -> dict[str, Any]:
        status = str(row.get("status") or "")
        usable = status == USABLE_STATUS
        return {
            "id": row.get("id"),
            "code": str(row.get("指标编号") or ""),
            "name": str(row.get("指标名称") or ""),
            "version": str(row.get("口径版本") or ""),
            "status": status,
            "formula": str(row.get("计算公式") or ""),
            "usable": usable,
            "reason": "" if usable else f"指标当前状态为「{status}」，仅已生效指标可被聚合规则引用",
        }

    @staticmethod
    def _dimension_brief(row: dict[str, Any]) -> dict[str, Any]:
        status = str(row.get("status") or "")
        usable = status == USABLE_STATUS
        return {
            "id": row.get("id"),
            "code": str(row.get("维度编号") or ""),
            "name": str(row.get("维度名称") or ""),
            "keyColumn": str(row.get("维度键列") or ""),
            "sourceTable": str(row.get("数据来源表") or ""),
            "status": status,
            "usable": usable,
            "reason": "" if usable else f"维度当前状态为「{status}」，仅已生效维度可被聚合规则引用",
        }

    # ------------------------------------------------------------------
    # 单条引用解析（带修订号失效的 token 缓存）
    # ------------------------------------------------------------------
    def resolve_metric(self, token: str) -> ResolvedRef:
        return self._resolve_token("metric", token, METRIC_MODULE, "指标编号", "指标名称", self._metric_brief)

    def resolve_dimension(self, token: str) -> ResolvedRef:
        return self._resolve_token("dimension", token, DIMENSION_MODULE, "维度编号", "维度名称", self._dimension_brief)

    def _resolve_token(
        self,
        kind: str,
        token: str,
        module: str,
        code_field: str,
        name_field: str,
        brief_builder,
    ) -> ResolvedRef:
        revision = store.revision(module)
        cache_key = (kind, token)
        with self._lock:
            cached = self._token_cache.get(cache_key)
            if cached is not None and cached[0] == revision:
                self.stats["token_hits"] += 1
                return cached[1]
            self.stats["token_misses"] += 1

            rows = store.rows(module)
            label = "指标" if kind == "metric" else "维度"
            # 第一步：编号精确匹配。编号是唯一键，命中即定，不受同名记录干扰。
            code_matches = [row for row in rows if str(row.get(code_field) or "").strip() == token]
            if len(code_matches) == 1:
                resolved = self._build_ref(token, kind, code_matches[0], brief_builder, label)
            elif len(code_matches) > 1:
                # 数据异常情况下编号重复：同样不猜，按歧义处理。
                candidates = [brief_builder(row) for row in code_matches]
                resolved = ResolvedRef(
                    token=token, kind=kind, status="ambiguous", usable=False,
                    reason=f"{label}编号「{token}」命中 {len(candidates)} 条记录，需人工确认",
                    candidates=candidates,
                )
            else:
                # 第二步：名称匹配。同名不同编号 -> 歧义，绝不自行选中。
                name_matches = [row for row in rows if str(row.get(name_field) or "").strip() == token]
                if len(name_matches) > 1:
                    candidates = [brief_builder(row) for row in name_matches]
                    codes = "、".join(item["code"] for item in candidates)
                    resolved = ResolvedRef(
                        token=token, kind=kind, status="ambiguous", usable=False,
                        reason=f"名称「{token}」对应 {len(candidates)} 个不同{label}编号（{codes}），请改用编号明确引用",
                        candidates=candidates,
                    )
                elif len(name_matches) == 1:
                    resolved = self._build_ref(token, kind, name_matches[0], brief_builder, label)
                else:
                    resolved = ResolvedRef(
                        token=token, kind=kind, status="not_found", usable=False,
                        reason=f"未找到编号或名称为「{token}」的{label}定义，引用已失效",
                    )

            self._put_token(cache_key, revision, resolved)
            return resolved

    def _build_ref(self, token: str, kind: str, row: dict[str, Any], brief_builder, label: str) -> ResolvedRef:
        brief = brief_builder(row)
        if brief["usable"]:
            return ResolvedRef(
                token=token, kind=kind, status="resolved", usable=True,
                reason="", candidates=[brief], selected=brief,
            )
        return ResolvedRef(
            token=token, kind=kind, status="unusable", usable=False,
            reason=brief["reason"], candidates=[brief], selected=brief,
        )

    def _put_token(self, key: tuple[str, str], revision: int, ref: ResolvedRef) -> None:
        self._token_cache[key] = (revision, ref)
        if len(self._token_cache) > _TOKEN_CACHE_LIMIT:
            # 简单淘汰最旧的一批；正确性由修订号保证，与淘汰无关。
            for old_key in list(self._token_cache)[:_TOKEN_CACHE_LIMIT // 4]:
                self._token_cache.pop(old_key, None)

    # ------------------------------------------------------------------
    # 整份聚合配置校验（带快照指纹的预览缓存）
    # ------------------------------------------------------------------
    def preview_aggregation(self, values: dict[str, Any], *, use_cache: bool = True) -> dict[str, Any]:
        fingerprint = config_fingerprint(values)
        revisions = store.revisions(SNAPSHOT_MODULES)
        revision_tuple = tuple(revisions[name] for name in SNAPSHOT_MODULES)

        with self._lock:
            cached = self._preview_cache.get(fingerprint)
            if use_cache and cached is not None and cached[0] == revision_tuple:
                self.stats["preview_hits"] += 1
                # 命中复用的仍是同一快照下的结论，补一份快照信息回传。
                result = dict(cached[1])
                result["cache"] = {"preview": "hit", "fingerprint": fingerprint}
                return result
            self.stats["preview_misses"] += 1

        # 解析在锁外执行：token 缓存内部自带锁，且单次预览内读到的修订号已固定，
        # 保证同一次预览采用一致的依赖快照。
        metric_tokens = split_refs(values.get("目标指标"))
        dimension_tokens = split_refs(values.get("分组维度"))
        metric_refs = [self.resolve_metric(token).to_dict() for token in metric_tokens]
        dimension_refs = [self.resolve_dimension(token).to_dict() for token in dimension_tokens]

        errors: list[str] = []
        for ref in metric_refs + dimension_refs:
            if not ref["usable"]:
                errors.append(f"[{ '指标' if ref['kind'] == 'metric' else '维度' }] {ref['token']}：{ref['reason']}")

        snapshot = DependencySnapshot(
            revisions=revisions,
            fingerprint=fingerprint,
            metrics=[ref["selected"] for ref in metric_refs if ref["selected"]],
            dimensions=[ref["selected"] for ref in dimension_refs if ref["selected"]],
        )
        result = {
            "ok": not errors,
            "fingerprint": fingerprint,
            "snapshot": snapshot.to_dict(),
            "metricRefs": metric_refs,
            "dimensionRefs": dimension_refs,
            "errors": errors,
            "cache": {"preview": "miss", "fingerprint": fingerprint},
        }
        with self._lock:
            self._preview_cache[fingerprint] = (revision_tuple, result)
            if len(self._preview_cache) > _PREVIEW_CACHE_LIMIT:
                for old_key in list(self._preview_cache)[:_PREVIEW_CACHE_LIMIT // 4]:
                    self._preview_cache.pop(old_key, None)
        return result

    # ------------------------------------------------------------------
    # 聚合规则记录解析：报告模块沿同一条数据流复用，判断口径必须一致
    # ------------------------------------------------------------------
    def resolve_aggregation_rule(self, token: str) -> ResolvedRef:
        """把报告里引用聚合规则的文字（规则编号或规则名称）解析成具体规则。"""
        revision = store.revision(AGGREGATION_MODULE)
        cache_key = ("aggregation", token)
        with self._lock:
            cached = self._token_cache.get(cache_key)
            if cached is not None and cached[0] == revision:
                self.stats["token_hits"] += 1
                return cached[1]
            self.stats["token_misses"] += 1

            rows = store.rows(AGGREGATION_MODULE)
            # 聚合规则没有独立名称字段，统一按规则编号精确匹配；
            # 编号命不中即视为引用失效，不做模糊猜测。
            code_matches = [row for row in rows if str(row.get("规则编号") or "").strip() == token]
            if len(code_matches) == 1:
                ref = self._rule_ref(token, code_matches[0])
            elif len(code_matches) > 1:
                candidates = [self._rule_brief(row) for row in code_matches]
                ref = ResolvedRef(token, "aggregation", "ambiguous", False,
                                  f"规则编号「{token}」命中 {len(candidates)} 条记录，需人工确认", candidates)
            else:
                ref = ResolvedRef(token, "aggregation", "not_found", False,
                                  f"未找到规则编号为「{token}」的聚合规则，报告引用已失效")
            self._put_token(cache_key, revision, ref)
            return ref

    def _rule_ref(self, token: str, row: dict[str, Any]) -> ResolvedRef:
        brief = self._rule_brief(row)
        # 规则本身存在与否先确定；其依赖是否有效由 preview_aggregation 给结论。
        return ResolvedRef(token, "aggregation", "resolved", True, "", [brief], brief)

    @staticmethod
    def _rule_brief(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row.get("id"),
            "code": str(row.get("规则编号") or ""),
            "metricText": str(row.get("目标指标") or ""),
            "dimensionText": str(row.get("分组维度") or ""),
            "function": str(row.get("聚合函数") or ""),
            "status": str(row.get("status") or ""),
        }

    def preview_report_rules(self, rule_text: Any) -> dict[str, Any]:
        """报告选取聚合规则后的依赖解释：逐规则解析并复用同一套快照判断。

        报告不能绕过无效依赖：任一规则缺失/歧义，或规则内部的指标、维度引用不可用，
        整体 ok=False，并给出阻断原因。
        """
        tokens = split_refs(rule_text)
        rule_refs: list[dict[str, Any]] = []
        errors: list[str] = []
        adopted: list[dict[str, Any]] = []
        for token in tokens:
            rule_ref = self.resolve_aggregation_rule(token)
            if not rule_ref.usable or rule_ref.selected is None:
                rule_refs.append(rule_ref.to_dict())
                errors.append(f"[聚合规则] {token}：{rule_ref.reason}")
                continue
            rule_row = rule_ref.selected
            # 与聚合查询预览走完全相同的解析入口，保证同一快照判断一致。
            preview = self.preview_aggregation(
                {
                    "规则编号": rule_row["code"],
                    "目标指标": rule_row["metricText"],
                    "分组维度": rule_row["dimensionText"],
                    "聚合函数": rule_row["function"],
                },
            )
            rule_refs.append({**rule_ref.to_dict(), "preview": preview})
            if not preview["ok"]:
                errors.extend(f"[规则 {rule_row['code']}] {msg}" for msg in preview["errors"])
            else:
                adopted.append({
                    "ruleCode": rule_row["code"],
                    "snapshot": preview["snapshot"],
                })
        revisions = store.revisions(SNAPSHOT_MODULES)
        return {
            "ok": not errors,
            "ruleTokens": tokens,
            "ruleRefs": rule_refs,
            "errors": errors,
            "adopted": adopted,
            "snapshotRevisions": revisions,
        }

    # ------------------------------------------------------------------
    # 重复口径识别：同样的指标+维度+聚合函数才算同一口径
    # ------------------------------------------------------------------
    def duplicate_groups(self) -> list[dict[str, Any]]:
        """按「目标指标 token 集合 + 分组维度 token 集合 + 聚合函数」归组。

        文字不同但解析后指向同一批指标/维度的规则会被识别为同口径重复；
        解析失败的引用不参与归组，避免把失效引用误判成重复。
        """
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in store.rows(AGGREGATION_MODULE):
            metric_tokens = split_refs(row.get("目标指标"))
            dimension_tokens = split_refs(row.get("分组维度"))
            metric_ids: list[Any] = []
            dimension_ids: list[Any] = []
            resolved_ok = True
            for token in metric_tokens:
                ref = self.resolve_metric(token)
                if ref.usable and ref.selected:
                    metric_ids.append(ref.selected["id"])
                else:
                    resolved_ok = False
            for token in dimension_tokens:
                ref = self.resolve_dimension(token)
                if ref.usable and ref.selected:
                    dimension_ids.append(ref.selected["id"])
                else:
                    resolved_ok = False
            if not resolved_ok or not metric_ids:
                continue
            key = "|".join(
                [str(row.get("聚合函数") or "").strip(),
                 ",".join(map(str, sorted(metric_ids))),
                 ",".join(map(str, sorted(dimension_ids)))]
            )
            groups.setdefault(key, []).append({
                "id": row.get("id"),
                "code": str(row.get("规则编号") or ""),
                "status": str(row.get("status") or ""),
            })
        return [
            {"key": key, "count": len(rows_), "rules": rows_}
            for key, rows_ in groups.items() if len(rows_) > 1
        ]

    def reset_stats(self) -> None:
        with self._lock:
            self.stats = {"token_hits": 0, "token_misses": 0, "preview_hits": 0, "preview_misses": 0}


resolver = DependencyResolver()
