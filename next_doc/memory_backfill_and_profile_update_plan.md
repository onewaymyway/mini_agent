# 记忆回填（Memory Backfill）与用户画像更新机制改进方案

- **版本**: v2（v1 基础上完成一轮评审，第 4 节四个风险项均已给出明确
  决策：不加回填时间窗口 / session_id 合成规则已确认不冲突 / 画像
  "最后被印证时间"本轮直接做 / `profile.enabled` 默认值改为
  `True`——仍是方向级规划，未开始实施）
- **前置文档**: `next_doc/growth_advisor_design.md`、
  `next_doc/growth_advisor_improvement_plan_v2.md` / `v3.md`（成长顾问的
  信号扫描/候选生成机制，本方案是其上游数据源问题的修复）、
  `src/mini_agent/evolution/session_cleanup.py`（已有的 session 扫描 +
  离线知识抽取基础设施，本方案会复用其模式但不能直接复用其产出）
- **触发背景**: 用户在成长顾问看板上发现候选恒为 0，诊断信息显示
  `记忆总条数：0`。排查后定位到两个独立但相关的问题：
  1. **长期记忆(memory entries)本身覆盖率很低**——大量 session
     （尤其是被中断的、以及 daemon/cron 自动跑的）从未生成过摘要、
     从未写入过记忆，导致成长顾问这类"扫描记忆找信号"的机制天然
     无米下锅。
  2. **用户画像(`profile.derived.summary/tech_stack/habits`)是"最近
     快照"而非"整体画像"**——每次刷新都用最近 N 条记忆全量重新生成
     并整体覆盖旧画像，旧的、但仍然成立的特征会随时间被悄悄冲掉。
- **本文档定位**: 跟成长顾问系列文档一致，只排方向和优先级、给出
  设计要点和改动量级评估，不在此给出逐行实现细节。

---

## 0. 现状回顾（问题根因）

### 0.1 记忆覆盖率低的三类根因

长期记忆的写入路径只有一条：`ProfileMixin._generate_and_save_summary()`
(`src/mini_agent/agent/profile.py:129`)，而它只在 `save_session()` 内部
被 `trigger_summary_and_profile()` 触发(`src/mini_agent/agent/
lifecycle.py:794-807`)，且要同时满足：

```python
self.cfg.session_summary_enabled
and self.stats.turns >= self.cfg.session_summary_min_turns
and turns_since_last >= self.cfg.session_summary_min_turns
```

这条路径存在三类系统性盲区，会导致大量 session"有会话内容、但从没
产生过一条 memory"：

1. **异常中断**：进程被 kill / 崩溃 / 网络中断导致没有走到
   `save_session()`，或者走到了但轮次没达到 `session_summary_min_turns`
   （默认阈值，见 `SessionConfig.summary_min_turns` /
   `session_summary_min_turns`）。这类 session 在磁盘上有
   `raw_history.jsonl`/`meta.json`，但 `summary` 字段为空、从未触发过
   摘要生成线程。
2. **摘要生成本身失败但不重试**：`_generate_and_save_summary()` 是
   fire-and-forget 的后台线程（`threading.Thread(daemon=True)`），LLM
   调用失败、解析失败等异常会被 `log_exception` 吞掉
   (`src/mini_agent/agent/profile.py:253-256`)，之后没有任何重试或
   补偿机制——除非用户手动再触发一次 `/memory`（force=True）。
3. **daemon/cron 自动运行的 session 几乎完全绕开这条路径**：
   `evolution/cron_agent_bridge.py` 的设计明确是"每次触发都重新构建
   Agent，不跨触发保留 session 历史"（见 `session_cleanup.py:29-31`
   的说明），cron 任务本身不属于任何"持有 session 的场景"。这意味着
   **cron 每天/每小时自动执行的一大批任务，天生就不会产生任何
   memory entry**——而这恰恰是用户提到的"daemon 跑的任务，大部分都
   不会有 memory"的根因，比第 1/2 点更结构性，修复成本也更高。

