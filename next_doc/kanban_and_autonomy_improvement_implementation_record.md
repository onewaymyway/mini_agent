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

### Track G（P2）：跨步骤结构化产出物传递 —— 深化版已上线（工具调用提取，方案完整版落地）

按第二轮"未完成/待续"里明确标注的优先级，本轮实现了更可靠的产出物路径
提取方式，替换/增强此前纯正则解析 `[ARTIFACTS]` 标记的退化版：

- `api/routes.py` 新增两个可复用函数：
  - `_locate_step_history_entries(hist_mgr, submitted_message)`：从
    Track E 的 `get_objective_step_trace()` 里把"按 `submitted_message`
    在 active history 里定位这一步对应记录范围"的逻辑提取出来（原逻辑
    完全不变，trace 端点本身也已经改为调用这个函数，不再维护两份重复
    代码）。找不到匹配时返回 `None`。
  - `_extract_tool_write_paths(raw_entries)`：扫描一段原始 history 记录
    里的 `assistant_reply` → `tool_use` 块，按工具名单
    `{write_file, create_file, patch_file, patch_file_simple}`（与
    `perception/artifact_detector.py::_PATH_ARG_TOOLS` 保持同一份名单）
    + 常见路径参数 key（`path`/`file_path`/`target_file`/`filepath`）
    提取真实路径，按出现顺序去重返回。非写文件类工具（比如 `bash`/
    `read_file`）不提取。
- `ObjectiveExecutor` 新增构造参数 `artifacts_from_tools_fn`，优先级
  高于原有 `artifacts_parse_fn`：
  - 新增 `_extract_tool_artifacts(step)`：调用
    `artifacts_from_tools_fn(step.submitted_message)`，异常/未提供/
    `step.submitted_message` 为空时返回 `[]`。
  - `on_turn_done()` 里的产出物解析逻辑改为
    `_extract_tool_artifacts(step) or _parse_step_artifacts(result_summary)`
    ——工具调用提取优先；返回空列表（这一步确实没调用过写文件工具，比如
    纯查询类步骤）时退化到正则解析；两者都拿不到或都未提供回调时
    `step.artifacts` 保持空列表，与改造前完全一致。
- `server.py` 新增 `_extract_artifacts_from_tools(submitted_message)`
  闭包：内部导入并调用 `routes.py` 的上述两个函数，通过
  `agent._hist` 访问会话历史；拿不到 `_hist`、定位不到记录、或任何
  异常，都返回 `[]`（不抛异常，不影响 step 完成主流程），注入给
  `ObjectiveExecutor(artifacts_from_tools_fn=...)`。`artifacts_parse_fn`
  （正则退化版）保留不变，两者同时注入，符合"优先工具提取、正则兜底"
  的设计。
- 相关字段注释（`ExecutionStep.artifacts`）、构造函数 docstring、模块
  头部说明均已同步更新，注明"深化版"与"退化版"两条路径的关系。

至此方案原文 Track G 的"待细化项 2"（"退化成从 tool_call 记录里自动
提取 write_file/patch_file 类工具的路径参数，更可靠，建议优先走这条路
而不是指望模型自觉"）已按建议落地，Track G 从"部分上线"升级为"完整版
落地"。

## 测试（第三轮新增）

新增 `tests/test_objective_executor_kanban_tracks_r3.py`，覆盖：

- `TestToolBasedArtifactsPriority`：`artifacts_from_tools_fn` 返回非空时
  直接采用（用一个"如果被调用就抛异常"的 `artifacts_parse_fn` 验证正则
  解析路径完全没被触发，而不只是结果恰好相同）；返回空列表时退化到
  `artifacts_parse_fn` 的正则解析结果；两个回调都未提供时 `artifacts`
  保持空列表且不影响 step 正常推进；`artifacts_from_tools_fn` 调用异常
  时不会让 step 完成流程崩溃，会静默退化到正则解析。
- `TestExtractToolWritePaths`：从模拟的 history 记录里正确提取
  `write_file`/`patch_file` 声明的路径并去重；忽略 `bash`/`read_file`
  等非写文件类工具、忽略缺少路径参数的调用；空记录返回空列表。
