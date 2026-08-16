# 人设能力自主学习系统设计方案（Persona Capability Learning）

- **版本**：v0.21.1（bug 修复：`run_capability_learning_cycle()` 检索没有任何结果时错误地把子主题标成 `covered`。**触发背景**：用户反馈打开某个 wiki 页面看到正文只有"（暂无检索结果）"，但对应的学习台账却写着"🔍 已检索沉淀　检索并写入 1 个 wiki 页面"，感觉不正常。**根因**：`make_wiki_writer()` 无论 `retriever` 返回的 `results` 是否为空，都会无条件写出一页——真的检索到内容就写正文，检索不到就写占位文案"（暂无检索结果）"，`page_ids` 因此永远非空；而 `run_capability_learning_cycle()` 原本直接用 `page_ids` 是否非空判断 `topic.coverage_state`，导致"其实什么也没查到"的子主题被永久标记为 `covered`——`scan_outline_gaps()` 从此再也不会把它选回候选池重试，看起来"已覆盖"实际上永远是一页空内容，学习台账的文案也没有区分"写入了有内容的页面"和"写入了占位页面"这两种情况。**修复**：新增 `topic.coverage_state` 只在真正查到内容（`results` 里至少有一条非空 `summary`/`text`）时才标记为 `covered`，否则保持/回退为 `partial`（下一轮还会被重新选中重试）；学习台账新增 `research_empty` 台账动作，文案明确写"本轮检索未获得有效结果……不计入已覆盖"；`run_capability_learning_cycle()` 返回值新增 `topics_research_empty` 计数；`maybe_dispatch_capability_notification()`"新沉淀页面数"改用 `topics_researched - topics_research_empty`，不把空占位页当作新沉淀内容推送；占位页正文文案也从"（暂无检索结果）"改为更明确的"（本轮检索未获得有效结果，后续轮次会自动重试，该子主题暂不计入已覆盖）"。见下方「检索空结果 bug 修复」小节。）
- **上一版本**：v0.21（「后续计划」三项全部完成。第 1 项：通知系统接入（`notification_enabled`/`notification_frequency`/`notification_max_per_day` + `maybe_dispatch_capability_notification()`，见「§8 通知系统接入」小节）。第 2 项：大纲动态生长建议核心逻辑 + CLI（`OutlineSuggestion` + `CapabilityOutlineSuggestionStore` + `generate_outline_suggestion_from_answer()` + `accept_outline_suggestion()` + `/capability suggestions`，见「§13.2-f 大纲动态生长建议」小节）。第 2 项补齐 HTTP API（`GET /v1/capability/suggestions`、`POST .../accept`、`POST .../dismiss`）与看板 UI（能力学习 Tab 新增「💡 大纲扩展建议」区块，采纳/忽略按钮）；第 3 项 Persona 详情页镜像视图——能力学习 Tab 新增「🎭 已发布角色一览」区块，按角色列出各自绑定的 `wiki_scopes`，实现 §11.2 末尾"双向可见"；「人设 / 能力方向列表」的「能力大纲覆盖状态」区块新增「查看」按钮直接展开对应 wiki 页面正文（`GET /v1/capability/wiki_pages/{page_id}`））
- **再上一版本**：v0.20（P1 全部计划项已实现；并提前完成了原标注在 P2/P3 的九项——`miss_observed` 台账接入 `scan_outline_gaps()` 优先级排序、LLM 辅助大纲起草（CLI `--llm-draft` + HTTP API `llm_draft` 字段 + 看板复选框）、§11.4 看板"知识范围绑定"卡片、§12.1-a `capability_map` 排序信号、§13.1-b 多 Track 公平调度、§13.2-d 知识时效性衰减（`volatility` 消费）、§13.1-c 跨 Track 子主题去重与知识共享、§10 `target_type="persona"` 人设草稿合成与发布全链路（核心库 + CLI + HTTP API + 看板 UI）。**本轮（v0.20）**：真实检索与 cron 任务评审条件已满足，`CapabilityLearningConfig.retriever_enabled` 与 `sys:capability_learning_cycle`/`sys:capability_question_sweep` 两个 cron job 的 `enabled` 字段均改为**默认开启**（opt-out，此前是 opt-in 默认关闭），见「实施状态」新增小节；同时把看板人设草稿区从最简版本（一句纯文字完成度摘要 + 单一源码预览）升级为进度条+逐维度勾选清单、渲染效果/源码双 Tab 预览、§10.4-2 真人模仿安全提示单独高亮）
- **定位**：mini_agent 新增能力设计方案——让 Agent 围绕用户设定的一个**能力人设/方向**（例如"希望你具备强大的股票分析能力"），持续自主地从互联网检索、整理、沉淀为 wiki 知识，并在必要时**异步**向用户提问以获取只有用户才知道的信息（偏好、真实需求边界、私有语境），全程不阻塞任何一方。
- **一句话概括**：复用 `growth_advisor.py`（信号→候选→调研→反馈闭环）与 `wiki/`（写入/去重/关联/检索）已经跑通的架构范式，新增一条服务对象是"Agent 自身某项专精能力"而不是"用户成长方向"或"Agent 通用自我进化"的平行闭环，并补齐一个此前项目里没有的能力：**Agent 主动提问、用户异步作答、Agent 消费答案继续推进**的问答队列机制。同一套循环骨架进一步延伸到 `.agent/personas/` 角色扮演系统：既可以用来**持续养成一个新的人设**（第 10 节），也可以让**每个角色拥有自己专属的 wiki 检索范围**，让"人设的专业感"从语气层面真正落到回答内容层面（第 11 节）。

---

## 实施状态

本节记录方案落地进度，每完成一个阶段就更新，保持和代码库实际状态同步，
避免文档和实现脱节。

### 检索空结果 bug 修复（v0.21.1）—— ✅ 已实现

**现象**：用户打开某个 wiki 页面看到正文只有"（暂无检索结果）"，但
对应的学习台账却写着"🔍 已检索沉淀　检索并写入 1 个 wiki 页面"——看起来
矛盾：明明"没检索到结果"，为什么台账说"检索并写入"了？

**根因**：`make_wiki_writer()`（`_writer` 闭包）无论 `retriever` 返回的
`results` 是否为空都会**无条件写出一页**——真的检索到内容就写正文，
检索不到任何东西就写占位文案"（暂无检索结果）"，两种情况下
`page_ids` 都是非空的（`[page_id]`）。而 `run_capability_learning_cycle()`
原本直接拿 `page_ids` 是否非空来判断 `topic.coverage_state`：

```python
topic.coverage_state = "covered" if page_ids else "partial"  # 改动前
```

这意味着"检索器压根没查到任何东西"（比如 `make_web_search_retriever()`
里 `provider.search()` 抛出 `WebSearchError` 被兜底吞掉、返回 `[]`）的
子主题，也会被永久标记为 `covered`。`scan_outline_gaps()` 只会把
非 `covered`（或 `covered` 但触发了知识时效性衰减）的子主题选回候选池，
一个被误标为 `covered` 的空内容子主题从此**再也不会被重试**——用户
看到的"已覆盖"状态和"关联 1 篇 wiki 页面"都是真的，但那篇页面从头到尾
只有一句占位文案，永远不会被后续轮次修正。

**修复**：

| 项目 | 修复前 | 修复后 |
|---|---|---|
| `topic.coverage_state` 判定依据 | `page_ids` 是否非空（`wiki_writer` 是否写出了页面） | `results`（`retriever` 的原始返回值）里是否至少有一条非空 `summary`/`text`——即真的查到内容，而不是"写出了文件"这个更弱的条件 |
| 检索没有结果时的台账 `action` | `researched`（和真正查到内容时完全一样，无法区分） | `research_empty`，`summary` 明确写"本轮检索未获得有效结果（写入了占位页面，下轮会重新尝试，不计入已覆盖）" |
| `run_capability_learning_cycle()` 返回值 | 无对应字段 | 新增 `topics_research_empty` 计数，`/capability cycle` CLI 输出同步展示 |
| `maybe_dispatch_capability_notification()` 的"新沉淀页面数" | 直接用 `topics_researched`（包含空占位页） | 改用 `topics_researched - topics_research_empty`，不把空占位页当作"新沉淀内容"推送 |
| 占位页正文文案 | `（暂无检索结果）`，容易被误读成"系统确认了这个方向没有可查内容" | `（本轮检索未获得有效结果，后续轮次会自动重试，该子主题暂不计入已覆盖）`，明确传达"这不是终态，会重试" |

`topic.coverage_state` 判定改用 `results` 而不是 `page_ids`，是因为
`page_ids` 反映的是"`wiki_writer` 这一步做了什么"，而 `coverage_state`
本该反映的是"这个子主题的知识缺口是否真的被填上了"——两者在
`make_wiki_writer()` 的默认实现里被意外耦合到了一起（写占位页也算
"写了页面"），修复后用更贴近语义本身的信号（有没有真实检索到内容）
来判断，同时保留"无论如何都写一页占位文档"这个原有设计（方便用户
在页面里直接看到"系统尝试过但没查到"的记录，而不是完全没有痕迹）。

**已知局限（暂不处理）**：修复后，一个持续查不到内容的子主题
（比如查询词本身过于生僻、或长期处在错误配置的检索环境下）会在每一轮
`sys:capability_learning_cycle` 里被反复选中、反复检索、反复写出占位页，
没有引入"连续失败 N 次后自动降级/跳过"的熔断机制——这不是本次要解决
的问题（本次是修正"错误标记为已覆盖"这个更基础的正确性 bug），如果
后续观察到真实的重复空转消耗过多检索配额，需要单独评估。

**测试**：新增 `tests/test_capability_learning_empty_retrieval_fix.py`
（7 项，覆盖检索无结果时 `coverage_state` 保持 `partial`、台账
`action=research_empty` 且文案正确、空结果子主题下一轮确实被重新
选中检索、真实结果时行为不变（回归）、`summary`/`text` 全是空白字符串
时同样视为无效结果、通知函数在"全是空占位"时不发送/在"有真实新内容"
时正常发送两种场景）；全部通过。`tests/test_capability_learning_p1.py`
+ `tests/test_capability_cmd.py` + `tests/test_capability_routes_mount.py`
+ `tests/test_capability_notification_v021.py` +
`tests/test_capability_outline_suggestions_v021.py` 共 126 项全部通过，
无回归。

