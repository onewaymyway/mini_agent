---
name: workflow-generator
description: 帮助用户创建符合 mini_agent 最新版规范的 workflow（.agent/workflows/<name>/workflow.yaml，可携带私有 agents/skills/prompts；支持顶层 defaults 默认配置继承、可复用 step 片段 include、max_total_tokens 用量护栏、mode: autonomous 全自动执行模式、插件自定义 step 类型、foreach/wait/merge 批处理与汇聚类型、tool_call 占位符入参、result_file 字段级占位符）。当用户说"帮我创建一个workflow"、"做一个工作流"、"写一个xxx流水线"、"把这个workflow转成文件夹模式"、"做一个全自动/挂后台跑的workflow"、"对一批数据逐个处理"、"把几个step的结果合并一下"时使用。
triggers: workflow, 工作流, 流水线, pipeline, 创建workflow, workflow.yaml, 文件夹模式workflow, 工作流模板, list_workflow_templates, create_workflow_from_template, preview_workflow, output_export_dir, foreach, merge, wait, tool_call, result_file
---

# Workflow Generator（文件夹模式）

用于创建符合本项目 `WorkflowStore`/`WorkflowDef`/`WorkflowStep`
（`src/mini_agent/workflow/schema.py`、`src/mini_agent/workflow/store.py`）
解析规范的 workflow。优先按"文件夹模式"创建——除非用户明确要求单文件、
或工作流足够简单（≤2 步、不需要私有 agent/skill/prompt 文件）。

完整设计背景见 `next_doc/workflow_directory_mode_design.md`，可运行的完整
示例见 `.agent/workflows/doc_change_review/`（本 skill 生成的内容应与它
风格一致）。批处理/汇聚/占位符相关的最新规范来自 P12-P15 四轮迭代（
`next_doc/workflow_mechanism_improvement_plan_p12.md` ~ `_p15.md`），完整
细节见 `docs/workflow-guide.md`，本 skill 只摘录生成时需要的规则。

## 两种模式，如何选择

| | 单文件模式 | 文件夹模式 |
|---|---|---|
| 路径 | `.agent/workflows/<name>.yaml` | `.agent/workflows/<name>/workflow.yaml` |
| 适用场景 | 简单、prompt 内嵌几行文本就够、不需要专属 agent/skill | 复杂流水线、prompt 较长、需要工作流私有的角色 agent 或 skill |
| 私有资源 | 不支持 | 支持 `agents/`、`skills/`、`prompts/` |

`WorkflowStore.load(name)` 优先找文件夹模式，找不到再退回单文件模式，
两者可以在 `.agent/workflows/` 下共存，不冲突。

## 动手写之前先看看有没有现成模板

除了从零写 YAML，还可以用 `list_workflow_templates()` 看内置模板
（`code_review`/`research_report`/`multi_perspective_debate` 等，定义在
`src/mini_agent/workflow/templates/*.yaml`），需求和某个模板高度相似时用
`create_workflow_from_template(template_name, new_name)` 直接复制一份、
换个名字保存，比手写更稳（模板本身已经过校验），后续再用
`patch_workflow_step`（workflow-debugger skill）微调具体字段。只有需求和
现有模板差异明显、或用户要求"自己设计流程"时才走下面的手写流程。

## 另一条生成路径：从已完成的 session 反向生成

本 skill 覆盖"用户用自然语言描述一个流程，帮他从零写 YAML"这条路径。如果
用户想要的是"把之前某次 session 里实际做成的一件事沉淀成 workflow"（比如
"把刚才那次修 bug 的过程整理成一个 workflow"），不要在这里手写 YAML——
这是 `summarize_session_for_workflow` → `build_workflow_from_summary` 两个
Agent 工具（或 CLI `/workflow from-session <session_id>`）的场景，设计见
`next_doc/session_to_workflow_design.md`，使用说明见
`docs/workflow-guide.md`"从历史 Session 生成 Workflow"一节。区分方式：
用户给的是"一段流程描述"→ 本 skill；用户指的是"之前发生过的一次具体任务"
→ 那两个工具。该路径受 `agent_config.json` 里
`workflow.session_to_workflow_enabled` 开关控制（默认开启），关闭后这两个
工具和对应 CLI 子命令会返回"功能已关闭"提示。

## 再另一条边界：测试、调试、修改已有 workflow

本 skill 只覆盖"从零生成/保存一个新 workflow"。如果用户是针对**已经存在**
的 workflow 说"跑一下试试"、"这步失败了帮我看看"、"改一下这个 step 的
prompt/timeout"、"这个改动会不会影响后面的 step，先测一下"，这些是
**workflow-debugger** skill 的场景（沙箱单步测试 `test_workflow_step`、
断点续跑 `resume_workflow_run`、单步修改 `patch_workflow_step`、执行统计
`get_workflow_stats` 等），不要在这里手工排查或重新走一遍生成流程。

**默认按文件夹模式创建**：即使当前只有 2-3 步，文件夹模式也不吃亏
（`agents/`/`skills/` 目录为空即可，不强制使用），而且用户后续加步骤、
加私有资源时不需要再迁移。只有用户明确说"就要单文件"、"简单点不要建
文件夹"时才退回单文件模式（直接写 `.agent/workflows/<name>.yaml`，字段
和文件夹模式下的 `workflow.yaml` 完全一样，只是没有 `agents/`/`skills/`/
`prompts/` 子目录、也不能用 `prompt_file`/本地 `role`/本地 `skill_agent`）。

## 目录结构（文件夹模式）

```
.agent/workflows/<name>/
  workflow.yaml              必需，主入口
  agents/                    可选，工作流私有 agent profile（同 .agent/agents/*.md 格式）
    <role>.md
  skills/                    可选，工作流私有 skill（同 .claude/skills 目录格式）
    <skill-name>/
      SKILL.md
  prompts/                   可选，抽出来的 prompt 模板文件
    <step_id>.md
```

- `agents/`、`skills/`、`prompts/` 都是可选的；用不到就不创建，或创建成
  空目录也没关系（`AgentProfileLoader`/`SkillLoader` 会自动跳过空目录）。
- `agents/*.md` 的写法和全局 `.agent/agents/*.md` 完全一样（可以参考
  `.claude/skills/agent-generator/SKILL.md`），区别只是它只在**这一个
  workflow** 内生效、且同名时优先于全局/项目级同名 profile。
- `skills/<name>/SKILL.md` 的写法和 `.claude/skills/<name>/SKILL.md`
  完全一样，区别同样是只在这一个 workflow 内生效（配合
  `type: skill_agent` 使用，见下文）。

## workflow.yaml 字段规范