**需要特别注意的一个已有陷阱**：仓库里已经存在一套"扫描 session +
离线补跑 + 打标记"的基础设施——`evolution/session_cleanup.py`，它的
`knowledge_extracted` 标记很容易被误认为可以直接复用。但读代码可以
确认这是**两条完全独立的流水线**：

| | 触发路径 | 产出 | 判定字段 |
|---|---|---|---|
| Session 摘要 → 长期记忆 | `_generate_and_save_summary()` | `MemoryEntry`（写入 `self._memory`/`self._global_memory`，成长顾问扫描的就是这个） | `Session.summary` 是否为空 |
| 离线知识抽取 | `HistoryManager.dispatch_extraction_for_entries()` | decision / entity / fact（wiki 知识，`session_cleanup.py:8` 说明用途是"删除前值不值得留痕"） | `Session.knowledge_extracted` |

也就是说，一个 session 完全可能 `knowledge_extracted=True`（已经跑过
知识抽取）但从来没有生成过 `summary`、没有写入过一条 `MemoryEntry`。
`session_cleanup.py` 目前只在**删除前**才会触发知识抽取补跑，且补跑
的是错误的那条流水线——**不能直接拿它当成记忆回填机制来用，但它的
扫描/判定/幂等模式非常值得复用**（下面第 2 节详述）。

### 0.2 画像"只反映最近任务"的根因

`UserProfileManager.generate()` (`src/mini_agent/profile.py:162-213`)：

```python
entries = sorted(entries, key=lambda e: e.created_at)[-self.cfg.profile.max_entries_for_profile:]
...
new_fields = {
    "summary": str(parsed.get("summary", ""))[:1000],
    "tech_stack": list(parsed.get("tech_stack", []))[:20],
    "habits": list(parsed.get("habits", []))[:20],
    ...
}
merged = dict(profile.derived or {})
merged.update(new_fields)   # summary/tech_stack/habits 三个字段整体覆盖
```

两个叠加因素导致画像"失忆"：

1. **固定小窗口**：只取最近 `max_entries_for_profile`（默认 20）条
   记忆喂给 LLM，超出窗口的历史任务对本次生成完全不可见。
2. **整体替换而非增量更新**：`merged.update(new_fields)` 虽然保护了
   `growth_focus_areas` 等其它模块写入的 key（这是 v2 计划 P4-0 修的
   问题，见 `profile.py:204-208` 的注释），但 `summary/tech_stack/
   habits` 这三个字段本身每次都是"从最近 20 条记忆重新总结一遍"，
   不会参考上一版画像——哪怕上一版总结出的某个长期特征依然成立，只要
   相关记忆条目掉出了这次的 20 条窗口，这次生成的 LLM 根本看不到它，
   自然也就总结不出来，画像因此持续向"只反映最近状态"漂移。

这两个问题结合"记忆条目本身覆盖率低"（0.1 节），会进一步放大：本来
样本就稀疏，还只挑最近一小段来看，画像失真程度会比记忆完整时更严重。

---

## 1. 目标与非目标

**目标**：
- 让"有实质内容但从未生成摘要"的 session 都能补上一条长期记忆，
  尤其覆盖被中断的会话和 daemon/cron 自动运行产生的会话。
- 把用户画像的生成方式从"每次从最近 N 条记忆重新总结、整体覆盖"改为
  "基于上一版画像 + 新增证据做增量更新"，让画像能沉淀长期稳定的特征，
  而不是持续被最近一小段任务冲刷。

**非目标**：
- 不在本文档里给出逐行实现代码，只到"设计要点 + 改动量级"的粒度。
- 不改变成长顾问自身的信号扫描/候选生成逻辑（`growth_advisor.py`
  不动），只修复它的上游数据源。
- 第 2.4 节提到的"让 cron 任务自身也持久化可回填的历史"是本方案里
  改动量级最大、风险最高的一块，本文档只给出方向性设计，不建议第一
  期就做——具体见分期建议（第 5 节）。

