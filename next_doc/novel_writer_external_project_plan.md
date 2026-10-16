# novel_writer 外部项目设计方案（小说生成 + Streamlit 操作界面）

> 状态：**已确认（2026-10-16），按 M0→M5 分期实施**。Q1–Q10 全部按推荐项确认，见第 10 节与第 11 节实施进度。
> 落地位置（确认后）：`external_projects/novel_writer/`，本文件同步放入 `next_doc/novel_writer_external_project_plan.md`。
> 依据：`skills.zip`（23 个小说创作 skill）、`external_projects/world_simulator`（参考实现）、`mini_agent` 的 `workflow` / `external_projects` 模块。

---

## 0. 一页结论

1. **23 个 skill 是一套"总控 + 规划 + 知识库 + 设计 + 执行 + 质检"的分层写作系统**，数据全部落在一个 `{WORKSPACE}` 目录里。这与"每本小说一个工作目录"的要求天然吻合，直接沿用其目录标准。
2. **执行方式**：与 world_simulator 完全同构——不自己调 LLM。每个创作类 skill 由 `mini_agent.workflow.WorkflowRunner` 的 `skill_agent` 步骤执行（动态绑定 `skill_name`，要求 agent 写文件并产出一份 JSON 回执）；LLM 配置统一走 `load_llm_cfg()`。
3. **分工原则：确定性的归代码，创造性/判断性的归 skill。** workspace-manager、novel-director、word-count 计数、memory-manager 的上下文组装由 Python 实现；其余 19 个由 LLM 执行。
4. **自动/手动靠两个正交的旋钮**：
   - **控制挡位**（手动 / 引导 / 半自动 / 全自动）决定"每一步要不要人确认"；
   - **质量档位**（草稿 / 标准 / 精品）决定"每章跑多少个可选 skill、审稿多深"。
   二者只是预设，最终落到一张可逐步骤覆盖的"步骤 × 闸门"矩阵上。
5. **好小说靠流程而不是靠一次生成**：先设计后写作、每章强制"机器校验 → 一致性核查 → 修改闭环 → 归档同步"、记忆只在通过后落盘、幕末深度审稿、关键节点人工确认。详见第 7 节。
6. **长任务靠后台 worker 子进程 + 工作目录内的状态文件**（可暂停/恢复/崩溃自愈），Streamlit 只做展示和下指令。
7. 默认**保守 opt-in**：默认手动挡、标准质量档、无自动批量调度、无预算外自动续写。

---

## 1. 调研结论

### 1.1 skills.zip 全景（23 个）

| 层 | skill | 核心职责 | 主要产出（相对 WORKSPACE） | 执行器 |
|---|---|---|---|---|
| 基础设施 | workspace-manager | 初始化标准目录、统一路径、项目恢复 | `项目状态/workspace_config.json`、整套目录 | **Python** |
| 总控 | novel-director | 阶段编排、进度视图、下一步建议、异常协调 | `项目状态/system_state.json`、`progress.json` | **Python**（状态机） |
| 决策 | novel-planner | 全书蓝图：结构模型、幕/章骨架、伏笔索引、自检 | `大纲规划/小说蓝图.json`、`章节大纲.md` | LLM |
| 决策 | plot-simulator | 沙盘推演：快进/分支树/压力测试/蝴蝶效应，含决策日志 | `simulations/…`、`线索设定/decision_log.json` | LLM |
| 知识库 | character-designer | 人设卡、关系对设计 | `人物设定/人设卡/*.md` | LLM（每主要角色一次） |
| 知识库 | character-manager | 人物档案、章后状态更新、wont_do 行为边界 | `人物设定/characters.json`、`behavior_index.json` | LLM（JSON 合并） |
| 知识库 | world-builder | 世界圣经、设定使用率、按需扩展 | `世界观设定/world_bible.json` 等 | LLM |
| 知识库 | plot-tracker | 伏笔注册/依赖、章前任务清单、章后更新、张力曲线 | `线索设定/plot_tracker.json`、`伏笔注册表.md` | 混合（章前清单可由代码过滤） |
| 枢纽 | memory-manager | 三级记忆、动态 token 预算、上下文包、摘要校验、10 章快照 | `项目状态/memory/*` | **Python 为主**（组装/预算/检索），摘要校验可选 LLM |
| 设计 | hook-designer | 前三章开篇系统、章末钩子、章首衔接 | 写入蓝图/章节备注 | LLM |
| 设计 | scene-architect | 单场景结构（戏剧性问题、5 拍小弧线） | 章节写作指导 | LLM（关键场景才跑） |
| 设计 | theme-architect | 主题设计与防说教、幕末主题检查 | 主题设计文档 | LLM |
| 设计 | pacing-architect | 跨章节奏、三章单元、幕次张力曲线 | 节奏规划 | LLM |
| 设计 | ending-designer | 结局设计、故事线收束、最后一句话打磨 | 结局方案 | LLM |
| 设计 | idea-catcher | 灵感捕捉→评估→整合/暂存 | `idea_bank.json` | LLM（按需） |
| 执行 | chapter-writer | **唯一内容生产者**，7 种 chapter_type 模板 | `正文/第nn章 标题.md` | LLM |
| 执行 | chapter-review | 完章闭环：核查→修改驱动→概述归档→状态同步 | `一致性检查/`、`章节总结/`、`项目状态/` | LLM（拆成 4 个步骤，修改循环由代码驱动） |
| 执行 | dialogue-crafter | 关键对话（潜台词/冲突/群体） | 对话草稿（供 writer 吸收） | LLM（关键场景才跑） |
| 质检 | consistency-checker | 事实/人物/世界规则/伏笔/主题五维校验、幕末深度审稿 | `一致性检查/*` | LLM |
| 质检 | style-guardian | 风格指纹、写前提示、写后检查、幕间对比、词频 | `其他设定/style_profile.json` 等 | 混合（词频统计由代码） |
| 质检 | revision-editor | 8 维主动巡检 + L1–L4 分级修订 | `修订记录/*` | LLM |
| 质检 | reader-simulator | 注意力/理解/情感/可信/节奏五维读者体验 | 读者体验报告 | LLM |
| 质检 | word-count-controller | 字数配额、偏差处理、压缩/扩写法则 | `其他设定/word_count.json` | **Python 计数/配额**，超标策略由 revision 步骤处理 |

