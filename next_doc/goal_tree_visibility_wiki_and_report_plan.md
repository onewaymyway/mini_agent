# 目标树可视化、产出 Wiki 化与汇总报告改进方案

> 状态：**Stage 1（树级汇总报告，纯只读）/ Stage 2（节点详情页，纯只读）/
> Stage 3（目标产出 Wiki 落盘镜像）/ Stage 4（反馈闭环）已实施**，
> Stage 5（可选，视使用效果再定）未实施。
> Stage 1 实现见 `src/mini_agent/perception/goal_tree_report.py`
> （`build_goal_tree_report()`）、CLI `/agent goals report [root_id]`
> （`cli/commands/goals.py::_cmd_report()`）、REST
> `GET /v1/goals/tree_report`（`api/routes.py::get_goal_tree_report()`）、
> `tests/test_goal_tree_report.py`。
> Stage 2 实现见 `src/mini_agent/perception/goal_node_page.py`
> （`build_goal_node_page()`，复用 `cycle_diagnostics`/`output_workspace`/
> `goal_tree_report.collect_pending_items_for_node()`，不重复实现）、CLI
> `/agent goals show <id>`（`_cmd_show()`）、REST
> `GET /v1/goals/{goal_id}/page`（`get_goal_node_page()`）、
> `tests/test_goal_node_page.py`。
> Stage 3 实现见 `src/mini_agent/evolution/goal_wiki.py`
> （`render_goal_wiki_page()` 渲染单节点 Markdown 落盘、
> `build_goal_wiki_tree()` 批量遍历子树，复用 `goal_node_page.
> build_goal_node_page()`/`goal_tree_report._collect_subtree()`，不重复
> 实现）、CLI `/agent goals wiki build [root_id]`（`_cmd_wiki_build()`）、
> REST `POST /v1/goals/wiki/build`（`build_goal_wiki()`）、`goal_cron_
> bridge.reap_finished_cycles()` 里挂了单节点自动刷新（阶段收敛为
> running/tidy 时触发，不递归整棵子树，保持每次 tick 开销可控）、
> `tests/test_goal_wiki.py`。通用 wiki 关联链接（§2.2 最后一点）按 §6
> 建议暂不实现。看板集成（三个 Stage 都涉及的折叠区/详情面板/wiki 静态
> 文件预览）仍未做，见 §6。
> Stage 4 实现见 `src/mini_agent/perception/goal_backlog.py`
> （`add_user_feedback()` 加 `status`/`about` 字段、`mark_feedback_
> addressed()`/`_mark_feedback_addressed_on_node()`）、
> `src/mini_agent/perception/cycle_tuning.py`（`confirm_tuning_
> proposal()`/`apply_tuning_proposal()`/`reject_tuning_proposal()` 挂
> 反馈关闭钩子）、CLI `/agent goals feedback <id> <text> [--about ...]`、
> REST `POST /v1/goals/{goal_id}/feedback` body 新增 `about`、
> `tests/test_goal_feedback_loop.py`。
> 触发背景：用户在实际使用目标树系统
> （`goal_tree_system_plan.md` 系列）过程中发现，树形结构本身编辑已经
> 很顺手（`goals tree` / `decompose` / `candidates` / `focus pin`），
> 但**看不清每个目标的进度和产出内容**；产出散落在
> `output_workspace.py` 的四固定目录模型里，要看得知道 output_dir 在
> 哪、进去翻文件；诊断能力（`cycle_diagnostics.py`）目前只覆盖单个
> Goal，看不到整棵树的全局状态；用户反馈（`goals feedback`）写进去后
> 是纯文本追加，没有"这条反馈之后怎样了"的回路。用户提出：是否应该
> 建一个类似人生目标 wiki 目录的东西，把各种产出都归位到可浏览的目录
> 下；是否应该有个能输出"整个目标树状态"的报告机制；反馈交互方式是否
> 也需要更合理的设计。

## 0. 现状盘点：已经有什么、还缺什么

