# Goal 执行规范自动生成 + 用户确认机制

- **状态**：设计草案 / 待评审（尚未实施——先把机制想清楚，评审通过后再拆
  Track 落地，延续既有节奏）。
- **触发背景**：用户反馈"goal 系统已经有自动生成验收标准的能力
  （`goal_mode/spec.py::GoalSpecBuilder`），但缺一步把 Goal 具体化——
  尤其是设为周期性执行时，目录结构、产出物、跨轮次怎么传信息这些细节
  应该有一份规范，且规范生成后要给用户确认/反馈修改，看板新建 Goal 时
  也要能触发这个流程"。

## 0. 现状盘点（先说清楚"已经有什么、还缺什么"）

**已经有的**：

| 机制 | 位置 | 覆盖的问题 | 与本方案的关系 |
| --- | --- | --- | --- |
| `GoalSpecBuilder`（draft → 反馈迭代 → confirm） | `goal_mode/spec.py` | 单次会话内的一次性目标，自动生成结构化验收标准，独立协商会话，不进主 Agent 历史 | **架构上的直接前例**——本方案是把这一套"生成草案→用户确认/反馈重生成→冻结"的流程搬到 `GoalBacklog` 的跨会话 Goal 上，产出的规范内容不同（验收标准 → 执行规范） |
| `output_workspace.py`（`allocate_cycle_dir`/`write_manifest`/`format_manifest_for_prompt`） | `evolution/output_workspace.py` | 每个 Goal（周期性 `cycle_%04d` / 一次性 `run_%04d`）分配固定的产出目录，`manifest.json` 记录本轮产出清单，`latest.json` 指针 + prompt 占位符把上一轮产出自动摘要注入下一轮 | **本方案要复用的基础设施**——目录分配和跨轮传递的"通用管道"已经齐了，本方案不重新造轮子，只是让这套通用管道对**具体某个 Goal**能有更贴合它本身的定制内容（见 §1） |
| `output_path_policy.md` | `.agent/policies/output_path_policy.md` | 全局负面清单（不许写 `src/`、`tests/`），对所有任务生效 | 全局层面的约束，粒度是"整个项目"，不针对单个 Goal |
| `GoalNode.description`/`progress_notes`/`user_feedback` | `perception/goal_backlog.py` | Goal 的静态描述、动态进度、用户持续意见 | 都是自由文本，没有结构化的"这个 Goal 具体怎么执行"规范 |

**缺的（用户这次反馈的核心）**：

1. `output_workspace.py` 提供的目录/manifest/传递机制是**对所有 Goal
   一视同仁的通用约定**（同样的字段、同样的目录命名规则）——但"这个
   具体的 Goal 每一轮应该产出什么格式的文件、文件名怎么命名、除了
   通用 `artifacts` 列表之外还需要在轮次之间显式传递哪些结构化信息
   （比如"累计已处理到第几页"、"上次报告里的具体数字，这次要对比"）"，
   这些是**Goal 特定**的，现在完全没有任何机制去想清楚、写下来。
2. 没有一步"生成 → 用户确认/反馈修改"的交互，跟 `GoalSpecBuilder` 已经
   验证过的模式脱节——用户已经习惯了"AI 先想一遍细节，我来确认/改"这种
   交互，Goal 具体化这一步现在是空白，只能靠用户自己在创建 Goal 时的
   `description` 里手写（大多数情况下不会写，或写得不够细）。
3. 看板"设为周期性"（`apps/mini_agent_kanban/app.py` 的"⏰ 周期性设置"
   expander）现在只问"调度 + 每轮任务内容"两个字段，没有触发任何"想清楚
   细节"的步骤——恰恰是用户认为"周期性执行更需要具体化"的场景，现在
   反而是最简陋的表单。

## 1. 核心概念：`GoalExecutionSpec`（Goal 执行规范）

一份**每个 Goal 一份、可覆盖/可留空回退默认**的结构化规范，字段设计
参考用户提出的三类细节 + 复用现有验收标准的位置：