**改动文件**：
- `src/mini_agent/evolution/capability_learning.py`
  （`run_capability_learning_cycle()` 新增 `has_real_content` 判断、
  `research_empty` 台账动作、`topics_research_empty` 汇总字段；
  `make_wiki_writer()` 占位页正文文案更新；
  `maybe_dispatch_capability_notification()` 改用差值计算新沉淀页面数）
- `src/mini_agent/cli/commands/capability_cmd.py`（`/capability cycle`
  输出追加"其中 N 个检索未获得有效结果"提示）
- `tests/test_capability_learning_empty_retrieval_fix.py`（新增，7 项）
- `next_doc/persona_capability_learning_design.md`（本节）

### P1（最小可用闭环）—— ✅ 数据层与逻辑层已实现，未接入真实检索/wiki 写入

| 项目 | 状态 | 对应文件 |
|---|---|---|
| `CapabilityTrack` / `OutlineTopic` / `CapabilityLedgerEntry` / `CapabilityQuestion` 数据模型 | ✅ 已实现 | `src/mini_agent/evolution/capability_learning.py` |
| 存储路径（`capability_tracks_path` / `capability_ledger_path` / `capability_questions_path`） | ✅ 已实现 | `src/mini_agent/storage/paths.py` |
| Track CRUD（`CapabilityTrackStore`） | ✅ 已实现 | 同上 |
| 大纲缺口扫描 `scan_outline_gaps()`（规则式：uncovered 优先，partial 按最久未触达优先） | ✅ 已实现 | 同上 |
| `needs_user_context()` 判定（P1 版本：persona 型默认需要用户输入，knowledge 型默认不需要，更细致判定留 P2） | ✅ 已实现（占位规则） | 同上 |
| `CapabilityQuestion` 异步问答队列（生成 `raise_question` / 提交 `answer` / 忽略 `dismiss` / 过期清理 `sweep_expired`） | ✅ 已实现 | 同上 |
| `CapabilityLedgerEntry` 台账（`CapabilityLedgerStore`） | ✅ 已实现 | 同上 |
| 单轮循环编排 `run_capability_learning_cycle()` | ✅ 已实现，**检索/wiki 写入以可注入回调形式暴露**，未接线时会安全跳过并记录 `action="skipped"` 台账 | 同上 |
| `make_wiki_writer(paths)`：真实 wiki 写入回调（对接 `wiki/writer.py`） | ✅ 已实现并有单测覆盖（含端到端跑通一轮循环、验证落盘页面可被 `wiki/parser.py` 正确解析）。**未接入 `wiki/dedup.py` 判重**——需要先确认"按 wiki_tag 批量加载已有页面"该走哪条现成接口，留到接线阶段和 wiki 模块维护者一起确认，不猜测拼接 | 同上 |
| 真实 `retriever` 回调（对接 `web_search`） | ⏳ 未实现——需要真实网络请求，P1 单测里全部用假实现替代，避免单测依赖外部网络 | — |
| `record_wiki_miss()`（§14.1-a 使用驱动学习的记录接口） | ✅ 已实现，`context_builder.py` 已接线（见「第 11 节」小节下方新增说明）；`scan_outline_gaps()` 消费这批台账调整排序留到 P2 | 同上 |
| HTTP API（`/v1/capability/tracks`、`/v1/capability/questions` 等 9 个端点，对应 §7.1） | ✅ 已实现为独立 router，**本轮已挂载到 `api/server.py`**（`create_app()` 里 `app.include_router(router)` 之后新增 `app.include_router(capability_router)`），端点对外可用 | `src/mini_agent/api/capability_routes.py`、`src/mini_agent/api/server.py` |
| 单元测试（14 组用例，覆盖 CRUD / 缺口排序 / 异步问答生命周期 / 循环编排的多种分支 / 真实 wiki 写入落盘与解析回读 / 端到端一轮循环） | ✅ 全部通过 | `tests/test_capability_learning_p1.py` |
| API 挂载单测（4 组：列表为空 / 创建+详情 / 问答提交 / 删除 404） | ✅ 已实现并全部通过 | `tests/test_capability_routes_mount.py` |
| cron 注册 `sys:capability_learning_cycle` | ✅ 已在 `cron_scheduler.py` 的 `SYSTEM_JOBS` 里注册（连同 `sys:capability_question_sweep` 过期问题清理），task_template 引用 §4 的 `/capability cycle` / `/capability questions --sweep-expired`。**v0.20 起默认 `enabled: True`**（opt-out，见文档开头「后续计划」小节），用户仍可在看板「⏰ Cron 任务」Tab 或 CLI 手动关闭 | `src/mini_agent/evolution/cron_scheduler.py` |
| 看板三个 UI 区域（人设管理 / 进度展示 / 待回答问题） | ✅ 已实现为新 Tab「🎓 能力学习」（挂在「🌱 成长顾问」之后），三区域：新建/管理 Track（暂停恢复/二次确认删除，不级联删 wiki）、大纲覆盖状态 + 学习台账、待回答问题（提交/忽略）+ 折叠历史问答。cron 已注册，v0.20 起默认开启（见文档开头「后续计划」小节），UI 上暂未加"距离下次学习还有多久"之类的倒计时展示，后续可评估是否补上 | `apps/mini_agent_kanban/app.py`（`render_capability_tab`）、`apps/mini_agent_kanban/client.py`（新增 11 个 `capability_*` 方法） |
| `context_builder.py` 接入检索复用 | ✅ 经代码走查确认已经**天然满足**，不需要额外实现：`_try_inject_wiki_search()` 本来就是"命中就注入"的全库/`tags` 范围检索（§11 的 `wiki_scopes` 只是把 `tags` 收窄到当前 persona 范围，不传 `wiki_scopes` 时就是全库检索），knowledge 型 Track 沉淀的 wiki 页面天然会被这条既有链路检索命中并注入——§6 想要的"命中 active Track 时按需注入"和现有"每轮 turn 命中就注入"是同一件事，没有必要再实现一层重复的"识别话题是否命中某个 Track"关键词匹配（重复匹配反而增加噪音，与项目一贯的"不重复实现"原则一致）。§14.1-a 的未命中记录部分本来就已提前完成 | `src/mini_agent/context_builder.py`（未改动，本次为确认结论，非新代码） |
| 真实 `retriever` / `wiki_writer` 回调实现（对接 `web_search` 与 `wiki/writer.py` + `wiki/dedup.py`） | ✅ `make_web_search_retriever(cfg)` 已实现并接入 `/capability cycle`：query 用 `f"{track.title} {topic.name}"` 朴素拼接，复用既有 `web_search/factory.py` 的 provider 抽象（不新增检索后端），结果截断到 `summary_max_chars`（默认 400 字），`WebSearchError` 兜底为空结果走既有 skipped 路径。写入前经过 `apply_compliance_filter()`，不会绕过。**v0.20 起默认开启**：`CapabilityLearningConfig.retriever_enabled = True`（此前默认 False，见文档开头「后续计划」小节），评审条件（§13.3-g 合规过滤已实现并有测试覆盖）已满足，改为与 `GrowthAdvisorConfig` 一致的默认开启取向；用户仍可在配置里显式关回 `False` | `src/mini_agent/evolution/capability_learning.py`（`make_web_search_retriever`）、`src/mini_agent/config/models.py`（`CapabilityLearningConfig`）、`src/mini_agent/cli/commands/capability_cmd.py` |
| §13.3-g 合规过滤（写入前剔除具体买卖建议等风险表述 + 金融/医疗/法律领域页面加 `requires_disclaimer` 标记） | ✅ 已实现并接入 `make_wiki_writer()` 写入路径：`apply_compliance_filter()` 做句级关键词/正则过滤（整句剔除，不做局部改写），`is_disclaimer_required_track()` 按 Track 标题/描述/wiki_tag 关键词判定风险领域；命中时 frontmatter 加 `requires_disclaimer: true` + 正文追加"仅供参考"免责声明。规则式实现，不接 LLM 改写（见函数上方注释的取舍说明） | `src/mini_agent/evolution/capability_learning.py` |

**P1 阶段的设计取舍说明**：数据模型、存储、核心逻辑（缺口扫描、异步问答队列、循环编排）已经是可以真实跑起来、有测试覆盖的代码，但两处"会产生真实外部副作用"的关口——① 真正调用互联网检索和 wiki 写入、② 挂载到 cron 定时任务表使其自动周期性运行——都刻意留了一步显式开关，需要在功能评审通过、且第 14.3-g 合规过滤到位之后再接线，不在 P1 这一步默认打开。这是为了让"代码已经写好、可测试"和"功能对用户/系统实际生效"两件事分开推进，降低一次性铺开的风险。

### §8 通知系统接入（v0.21 第 1 项）—— ✅ 已实现

| 项目 | 状态 | 对应文件 |
|---|---|---|
| `CapabilityLearningConfig.notification_enabled` / `notification_frequency`（`"daily"` \| `"kanban_only"`）/ `notification_max_per_day`：独立于 `GrowthAdvisorConfig` 的一套字段，节流状态也单独落盘，不共用 growth_advisor 的额度 | ✅ 已实现 | `src/mini_agent/config/models.py` |
| `capability_notify_state_path()`：`.agent/capability_notify_state.json`，记录 `last_notify_date`/`notify_count_today` | ✅ 已实现 | `src/mini_agent/storage/paths.py` |
| `maybe_dispatch_capability_notification(paths, cfg, cycle_summary, pending_questions_count)`：空轮（本轮无新问题且无新页面）不占额度不发送；`notification_frequency="kanban_only"` 或 `notification_enabled=False` 时不发送；当天额度耗尽时不发送；否则拼一条摘要（待回答问题数 / 本轮新沉淀页面数 / 本轮新生成问题数），走 `NotificationDispatcher` 发送，异常统一 `log_exception` 兜底不影响主流程 | ✅ 已实现 | `src/mini_agent/evolution/capability_learning.py` |
| `/capability cycle`（对应 `sys:capability_learning_cycle` 引用的中间层命令）跑完 `run_capability_learning_cycle()` 后调用上述函数，`try/except` 包裹不影响命令本身的输出 | ✅ 已实现 | `src/mini_agent/cli/commands/capability_cmd.py` |
| 单元测试（6 组：空轮不发送 / 有新页面触发 / 当天额度耗尽后节流 / kanban_only 不发送 / 关闭开关不发送 / `cfg=None` 走默认值仍能发送） | ✅ 全部通过 | `tests/test_capability_notification_v021.py` |

