# mini_agent 自我进化改造后续规划（Stage 4+）

> 本文档承接 `next_doc/self_evolution_implementation_plan.md`（Stage 0-3），覆盖该文档
> "暂不启动的部分"——Phase W2/W3（知识层）、第 9-16 章横向加固、Phase G（后台循环）、
> Phase H（自主运行时）。定位与前文档一致：**可执行的实施计划，不是新的架构设计**，
> 所有结论均来自对源码的逐项核对。
>
> 前提：本文档假设 Stage 0-3 全部完成（Stage 3.1 由其他人推进中，不阻塞本文档的启动条件，
> 见下文"二、依赖关系再核查"）。
>
> 核查时间：2026-06（对应代码快照见仓库当前 HEAD，含 Stage 3.2 完成后的状态）。

---

## 一、现状核查结论（针对"暂不启动的部分"逐项复核）

`self_evolution_implementation_plan.md` 把"暂不启动的部分"概括为两类——Phase G/H（因为
"W 完全空白，没有地基"）、第 9-16 章横向加固（因为"可在任意阶段穿插，非阻塞"）。但这个
判断写于 Stage 0 之前，此后 Stage 0-3 顺手完成了一部分本属于这个范围的工作。逐项复核：

| 文档项 | 状态 | 实际情况 |
|---|---|---|
| W1（`task_manifest.json` / `plan_snapshot.json`） | ✅ 已实现（Stage 0.2） | 误以为"W 完全空白"的前提已经不成立——W1 早已完整落地，详见前文档 Stage 0.2 |
| W2（Workdir 知识层：`project.json`/`timeline.jsonl`/`work_index.json`/`open_threads.json`/`knowledge.md`） | ❌ 未实现 | 全局零代码痕迹 |
| W3（Global 知识层：`self_profile.json`/`projects_index.json`/`cross_project_index.json`/`activity_log.jsonl`） | ❌ 未实现 | 全局零代码痕迹 |
| `MemoryEntry.entry_type="capability_map"` | ⚠️ 仅预留枚举值 | 数据结构层面留了口子（`memory_store.py` 注释列出该取值），但没有任何代码生成或消费这种条目——6.6 节"能力地图"完全没有实现，只是当年设计 lesson schema 时顺手埋了个钩子 |
| 6.1（进化者 subagent 角色分离） | 🔄 由他人推进中（Stage 3.1） | 不在本文档规划范围，见"二、依赖关系再核查" |
| 6.4/6.5/6.7（剪枝去重 / Scope 晋升 / 演化节奏治理） | ❌ 未实现 | `occurrence_count` 字段已存在并被 lesson 规则触发使用，但没有任何"超过阈值后剪枝/晋升/限流"的逻辑 |
| 第 9 章观察性（tracing / `/diagnostics` / 异常检测 / 因果链） | ❌ 未实现 | 只有 `/status` 端点；`events.jsonl` 无 `error_category`/`resolves_seq` 字段 |
| 第 10 章环境感知（`FILE_CHANGE_EFFECTS` / 环境漂移 / inbound webhook） | ❌ 未实现 | `file_watcher.py` 仍是纯被动通知 |
| 第 11 章多 Agent 协调深化（能力匹配调度 / 降级重试链 / 中间结果流） | ❌ 未实现 | `agent_profiles.py` 无 `capability_tags` 字段；`TaskManager` 无 fallback 链（现有 `llm_fallback_chain` 是 LLM provider 级别切换，与本节"SubAgent 降级链"是完全不同的机制，纯属命名相似） |
| 第 12 章知识表示深化（`knowledge_index.json` / skill 依赖图 / 置信度传递） | ❌ 未实现 | SKILL.md frontmatter 无 `activation_conditions`/`conflicts_with`/`confidence_score` 扩展字段 |
| 第 13 章执行层鲁棒性（元认知 checkpoint / 错误分类恢复 / 任务降级） | ❌ 未实现 | reminder 体系仍是关键词匹配，无 `error_category` 路由表 |
| 16.1（审批中插话：三选项 + `(e)dit`） | ✅ 已实现（Stage 1.5） | 比文档设想更早完整落地——`(e)dit` 选项 + `user_correction` 消息接入纠正检测，详见前文档 Stage 1.5 |
| 16.2（隐式反馈捕捉） | ❌ 未实现 | 无编辑事件监听、无响应时间异常检测 |
| 16.3（澄清优先分支 `PlanTaskType.CLARIFY/VERIFY`） | ❌ 未实现 | `plan.py` 无此枚举扩展 |
| 18.1（时间感知检索权重） | ⚠️ 部分实现 | `memory_store.py` 已有固定半衰期的时间衰减（`decay_half_life_days`，配置可调），但"重复印证衰减更慢"（按 `occurrence_count` 动态拉长半衰期）和"趋势放大"（`trend_boost`）两部分均未实现 |
| 18.2（版本里程碑 `milestones`） | ❌ 未实现 | 无任何里程碑事件钩子 |
| 18.3（知识时效性标注 `expires_after_days`） | ❌ 未实现 | `MemoryEntry` 无此字段，无过期归档逻辑 |
| Phase G（后台循环） | ❌ 未实现 | 无 `AutonomousLoop`，无任何周期性任务调度 |
| Phase H（自主运行时） | ❌ 未实现 | `AgentBridge`/`InputQueue` 长驻进程雏形已存在，其余（`AgentSelfProfile`、Goal Backlog、`autonomy_level` 分级）全无 |

**核心判断（与前文档一脉相承）**：第 9-16 章横向加固里，**只有挂在 Stage 0-3 主线任务上"顺手做"的两项**
（16.1 因为 Stage 1.5 收尾 `(e)dit`、`occurrence_count` 因为 Stage 1 的 lesson 触发规则）已经落地，
其余横向加固条目仍然原样空白——证明了"横向加固没有自动被覆盖，必须显式排期才会推进"。Phase W2/W3
依然是"完全空白"，意味着前文档"H 强依赖 W 全部就位，跳过去做没有地基"的判断**对 H 依然成立**，
但对 G 需要重新评估（见下节）。

---

## 二、依赖关系再核查：G 能否在 W2/W3 之前启动？

前文档把 G/H 打包成一个整体，理由是"H 强依赖 W"。但设计文档原话（第 17 章依赖图）显示
**G 和 H 之间也有顺序**：G（后台循环）依赖 W2/W3 做"巡视什么"的数据基础，H（自主运行时）
依赖 G 已经在跑的周期性任务作为"调度器要挂什么活"的内容来源。逐条拆解 G 内部四个子项
（6.1/6.4/6.5/6.6/6.7）对 W 的真实依赖程度：

| Phase G 子项 | 对 W2/W3 的依赖 | 能否提前 |
|---|---|---|
| 6.4 剪枝/去重/冲突检测 | 只读 `memory.jsonl`（已存在）+ `SkillUsageTracker`（已存在），不依赖任何 W2/W3 文件 | ✅ 可独立先做 |
| 6.6 能力地图（`capability_map`） | 数据来源是 `events.jsonl`/`task_manifest`（W1，已完成）的统计聚合，写回 `memory.jsonl`（已存在），不强依赖 W2/W3——只是设计文档把 global scope 的能力地图放进了 `self_profile.json`（W3），但 **workdir scope 的能力地图本身可以先落在 memory 条目里**，不需要等 W3 | ✅ 可提前做 workdir scope 版本，global scope 汇总留给 W3 之后 |
| 6.5 Scope 晋升（workdir → global） | 直接依赖 `cross_project_index.json`（W3） | ❌ 必须等 W3 |
| 6.1 角色分离（evolution-agent） | 由 Stage 3.1 推进中，不在本文档范围 | （不适用） |
| 6.7 演化节奏治理（频率限流/观察期） | 依赖 6.4/6.6 的统计数据作为限流判据，不直接依赖 W | ✅ 可在 6.4/6.6 之后做 |