```json
{
  "version": 1,
  "goal_id": "goal_abcd1234",
  "generated_at": 1754567890.0,
  "confirmed": false,
  "confirmed_at": null,

  "deliverables": [
    {
      "name": "weekly_report.md",
      "description": "本周数据对比报告，Markdown 格式，含上周同期对比表格",
      "naming_pattern": "weekly_report.md",
      "required_every_cycle": true
    }
  ],

  "handoff_fields": [
    {
      "key": "last_processed_cursor",
      "description": "上次处理到的游标/页码，下一轮从这里继续，避免重复处理",
      "example": "page_42"
    },
    {
      "key": "last_reported_metrics",
      "description": "上次报告里的关键数字快照，本轮生成对比时引用",
      "example": "{\"total_users\": 1200}"
    }
  ],

  "directory_notes": "本 Goal 每轮除了标准 artifacts 目录外，还需要一个\n    子目录 raw/ 存放原始抓取数据，避免和最终报告混在同一层",

  "completion_criteria": [
    "weekly_report.md 存在且非空",
    "报告里包含至少一张对比表格"
  ],

  "special_constraints": [
    "不要在报告里包含任何用户的真实姓名，只用匿名 ID"
  ]
}
```

字段设计原则：
- **`deliverables`**：这个 Goal 每一轮/每一次期望产出什么文件、格式、
  命名——比 `output_workspace.py` 已有的"事后从工具调用里提取
  artifacts"更进一步，是**事前**告诉 agent 该往哪个方向产出，两者
  互补（一个是"应该做什么"，一个是"做完了记录做了什么"）。
- **`handoff_fields`**：`output_workspace.py` 的 `manifest.json` 里
  `progress_note` 是自由文本、`artifacts` 是通用文件清单，都不是
  "这个 Goal 特有的、需要显式记住的结构化状态"。`handoff_fields` 明确
  列出这个 Goal 需要跨轮记住的具体信息是什么，生成 prompt 时会要求
  agent 在 `progress_note` 里按这几个 key 显式回答（而不是完全自由
  发挥，导致下一轮 agent 读到一段东拉西扯的文字却找不到关键数字）。
- **`directory_notes`**：对通用目录规范的**追加**说明（不是替代）——
  多数 Goal 用默认的 `cycle_%04d/` 平铺结构就够，只有少数需要子目录
  组织的才需要这个字段，允许留空。
- **`completion_criteria`**：与 `GoalSpecBuilder.acceptance_criteria`
  同源的概念，复用同一套"怎么判断这轮/这个 Goal 算完成"的思路，但
  这里对象是 `GoalBacklog` 的 Goal 而不是 goal_mode 的单次会话——这也
  是用户说"现在只有验收标准生成，缺具体化"时，"验收标准"能直接对应
  上的字段；本方案把它一并纳入 `GoalExecutionSpec`，不需要用户在两套
  系统里分别生成两份类似的东西。
- **`confirmed`**：草稿状态；只有 `confirmed=true` 之后才会被
  `goal_cron_bridge`/`GoalBacklog.add_objectives_for_goal()` 实际读取
  注入 prompt——**未确认的草稿不生效**，这是"用户确认"要求的硬约束，
  不是可选项。

非目标：
- 不做规范的强制校验/硬拦截（跟 `output_path_policy.md` 一贯的
  "prompt 层引导，不做 hook 拦截"立场一致，`deliverables`/
  `completion_criteria` 都只是注入 prompt 的引导信息，不会在 agent
  没产出对应文件时自动判失败）。
- 不做规范的多版本历史 UI（只保留"当前生效版本"，重新生成会覆盖草稿，
  确认后如果又想改，走"重新生成"再确认一次，不维护完整版本树——与
  `GoalSpec` 目前只保留"当前版本"的取舍一致）。
