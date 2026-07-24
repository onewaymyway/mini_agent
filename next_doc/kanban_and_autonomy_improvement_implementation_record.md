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

## 第二轮已完成 Track（本次续做）

### Track B（P0）：GoalNode 与 ObjectiveExecution 状态单向同步 —— 补齐反向同步，完整版已上线

- `ObjectiveExecutor` 新增两个只读查询方法：
  - `get_execution(execution_id) -> Optional[ObjectiveExecution]`：按 execution_id
    查询执行记录（Track E 的 trace 接口也复用了这个方法）。
  - `find_running_execution_by_objective(objective_id) -> Optional[str]`：查询
    某个 objective 当前是否有 `running`/`pending` 的 execution，有则返回其
    execution_id，供反向同步定位目标。
- `cancel()` 新增 `sync_goal_status: bool = True` 参数（`_on_objective_cancelled()`
  同步新增该参数）。默认行为不变（看板"🛑 终止"按钮走这条路径，仍然会把
  GoalNode.status 同步为 `"cancelled"`）。
- **反向同步**：`api/routes.py::update_goal()`（PATCH `/v1/goals/{goal_id}`）在
  `backlog.update_fields()` 成功之后，若本次 PATCH 把 `status` 改成了非 `"active"`
  的值，查询该 objective 是否还有 running/pending 的 execution，有则调用
  `oe.cancel(execution_id, sync_goal_status=False)`——**特意传 `False`**：这次
  GoalNode.status 已经是用户刚显式选择的值（比如看板下拉框选了"已放弃"/
  `abandoned`），如果 `cancel()` 仍按默认行为把状态覆盖回 `"cancelled"`，
  会出现"用户明明选了已放弃，看板却显示已终止"的错误覆盖——这是本轮踩到
  的一个真实坑，加这个参数就是为了避免它。
- 查找 ObjectiveExecutor 实例的方式与既有 `_objective_executor_or_404()` 一致
  （先查 `http_server.bridge._objective_executor`，再退化到
  `http_server.autonomous_loop._objective_executor`），但整个反向同步块包在
  `try/except` 里且不抛 404——找不到 ObjectiveExecutor（比如自主执行功能未
  启用）时静默跳过，不影响 PATCH 本身的成功返回。

至此方案原文规则 1（正向：完成/失败/取消回写 GoalNode）和规则 2（反向：
用户手动改状态驱动 execution cancel）都已落地，"两个方向互相覆盖"的风险
通过 `sync_goal_status` 参数规避。

### Track E（P1）：执行细节可钻取 —— 已上线

- `ExecutionStep` 新增 `submitted_message: str` 字段（持久化），在
  `_submit_step()` 构建完 `message` 后立即记录——保存的是**拼装后的完整
  prompt 文本**（含前序步骤上下文/重试原因/用户插话），而不是原始
  `description`，因为写进 agent 会话历史里的就是这段拼装后的文本，只有
  它能做精确匹配。
- 新增 `GET /v1/objectives/{execution_id}/steps/{step_index}/trace`
  （`api/routes.py`）：
  - 用 `step.submitted_message` 去匹配主 agent 会话（`http_server.bridge.agent`）
    的 active history（`agent._hist.history`）里 `_type == "user_input"` 且
    `content` 完全相等的条目，取**最后一次**匹配（对应最新一次重试提交）；
    截取到下一条 `user_input`（或历史末尾）之间的所有条目，即为这一步
    实际发生的完整过程。
  - 用 `_format_history_entry_for_trace()` 把 `assistant_reply`（含
    `text`/`tool_use` 混合块）和 `tool_result` 类型的条目转成精简结构
    （`{"type": "tool_call", "tool_name":, "tool_input":}` /
    `{"type": "tool_result", "text":}` 等），压缩/摘要/提醒类内部记录
    过滤掉。
  - 找不到匹配（该 step 已被压缩/归档、或数据里没有 `submitted_message`、
    或当前访问不到 agent 历史）时返回 `{"entries": [], "note": "..."}`
    而不是报错，前端据此展示提示文案而不是空白/报错。
