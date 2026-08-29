# 候选人设/能力自动检测方案（Persona Candidate Auto-Scan）

- **版本**：v0.1（设计草案，尚未实现）
- **背景**：`next_doc/persona_capability_learning_design.md` 里
  `target_type="persona"` 全链路已经打通（草稿合成/发布/看板 UI），
  但**新建 Track/人设的入口目前只有用户主动发起**——用户在看板/CLI
  手动填标题+人设描述才能开一条新的能力学习 Track。用户反馈希望
  Agent 在执行过程中**主动检测**"可能值得养成的人设/能力方向"，
  生成候选供用户挑选，体验对齐已经跑通的 `growth_advisor.py`
  （成长顾问：信号扫描 → 候选 → 用户采纳/忽略）范式。
- **定位**：这是 `persona_capability_learning_design.md` 的一个平行
  子系统（新建 Track 的另一条自动化入口），不改动该文档已有的
  Track/大纲/检索/问答队列/发布 任何一个环节；候选一旦被采纳，就是
  一条普通的 `target_type="persona"` Track，之后完全走既有闭环。

---

## 1. 核心诉求（用户原话拆解）

1. Agent 在执行过程中检测，识别"可能需要的人设"，生成候选；
2. 候选需要用户从中挑选，决定要不要学习对应的能力/人设；
3. 生成候选时要**避免**：
   - 用户已经忽略（dismiss）过的人设建议；
   - 已经存在的人设（已发布的 `.agent/personas/*.md`）或已存在的
     能力 Track（`target_type="persona"` 或 `"knowledge"` 均算，避免
     和已有方向重复）；
4. 去重判断**必须用 LLM**，而不是字面/关键词规则；LLM 调用统一走
   agent 框架已有的通用 `LLMHelper`（`llm/service.py`），不新写一套
   调用逻辑。
5. 看板（Streamlit，`apps/mini_agent_kanban`）里要能管理这些候选
   （查看/采纳/忽略）。

---

## 2. 复用与新增

### 2.1 复用（不新造轮子）

| 能力 | 复用来源 |
|---|---|
| 候选生成→用户裁决的状态机范式（pending/accepted/dismissed + 冷却期） | `growth_advisor.py::GrowthBacklog`（`STATUS_*`、`dismissed_cooldown_days` 处理逻辑） |
| 通用 LLM 调用入口 | `llm/service.py::LLMHelper`（`.ask(prompt)`），获取方式对齐 `api/routes.py` 里 `/growth/summary` 已有的 `llm_helper = lambda prompt: helper.ask(prompt)` 写法 |
| LLM 语义判重的 prompt 设计范式 | `growth_advisor.py::_llm_find_duplicate_direction()`（"给一个新标题 + 一份已有标题列表，判断是否本质同一件事，命中原样返回，未命中输出 `NONE`"）——本方案直接照搬这个契约，只是判断对象从"成长方向"换成"人设/能力方向" |
| 已存在人设读取 | `orchestrator/persona_profiles.py::list_personas_for_paths(paths)` |
| 已存在能力 Track 读取 | `evolution/capability_learning.py::CapabilityTrackStore.list_tracks()` |
| 采纳后创建 Track | `evolution/capability_learning.py::CapabilityTrackStore.create(..., target_type="persona")` |
| 异步任务机制（扫描可能触发 LLM 调用，避免阻塞请求） | 看板已有的 `async_job_ui.py` / `start_async_job()` / `run_async_job()`（`growth_scan` 按钮同款） |
| cron 接线范式（opt-in 后台定时任务） | `cron_scheduler.py::SYSTEM_JOBS` 注册模式（参照 `sys:capability_learning_cycle`） |

### 2.2 新增

- `evolution/persona_candidates.py`：新模块，数据模型 + 存储 + 扫描
  + 去重 + 状态流转的纯逻辑层（不依赖 FastAPI/Streamlit，和
  `capability_learning.py` 的分层原则一致）。
- `AgentPaths.persona_candidates_path`：新增 property，落盘路径
  `~/.agent/persona_candidates.json`（与 `capability_tracks_path`
  平级）。
- `api/capability_routes.py`（或独立 `api/persona_candidate_routes.py`，
  实现时再定）：新增 4 个端点（见 §5）。
- `apps/mini_agent_kanban/client.py`：对应 4 个客户端方法。
- `apps/mini_agent_kanban/app.py`：能力学习 Tab 内新增"🎭 候选人设"
  子区域。
- `config_catalog.py`：新增 `PersonaCandidateConfig`（或挂在现有
  `CapabilityLearningConfig` 下作为子字段，实现时再定），至少包含
  `enabled`（默认 `False`，opt-in）、`dismissed_cooldown_days`
  （默认 30）、`min_evidence_count`（默认参照 growth_advisor 的
  `min_evidence_count` 语义）。