### 1.2 skill 内部的流转关系（来自 novel-director）

```
阶段零 workspace-manager（目录初始化 / 项目恢复）
阶段一 novel-planner ─┬→ character-designer(×主要角色) → character-manager
                      ├→ world-builder
                      ├→ plot-tracker（注册伏笔、张力基准）
                      ├→ word-count-controller（配额）
                      └→ plot-simulator（快进验证，可选）  + style-guardian 初始化（模式 A/B）
阶段二 前三章：hook-designer（前三章开篇系统）→ 单章循环 → 用户确认 → style-guardian 建立指纹
阶段三 单章循环：
   写前  字数配额 → 章前情节清单 →（关键章）plot-simulator → 上下文包 → 风格提示
   写中  chapter-writer（遇重要对话 → dialogue-crafter）
   写后  chapter-review：核查 →（硬错误？≤3 轮修改）→ 概述归档 → 状态同步（→ 6 个下游模块）
        reader-simulator（每 3–5 章，可选）
   幕末  consistency-checker 深度审稿 / style-guardian 幕间对比 / plot-simulator 中层推演 / 每 10 章快照
阶段四 修订循环：revision-editor 诊断 L1–L4 → chapter-writer 执行 → 按等级触发下游更新
```

### 1.3 skills 里需要我们"补齐/规避"的地方（实施时的适配点）

| # | 发现 | 处理方式 |
|---|---|---|
| 1 | **路径写法不一致**：novel-director 3.7 与 chapter-review 用 `project/summaries/ch_n_概述.md`、`project/state/*.json`；而 workspace-manager 标准是 `章节总结/`、`项目状态/`。planner 还出现 `novel_blueprint.json` 与 `小说蓝图.json` 两个名字。idea-catcher 的 `idea_bank.json`、plot-simulator 的 `simulations/` 没有给 WORKSPACE 前缀。 | 以 workspace-manager 的标准目录为准，在**每步 prompt 里注入统一路径表**覆盖冲突写法；缺失的路径在路径表里补定义（见第 3 节）。**不改 skill 原文。** |
| 2 | skill 假设"可以向用户提问/等待确认"（询问工作目录、展示蓝图等待确认、plot-simulator 让作者选路径）。后台 agent 无法交互。 | prompt 前言约定：**不要等待用户**；需要用户决定的事项写进回执的 `needs_user`，引擎把该步置为 `waiting_user`，UI 渲染成表单，答复后带着答案重跑。 |
| 3 | chapter-review 在内部"把修改任务发回 chapter-writer，最多 3 轮"——skill 之间互相调用。 | 修改循环**由 Python 编排**（review → writer 修订 → 只复核失败项），每次都是**新起的独立 agent**，既符合"最多 3 轮"又保证审稿与写作不同上下文。 |
| 4 | chapter-review 阶段四要同步 6 个下游模块，每章若各跑一个 LLM 步骤成本偏高。 | 按质量档位分级：草稿档合并为一个"同步"步骤；标准/精品档拆成 character-manager / plot-tracker / style-guardian 各自步骤。 |
| 5 | style-guardian 明确要求**不能在前 3 章未被确认时自动建指纹**（错误基准比没有更糟）。 | 作为**不可被挡位绕过的检查点**（见 6.3）。 |
| 6 | consistency-checker 硬错误时"严禁写下一章"。 | 引擎层硬闸门：有未清零的 🔴 不会 enqueue 下一章。 |

### 1.4 world_simulator 的接入模式（本项目沿用）

- **LLM 调用**：`world_simulator/config.py::load_llm_cfg()` → `mini_agent.config.load_config()`（自动继承主项目 provider/key/重试/fallback）。本项目不写任何 provider/重试逻辑。
- **执行单元**：`workflows/*.yaml` 里的 `skill_agent` 步骤 + `WorkflowStore(root).load(name)` → 运行前把 `step.skill_name` 改写 → `WorkflowRunner(cfg).run(wf, inputs)`；产出走 `result_file` 契约（`mini_agent` 内置"写文件校验 → resume 重试 ×3 → 重开 agent ×3"）。
- **三层结构**：`tool_api.py`（唯一业务层，返回 `{ok, data|error}`）→ `service_cli.py` / `server.py` / `app.py`（只是壳）。
- **entrypoints + project.yaml**：每个调度入口都可脱离 daemon 独立运行，`tracked_run()` 写账本，缺框架时降级。
- 已有的教训（来自你的记忆）：app.py 长到 8000+ 行难维护 → 本项目 UI 拆成 `ui/` 多模块；"新机制没在真实 LLM 下验证/UI 只做静态检查" → 本项目测试策略见第 8 节。

---

## 2. 总体架构

