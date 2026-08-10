# Goal 执行规范自动生成 + 用户确认机制 —— 实施记录（Stage 1 + Stage 2 + Stage 3）

对应设计文档：`next_doc/goal_execution_spec_generation_plan.md`

本记录覆盖 Stage 1（后端核心能力）+ Stage 2（`overall_completion_criteria`
驱动的一次性 Goal 整体关闭判断）+ Stage 3（看板 UI 最小可用版本）的实施
情况，不重复方案本身的设计论证，只记录"最终落地成什么样、和方案哪里
不同、还差什么"。

## 1. 已实施

### 1.1 核心数据模型 + 存储（对应方案 §2/§4）

新增 `src/mini_agent/perception/goal_execution_spec.py`：

- `GoalExecutionSpec` + `Deliverable`/`HandoffField`/`SubDirectory`/
  `Criterion` 四个子结构，字段与方案 §2 schema 一一对应。
- `load_spec()`/`save_spec()`/`delete_spec()`：独立文件
  `.agent/goal_execution_specs/<goal_id>.json`，不进 `goals.json`。
- `GoalNode` 新增 `execution_spec_confirmed: bool = False` 指针字段
  （`to_dict`/`from_dict` 已同步更新，旧数据反序列化时缺省为 `False`，
  不需要迁移脚本）。
- `render_summary_for_user()`（协商展示）/ `render_prompt_block()`
  （拼进子 Objective description 的格式化文本，含 handoff 字段的
  ` ```handoff\n{...}\n``` ` 填空模板提示）。
- `is_empty()`：全部字段为空 == 等价于"沿用 output_workspace.py 通用
  行为"，供消费方判断是否要拼接任何文字。

### 1.2 生成器 `GoalExecutionSpecBuilder`（对应方案 §3）

- `build_draft(goal_id, goal_title, goal_description, schedule=,
  task_template=, template_id=, history_manifests=)`：单一入口覆盖
  方案 §3 的三种输入源——`template_id` 为空即"完全从零生成"，非空即
  "从模板起步"（骨架作为 few-shot 拼进 prompt）；`history_manifests`
  非空时额外把该 Goal 过去若干轮的实际产出摘要拼进 prompt。
- `revise(prior_spec, feedback, locked_fields=)`：字段级锁定（对应
  方案 §6.2）——prompt 里明确要求 LLM 保留锁定字段，且**额外做了一层
  代码级强制覆盖**（不完全依赖 LLM 是否听话）：锁定字段的最终值直接用
  `prior_spec` 对应字段覆盖 LLM 输出，保证"锁定"是硬约束。
- `confirm(spec)`：`confirmed=True` + `confirmed_at`。
- 失败兜底：`build_draft()` 解析失败返回全字段为空的草稿 +
  `generation_error` 说明；`revise()` 解析失败**保留上一版内容**（不清空，
  区别于 `build_draft()` 的空白兜底——修订失败不该让用户已经确认过的字段
  凭空丢失），同样附 `generation_error`，`confirmed` 重置为 `False`。
- `builder_mode` 三态接口（`llm`/`agent`/`auto`）已在配置和构造函数里
  预留，但**第一版只实现了 `llm` 路径**（裸单轮 `LLMHelper.ask()`）；
  `agent`/`auto` 当前效果等价于 `llm`——见 §3 未实施部分。

### 1.3 模板库（对应方案 §7）

`src/mini_agent/perception/goal_execution_spec_templates/*.json`，覆盖
方案列出的全部 5 类：`periodic_report`/`data_collection`/
`monitoring_patrol`/`codebase_maintenance`/`research_exploration`。
`list_templates()`/`load_template()` 提供读取接口。方案里"自动匹配模板"
的关键词规则未实现（第一版只支持显式传 `template_id`，见 §3 未实施）。

### 1.4 §5.1 轻量核对

