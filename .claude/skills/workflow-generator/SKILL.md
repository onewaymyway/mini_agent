---
name: workflow-generator
description: 帮助用户创建符合 mini_agent 最新版规范的 workflow（.agent/workflows/<name>/workflow.yaml，可携带私有 agents/skills/prompts；支持顶层 defaults 默认配置继承、可复用 step 片段 include、max_total_tokens 用量护栏、插件自定义 step 类型）。当用户说"帮我创建一个workflow"、"做一个工作流"、"写一个xxx流水线"、"把这个workflow转成文件夹模式"时使用。
triggers: workflow, 工作流, 流水线, pipeline, 创建workflow, workflow.yaml, 文件夹模式workflow
---

# Workflow Generator（文件夹模式）

用于创建符合本项目 `WorkflowStore`/`WorkflowDef`/`WorkflowStep`
（`src/mini_agent/workflow/schema.py`、`src/mini_agent/workflow/store.py`）
解析规范的 workflow。优先按"文件夹模式"创建——除非用户明确要求单文件、
或工作流足够简单（≤2 步、不需要私有 agent/skill/prompt 文件）。

完整设计背景见 `next_doc/workflow_directory_mode_design.md`，可运行的完整
示例见 `.agent/workflows/doc_change_review/`（本 skill 生成的内容应与它
风格一致）。

## 两种模式，如何选择

| | 单文件模式 | 文件夹模式 |
|---|---|---|
| 路径 | `.agent/workflows/<name>.yaml` | `.agent/workflows/<name>/workflow.yaml` |
| 适用场景 | 简单、prompt 内嵌几行文本就够、不需要专属 agent/skill | 复杂流水线、prompt 较长、需要工作流私有的角色 agent 或 skill |
| 私有资源 | 不支持 | 支持 `agents/`、`skills/`、`prompts/` |

`WorkflowStore.load(name)` 优先找文件夹模式，找不到再退回单文件模式，
两者可以在 `.agent/workflows/` 下共存，不冲突。

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

单个 step 的字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | 步骤唯一标识，其他 step 通过 `{id.output}` / `{id.score}` 引用它的结果 |
| `name` | 建议 | 可读名称 |
| `type` | 建议显式写 | 见下方"Step 类型"表；不写时按 `role` 是否为空自动推断为 `agent`/`role_agent`（向后兼容旧写法，新建议一律显式写 `type`）。也可以是插件通过 `register_step_executor()` 注册的自定义类型（不在下表里，见"自定义/插件 Step 类型"一节） |
| `include` | 与 `prompt` 二选一 | 引用 `.agent/workflow_snippets/<n>.yaml` 里的一段可复用 step，见下方"善用可复用 step 片段（`include`）"。填了这个之后该 step 条目本身只需要 `id`（作为命名空间前缀）+ `depends_on`（挂接点），`prompt`/`type` 等字段不需要填、填了也会被忽略 |
| `prompt` | 二选一 | 内嵌 prompt 文本，支持 `{参数名}`、`{其他step_id.output}`、`{其他step_id.score}` 占位符 |
| `prompt_file` | 二选一 | 相对 workflow 所在目录的相对路径，如 `prompts/analyze.md`；与 `prompt` 都填时 `prompt_file` 优先；**文件夹模式下优先用这个而不是内嵌 `prompt`**，尤其是 prompt 超过几行的时候 |
| `role` | `role_agent` 专用 | 角色 agent 名，优先匹配本工作流 `agents/<role>.md`，没有则退回全局 `.agent/agents/` |
| `skill_name` | `skill_agent` 专用 | skill 名，优先匹配本工作流 `skills/<skill_name>/SKILL.md`，没有则退回全局 skills 目录 |
| `depends_on` | 否 | 依赖的 step id 列表，决定拓扑执行顺序；同一"层"（无相互依赖）的 step 默认并发执行 |
| `condition` | 否 | 形如 `"evaluate.score >= 6"` / `"review.output != ''"` 的表达式，不满足则跳过该 step |
| `allow_parallel` | 否 | 不写（`null`）时按"本字段 → 顶层 `defaults.allow_parallel` → 硬编码兜底 `true`"三层继承；step 有隐式副作用（读写同一外部状态）时显式设为 `false` 强制串行 |
| `max_turns` | 否 | 该 step 允许的最大 LLM 轮数；不写时同样三层继承，硬编码兜底 `10` |
| `model` | 否 | 覆盖该 step 用的模型；不写时三层继承（`defaults.model` → 全局配置） |
| `timeout` | 否 | 该 step 超时（秒）；不写时三层继承，硬编码兜底为不限制 |
| `retry_on_gate_fail` | 否 | evaluator 质检门不达标时重跑前序步骤的最大次数，仅对 `role_type: evaluator` 的角色有意义。**不参与 `defaults` 继承**，缺省就是 `0`，与其它几个"三层继承"字段是两套独立机制 |
| `retry_on_error` | 否 | 普通异常（超时/工具报错）重试次数；不写时三层继承，硬编码兜底 `0` |
| `require_approval` | 否 | 是否需要人工审批门放行，需配合 `run_workflow(background=True)` |
| `workflow_name` | `sub_workflow` 专用 | 引用的另一个已保存工作流名称，不能引用自身 |
| `tool_name` / `tool_args` | `tool_call` 专用 | 直接调用某个已注册工具，不启动整个 Agent |
| `input_prompt` | `human_input` 专用 | 展示给人类的提示语，缺省用 `prompt` 本身 |
| `script` | `script` 专用 | 要执行的 shell 命令，受 `cfg.workflow.script_step_enabled` 开关保护，默认关闭 |