先说清楚现状，避免重复造轮子——跟 `goal_cron_cycle_diagnostics_and_
interactive_tuning_plan.md` 当时的情况类似，这条主线上散落着不少可以
直接复用的单点能力，缺的是**把它们缝合成一份看得懂的视图**。

**已经有的、可以直接复用的能力**：

| 能力 | 位置 | 覆盖的信息 | 局限 |
|---|---|---|---|
| 目标树结构读写 | `perception/goal_backlog.py` | 节点、父子关系、状态、`current_focus_ids` | 只是数据结构，无展示层 |
| 文本树形打印 | `goals tree [root_id]` | 标题、状态、待确认候选、⭐ 焦点标记 | 只打标题一行，无进度/产出 |
| 单节点诊断报告 | `perception/cycle_diagnostics.py` | 阶段历史、健康告警、`recent_cycle_summaries`、`progress_notes_tail` | **仅覆盖单个 Goal**，无跨节点汇总 |
| 产出目录扫描 | `output_workspace.py::scan_output_structure()` | output/ 下子目录、文件数、`_misc/` 未整理项、根目录散落文件 | 只服务于 tidy 阶段自查，未面向"用户想看这个目标产出了什么" |
| 产出目录自动索引 | `output_workspace.py::render_output_readme()` | 机械扫描生成 `output/README.md`，**刻意不经过 LLM，保证客观** | 只覆盖单个目标的 output/ 一层，不感知子目标、不感知整棵树 |
| 通用知识 wiki | `wiki/`（entity_digest / decision_writer / experience_writer / graph …） | agent 沉淀的可复用知识、经验、实体 | 是"横向"知识库，不按目标树结构组织，跟具体目标弱关联 |
| 焦点行动建议 | `goals next-steps [id]` | `focus_next_step` 候选，只读现有落盘结果 | 只读展示，未汇入统一报告 |
| 反馈持久化 | `goals feedback <id> <text>` | 追加到该 Goal，后续派生执行会带上这条意见 | 纯文本黑盒，无状态、无"处理结果"回执 |
| kanban 目标树子页 | `apps/mini_agent_kanban/app.py:4708` 起 | 列表/看板视图 + 🌳 目标树视图切换 | 前端已有壳子，可承载新增的详情页/报告展示，不用新起页面 |

**明确缺失的部分**：

1. **没有"节点详情页"**——`tree` 命令给的是列表视角（一行一个节点），
   `diagnose` 给的是单节点结构化数据但要单独按 id 查、且是为"调优决策"
   设计的，不是给人顺手读的"这个目标做到哪了、上次产出了什么"。
2. **没有目标产出的 wiki 化镜像**——`render_output_readme()` 已经证明
   了"机械扫描文件系统生成索引"这条路可行，但只做了单目标的 output/
   一层，没有把整棵目标树的产出串成一个可点击浏览的目录。
3. **没有树级汇总报告**——`cycle_diagnostics` 的聚合逻辑目前锁死在单
   Goal 粒度，遍历子树、拼"全局待办清单"这层完全没有。
4. **没有反馈闭环**——`goals feedback` 是"写了就沉底"，缺状态字段和
   "下次报告里回顾一下上次反馈处理成什么样"的机制。

## 1. 需求拆解：三个子能力 + 一条共享原则

把用户的需求拆成职责清晰的三块，分开设计，但共享同一条设计原则：

- **能力 A（节点详情 + 产出 Wiki）**：把"这个目标做到哪了、产出了什么"
  从"要知道去哪翻文件"变成"打开一个页面就有"。
- **能力 B（树级汇总报告）**：把"整棵目标树现在状态如何、有哪些事等我
  处理"一次性拼出来，替代用户自己一个个节点点开看。
- **能力 C（反馈闭环）**：把反馈从自由文本黑盒变成有状态、可追溯的
  待办项。

**共享原则**（延续 `cycle_diagnostics` 定下的规矩，不重新发明）：