顶层字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | 是 | 工作流唯一标识，建议与文件夹名一致 |
| `description` | 建议 | 一句话说明这个工作流做什么、什么场景下用 |
| `version` | 否 | 字符串，缺省 `"1.0"` |
| `steps` | 是 | step 列表，见下 |
| `max_total_duration` | 否 | 整体超时（秒），缺省走全局配置 |
| `max_total_tokens` | 否 | 整体 token 用量护栏，超过则看护线程主动取消该次执行，缺省走全局配置（只统计 `agent`/`skill_agent` 类型 step） |
| `defaults` | 否 | 一个 dict，给 `model`/`timeout`/`max_turns`/`retry_on_error`/`allow_parallel` 这 5 个 step 字段提供统一默认值，减少重复。见下方"善用 `defaults`" |
| `mode` | 否 | `interactive`（默认）或 `autonomous`。**用户明确表示这个 workflow 要"全自动跑完、中途不能等人"时必须设为 `autonomous`**，见下方"全自动执行模式"一节；保存时会校验，混入阻塞点会直接报错，不是等运行时才发现卡住 |

单个 step 的字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | 步骤唯一标识，其他 step 通过 `{id.output}` / `{id.score}` 引用它的结果 |
| `name` | 建议 | 可读名称 |
| `type` | 建议显式写 | 见下方"Step 类型"表；不写时按 `role` 是否为空自动推断为 `agent`/`role_agent`（向后兼容旧写法，新建议一律显式写 `type`）。也可以是插件通过 `register_step_executor()` 注册的自定义类型（不在下表里，见"自定义/插件 Step 类型"一节）。P13/P14 新增 `foreach`/`wait`/`merge` 三种内置类型，见下方专门一节 |
| `include` | 与 `prompt` 二选一 | 引用 `.agent/workflow_snippets/<n>.yaml` 里的一段可复用 step，见下方"善用可复用 step 片段（`include`）"。填了这个之后该 step 条目本身只需要 `id`（作为命名空间前缀）+ `depends_on`（挂接点），`prompt`/`type` 等字段不需要填、填了也会被忽略 |
| `prompt` | 二选一 | 内嵌 prompt 文本，支持 `{参数名}`、`{其他step_id.output}`、`{其他step_id.score}` 占位符 |
| `prompt_file` | 二选一 | 相对 workflow 所在目录的相对路径，如 `prompts/analyze.md`；与 `prompt` 都填时 `prompt_file` 优先；**文件夹模式下一律优先用这个而不是内嵌 `prompt`**——内联 `prompt` 超过 5 行时 `validate()` 会给出 warning（不阻断保存，但生成新 workflow 时应主动避免） |
| `role` | `role_agent` 专用 | 角色 agent 名，优先匹配本工作流 `agents/<role>.md`，没有则退回全局 `.agent/agents/` |
| `skill_name` | `skill_agent` 专用 | skill 名，优先匹配本工作流 `skills/<skill_name>/SKILL.md`，没有则退回全局 skills 目录 |
| `depends_on` | 否 | 依赖的 step id 列表，决定拓扑执行顺序；同一"层"（无相互依赖）的 step 默认并发执行 |
| `condition` | 否 | 形如 `"evaluate.score >= 6"` / `"review.output != ''"` 的表达式，不满足则跳过该 step（`SKIPPED`）。**注意（P12）**：如果表达式本身写错（引用了不存在的 step、或此刻类型不对导致 `AttributeError`/`TypeError`），求值会抛异常，这种情况判的是 `NEEDS_FIX` 而不是 `SKIPPED`——生成时如果不确定某个字段这时是否已就绪，先用 `preview_workflow`/`test_workflow_step` 验证 |
| `allow_parallel` | 否 | 不写（`null`）时按"本字段 → 顶层 `defaults.allow_parallel` → 硬编码兜底 `true`"三层继承；step 有隐式副作用（读写同一外部状态）时显式设为 `false` 强制串行 |
| `max_turns` | 否 | 该 step 允许的最大 LLM 轮数；不写时同样三层继承，硬编码兜底 `10` |
| `model` | 否 | 覆盖该 step 用的模型；不写时三层继承（`defaults.model` → 全局配置） |
| `timeout` | 否 | 该 step 超时（秒）；不写时三层继承，硬编码兜底为不限制 |
| `retry_on_gate_fail` | 否 | evaluator 质检门不达标时重跑前序步骤的最大次数，仅对 `role_type: evaluator` 的角色有意义。**不参与 `defaults` 继承**，缺省就是 `0`，与其它几个"三层继承"字段是两套独立机制 |
| `retry_on_error` | 否 | 普通异常（超时/工具报错）重试次数；不写时三层继承，硬编码兜底 `0` |
| `escalate_after_n_same_failures` | 否 | 同一 step 在 `retry_on_error` 重试循环里连续（中间没成功）出现同一个 `error_type` 达到这个次数时，看护线程提前判定"大概率不是瞬时故障"，直接标记 `needs_fix`、跳过剩余重试预算；不写时三层继承（`defaults` → 全局默认 `2`），是与 `max_turns` 等同类的"三层继承"字段之一，`resume_workflow_run(step_overrides=...)` 也支持临时覆盖它，见新增的 workflow-debugger skill |
| `require_approval` | 否 | 是否需要人工审批门放行，需配合 `run_workflow(background=True)` |
| `workflow_name` | `sub_workflow` 专用 | 引用的另一个已保存工作流名称，不能引用自身 |
| `tool_name` / `tool_args` | `tool_call` 专用 | 直接调用某个已注册工具，不启动整个 Agent。`tool_args`（P12 起）支持占位符——值是字符串时可以写 `{step_id.output}`/`{step_id.result_file:a.b[0].c}` 等，嵌套 `dict`/`list` 里的字符串叶子节点同样会被替换，纯字面量值不受影响；引用的 step 同样要出现在 `depends_on` 里，`validate()` 会检查 |
| `input_prompt` | `human_input` 专用 | 展示给人类的提示语，缺省用 `prompt` 本身 |
| `input_key` | `human_input` 专用 | 若启动 `run_workflow(inputs={...})` 时能通过该 key 找到值，直接使用、不阻塞等待；`mode: autonomous` 的 workflow 里 `human_input` 步骤**必须**设置这个字段，否则保存时校验失败。见下方"全自动执行模式"一节 |
| `script` | `script` 专用 | 要执行的 shell 命令，受 `cfg.workflow.script_step_enabled` 开关保护，默认关闭 |
| `script_path` | `python_step` 专用 | 相对 workflow 目录的脚本路径（如 `steps/03_filter.py`），受 `cfg.workflow.python_step_enabled` 开关保护，默认关闭；脚本须暴露 `def run(ctx: PyStepContext) -> str\|dict` |
| `params` | 否，`python_step` 常用 | 透传给 `python_step` 脚本的自定义参数字面量（dict），脚本内通过 `ctx.params` 读取 |
| `output_file` | 否，通用于所有 type | 该 step 执行完成后，输出统一落盘到当前 workflow session 的 `output/<output_file>`，不管是哪种 type 产生的输出；下游 step 可用 `{该step.output_file}` 占位符引用落盘文件的绝对路径 |
| `result_file` / `result_file_required_keys` | 否，`skill_agent`/`script` 通用（P15 起） | 期望执行方主动产出的结构化结果文件名 + 必须包含的顶层字段。`skill_agent` 通过 prompt 注入路径文字告知 Agent；`script`（P15 起）通过环境变量 `WORKFLOW_RESULT_FILE_PATH` 注入子进程 env，脚本自行写文件。下游可用 `{该step.result_file}`（路径）或 `{该step.result_file:a.b[0].c}`（P12，直接取字段值）引用 |
| `items` | `foreach` 专用 | 要遍历的列表：字面量列表，或单个占位符字符串（如 `"{search.result_file:questions}"`，此时取解析出的原始 Python list） |
| `foreach_step` | `foreach` 专用 | 内层每个元素要执行的 step 定义子集（须含 `type`），内层 prompt 支持 `{item}`/`{item_index}`，不能是 `foreach`（禁止嵌套） |
| `foreach_max_concurrency` | 否，`foreach` 专用 | 内层并发度，默认 `1`（串行），与外层 `allow_parallel` 是独立的并发维度 |
| `foreach_stop_on_error` | 否，`foreach` 专用 | 默认 `false`：单元素失败记入聚合结果、不影响整体；`true`：第一个失败即整体抛异常交给 `retry_on_error`/`NEEDS_FIX` |
| `wait_seconds` | `wait` 专用 | 要等待的秒数（正数），可被 `pause_workflow_run`/`cancel_workflow_run` 打断 |
| `merge_sources` | `merge` 专用 | 要汇聚的上游 step id 列表，顺序即聚合顺序，须非空、无重复，且都要出现在 `depends_on` 里 |
| `merge_strategy` | 否，`merge` 专用 | `concat_text`（默认，拼接 `output` 文本）/ `json_array`（组成 JSON 数组）/ `json_merge`（按顺序 `dict.update`，须为 JSON object） |
| `merge_separator` | 否，`merge` 专用 | `concat_text` 策略下的拼接分隔符，默认 `"\n\n"` |
| `merge_use_result_file` | 否，`merge` 专用 | `true` 时 `json_array`/`json_merge` 从各来源的 `result_file` 读取；`false`（默认）读 `output` 文本 |

