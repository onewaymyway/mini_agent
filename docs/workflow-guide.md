# 工作流系统（Workflow System）

mini_agent 内置了一套轻量的工作流引擎，支持将多步 AI 任务固化为可复用的流程定义。
工作流可以通过 LLM 自动生成，也可以手动编写 YAML 文件，保存后随时执行。

---

## 核心概念

```
WorkflowDef（工作流定义）
  ├─ name / description / version
  └─ steps: list[WorkflowStep]
        ├─ id          步骤唯一标识（用于依赖引用和占位符）
        ├─ prompt      Prompt 模板（支持 {step_id.output} 占位符）
        ├─ role        执行角色（null = 主 Agent，"evaluator" = 质检角色）
        ├─ depends_on  依赖的步骤 id 列表（拓扑排序依据）
        ├─ condition   执行条件表达式（如 "evaluate.score >= 60"）
        └─ retry_on_gate_fail  质检不达标时重跑前序步骤的最大次数
```

**关键设计原则**：
- **步骤核心固定**：工作流文件保存后，步骤的 id / 依赖关系 / 角色绑定不会在运行时改变
- **参数动态注入**：步骤 prompt 中的 `{param_name}` 占位符在 `run_workflow` 时传入
- **结果自动传递**：`{step_id.output}` 和 `{step_id.score}` 在执行时自动替换为前序步骤的输出

---

## 文件位置

```
<project_root>/.agent/workflows/*.yaml   # 工作流定义文件
```

框架启动时不预加载工作流，按需通过 `run_workflow` 工具名称加载。

---

## YAML 格式

### 完整字段说明

```yaml
name: workflow_name        # 工作流唯一名称（英文小写，对应文件名）
description: 描述          # 用于 list_workflows 展示，中文可用
version: "1.0"             # 版本号，纯标识用途

steps:
  - id: step_id            # 步骤唯一标识，英文小写下划线
    name: 步骤名称          # 可读名称
    prompt: |              # Prompt 模板（支持占位符）
      ...
    role: null             # 执行角色：null（主 Agent）或角色 profile name
    type: null             # [P5] 显式类型：null=按role自动推断 / agent / role_agent /
                           #      sub_workflow / tool_call / human_input / script
    workflow_name: null    # [P5] type=sub_workflow 时必填：引用的工作流名称
    tool_name: null        # [P5] type=tool_call 时必填：要调用的工具名称
    tool_args: {}          # [P5] type=tool_call 时的工具入参（为空则用 prompt 作为唯一实参）
    input_prompt: null     # [P5] type=human_input 时展示给人类的提示语（为空则用 prompt）
    script: null           # [P5] type=script 时必填：要执行的 shell 命令
    require_approval: false # 是否要求人工审批门放行
    depends_on: []         # 依赖的步骤 id 列表，控制执行顺序
    condition: null        # 执行条件，null 表示无条件执行
    max_turns: 10          # 该步骤允许的最大 LLM 轮数（默认 10）
    model: null            # 覆盖模型（null = 继承全局）
    timeout: null          # 超时秒数（null = 不限制）
    retry_on_gate_fail: 0  # 质检不达标时重跑次数（0 = 不重跑）
    retry_on_error: 0      # 普通异常重试次数（0 = 不重试）
```

### Prompt 占位符

| 占位符 | 含义 | 示例 |
|--------|------|------|
| `{param_name}` | 运行时从 `inputs` 注入的动态参数 | `{code}`, `{topic}` |
| `{step_id.output}` | 指定步骤的完整输出文本 | `{analyze.output}` |
| `{step_id.score}` | 指定步骤的评分（0-100 整数字符串）| `{evaluate.score}` |

### condition 表达式

`condition` 支持简单 Python 表达式，变量为步骤 id，属性有：

| 属性 | 类型 | 说明 |
|------|------|------|
| `step_id.score` | int | 该步骤的评分（0-100），未提取到时为 0 |
| `step_id.output` | str | 该步骤的完整输出文本 |
| `step_id.status` | str | `"done"` / `"skipped"` / `"failed"` / `"gate_failed"` |
| `step_id.passed` | bool | `status == "done"` |

```yaml
condition: "evaluate.score >= 60"          # 评分达标才执行
condition: "analyze.passed"                # 分析步骤成功才执行
condition: "evaluate.score >= 60 and analyze.passed"  # 多条件组合
```

