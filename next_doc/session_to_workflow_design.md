# Session → Workflow 转换机制设计（P8）

> 状态：**核心功能已实现**（总结阶段 + 构建阶段 + 三个 Agent 工具 + CLI +
> 单元测试 + 文档），实施记录见文末"实施检查清单"勾选情况与备注。
> 编号延续 `workflow_mechanism_improvement_plan.md`（P1-P7）之后，记为 **P8**。
> 目标：让用户可以把"之前某次 session 里实际做成的一件事"沉淀成一个可复用的
> `WorkflowDef`，而不是每次都要么重新一步步指挥 Agent，要么手写 YAML。
>
> **补充更新**：三个 Agent 工具 + CLI 的 `sessions`/`from-session` 子命令
> 已补充 `agent_config.json` → `workflow.session_to_workflow_enabled` 开关
> （默认开启），关闭后返回明确提示；详见
> `next_doc/p8_p9_config_toggle_and_cli_hint_record.md`。

---

## 0. 核心判断：两段式，"总结"先于"构建"

最初的思路是直接把 session 的 `history.json` 结构化解析（按用户轮次切段，
把每段的工具调用序列映射成 step），这个思路的问题是：**真实 session 里包含
大量失败重试**（读错文件、格式改错、测试没过再改一次），如果不做真正的语义
理解，机械分组分不清"这一串调用里哪几步是徒劳的，哪几步是真正促成最终结果
的"，生成出来的 workflow 会把重试过程也当成"标准流程"的一部分，越描述越乱。

所以设计成**两个独立阶段**，中间产物（`TaskSummary`）本身要展示给用户确认：

```
① 总结：session 原始素材（压缩后）→ LLM 一次调用 → TaskSummary（结构化，非自由文本）
         │
         ▼ 展示给用户，人工确认/纠正
② 构建：TaskSummary（不是原始 history）→ LLM 一次调用 → Draft WorkflowDef YAML
         │
         ▼ 复用现有 generate_workflow 的预览 + save_workflow 交互
```

**为什么不能跳过①、直接从原始 history 生成 workflow**：①负责把"发生了什么"
提炼成"哪些是达成目标的主线、哪些是可以丢弃的失败重试、哪些重试模式值得被
抽象成质检门"——这是需要语义判断的一步，跟②"把一个已经理清楚的任务流程
写成 YAML"（这一步跟现有 `generate_workflow` 做的事情本质相同）职责不同，
混在一次 LLM 调用里容易互相干扰（这一点也是 `reflection.py` 里
`_reflect_and_save_lessons` 和 `_reflect_timeline_summary` 特意分成两次独立
调用、不复用同一次调用的原因，见该文件对应注释）。

**为什么①的产物要先给用户看**：如果总结阶段理解错了任务（比如把一次探索性
的失败尝试当成了主线），后面生成的 YAML 必然也是错的；让用户在生成完整 YAML
之前，用一段更短的自然语言确认"我理解的是不是这么回事"，纠正成本远低于
"生成完 YAML 再发现整个理解错了"。

---

## 1. 入口设计：只做"跨 session"这一种，起干净的临时 Agent

> 本节按照已确认的方向定稿：**总结阶段总是起一个干净的临时 Agent**，数据源
> 换成"内存 history 还是磁盘 history.json"两种情况都走同一套代码；**要求
> 用户直接给出 session_id**，不做模糊语义定位。

### 1.1 为什么"当前 session 建自己的 workflow"也要起临时 Agent，而不是借用当前 Agent 续调用

看起来"用当前正在跑的这个 Agent 直接续一次总结调用"更省一次 LLM
初始化成本，但有两个问题：

1. **上下文污染**：当前对话到"用户要求总结成 workflow"这一刻为止，
   history 里除了"要总结的这件事"之外，可能还夹杂着别的话题、追问、
   跟这次任务无关的闲聊——借用当前 Agent 续调用，等于让总结这一步"看得到"
   这些无关内容，容易分散总结质量。而临时 Agent 只喂给它"从 session 数据里
   精确抽取出来的、经过压缩的素材"，输入是可控、干净的。