未做：多轮循环产生的新内容目前是"当轮汇总"而非"跨轮持续累加直到真正推送成功"——如果当天额度已耗尽，未推送出去的那部分新增内容不会被下一次成功推送时一并带上（不像 growth_advisor 的 `_pop_pending_pursuit_digest_lines` 那样有专门的待推送队列）。这属于"下一条摘要只反映最新一轮，不追溯此前被节流掉的部分"，评估后认为可接受（能力学习的新增内容本身可以在看板随时查看，不依赖推送），暂不在本轮引入额外的待推送队列机制。

### §13.2-f 大纲动态生长建议（v0.21 第 2 项）—— ✅ 已实现

| 项目 | 状态 | 对应文件 |
|---|---|---|
| `OutlineSuggestion` 数据模型（`suggestion_id`/`track_id`/`source_question_id`/`suggested_name`/`rationale`/`status: pending\|accepted\|dismissed`） | ✅ 已实现 | `src/mini_agent/evolution/capability_learning.py` |
| `capability_outline_suggestions_path`（`.agent/capability_outline_suggestions.jsonl`） | ✅ 已实现 | `src/mini_agent/storage/paths.py` |
| `CapabilityOutlineSuggestionStore`（list/add/dismiss/mark_accepted，风格对齐 `CapabilityQuestionStore`：整体读出/内存改/整体写回） | ✅ 已实现 | `src/mini_agent/evolution/capability_learning.py` |
| `generate_outline_suggestion_from_answer(track, question, llm_helper, existing_pending_names)`：无 `llm_helper` 时整体跳过（不做规则式猜测）；LLM 判定无新方向（约定输出 `NONE`）或解析不出有效名称时返回 `None`；命中但与现有大纲子主题或已有 pending 建议高度相似（复用 §13.1-c `_topic_name_similarity`，同一阈值）时视为重复，不生成 | ✅ 已实现 | 同上 |
| `accept_outline_suggestion(paths, suggestion_id)`：追加为新 `OutlineTopic`（`coverage_state="uncovered"`）写回 Track 大纲，并把建议标记为 accepted；Track 已被删除/建议不存在或已处理过时返回 `None`，不抛异常 | ✅ 已实现 | 同上 |
| `run_capability_learning_cycle(..., llm_helper=None)`：消费已回答问题的同时，拿到 `llm_helper` 就调用上述生成函数，命中则落一条 `action="outline_suggested"` 台账并计入 `summary["outline_suggestions_generated"]`；不传 `llm_helper` 时行为与此前完全一致（向后兼容） | ✅ 已实现 | 同上 |
| `/capability cycle`：接入 `_get_llm_helper(agent)`，拿得到就透传给 `run_capability_learning_cycle`，本轮结果里新增"生成大纲建议 N 条"提示；`/capability suggestions [track_id]`（列出 pending）/ `/capability suggestions accept\|dismiss <suggestion_id>`（采纳/忽略） | ✅ 已实现 | `src/mini_agent/cli/commands/capability_cmd.py` |
| 单元测试（8 组：无 llm_helper 跳过 / LLM 判定 NONE 跳过 / 命中生成建议 / 与既有大纲重复被去重 / 采纳建议追加子主题 / 采纳未知建议返回 None / cycle 传 llm_helper 生成建议 / cycle 不传时不生成） | ✅ 全部通过 | `tests/test_capability_outline_suggestions_v021.py` |

**未做的部分**（本次不在范围内，留给后续轮次）：
- HTTP API 端点（`GET /v1/capability/suggestions`、`POST /v1/capability/suggestions/{id}/accept`、`.../dismiss`）—— ✅ **本轮已补齐**，见下方「§13.2-f 大纲扩展建议 API + 看板 UI（本轮补齐）」小节
- 看板 UI 展示 pending 建议列表 + 一键采纳/忽略按钮—— ✅ **本轮已补齐**，见下方同一小节

### §13.2-f 大纲扩展建议 API + 看板 UI（本轮补齐）—— ✅ 已实现

| 项目 | 状态 | 对应文件 |
|---|---|---|
| `GET /v1/capability/suggestions`（支持 `status`/`track_id` 过滤）/ `POST /v1/capability/suggestions/{id}/accept`（复用 `accept_outline_suggestion()`，不重复业务逻辑）/ `POST /v1/capability/suggestions/{id}/dismiss` | ✅ 已实现 | `src/mini_agent/api/capability_routes.py` |
| 看板 client 方法 `capability_outline_suggestions()` / `accept_capability_outline_suggestion()` / `dismiss_capability_outline_suggestion()` | ✅ 已实现 | `apps/mini_agent_kanban/client.py` |
| 能力学习 Tab 新增「💡 大纲扩展建议」区块：列出 pending 建议（建议名称 + 来源理由 + 所属 Track），逐条「采纳」/「忽略」按钮，风格对齐既有「❓ 待回答问题」区块 | ✅ 已实现 | `apps/mini_agent_kanban/app.py`（`render_capability_tab`） |
| 单元测试（5 组：列表为空 / 采纳追加子主题 / 忽略 / 采纳未知建议 404 / 忽略未知建议 404） | ✅ 全部通过 | `tests/test_capability_routes_mount.py`（新增 `TestCapabilityOutlineSuggestionRoutes`） |

### 第 11 节（`PersonaProfile.wiki_scopes`）—— ✅ 已提前实现

按第 14 节末尾的说明（"这一项依赖优先级最高……可以考虑提前到 P2 甚至 P1 收尾阶段单独插入"）提前插入实现，因为它不依赖 P1 其余部分（`CapabilityTrack` 是否接线检索/cron 都无所谓），只是把已有的 `wiki_shelf_search(tags=...)` 能力接到 persona 系统上，改动面很小：

| 项目 | 状态 | 对应文件 |
|---|---|---|
| `PersonaProfile.wiki_scopes` 字段 + frontmatter 解析（`wiki_scopes:` YAML 列表或逗号分隔字符串，空/不填=不限制，向后兼容） | ✅ 已实现 | `src/mini_agent/orchestrator/persona_profiles.py` |
| `context_builder.py` 每轮检索透传 `wiki_scopes` 到 `library.wiki_search(tags=...)` | ✅ 已实现（`ContextBuilder._active_persona_wiki_scopes()`） | `src/mini_agent/context_builder.py` |
| 软优先语义（§11.3） | ✅ 天然满足，未额外实现"硬限制/回退"逻辑——`wiki_shelf_search` 的 `tags` 参数本身只参与 `_rule_score` 打分，不是候选过滤条件，限定范围内零命中时本来就会检索到范围外页面，不需要在 `context_builder.py` 里再实现一次"零命中回退全库"的逻辑 | `src/mini_agent/wiki/search.py`（既有实现，未改动） |
| 文档 | ✅ 已更新 | `docs/persona-guide.md` |
| 单元测试（7 组：未激活 persona / persona 未声明字段 / 声明了字段透传 tags / loader 异常静默降级 / frontmatter 解析 2 组） | ✅ 全部通过 | `tests/test_context_builder_persona_wiki_scopes.py` |

**未做的部分**（本次不在范围内，留给第 10 节 persona 型 Track 或看板迭代）：
- "超出边界"的台账/日志标注（§11.3 提到的可选项）——`wiki_shelf_search` 本身不返回"这次是否命中了限定范围外的页面"这类信息，要做需要先给 `WikiSearchResult` 加字段，评估后决定是否值得为一条弱提示改动检索层返回结构，暂不在这次里做

### §11.4（看板"知识范围绑定"）—— ✅ 本轮已实现

依赖的 `CapabilityTrack` 看板 UI（第 7 节）已经落地，本轮补齐这一项：

| 项目 | 状态 | 对应文件 |
|---|---|---|
| `list_personas_for_paths(paths)` / `set_persona_wiki_scopes(source_path, scopes)`：扫描 project+global 两级 personas 目录列出全部角色；精确定位 frontmatter 里的 `wiki_scopes:` 行做替换/插入并写回磁盘（不整体反序列化重 dump，避免破坏用户手写格式） | ✅ 已实现 | `src/mini_agent/orchestrator/persona_profiles.py` |
| `GET /v1/capability/personas`：列出所有 persona 及其 `wiki_scopes` | ✅ 已实现 | `src/mini_agent/api/capability_routes.py` |
| `POST /v1/capability/personas/{name}/wiki_scopes`：整体替换某个 persona 的 `wiki_scopes`（看板前端本地增删数组后整体提交，不做单个 tag 的增/删两个端点），写回成功后尝试 `PersonaLoader.rediscover()` 让运行中 agent 立即感知 | ✅ 已实现 | 同上 |
| 看板 Track 详情页「知识范围绑定」卡片（仅 knowledge 型、已填 `wiki_tag` 的 Track 展示）：展示当前绑定的角色列表 + `st.popover` 里逐个角色勾选框绑定/解绑 | ✅ 已实现 | `apps/mini_agent_kanban/app.py`（`render_capability_tab`）、`apps/mini_agent_kanban/client.py`（`list_capability_personas` / `set_persona_wiki_scopes`） |
| 弱提示：存在 knowledge 型 Track 时，列出当前"未绑定任何知识范围"的角色名单，提示可以关联（不强推送，纯展示） | ✅ 已实现 | `apps/mini_agent_kanban/app.py` |
| 单元测试（9 组：工具函数列出/插入/替换/清空/无 frontmatter 失败 5 组，API 端点列出/设置/未知角色 404 3 组，共 8 个测试方法覆盖 9 个断言场景） | ✅ 全部通过 | `tests/test_capability_persona_wiki_scopes_binding.py` |

Persona 详情页反向展示"绑定的知识范围"列表未单独实现——现有 `/role` CLI 侧没有一个"persona 详情页"承载这类信息，看板目前也没有独立的 Persona 管理 Tab；本轮选择把双向可见性做在 Track 详情页一侧（已绑定角色列表 + 全局未绑定弱提示），已能覆盖 §11.2 末尾"双向可见"的核心诉求，若后续新增独立的 Persona 管理 Tab，可以在那里补上镜像视图。

### Persona 详情页镜像视图（v0.21 第 3 项）—— ✅ 已实现

