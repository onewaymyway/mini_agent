# 人设能力自主学习系统设计方案（Persona Capability Learning）

- **版本**：v0.1（初稿）
- **定位**：mini_agent 新增能力设计方案——让 Agent 围绕用户设定的一个**能力人设/方向**（例如"希望你具备强大的股票分析能力"），持续自主地从互联网检索、整理、沉淀为 wiki 知识，并在必要时**异步**向用户提问以获取只有用户才知道的信息（偏好、真实需求边界、私有语境），全程不阻塞任何一方。
- **一句话概括**：复用 `growth_advisor.py`（信号→候选→调研→反馈闭环）与 `wiki/`（写入/去重/关联/检索）已经跑通的架构范式，新增一条服务对象是"Agent 自身某项专精能力"而不是"用户成长方向"或"Agent 通用自我进化"的平行闭环，并补齐一个此前项目里没有的能力：**Agent 主动提问、用户异步作答、Agent 消费答案继续推进**的问答队列机制。

---

## 0. 为什么不是简单套用 growth_advisor 或 gap_scanner

先说清楚这个方案和现有两个最相似的模块的本质区别，避免做成"换个名字的重复实现"：

| 现有模块 | 服务对象 | 触发/终止条件 | 和本方案的关系 |
|---|---|---|---|
| `evolution/growth_advisor.py` | **用户自己**的成长方向（比如"最近在学 Rust，建议看看 xxx"） | 没有明确的"完成"概念，持续观察用户信号、持续推荐，用户可采纳可忽略 | 架构范式可以复用（候选/报告/反馈台账三层结构），但服务对象、触发信号完全不同——本方案不是"观察用户反推用户该学什么"，而是"用户已经明确说了要 Agent 具备什么能力，Agent 照着这个方向去学" |
| `wiki/gap_scanner.py` | **已有 wiki 内容自身**的结构完整性 | 规则扫描孤儿页/浅层实体，不涉及"要不要主动去互联网找新内容" | 只解决"发现内部缺口"，不解决"围绕一个用户指定的目标主动对外检索、填内容"——本方案的 gap 判定要先建立在一份"目标应该覆盖什么"的能力大纲上，这份大纲 gap_scanner 完全没有 |
| `evolution/soft_goal_deriver.py` / `objective_executor.py` | Agent 自身能力短板（系统自己发现的，不是用户指定的） | 一次性 Goal → Objective → 执行 → 完成后归档 | 本方案的 Track 是**长期持续**的，不是"完成即结束"的一次性 Goal；但 Track 每一轮拆出来的"检索这个子主题"这类具体动作，可以复用 objective_executor 的执行框架 |

**结论**：这是一个新的平行子系统（暂命名 `evolution/capability_learning.py` + `wiki/` 复用 + 新增 `kanban` 面板），不是给已有任何一个模块打补丁。

---

## 1. 目标与非目标

**目标**：
1. 用户只需要给一句话方向（"希望你具备强大的股票分析能力"），Agent 自动把它拆成一份**能力大纲**（子主题清单），并持续、克制地推进覆盖
2. 学习产出以 **wiki 页面**形式沉淀，可被用户随时阅读，也可被 Agent 在后续对话中检索复用，不需要每次现查
3. 遇到"互联网查不到、只有用户自己知道"的信息缺口时，Agent 生成结构化问题，**异步**丢给用户，不阻塞当前学习循环，也不阻塞用户当前正在做的任何事
4. 整个过程在看板上**完全可见、可控**：人设可查看/编辑，进度可查看，可暂停/恢复/删除，待回答问题集中列出
5. 默认克制：不铺开全网抓取，每轮小步推进，资源消耗可控、可审计

**非目标（本方案不做）**：
- 不做"Agent 自动交易/自动决策"这类需要资金/账户权限的执行动作——本方案只产出**知识**，不产出**操作**
- 不对检索到的内容做事实正确性背书；wiki 页面必须带来源链接和检索时间，让用户自行判断可信度（继承 `wiki` 现有的 frontmatter 惯例）
- 不用这套机制处理"用户自己的成长"（那是 growth_advisor 的职责），也不用来做 Agent 通用自我进化（那是 `soft_goal_deriver` 的职责）——三者数据不打通、UI 分区展示，避免用户混淆"这是在帮我，还是在帮 Agent 自己"