```
┌────────────────────────────── Streamlit（app.py + ui/*）──────────────────────────────┐
│ 书架 │ 新建向导 │ [单本驾驶舱：总控台/规划/设定库/线索/写作台/审校/阅读/灵感箱/度量/设置] │ Skill库 │
└───────────────┬───────────────────────────────────────────────────────────────────────┘
                │ 只调用 tool_api（同进程读状态；"运行"类命令 → 起 worker 子进程）
┌───────────────▼───────────────┐      ┌────────────────────────────────────────────────┐
│ novel_writer/tool_api.py       │ ←──→ │ worker 子进程：entrypoints/run_novel.py          │
│  {ok,data|error}               │      │  循环：取下一可运行步骤 → 闸门判定 → 执行 → 落盘   │
└───────┬────────────────────────┘      └───────────────┬────────────────────────────────┘
        │                                               │
┌───────▼───────────────────────────────────────────────▼────────────────────────────────┐
│ engine：pipeline（阶段/章节展开）· state（步骤状态机/脏标记/心跳）· gates（挡位→闸门）     │
│        runner（构建 prompt→WorkflowRunner→校验→回执）· context_pack · validators · metrics │
└───────────────────────────────────────┬────────────────────────────────────────────────┘
                                        │
         mini_agent：WorkflowRunner(skill_agent) · load_llm_cfg · tracked_run/ledger
                                        │
                 workspaces: data/novels/<书名>_<时间戳>/   ← 一本书一个工作目录
```

### 2.1 项目目录（`external_projects/novel_writer/`）

```
project.yaml            entrypoints 与 daemon 的唯一契约
PROJECT.md              目标、限制、分阶段交付记录
app.py                  薄入口（set_page_config + 路由）
ui/                     各页面模块（shelf.py / wizard.py / cockpit/*.py / skills_page.py / components.py）
novel_writer/
  workspace.py          = workspace-manager：路径表、初始化、恢复、体检
  settings.py           挡位 / 质量档 / 步骤覆盖 / 预算（读写 _studio/settings.json）
  steps.py              步骤注册表：skill、输入、产出、校验器、默认闸门、启用的质量档、成本级别
  pipeline.py           = novel-director：阶段 DAG + 章节循环展开 + 下一步建议
  state.py              步骤状态机、输入哈希/脏标记、心跳、孤儿自愈
  gates.py              挡位/质量档 → 每步闸门；强制检查点
  runner.py             单步执行（prompt 组装、WorkflowRunner、回执、重试、用量记录）
  context_pack.py       = memory-manager 的确定性部分
  validators.py         机器校验（见 7.2）
  metrics.py            进度/张力曲线/伏笔健康/成本统计
  exporter.py           合并导出 md/txt（docx/epub 后期）
  tool_api.py / service_cli.py
  config.py             路径常量 + load_llm_cfg（与 world_simulator 同写法）
skills/                 23 个 skill 原样放入（不改动）
workflows/skill_step.yaml   通用单步工作流（skill_agent，运行时改写 skill_name）
entrypoints/            create_novel / run_novel / advance_all_auto / list_novels / health
docs/  tests/  data/(gitignore)
```

> 为什么只用**一个**通用 workflow：23 个 skill 若各写一个 yaml 会产生大量重复；world_simulator 也是"一个 yaml + 运行时改写 skill_name"。

---

## 3. 工作目录规范（每本小说一个目录，所有内容都在里面）

默认位置：`external_projects/novel_writer/data/novels/<书名>_<yyyymmddHHMM>/`；新建时允许指定任意绝对路径（书架索引记录其位置）。

```
<WORKSPACE>/
├─ 大纲规划/         小说蓝图.json 章节大纲.md │ 人物设定/{characters.json,behavior_index.json,人设卡/,snapshots/}
│                    世界观设定/{world_bible.json,world_changelog.md,地点详情/} │ 线索设定/{plot_tracker.json,伏笔注册表.md,故事线状态.md,tension_curve.md,decision_log.json}
│                    其他设定/{style_profile.json,word_count.json,idea_bank.json,…}   ← idea_bank 在此补定义
├─ 正文/             第nn章 标题.md（含 _drafts/ 存放对话草稿、候选稿）
├─ 一致性检查/       第nn章_检查报告.md │ 幕次健康报告/
├─ 修订记录/         第nn章_修订记录.md │ 蓝图变更日志.md
├─ 章节总结/         第nn章_总结.md │ 全书章节索引.md
├─ 项目状态/         progress.json 当前状态概览.md workspace_config.json │ memory/{ch_n.json,snapshots/,search_index.json,context_ch{n}.md}
├─ 推演记录/         plot-simulator 的 simulations/ 在此补定义
├─ 导出/             合并稿
└─ _studio/          ← 本项目自己的系统文件（同样在工作目录内）
   ├─ manifest.json        书名/创建时间/模板/schema_version
   ├─ settings.json        挡位、质量档、步骤覆盖矩阵、预算、模型覆盖
   ├─ pipeline_state.json  每个步骤的状态、输入哈希、尝试次数、耗时
   ├─ control.json         UI→worker 指令（pause/stop/run_until）
   ├─ worker.json          worker 心跳/pid（崩溃自愈依据）
   ├─ inbox/               待确认项（检查点、needs_user 表单、升级人工）
   ├─ answers/             用户对 needs_user 的答复
   ├─ author_notes/        给指定章节/步骤的作者指导（"下一章请…"）
   ├─ steps/<step_id>/run_<n>/{prompt.md,receipt.json,trace.json}   每次执行留痕
   ├─ snapshots/<时间>/    覆盖关键文件前的备份（可回滚/对比）
   ├─ usage.jsonl          每次 LLM 步骤的耗时/估算 token/结果
   └─ run.log
```