**结论**：G 不必整体等待 W2/W3，但 G 的"巡视/调度"价值（"今天该看哪个项目、该刷新哪个能力地图"）
只有在 W2（`work_index.json` 知道有哪些 WorkThread 要推进）之后才真正成立——没有 W2，G 的剪枝/
能力地图逻辑能跑，但"后台循环"本身没有跨 session 的待办列表可循环，等于有引擎没有方向盘。
因此实际排期仍然是 **W2 → G 的核心子项 → W3 → G 的 Scope 晋升子项 → H**，与前文档判断的方向一致，
只是不是"打包等待"，而是"G 内部按子项错峰启动，W2 完成后 G 的大部分子项即可推进，无需等 W3"。

第 9 章观察性维持设计文档原判——"是所有量化判断的数据基础，越早建越省事"，且对 W2/W3 无依赖，
本文档把它提到 W2 之后、G 之前，作为独立 Stage 优先做（详见 Stage 6）。

---

## 三、改造原则（延续前文档，针对本批内容补充三条）

1. **不重复造轮子**（延续）：W1 已完成，本批不涉及；16.1/`occurrence_count` 已完成，相关子项做增量而非重写。
2. **遵循依赖链，但允许子项级别的错峰**（延续 + 细化）：不再是"整个 Stage 等待整个 Stage"，
   而是按"二"节拆出的子项粒度判断各自的真实前置条件，避免不必要的串行等待。
3. **每个里程碑都可独立验证**（延续）：本批内容大量是"写入但无人读取"风险高的结构化数据
   （`work_index.json`/`self_profile.json` 等），验证标准必须包含"确认 context_builder 真的注入了
   这些数据"，不能只验证文件被正确写出。
4. **【新增】数据先于行为**：W2/W3/第 9 章是纯粹的"知识沉淀与观察"层，不产生任何自主行为，
   风险极低，应该先完整建好再开始 Phase G/H 这类"基于数据做决策"的行为层——参考前文档
   "T3 红线最先画"的精神，这里是"数据地基最先打"。
5. **【新增】Phase H 不是常规 Stage，是决策点**：设计文档第 18 节"开放问题"1-9 条已经列出
   Phase H 涉及的产品定位问题（"该不该让 agent 有自己的议程"），这不是工程问题，本文档把
   Phase H 列为"达到条件后需要人工决策是否启动"，不纳入常规人天估算的累加总数。
6. **【新增】横向加固走"机会性任务池"而非独立 Stage**：第 10/11/12/13/14/15 章延续设计文档
   "可在任意阶段穿插"的定位，本文档不为它们单独划分人天周期，而是列成一张任务池，挂在
   Stage 4-8 的具体改动点上顺带做（同一处代码改动，增量成本低），并标注"若机会性时机一直
   不出现，最晚不晚于 Stage 8 结束前补做"的兜底时间点。

---

## 四、实施计划

### Stage 4（核心，约 3-4 人天）—— Phase W2：Workdir 知识层 ✅ 已完成

> 设计文档 8.2 节。在 `.agent/` 下新增四个文件 + 1 个 Markdown，是本文档范围内优先级最高的一项——
> 它是 Stage 4 之后几乎所有内容（G 的巡视、context 注入、晨报）的数据来源。

> **完成情况摘要**（实现细节见对应代码与测试，此处只记结论，供 Stage 5/6/8 衔接参考）：
> - 新增文件：`perception/workdir_knowledge.py`（数据模型 + 读写，含 4.1-4.5 全部数据结构，
>   以及横向加固的 `capture_environment_fingerprint`/`detect_environment_drift`/
>   `KnowledgeIndexEntry`）、`tools/workdir_knowledge.py`（`add_open_thread`/
>   `update_work_thread`/`update_knowledge` 三个工具，thread-local provider 机制与
>   `tools/evolution.py` 同构）、`prompts/system|user/timeline_reflection*.md`（4.2 独立轻量
>   反思调用，采用方案①而非与 lesson 反思合并）。
> - 修改文件：`storage/paths.py`（7 个 `workdir_xxx` 路径方法，含 `workdir_knowledge_index`）、
>   `config/models.py|loader.py|__init__.py`（`WorkdirKnowledgeConfig`，默认 `enabled=True`）、
>   `agent.py`（`_maybe_ensure_project_meta` 接入 `_init_session`，仅在进程启动时调用一次，
>   不在 `load_session()`/`new_session()` 里重复计入 `total_sessions`；
>   `_update_workdir_knowledge_on_session_end` 接入 `trigger_session_end()`，与 lesson 反思
>   并列、互不阻塞）、`context_builder.py`（4.6 三项 always-on 注入：`project.json` 身份信息 +
>   active WorkThread 进度 + 高优先级 open_threads；`timeline.jsonl`/`knowledge.md` 按设计文档
>   8.4 节定位为"按意图检索注入"，不在本 Stage always-on 范围内）、`cli/app.py`（注册新工具模块）。
> - 关键设计取舍：四个 JSON/JSONL 文件全部走原子写（tmp + `os.replace`），不经过 StateRepo——
>   定位是"观察性数据"，与 W1 的 `task_manifest.json` 一致；只有 `knowledge.md` 走
>   `StateRepo.apply()`（tier 固定 T1）。`relate_session_to_work_thread` 只做"关联到已有
>   active WorkThread"，不自动新建——避免启发式误判污染 `work_index.json`。
> - 横向加固 12.2（`environment_fingerprint`）与 14.1（`knowledge_index.json`）已按计划文档建议
>   提前在本 Stage 完成；12.2 目前只做"检测并报告"（`_maybe_ensure_project_meta` 里打印提醒），
>   未接入"自动降低 lesson/skill 置信度"的下游联动（设计文档原意如此，留待按需排期，避免
>   一次启动检查牵连读写 `memory.jsonl`/`skills/`）。
> - 测试：`tests/test_workdir_knowledge.py`（62）、`tests/test_workdir_knowledge_tools.py`（32）、
>   `tests/test_session_end_workdir_knowledge.py`（27）、
>   `tests/test_context_builder_workdir_knowledge.py`（14）、
>   `tests/test_agent_startup_project_meta.py`（7），共 142 条，全绿，无回归。

#### 4.1 `project.json`（项目身份证）
- 新增 `AgentPaths.workdir_project_meta()` 路径方法（命名对齐现有 `workdir_memory()`/
  `workdir_prompts_dir()` 的 `workdir_xxx` 惯例；本 Stage 4.2-4.5 的新文件同理分别命名为
  `workdir_timeline()`/`workdir_work_index()`/`workdir_open_threads()`/`workdir_knowledge_md()`）