- **只读聚合优先，不做新判定逻辑**——能力 A/B 都是"把已有数据源拼给人
  看"，健康信号复用 `check_phase_health()`，产出扫描复用
  `scan_output_structure()`，不新增一套"综合评分"算法。
- **默认不引入 LLM**——索引类内容（目录结构、文件列表、状态汇总）机械
  生成，保证客观、零成本、可离线跑；LLM 摘要作为可选、默认关闭的增强层，
  失败自动降级为纯结构化展示，跟 `cycle_diagnostics.py::
  summarize_report_with_llm()` 的既有取舍一致。
- **产出 Wiki 与通用知识 wiki 分层，不混用同一套存储**：`wiki/` 模块
  服务的是"agent 沉淀的可复用知识"，有转正/隔离/去重这套治理生命周期；
  目标产出 wiki 是"目标树结构的只读镜像 + 索引"，两者语义、生命周期都
  不同。产出 wiki 页可以**链接**通用 wiki 里的相关知识条目，但不共用
  存储和治理机制，避免把两套东西搅在一起谁都说不清楚。

## 2. 能力 A：节点详情页 + 目标产出 Wiki

### 2.1 节点详情页：合并现有数据，不新建存储

新增一个只读聚合函数，输入 `goal_id`，输出一份人类友好的"节点主页"数据：

```python
def build_goal_node_page(paths: AgentPaths, goal_backlog: GoalBacklog,
                          goal_id: str) -> GoalNodePage:
    ...
```

`GoalNodePage`（dataclass）：

```python
@dataclass
class GoalNodePage:
    goal_id: str
    title: str
    status: str
    path_from_root: list[str]        # 面包屑：从根到当前节点的标题链
    # ── 进度（复用 cycle_diagnostics 的既有字段，不重复计算）──
    execution_phase_mode: str
    recent_cycle_summaries: list[dict]
    progress_notes_tail: str
    # ── 产出（复用 output_workspace 的既有扫描）──
    output_structure: dict           # scan_output_structure() 原样结果
    output_readme_text: str          # render_output_readme() 生成的文本，直接嵌入
    # ── 子节点（导航用，一行一个，不递归展开全部详情）──
    children: list[dict]             # [{"id":..., "title":..., "status":..., "phase":...}]
    # ── 待处理项（给能力 C 反馈闭环挂靠用）──
    pending_items: list[dict]        # 未处理的 decompose_candidates / 未 pin 的 focus / 未 confirm 的 spec、tune 草案
    # ── 反馈历史 ──
    feedback_history: list[dict]     # 见 §4
    generated_at: float
```

关键点：这个函数**不新增数据源**，只是把 `cycle_diagnostics.py` 里已经
读过的进度数据、`output_workspace.py` 里已经扫描过的产出数据、
`goal_backlog.py` 里已经有的子节点/候选数据拼到一起，按"一个节点一页"
的形状重新排列。

### 2.2 目标产出 Wiki：节点详情页的落盘镜像

在节点详情页的基础上，加一层**落盘生成**，让整棵目标树变成可以像 wiki
一样点击浏览的静态目录，而不是每次都要现查：

- 路径约定：`<outputs_root>/goals_wiki/<goal_id>/index.md`，结构与
  `output_workspace.py` 现有的 `<outputs_root>/goals/<goal_id>/...`
  约定平行，不侵入既有产出目录。
- 内容 = `GoalNodePage` 的 Markdown 渲染：标题、面包屑、进度摘要、
  `output_readme_text` 原样嵌入、子节点列表（每个子节点是一个指向
  `../<child_id>/index.md` 的链接）。
- **生成时机**：跟 `render_output_readme()` 一样，在 tidy 阶段 / 每轮
  执行结束时机械重新生成一次，刻意不做增量 diff、不经过 LLM——保证这份
  wiki 反映的始终是客观当前状态，而不是某一次的"整理报告"快照。
- **导航体验**：根节点页 `goals_wiki/<root_id>/index.md` 或
  `goals_wiki/index.md`（全局根）就是整个目标 wiki 的入口，点子节点链接
  一路下钻，天然对应目标树结构，不需要额外维护一份导航索引。