- 不强制每个 Goal 都必须有规范——一次性、简单的 Goal（比如"研究一下
  某个技术方案"这种不涉及周期性执行、产出也很随意的）可以跳过整个
  流程，直接用 `output_workspace.py` 的默认通用行为，规范生成是**可选
  增强**，不是新的必经关卡。

## 2. 生成器：`GoalExecutionSpecBuilder`

镜像 `goal_mode/spec.py::GoalSpecBuilder` 的架构，放在
`perception/goal_execution_spec.py`（新模块，与 `goal_backlog.py` 同层，
避免把 `goal_backlog.py` 塞得更臃肿）：

- **输入**：Goal 的 `title` + `description`（+ 如果是"设为周期性"流程，
  额外带上用户填的"调度"和"每轮任务内容"）、以及项目里已有的相关信息
  （复用 `goal_mode` 的 `spec_builder_mode="auto"` 思路：如果目标看起来
  涉及项目内部结构，允许起一个只读受限 Agent 先看一眼项目再生成，
  否则直接裸 LLM 一次调用——同一套 `"llm"/"agent"/"auto"` 三态设计，
  配置项复用同名命名风格：`GoalExecutionSpecConfig.builder_mode`）。
- **生成 prompt 要求 LLM"尽量详尽地想清楚各种细节"**（对应用户原话）：
  - 这个 Goal 反复执行时，每一轮大概率会产出什么？格式/命名有没有
    值得固定下来的约定？
  - 除了"做了什么"，有没有需要显式记住、传给下一轮的具体信息（累计
    进度、上次的关键数字、需要去重的标识符列表等）？
  - 有没有需要额外的子目录组织产出（原始数据 vs 最终报告分开放）？
  - 用什么标准判断"这一轮/这个 Goal 算是做到位了"？
  - 有没有过程中要注意的特殊约束（隐私、不要覆盖某些文件等）？
  LLM 输出结构化 JSON（同 §1 schema），字段允许为空数组/空字符串——
  "想清楚之后发现不需要特殊规范"本身也是一种合法结果，不强行凑内容。
- **协商流程**：与 `GoalSpecBuilder` 完全对称——
  1. `build_draft(goal_title, goal_description, ...)` 生成第 1 版。
  2. 用户可以提反馈（自然语言，比如"deliverables 里再加一个 CSV 导出"），
     `revise(prior_spec, feedback)` 基于"上一版 + 反馈"重新生成，
     `version += 1`。
  3. `confirm(spec)` → `confirmed=True`，冻结。
  4. 整个协商过程是独立的一次性 LLM 调用序列，不占用/污染主 Agent 的
     对话历史（与 `GoalSpecBuilder` 的"独立会话态"要求一致）。
- **失败兜底**：LLM 调用失败/解析失败时，返回一个"全部字段为空/使用
  默认值"的最小草稿（`deliverables=[]`、`handoff_fields=[]`，等价于
  "沿用 `output_workspace.py` 通用行为"），并在草稿里附一条
  `generation_error` 说明，用户看到的是"生成失败，你可以手动填写或
  跳过"，不是整个流程卡死——与 `GoalSpecBuilder` 遇到解析失败时的
  `_fallback_criteria()` 兜底策略一致。

## 3. 存储

新增 `.agent/goal_execution_specs/<goal_id>.json`（独立文件，不塞进
`goals.json` 的 `GoalNode`）：

- 理由：规范草稿在协商阶段可能被多次覆盖重写（用户反复提反馈），如果
  直接存进 `GoalNode` 字段，`GoalBacklog` 的锁/落盘逻辑会为一个"还没
  定稿"的草稿反复触发整份 `goals.json` 重写，没必要；且规范内容体量
  可能比 `GoalNode` 其余字段大（多条 `deliverables`/`handoff_fields`
  说明文字），拆开存跟 `CronJobWorkspace` 把 job 的配置单独存
  （而不是塞进 `goals.json`）是同一取舍。