---

## 2. 方向一：记忆回填（Memory Backfill）

### 2.1 新增判定维度：区分"有内容但没摘要"的 session

复用 `session_cleanup.py` 的扫描风格（纯 Python、确定性规则，只有
真正需要理解语义的那一步才调 LLM），但判定条件必须换成：

```
turns >= 摘要生成阈值（对齐 session_summary_min_turns，而不是
                        session_cleanup 用的 min_turns_for_extraction）
且 summary 为空（Session.summary == ""，不复用 knowledge_extracted）
```

`Session.summary` 本身已经是持久化字段（`session.py:52`），不需要新
增存储结构，天然就是"这个 session 是否已经产生过摘要/记忆"的判据。

### 2.2 新增 daemon 任务：`sys:memory_backfill_scan`

参照 `_BUILTIN_JOBS` 里 `sys:growth_advisor_daily` 的写法
(`cron_scheduler.py:305-318`)，新增一个系统内置 cron job：

```python
{
    "id": "sys:memory_backfill_scan",
    "name": "记忆回填：补跑遗漏摘要",
    "schedule": "interval:21600",   # 每 6 小时，具体值待定
    "description": "扫描 summary 为空但内容达标的 session，补生成摘要并写入长期记忆",
    "task_template": "[记忆回填] 执行一次 /memory backfill，扫描待补摘要的 session 并回填长期记忆",
    "tags": ["memory", "profile"],
    "enabled": True,
}
```

对应新增一个 `/memory backfill [--dry-run] [--limit N]` CLI 子命令
（参照 `cli/commands/sessions.py` 里 `/session cleanup` 的结构），底层
新建 `evolution/memory_backfill.py`，核心流程：

1. 扫描候选（同 `scan_sessions_for_cleanup` 的分页/排序方式，但判定
   条件按 2.1 节）。
2. 对每个候选 session：加载 `raw_history.jsonl`（或旧格式的
   `history.json`）→ 复用 `_generate_and_save_summary()` 里"生成摘要 +
   写入 MemoryEntry"那部分逻辑（需要从 `ProfileMixin` 里抽出一个不
   依赖存活 Agent 实例状态（`self._history`/`self.stats`）的纯函数
   版本，输入是 `user_turns` 文本 + session 元信息，输出是 summary +
   写库，方便在离线扫描场景下复用，而不是要求扫描线程构造一个完整
   Agent）。
3. 成功后回写 `Session.summary`（复用 `SessionManager.save()`），
   顺带触发一次 `_maybe_refresh_profile()`（批量回填多个 session 后，
   只需要在全部处理完之后统一触发一次画像刷新，不必每条都触发——
   避免同一批回填内触发多次 LLM 画像生成）。
4. 每个 session 独立 try/except，单条失败不影响其它候选（对齐
   `cleanup_sessions()` 的 `failed` 分类，下次扫描再重试，不做无限
   重试计数器）。

### 2.3 幂等与并发注意事项

- 复用 `self._summary_lock` 的思路：回填任务和用户当前正在进行的
  session 摘要生成可能并发触发，需要有一个锁或者至少检查目标
  session 是否是"当前存活 session"（`exclude_ids`，直接复用
  `session_cleanup.py` 里已有的排除逻辑）。
- 回填只写 `summary` 为空的 session，写入后 `summary` 非空，天然
  幂等——不会重复处理已回填过的 session，不需要额外的
  "backfilled_at" 标记字段。
- 批量回填要设置单轮处理上限（比如每次最多处理 20 个候选），避免
  首次上线时存量 session 太多导致一次 cron 触发跑很久、大量并发调用
  LLM。

### 2.4 更大的问题：cron/daemon 任务本身不产生可回填的 session