> **写法提示**：`max_turns`/`model`/`timeout`/`retry_on_error`/`allow_parallel`/
> `escalate_after_n_same_failures` 这 6 个字段现在语义是"不写=继承"而不是
> "不写=固定默认值"——生成 workflow
> 时，如果多个 step 都要用同一个非默认值（比如整个流水线都想用
> `model: gpt-4.1-mini`、`max_turns: 6`），**优先写进顶层 `defaults`**，
> 不要在每个 step 里重复写一遍；只有某个 step 需要跟其它 step 不一样时才
> 在该 step 上单独覆盖。

### Step 类型（`type`）

| `type` | 语义 |
|---|---|
| `agent`（默认） | 主 Agent 执行。文件夹模式下会自动带上本工作流的 `agents/`/`skills/`，因此这一步内部通过 `spawn_named_agent` 也能看到本地 agent，技能触发/`skill_activate` 也能看到本地 skill |
| `role_agent` | 用指定 `role` 的角色 agent 执行（优先本地 `agents/<role>.md`，找不到退回全局） |
| `skill_agent` | 临时启动一个只强制挂载 `skill_name` 对应 skill（不走关键词触发判断）的最小 Agent 执行 `prompt`（优先本地 `skills/`，找不到退回全局） |
| `sub_workflow` | 调用另一个已保存的工作流（`workflow_name`）。**注意**：子工作流不继承父工作流的本地资源包，子工作流按自己的 `source_dir` 独立解析 |
| `tool_call` | 直接调用某个已注册工具，不启动 Agent，适合纯确定性操作 |
| `human_input` | 暂停等待人工输入，配合 `/workflow input` |
| `script` | 执行 shell 命令（默认关闭，需要显式开启配置项） |
| `python_step` | 在独立子进程里跑一段外置 `.py` 脚本（`script_path`），不启动 Agent，适合"给定输入产出结构化 JSON"这类确定性数据加工（默认关闭，需要显式开启 `python_step_enabled`）。真正需要临场应变（如浏览器交互）的步骤仍应使用 `agent`/`skill_agent`，见下方"`python_step`：脚本外置与批量处理"一节 |
| `foreach`（P13） | 对一个列表逐元素执行同一份 `foreach_step` 定义（可控并发度），结果聚合成 JSON 数组。**"先搜出一批候选、再逐个 enrich"这类场景应该用这个类型，而不是在 `python_step` 里手写循环调 `ctx.run_agent_turn()`**——那样会把编排层的逻辑硬编码进脚本，YAML 完全看不出这里其实是批处理 |
| `wait`（P13） | 等待指定秒数，可被 pause/cancel 信号打断。用于限速节流、等一段固定时间；不要用 `python_step` 里 `time.sleep` 代替，那会跟子进程超时/watchdog 硬超时打架 |
| `merge`（P14） | 把多个上游 step 的结果按策略汇聚成一个 step 的输出（拼接文本 / 组数组 / 合并对象）。多分支/多并发结果需要汇总时用这个类型，而不是在某个 step 的 prompt 里手写 `{a.output}{b.output}` 拼接 |

### 选型强制规则：`python_step` 优先，`agent` 是兜底方案

**每生成一个 step 前都要先过一遍这个判断，不是可选的风格偏好**：

0. 这一步的本质是"对一个列表的每个元素重复执行同一份逻辑，再把结果聚合
   成一个列表"吗（比如"先搜出一批候选、再逐个 enrich/打分/提取"）？是的话
   **必须**用 `type: foreach`（见下方"批处理与汇聚"一节），**不要**在
   `python_step` 脚本里手写 `for` 循环调 `ctx.run_agent_turn()`——那样会把
   本该是编排层的批处理逻辑硬编码进脚本，YAML 完全看不出这里其实是批处理，
   调试时也没法单独用 `test_workflow_step` 验证某一个元素的处理逻辑。
1. 这一步的功能能不能用 `python_step` + `ctx.llm`/`ctx.llm.ask_json` 解决
   （确定性加工、或"给模型一段明确输入、要求它给出结构化判断"这类单次/
   分批调用）？**能的话必须用 `python_step`**，即使这一步涉及"要不要收
   录这条数据""打几分"这类看起来需要判断力的任务——只要能把判断要求写清楚
   成一次 `ask_json` 调用，就不需要升级成 `agent`。
2. 不能的话，这一步是否可以约束在"强制挂载单一 skill、按固定套路多轮
   调用"的范围内？可以的话用 `skill_agent`，不要用无约束的 `agent`。
3. 只有步骤确实需要临场应变（工具选择、页面结构、下一步动作都要运行时
   才能确定，比如真实浏览器交互、开放式调研）时，才生成 `type: agent`/
   `role_agent`——**`agent` 是前两种方案都覆盖不了时才用的兜底选项**，
   不是"不确定用什么类型就先写 agent"的默认落点。

生成 workflow 时如果发现自己因为"图省事、不想让用户额外去开
`python_step_enabled` 开关"而把一个本该是 `python_step` 的确定性加工步骤
写成了 `agent`，这是错误的取舍——应该按规则生成 `python_step`，并在结果里
提示用户开启开关，而不是为了"少一步配置"牺牲步骤的确定性和稳定性。

