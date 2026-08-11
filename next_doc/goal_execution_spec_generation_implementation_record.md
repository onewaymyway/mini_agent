# Goal 执行规范自动生成 + 用户确认机制 —— 实施记录（Stage 1 ~ Stage 10）

对应设计文档：`next_doc/goal_execution_spec_generation_plan.md`

本记录覆盖 Stage 1（后端核心能力）～ Stage 10（整体关闭判定结果持久化
展示 + 单次覆盖 `overall_completion_use_agent`）的实施情况，不重复方案
本身的设计论证，只记录"最终落地成什么样、和方案哪里不同、还差什么"。

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
- **看板侧"从执行历史反推"开关**：Stage 3 首版未做，**Stage 5 已补上**
  （见 §5），这里不再列为差异项。

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

## 4. Stage 4 已实施：模板自动匹配（对应方案 §7 末段，Stage 1-3 未实施
   清单第 3 项）

- 每个模板 JSON（`perception/goal_execution_spec_templates/*.json`）新增
  `keywords` 字段（字符串数组，人工维护，第一版每个模板 5~8 个关键词，
  覆盖模板 `applicable_to` 描述里提到的典型场景词）。`list_templates()`
  的返回摘要里一并带出 `keywords`，供调用方自行展示"为什么推荐了这个
  模板"（当前 UI 没有展示 keywords 明细，只用来做匹配，接口层面先留出
  这个字段）。
- 新增纯函数 `suggest_template(goal_title, goal_description) -> Optional
  [str]`：对 `title + description` 文本做最朴素的子串命中计数，命中最多
  的模板 id 即为推荐；全部模板 0 命中，或 `title`/`description` 都是空
  字符串时返回 `None`（代表"不推荐、不预选，用户自行选择"，不会为了给
  出一个结果而随便选一个模板凑数）。刻意不做分词/语义匹配——方案里这个
  功能的定位是"给一个默认预选、减少手动挑选的心智负担"，不是"精确判断
  Goal 类型"，用户始终可以在下拉框里改选或选"不用模板"，匹配错了代价
  很低，没有必要为此引入额外的分词依赖。
- `GET /goal_execution_spec_templates` 端点新增两个可选 query 参数
  `goal_title`/`goal_description`，传了其中任一个就调用
  `suggest_template()` 并在响应里附加 `suggested_template_id`（未传则为
  `None`，行为与 Stage 3 完全一致，不影响没有传这两个参数的既有调用方）。
- `AgentClient.execution_spec_templates()` 新增同名可选参数透传。
- 看板 `_render_goal_execution_spec_widget()` 新增 `goal_title`/
  `goal_description` 两个可选形参：三处接入点（"⏰ 周期性设置"已绑定/
  未绑定分支、"➕ 新建目标"确认区块）都改为传入对应 Goal 的 title/
  description；下拉框里如果有 `suggested_template_id` 就默认预选那一项，
  并在"起草方式"标签后追加"（已根据 Goal 描述自动推荐）"提示，用户仍可
  改选或选"不使用模板"。"➕ 新建目标"场景下 title/description 随
  `_ges_pending_new_goal` 一起存进 `st.session_state`（创建表单本身
  `clear_on_submit=True` 会清空输入框，不能在确认区块渲染时重新从表单
  读取）。
- CLI `/agent goals spec generate` 未接入自动匹配——`--template` 仍然
  要求显式传入，这与方案"用户在触发入口里选择"的表述一致（CLI 场景下
  "触发入口"就是命令行参数本身，没有下拉框可预选，接入意义不大）。

### 4.1 测试

- `tests/test_goal_execution_spec.py` 追加 4 个用例：`list_templates()`
  返回值包含 `keywords`；`suggest_template()` 对三类典型描述（周报/巡检/
  数据抓取）分别命中对应模板；空输入/无关描述时返回 `None`。
- `tests/test_goal_execution_spec_kanban_routes.py` 追加 1 个用例：
  `GET /goal_execution_spec_templates` 带 `goal_title`/`goal_description`
  时返回正确的 `suggested_template_id`；不带时该字段为 `None`（已有用例
  覆盖，无需新增）。
- 全部新增用例 + 此前全部回归用例合计 104 个通过。

## 5. Stage 5 已实施：看板"从执行历史反推"开关（对应方案 §3 输入源 3，
   未实施清单第 5 条前半）

- `_render_goal_execution_spec_widget()` 的"生成第 1 版草稿"步骤新增
  「从最近一轮的执行记录反推草稿内容」勾选框，勾选后随生成请求一起把
  `from_history=True` 传给 `generate_execution_spec()`，与 CLI
  `--from-history` 走同一条后端路径（`output_workspace.read_latest_
  manifest()` 读最新一轮 manifest 拼进生成 prompt）。
- **只在"⏰ 周期性设置"两个分支里展示这个勾选框，"➕ 新建目标"确认区块
  不展示**：新建的 Goal 在这一步必然还没跑过任何一轮，`from_history=
  True` 传过去也只会读到空历史，等价于没勾选，展示这个选项对这个场景
  没有意义，反而增加一次无谓的选择负担。用 `key_prefix != "newgoal_"`
  做区分（"➕ 新建目标"确认区块固定用 `key_prefix="newgoal_"`，是当前
  代码里唯一带这个前缀的调用点）。
- 默认不勾选（`value=False`），与 CLI 默认不带 `--from-history` 一致。