- session 启动时若文件不存在则创建：`root_language`/`key_files` 可直接复用现有
  `ProjectScanner.scan()` 产出的 `ProjectSnapshot`（`languages`/`key_files`/`dependencies`
  字段已经就位）；但 `name`/`description`/`key_modules` 这几个语义性字段 `ProjectSnapshot`
  没有现成数据，需要新写（`name` 取目录名兜底即可，`description`/`key_modules` 可以留空或
  做一次轻量 LLM 摘要调用，不要为了"完全复用"而强行从 `ProjectSnapshot` 拼凑不存在的语义）；
  存在则更新 `last_active`/`total_sessions`
- **【横向加固机会】** 顺手把 12.2 节 `environment_fingerprint` 字段加进 `project.json`（同一个文件，
  同一次"session 启动时检查/更新"代码路径，增量成本接近零）——这是设计文档自己建议挂靠的位置
- **验证**：新项目首次启动后检查 `project.json` 生成且字段非空；连续跑两次 session 后 `total_sessions` 递增

#### 4.2 `timeline.jsonl`（session 时序骨架）
- SessionEnd hook（已在 Stage 1.3 接入 `trigger_session_end()`，与触发 lesson 反思是同一个方法）
  新增一条处理逻辑：追加一行精简记录（`sid`/`at`/`duration_min`/`theme`/`key_outcomes`/
  `task_count`/`status`）
- `theme`/`key_outcomes` 来源：**核实后发现不能直接复用 Stage 1.3 已有的反思 LLM 调用结果**——
  `_reflect_and_save_lessons()` 产出的是"问题诊断"维度的结构化字段（`trigger`/`root_cause`/
  `suggested_action`，回答"这次出了什么问题、怎么解决的"），与 `timeline.jsonl` 需要的"会话
  概览"维度（`theme`/`key_outcomes`，回答"这次做了什么方向、有什么产出"）是两种不同的反思
  目标，提示词也完全不同，不应该共用同一次 LLM 调用强行拼出两种结果。两种可选方案：
  ① 新增一次独立的轻量反思调用（成本：每个 session 多一次小 LLM 调用）；
  ② 改造现有反思 prompt，让一次调用同时返回两种结构（成本：prompt 复杂度上升，且两种目标
  混在一个调用里可能互相干扰输出质量）。建议选①，理由是 `theme`/`key_outcomes` 用途更轻量
  （只是时序骨架的一行摘要），值得用一次成本可控的独立小调用换取 prompt 清晰度，
  不与 lesson 反思的诊断目标混在一起
- **验证**：跑完一个 session 后检查 `timeline.jsonl` 追加了对应行，字段与 session 实际内容吻合

#### 4.3 `work_index.json`（WorkThread 聚合）—— 本 Stage 价值最高的一项
- 数据结构：`WorkThread`（`id`/`title`/`status`/`related_sessions`/`cumulative_progress`/
  `open_questions`/`next_suggested`/`related_goal_id`），按设计文档 8.2 节 schema
- **维护机制选择最简版本**（设计文档给了三条路径，本 Stage 先做"轻量自动 + 手动兜底"两条，
  evolution-agent 周期性扫描那条留给 Stage 8 的 Phase G，因为需要后台调度才有意义跑"周期性"）：
  - SessionEnd hook：若本次 session 与某个 `status=active` 的 WorkThread 有关（简单启发式：
    `related_sessions` 最近一条与本次 session 时间间隔 < N 天，或本次任务的 `task_manifest.goal`
    与某 WorkThread 的 `title` 语义相似度超阈值），追加 `related_sessions`，更新 `cumulative_progress`
  - 新增工具 `update_work_thread(thread_id, cumulative_progress, next_suggested, open_questions)`，
    供 agent 在长任务里主动维护（呼应 W1 `update_task_progress` 的"主动写入"原则）
- **验证**：连续两个相关 session 后检查同一个 WorkThread 是否正确累积了两条 `related_sessions`

#### 4.4 `open_threads.json`（跨 session 待处理线索池）
- 新增工具 `add_open_thread(title, type, priority, description, work_thread_ref)`
- SessionEnd hook 自动把 W1 `task_manifest.outcome.unresolved` 里的条目推进来（W1 已经产出这个字段，
  本 Stage 只需要"读取并转换格式"，无需改动 W1）
- **验证**：构造一个任务，`update_task_progress` 时标记 `unresolved` 含一条问题，session 结束后检查
  `open_threads.json` 是否出现对应条目

#### 4.5 `knowledge.md`（项目软知识，T1，走 `StateRepo.apply()`）
- 新增工具 `update_knowledge(section, content)`，内部调用 Stage 2 已完成的 `StateRepo.apply()`，
  tier 固定 T1（与 6.5 节 `skill_propose` 的 tier 选择保持一致的判断逻辑：内容性而非治理性变更）
- **验证**：调用工具后检查 `git log` 出现对应 commit，`knowledge.md` 内容正确追加

#### 4.6 context 注入接入（设计文档 8.4 节）
- `context_builder.py` 新增 Workdir 层注入：`project.json` 身份信息 always-on；`work_index` 里
  `status=active` 的 WorkThread 的 `cumulative_progress`/`next_suggested` always-on；
  `open_threads` 里 `priority=high` 的条目 always-on（限制最多 N 条，避免占用过多 context）
- **这是本 Stage 唯一"行为侧"改动**，必须验证注入确实生效，不能只验证文件写入正确（呼应"改造原则 3"）
- **验证**：人工构造一个含 active WorkThread 和高优先级 open_thread 的 `.agent/`，启动新 session，
  检查 `_build_system()` 产出的 system prompt 包含对应内容

**Stage 4 验证标准**：跑 3-5 个真实 session（覆盖"新项目首次启动"/"延续已有 WorkThread"/"产生
unresolved 问题"三种场景），人工核对 `.agent/` 下 5 个新文件内容与实际 session 内容一致，且新
session 启动时能在 system prompt 里看到上一次 session 的 WorkThread 进度提示。

---

### Stage 5（核心，约 3-4 人天）—— Phase W3：Global 知识层 ✅ 已完成

> 设计文档 8.3 节。`~/.agent/` 下新增四个文件。与 Stage 4 同构，但 scope 从单项目变为跨项目，
> 复杂度集中在"如何从多个 workdir 汇总"而非单文件写入本身。`AgentPaths` 新增方法统一命名为
> `global_self_profile()`/`global_projects_index()`/`global_cross_project_index()`/
> `global_activity_log()`，对齐现有 `global_memory()`/`global_skills_dir()` 的 `global_xxx` 惯例。

#### 5.1 `self_profile.json`（agent 自我模型）
- 这是设计文档里**两处重复设计**的同一个概念（7.2 节 `AgentSelfProfile` 与 8.3 节 `self_profile.json`
  是同一份数据的"设计稿"与"落地文件"两个视角，本 Stage 直接按 8.3 节 schema 实现，
  7.2 节作为补充说明）
- 首次创建时 `identity`/`self_assessment` 留空或给保守默认值；`operating_state`/`resource_budget`/
  `evolution_state` 由维护机制填充（见 5.5）
- **【横向加固机会】** `resource_budget.daily_token_budget` 这个字段本身先加进 schema，但"硬限制"
  逻辑（超预算后拒绝执行）属于 Phase H 7.5 节范畴，本 Stage 只做"记录"不做"仲裁"——
  仲裁逻辑没有 daemon 调度器无从挂载，过早做这部分等于无效代码