2.2/2.3 节能解决"存量的、已经在磁盘上但 summary 为空"的 session，但
**无法覆盖 cron 任务本身**——因为 `cron_agent_bridge.py` 目前设计上
根本不会为每次 cron 触发落一个可被 2.1 节扫描到的 `Session` 记录。
这是用户反馈里"daemon 跑的任务，大部分都不会有 memory"更核心的那
部分，修复需要动 cron 执行链路本身，量级明显大于 2.1-2.3：

- **方案 A（轻量）**：不改变"cron 不持久化完整 session"的设计，但让
  `CronJobExecutor` 在一次 job 运行 `done=True` 收尾时，直接调用一个
  轻量版的"摘要 + 写记忆"（复用 2.2 节抽出的纯函数），跳过
  `Session`/`summary` 中转，直接落一条 `MemoryEntry`（`session_id`
  用 `cron:<job_id>:<run_id>` 这类合成 ID）。改动集中在
  `cron_job_executor.py` 收尾逻辑，不涉及 `Session` 存储结构变化，
  风险和改动量可控。
- **方案 B（重量）**：改变 cron 执行模型，让每次触发都产生一个真正
  的、可被 `session_cleanup`/本方案统一扫描的 `Session` 记录。收益是
  统一了两条路径，但要动 `cron_agent_bridge.py` 的核心设计前提
  （"不跨触发保留 session 历史"），影响面不可控，需要单独立项评估。

**建议**：本轮先只做方案 A，且限定为"cron job 运行有实质产出
（`StepResult.text` 非空、且 job 判定为 `done=True` 正常收尾，不含
`timed_out`/`needs_human_review` 的异常收尾）才生成记忆"，避免给
大量空转/失败的 cron 触发也生成低质量记忆，污染成长顾问的信号扫描。

### 2.5 配置项设计

新增 `MemoryBackfillConfig`（对齐 `ProfileConfig`/`GrowthAdvisorConfig`
的风格），字段草案：

```python
@dataclass
class MemoryBackfillConfig:
    enabled: bool = True
    scan_interval_seconds: int = 21600       # 6 小时
    min_turns_for_backfill: int = 4          # 对齐 session_summary_min_turns
    max_sessions_per_run: int = 20
    cron_session_backfill_enabled: bool = True  # 对应 2.4 节方案 A 的开关，
                                                  # 独立开关：即使记忆回填本体
                                                  # 出问题也能单独关掉 cron 这块
```

---

## 3. 方向二：画像更新机制（从"替换"到"更新"）

### 3.1 改进设计：把上一版画像也喂给 LLM，要求"更新"而不是"重写"

核心改动在 `UserProfileManager.generate()` 和对应的 prompt
(`prompts/user/profile_update_request.md` /
`prompts/system/profile_summarizer.md`)：

- **输入侧**：除了"最近 N 条记忆摘要"，额外把当前 `profile.derived`
  里的 `summary/tech_stack/habits` 也序列化进 prompt，明确告诉模型
  "这是你上一次总结出的画像，下面是这之后新增的会话摘要，请在上一版
  基础上更新，而不是只根据新增内容重新生成"。
- **取数范围也要跟着改**：目前的"取最近 20 条"是为了控制 prompt
  长度，改成增量更新之后，理论上只需要传"自上次画像生成以来新增的
  记忆条目"（`source_entry_count` 已经记录了上次生成时的条目数，天然
  可以用来做差集），而不必再固定截断最近 20 条——新增条目数量本身
  往往远小于 20，prompt 长度反而更可控。`max_entries_for_profile`
  可以保留作为"新增条目数万一异常多时的兜底上限"，含义从"每次总用
  这么多"变成"最多这么多"。
- **输出侧的合并策略**：不能简单地"新旧字符串拼接"（会导致
  summary 越滚越长、tech_stack/habits 重复项堆积）。要求 LLM 输出的
  仍然是一版完整、去重、精炼的 `summary/tech_stack/habits`，只是生成
  依据从"只看最近 N 条"变成"看上一版 + 新增证据"，新旧特征的取舍
  （哪些旧特征已经过时该被自然淘汰、哪些该保留、哪些该新增）交给 LLM
  在 prompt 里显式判断，而不是由代码规则决定——这跟
  `growth_advisor_improvement_plan_v3.md` 里"理解/归类文本一律走
  LLM，不引入额外规则系统"的既定原则一致。

