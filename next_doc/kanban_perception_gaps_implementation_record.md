# 看板感知层面改进方案 — 实现记录

对应设计文档：`next_doc/kanban_perception_gaps_improvement_plan.md`。

本记录只跟踪"设计文档里的哪些方向已经落地、具体改了哪些文件、跟设计
文档相比有哪些取舍"，不重复设计文档本身的动机分析。

---

## 第一期（S1 + S2 + S3 + S4）— 已完成

按设计文档"优先级与分期建议"表的第一期建议实现，额外把 S4（方向 C，
仲裁状态聚合统计）提前并入本期——因为 S3（哨兵面板）的 `arbitration_
recent_ratio` 字段本身就依赖 S4 的计算函数，两者天然是同一批改动。

### S4／方向 C：仲裁状态聚合统计

- 新增 `evolution/resource_arbiter.py::gating_ratio_summary(paths,
  window_days=7.0, limit=200)`：基于 `read_gating_history()` 的逐条状态
  变化记录重建 `full`/`degraded`/`blocked` 三态在窗口期内的累计时长占比。
  纯计算，不新增任何落盘文件。
- `GET /v1/autonomous/gating_history` 响应新增 `ratio_summary` 字段（不
  新增端点，复用同一次请求）。
- **裁剪边界的处理**：设计文档提到 `_GATING_HISTORY_MAX_ENTRIES=200` 的
  裁剪上限可能导致窗口期内数据缺失。实现里的判定条件是"记录条数达到
  `min(limit, 200)` 上限，且最早一条记录本身仍晚于窗口起点"——满足时
  `incomplete=True`，前端据此展示"数据不完整"提示而不是静默给出不准确
  的比例。窗口比实际历史更长（记录总数没被裁剪，只是天然不够多）的
  情况**不**判定为 incomplete，这是设计文档没有明确展开的一个边界，
  实现时按"只有被裁剪掉的缺失才算 incomplete"的原则处理。
- 看板"🗓️ 全局日程"Tab 的仲裁时间线上方新增一行占比摘要。

### S1／方向 B.1：LLM 故障转移状态暴露

- 新增 `perception/sentinel.py::read_llm_pool_snapshot(client_pool)`：
  包一层 `LLMClientPool.snapshot()`，补充 `switched_from_preferred`
  （`current != 0` 的简化标记）。
- 新增只读端点 `GET /v1/self/llm_pool_status`，对齐 `api/routes.py` 里
  已有的两处 `_client_pool` 取法（约 388/473 行）。
- 看板"🧠 自我状态"Tab 新增"🔀 LLM 故障转移状态"区块。
- 未做（按设计文档 B.3 的优先级排序，本期只做 B.1）：B.2 轻量调用计数
  （`llm/call_stats.py`、每日降采样、调用量折线图）——需要新的落盘格式
  和降采样治理，设计文档本身建议"排在 S1 验证完有没有人真的关心这个
  数据之后再做"。

### S2／方向 E：wiki 隔离区暴露

- 新增 `perception/sentinel.py::_scan_quarantine_backlog(paths)`：调用
  既有的 `wiki/quarantine.py::load_quarantine()`，返回积压条数（排除
  `STATUS_REPAIRED`）+ 最早一条的 `first_seen_at` + 前 20 条明细。
- 新增只读端点 `GET /v1/wiki/quarantine_status`。
- 未新增任何看板端修复交互——明细仍然通过 `cli/commands/quarantine.py`
  处理，符合设计文档"看板这一步只负责让用户知道有积压"的定位。

### S3／方向 A：哨兵聚合面板

- 新增 `perception/sentinel.py::sentinel_summary(paths, client_pool=,
  cron_failure_threshold=2)`，聚合五类信号：
  1. `_scan_cron_consecutive_failures`：遍历 `.agent/cron_jobs/*/
     state.json`，`consecutive_failures >= threshold`（默认 2）的
     job，附带从 `.agent/cron_jobs.json` 读到的 name/enabled；
  2. `_scan_objective_retry_hotspots`：遍历 `.agent/
     objective_executions.json` 里 `status=="running"` 的执行，筛出
     有 step `retry_count >= MAX_STEP_RETRIES - 1` 的（快要判定失败前
     的最后一次机会）；
  3. `_scan_quarantine_backlog`（同 S2）；
  4. `read_llm_pool_snapshot`（同 S1）；
  5. `gating_ratio_summary`（同 S4，窗口固定 7 天）。