---

## 步骤执行状态

| 状态 | 值 | 含义 |
|------|-----|------|
| `DONE` | `done` | 成功完成 |
| `SKIPPED` | `skipped` | `condition` 不满足，跳过 |
| `FAILED` | `failed` | 执行抛出异常 |
| `GATE_FAILED` | `gate_failed` | evaluator 角色评分未达 `pass_threshold` |
| `PENDING` | `pending` | 因依赖步骤失败未能执行 |

`GATE_FAILED` 与 `FAILED` 的区别：
- `FAILED`：系统级错误（网络超时、代码异常等），无法继续
- `GATE_FAILED`：内容质量不达标，可以通过 `retry_on_gate_fail` 触发重跑

---

## 质检门（Evaluator Gate）

当步骤的 `role` 指向一个 `role_type: evaluator` 的 profile 时，
该步骤自动具备质检门能力：

```
执行 evaluator 步骤
  → 提取评分
  → 评分 ≥ profile.pass_threshold → DONE，继续后续步骤
  → 评分 < pass_threshold         → GATE_FAILED
        → retry_on_gate_fail > 0？
              → 是：把评估反馈追加到依赖步骤的 prompt，重跑依赖步骤
                    → 再次运行 evaluator → 循环直到通过或达到重试上限
              → 否：步骤标记为 GATE_FAILED，后续依赖此步骤的步骤被跳过
```

**阈值来源**：`pass_threshold` 从角色 profile 的 frontmatter 读取，
而非工作流 YAML。这样同一个 evaluator profile 在不同工作流中保持一致的质量标准。

---

## 内置工具（主 Agent 可直接调用）

工作流系统向主 Agent 注册了 16 个工具（P1 基础 6 个 + P2-P4 看护机制 7 个 +
P5/P6 新增 3 个）：

### `generate_workflow`

根据自然语言描述生成工作流 YAML，展示预览，用户确认后调用 `save_workflow` 保存。

```
参数：
  description (str)     工作流的自然语言描述
  example_input (str)   可选，运行时需要的输入参数示例

示例调用：
  generate_workflow("做一个技术文档写作流程，包括大纲生成、内容撰写和质量审核")
  generate_workflow("代码审查流程", '{"code": "def foo(): pass", "lang": "python"}')
```

### `save_workflow`

将 YAML 字符串保存为工作流文件。

```
参数：
  yaml_content (str)   完整的工作流 YAML 字符串

保存路径：<project_root>/.agent/workflows/<name>.yaml
```

### `run_workflow`

执行已保存的工作流，按拓扑顺序逐步执行，返回完整的执行摘要。

```
参数：
  name (str)       工作流名称
  inputs (str)     JSON 字符串，步骤 prompt 中的动态参数

示例：
  run_workflow("code_review", '{"code": "def foo(): pass"}')
  run_workflow("article_writer", '{"topic": "大模型应用架构"}')
```

### `list_workflows`

列举所有已保存工作流的名称、描述、步骤数和步骤列表。

### `show_workflow`

查看指定工作流的完整 YAML 定义，用于检查或准备编辑。

### `delete_workflow`

删除指定工作流文件。

### `provide_workflow_step_input`（P5）

向一个正在等待人工输入（`human_input` 类型 step）的执行送入文本。

```
参数：
  workflow_session_id (str)   正在执行的工作流的执行 ID
  input_text (str)            要送入的文本
```

### `list_workflow_templates`（P6）

列举内置工作流模板（`code_review` / `research_report` / `multi_perspective_debate`）。

### `create_workflow_from_template`（P6）

基于内置模板创建并保存一个新工作流，比 `generate_workflow` 更稳定。

```
参数：
  template_name (str)   模板名称，见 list_workflow_templates 的输出
  new_name (str)        新工作流的名称

示例：
  create_workflow_from_template("code_review", "my_pr_review")
```

---

## 示例工作流：Step 类型化全览（`release_pipeline.yaml`）

`.agent/workflows/release_pipeline.yaml`（配套 `notify_summary.yaml` 作为
被引用的子工作流）演示了 P5 新增的全部 6 种 step 类型，可作为编写自定义
工作流时的参考模板：

