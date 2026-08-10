# Goal 执行规范自动生成 + 用户确认机制 —— 实施记录（Stage 1 + Stage 2）

对应设计文档：`next_doc/goal_execution_spec_generation_plan.md`

本记录覆盖 Stage 1（后端核心能力）+ Stage 2（`overall_completion_criteria`
驱动的一次性 Goal 整体关闭判断）的实施情况，不重复方案本身的设计论证，只
记录"最终落地成什么样、和方案哪里不同、还差什么"。

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

## 3. 与方案的偏差 / 未实施清单

以下条目方案里有描述，**未实施**，留作后续 Track：

1. **看板 UI（§6.1/§6.2/§6.3）**：`apps/mini_agent_kanban/app.py` 的
   "⏰ 周期性设置"/"➕ 新建目标"表单尚未接入草稿确认区块、字段级锁定
   勾选框、差异高亮。当前只能通过 CLI（`/agent goals spec ...`）生成/
   确认/查看规范。这是本次范围内最大的缺口——方案里"看板"是用户
   反馈的主要触发场景，目前只有 CLI 路径可用。§2 新增的"整体关闭
   判定"结果（`✅`/`ℹ️` 两种 progress_notes）目前也只能在看板"进展记录"
   区块里以纯文本形式看到，没有专门的状态徽标/提示样式。
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
5. **看板"从执行历史反推"默认预填**、**差异高亮 UI**：均依赖 1，随看板
   UI 一起实施。
6. **CLI 侧暴露"整体关闭判定"的手动触发/查看入口**：目前
   `maybe_close_goal_by_overall_criteria()` 只在 Objective 正常完成的
   自动路径里触发，没有对应的 `/agent goals ...` 子命令让用户在不新增
   子 Objective 的情况下手动重新触发一次判定（比如判定结果是
   `continue` 之后，用户后续手动补充了一些材料，想重新判一次）。

以上未实施项均不影响已实施部分的正确性——`execution_spec_confirmed`
默认 `False`，未生成/未确认的 Goal 行为与方案引入前完全一致；
`overall_completion_criteria` 为空（绝大多数周期性 Goal 的默认情况）
时 §2 的判定逻辑不会被触发，等价于该功能关闭。

## 4. 后续建议顺序

1. 看板 UI（§6.1 最小可用版本：展示 5 个 section + 反馈文本框 + 确认/
   跳过两个按钮即可，字段级锁定/差异高亮可以作为该 Track 内的第二个
   迭代，不必一次做全；同时把 §2 的整体关闭判定结果做成看板上更显眼的
   状态展示，而不只是 progress_notes 里的一行文本）。
2. CLI 侧补一个手动重新触发整体关闭判定的命令（见 §3 第 6 条），成本低，
   可以和看板 UI 并行。
3. `builder_mode="agent"` 路径 + 模板自动匹配（优先级较低，当前 `llm`
   路径已经可用，且 `revise()` 的字段锁定机制已经能覆盖"生成方向不对
   需要人工干预"的场景）。