- **与通用 wiki 的关系**：`output_readme_text` 或人工 `progress_notes`
  里如果提到某条已沉淀进 `wiki/` 的知识/经验，渲染时按标题做简单文本
  匹配加一行"相关知识："链接列表（可选增强，命中不了就不显示，不做强
  关联判定）。

### 2.3 呈现层

- **CLI**：`goals show <id>`——打印节点详情页（复用 §2.1 的数据结构，
  终端友好格式，跟 `goals diagnose` 同一层级但更偏"人看的摘要"而非
  "调优用的结构化字段"）。
- **CLI**：`goals wiki build [root_id]`——手动触发一次 §2.2 的落盘生成
  （批量，遍历子树）；正常情况下由 tidy 阶段自动触发，这个命令是手动
  补触发入口，风格上跟 `goals decompose --force` 类似。
- **REST**：`GET /v1/goals/{goal_id}/page` 返回 `GoalNodePage.to_dict()`。
- **看板**：目标树子页里，点击某个节点从"展开一行"改为"打开详情面板"，
  展示 §2.1 内容；wiki 静态文件可以直接作为可下载/可预览的产出物挂在
  同一面板里，不用额外开发一套 wiki 阅读器前端。

## 3. 能力 B：树级汇总报告

在 `cycle_diagnostics.py` 之上加一层"树级 rollup"，把粒度从单节点提到
子树，但不改动 `cycle_diagnostics` 本身（它继续服务单节点场景）：

```python
def build_goal_tree_report(paths: AgentPaths, goal_backlog: GoalBacklog,
                            root_id: Optional[str] = None) -> GoalTreeReport:
    ...
```

`GoalTreeReport`：

```python
@dataclass
class GoalTreeReport:
    root_id: Optional[str]           # None 表示全局根
    node_count: int
    # ── 按维度分组（每组是一份 goal_id 列表 + 一句话原因）──
    by_phase: dict[str, list[str]]           # explore/converge/stable/tidy 各多少个
    stuck_or_alerted: list[dict]             # 复用 check_phase_health 的告警结果
    cron_unhealthy: list[dict]               # consecutive_skip_count 异常的
    # ── 全局待办清单（最有行动价值的部分）──
    pending_decompose_candidates: list[dict]
    pending_focus_confirmation: list[dict]
    pending_tuning_proposals: list[dict]
    pending_execution_specs: list[dict]      # draft 未 confirm 的
    # ── 产出速览 ──
    recent_outputs_digest: list[dict]        # 每个活跃节点最近一次产出的一句话摘要
    generated_at: float
```

设计要点：

- **遍历策略**：从 `root_id`（缺省为全局根）做一次子树遍历，对每个
  节点复用已有的只读读取函数（`check_phase_health()`、
  `_tail_jsonl_records()`、`goal_backlog` 的候选/焦点字段读取），不重新
  实现判定逻辑，只做分组聚合。
- **"全局待办清单"是核心价值点**，比单纯罗列进度更有用——用户真正需要
  的是"这棵树现在有哪些事在等我拍板"，而不是逐个节点的状态复述。这四类
  待办（候选分解/焦点确认/调优草案/执行规范）目前都已经有独立的落盘
  状态字段，只是从没被汇总到一起过。
- **性能边界**：树可能较大，遍历时对每个节点只读取"生成报告所需的
  轻量字段"（状态、阶段、告警、待处理标记），不像 `cycle_diagnostics`
  那样为单节点拉取完整的 `recent_cycle_summaries` 历史；产出摘要只取
  最近一条，不做深度聚合，避免报告本身生成变慢。
- **可选 LLM 总结层**：结构化报告之上可以像 `cycle_diagnostics` 的
  `summarize_report_with_llm()` 一样加一层可选的自然语言总述（"整体上
  N 个目标健康，3 个卡住需要关注，5 项待你确认"），默认关闭，失败降级
  为不生成，不影响报告本身。