2. **两种场景（建自己的 workflow / 建别的 session 的 workflow）统一成同一套
   实现**：既然跨 session 场景必须起临时 Agent（当前 Agent 完全没有目标
   session 的上下文），当前 session 场景如果也统一走"读 history → 起临时
   Agent → 总结"，两种场景在"总结"这一步就是完全同一份代码，只有"素材从
   哪读"不同——降低实现和维护成本，也保证两种入口生成质量一致。

**结论**：不区分"当前 session 场景"和"跨 session 场景"两条实现路径，
统一成一个函数：

```python
def summarize_session_for_workflow(history_entries: list[dict], cfg: AppConfig) -> TaskSummary:
    """
    history_entries: 可以是当前 Agent 的 self._history（内存中的活对象），
                      也可以是从磁盘 history.json 读出来的列表——两种来源
                      结构完全一致（都是 history/entry.py 里 make_*() 系列
                      函数产出的类型化 dict），这个函数不关心来源。
    """
```

"当前 session"和"目标 session"两种入口，只是各自负责准备好
`history_entries` 之后调用同一个 `summarize_session_for_workflow()`，
不重复实现总结逻辑。

### 1.2 为什么"跨 session"要求用户直接给 session_id，不做模糊定位

用户可能记不住 session_id，"接一个语义匹配去猜用户说的是哪个 session"
看起来更友好，但：

- 猜错了代价不小——总结阶段是一次真金白银的 LLM 调用，猜错 session 意味着
  这次调用完全白费，还要让用户发现"这总结的不是我说的那次"才能纠正，
  比"一开始就让用户明确指定"绕的路更远。
- 已有 `list_workflow_runs` 之类的列举类工具建立的产品习惯就是"先列出来，
  用户自己挑"——跟这个习惯保持一致，不必为这一个功能单独造一套模糊匹配。
- 简单可靠优先：先做"要求精确 session_id + 提供一个列举/预览工具帮用户
  自己找到它"，模糊定位可以作为纯体验优化留到以后有需要再加，不阻塞这次
  功能落地。

**落地方式**：新增一个轻量列举工具/CLI 命令，只做"列出最近 N 个 session
的 id + 起止时间 + 首条用户输入前 N 字"这种摘要（不需要 LLM，纯读
`meta.json`/`history.json` 首尾），帮用户自己对上是哪次；总结阶段的工具
本身要求必填 `session_id` 参数，不接受模糊描述。

---

## 2. 数据准备：从 history 到"总结阶段能用的压缩素材"

不管 `history_entries` 来自内存还是磁盘，准备总结素材的逻辑一致，直接复用/
参考现有基础设施：

### 2.1 用户意图轮次文本

跟 `_reflect_and_save_lessons()` 一致：

```python
from mini_agent.history.entry import is_turn_boundary
user_turns = [m["content"] for m in history_entries
              if is_turn_boundary(m) and isinstance(m.get("content"), str)]
```

`is_turn_boundary()` 已经把 `user_input`/`user_correction` 同等对待，
不需要重新判断类型。**不做 `[-10:]` 截断**（lesson reflection 只看最后
10 轮是因为它是"会话尾声的轻量反思"，这里的目标是完整还原一次任务的
全过程，应该覆盖 session 的完整用户轮次；如果 session 特别长导致
token 超限，见 2.4"长 session 的截断策略"）。

### 2.2 按"意图批次"分组的执行时间线

每个 assistant_reply 条目的 `content` 是结构化 block 列表
（`{"type": "text", ...}` / `{"type": "tool_use", "id", "name", "input"}`，
见 `history_manager.py::append_assistant()`）。按 assistant_reply 出现顺序，
把其中的 `tool_use` block 转成轻量对象（只需要 `.name`/`.input` 两个属性，
用 `types.SimpleNamespace` 或一个一次性小 dataclass 即可，不需要引入真正的
`ToolCall` 类型），喂给已有的 `IntentActionMapper.group_calls()`：

```python
from mini_agent.perception.intent_action_mapper import IntentActionMapper

def _extract_tool_uses(history_entries: list[dict]) -> list[SimpleNamespace]:
    calls = []
    for m in history_entries:
        if m.get("_type") != "assistant_reply":
            continue
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                calls.append(SimpleNamespace(name=block.get("name", ""), input=block.get("input") or {}))
    return calls
```

