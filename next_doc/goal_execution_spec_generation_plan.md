# Goal 执行规范自动生成 + 用户确认机制

- **状态**：Stage 1（核心数据模型/存储/生成器/模板库/配置 + 两处消费方
  注入 + §5.1 轻量核对 + CLI 命令）+ Stage 2（`overall_completion_
  criteria` 驱动的一次性 Goal 整体关闭判断）+ Stage 3（看板 UI 最小可用
  版本：§6.1/§6.2/§6.3 的生成/反馈迭代/字段级锁定/确认/手动整体关闭重判
  已接入看板）+ Stage 4（§7 末段模板自动匹配：关键词规则命中模板后在
  看板下拉框默认预选）+ Stage 5（看板"从执行历史反推"开关，仅在已绑定
  过至少一轮的场景展示）+ Stage 6（看板"📄 从模板重新起草"独立按钮，
  未确认草稿一步换模板重生成，不用先放弃草稿）+ Stage 7
  （`builder_mode="agent"` 只读探索路径：镜像
  `GoalSpecBuilder._run_builder_agent()`，`mode="auto"` 用关键词规则判断
  是否需要项目上下文）+ Stage 8（CLI `spec generate --mode` 与看板"生成
  路径"下拉框，单次覆盖配置默认的 `builder_mode`，不改配置文件；REST
  `generate`/`revise` 响应体新增 `effective_path`，看板展示"上次生成走的
  路径"）已实施。看板侧的"直接编辑字段文本框"“差异高亮”、
  `evaluate_overall_completion()` 挂只读工具核查产出内容仍未实施，见
  `next_doc/goal_execution_spec_generation_implementation_record.md`
  的"未实施"清单。
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

## 1. 先想清楚："可行"的执行规范要满足什么

在定 schema 之前先回答一个更基础的问题：一份规范生成出来，凭什么说它
"可行"？本方案认为要同时满足三条，三者缺一不可：

1. **具体**——不是空话套话，字段里填的是"这个 Goal 特有"的信息，而
   不是随便哪个 Goal 套上去都成立的通用描述。
2. **可核查**——规范里的每一条，agent 或人事后要能判断"做到了没有"，
   而不是只能靠模糊的主观感受去猜。纯自然语言描述天然只能达到"具体"，
   达不到"可核查"，两者要在字段设计上分开考虑。
3. **可持续**——周期性 Goal 会跑很多轮，规范定完不代表一劳永逸；跑
   了几轮之后如果现实情况和规范对不上，要有办法让用户知道，而不是
   规范和实际执行两条线各走各的，用户毫无察觉。

现有的"生成草案 → 用户确认/反馈" 交互（见 §5）主要解决①，下面 schema
设计和 §4 消费逻辑要一起把②③补上，不能只停在"生成得足够详细"这一层。

## 2. 核心概念：`GoalExecutionSpec`（Goal 执行规范）

一份**每个 Goal 一份、可覆盖/可留空回退默认**的结构化规范，字段设计
参考用户提出的三类细节 + 复用现有验收标准的位置：