### 3.2 Prompt 改造草案

`system/profile_summarizer.md` 追加一段规则：

```
If a previous profile is provided, treat it as your starting point:
keep any part that still holds up against the new evidence, update or
remove parts that the new evidence contradicts or that no longer seem
relevant, and add genuinely new observations. Do not simply summarize
only the new sessions in isolation.
```

`user/profile_update_request.md` 新增一个可选变量 `{{previous_profile}}`
（为空时——即首次生成——退化为现在的行为，不引入分支逻辑复杂度）：

```
{{#if previous_profile}}
Previous profile (built from earlier sessions):
{{previous_profile}}
{{/if}}

New session summaries since then:
{{memory_text}}
```

（具体模板语法以项目现有的 `pm.render` 实现为准，这里只表达变量
结构。）

### 3.3 `should_refresh` / 全量重建入口保留

- `should_refresh()` 的触发条件（新增条目数达到
  `refresh_interval_entries`）不需要改，只是触发后走的是"增量更新"
  而不是"全量重新生成"。
- 需要保留一个显式的"全量重建"入口（比如 `/profile rebuild`，区别于
  日常自动刷新的 `/profile` 或 cron 触发），用于用户觉得画像跑偏了、
  想从头再来的场景——直接复用现在的实现（不传 `previous_profile`），
  不需要新写代码，只是在命令层面区分两种调用方式。

### 3.4 `tech_stack`/`habits` 挂"最后被印证时间"（原第 4 节风险项，
评审后决定本轮直接做，不推迟）

**目的**：给"淘汰"这件事补一个代码层面能感知的信号，而不是完全依赖
LLM 每次都主动判断"这条是不是该删了"——LLM 在没有明确提示的情况下，
倾向于保留旧内容（增量更新的 prompt 本身也是鼓励"保留依然成立的
部分"），如果没有一个客观的"多久没被印证"信号提醒它去重新评估，容易
出现旧特征长期滞留、画像越攒越多的问题。

**存储结构变化**：`tech_stack`/`habits` 从"字符串列表"改为"结构化
条目列表"，每一项携带一个"最后被印证时间"：

```python
# profile.derived["tech_stack"] / profile.derived["habits"] 新结构
[
    {"text": "熟悉 Python 异步编程", "last_confirmed_at": 1723200000.0},
    {"text": "习惯先写测试再写实现", "last_confirmed_at": 1720000000.0},
    ...
]
```

- 首次生成（无 `previous_profile`）时，所有条目的 `last_confirmed_at`
  = 本次生成时间。
- 增量更新时，LLM 的输出仍然是纯文本条目列表（不要求 LLM 自己维护
  时间戳——时间戳是客观事实，不该交给 LLM 生成，容易出现幻觉时间）。
  代码侧按文本做匹配后处理：
  - 新一轮输出里，文本与上一版某一项**语义高度重合**（先做规则层面
    的宽松匹配，比如去空白/大小写归一化后完全相等；不做模糊匹配，
    避免误判合并了本该是两条不同的特征）的，视为"被再次印证"，
    `last_confirmed_at` 更新为本次生成时间。
  - 新一轮输出里，文本在上一版找不到对应项的，视为"新增"，
    `last_confirmed_at` = 本次生成时间。
  - 上一版存在、但本次 LLM 输出里没有再提到的条目，即"LLM 自己判断
    该淘汰的"，按现在的设计直接跟随 LLM 输出被移除（这部分淘汰机制
    本身不变，`last_confirmed_at` 只是给"LLM 选择保留"的条目提供一个
    可观测的新鲜度信号，不改变"LLM 决定去留"这个核心机制）。