| step id | type | 说明 |
|---|---|---|
| `inspect_project` | `tool_call` | 直接调用 `list_dir` 工具检查项目结构 |
| `run_smoke_test` | `script` | 执行 shell 命令模拟冒烟测试（需开启 `script_step_enabled`） |
| `collect_release_notes` | `human_input` | 阻塞等待人工输入本次发布要点 |
| `draft_changelog` | `agent`（默认） | 独立主 Agent 撰写正式 changelog |
| `quality_check` | `role_agent` | evaluator 角色打分门，`SCORE < 60` 判 `GATE_FAILED` |
| `notify` | `sub_workflow` | 引用 `notify_summary.yaml` 生成一句话摘要通知 |

试跑（人工审批 + script 默认关闭，需要按需调整 `agent_config.json` 或加
`--background`）：

```
/workflow run release_pipeline --background
# 另开一个终端等 collect_release_notes 步骤挂起后：
/workflow input <workflow_session_id> "1. 新增暗黑模式\n2. 修复登录闪退"
```

> 提示：`sub_workflow` 执行器固定只把已解析占位符的 prompt 文本作为
> `{"input": ...}` 传给子工作流，因此任何打算被 `sub_workflow` 引用的
> 工作流（如 `notify_summary.yaml`），顶层步骤都应该用 `{input}` 接收
> 这唯一的一份文本，而不能像 `code_review.yaml` 那样用 `{code}` 这类
> 自定义参数名（那类工作流只能被 `run_workflow` 直接调用）。

---

## 示例工作流：代码审查（`code_review.yaml`）

框架内置了一个完整示例，位于 `.agent/workflows/code_review.yaml`：

```yaml
name: code_review
description: 代码审查完整流程，包括分析、深度审查、质量评估和报告生成
version: "1.0"
steps:
  - id: analyze
    name: 静态分析
    prompt: |
      请对以下代码进行静态分析：
      {code}

  - id: review
    name: 深度审查
    prompt: |
      基于分析结果：{analyze.output}
      原始代码：{code}
      请进行安全性、性能、可维护性的深度审查。
    depends_on: [analyze]

  - id: evaluate
    name: 质量评估
    prompt: |
      请对审查结论进行质量评估（输出必须包含 SCORE: x/10）：
      分析：{analyze.output}
      审查：{review.output}
    depends_on: [review]
    role: evaluator
    retry_on_gate_fail: 1    # 评分不达标时，重跑 review 步骤一次

  - id: report
    name: 生成审查报告
    prompt: |
      生成正式代码审查报告。
      分析：{analyze.output}  审查：{review.output}  评分：{evaluate.score}/100
    depends_on: [evaluate]
    condition: "evaluate.score >= 40"   # 评分低于 40 不生成报告
```

**运行方式**：
```
run_workflow("code_review", {"code": "def calculate(a, b): return a/b"})
```

---

## 自定义工作流示例

### 技术文章写作流程

```yaml
name: article_writer
description: AI 技术文章写作，包括大纲、正文和润色
version: "1.0"
steps:
  - id: outline
    name: 生成大纲
    prompt: |
      请为主题"{topic}"生成一份详细的技术文章大纲。
      目标读者：{audience}
      文章长度：{length}

  - id: write
    name: 撰写正文
    prompt: |
      基于以下大纲撰写完整的技术文章：
      {outline.output}
      主题：{topic}
    depends_on: [outline]
    max_turns: 20          # 写作可能需要更多轮次

  - id: quality_check
    name: 质量评估
    prompt: |
      对以下文章进行技术准确性和可读性评估：
      {write.output}
      必须在最后输出：SCORE: x/10
    depends_on: [write]
    role: evaluator
    retry_on_gate_fail: 1

  - id: polish
    name: 润色优化
    prompt: |
      基于质检意见对文章进行润色：
      原文：{write.output}
      质检意见：{quality_check.output}
    depends_on: [quality_check]
    condition: "quality_check.score >= 50"
```

**运行**：
```
run_workflow("article_writer", {
  "topic": "大模型 Agent 的记忆系统设计",
  "audience": "高级工程师",
  "length": "2000字"
})
```

---

## 执行摘要格式

`run_workflow` 执行完成后返回标准格式摘要：