```json
{
  "version": 1,
  "goal_id": "goal_abcd1234",
  "generated_at": 1754567890.0,
  "confirmed": false,
  "confirmed_at": null,
  "locked_fields": ["deliverables"],

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

  "sub_directories": [
    {
      "name": "raw/",
      "purpose": "存放原始抓取数据，避免和最终报告混在同一层"
    }
  ],

  "per_cycle_criteria": [
    {
      "text": "weekly_report.md 存在且非空",
      "verification_method": "file_check"
    },
    {
      "text": "报告里包含至少一张对比表格",
      "verification_method": "manual_review"
    }
  ],

  "overall_completion_criteria": [],

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
  列出这个 Goal 需要跨轮记住的具体信息是什么；为了避免"结构化地要求，
  非结构化地传递"（agent 把 `last_processed_cursor` 写成"处理到第42页
  了"这种自然语言，下一轮又要靠 LLM 摘要去猜），生成 prompt 时要求
  agent 在 `progress_note` 里用一个固定的 ` ```handoff\n{...}\n``` `
  JSON 代码块按 key 回答，下一轮消费方按 key 精确取值，而不是把整段
  `progress_note` 摘要塞给下一轮 LLM 去猜。
- **`sub_directories`**：对通用目录规范的**追加**说明（不是替代）——
  多数 Goal 用默认的 `cycle_%04d/` 平铺结构就够，只有少数需要子目录
  组织的才需要这个字段，允许留空数组。相比一段自由文本，结构化成
  `{name, purpose}` 列表是为了和 `deliverables`/`handoff_fields` 的
  编辑体验保持一致（看板里同样按"一行一条"渲染/编辑，见 §5.1），也
  方便以后有需要时做程序化处理（比如自动建目录），不用解析自由文本。
- **`per_cycle_criteria` / `overall_completion_criteria`**：原方案只有
  一个 `completion_criteria`，会把两种不同语境的"完成"标准混在一起——
  周期性 Goal 本质上不存在"整体完成"（一直巡检下去），"完成"指的是
  "这一轮算做到位了"；只有一次性、拆了多个子 Objective 的 Goal（见
  §8 第 3 条）才存在"整体完成"这个状态。拆成两个字段后：
  - `per_cycle_criteria`：周期性 Goal 主要用这个，`goal_cron_bridge`
    每轮拼 prompt 时读取；
  - `overall_completion_criteria`：默认留空数组，只有一次性多子任务
    场景才由用户/LLM 填写，`GoalBacklog` 判断"整个 Goal 能否关闭"时
    读取。
  两个字段都与 `GoalSpecBuilder.acceptance_criteria` 同源（"验收标准"
  这个概念用户已经在用），本方案把它按语境拆开纳入 `GoalExecutionSpec`，
  不需要用户在两套系统里分别生成两份类似的东西。
  每条标准额外挂一个可选的 `verification_method`
  （`run_command`/`file_check`/`manual_review`，与 `GoalSpec` 完全复用
  同一套枚举，不新造概念）——生成 prompt 会明确要求 LLM **优先往
  `file_check`/`run_command` 方向收敛**（例如把"报告要写得详细"这种
  纯主观表述，尽量改写成"报告文件里必须出现'环比'或'同比'字样"这种
  可核查的表述），做不到才退回 `manual_review`。这是"规范尽量具体"
  在生成阶段真正可执行的抓手：不是要求 LLM"写得更细"，而是要求它
  "写得更可判断"。只有 `file_check`/`run_command` 的标准会被 §4 的
  轻量核对机制使用，`manual_review` 的标准仍然只作为 prompt 引导。
- **`locked_fields`**：记录用户在上一轮反馈时已经明确认可、不希望被
  下一次 `revise()` 改动的顶层字段名，见 §5.2 交互设计。
- **`confirmed`**：草稿状态；只有 `confirmed=true` 之后才会被
  `goal_cron_bridge`/`GoalBacklog.add_objectives_for_goal()` 实际读取
  注入 prompt——**未确认的草稿不生效**，这是"用户确认"要求的硬约束，
  不是可选项。

非目标：
- 不做规范的强制校验/硬拦截（跟 `output_path_policy.md` 一贯的
  "prompt 层引导，不做 hook 拦截"立场一致，`deliverables`/
  `per_cycle_criteria` 都只是注入 prompt 的引导信息，不会在 agent
  没产出对应文件时自动判失败——§4 新增的轻量核对机制同样只提示不拦截，
  见下）。
- 不做规范的多版本历史 UI（只保留"当前生效版本"，重新生成会覆盖草稿，
  确认后如果又想改，走"重新生成"再确认一次，不维护完整版本树——与
  `GoalSpec` 目前只保留"当前版本"的取舍一致）。
- 不强制每个 Goal 都必须有规范——一次性、简单的 Goal（比如"研究一下
  某个技术方案"这种不涉及周期性执行、产出也很随意的）可以跳过整个
  流程，直接用 `output_workspace.py` 的默认通用行为，规范生成是**可选
  增强**，不是新的必经关卡。

## 3. 生成器：`GoalExecutionSpecBuilder`

镜像 `goal_mode/spec.py::GoalSpecBuilder` 的架构，放在
`perception/goal_execution_spec.py`（新模块，与 `goal_backlog.py` 同层，
避免把 `goal_backlog.py` 塞得更臃肿）：

- **输入源**：接口设计上明确支持三种起草方式，第一版可以只实现其中
  一部分，但输入结构不要只留"title+description"这一条路，避免以后
  补别的输入源时要改接口：
  1. **从零生成**：Goal 的 `title` + `description`（+ 如果是"设为
     周期性"流程，额外带上用户填的"调度"和"每轮任务内容"），以及
     项目里已有的相关信息（复用 `goal_mode` 的 `spec_builder_mode=
     "auto"` 思路：如果目标看起来涉及项目内部结构，允许起一个只读
     受限 Agent 先看一眼项目再生成，否则直接裸 LLM 一次调用——同一套
     `"llm"/"agent"/"auto"` 三态设计，配置项复用同名命名风格：
     `GoalExecutionSpecConfig.builder_mode`）。
  2. **从模板起步**（第一版就做，见 §7 模板库）：预置模板提供
     `deliverables`/`handoff_fields`/`per_cycle_criteria` 的骨架，
     LLM 基于目标对模板做微调而不是空白生成。模板同时起到"教会 LLM
     什么叫具体、什么叫可核查"的 few-shot 作用，比纯从零生成更容易
     稳定地达到 §1 说的"可核查"水准。用户可以选"从模板起步"也可以
     选"完全从零生成"，两条路径共用同一个 `build_draft()` 入口，只是
     `template_id` 参数是否为空的区别。
  3. **从执行历史反推/校正**：对"已绑定周期性但从未生成过规范"的
     既有 Goal（见 §6.1），生成器允许额外读取该 Goal 过去若干轮的
     `manifest.json`/`progress_note` 实际产出，用真实跑出来的东西
     校验规范是否可行，而不是纯粹凭 `description` 空想——这类 Goal
     往往比"刚创建、从没跑过"的 Goal 更容易生成出贴近实际的规范。
- **生成 prompt 要求 LLM"尽量详尽地想清楚各种细节，并尽量往可核查的
  方向收敛"**（对应用户原话 + §1 的"可核查"要求）：
  - 这个 Goal 反复执行时，每一轮大概率会产出什么？格式/命名有没有
    值得固定下来的约定？
  - 除了"做了什么"，有没有需要显式记住、传给下一轮的具体信息（累计
    进度、上次的关键数字、需要去重的标识符列表等）？
  - 有没有需要额外的子目录组织产出（原始数据 vs 最终报告分开放）？
  - 用什么标准判断"这一轮算是做到位了"？这些标准里，有多少能落到
    "文件是否存在""是否能跑一条命令验证"这种可核查的方式，而不是
    只能"读一遍再判断"？（对应每条标准的 `verification_method`）
  - 是否存在"整个 Goal 彻底完成、可以关闭"这个状态？（多数周期性
    Goal 应该回答"不适用"，`overall_completion_criteria` 留空）
  - 有没有过程中要注意的特殊约束（隐私、不要覆盖某些文件等）？
  LLM 输出结构化 JSON（同 §2 schema），字段允许为空数组/空字符串——
  "想清楚之后发现不需要特殊规范"本身也是一种合法结果，不强行凑内容。
- **协商流程**：与 `GoalSpecBuilder` 大体对称，但反馈粒度细化到字段
  级，而不是整份重来（见 §5.2 的问题分析）：
  1. `build_draft(goal_title, goal_description, template_id=None,
     history_manifests=None, ...)` 生成第 1 版。
  2. 用户可以对某几个字段勾选"这部分已经满意，先锁定"，也可以提自然
     语言反馈（比如"deliverables 里再加一个 CSV 导出"）；
     `revise(prior_spec, feedback, locked_fields)` 基于"上一版 + 反馈"
     重新生成，prompt 里明确列出 `locked_fields` 要求 LLM 原样保留，
     只调整未锁定的部分，`version += 1`。
  3. `confirm(spec)` → `confirmed=True`，冻结。
  4. 整个协商过程是独立的一次性 LLM 调用序列，不占用/污染主 Agent 的
     对话历史（与 `GoalSpecBuilder` 的"独立会话态"要求一致）。
- **失败兜底**：LLM 调用失败/解析失败时，返回一个"全部字段为空/使用
  默认值"的最小草稿（`deliverables=[]`、`handoff_fields=[]`，等价于
  "沿用 `output_workspace.py` 通用行为"），并在草稿里附一条
  `generation_error` 说明，用户看到的是"生成失败，你可以手动填写或
  跳过"，不是整个流程卡死——与 `GoalSpecBuilder` 遇到解析失败时的
  `_fallback_criteria()` 兜底策略一致。

## 4. 存储

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

## 5. 消费方：怎么真正影响执行

- `goal_cron_bridge._fire_goal_cycle()` 现有的
  `_append_output_workspace_context()`（拼"本轮产出目录 + 上一轮
  manifest 摘要"进子 Objective description）之后，新增一步：如果
  `goal.execution_spec_confirmed`，追加读取规范文件，把 `deliverables`/
  `sub_directories`/`per_cycle_criteria`/`special_constraints` 格式化
  成一段"本 Goal 的执行规范"文字一并拼进 description；`handoff_fields`
  格式化成"请在完成后用 ` ```handoff\n{...}\n``` ` 代码块按以下字段
  回答"的提示，配合现有的 `progress_note` 落盘位置（agent 产出的回答
  仍然落进 `manifest.json.progress_note`，只是这次有了明确的填空模板
  和固定的 JSON 格式约定，而不是自由发挥、下一轮又要靠摘要去猜）。
- `GoalBacklog.add_objectives_for_goal()` 对称处理一次性 Goal 的多个
  子 Objective（复用同一套格式化工具函数，与 output_workspace 规范
  §7 的对称设计保持一致），并在最后一个子 Objective 完成时读取
  `overall_completion_criteria` 判断 Goal 是否可以整体关闭。
- 未确认（`execution_spec_confirmed=False`，包括"从未生成过"和"生成了
  但用户没确认"两种情况）时，两处消费方完全不读规范文件，行为与本
  方案引入前一致——**不确认就不生效**，不会有"用户以为规范还是草稿，
  结果已经在偷偷影响执行"的意外。

### 5.1 轻量核对：让"规范不可行"能被用户看到

方案的非目标明确"不做强制校验"，这个大方向不变——但完全没有任何
反馈信号，会导致规范定完之后没人知道它有没有被真正执行，变成沉没
成本。这里补一个成本接近零、不涉及语义判断、不阻断任何流程的轻量
核对，作为 §8 第 1 条"弱校验"的最小可行版本，第一版就做：

- 每轮 `_append_output_workspace_context()` 处理完这一轮的
  `manifest.json` 后，顺手做两次**纯文件名/key 字符串匹配**（不做
  任何语义判断，成本可以忽略）：
  - `deliverables` 里 `required_every_cycle=true` 的条目，检查
    `naming_pattern` 是否出现在这一轮 `manifest.json.artifacts` 的
    文件名里；
  - `handoff_fields` 里的 key，检查是否出现在这一轮 `progress_note`
    的 ` ```handoff ``` ` JSON 块里。
- 匹配不上：**不判失败、不阻断**，只做两件事——
  1. 下一轮 prompt 里追加一句"上一轮规范要求的 `xxx` 未见产出，请
     注意"（软性提示，闭环仍然在 prompt 层，不违背既有"引导不拦截"
     哲学）；
  2. 连续 N 轮（默认 3，见 §6 配置）都没匹配上，在 `GoalNode` 上
     追加一条系统备注（或看板里给一个"⚠️ 建议复查执行规范"的提示），
     把"这份规范可能不再可行"从"用户偶然发现"变成"系统主动提示"。
- 只有挂了 `verification_method="file_check"` 的 `deliverables`/
  `per_cycle_criteria` 条目、以及全部 `handoff_fields`，会被这个机制
  处理；`manual_review` 的标准不参与核对，仍然只作为 prompt 引导——
  这是这个机制能保持"零语义判断成本"的前提，不打算做成通用的规则
  引擎。

## 6. 触发入口

### 6.1 看板"设为周期性"（主要场景，用户明确提到）

`apps/mini_agent_kanban/app.py` 的"⏰ 周期性设置" expander，"设为
周期性"表单增加一步，不改变现有两个输入框（调度 + 每轮任务内容）：

- 表单提交后，不直接调用 `client.recur_goal(...)`，而是先调用一个新的
  `client.generate_goal_execution_spec(goal_id, schedule, task)` 触发
  草稿生成，把草稿渲染成一个新的确认区块（`st.session_state` 暂存草稿，
  不落盘 `confirmed`）：
  - 分节展示 `deliverables`/`handoff_fields`/`sub_directories`/
    `per_cycle_criteria`/`special_constraints`，每节可编辑（文本框，
    不做复杂的表格增删 UI——列表字段用一行一条的多行文本框，提交时
    按行拆分，跟"⚙️ 配置"tab 里 `excluded_topics` 的编辑方式一致），
    每节旁边带一个「🔒 这部分不用改了」勾选框，对应写入
    `locked_fields`（见 §6.2 交互细化）。
  - 底部三个按钮：「✅ 确认并设为周期性」（保存规范
    `confirmed=true`，紧接着调用 `client.recur_goal(...)` 完成绑定）、
    「🔄 补充意见重新生成」（文本框输入反馈，带上已勾选的
    `locked_fields`，调用 `revise()` 刷新草稿，停留在确认区块，不绑定
    周期性）、「📄 从模板重新起草」（下拉选一个 §7 模板库里的模板，
    重新走一次 `build_draft(template_id=...)`，用于用户发现"从零生成
    的方向不太对，想换个模板起步"的情况）。
  - 「跳过，不生成规范」链接/按钮：直接走原有的 `recur_goal(...)`，
    不生成规范——尊重"这是可选增强"的非目标声明，避免用户觉得被强制
    多走一步。
- `revise()` 之后的草稿展示做**差异高亮**：新增条目、删除条目、被
  改写的条目分别标出来（前端对比新旧 JSON 即可，不需要额外 LLM
  调用），而不是让用户重新通读整份规范去猜"这次改了什么"——通读成本
  过高会让用户倾向于"差不多得了，直接确认"，反而违背"迭代到确认可行
  为止"的初衷。
- 已经绑定周期性、但从未生成过规范的既有 Goal，"⏰ 周期性设置"里已绑定
  分支追加一个「📋 生成执行规范」按钮，走同一套草稿确认流程；这类
  Goal 如果已经跑过若干轮，草稿生成时默认带上"从执行历史反推"（见
  §3 输入源 3），比"从零生成"更容易贴近实际情况。确认后下一轮触发
  即生效——不需要先解绑再重新绑定。

### 6.2 交互细化：字段级锁定，而不是整体重来

原方案 `revise(prior_spec, feedback)` 是拿"上一版 + 反馈"整份重新
生成，实际使用中有两个问题：① LLM 有一定概率把用户没提意见、已经
满意的字段也顺带改动，用户需要重新通读全部 5 个 section 才能确认
"没有意外改动"；② 多轮反馈下每次都要为已满意的部分重新付出一次审阅
成本，容易导致用户提前放弃迭代。

处理方式：`GoalExecutionSpec.locked_fields` 记录用户已勾选"不用改了"
的顶层字段名，`revise()` 的 prompt 里明确列出这些字段要求 LLM
原样保留、只字符串复制不重新生成，只根据反馈调整未锁定的字段。看板
UI 上每个 section 旁边给一个「🔒」勾选框，而不是只有"整体确认"和
"整体重新生成"两个粒度——**这是"生成尽量具体、反馈迭代直到确认可行"
这条交互主线里最容易被忽视但影响体验最大的一环**：反馈成本越低，
用户才越有耐心把规范磨到真正可行，而不是为了省事随便点确认。

### 6.3 看板"新建目标"

`render_kanban_tab()` 的"➕ 新建目标"表单，创建成功后的提示区（`st.toast`
之后）追加一个不打断当前流程的建议：如果用户接下来想设为周期性，
引导去"⏰ 周期性设置"。同时表单本身增加一个「生成一次性 Goal 的执行
规范」复选框（默认不勾选）：勾选后创建成功走同一套草稿确认流程，
适用于"一次性但会拆多个子 Objective、需要跨子任务传递信息"的场景
（对应 §8 第 3 条，`overall_completion_criteria` 在这个场景下才有
意义）。**默认不勾选、不在新建这一步强推规范生成**，因为多数临时
创建的 Goal 是一次性、随手记的，值不值得投入一次 LLM 调用去想细节，
应该由用户主动决定，与非目标"规范生成是可选增强，不是必经关卡"一致。

### 6.4 CLI

新增 `/agent goals spec generate <goal_id> [--template <id>] [--from-history]`
/ `/agent goals spec confirm <goal_id>` / `/agent goals spec show
<goal_id>`，与看板走同一套 `GoalExecutionSpecBuilder`/存储模块，行为
对称；`/agent goals recur` 命令本身不强制依赖规范存在（跟看板"跳过"
选项一致），只是在没有已确认规范时打一行提示"该 Goal 还没有执行
规范，可以先 `/agent goals spec generate` 想清楚细节，或直接继续"。

## 7. 模板库

第一版就要有模板库，`build_draft()` 支持"完全从零生成"和"从模板
微调"两条路径，用户在 §6.1/§6.4 的入口里选择。模板存放为静态资源
（`perception/goal_execution_spec_templates/<template_id>.json`），
每个模板预置 `deliverables`/`handoff_fields`/`per_cycle_criteria`
（含 `verification_method`）的骨架，LLM 生成时把模板骨架作为 few-shot
参考对目标做微调，而不是空白发挥——模板骨架本身也起到"教会 LLM 什么
叫具体、什么叫可核查"的作用，比纯 LLM 从零生成更容易稳定地达到 §1
说的水准。

第一版覆盖几类常见 Goal 类型（可随实际使用逐步扩充）：

| 模板 | 适用场景 | 骨架要点 |
| --- | --- | --- |
| `periodic_report` | 周报/日报/定期汇总类（如 growth_advisor 报告） | `deliverables` 固定报告文件名模式；`handoff_fields` 预置 `last_reported_metrics`；`per_cycle_criteria` 预置"报告文件存在"（`file_check`） |
| `data_collection` | 定期抓取/采集类 | `sub_directories` 预置 `raw/` 存放原始数据；`handoff_fields` 预置 `last_processed_cursor`/`seen_ids`（去重用） |
| `monitoring_patrol` | 巡检/监控类（检查某些状态是否异常） | `per_cycle_criteria` 预置"异常时是否有明确记录"；`handoff_fields` 预置 `last_known_state` |
| `codebase_maintenance` | 代码维护/清理类（如批量修复、依赖升级） | `per_cycle_criteria` 预置"是否运行了相关测试"（`run_command`）；`special_constraints` 预置"不要修改用户明确排除的目录"占位 |
| `research_exploration` | 调研/学习类，产出较随意 | 骨架最简，`deliverables` 只预置一条"调研笔记"，`per_cycle_criteria` 默认 `manual_review`——提醒用户这类 Goal 未必需要很重的规范，是否需要更多字段留给用户反馈决定 |

模板选择方式：用户在触发入口（§6.1/§6.3/§6.4）里可选"自动匹配"（关键
词规则粗略匹配 Goal 描述，命中某个模板则默认预选，允许用户改选或
选"不用模板"）或手动指定 `template_id`；`build_draft()` 内部把选中
模板的骨架拼进生成 prompt 作为参考结构，LLM 仍然可以对骨架里的具体
内容做增删改，不是机械套用。

## 8. 配置项（新增 `GoalExecutionSpecConfig`，独立配置块）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 总开关；关闭后看板/CLI 的规范生成入口隐藏，`goal_cron_bridge` 也不读取任何已确认规范（等价于回退到方案实施前行为） |
| `builder_mode` | `"auto"` | `"llm"`/`"agent"`/`"auto"`，与 `goal_mode.spec_builder_mode` 同名同义 |
| `prompt_on_recur` | `true` | 看板"设为周期性"表单是否默认展示"生成规范"步骤（而不是默认走"跳过"）；关闭后表单直接绑定，用户需要主动点"生成规范"按钮 |
| `soft_check_enabled` | `true` | §5.1 轻量核对总开关；关闭后仍然把规范拼进 prompt，但不做 `file_check`/`handoff` 的产出匹配、不会触发"建议复查规范"提示 |
| `soft_check_alert_after_cycles` | `3` | 连续多少轮 `file_check`/`handoff_fields` 匹配不上才在 `GoalNode`/看板给出"建议复查执行规范"提示 |

## 9. 与既有设计哲学的一致性检查

- **确认优先于生效**：完全对齐 `GoalSpecBuilder` 已经验证过的
  "草稿→确认→冻结"模式，未确认不生效，用户随时可以选择不生成/不确认。
- **复用而非重造**：目录分配、manifest 落盘、prompt 占位符注入全部
  复用 `output_workspace.py` 现成的管道，`verification_method` 直接
  复用 `GoalSpec` 已有的枚举，本方案只新增"规范从哪来、谁确认、确认
  后往 description 里多拼一段什么内容、执行结果要不要回过头对一下"
  这几层，不重新发明"验收/验证"这套概念。
- **可选增强，不是新增强制关卡**：一次性简单 Goal 可以完全不碰这套
  机制，跟 `report_active_search_enabled` 等一系列"默认关闭/可跳过的
  增强能力"是同一取舍风格；§5.1 的轻量核对同样只提示不拦截。
- **容错优先于完整**：生成失败/解析失败都有兜底（空草稿 + 错误提示），
  不会卡住 Goal 创建或周期性绑定的主流程。
- **反馈成本决定迭代深度**：字段级锁定 + 差异高亮（§6.2）是为了让
  "生成→反馈→确认可行"这条主线真正可持续，而不是因为反馈成本高，
  用户被迫在第一版草稿上就草草确认——这是本次修订相比初版方案新增的
  一条设计原则，后续任何交互相关的改动都应该往"降低反馈成本"方向看。

## 10. 待评审的开放问题（已决策项见各条末尾）

1. `handoff_fields`/`deliverables` 要不要做**弱校验**——agent 没有
   按规范要求产出/回答时，要不要给一个提示？

   **已决策**：做，但只做 §5.1 描述的最小可行版本——纯文件名/key
   字符串匹配，不做语义判断，不阻断，只在 prompt 里追加提示 +
   连续多轮未达标时给用户一个"建议复查规范"的提示。更复杂的语义级
   校验（比如用 LLM 判断"报告是否真的包含对比表格"）留作后续可选
   Track，第一版不做。
2. 规范要不要支持"从模板起步"？

   **已决策**：第一版要有模板库（见 §7），覆盖周报类/数据抓取类/
   巡检类/代码维护类/调研类五个常见场景。LLM 可以完全从零开始，也
   可以从模板微调生成，两条路径都保留，由用户在触发入口选择。
3. 一次性、非 recurring 但拆了多个子 Objective 的 Goal 要不要也在
   "➕ 新建目标"里给一个"生成执行规范"的可选入口？

   **已决策**：支持。新建表单里加一个复选框（见 §6.3），默认不勾选，
   避免多数随手创建的一次性 Goal 被强推这一步。
4. `revise()` 反馈粒度要不要细化到字段级？（本次修订新增的问题）

   **已决策**：做，见 §6.2。`GoalExecutionSpec` 新增 `locked_fields`
   字段，看板每个 section 旁边加锁定勾选框，`revise()` 的 prompt
   明确保留已锁定字段不变。
5. `completion_criteria` 要不要拆分"每轮完成"和"整体完成"两种语境？
   （本次修订新增的问题）

   **已决策**：拆，见 §2。`per_cycle_criteria`（周期性 Goal 主要用）
   与 `overall_completion_criteria`（仅一次性多子任务场景使用，默认
   留空）分离，避免消费方需要靠上下文猜测某条标准到底是哪个语境。
6. `handoff_fields` 的传递格式要不要固定？（本次修订新增的问题）

   **已决策**：固定为 ` ```handoff\n{...}\n``` ` JSON 代码块（见
   §2/§5），下一轮消费方按 key 精确取值，避免"结构化定义、非结构化
   传递"导致关键信息在自由文本摘要里丢失。
