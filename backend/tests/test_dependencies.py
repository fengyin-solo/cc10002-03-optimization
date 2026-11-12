"""聚合依赖解析与缓存失效的验收测试。

覆盖需求里的关键口径：
1. 同名不同编号 -> 歧义，不自行猜中；
2. 失效维度/废弃指标 -> 明确不可用原因；
3. 连续修改配置快速预览 -> 最后一次配置的结果生效，旧成功结果不掩盖本次失败；
4. 频繁改筛选条件 -> 未变的依赖 token 走解析缓存；
5. 指标废弃、维度版本变化 -> 修订号推进，旧缓存立即失效；
6. 报告引用与聚合预览对同一快照判断一致，报告不能绕过无效依赖；
7. 并发预览只接受与当前配置版本（指纹）匹配的结果。

运行：python3 -m tests.test_dependencies
"""
from __future__ import annotations

import os
import sys
import threading

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 本地隔离依赖目录（生产/CI 里直接用已安装的环境即可，该目录不存在则跳过）。
_LOCAL_DEPS = os.path.join(os.path.dirname(_BACKEND_ROOT), ".pydeps")
if os.path.isdir(_LOCAL_DEPS):
    sys.path.insert(0, _LOCAL_DEPS)
sys.path.insert(0, _BACKEND_ROOT)

from app.services.dependencies import config_fingerprint, resolver  # noqa: E402
from app.services.metricdef import MetricdefService  # noqa: E402
from app.services.dimension import DimensionService  # noqa: E402
from app.services.aggregation import AggregationService  # noqa: E402
from app.services.report import ReportService  # noqa: E402
from app.store import store  # noqa: E402

metric_service = MetricdefService()
dimension_service = DimensionService()
aggregation_service = AggregationService()
report_service = ReportService()

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" -> {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def base_config(**overrides) -> dict:
    values = {
        "规则编号": "AGGR-TEST",
        "聚合函数": "SUM",
        "目标指标": "METR-0001",
        "分组维度": "DIME-0001",
        "时间范围": "2026-10-01~2026-10-31",
        "筛选条件": "province_code='310000'",
        "采样比例": "1.0",
    }
    values.update(overrides)
    return values


# ---------------------------------------------------------------- 1. 歧义不猜
resolver.reset_stats()
amb = resolver.resolve_metric("成交金额")
check("同名不同编号判为歧义", amb.status == "ambiguous" and amb.usable is False)
check("歧义列出全部候选且不选中", amb.selected is None and len(amb.candidates) == 2)
check("歧义原因包含两个编号", "METR-0001" in amb.reason and "METR-0002" in amb.reason, amb.reason)

# 用编号则能精确定位，不受同名干扰
by_code = resolver.resolve_metric("METR-0002")
check("编号精确命中同名记录之一", by_code.usable and by_code.selected["version"] == "v5")

# ---------------------------------------------------------------- 2. 失效原因
deprecated_metric = resolver.resolve_metric("METR-0004")
check("废弃指标不可用并给原因", deprecated_metric.status == "unusable" and "已废弃" in deprecated_metric.reason)

deprecated_dim = resolver.resolve_dimension("DIME-0003")
check("失效维度不可用并给原因", deprecated_dim.status == "unusable" and "已废弃" in deprecated_dim.reason)
check("失效维度仍回传维度键列", deprecated_dim.selected["keyColumn"] == "channel_code")

missing = resolver.resolve_dimension("DIME-9999")
check("不存在引用判 not_found", missing.status == "not_found" and missing.usable is False)

# 整体预览：失效依赖导致 ok=False 且错误不被吞
bad_preview = resolver.preview_aggregation(
    base_config(目标指标="METR-0004", 分组维度="DIME-0003"), use_cache=False
)
check("失效依赖预览失败", bad_preview["ok"] is False and len(bad_preview["errors"]) == 2)
check("失败结果带快照修订号", set(bad_preview["snapshot"]["revisions"]) == {"metricdef", "dimension", "aggregation"})