§11.4 已经做了 Track 详情页「知识范围绑定」卡片的正向视图（"这个 Track 被哪些角色引用"）；本轮补上反向的镜像视图，实现设计文档 §11.2 末尾"双向可见"：

| 项目 | 状态 | 对应文件 |
|---|---|---|
| 能力学习 Tab 新增「🎭 已发布角色一览」区块：复用 `list_capability_personas()`（与知识范围绑定卡片同一份数据源，不额外请求），按角色列出 `display_name` + 已绑定的 `wiki_scopes`（未绑定则显示"不限定范围（检索全库）"） | ✅ 已实现 | `apps/mini_agent_kanban/app.py`（`render_capability_tab`） |

未新开独立的 Persona 管理 Tab——沿用文档此前的判断，双向可见性做在同一个 Tab 的两个区块里已经足够，若后续这个 Tab 内容变得过于拥挤，再评估是否拆分独立 Tab。

### §14.1-a（`record_wiki_miss()` 接线）—— ✅ 已提前实现

顺着第 11 节的 `wiki_scopes` 顺手接上：既然 `context_builder.py` 现在已经知道"当前激活角色绑定了哪个 wiki_tag"，就可以用同一份信息判断"这次未命中的检索是不是恰好落在某个 knowledge 型 Track 的范围内"，不需要额外猜测。这比 §14.1-a 原始设想（对所有未命中查询做关键词/语义匹配去猜它属于哪个 Track）风险小得多——只在能通过 `wiki_scopes ∩ wiki_tag` 明确关联时才记录，避免把"这个概念本来就该自己查"的普通未命中也算作缺口信号，符合设计文档 §3.3"生成问题要克制"的同一条原则延伸到"记录缺口信号也要克制"。

| 项目 | 状态 | 对应文件 |
|---|---|---|
| `ContextBuilder._maybe_record_capability_wiki_miss()`：wiki_search 未命中 + persona `wiki_scopes` 命中某个 active knowledge 型 Track 的 `wiki_tag` 时，调用 `record_wiki_miss()` | ✅ 已实现 | `src/mini_agent/context_builder.py` |
| `MemoryConfig.capability_wiki_miss_tracking_enabled` 配置开关（默认开，未绑定 persona/wiki_scopes 时零开销，关闭后完全跳过） | ✅ 已实现 | `src/mini_agent/config/models.py` |
| 单元测试（5 组：命中记录 / 不命中不记录 / 无激活 persona 不记录 / 配置关闭不记录 / 记录环节异常不影响主流程） | ✅ 全部通过 | `tests/test_context_builder_persona_wiki_scopes.py` |

**已收尾**：
- `scan_outline_gaps()` 消费 `miss_observed` 台账、据此调整候选排序——原设计文档标注在 P2，已提前实现：`_topic_miss_counts()` 统计最近 200 条台账中各子主题的未命中次数，`scan_outline_gaps(track, miss_counts=...)` 在同一 coverage_state 内按 miss 次数降序排列，`run_capability_learning_cycle()` 已接线调用。不传 `miss_counts` 时行为与此前完全一致（向后兼容）
- cron 已注册（v0.20 起默认开启，见上方 P1 表格），`/capability cycle` 手动触发时同样会读取这份台账并影响排序，不依赖 cron 是否已开启

### §12.1-a（`capability_map` 排序信号）—— ✅ 本轮已实现

设计文档 §12.1 把这一项列为"可直接采纳（低风险、单向依赖）"：只读消费 `perception/self_model.py` 已有的 `consolidation.build_capability_map()`，不引入新的自动化决策链路。本轮接入：

| 项目 | 状态 | 对应文件 |
|---|---|---|
| `_topic_capability_confidence(track, paths)`：只读调用 `build_capability_map(paths, None)`（`memory_backend=None`，只读不写回），用关键词双向子串匹配（`domain in topic.name` 或 `topic.name in domain`，不区分大小写）把领域置信度映射到子主题；一个子主题匹配多个 domain 时取置信度最低者；`consolidation` 不可用或无数据时静默返回空字典 | ✅ 已实现 | `src/mini_agent/evolution/capability_learning.py` |
| `scan_outline_gaps(track, ..., capability_confidence=None)`：排序在 `miss_counts` 之后、`last_touched_at` 之前新增一级——置信度越低越优先；未匹配到 capability_map 条目的子主题给中性值 0.5（不因"没数据"被排到最后，也不会抢在明确低置信度子主题前面）；不传该参数时行为与此前完全一致 | ✅ 已实现，向后兼容 | 同上 |
| `run_capability_learning_cycle()` 接线：每轮为每个 Track 计算一次 `capability_confidence` 并传给 `scan_outline_gaps()` | ✅ 已实现 | 同上 |
| 单元测试（5 组：关键词匹配置信度 / 无 manifest 时返回空 / 排序 tie-break 生效 / 不传参数向后兼容 / `run_capability_learning_cycle` 确实完成接线） | ✅ 全部通过 | `tests/test_capability_learning_p1.py` |

单向只读消费，不写回 `capability_map`、不影响 `self_model.py` 自身读取逻辑，符合设计文档 §12.1-a 的边界要求。

### §13.1-b（多 Track 之间的公平调度）—— ✅ 本轮已实现

设计文档 §13.1 把这一项列为"建议优先纳入 P1/P2，成本低"：

| 项目 | 状态 | 对应文件 |
|---|---|---|
| `CapabilityTrack.last_advanced_at`：记录该 Track 上次在 `run_capability_learning_cycle()` 里真正被推进（处理过至少 1 个子主题，不论 researched/question_raised/skipped）的时间戳，None=从未推进过；旧 Track 文件无该字段时 `from_dict` 默认 None，向后兼容 | ✅ 已实现 | `src/mini_agent/evolution/capability_learning.py` |
| `run_capability_learning_cycle()` 按 `last_advanced_at` 升序处理 active Track（从未推进过/最久没推进过的优先） | ✅ 已实现 | 同上 |
| `max_topics_per_run_cycle` 可选全局预算参数：多个 Track 共享同一份预算，预算耗尽的 Track 本轮不再推进；默认 `None`（不设上限，向后兼容——没有全局预算时，排序变化不影响最终结果，因为每个 Track 依然各自跑满 `topics_per_cycle`，只是处理顺序不同） | ✅ 已实现 | 同上 |
| 消费"已回答问题"这一步（成本可忽略）不占用全局预算、不受限，任何情况下都会为每个 active Track 执行 | ✅ 已实现 | 同上 |
| 单元测试（5 组：`last_advanced_at` 在处理后更新 / 无子主题可推进时不更新 / 公平排序在预算受限时生效 / 不传预算参数向后兼容 / 与既有测试无冲突） | ✅ 全部通过 | `tests/test_capability_learning_p1.py` |

`sys:capability_learning_cycle` cron job 目前调用 `run_capability_learning_cycle()` 时未显式传 `max_topics_per_run_cycle`（沿用默认 `None`），即当前实际运行行为不变——这一项先把机制和字段落地、验证正确，是否要在 cron 任务里默认打开全局预算（以及预算取多少合适）留给后续结合真实使用量评估，不在本轮直接改变默认运行行为。

### §4（`/capability` slash command 中间层）—— ✅ 已提前实现

设计文档写作过程中发现的那层缺口——`cron_scheduler.py` 的 `sys:` 内置任务是"生成 `task_template` 文本交给 Agent 执行"而不是直接调用 Python 函数，真正接线 cron 前需要先有一个 slash command——本次补上：

| 项目 | 状态 | 对应文件 |
|---|---|---|
| `/capability [list]`：展示所有 Track 概况 | ✅ 已实现 | `src/mini_agent/cli/commands/capability_cmd.py` |
| `/capability create <title> \| <persona_desc> [--llm-draft]`：创建 knowledge 型 Track | ✅ 已实现；`--llm-draft` 用 `agent.llm_helper` 起草初始大纲（§14 P2，已提前实现，见下方「§14 P2：LLM 辅助大纲起草」小节），不加这个 flag 时仍是原有的空大纲行为 | 同上 |
| `/capability cycle`：手动触发一轮学习循环，等价于 `sys:capability_learning_cycle` 执行的内容 | ✅ 已实现；是否使用真实检索由 `CapabilityLearningConfig.retriever_enabled` 配置项控制（v0.20 起默认 True），关闭时 `retriever=None`，需要检索的子主题仍会被安全跳过并记 `skipped` 台账；默认打开时用 `make_web_search_retriever(cfg)`，写入前仍经过 §13.3-g 合规过滤 | 同上 |
| `/capability questions [track_id]` / `/capability questions --sweep-expired` / `/capability answer <id> <text>`：异步问答队列的 CLI 入口 | ✅ 已实现 | 同上 |
| repl.py 分发、`cli/commands/__init__.py` 导出、`/help`（`parser.py`）文案 | ✅ 已同步更新 | `src/mini_agent/cli/repl.py`、`src/mini_agent/cli/commands/__init__.py`、`src/mini_agent/cli/parser.py` |
| 单元测试（10 组：无 agent / 空列表 / 创建 / cycle 空跑 / cycle 在真实 Track 上确认不产生"假装学会了"的覆盖率变化 / 问答队列列出与提交 / 未知子命令） | ✅ 全部通过 | `tests/test_capability_cmd.py` |

cron 任务表（`cron_scheduler.py::SYSTEM_JOBS`）已注册 `sys:capability_learning_cycle` / `sys:capability_question_sweep`，v0.20 起默认 `enabled: True`（opt-out，理由见该条目上方注释及文档开头「后续计划」小节）。

### §14 P2：LLM 辅助大纲起草 —— ✅ 已提前实现