### `skill_agent`（或需要结构化产出的 `agent`/`role_agent`）：务必配 `result_file`

`skill_agent` 的产出是一段自由文本对话，如果下游是 `python_step`（或任何
需要拿到合法 JSON 的场景），**不要**指望靠 `{step_id.output}` 占位符或
`ctx.input_output()` 解析对话原文——模型经常在 JSON 前后夹杂解释文字，
解析很脆弱。生成这类 step 时按下面的写法：

```yaml
- id: search_zhihu
  type: skill_agent
  skill_name: browser-cdp
  prompt_file: prompts/02_search_zhihu.md
  output_file: search_results.json        # 对话原文存档，供人工排查
  result_file: search_results_data.json   # 下游真正消费的结构化产物
  result_file_required_keys: [questions]  # 必须包含的顶层字段
  max_turns: 25
  timeout: 900
```

- 声明了 `result_file` 后，runner 会自动在 prompt 末尾追加"必须用文件写入
  工具把结果写到这个绝对路径"的指令并在结束后校验；校验失败会自动
  resume/重开 agent 补救（最多 3+3 次），不需要自己在 prompt 里手写这段
  指令、也不需要在 `python_step` 脚本里自己兜底解析脏 JSON。
- 下游 `python_step` 通过 `ctx.input_json("search_zhihu")` /
  `ctx.input_output("search_zhihu")` 取值时，只要上游声明了 `result_file`
  且校验通过，会**优先读文件**而不是对话原文——这也是为什么
  `result_file_required_keys` 要如实列出下游脚本真正会用到的顶层字段，
  避免"文件写了但少了个字段，下游脚本才发现"这种情况。
- **一定要在 prompt 里额外加一句"写完文件并自检通过后立即收尾，不要再做
  其它操作"**：`result_file` 只在这一轮 agent 自然结束后才会被校验，agent
  如果写完文件还继续浏览/反复确认，即使结果早就产出了，这一步也要等它
  自己收尾才会往下走，实测会明显拖慢整体执行时间。不写这句提示是目前
  生成的 workflow 里最容易漏掉、也最容易导致"文件已经生成但迟迟不结束"
  的一个点。


### 自定义/插件 Step 类型

以上是内置的 11 种类型（`agent`/`role_agent`/`sub_workflow`/`tool_call`/
`human_input`/`script`/`skill_agent`/`python_step`/`foreach`/`wait`/
`merge`）。项目还可能通过 `myplugins/*.py` 里的
`register_step_executor()` 注册了自定义类型（比如示例插件
`myplugins/example_http_step.py` 注册的 `type: http`）。生成 workflow
前，如果不确定项目里有没有这类插件、支持哪些自定义类型，可以查一下：

```python
from mini_agent.workflow.executors import get_registered_types
print(get_registered_types())   # 内置 11 种（含 P13/P14 的 foreach/wait/merge）+ 插件注册的自定义类型
```

用户明确要求某个自定义类型（如"用 http 类型调一下这个接口"）时才使用；
不确定该类型需要哪些专属字段时，去看对应插件源码里的
`StepExecutor.validate_step()` 实现，或直接问用户。不要凭空编造一个不存在
的 `type`——`WorkflowDef.validate()` 会在保存时因"type 非法"直接报错。

## `python_step`：脚本外置与批量处理

完整规范见 `next_doc/workflow_authoring_guide.md`，参考实现见
`.agent/workflows/zhihu_content_publish/`（4 个 step、4 个 prompt 文件、
2 个 `python_step` 脚本）。生成含 `python_step` 的 workflow 时遵循：

1. **脚本代码一律外置到 `steps/*.py`**，`workflow.yaml` 只写
   `script_path: steps/xx.py`，不要把脚本内容内嵌进 YAML。
2. **脚本入口函数签名固定**：`def run(ctx: PyStepContext) -> str | dict:`。
   `ctx` 提供 `ctx.llm.ask()`/`ctx.llm.ask_json()`（LLM 调用）、
   `ctx.run_agent_turn()`（临时起最小 Agent 处理需要判断力的子任务）、
   `ctx.params`（`workflow.yaml` 里该 step 的 `params` 字面量）、
   `ctx.inputs`/`ctx.input_output()`/`ctx.input_json()`（读上游 step 结果，
   **默认只包含该 step `depends_on` 里声明过的上游**，需要读取某个历史
   step 必须先把它加进 `depends_on`）、`ctx.load_prompt_file()`（读
   `prompts/*.md`）、`ctx.write_output()`（往 output_dir 落盘中间产物）。
3. **默认关闭**：生成含 `python_step` 的 workflow 后，要提示用户在
   `agent_config.json` 里显式开启 `{"workflow": {"python_step_enabled":
   true}}`，否则运行时会被直接拦截。
4. **多条候选数据的判断类场景走批量而不是逐条**：比如"从 N 条候选里筛选
   符合要求的"，脚本内部分批调用 `ctx.llm.ask_json()`（配合独立的
   `prompts/xx_batch.md` 模板，要求模型逐条给出 `id`+判断+理由的 JSON
   数组），而不是对每条数据单独起一次 LLM 调用——省 token、省延迟。批量
   调用返回的判断数量明显少于批次数量（漏判）时，应该拆成更小的子批重试
   而不是直接丢弃，可参考
   `.agent/workflows/zhihu_content_publish/steps/03_filter.py` 的
   `BATCH_SIZE`/`MISS_RATIO_THRESHOLD`/`MIN_SUB_BATCH` 写法。
5. **`search_zhihu`/`enrich_questions` 这类真实浏览器交互步骤仍用
   `skill_agent`**，不要为了"统一用 python_step"而把需要临场应变的步骤也
   硬写成脚本——页面结构会变、要应对弹窗/滚动加载，脚本硬编码的稳定性
   反而更差。

`output_file` 契约对 `python_step` 同样适用（且是通用契约，不限于这一种
type）：声明了 `output_file` 之后，脚本 `return` 的内容会被 runner 自动
落盘到 session `output/` 目录，脚本本身不需要自己拼路径写文件；下游 step
可以用 `{该step.output_file}` 占位符拿到落盘文件的绝对路径，提示
`agent`/`role_agent` 类型的 step 直接读文件而不是把整段 JSON 塞进 prompt。

```yaml
- id: analyze_doc
  type: python_step
  script_path: steps/01_analyze_doc.py
  output_file: doc_analysis.json
  timeout: 120

- id: filter_questions
  type: python_step
  script_path: steps/03_filter.py
  depends_on: [analyze_doc, search_zhihu]   # ctx.inputs 只会包含这两个 step
  output_file: filtered_questions.json
  timeout: 300
```

## 批处理与汇聚：`foreach` / `wait` / `merge`（P13/P14）

三种类型都以纯 `StepExecutor` 插件形式实现，不改变外层拓扑调度语义——从
外部看它们跟其它 step 类型没有区别（一份输入、一段输出，`output_file`/
评分提取/`NEEDS_FIX` 判定都通用）。完整机制细节见
`docs/workflow-guide.md`"批处理与汇聚"一节，这里只给生成时要遵循的规则。

