# 看板主界面化 + 自主执行/自主进化 改进方案 —— 实施记录

> 关联方案：`next_doc/kanban_and_autonomy_improvement_plan.md`
> 本记录只覆盖**已落地**的部分，未提及的 Track 视为未开始，请以方案原文的
> Track 编号为准继续核对。

## 已完成 Track

### Track C（P0）：并行 Objective 路径互斥检测 —— 退化版已上线

- `ExecutionStep` 新增 `paths: list[str]` 字段。`ObjectiveExecutor` 新增：
  - `_declare_step_paths()`：调用可选的 `declare_paths_fn(step_description)` 猜测本步骤
    涉及的路径，缓存到 `step.paths`，避免重复调用 LLM。
  - `_find_path_conflict()`：与其他正在运行的 execution 的路径集合比对，命中则返回冲突方
    execution_id。
  - `_active_step_paths: dict[execution_id, set[path]]`：内存态登记当前各 execution 占用
    的路径，随 step 完成/失败/取消/超时释放（`_release_step_paths()`）。
  - 声明不出路径（`declare_paths_fn` 未提供、调用失败、或返回空列表）时退化为哨兵路径
    `_UNKNOWN_PATH_SENTINEL`——按方案原文"保守当冲突"的哲学，两个都拿不到路径信息的
    Objective 不允许并行，真正的路径冲突不受影响。
  - 冲突时 step 状态置为 `"blocked"`（新状态，区别于 `"failed"`），`start()`/
    `on_turn_done()` 均已改为识别这个状态，不会误判 Objective 为失败。
  - 新增 `retry_blocked_steps()`，由 `AutonomousLoop._tick_maintenance()` 每次 tick 时
    调用，尝试重新提交所有 blocked 的当前 step；放在 `reap_stale_steps()` 之后、
    `ResourceArbiter` 门控 early-return 之前（与回收卡死 step 同样的理由：这是"推进已
    存在的排队状态"，不应被"是否允许发起新自主任务"的门控挡住）。
- `server.py` 新增 `_declare_paths()` 包装函数（内部调用新增的
  `_default_declare_paths(llm_helper, step_description)`），注入
  `ObjectiveExecutor(declare_paths_fn=...)`。
- **未做**（按方案原文标注为 P1 优化项，本轮不在范围内）：Track G 的"结构化产出物
  传递"反哺路径声明精确度——目前 `ExecutionStep.artifacts` 字段已预留，但还没有从
  agent 回复/工具调用记录里解析写入的逻辑。

### Track B（P0）：GoalNode 与 ObjectiveExecution 状态单向同步 —— 已上线

- `GoalNode.status` 补充注释，新增可用取值 `"failed"`、`"cancelled"`（自由字符串字段，
  无需 schema 迁移，看板 `GOAL_STATUS_COLUMNS` 已加两列对应展示）。
- `ObjectiveExecutor.__init__` 新增 `goal_backlog` 参数；新增 `_sync_goal_status()`，
  在 `_on_objective_completed()` / `_on_objective_failed()` / 新增的
  `_on_objective_cancelled()` 三处回调时单向回写：
  - `completed` → GoalNode.status = `"completed"`
  - `failed` → GoalNode.status = `"failed"`
  - `cancelled` → GoalNode.status = `"cancelled"`（用户主动终止，见 Track D）
- 同步方向严格单向：执行事实（ObjectiveExecutor）→ 决策记录（GoalBacklog）。反方向
  （用户在看板上手动改 GoalNode 状态）**尚未**触发对应 execution 的 pause/cancel——
  这部分是方案原文规则 2 的内容，本轮未实现，属于遗留项（见下方"未完成/待续"）。
- `server.py` 实例化 `ObjectiveExecutor` 时传入已有的 `goal_backlog` 变量。

### Track D（P1）：看板可操作能力（终止 / 重试 / 插话） —— 已上线

- `ObjectiveExecutor` 新增三个公开方法：
  - `cancel(execution_id) -> bool`：置 `status="cancelled"`，释放并发槽位与路径占用，
    调用 `_on_objective_cancelled()` 同步 GoalNode 状态。已完成/失败/已取消的
    execution 不能重复 cancel。
  - `retry_current_step(execution_id) -> bool`：不检查超时，随时可手动触发当前 step
    重新提交（复用 `_submit_step()`，与自动重试路径一致）。
  - `inject_guidance(execution_id, message) -> bool`：把用户输入存进
    `ExecutionStep.pending_guidance`，`_submit_step()` 拼 prompt 时会以
    `[用户补充说明]` 段落附加进去，提交后清空，不会重复注入。若希望立即生效，需要
    配合 `retry_current_step()` 让当前 step 重新提交。
- 对应 REST 端点：`POST /v1/objectives/{execution_id}/{cancel,retry,guidance}`
  （`src/mini_agent/api/routes.py`，`_objective_executor_or_404()` 统一定位
  ObjectiveExecutor 实例并做 owner 校验）。
- `apps/mini_agent_kanban/client.py` 新增 `cancel_objective()` / `retry_objective()` /
  `inject_objective_guidance()`。
- 看板 UI（`apps/mini_agent_kanban/app.py::_render_objective_execution_detail()`）
  在每张有执行记录的 Objective 卡片下追加三个操作入口：🛑 终止 / 🔁 重试当前步 /
  💬 插话（`st.popover` 内嵌文本框 + 发送按钮），仅在对应状态下显示（已完成/已终止的
  Objective 不显示任何按钮）。

### Track F（P1）：Step 失败重试策略升级 —— 已上线（第一部分）