未新增独立测试用例——`from_history` 参数的透传链路（REST 端点 →
`AgentClient.generate_execution_spec()`）已经在 Stage 3 的
`test_generate_builds_and_saves_draft`（打桩 `build_draft`，未显式传
`from_history` 走默认值 `False` 的路径）里间接覆盖；看板这一步只是新增
了一个可选 UI 输入，不引入新的后端分支逻辑，不需要为纯 UI 勾选框单独
补 Streamlit 层面的测试（现有测试体系不覆盖 Streamlit 渲染本身）。

## 6. Stage 6 已实施：看板"📄 从模板重新起草"独立按钮（对应方案 §6.1，
   未实施清单第 1 项后半）

- 未确认草稿区块（"有草稿、未确认"分支）里追加一个折叠的
  「📄 从模板重新起草」区块：模板下拉框 + 「♻️ 用此模板重新起草」按钮，
  点击后直接调用 `generate_execution_spec()`（与首次生成同一个接口），
  用返回结果整段覆盖 `st.session_state` 里的当前草稿。不需要像 Stage 3
  首版那样"先点❌放弃草稿、回到未生成状态、再重新选模板生成"两步，
  合并成一步。
- 语义上是**整段覆盖，不是合并**：`build_draft()` 固定生成"第 1 版"，
  用户在当前草稿上做的补充意见迭代、字段锁定都会丢失。这与方案里
  "随时可以从模板重新起草"的表述一致（"重新起草"本身就是推倒重来），
  真想保留已改好的部分应该用「🔄 补充意见重新生成」+ 字段锁定。
- **不需要额外的"是否会覆盖已确认规范"防呆**：这个区块只出现在"未确认"
  分支里（"已确认"分支在渲染这段代码之前已经 `return True` 提前退出），
  控制流天然保证了它碰不到已确认的规范，不需要在按钮点击时再加一次
  运行时判断。
- 纯前端改动，不涉及新的 REST 端点/参数，直接复用 Stage 3 的
  `generate_execution_spec()`/`GET .../execution_spec/generate`，未新增
  测试用例（原因同 Stage 5：现有测试体系不覆盖 Streamlit 渲染，后端
  路径已有 `test_generate_builds_and_saves_draft` 覆盖）。

## 7. Stage 7 已实施：`builder_mode="agent"` 只读探索路径（对应方案 §3
   输入源 1，未实施清单第 2 项）

镜像 `goal_mode/spec.py::GoalSpecBuilder._run_builder_agent` 的"只读、有限
工具的受限 Agent"架构，补齐此前 `mode="agent"`/`"auto"` 配置项存在但实际
行为等同 `"llm"` 的缺口。

### 7.1 配置（`config/models.py::GoalExecutionSpecConfig`）

新增三个字段，与 `GoalModeConfig` 的同名字段同义：
`builder_agent_allowed_tools`（默认 `skill_list`/`list_workflows`/
`show_workflow`/`read_file`/`list_dir`/`tree_summary`/`grep`/`glob`，不含
`bash`、不含任何写文件的工具）、`builder_agent_allowed_tool_groups`（默认
空）、`builder_agent_max_turns`（默认 6）。`builder_mode` 字段的说明文字
同步更新，不再写"agent 路径尚未落地"。

### 7.2 `GoalExecutionSpecBuilder`（`perception/goal_execution_spec.py`）

- `__init__` 新增可选参数 `parent_session_id`/`parent_session_dir`（透传给
  受限 Agent，用于把它的会话记录挂到父会话下面，与 `GoalSpecBuilder` 的
  同名参数用途一致），新增 `last_effective_path` 属性（记录这次调用实际
  走了 `"llm"` 还是 `"agent"`，供调用方/测试判断）。
- 新增模块级函数 `_rule_based_needs_project_context(text)`：关键词规则
  （"项目里/项目中"“现有的 XXX”“已有的 XXX”“沿用”“参考…现有/已有/项目”
  “skill”“workflow”“工作流”“代码风格”“目录结构”“命名约定/规范”“复用"）
  粗略匹配一段文本是否提到"参考/沿用项目已有内容"类诉求。**与
  `goal_mode/spec.py` 同名函数的区别**：不做"已知 skill/workflow 名称"的
  二次匹配（那一层依赖遍历项目实际 skill/workflow 列表，对"要不要起一个
  受限 Agent"这个初筛决策收益有限，先用最朴素的关键词规则覆盖最常见
  场景）。
- 新增 `_run_builder(prompt, *, detection_text=None)` 作为 `build_draft`/
  `revise` 的唯一入口，按 `self.mode` 分诊：
  - `"llm"` → 固定走 `_run_llm`（裸单轮 chat completion，不挂工具）。
  - `"agent"` → 固定走 `_run_builder_agent`。
  - `"auto"` → 对 `detection_text`（`build_draft` 传 `title+description`，
    `revise` 传用户反馈文本）跑关键词规则，命中走 agent，否则走 llm。
  - **与 `GoalSpecBuilder._run_builder` 的一处简化**：没有做"LLM 在裸输出
    JSON 里自报 `needs_project_context` 后二次重生成"那层兜底——
    `goal_execution_spec_builder.md` 的输出 schema 目前不包含这个字段，
    规则漏判时不会自动补救，只能显式传 `mode="agent"` 绕过。这个简化在
    `_run_builder` 的 docstring 里也写明了，不是隐藏的行为差异。
- 新增 `_run_builder_agent(prompt)`：通过 `role_agents/judge_factory.py::
  spawn_judge_agent`/`run_judge_turn` 构造并运行一个只读受限 Agent，system
  prompt 用 `goal_execution_spec_builder` 基础说明 + 新增的
  `goal_execution_spec_builder_agent_addendum`（新文件，告知模型"你现在有
  只读工具可用"，内容结构对齐 `goal_spec_builder_agent_addendum.md`）拼接
  而成。构造失败/运行失败/空输出三种失败路径都写 `self.last_error` 并
  返回空字符串，走到 `build_draft`/`revise` 里既有的"解析失败 → 兜底空
  草稿/保留上一版"逻辑，不需要新增错误处理分支。