# ---------------------------------------------------------------- 3. token 缓存复用
resolver.reset_stats()
r1 = resolver.preview_aggregation(base_config(筛选条件="a=1"), use_cache=False)
r2 = resolver.preview_aggregation(base_config(筛选条件="a=2"), use_cache=False)
check("改筛选条件生成不同指纹", r1["fingerprint"] != r2["fingerprint"])
check("未变依赖命中 token 缓存", resolver.stats["token_hits"] >= 2, str(resolver.stats))
check("不同配置预览缓存各算一次", resolver.stats["preview_misses"] == 2)
# 完全相同配置再来一次 -> 预览缓存命中
r3 = resolver.preview_aggregation(base_config(筛选条件="a=2"))
check("相同配置命中预览缓存", r3["cache"]["preview"] == "hit" and resolver.stats["preview_hits"] == 1)

# ---------------------------------------------------------------- 4. 连续修改，最后一次生效
# 先成功（有效指标+有效维度），再改成失效配置，失败不能被旧成功缓存掩盖
ok_preview = resolver.preview_aggregation(base_config(目标指标="METR-0001", 分组维度="DIME-0001"))
check("有效配置预览成功", ok_preview["ok"] is True)
flip_preview = resolver.preview_aggregation(base_config(目标指标="METR-0001", 分组维度="DIME-0003"))
check("改成失效维度后本次预览失败", flip_preview["ok"] is False)
flip_back = resolver.preview_aggregation(base_config(目标指标="METR-0001", 分组维度="DIME-0001"))
check("改回有效配置后预览恢复成功（错误不污染下一次）", flip_back["ok"] is True)

# 快速连续切换：并发发出多个不同指纹的预览，结果必须各自匹配自己的指纹
results: dict[str, dict] = {}
threads = []
configs = {
    "A": base_config(分组维度="DIME-0001"),
    "B": base_config(分组维度="DIME-0003"),
    "C": base_config(分组维度="DIME-0002"),
}
def run(tag: str, cfg: dict) -> None:
    results[tag] = resolver.preview_aggregation(cfg)
for tag, cfg in configs.items():
    threads.append(threading.Thread(target=run, args=(tag, cfg)))
for t in threads:
    t.start()
for t in threads:
    t.join()
expected_fp = {tag: config_fingerprint(cfg) for tag, cfg in configs.items()}
check("并发预览结果与各自配置指纹匹配",
      all(results[tag]["fingerprint"] == expected_fp[tag] for tag in configs))
check("并发预览成败互不串结果",
      results["A"]["ok"] is True and results["B"]["ok"] is False and results["C"]["ok"] is True)

# ---------------------------------------------------------------- 5. 修订号驱动失效
metric_rev_before = store.revision("metricdef")
entry, message = metric_service.run_action(1, "废弃下线")
check("废弃动作推进指标修订号", store.revision("metricdef") == metric_rev_before + 1, message)
# 旧 token 缓存里 METR-0001 曾是可用的，修订号变化后必须重算为不可用
stale = resolver.resolve_metric("METR-0001")
check("指标废弃后旧缓存失效", stale.usable is False and stale.status == "unusable")
# 恢复生效，缓存再次跟随
metric_service.run_action(1, "生效发布")
recovered = resolver.resolve_metric("METR-0001")
check("指标恢复生效后解析恢复可用", recovered.usable is True)

# 维度字段更新（模拟维度版本变化）
dim_rev_before = store.revision("dimension")
_, msg = dimension_service.update_entry(1, {"维度键列": "province_code_v2"})
check("维度字段更新推进修订号", store.revision("dimension") == dim_rev_before + 1, msg)
dim_ref = resolver.resolve_dimension("DIME-0001")
check("维度缓存失效后读到新键列", dim_ref.selected["keyColumn"] == "province_code_v2")
# 无变化更新不推进
rev_hold = store.revision("dimension")
dimension_service.update_entry(1, {"维度键列": "province_code_v2"})
check("无实质变化不推进修订号", store.revision("dimension") == rev_hold)