```
## 工作流执行结果：code_review
状态：done  耗时：42.3s

✅ **analyze**  (8.1s)
   代码结构较为简单，存在除零风险...
✅ **review**  (12.4s)
   安全问题：未处理 ZeroDivisionError...
✅ **evaluate**  评分：72/100  (6.2s)
   内容较完整，SCORE: 7.2/10
🔄 **evaluate** (retry)  评分：85/100  (5.8s)
   改进后内容良好，SCORE: 8.5/10
✅ **report**  (9.8s)
   ## 代码审查报告...

---
### 最终输出
## 代码审查报告
...
```

状态图标含义：`✅ done` · `⏭️ skipped` · `❌ failed` · `🔄 gate_failed`

---

## 手动编写工作流 YAML

除了通过 `generate_workflow` 自动生成，也可以直接在 `.agent/workflows/` 目录
下创建 YAML 文件。格式同上，保存后无需重启，下次 `run_workflow` 调用时自动加载。

**命名规范**：
- 文件名与 `name` 字段保持一致（如 `code_review.yaml` ↔ `name: code_review`）
- 名称只使用英文小写字母、数字、中划线、下划线

**编辑已有工作流**：
1. `show_workflow("name")` 查看当前 YAML
2. 在文件系统直接编辑 `.agent/workflows/<name>.yaml`
3. 再次 `run_workflow` 时自动加载新版本

---

## 工作流与角色 Agent 的集成

工作流步骤可以通过 `role` 字段直接绑定角色 Agent：

```yaml
# 步骤绑定 evaluator
- id: evaluate
  role: evaluator     # 对应 .agent/agents/evaluator.md
  retry_on_gate_fail: 1

# 步骤绑定自定义角色
- id: compliance_check
  role: compliance-checker   # 对应 .agent/agents/compliance-checker.md
```

绑定规则：
- `role` 的值是角色 profile 的 `name` 字段（文件名去掉 `.md`）
- 框架自动从 profile 读取 `role_type` 和 `pass_threshold`
- `evaluator` 类型的步骤才参与质检门逻辑；`coach` / `custom` 类型只注入输出，不评分

---

## 注意事项

1. **步骤隔离**：每个步骤使用独立的 Agent 实例，历史不互通，只通过占位符传递结果。
   这避免了长上下文累积，但也意味着步骤间不能依赖隐式上下文。

2. **循环依赖检测**：`WorkflowDef.validate()` 会在保存时检测循环依赖，
   `run_workflow` 也会在执行前做拓扑排序，循环依赖会导致执行失败并报错。

3. **condition 安全**：`condition` 表达式在受限环境中执行（`__builtins__` 为空），
   只能访问步骤结果命名空间，不能执行任意 Python 代码。

4. **inputs 参数**：`run_workflow` 的 `inputs` 必须是合法 JSON 字符串。
   工作流 prompt 中没有对应变量的占位符会保持原样（不报错），方便调试。

5. **并发执行**：同一拓扑层内互不依赖的步骤默认并发执行（线程池），
   可通过 `agent_config.json` 的 `workflow.parallel_enabled` / `max_parallel`
   全局控制，或在单个步骤上设置 `allow_parallel: false` 强制串行。

---

## Workflow Session：执行会话与数据目录（P1/P2）

自本次改进起，**每一次 `run_workflow` 调用都会创建一个 WorkflowSession**，
不再是"跑完即焚"的一次性调用。所有相关数据聚合在：

```
.agent/workflow_sessions/<workflow_session_id>/
  ├── session.json          # 执行状态：status/当前批次/control_flags/待审批step
  ├── workflow_def.yaml     # 执行时使用的工作流定义快照（防止运行中途原文件被改）
  ├── events.jsonl          # 结构化事件流（workflow_start/step_end/paused/...）
  ├── watchdog.jsonl        # 看护线程的心跳超时/资源护栏告警记录
  └── step_<step_id>/       # 该 step 对应 Agent 的完整数据
      └── <session_id>/     # history / meta / traces / temp / output / artifacts
```

`workflow_session_id` 在调用 `run_workflow` 时自动生成并在返回结果里给出
（后台模式下直接在返回文本里）；`list_workflow_runs` / `get_workflow_run_status`
可以查询任意一次执行的实时进度。

**断点恢复**：进程崩溃或主动暂停后，用 `resume_workflow_run(workflow_session_id)`
即可从已完成的批次之后继续跑，不会重跑已经 `done` 的步骤。