- `build_draft()`/`revise()` 的调用点从 `self._run_llm(prompt)` 改为
  `self._run_builder(prompt, detection_text=...)`，行为完全对齐既有测试
  （默认 `mode="auto"` 时，不含触发关键词的普通描述仍然走 llm 路径，原有
  25 个测试用例全部原样通过，无需改动断言）。

### 7.3 新增文件

`src/mini_agent/prompts/system/goal_execution_spec_builder_agent_addendum.md`
——"补充说明：你现在挂载了一组只读工具" + "使用原则"（先查证再产出、不要
过度探索、工具只读、查证结果要体现在规范里、最终仍只输出一个 JSON
对象），结构与 `goal_spec_builder_agent_addendum.md` 对齐，产出对象从
"验收标准"换成"执行规范"。

### 7.4 测试

`tests/test_goal_execution_spec.py` 追加 8 个用例：
`_rule_based_needs_project_context()` 对典型触发词/无关描述的判断；
`mode="llm"` 即便描述里出现关键词也绝不构造受限 Agent（用一个断言型假
`spawn_judge_agent` 顶替，一旦被调用测试直接失败）；`mode="agent"` 即便
描述完全不涉及项目上下文也一定构造受限 Agent，并正确解析其
`raw_output`；`mode="auto"` 命中关键词时用 agent 路径且不再调用裸 LLM
helper、不命中时走 llm 路径且不构造 Agent；`revise()` 场景下
`detection_text` 用的是用户反馈文本而不是 Goal 描述本身；受限 Agent 构造
失败（`spawn_judge_agent` 抛异常）时正确落到空草稿兜底、`last_error` 里
包含"构造受限 Agent 失败"字样。全部通过 `monkeypatch` 打桩
`mini_agent.role_agents.judge_factory.spawn_judge_agent`/`run_judge_turn`，
不依赖真实 LLM/沙箱环境。

全部新增用例 + 此前全部回归用例（`test_goal_execution_spec.py`/
`test_goal_execution_spec_kanban_routes.py`/`test_goal_cron_bridge.py`/
`test_goal_backlog.py`/`test_goal_overall_completion.py`/
`test_goals_spec_close_check_cli.py`/`test_kanban_config_routes.py`/
`test_goal_output_directory_onetime.py`/`test_goal_execution_fairness.py`/
`test_config_catalog_list_seed_merge.py`/`test_external_input_config.py`）
合计 125 个用例回归通过。

### 7.5 与方案的偏差 / 未做的部分

- **`evaluate_overall_completion()`（Stage 2 整体关闭判定）不受这次改动
  影响，仍然只有裸 LLM 单轮路径**，不挂只读工具去实际核查产出文件内容，
  只依赖 manifest 摘要文本——这是 Stage 2 就已知的未实施项，Stage 7 的
  范围明确限定在 `GoalExecutionSpecBuilder`（草稿生成/修订），没有顺带
  扩展到整体关闭判定，避免两个本来独立的改动混在一次改动里。
- **CLI `/agent goals spec generate` 未新增 `--mode` 参数**：`mode` 目前
  只能通过配置文件的 `goal_execution_spec.builder_mode` 设置，或者代码
  里显式传 `GoalExecutionSpecBuilder(cfg, mode=...)`；CLI/看板都没有暴露
  单次调用覆盖 `mode` 的入口。这与 GoalSpecBuilder 侧
  `/goal --mode=agent <文本>` 能单次覆盖的能力不对称，留作后续如果有
  实际需求再补。
- **看板没有展示"这次走的是 llm 还是 agent 路径"**：`last_effective_path`
  目前只是 Python 属性，没有通过 REST 响应体传给前端、也没有在草稿摘要
  里展示"生成这份草稿时是否读取了项目内容"。

## 8. Stage 8 已实施：CLI/看板暴露单次覆盖 `builder_mode` 的入口（对应
   实施记录 §7.5/§9 未实施清单第 2 条"CLI/看板未暴露单次覆盖 mode 的
   入口"）

Stage 7 补齐了 `builder_mode="agent"` 的实际执行路径，但当时 `mode` 只能
通过配置文件 `goal_execution_spec.builder_mode` 设置，单次调用无法覆盖。
Stage 8 补上这个入口，行为对齐 `GoalSpecBuilder` 侧 `/goal --mode=agent
<文本>` 能单次覆盖的能力。

### 8.1 REST 端点（`api/routes.py`）

- `POST /goals/{id}/execution_spec/generate`、`POST /goals/{id}/
  execution_spec/revise` 两个端点的请求体新增可选字段 `"mode"`
  （`"llm"`/`"agent"`/`"auto"`），透传进
  `GoalExecutionSpecBuilder(cfg, mode=body.get("mode") or None)`——不传
  或传空字符串时等价于 `mode=None`，构造函数内部按既有逻辑回退配置文件
  `builder_mode`（默认 `"auto"`），不修改配置文件本身，只影响这一次
  调用。
- 两个端点的响应体新增 `"effective_path"` 字段（`builder.
  last_effective_path`，取值 `"llm"`/`"agent"`），供前端展示"这次实际
  走的是哪条路径"，不需要额外调用或猜测。`confirm`/`close_check`/`GET`
  端点不涉及生成，不需要这个字段。

### 8.2 CLI（`cli/commands/goals.py`）