- **验证**：检查文件首次创建后 schema 完整，且字段语义与设计文档一致（尤其是 `autonomy_level`
  默认值必须是 `"passive"`，呼应 7.9 节"默认建议 passive 起步"）

#### 5.2 `projects_index.json`（workdir 注册表）
- session 启动时：若当前 workdir 不在索引里则注册（`first_seen`/`status="active"`）；若已存在则更新
  `last_active`/`total_sessions`
- `status="dormant"` 判定：定期（不需要等 Phase G 的调度器，可以简单做成"每次任何 session 启动时
  顺手检查一遍全部已注册项目，30 天无 `last_active` 更新则标记 dormant"，O(项目数) 量级，足够轻量
  不需要专门的后台任务）
- **验证**：在两个不同目录分别跑一次 session，检查 `projects_index.json` 出现两条记录且字段正确；
  手动改一条记录的 `last_active` 为 31 天前，下次任意 session 启动后检查其 `status` 变为 `dormant`

#### 5.3 `activity_log.jsonl`（全局活动时序）
- SessionEnd hook 追加一行（`at`/`project_id`/`sid`/`theme`/`duration_min`），与 Stage 4.2
  `timeline.jsonl` 在同一处代码路径里一起写，避免两次遍历 session 数据
- **验证**：与 4.2 联合验证，跑一次 session 后检查 `timeline.jsonl`（workdir 层）和 `activity_log.jsonl`
  （global 层）同时正确追加

#### 5.4 `cross_project_index.json`（跨项目模式与能力图谱）—— 本 Stage 复杂度最高的一项
- **依赖 Stage 4.3 的 `work_index.json` 已经在多个 workdir 里积累了数据**，否则没有"跨项目重复模式"
  可以汇总——这是本 Stage 必须排在 Stage 4 之后的直接原因
- 第一版只做"读取 + 聚合 + 落盘"，不做"自动触发 skill 晋升提案"（那一步需要 evolution-agent
  的周期性扫描，属于 Phase G 范畴，留给 Stage 8）：扫描各 workdir 的 `memory.jsonl`，按
  lesson 的语义/`trigger` 相似度聚类，写入 `cross_project_patterns`，计算 `observed_in_projects`/
  `occurrence_count`/`confidence`
- **验证**：构造两个 workdir 各产生一条相似 lesson（如都触发了 "bash rm 高危" 规则），手动运行
  聚合逻辑（先不接调度器），检查 `cross_project_index.json` 正确识别为同一个 `cross_project_pattern`
  且 `observed_in_projects` 计数为 2

#### 5.5 维护机制与 context 注入
- SessionEnd hook 部分（轻量，无 LLM）：本 Stage 完整实现（更新 `operating_state`、追加
  `activity_log`、更新 `projects_index`）
- evolution-agent 周期性扫描部分（`cross_project_index` 的自动更新 + skill 晋升提案触发）：
  **本 Stage 只实现"扫描聚合"函数本身，不接调度触发**，触发时机留给 Stage 8（Phase G）——
  这是"二、依赖关系再核查"里 6.5 节必须等 W3 的具体落点
- `context_builder.py` 新增 Global 层注入（8.4 节表格）：`self_profile.self_assessment` always-on
  精简注入；`evolution_state.pending_evolve_branches` always-on；workdir 变化时注入
  `projects_index`+`activity_log` 最近几条
- **验证**：与 Stage 4.6 同样的方式验证 context 注入生效

**Stage 5 验证标准**：在至少两个不同项目目录里各跑若干 session，人工核对 `~/.agent/` 下四个新文件
正确反映跨项目状态，且 `cross_project_index.json` 能正确识别出至少一组跨项目重复模式。

> **完成记录**：5.1-5.5 全部落地。核心实现在 `perception/global_knowledge.py`（数据模型 +
> 读写 + `scan_cross_project_patterns`/`merge_cross_project_patterns` 跨项目聚合）；
> `storage/paths.py` 新增 `global_self_profile`/`global_projects_index`/
> `global_cross_project_index`/`global_activity_log` 四个路径属性；`config/models.py` 新增
> `GlobalKnowledgeConfig`；`agent.py` 接入 `_maybe_register_global_project`（session 启动注册 +
> dormant 巡检）与 `_update_workdir_knowledge_on_session_end` 尾部追加（activity_log + self_profile
> 更新，复用同一次 theme/duration 计算）、`_reflect_and_save_lessons` 事件驱动更新
> `lifetime_lessons_generated`；`context_builder.py` 新增 `_build_global_knowledge_block`
> （self_assessment + pending_evolve_branches always-on，projects_index+activity_log 仅
> workdir 变化时注入）。`resource_budget.used_today` 按 UTC 日历日做了真实的跨日重置（而非占位）。
> 测试覆盖：`tests/test_global_knowledge.py`（46，纯函数）+
> `tests/test_global_knowledge_integration.py`（14，agent.py 接入）+
> `tests/test_context_builder_global_knowledge.py`（14，context 注入）= 74 个新增测试，
> 全部通过；同时补充 `tests/conftest.py` 全局隔离 `Path.home()`，修复了本 Stage 暴露出的
> "测试构造真实 Agent 会污染运行测试机器的 `~/.agent/`"问题（Stage 1-4 即已存在，本 Stage
> 一并修复）。手动场景验证：两个不同 workdir 各跑一次 session 后 `projects_index.json`/
> `self_profile.json`/`activity_log.jsonl` 均正确反映跨项目状态；两个 workdir 各产生一条
> "bash rm -rf 删除重要文件"风险 lesson 后，`scan_cross_project_patterns` 正确识别为同一个
> 跨项目模式（`observed_in_projects` 计数为 2，`global_skill_candidate=True`）。
> 5.4 节"自动触发 skill 晋升提案"按计划留给 Stage 8（Phase G），本 Stage 只实现扫描聚合函数本身。

---

### Stage 6（核心，约 2-3 人天）—— 第 9 章：观察性 ✅ 已完成

> 设计文档原话"是所有量化判断的数据基础，越早建越省事，越晚建欠债越多"。本文档把它排在
> W2/W3 之后而非最前面，理由是：第 9 章的产出（`traces.jsonl`/`/diagnostics`）主要服务于
> Phase G 的剪枝判断和 Phase H 的自我监控，在那两者明确要做之前提前建好，能保证 Stage 8/9
> 启动时数据已经有积累，不需要"从零开始攒基线"。

#### 6.1 时序性能追踪（Tracing，11.1 节）
- 在 `run_turn()` 关键路径（`_build_system()`/`_call_llm()`/`_execute_tools()`/`_inject_reminder()`）
  打点，追加到 `session_dir/traces.jsonl`
- `context_breakdown` 字段（`system_base`/`skill_context`/`memory_inject`/`history` 各占多少 token）
  是 6.4 节剪枝判断的直接数据来源——这是本 Stage 与 Stage 8（Phase G 剪枝）的关键接口，
  必须在 Stage 8 启动前就绪
- **【开放问题 7 的回应】** 设计文档第 18 节开放问题 7 问"traces 存储成本如何控制"，本 Stage
  直接给出答案而非留作悬念：复用 Stage 1 已有的 history 压缩思路，`traces.jsonl` 按 session
  生命周期保留（session 结束即可归档或删除明细，只保留 `/diagnostics` 已聚合的统计摘要），
  不单独维护"保留最近 N 天"的全局清理任务