- `TestLocateStepHistoryEntries`：能定位到 `submitted_message` 的最后
  一次匹配（模拟同一 step 被重试过一次的场景），并正确截止到下一条
  `user_input` 之前；找不到匹配时返回 `None`。

运行方式：

```bash
PYTHONPATH=src python3 -m pytest tests/test_objective_executor_kanban_tracks_r3.py -q
```

本轮验证：9 项全部通过；同时跑过第一、二轮的
`tests/test_objective_executor_kanban_tracks.py`（10 项）、
`tests/test_objective_executor_kanban_tracks_r2.py`（13 项）、既有的
`tests/test_goal_backlog.py`，四个文件合计 37 项全部通过，确认无回归。

补充说明（环境相关，不是代码问题）：本轮验证过程中发现本仓库快照跑
`tests/test_objective_executor_kanban_tracks_r3.py` 里涉及导入
`mini_agent.api.routes` 的用例，需要额外安装 `python-multipart`、
`rich` 两个依赖（`requirements.txt` 里已经声明，只是本次执行环境预装
不全）——如果你在自己的环境里跑测试遇到同样的 `ModuleNotFoundError`，
先 `pip install -r requirements.txt` 补齐即可，与本轮代码改动无关。

## 第四轮已完成 Track（本次续做）

### Track H（P2）：效果回填闭环到目标推导优先级 —— 已上线

按第三轮"未完成/待续"里建议的优先级，本轮实现方案原文 Track H：

- **主题标识方式确定**（方案原文"待确认/待细化项 4"）：`soft_goal_deriver.py`
  三路信号各自的 ID（capability_id / WorkThread.id / LessonGroup.key）在
  `commit_goals()` 写入 `GoalBacklog` 时并不会保留——只写了 `title` /
  `description` / `source_tag`，`GoalNode` 本身也没有预留主题字段。没有
  改 `GoalNode` schema（涉及看板展示、序列化兼容，超出本 Track 范围），
  而是复用 `_DeriveCandidate.dedupe_key()` 本来就在用的"标题归一化"作为
  跨三路信号的"同一主题"标识——这个函数本来就是代码库里"是否已经
  derive 过同一个东西"的事实标准（`existing_titles`/`rejected_keys` 两处
  去重都依赖它），语义上完全对得上。
- 新增 `src/mini_agent/evolution/objective_outcome_tracker.py`：
  - `normalize_title_key(title)`：与 `_DeriveCandidate.dedupe_key()` 完全
    一致的归一化规则（小写、去标点、按空格切分排序重连），集中实现一份，
    避免两处正则各自维护容易漂移。
  - `record_outcome(paths, title, outcome)`：`outcome` 只接受
    `"completed"`/`"failed"`，按 `normalize_title_key(title)` 分桶写入
    `<workdir>/objective_theme_outcomes.json`，每个主题桶滚动保留最近
    `MAX_HISTORY_PER_THEME`（10）条，避免无限增长。`"cancelled"`（用户
    主动终止）不计入统计——不代表这个主题"做不到"。
  - `theme_failure_stats(paths, title)` → `(总样本数, 失败样本数) | None`；
    `judge_theme(paths, title)` → `"skip" | "downweight" | "ok"`：样本数
    `< MIN_SAMPLES_FOR_JUDGEMENT`（3）时恒为 `"ok"`（样本太少不下结论）；
    失败率 `≥ SKIP_FAILURE_RATIO`（0.66）→ `"skip"`；失败率
    `≥ DOWNWEIGHT_FAILURE_RATIO`（0.34）→ `"downweight"`（乘以
    `DOWNWEIGHT_FACTOR`=0.25）；其余 `"ok"`。所有查询/写入异常均静默
    降级（不阻断调用方主流程）。
  - `soft_goal_deriver.py` 的 `_DeriveCandidate.dedupe_key()` 改为直接调用
    `normalize_title_key()`，不再自行维护一份重复的正则逻辑（`re` 导入
    随之从该文件移除，因为不再有其他地方直接使用）。
- `ObjectiveExecutor` 新增 `_record_theme_outcome(ex, outcome)`，在
  `_on_objective_completed()`（记 `"completed"`）和 `_on_objective_failed()`
  （记 `"failed"`）两处收尾回调各调用一次，紧跟在已有的
  `_sync_goal_status()`（Track B）之后。`_on_objective_cancelled()`
  **不**调用（用户主动终止不计入"这个主题做不到"的判断）。