- `/agent goals spec generate <goal_id> [--template <id>] [--from-history]
  [--mode llm|agent|auto]`：新增 `--mode` 参数，`argparse` 用 `choices=
  ["llm", "agent", "auto"]` 做校验，非法值直接报用法错误、不调用生成器
  （不消耗任何 LLM/Agent 调用）。`_cmd_spec_generate()` 新增形参 `mode:
  Optional[str] = None`，透传进 `GoalExecutionSpecBuilder(cfg, mode=mode)`。
- 生成成功的提示文案追加"走 {纯 LLM｜只读探索 Agent} 路径"，读取
  `builder.last_effective_path` 后映射成中文标签展示，不需要用户额外查
  日志才知道这次有没有读取项目内容。
- 顶层 `handle_goals_cmd()` 的 usage 提示与命令文档字符串（模块开头
  docstring）同步更新，加入 `--mode` 说明。

### 8.3 `AgentClient` 封装（`apps/mini_agent_kanban/client.py`）

`generate_execution_spec()`/`revise_execution_spec()` 各新增可选形参
`mode: str = ""`，空字符串时不在请求体里带 `"mode"` 键（沿用既有
`from_history`/`template_id` 等参数"传空等于不传"的约定风格），非空时
透传进请求体，与 REST 端点的 `body.get("mode") or None` 语义对齐（客户端
传空字符串、不传字段、服务端拿到 `None`，三者最终效果一致）。

### 8.4 看板 UI（`apps/mini_agent_kanban/app.py`）

- `_render_goal_execution_spec_widget()` 新增 `path_key` session_state
  键（`f"{key_prefix}ges_path_{goal_id}"`），保存最近一次 `generate`/
  `revise` 响应里的 `effective_path`；草稿区块顶部（在"已确认"/"未生成"
  两个分支之前）新增一行 `st.caption`，把 `effective_path` 翻译成"纯
  LLM（未读取项目内容）"/"只读探索 Agent（读取过项目内容）"展示给用户
  ——这是实施记录 §7.5/§9 未实施清单里"看板没有展示'这次走的是 llm 还是
  agent 路径'"的直接补齐。
- "生成第 1 版草稿"步骤（未生成分支）新增"生成路径"下拉框，选项为
  "跟随配置默认"/"自动判断"/"纯 LLM"/"只读探索 Agent"，默认选中"跟随
  配置默认"（对应传空字符串，等价于此前行为，不改变任何既有用户的默认
  体验）；点击生成按钮时把选中值透传给 `generate_execution_spec(mode=...)`，
  返回结果里的 `effective_path` 写入 `path_key`。
- "🔄 补充意见重新生成"步骤同样新增一个精简版下拉框（"跟随配置默认"/
  "自动判断"/"纯 LLM"/"只读探索 Agent"），随 `revise_execution_spec()`
  一起提交；"❌ 放弃草稿"/"♻️ 生成新草稿"两处清空 `draft_key` 的地方同步
  清空 `path_key`，避免展示"上一份已放弃草稿"的路径信息。
- "📄 从模板重新起草"独立按钮（Stage 6）**未新增**这个下拉框——这个
  按钮的定位是"整段覆盖、推倒重来"的快捷操作（见 Stage 6 章节），保持
  原有的"跟随配置默认"行为不额外增加选择负担；如果用户想在换模板的同时
  指定生成路径，需要先放弃草稿，走"生成第 1 版草稿"入口。它的返回结果
  仍然会更新 `path_key`（服务端总是返回 `effective_path`），只是没有
  UI 输入让用户提前指定。

### 8.5 与方案的偏差 / 简化取舍

- 方案 §6.4 原文只提到"CLI/看板都没有暴露单次覆盖 mode 的入口"是遗留
  问题，没有规定具体交互形式；Stage 8 选择"下拉框 + 默认跟随配置"而不是
  单独的"高级选项"折叠区，是因为可选项只有 4 个（含"跟随配置默认"），
  平铺展示的心智负担和一个折叠 expander 相当，没必要再加一层折叠。
- `evaluate_overall_completion()`（Stage 2 整体关闭判定）**仍然不支持**
  单次覆盖任何执行路径参数——它本来就没有 `mode` 概念（该方法固定走裸
  LLM 单轮调用，不涉及 `_run_builder` 分诊逻辑），Stage 8 的范围明确
  限定在 `GoalExecutionSpecBuilder.build_draft()`/`revise()` 这两个已经
  有 `mode` 概念的入口，没有顺带给 `evaluate_overall_completion()` 加
  这个能力，这一点与"未实施清单"里"`evaluate_overall_completion()`
  挂只读工具核查产出内容"是同一个待办，还没有做。

### 8.6 测试

- `tests/test_goal_execution_spec_kanban_routes.py` 追加 3 个用例：
  `generate`/`revise` 端点透传 `mode` 到 `GoalExecutionSpecBuilder` 构造
  函数（打桩验证构造参数）、响应体正确携带 `effective_path`；不传 `mode`
  时构造函数收到 `None`（而不是空字符串），避免"不传"与"显式传空"两种
  语义在服务端悄悄产生歧义。
- 新增 `tests/test_goals_spec_generate_cli_mode.py`（3 个用例）：
  `--mode agent` 正确透传并在成功提示里体现"只读探索 Agent"字样；不带
  `--mode` 时透传 `None` 且提示体现"纯 LLM"；非法 `--mode` 值被
  `argparse choices` 拦截，`_cmd_spec_generate()`（打桩）完全不会被
  调用，不消耗任何生成器调用。
- 看板 UI 改动是纯前端下拉框 + 参数透传，不引入新的后端分支逻辑，沿用
  Stage 3/5/6 一贯的取舍——不为纯 Streamlit 渲染新增测试（现有测试体系
  不覆盖 Streamlit 渲染本身），后端参数透传路径已由上面两批用例覆盖。