- **验证**：跑一个含多轮工具调用的 session，检查 `traces.jsonl` 每个关键节点都有对应记录，
  `context_breakdown` 各分项之和与实际 prompt token 数吻合（容差 5%）

#### 6.2 系统健康检查 `/diagnostics`（11.2 节）
- 新增 API 端点，聚合 `traces.jsonl`（性能）+ `memory.jsonl`（记忆统计）+ skill 激活状态 +
  Stage 4/5 的 `pending_evolve_branches`/`open_threads_high_priority` 等字段
- 这是 W3 `self_profile.json` 的实时数据来源之一（设计文档原话），但**不反向依赖** W3——
  `/diagnostics` 自己直接聚合底层数据，W3 的 `evolution_state` 在 SessionEnd 时单独同步更新，
  两者是平行的两条写入路径，不要做成"`/diagnostics` 读 `self_profile.json` 再读回真实数据"
  的间接链路
- **验证**：调用端点检查 7 个字段分组（`performance`/`memory`/`skills`/`evolution`/`anomaly_flags`）
  均返回与当前 `.agent/` 实际状态吻合的数据

#### 6.3 异常行为检测（11.3 节）
- 从 `activity_log.jsonl`（Stage 5.3 产出）推导基线（平均工具调用次数/token 消耗范围），
  超出基线 3 倍标准差时写入 `anomaly_flags`
- **依赖 Stage 5.3 已有数据积累**，建议至少有 10-20 条 `activity_log` 记录后才能算出有意义的基线，
  否则方差计算在小样本下不稳定——这是本节排在 Stage 5 之后而非更早的直接原因
- **验证**：人工构造一次"异常"session（如循环调用同一工具 50 次），检查 `anomaly_flags`
  正确触发对应告警类型

#### 6.4 工具调用因果链（11.4 节）
- `events.jsonl` 每条记录新增 `turn_id`/`sequence_in_turn`/`error_category`/`resolves_seq`
- `error_category` 枚举判定：现有 `lesson_rules.py` 的 `is_tool_error()` 只做"是否为错误"的
  布尔判断，**没有**现成的细分类别映射，但其内部已经维护了一份异常类名的正则模式
  （`PermissionError`/`FileNotFoundError`/`TimeoutError` 等），可以复用这份正则模式本身，
  在此基础上新写一层"正则命中哪个类名 → 映射到哪个 `error_category`"的分类函数，
  不是直接调用现成的分类结果（避免高估复用程度）
- **这是横向加固任务池里 13.2 节"错误分类驱动恢复"的前置数据**，本节做完后 13.2 才有
  `error_category` 可用，建议在 Stage 7 任务池里优先捡这一项（成本低、价值高）
- **验证**：构造一次"失败后重试成功"的工具调用序列，检查后一条记录的 `resolves_seq` 正确
  指向前一条失败记录

**Stage 6 验证标准**：`/diagnostics` 端点可用且数据真实；跑一组包含正常和异常模式的 session，
`anomaly_flags` 正确区分两者；`events.jsonl` 的因果链字段在 Phase B 现有的反思 LLM 调用里
可以被正确读取（验证"6.4 是 Phase B 反思质量飞跃的关键输入"这一设计意图，而非只是字段存在）。

> **完成记录**（2026-06）：
>
> - **6.1 traces.jsonl**：`SessionTracer` + `span()` context manager，在 `_agentic_loop()` 的
>   `call_llm` / `execute_tools` / `build_system` 三处打点，含 `context_breakdown` 字段（`system_base` / `history` / `total`）。
>
> - **6.2 /diagnostics**：`GET /v1/diagnostics` 端点，五个分组：`performance`（traces 聚合）/
>   `memory`（条目统计）/ `skills`（激活列表）/ `evolution`（演化状态）/ `anomaly_flags`（异常标记）。
>
> - **6.3 异常检测**：`detect_anomalies()` k-σ 算法，检测 `tool_call_spike` / `token_spike` /
>   `session_duration_spike`，依赖 `activity_log.jsonl` 中的 `session_metrics` 行（每次
>   `trigger_session_end()` 时由 `_run_observability_on_session_end()` 写入）。
>
> - **6.4 因果链**：`classify_error()` 14 种 error_category 分类（基于正则规则，复用
>   `lesson_rules.py` 的异常类名模式）；`traces.jsonl` tool_call 记录含 `sequence_in_turn` /
>   `error_category` / `resolves_seq`；`_execute_tools()` 尾部写入因果链记录。
>
> - **核心模块**：`src/mini_agent/perception/observability.py` + `src/mini_agent/api/routes.py`
>   + `src/mini_agent/config/models.py`（`ObservabilityConfig`）。
>
> - **测试**：`tests/test_observability.py` 33 个测试，全绿。
>
> - **config**：`ObservabilityConfig`（`enabled` / `tracing_enabled` / `anomaly_k_sigma` /
>   `anomaly_min_samples`）接入 `AppConfig`，便捷属性 `cfg.observability_enabled` / `cfg.tracing_enabled`。

---

### Stage 7（机会性任务池，不单独估算人天）—— 横向加固清单 ✅ 本批已完成（13.2+15.3+15.2 已落地；其余见表格注）

> 延续设计文档"可在任意阶段穿插"的定位。下表是第 10/11/12/13/14/15 章里**未被 Stage 0-6
> 顺手覆盖**的全部条目，按"挂靠点"分组——意思是"这一项最适合在改动哪个模块时顺手做"，
> 而不是要求专门排期。**兜底规则**：若某条目到 Stage 8 启动前仍未找到机会挂靠，视为
> Stage 8 启动的前置条件之一统一补做（因为 Phase G 的剪枝/晋升/调度逻辑会直接用到其中
> 多项，如 14.2 的冲突检测、13.2 的降级链）。

