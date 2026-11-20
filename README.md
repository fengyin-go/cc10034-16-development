# 实时数仓CDC变更捕获中心

面向实时数仓建设场景，提供Binlog解析、变更事件捕获、DDL追踪、SCD处理和下游分发同步的CDC治理平台

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/views/AuditReviewView.vue    审计复核台（脱敏样本/补证回放）
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py      通用查询/导出/登记/动作接口
│   ├── app/routers/audit_review.py  审计复核样本接口
│   ├── app/services/<key>.py        每个模块独立领域逻辑
│   ├── app/services/audit_review.py 事务镜像脱敏、字段确认与整笔证明
│   ├── app/services/base.py         共享服务骨架
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
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 审计复核样本（字段级变更证据）

审计轨迹原本只有操作摘要，无法给出可分享的字段级变更证据。复核台把 `change_event`
中同一事务的变更前后镜像，按查阅权限转换成脱敏复核样本集：

- `POST /api/audit-review/transactions/{tx_id}/samples?account_id=...`：生成样本，越权字段只计数量、不返回值。
- `GET  /api/audit-review/samples/{sample_id}?account_id=...`：回查旧样本，按当前授权重新脱敏。
- `POST /api/audit-review/samples/{sample_id}/confirmations`：基于样本生成按字段划分的确认记录。
- `POST /api/audit-review/transactions/{tx_id}/replay?account_id=...`：补证回放，挑出仍需补证的成员。
- `POST /api/audit-review/transactions/{tx_id}/attest?account_id=...`：整笔证明，成员或字段未全部复核时拒绝。
- `POST /api/audit-review/accounts/{account_id}/revoke|grants`：收回/授予字段查阅权限。

示例账号 `order_status_reader` 对 `sales.orders` 只读且仅能看 `status`；
样例事务 `tx-2026-11-20-0001` 同时改动了订单金额和归属其他团队的客户信用等级、手机号。
收回授权后旧样本会重新脱敏，历史确认也不再计入已确认范围。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| Binlog解析 | `binlog_parser` | 解析配置 | 配置名称、源数据库、解析模式 |
| 变更事件 | `change_event` | 变更记录 | 事件ID、来源表、操作类型 |
| DDL追踪 | `ddl_tracker` | DDL记录 | 记录ID、变更数据库、变更表名 |
| Schema同步 | `schema_sync` | 同步任务 | 任务名称、源表、目标表 |
| 采集通道 | `cdc_channel` | 通道实例 | 通道名称、Binlog源、输出目标 |
| 检查点管理 | `checkpoint_manager` | 检查点 | 检查点ID、所属通道、Binlog文件 |
| 位点存储 | `position_store` | 位点记录 | 记录ID、数据库实例、Binlog名称 |
| 格式适配 | `format_adapter` | 适配规则 | 规则名称、输入格式、输出格式 |
| 缓慢变化维 | `scd_handler` | SCD配置 | 配置名称、维度表、SCD类型 |
| 去重窗口 | `dedup_window` | 窗口配置 | 配置名称、去重键、窗口大小 |
| 路由分发 | `route_dispatcher` | 路由规则 | 规则名称、匹配条件、目标通道 |
| 回填位点 | `backfill_position` | 回填任务 | 任务名称、源位点、目标位点 |
| 延迟监控 | `lag_monitor` | 延迟指标 | 指标时间、所属通道、延迟秒数 |
| 快照比对 | `snapshot_compare` | 比对报告 | 报告名称、源表名、目标表名 |
| 事件过滤 | `event_filter` | 过滤规则 | 规则名称、过滤类型、匹配表达式 |
| 分区同步 | `partition_sync` | 同步配置 | 配置名称、源分区策略、目标分区策略 |
| 审计轨迹 | `audit_trail` | 审计日志 | 审计时间、操作类型、目标对象 |
| 故障切换 | `failover_group` | 切换组 | 组名、主通道、备用通道 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数仓与数据湖