| 项目 | 状态 | 对应文件 |
|---|---|---|
| `draft_outline_with_llm(title, persona_desc, llm_helper)`：用 LLM 起草 3-8 个初始大纲子主题名称 | ✅ 已实现。"能用就用，用不了就当没发生"：`llm_helper` 抛异常/返回空/解析出的条数不在 `[DRAFT_OUTLINE_MIN_TOPICS, DRAFT_OUTLINE_MAX_TOPICS]`（3-8）范围内，一律返回空列表，调用方退回空大纲，不重试、不报错 | `src/mini_agent/evolution/capability_learning.py` |
| `CapabilityTrackStore.create(..., llm_helper=...)`：`outline_names` 为空且传入了 `llm_helper` 时调用上面这个函数起草大纲 | ✅ 已实现，显式传了 `outline_names` 时不会调用 LLM（避免意外覆盖用户/调用方指定的大纲） | 同上 |
| CLI `/capability create ... --llm-draft`：复用 `growth_cmd.py::_get_llm_helper` 同款把 `agent.llm_helper` 包成 `Callable[[str], str]` 的约定 | ✅ 已实现 | `src/mini_agent/cli/commands/capability_cmd.py` |
| HTTP API `POST /v1/capability/tracks` 的 `llm_draft: bool` 字段，`_get_llm_helper(request)` 从 `request.app.state.bridge.agent.llm_helper` 取 | ✅ 已实现，与 CLI 侧同款容错（拿不到 helper 时静默退回空大纲） | `src/mini_agent/api/capability_routes.py` |
| 看板「➕ 新建能力 / 人设方向」表单新增"用 LLM 起草初始大纲"复选框 | ✅ 已实现 | `apps/mini_agent_kanban/app.py`、`apps/mini_agent_kanban/client.py` |
| 单元测试（10 组：解析多行/条数校验/空响应/异常兜底/`create()` 走 LLM 路径/`outline_names` 优先级/失败兜底空大纲/HTTP 端点两条路径） | ✅ 全部通过 | `tests/test_capability_learning_p1.py`、`tests/test_capability_routes_mount.py` |

### §13.2-d（知识时效性衰减，`volatility` 消费）—— ✅ 本轮已实现

`OutlineTopic.volatility` 字段在 P1 就已带上（避免后续迁移），但此前 `scan_outline_gaps()` 从不消费它——`covered` 子主题一旦覆盖就永久排除在候选之外，会出现"名义覆盖率 100%，内容早已过期"的假象（比如"当前宏观利率环境"这类 `volatile` 内容，检索一次之后再也不会被重新推进）。本轮补上：

| 项目 | 状态 | 对应文件 |
|---|---|---|
| `STALENESS_SECONDS_BY_VOLATILITY`：`volatile` → 7 天、`periodic` → 30 天的过期阈值常量；`stable`（默认值）或未识别取值不设阈值，永不因时间被重新纳入候选 | ✅ 已实现 | `src/mini_agent/evolution/capability_learning.py` |
| `_needs_staleness_refresh(topic, now)`：仅对 `coverage_state == "covered"` 的子主题生效；`last_touched_at is None` 视为需要刷新（比如手动改了 `volatility` 标注但还没有触达记录），不会因为"没有时间戳"被永久跳过 | ✅ 已实现 | 同上 |
| `scan_outline_gaps(track, ..., now=None)`：候选集从"非 `covered`"扩展为"非 `covered` 或已过期需刷新"；过期的 `covered` 子主题排序上归入 `partial` 同一优先档（已有内容但需要刷新，不抢在真正 `uncovered` 之前，也不会被排到"确定还新鲜"的 covered 子主题之后）。新增 `now` 参数供单测传固定时间戳，默认 `time.time()`，不传各参数时行为与此前完全一致 | ✅ 已实现，向后兼容 | 同上 |
| 刷新后的状态回写 | ✅ 天然满足，未额外实现——`run_capability_learning_cycle()` 里已有的写入后更新逻辑（`topic.coverage_state = "covered" if page_ids else "partial"` + `topic.last_touched_at = time.time()`）对"过期被重新选中的 covered 子主题"和"本来就是 partial/uncovered 的子主题"走的是完全相同的代码路径，刷新后 `last_touched_at` 自然重置，过期计时器自动重新开始，不需要新增分支 | 同上（未改动，本次为确认结论） | — |
| 单元测试（4 组：`stable` 永不过期 / `volatile` 超过 7 天阈值后被重新纳入且排序正确 / `last_touched_at is None` 视为需要刷新 / `periodic` 使用 30 天更长阈值） | ✅ 全部通过 | `tests/test_capability_learning_p1.py` |

`run_capability_learning_cycle()` 未改动——它调用 `scan_outline_gaps()` 时不传 `now`，沿用默认 `time.time()`，行为自动获得这项能力，不需要额外接线。

### §13.1-c（跨 Track 子主题去重与知识共享）—— ✅ 本轮已实现

设计文档 §13.1 把这一项列为"建议优先纳入 P1/P2，成本低"：用户同时开多个 Track（比如"股票分析"和"宏观经济"）时，子主题会有交叉（"利率对资产价格的影响"两边都可能各自检索一遍），缺口扫描前加一步轻量的相似度检测，命中就复用已有页面，不重复检索：

| 项目 | 状态 | 对应文件 |
|---|---|---|
| `_topic_name_similarity(a, b)`：字符级 2-gram（bigram）Jaccard 相似度，取值 [0,1]——"关键词/tag 相似度即可，不需要语义匹配"（原文 §13.1-c），对中文子主题名称（通常没有空格）比朴素分词更友好，也不引入 embedding | ✅ 已实现 | `src/mini_agent/evolution/capability_learning.py` |
| `find_cross_track_reuse(topic, track, other_tracks)`：在其它 **active** Track 的大纲里找相似度 ≥ 0.5（`CROSS_TRACK_REUSE_SIMILARITY_THRESHOLD`）且已 `covered`、已有 wiki 页面的子主题；只在本子主题**自己还没有任何 wiki 页面**时才会命中——已经有内容的子主题不应被复用逻辑覆盖掉可能更贴合自身 Track 语境的既有页面；`paused` Track 不参与匹配；纯函数、无副作用，方便单测 | ✅ 已实现 | 同上 |
| `run_capability_learning_cycle()` 接线：检索/写入回调之前先做复用检测（即使 `retriever`/`wiki_writer` 未接线也照样生效，不会被 P1 的"未接线安全跳过"分支挡住）；命中时台账记 `action="reused"`，`summary` 新增 `topics_reused` 计数字段 | ✅ 已实现 | 同上 |
| 单元测试（9 组：相似度函数 3 组 / `find_cross_track_reuse` 4 组（命中/已有页面不覆盖/忽略 paused Track/低于阈值不命中）/ `run_capability_learning_cycle` 端到端复用 1 组 / 无相似主题时向后兼容退回原有跳过逻辑 1 组） | ✅ 全部通过 | `tests/test_capability_learning_p1.py` |

跨 Track 关联本身没有建立额外的持久化结构（比如"这两个子主题被判定为等价"的记录）——每轮临时计算，命中就直接把 `wiki_page_ids` 并入当前子主题，足够满足"不重复检索"这个核心诉求，避免为一个轻量优化引入新的存储实体。

### 后续计划（本轮 v0.20 已实现）—— 真实检索/cron 默认打开 + 人设草稿区体验优化

此前文档在多处（P1 表格、`CapabilityLearningConfig` 注释、`SYSTEM_JOBS` 注释）把
"真实检索默认关闭"和"cron 任务默认关闭"标注为需要用户后续显式打开的
待办项；同时"看板人设草稿区目前是最简版本"也在多处注释里留了"后续可以
优化"的说明。这两项在 v0.20 一并纳入并实现：

| 项目 | 状态 | 对应文件 |
|---|---|---|
| `CapabilityLearningConfig.retriever_enabled` 默认值 `False → True` | ✅ 已修改，评审条件（§13.3-g 合规过滤已实现并有测试覆盖）已满足，改为 opt-out | `src/mini_agent/config/models.py` |
| `sys:capability_learning_cycle` / `sys:capability_question_sweep` 两个 cron job 默认 `enabled` `False → True` | ✅ 已修改；仍受 active Track、单轮子主题数上限、待回答问题数上限（§3.3、§9）约束，不会变成无限制抓取；用户仍可在看板「⏰ Cron 任务」Tab 或 CLI 单独关闭 | `src/mini_agent/evolution/cron_scheduler.py` |
| CLI `/capability cycle` 帮助文案、cron 模块头部注释同步更新默认值描述 | ✅ 已更新 | `src/mini_agent/cli/commands/capability_cmd.py`、`src/mini_agent/evolution/cron_scheduler.py` |
| 看板能力学习 Tab 顶部说明文案：不再提示"需要手动 `/capability cycle` 触发"，改为说明默认自动运行、如何关闭 | ✅ 已更新 | `apps/mini_agent_kanban/app.py` |
| 单元测试 `test_capability_learning_config_default_retriever_disabled` → 改名为 `test_capability_learning_config_default_retriever_enabled`，断言改为 `is True` | ✅ 已更新 | `tests/test_capability_learning_p1.py` |
| 看板"人设草稿区"体验优化：完成度从纯文字摘要升级为 `st.progress` 进度条 + 缺失维度的逐条勾选清单（`st.popover`）；草稿预览从单一 `st.code` 源码块升级为"预览效果"（去掉 frontmatter 后按 markdown 渲染，更接近 `/role use` 实际效果）/"源码"双 Tab；检测到草稿里嵌入了 §10.4-2 真人模仿安全提示时用 `st.warning` 单独高亮，不再淹没在源码文本里；发布前若完成度不满即在二次确认按钮旁加一句弱提醒（不阻断） | ✅ 已实现 | `apps/mini_agent_kanban/app.py` |
| `GET /v1/capability/tracks/{track_id}/persona/draft` 端点补充返回 `completeness` 字段（此前只有 POST 端点返回，GET 端点只读已落盘草稿时看板拿不到完成度，导致刷新页面后进度条无法展示） | ✅ 已实现，只读计算不产生写入 | `src/mini_agent/api/capability_routes.py` |
| 单元测试：验证上述改动未破坏既有测试套件（`test_capability_learning_p1.py`、`test_capability_cmd.py`、`test_capability_routes_mount.py`、`test_capability_persona_wiki_scopes_binding.py` 共 106 组全部通过） | ✅ 全部通过 | 同上四个测试文件 |

### §10（`target_type="persona"` 全链路：人设草稿生成/发布）—— ✅ 本轮已实现

原本标注为 P3 里工作量最大的一项。设计文档 §10.3 要求的四步全部落地：

