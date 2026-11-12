# 数据指标分析平台

面向指标口径定义、多维度聚合分析、AB实验评估、数据服务发布与数据应用交付的数据指标分析平台。

这是一个前后端分离的管理平台：前端 React + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 React + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 指标定义 | `metricdef` | 指标口径 | 指标编号、指标名称、计算公式 |
| 维度管理 | `dimension` | 分析维度 | 维度编号、维度名称、数据来源表 |
| 数据源连接 | `datasource` | 数据源配置 | 配置编号、连接类型、接入地址 |
| 聚合查询 | `aggregation` | 聚合规则 | 规则编号、聚合函数、目标指标 |
| 分析看板 | `dashboard` | 看板配置 | 看板编号、看板名称、关联指标 |
| 分群分析 | `cohort` | 用户分群 | 分群编号、分群名称、筛选条件 |
| AB实验 | `abtest` | 实验方案 | 方案编号、实验名称、对照组配置 |
| 分析报告 | `report` | 报告模板 | 模板编号、模板名称、包含指标 |
| 指标预警 | `alert` | 预警规则 | 规则编号、关联指标、阈值上界 |
| 数据挖掘 | `datamine` | 挖掘任务 | 任务编号、输入数据集、算法类型 |
| 数据API | `api` | API服务 | 服务编号、输出数据集、查询参数定义 |
| ETL任务 | `etljob` | 加工任务 | 任务编号、输入源、输出目标 |
| 数据质量 | `quality` | 质量规则 | 规则编号、校验数据集、目标字段 |
| 数据建模 | `datamodel` | 模型定义 | 模型编号、模型名称、事实表名 |
| 数据导出 | `export` | 导出任务 | 任务编号、查询SQL、导出格式 |
| 数据订阅 | `subscribe` | 订阅任务 | 任务编号、数据源标识、订阅频率 |
| 数据血缘 | `lineage` | 血缘关系 | 关系编号、上游节点、下游节点 |
| 人群圈选 | `segment` | 圈选规则 | 规则编号、规则名称、筛选条件组合 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 聚合查询的跨模块依赖校验

聚合规则里的「目标指标」「分组维度」本质是对指标定义、维度管理的文字引用，平台在
`backend/app/services/dependencies.py` 里统一解析，聚合查询预览与分析报告引用走同一套判断：

- **候选选择**：`GET /api/aggregation/candidates` 返回指标/维度候选，带口径版本、维度键列、
  状态与不能使用的原因；前端聚合页可点选编号引用，同时保留文字自由编辑（兼容历史配置）。
- **歧义不猜**：编号精确匹配优先；按名称命中同名不同编号时一律判 `ambiguous` 并列出全部候选，
  不会自行选中。
- **校验预览**：`POST /api/aggregation/preview` 返回每条引用的解析结论、实际采用版本、
  依赖快照修订号与错误清单；失效/歧义依赖会阻断规则的提交校验与启用。
- **同口径重复**：`GET /api/aggregation/duplicates` 按解析后的真实指标、维度与聚合函数归组，
  失效引用不参与归组。
- **报告依赖解释**：`POST /api/report/dependency-preview` 展示报告所选聚合规则实际采用的
  指标版本/维度键列；报告提交审核、生效发布前强制校验，不能绕过无效依赖。

缓存与一致性策略（不靠延长 TTL 保证正确性）：

- 仓库为每个模块维护修订号（`Store.bump_revision`），指标废弃、维度字段/版本变化等写入
  立即推进修订号。
- 文字引用解析缓存按 `(类型, token)` 缓存并带依赖表修订号，频繁改筛选条件时未变依赖直接复用；
  修订号变化后旧条目即刻失效，错误结果不会污染下一次有效预览。
- 整份配置预览按「配置指纹 + 三模块快照修订号」缓存；单次预览内快照一致，配置一改指纹即变，
  旧成功结果无法掩盖本次失败。前端用单调递增的请求序号丢弃过期响应，并发预览只接受与当前
  配置版本匹配的结果。

后端验收测试（歧义、失效、连续修改、缓存失效、并发隔离、报告一致性、重复口径）：

```bash
cd backend
python3 tests/test_dependencies.py
```


领域：数据工程
子方向：数据分析与数据应用
