# 候选人设/能力自动检测方案（Persona Candidate Auto-Scan）

- **版本**：v0.2（设计草案，尚未实现。**本轮改动**：用户反馈"候选生成
  本身也应该用 LLM，因为直接复用 growth_advisor/wiki miss 现成的主题/
  条目，是从别的场景的视角提炼出来的，角度不一样，不一定能直接映射成
  一个合适的人设/能力方向"。原方案里"候选生成规则式、只有去重判重用
  LLM"的分层被推翻——改成候选生成（从原始信号提炼出人设标题+描述）
  和去重判断都用 LLM，只是原始信号收集（哪些记忆条目/wiki miss 记录
  值得喂给 LLM）仍然是规则式的粗筛，降低 LLM 调用量级。详见 §4。）
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
| LLM 语义判重的 prompt 设计范式 | `growth_advisor.py::_llm_find_duplicate_direction()`（"给一个新标题 + 一份已有标题列表，判断是否本质同一件事，命中原样返回，未命中输出 `NONE`"）——本方案直接照搬这个契约，只是判断对象从"成长方向"换成"人设/能力方向"（去重判断复用，候选*生成*本身是本方案新增的 LLM 提炼步骤，见 §4） |
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

原方案打算"候选生成规则式（直接把 growth_advisor 的主题名/wiki miss
的检索词当候选标题）、只有去重判重过 LLM"，但这些信号本身是从别的
场景的视角提炼出来的（growth_advisor 的主题是"用户的成长方向"，wiki
miss 记录的是"某次具体检索的原始查询词"），直接拿来当"人设/能力方向
标题"角度不一定对——比如一个 growth 主题叫"Rust 异步编程"，未必适合
直接开一个叫"Rust 异步编程"的人设，可能更合适的是"系统编程陪练"这种
更贴近"人设"语境的提炼。因此**候选生成本身也要过 LLM**，只是原始信号
的采集/粗筛仍是规则式，控制喂给 LLM 的输入量级和调用频次：

1. **收集原始信号**（规则式，不调用 LLM，只做采集+粗筛，不做提炼）：
   - 复用 `growth_advisor._effective_topic_keywords()` 里
     `confirmed_by_user=True` 或 `auto_confirmed=True` 的主题及其
     `keywords`——这些是已经被验证"用户持续关注"的方向，作为原始
     素材而非直接当标题；
   - 复用 `capability_learning.py` 的 wiki miss 台账（`record_wiki_miss`
     累积的高频未命中检索）——按检索词聚类，取出现次数较高的一批
     原始查询词，同样作为素材而非直接当标题；
   - 两类信号各自按 `evidence_count`/出现次数排序，取 Top N 截断
     （避免一次把过多原始素材塞进 prompt，对齐
     `GrowthAdvisorConfig.max_pending_candidates` 的节流思路，也控制
     单次 LLM 调用的输入长度）。
2. **LLM 提炼候选**（新增的核心步骤）：
   - 把 §1 收集到的原始信号（主题名+关键词、高频未命中检索词，各自
     标注来源）整批交给一次 `llm_helper.ask(prompt)` 调用（prompt
     设计见 §4.1），要求 LLM 站在"这个人是否适合养成一个专属人设/
     能力方向来持续支撑 ta"的角度，重新提炼出 0~N 条候选，每条包含
     `title`（人设/能力方向标题，不要求和原始素材字面一致）+
     `persona_desc`（一段简介）+ `rationale`（为什么建议，需要指出
     依据了哪些原始信号）；
   - 要求结构化输出（JSON），解析失败/为空时静默跳过本轮（不重试、
     不报错中断整个扫描——对齐 `capability_learning.py` 里
     `draft_outline_with_llm()` "起草辅助而非关键路径，失败退回空"
     的一贯降级策略）；
   - 每条候选保留原始信号的弱引用（`evidence_refs`），供 §5 去重和
     用户查看理由使用。
