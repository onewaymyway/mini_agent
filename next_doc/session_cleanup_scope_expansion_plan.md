# 会话清理范围扩展改进计划（session_cleanup_scope_expansion_plan）

> 状态：已实施（阶段一、阶段二）。阶段三为后续项，未实施。

## 1. 背景与问题

看板「🧹 批量清理旧会话」与 CLI `/session cleanup` 共用 `evolution/session_cleanup.py`，
当前清理范围只有两类：

- `.agent/sessions/<id>/` 整目录（主 agent 与外部项目各自的 sessions 目录）
- 与之联动的 `.agent/raw_results/<id>/`

存在以下缺口：

1. **`workflow_sessions/<wid>/` 完全没有清理**。模块文档甚至写明"天然不受本模块影响"，
   这是当初设计时的排除，不是有意保留。每次工作流运行都会留下 `session.json`、
   `events.jsonl`、`watchdog.jsonl`、各 step 子 Agent 会话目录与 `output/`。
   主项目和外部项目（如 `external_projects/<name>/.agent/workflow_sessions`）都受影响。
2. **产出物会被连带删除（优先级最高）**。`session.py::delete()` 对整个会话目录执行
   `rmtree`；`output_registry/inventory.py` 把 `sessions/<sid>/output` 与
   `workflow_sessions/<wid>/output` 列为用户产出位置，但清理逻辑中没有任何产出保护。
   dry-run 预览只显示会话条目，用户无法察觉其中含有交付物。
3. **孤立的 raw_results 永远残留**。raw_results 只在对应会话被删时才跟着删，
   会话目录早已不存在的 `raw_results/<id>/` 没有任何清理路径。
   `perception/raw_result_cleanup.py` 写好了巡检，但从未被注册调度，也没有入口。
4. **workflow 子 Agent 的 raw_results 归属**。工作流 step 的子 Agent 会话位于
   `workflow_sessions/<wid>/<agent_sid>/`，其 raw_results 同样以 agent_sid 为目录名，
   需要在删除 workflow 运行时一并清理。

## 2. 目标

- 清理范围覆盖 `workflow_sessions`（主项目与外部项目统一处理）。
- 任何含产出文件（`output/` 非空）的会话或工作流运行，默认不删除，并在预览中明示。
- 清理孤立的 raw_results 目录，判定时把工作流子 Agent 的 id 视为"仍被引用"。
- 保持向后兼容：旧字段不变，新增字段均有默认值；看板/CLI 的确认式流程不变。

## 3. 设计

### 3.1 产出保护（阶段一）

- 判定：会话目录 `sessions/<id>/output/` 或运行目录 `workflow_sessions/<wid>/output/`
  下存在至少一个文件 → 保留，原因写明"含产出文件 N 个，受保护"。
- 始终开启（`protect_outputs` 只在内部函数参数上存在，不暴露给 HTTP 请求体，
  避免看板误操作关闭保护）。
- 产出保护的会话仍计入 `kept`，并统计到 `output_protected_count`。

### 3.2 workflow_sessions 清理（阶段一）

新增 `evolution/workflow_run_cleanup.py`，判定规则（自上而下，命中即保留）：

1. 目录无 `session.json`：目录年龄 < `orphan_min_age_hours` 保留（可能正在启动）；
   超过安全窗口且无产出 → 删除（孤儿运行目录）；有产出 → 保留。
2. 状态非终结（`running` / `paused` / `awaiting_approval` / 未知值）→ 保留。
   注意：daemon 崩溃遗留的假 `running` 不自动删除，需在工作流页面
   `mark_interrupted` 后再进入清理范围。
3. 含产出文件 → 保留。
4. 按 `updated_at` 倒序，最近 `keep_recent_count` 个保留。
5. `updated_at` 在 `keep_recent_days` 内保留。
6. 其余（终结状态：`done` / `failed` / `partial` / `cancelled`）→ 删除。

删除时：先过受保护清单检查，再 `rmtree`；同时遍历运行目录下的所有子目录名，
调用 `_clear_raw_results` 清理对应的 raw_results（即 step 子 Agent 的原文）。

不需要知识抽取（工作流不是对话 session）。

### 3.3 孤立 raw_results 巡检（阶段二）