`soft_check_manifest(spec, manifest)`：纯文件名/key 字符串匹配（
`deliverables.naming_pattern` 是否出现在 `manifest.artifacts` 文件名里；
`handoff_fields.key` 是否出现在 ` ```handoff``` ` JSON 块里），不做语义
判断，返回 `{missing_deliverables, missing_handoff_keys, ok}`。

### 1.5 消费方接入（对应方案 §5）

- `evolution/goal_cron_bridge.py`：`_fire_goal_cycle()` 在
  `_append_output_workspace_context()` 之后新增
  `_append_execution_spec_context()`——未确认（`execution_spec_confirmed=
  False`）完全不读规范文件，确认后把 `render_prompt_block()` 拼进
  description，并调用 `_soft_check_execution_spec()` 对上一轮 manifest
  做核对：不匹配时追加一句软提示；`soft_check_miss_streak` 连续达到
  `soft_check_alert_after_cycles`（默认 3）时，在 `GoalNode.progress_notes`
  末尾追加一条"⚠️ 建议复查执行规范"备注（字符串拼接追加，不覆盖 agent
  自己写的进展记录），并把 `soft_check_alerted` 置位避免重复提示；一旦
  某轮重新匹配上，计数器和标记都会清零。
- `perception/goal_backlog.py`：`add_objectives_for_goal()`（一次性 Goal
  路径）对称新增 `_append_execution_spec_prompt_block()`，逻辑相同但**不
  做 §5.1 核对**——一次性 Goal 的子 Objective 之间不是"轮次"关系，"连续
  N 轮"语义不适用，见该函数 docstring 的说明。

### 1.6 配置项（对应方案 §8）

`config/models.py` 新增 `GoalExecutionSpecConfig`（独立配置块，挂在
`AppConfig.goal_execution_spec`），已注册进 `param_registry.py`/
`config_catalog.py`/`config/loader.py`/`config/__init__.py` 四处，
`load_config()` 验证可正常读取默认值：

| 字段 | 默认值 |
| --- | --- |
| `enabled` | `true` |
| `builder_mode` | `"auto"`（当前效果等价于 `"llm"`，见上） |
| `builder_model`/`builder_provider` | `None`（回退主模型） |
| `prompt_on_recur` | `true`（第一版看板 UI 未实施，暂无实际读取方） |
| `soft_check_enabled` | `true` |
| `soft_check_alert_after_cycles` | `3` |

### 1.7 CLI（对应方案 §6.4）

`cli/commands/goals.py` 新增 `/agent goals spec generate/confirm/show`：

- `spec generate <goal_id> [--template <id>] [--from-history]`：调用
  `GoalExecutionSpecBuilder.build_draft()` 并落盘（未确认）。
  `--from-history` 时读取该 Goal 最新一轮 `manifest.json` 作为
  `history_manifests` 输入（第一版只取最新一轮，不是方案 §3 描述的
  "过去若干轮"，见 §3 未实施部分）。
- `spec confirm <goal_id>`：加载已有草稿、`confirm()`、落盘，并把
  `GoalNode.execution_spec_confirmed` 置 `True`。
- `spec show <goal_id>`：打印 `render_summary_for_user()`。
- `/agent goals recur` 命令尾部新增一句提示：没有已确认规范时提示可以
  先 `spec generate`，不强制依赖（对应方案 §6.4 最后一句）。
- Stage 2 追加了 `spec close-check <goal_id>` 子命令，见 §2.2。

### 1.8 测试

- `tests/test_goal_execution_spec.py`（新增，22 个用例）：数据模型
  往返、存储读写（含损坏文件/文件缺失）、模板库、Builder 的
  `build_draft`/`revise`（含字段锁定强制覆盖、失败兜底两条路径）、
  `soft_check_manifest`/`get_handoff_data`。
- `tests/test_goal_cron_bridge.py`（追加 `TestExecutionSpecIntegration`，
  3 个用例）：未确认不影响 description、确认后正确拼入、连续 4 轮未命中
  触发"建议复查"备注。