全部新增 6 个用例 + 此前全部回归用例（`test_goal_execution_spec.py`/
`test_goal_execution_spec_kanban_routes.py`/`test_goal_cron_bridge.py`/
`test_goal_backlog.py`/`test_goal_overall_completion.py`/
`test_goals_spec_close_check_cli.py`/`test_kanban_config_routes.py`/
`test_goal_output_directory_onetime.py`/`test_goal_execution_fairness.py`）
合计 117 个用例回归通过。

## 9. Stage 9 已实施：`evaluate_overall_completion()` 可选的只读受限
   Agent 判定路径（对应实施记录 §10 后续建议顺序第 2 条 / 未实施清单第
   8 项"`evaluate_overall_completion()` 挂只读工具核查产出内容"）

此前 §2 的整体关闭判定固定走裸 LLM 单轮调用，只依据 `read_all_
manifests()` 拼出的摘要文本判断（文件清单 + `progress_note` 备注），
没有能力实际打开产出文件核实内容是否真的符合标准（比如"报告里是否真的
包含对比表格"这种要点，manifest 摘要文本本身给不出可靠依据）。Stage 9
复用 Stage 7 已经搭好的"只读、有限工具的受限 Agent"基础设施，给这个
判定补上一条可选的、更可靠但成本更高的路径。

### 9.1 配置（`config/models.py::GoalExecutionSpecConfig`）

新增三个字段：

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `overall_completion_use_agent` | `false` | 总开关。关闭时行为与 Stage 9 引入前完全一致（裸 LLM 单轮）；打开后 `evaluate_overall_completion()` 改走受限 Agent 路径 |
| `overall_completion_agent_allowed_tools` | `["read_file", "list_dir", "tree_summary", "grep", "glob"]` | 只读工具白名单。**不含** `skill_list`/`list_workflows`（与 `builder_agent_allowed_tools` 的区别）——判官核查的是"这个 Goal 自己的产出目录"，不需要了解项目里其它 skill/workflow 的定义 |
| `overall_completion_agent_max_turns` | `8` | 受限 Agent 的最大轮次，比 `builder_agent_max_turns`（默认 6）略高——核查产出内容往往需要多打开几个文件 |

默认关闭的理由：整体关闭判定本身就是"只有一次性、拆了多个子 Objective
且填了 `overall_completion_criteria` 的 Goal 才会触发"的可选增强（见方案
§2 非目标声明），这里进一步细分为"要不要为了判定准确性多付出一次
（甚至多轮）Agent 工具调用的成本"，交给用户按需开启，不默认让所有已经
在用这个功能的 Goal 判定成本悄悄上升。

### 9.2 `GoalExecutionSpecBuilder`（`perception/goal_execution_spec.py`）

- `evaluate_overall_completion()` 新增可选形参 `output_base_dir:
  Optional[str] = None`（该 Goal 产出目录的实际路径，通常来自
  `output_workspace.goal_output_base_dir()`）。裸 LLM 路径下这个参数
  不产生任何效果，只有 `overall_completion_use_agent=True` 时才会被
  用到——拼进 user prompt 的 `{{output_dir_block}}` 占位符，告诉受限
  Agent 去哪个目录打开文件；未传时该占位符渲染为空字符串，不影响裸 LLM
  路径的既有 prompt 内容。
- 新增 `_run_overall_completion_judge_agent(prompt)`：与 `_run_builder_
  agent()` 是同一套 `role_agents/judge_factory.py::spawn_judge_agent`/
  `run_judge_turn` 基础设施，区别只在 system prompt（`goal_overall_
  completion_judge` + 新增的 `_agent_addendum`）、工具白名单/`max_turns`
  来源（`overall_completion_agent_*` 而不是 `builder_agent_*`）。构造
  失败/运行失败/空输出三种失败路径与 `_run_builder_agent()` 完全对称，
  都写 `self.last_error` 并返回空字符串，落到 `evaluate_overall_
  completion()` 既有的"解析失败 → 保守判定为 continue"兜底，不需要
  新增错误处理分支。
- `evaluate_overall_completion()` 内部按 `cfg.goal_execution_spec.
  overall_completion_use_agent` 二选一调用 `_run_judge_llm()`（原有裸
  LLM 路径，未改动）或 `_run_overall_completion_judge_agent()`——不像
  `_run_builder()` 那样有"auto"三态分诊，这里只有"开/关"两态：整体关闭
  判定的触发条件本身已经很收窄（只读前置判断 + `overall_completion_
  criteria` 非空），不需要再叠加一层关键词规则去猜"这次要不要挂
  Agent"，直接由配置显式控制更直接。

### 9.3 新增文件

`src/mini_agent/prompts/system/goal_overall_completion_judge_agent_
addendum.md`——"补充说明：你现在可以调用工具打开该 Goal 产出目录下的
实际文件" + "使用原则"（manifest 摘要只是起点、不要过度探索、工具只读、
查证结果要体现在 reasoning 里、最终仍只输出一个 JSON 对象），结构与
`goal_execution_spec_builder_agent_addendum.md` 对齐，但强调点不同：
生成规范时"查证项目结构"，这里是"核查产出文件内容是否真的达标"。

`prompts/system/goal_overall_completion_judge.md` 顶部注释同步更新，
说明默认裸 LLM、`overall_completion_use_agent=true` 时额外拼接哪份
附录，判定原则/输出格式两条路径完全共用，不因是否挂工具而改变。