- **展示/下游使用调整**：`_get_profile_text()`
  (`agent/profile.py:41-51`) 目前直接读 `derived.get("summary")`
  拼进 system prompt，不涉及 `tech_stack`/`habits`，本次改动不影响
  该路径。诊断面板 `diagnostics_snapshot()` 里的 `user_profile_snapshot`
  (`growth_advisor.py:2529-2535`) 目前把 `tech_stack`/`habits` 当
  字符串列表直接透出，结构变成对象列表后需要同步调整该处的取值
  逻辑（取 `text` 字段），否则看板展示会出错——这是一个必须同步修改
  的下游依赖点，已记入实现清单。
- **过期提示怎么用**：把"距今天数"超过一个阈值（草案：90 天，与
  `SIGNAL_SCAN_WINDOW_DAYS` 保持同一量级，具体值留到实现时再定）的
  条目在 prompt 里单独列出并标注"以下特征已经很久没有新证据支持，
  请重新评估是否仍然成立"，而不是简单按天数硬删——是否保留依然是
  LLM 的判断，代码只负责把"新鲜度"这个原本对 LLM 不可见的信号显式
  暴露出来。

**改动量级**：中——涉及 `profile.py` 的数据结构、prompt 输出解析、
`growth_advisor.py` 诊断面板一处下游读取点，以及需要一次性的旧数据
迁移（已有画像里 `tech_stack`/`habits` 是纯字符串列表的，加载时需要
兼容转换成新结构，`last_confirmed_at` 无法回溯只能取"本次加载时刻"
作为起始值——这个迁移期副作用值得在实现时写进代码注释，风格上对齐
`growth_advisor_improvement_plan_v3.md` 里"迁移期检查清单"的做法）。

### 3.5 与方向一的联动

方向一（记忆回填）落地后，`_maybe_refresh_profile()` 会因为长期记忆
条目突然大量补齐而被触发（`current_entry_count - last_count` 一次性
涨很多）。如果这时候还是"全量重新生成"，相当于一次性把大量历史
一起塞进 20 条窗口，早期记忆大概率还是会被挤掉。**这正是方向二必须
和方向一同批上线、而不是分别独立排期的原因**——否则记忆回填补上的
历史信息，画像这边依然看不见。

---

## 4. 风险与开放问题（含评审后决策）

1. **回填任务本身的 LLM 成本 / 是否要加"最多回溯多少天"窗口**：
   **决策：不加窗口。** `max_sessions_per_run` 的限流已经足够控制单轮
   开销；额外的时间窗口会让"陈年 session 永远没机会被回填"，与本方案
   的初衷（把遗漏的记忆尽量找回来）冲突，索性不引入，用限流 + 排序
   （候选按 `updated_at` 从旧到新或从新到旧的顺序，见下方待定项）
   自然控制节奏即可。
2. **2.4 节方案 A 的 `session_id` 合成规则是否会与真实 `Session.id`
   冲突** —— **已在改动前确认，结论：不会冲突，可以按原方案推进**。
   核实过程与依据：
   - 真实 `Session.id` 的生成方式是 `uuid.uuid4().hex[:8]`
     (`session.py:202`)——固定 8 位十六进制字符，**不含任何冒号或
     前缀**。本方案提议的合成 ID `cron:<job_id>:<run_id>` 带有
     `cron:` 前缀和冒号分隔符，在字符串层面与真实 ID 的取值空间
     完全不相交，不存在碰撞可能。
   - 过了一遍 `memory_store.py` 及其调用方对 `session_id` 的所有用法
     （`delete_by_session`/`upsert` 的去重比较、`context_builder.py`
     里 `m.session_id[:6]` 仅用于展示截断、API 层 `entry.session_id`
     的过滤/序列化），**全部是精确字符串相等比较或纯展示切片，没有
     任何地方对 `session_id` 的格式（长度/是否纯十六进制/是否含
     分隔符）做解析或断言**，合成 ID 可以安全地作为普通字符串值
     传入。
   - 额外确认了成长顾问侧：`growth_advisor.py` 用于证据追踪、去重的
     是 `MemoryEntry.entry_id`（`uuid.uuid4().hex[:12]` 自动生成，
     `memory_store.py:74`），**不是 `session_id`**，本方案完全不涉及
     `entry_id` 的生成方式，因此不影响成长顾问的信号扫描/候选去重
     逻辑。
   - 结论：2.4 节方案 A 可以按原设计推进，无需额外改动或加前缀
     校验。