| 项目 | 状态 | 对应文件 |
|---|---|---|
| 第 1 点·草稿生成：`draft_persona_markdown(track, questions)` 把 persona 型 Track 的大纲 + 已回答的 `CapabilityQuestion`（`status="answered"` 且答案非空，不受 `consumed` 影响）合成一份与手写 `.agent/personas/*.md` 同样格式的 frontmatter + 正文；`allowed_tools`/`wiki_scopes` 故意留空，不由自动合成放宽或猜测（§10.4-3） | ✅ 已实现 | `src/mini_agent/evolution/capability_learning.py` |
| 第 2 点·完成度反馈：`persona_draft_completeness(track, questions)` 返回已回答/总维度数 + 缺失维度名称列表 | ✅ 已实现 | 同上 |
| §10.4-2 真人扮演风险提示：`detect_real_person_reference(persona_desc)` 用关键词/正则启发式识别"要求模仿/扮演某个真实公众人物本人"的表述，命中时在草稿里嵌入警示注释，**不自动阻断**（宁可漏报，不误伤正常人设描述），供用户在发布前的预览阶段自行判断 | ✅ 已实现 | 同上 |
| 草稿持久化：`save_persona_draft()`/`load_persona_draft()` 落盘到新增的 `<project_root>/.agent/capability_persona_drafts/<track_id>.md`（草稿态，非正式 personas 目录） | ✅ 已实现 | 同上、`src/mini_agent/storage/paths.py` |
| 第 4 点·显式发布：`publish_persona_draft(paths, track_id)` 把草稿写入项目级 `project_personas_dir`（不写全局目录，避免静默污染），完成后尝试 `PersonaLoader.rediscover()` 让运行中的 agent 立即感知；**只能由 CLI/API 显式调用，不会被 `run_capability_learning_cycle()` 自动带出** | ✅ 已实现 | 同上 |
| CLI 接线：`/capability create ... --persona` 创建 persona 型 Track；`/capability persona draft/show/publish <track_id>` 三个子命令对应生成/预览/发布，对 knowledge 型 Track 调用会报错拒绝 | ✅ 已实现 | `src/mini_agent/cli/commands/capability_cmd.py`、`src/mini_agent/cli/parser.py` |
| 单元测试（persona 草稿合成 10 组 + CLI 子命令 6 组） | ✅ 全部通过 | `tests/test_capability_learning_p1.py`、`tests/test_capability_cmd.py` |

| HTTP API：`POST /v1/capability/tracks/{track_id}/persona/draft`（生成/刷新草稿，返回草稿全文 + 完成度摘要）、`GET .../persona/draft`（读取上次落盘草稿）、`POST .../persona/publish`（显式发布）；knowledge 型 Track 调用 draft/publish 返回 400，publish 前未生成过草稿返回 400，都不是裸 500 | ✅ 已实现 | `src/mini_agent/api/capability_routes.py` |

| 看板 UI：`apps/mini_agent_kanban/` 能力学习 Tab（`render_capability_tab`）里，persona 型 Track 卡片新增"人设草稿"区块——「生成/刷新草稿」「发布」两个按钮 + 完成度提示 + 草稿预览（`st.expander` + `st.code`）；「发布」需要二次确认（复用既有的"删除 Track"同款确认交互模式），不会一键误发布 | ✅ 已实现 | `apps/mini_agent_kanban/app.py`、`apps/mini_agent_kanban/client.py` |

至此 §10 全链路（核心库 + CLI + HTTP API + 看板 UI）四层全部完成，`target_type="persona"` 不再是"文档写了但打不通"的半成品。

### P3

规划内容见文末「实施阶段划分」一节（第 11 节主体 + §14.1-a 记录接线 + §14 P2 大纲起草 + §11.4 看板知识范围绑定 + §12.1-a capability_map 排序信号 + §13.1-b 多 Track 公平调度 + §13.2-d 知识时效性衰减 + §13.1-c 跨 Track 子主题去重 + §10 persona 全链路（CLI + HTTP API + 看板 UI）均已提前完成，见上）。剩余方向：与 `external_trend_capability_link`/`objective_executor`/`decision_profile_builder` 的协同（见文档 §12.1-b/c、§12.2）、13.2-e 可验证学习效果。

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

> **实现阶段发现的修正**（写代码时才发现，原设计假设有误，记录在这里避免
> 下次又踩一遍）：`cron_scheduler.py` 里已有的 `sys:` 内置任务并不是直接
> 调用一个 Python 函数，而是生成一段 `task_template` 文本，交给 Agent
> 自己带着工具去执行（参照 `sys:growth_advisor_daily` 是让 Agent 跑一次
> `/growth scan` slash command）。这意味着下面这张"直接注册 job 调用
> `run_capability_learning_cycle()`"的示意图，实际接线时还需要先补一个
> `/capability cycle` 这样的 slash command 处理器作为中间层，`task_template`
> 引用这个命令，而不是任务表里能直接挂 Python 可调用对象。这一层目前
> 代码里还没有实现（见文档开头「实施状态」），下面的示意仍保留作为
> "最终效果应该是什么"的说明，但接线时请按"先做 slash command，cron
> 任务模板引用它"这个顺序推进，不要直接找 `CRON_JOBS` 表插入函数引用。

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

## 10. 与 Persona 角色系统的结合：人设型 Track

前面几节讨论的都是 `target_type: "knowledge"`——学习产出是 wiki 知识页。但同一套"大纲缺口扫描 → 检索/追问 → 落盘 → 台账"的循环骨架，同样可以用来**构建 `.agent/personas/*.md` 这样的角色人设**，只是产出物形态和信息来源完全不同。这一节把这条平行路径讲清楚。

### 10.1 `.agent/personas/*.md` 和本方案的关系

`orchestrator/persona_profiles.py` 里的角色扮演机制（`docs/persona-guide.md`）解决的是"人格/语气切换"：用户预先写好一份 frontmatter（`name`/`display_name`/`description`/`tone`/`break_character_policy`/`allowed_tools`）+ Markdown 正文（身份设定、说话风格、行为准则），激活后主 Agent 跨轮持续保持这个人格，且代码强制追加一段不可被覆盖的安全边界声明。

这套机制目前完全依赖**用户手写**人设文件。本方案要补的是：用户只给一个粗略方向（"我想要一个说话犀利、经验老道的资深投资顾问人设"），系统通过持续检索 + 向用户追问，把这个方向逐步"养成"一份完整、细节饱满、风格一致的人设文件——本质上是把 `.claude/skills/persona-generator`、`character-designer` 这类"一次性生成"的技能，升级成"持续迭代、有记忆、可追问"的过程。

### 10.2 `target_type: "persona"` 的循环差异

给 `CapabilityTrack` 新增 `target_type: "knowledge" | "persona"` 字段后，两条路径共用骨架但内容分叉：

| 环节 | knowledge 型 | persona 型 |
|---|---|---|
| "大纲"的构成 | 领域子主题（技术分析/基本面…） | 人设维度：身份背景、性格特质、说话习惯与口头禅、知识边界、行为准则、人际关系设定、反差/矛盾感——直接对齐 `character-designer` skill 已经用在小说人物创作上的那套维度 |
| 信息主要来源 | 互联网检索为主 | **用户异步回答为主**，检索为辅（检索的是"塑造手法/参考原型"，不是关于用户本人或某个真人的事实） |
| 检索内容示例 | "MACD 指标怎么用" | "管家式角色常见语言风格参考""如何写出温柔但坚定的性格反差"（检索抽象的写作/人格塑造手法，不抓具体某个真人的传记） |
| 落盘 | 直接写正式 wiki 页面 | 先写"人设调研草稿"（`page_type: persona_draft`，可以复用 wiki 存储，但不进入正式检索索引），最终由一个新增的**人设合成步骤**编译成 `.agent/personas/xxx.md` |
| 提问频率 | 克制，达不到"互联网查不到+明显影响方向"才问 | 应主动放宽——人格细节大部分天然只能靠问，`MAX_PENDING` 上限应比 knowledge 型更高 |

### 10.3 人设合成（persona composer）与发布流程

人设构建应该有一个"草稿完成度"概念（这点和 knowledge 型"没有终点、持续补"不同）：

1. 每轮循环后，用已收集的追问答案 + 调研笔记，尝试生成一版**人设草稿**，渲染成与 `jarvis.md`/`rem.md` 完全同样的 frontmatter + 正文格式
2. 草稿**不直接写入 `.agent/personas/` 生效**，而是先在看板"预览"，复用 `/role show` 的渲染逻辑展示（含强制追加的安全边界声明，让用户提前看到最终效果），并标注"这版草稿还缺哪些维度"
3. 用户可以针对某个维度直接编辑/否决草稿内容（触发针对性追问而不是整体重来），也可以直接手改正文
4. 用户点"发布"后才真正写入 `.agent/personas/xxx.md`，此时可以 `/role use` 激活；发布不等于 Track 结束——用户后续用这个角色聊多了，发现某处违和，可以继续通过这个 Track 追问优化

发布这一步必须是**显式用户动作**，不能自动发布——正在进行中的会话如果角色人设被后台悄悄改掉，会破坏用户体验的连续性，这一点和"sys: 前缀 job 不可被自动删除，只可 disable"是同一种克制哲学的延伸。

### 10.4 安全边界（persona 型特有的风险，是对第 9 节的补充而非替代）

1. **`_SAFETY_SUFFIX` 的强制追加逻辑完全不受影响**——人设合成器只负责生成 frontmatter 和正文，代码写死的安全边界声明依然在渲染时无条件追加，这条链路不能碰、不能绕
2. **真人模仿要格外谨慎**。用户方向若指向"像某位可辨识的真实公众人物一样说话"，检索环节不应去扒这个人的传记/生平资料来"喂"人设——这正好撞上项目已有的"不写涉及真实公众人物的说服性内容/不把虚构语录归因给真人"的边界。系统识别到方向指向真实个人时，应提示用户改为"参考某种风格但作为原创虚构人物"，检索也只抓"某类角色常见的语言塑造手法"这种抽象层面，不抓具体某个真人的具体事实
3. **`allowed_tools` 等安全相关 frontmatter 字段不能由自动合成随意放宽**。即使检索到"这个角色应该能操作股票账户"之类的内容，也不能让人设合成器据此自动开放工具白名单——工具权限这类字段必须始终要求用户显式确认，不能被"学习循环"自动决定
4. **UI 上要和 knowledge 型 Track 明确区分**，避免用户混淆"我在给 Agent 攒知识"和"我在给 Agent 捏人设"——创建 Track 时第一步先问"知识能力"还是"角色人设"，分流到不同向导

---