- `apps/mini_agent_kanban/client.py` 新增 `objective_step_trace(execution_id,
  step_index)`。
- 看板 UI（`_render_objective_execution_detail()`）在步骤列表下方，对每个
  `done`/`failed` 的 step 追加一个"🔍 查看详情"expander，展开后按
  `user_input`/`assistant_reply`（含 `tool_call` 子块）/`tool_result` 分别
  用 `st.markdown`/`st.json`/`st.code` 渲染，默认收起不占版面。
- **已知局限**（据实记录）：
  - 若该 step 因为历史压缩（compact）已经不在 active history 里，这里
    直接判"找不到"并提示，不会去扫描 `raw_history.jsonl` 兜底——逐行反查
    匹配对一次看板点击来说成本不划算，属于本轮不覆盖的边界情况。
  - 只支持"当前能访问到运行中 agent 实例"的场景（单用户模式下的主
    bridge）；多用户/多 session 场景下，如果 Objective 步骤实际跑在别的
    session 的 agent 上，这里会定位不到——目前 Objective 执行确实统一走
    `http_server.bridge`（主 self session），暂不构成实际限制，但如果未来
    允许按 session 派发 Objective 执行，这里需要同步改造。

### Track F（P1）：Step 失败重试策略升级 —— 第二部分已上线，方案完整版落地

- `ObjectiveExecution` 新增 `redecompose_attempted: bool`（持久化），每个
  execution 只允许尝试一次"重新分解剩余步骤"，避免"新步骤又失败 → 又
  分解"的隐性资源浪费循环。
- 新增 `ObjectiveExecutor._attempt_redecompose(ex, step_idx, failure_reason)`：
  某个 step 耗尽 `MAX_STEP_RETRIES` 后，先把"已完成步骤的结果摘要 + 原计划
  剩余步骤描述 + 这次失败原因"喂给 `llm_redecompose_fn`，返回非空的新步骤
  列表时，用它替换 `ex.steps[step_idx:]`（保留之前已完成步骤不变），
  `ex.status` 保持/恢复为 `"running"`，提交新的第一步；未提供该回调、
  调用异常、或返回空列表时原样返回 `False`，调用方按原有逻辑判
  Objective failed——**不提供该回调时行为与改造前完全一致**（专门写了
  回归测试验证这一点）。
- 接入点：`on_turn_failed()` 和 `reap_stale_steps()` 各自的"重试次数耗尽"
  分支，都改成先调用 `_attempt_redecompose()`，成功则提前 return/continue，
  不再往下走"判 Objective failed"的代码路径。
- 默认实现 `_default_llm_redecompose()`（`objective_executor.py`）+
  `server.py::_llm_redecompose()` 包装函数并注入 `ObjectiveExecutor(
  llm_redecompose_fn=...)`。

### Track G（P2）：跨步骤结构化产出物传递 —— 部分上线（`[ARTIFACTS]` 标记解析版）

- `ObjectiveExecutor.on_turn_done()` 完成时调用新增的
  `_parse_step_artifacts(result_summary)` → 内部走可选的
  `artifacts_parse_fn` 回调，写入 `step.artifacts`（未提供回调时保持空
  列表，向后兼容）。
- 默认实现 `_default_parse_artifacts()`：用正则 `\[ARTIFACTS\]\s*(.+)` 解析
  agent 回复文本里的标记，按逗号切分成路径列表——这是方案原文"待确认/
  待细化项 2"里标注的**退化方案**，不是更可靠的"从 `write_file`/
  `patch_file` 工具调用记录里自动提取路径参数"那种做法（那种做法依赖能
  访问该 step 完整的 tool_call 序列，本轮已经在 Track E 里做出了这个能力，
  但还没有反过来把它接给 Track G 用——标注为下一轮的明确后续项，见下方
  "未完成/待续"）。