**关键设计**：`IntentActionMapper.group_calls()` 目前只按"意图类别的连续
游程"分组，不知道每次调用是否成功（`result_strs` 是可选参数，成功/失败要
另外配）。需要同时从紧随其后的 `tool_result` 条目里判断成功/失败——
`tool_result` 的 `content` 是 `render_tool_results()` 渲染出来的文本，
按 `tool_call_id` 关联结果（`render_tool_results` 的具体格式需要在实现时
读一下 `mini_agent/llm/system_tool_call.py`，这里不假设细节）。判断单条结果
是否报错，复用现成的 `perception/lesson_rules.py::is_tool_error()`，不重新
写判断逻辑。

把 `(tool_use 序列, 对应 result_strs)` 一起传给 `group_calls()`，得到
`list[ActionEvent]`，每个 `ActionEvent.to_summary_text()` 已经是现成的
"代码编辑 ×3（read_file, patch_file），1 次出错"这种人类可读摘要——直接
拼接成时间线文本喂给总结 LLM，不需要再额外设计一套摘要格式。

### 2.3 assistant 的阶段性结论文字

同一批 `content` block 列表里的 `{"type": "text", "text": ...}` 部分，是
assistant 在做完一批工具调用后、或者下一批工具调用前说的话（比如"分析完成，
发现是空指针问题，开始修复"）。这些文本按顺序跟 2.2 的 ActionEvent 摘要
交替拼接，形成一条"时间线"：

```
[用户] 帮我修一下 xxx 报的这个 bug
[执行] 探索/检索代码 ×4（grep, read_file）
[assistant] 定位到问题在 foo.py 的空指针检查缺失
[执行] 代码编辑 ×1（patch_file），1 次出错
[执行] 代码编辑 ×1（patch_file）
[执行] 运行测试 ×1（bash），1 次出错
[assistant] 测试失败是因为漏了一个边界条件，补一下
[执行] 代码编辑 ×1（patch_file）
[执行] 运行测试 ×1（bash）
[assistant] 测试通过，修复完成
```

这条时间线本身已经把"失败重试在哪、最终怎么解决的"暴露出来了，但仍然是
"发生了什么"的忠实记录，**折叠判断留给总结阶段的 LLM 做**，不在这一步用
规则强行去掉重试记录——规则判断不出"这次重试是否值得成为质检门"，这是
需要语义理解的判断，交给 LLM。

### 2.4 长 session 的截断策略

如果拼出来的时间线过长（有 token 预算问题），按"ActionEvent 数量"从两头
向中间做保留优先级：**优先保留每个用户轮次开头/结尾的 ActionEvent 和所有
assistant 文本**，中间连续的同类型 ActionEvent（比如连续 5 次"代码编辑"）
可以合并成一条"代码编辑 ×N（合计）"而不是逐条罗列——这个截断只影响总结
阶段喂给 LLM 的素材密度，不影响最终 TaskSummary 的结构。具体阈值实现时
参考 `perception/token_counter.py::estimate_messages_tokens()` 现有的
token 估算方式，不重新造轮子。

---

## 3. 总结阶段：`TaskSummary` 结构与 Prompt 设计

### 3.1 输出 Schema

沿用仓库里"要求 LLM 只输出 JSON，用正则/json 解析"的既有模式
（`_parse_lesson_candidates`/`_parse_timeline_summary` 所在的
`agent/_helpers.py`，新增一个 `_parse_task_summary()` 放在同一处）：

```json
{
  "goal": "这次 session 要完成的总体目标，一句话",
  "final_outcome": "最终实际交付/达成的是什么",
  "stages": [
    {
      "id": "简短英文标识，供后续映射成 step id 参考，如 analyze/fix/verify",
      "purpose": "这个阶段要达成什么",
      "approach": "做法摘要，意图层面的描述，不是工具调用流水账",
      "depends_on_stage_ids": ["依赖的前置阶段 id"],
      "had_retries": false,
      "retry_note": "如果 had_retries=true，一句话说明失败原因和最终怎么解决的；不展开具体重试了几次",
      "gate_candidate": false
    }
  ],
  "candidate_parameters": [
    {"name": "建议的参数名", "example_value": "这次实际用的值", "source": "取值来源，如'首个用户输入'"}
  ],
  "repeated_pattern": null
}
```