### 3.1 呈现层

- **CLI**：`goals report [root_id]`。
- **REST**：`GET /v1/goals/tree_report?root_id=...`。
- **看板**：目标树子页顶部加一个"📊 全局报告"折叠区，默认收起，展开后
  展示分组统计 + 待办清单，点击待办项直接跳转到对应节点的详情面板
  （复用能力 A 的 REST 端点，不新增专门的"处理"接口，处理动作还是走
  各自已有的 `candidates accept/reject`、`focus pin`、`tune confirm` 等
  命令）。

## 4. 能力 C：反馈闭环

现状 `goals feedback <id> <text>` 只是追加文本，看不出"这条反馈之后
怎样了"。改进方向：

- **反馈挂靠到具体待办项，而不是笼统贴在 Goal 上**：能力 B 汇总出的
  `pending_*` 待办清单，每一项天然有一个稳定 id（candidate_id /
  proposal_id / spec 版本号），反馈可以选择性关联到某一项：
  `goals feedback <id> <text> [--about candidate:<cid> | proposal:<pid>]`。
  不加 `--about` 时保持现有行为（笼统贴在 Goal 上），向后兼容。
- **反馈加状态字段**：`pending`（写入后默认状态）→ 关联的待办项被
  `accept`/`reject`/`confirm`/`apply` 处理后，自动把对应反馈标记为
  `addressed`，不需要用户手动关闭。没有关联具体待办项的笼统反馈保持
  `pending`，靠 agent 下次派生执行时自然消费（现有行为不变）。
- **报告里回顾**：能力 A 的节点详情页和能力 B 的树级报告都展示
  `feedback_history`，`addressed` 的反馈显示"已处理→变成了什么"（比如
  链接到对应已 apply 的调优草案 diff），让用户能看到闭环，而不是写进去
  就没有下文。
- **不新增判定逻辑**：反馈"被处理"的判定完全依附于已有的
  accept/reject/confirm/apply 动作，本身不做任何自动语义匹配或 LLM
  判断"这条反馈是否已经被满足"，避免引入不可靠的自动关闭。

## 5. 分阶段规划

> 各 Stage 完成后在此处更新实施状态与实际落地位置，不新开"实施记录"
> 文档——这份计划本身随实施进度滚动更新，跟 `goal_cron_cycle_
> diagnostics_and_interactive_tuning_plan.md` 的做法一致。

延续项目"每个 Stage 完成后更新文档 + 跑回归"的节奏，优先级按"风险低、
立刻能用"排序：

**Stage 1 — 树级汇总报告（能力 B，纯只读）：✅ 已实施**
- `perception/goal_tree_report.py`：`build_goal_tree_report()`——按
  `root_id`（省略时覆盖全局森林）BFS 收集子树节点，复用
  `execution_phase.check_phase_health()`/`cycle_tuning.list_proposals()`/
  `goal_execution_spec.load_spec()`/`output_workspace.read_all_manifests()`
  等既有只读函数做分组聚合，不新增判定逻辑
- CLI `goals report [root_id]`（`_cmd_report()`），REST
  `GET /v1/goals/tree_report?root_id=...`（`get_goal_tree_report()`）
- 看板集成**未做**（详情见 §6）
- 回归：`tests/test_goal_tree_report.py`（9 个用例），覆盖 root_id 不存在/
  空森林/子树范围裁剪/全局森林/按状态分组/按阶段分组（默认 explore）/
  待处理分解候选/待确认焦点/结果可 JSON 序列化
- 验收标准：能一次性看到"整棵树有哪些事等我处理"，不用逐节点点开 ✅

**Stage 2 — 节点详情页（能力 A 前半，纯只读，不落盘）：✅ 已实施**
- `perception/goal_node_page.py`：`build_goal_node_page()`——面包屑
  （父链回溯）+ 进度（复用 `cycle_diagnostics.build_cycle_diagnostics()`）
  + 产出扫描（复用 `output_workspace.scan_output_structure()`/
  `render_output_readme()`）+ 子节点导航（不递归展开）+ 待处理项（复用
  Stage 1 新增的 `goal_tree_report.collect_pending_items_for_node()`，
  两处逻辑合一）+ 反馈历史（直接读 `GoalNode.user_feedback`）