3. **候选去重过滤**（LLM 判重，独立于第 2 步的另一次 LLM 调用）：
   - 收集"已存在标题池"：`list_personas_for_paths(paths)` 的
     `display_name`（或 `name`）+ `CapabilityTrackStore.list_tracks()`
     的 `title`（active/paused，archived 不计入，允许重新提议）；
   - 收集"近期被忽略标题池"：`status == "dismissed"` 且
     `decided_at` 在 `dismissed_cooldown_days` 冷却期内的
     `PersonaCandidate.title`；
   - 对第 2 步 LLM 提炼出的每个候选标题，调用
     `llm_helper.ask(prompt)`（prompt 设计见 §4.2），一次性把两个
     标题池都交给 LLM 判断，只要命中任意一个池子就跳过，不落盘该
     候选；
   - LLM 调用失败/输出解析不出时（对齐
     `_llm_find_duplicate_direction()` 的降级策略）：**当作不重复**，
     宁可多生成一条候选让用户手动 dismiss，也不要因为一次判断失败
     就悄悄漏掉一个真正的新方向。
4. **落盘**：通过去重过滤的候选，写入 `PersonaCandidate(status="pending")`。

这样第 2、3 步是两次独立的 LLM 调用（提炼 1 次批量调用产出多条候选，
判重则每条候选各 1 次调用——判重沿用 `_llm_find_duplicate_direction()`
现成实现，不合并进提炼那一次调用，保持"一次调用只做一件事"、prompt
职责单一，也方便判重逻辑被其它场景复用）。单轮扫描的 LLM 调用总量
= 1（提炼）+ N（判重，N = 提炼出的候选数，受 Top N 截断和
`max_pending_candidates` 节流，量级可控）。

### 4.1 LLM 候选提炼 Prompt（草案）

```
下面是从这个人最近的对话记忆/知识检索记录里整理出的一些原始信号
（不代表最终结论，只是素材）：

【持续关注的方向】（来自成长顾问，用户反复表现出兴趣并已确认）
- <主题 1>（关键词：...）
- <主题 2>（关键词：...）

【反复检索但目前没有对应知识沉淀的内容】（来自知识库未命中记录）
- <高频查询词 1>（出现 N 次）
- <高频查询词 2>（出现 N 次）

请你站在"是否值得为这个人养成一个专属的人设/能力方向，让 Agent 持续
学习、专精支撑 ta"的角度，重新判断、提炼出 0 到 5 个值得建议的人设/
能力方向。不要求和上面的原始素材字面一致——原始素材可能是从别的场景
（成长方向追踪/单次检索）提炼出来的，角度不一定适合直接当人设标题，
请你重新组织表述。如果原始素材不足以支撑任何靠谱的建议，可以输出
空列表，不要为了凑数量硬造。

请只输出如下 JSON 数组，不要输出任何其它文字：
[
  {
    "title": "人设/能力方向标题，简洁，不超过 20 字",
    "persona_desc": "一段 1-2 句的简介，说明这个人设/能力方向具体是
      指什么、大致覆盖哪些子领域",
    "rationale": "为什么建议这个方向，需要提到依据了上面哪些原始信号"
  }
]
```

解析约定：要求 LLM 只输出 JSON 数组，解析时仍要做防御式处理（strip
markdown 代码块围栏、`json.loads` 失败时整批放弃本轮提炼），对齐
项目里其它"要求 LLM 输出结构化数据"场景（如
`classify_topic_category_llm()`）的既有容错写法。

### 4.2 LLM 判重 Prompt（草案）

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
- 原始信号的采集/粗筛（哪些记忆条目/wiki miss 记录值得喂给 LLM）
  仍是规则式，不整批交给 LLM 自由发挥去翻记忆库——避免单次扫描的
  输入长度/成本失控，也避免绕开 growth_advisor/capability_learning
  已有的"知情但克制"边界（不把原始记忆条目全文交给候选提炼这一步，
  只传聚合后的主题名/关键词/高频查询词）；
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