- **路径唯一来源**：`workspace.py::PATHS`（与 workspace-manager 的路径表一致 + 上述补定义项），并渲染成 Markdown 表注入每步 prompt，skill 不得硬编码。
- 章节文件命名规则沿用 workspace-manager（`第{nn}章 {title}.md`，超过 99 章改三位），由代码校验。
- workflow 自身的 session 数据默认在 `mini_agent` 的 `.agent/workflow_sessions/`；执行时传 `output_export_dir=<WORKSPACE>/_studio/steps/...`，保证**回执与产物副本都在小说目录里**。

---

## 4. 流水线：skill 最终怎样被使用

### 4.1 步骤模型

每个步骤（`StepDef`）= `{id, skill, 触发条件, 输入文件, 期望产出, 校验器, 默认闸门, 质量档启用, 成本级别}`。
状态：`pending → ready → running → (waiting_review | waiting_user) → passed`，另有 `failed / skipped / stale(上游变更)`。

### 4.2 阶段与步骤（对应 novel-director）

| 阶段 | 步骤 | 执行器 | 默认闸门（手动挡） | 说明 |
|---|---|---|---|---|
| P0 | `init_workspace` | Python | auto | 目录/配置/初始文件；已存在则走恢复体检 |
| P1 规划 | `plan_blueprint` | planner | **review（强制检查点）** | 向导里已收集意图→一次性成稿；代码跑蓝图自检复核 |
| | `design_theme` | theme-architect | review | 蓝图后 |
| | `design_pacing` | pacing-architect | review | 幕次节奏/三章单元 |
| | `design_character:<角色>`×N | character-designer | review | 主要角色=出现≥3章，**串行** |
| | `build_characters` | character-manager | auto | 人设卡→JSON 档案 |
| | `build_world` | world-builder | review | |
| | `register_plot` | plot-tracker | auto | 注册伏笔 + 张力基准 |
| | `init_word_count` | Python | auto | 全书配额 |
| | `simulate_blueprint` | plot-simulator（快进） | manual/可选 | 验证伏笔可兑现、断裂点 |
| | `init_style`（模式 A） | style-guardian | review | 仅当用户提供了参考文本；模式 B 在前三章后 |
| P2 前三章 | `design_opening` | hook-designer | review | 三章钩子系统，作为 writer 额外指导 |
| | 第 1–3 章单章循环 | 见 4.3 | | |
| | **`confirm_first3`** | 人工检查点 | **强制** | 确认后才 `init_style`（模式 B） |
| P3 正文 | 第 n 章单章循环 ×N | 见 4.3 | | |
| 幕末 | `act_review:<幕>` | consistency-checker（深度） | review | 幕次健康报告 |
| | `act_style:<幕>` | style-guardian（幕间对比） | auto | |
| | `act_pacing:<幕>` | pacing-architect | auto | 重规划下一幕三章单元 |
| | `act_theme:<幕>` | theme-architect（主题一致性） | auto | |
| | `act_simulate:<幕>` | plot-simulator（中层） | manual | 下一幕开始前 |
| | `act_worldcheck:<幕>` | world-builder（冗余巡检） | auto | |
| | `act_ideas:<幕>` | idea-catcher（复查暂存灵感） | review | |
| 每 10 章 | `snapshot` | Python | auto | memory 全局快照 |
| 结局 | `design_ending` | ending-designer | review | 进入末幕前；终章前再打磨最后一句 |
| 收尾 | `final_review` | consistency + revision-editor 全书巡检 | review | 伏笔兑现率 ≥90%、字数 ±5% |
| 按需 | `revise` / `capture_idea` / `simulate_stuck` | revision-editor / idea-catcher / plot-simulator | manual | 由 UI 按钮触发 |

### 4.3 单章循环（第 n 章）

```
写前（多数为 Python，几乎零 LLM 成本）
  W1  word_quota        Python  配额 + 场景分配 + 是否消化上章偏差
  W2  plot_tasks        Python  章前情节清单（待埋/待兑现伏笔、故事线任务）
  W3  context_pack      Python  按 chapter_type 的 token 预算组装上下文包 → memory/context_ch{n}.md
  W4  style_hint        Python  从 style_profile 取基调/禁用高频词（模式 A/B 已建立时）
  W5* simulate_key      plot-simulator   仅"转折/逆转/高潮章"或手动，精品档默认开
  W6* design_scenes     scene-architect  仅关键场景，精品档默认开
  W7* craft_dialogue    dialogue-crafter 仅重要对话场景，精品档默认开
写中
  W8  write_chapter     chapter-writer   输出正文 + 写作备注（回执含字数/备注）
  W9  machine_check     Python           字数、命名、禁用词、角色名白名单、伏笔 id 存在性（见 7.2）
写后（强制完章闭环，不可跳过）
  W10 review            chapter-review 阶段一（+ consistency-checker）→ 🔴/🟡 清单
  W11 revise_loop       Python 编排：有 🔴 → chapter-writer 按清单最小修改 → 只复核失败项；≤3 轮
                        仍有 🔴 → 升级人工（inbox），**不推进到下一章**
  W12 archive           chapter-review 阶段三：章节总结 + 全书章节索引 + memory/ch_n.json
  W13 sync              阶段四：progress/characters_current/foreshadowing_status…；
                        标准/精品档拆为 character-manager、plot-tracker、style-guardian 三个独立步骤
  W14* reader_sim       reader-simulator   每 3–5 章（质量档决定）
```

估算每章 LLM 调用数：草稿档 ≈ 3（写/审/归档同步）；标准档 ≈ 6；精品档 ≈ 9–12（+推演/场景/对话/best-of-2）。UI 会在运行前显示预估。

---