- `cron_scheduler.py`：新增 `sys:persona_candidate_scan`，默认
  `enabled: False`（opt-in，和 `capability_learning_cycle` 刚上线时
  的保守默认一致，观察一段时间再考虑要不要 opt-out）。

---

## 3. 数据模型

```python
@dataclass
class PersonaCandidate:
    candidate_id: str
    title: str                 # 候选人设/能力方向标题
    persona_desc: str          # 一段简介，用于用户判断 + 采纳后作为
                                # CapabilityTrackStore.create() 的 persona_desc
    rationale: str              # 为什么会生成这条候选（给用户看的解释，
                                # 例如"最近 7 天有 5 次对话反复涉及 XX 领域"）
    evidence_count: int         # 支撑证据数量（用于排序 + min_evidence_count 过滤）
    evidence_refs: list[str]    # 证据来源的弱引用（entry_id / wiki_miss id 等，
                                # 不回显原文，和 growth_advisor 的"知情但克制"一致）
    source: str                  # "growth_topic" | "wiki_miss" | "manual_scan"
    dedupe_key: str              # normalize_title_key(title)，供冷却期匹配用
    status: str = "pending"      # pending / accepted / dismissed
    created_at: float = ...
    decided_at: Optional[float] = None
    dismiss_reason: Optional[str] = None   # 复用 growth_advisor 的
                                            # DISMISS_REASON_* 常量族，
                                            # 至少要有 ALREADY_EXISTS/NOT_INTERESTED
    accepted_track_id: Optional[str] = None  # 采纳后关联的 CapabilityTrack.track_id
```

存储：单个 JSON 文件（含全部 pending/accepted/dismissed 记录，仿
`growth_advisor.py::_read_jsonl`/`GrowthBacklog` 的落盘方式；候选量级
不大，先不上 JSONL 分文件）。

---

## 4. 扫描与生成流程

`scan_persona_candidates(paths, cfg, profile, memory_store, llm_helper)`：

1. **收集信号**（规则式，不调用 LLM，成本低，先过一轮粗筛）：
   - 复用 `growth_advisor._effective_topic_keywords()` 里
     `confirmed_by_user=True` 或 `auto_confirmed=True` 的主题——这些
     是已经被验证"用户持续关注"的方向，是人设候选的第一来源；
   - 复用 `capability_learning.py` 的 wiki miss 台账（`record_wiki_miss`
     累积的高频未命中检索）——反复查不到、说明该领域目前没有对应
     人设/知识沉淀在支撑；
   - 两类信号各自按 `evidence_count` 排序，取 Top N（避免一次扫描
     生成过多候选，对齐 `GrowthAdvisorConfig.max_pending_candidates`
     的节流思路）。
2. **候选去重过滤**（LLM 判重，本方案的核心新增点）：
   - 收集"已存在标题池"：`list_personas_for_paths(paths)` 的
     `display_name`（或 `name`）+ `CapabilityTrackStore.list_tracks()`
     的 `title`（active/paused，archived 不计入，允许重新提议）；
   - 收集"近期被忽略标题池"：`status == "dismissed"` 且
     `decided_at` 在 `dismissed_cooldown_days` 冷却期内的
     `PersonaCandidate.title`；
   - 对每个新粗筛出来的候选标题，调用
     `llm_helper.ask(prompt)`（prompt 设计见 §4.1），一次性把两个
     标题池都交给 LLM 判断，只要命中任意一个池子就跳过，不生成新
     候选；
   - LLM 调用失败/输出解析不出时（对齐
     `_llm_find_duplicate_direction()` 的降级策略）：**当作不重复**，
     宁可多生成一条候选让用户手动 dismiss，也不要因为一次判断失败
     就悄悄漏掉一个真正的新方向。
3. **落盘**：新增 `PersonaCandidate(status="pending")`，`persona_desc`
   规则式生成一段简短描述（沿用 `capability_learning.py` 里已有的
   "规则先行、LLM 起草作为可选增强"的分层，`persona_desc` 的 LLM
   润色留作后续 opt-in 项，不在本轮范围内）。

### 4.1 LLM 判重 Prompt（草案）

直接照搬 `_llm_find_duplicate_direction()` 的契约（一次性把已有标题
列表 + 新标题交给 LLM，命中要求逐字复制原文，未命中输出 `NONE`），
新增第二个标题池（"用户已忽略过的建议"）需要在 prompt 里显式区分
两类池子的语义，避免 LLM 混淆"已经在做的"和"用户不想要的"：

```
下面是当前已经存在的人设/能力方向：
- <已存在标题 1>
- <已存在标题 2>
...

下面是用户明确表示不感兴趣、近期已经忽略过的人设/能力方向建议：
- <已忽略标题 1>
...

现在有一个新提议的人设/能力方向：「<新标题>」

这个新方向是否和上面任意一个列表中的某一项本质上是同一件事（只是
措辞、范围表述不同）？如果是，请只输出那一项**完全一致**的原文
（逐字复制，不要改写、不要加编号或标点，不要说明来自哪个列表）。
如果不是（这是一个真正新的方向），请只输出 NONE。不要输出除以上
两种情况之外的任何内容。
```