- `tests/test_goal_output_directory_onetime.py`（追加
  `TestOnetimeGoalExecutionSpecInjection`，2 个用例）：一次性 Goal 侧的
  确认/未确认对称行为。
- 全部新增用例 + 既有 `test_goal_backlog.py`/`test_goal_cron_bridge.py`/
  `test_goal_execution_fairness*.py` 回归通过（本地环境下 `test_goal_mode.py`
  等一批用例因缺少 `json_repair`/`fastapi` 等无关依赖而报
  `ModuleNotFoundError`，与本次改动无关，不在本次验证范围内当作基线）。

## 2. Stage 2 已实施：`overall_completion_criteria` 驱动的一次性 Goal
   整体关闭判断（对应方案 §5 第二段 / Stage 1 未实施清单第 5 项）

### 2.1 判定器

`perception/goal_execution_spec.py::GoalExecutionSpecBuilder` 新增
`evaluate_overall_completion(goal_title, goal_description, spec, children,
manifests)`：

- 独立的一次性 LLM 调用（专用 system prompt
  `prompts/system/goal_overall_completion_judge.md` + user prompt
  `prompts/user/goal_overall_completion_request.md`，不复用生成草案用的
  `_run_llm()`/`goal_execution_spec_builder.md`，两种任务性质不同，分开
  演进），对照 `spec.overall_completion_criteria`、全部子 Objective 的
  标题+终态、该 Goal 历史全部轮次的 manifest（产出文件+备注），逐条核查
  后输出 `{"decision": "close"|"continue", "reasoning": str}`。
- 解析失败/LLM 调用失败时保守返回 `continue`（附错误说明）——"不确定时
  绝不主动关闭 Goal"，与方案"确认优先于生效"的哲学一致，关闭动作本身
  也需要"证据充分"才触发，不是超时/异常时的默认行为。
- `evolution/output_workspace.py` 新增 `read_all_manifests(base_dir)`：
  读取某个 Goal 目录下**全部**轮次（`run_%04d/`）的 `manifest.json`（与
  只读最新一轮的 `read_latest_manifest()` 互补），供本判定器拿到"这个
  Goal 到目前为止一共产出过什么"的完整证据，而不是只看最后一轮。

### 2.2 触发入口与前置条件

`perception/goal_backlog.py::GoalBacklog.maybe_close_goal_by_overall_
criteria(goal_id, cfg=None)`：

- 前置判断（不满足时直接返回 `None`，不消耗任何 LLM 调用）：Goal 存在
  且是 `level="goal"`；非 `recurring`（周期性 Goal 不适用"整体关闭"这个
  概念，与方案 §2 一致）；`status == "active"`；存在子节点且**全部**子
  节点都已进入终态（`completed`/`failed`/`cancelled`，只要有一个还是
  `active`/`paused` 就直接返回）；`execution_spec_confirmed=True` 且独立
  文件里 `spec.confirmed=True`；`spec.overall_completion_criteria` 非空。
- 前置条件全部满足后才调用 `evaluate_overall_completion()`：`decision==
  "close"` 时 `set_status(goal_id, "completed")` + 追加一条
  `✅ 整体完成判定：<reasoning>` 的 `progress_notes`；`"continue"` 时
  goal 状态不变，追加一条 `ℹ️ 整体完成判定（暂不关闭）：<reasoning>` 的
  `progress_notes`（便于用户在看板/CLI 回看"为什么还没关闭"，不是静默
  丢弃这次判定结果）。
- 只读前置判断与 LLM 调用都在锁外完成，与 `goals_missing_objective()`
  的"读写分离"原则一致，只有最终 `set_status()`/`append_progress_note()`
  落盘时各自短暂加锁；`cfg` 不传时退化为 `load_config(project_root)`
  现读一份。
- 任何环节异常都被捕获、`log_exception` 记录后返回 `None`，不影响调用方
  主流程——这是可选增强，不是必经关卡。

