# 用户画像"信息来源不够全"改进方案

- **版本**: v1——**第一期（watchlist / preferences 接入）已实施**，详见
  文末"实施记录"一节；其余方向待评估排期。
- **前置文档**:
  - `next_doc/profile_staleness_and_goal_tree_gap_plan.md`（已实施：
    画像刷新时间兜底 + 目标树接入画像，本文档是它的延续，处理"目标树
    之外还有哪些用户相关信息源没被用上"）
  - `next_doc/memory_backfill_and_profile_update_plan.md`（画像增量更新
    机制的基础设计，本文档不改动其核心增量逻辑，只扩充输入源）
  - `src/mini_agent/profile.py`（`UserProfileManager.generate()`）
  - `src/mini_agent/external_input/watchlist.py`
  - `apps/mini_agent_kanban/app.py::_render_growth_profile_and_keywords`
- **触发背景**: 用户看到看板"🧠 Agent 对你的了解"里的画像内容后反馈
  "感觉信息来源不够全，没有充分利用系统中所有用户相关的信息"，并具体
  举例"用户的目标树信息、用户关注的信息"。经代码核实，目标树已经在
  上一期接入，但确实还有其它几类用户相关信息完全没有进入画像生成的
  输入链路。

---

## 现状核实

`UserProfileManager.generate()`（`profile.py`）当前的输入组成：

| 输入 | 来源 | 是否已接入 |
|---|---|---|
| 长期记忆摘要（`memory_text`） | `MemoryEntry.summary/tags`，全量首建/增量更新两种模式 | ✅ 核心输入 |
| 上一版画像（`previous_profile_block`） | 增量更新时拼接旧 `summary/tech_stack/habits` | ✅ |
| 目标树快照（`goal_tree_block`） | `goal_tree_report.build_goal_tree_profile_snapshot()`，最多 8 个活跃 Goal 标题 + 最近一条产出摘要 | ✅（上一期接入） |
| 用户显式关注的话题（`watchlist.yaml`） | `WatchlistMatcher` 消费的配置文件，用户手动维护的关注关键词列表 | ✅（本期新增，见下） |
| 用户显式设置的偏好（`profile.preferences`） | `UserProfileManager.set_preference()` | ✅（本期新增，见下） |
| growth_advisor 自维护的关注领域/关键词（`derived["growth_focus_areas"]` / `derived["growth_topic_keywords"]`） | `evolution/growth_advisor.py` | ❌ 未接入 |
| 已完成的目标树节点（历史成果，而非仅"活跃中"） | `goal_tree_report.py` | ❌ 未接入（`build_goal_tree_profile_snapshot()` 只取 `by_status["active"]`） |
| wiki / 知识库内容（`research/` 目录下 wiki 条目） | `wiki/` 子系统 | ❌ 未接入（只有当某条内容恰好被总结进了某条 memory entry 时才间接可见） |
| 决策画像（`decision_profile`，看板"配置"页可见的另一套 profile） | `api/routes.py` 的 `/decision_profile` 系列接口 | ❌ 完全独立的另一套机制，跟 `UserProfileManager` 没有任何数据交换 |
| 用户在对话中的显式指令性反馈（如"用简洁的结构化摘要回复我"这类元指令） | 分散在各条 session 的历史消息里 | ⚠️ 间接可见（只有被某条 memory entry 摘要捕捉到才算数，命中靠运气，跟"目标树只有偶然被提到才进入画像"是同一类结构性缺口） |

结论：这不是"LLM 总结得不够好"的问题，而是**多个信息源天然分散在系统
不同子模块里，`generate()` 只主动去拉了其中两三个**，其余的即使内容
本身很适合出现在画像里，也因为没有专门的采集通路，只能"恰好被写进某条
memory 摘要"才有机会被看到——这跟上一期文档指出的目标树缺口是完全同一
类问题，只是换了几个具体信息源。

---

## 已实施：第一期（watchlist / preferences 接入）