- `gate_candidate: true` 标记"这个阶段的重试模式值得被抽象成质检门"
  （`role: evaluator` + `condition` + `retry_on_gate_fail`），而不是普通的
  `retry_on_error`；由 LLM 判断，规则层不介入。
- `repeated_pattern`：如果 LLM 发现某几个阶段的组合在这次 session 里出现
  了不止一次（比如对多个文件重复执行了同一套"打分→报告"），填一段描述，
  作为②阶段"要不要建议存成 `workflow_snippets` 片段"的信号；没有则为 `null`。
- `candidate_parameters`：直接对应 P7-③2 之前设计里提到的参数化思路——
  这次实际用的具体值，标成"以后换个输入应该变成什么"的候选，②阶段生成
  YAML 时用它们产出 `{param}` 占位符和 `example_input`。

### 3.2 Prompt 模板（新增两个文件，沿用 `pm.render()` 机制）

`src/mini_agent/prompts/system/session_to_workflow_summary.md`：

```
# prompts/system/session_to_workflow_summary.md
#
# 用于 session_to_workflow 机制第①阶段：把一次已完成 session 的执行过程
# 总结成结构化 TaskSummary，供第②阶段构建 workflow 使用。
# 对应 next_doc/session_to_workflow_design.md

You are analyzing a finished AI agent session to extract a reusable task
summary. The session log below is a compressed timeline: user requests,
grouped tool-call intent summaries (not raw tool logs), and the agent's
own stage-transition remarks.

Your job is NOT to narrate everything that happened. Distinguish the
**main line** of actions that actually led to the final outcome from
**exploratory or failed attempts** that were later abandoned or corrected.
Collapse retries into a one-line note about why it failed and how it was
eventually resolved — do not enumerate every retry attempt as a separate
stage. If a retry pattern looks like it should become a quality-gate
mechanism in a reusable workflow (score/evaluate → retry until it passes),
mark that stage as a gate candidate instead of treating each retry as its
own step.

Always respond with ONLY a JSON object (no markdown fences, no commentary)
matching this exact schema: {goal, final_outcome, stages[], candidate_parameters[], repeated_pattern}.
See the user message for the concrete field definitions.

Respond in the same language as the session content provided below.
```

`src/mini_agent/prompts/user/session_to_workflow_summary_request.md`：

```
# prompts/user/session_to_workflow_summary_request.md
#
# 变量：
#   {{timeline_text}} — 2.3 节格式的执行时间线（用户轮次 + ActionEvent 摘要 + assistant 阶段性文本交替）

Summarize this session into the following JSON structure:

{
  "goal": "...",
  "final_outcome": "...",
  "stages": [
    {"id": "...", "purpose": "...", "approach": "...",
     "depends_on_stage_ids": ["..."], "had_retries": false,
     "retry_note": "", "gate_candidate": false}
  ],
  "candidate_parameters": [
    {"name": "...", "example_value": "...", "source": "..."}
  ],
  "repeated_pattern": null
}

Session timeline:
{{timeline_text}}
```

### 3.3 承载函数

放在新模块 `src/mini_agent/workflow/session_summarizer.py`（不放进
`agent/reflection.py`——那个文件是"当前 Agent 自身会话结束时的反思"，
这里是"任意给定 session 的离线总结"，职责不同，复用的只是同样的
`pm.render()` + LLM 调用模式，不是同一个类）：

```python
def build_timeline_text(history_entries: list[dict]) -> str:
    """2.1-2.3 节逻辑：拼出喂给总结 LLM 的时间线文本。"""

def summarize_session_for_workflow(history_entries: list[dict], cfg: "AppConfig") -> "TaskSummary":
    """
    起一个干净的临时 Agent（复用 WorkflowGenerator.generate() 里
    "load_config(auto_approve=True) + 空 registry + system_extra 覆盖"
    的同一套搭建方式，max_turns=1，不给工具），跑一次总结调用，
    解析成 TaskSummary dataclass。解析失败/字段缺失时抛 ValueError，
    由调用方（工具层）转成用户可读的报错，不静默返回空结构。
    """
```

`TaskSummary` 定义成 `dataclass`（放 `schema.py` 同目录的
`session_summarizer.py` 里即可，不需要进 `workflow/schema.py`——它不是
`WorkflowDef` 体系的一部分，只是这个功能内部的中间数据结构），带
`to_markdown()` 方法用于阶段①的人工确认展示（3.4 节）。