- 新增只读端点 `GET /v1/sentinel/summary?cron_failure_threshold=`。
- 看板顶栏新增"⚠️ 系统状态哨兵"可折叠区块，跟既有"📥 全局待办中心"
  并列、语义不合并（详见方向 A.0）。五类信号总数非空时区块默认展开；
  全部为空时区块本身不渲染。cron 条目提供"跳转"按钮，复用既有的
  `cron_focus_job_id` + `_pending_tab_switch` 机制定位到"⏰ Cron 任务"
  Tab（跟顶栏"⚙️ daemon 正在执行 N 项任务"已经实现的跳转是同一套
  基础设施，没有另起一套）。
- **实现细节，设计文档没有展开的部分**：cron job 的目录名是
  `job_id.replace(":", "_")` 后的结果（`CronJobWorkspace.__init__`），
  从磁盘扫描 `.agent/cron_jobs/` 目录时反查原始 `job_id` 需要跟
  `cron_jobs.json` 里的记录做一次名称映射；找不到映射时退化为直接用
  目录名当 `job_id` 展示（自定义 job 没有走标准注册流程时的兜底）。
- **未做**：设计文档 A.4 提到的"cron job 数量变得很大（几十上百个）
  时需要重新评估是否要加缓存"——当前实现是每次请求都重新遍历目录，
  跟设计文档"量级不大，可以接受"的判断一致，暂不加缓存。

### 未实现（按设计文档优先级，留给后续分期）

- B.2 轻量调用计数（见上）
- 方向 D（Goal/Objective 完成率趋势、通用"每日快照"小工具抽取）

---

## 涉及文件清单

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `src/mini_agent/perception/sentinel.py` | 新增 | 五类扫描函数 + `sentinel_summary()` |
| `src/mini_agent/evolution/resource_arbiter.py` | 修改 | 新增 `gating_ratio_summary()` |
| `src/mini_agent/api/routes.py` | 修改 | 新增 3 个端点 + `gating_history` 响应扩展字段 + 路由列表注释更新 |
| `apps/mini_agent_kanban/client.py` | 修改 | 新增 `llm_pool_status()` / `wiki_quarantine_status()` / `sentinel_summary()` |
| `apps/mini_agent_kanban/app.py` | 修改 | 新增 `_render_llm_pool_status()` / `_render_sentinel_panel()`，全局日程 Tab 展示 ratio_summary |
| `tests/test_sentinel_panel.py` | 新增 | 18 个测试用例，覆盖全部新增函数 |
| `docs/kanban-dashboard-guide.md` | 修改 | 顶栏哨兵面板 / 自我状态 Tab LLM 区块 / 全局日程 Tab 占比摘要 / API 端点表 |
| `docs/llm-failover-guide.md` | 修改 | 新增"daemon 模式下的可观测性"一节 |

## 测试情况

`tests/test_sentinel_panel.py`：18 个用例全部通过（`PYTHONPATH=src
python3 -m pytest tests/test_sentinel_panel.py -q`）。回归验证了
`test_gating_history_active_recording.py`、
`test_resource_arbiter_gating_track_j.py`、
`test_resource_arbiter_behavior_gating.py`、`test_wiki_quarantine.py`、
`test_cron_job_runner_resource_arbiter.py` 共 52 个既有用例，全部通过，
无回归。`test_gating_history.py`（依赖 fastapi TestClient）因为当前
环境缺少可用的 `httpx2` 依赖无法收集，是既有环境缺口，跟本次改动无关
（其余测试文件也依赖同样的 fastapi 但不需要 TestClient 因而不受影响）。