| 条目 | 挂靠点（建议在改动以下模块时顺手做） | 价值/成本备注 |
|---|---|---|
| 12.1 `FILE_CHANGE_EFFECTS` 映射表 | `file_watcher.py`（Stage 8 的 6.4 节"剪枝判断 skill 是否实际被使用"会用到缓存失效信号，建议挂在 Stage 8 启动前） | 纯规则表，无 LLM，成本很低 |
| 12.2 环境漂移检测 | Stage 4.1 `project.json`（已建议在该处顺手加 `environment_fingerprint` 字段，本条目是消费这个字段的逻辑，建议同批做完，不要分两次改同一个文件） | 中等价值：防止"环境升级后旧经验变噪音" |
| 12.3 inbound webhook | `api/routes.py`（Phase H 的 7.4 节 tick 循环里 `external_event` 优先级介于用户和自主之间，建议在 Stage 9 决定启动 H 时一并做，过早做没有消费方） | 留到 Stage 9 |
| 13.1 能力匹配调度 | `agent_profiles.py` + Stage 5.4 的 workdir capability_map（先有能力地图数据，匹配调度才有意义，建议挂在 Stage 8 的 6.6 节之后） | 依赖 6.6，不要提前 |
| 13.2 SubAgent 降级重试链 | `TaskManager`（任何时候都可独立做，不依赖本批其他内容，是任务池里**最适合优先捡**的一项——Phase H 自主运行时"没有用户纠正"的场景必须有这个兜底，建议不晚于 Stage 9 启动前完成） | 优先级：高 |
| 13.3 SubAgent 间中间结果流 | `orchestrator/task.py` 的 `task_artifacts/`（独立功能，无强依赖，时机灵活） | 优先级：中 |
| 14.1 `knowledge_index.json` | Stage 4.5 `update_knowledge()` 工具（同一处改动，写 Markdown 时顺手生成索引，建议与 4.5 同批做） | 建议提前到 Stage 4 一并完成 |
| 14.2 Skill 依赖与冲突图 | `skills/__init__.py`（`SkillLoader.exclude()` 刚在 Stage 3.2 新增，这里是该模块的下一块自然延伸——`activation_conditions`/`conflicts_with` 在 `auto_activate()`/`activate()` 里加约束检查） | 优先级：中高，模块刚被改动过，上下文新鲜 |
| 14.3 知识可信度传递 | 同上，`SkillLoader` 注入 context 时按 `confidence_score` 调整语气；**直接回应设计文档第 18 节开放问题 9**"通货膨胀"问题——建议实现时同时加入"反例计数"（人工纠正/revert record 出现时大幅降低 confidence，而不只是正向计数），不要只做加分不做减分 | 优先级：中高 |
| 15.1 元认知 Checkpoint | `agent.py` 的 `run_turn()`（独立功能，与本批其他内容无强依赖） | 优先级：中，价值随 Phase H 临近上升（H 没有用户实时纠正，更需要自我检查） |
| 15.2 错误分类驱动恢复 | `reminders/` 体系，**依赖 Stage 6.4 的 `error_category`**（已在 Stage 6 完成数据基础，本条目是直接消费方，建议紧跟 Stage 6 之后做） | 依赖已就位，建议优先捡 |
| 15.3 任务降级策略 `DemotionOptions` | `TaskManager`，与 13.2 降级重试链是同一处代码的两个互补视角（13.2 是"换 profile/换 SubAgent"，15.3 是"换目标范围"），建议合并到同一次改动里一起做 | 与 13.2 合并实施 |
| 16.2 隐式反馈捕捉 | `api/bridge.py`（Web demo 编辑事件监听），独立功能但信噪比问题（设计文档开放问题 8）未解决前不建议投入，**建议本条目暂缓**，等 Stage 6 的异常检测基线建立后用同一套统计思路设计置信度校准，而不是直接照搬文档给的 `confidence=0.2-0.3` 拍脑袋数字 | 建议暂缓，理由见左 |
| 16.3 澄清优先分支 | `orchestrator/plan.py` 的 `PlanTaskType` 扩展，独立功能 | 优先级：中 |
| 17.2 Prompt 工程版本化 | 依赖 Phase H 7.10 节 Experiment 机制（控制变量实验），**只有 Experiment 机制就位才有意义跑"prompt A/B 对比"**，建议留到 Stage 9 的 7.10 节子项之后 | 留到 Stage 9 |

**任务池使用方式**：本 Stage 不需要在甘特图里占据独立时间块；每当 Stage 4-9 的某次改动恰好
触及表中"挂靠点"列出的模块时，检查这张表是否有可以顺手捎带的条目。表格本身应随每个
Stage 完成后回顾更新（已完成的条目标注完成日期，移出待办状态）。

> **完成记录**（2026-06）：
>
> - **13.2 + 15.3（已完成）**：`TaskManager._try_demotion()` + `_resubmit_demoted()`，两阶段降级：
>   profile fallback（按 `Task.fallback_profiles` 切换）→ scope demotion（追加 `Task.demotion_scope`
>   约束），复用 task_id，下次 tick 自动调度。
>
> - **15.2（已完成）**：`error_category` 精确路由已在 `reminders/matcher.py` 接入，reminder 的
>   `condition.error_category` 字段可精确匹配 14 种分类，无需正则。
>
> - **14.1（已完成，Stage 4 顺手）**：`knowledge_index.json` + `upsert_knowledge_index_entry()`。
>
> - **14.2（已完成，Stage 3.2 顺手）**：`SkillLoader.activate()` 里 `conflicts_with` + `activation_conditions` 检查。
>
> - **14.3（已完成，Stage 3.2 顺手）**：`confidence_score` 字段注入 context 时调整语气。
>
> - **12.2（已完成，Stage 4 顺手）**：`detect_environment_drift()` + `_maybe_ensure_project_meta()` 打印漂移警告。
>
> - **12.1 / 12.3 / 13.1 / 13.3 / 15.1 / 16.2 / 16.3 / 17.2**：按计划表的建议延后或暂缓，
>   见表格各行的"挂靠点"说明。

---

### Stage 8（核心，约 4-6 人天）—— Phase G：后台循环 ✅ 已完成

> 设计文档 6.1/6.4/6.5/6.6/6.7 节（6.1 由 Stage 3.1 他人负责，本 Stage 不重复）。
> 前提：Stage 4（W2）必须完成；Stage 5（W3）的 5.4 节"扫描聚合函数"必须完成
> （但 Stage 5 本身的触发时机正是本 Stage 要补的）；Stage 6（观察性）的 `context_breakdown`
> 必须完成（6.4 剪枝判断的数据来源）。

#### 8.1 调度骨架：最小可用的周期性任务触发器
- **不是** Phase H 7.4 节完整的 `AutonomousLoop`（那个依赖 `InputQueue`/daemon 化，是 Stage 9 范畴）——
  本 Stage 先做一个不需要常驻进程的最小版本：CLI 新增 `/evolve review` 一类的手动触发命令
  （前文档 Stage 3.1 已经规划了类似的"`/evolve review` 手动命令"用于 skill_propose 触发，
  本 Stage 复用同一套触发器框架，新增几个不同的扫描任务挂上去，而非另起一套机制）
- 同时支持"基于时间的简单判定"（不需要 cron，例如"上次跑 consolidation 距今超过 24 小时"
  这种条件直接记录在对应数据文件的 `last_xxx_at` 字段里，每次任意 session 启动时检查一次，
  够用且不需要额外的调度进程）
- **验证**：手动触发 `/evolve review`，检查能正确路由到下面 8.2-8.5 的各扫描任务

#### 8.2 6.4 节：剪枝、去重、冲突检测
- 输入：Stage 6.1 的 `traces.jsonl` `context_breakdown.skill_context` 占比（成本）+
  `SkillUsageTracker`（已存在，使用频率）
- 判定规则：成本高但近期未被使用的 skill 标记为"剪枝候选"，输出到晨报式的提示（不自动删除，
  人工确认后才真正下线——遵循前文档"T3/T1 改动需要可逆与留痕"的一贯精神）
- 冲突检测部分依赖 Stage 7 任务池里 14.2 节的 `conflicts_with` 字段，若该条目未提前做，
  本节冲突检测先跳过，只做剪枝/去重
- **验证**：构造一个高 token 成本但从未被 `SkillUsageDetector` 命中的 skill，运行扫描后
  检查其出现在剪枝候选列表

#### 8.3 6.6 节：能力地图（Capability Map）—— 激活预留的 `entry_type`
- 终于消费掉 `MemoryEntry.entry_type="capability_map"` 这个早就存在的枚举值——扫描
  `events.jsonl`（Stage 6.4 因果链）+ `task_manifest.outcome` 按任务类型聚合成功率，
  写入一条 `entry_type="capability_map"` 的 memory 条目，`confidence_by_domain` 结构
  与设计文档 7.2/8.3 节一致