调用方（`scan_persona_candidates`）不需要区分命中的是"已存在"还是
"已忽略"——两种情况的处理动作相同（跳过，不生成候选），所以合并成
一次 LLM 调用、一个统一标题池即可，上面分两段列出只是为了 prompt
可读性/未来若需要区分动作时留出扩展空间；`existing_titles` 传参上
实现时可以直接 `existing + dismissed` 合并成一个 list 传给
`_llm_find_duplicate_direction()` 本体（该函数已经足够通用，本方案
优先直接复用而不是另写一份近似实现）。

---

## 5. HTTP API

| Method | Path | 说明 |
|---|---|---|
| GET | `/capability/persona_candidates` | 列出候选（默认只返回 pending，加 `status` 查询参数可查其它状态，对齐 `/growth/candidates` 现有风格） |
| POST | `/capability/persona_candidates/scan` | 触发一次扫描；因为要调用 LLM，走异步任务返回 `{"job_id", "key"}`，前端用 `run_async_job()` 轮询（同 `growth_scan`） |
| POST | `/capability/persona_candidates/{id}/accept` | 采纳：调用 `CapabilityTrackStore.create(title, persona_desc, target_type="persona")`，回写 `accepted_track_id` |
| POST | `/capability/persona_candidates/{id}/dismiss` | 忽略：body 可选 `{"reason": ...}`，复用 `growth_advisor.DISMISS_REASON_*` 常量 |

`_require_owner(request)` 鉴权、`run_blocking()` 包装、错误处理均对齐
`growth/keywords` 系列端点已有写法。

---

## 6. Streamlit 看板改动

在 `apps/mini_agent_kanban/app.py` 现有"能力学习/人设养成"Tab（渲染
Track 列表的那个函数附近）新增一个子区域"🎭 候选人设"：

- 顶部一个"🔍 扫描候选"按钮（`start_async_job` + `run_async_job()`，
  同成长顾问"立即为我看看"按钮的交互模式）；
- pending 候选列表：每条展示 `title` + `rationale` + `evidence_count`，
  "✅ 采纳"/"❌ 不要"两个按钮（采纳后可选弹出"是否现在就补充大纲"，
  或者直接跳转到新建的 Track 详情——实现时对齐现有 Track 创建后的
  跳转体验）；
- 复用上一轮已经修好的"批量操作"模式（`_kw_batch_bar` 那一套勾选+
  批量按钮的写法），避免候选一多逐条点很烦——这也是这次要顺带修的
  上一个 bug 的教训：新按钮统一走 JSON body 传参，不把 candidate_id
  拼进 URL 路径（`candidate_id` 是系统生成的 uuid，本身不会有特殊
  字符问题，但保持接口风格统一，也省得以后 title 之类的字段被误用
  当路径参数）。

---

## 7. 明确不做的部分（对齐项目一贯的保守默认原则）

- 不自动创建 `.agent/personas/*.md` 人设文件——采纳候选只创建
  `target_type="persona"` 的 Track，是否/何时正式发布成人设文件，
  沿用 `persona_capability_learning_design.md` §10 已有的发布流程，
  由用户在 Track 详情页决定；
- `persona_desc` 初版只做规则拼装，不接 LLM 润色（LLM 只用在"去重
  判断"这一步，符合用户"去重要用 LLM"的明确诉求，避免顺带扩大 LLM
  调用面）；
- cron 定时扫描默认关闭（`enabled: False`），先支持看板手动触发，
  观察实际候选质量/去重准确率后再评估要不要 opt-out。

---

## 8. 待确认问题

1. `PersonaCandidateConfig` 是独立配置块，还是挂在
   `CapabilityLearningConfig` 下面作为子字段？（倾向独立，因为候选
   扫描的开关/冷却期语义和"某条 Track 内部怎么学习"是两件事，参照
   `GrowthAdvisorConfig` 独立于 `CapabilityLearningConfig` 的现状）
2. HTTP 路由挂在 `capability_routes.py` 里还是新开一个文件？
   （倾向新开 `persona_candidate_routes.py`，`capability_routes.py`
   已经不小，且这是一个概念上独立的子系统，参照
   `growth_advisor.py` 和 `capability_learning.py` 本来就是分开的
   两个模块这一先例）
3. 看板"✅ 采纳"之后是否需要立即弹出"补充大纲"的交互，还是先创建
   空大纲 Track、静默留给用户之后自己去 Track 详情页补充？（倾向
   后者，更简单，和 `CapabilityTrackStore.create()` 现有的
   "outline 可为空，之后再补"语义一致）

以上三点不影响核心架构，实现时按现有代码最贴近的先例决定即可；如果
你有明确倾向，请直接告知，我会在实现前对齐。