## 5. 界面模块设计：skill → 页面 → 交互

### 5.1 页面总览

| 页面/Tab | 对应 skill | 展示内容 | 可做的交互 |
|---|---|---|---|
| **📚 书架** | workspace-manager | 所有小说：书名/进度/字数/当前挡位/状态灯/最近一次运行 | 新建、打开、导入已有工作目录（走项目恢复体检）、归档/删除（二次确认） |
| **➕ 新建向导** | workspace-manager、novel-planner（输入收集） | 分步表单：题材/主题/篇幅/平台/主角+核心欲望+障碍+冲突/结局走向/已有创意碎片/参考风格（可粘贴参考文本）→ 选挡位与质量档 → 工作目录 | 提交后创建目录并（按挡位）排出 P1 步骤 |
| **🧭 总控台** | novel-director | 流水线图（graphviz，节点着色=状态）、当前阶段/下一步建议、**待确认收件箱**、预算与预估成本、运行监控（心跳/当前步骤/最近日志） | ▶运行下一步 / ▶运行到下个检查点 / ▶连续写 N 章 / ⏸暂停 / ⏹停止 / 切换挡位 / 项目体检 / 回退到某步（下游标记 stale） |
| **🗺 规划** | novel-planner、plot-simulator、theme-architect、pacing-architect、ending-designer | 蓝图（幕结构、章节表、主角弧、伏笔索引、自检结果、版本日志）；推演报告与决策日志；主题/节奏/结局方案 | 在表格里直接改章节（`st.data_editor`）→ 自动生成**变更影响评估**（planner 的分级：轻微/中等/重大→提示需重跑的下游步骤）；带意见重新生成；触发推演 |
| **👥 设定库** | character-designer、character-manager、world-builder | 人物卡/档案（wont_do、当前状态、关系）、世界圣经、设定使用率与冗余提示 | 新增/编辑角色（编辑核心欲望/恐惧会提示"应走 designer 重设计"——skill 的边界规则）、按需扩展设定（先做冲突检查）、重新生成某角色 |
| **🧵 线索** | plot-tracker | 伏笔注册表（状态/依赖链/计划兑现章）、故事线进度、**张力曲线：计划 vs 实际**、健康度报告 | 手动登记/改伏笔、标记兑现、查看依赖链检验结果 |
| **✍ 写作台** | memory-manager、hook-designer、scene-architect、dialogue-crafter、chapter-writer、word-count-controller | 选章后显示：蓝图要求、字数配额、情节任务、**上下文包（各部分 token 占用条）**、章首衔接/章末钩子设计；正文编辑器；候选稿对比 | 补充"作者指导"→ ▶生成/重写本章；分步执行写前各项；**手写**或在编辑器里改稿；选中段落做 AI 辅助（续写/扩写/对话重写→dialogue-crafter、润色→revision-editor L1） |
| **🔍 审校** | chapter-review、consistency-checker、style-guardian、reader-simulator、revision-editor | 每章检查报告（🔴/🟡 逐条，带原文定位）、修改轮次、幕次健康报告、风格漂移、词频、读者注意力曲线、主动巡检 8 维 | 对每条问题：采纳→送回 writer / 忽略（记录原因）/ 手动改；3 轮失败的升级项处理；触发 L1–L4 修订并预览**影响传播**；"接受风险继续"（仅用户可点，写入修订记录） |
| **📖 阅读/成稿** | — | 章节目录、正文预览、字数统计、全书章节索引 | 合并导出 md/txt；按章/全书复制 |
| **💡 灵感箱** | idea-catcher | 灵感列表（待评估/已整合/暂存/丢弃）、评估三步结果 | 随手记一条 → ▶评估 → 整合到某章/暂存；幕末复查队列 |
| **📊 度量与日志** | — | 字数曲线、每章审稿轮数、🔴/🟡 趋势、伏笔健康、用量与耗时、失败/重试、步骤时间线 | 点开任一步骤看 prompt/回执/trace（排错与调 skill 用） |
| **⚙ 设置** | — | 挡位与质量档、**步骤×闸门矩阵**、预算（章数/调用数/时长上限）、模型与 max_turns 覆盖、强制检查点开关、禁用词表 | 保存后从下一步生效 |
| **🧩 Skill 库**（全局） | 全部 | 23 个 skill 的描述、归属阶段、在本项目中的执行器类型（LLM/Python）、SKILL.md 预览 | 只读 + 各 skill 的"在哪个页面被使用"跳转 |

### 5.2 每个步骤卡片的统一交互（"各流程的交互操作"）

```
[步骤名]  状态徽章  耗时/尝试次数   闸门：auto/review/manual/skip ▾
 ▶运行  🔁带意见重跑  ✏编辑产物（直接改文件，改动会使下游标记 stale）  ✅批准  ⏭跳过
 ↩回退到此步  📎查看 prompt/回执/trace  📝给这一步留作者指导
 [产物预览] [与上一版对比（difflib）] [机器校验结果]
```

- **产物依赖与脏标记**：每步记录输入文件哈希；上游被重新生成/手改后，下游标 `stale`，UI 提示"下游 N 步需要重跑"，由用户选择全部重跑/逐个重跑/忽略（沿用你在小说转视频方案里"上游重生成后下游必须重跑"的约定）。
- **needs_user 表单**：skill 回执若含 `needs_user`（如 planner 缺主角信息、plot-simulator 让作者选路径），收件箱出现表单卡；提交后带答案重跑该步。
- **手动写作旁路**：任何"产出文件"的步骤，用户都可以选择"我自己写/上传"——系统只做校验并把该步标 passed，后续流程照常运转。

---