- CLI `goals show <id>`（`_cmd_show()`），REST
  `GET /v1/goals/{goal_id}/page`（`get_goal_node_page()`）
- 看板集成**未做**（跟 Stage 1 一起留到看板改造统一做，见 §6）
- 回归：`tests/test_goal_node_page.py`（8 个用例），覆盖节点不存在/基本
  字段/多层面包屑/子节点列表/待办项与 Stage 1 helper 一致/反馈历史透传/
  产出扫描非空/结果可 JSON 序列化
- 验收标准：打开一个节点就能看到进度 + 产出 + 待办，不用再去翻
  output_dir 或单独跑 diagnose ✅

**Stage 3 — 目标产出 Wiki 落盘镜像（能力 A 后半）：✅ 已实施**
- `evolution/goal_wiki.py`：`render_goal_wiki_page()`——直接复用
  `goal_node_page.build_goal_node_page()` 的聚合结果渲染成 Markdown，
  整份覆盖写入 `goals_wiki/<goal_id>/index.md`（子节点用相对链接
  `<child_id>/index.md`，面包屑用 `../<id>/index.md`）；
  `build_goal_wiki_tree()`——复用 `goal_tree_report._collect_subtree()`
  的同一份 BFS 遍历批量落盘，`root_id=None` 时额外刷新全局根索引
  `goals_wiki/index.md`
- CLI `goals wiki build [root_id]`（`_cmd_wiki_build()`），REST
  `POST /v1/goals/wiki/build?root_id=...`（`build_goal_wiki()`）
- 自动触发：`goal_cron_bridge.reap_finished_cycles()` 里，跟既有的
  "阶段收敛（running/tidy）才归档已完成子节点"用同一个 `allow_archive`
  判断，命中时顺带刷新这一个 Goal 的 wiki 页（不递归整棵子树，避免
  每次 tick 开销随树的规模线性增长）；批量重建整棵树仍然是手动
  `/agent goals wiki build` 或未来看板定时任务的职责
- 看板集成**未做**（跟 Stage 1/2 一起留到看板改造统一做，见 §6）
- 回归：`tests/test_goal_wiki.py`（8 个用例），覆盖节点不存在返回
  None 不写文件/单节点渲染落盘且含产出目录索引内容/子节点链接对应
  树结构/`root_id=None` 遍历全局森林并生成根索引/`root_id=<id>` 只
  重建子树且不生成根索引/重复生成幂等（文件集合不变）/root_id 不
  存在返回空列表
- 验收标准：`goals_wiki/` 目录点开根节点能一路点到任意子节点，内容
  跟实际 output_dir 状态一致 ✅（通用 wiki 关联链接增强按 §6 建议
  暂不实现）

**Stage 4 — 反馈闭环（能力 C）：✅ 已实施**
- `goal_backlog.py::add_user_feedback()` 反馈记录加 `status` 字段（初始
  `pending`）+ `about` 关联参数（`"candidate:<id>"` / `"proposal:<id>"`）；
  新增 `mark_feedback_addressed()`（加锁版本，供不持有 `_locked()` 的
  调用方用）+ `_mark_feedback_addressed_on_node()`（不加锁版本，供已在
  `_locked()` 临界区内的方法内联调用，避免文件锁不可重入导致死锁）
- 钩子落点：`accept_candidate()`/`reject_candidate()`（`goal_backlog.py`，
  已在锁内直接调用不加锁版本）、`cycle_tuning.confirm_tuning_proposal()`
  （新增可选 `goal_backlog` 参数，向后兼容不传时跳过）/
  `apply_tuning_proposal()`/`reject_tuning_proposal()`（已有
  `goal_backlog` 参数，直接调用加锁版本）；CLI/REST 对应调用点
  （`_cmd_tune` confirm、`confirm_tuning_proposal_route`）同步传入
  `goal_backlog`
