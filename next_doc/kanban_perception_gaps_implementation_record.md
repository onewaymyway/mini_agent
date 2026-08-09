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

（无——方向 A/B.1/B.2/C/D/E 已在本记录的三期改动中全部落地，详见下方
第三期。）

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

## 第三期（D.1 + D.2）— 已完成

### D.3 风险 1 的应对：抽取通用每日快照存储小工具

- 新增 `perception/daily_snapshot.py`：`append_daily_snapshot()` /
  `compact_daily_snapshot_storage()`（按天分桶、桶内保留 `recorded_at`
  最大的一条）/ `read_daily_snapshot_series()`，跟 `growth_advisor.py::
  _compact_health_trend_rows()` 的降采样语义完全一致（"每天一条，取
  最新覆盖"）。
- **有意的不一致，不是遗漏**：`growth_health_trend.jsonl` 本身**没有**
  迁移到这个通用工具上——它已经有一套跑通并测试过的独立实现，本次不做
  无收益的迁移重构（风险 > 收益：现成实现没有 bug，迁移只是为了"统一"
  而统一）；`llm/call_stats.py`（B.2）也**没有**改造成基于本模块——它的
  降采样语义是"按天求和"而不是"按天取最新"，语义不同，勉强复用只会让
  接口参数变得更绕（需要传一个聚合函数）。这个通用小工具目前只服务于
  新增的 D.1 场景，是"给未来同类场景一个现成的选项"，不是"强制所有
  日快照类数据统一到一个实现"。

### D.1：Objective 完成率趋势

- 新增 `evolution/objective_trend.py`：`compute_objective_completion_
  snapshot()`（纯计算，从 `.agent/objective_executions.json` 统计
  `objectives_completed_today`/`objectives_failed_today`/
  `avg_retry_count`/`active_goals_count`）+ `record_objective_
  completion_snapshot()`（计算并追加快照）+
  `objective_completion_trend_series()`（查询）+
  `compact_objective_completion_trend_storage()`（降采样，委托给
  `daily_snapshot.py`）。
- **挂载点**：按设计文档"复用 `growth_advisor.run_daily_cycle()` 同一个
  每日调用点"的建议，选择了 `POST /v1/growth/scan` 路由（cron
  `sys:growth_advisor_daily` 每日调用的既有端点）——在
  `ga.run_daily_cycle()` 成功后顺带记一条快照，best-effort（try/except
  + log_exception，不影响成长顾问本身的返回结果）。**跟设计文档的取舍
  差异**：设计文档也提到"或者新建一个平行的每日 cron"这个选项，本次
  选择挂载到既有路由而不是新建 cron，是因为 Objective 完成率快照和
  成长顾问信号扫描虽然领域不同，但都符合"daemon 每日收尾时该做的事"
  这个语义，没有必要为了"代码归属"洁癖再新增一个 cron job 定义、
  多一次调度开销——`objective_trend.py` 独立成模块（不是塞进
  `growth_advisor.py`），已经保证了领域边界清晰，调用点复用不等于
  代码耦合。
- **"今天"的窗口口径**：`compute_objective_completion_snapshot()` 用
  `now` 所在自然日的 `[day_start, day_start+86400)` 时间戳窗口判定
  `completed`/`failed` 是否计入"今天"，`active_goals_count` 则不受时间
  窗口限制（"现在还有多少个在跑"是瞬时状态，不是"今天发生了多少次"）。
  测试覆盖了窗口内/窗口外两种边界。
- 新增只读端点 `GET /v1/objectives/completion_trend?limit=30`。
- 看板"📌 目标看板"Tab 顶部新增"📈 完成率趋势"折叠区块（默认收起，展开
  才拉取），跟"🌱 成长顾问"tab 的"📈 健康度趋势"是同一套"折叠区块 + 折线
  图 + 最新指标"展示模式，数据源完全独立。

### D.2：记忆库增长趋势（几乎零成本的展示位置决策）

- 设计文档明确指出这个需求已经被 `growth_health_trend.jsonl` 覆盖，
  不需要新增任何采集逻辑——落地方式是"在'🧠 自我状态'Tab 也调用一次
  既有的 `_render_growth_health_trend(client)` 组件"，跟"🌱 成长顾问"
  Tab 复用同一个函数、同一份数据源，零新增存储/端点。

### 未实现（超出改进方案范围，供未来参考）

- D.3 风险 2 提到的"避免新增独立线程/cron"约束已经满足（D.1 挂载在
  既有路由上）；风险 1 的通用小工具已抽取但只用于新场景，不做存量迁移
  （见上）。方向 D 至此已按设计文档的范围全部落地。

---