### `foreach`：对列表逐元素批处理

```yaml
- id: enrich_each_question
  type: foreach
  depends_on: [search_zhihu]
  items: "{search_zhihu.result_file:questions}"   # 引用上游 result_file 字段，解析为原始 list
  foreach_max_concurrency: 3                        # 默认 1（串行），需要并发时显式调大
  foreach_stop_on_error: false                      # 默认单元素失败不影响其它元素
  foreach_step:
    type: skill_agent
    skill_name: browser-cdp
    prompt: "打开问题详情页并提取正文：{item}（第 {item_index} 条）"
    max_turns: 15
```

生成规则：
- `items` 只能是字面量列表，或**单个**占位符字符串（不能是拼接了别的文本
  的模板）；引用别的 step 的 `result_file` 字段时用
  `{step_id.result_file:field_path}` 语法（同 P12 的 `result_file` 字段
  访问），会拿到解析出的原始 Python list，不是文本。
- `foreach_step` 必须显式写 `type`，且**不能是 `foreach`**（禁止嵌套，
  `validate()` 会直接拒绝）——遇到"批处理里还要批处理"的需求，说明这一步
  本身该拆成两个 workflow 或换个粒度设计，不要硬套嵌套。
- 内层 prompt 只有 `{item}`/`{item_index}` 两个占位符可用，**不能**引用
  外层的其它 step（内层没有"上游 step 结果"的概念）；确实需要外层上下文，
  写进外层 `resolved_prompt` 之外的固定字段（如 `foreach_step.params`）。
- `foreach_max_concurrency` 默认 `1`（串行），只有明确要提速、且各元素
  之间没有隐式副作用冲突时才调大；这是独立于 `allow_parallel` 的并发维度，
  不要混淆。
- 需要"某个元素失败就整体停下"时设 `foreach_stop_on_error: true`（配合
  `retry_on_error` 使用）；否则保持默认 `false`，让失败元素单独记录在聚合
  结果里、不拖累其它元素。

### `wait`：可中断的等待

```yaml
- id: rate_limit_pause
  type: wait
  wait_seconds: 30
```

只用于限速节流、等一段固定时间；`wait_seconds` 必须是正数。**不要**为了
"等一下"而在 `python_step` 脚本里写 `time.sleep`——那会跟子进程超时/
watchdog 硬超时的语义打架，且不能被 `pause_workflow_run`/
`cancel_workflow_run` 提前打断。

### `merge`：把多分支/多并发结果汇聚成一等公民 step

```yaml
- id: final_report
  type: merge
  depends_on: [summary_a, summary_b, enrich_each_question]
  merge_sources: [summary_a, summary_b, enrich_each_question]  # 顺序即聚合顺序
  merge_strategy: concat_text     # concat_text（默认）/ json_array / json_merge
  merge_separator: "\n\n---\n\n"
  merge_use_result_file: false
```

生成规则：
- `merge_sources` 里的每个 id 必须同时出现在该 step 的 `depends_on` 里
  （跟 prompt 占位符同一套校验，写漏会在 `save_workflow` 阶段报 error）。
- 单纯拼接文本（原来靠 prompt 手写 `{a.output}{b.output}` 的场景）用默认
  `concat_text`；需要下游拿到结构化数组用 `json_array`；需要把多个 JSON
  object 合并成一个用 `json_merge`（注意后面的来源会覆盖前面同名 key，
  设计合并顺序时留意这一点）。
- `foreach` 产出一份 JSON 数组后，常见的下一步就是用 `merge`
  （`json_array`/`json_merge`）跟另一个 step 的结果合并成最终输出——这是
  两者最自然的组合方式，生成"批处理 + 汇总"类流水线时优先考虑这个组合，
  而不是让某个 `agent`/`python_step` 手写拼接逻辑。

## 善用 `defaults`（多个 step 共享同一非默认配置时）

如果整个流水线的多个 step 都要用同一个非默认的 `model`/`max_turns`/
`timeout`/`retry_on_error`/`allow_parallel`，写进顶层 `defaults`，不要
在每个 step 上重复：

```yaml
name: data_pipeline
defaults:
  model: gpt-4.1-mini
  max_turns: 6

steps:
  - id: fetch
    type: agent
    prompt: "..."
    # 不写 model/max_turns → 继承 defaults 里的值

  - id: analyze
    type: agent
    prompt: "..."
    max_turns: 20   # 这一步比较复杂，单独覆盖成 20，其它仍继承 defaults.model
```

只有单个 step 需要偏离多数 step 的配置时，才在该 step 上单独写这几个
字段——**不要把每个 step 都显式写一遍相同的值**，那样起不到"改一处生效
全局"的作用，也让 YAML 更啰嗦。

## 全自动执行模式（`mode: autonomous`）

用户如果表达了"这个 workflow 要全程自动跑完，中途不能停下来等人""所有
参数在一开始就给全，别问我""要挂后台跑，没人盯着"这类诉求，生成时要做
两件事，缺一不可：

1. **顶层写 `mode: autonomous`**：这不是可选的装饰，而是一道保存期的
   保险——设了之后，如果 `steps` 里混入了会真正阻塞等待人工的写法
   （没有 `input_key` 的 `human_input`、或 `require_approval: true`），
   `save_workflow`/`patch_workflow_step` 会直接校验失败并报出具体是
   哪个 step 的问题，而不是等运行到后台才因为没人应答而卡到超时。
2. **每个 `human_input` step 必须配 `input_key`**：这个 key 对应运行时
   `run_workflow(inputs={...})` 里的某个字段名，命中就直接取值使用，不
   进入阻塞等待。也就是说，`autonomous` 模式下 `human_input` 的语义从
   "临场问人"变成了"从启动参数里取一个命名字段"——写 `prompt`/
   `input_prompt` 时仍然要写清楚这个字段该填什么，因为它同时也是运行时
   报错提示（`require_all_inputs_upfront=true` 时缺参数会报出这个提示）
   和给人类阅读者的说明。

```yaml
name: nightly_release_check
mode: autonomous
steps:
  - id: intake
    type: human_input
    input_key: release_tag       # 必须写，否则保存时报错
    input_prompt: "本次要检查的 release tag，如 v1.4.0"
    prompt: "release_tag"        # 同样会展示给人看，写法不强制但建议和 input_prompt 呼应

  - id: run_checks
    type: agent
    depends_on: [intake]
    prompt: |
      对 release {intake.output} 跑一遍发布前检查清单，输出结果。
```

**不要**在 `mode: autonomous` 的 workflow 里写 `require_approval: true`
——这个字段没有类似 `input_key` 的"预置值"逃生舱，只要开着就一定会阻塞
等待人工审批，跟"全自动"的诉求直接矛盾，保存时会被拒绝。如果流程里确实
需要一个人工把关的节点，说明用户描述的其实不是"全自动"场景，应该回去
跟用户确认清楚，而不是硬塞一个 `autonomous` 顶层字段掩盖矛盾。