### 3.4 用户确认展示格式

```
## 这次 session 做了什么（摘要）

**目标**：修复 xxx 报的空指针 bug
**最终结果**：修复完成，测试通过

### 主线阶段
1. **analyze** — 定位问题：在代码里检索并定位到 foo.py 的空指针检查缺失
2. **fix** — 修复代码（经历 1 次失败重试：漏了一个边界条件，补上后通过）
   ⚠️ 这个阶段的重试模式建议做成质检门（打分/验证 → 不通过就重跑）
3. **verify** — 运行测试确认修复生效

### 建议参数化的值
- `bug_description`（来源：首个用户输入）：xxx 报的空指针问题

以上理解正确吗？确认后我会据此生成 workflow YAML；如果哪里理解错了，
直接告诉我需要调整的地方。
```

---

## 4. 构建阶段：`TaskSummary` → Draft WorkflowDef YAML

用户确认①的总结之后，②阶段复用/扩展现有 `WorkflowGenerator`
（`workflow/generator.py`），而不是另起一套生成逻辑：

- 新增 `WorkflowGenerator.generate_from_summary(task_summary: TaskSummary, example_input: Optional[str] = None) -> str`，
  跟现有 `generate(description, example_input)` 平行存在，内部同样是
  "起临时 Agent + `system_extra` 覆盖 + 一次 `run_turn()`"，只是喂给 LLM
  的 prompt 从"自然语言 description"换成"结构化的 TaskSummary 序列化文本
  + 明确的字段映射指引"：

  ```
  请把以下已确认的任务总结，转换成一个 workflow YAML 定义：
  - stages[] 里每个阶段映射成一个 step，id 直接用 stage.id
  - depends_on 用 stage.depends_on_stage_ids
  - gate_candidate=true 的阶段，用 role: evaluator + condition + retry_on_gate_fail
    表达质检门语义，不要把重试展开成多个 step
  - candidate_parameters[] 里的值在 prompt 里换成 {参数名} 占位符，
    并据此生成 example_input
  - repeated_pattern 非空时，在生成结果最后额外提示"这段可以存成可复用
    step 片段"，但不要求这一步就自动调用 save_snippet（交给用户在预览
    阶段决定）

  任务总结：
  {task_summary 的 JSON/markdown 序列化}
  ```

- 生成结果照样过 `WorkflowGenerator.parse_yaml()`（`WorkflowDef.from_dict()`
  + `.validate()`），复用现成的校验路径，不新增第二套校验。
- **工具名幻觉防护**：`TaskSummary` 的阶段描述本身不含具体工具名（"approach"
  是意图层描述），所以②阶段生成的 step 默认应该是 `type: agent`（让主
  Agent 在执行时自己决定用什么工具），而不是让 LLM 凭空猜一个
  `type: tool_call` + `tool_name`。只有 `stage.approach` 里的操作明确到
  "调用某个确定性工具就够了"（比如整个阶段就是"跑一下测试命令"）这种情况，
  才允许生成 `tool_call`/`script` 类型，且要求生成的 `tool_name`
  必须能在 `get_default_registry()` 里查到，查不到就降级成 `agent` 类型
  （在 prompt 里明确这条约束，而不是生成完再校验拦截——校验拦截只能报错，
  没法自动降级）。

预览环节复用 `WorkflowGenerator.preview()`，在其基础上追加"哪些地方是从
总结抽象出来的"标注（比如给 gate_candidate 来源的 step 加一行
"⚠️ 由重试模式推断出的质检门，请确认这是你想要的效果"），不需要新的预览
函数，在现有 `preview()` 输出后面追加即可。

---

## 5. 入口与交互流程（完整拼接）

### 5.1 新增 Agent 工具