`evolution/objective_executor.py::ObjectiveExecutor` 新增
`_maybe_close_parent_goal(ex)`，只在 `_on_objective_completed()`（正常
完成路径）末尾调用一次：查找该 Objective 的 `parent_id`，调用
`goal_backlog.maybe_close_goal_by_overall_criteria(parent_id, self._cfg)`。
不在 `_on_objective_failed()`/`_on_objective_cancelled()` 路径调用——与
方案 §5 第二段"在最后一个子 Objective **完成**时"的表述一致，失败/取消
路径即使导致全部子节点凑齐终态，也不在那两条路径上额外触发一次判断
（下次任何其它子节点走完成路径收尾时，或用户手动干预后，仍会自然触发）。

`cli/commands/goals.py` 新增 `/agent goals spec close-check <goal_id>`
子命令，直接调用同一个 `GoalBacklog.maybe_close_goal_by_overall_
criteria()`，供用户在不新增子 Objective 的情况下手动（重新）触发一次
判定（比如上一次自动判定结果是"暂不关闭"，用户后续补充了材料想重判）；
Goal 非 `active` 时提前给出提示、不调用该方法，其余前置条件判断与自动
路径完全一致。

### 2.3 测试

`tests/test_goal_overall_completion.py`（新增，12 个用例）：

- `evaluate_overall_completion()`：`close`/`continue` 两种正常解析、
  解析失败兜底为 `continue`。
- `maybe_close_goal_by_overall_criteria()`：5 种前置条件不满足的场景各
  返回 `None`（recurring Goal / 子节点未全部终态 / 规范未确认 /
  `overall_completion_criteria` 为空）+ `close`/`continue` 两种正常判定
  路径的状态与 `progress_notes` 断言。
- `output_workspace.read_all_manifests()`：多轮 manifest 按目录名顺序
  读出、目录不存在时返回空列表。
- `ObjectiveExecutor` 端到端集成：唯一子 Objective 走 `on_turn_done()`
  正常完成后，`_maybe_close_parent_goal()` 被自动触发，父 Goal 最终被
  置为 `completed`（mock `GoalExecutionSpecBuilder` 返回 `close`）。
- 全部新增用例 + 既有 `test_goal_execution_spec.py`/`test_goal_cron_
  bridge.py`/`test_goal_backlog.py`/`test_goal_output_directory_onetime.py`/
  `test_objective_outcome_tracker.py`/`test_goal_execution_fairness*.py`
  回归通过（共 98 个用例）。

`tests/test_goals_spec_close_check_cli.py`（新增，5 个用例）：`spec
close-check` 命令的 Goal 不存在报错、非 active 提前跳过（不调用
`maybe_close_goal_by_overall_criteria`）、`None`/`closed`/`kept_open`
三种返回值对应的提示文案。

全部改动累计新增测试 17 个，与既有测试合计 103 个用例回归通过。

## 3. Stage 3 已实施：看板 UI 最小可用版本（对应方案 §6.1/§6.2/§6.3，
   Stage 1/2 未实施清单第 1 项）

### 3.1 REST 端点（`api/routes.py`）

在既有"周期性 Goal 绑定/解绑/跳过"三个端点旁新增一组"Goal 执行规范"端点，
直接复用 `perception/goal_execution_spec.py` 里 CLI 已经在用的同一套
`GoalExecutionSpecBuilder`/`load_spec`/`save_spec`，行为与 CLI 对称：