`prompts/user/goal_overall_completion_request.md` 新增可选变量
`{{output_dir_block}}`，仅 agent 路径且调用方传了 `output_base_dir`
时非空。

### 9.4 调用方（`perception/goal_backlog.py`）

`GoalBacklog.maybe_close_goal_by_overall_criteria()` 已经在计算
`base_dir`（用于 `read_all_manifests()`），这里顺带把它透传给
`evaluate_overall_completion(output_base_dir=str(base_dir))`——不需要
额外一次目录解析。CLI `spec close-check` 路径同样调用这个方法，因此
手动重触发的判定也会自动享受到这个能力（配置打开时）。

### 9.5 与方案的偏差 / 未做的部分

- **不支持单次覆盖**：与 Stage 8 给 `build_draft`/`revise` 加的单次
  `mode` 覆盖不同，`evaluate_overall_completion()` 目前只能通过配置文件
  开关，CLI `spec close-check`/看板"🔁 手动重判整体是否可以关闭"都没有
  暴露单次覆盖的入口。整体关闭判定的触发频率远低于草稿生成/修订（一次
  一次性 Goal 通常只会真正关闭一次），单次覆盖的收益相对更小，暂不做。
- **看板没有展示"这次整体关闭判定是否挂了 Agent"**：`GoalBacklog.
  maybe_close_goal_by_overall_criteria()` 返回值仍然只有
  `"closed"/"kept_open"/None` 三种，`reasoning` 文本里 agent 路径已经
  会体现"已打开 xxx 文件确认..."这类具体依据（见 §9.3 附录第 4 条使用
  原则），但没有单独的字段/UI 徽标标出"这次走的是哪条路径"，与 Stage 8
  给草稿生成加的 `effective_path` 展示不对称。
- **`overall_completion_agent_max_turns` 默认值（8）纯粹是经验估计**，
  没有做专门的压测/调优；如果实际使用中发现产出目录文件较多、经常在
  `max_turns` 内收敛不到结论，需要用户自行调大这个配置。

### 9.6 测试

`tests/test_goal_overall_completion.py` 追加 4 个用例：
`overall_completion_use_agent` 默认关闭时即便传了 `output_base_dir` 也
绝不构造受限 Agent（用断言型假 `spawn_judge_agent` 顶替，一旦被调用
测试直接失败）；开启后正确构造只读受限 Agent（工具白名单含
`read_file`、不含 `skill_list`）且 prompt 里正确带上 `output_base_dir`；
开启但未传 `output_base_dir` 时 prompt 里不出现目录提示文字（旧调用方
兼容）；受限 Agent 构造失败时正确落到"保守返回 continue"兜底、
`last_error` 里包含"构造受限 Agent 失败"字样。全部通过 `unittest.mock.
patch` 打桩 `mini_agent.role_agents.judge_factory.spawn_judge_agent`/
`run_judge_turn`，不依赖真实 LLM/沙箱环境。

全部新增用例 + 此前全部回归用例（`test_goal_execution_spec.py`/
`test_goal_execution_spec_kanban_routes.py`/`test_goal_cron_bridge.py`/
`test_goal_backlog.py`/`test_goal_overall_completion.py`/
`test_goals_spec_close_check_cli.py`/`test_kanban_config_routes.py`/
`test_goal_output_directory_onetime.py`/`test_goal_execution_fairness.py`/
`test_goals_spec_generate_cli_mode.py`/
`test_config_catalog_list_seed_merge.py`/`test_external_input_config.py`）
合计 135 个用例回归通过。

## 10. Stage 10 已实施：整体关闭判定结果持久化展示 + 单次覆盖
    `overall_completion_use_agent`（对应 §9.5 未做的部分第 1 条 /
    implementation_record.md §11 后续建议顺序第 1/2 条）

Stage 9 之后仍留了两个口子：①`GoalBacklog.maybe_close_goal_by_overall_
criteria()` 的判定结果只写进 `progress_notes` 里的一行文本，没有结构化
的持久化状态供看板/CLI 直接读取展示；②`overall_completion_use_agent`
只能通过配置文件开关，没有像 `build_draft`/`revise` 的 `mode` 那样的
单次覆盖入口。Stage 10 把这两点补上，不引入新的架构层，全部复用 Stage
8/9 已经搭好的"单次覆盖 + 响应体带上实际生效路径"模式。

### 10.1 结果持久化（`perception/goal_backlog.py`）

`GoalNode` 新增 `overall_completion_last_check: Optional[dict] = None`
字段（`to_dict`/`from_dict` 已同步，旧数据反序列化缺省为 `None`，代表
"从未触发过判定"）：

```json
{"outcome": "closed" | "kept_open", "reasoning": str, "used_agent": bool, "at": float}
```

`maybe_close_goal_by_overall_criteria()` 在前置条件全部满足、真正调用了
`evaluate_overall_completion()` 之后（无论最终结果是 `closed` 还是
`kept_open`）都会用 `update_fields()` 写入这个字段；前置条件不满足直接
`return None` 的路径不写（与"这个 Goal 根本不适用本机制"的既有语义
一致，不会在从未真正判定过的 Goal 上凭空出现一条快照）。看板/CLI 据此
可以直接展示"上一次判定是什么时候、判了什么、走的是哪条路径"，不需要
翻 `progress_notes` 里的文本行去找。

### 10.2 单次覆盖 `use_agent`

- `GoalExecutionSpecBuilder.evaluate_overall_completion()` 新增可选形参
  `use_agent_override: Optional[bool] = None`——`True`/`False` 时直接
  决定这次判定是否走受限 Agent 路径，`None`（默认，不传）时回退配置
  `goal_execution_spec.overall_completion_use_agent`（Stage 9 引入前/
  引入后的既有行为，完全兼容旧调用方）。新增 `self.last_used_agent:
  Optional[bool]`（构造时为 `None`，判定后写入实际使用的路径），与
  `self.last_effective_path` 是同一风格的"实际生效结果"记录。