## 6. 自动模式 / 手动模式 / 挡位

### 6.1 闸门（每个步骤一个）

| 闸门 | 行为 |
|---|---|
| `manual` | 不自动运行，等用户点 ▶（或手写产物） |
| `review` | 自动/手动运行后停在 `waiting_review`，用户 ✅批准 / ✏编辑后批准 / 🔁带意见重跑 |
| `auto` | 运行并自动继续 |
| `skip` | 跳过（下游若依赖其产物则提示） |

### 6.2 挡位预设（控制"人参与多少"）

| 步骤类别 | G0 手动挡 | G1 引导挡 | G2 半自动挡 | G3 全自动挡 |
|---|---|---|---|---|
| 蓝图/主题/节奏/结局设计 | manual | review | review | review（蓝图仍须确认，可在设置中显式豁免） |
| 人设/世界/伏笔建档 | manual | review | auto | auto |
| 前三章 | manual | review（逐章） | review（逐章） | review（逐章） |
| 第 4 章起：写前准备（W1–W4） | manual | auto | auto | auto |
| 第 4 章起：写作 W8 | manual | review | auto | auto |
| 审校/修改循环 W10–W11 | manual | auto | auto | auto |
| 归档/同步 W12–W13 | manual | auto | auto | auto |
| 幕末步骤 | manual | review | review | auto（深度审稿仍 review，可改） |
| 修订/推演/灵感 | manual | manual | manual | manual（始终手动触发，避免无谓成本） |
| 连续运行范围 | 单步 | 到下个检查点 | 每批 N 章（默认 3） | 到预算上限/全书完成 |

- **默认挡位 = G0 手动挡**（保守 opt-in）；其余在新建向导/设置里显式选择。
- 挡位可随时切换，**从下一个步骤起生效**；也可"按阶段"覆盖（如规划用 G1、写作用 G3）。
- 设置页的矩阵显示最终生效闸门，任何一格都可单独改，并标注"与预设不同"。

### 6.3 不可被挡位绕过的检查点（默认强制）

1. 蓝图确认（G3 可在设置里显式豁免）；
2. 前三章确认 + 风格指纹建立（skill 明确禁止自动建立）；
3. 修改循环 3 轮仍有 🔴 → 升级人工（引擎层保证不推进下一章）；
4. 蓝图重大变更（L4 / 章节合并拆分 / 幕次结构）；
5. 预算耗尽（章数/调用数/时长）→ **暂停而不是失败**，可续跑。

### 6.4 质量档位（控制"每章做多细"）

| 能力 | 草稿 | 标准（默认） | 精品 |
|---|---|---|---|
| 机器校验 + chapter-review | ✓（合并 consistency 维度） | ✓ + 独立 consistency-checker | ✓ |
| 同步下游模块 | 单步合并 | 拆分（人物/情节/风格） | 拆分 |
| style 写前提示/写后检查 | 仅写前 | 写前+写后 | 写前+写后+每 5 章词频 |
| reader-simulator | 关 | 每 5 章 | 每 3 章 |
| scene-architect / dialogue-crafter | 关 | 手动触发 | 关键场景自动 |
| plot-simulator | 关 | 幕末/手动 | 转折章前自动 |
| 候选稿（best-of-2，择优） | 关 | 关 | 关键章开（M6） |
| 修订巡检（revision-editor 8 维） | 关 | 幕末 | 幕末 + 每 10 章 |

### 6.5 自动运行的机制

- **worker 子进程**：UI 的"运行"只写 `control.json` 并拉起 `entrypoints/run_novel.py --novel <路径>`；worker 循环"挑下一个可运行步骤 → 查闸门/预算/control → 执行 → 落盘"，步骤之间检查暂停/停止指令（协作式；单步受 workflow `timeout`/`max_turns` 约束）。
- **崩溃自愈**：启动时扫描遗留 `running` 步骤，若 worker 心跳过期则标 `interrupted` 并可重跑（直接吸取你"孤儿执行记录导致永久停摆"那次的教训）。
- **幂等与断点续跑**：步骤以"产出文件 + 回执"为完成标志；重复运行已 passed 的步骤默认跳过。
- **批量/定时**（可选、默认关）：`project.yaml` 提供 `advance_all_auto`（对所有自动挡小说各推进 1 章/批），cron 行默认注释，需要时手动打开；`max_concurrency: 1`。
- **并发**：同一本书同一时刻只允许一个 worker（工作目录内文件锁）。

---

## 7. 怎样让它真正写出好小说

> 说明：质量最终取决于所用模型，下面是能把"一次性生成"变成"可控、可校验、可修复"的机制；我没有真实 LLM 密钥，无法在本环境验证文笔，见第 8、9 节。

### 7.1 设计先行，写作靠约束
- 蓝图自检（字数/伏笔/结构/主角弧）由 planner 的清单 + **代码再校一遍**（planner 自己的"自检"不能当唯一依据）。
- 人物 `wont_do` 行为边界、世界规则、伏笔依赖链写成**结构化 JSON**，写作时注入、审稿时对照——比"凭记忆写"可靠。
- 风格：优先用户给的参考文本建指纹；没有则前三章经用户确认后再建。

### 7.2 机器校验（零 LLM 成本，写完立刻跑）
字数偏差（配额 ±阈值）、文件命名、章节标题与蓝图一致、禁用词/高频词、出场角色名白名单（出现未登记人名→告警）、引用的伏笔 id 是否存在、对话占比与段落长度异常、重复段落（与近 3 章 n-gram 重叠）、结尾是否满足钩子类型的基本形态。结果作为 W10 的输入，也显示在审校页。