| 端点 | 对应 CLI | 说明 |
| --- | --- | --- |
| `GET /goal_execution_spec_templates` | （模板库摘要，CLI 无直接对应） | 供看板"起草方式"下拉框 |
| `GET /goals/{id}/execution_spec` | `spec show` | 没生成过时返回 `{"spec": null}`，不是 404 |
| `POST /goals/{id}/execution_spec/generate` | `spec generate` | body 支持 `schedule`/`task_template`/`template_id`/`from_history` |
| `POST /goals/{id}/execution_spec/revise` | （CLI 无对应，仅看板迭代场景使用） | body: `feedback`（必填）、`locked_fields` |
| `POST /goals/{id}/execution_spec/confirm` | `spec confirm` | 确认与"设为周期性"解耦成两次独立请求，任一失败不会让另一半处于不一致状态 |
| `POST /goals/{id}/execution_spec/close_check` | `spec close-check` | 非 `active` 时直接返回 `{"outcome": null, "reason": "..."}`，不算错误 |

新增 `_goal_backlog_only(request)` 辅助函数，与既有 `_goal_backlog_and_
scheduler()` 的区别是不解析 `CronScheduler`——执行规范相关端点都不涉及
cron job 读写，复用带 scheduler 解析的版本会引入无关的失败面（测试环境/
精简嵌入场景下 `http_server.autonomous_loop` 可能缺失）。两者共用同一份
`project_root`/`AgentPaths`/`GoalBacklog` 解析逻辑，只是要不要多解析一次
scheduler 的区别。

### 3.2 `AgentClient` 封装（`apps/mini_agent_kanban/client.py`）

新增 `execution_spec_templates()`/`get_execution_spec()`/
`generate_execution_spec()`/`revise_execution_spec()`/
`confirm_execution_spec()`/`close_check_execution_spec()`，与既有
`recur_goal`/`unrecur_goal` 等方法同样的"失败返回带 `_error` 字段的 dict，
不抛异常"约定。

### 3.3 看板 UI（`apps/mini_agent_kanban/app.py`）

新增 `_render_goal_execution_spec_widget(client, goal_id, key_prefix,
on_confirm_extra=None)`，实现方案 §6.1/§6.2 的"生成草稿 → 反馈迭代（字段
级锁定）→ 确认/放弃"主线，草稿缓存在 `st.session_state` 里跨 rerun 保留
（不在每次 rerun 时重新触发 LLM 调用）；打开时若服务端已有未确认草稿或
已确认规范会自动预载，不需要用户重新点一次"生成"。三处接入：

- **"⏰ 周期性设置" expander**（对应 §6.1）：
  - 未绑定周期性分支：规范草稿区块渲染在"设为周期性"表单**之上**，规范
    生成/确认与"设为周期性"表单提交解耦成两个独立操作——不管规范是否
    已确认，"设为周期性"表单随时可以直接提交（对应方案"跳过，不生成
    规范"的非目标声明，只是这里用"两个区块共存、互不阻塞"替代了原方案
    描述的"表单内嵌一个跳过按钮"，效果等价：用户可以只点规范区块、只点
    绑定表单，或两者都点）。
  - 已绑定周期性分支：追加同一个规范区块（对应方案"已绑定但从未生成过
    规范的既有 Goal，追加一个「📋 生成执行规范」按钮"）。
  - 同一分支内追加「🔁 手动重判整体是否可以关闭」按钮，对应 CLI `spec
    close-check`，处理三种返回（`closed`/`kept_open`/`null`）对应的提示
    文案。
- **"➕ 新建目标" expander**（对应 §6.3）：表单内新增「同时生成一次性
  Goal 的执行规范」复选框，默认不勾选。创建成功后若勾选，把新 Goal id
  存入 `st.session_state["_ges_pending_new_goal"]`（创建请求本身只返回
  新 Goal 的 id，规范生成需要在拿到 id 之后才能调用，所以确认区块渲染在
  表单外、创建之后的一次 rerun 里，而不是表单内联）；确认或用户主动收起
  后清除该 session_state 标记。
- **字段级锁定**（对应 §6.2）：5 个顶层 section（产出物/跨轮传递/子目录/
  每轮标准/特殊约束）各一个 `🔒` 复选框，勾选状态随 `revise()` 请求一并
  提交为 `locked_fields`，服务端沿用 Stage 1 已实现的"prompt 提示 + 代码
  级强制覆盖"双重保障。