---

## 后台执行、暂停、取消（P3）

`run_workflow` 新增 `background` 参数：

- `background=False`（默认，或 `agent_config.json` 里
  `workflow.background_execution_default=false`）：**前台同步执行**，
  工具调用会一直阻塞到工作流跑完，返回完整的结果摘要，行为与改进前一致。
- `background=True`：立即返回 `workflow_session_id`，工作流在**后台线程**
  继续执行。此时可以用：
  - `get_workflow_run_status(workflow_session_id)` 查看进度
  - `pause_workflow_run(workflow_session_id)`：请求暂停，会在当前批次
    跑完后停下，可用 `resume_workflow_run` 续跑
  - `cancel_workflow_run(workflow_session_id)`：请求取消，正在跑的步骤
    尽快中止，未开始的步骤标记为 `cancelled`

**看护线程**（`workflow.watchdog_enabled`，默认开启）在后台监控：
- 每个步骤的心跳是否超过其 `timeout` 未更新，超时后强制标记该步骤为
  `timeout` 状态并继续推进（已知限制：Python 线程无法被安全强杀，超时
  后底层线程可能仍在后台跑完，但 runner 不再等待）；
- 累计执行时长是否超过 `WorkflowDef.max_total_duration` 或全局配置
  `workflow.max_total_duration_seconds`，超过则主动请求取消。

---

## 人工审批门（P4）

在步骤定义里加上 `require_approval: true`，工作流跑到该步骤前会暂停，
等待人工调用 `approve_workflow_step(workflow_session_id)` 放行，或
`reject_workflow_step(workflow_session_id, reason="...")` 拒绝（该步骤会
被标记为 `rejected` 并跳过，下游依赖它的步骤按 `SKIPPED`/`FAILED` 语义
处理）。

```yaml
- id: send_notification
  role: main
  prompt: "..."
  require_approval: true   # 高风险/有外部副作用的步骤，默认要求人工放行
```

**必须配合 `background=True` 使用**：前台同步执行时没有其它线程能在
阻塞期间调用 approve/reject，等待会在
`workflow.approval_wait_timeout_seconds`（默认 600 秒）后自动判定为拒绝。
若工作流里检测到任何 `require_approval` 步骤，`run_workflow`/`resume_workflow_run`
会自动切换为后台执行，无需手动传 `background=True`。

---

## 通用失败重试（P4）

`retry_on_error`（区别于原有的 `retry_on_gate_fail` 质检门重试）用于处理
网络超时、工具报错等**普通异常**：

```yaml
- id: fetch_external_api
  role: main
  prompt: "..."
  retry_on_error: 2   # 失败后最多重试 2 次，每次退避时长递增
```

退避时长由 `workflow.retry_on_error_backoff_seconds`（默认 5 秒）线性递增
（第 N 次重试等待 N × backoff 秒）。

---

## Step 类型化（P5）

`WorkflowStep.type` 显式声明该步骤"怎么被执行"，未设置时按旧语义自动推断
（`role` 非空 → `role_agent`，否则 → `agent`），**完全向后兼容旧 YAML**：

| `type` | 说明 | 专属字段 |
|---|---|---|
| `agent`（默认） | 独立主 Agent 实例执行 | — |
| `role_agent` | 指定角色 Agent 执行（`role` 非空时的旧默认行为） | `role` |
| `sub_workflow` | 把另一个已保存的工作流当作一个 step 执行 | `workflow_name` |
| `tool_call` | 直接调用一个已注册工具，不启动整个 Agent 会话 | `tool_name`, `tool_args` |
| `human_input` | 阻塞等待人工通过 `provide_workflow_step_input` 送入文本 | `input_prompt` |
| `script` | 执行一段 shell 命令 | `script` |

```yaml
- id: notify
  type: tool_call
  tool_name: send_slack_message
  tool_args:
    channel: "#eng"
    text: "审查通过"

- id: ask_reviewer
  type: human_input
  input_prompt: "请输入本次发布的审批意见"

- id: sub_report
  type: sub_workflow
  workflow_name: research_report   # 引用另一个已保存的工作流
  depends_on: [sub_report_input]

- id: build
  type: script
  script: "npm run build"
```