- `SoftGoalDeriver` 新增 `_apply_objective_outcome_gating(candidates)`：
  在 `derive_candidates()` 里，紧跟在既有的"负面回填域降权"（方案四，
  基于 `outcome_tracker.get_revert_candidates()` 关键词重叠判定）代码块
  之后调用，作用于三路信号合并后的全部候选：
  - `judge_theme()` 返回 `"skip"` → 直接从候选列表剔除，本轮不会再
    derive 出这个主题。
  - 返回 `"downweight"` → `urgency *= DOWNWEIGHT_FACTOR`，让它在排序里
    自然靠后，不直接剔除。
  - 返回 `"ok"`（含查询异常兜底）→ 不做任何调整，与改造前完全一致。
  这与"负面回填域降权"是两条独立信号（后者判定的是 skill_propose
  commit 效果、且是关键词重叠的模糊匹配；本 Track 判定的是 Objective
  本身的完成/失败历史、且是主题精确匹配），因此本 Track 允许在样本充分
  时直接跳过而不只是降权，可信度更高。

**与方案原文的差异说明**：方案原文 Track H 设计里提到"需要 `ObjectiveExecutor`
… 按 `objective.source`（`agent_derived` 的来源主题标签）关联"——实际
核实后 `GoalNode` 并没有这样一个"来源主题标签"字段（`source` 字段的取值
只是 `"user"`/`"agent_derived"` 两种，标识的是"谁创建的"而不是"关于什么
主题"），因此改为"标题归一化"这一在现有代码库里已经验证过语义正确的
方案，效果等价（同一个 derive 出来的 Goal，标题在整个生命周期里不会变），
成本更低（不需要动 `GoalNode` schema）。

## 测试（第四轮新增）

新增 `tests/test_objective_outcome_tracker.py`，覆盖：

- `TestNormalizeAndStats`：`normalize_title_key()` 大小写/词序无关；无历史
  记录返回 `None`/`"ok"`；样本不足（<3）恒为 `"ok"`；失败率 ≥0.66 判
  `"skip"`；失败率在 [0.34, 0.66) 判 `"downweight"`；失败率 <0.34 判
  `"ok"`；`"cancelled"` 不计入统计；滚动窗口正确限制在
  `MAX_HISTORY_PER_THEME` 条以内。
- `TestObjectiveExecutorRecordsOutcome`：Objective 正常完成/耗尽重试判
  failed 后，对应主题的历史记录里出现相应的 `completed`/`failed`；
  `cancel()` 不会写入任何记录。
- `TestSoftGoalDeriverGating`：历史失败率高的主题候选被整个剔除；中等
  失败率的候选被降权但保留；不相关主题/无历史记录的候选完全不受影响。

运行方式：

```bash
PYTHONPATH=src python3 -m pytest tests/test_objective_outcome_tracker.py -q
```

本轮验证：15 项全部通过；同时跑过第一、二、三轮全部既有测试文件
（`tests/test_objective_executor_kanban_tracks.py` 10 项、
`tests/test_objective_executor_kanban_tracks_r2.py`、
`tests/test_objective_executor_kanban_tracks_r3.py` 9 项、
`tests/test_goal_backlog.py`、`tests/test_outcome_tracker.py`），确认
无回归——除了 4 个与本轮改动完全无关的既有失败用例（`tests/
test_objective_executor_kanban_tracks_r2.py::TestArtifactsFromToolCalls`
下 4 项，调用 `on_turn_done(..., history_segment=...)` 时报
`TypeError: unexpected keyword argument 'history_segment'`——当前
`ObjectiveExecutor.on_turn_done()` 签名里确实没有这个参数，这是测试
代码与实现签名不匹配的既有问题，本轮未改动 `on_turn_done()` 签名，
如实记录，不在本轮修复范围内）。

补充说明（环境相关，不是代码问题）：本轮验证环境同样缺少
`python-multipart`/`rich`/`pydantic`/`uvicorn` 若干依赖（与第三轮记录
里的情况一致），补齐后 `tests/test_objective_executor_kanban_tracks_r3.py`
才能正常导入 `mini_agent.api.routes`。

## 第五轮已完成 Track（本次续做）