```python
@tool(name="list_recent_sessions", group="workflow", ...)
def list_recent_sessions(limit: int = 10) -> str:
    """
    列出最近 N 个 session：session_id、起止时间、首条用户输入前 60 字。
    纯读 meta.json + history.json 首条 user_input，不调用 LLM。
    用户想不起具体 session_id 时，先调这个工具帮用户定位。
    """

@tool(name="summarize_session_for_workflow", group="workflow", ...)
def summarize_session_for_workflow(session_id: str) -> str:
    """
    第①阶段：读取指定 session 的 history.json，起临时 Agent 生成
    TaskSummary，返回 3.4 节格式的人类可读摘要 + 内部保存 TaskSummary
    （落一份到 .agent/workflow_sessions/ 之外的临时位置，或者直接把
    TaskSummary 的 JSON 序列化附在返回文本末尾用 <!-- --> 之类的方式
    带一份，供下一步 build_workflow_from_summary 直接复用而不用重新总结
    ——具体落地方式在实现阶段确定，这里只要求"用户确认后不需要重新总结"）。
    对当前正在运行的这个 session 自己生成 workflow 时，session_id 传
    当前 session 的 id 即可，逻辑完全一致（1.1 节已定稿：始终读 history、
    起临时 Agent，不借用当前 Agent 续调用）。
    """

@tool(name="build_workflow_from_summary", group="workflow", ...)
def build_workflow_from_summary(session_id: str, adjustments: str = "") -> str:
    """
    第②阶段：读回上一步生成的 TaskSummary（或用户对总结提出的调整
    adjustments 一并带入），调用
    WorkflowGenerator.generate_from_summary()，返回预览 + YAML，
    走跟 generate_workflow 一样的"确认后调用 save_workflow"收尾。
    """
```

三个工具划得比较细，是为了让每一步都有一个明确的、用户可以打断/纠正的
落点（跟 `generate_workflow` → `save_workflow` 两段式是同一个设计原则的
延伸）；如果实现时发现主 Agent 编排这三个工具的调用顺序不够顺畅，可以在
主 Agent 的 system prompt 或者一段固定的工具描述文案里明确"这三个工具要
按顺序调用，中间等待用户确认"，不需要合并成一个大工具——合并成一个工具
会丢失"总结确认"这一步的打断点，违背 0 节的核心判断。

### 5.2 CLI 命令

```
/workflow sessions                      列出最近 session（同 list_recent_sessions）
/workflow from-session <session_id>     从指定 session 生成 workflow（触发完整两段式流程，
                                         在 CLI 里以连续的确认提示呈现，而不是拆两次命令）
```

CLI 路径下，因为没有"主 Agent 编排多个工具调用"这一层，`/workflow
from-session` 命令内部直接顺序调用"总结→展示确认（y/n/修改意见）
→构建→展示确认→保存"，用同步阻塞的方式做完整个流程，复用 5.1 节的
三个函数（不是三个 `@tool`，是它们背后的纯函数实现）。

### 5.3 完整流程示例

```
用户：把刚才那次修 xxx bug 的过程整理成一个 workflow
  │
Agent 调用 summarize_session_for_workflow(session_id="<当前session_id>")
  │
返回：3.4 节格式的摘要，Agent 转述给用户 + 追问"理解得对吗？"
  │
用户：对，但"修复"那步不需要做成质检门，就是普通重试就行
  │
Agent 调用 build_workflow_from_summary(session_id="...", adjustments="修复阶段不要做成质检门，用普通 retry_on_error 即可")
  │
返回：预览 + YAML
  │
用户：可以，保存吧
  │
Agent 调用 save_workflow(yaml_content=...)
```

---

## 6. 与已有机制的衔接

- **`workflow_snippets`（P7-③2）**：`TaskSummary.repeated_pattern` 非空时，
  ②阶段生成结果里额外提示"这几个阶段的组合在原 session 里重复出现了，
  要不要存成可复用片段"，用户确认后调用已有的
  `WorkflowStore.save_snippet()`，不需要新逻辑。
- **`defaults`（P7-③1）**：②阶段生成 YAML 时，如果多个 stage 映射出的
  step 用同一个非默认 `model`/`max_turns`（这次 session 实际用的配置可以
  从 session 的 `meta.json` 读到），直接折叠进顶层 `defaults`，复用现有
  字段，不新增机制。
- **自定义/插件 step 类型（P7-④1/④2）**：如果 session 里实际调用过
  `myplugins/` 注册的自定义类型的能力（这个信号目前 history 里不直接可见，
  只能通过工具名反推——如果 ActionEvent 里出现了不在
  `IntentActionMapper` 内置分类表里的工具名，且该工具名匹配某个已注册的
  自定义 step 类型名，可以在①阶段的时间线摘要里附带一条"该工具属于插件
  XXX"的提示，供②阶段判断是否直接生成对应 `type`）。这一条优先级较低，
  可以留到基础功能跑通之后再补。