---

## 2. 整体架构

```
┌─────────────────────────────────────────────────────────────────────┐
│  用户输入                                                             │
│  "希望你具备强大的股票分析能力" ──► 看板/对话 创建 CapabilityTrack        │
└───────────────────────────────────┬───────────────────────────────────┘
                                     ▼
                    ┌────────────────────────────────┐
                    │  CapabilityTrack（持久实体）      │
                    │  人设描述 / 能力大纲 / 状态 / tag  │
                    └───────────────┬────────────────┘
                                     │  cron: sys:capability_learning_cycle
                                     ▼
┌───────────────────────────────────────────────────────────────────────┐
│                      每轮学习循环（每个 active Track 各跑一次）           │
│                                                                         │
│  1. 大纲缺口扫描 —— 对比大纲子主题 vs 已有 wiki 覆盖情况，选 1~2 个待推进   │
│  2. 检索规划 —— 生成检索关键词；判断这个子主题是否需要"只有用户知道的信息"  │
│       │                                    │                          │
│       │ 需要用户输入                         │ 可直接检索                │
│       ▼                                    ▼                          │
│  生成 CapabilityQuestion（挂起，          web_search 检索 → LLM 提炼摘要  │
│  不阻塞本轮循环，继续跑下一个子主题）              │                       │
│                                             ▼                          │
│                                    wiki/writer + dedup + graph 落盘     │
│                                             │                          │
│                                             ▼                          │
│                              CapabilityLedger 记录本轮增量               │
└───────────────────────────────────┬───────────────────────────────────┘
                                     ▼
                    ┌────────────────────────────────┐
                    │  看板：人设管理 / 进度 / 待回答问题 │
                    │  用户随时查看、编辑、暂停、回答      │
                    └───────────────┬────────────────┘
                                     │  用户回答问题（异步，任意时间）
                                     ▼
                    下一轮 cron 循环读取已回答问题 → 写入对应 wiki 页面 /
                    调整大纲 → 关闭该 CapabilityQuestion
```

关键设计原则：**检索循环和提问循环解耦**。生成问题这件事本身不消耗"这一轮"的执行时间，也不等待用户回答——它只是把问题放进队列，循环立即转向下一个可以独立推进的子主题。用户答不答、什么时候答，只影响"这一个子主题什么时候能补完"，不影响 Track 整体的持续运转。

---

## 3. 数据模型

### 3.1 `CapabilityTrack`（人设/方向，长期持续实体）

```python
@dataclass
class CapabilityTrack:
    track_id: str
    title: str                      # "股票分析能力"
    persona_desc: str               # 用户原话或整理后的方向描述
    outline: list[OutlineTopic]     # 能力大纲：子主题清单
    status: str                     # active / paused / archived
    wiki_tag: str                   # 对应 wiki 命名空间，如 "capability:stock_analysis"
    excluded_keywords: list[str]    # 黑名单，不检索/不生成问题
    created_at: float
    updated_at: float
    cadence: str = "interval:21600" # 学习节奏，默认 6 小时一轮，可配置

@dataclass
class OutlineTopic:
    topic_id: str
    name: str                       # "技术分析基础"
    coverage_state: str             # uncovered / partial / covered
    last_touched_at: Optional[float]
    wiki_page_ids: list[str]        # 关联的 wiki 页面
```

`outline` 的生成方式：创建 Track 时，用 LLM 根据 `persona_desc` 起草一份初始大纲（P1 阶段用规则式模板兜底：常见能力方向内置几套通用大纲骨架，比如"分析类能力"统一套路是"基础概念/主流方法/数据来源/常见误区/进阶专题"），用户可在看板上增删子主题——**大纲本身也是可编辑的，不是一次性写死**。

存储路径（沿用 `storage/paths.py` 命名惯例）：
- `capability_tracks_path()` → `<project_root>/.agent/capability_tracks.json`（全部 Track 列表，类似 `cron_jobs.json` 的单文件存法，量级不大不需要分文件）
- `capability_ledger_path(track_id)` → 每个 Track 一份进度台账