- `_submit_step()` 拼装前序上下文时，新增"[前序步骤产出文件]"段，汇总
  `ex.steps[:step_idx]` 里所有已声明的 `artifacts`（去重且保序），供后续
  步骤引用具体路径而非模糊指代。
- `server.py::_parse_artifacts()` 包装函数并注入
  `ObjectiveExecutor(artifacts_parse_fn=...)`。
- **未做**：依赖 Track E 已具备的"按 step 精确截取 tool_call 序列"能力，
  从中自动提取 `write_file`/`patch_file` 类工具的路径参数，替换/增强现在
  这个纯正则解析版本——本轮未实现，见下方"未完成/待续"。

## 测试（第二轮新增）

新增 `tests/test_objective_executor_kanban_tracks_r2.py`，覆盖：

- `TestCancelSyncFlag`：`cancel()` 默认会同步 GoalNode.status 为
  `"cancelled"`；传 `sync_goal_status=False` 时完全不调用
  `goal_backlog.set_status()`（模拟反向同步路径）。
- `TestFindRunningExecutionByObjective`：能找到 running 的 execution；
  不存在/已完成的 objective 返回 `None`。
- `TestSubmittedMessageForTrace`：`_submit_step()` 会把拼装后的完整 prompt
  记录到 `step.submitted_message`；`get_execution()` 对未知 id 返回 `None`。
- `TestRedecomposeOnExhaustedRetries`：提供 `llm_redecompose_fn` 时，耗尽
  重试后会替换剩余步骤并保持 `running`；新步骤继续失败不会触发第二次
  重新分解（`redecompose_attempted` 生效）；不提供回调、或回调返回空列表
  时，行为与改造前完全一致（直接判 `failed`）。
- `TestArtifactsParsing`：提供 `artifacts_parse_fn` 时会解析并写入
  `step.artifacts`，且出现在下一步 `submitted_message` 的"[前序步骤产出
  文件]"段；不提供回调时 `artifacts` 保持空列表（向后兼容）。

运行方式：

```bash
PYTHONPATH=src python3 -m pytest tests/test_objective_executor_kanban_tracks_r2.py -q
```

本轮验证：13 项全部通过；同时跑过 `tests/test_goal_backlog.py`（既有测试）
确认无回归。第一轮的 `tests/test_objective_executor_kanban_tracks.py`
（覆盖 Track C/B 单向/D/F 第一部分）未在本次改动范围内改动，理论上不受
影响，但本仓库当前快照里未包含该文件，无法在本轮实际重跑验证——如果
你本地仓库里有这个文件，建议连同一起跑一遍确认无回归。

## 第三轮已完成 Track（本次续做）

### Track G（P2）：跨步骤结构化产出物传递 —— 深化版已上线，方案完整落地

延续第二轮"未完成/待续"里点名的下一步：用 Track E 已具备的"精确定位某个
turn 的历史区间"能力，把 Track G 的产出物解析从纯文本正则升级为"直接从
工具调用记录里提取"，不再依赖模型自觉输出 `[ARTIFACTS] ...` 标记。

- 新增 `_ARTIFACT_TOOL_NAMES`（`write_file`/`create_file`/`patch_file`/
  `patch_file_simple`/`delete_file`——即 `tools/builtin.py` 里所有会实际
  写盘的内置工具，都用 `path` 作为路径参数 key）和纯函数
  `_extract_artifacts_from_tool_calls(history_segment)`：扫描一段
  `_type=="assistant_reply"` 历史条目里的 `tool_use` 块，收集命中工具的
  `path` 参数，按出现顺序去重。不依赖 agent/session 实例，输入输出都是
  普通 dict/list，方便单测。