### 7.3 上下文工程（memory-manager 的代码化）
按 chapter_type 的 token 预算表（总上限 20000）确定性组装：最近 2–3 章摘要 > 角色状态 > 情节任务 > 相关历史（关键词/TF-IDF 检索，reveal 章优先找回伏笔埋设处原文）> 世界设定 > 风格提示；中文按字符数估算 token；上下文包落盘并在 UI 可见、可手动增删条目。**摘要只在核查通过后才存**，章节被改后必须重建（skill 的陷阱 #3）。

### 7.4 完章闭环与独立审稿
- 每个审稿/修改步骤都是新起的独立 agent（上下文与写作 agent 隔离）；设置里可给审稿类步骤单独指定另一个模型，避免"自己审自己"。
- 修改循环只改被指出的问题并只复核失败项，防止越改越偏；3 轮失败升级人工。
- 🟡 建议项不阻塞，但汇总进幕末深度审稿。

### 7.4b 长篇不崩的保障
- 伏笔健康（≤3 条主线伏笔、兑现率 ≥90%）、张力曲线计划 vs 实际、字数偏差在幕边界归零、每 10 章全局快照、幕末深度审稿 + 幕间风格对比（区分"有意调整"与"有害漂移"）。

### 7.5 人在回路的关键点
蓝图、前三章、幕末、结局、升级项必须有人看；其余可托管。UI 的"作者指导"和"灵感箱"让人随时把想法注入后续章节而不打断流水线。

### 7.6 度量闭环
度量页汇总每章审稿轮数、🔴 类型分布、风格漂移、读者模拟分数，用来回头调整 skill 提示、挡位和质量档（例如某类硬错误高发 → 提高该类的机器校验或写前注入）。

---

## 8. 实施分期（每期完成：更新文档 + 测试 + 打包 diff zip，保持目录结构可覆盖）

| 期 | 内容 | 验收 |
|---|---|---|
| **M0 脚手架** | `external_projects/novel_writer/` 骨架（可用 external-project-manager 的 scaffold）、23 个 skill 落位、`workspace.py` 路径表/初始化/恢复/体检、`project.yaml` 校验、`entrypoints/health.py` | manifest 校验通过；初始化/恢复单测；体检脱离 LLM 可跑 |
| **M1 引擎内核** | `state/steps/gates/runner/pipeline`、`skill_step.yaml`、回执契约、`validators`、worker 子进程与 control/心跳/孤儿自愈、`tool_api` + `service_cli`；**用桩 WorkflowRunner** 跑通全流程 | 单测 + 端到端桩测：暂停/恢复/崩溃续跑/脏标记/闸门/预算 |
| **M2 规划阶段 + UI 第一批** | P1 全部步骤；书架、新建向导、总控台、规划、设定库、线索页 | Streamlit `AppTest` 跑通各页；桩数据下向导→蓝图→建档全链路 |
| **M3 章节循环 + UI 第二批** | W1–W14、`context_pack`、修改循环、归档同步；写作台、审校、阅读页 | 桩 LLM 下连续写 3 章 + 故意注入硬错误验证 3 轮升级与"不推进下一章" |
| **M4 幕末/修订/灵感/度量/设置矩阵** | 幕末步骤、revision L1–L4 与传播、灵感箱、度量日志页、步骤×闸门矩阵 UI | 覆盖矩阵保存/生效、L3/L4 下游触发测试 |
| **M5 自动模式完善** | 各挡位预设落地、预算、批量 `advance_all_auto`、通知/收件箱、手写旁路与 AI 辅助 | 四个挡位的闸门行为表驱动测试；预算耗尽=暂停 |
| **M6 质量增强（可选）** | 候选稿 best-of-N、词频与 n-gram 重复检测增强、全书终审、docx/epub 导出 | 视需要再确认范围 |

测试策略：
- 引擎/状态/闸门/校验器/上下文包：纯 pytest；LLM 层用**桩 `WorkflowRunner`**（让桩按步骤写出预设的产物文件与回执），与 world_simulator 的测试风格一致。
- UI：用 `streamlit.testing.v1.AppTest` 做页面级冒烟（比"只做静态检查"更进一步）；仍不能代替人工在浏览器里点一遍，会在实施记录里如实标注。
- **真实 LLM 端到端我无法在本环境运行**（无密钥/网络白名单）。会提供 `entrypoints/smoke_real_llm.py`（极小规模：3 章短篇、草稿档、低预算）供你本机一键验证，并在文档里列出需要你重点观察的点。

---

## 9. 风险与已知限制

1. **skill_agent 每步开销大**（新起 agent、读文件、写文件）：通过"写前准备代码化 + 上下文包预注入 + 质量档分级"控制，但长篇全自动仍会消耗可观 token，所以预算与预估是首版必备项。
2. **agent 写 JSON 可能不规范**：依赖 `mini_agent` 的 `result_file` 校验重试 + 我们的 schema 校验器；人物/伏笔等关键 JSON 在写入前做快照以便回滚。
3. **skill 原文假设交互式**：用 prompt 前言适配，但个别 skill 的输出格式（长篇报告）可能与 UI 渲染预期不完全一致——首期按 Markdown 原样展示 + 关键字段结构化提取，必要时再逐个调整（会先告知你，不擅自改 skill 原文）。
4. **100 章以上的超长篇**：设计基线是 40 章/30 万字蓝图；更长需要分卷/更强检索，列为后续。
5. **Streamlit 轮询**：用 `st.fragment(run_every=…)` 读状态文件，避免整页重跑；多人同时操作同一本书不在范围内。

---

## 10. 决策清单（2026-10-16 已全部按推荐确认）