如果用户没有明确表达"全自动/挂后台/别问我"这类诉求，**不要**主动加
`mode: autonomous`——默认的 `interactive` 已经能覆盖"有 human_input/
审批门、但允许运行时临场交互"的场景，强行改成 `autonomous` 反而会让
原本合理的 `human_input`/`require_approval` 写法在保存时报错。

## 善用可复用 step 片段（`include`）

如果这次要生成的 workflow 里有一段 step 组合（比如"打分 → 生成报告"这类
质检套路）明显可能被其它 workflow 复用，或者用户之前已经在
`.agent/workflow_snippets/` 下存过同类片段，优先复用而不是重新写一遍：

1. 先检查有没有现成片段可用：
   ```python
   from pathlib import Path
   from mini_agent.workflow.store import WorkflowStore
   store = WorkflowStore(project_root=Path("."))
   print(store.list_snippets())   # [{'name': ..., 'step_count': ..., 'steps': [...]}]
   ```
2. 有现成的且语义匹配，直接在 `steps` 里用 `include` 引用：
   ```yaml
   - id: qc                # 这个 id 是片段展开后所有 step 的命名空间前缀
     include: quality_check  # 片段名，不带 .yaml 后缀
     depends_on: [analyze]   # 挂到片段"入口" step 上
   ```
   工作流里其它 step 想引用这段片段的"整体输出"，直接写
   `depends_on: [qc]` / `{qc.output}` 即可（会自动指向片段展开后的
   最后一个 step），不需要知道片段内部具体的 step id。
3. 没有现成片段、但这段 step 组合确实通用（用户明确表示"以后还要用"），
   可以先用 `store.save_snippet(name, steps)` 存一份，再在 workflow 里
   `include` 引用；如果只是这一个 workflow 专用，不要为了"显得工程化"
   强行拆成片段，直接内嵌在 `steps` 里更直观。
4. **不要**把 `include` 和该条目自己的 `prompt`/`type` 等字段混着填，
   `include` 存在时其它字段（除了 `id`/`depends_on`）会被忽略；也不要
   凭空引用一个不存在的片段名，`store.load_snippet()` 找不到文件时会
   直接抛错。

## Prompt 占位符

- `{参数名}`：运行时由调用方通过 `run_workflow(inputs={...})` 传入（如 `{old_path}`），无法静态校验，运行到该 step 且未提供对应 input 时会替换为空字符串。
- `{step_id.output}`：引用某个前序 step 的输出文本。
- `{step_id.score}`：引用某个前序 step（通常是 `role_type: evaluator` 的评估 step）产出的分数。
- `{step_id.output_file}`：引用某个前序 step `output_file` 落盘文件的**绝对路径字符串**（不是内容），适合上游产出较大 JSON、下游只需要提示 Agent 去读文件的场景。
- `validate()` 会静态扫描这三类占位符，`{step_id.xxx}` 引用了不存在的 step id、或 `.xxx` 不是 `output`/`score`/`output_file` 时会报错——**写完 prompt 后要检查占位符拼写**。
- **引用某个 step 的输出/分数/落盘文件必须同时在 `depends_on` 里声明该 step**——这一点现在是强制校验（不再是弱一致性检查）：`validate()` 会检查占位符引用的 step_id 是否在当前 step 的 `depends_on`（直接或传递）范围内，写漏会直接报 error，而不是等到运行期才因为该 step 还没跑完而报错。生成 workflow 后务必用 `wf.validate()` 确认没有这类报错。
- `condition` 表达式里除了 `{step_id 对应的裸名}.output`/`.score`/`.passed` 这类属性访问外，还有一个始终可见、不受 `depends_on` 约束的命名空间 `inputs.xxx`，指向 `run_workflow(inputs={...})` 传入的外部参数，如 `"inputs.env == 'prod'"`；`validate()` 的静态一致性检查会跳过 `inputs.*` 引用，只对引用了其它 step 却没声明 `depends_on`（直接或传递）的情况报错，生成含 `condition` 的 step 时留意这一区别。

## 创建流程（生成 workflow 时遵循）

1. **确认需求**，向用户澄清（不确定就问，不要瞎猜）：
   - 这个流水线要完成什么目标，分成几个逻辑阶段
   - 每个阶段该由谁执行：按"选型强制规则"（见上文）先判断能不能用
     `python_step`（配合 `ctx.llm`/`ctx.llm.ask_json`）解决；不能的话再看
     能不能约束成 `skill_agent`（固定挂载某个技能，是否要用工作流私有
     skill）；纯确定性操作用 `tool_call`；只有确实需要临场应变（工具选择/
     页面结构等运行时才能确定）才落到 `agent`（是否要用工作流私有 agent，
     即 `role_agent`）——`agent`/`role_agent` 是兜底选项，不是默认答案；
     还可能是某个插件注册的自定义类型
   - 阶段之间的依赖关系（`depends_on`）、是否有需要人工确认的关卡
     （`human_input`/`require_approval`）、是否有质检门（`role_type: evaluator`
     + `condition: "xxx.score >= N"`）
   - 运行时需要哪些外部输入参数（如文件路径），会被哪些 step 的 prompt 引用
   - **这个流程是否要全自动跑完、中途不能停下来等人**（比如挂后台批处理、
     定时任务），是的话顶层要写 `mode: autonomous`，且所有 `human_input`
     step 都要配 `input_key`（对应到"运行时需要哪些外部输入参数"里问到的
     那些参数名），不能再用 `require_approval: true`，见下方"全自动执行
     模式"一节
   - 是否有明显重复、可能被其它 workflow 复用的 step 组合（考虑
     `include`）；多个 step 是否会共用同一个非默认的
     `model`/`max_turns`/`timeout`/`retry_on_error`/`allow_parallel`
     （考虑顶层 `defaults`）；是否需要限制单次执行的总 token 用量
     （`max_total_tokens`，通常只在用户明确担心成本失控时才主动问）
2. **选择模式**：默认文件夹模式（见上方"如何选择"），按用户要求调整。
3. **检查可复用片段**：`WorkflowStore.list_snippets()` 看有没有现成的
   `include` 片段可以直接用，避免重复造轮子（见上方"善用可复用 step
   片段"）。
4. **创建目录结构**：`.agent/workflows/<name>/`，按需创建 `agents/`、
   `skills/`、`prompts/`。
5. **写 `prompts/*.md`**（如果用文件夹模式且 prompt 较长）：每个文件只放
   一个 step 的 prompt 正文，占位符规则同上。简短的 prompt（一两句话）
   可以直接内嵌 `prompt` 字段，不强制拆文件。
6. **写 `agents/<role>.md`**（如果某个 step 需要专属角色）：格式参考
   `.claude/skills/agent-generator/SKILL.md`（frontmatter: name/description/
   tools/inputs，正文用 `{参数名}`/`{context}` 占位符）。这一份只服务于当前
   workflow，description 里可以写清楚"这是 xxx workflow 私有的 agent"。