- 先做 workdir scope（写入项目级 `memory.jsonl`），global scope 汇总（写入 `self_profile.json`）
  留给本节之后、Stage 5.4 已就位的跨项目聚合逻辑
- **验证**：跑若干同类型任务（如多次 Python 重构任务，刻意含成功和失败），运行扫描后
  检查生成的 capability_map 条目里对应 domain 的置信度数值合理（成功率高则置信度高）

#### 8.4 6.5 节：Scope 晋升（workdir → global）
- 依赖 Stage 5.4 `cross_project_index.json` 已有跨项目模式数据
- 晋升判据：`observed_in_projects >= 2` 且 `confidence` 超阈值（具体数值留作可配置参数，
  对应设计文档第 18 节开放问题 1 的"T1 自动合并边界"，建议默认偏保守，先观察实际触发频率
  再调整，不要一开始就调得很激进）
- 晋升动作：调用 Stage 3.1（他人负责）已实现的 `skill_propose`，tier 固定 T1，走 evolve 分支——
  **本节不重新实现 skill 写入逻辑，只负责"判定该不该晋升 + 调用已有工具"**
- **验证**：构造跨两个项目的重复模式（复用 Stage 5.4 的验证场景），检查触发了一次
  `skill_propose` 调用且 evolve 分支正确创建

#### 8.5 6.7 节：演化节奏治理
- 这是对 6.4/6.6/8.4 产出的"提案频率"做限流，避免审核疲劳（设计文档原话）：
  同一类型的提案（剪枝建议/晋升提案）设置最小间隔（如"同一个 skill 7 天内只提一次剪枝建议"）
- **直接回应设计文档第 18 节开放问题 1**："是否需要观察期"——本节实现"T1 自动合并前先观察
  N 个 session"的等待窗口，把开放问题落成具体参数（建议默认 N=5，可配置）
- **验证**：连续两次触发同一个剪枝建议（间隔小于限流窗口），检查第二次被正确抑制

**Stage 8 验证标准**：完整跑一轮"`/evolve review` 手动触发 → 剪枝候选生成 → capability_map
更新 → 跨项目模式检测 → 晋升提案（若条件满足）"的链路，每一步产出可在 `/diagnostics`
（Stage 6.2）里看到对应的统计变化。

> **完成记录**（2026-06）：
>
> - **8.1 调度骨架**：采用"时间门控"方案（无常驻进程），`phase_g_rhythm.json` 的 `_last_run_at`
>   字段替代 cron，`should_run_phase_g()` 在每次 `trigger_session_end()` 时检查（24h 间隔）。
>   手动触发入口：`/evolve phase-g [--force] [--dry-run]`。
>
> - **8.2 剪枝候选**：`prune_skills()` 实现，规则 A（高 token 成本 + 未使用）和规则 B（冲突检测），
>   输出 `PruneCandidate` 列表，不自动执行任何下线操作。
>
> - **8.3 能力地图**：`build_capability_map()` 扫描 `tasks/*/manifest.json`，按
>   `_infer_domain()` 规则式推断任务类型，聚合成功率，写入 `entry_type="capability_map"` 的
>   memory 条目。Global scope 汇总（写入 `self_profile.json`）留待数据积累后扩展。
>
> - **8.4 Scope 晋升**：`check_scope_promotion()` 读 `cross_project_index.json`，
>   判据：`observed_in_projects ≥ 2` 且 `confidence ≥ 0.70` 且 `global_skill_candidate=true`。
>   当前只输出候选列表（`PromotionCandidate`），不直接调用 `skill_propose`（人工确认后由
>   `/evolve review` 处理）。
>
> - **8.5 节奏治理**：`rhythm_is_allowed()` / `record_proposal()`，7 天冷却期，`phase_g_rhythm.json`
>   持久化，独立于 8.2/8.3/8.4 的逻辑，可以为任意 `(proposal_type, key)` 对做限流。
>
> - **核心模块**：`src/mini_agent/evolution/phase_g.py`（`run_phase_g()` 整体入口）
>   + `src/mini_agent/cli/commands/evolve.py`（`_handle_phase_g()` + `_print_phase_g_report()`）
>   + `agent.py → _maybe_run_phase_g()`（SessionEnd 时间门控接入点）。
>
> - **测试**：`tests/test_phase_g.py` 35 个测试，全绿。
>
> - **遗留**：8.1 节"调度器与 8.3/8.4 产出互通"中的"`/diagnostics` 反映每一步统计变化"
>   已通过 `performance.tool_error_rate` + `evolution.pending_evolve_branches` 两个分组覆盖；
>   `anomaly_flags` 需要 10+ 条 `session_metrics` 历史积累才有效（小样本期无误报是预期行为）。

---

### Stage 9（决策点，非常规人天估算）—— Phase H：自主运行时

> 设计文档原话"是性质上的跃迁"。本文档遵循"改造原则 5"：**这不是一个排期 Stage，是一个
> 决策点**。以下内容是"若决定启动，应该怎么做"的预案，不代表 Stage 8 完成后自动推进到这里。

#### 9.0 启动前置检查清单（建议作为决策会议的议程，而非工程任务）
- [ ] Stage 4-8 全部完成并稳定运行至少一段观察期（具体时长留给决策时判断，设计文档没有给
  硬性数字，本文档不替代这个判断）
- [ ] 用户/团队明确确认产品定位允许"持续存在、有自己议程的 agent"——这是设计文档 7.9 节
  强调的信任模型问题，不是技术就位与否的问题
- [ ] Stage 7 任务池里 13.2（降级重试链）+ 15.3（任务降级）已完成——这两项是"没有用户在场
  纠正时的兜底"，理论上是 Phase H 启动的硬性技术前提，不属于"可选的横向加固"
- [ ] 明确 7.9 节 `autonomy_level` 默认从 `passive` 起步，且团队认可"逐档开放、可随时降级"
  的升级路径

#### 9.1 若决定启动，建议的内部顺序
1. `AgentSelfProfile`/`autonomy_level` 字段先落地（在 Stage 5.1 `self_profile.json` 基础上
   补充 `passive` 档位的语义——此时只是声明字段，daemon 尚未读取它做任何决策）
2. `Goal Backlog`（`.agent/goals.json`）数据结构 + 与 Stage 4.3 `work_index.json` 的
   WorkThread 互通（设计文档原话"WorkThread 是 Goal Backlog 里 Objective 节点的自然前身"）
3. `AutonomousLoop` 调度器：先只接 Stage 8 已有的周期性任务（剪枝/能力地图/演化节奏检查），
   `autonomy_level=passive` 时不创建任何新 Goal/Objective，只验证调度器本身能正确触发
   已有任务，不引入新的自主行为
4. 资源仲裁（7.5 节）+ 主动汇报（7.6 节晨报）：在调度器开始处理真正的自主任务之前必须先有
   这两项，否则"自主任务和用户冲突"以及"用户不知道 agent 做了什么"两个风险点没有兜底
5. 安全网 `initiator` 字段（7.7 节）：`StateRepo.apply()` 加参数，自主发起的改动 tier 上浮，
   这一步必须在 `autonomy_level` 从 `passive` 升到 `maintenance` 之前完成
