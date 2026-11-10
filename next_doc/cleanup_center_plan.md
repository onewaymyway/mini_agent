# 清理中心改进计划（cleanup_center_plan）

> 状态：已实施。承接 `session_cleanup_scope_expansion_plan.md` 第 4 节"未实施"中的非会话类数据。

## 1. 目标

会话清理（`/session cleanup`）只覆盖会话与工作流运行。其余非会话类数据（缓存、调试输出、
adhoc 临时目录、每日报告、事件归档、daemon 运行产出、全局轮转日志等）目前没有任何清理入口。

新增一个独立的「🧽 清理中心」页面：

- 每个数据类型一个清理类别，各自有独立的判定规则与保留天数；
- 页面上勾选要清理的类别，设置各自的年龄阈值，先预览、再确认执行；
- 与会话清理共用"预览 → 勾选确认 → 执行"的两段式流程，不做一键全删。

## 2. 类别清单

| key | 数据位置 | 判定 | 默认勾选 | 默认天数 | 风险 |
|---|---|---|---|---|---|
| `adhoc_temp` | `.agent/adhoc/<key>/temp/` | 条目最后活动早于 N 天 | ✅ | 7 | 低 |
| `raw_orphan` | `.agent/raw_results/<name>/` | 无会话/工作流目录引用且早于 N 天 | ✅ | 1 | 低 |
| `cache` | `.agent/cache/` | 顶层条目早于 N 天 | ✅ | 30 | 低（可重建） |
| `debug` | `.agent/debug/` | 顶层条目早于 N 天 | ✅ | 14 | 低 |
| `global_rotated_logs` | `~/.agent/logs/` | 仅已轮转文件（`.N` / `.gz` / `.jsonl.N`）早于 N 天；活动日志只报告大小 | ✅ | 30 | 低 |
| `daily_reports` | `.agent/daily_reports/` | 顶层条目早于 N 天 | ❌ | 90 | 中（报告可能仍被阅读） |
| `events_archive` | `.agent/events_archive/` | 顶层条目早于 N 天 | ❌ | 90 | 中（事件历史用于回溯） |
| `daemon_run_outputs` | `.agent/daemon_run_outputs/{goals,cron}/` | 条目早于 N 天 | ❌ | 30 | 高（属产出位置，可能被产出登记引用） |

明确不纳入（需要另行设计）：

- `.agent/external_input/`：外部输入网关的配置与私有状态，不是缓存，不能按时间删。
- `.agent/workflow_sessions/` 与 `.agent/sessions/`：已由会话清理负责。
- `.agent/wiki/`、`.agent/goals.json`、各类状态 JSON/JSONL：属于业务状态，删除会破坏功能，应走归档机制。

## 3. 安全约定

- 只有显式勾选的类别会参与执行；默认预览只做 dry-run。
- 所有路径由服务端 `AgentPaths` 解析，客户端只能传类别 key 与天数，不能传路径。
- 天数范围校验 0–3650；未知 key 直接 `400`。
- 删除前经过受保护文件清单检查（`protected_files_guard`），命中则跳过并标记失败。
- 高风险类别在看板上默认不勾选，并显示风险说明。
- owner 权限（与会话清理一致）。

## 4. 接口

- `GET  /v1/cleanup/categories`：类别清单（label、描述、默认勾选、默认天数、风险说明）。
- `POST /v1/cleanup/run`：请求体 `{keys, age_days, dry_run, project}`。
  `project` 为外部项目注册名（可选，缺省为主项目），路径仍由注册表解析。
  返回每个类别的条目列表、总大小、删除与失败数。

## 5. 看板

新增页签「🧽 清理中心」：清理范围下拉（主项目 / 外部项目）→ 类别勾选列表（每项含天数输入与
风险说明）→ 预览 → 结果按类别折叠展示 → 勾选确认后执行。

## 6. 验收

- `tests/test_cleanup_catalog.py`：各类别的年龄判定、活动日志保护、dry-run 不删、
  未知 key 拒绝、天数校验、受保护清单跳过、外部项目路径解析。
- 文档：`docs/kanban-dashboard-guide.md` 新增清理中心专节，`docs/http-api-guide.md` 新增接口说明。

## 7. 不在本次范围

- 定时执行（删除不可逆，先保留为手动入口）。
- `external_input`、业务状态类 JSON/JSONL 的清理（见第 2 节）。
- 增量归档 JSONL（`archive/gc.py` 的扩展，单独立项）。

## 8. 实施记录

- 后端：`evolution/cleanup_catalog.py`（类别定义、扫描、执行、参数校验）；
  路由 `GET /v1/cleanup/categories`、`POST /v1/cleanup/run`（`api/routes.py`），模型见 `api/models.py`。
- 看板：`apps/mini_agent_kanban/app.py` 新增页签「🧽 清理中心」（`render_cleanup_center_tab`），
  `client.py` 新增 `cleanup_categories()` / `cleanup_run()`。
- 测试：`tests/test_cleanup_catalog.py`（类别判定、dry-run、adhoc 与 output 隔离、全局日志保护、
  参数校验、路由与外部项目范围）。
- 与会话清理的关系：会话与工作流运行仍由会话清理负责；本页不重复处理。
- 设计取舍：参数校验前置到线程池之前，避免用户填错天数触发熔断。