3. **画像增量更新的"该淘汰的旧特征迟迟没被淘汰"问题** ——
   **决策：本轮直接引入"最后被印证时间"字段，不再推迟。** 具体设计
   见第 3.5 节（新增）。
4. **`profile.enabled` 默认值** —— **决策：改为默认 `True`**
   （`ProfileConfig.enabled`，`models.py:483`）。这意味着画像功能
   从 opt-in 变成 opt-out，与 `GrowthAdvisorConfig.enabled` 的默认
   策略（"零成本用起来"，见 `growth_advisor.py` 顶部注释）保持一致。
   需要同步确认/处理的连带事项：
   - `_maybe_refresh_profile()` 本身已经有"无记忆来源/记忆为空则
     安全跳过"的兜底（`agent/profile.py:63-73`），默认开启后不会对
     没有配置记忆功能的用户产生副作用或报错，只是"有记忆就会自动
     生成画像"从需要手动开启变成默认行为。
   - 需要在改动 `models.py:483` 的同时检查 `config/loader.py` 里
     `ProfileConfig` 对应的加载逻辑（`_fn(...)` 系列取默认值的调用
     点）是否也硬编码了 `False`，避免出现"dataclass 默认值改了，
     但加载路径的默认值没同步改，配置文件里没显式写
     `profile.enabled` 的用户实际读到的还是 False"这种两处不一致。
   - 需要在改动说明/发布记录里明确写清楚这一行为变化（"画像功能
     默认开启"），因为这是一次面向已有用户的默认行为变更，不是纯
     新增功能。

---

## 5. 优先级与分期建议

| 序号 | 方向 | 优先级 | 理由 | 改动量级 |
|---|---|---|---|---|
| M1 | 2.1-2.3 记忆回填（存量 session，不含 cron） | 高 | 直接解决"记忆总条数为 0"的大部分场景，改动集中、风险低，可复用 `session_cleanup.py` 的成熟模式 | 中 |
| M2 | 3.1-3.4 画像增量更新（含"最后被印证时间"，本轮一并做） | 高，且应与 M1 同批 | 不做的话 M1 补齐的记忆依然进不了画像（见 3.5） | 中 |
| M3 | 2.4 方案 A（cron 任务直接产出记忆） | 中 | 覆盖"daemon 跑的任务大部分没有 memory"这一半的问题，但改动面涉及 cron 执行链路收尾逻辑，需要更仔细的测试（尤其是 timed_out/needs_human_review 场景不应该产出记忆） | 中偏大 |
| M4 | 2.4 方案 B（cron 全面持久化 session） | 低，先不做 | 收益是架构统一，但改变 cron 核心设计前提，影响面评估成本本身就很高，建议观察 M3 上线效果后再决定是否需要 | 大，需单独立项 |

---

## 6. 验收标准

- 对一批"人为中断（kill 掉进程）"和"cron 自动触发"的历史 session，
  跑一次 `/memory backfill --dry-run`，候选列表里应该能看到这些
  session（对照现在的 0 条基线）。
- 回填执行后，成长顾问诊断信息里的"记忆总条数"/"落在扫描窗口内的
  条数"应显著回升，且信号扫描能命中至少一部分内置主题（不再是全 0）。
- 画像刷新前后对比：故意构造"早期记忆包含 A 特征，最近 20 条记忆
  不再提及 A，但 A 依然成立"的场景，增量更新后的画像应仍保留 A（而
  当前的全量替换实现会丢失 A）——可以作为一个手工验证 case，也可以
  沉淀成 `tests/test_profile.py` 里的一个用例。