- 已知 id 集合 = `sessions/` 下所有目录名 ∪ `workflow_sessions/` 下所有子目录名（递归）。
- `raw_results/<name>` 不在已知集合中、且目录最后活动时间早于 `orphan_min_age_hours`
  → 删除，并受受保护清单检查。
- 仅在 `include_orphans=True` 时执行（与孤儿会话目录同一开关，不新增 UI 选项）。

### 3.4 对外接口与看板

- `CleanupReport` 新增：`workflow_kept` / `workflow_deleted` / `workflow_failed`、
  `raw_orphan_deleted`、`output_protected_count`。原有字段不变。
- HTTP 响应 `SessionCleanupProjectResult` 新增对应字段（均带默认值），旧调用方可忽略。
- 外部项目范围自动生效：外部项目的 `.agent/workflow_sessions` 通过 `AgentPaths(root)` 解析。
- 看板清理面板新增「工作流运行」「孤立 raw_results」两块结果表，
  确认执行的可勾选条件同时考虑这两类。

## 4. 不在本次范围（阶段三，待确认后立项）

| 项目 | 原因 |
|---|---|
| `.agent/adhoc/<key>/temp` 与 `output` 清理 | 涉及 temp 与产出边界的判定，需先确认 temp 是否可能被后台任务引用 |
| `daemon_run_outputs/{goals,cron}` | 与 goal 产出目录的所有权关系需要先梳理 |
| `cache/`、`debug/`、`external_input/`、`events_archive/`、`daily_reports/` | 各自有独立用途，需逐个确认保留语义 |
| JSONL 增长文件（events、llm_call_stats、growth_*、timeline 等） | 应走 `archive/gc.py` 的归档机制而非删除，需扩展 archive target 清单 |
| `~/.agent/logs/*` 轮转 | 全局日志，影响面大，单独立项 |
| 会话清理定时化 | 删除为不可撤销操作，定时执行前需先有可靠的预览与告警 |
| `raw_result_cleanup.py` 巡检注册调度 | 阶段二已用更精确的孤立判定替代其"按 mtime 删整目录"的粗粒度规则，原巡检保持未注册 |

## 5. 验收

- `tests/test_session_cleanup_scope_expansion.py` 覆盖：产出保护、工作流终结/非终结/
  产出/保留窗口、无 session.json 孤儿运行、子 Agent raw_results 联动、孤立 raw_results、
  dry-run 不删除、外部项目根路径、受保护清单。
- 既有 `tests/test_session_cleanup_external_projects.py` 与 `tests/test_cleanup_external_goal_candidates.py` 不回归。
- 文档同步：`docs/external-projects-guide.md`（清理范围说明）、`docs/kanban-dashboard-guide.md`（清理面板）。

## 6. 行为变化（上线前必读）

- **产出保护生效后，旧的"按时间删会话"会保留更多会话**：凡含 `output/` 产出的会话不再删除。
  这是预期的安全修复，但磁盘占用会比以前高。
- **定时任务 `sys:session_cleanup`（每 7 天，`/session cleanup --extract-first`）会同时清理
  工作流运行目录**，因为它走的是同一套默认参数（`include_workflow_runs=True`）。
  如果不希望无人值守删除工作流记录，可以在 `cron_scheduler.py` 的该任务模板中去掉依赖，
  或把 `include_workflow_runs` 改为由 CLI 参数控制（当前未提供）。
- **孤立 raw_results 清理只在 `include_orphans=True` 时执行**，定时任务默认不带该参数，
  因此定时任务不会清理孤立 raw_results；手动预览勾选"含孤儿目录"时才会出现。
- 删除工作流运行是不可撤销操作，看板仍遵循"预览 → 勾选确认 → 执行"的两段式流程。

## 7. 已验证

- 新增 `tests/test_session_cleanup_scope_expansion.py`（11 个用例）全部通过。
- 既有 `tests/test_session_cleanup_external_projects.py`、`tests/test_cleanup_external_goal_candidates.py`
  全部通过（含 HTTP 路由测试）。
- 其余 10 个失败用例（browser_core_session_manager、session_to_workflow、workflow_p11、workflow_p15）
  在原始代码上同样失败（环境缺少 json_repair 等依赖，与本次改动无关），未被本次改动引入。