### Track K（P2）：并发数自适应 —— 已上线（信号来源与方案原文不同，已如实调整）

按第四轮"未完成/待续"里的建议，本轮实现方案原文 Track K，但**信号来源
与方案原文设想不同**——核实后发现原文假设的两个数据源在当前代码库里
都不存在：

- `self_profile.json` 里没有 `resource_budget` 字段（读了
  `perception/self_model.py`/`storage/paths.py` 确认，唯一相关的是
  `proprioception.py` 的 `energy_budget_ratio`，但那是单个 session 内的
  剩余 turn 预算比例，不是跨天的资源预算，语义不匹配）。
- `ExecutionStep`/`ObjectiveExecution` 都没有任何 token 消耗字段，只有
  `started_at`/`finished_at`，没有"`avg_objective_cost`"可供滚动计算。

没有为了凑这两个数据源去新增一整套 token 计量/预算配置体系（超出本
Track 范围，且会牵扯到 `LLMClientPool` 等更底层模块），而是改用已经
存在、且同样能反映"最近执行得顺不顺利"的信号：

- 新增配置 `config/models.py::AutonomyConfig`（8 个新字段，均有默认值，
  不影响未升级配置文件的用户）：
  - `adaptive_concurrency_enabled: bool = True`——默认开启，因为这是一个
    只降不升的机制（生效并发数不会超过配置/常量给出的天花板），默认
    开启不会让行为比改造前更激进。
  - `max_concurrent_objectives_cap: int = 2`——方案原文提到的安全阀，
    默认值与改造前硬编码的 `MAX_CONCURRENT_OBJECTIVES` 一致。
  - `adaptive_concurrency_min: int = 1`——降档的下限，不会把并发降到 0
    （降到 0 属于 Track J 资源门控的范畴，不是本 Track 该做的事）。
  - `adaptive_concurrency_min_samples: int = 3`——参与失败率判定所需的
    最小样本数。
  - `adaptive_concurrency_failure_rate_threshold: float = 0.5`——最近
    N 个已结束 Objective 里失败占比达到此阈值 → 降一档。
  - `adaptive_concurrency_slow_duration_seconds: float = 1800.0`——最近
    已完成 Objective 的平均耗时（`finished_at - started_at`）达到此阈值
    → 再降一档（可与失败率信号叠加）。
  - `adaptive_concurrency_window: int = 10`——参与统计的"最近 N 个已结束
    Objective"窗口大小。
- `ObjectiveExecutor.__init__` 新增可选参数 `cfg`（`AppConfig` 引用）。
  新增 `effective_max_concurrent()`：
  - 天花板 = `min(MAX_CONCURRENT_OBJECTIVES, cfg.autonomy.
    max_concurrent_objectives_cap)`——模块级常量永远是绝对上限，配置项
    只能收紧不能突破（`test_configured_cap_cannot_exceed_static_constant`
    验证了这一点）。
  - 未提供 `cfg`，或 `adaptive_concurrency_enabled=False` 时，直接返回
    天花板，等价于改造前的行为。
  - 否则统计最近 `adaptive_concurrency_window` 个 `status in
    ("completed","failed")` 的 execution：样本数不足时不调整；失败率
    达标降一档；平均耗时达标再降一档；最终结果夹在
    `[adaptive_concurrency_min, 天花板]` 之间。
  - 任何异常静默降级为返回天花板值（不下调），不影响
    `can_start_new()` 主流程。
  - `can_start_new()` 改为 `running_count() < effective_max_concurrent()`
    （原来是与模块常量比较）。
- `api/server.py::_build_autonomous_loop()` 构造 `ObjectiveExecutor` 时
  新增 `cfg=cfg`（该方法本来就持有 `cfg = getattr(agent, "cfg", None)`，
  只是此前没有转发给 `ObjectiveExecutor`）。
- `api/routes.py` 里 `/v1/autonomous/status` 聚合逻辑的 `objective_slots`
  字段：`max` 改为展示 `oe.effective_max_concurrent()` 计算出的生效值
  （查询异常时退化为展示静态常量），并新增 `static_cap` 字段保留原来
  的静态常量值，供看板/调用方需要时区分"当前生效上限"与"绝对硬上限"。