- CLI `goals feedback <id> <text> [--about candidate:<cid> |
  proposal:<pid>]`；REST `POST /v1/goals/{goal_id}/feedback` body 新增
  可选 `about` 字段
- 展示层：能力 A 节点详情页（`_cmd_show`/`goal_wiki._render_markdown`）
  反馈历史行加 `✅/⏳` + `status` + `about`；能力 B 树级报告新增
  `pending_feedback` 字段（`goal_tree_report.py`），收集全树里状态仍是
  `pending`（含 Stage 4 之前写入、没有 `status` 字段的旧数据，视同
  `pending`）的反馈条目，CLI `goals report` 对应打印一段"未处理反馈"
- 回归：`tests/test_goal_feedback_loop.py`（11 个用例），覆盖默认
  `status=pending`/`about` 落盘/accept 关联反馈变 addressed 且不影响
  笼统反馈/reject 同样触发/不关联的反馈不被误标/`mark_feedback_
  addressed` 对不存在节点返回 0/confirm 不传 `goal_backlog` 时行为不变
  （向后兼容）/confirm、apply、reject 三个入口分别验证标记/树级报告
  `pending_feedback` 排除已 addressed 的条目
- 验收标准：写一条关联到某个候选的反馈，`accept` 该候选后，`goals
  show`/`goals report` 里能看到这条反馈自动变成 `addressed` ✅

**Stage 5（可选，视 Stage 1-4 使用效果再定）— LLM 自然语言总结层：未实施**
- 按方案措辞是"可选、视前几个 Stage 实际使用效果再定"，不是既定必做
  项，本轮暂不做，留到 Stage 1-4 用起来之后再评估是否需要。

## 6. 开放问题

- **看板集成延后到 Stage 1+2+3+4 一起做**：§3.1 设想的"📊 全局报告"折叠区、
  节点"详情面板"、wiki 静态文件预览、反馈状态标记都没有随各自 Stage 一起
  做（`apps/mini_agent_kanban/app.py` 单文件 8000+ 行，改动面比后端聚合
  函数本身大得多，且没有独立回归覆盖），CLI/REST 已经可用（`goals
  report`/`goals show`/`goals wiki build`/`goals feedback --about` +
  对应 REST 端点）。四者都要改同一处"目标树子页"，合并到一次看板改造里
  做，避免分次改互相冲突。

- **Stage 3 的落盘量级**：树很大时 `goals_wiki/` 目录文件数会随节点数
  线性增长，是否需要限制深度或提供"只生成当前 focus 子树"的窄化选项，
  留到实际用起来再看是否有必要。目前的自动触发只在 reap 到已完成子
  节点时刷新单个节点（见 Stage 3 实施记录），本身不会因为树变大而让
  单次 tick 开销失控，量级问题主要落在"手动/批量全量重建"这条路径上。
- **看板"详情面板"与"全局报告"点击跳转**的具体交互（新开面板 vs 原地
  展开）留给 Stage 1/2 实现时结合 kanban 现有的 tab 懒渲染机制
  （`app.py` 里 `st.tabs()` 替换方案）具体设计，不在本文档里预先定死。
- **通用 wiki 关联链接**（§2.2 最后一点）命中率如何、是否值得做，建议
  Stage 3 先不做，等 wiki 页本身用起来之后再评估要不要加。
- **目标树节点失败自动重试**（用户对话内提出，见
  `evolution/goal_node_retry.py` 模块头部说明）：failed 状态的
  Objective 自动拉回 active 重试、不限次数，连续失败达到阈值倍数时
  推通知但不叫停。看板上"查看重试/失败历史"的入口用户已确认**纳入
  后续统一看板改造，暂不单独加**，跟本文档 Stage 1/2/3 的看板集成是
  同一个决定（见上面"看板集成延后"一条），CLI/REST（`goals show` 里
  的"⚠️ 连续失败 N 次"提示）先顶上。