> **写法提示**：`max_turns`/`model`/`timeout`/`retry_on_error`/`allow_parallel`
> 这 5 个字段现在语义是"不写=继承"而不是"不写=固定默认值"——生成 workflow
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

### 自定义/插件 Step 类型

以上是内置的 7 种类型。项目还可能通过 `myplugins/*.py` 里的
`register_step_executor()` 注册了自定义类型（比如示例插件
`myplugins/example_http_step.py` 注册的 `type: http`）。生成 workflow
前，如果不确定项目里有没有这类插件、支持哪些自定义类型，可以查一下：

```python
from mini_agent.workflow.executors import get_registered_types
print(get_registered_types())   # 内置 7 种 + 插件注册的自定义类型
```

用户明确要求某个自定义类型（如"用 http 类型调一下这个接口"）时才使用；
不确定该类型需要哪些专属字段时，去看对应插件源码里的
`StepExecutor.validate_step()` 实现，或直接问用户。不要凭空编造一个不存在
的 `type`——`WorkflowDef.validate()` 会在保存时因"type 非法"直接报错。

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
- `validate()` 会静态扫描这两类占位符，`{step_id.xxx}` 引用了不存在的 step id、或 `.xxx` 不是 `output`/`score` 时会报错——**写完 prompt 后要检查占位符拼写**。
- 引用某个 step 的输出/分数，建议同时在 `depends_on` 里声明该 step（不是强制，但能让拓扑顺序、并发分层符合预期；`validate()` 不会校验"引用了但没声明依赖"这种弱一致性问题，运行时靠 `depends_on` 保证该 step 已经跑完）。

## 创建流程（生成 workflow 时遵循）

1. **确认需求**，向用户澄清（不确定就问，不要瞎猜）：
   - 这个流水线要完成什么目标，分成几个逻辑阶段
   - 每个阶段该由谁执行：主 Agent（`agent`）？固定角色（`role_agent`，是否要
     用工作流私有 agent）？某个特定技能（`skill_agent`，是否要用工作流私有
     skill）？纯工具调用（`tool_call`）？还是某个插件注册的自定义类型？
   - 阶段之间的依赖关系（`depends_on`）、是否有需要人工确认的关卡
     （`human_input`/`require_approval`）、是否有质检门（`role_type: evaluator`
     + `condition: "xxx.score >= N"`）
   - 运行时需要哪些外部输入参数（如文件路径），会被哪些 step 的 prompt 引用
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
   - 每个 `id` 唯一、`depends_on` 引用的 id 都存在（`include` 条目的 `id`
     算作其它 step 引用它的入口 id，不需要等于片段内部任何一个 id）
   - `prompt`/`prompt_file`/`include` 三选一至少有一个非空（`human_input`
     例外）
   - `prompt_file` 指向的文件路径确实存在（相对 workflow 目录）
   - `include` 引用的片段名在 `.agent/workflow_snippets/` 下确实存在
   - `type` 专属字段都填了（`sub_workflow`→`workflow_name`、`tool_call`→
     `tool_name`、`script`→`script`、`skill_agent`→`skill_name`）；用了
     插件自定义类型的话，该类型专属的必填字段（看插件的
     `validate_step()`）也要填
   - `sub_workflow` 没有引用自身
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
    - `/workflow run <name> '{"参数名": "值"}'` 运行（有需要外部输入的话）
    - 已有单文件工作流想升级为文件夹模式，用 `/workflow to-dir <name>`
      （自动建 `agents/`/`skills`/`prompts` 空目录，原 YAML 移入
      `workflow.yaml`）

## 常见坑

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
- 不确定字段语义时，直接看 `.agent/workflows/doc_change_review/` 这个完整
  可跑的例子，或者读 `src/mini_agent/workflow/schema.py` 顶部的 docstring
  和 `docs/workflow-guide.md`"文件夹模式 Workflow"一节。

## 示例：文档变更审查工作流（参考本仓库已有实例）

`.agent/workflows/doc_change_review/` 是一个可以直接运行的完整例子，
四个 step 分别覆盖了 `agent`（主 Agent + `prompt_file`）、`skill_agent`
（调用本地 `changelog-diff` skill）、`role_agent`（调用本地 `reviewer`
agent）、以及最后再用 `agent` 汇总生成报告并带 `condition`。生成新
workflow 时，结构上可以直接照这个例子的骨架改。

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