- `GoalNode` 只新增一个轻量指针字段 `execution_spec_confirmed: bool =
  False`（默认 `False`，只有 `.agent/goal_execution_specs/<goal_id>.json`
  存在且 `confirmed=true` 时才置 `True`），供 `goal_cron_bridge`/看板
  快速判断"这个 Goal 有没有确认过的规范"而不用每次读一遍独立文件；
  真正的规范内容仍以独立文件为准，这个布尔字段只是缓存/索引，不是
  真值来源。
- 读写走新模块 `perception/goal_execution_spec.py` 里的
  `load_spec(paths, goal_id)`/`save_spec(paths, goal_id, spec)`，跟
  `output_workspace.py` 的模块划分风格一致（一个模块管一类边车数据）。

## 4. 消费方：怎么真正影响执行

- `goal_cron_bridge._fire_goal_cycle()` 现有的
  `_append_output_workspace_context()`（拼"本轮产出目录 + 上一轮
  manifest 摘要"进子 Objective description）之后，新增一步：如果
  `goal.execution_spec_confirmed`，追加读取规范文件，把 `deliverables`/
  `directory_notes`/`completion_criteria`/`special_constraints` 格式化
  成一段"本 Goal 的执行规范"文字一并拼进 description；`handoff_fields`
  格式化成"请在完成后按以下字段回答"的提示，配合现有的
  `progress_note` 落盘位置（agent 产出的回答仍然落进
  `manifest.json.progress_note`，只是这次有了明确的填空模板，而不是
  自由发挥）。
- `GoalBacklog.add_objectives_for_goal()` 对称处理一次性 Goal 的多个
  子 Objective（复用同一套格式化工具函数，与 output_workspace 规范
  §7 的对称设计保持一致）。
- 未确认（`execution_spec_confirmed=False`，包括"从未生成过"和"生成了
  但用户没确认"两种情况）时，两处消费方完全不读规范文件，行为与本
  方案引入前一致——**不确认就不生效**，不会有"用户以为规范还是草稿，
  结果已经在偷偷影响执行"的意外。

## 5. 触发入口

### 5.1 看板"设为周期性"（主要场景，用户明确提到）

`apps/mini_agent_kanban/app.py` 的"⏰ 周期性设置" expander，"设为
周期性"表单增加一步，不改变现有两个输入框（调度 + 每轮任务内容）：

- 表单提交后，不直接调用 `client.recur_goal(...)`，而是先调用一个新的
  `client.generate_goal_execution_spec(goal_id, schedule, task)` 触发
  草稿生成，把草稿渲染成一个新的确认区块（`st.session_state` 暂存草稿，
  不落盘 `confirmed`）：
  - 分节展示 `deliverables`/`handoff_fields`/`directory_notes`/
    `completion_criteria`/`special_constraints`，每节可编辑（文本框，
    不做复杂的表格增删 UI——列表字段用一行一条的多行文本框，提交时
    按行拆分，跟"⚙️ 配置"tab 里 `excluded_topics` 的编辑方式一致）。
  - 底部两个按钮：「✅ 确认并设为周期性」（保存规范
    `confirmed=true`，紧接着调用 `client.recur_goal(...)` 完成绑定）、
    「🔄 补充意见重新生成」（文本框输入反馈，调用 `revise()` 刷新草稿，
    停留在确认区块，不绑定周期性）。
  - 「跳过，不生成规范」链接/按钮：直接走原有的 `recur_goal(...)`，
    不生成规范——尊重"这是可选增强"的非目标声明，避免用户觉得被强制
    多走一步。
- 已经绑定周期性、但从未生成过规范的既有 Goal，"⏰ 周期性设置"里已绑定
  分支追加一个「📋 生成执行规范」按钮，走同一套草稿确认流程，确认后
  下一轮触发即生效——不需要先解绑再重新绑定。

### 5.2 看板"新建目标"