---

## 7. 实施检查清单

- [x] `perception/lesson_rules.py::is_tool_error()` 复用确认；
      `mini_agent/llm/system_tool_call.py::render_tool_results()` 的输出
      格式确认（用于按 `tool_call_id` 关联 tool_result 到具体 tool_use，
      判断每次调用成功/失败）——实际实现按"同一批 assistant_reply 之后
      紧随的一条 tool_result 消息，内含按顺序拼接的 `<tool_result>{json}
      </tool_result>` 块"这个不变式做位置对应，不依赖显式 `tool_call_id`
      字段（`render_tool_results()` 目前不写 `tool_call_id`）。
- [x] `workflow/session_summarizer.py`：`build_timeline_text()` +
      `TaskSummary`（dataclass + `to_markdown()`）+
      `summarize_session_for_workflow()`
- [x] `agent/_helpers.py` 新增 `_parse_task_summary()`（沿用
      `_parse_lesson_candidates` 的解析模式）
- [x] 新增 prompt 模板：`prompts/system/session_to_workflow_summary.md` +
      `prompts/user/session_to_workflow_summary_request.md`
- [x] `workflow/generator.py`：`WorkflowGenerator.generate_from_summary()`
      + 对应 prompt 文案；工具名幻觉防护（生成前 prompt 约束 + 生成后
      `get_default_registry().names` 校验降级为 `_downgrade_unknown_tool_types()`）
- [x] `workflow/tools.py`：新增 `list_recent_sessions` /
      `summarize_session_for_workflow` / `build_workflow_from_summary`
      三个 `@tool`；`TaskSummary` 中间产物落地方式：进程内内存缓存
      （`_task_summary_cache: dict[str, TaskSummary]`，按 session_id 索引，
      供②阶段直接复用；进程重启会丢失缓存，属于可接受降级——重新总结一次
      即可，不是数据丢失）
- [x] CLI：`/workflow sessions` / `/workflow from-session <session_id>`
      （`cli/commands/workflow_cmd.py::_handle_sessions/_handle_from_session`，
      同步阻塞完成"总结→确认→构建→确认→保存"全流程；同时把两个子命令
      补进 `ui/terminal.py` 的 Tab 补全列表 `_COMMANDS`）
- [x] 文档：完成后同步更新 `docs/workflow-guide.md`（新增"从历史 Session
      生成 Workflow（session_to_workflow，P8）"一节）和
      `.claude/skills/workflow-generator/SKILL.md`（新增"另一条生成路径"
      说明，区分"手写 YAML"场景与"从 session 反向生成"场景）
- [x] 单元测试：`tests/test_session_to_workflow.py`（`build_timeline_text`
      的交替拼接与出错计数、`TaskSummary.from_dict`/`to_markdown`、
      `_parse_task_summary` 的围栏剥离/非法 JSON 降级、
      `summarize_session_for_workflow` 的空 timeline/无效 LLM 输出报错路径、
      `_downgrade_unknown_tool_types` 的降级/保留分支）

**已知未完成 / 后续可做**：
- 2.4 节"长 session 截断策略"（合并连续同类型 ActionEvent、按
  `token_counter.py::estimate_messages_tokens()` 估算阈值）尚未实现——当前
  `build_timeline_text()` 是无截断的基础版本，超长 session 可能在总结阶段
  遇到 token 预算问题，需要时再补。
- 6 节"自定义/插件 step 类型"的"该工具属于插件 XXX"提示尚未实现（design
  doc 里已标注优先级较低，留到基础功能跑通之后再补）。
- `workflow_snippets`/`defaults` 的自动衔接（6 节前两条）目前只在生成结果
  文案里做了"建议手动 save_snippet"的提示，未实现"自动折叠进顶层
  defaults"这一步。

> 建议实施顺序：先做 `session_summarizer.py` + 总结 prompt（可以先用 CLI
> 命令 `/workflow sessions` + 一个临时脚本验证总结质量，不急着接语义确认
> UI），总结质量达标后再接②构建阶段和三个 Agent 工具，最后补 CLI 和文档。