### 3.2 `CapabilityLedgerEntry`（每轮进度台账，供看板展示 + 复盘）

```python
@dataclass
class CapabilityLedgerEntry:
    track_id: str
    cycle_ts: float
    topic_id: str
    action: str                # "researched" / "question_raised" / "question_answered" / "skipped"
    wiki_page_ids: list[str]   # 本轮新增/更新的页面
    summary: str                # 一句话说明本轮做了什么，供看板列表展示
```

### 3.3 `CapabilityQuestion`（异步问答队列的核心，本方案新增机制）

这是与 `tools/user_input.py` 里现有 `ask_user` 最本质的区别所在——**`ask_user` 是同步阻塞的**（在一次对话 turn 内，Agent 停下来等答案，走的是 `interaction.ask()` 那套 INTERACTION_REQ/回传机制）；而后台 cron 循环里生成的问题**绝不能阻塞 cron 线程**，必须是"扔进队列就继续干活，答案什么时候来什么时候消费"的模式。这一点在实现时是硬约束，不能图省事直接调用 `ask_user`。

```python
@dataclass
class CapabilityQuestion:
    question_id: str
    track_id: str
    topic_id: str
    question: str              # "你更关注短线技术分析还是长线基本面？这会影响后续检索方向"
    hint: Optional[str]
    status: str                # pending / answered / dismissed / expired
    created_at: float
    answered_at: Optional[float]
    answer: Optional[str]
    expires_at: Optional[float]  # 长期不回答自动降级为 dismissed，避免队列无限堆积
```

存储：`capability_questions_path()` → 追加写 jsonl（参照 `notification/reports_store` 或 `growth_feedback_ledger` 的落盘方式），看板/API 按 `status` 过滤展示。

**生成问题的克制原则**（避免变成"什么都问用户"）：
- 只有当"这个信息互联网上查不到、且明显影响后续检索方向"时才生成问题（比如用户的风险偏好、关注的具体行业/市场、已有的知识基础），单纯"这个概念是什么"这类可以自己查到的，绝不生成问题
- 单个 Track 同时处于 `pending` 状态的问题数设上限（比如 3 个），达到上限本轮不再新增问题，优先处理不需要用户输入的子主题
- 问题要具体、可回答（"你倾向短线还是长线？"），不能是开放式大问题（"你对股票了解多少？"）——这一点在 prompt 层面约束

---

## 4. cron 集成

新增内置 job（挂进 `cron_scheduler.py` 现有的 `sys:` 前缀内置任务列表）：

```
sys:capability_learning_cycle   — 遍历所有 active Track，各推进一轮   interval:21600（可每个 Track 单独覆盖节奏）
sys:capability_question_sweep   — 清理过期未回答问题，标记 expired    interval:86400
```

`sys:capability_learning_cycle` 单轮伪流程：

```python
for track in active_tracks:
    if pending_questions_count(track) >= MAX_PENDING:
        topics = topics_not_needing_user_input(track)
    else:
        topics = pick_next_topics(track, limit=2)  # 缺口扫描挑出的待推进子主题

    for topic in topics:
        if needs_user_context(topic):           # LLM 判断/规则判断
            raise_capability_question(track, topic)
            continue
        results = web_search_and_summarize(topic, track.excluded_keywords)
        pages = wiki_writer.write(track.wiki_tag, topic, results)
        ledger.append(track, topic, action="researched", pages=pages)

    # 消费已回答但尚未处理的问题
    for q in answered_unconsumed_questions(track):
        fold_answer_into_topic(track, q)         # 可能调整大纲、触发针对性检索
        q.status = "consumed"
```

资源阀门：复用 `evolution/resource_arbiter.py` 对本 job 的检索/LLM 调用做限速，避免和其它 cron job（growth_advisor、self_eval 等）抢占额度；`excluded_keywords` 命中的子主题直接跳过。

---

## 5. wiki 沉淀规范