### 3.4 与方案的简化取舍（Stage 3 范围内）

- **不做方案 §6.1 描述的"每节可编辑文本框，提交时按行拆分"**：改为"展示
  只读摘要 + 补充意见文本框 + 字段级锁定"，用户对某个字段不满意时通过
  反馈文字描述、锁定其余满意的字段来迭代，而不是直接在文本框里逐行改写
  结构化字段。理由：直接编辑要求前端把 5 种不同结构（`Deliverable`/
  `HandoffField`/`SubDirectory`/`Criterion`/纯字符串）分别设计一套"编码
  成单行文本 ↔ 解析回结构化字段"的双向转换，且编辑后如果不点"重新生成"
  直接点"确认"，服务端当前还是按最后一次 `generate`/`revise` 返回的
  版本落盘确认——文本框里的手工编辑不会被保存，容易让用户误以为编辑生效
  了。改为"反馈驱动迭代"完全避开这个陷阱，但确实降低了"精确控制某一个
  字段具体怎么改"的能力，留作后续差异。
- **不做 §6.1 描述的"差异高亮"**：`revise()` 前后的草稿摘要都是重新整段
  渲染，不做新增/删除/改写条目的高亮对比。
- **不做"📄 从模板重新起草"独立按钮**：模板选择只出现在"生成第 1 版草稿"
  这一步（下拉框 + 生成按钮），已有草稿后如果想换模板，需要先「❌ 放弃
  草稿」回到"未生成"状态重新选模板生成——比方案描述的"随时可以从模板
  重新起草而不丢弃当前进度"少一步便利性，但实现更简单、不需要额外维护
  "草稿 A 是否源自模板 B"这类隐藏状态。
- **不做"从执行历史反推"在看板侧的默认预填**：看板生成按钮固定不传
  `from_history`；`from_history=True` 目前只有 CLI `--from-history` 参数
  在用，REST 端点虽然已经支持透传这个字段（`generate_execution_spec()`
  客户端方法也留了 `from_history` 参数），但看板 UI 没有暴露对应的
  勾选框——history_manifests 输入源第一版就绪，只是这次没做前端开关。

### 3.5 测试

`tests/test_goal_execution_spec_kanban_routes.py`（新增，11 个用例）：
沿用 `tests/test_kanban_config_routes.py` 的最小 FastAPI app 测试模式，
不拉起完整 `HttpServer`；覆盖模板列表端点、未生成时 `GET` 返回
`{"spec": null}`、`generate`/`revise`/`confirm`/`close_check` 四个端点的
正常路径（含落盘验证）与各自的前置条件报错（Goal 不存在、没有草稿就
revise/confirm、`feedback` 为空、Goal 非 active 时 `close_check` 短路）。
`GoalExecutionSpecBuilder.build_draft`/`revise`、`GoalBacklog.maybe_close_
goal_by_overall_criteria` 均打桩，只验证路由层的参数透传/状态码/错误
处理，不重复 `test_goal_execution_spec.py` 已经覆盖的生成器内部逻辑。

全部新增用例 + 既有 `test_goal_execution_spec.py`/`test_goal_cron_
bridge.py`/`test_goal_backlog.py`/`test_goal_overall_completion.py`/
`test_goals_spec_close_check_cli.py`/`test_kanban_config_routes.py`/
`test_goal_output_directory_onetime.py`/`test_goal_execution_fairness.py`
回归通过（共 100 个用例）。

## 4. 与方案的偏差 / 未实施清单

以下条目方案里有描述，**未实施**，留作后续 Track：