- `GoalBacklog.maybe_close_goal_by_overall_criteria()` 新增
  `use_agent: Optional[bool] = None` 形参，透传给
  `evaluate_overall_completion(use_agent_override=use_agent)`，并把
  `builder.last_used_agent` 写进 §10.1 的持久化快照。
- CLI：`/agent goals spec close-check <goal_id> [--use-agent | --no-
  agent]`（`argparse` 的 `store_true`/`store_false` 互斥对，都不传时为
  `None`）；判定完成后从持久化快照读 `used_agent` 决定提示文字里展示
  "只读探索 Agent"还是"纯 LLM"。
- REST：`POST .../execution_spec/close_check` 新增可选 body 字段
  `"use_agent": bool`，不传或传 `null` 时透传 `None`（回退配置默认值）。
- 看板：`_render_goal_execution_spec_widget()` 附近"🔁 手动重判整体是否
  可以关闭"按钮旁新增"整体关闭判定路径"下拉框（跟随配置默认 / 只读
  探索 Agent / 纯 LLM），与"生成路径"下拉框是同一交互模式；按钮上方
  新增一行持久化状态展示（图标 + 判定时间 + 路径 + 结论），点击一次
  之后即使刷新页面也还能看到"上一次判定的结果"，不再只是一次性
  `st.success`/`st.info` 提示——对应 §9.5"看板没有展示这次整体关闭判定
  是否挂了 Agent"，Stage 10 已补上。

### 10.3 与方案/§9.5 的偏差

- 与 Stage 8 给 `build_draft`/`revise` 加的单次 `mode` 覆盖完全对称
  （单次覆盖 + 响应体/CLI 提示带上实际路径），没有引入新的交互范式。
- 持久化快照只保留"最近一次"，不做历史列表——与 §2 非目标"不做规范的
  多版本历史 UI"、Stage 9 之前"只保留当前生效版本"的一贯取舍一致。
- 仍然没有暴露"整体关闭判定"的 `builder_model`/`builder_provider`/
  `max_turns` 等更细粒度参数的单次覆盖入口——判定触发频率低，`use_
  agent` 单次覆盖已覆盖"想临时看一眼 Agent 路径判断更准还是更不准"这个
  最主要的排查场景，更细粒度的覆盖收益更低，暂不做。

### 10.4 测试

- `tests/test_goal_overall_completion.py` 追加 2 个用例：判定后
  `GoalNode.overall_completion_last_check` 正确写入 `outcome`/
  `reasoning`/`used_agent`/`at`；`use_agent=True` 单次覆盖时即便配置
  文件 `overall_completion_use_agent=False`，`spawn_judge_agent` 仍会被
  尝试调用（用 `unittest.mock.patch` 让其失败以验证确实"尝试走了 agent
  路径"），持久化快照的 `used_agent` 仍记为 `True`（代表"这次尝试的
  路径"，与是否成功无关，构造失败落到既有"保守返回 continue"兜底）。
- `tests/test_goals_spec_close_check_cli.py` 追加 2 个用例：
  `--use-agent`/`--no-agent`/都不传三种情况下 `use_agent` 参数正确
  透传给 `maybe_close_goal_by_overall_criteria()`；判定结果的持久化
  快照 `used_agent=True` 时提示文字里正确展示"只读探索 Agent"。既有
  3 个用例的 mock lambda 签名同步补上 `use_agent=None` 形参（不改变
  原有断言）。
- `tests/test_goal_execution_spec_kanban_routes.py` 追加 2 个用例：
  body 里 `"use_agent": true` 正确透传给
  `maybe_close_goal_by_overall_criteria(use_agent=True)`；不传时透传
  `None`。

全部新增用例 + 此前全部回归用例（同 §9.6 列出的 12 个测试文件）合计
141 个用例回归通过。

## 11. 与方案的偏差 / 未实施清单

以下条目方案里有描述，**未实施**，留作后续 Track：

1. **看板 UI 的"精细"部分**：Stage 3 已实现"生成→反馈迭代（字段级锁定）
   →确认/放弃"主线接入"⏰ 周期性设置"/"➕ 新建目标"/手动整体关闭重判，
   Stage 6 补上了"从模板重新起草"独立按钮；方案 §6.1/§6.2 描述的以下
   细节仍未做（见 §3.4 的具体取舍说明）：
   - 每个 section 直接编辑文本框（按行拆分），当前只能"看摘要 + 写反馈
     + 重新生成"，不能手工微调某个字段的具体文字；
   - `revise()` 前后的差异高亮；
   - §2 新增的"整体关闭判定"结果**已实施持久化展示**（Stage 10，见
     §10.1）——`GoalNode.overall_completion_last_check` 保存最近一次
     判定的 `outcome`/`reasoning`/`used_agent`/`at`，看板"🔁 手动重判"
     按钮上方常驻展示，不再只是一次性 `st.success`/`st.info` 提示；
     仍然只保留"最近一次"，不做历史列表（与 §2 非目标一致）。
   - 看板展示"这份草稿生成时走的是 llm 还是 agent 路径"：**已实施**
     （Stage 8，见 §8.4）——`last_effective_path` 已通过 REST 响应体的
     `effective_path` 字段暴露，看板草稿区块顶部展示"上次生成走的路径"。