参照 `goal_tree_block` 的既有模式（零 LLM 成本、独立于"上一版画像"、
每次生成时重新拉取当前状态、任一环节异常兜底为空串，不影响主流程），
新增两个并列的背景信息块：

**1. `watchlist_block`** —— 新增
`external_input/watchlist.py::build_watchlist_profile_snapshot()`：

```python
def build_watchlist_profile_snapshot(paths, *, max_items: int = 10) -> str:
    items = load_watchlist_config(paths)
    enabled = [it for it in items if it.enabled]
    ...
    # 只取 id + keywords，不展开命中的具体新闻内容
```

理由：`watchlist.yaml` 是用户显式配置的"我要关注这些话题"，比"从历史
会话摘要里反推用户关心什么"更直接、更权威，且此前完全没有被画像生成
使用过。

**2. `preferences_block`** —— `profile.py::generate()` 内联生成，把
`profile.preferences` 的键值对作为**既定事实**（不是需要会话证据验证
的推测）传给 LLM。

两个 prompt 模板（`prompts/user/profile_update_request.md` /
`prompts/system/profile_summarizer.md`）已同步更新变量说明和使用指引：
明确告诉模型"目标树/watchlist 只是背景，不要凭标题瞎编 tech_stack"、
"preferences 是既定事实，不用验证、不要质疑"。

**已知限制**：`profile.preferences` 目前是"有存取接口但没有任何调用方"
的死功能——代码库里搜不到任何地方调用过 `set_preference()`。本期改动
打通了"如果有数据，会被使用"这条链路，但不会凭空产生数据。要让这块
真正起作用，需要方向 D（见下）补上写入入口。

---

## 后续改进方向（未实施，待评估排期）

### 方向 A：growth_advisor 自维护的关注领域反哺回画像

`derived["growth_focus_areas"]` / `derived["growth_topic_keywords"]`
是 growth_advisor 通过分析用户行为持续积累的"agent 认为用户关注什么"，
跟画像的"summary"在语义上高度相关，但两者目前是完全独立的两条产出，
在看板上分别展示（"Agent 对你的了解" vs "关键词列表"），互相没有校准。

**建议**：作为第三个并列的背景块（`growth_focus_block`）传入
`generate()` 的 prompt，明确告知模型"这是 agent 自己已经在跟踪的领域，
如果 summary 里已经体现，不用重复；如果有冲突，以本次的记忆证据为准"。
需要注意避免循环依赖——`growth_advisor.py` 和 `profile.py` 目前是否有
双向 import 关系需要先确认，建议沿用现有 `goal_tree_block` 的做法，
在 `generate()` 内部延迟 import + try/except 兜底，不改变模块间的
静态依赖方向。

### 方向 B：目标树快照补充"最近完成的目标"

`build_goal_tree_profile_snapshot()` 目前只读取
`report.by_status.get("active")`，完全不涉及已完成节点。建议追加一小段
"最近 N 个已完成的 Goal 标题（按完成时间倒序，取 3~5 个）"，让画像的
summary 能体现"进展"而不只是"在做什么"——这也直接对应用户原话里
"感觉不够全"的一部分：现在的画像读起来像是"正在做什么的快照"，缺少
"已经做成了什么"的历史纵深。

实现上可复用 `report.by_status.get("completed")`（需确认
`GoalTreeReport` 是否已有这个分组；如果没有，需要在
`goal_tree_report.py` 补一个轻量的"按完成时间排序取 top N"逻辑，避免
整份 `to_dict()` 的重量级序列化）。

### 方向 C：wiki / 知识库内容摘要接入

`research/` 目录下的 wiki 条目（如用户示例里提到的
`research/agent_and_ai` 技术 wiki、`research/agent_and_ai_advice`
改进建议）是 agent 长期产出的结构化知识，理论上比零散的记忆摘要更能
反映"用户主导的长期任务做到了什么程度"。

**建议**（分两步，避免一次引入过多 LLM 成本）：
1. 先做"零 LLM 成本"版本：只取 wiki 目录下最近更新的条目标题 + 更新
   时间，作为一个轻量背景块（类似 goal_tree_block），不读取正文内容。