7. **写 `skills/<skill-name>/SKILL.md`**（如果某个 step 需要强制挂载某项
   能力）：格式参考 `.claude/skills/reminder-generator/SKILL.md` 等现有
   skill（frontmatter: name/description/triggers，正文是技能使用说明）。
   配合 `type: skill_agent` + 对应 `skill_name` 使用。
8. **写 `workflow.yaml`**：按上方字段表填写，`steps` 里体现第 1 步确认的
   拓扑结构；多个 step 共享的非默认配置提到顶层 `defaults`，明确要复用的
   step 组合用 `include` 引用。
9. **自检**（写完后必须做，不要跳过）：
   - **逐个检查 `agent`/`role_agent` 类型的 step**：能不能改写成
     `python_step`（配合 `ctx.llm.ask_json`）或收紧成 `skill_agent`？改不了
     的话明确这是"需要临场应变"（见"选型强制规则"），不是图省事的默认写法
   - 每个 `id` 唯一、`depends_on` 引用的 id 都存在（`include` 条目的 `id`
     算作其它 step 引用它的入口 id，不需要等于片段内部任何一个 id）
   - `prompt`/`prompt_file`/`include` 三选一至少有一个非空（`human_input`
     例外）
   - `prompt_file` 指向的文件路径确实存在（相对 workflow 目录）
   - `include` 引用的片段名在 `.agent/workflow_snippets/` 下确实存在
   - `type` 专属字段都填了（`sub_workflow`→`workflow_name`、`tool_call`→
     `tool_name`、`script`→`script`、`skill_agent`→`skill_name`、
     `python_step`→`script_path`、`foreach`→`items`+`foreach_step`
     （含 `type`）、`wait`→`wait_seconds`（正数）、`merge`→
     `merge_sources`（非空无重复））；用了插件自定义类型的话，该类型专属的
     必填字段（看插件的 `validate_step()`）也要填
   - `foreach_step.type` 不是 `foreach`（禁止嵌套）；`foreach_max_concurrency`
     若填写须 `>= 1`
   - `merge_sources`/`tool_args` 里引用的 step id（含占位符 `{step_id.xxx}`
     形式）都出现在该 step 的 `depends_on`（直接或传递）范围内
   - `python_step` 的 `script_path` 指向的文件确实存在（相对 workflow
     目录），且脚本内暴露了 `def run(ctx) -> str|dict` 入口函数；用了
     `python_step` 的话记得提示用户显式开启 `python_step_enabled`
   - `sub_workflow` 没有引用自身
   - `mode: autonomous` 的 workflow：每个 `human_input` step 都配了
     `input_key`，且没有任何 step 写 `require_approval: true`（这两点
     `validate()` 会校验，但生成时自己先过一遍能提前发现问题）
   - prompt 里的 `{step_id.output}`/`{step_id.score}` 占位符拼写正确、
     引用的 step 确实存在（`include` 片段展开后新增的 `前缀__原id`
     形式的 id 也要留意，不要在片段外直接用未加前缀的原始 id 引用）
   - 尽量用代码验证而不是纯人工检查：
     ```python
     from pathlib import Path
     from mini_agent.workflow.store import WorkflowStore
     wf = WorkflowStore(project_root=Path(".")).load("<name>")
     errors = wf.validate()
     print(errors)   # 空列表才算过关；有 include 的话 load() 已经完成展开，
                      # 这里看到的是展开后的真实校验结果
     ```
10. **提示用户如何验证/运行**：
    - `/workflow show <name>` 查看解析结果
    - 正式运行前建议先 `preview_workflow(name, inputs)` 做一次 dry-run：
      不会真的执行，但会给出并发分批结果、prompt 占位符替换预览、
      `condition` 静态求值情况和 `unresolved_placeholders` 清单——比直接跑
      一遍正式执行更省成本，尤其适合刚写完、还不确定 `inputs` 是否传全时
    - `/workflow run <name> '{"参数名": "值"}'` 运行（有需要外部输入的话）
    - 用户要求"跑完的产出文件放到指定目录"时，用
      `run_workflow(name, inputs, output_export_dir="<绝对路径>")`：跑到
      终态后会自动把本次 `output/` 目录下的文件复制过去，不用手动 cp
      （复制失败不影响 workflow 本身的执行结果，只在返回信息里提示）
    - 不想进交互 REPL、只想在 shell/cron/systemd 里直接跑，把前缀换成
      `mini-agent workflow ...` 即可（子命令名和参数一致），如
      `mini-agent workflow run <name> '{"参数名":"值"}' --background
      --project <path>`；`--background` 在这条路径下会 spawn 一个独立 OS
      子进程，父进程立刻返回、子进程即使触发它的 shell 已退出也会跑完——
      这条路径本身的排查/调试属于 workflow-debugger skill，这里只需要知道
      它存在、什么时候该推荐给用户（用户说"写个脚本/加个定时任务跑这个
      workflow"时）
    - `mode: autonomous` 的 workflow，建议提示用户可以用
      `run_workflow(name, inputs='{...}', require_all_inputs_upfront=true)`
      启动——所有 `input_key` 对应的值都要在 `inputs` 里给全，缺了会在
      启动前直接报错列出缺哪些字段，而不是跑到一半才卡住；需要这次运行
      强制不并发时可以加 `force_serial=true`
    - 已有单文件工作流想升级为文件夹模式，用 `/workflow to-dir <name>`
      （自动建 `agents/`/`skills`/`prompts` 空目录，原 YAML 移入
      `workflow.yaml`）
    - 生成/保存完之后，如果用户想先验证某个 step 的 prompt 措辞是否合适、
      或者运行后某个 step 失败（状态 `failed`/`needs_fix`）需要排查、
      修改后要不要重跑等——不属于本 skill 覆盖的范围，也不需要重新走一遍
      本 skill 的生成流程，改用 **workflow-debugger** skill
      （`test_workflow_step` 沙箱测试单步、`get_workflow_run_status`/
      `get_workflow_stats` 排查、`patch_workflow_step` 改定义、
      `resume_workflow_run` 续跑）

## 常见坑

- **默认写 `type: agent` 是最常见的错误取舍**：只要一个阶段的描述看起来是
  "解析/过滤/打分/重排结构化数据"，先假设它应该是 `python_step`，而不是
  因为不确定就顺手落到 `agent`；生成完之后回头检查一遍 `steps` 里的
  `agent`/`role_agent`，每一个都应该能说清楚"为什么这一步不能用
  `python_step`/`skill_agent`"（见上文"选型强制规则"），说不清楚就是生成
  错了，应该改写。
- **`prompt_file` 路径是相对 workflow 目录的相对路径**（如 `prompts/analyze.md`），
  不是相对项目根目录，也不要写成绝对路径。
- **`prompt` 和 `prompt_file` 都填时 `prompt_file` 优先**——文件夹模式下如果
  两个都写了，`prompt` 字段的内容会被完全忽略，不会合并。