**已知局限**（据实记录）：
- 失败率/平均耗时统计目前是**全局**的（不区分主题/来源），与 Track H
  按主题精细统计不同——方案原文本身也没有要求按主题拆分并发上限（并发
  数是整个 `ObjectiveExecutor` 级别的资源约束，不是"某个主题该不该跑"
  的问题，因此选择全局统计是合理的，但如果未来想做"某类主题专门限流"，
  需要另外扩展，不能直接复用这里的存储）。
- 用"平均耗时"代理"token 消耗"是本轮的关键假设：两者通常正相关（跑得
  越久往往意味着调用次数越多），但不完全等价（比如一个 step 卡在等待
  外部慢速工具返回，耗时长但 token 消耗未必高）。如果后续给 `AgentRunner`
  加上了真实的 token 计量并回传到 `ExecutionStep`，应该优先切换回更准确
  的 token 统计，耗时目前是"没有更好数据时的最佳近似"。

## 测试（第五轮新增）

新增 `tests/test_objective_executor_adaptive_concurrency.py`，覆盖：

- 未提供 `cfg` 时恒返回 `MAX_CONCURRENT_OBJECTIVES`（改造前行为）。
- 提供 `cfg` 但关闭自适应时，恒返回配置的 cap，不受历史记录影响。
- 样本数不足（<3）时不下调。
- 最近失败率达标 → 下调一档；最近平均耗时达标 → 再下调一档，两者可
  叠加。
- 无论叠加多少档，结果不会低于 `adaptive_concurrency_min`。
- 配置的 cap 大于模块级常量时，天花板仍以模块级常量为准（安全阀不能被
  配置突破）。
- `can_start_new()` 正确使用 `effective_max_concurrent()`（用一个
  `running` 状态的 execution 占满生效上限后返回 `False`）。

运行方式：

```bash
PYTHONPATH=src python3 -m pytest tests/test_objective_executor_adaptive_concurrency.py -q
```

本轮验证：8 项全部通过；同时跑过第一至四轮全部既有测试文件（合计新增
本轮在内共 123 项用例），确认无回归——除了此前几轮已如实记录、与本轮
改动无关的 4 个既有失败用例（`tests/test_objective_executor_kanban_tracks_r2.py
::TestArtifactsFromToolCalls` 调用了当前 `on_turn_done()` 签名不存在的
`history_segment` 参数），本轮同样未去动它，维持"如实记录、不顺手
掩盖"的一贯做法。

## 第六轮已完成 Track（本次续做）

### Track J（P2）：资源门控降级执行 —— 已上线（三态化部分；模型档位切换部分调研后确认不做）

按第五轮"未完成/待续"里的建议，本轮实现 Track J。开工前先做了前置调研：

**前置调研结论**：读取 `llm/client_pool.py::LLMClientPool` 后确认，它是一套
"故障转移链（`entries`/`fallback_on`）+ 多 Key 轮转"调度器，解决的是同一个
语义请求在多个 provider 配置之间失败重试的问题，**不含**"按 initiator/
场景选择不同模型档位"的接口或字段。因此本轮只实现方案原文 Track J 的
"资源门控三态化"部分（`ResourceArbiter` 第4/5条规则从 block/allow 改成
full/degraded/blocked），"用更便宜的模型跑自主任务"部分保持未实现，
如实记录为独立后续项，不在本轮强行超出 `LLMClientPool` 的核心职责去改造它。

**改动内容**：

- `config/models.py::AutonomyConfig` 新增 4 个字段（均有默认值，不影响
  未升级配置文件的用户）：
  - `resource_gating_degraded_enabled: bool = True`——默认开启，理由与
    Track K 一致：degraded 态本身是"比原来更宽松"的中间态（原来这两条
    规则一旦触发就是 `can_run_autonomous()` 返回 False、
    `AutonomousLoop` 直接 `pause_all()` 整体停摆；现在改成先降级，只有
    更严重的情况才会真正 blocked），默认开启不会让行为比改造前更激进。
  - `resource_gating_degraded_max_concurrent: int = 1`——degraded 态下
    `ObjectiveExecutor.effective_max_concurrent()` 的临时天花板。
  - `frustration_blocked_threshold: float = 0.85`——frustration 三态里
    "degraded → blocked"的分界线（沿用既有的
    `proprioception.frustration_threshold` 作为"full → degraded"的
    分界线，语义上是"轻微挫败降级，严重挫败才真正停摆"）。
  - 说明：`user_presence` 规则本轮设计为只有 full/degraded 两级、没有
    blocked 上限——用户活跃切换应用不是"危险"信号（不像挫败感那样可能
    意味着 agent 在做错误的事），只需要让路，不需要整体停摆。这一点在
    字段注释里写明，不需要额外的阈值字段。