- 所有页面 `frontmatter` 新增/复用字段：`capability_track_id`、`source_urls`（检索来源）、`retrieved_at`（检索时间，供时效性判断）
- 落盘统一走 `wiki/writer.py`，写入前经 `wiki/dedup.py` 判重（避免同一子主题被反复检索反复写出几乎相同的页面）
- 子主题之间的关联走 `wiki/graph.py`，覆盖率高、关联密的一批子主题可以被 `wiki/topics.py` 自动聚合成专题页（复用现有机制，不用额外开发）
- 定期健康检查复用 `wiki/gap_scanner.py`（找孤儿页/浅层实体）与 `evolution/wiki_utility_audit.py`（这批自动生成的内容有没有被后续对话真正用到，用不到的要能被发现、被降权或清理，避免"学了一堆但从没用过"）

---

## 6. 使用侧：对话中如何用上这些沉淀

在 `context_builder.py` 里增加一步：识别到当前对话话题命中某个 active Track 的 `wiki_tag`（关键词匹配或轻量 LLM 判定）时，走 `wiki/search.py` 检索该 tag 下相关页面，按需注入上下文——**学习在后台持续发生，使用时是查库，不是现场现查**。这是本方案区别于"每次现搜现答"的核心价值：随时间推移，Agent 在这个方向上的回答质量应该肉眼可见地变好。

---

## 7. 看板集成（Tab 设计）

新增一个 Tab（或挂进现有"📌 目标看板"旁边，命名"🎓 能力学习"），三个区域：

### 7.1 人设管理区
- Track 列表：标题、状态（active/paused）、整体覆盖率（`covered 子主题数 / 总数`）
- 新建 Track：一个文本输入框（人设描述）+ "生成大纲"按钮（调 LLM 起草大纲，用户确认或编辑后创建）
- 单个 Track 详情：可编辑大纲（增删子主题、拖拽调整优先级）、编辑黑名单关键词、调整学习节奏（cadence）、暂停/恢复/删除
- **删除需要二次确认**，且删除只下线 Track 本身，不级联删除已产出的 wiki 页面（页面是独立资产，用户可能仍想保留阅读）——这一点参考项目里"sys: 前缀 job 不可删除只可 disable"的克制哲学

### 7.2 进度展示区
- 按子主题的覆盖状态做一个简单看板（uncovered / partial / covered 三栏，或进度条），每个子主题点开能看到关联的 wiki 页面列表和最近一次学习台账
- 时间线视图：复用类似 `_build_growth_timeline_svg` 的现成 SVG 时间线绘制逻辑，展示这个 Track 最近的学习活动

### 7.3 待回答问题区（异步问答的 UI 落地）
- 列出所有 `status=pending` 的 `CapabilityQuestion`，按 Track 分组，每条显示问题文本 + hint + 所属子主题
- 每条问题下有一个文本输入框 + "提交回答"按钮，提交后 `status → answered`，**不需要停留在这个页面等**，看板本身不阻塞用户做任何其它操作，也不阻塞 cron 循环——下一轮 `sys:capability_learning_cycle` 会自动捡起这条已回答的问题去处理
- 已回答/已过期的问题可以在一个折叠的"历史问答"区查看，便于用户回顾"Agent 之前问过我什么"

对应新增 API（挂进 `api/routes.py`，风格参照现有 `/growth/*` 系列）：

```
GET    /v1/capability/tracks                        列出所有 Track
POST   /v1/capability/tracks                         创建（persona_desc，可选 outline）
GET    /v1/capability/tracks/{track_id}               详情（含大纲覆盖状态）
PATCH  /v1/capability/tracks/{track_id}                编辑（大纲/黑名单/节奏/status）
DELETE /v1/capability/tracks/{track_id}                删除 Track（不级联删 wiki）
GET    /v1/capability/tracks/{track_id}/ledger          学习台账（分页，同 external_input_events 的分页模式）
GET    /v1/capability/questions?status=pending&track_id=  待回答问题列表
POST   /v1/capability/questions/{question_id}/answer      提交回答（body: answer）
POST   /v1/capability/questions/{question_id}/dismiss      忽略问题（不回答但不想一直挂着）
```

---

## 8. 与通知系统的接入（可选，P2 再做）