## 11. Persona 专属 wiki 检索范围（每个角色可以有自己的知识边界）

第 10 节解决的是"怎么用这套系统构建人设"，这一节解决一个更进一步、也更有价值的结合点：**现有 persona 系统里的"知识边界"目前只是正文里的一句描述性文字（比如 jarvis.md 里说"如实说明你能做什么"），并没有真正约束检索行为**——不管激活的是哪个角色，`context_builder.py` 的 wiki 检索都是全库不区分的。这意味着"资深股票分析师"人设和"贾维斯管家"人设在实际检索行为上没有任何区别，人设的"专精感"只停留在语气层面，没有落到知识层面。

### 11.1 核心思路：把 wiki 检索范围声明成 persona frontmatter 的一部分

给 `PersonaProfile` 新增一个可选字段：

```yaml
---
name: stock-advisor
display_name: 老李投顾
description: 经验老道的资深投资顾问人设，说话犀利、直击要害
tone: 犀利、老练、偶尔带点行话
break_character_policy: soft
wiki_scopes:                      # 新增字段：这个角色检索时优先/仅限使用的 wiki 范围
  - capability:stock_analysis
  - capability:macro_economics
---
```

`wiki_scopes` 留空/不填 = 不限制（沿用 `allowed_tools` 已有的"空即不限制"惯例，保持向后兼容、不破坏现有 persona 文件）。填了之后，`context_builder._try_inject_wiki_search()` 在当前有激活角色时，把 `wiki_scopes` 透传进 `wiki_shelf_search()` 已有的 `tags` 参数——这个参数**本来就支持**，改动量很小，不需要新造检索能力。

### 11.2 与第 10 节"人设型 Track"天然打通

`wiki_scopes` 最自然的取值来源，正是本方案第 2~6 节里 `knowledge` 型 `CapabilityTrack` 产出的 `wiki_tag`（比如 `capability:stock_analysis`）。这样两条路径就能在看板上无缝衔接：

- 用户新建一个"知识型" Track（"希望你具备强大的股票分析能力"），系统持续检索沉淀出 `capability:stock_analysis` 这个 wiki 命名空间
- 用户再新建一个"人设型" Track，构建"老李投顾"这个角色，发布时看板上可以直接勾选"绑定已有的知识范围"，把 `capability:stock_analysis` 写进这个角色的 `wiki_scopes`
- 之后 `/role use stock-advisor` 时，Agent 不仅语气变成"老李"，检索到的知识也真正被限定/优先在这个专精领域——人设的专业感第一次能在**回答内容**而不只是**语气**上体现出来
- 同一个 `wiki_tag` 也可以被多个角色的 `wiki_scopes` 共享（比如"老李投顾"和默认助手身份都能访问这份股票知识），知识资产和人设是多对多关系，不是一对一绑定

反过来，一个知识型 Track 也可以同时关联多个 persona——这一点在看板 UI 里应该体现为：Track 详情页新增"被以下角色引用"列表，Persona 详情页新增"绑定的知识范围"列表，双向可见。

### 11.3 边界策略：硬限制还是软优先

这里有个设计选择，建议默认走**软优先**而不是硬限制：

- `wiki_shelf_search(tags=persona.wiki_scopes, ...)` 优先在限定范围内检索
- 若限定范围内零命中，**允许回退到全库检索**，但可以在内部日志/看板台账里标注"这次回答超出了当前角色声明的知识边界"，而不是直接让 Agent 回答"我不知道"
- 是否要把"超出边界"这件事对用户可见（比如角色语气里自然带一句"这个话题我不算特别专精，简单说说…"），交给 `break_character_policy` 现有的语义去处理（`soft` 本来就允许"短暂跳出角色回答严肃问题"，这里可以视为同一机制的自然延伸，不需要新造一套策略）

硬限制（限定范围内零命中就拒绝回答）风险更大——容易把一个本来只是语气人设的功能，意外变成"知识黑名单"，伤害可用性，且和"角色扮演不改变真实能力边界"这条已有原则（persona-guide.md 里明确写的）冲突。软优先能兼顾"专精感"和"不故意让 Agent 变笨"。

### 11.4 看板 UI 补充

在第 7 节人设管理区的基础上，`target_type: "persona"` 的 Track 详情页 / 已发布 persona 的管理页新增：

- "知识范围绑定"卡片：展示当前绑定的 `wiki_tag` 列表，可勾选添加/移除已有 knowledge 型 Track 的 tag（不需要用户手记 tag 字符串，直接从已有 Track 列表选）
- 每个 tag 旁边显示这个知识范围下已有多少 wiki 页面、最近更新时间，帮助用户判断"这个角色的专精程度够不够"
- 若某个 persona 长期没有绑定任何 `wiki_scopes`，看板可以给一条**弱提示**（不是强推送）——"这个角色目前不限定知识范围，如果想让它在某个领域显得更专业，可以关联一个知识型 Track"，把两条路径的协同价值主动露出来，但不强制、不打扰。

**「能力大纲覆盖状态」区块直接查看 wiki 页面（已实现）**：此前每个子主题只显示 `✅/🟨/⬜` 覆盖状态图标 + "关联 N 篇 wiki 页面"这一句文字，用户想看具体沉淀了什么内容得去 wiki 检索里另外找。现在关联页面数 > 0 的子主题旁新增一个「查看」按钮，点击后就地展开每篇关联页面的 Markdown 正文（`st.expander` 逐篇展示，不跳转页面），再点一次收起。后端新增 `GET /v1/capability/wiki_pages/{page_id}` 只读端点，复用 `wiki/index_reader.py::find_page_path()`（`<page_id>.md` 文件名约定）定位文件、原样读取正文，不做隔离区/合规状态判断——那是 wiki 检索侧的关注点，这个端点只负责"page_id 存在就把内容读出来"；页面被外部删除或 id 有误时返回 404，看板侧展示"页面加载失败"而不是让整个 Track 详情页报错。

---

## 12. 与其它现有系统的协同边界（可行性与可维护性评估）

上面几节已经确定了核心架构，这一节单独评估"还能和哪些现有系统联动"——**不是所有能想到的关联都值得做**，这里按"能直接采纳 / 需要边界限制才能采纳 / 明确排除"三档给出结论，避免方案范围无限扩张、埋下难以维护的隐性耦合。

### 12.1 可直接采纳（低风险、单向依赖）

**a. 用 `perception/self_model.py` 的 `capability_map` 给大纲排优先级**

新建 knowledge 型 Track 时，子主题的推进顺序应该参考 `capability_map` 里 Agent 现有的能力评估（`confidence` 低/`total_calls` 少的领域优先），而不是纯按 wiki 覆盖率排。这是**单向只读消费**，不产生任何反向副作用，不引入新的自动化决策链路，成本低、收益明确，直接采纳。

**b. 用 `evolution/decision_profile_builder.py` 的 `user_value_profile.md` 减少 persona 构建时的重复追问**

第 10 节 persona 型 Track 的追问环节，先检查这份用户决策画像有没有可直接复用的信号（比如沟通风格偏好），有则作为草稿默认值，减少不必要的追问。**但必须原样继承它自己的证据门槛**——`decision_profile_builder` 本身有严格的 `MIN_EVIDENCE_COUNT`（证据不够不落地）的克制原则，引用其结论时不能绕开这个门槛，证据不足的领域该追问还是要追问，不能因为"复用了画像"就放松追问的严谨性。

**c. 月度复盘并入 `growth_advisor` 已有的月度复盘入口**

不新开复盘展示面板，能力学习 Track / persona 构建的月度统计（新增多少 wiki 页面、哪个 Track 进展最快、发布了几个新人设）并入同一份月度摘要。**但报告内部必须分节展示，不能合并叙事**——"帮用户成长"（growth_advisor）、"帮 Agent 学能力"（capability learning）、"构建人设"（persona track）是三件性质不同的事，混在一段话里会让用户分不清这个月的进展到底是自己在变化还是 Agent 在替自己攒东西。

### 12.2 需要边界限制才能采纳（有真实耦合风险，不能默认打通）

**d. 与 `evolution/external_trend_capability_link.py` 的关联——只做人工审核层面的浅集成**

这个模块和本方案架构高度同构（外部知识 wiki 页面 × 能力薄弱点 → 改进候选），看似可以"打个兼容 tag 就复用"，但它的下游是 `soft_goal_deriver.py`，在 `autonomous` 档位下会**自动生成 Goal**。如果能力学习 Track 产出的 wiki 页面被无差别打上兼容标签，相当于"用户明确要求 Agent 学某项能力"这件事，可能间接触发"Agent 自主决定要改进自身行为"的自动化链路——这与第 9 节"人设/能力方向的所有权在用户，Agent 不能自作主张扩展"的原则相冲突，所有权边界会被模糊掉。

**结论**：只允许写入该模块"人类可读草稿"这一半（`external_trend_capability_candidates_path`，供人工审核），**不允许**进入自动生成候选、进而可能被 `soft_goal_deriver` 消费为 Goal 的那条链路。这个限制要在实现时用配置显式关闭默认打通，而不是靠"没接口就不会触发"这种隐式保证。

**e. 与 `evolution/objective_executor.py` 的关联——只借鉴模式，不共享执行池**

`objective_executor` 的并发控制 `MAX_CONCURRENT_OBJECTIVES`（默认 2）是为"完整多步 Objective、真实 agent turn"设计的重量级资源池，而项目里已经存在 `next_doc/goal_execution_fairness_improvement_plan.md` 这样的文档，说明这套执行池的**公平性本身就是已知痛点**。如果把"检索一个子主题"这种应该轻量、高频的后台任务也塞进同一个池子抢并发槽位，大概率会让已有的公平性问题雪上加霜，用户真正手动发起的 Objective 反而可能被后台学习任务挤占资源。

**结论**：不直接复用 `objective_executor` 的调度池。可以借鉴的是它的**局部实现模式**——失败重试策略、`ResourceArbiter` 接入方式——但检索任务的执行队列必须独立调度，不与用户手动发起的 Objective 共享并发槽位。

### 12.3 明确排除（语义冲突，非本方案范围）

**f. 小说创作技能族（`character-designer`/`world-builder`/`plot-tracker` 等）接入 wiki**