# ---------------------------------------------------------------- 6. 规则动作受依赖守卫
# AGGR-0004 引用废弃指标+失效维度，启用必须被拦
blocked_entry, blocked_msg = aggregation_service.run_action(4, "启用规则")
check("无效依赖阻断规则启用", blocked_entry is None and "依赖校验未通过" in blocked_msg, blocked_msg)
# AGGR-0001 依赖有效，可以提交校验
ok_entry, ok_msg = aggregation_service.run_action(1, "提交校验")
check("有效依赖允许提交校验", ok_entry is not None and ok_entry["status"] == "待校验", ok_msg)

# ---------------------------------------------------------------- 7. 报告与预览同快照，不能绕过
# REPO-0002 引用 AGGR-0004（失效依赖），生效发布必须被拦
report_blocked, report_msg = report_service.run_action(2, "生效发布")
check("报告不能绕过无效依赖", report_blocked is None and "依赖校验未通过" in report_msg, report_msg)

# 报告依赖解释：REPO-0001 -> AGGR-0001 -> METR-0001/DIME-0001，版本可见
explain = resolver.preview_report_rules("AGGR-0001")
check("报告依赖解释成功", explain["ok"] is True)
adopted = explain["adopted"][0]
check("报告展示实际采用的快照版本",
      adopted["snapshot"]["metrics"][0]["version"] == "v3"
      and adopted["snapshot"]["dimensions"][0]["keyColumn"] == "province_code_v2")

# 同一规则直接走聚合预览，与报告解释的快照修订号一致
direct = resolver.preview_aggregation({
    "规则编号": "AGGR-0001",
    "目标指标": "METR-0001",
    "分组维度": "DIME-0001",
    "聚合函数": "SUM",
})
check("报告引用与聚合预览快照判断一致",
      direct["snapshot"]["revisions"] == explain["snapshotRevisions"]
      and direct["ok"] == explain["ok"])

# 报告引用不存在的规则
missing_rule = resolver.preview_report_rules("AGGR-4040")
check("报告引用缺失规则被判失效", missing_rule["ok"] is False and "未找到" in missing_rule["errors"][0])

# ---------------------------------------------------------------- 8. 同口径重复识别
groups = resolver.duplicate_groups()
# AGGR-0001(METR-0001,DIME-0001,SUM) 与 AGGR-0002(METR-0002,DIME-0001,SUM) 指标不同，不算重复
codes = sorted(code for g in groups for code in (r["code"] for r in g["rules"]))
check("不同指标口径不判重复", "AGGR-0001" not in codes and "AGGR-0002" not in codes, str(groups))
# 造一条与 AGGR-0003 同指标同维度同函数、仅文字写法不同的规则
aggregation_service.create_entry({
    "规则编号": "AGGR-0005", "聚合函数": "COUNT_DISTINCT",
    "目标指标": "METR-0003", "分组维度": "DIME-0002",
    "时间范围": "2026-11-01~2026-11-30", "筛选条件": "x=1",
    "采样比例": "0.5", "缓存秒数": "120",
})
groups2 = resolver.duplicate_groups()
dup_codes = sorted(code for g in groups2 for code in (r["code"] for r in g["rules"]))
check("解析后同口径识别为重复", "AGGR-0003" in dup_codes and "AGGR-0005" in dup_codes, str(dup_codes))
# 失效引用的规则不参与重复归组
all_dup = [code for g in groups2 for code in (r["code"] for r in g["rules"])]
check("失效引用规则不参与重复归组", "AGGR-0004" not in all_dup)

# ---------------------------------------------------------------- 9. 历史文字配置兼容
# 用逗号/顿号/空格混排的老式文字配置仍可解析
legacy = resolver.preview_aggregation(base_config(
    目标指标="METR-0001, METR-0003、METR-0002",
    分组维度="DIME-0001 DIME-0002",
))
check("兼容历史多引用文字分隔符", legacy["ok"] is True
      and len(legacy["metricRefs"]) == 3 and len(legacy["dimensionRefs"]) == 2)

print()
if failures:
    print(f"{len(failures)} 项失败：{failures}")
    raise SystemExit(1)
print("全部验收用例通过")
