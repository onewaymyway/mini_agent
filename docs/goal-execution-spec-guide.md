# Goal 执行规范指南（GoalExecutionSpec）

> 对应设计与实施记录：`next_doc/goal_execution_spec_generation_plan.md` /
> `next_doc/goal_execution_spec_generation_implementation_record.md`
> 前置阅读：[Goal 与 Cron 绑定指南 · 10. 产出目录规范](goal-cron-binding-guide.md#10-产出目录规范周期性goalcronjob--一次性-goal)
>
> 注意与 [Goal 模式指南](goal-mode-guide.md) 里的 `GoalSpecBuilder`/`GoalSpec`
> 区分：那是单次会话内一次性目标的**验收标准**生成器，属于 `goal_mode/`
> 包，产出确认后驱动 `GoalRunner` 的多轮尝试循环。本文档的
> `GoalExecutionSpecBuilder`/`GoalExecutionSpec` 是架构上的姐妹实现（同一套
> "草稿 → 反馈迭代 → 确认冻结"交互模式），但属于 `GoalBacklog`（跨会话
> Goal/Objective 体系），解决的是"这个 Goal 具体怎么执行"——每一轮该产出
> 什么文件、跨轮次要传递哪些结构化信息、用什么标准判断这一轮做到位了、
> 整个 Goal 什么时候能彻底关闭。两者互不依赖，命名相似但服务不同场景。
>
> 另见：[Goal 执行阶段指南](goal-execution-phase-guide.md)——回答"现在该用
> 什么心态执行"（探索/收敛/稳定/整理），与本文档的"每轮该产出什么"是互相
> 配合、各自独立的两套机制。

## 1. 要解决的问题

`output_workspace.py` 提供的产出目录/`manifest.json`/跨轮传递机制（见
[Goal 与 Cron 绑定指南 · 10](goal-cron-binding-guide.md#10-产出目录规范周期性goalcronjob--一次性-goal)）
是**对所有 Goal 一视同仁的通用管道**——同样的字段、同样的目录命名规则。
但"这个具体的 Goal 每一轮应该产出什么格式的文件、除了通用 `artifacts`
列表之外还需要跨轮次显式传递哪些结构化信息（比如累计处理到第几页、上次
报告里的具体数字）"，这些是 **Goal 特定** 的细节，此前完全没有机制帮用户
想清楚、写下来——只能靠用户在 `description` 里手写，大多数情况下不会写，
或写得不够细。

`GoalExecutionSpec` 补上这一步：像 `GoalSpecBuilder` 生成验收标准一样，
自动生成一份"这个 Goal 具体怎么执行"的结构化规范草稿，用户反馈迭代直到
满意再确认，确认后才会被 `goal_cron_bridge`/`GoalBacklog` 实际读取、拼进
每一轮/每个子任务的执行 prompt。

## 2. 数据结构

`GoalExecutionSpec`（`src/mini_agent/perception/goal_execution_spec.py`），
每个 Goal 一份，独立存储在 `.agent/goal_execution_specs/<goal_id>.json`
（不进 `goals.json`）：

| 字段 | 说明 |
| --- | --- |
| `version` | 修订版本号，`revise()` 每次调用 +1 |
| `confirmed` / `confirmed_at` | 是否已确认冻结；**未确认的草稿不生效** |
| `locked_fields` | 用户已勾选"不用改了"的顶层字段名，`revise()` 时原样保留 |
| `deliverables` | `[{name, description, naming_pattern, required_every_cycle}]`——这个 Goal 每轮/每次期望产出什么文件、格式、命名 |
| `handoff_fields` | `[{key, description, example}]`——需要跨轮显式记住、传递的结构化状态（如 `last_processed_cursor`） |
| `sub_directories` | `[{name, purpose}]`——对通用平铺目录结构的追加子目录组织说明，可留空 |
| `per_cycle_criteria` | `[{text, verification_method}]`——"这一轮算做到位了"的标准，周期性 Goal 主要用；`verification_method` 取 `run_command`/`file_check`/`manual_review`，与 `goal_mode` 的 `GoalSpec` 复用同一套枚举 |
| `overall_completion_criteria` | 同上结构，仅一次性、拆了多个子 Objective 的 Goal 使用，默认留空数组（绝大多数周期性 Goal 不适用"整体完成"这个状态） |
| `special_constraints` | 自由文本列表，过程中需要注意的特殊约束（隐私、不要覆盖某些文件等） |
| `generation_error` | 生成/修订失败时的兜底说明（见 §4 失败兜底） |

`GoalNode` 只新增一个轻量指针字段 `execution_spec_confirmed: bool`
（默认 `False`），供消费方快速判断"这个 Goal 有没有确认过的规范"而不用
每次读一遍独立文件；真正内容仍以独立文件为准。

生成 prompt 明确要求 LLM 把标准尽量往 `file_check`/`run_command` 方向收敛
（例如把"报告要写得详细"改写成"报告文件里必须出现'环比'或'同比'字样"），
只有落到这两种方式的标准会被 §5 的轻量核对机制实际使用，`manual_review`
仍然只作为 prompt 引导，不参与核对。

## 3. 生成器：`GoalExecutionSpecBuilder`

镜像 `goal_mode/spec.py::GoalSpecBuilder` 的"草稿 → 反馈迭代 → 确认"架构：

- **`build_draft(goal_id, goal_title, goal_description, schedule=, task_template=, template_id=, history_manifests=, mode=)`**
  生成第 1 版草稿。支持三种输入源，可组合使用：
  1. **从零生成**：仅基于 Goal 的标题/描述（+ 周期性场景下的调度/任务模板）。
  2. **从模板起步**：传入 `template_id`（见 §6 模板库），模板骨架作为
     few-shot 参考拼进 prompt，LLM 在骨架基础上微调而非空白发挥。
  3. **从执行历史反推**：传入 `history_manifests`（该 Goal 过去若干轮的
     实际 `manifest.json`），让生成结果贴近真实产出，而不是纯凭空想象——
     对"已绑定周期性但从未生成过规范"的既有 Goal 尤其有用。
- **`revise(prior_spec, feedback, locked_fields=)`**：基于"上一版 + 自然
  语言反馈"重新生成，`version += 1`。`locked_fields` 里的字段**代码层面
  强制**用 `prior_spec` 对应值覆盖 LLM 输出（不完全依赖 LLM 是否听话），
  保证"锁定"是硬约束而非软提示。
- **`confirm(spec)`**：置 `confirmed=True`，冻结。
- **失败兜底**：`build_draft()` 解析/调用失败时返回全字段为空的草稿
  （等价于"沿用 `output_workspace.py` 通用行为"）+ `generation_error`
  说明；`revise()` 失败则**保留上一版内容**不清空（修订失败不该让用户
  已确认过的字段凭空丢失），同样附 `generation_error`，`confirmed`
  重置为 `False`。
- **`builder_mode`（`llm`/`agent`/`auto`）**：与 `goal_mode.spec_builder_mode`
  同名同义的三态设计：
  - `llm`：裸单轮 `LLMHelper.ask()`，不连接任何工具/MCP。
  - `agent`：镜像 `GoalSpecBuilder._run_builder_agent()`，起一个只读受限
    Agent（工具白名单限定为只读探索类）先看一眼项目再生成，用于目标本身
    涉及项目内部结构、需要先了解代码/目录现状才能想清楚细节的场景。
  - `auto`（默认）：先用关键词规则粗筛 Goal 描述是否"看起来涉及项目内部
    结构"，命中则直接走 `agent` 路径；规则没命中时先跑一次裸 LLM，若其
    在输出里自报 `needs_project_context=true`（"这次答不准，需要先看
    项目"），则丢弃这次结果、改用 `agent` 路径重新生成一次——与
    `goal_mode/spec.py::GoalSpecBuilder._run_builder` 的三态设计完全对称。
  三态均可在调用时单次覆盖（见 §7.4），不修改配置文件默认值。

## 4. 整体关闭判定：`evaluate_overall_completion()`

仅当 `spec.overall_completion_criteria` 非空时才有意义（多数周期性 Goal
留空，等价于该功能关闭）。独立的一次性 LLM 调用（专用
`prompts/system/goal_overall_completion_judge.md` +
`prompts/user/goal_overall_completion_request.md`，不复用生成草案用的
prompt），对照 `overall_completion_criteria`、全部子 Objective 的标题+
终态、该 Goal 历史全部轮次的 `manifest.json`（`output_workspace.
read_all_manifests()`），逐条核查后输出 `{"decision": "close"|"continue",
"reasoning": str}`。

- 解析失败/调用失败时保守返回 `continue`——"不确定时绝不主动关闭 Goal"，
  与"确认优先于生效"的一贯哲学一致。
- 可选配置 `overall_completion_use_agent`（默认 `false`）：开启后判定器
  复用 §3 的受限只读 Agent 基础设施，可以实际打开该 Goal 产出目录下的
  文件核查内容，而不只依赖 manifest 里的摘要文本；`use_agent` 可在单次
  调用时覆盖配置默认值（CLI `--use-agent`/`--no-agent`、REST body
  `use_agent` 字段、看板"整体关闭判定路径"下拉框）。
- 每次判定结果持久化到 `GoalNode.overall_completion_last_check`
  （`{outcome, reasoning, used_agent, at}`），看板"🔁 手动重判"按钮上方
  常驻展示上一次判什么、什么时候判的、走的哪条路径，不再只是一次性提示；
  只保留"最近一次"，不做历史列表。
- `GoalBacklog.maybe_close_goal_by_overall_criteria()` 在最后一个子
  Objective 完成时自动触发一次；也可手动触发（CLI `spec close-check`、
  看板"🔁 手动重判"按钮），Goal 非 `active` 状态时提前跳过，不消耗
  LLM 调用。

## 5. 消费方：怎么真正影响执行

- `evolution/goal_cron_bridge.py::_fire_goal_cycle()`：在现有
  `_append_output_workspace_context()`（拼"本轮产出目录 + 上一轮 manifest
  摘要"）之后，新增 `_append_execution_spec_context()`——未确认
  （`execution_spec_confirmed=False`）完全不读规范文件，行为与本功能
  引入前一致；确认后把 `deliverables`/`sub_directories`/
  `per_cycle_criteria`/`special_constraints` 格式化文字 + `handoff_fields`
  的填空模板提示一并拼进子 Objective description。
- `perception/goal_backlog.py::add_objectives_for_goal()`：一次性 Goal
  路径对称接入（`_append_execution_spec_prompt_block()`），逻辑相同但
  **不做 §5.1 轻量核对**——一次性 Goal 的子 Objective 之间不是"轮次"
  关系，"连续 N 轮"语义不适用。
- `handoff_fields` 的传递格式固定为 ` ```handoff\n{...}\n``` ` JSON
  代码块，要求 agent 在完成后按 key 精确回答，落进
  `manifest.json.progress_note`；下一轮消费方按 key 精确取值，避免
  "结构化定义、非结构化传递"导致关键信息在自由文本摘要里丢失。

### 5.1 轻量核对（软提示，不拦截）

每轮 `_append_output_workspace_context()` 处理完这一轮 `manifest.json`
后，`soft_check_manifest(spec, manifest)` 做两次纯文件名/key 字符串匹配
（不做任何语义判断）：

- `deliverables` 里 `required_every_cycle=true` 的条目，`naming_pattern`
  是否出现在这一轮 `manifest.json.artifacts` 的文件名里；
- `handoff_fields` 的 key，是否出现在这一轮 ` ```handoff``` ` JSON 块里。

匹配不上时**不判失败、不阻断**：下一轮 prompt 追加一句软性提示；连续
`soft_check_alert_after_cycles`（默认 3）轮都没匹配上，在
`GoalNode.progress_notes` 末尾追加一条"⚠️ 建议复查执行规范"备注（字符串
拼接追加，不覆盖 agent 自己写的进展记录），并置位 `soft_check_alerted`
避免重复提示；一旦某轮重新匹配上，计数器和标记都会清零。只有
`verification_method="file_check"` 的条目和全部 `handoff_fields` 参与
核对，`manual_review` 的标准不参与，仍然只作为 prompt 引导。

## 6. 模板库

`src/mini_agent/perception/goal_execution_spec_templates/*.json`，覆盖 5
类常见 Goal 类型，`build_draft(template_id=...)` 时把骨架作为 few-shot
参考拼进生成 prompt（LLM 仍可对骨架内容增删改，不是机械套用）：

| 模板 | 适用场景 | 骨架要点 |
| --- | --- | --- |
| `periodic_report` | 周报/日报/定期汇总类 | 固定报告文件名模式；`handoff_fields` 预置 `last_reported_metrics`；`per_cycle_criteria` 预置"报告文件存在"（`file_check`） |
| `data_collection` | 定期抓取/采集类 | `sub_directories` 预置 `raw/`；`handoff_fields` 预置 `last_processed_cursor`/`seen_ids`（去重用） |
| `monitoring_patrol` | 巡检/监控类 | `per_cycle_criteria` 预置"异常时是否有明确记录"；`handoff_fields` 预置 `last_known_state` |
| `codebase_maintenance` | 代码维护/清理类 | `per_cycle_criteria` 预置"是否运行了相关测试"（`run_command`）；`special_constraints` 预置"不要修改用户明确排除的目录"占位 |
| `research_exploration` | 调研/学习类，产出较随意 | 骨架最简，只预置一条"调研笔记"，`per_cycle_criteria` 默认 `manual_review` |

看板触发入口（§7.1/§7.3）里支持"自动匹配"：关键词规则粗略匹配 Goal 的
`title`+`description`，命中某个模板则在下拉框里默认预选，用户仍可改选或
选"不用模板"。CLI 目前未接入自动匹配，`--template` 仍要求显式传入
`template_id`。

## 7. 触发入口

### 7.1 看板「⏰ 周期性设置」

- 「设为周期性」表单提交后先展示草稿确认区块（`st.session_state` 暂存，
  不落盘 `confirmed`），分节展示 `deliverables`/`handoff_fields`/
  `sub_directories`/`per_cycle_criteria`/`special_constraints`，每节旁
  有「🔒 这部分不用改了」勾选框（写入 `locked_fields`）。
- 三个操作按钮：
  - **「✅ 确认并设为周期性」**——保存规范 `confirmed=true`，紧接着完成
    周期性绑定；
  - **「🔄 补充意见重新生成」**——文本框输反馈，带上已勾选的
    `locked_fields`，调用 `revise()`；
  - **「📄 从模板重新起草」**——独立按钮，下拉选模板后整段覆盖当前草稿
    重新生成，不需要先放弃已有草稿；
  - 「跳过，不生成规范」——直接走原有绑定流程，不生成规范（规范生成是
    可选增强，不是必经关卡）。
- `revise()`/「从模板重新起草」整段覆盖草稿后，展示**差异高亮**：对比
  新旧两份 JSON，标出新增/删除/改写的条目，纯前端对比、不需要额外 LLM
  调用；只在存在"上一版"可比时展示，第一次生成草稿不受影响。
- 已绑定周期性但从未生成过规范的既有 Goal，追加一个「📋 生成执行规范」
  按钮，走同一套草稿确认流程；如果该 Goal 已跑过若干轮，默认带上"从
  执行历史反推"（读取历史 manifest）。
- 「生成路径」下拉框：单次覆盖 `builder_mode`（`llm`/`agent`/`auto`/
  跟随配置默认），不修改配置文件；草稿区块展示"上次生成走的路径"
  （REST 响应体 `effective_path` 字段）。

### 7.2 看板「🔁 手动重判整体是否可以关闭」

Goal 卡片内，「整体关闭判定路径」下拉框（单次覆盖 `use_agent`）+ 按钮，
按钮上方常驻展示 `overall_completion_last_check` 的上一次判定结果。

### 7.3 看板「➕ 新建目标」

新建表单新增一个「同时生成一次性 Goal 的执行规范（用于会拆多个子任务的
场景）」复选框，**默认不勾选**（多数临时创建的一次性 Goal 是随手记的，
是否投入一次 LLM 调用去想细节应由用户主动决定）；勾选后创建成功走同一套
草稿确认流程，适用于会拆多个子 Objective、`overall_completion_criteria`
才有意义的场景。

表单下方还有一个独立的「🔍 查找相似的历史执行规范」按钮
（`next_doc/cross_goal_experience_reuse_plan.md`）：点击后用当前填写的
标题/描述，在**已确认过执行规范**的历史 Goal 里做一次轻量文本相似度
匹配（`difflib`，不引入向量检索），把相似度足够高的候选连同对方的
`render_summary_for_user()` 摘要展示出来。纯查询，不修改任何状态，也
不会自动把匹配到的规范套用到新 Goal 上——用户自己判断要不要参考着写，
这是"避免同类型 Goal 各自从头踩坑"的最小可行版本，不是自动化。

### 7.4 CLI

```bash
/agent goals spec generate <goal_id> [--template <id>] [--from-history] [--mode llm|agent|auto]
/agent goals spec confirm <goal_id>
/agent goals spec show <goal_id>
/agent goals spec close-check <goal_id> [--use-agent | --no-agent]
```

- `generate`：调用 `build_draft()` 并落盘（未确认）；`--mode` 单次覆盖
  `builder_mode`，不改配置文件；`--from-history` 目前只取该 Goal
  **最新一轮** `manifest.json`（方案原描述为"过去若干轮"，`build_draft()`
  本身的 `history_manifests` 参数已支持传入列表，CLI 侧尚未扩展收集逻辑，
  见 §8 未实施清单）；`--template` 需要显式传入 `template_id`，CLI 未接入
  §6 的自动匹配。
- `confirm`：加载已有草稿、`confirm()`、落盘，并把
  `GoalNode.execution_spec_confirmed` 置 `True`。
- `show`：打印规范当前内容摘要。
- `close-check`：直接调用 `maybe_close_goal_by_overall_criteria()`，
  `--use-agent`/`--no-agent` 单次覆盖 `overall_completion_use_agent`；
  Goal 非 `active` 时提前跳过。
- `/agent goals recur` 命令本身不强制依赖规范存在；没有已确认规范时会
  提示一句"可以先 `/agent goals spec generate` 想清楚细节，或直接继续"。

## 8. 配置项

`config/models.py::GoalExecutionSpecConfig`，挂在
`AppConfig.goal_execution_spec`：

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 总开关；关闭后看板/CLI 的规范生成入口隐藏，消费方也不读取任何已确认规范 |
| `builder_mode` | `"auto"` | `"llm"`/`"agent"`/`"auto"`，见 §3 |
| `builder_model` / `builder_provider` | `None` | 生成器使用的模型/provider，留空回退主模型 |
| `prompt_on_recur` | `true` | 看板"设为周期性"表单是否默认展示"生成规范"步骤（第一版看板 UI 尚未读取这个字段，见 §9） |
| `soft_check_enabled` | `true` | §5.1 轻量核对总开关 |
| `soft_check_alert_after_cycles` | `3` | 连续多少轮未匹配上才追加"建议复查执行规范"备注 |
| `overall_completion_use_agent` | `false` | §4 整体关闭判定是否默认使用受限 Agent 核查产出文件内容（单次调用可覆盖） |

`enabled=false`、`overall_completion_criteria` 为空、`execution_spec_
confirmed=False` 均等价于"该 Goal/该能力不受本功能影响"，与方案引入前
行为完全一致——本功能全程是**可选增强**，不是新增的必经关卡。

## 9. 已知限制 / 未实施清单

- 看板每个 section 目前只能"看摘要 + 写反馈 + 重新生成"，不支持直接
  编辑某个字段的具体文字（按行拆分的文本框），微调一个字都要走一次
  `revise()` 的 LLM 调用。
- CLI `--from-history` 只取最新一轮 manifest，不是"过去若干轮"。
- CLI 未接入模板自动匹配（§6），`--template` 需显式指定。
- `evaluate_overall_completion()` 只保留"最近一次"判定结果，不做历史
  列表；差异高亮只做条目级对比，不做字符级行内 diff。
- `prompt_on_recur` 配置项当前无看板 UI 实际读取（看板固定展示"生成
  规范"步骤，用户可点"跳过"）。
- §5.1 轻量核对是纯字符串匹配，不做语义判断，不能识别"内容对但文件名
  拼错了"之类的情况；`manual_review` 标准完全不参与核对。
- 不做规范的多版本历史 UI，只保留"当前生效版本"；也不做旧数据迁移或
  强制校验/硬拦截——始终是 prompt 层引导，不会因为 agent 没按规范产出
  就自动判定该轮失败。

详细的阶段划分（Stage 1 ~ Stage 12）、每一步的取舍论证，见
`next_doc/goal_execution_spec_generation_implementation_record.md`；
设计论证的完整版见 `next_doc/goal_execution_spec_generation_plan.md`。