1. **看板 UI 的"精细"部分**：Stage 3 已实现"生成→反馈迭代（字段级锁定）
   →确认/放弃"主线接入"⏰ 周期性设置"/"➕ 新建目标"/手动整体关闭重判，
   但方案 §6.1/§6.2 描述的以下细节仍未做（见 §3.4 的具体取舍说明）：
   - 每个 section 直接编辑文本框（按行拆分），当前只能"看摘要 + 写反馈
     + 重新生成"，不能手工微调某个字段的具体文字；
   - `revise()` 前后的差异高亮；
   - "📄 从模板重新起草"独立按钮（当前只能放弃草稿后重新选模板生成）；
   - 看板侧"从执行历史反推"的开关（`from_history` 参数 REST 层已支持
     透传，只是看板没有暴露对应勾选框）；
   - §2 新增的"整体关闭判定"结果目前在看板上只有 close_check 按钮点击
     后的一次性 `st.success`/`st.info` 提示，没有做成持久化的状态徽标
     （历史判定结果仍然只能在"进展记录"里看纯文本）。
2. **`builder_mode="agent"` 只读探索路径**：方案 §3 输入源 1 提到应
   支持"起一个只读受限 Agent 先看一眼项目再生成"，镜像
   `GoalSpecBuilder._run_builder_agent()`。当前 `GoalExecutionSpecBuilder`
   只实现了裸 LLM 单轮路径，`mode="agent"`/`"auto"` 配置项存在但实际
   行为等同 `"llm"`（§2 新增的整体关闭判定同样只有裸 LLM 单轮路径，未
   挂只读工具去实际核查产出文件内容，只依赖 manifest 摘要文本）。
3. **模板自动匹配**：方案 §7 提到"关键词规则粗略匹配 Goal 描述，命中
   某个模板则默认预选"，当前只支持显式传 `template_id`，不做自动推荐。
4. **`--from-history` 只取最新一轮**：方案 §3 输入源 3 描述为"过去若干
   轮"，当前 CLI 实现只读 `read_latest_manifest()` 取最新一轮；
   `build_draft()` 本身的 `history_manifests` 参数已支持传入一个列表
   （`_run_llm` 拼 prompt 时会取最后 3 条），CLI 调用方后续要支持"多轮"
   只需要改 `_cmd_spec_generate()` 里收集 manifest 的逻辑，不需要改
   核心模块签名。（§2 的 `evaluate_overall_completion()` 不受此限——它
   走 `read_all_manifests()`，本来就读全部历史轮次。）
5. **看板"从执行历史反推"默认预填**、**差异高亮 UI**：Stage 3 未做，见
   §3.4/未实施清单第 1 项。
6. **CLI 侧暴露"整体关闭判定"的手动触发/查看入口**：**已实施**——见
   `/agent goals spec close-check <goal_id>`（`cli/commands/goals.py::
   _cmd_spec_close_check()`），直接调用
   `GoalBacklog.maybe_close_goal_by_overall_criteria()`，Goal 非
   `active` 时提前跳过（不消耗 LLM 调用），其余前置条件判断复用同一个
   方法，行为与自动触发路径完全一致。测试见
   `tests/test_goals_spec_close_check_cli.py`（5 个用例）。

以上未实施项均不影响已实施部分的正确性——`execution_spec_confirmed`
默认 `False`，未生成/未确认的 Goal 行为与方案引入前完全一致；
`overall_completion_criteria` 为空（绝大多数周期性 Goal 的默认情况）
时 §2 的判定逻辑不会被触发，等价于该功能关闭。

## 5. 后续建议顺序

1. 看板 UI 的剩余精细化：字段直接编辑文本框 + 差异高亮（当前"反馈驱动
   迭代"已经能覆盖大多数场景，这一项主要是进一步降低反馈成本）；整体
   关闭判定结果做成更显眼的持久化状态展示，而不只是一次性 toast +
   progress_notes 里的文本行。
2. `builder_mode="agent"` 路径 + 模板自动匹配（优先级较低，当前 `llm`
   路径已经可用，且 `revise()` 的字段锁定机制已经能覆盖"生成方向不对
   需要人工干预"的场景）。