新问题产生、或某个子主题覆盖率达标时，可以像 `growth_advisor` 一样接入 `NotificationDispatcher`（email/kanban channel），但要做**同等力度的节流**——不能每生成一个问题就推一条通知，应该合并成"你有 N 个待回答问题"这类摘要通知，且遵循已有的 `notification_frequency` / `notification_max_per_day` 配置项。P1 阶段先不做主动推送，只在看板被动展示，避免过早引入打扰问题。

---

## 9. 理念与边界（重要，决定这个功能会不会跑偏）

1. **主动学习不等于无限制抓取**。默认节奏、每轮子主题数、待回答问题上限都要有硬性上限，且必须在配置里可调（沿用 `config/config_catalog.py` 的配置项注册方式），资源消耗要能被 `resource_arbiter` 统一管控，不能让这个功能和其它 cron job 抢占额度导致整个 daemon 变慢。

2. **提问要克制，不能变成"审讯用户"**。异步问答机制解决的是"信息缺口"，不是"让用户替 Agent 干活"。第 3.3 节的生成原则（互联网查不到 + 明显影响方向 + 数量上限）必须严格执行，且要在 prompt 里明确告诉 LLM"能自己查到的不要问"。

3. **内容质量要可追溯、可审计，不能是黑箱**。每个 wiki 页面必须带来源链接和检索时间；`wiki_utility_audit.py` 要能追踪这些自动生成内容有没有真的被后续对话用到，用不到的要被发现（这是避免"Agent 自己以为在学习，但学的东西从来没用上"的关键校验点，长期看这也是判断这个功能是否值得默认开启的依据）。

4. **人设方向的所有权在用户，不在 Agent**。大纲可以由 Agent 起草，但用户随时能编辑/删除/暂停，Agent 不能"自作主张"扩展到用户没同意的方向（比如用户只说"股票分析"，Agent 不能自己扩展去学"期货""加密货币"，除非用户明确同意扩大范围或在追问回答里提到）。

5. **和 growth_advisor / soft_goal_deriver 严格分区，不混淆职责**。三者共享架构范式但服务对象不同，看板 UI 上要清晰区分"这是在帮你自己成长"还是"这是在帮 Agent 具备你要的能力"，避免用户误解 Agent 在做什么、为了谁做。

6. **异步问答是这个方案里唯一真正意义上的新基础设施**，不是简单复用 `ask_user`（同步阻塞）也不是复用 `permission_req`（也是同步等待用户批准才能继续当前 turn）。它是第一个"生产者（cron 循环）持续产生问题、消费者（用户）在任意时刻异步处理、生产者下一轮自己回来消费答案"的队列式交互模式——如果后续这个模式被验证有效，未来 growth_advisor 或其它 cron job 需要"问用户一句但不想阻塞"时，也应该复用这套 `CapabilityQuestion` 机制而不是各自发明一套，值得作为通用能力沉淀（比如未来可以重命名/上移成 `evolution/async_question.py` 供多个模块共用）。

---

## 10. 实施阶段划分

**P1（最小可用闭环）**：
- `CapabilityTrack` / `OutlineTopic` / `CapabilityLedgerEntry` / `CapabilityQuestion` 数据模型 + 存储路径
- `sys:capability_learning_cycle` cron job：缺口扫描（规则式）+ 检索 + wiki 写入 + 台账记录
- 异步问答队列的生成与消费（不接通知，只落队列）
- 看板三个区域（人设管理/进度展示/待回答问题）+ 对应 API
- `context_builder.py` 接入检索复用

**P2（体验与质量增强）**：
- 大纲生成/缺口判定引入 LLM 辅助（替换纯规则）
- 通知系统接入（节流摘要式推送）
- `wiki_utility_audit.py` 联动，追踪自动生成内容的实际使用率，作为后续调权/清理依据
- 看板拖拽式大纲编辑、时间线可视化

**P3（跨 Track 能力沉淀，方向级，暂不细化）**：
- 多个 Track 之间共享的通用子主题去重复用（比如"数据来源可靠性判断"可能在多个能力方向下都用得到）
- 问答队列机制上移为通用基础设施，供其它 cron 模块复用