- `ObjectiveExecutor.on_turn_done()` 新增 `history_segment: Optional[list]`
  参数；`_parse_step_artifacts()` 改为两级优先级：
    1. `history_segment` 非空且能提取出路径 → 直接用（更可靠，这是本轮
       升级的主路径）；
    2. 提取不到 / 未提供 `history_segment` → 退化为原有的
       `artifacts_parse_fn` 文本解析（第二轮的 `[ARTIFACTS]` 正则版本，
       现在变成兜底而不是主力）。
  两者都拿不到时保持空列表，不影响 step 完成主流程；不传 `history_segment`
  时行为与第二轮完全一致（专门写了回归测试验证）。
- `api/server.py`：在 `AgentRunner._main_loop()` 里，如果这一轮是
  ObjectiveExecutor 提交的自主步骤（`cmd.initiator in ("autonomous",
  "cron")` 且 `bridge._objective_executor` 存在），在调用
  `bridge.agent.run_turn()` **之前**先记下 `len(bridge.agent._hist.
  history)`；跑完之后用 `history[_hist_len_before:]` 切出"这一轮真正
  新增的全部条目"，作为 `history_segment` 传给 `on_turn_done()`。
  这比 Track E trace 接口"事后用 submitted_message 文本反查边界"更简单
  精确——这里本来就精确知道边界（调用前后各记一次长度），完全不需要
  文本匹配，也不受"历史被压缩"影响（压缩只会发生在下一轮开始前，这一轮
  还没结束就不会被压缩掉）。
- 非自主步骤的普通聊天 turn 不受影响：只有满足上述条件才会去记录
  `_hist_len_before`，其余场景该变量恒为 `None`，`on_turn_done()` 拿到
  的 `history_segment` 也是 `None`，退化路径与第二轮行为完全一致。

至此方案原文"待确认/待细化项 2"里标注的两种做法（模型自觉声明 vs.
从工具调用记录自动提取）都已实现，且按"更可靠的优先、模型自觉的兜底"
的顺序组合在一起，不是简单二选一。

## 测试（第三轮新增）

在 `tests/test_objective_executor_kanban_tracks_r2.py` 里新增
`TestArtifactsFromToolCalls`（5 项），覆盖：

- 从 `write_file`/`patch_file` 类工具调用里正确提取路径，`read_file`
  等非写入类工具不会被误收集。
- 两种来源都命中时，工具调用记录优先于文本标记解析结果。
- `history_segment` 里找不到任何写入类工具调用时，正确退化到文本正则
  解析（第二轮的 `[ARTIFACTS]` 兜底路径）。
- 不传 `history_segment`（或传空）时不报错，行为与第二轮完全一致。
- 同一路径被多个工具调用命中（比如先 `write_file` 后 `patch_file`）时
  去重，只保留一条。

连同第二轮已有的 13 项，本文件当前共 18 项测试，全部通过；同时重跑
`tests/test_goal_backlog.py` 确认无回归。

```bash
PYTHONPATH=src python3 -m pytest tests/test_objective_executor_kanban_tracks_r2.py -q
```

## 未完成 / 待续（供下一轮参考）

按方案原文的路线图，以下项目**仍未开始**，需要后续排期：

- **Track E 边界情况**（未变化，仍待后续）：历史被压缩（compact）后无法
  定位到某个 step 的 trace；多 session 场景下 Objective 执行如果不再
  统一走单一主 bridge，trace 接口需要能定位到正确的 session/agent。
- **Track H / I / J / K**（P2）：效果回填闭环、进化提案分级自治、资源
  门控降级执行、并发数自适应——均未开始，需要先完成方案原文"待确认/
  待细化项"里列出的前置调研（主题关联字段粒度、`LLMClientPool` 是否
  支持按场景切换模型档位等）。

Track A~G 已全部落地（含各自方案原文标注的深化/完整版）。建议下一轮从
P2 剩余的 Track H/I/J/K 里选起，按方案原文路线图，Track H（效果回填闭环）
是其余三项的前置依赖（K 依赖 H 的统计基础设施），建议优先做 Track H 的
"待确认/待细化项 4"（`GoalNode`/`activity_digest` 的主题关联字段粒度
核实）——这是本轮开工前必须先拍板的调研项，尚未开始。
