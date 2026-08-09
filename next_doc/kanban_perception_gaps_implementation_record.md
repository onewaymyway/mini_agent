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

- 方向 D（Goal/Objective 完成率趋势、通用"每日快照"小工具抽取）

---

## 第二期（B.2）— 已完成

### 方向 B.2：LLM 调用轻量计数

- 新增 `src/mini_agent/llm/call_stats.py`：默认开启（跟需要手动设置
  `LLM_DEBUG=1` 的 `llm/debug_logger.py` 完整调试日志是两套独立的东西），
  每次调用只记数字（provider/model/输入输出 token 数/耗时/结果分类），
  不含任何请求/响应正文。
- **挂载点**：`agent/llm_control.py::_call_llm()`（主对话循环）+
  `llm/service.py::LLMHelper.chat()`（judge/ensemble/目标拆解等场景），
  这是全系统仅有的两处 `LLMClientPool.call_with_pool()` 调用点，逐一
  确认过（`grep -rn "call_with_pool("`），覆盖率上不遗漏任何调用路径。
  同时利用既有的 `on_switch_key`/`on_switch_config` 回调顺带记录 key/
  配置切换事件，不需要额外的挂载点。
- **写入策略**：攒批而非逐条落盘（设计文档 B.3 风险 1 的应对）——内存
  缓冲区攒够 10 条或超过 30 秒未落盘才真正写文件，缓冲区按
  `project_root` 字符串隔离。三层失败兜底：`record_call()` 内部
  try/except（记 log_exception）→ 两处挂载点各自的 `_record_llm_call_
  stat`/`_record_call_stat` helper 再包一层裸 try/except（不记
  log_exception，避免调用计数本身的问题产生额外噪音）→ 两处调用点都
  放在 `call_with_pool()` 成功/失败之后，不在 LLM 调用的 try 块内部，
  确保任何一层出问题都不影响真正的 LLM 调用结果。
- **降采样**：原始逐条记录只保留最近 7 天（`_RAW_WINDOW_DAYS`），更早
  的记录由 `compact_call_stats_storage()` 压缩成按天求和的汇总行。跟
  `growth_advisor.py::compact_health_trend_storage()` 是平行实现但聚合
  语义不同——健康度快照"取当天最新一条"，调用计数"当天全部记录求和"。
  **跟设计文档的取舍差异**：当前**没有**接一个自动定期调用压缩函数的
  调度点（`growth_health_trend` 挂在 `run_daily_cycle()` 上，调用计数
  没有对应的"每日一次"的天然挂载点，因为它不是"每日快照"而是"高频
  事件流"）。查询接口 `call_stats_series()` 在内存里重新聚合、不依赖
  压缩是否发生过，所以晚一点压缩不影响展示正确性，只是文件会先涨到
  一定大小再被压缩。留给后续视实际文件增长速度决定要不要补一个调度点
  （比如挂到 cron 的 `sys:consolidation` 之类的既有每日任务上）。
- 新增只读端点 `GET /v1/self/llm_call_stats?days=7`。
- 看板"🧠 自我状态"Tab 新增"📊 LLM 调用统计"区块：调用次数/失败数柱状图 +
  当日四个汇总指标。

---

## 涉及文件清单

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `src/mini_agent/perception/sentinel.py` | 新增 | 五类扫描函数 + `sentinel_summary()` |
| `src/mini_agent/llm/call_stats.py` | 新增 | 轻量调用计数：攒批写入 + 按天聚合 + 降采样压缩 |
| `src/mini_agent/agent/llm_control.py` | 修改 | `_call_llm()` 挂载调用计数记录，新增 `_record_llm_call_stat()` helper |
| `src/mini_agent/llm/service.py` | 修改 | `LLMHelper.chat()` 挂载调用计数记录，新增 `_record_call_stat()` helper |
| `src/mini_agent/storage/paths.py` | 修改 | 新增 `llm_call_stats_path` 属性 |
| `src/mini_agent/evolution/resource_arbiter.py` | 修改 | 新增 `gating_ratio_summary()` |
| `src/mini_agent/api/routes.py` | 修改 | 新增 4 个端点（llm_pool_status/wiki_quarantine_status/sentinel/summary/llm_call_stats）+ `gating_history` 响应扩展字段 + 路由列表注释更新 |
| `apps/mini_agent_kanban/client.py` | 修改 | 新增 `llm_pool_status()` / `llm_call_stats()` / `wiki_quarantine_status()` / `sentinel_summary()` |
| `apps/mini_agent_kanban/app.py` | 修改 | 新增 `_render_llm_pool_status()`（含调用统计图表）/ `_render_sentinel_panel()`，全局日程 Tab 展示 ratio_summary |
| `tests/test_sentinel_panel.py` | 新增 | 18 个测试用例，覆盖 sentinel.py 全部函数 |
| `tests/test_llm_call_stats.py` | 新增 | 10 个测试用例，覆盖 call_stats.py 全部函数 |
| `docs/kanban-dashboard-guide.md` | 修改 | 顶栏哨兵面板 / 自我状态 Tab LLM 区块 / 全局日程 Tab 占比摘要 / API 端点表 |
| `docs/llm-failover-guide.md` | 修改 | 新增"daemon 模式下的可观测性"与"轻量调用计数"两节 |

## 测试情况

`tests/test_sentinel_panel.py`（18 用例）+ `tests/test_llm_call_stats.py`
（10 用例）全部通过（`PYTHONPATH=src python3 -m pytest tests/
test_sentinel_panel.py tests/test_llm_call_stats.py -q`）。回归验证了
`test_gating_history_active_recording.py`、
`test_resource_arbiter_gating_track_j.py`、
`test_resource_arbiter_behavior_gating.py`、`test_wiki_quarantine.py`、
`test_cron_job_runner_resource_arbiter.py`、`test_llm_helper.py`、
`test_orchestration_llm_helper_provider.py`、
`test_hybrid_exec_llm_pool_sharing.py` 共 102 个既有用例（前 52 个 +
本次新增的 LLMHelper/call_with_pool 相关回归），全部通过，无回归。
`test_gating_history.py`（依赖 fastapi TestClient）因为当前环境缺少
可用的 `httpx2` 依赖无法收集，是既有环境缺口，跟本次改动无关（其余
测试文件也依赖同样的 fastapi 但不需要 TestClient 因而不受影响）。
`test_goal_mode.py` 中 5 个 `test_build_from_history_*` 用例本身就
因为测试 mock 的 `_run_builder` 签名（单参数 `lambda prompt`）跟生产
代码当前签名（`_run_builder(self, prompt, *, detection_text=None)`）
不一致而失败，是运行环境里已经存在的测试/生产代码漂移，跟本次改动
的调用路径（`GoalSpecBuilder._run_builder` 完全不经过 `LLMHelper.
chat()`/`call_with_pool()`）无关，未做修复（超出本次改动范围）。