| # | 问题 | 确认结果 |
|---|---|---|
| Q1 | 项目名与位置 | `external_projects/novel_writer`（Python 包同名） |
| Q2 | 工作目录默认位置 | `data/novels/<书名>_<时间戳>/`，创建时可改为任意绝对路径 |
| Q3 | skill 处理方式 | 23 个原样放入 `skills/`，**不改原文**；适配全靠 prompt 前言 + 统一路径表（发现的路径不一致以 workspace-manager 为准） |
| Q4 | 默认挡位 / 质量档 | 默认 **G0 手动挡 + 标准档**；G3 仍保留"蓝图确认"检查点（可显式豁免） |
| Q5 | 后台执行方式 | worker 子进程 + 工作目录状态文件，不依赖 daemon；批量 cron 默认关闭 |
| Q6 | 首版范围 | 做 M0–M5；M6（best-of-N、docx/epub、全书终审增强）待 M5 后再议；**不做** HTTP server、视频化（已有 novel-video-studio） |
| Q7 | LLM 调用方式 | 首版仅 `skill_agent` 步骤；单轮轻量调用（`python_step` + `ctx.llm`）作为后续成本优化项 |
| Q8 | UI 组织 | `app.py` 仅作入口，页面拆到 `ui/` 多模块（避免重蹈 8000 行单文件） |
| Q9 | 文档与注释语言 | 中文（与你既有偏好一致） |
| Q10 | 交付节奏 | 每个 M 期完成后更新文档、跑测试并打包 diff zip，等你确认再进入下一期 |

---

## 11. 实施进度

| 期 | 状态 | 备注 |
|---|---|---|
| M0 脚手架 | **已完成（2026-10-17）** | 骨架、23 个 skill 原样落位、workspace/settings/shelf/skills_catalog/tool_api、3 个入口、简版界面、39 个测试通过；project.yaml 已通过框架 load_manifest 校验 |
| M1 引擎内核 | **已完成（2026-10-17，待确认）** | 见下方「M1 实施记录」 |
| M2 规划阶段 + UI 第一批 | 未开始 | |
| M3 章节循环 + UI 第二批 | 未开始 | |
| M4 幕末/修订/灵感/度量/设置矩阵 | 未开始 | |
| M5 自动模式完善 | 未开始 | |
| M6 质量增强（可选） | 待议 | 不属于已确认范围 |

### M1 实施记录（2026-10-17）

**交付范围**：`novel_writer/{steps,gates,state,pipeline,validators,runner,worker,service_cli}.py`、`workflows/skill_step.yaml`、`entrypoints/{run_novel,smoke_real_llm}.py`；`tool_api` 增加运行与步骤操作接口；`tests/test_engine_m1.py`（32 个用例，全部使用桩执行器）。全量测试 71 个通过（M0 39 + M1 32）。

**验收对照（第 8 节 M1 行）**：
- 暂停 / 停止：在两步之间生效，返回 `paused` / `stopped`，不打断正在执行的 LLM 调用。✔
- 恢复：`paused` 后再次运行即从前沿续跑；预算耗尽同样可续跑。✔
- 崩溃续跑：遗留 `running` 步骤在心跳超时后置为 `interrupted` 并可重跑（测试覆盖）。✔
- 脏标记：上游产物哈希变化时下游 `passed` 步骤转为 `stale`，旧记录只补哈希、不误标。✔
- 闸门：强制检查点不可被覆盖降级；挡位预设与阶段覆盖按方案 6.2 表格实现。✔
- 预算：章数 / LLM 调用数 / 时长上限，耗尽即暂停（G2 的半自动批大小并入章数上限）。✔

**与方案的偏差（有意为之，已记录）**：
1. **章节 python 步骤未实现**：W1–W4、W9、W11、W14 注册为 `blocked`（错误信息提示 M3 实现），不做假成功。M1 能真实跑通的是 P0–P2 前三章设计；章节写作链路由 M3 接上。
2. **W5–W7 未展开**：推演 / 场景 / 对话属于 M3/M4 范围，本期不进入流水线。
3. **`model_overrides` 未接入**：设置里已有字段并校验，但 `FrameworkInvoker` 暂未使用；步骤×闸门矩阵 UI（M4）与模型覆盖一并接入。`max_turns_overrides` 已接入。
4. **`run_novel` 未登记为 daemon 入口**：它需要 `--workspace`，daemon 调用 entrypoint 不会传参。批量入口放到 M5 的 `advance_all_auto`。
5. **真实 LLM 未验证**：`FrameworkInvoker` 已对照 world_simulator 的 skill_agent 用法，且 `WorkflowStore` 能解析 `skill_step.yaml`（属性 `max_turns` / `timeout` / `result_file` 均存在）；端到端需要本机运行 `entrypoints/smoke_real_llm.py`。
6. **跨进程并发**：状态写入同进程加锁，跨进程以"worker 每轮重读盘"收敛；极端竞争下以后写者为准（已在 PROJECT.md 注明）。

**需要你重点观察的点（真实 LLM 下）**：
- skill 是否真的按统一路径表写文件（而不是 skill 原文里的旧路径）；
- 回执 `receipt.json` 是否在第一次就合格，重试频率是否可接受；
- `plan_blueprint` 的 `needs_user` 是否能正常挂起并被 `answer` 续上；
- 一次规划的 token 消耗（`_studio/usage.jsonl` 不记 token 数，目前只记耗时与结果，需要的话 M5 补充）。

交付节奏（Q10）：每期完成后更新文档、跑测试、打包 diff zip（保持目录结构，可直接覆盖仓库），等用户确认再进入下一期。