2. 如果标题级别的信息不够，再评估是否需要对 wiki 正文做单独的摘要
   （可能需要复用 `wiki/stats.py` 或 `wiki/promotion.py` 里已有的
   统计/摘要能力，而不是重新对全文调用一次 LLM——那样成本和延迟都会
   明显上升，需要先确认必要性）。

### 方向 D：让 `profile.preferences` 真正可写

当前 `set_preference()` 没有任何调用方，是本期改动发现的一个"死代码"
现象。建议至少提供一个入口：
- 对话内命令（如 `/profile set <key> <value>`），或
- 看板"⚙️ 配置"tab 里加一个简单的键值对编辑区。

不建议让 LLM 自动写入 `profile.preferences`——这个字段的设计初衷就是
"用户显式设置、系统不会自动覆盖"（见 `profile.py` 模块文档字符串），
如果改成模型自动写入，会跟 `derived.tech_stack/habits`（模型推断）在
语义上重叠，破坏"preferences vs derived"这条既有的职责分离。

### 方向 E（架构性，优先级较低）：统一的 ProfileContextCollector

目前每接入一个新信息源，都是在 `generate()` 内部手写一段
"try: from xxx import build_xxx_snapshot ... except: 空串"的样板代码，
本次改动本身也是这个模式的第三次重复（继 `goal_tree_block` 之后）。
如果后续还会持续增加信息源（方向 A/B/C 加起来会有 3 个以上新的
snapshot block），建议抽象成统一的注册机制：

```python
# 概念示意，非最终实现
PROFILE_CONTEXT_PROVIDERS: list[Callable[[AgentPaths], str]] = [
    build_goal_tree_profile_snapshot,
    build_watchlist_profile_snapshot,
    build_growth_focus_snapshot,      # 方向 A
    build_recent_completed_goals_snapshot,  # 方向 B
    build_wiki_recent_updates_snapshot,     # 方向 C
]

def _collect_context_blocks(paths) -> str:
    blocks = []
    for provider in PROFILE_CONTEXT_PROVIDERS:
        try:
            snippet = provider(paths)
            if snippet:
                blocks.append(snippet)
        except Exception:
            continue
    return ("\n\n".join(blocks) + "\n\n") if blocks else ""
```

这样新增一个信息源只需要写一个符合签名的函数并注册，不需要改
`generate()` 主体逻辑，也不需要每次都改 prompt 模板的变量列表（可以
统一渲染成一个 `{{context_blocks}}` 变量）。**这一步是纯重构，不改变
行为**，建议在方向 A/B/C 至少落地一个之后再做，避免为了还不存在的
扩展性提前设计。

---

## 优先级建议

1. 方向 D（补 preferences 写入入口）—— 成本最低，否则本期已实施的
   `preferences_block` 长期是"有能力用但没数据可用"的空转状态。
2. 方向 B（已完成目标快照）—— 复用现有 `goal_tree_report` 基础设施，
   增量工作量小，直接回应用户"缺少历史纵深"的观感。
3. 方向 A（growth_focus 反哺）—— 需要先确认模块间依赖方向，工作量
   中等。
4. 方向 C（wiki 接入）—— 收益可能最大（用户举的例子里两个长期任务都
   落在 wiki 目录），但需要先明确"标题级别够不够"，避免过度设计。
5. 方向 E（统一采集器）—— 等方向 A/B/C 至少完成一个后再重构，避免
   提前抽象。

---

## 实施记录

- **第一期**（本次）：
  - `external_input/watchlist.py` 新增 `build_watchlist_profile_snapshot()`。
  - `profile.py::generate()` 接入 `watchlist_block` 与 `preferences_block`，
    并列于既有的 `goal_tree_block`。
  - `prompts/user/profile_update_request.md` / `prompts/system/profile_summarizer.md`
    同步更新变量说明与使用指引。
  - 已知限制：`preferences_block` 目前无实际数据（见方向 D）。
