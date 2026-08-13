# Goal Stuck 统计面板 + 执行阶段进展判断改用 LLM 改进方案

延续 `goal_execution_phase_improvement_plan.md`（Stage D 已完成）和
`goal_execution_fairness_improvement_plan.md`（改造项四待评估）讨论中
识别出的两个方向，独立立项：

1. 只读统计面板：这个/这些 Goal 历史上被判定 `stuck` 的次数，为"要不要上
   并行多路径择优（ensemble）"之类更高成本的机制提供真实频率数据依据。
2. Stage D 的"进展趋势"信号（`compute_progress_trend_signal`）目前是纯
   文本相似度（difflib）判断，容易把"内容确实雷同但属于正常重复"（比如
   周期性巡检类 Goal）和"真的在原地打转"混为一谈，改成可选的 LLM 判断，
   相似度降级为默认兜底。

## 1. Stuck 统计面板（只读）

### 1.1 数据来源

不新增存储，纯读取 `goal_mode/state.py::list_resumable_sessions(project_root,
include_all=True)` 已经在扫描的 `goal_state.json`（`status` 字段里
`"stuck"` 是 `goal_mode/runner.py::_finish()` 在恢复次数耗尽后才会写入的
终态，参见 `goal_mode_stuck_compact_plan.md`）。

### 1.2 聚合函数

新增 `perception/goal_stuck_stats.py::stuck_stats_summary(project_root,
recent_days=30)`：

- `total_sessions` / `stuck_count` / `stuck_ratio`
- `recent_stuck_count`（`recent_days` 窗口内）
- `top_stuck_goal_texts`：按 `goal_text` 归并（同一个目标反复被判 stuck，
  说明目标描述或验收标准本身有问题，比偶发一次更值得关注），取次数最多的
  若干条，附最近一次 `updated_at` 和 `final_report` 片段。

任何异常/目录不存在返回全零结构，不抛异常（与既有 `scan_goal_states`/
`sentinel_summary` 风格一致）。

### 1.3 暴露方式

- REST：`GET /v1/goal_mode/stuck_stats`（只读，复用 `_require_owner`）。
- 看板：`AgentClient.goal_mode_stuck_stats()` + `🧠 自我状态` tab 新增一个
  折叠区块，展示总数/比例 + top 列表，用于回答"这个功能到底值不值得做"这类
  问题，不提供任何操作按钮（纯参考数据）。

## 2. 进展趋势信号改用 LLM 判断

### 2.1 现状问题

`compute_progress_trend_signal()` 用 `difflib.SequenceMatcher` 比较相邻
两轮 `progress_notes` 的字符串相似度，阈值 0.85。这只能识别"文字层面
雷同"，识别不了：

- 文字表述不同但本质是同一件事在原地打转（相似度算法测不出来）；
- 文字表述相似但确实有实质推进（比如结构化报告每轮格式相同，只是数字/
  结论变了，被误判为"雷同"）。

### 2.2 改动方式

比照 `growth_advisor.py::_llm_summarize_feedback_pattern` 与
`GrowthAdvisorConfig.feedback_pattern_llm_enabled` 的"规则兜底 + 可选 LLM
增强"模式：

- `compute_progress_trend_signal()` 新增可选 `llm_helper: Optional[Callable[[str], str]]`
  参数。传入且能拿到有效响应时，改用 LLM 判断（新增
  `_llm_judge_progress_trend()`：把最近几轮 `progress_notes` 原文交给
  LLM，要求只回答 `STUCK` / `PROGRESSING` / `UNSURE` 三选一，解析失败或
  空响应时静默退回 difflib 结果，不抛异常、不影响主流程）。
- 未传 `llm_helper`（默认 `None`）时行为与 Stage D 完全一致，纯 difflib。
- 新增 `config/models.py::ExecutionPhaseConfig`（`progress_trend_llm_enabled:
  bool = False`），挂到 `AppConfig.execution_phase`；`goal_cron_bridge.py`
  只有配置开启且能拿到 `llm_helper_provider` 时才传入，否则维持规则版。
- `register_goal_cycle_handler()` 新增可选 `llm_helper_provider` 参数
  （惰性获取，daemon 启动时 agent 可能还没就绪也不影响注册本身，与
  `ensure_goal_relevance_judge_job` 等既有 P5 机制同款写法），`api/server.py`
  注册处按需传入 `lambda: getattr(agent, "llm_helper", None)`。

### 2.3 兼容性

- 默认关闭，未开启配置的现有部署行为完全不变（继续用 difflib）。
- LLM 判断本身也是"能用就用，用不了就当没发生"——响应为空/超时/不在三个
  合法值内一律退回规则版结果，不会因为 LLM 异常导致 Goal 触发主流程报错。

## 3. 落地顺序

1. Stuck 统计面板（无依赖，纯读取聚合，风险最低）。
2. LLM 进展判断（在 Stage D 基础上做可选增强，默认关闭）。

每个方向完成后更新对应文档，打包改动文件。

## 4. 实施记录

两个方向均已实现：

- **§1 Stuck 统计面板**：`perception/goal_stuck_stats.py::stuck_stats_summary()`
  + `GET /v1/goal_mode/stuck_stats` + `AgentClient.goal_mode_stuck_stats()` +
  "🧠 自我状态"tab 新增"🧊 Goal Stuck 历史统计"只读区块。
  `tests/test_goal_stuck_stats.py`（7 例）覆盖空项目/混合状态过滤/时间窗口/
  按 goal_text 归并排序/缺失描述兜底/损坏文件不崩溃。

- **§2 LLM 进展判断**：`compute_progress_trend_signal()` 新增可选
  `llm_helper` 参数，新增 `_llm_judge_progress_trend()`（STUCK/
  PROGRESSING/UNSURE 三选一解析，异常或不确定一律退回 difflib）；新增
  `ExecutionPhaseConfig.progress_trend_llm_enabled`（默认 `False`）；
  `goal_cron_bridge.py` 的 `register_goal_cycle_handler()`/
  `_fire_goal_cycle()`/`_append_execution_phase_context()` 新增可选
  `llm_helper_provider` 链路（惰性获取，只有配置开启且能拿到非 None
  helper 时才真正调用）；`api/server.py` 在注册 goal_cycle handler 时接入
  `lambda: getattr(agent, "llm_helper", None)`。`tests/test_execution_phase.py`
  新增 11 个用例（`_llm_judge_progress_trend` 的 7 种响应场景 +
  `compute_progress_trend_signal` 的 LLM 优先/回退 2 例 + bridge 层配置
  开关的 2 例联通性测试），全部通过；`tests/test_execution_phase.py`
  当前共 44 例。

全量回归：`tests/test_execution_phase.py` + `tests/test_goal_cron_bridge.py`
+ `tests/test_goal_stuck_stats.py` 共 70 例通过；`tests/test_goal_execution_
spec.py`（35 例，验证 config 模块改动无回归）通过。`docs/goal-execution-
phase-guide.md`、`docs/kanban-dashboard-guide.md` 已同步更新。