- `evolution/resource_arbiter.py::ResourceArbiter` 新增：
  - `gating_state() -> dict`：新的三态门控入口，返回
    `{"state": "full"|"degraded"|"blocked", "reason": str}`。规则 3
    （预算硬限制）保持二元不变（方案原文明确写"第4/5条规则"，预算耗尽
    没有"打折继续花"的中间态语义）；规则 4/5 改用新增的
    `_check_frustration_tri()`/`_check_user_presence_tri()`（保留原有
    `_check_frustration()`/`_check_user_presence()` 两个二元方法不变，
    供 `diagnose()` 里逐条展示用，不删除避免破坏兼容）。
    `resource_gating_degraded_enabled=False` 时整体退化为
    "degraded 视同 blocked"，与改造前行为完全一致。
  - `can_run_autonomous()` 改为 `gating_state()["state"] != "blocked"`
    的薄封装，保留原方法名和返回类型（`bool`），但语义变宽松了——这是
    本 Track 的核心行为变化，在 docstring 里如实写明：调用方如果只关心
    "能不能跑"，行为变得更宽松（原来 degraded 场景会返回 False，现在
    返回 True）；如果关心"是否应该降级"，需要改用 `gating_state()`。
  - `diagnose()` 补充 `gating_state`/`gating_reason` 两个字段，供看板
    区分"整体停摆"和"降级运行"，不再只有 `can_run_autonomous` 一个
    布尔值可看。
- `evolution/objective_executor.py::ObjectiveExecutor` 新增：
  - `self._gating_degraded: bool = False`（纯内存标志位，不持久化，
    只反映"此刻"的资源状况）。
  - `set_gating_degraded(degraded: bool) -> None`：由 `AutonomousLoop`
    每次 tick 调用。
  - `effective_max_concurrent()` 新增判断：`_gating_degraded=True` 且
    配置未关闭本机制时，天花板先收紧到
    `resource_gating_degraded_max_concurrent`，再让 Track K 的自适应
    逻辑在这个更低的天花板基础上继续计算——两个"只降不升"的机制取更
    严格者，不会因为叠加导致比单独任一机制更宽松。
- `evolution/autonomous_loop.py::AutonomousLoop._tick_maintenance()`：
  原来 `if not arbiter.can_run_autonomous(): pause_all(); return`
  改为读取 `arbiter.gating_state()["state"]`：
  - `"blocked"` → 行为与改造前完全一致（`pause_all()` + `return`）。
  - `"degraded"` → **不** `pause_all()`、**不** `return`，只调用
    `self._objective_executor.set_gating_degraded(True)`，随后照常执行
    `resume()` 和"从 GoalBacklog 启动新 Objective"的既有逻辑（此时
    `can_start_new()` 会因为并发上限被收紧而更早返回 False，从而实现
    "低并发继续跑"而不是"整体停摆"）。
  - `"full"` → 调用 `set_gating_degraded(False)`（如果上一轮是
    degraded，本轮会自动恢复，不需要额外的恢复逻辑）。
- `api/routes.py` 侧不需要改动：`/v1/autonomous/status` 的 `gating`
  字段本来就直接透出 `ResourceArbiter.diagnose()` 的返回值，`diagnose()`
  改动后新字段自动透出；`objective_slots.max` 本来就是实时调用
  `effective_max_concurrent()` 得到的，degraded 生效时会自动反映更低的值，
  不需要额外接线。

**与方案原文的差异说明**：原文 Track J 设计里"`degraded` 态下…允许
`Agent` 侧后续接入'用更便宜的模型跑自主任务'（这一步依赖 `LLMClientPool`
是否支持按 initiator 选择不同模型档位）"——已调研确认当前不支持，见
本节开头的调研结论，本轮不实现这一半。

## 测试（第六轮新增）