## 涉及文件清单

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `src/mini_agent/perception/sentinel.py` | 新增 | 五类扫描函数 + `sentinel_summary()` |
| `src/mini_agent/perception/daily_snapshot.py` | 新增 | 通用每日快照存储小工具（方向 D.3 风险 1） |
| `src/mini_agent/llm/call_stats.py` | 新增 | 轻量调用计数：攒批写入 + 按天聚合 + 降采样压缩 |
| `src/mini_agent/evolution/objective_trend.py` | 新增 | Objective 完成率每日趋势快照（方向 D.1） |
| `src/mini_agent/agent/llm_control.py` | 修改 | `_call_llm()` 挂载调用计数记录，新增 `_record_llm_call_stat()` helper |
| `src/mini_agent/llm/service.py` | 修改 | `LLMHelper.chat()` 挂载调用计数记录，新增 `_record_call_stat()` helper |
| `src/mini_agent/storage/paths.py` | 修改 | 新增 `llm_call_stats_path` / `objective_completion_trend_path` 属性 |
| `src/mini_agent/evolution/resource_arbiter.py` | 修改 | 新增 `gating_ratio_summary()` |
| `src/mini_agent/api/routes.py` | 修改 | 新增 5 个端点（llm_pool_status/wiki_quarantine_status/sentinel/summary/llm_call_stats/objectives/completion_trend）+ `gating_history` 响应扩展字段 + `/growth/scan` 顺带记录 Objective 快照 + 路由列表注释更新 |
| `apps/mini_agent_kanban/client.py` | 修改 | 新增 `llm_pool_status()` / `llm_call_stats()` / `wiki_quarantine_status()` / `sentinel_summary()` / `objective_completion_trend()` |
| `apps/mini_agent_kanban/app.py` | 修改 | 新增 `_render_llm_pool_status()`（含调用统计图表）/ `_render_sentinel_panel()` / `_render_objective_completion_trend()`，自我状态 Tab 复用健康度趋势组件，全局日程 Tab 展示 ratio_summary |
| `tests/test_sentinel_panel.py` | 新增 | 18 个测试用例，覆盖 sentinel.py 全部函数 |
| `tests/test_llm_call_stats.py` | 新增 | 10 个测试用例，覆盖 call_stats.py 全部函数 |
| `tests/test_objective_completion_trend.py` | 新增 | 13 个测试用例，覆盖 daily_snapshot.py + objective_trend.py 全部函数 |
| `docs/kanban-dashboard-guide.md` | 修改 | 顶栏哨兵面板 / 自我状态 Tab LLM+健康度趋势区块 / 目标看板 Tab 完成率趋势 / 全局日程 Tab 占比摘要 / API 端点表 |
| `docs/llm-failover-guide.md` | 修改 | 新增"daemon 模式下的可观测性"与"轻量调用计数"两节 |

## 测试情况

`tests/test_sentinel_panel.py`（18 用例）+ `tests/test_llm_call_stats.py`
（10 用例）+ `tests/test_objective_completion_trend.py`（13 用例）共 41
个新增用例全部通过（`PYTHONPATH=src python3 -m pytest tests/
test_sentinel_panel.py tests/test_llm_call_stats.py tests/
test_objective_completion_trend.py -q`）。回归验证了
`test_gating_history_active_recording.py`、
`test_resource_arbiter_gating_track_j.py`、
`test_resource_arbiter_behavior_gating.py`、`test_wiki_quarantine.py`、
`test_cron_job_runner_resource_arbiter.py`、`test_llm_helper.py`、
`test_orchestration_llm_helper_provider.py`、
`test_hybrid_exec_llm_pool_sharing.py` 共 74 个既有用例，全部通过，
三期加起来累计 115 个用例全绿，无回归。

**已知的、与本次改动无关的既有环境/代码问题**（均已核实不在本次改动
的调用路径上，未做修复）：
- `test_gating_history.py`（依赖 fastapi TestClient）因为当前环境缺少
  可用的 `httpx2` 依赖无法收集；
- `test_goal_mode.py` 中 5 个 `test_build_from_history_*` 用例的 mock
  签名（`lambda prompt`）跟生产代码当前签名（`_run_builder(self,
  prompt, *, detection_text=None)`）不一致而失败，是运行环境里已经
  存在的测试/生产代码漂移，`GoalSpecBuilder._run_builder` 完全不经过
  `LLMHelper.chat()`/`call_with_pool()`；
- `test_growth_advisor.py::TestHealthTrend::
  test_compact_health_trend_storage_downsamples_old_points` 断言
  `removed == 2` 实际得到 `1`，该测试和被测函数
  （`compact_health_trend_storage()`）均在 `growth_advisor.py` 里，
  本次三期改动都没有修改过这个文件（只是新增了 `daily_snapshot.py`
  作为供*未来*场景使用的独立工具，`growth_health_trend.jsonl` 的存量
  实现原样未动），确认是环境里已经存在的问题。