`render_kanban_tab()` 的"➕ 新建目标"表单，创建成功后的提示区（`st.toast`
之后）追加一个不打断当前流程的建议：如果用户接下来想设为周期性，
引导去"⏰ 周期性设置"——**不在新建目标这一步强推规范生成**，因为
多数临时创建的 Goal 是一次性、随手记的，值不值得投入一次 LLM 调用去
想细节，应该由用户在"确定要周期性执行"这个更明确的信号出现时再决定，
与 §1 非目标"规范生成是可选增强，不是必经关卡"一致。

### 5.3 CLI

新增 `/agent goals spec generate <goal_id>` / `/agent goals spec
confirm <goal_id>` / `/agent goals spec show <goal_id>`，与看板走同一套
`GoalExecutionSpecBuilder`/存储模块，行为对称；`/agent goals recur`
命令本身不强制依赖规范存在（跟看板"跳过"选项一致），只是在没有已确认
规范时打一行提示"该 Goal 还没有执行规范，可以先 `/agent goals spec
generate` 想清楚细节，或直接继续"。

## 6. 配置项（新增 `GoalExecutionSpecConfig`，独立配置块）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 总开关；关闭后看板/CLI 的规范生成入口隐藏，`goal_cron_bridge` 也不读取任何已确认规范（等价于回退到方案实施前行为） |
| `builder_mode` | `"auto"` | `"llm"`/`"agent"`/`"auto"`，与 `goal_mode.spec_builder_mode` 同名同义 |
| `prompt_on_recur` | `true` | 看板"设为周期性"表单是否默认展示"生成规范"步骤（而不是默认走"跳过"）；关闭后表单直接绑定，用户需要主动点"生成规范"按钮 |

## 7. 与既有设计哲学的一致性检查

- **确认优先于生效**：完全对齐 `GoalSpecBuilder` 已经验证过的
  "草稿→确认→冻结"模式，未确认不生效，用户随时可以选择不生成/不确认。
- **复用而非重造**：目录分配、manifest 落盘、prompt 占位符注入全部
  复用 `output_workspace.py` 现成的管道，本方案只新增"规范从哪来、
  谁确认、确认后往 description 里多拼一段什么内容"这一层。
- **可选增强，不是新增强制关卡**：一次性简单 Goal 可以完全不碰这套
  机制，跟 `report_active_search_enabled` 等一系列"默认关闭/可跳过的
  增强能力"是同一取舍风格。
- **容错优先于完整**：生成失败/解析失败都有兜底（空草稿 + 错误提示），
  不会卡住 Goal 创建或周期性绑定的主流程。

## 8. 待评审的开放问题

1. `handoff_fields` 要不要做**弱校验**——agent 在 `progress_note` 里
   没有按规范要求的字段回答时，要不要在 manifest 里标一个提示（不
   阻断，只提示"这轮没看到 `last_processed_cursor`"）？倾向于本方案
   第一版先不做（比照 output_workspace 规范里"不做路径存在性校验"的
   既有取舍），留作后续可选 Track。

   第一版先不做，留作后续可选。
2. 规范要不要支持"从模板起步"（比如"周报类""数据抓取类"预置几套
   `deliverables`/`handoff_fields` 模板，LLM 在此基础上微调而不是
   完全从零生成）？本方案倾向于第一版不做模板库，先看纯 LLM 生成的
   实际效果，模板库作为后续优化空间。

   第一版需要有模板库，请构建一些常见类型的模板。LLM可以完全从零开始 也可以从模板微调生成。
3. 一次性、非 recurring 但拆了多个子 Objective 的 Goal（output_workspace
   规范 §7 覆盖的场景）要不要也在"➕ 新建目标"里给一个"生成执行规范"
   的可选入口，而不是只在"设为周期性"里？倾向于**支持**（跟 §7 的
   "一次性 Goal 也需要跨子任务传递信息"结论一致），但入口摆在哪里
   （新建表单里加一个复选框 vs 事后在 Goal 详情里补）需要进一步确认，
   本方案先只把"设为周期性"这一条主路径讲清楚。
   
   支持。新建表单里加一个复选框