**安全默认值**（均可在 `agent_config.json` 的 `workflow` 节里配置，见下文）：
- `sub_workflow` 有递归深度保护（`max_sub_workflow_depth`，默认 3），避免
  A 引用 B、B 又引用 A 造成的无限递归。
- `tool_call` 默认视为高风险步骤，即使没有显式写 `require_approval: true`，
  也会走人工审批门（除非把 `tool_call_step_auto_approve` 显式设为 `true`）。
- `script` **默认关闭**（`script_step_enabled: false`），工作流 YAML 可能来自
  LLM 生成或他人分享，默认不允许执行任意 shell 命令，需要显式打开开关。
- `human_input` 等待有超时保护（`human_input_wait_timeout_seconds`，默认
  1800 秒），超时后该步骤标记为 `FAILED`。

在 CLI 里对正在等待人工输入的执行送入文本：

```
/workflow input <workflow_session_id> <要送入的文本>
```

Agent 侧对应的工具是 `provide_workflow_step_input(workflow_session_id, input_text)`。

---

## 生命周期 Hook（P5）

Workflow 执行时会触发以下 Hook 事件（复用项目现有的 `mini_agent.hooks` 体系，
在 `.agent/hooks.json` 里声明命令即可挂钩，无需改动源码）：

| 事件 | 触发时机 |
|---|---|
| `WorkflowStart` | 一次 `run()` 开始执行 |
| `WorkflowStepStart` | 每个 step 实际开始执行前 |
| `WorkflowStepEnd` | 每个 step 执行结束后（无论成功/失败/跳过） |
| `WorkflowGateFailed` | evaluator 质检门判定 `GATE_FAILED` 时 |
| `WorkflowEnd` | 一次执行结束（`done`/`failed`/`partial`/`paused`/`cancelled`） |

受 `workflow.hooks_enabled` 开关控制（默认开启）；Hook 触发失败不会影响
工作流主流程（异常会被吞掉并记录日志）。

---

## 保存前引用完整性校验（P6）

`save_workflow` / `create_workflow_from_template` 保存前会调用
`WorkflowDef.validate()`，除了原有的 id 重复/依赖缺失检查，新增：

- **类型专属必填字段**：`sub_workflow` 缺少 `workflow_name`、`tool_call`
  缺少 `tool_name`、`script` 缺少 `script` 命令都会被拒绝；`sub_workflow`
  引用自身（导致无限递归）也会被拒绝。
- **占位符引用完整性**（受 `workflow.validate_placeholders_on_save` 控制，
  默认开启）：扫描 `prompt` 中 `{step_id.output}` / `{step_id.score}` 形式
  的占位符，检查 `step_id` 是否真的存在于工作流里，避免笔误导致运行时
  才发现引用了不存在的步骤。`{param_name}` 这种不带 `.` 的占位符属于运行时
  `inputs`，不受此项检查。
- **角色引用校验**（受 `workflow.validate_role_refs_on_save` 控制，默认
  开启）：校验 `role` 字段是否为已注册的角色 Agent profile（需要调用方
  传入 `role_checker`，未传入时自动跳过，不影响单测/无 dispatcher 环境）。

---

## 内置工作流模板库（P6）

不想从零手写 YAML 或依赖 LLM 生成时，可以直接基于内置模板创建：

```
/workflow templates                          列举内置模板
/workflow from-template code_review my_review   基于模板创建新工作流
```

对应 Agent 工具：`list_workflow_templates()` / `create_workflow_from_template(template_name, new_name)`。

当前内置模板：

| 模板名 | 说明 |
|---|---|
| `code_review` | 静态分析 → 深度审查 → 质量评估（evaluator 打分门）→ 生成报告 |
| `research_report` | 资料收集 → 要点提炼 → 交叉验证（evaluator 打分门）→ 生成报告 |
| `multi_perspective_debate` | 正方/反方论证并行展开 → 综合裁决（evaluator 打分门）→ 生成结论 |

模板本身只是随包分发的只读 YAML（`workflow/templates/*.yaml`），
`create_workflow_from_template` 只是把模板加载出来、替换 `name` 字段后
交给普通的 `save()` 落盘——会经过和手写 YAML 完全一样的校验路径。

---

## workflow 相关配置（`agent_config.json`）