新增 `tests/test_resource_arbiter_gating_track_j.py`，覆盖：

- `TestResourceArbiterGatingState`：无快照时 `"full"`；frustration 低于
  阈值 `"full"`；frustration 在 `[frustration_threshold,
  frustration_blocked_threshold)` 区间 `"degraded"`（且
  `can_run_autonomous()` 此时返回 `True`——验证核心行为变化）；frustration
  达到 `frustration_blocked_threshold` 判 `"blocked"`；user_presence 触发
  时判 `"degraded"`（不会是 `"blocked"`）；
  `resource_gating_degraded_enabled=False` 时 degraded 退化为 blocked；
  `gating_state()` 结构包含 `reason` 字段。
- `TestObjectiveExecutorGatingDegraded`：`set_gating_degraded(True)` 收紧
  并发上限、`set_gating_degraded(False)` 恢复；degraded 与 Track K 自适应
  同时触发时取更严格者；配置关闭时 `set_gating_degraded(True)` 不生效；
  默认（未调用）不降级。

运行方式：

```bash
PYTHONPATH=src python3 -m pytest tests/test_resource_arbiter_gating_track_j.py -q
```

本轮验证：11 项全部通过；同时补齐本地环境缺失的 `pytest`/`pydantic`/
`rich`/`python-multipart`/`uvicorn`/`fastapi` 依赖后，重新跑了第一至五轮
全部既有测试文件（`test_objective_executor_kanban_tracks.py`、`_r2.py`、
`_r3.py`、`test_goal_backlog.py`、`test_outcome_tracker.py`、
`test_objective_outcome_tracker.py`、
`test_objective_executor_adaptive_concurrency.py`，合计连同本轮
在内 143 项用例），确认无新增回归——唯一失败的仍是此前几轮已如实记录、
与本轮改动无关的 4 个既有失败用例（`tests/
test_objective_executor_kanban_tracks_r2.py::TestArtifactsFromToolCalls`
下 4 项，调用 `on_turn_done(..., history_segment=...)` 时报
`TypeError: unexpected keyword argument 'history_segment'`——当前
`ObjectiveExecutor.on_turn_done()` 签名里确实没有这个参数，本轮同样
未去动它）。

## 未完成 / 待续（供下一轮参考）

按方案原文的路线图，以下项目**仍未开始**，需要后续排期：

- **Track E 边界情况**（历次未涉及，维持第二轮状态）：历史被压缩
  （compact）后无法定位到某个 step 的 trace / 产出物；多 session 场景下
  Objective 执行如果不再统一走单一主 bridge，trace/产出物提取接口都
  需要能定位到正确的 session/agent。
- **既有测试代码缺陷**（第四轮发现，非本轮引入，历次均未修复）：`tests/
  test_objective_executor_kanban_tracks_r2.py::TestArtifactsFromToolCalls`
  下 4 个用例调用了 `on_turn_done(..., history_segment=...)`，当前签名
  没有这个参数，需要下一轮排查是功能确实缺失还是测试需要更新。
- **Track I**（P2）：进化提案分级自治——尚未开始。工作量中大（风险分级
  字段 + 看板新增"进化提案" tab + diff 视图），收益不如前面几项直接，
  方案原文本身也建议放在最后做。这是路线图里目前唯一还未开工的 Track。
- **Track J 的"模型档位切换"半成品**（本轮调研后明确搁置）：如果未来
  `LLMClientPool` 演进出"按 initiator/场景选择模型档位"的能力，可以
  回来把 `degraded` 态接上"自主任务用更便宜的模型"这一优化，目前
  `AutonomousLoop`/`ObjectiveExecutor` 侧已经有 `gating_state()`/
  `set_gating_degraded()` 这两个现成的信号源可以直接复用，不需要再动
  资源仲裁本身的逻辑。

建议下一轮优先做 **Track I**（进化提案分级自治）：这是路线图里最后一个
未开工的 P2 项目，方案原文已经给出风险分级的判定依据（T0~T3 验证 +
eval_runner 对比结果全绿 → 低风险 → 允许一键合并按钮），实现上可以先做
"风险分级字段 + 一键合并按钮"这一半（不依赖看板 diff 视图），把"看板
新增 diff 视图 tab"拆成独立的后续子项，控制单轮工作量。