- **`allow_parallel`/`max_turns`/`model`/`timeout`/`retry_on_error` 不写
  ≠ 一定是旧的硬编码默认值**：现在是"本字段 → 顶层 `defaults` → 硬编码
  兜底"三层继承，如果这个 workflow 写了 `defaults`，某个 step 不写这几个
  字段时实际生效的是 `defaults` 里的值，不是表格里写的硬编码兜底值——生成
  时如果想让某个 step **明确不跟随** `defaults`、强制用某个值，必须在该
  step 上显式写出来，不能靠"不写"。
- **`include` 展开是加载期行为，保存时看到的还是未展开的 `include:` 声明**：
  `save_workflow`/`WorkflowStore.save()` 落盘的 YAML 里仍然是
  `include: <片段名>`，只有 `load()`（或 `/workflow show`/`run_workflow`）
  才会展开成真实 step；直接读磁盘上的 YAML 文件人工检查时，不要误以为
  片段没生效。
- **子工作流不继承父工作流的本地资源包**：`type: sub_workflow` 引用的另一个
  workflow，如果需要用到私有 agent/skill，必须在它自己的目录下也放一份，
  不能指望"父工作流有就行"。
- **`role`/`skill_name` 的本地优先级**：本工作流 `agents/`/`skills/` 目录下
  同名资源优先于全局/项目级同名资源，这是"覆盖"而不是"合并成一个"——如果
  本地和全局都有 `reviewer.md`，实际生效的是本地这一份。
- **`skill_agent` 和"关键词触发"不是一回事**：`type: skill_agent` 是强制
  挂载，不管 prompt 里有没有命中该 skill 的 `triggers` 关键词都会生效；如果
  只是希望"主 Agent 在需要时自动触发某个 skill"，用普通 `type: agent` step
  就够了，不需要 `skill_agent`。
- **`mode: autonomous` 不是"随手加的保险栓"**：只有用户明确表达了全自动/
  挂后台诉求时才加；加了之后忘记给 `human_input` 配 `input_key`，或者
  留了个 `require_approval: true`，`save_workflow` 会直接拒绝保存——这是
  故意设计成"保存期报错"而不是"运行期才发现"，生成时按上面的自检清单过
  一遍就能避免。反过来，不需要全自动时也不要为了"看起来更完善"顺手加
  `mode: autonomous`，那样会让原本合理的 `human_input`/`require_approval`
  写法保存不了。
- **`skill_agent` 的产出要喂给下游 `python_step` 时忘了配 `result_file`**：
  没有 `result_file` 时下游只能拿到对话原文，靠正则/脆弱解析拿 JSON 迟早
  会炸；只要下游需要结构化数据，就应该配 `result_file` +
  `result_file_required_keys`，见上面"`skill_agent`：务必配 `result_file`"
  一节。
- **把批处理逻辑写进 `python_step` 手写循环，而不是用 `foreach`**：只要发现
  一段脚本里在 `for` 循环调用 `ctx.run_agent_turn()`/重复调 LLM 处理列表里
  每个元素，就应该改成 `type: foreach` + `foreach_step`，让 YAML 能直接
  看出这里是批处理，也方便用 `test_workflow_step` 单独验证一个元素的处理
  逻辑。
- **`foreach_step` 写成嵌套 `foreach`**：`validate()` 会直接拒绝，遇到
  "批处理里还要批处理"的需求应该重新设计粒度，不要硬套。
- **`merge_sources` 忘了同步写进 `depends_on`**：跟 prompt/`tool_args`
  占位符一样，这是保存期强制校验，写漏会直接报 error。
- **多分支结果汇总还在 prompt 里手写 `{a.output}{b.output}` 拼接**：这类
  需求现在应该用 `type: merge`，让"这里是汇聚节点"在 YAML 里显式可见，
  调试时也能单独测这一步的汇聚逻辑。
- **`condition` 引用了还没跑完/类型不对的字段**：现在求值异常会被判定为
  `NEEDS_FIX` 而不是 `SKIPPED`（P12），生成时不确定某个 `.output`/`.score`
  字段此刻是否已就绪，先用 `preview_workflow` 验证，不要凭感觉写。
- **`tool_call` 的 `tool_args` 想传上游结构化字段却整段转成文本塞给
  prompt**：`tool_args` 的字符串值（含嵌套 dict/list）现在支持占位符
  （P12），应该直接写 `{step_id.output}`/`{step_id.result_file:a.b.c}`，
  不需要退化成"把整个 prompt 当第一个参数"的旧写法。
- 不确定字段语义时，直接看 `.agent/workflows/doc_change_review/` 这个完整
  可跑的例子，或者读 `src/mini_agent/workflow/schema.py` 顶部的 docstring
  和 `docs/workflow-guide.md`"文件夹模式 Workflow"一节。

## 示例：文档变更审查工作流（参考本仓库已有实例）

`.agent/workflows/doc_change_review/` 是一个可以直接运行的完整例子，
四个 step 分别覆盖了 `agent`（主 Agent + `prompt_file`）、`skill_agent`
（调用本地 `changelog-diff` skill）、`role_agent`（调用本地 `reviewer`
agent）、以及最后再用 `agent` 汇总生成报告并带 `condition`。生成新
workflow 时，结构上可以直接照这个例子的骨架改。

## 示例：全自动 workflow（`mode: autonomous`，供对比）

如果用户明确要求"要挂后台跑、中途别停下来问我"：

```yaml
# .agent/workflows/nightly_release_check/workflow.yaml
name: nightly_release_check
description: 全自动的发布前检查：给定 release tag，跑完检查清单并生成报告
version: "1.0"
mode: autonomous
steps:
  - id: intake
    name: 接收参数
    type: human_input
    input_key: release_tag
    input_prompt: "本次要检查的 release tag，如 v1.4.0"
    prompt: "release_tag"

  - id: run_checks
    name: 执行检查清单
    type: agent
    depends_on: [intake]
    prompt: |
      对 release {intake.output} 跑一遍发布前检查清单（依赖版本、
      变更日志、回归测试结果），逐项给出通过/不通过的结论。

  - id: report
    name: 生成报告
    type: agent
    depends_on: [run_checks]
    prompt: |
      基于以下检查结果，生成一份简明的发布检查报告：
      {run_checks.output}
```

启动方式：`run_workflow("nightly_release_check", '{"release_tag": "v1.4.0"}',
background=true, require_all_inputs_upfront=true)`——`intake` 这个
`human_input` step 会直接从 `inputs.release_tag` 取值，不会阻塞等待。

## 示例：最小两步骤 workflow（单文件模式，供对比）

如果用户明确要求"简单点，就一两步"，可以退回单文件模式：

```yaml
# .agent/workflows/quick_summary.yaml
name: quick_summary
description: 对一段文本做摘要 + 一句话点评
version: "1.0"
steps:
  - id: summarize
    name: 摘要
    type: agent
    prompt: |
      请用 3 句话概括以下内容：
      {text}

  - id: comment
    name: 点评
    type: agent
    prompt: |
      基于以下摘要，给一句简短的点评（褒贬皆可，说明理由）：
      {summarize.output}
    depends_on:
      - summarize
```