```json
"workflow": {
  "parallel_enabled": true,
  "max_parallel": 4,
  "watchdog_enabled": true,
  "heartbeat_check_interval_seconds": 5.0,
  "max_total_duration_seconds": null,
  "approval_poll_interval_seconds": 3.0,
  "approval_wait_timeout_seconds": 600.0,
  "retry_on_error_backoff_seconds": 5.0,
  "background_execution_default": false,
  "hooks_enabled": true,
  "max_sub_workflow_depth": 3,
  "script_step_enabled": false,
  "script_step_timeout_seconds": 60.0,
  "tool_call_step_auto_approve": false,
  "human_input_wait_timeout_seconds": 1800.0,
  "validate_placeholders_on_save": true,
  "validate_role_refs_on_save": true
}
```

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `parallel_enabled` | `true` | 是否允许同层步骤并发执行 |
| `max_parallel` | `4` | 同层并发的最大 worker 数 |
| `watchdog_enabled` | `true` | 是否启用看护线程（心跳超时检测+资源护栏） |
| `heartbeat_check_interval_seconds` | `5.0` | 看护线程轮询间隔（秒） |
| `max_total_duration_seconds` | `null` | 全局总执行时长护栏（秒），`null`=不限制，可被单个工作流的 `max_total_duration` 覆盖 |
| `approval_poll_interval_seconds` | `3.0` | 审批门等待时的轮询间隔（秒） |
| `approval_wait_timeout_seconds` | `600.0` | 审批等待超时（秒），`null`=无限等待 |
| `retry_on_error_backoff_seconds` | `5.0` | `retry_on_error` 重试的基础退避时长（秒） |
| `background_execution_default` | `false` | `run_workflow` 未显式传 `background` 时的默认行为 |
| `hooks_enabled`（P5） | `true` | 是否触发 WorkflowStart/StepStart/StepEnd/GateFailed/WorkflowEnd 生命周期 Hook |
| `max_sub_workflow_depth`（P5） | `3` | `sub_workflow` 类型 step 允许的最大嵌套深度 |
| `script_step_enabled`（P5） | `false` | 是否允许 `script` 类型 step 执行 shell 命令，默认关闭 |
| `script_step_timeout_seconds`（P5） | `60.0` | `script` 类型 step 的默认超时（可被 `step.timeout` 覆盖） |
| `tool_call_step_auto_approve`（P5） | `false` | `tool_call` 类型 step 是否默认跳过审批门 |
| `human_input_wait_timeout_seconds`（P5） | `1800.0` | `human_input` 类型 step 等待人工输入的超时（秒），`null`=无限等待 |
| `validate_placeholders_on_save`（P6） | `true` | 保存工作流时是否校验占位符引用完整性 |
| `validate_role_refs_on_save`（P6） | `true` | 保存工作流时是否校验 `role` 是否为已注册的角色 Agent profile |

---

## CLI 命令：`/workflow`

除了让主 Agent 调用工具，也可以在 CLI 里直接输入 `/workflow` 系列命令
（支持 Tab 补全）：

```
/workflow list                              列举所有已保存的工作流
/workflow show <name>                       查看工作流 YAML 定义
/workflow run <name> [inputs_json] [--background]
                                             执行工作流
/workflow runs [name]                       列举执行记录（可按工作流名过滤）
/workflow status <workflow_session_id>      查看某次执行的详细进度
/workflow resume <workflow_session_id> [--background]
                                             从断点续跑
/workflow pause <workflow_session_id>       暂停一次后台执行
/workflow cancel <workflow_session_id>      取消一次执行
/workflow approve <workflow_session_id>     批准当前等待审批的步骤
/workflow reject <workflow_session_id> [reason]
                                             拒绝当前等待审批的步骤
/workflow input <workflow_session_id> <text>
                                             向等待人工输入的步骤送入文本（P5）
/workflow templates                         列举内置工作流模板（P6）
/workflow from-template <template_name> <new_name>
                                             基于内置模板创建工作流（P6）
/workflow delete <name>                     删除工作流定义
```

**已知限制**：`pause`/`cancel`/`approve`/`reject`/`input` 依赖进程内的控制状态
（`workflow/registry.py`），只在**同一个进程**里对正在跑的后台执行有效；
若 CLI 进程重启，只能依赖磁盘上 `session.json` 的最终状态，配合
`resume_workflow_run` 重新接续执行。