- `on_turn_failed()` 在决定重试时先把失败原因写入 `step.error_msg`，`_submit_step()`
  拼装 prompt 时追加：
  ```
  [重试 - 第 N 次] 上一次尝试失败原因：{error_msg}
  请根据失败原因调整方法后重试，不要重复同样的做法。
  ```
- `reap_stale_steps()`（超时回收）本来就会写 `error_msg`，现在同样会被
  `_submit_step()` 拼进重试 prompt，行为一致。
- **未做**：方案原文 Track F 第二部分——"连续两次都失败后，先尝试重新分解剩余步骤"
  （调用 `_decompose()` 只针对未完成部分）——本轮未实现，`MAX_STEP_RETRIES` 耗尽后
  仍然是直接判 Objective failed。这部分与 Track K（P11 二级恢复）强相关，建议一起做。

### Track A（P0）：全局待办通知中心 —— 已上线

- 新增 `GET /v1/inbox`（`src/mini_agent/api/routes.py`）：
  - 遍历 `SessionAgentPool`（多用户模式）或单一 `bridge`（单用户模式）下所有活跃
    session，聚合 `permission_gate.list_pending()` / `interaction_gate.list_pending()`。
  - 聚合所有 `status == "failed"` 的 Objective execution（跨 objective，不区分 session）。
  - 返回统一结构：`{type, session_id?, req_id?, objective_id?, execution_id?, summary,
    created_at}`，按 `created_at` 倒序。
- `client.py` 新增 `inbox()`。
- 看板顶栏（`_render_topbar_body()`）新增 `_render_global_inbox()`：非空时展示
  "📥 全局待办中心：共有 N 条跨会话待办" 的可展开列表，权限/交互类待办若关联的
  session 不是当前页面绑定的 session，提供"跳转"按钮（复用现有
  `update_query_params(session_id=...)`，遵守"写入后不手动 rerun"的约定）。
- **已知局限**（据实记录，不夸大完成度）：
  - `permission_gate.list_pending()` / `interaction_gate.list_pending()` 目前不带
    `created_at` 字段（`bridge.py` 里的 dataclass 没有这个属性），因此权限/交互类待办
    项的 `created_at` 恒为 `None`，排序时视为最旧——影响的只是"全局待办列表内的排序
    顺序"，不影响数量统计和跳转功能。如果后续要做"按时间精确排序"，需要先给
    `_PendingPermission` / `_PendingInteraction` 加时间戳字段。
  - Objective 失败项没有关联 `session_id`（`GoalBacklog`/`ObjectiveExecution` 都不记录
    是哪个 session 创建的 Objective），所以这类待办项前端不显示"跳转"按钮，只做纯
    展示——这是现有数据模型的限制，不在本轮修复范围。

## 测试

新增 `tests/test_objective_executor_kanban_tracks.py`，覆盖：

- `TestPathMutex`：两个声明同路径的 Objective 并发提交时，后者被 `blocked` 而非直接
  失败/并行；不冲突路径可以正常并行；声明不出路径时退化为串行。
- `TestGoalStatusSync`：完成/失败/取消三种终态都能正确单向回写 `GoalNode.status`；
  已终止的 execution 不能重复 cancel。
- `TestKanbanActionableApis`：`retry_current_step()` 会重新提交；`inject_guidance()`
  写入的内容出现在下一次提交的 prompt 里；对不存在的 execution 调用返回 `False`
  而不是抛异常。
- `TestRetryPromptCarriesFailureReason`：自动重试的 prompt 里包含上一次失败原因和
  "不要重复同样的做法"提示语。

运行方式（仓库暂无 `pytest.ini`/`conftest.py` 设置 `PYTHONPATH`，手动指定 `src`）：

```bash
PYTHONPATH=src python3 -m pytest tests/test_objective_executor_kanban_tracks.py -q
```

本轮验证：10 项全部通过；同时跑过 `tests/test_goal_backlog.py`（既有测试）确认无回归。

## 未完成 / 待续（供下一轮参考）

按方案原文的路线图，以下项目本轮**未开始**，仍需后续排期：

- **Track B 完整版**：用户在看板上手动把 GoalNode 状态改成非"运行中"时，反向驱动
  对应 execution 的 pause/cancel（目前只做了"用户点终止按钮 → cancel() → 同步
  GoalNode"这一个方向，`update_goal()` PATCH 接口本身还没有调用 `cancel()`）。
- **Track E**：执行细节可钻取（读 `traces.jsonl` 按 `step_id` 过滤展示完整
  tool_call/tool_result 序列）——数据已存在，只是没接线。
- **Track F 第二部分**：连续失败后先尝试"重新分解剩余步骤"再判定 Objective failed。
- **Track G**：`ExecutionStep.artifacts` 字段已预留，解析 agent 回复里的
  `[ARTIFACTS] ...` 标记（或从 `write_file`/`patch_file` 工具调用记录里自动提取）
  尚未实现；这部分完成后可以反哺 Track C 的路径声明精确度。
- **Track H / I / J / K**（P2）：效果回填闭环、进化提案分级自治、资源门控降级执行、
  并发数自适应——均未开始，需要先完成方案原文"待确认/待细化项"里列出的前置调研
  （主题关联字段粒度、`LLMClientPool` 是否支持按场景切换模型档位等）。

建议下一轮延续方案原文推荐的顺序（本轮已完成 C→A→D→B→F 的第一部分），优先做
**Track B 完整版**（补齐反向同步，避免两个方向不一致的兜底提示长期存在）和
**Track E**（数据已就绪，工作量小），再排 P2 的其余项。