2. **`builder_mode="agent"` 只读探索路径**：**已实施**（Stage 7，见 §7）
   ——镜像 `GoalSpecBuilder._run_builder_agent()`，`mode="agent"` 固定走
   受限只读 Agent，`mode="auto"`（默认）用关键词规则判断是否需要项目
   上下文。简化点、以及未覆盖的部分见 §7.5（其中"`evaluate_overall_
   completion()` 仍是裸 LLM 单轮路径，未挂只读工具去实际核查产出文件
   内容"一点仍然成立，见本清单第 7 条；"CLI/看板未暴露单次覆盖 `mode`
   的入口"**已实施**，见 Stage 8/§8）。
3. **模板自动匹配**：**已实施**（Stage 4，见 §4）——关键词规则匹配
   Goal 的 title+description，命中模板则在看板下拉框里默认预选，用户
   仍可改选或选"不用模板"；CLI 未接入（`--template` 仍要求显式传入）。
4. **`--from-history` 只取最新一轮**：方案 §3 输入源 3 描述为"过去若干
   轮"，当前 CLI 实现只读 `read_latest_manifest()` 取最新一轮；
   `build_draft()` 本身的 `history_manifests` 参数已支持传入一个列表
   （`_run_llm` 拼 prompt 时会取最后 3 条），CLI 调用方后续要支持"多轮"
   只需要改 `_cmd_spec_generate()` 里收集 manifest 的逻辑，不需要改
   核心模块签名。（§2 的 `evaluate_overall_completion()` 不受此限——它
   走 `read_all_manifests()`，本来就读全部历史轮次。）
5. **看板"从执行历史反推"默认预填**：**已实施**（Stage 5，见 §5）。
   **差异高亮 UI**：仍未做，见未实施清单第 1 项。
6. **CLI 侧暴露"整体关闭判定"的手动触发/查看入口**：**已实施**——见
   `/agent goals spec close-check <goal_id>`（`cli/commands/goals.py::
   _cmd_spec_close_check()`），直接调用
   `GoalBacklog.maybe_close_goal_by_overall_criteria()`，Goal 非
   `active` 时提前跳过（不消耗 LLM 调用），其余前置条件判断复用同一个
   方法，行为与自动触发路径完全一致。测试见
   `tests/test_goals_spec_close_check_cli.py`（5 个用例）。
7. **CLI/看板暴露单次覆盖 `mode` 的入口**：**已实施**（Stage 8，见
   §8）——`POST .../execution_spec/generate`/`revise` 支持请求体
   `"mode"` 字段单次覆盖，CLI `spec generate` 支持 `--mode`，看板新增
   "生成路径"下拉框，均不修改配置文件，只影响单次调用；响应体新增
   `effective_path`，看板展示"上次生成走的路径"。
8. **`evaluate_overall_completion()` 挂只读工具核查产出内容**：**已实施**
   （Stage 9，见 §9）——新增可选配置 `overall_completion_use_agent`
   （默认 `false`），开启后复用 Stage 7 的受限 Agent 基础设施，判官可以
   实际打开该 Goal 产出目录下的文件核实内容，而不再只依赖 manifest
   摘要文本。默认关闭，不影响任何既有 Goal 的既有判定行为；单次覆盖
   入口**已实施**，见下第 9 条。
9. **CLI/看板暴露单次覆盖 `overall_completion_use_agent` 的入口 +
   整体关闭判定结果持久化展示**：**已实施**（Stage 10，见 §10）——
   `GoalNode` 新增 `overall_completion_last_check` 持久化字段；
   `evaluate_overall_completion()`/`maybe_close_goal_by_overall_
   criteria()` 新增 `use_agent_override`/`use_agent` 单次覆盖形参；CLI
   `spec close-check` 支持 `--use-agent`/`--no-agent`；REST body 支持
   `"use_agent"` 字段；看板"🔁 手动重判"按钮旁新增"整体关闭判定路径"
   下拉框，按钮上方常驻展示上一次判定结果。

以上未实施项均不影响已实施部分的正确性——`execution_spec_confirmed`
默认 `False`，未生成/未确认的 Goal 行为与方案引入前完全一致；
`overall_completion_criteria` 为空（绝大多数周期性 Goal 的默认情况）
时 §2 的判定逻辑不会被触发，等价于该功能关闭；`builder_mode` 默认
`"auto"`，关键词规则未命中时行为与"agent 路径不存在"完全一致（走 llm
单轮路径），不影响任何既有 Goal 的既有行为；单次 `mode` 覆盖不传时
（CLI 不带 `--mode`、看板选"跟随配置默认"、REST body 不带 `mode` 键）
行为与 Stage 8 引入前完全一致；`overall_completion_use_agent` 默认
`false`，行为与 Stage 9 引入前完全一致；单次 `use_agent` 覆盖不传时
（CLI 不带 `--use-agent`/`--no-agent`、REST body 不带 `use_agent` 键）
行为与 Stage 10 引入前完全一致。

## 12. 后续建议顺序

1. 看板 UI 的剩余精细化：字段直接编辑文本框 + 差异高亮（当前"反馈驱动
   迭代"已经能覆盖大多数场景，这一项主要是进一步降低反馈成本）——整体
   关闭判定结果的持久化展示 + 是否挂 Agent 展示**已实施**（Stage 10，
   见 §10.1/§10.2）。
2. `mode="auto"` 补上"LLM 自报 `needs_project_context` 后二次重生成"那层
   兜底（对齐 `GoalSpecBuilder` 的完整三态设计）——优先级较低，当前
   关键词规则 + Stage 8 的单次 `mode` 覆盖入口已经能覆盖多数场景（规则
   漏判时用户可以显式传 `--mode agent`/看板选"只读探索 Agent"绕过，不
   强依赖这层自动兜底）。