6. 升到 `maintenance`：启用真正的周期性任务自主触发（不再需要 `/evolve review` 手动调用）
7. 探索机制（7.10 节 Experiment）：在 `maintenance` 档稳定后再做，因为依赖独立核算的探索预算，
   预算仲裁机制需要 7.5 节已经跑顺
8. 升到 `autonomous`（软目标 derive）：是否要做这一档，留给届时重新评估——设计文档本身也在
   第 18 节把"默认与切换流程"列为开放问题，本文档不提前给出答案

> **细化方案**：以上八步的具体实现拆解（数据结构、接口改动、与现有代码的接口、验证标准、
> 风险与边界）见专门文档 `next_doc/self_evolution_stage9_plan.md`，该文档逐项核对了
> `api/bridge.py`/`orchestrator/task_manager.py`/`evolution/state_repo.py` 等现有源码，
> 并指出了若干本节描述与实际代码状态的偏差（如 `autonomy_level` 字段实际尚未落地）。
> 本节内容保持不变，作为决策会议的高层议程；具体实施请以细化文档为准。

**本 Stage 不设验证标准**——验证标准应该在决策启动时，根据当时团队对"自主行为边界"的
具体要求重新制定，本文档只负责确保技术前提（9.0 清单）就位。

---

## 五、依赖关系图（承接前文档第四节）

```
（前文档）Stage 0-3 ✅ 已完成 / 进行中
  Stage 3.1（他人负责，skill_propose）
  Stage 3.2 ✅ 已完成（eval 反馈环）
  Stage 3.3 ✅ 已完成（SubAgent 信息继承）
        │
        └─→ Stage 4（W2：Workdir 知识层）✅ 已完成
              │  ├─ 顺带：14.1 knowledge_index ✅、12.2 environment_fingerprint ✅
              │
              └─→ Stage 5（W3：Global 知识层，5.4 依赖 4.3 已有跨 session 数据）✅ 已完成
                    │
                    └─→ Stage 6（第 9 章：观察性）✅ 已完成
                          │  ├─ 顺带：Stage 7 中的 15.2（error_category Reminder 路由）
                          │
                          └─→ Stage 7（横向加固任务池）✅ 本批已完成
                                │  ├─ 13.2 SubAgent 降级重试链（TaskManager）✅
                                │  └─ 15.3 任务降级策略（与 13.2 合并实施）✅
                                │
                                └─→ Stage 8（Phase G：后台循环）✅ 已完成
                                      ├─ 8.1 时间门控调度（phase_g_rhythm.json）
                                      ├─ 8.2 剪枝候选（prune_skills）
                                      ├─ 8.3 能力地图（build_capability_map）
                                      ├─ 8.4 Scope 晋升（check_scope_promotion）
                                      └─ 8.5 节奏治理（rhythm_is_allowed）
                                            │
                                            └─→ Stage 9（Phase H：自主运行时）[决策点，未启动]
                    │
                    └─→ Stage 6（观察性：tracing/diagnostics/异常检测/因果链）
                          │  （6.3 异常检测依赖 Stage 5.3 activity_log 已有数据积累）
                          │
                          ├─→ Stage 7（横向加固任务池，与 Stage 4-9 穿插，无独立时间块）
                          │
                          └─→ Stage 8（Phase G：剪枝/能力地图/Scope晋升/节奏治理）
                                │  （8.4 依赖 5.4；8.2 依赖 6.1；8.3 依赖 6.4）
                                │  （8.1 复用 3.1 的 /evolve review 触发器框架）
                                │
                                └─→ Stage 9（Phase H，决策点，非自动推进）
                                      （9.0 前置清单要求 Stage 7 的 13.2/15.3 已完成）
```

---

## 六、时间与人力估算

| Stage | 工作量估计 | 并行度 | 状态 |
|---|---|---|---|
| Stage 4（W2） | 3-4 人天 | 4.1-4.5 内部有顺序（4.6 依赖前面全部），基本单线 | ✅ 已完成 |
| Stage 5（W3） | 3-4 人天 | 5.1-5.3 可与 5.4 部分并行，5.5 需等前面全部 | ✅ 已完成 |
| Stage 6（观察性） | 2-3 人天 | 6.1/6.2/6.4 可并行，6.3 需等 Stage 5.3 有数据积累 | 待开始（依赖的 Stage 5 已完成，可以开始） |
| Stage 7（横向加固任务池） | 不单独计入总量，分摊进 Stage 4-9 各自的改动成本 | 机会性，无固定并行度 | 持续滚动 |
| Stage 8（Phase G） | 4-6 人天 | 8.2/8.3 可并行，8.4/8.5 需等前面 | 待开始（依赖 Stage 4/5/6） |
| Stage 9（Phase H） | 不估算（决策点，启动后的具体人天留给届时按 9.1 八个子项重新估算） | 内部严格按 9.1 顺序串行 | 决策待定 |

不含 Stage 9（性质是决策点，不是常规工作量）和 Stage 7（已分摊），**Stage 4-6+8 总计约
12-17 人天**，可以把"知识层补全 + 观察性 + 后台循环"这条主线跑通，建立起设计文档第 17 节
依赖图里 W2/W3 → 9 → 10 → G 这一段的完整闭环。

---

## 七、单人执行时的串行顺序建议

若实际只有单人执行，无法并行，建议严格按以下顺序推进（编号即推荐顺序，Stage 7 穿插在
对应挂靠点出现时插入，不单独占据顺序位置）：

1. Stage 4：4.1 → 4.2 → 4.3 → 4.4 → 4.5（顺带 7 的 14.1）→ 4.6
2. Stage 5：5.1 → 5.2 → 5.3 → 5.4 → 5.5
3. Stage 6：6.1 → 6.4 → 6.2 → 6.3
4. Stage 8：8.1 → 8.2 → 8.3 → 8.4 → 8.5
5. **决策点 →** 是否启动 Stage 9，按 9.0 清单评估

---

## 八、开放问题继承与更新

前文档（设计文档原文）第 18 节开放问题 1-9 全部继承，本文档在以下几条给出了具体的落点
（不是给出最终答案，是给出"在哪个 Stage 用什么方式先给一版可调参数，而非无限期搁置"）：

- **问题 1（T1 自动合并边界/观察期）** → Stage 8.5（演化节奏治理）给出默认 N=5 的可配置观察期
- **问题 6（探索预算与冷却期校准）** → 留给 Stage 9.1 第 7 步，启动 Experiment 机制时一并校准，
  不在 Stage 4-8 阶段提前决定（此时探索机制本身还未启用，校准没有实际数据支撑）
- **问题 7（traces 存储成本）** → Stage 6.1 给出具体方案：按 session 生命周期保留明细，
  长期只留聚合摘要
- **问题 9（知识可信度通货膨胀）** → Stage 7 任务池 14.3 条目建议同时实现"反例计数"机制，
  不只做正向累积

其余开放问题（2/3/4/5/8）本文档不强行给出落点——它们分别涉及"LLM 自我验证的认知盲区"、
"治理流程本身该不该由人主导"、"资源成本控制"、"自主性升级的产品决策"、"隐式反馈信噪比"，
性质上更接近需要团队讨论而非工程排期就能解决的问题，继续保留为开放问题，留给 Stage 9
决策点前后视情况讨论。