这里存在一个**语义级冲突**，不只是"信息不够、待评估"：`wiki/dedup.py` 的设计前提是"同一个事实被重复检索到应该合并去重"，这个假设对真实世界知识成立，但对虚构世界设定不成立——小说创作中角色设定的修订、世界观的迭代往往是**有意为之的改动**，不应被自动判定为"重复内容"而合并掉；`plot-tracker`/`consistency-checker` 这些技能存在的意义，恰恰是精确区分"有意修订"和"无意矛盾"，这和 wiki 模块"去重即优化"的核心假设直接冲突。

**结论**：明确排除在本方案范围外，不作为"待评估方向"挂在文档里——挂着反而暗示"迟早要做"，误导后续读者。如果未来确有需要，应该是一份独立的、正视这个语义冲突（而不是绕开它）的新设计，不应顺带并入本方案。

---

## 13. 进一步改进方向

第 13 节评估的是"和现有系统怎么关联"，这一节是系统自身还没覆盖到的能力短板，按"能直接采纳 / 值得做但要评估投入 / 必须在实施前定好原则"分类，供后续迭代参考。

### 13.1 建议优先纳入 P1/P2（成本低，和已有架构耦合浅）

**a. 使用驱动学习：检索未命中反哺缺口优先级**

目前触发机制完全依赖 cron 周期性推进大纲，但更真实的缺口信号是"用户在对话里实际问到、但 wiki 库里没有"的内容——这比"大纲里理论上该有"更有说服力。`context_builder.py` 的 wiki 检索未命中时，记一笔到 `CapabilityLedgerEntry`（新增 `action: "miss_observed"`），下一轮 `sys:capability_learning_cycle` 把这类"真实问过但答不上"的子主题优先级拉到最高，插队到大纲原有顺序前面。改动成本低（只是多记一份已有事件），但让系统从"猜你可能需要什么"进化成"你已经证明你需要什么"。

**b. 多 Track 之间的公平调度**

方案假设"每轮遍历所有 active Track 各推进一次"，但没考虑用户同时开多个 Track 时单轮资源有限的情况。鉴于 `objective_executor` 池子的公平性已经是项目里的已知痛点（见 12.2-e），这个新系统从一开始就该有简单的公平策略（轮转优先级，或按"距离上次推进的时间"排序），避免早建的 Track 长期占满每轮配额、后建的 Track 得不到推进。

**c. 跨 Track 子主题去重与知识共享**

用户同时开"股票分析"和"宏观经济"两个 Track 时，子主题会有交叉（比如"利率对资产价格的影响"两边都可能检索）。缺口扫描前加一步轻量的"跨 Track 相似子主题检测"（关键词/tag 相似度即可，不需要语义匹配），命中就复用已有页面、建立跨 Track wiki 关联，不重复检索。成本不高，能明显减少浪费的检索配额。

### 13.2 值得做，但要独立评估投入再决定纳入哪个阶段

**d. 知识时效性衰减**

不是所有子主题"学完就完"——"技术分析基础"这类内容几乎不过时，但"当前宏观利率环境"这类内容可能几周就过期。`OutlineTopic` 需要加一个 `volatility` 标注（`stable` / `periodic` / `volatile`），缺口扫描时不只看"有没有覆盖过"，还要看"volatile 类型的页面是不是该重新检索了"——否则会出现"名义覆盖率 100%，内容早已过期"的假象。

**e. 可验证的学习效果**

现有台账只记录产出数量（新增了几个 wiki 页面），没有回答"这些内容真的让 Agent 在这个领域答得更好"这个更根本的问题。`wiki_utility_audit.py` 只能追踪"有没有被检索用到"，用到不等于用得好。可以借鉴 `self_model.py` 的能力自评思路，给每个 Track 定期跑一组轻量"探针问题"（构建 Track 时由用户或 LLM 拟定几个该领域典型问题），对比"有 wiki 上下文"和"无 wiki 上下文"两种情况下回答质量的差异，作为该 Track 是否值得继续投入的依据。**这一项价值最高，但实现复杂度也最高（涉及双份回答生成 + 质量对比判定），建议单独作为 P3 方向，不在早期阶段承诺。**

**f. 大纲的动态生长**

目前大纲创建后主要靠用户手动编辑。更自然的方式是：用户在追问回答或对话中提到大纲之外的新关注点时（比如原大纲是"股票分析"，用户提到"我其实更关心港股"），系统生成一条"要不要把这个也加进大纲"的建议，而不是让大纲外的信息被浪费掉。这一点和 13.1-a 是一体两面——a 解决的是"发现已有子主题的缺口"，f 解决的是"发现大纲本身该扩展"，两者机制不同，值得区分成两个独立能力点分别实现。

### 后续计划（v0.21）—— §8 通知接入 / §13.2-f 大纲动态生长 / Persona 镜像视图（三项均已实现）

三项从「进一步改进方向」里挑出、纳入本轮实施，三项均已完成：

1. ~~**§8 通知系统接入**~~ —— ✅ **已实现**，见文档开头「§8 通知系统接入」小节。
2. ~~**§13.2-f 大纲动态生长建议**~~ —— ✅ **已实现**（核心逻辑 + CLI + HTTP API + 看板 UI），见文档开头「§13.2-f 大纲动态生长建议」及「§13.2-f 大纲扩展建议 API + 看板 UI（本轮补齐）」两个小节。
3. ~~**Persona 详情页镜像视图**~~ —— ✅ **已实现**，见文档开头「Persona 详情页镜像视图（v0.21 第 3 项）」小节。



**g. 合规与风险提示（股票分析场景需要单独关注）**

用户举的例子恰好是金融领域，这类内容有真实的合规风险。wiki 页面沉淀"分析方法论"没问题，但如果检索结果混入了具体的"买入/卖出建议"这类内容，写入 wiki 前必须有一道过滤/改写，只保留方法论和事实性信息，剥离具体投资建议措辞；同时在这类领域的 wiki 页面 frontmatter 上加 `requires_disclaimer: true` 标记，对话中引用时自动带一句"仅供参考，不构成投资建议"。这不是本方案独有的问题，但类似场景（医疗、法律等专业建议类方向）都可能踩同样的线，**这道过滤必须在 P1 检索/写入环节就内置，不能等出问题后再补**，风险补救的成本远高于预防。

---

## 14. 实施阶段划分

**P1（最小可用闭环）**：
- `CapabilityTrack` / `OutlineTopic` / `CapabilityLedgerEntry` / `CapabilityQuestion` 数据模型 + 存储路径（`OutlineTopic` 直接带上 13.2-d 的 `volatility` 字段，避免后续迁移）
- `sys:capability_learning_cycle` cron job：缺口扫描（规则式，可直接引入第 12.1-a 节 `capability_map` 排序 + 13.1-a 检索未命中优先级）+ 检索（独立调度队列，不复用 `objective_executor` 并发池，见 12.2-e；纳入 13.1-b 多 Track 公平调度）+ wiki 写入（内置 13.3-g 合规过滤，不可延后）+ 台账记录
- 异步问答队列的生成与消费（不接通知，只落队列）—— ✅ 已实现
- 看板三个区域（人设管理/进度展示/待回答问题）+ 对应 API —— ✅ 均已实现，见文档开头「实施状态」
- `context_builder.py` 接入检索复用 —— ✅ 未命中记录部分（§14.1-a）已提前完成；§6 的"命中 active Track 时按需注入"部分经代码走查确认已被既有的 `_try_inject_wiki_search()` 全库检索链路天然覆盖，不需要额外实现，见文档开头「实施状态」说明
- 剩余未接线项：无——P1 计划项已全部实现，`miss_observed` 台账接入 `scan_outline_gaps()` 优先级排序、LLM 辅助大纲起草、§13.2-d 知识时效性衰减、§13.1-c 跨 Track 子主题去重、§10 persona 全链路（CLI + HTTP API + 看板 UI）也已提前完成（均原标注在 P2/P3）。真实 `retriever`（`web_search`）与 `sys:capability_learning_cycle`/`sys:capability_question_sweep` cron 任务 v0.20 起默认开启（opt-out，详见文档开头「后续计划」小节）。剩余 P3 方向：与 `capability_map` 等其它子系统的协同，另行评审

**P2（体验与质量增强）**：
- 大纲生成/缺口判定引入 LLM 辅助（替换纯规则）
- 通知系统接入（节流摘要式推送）
- `wiki_utility_audit.py` 联动，追踪自动生成内容的实际使用率，作为后续调权/清理依据
- 看板拖拽式大纲编辑、时间线可视化
- `decision_profile_builder` 联动减少 persona 构建重复追问（12.1-b）、月度复盘并入 growth_advisor 入口但分节展示（12.1-c）
- 13.1-c 跨 Track 子主题去重与知识共享
- 13.2-f 大纲动态生长建议（用户对话中提到大纲外新方向时，生成"要不要加进大纲"的建议）

**P3（跨 Track 能力沉淀 + Persona 结合，方向级）**：
- 多个 Track 之间共享的通用子主题去重复用（比如"数据来源可靠性判断"可能在多个能力方向下都用得到）
- 问答队列机制上移为通用基础设施，供其它 cron 模块复用
- `target_type: "persona"` 全链路：人设维度大纲、人设草稿生成、`/role show` 风格预览、显式发布流程（第 10 节）
- `PersonaProfile.wiki_scopes` 字段 + `context_builder` 透传 `wiki_shelf_search(tags=...)`——✅ **已提前实现**，见文档开头「实施状态」。看板知识范围绑定 UI（§11.4）——✅ **已提前实现**，见文档开头「§11.4」小节
- §12.1-a `capability_map` 排序信号——✅ **已提前实现**，见文档开头「§12.1-a」小节
- 与 `external_trend_capability_link.py` 的浅集成（仅人工审核草稿层，不打通自动 Goal 生成，见 12.2-d），作为方向级选项，实施前需团队评审确认边界配置默认关闭
- 13.1-c 跨 Track 子主题去重与知识共享——✅ **已提前实现**，见文档开头「§13.1-c」小节
- 13.2-d 知识时效性衰减（`OutlineTopic.volatility` 字段已在 P1 提前带上）——✅ **已提前实现**，见文档开头「§13.2-d」小节
- 13.2-e 可验证的学习效果（探针问题对比"有/无 wiki 上下文"回答质量）——实现复杂度高（涉及双份回答生成与质量判定），不在早期阶段承诺，需先验证 P1/P2 的基础闭环稳定后再评估投入