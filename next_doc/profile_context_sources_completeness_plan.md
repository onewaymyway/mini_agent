# 用户画像"信息来源不够全"改进方案

- **版本**: v4——方向 **A/B/C/D/E 全部已实施**（D 含 CLI + 看板双入口，
  E 为纯重构，不改变模型侧行为），详见文末"实施记录"一节。
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
| growth_advisor 自维护的关注领域/关键词（`derived["growth_focus_areas"]` / `derived["growth_topic_keywords"]`） | `evolution/growth_advisor.py` | ✅（方向 A，本轮接入） |
| 已完成的目标树节点（历史成果，而非仅"活跃中"） | `goal_tree_report.py` | ✅（方向 B，本轮接入，`build_goal_tree_profile_snapshot()` 新增"Recently completed goals"小节） |
| wiki / 知识库内容（`research/` 目录下 wiki 条目） | `wiki/` 子系统 | ✅（方向 C 第一步，本轮接入标题级别快照；正文摘要留作未来可选的第二步） |
| 决策画像（`decision_profile`，看板"配置"页可见的另一套 profile） | `api/routes.py` 的 `/decision_profile` 系列接口 | ❌ 仍是完全独立的另一套机制，本轮未处理（设计上两者定位不同，是否要打通留待后续单独评估，不属于本文档范围内的"信息源缺口"） |
| 用户在对话中的显式指令性反馈（如"用简洁的结构化摘要回复我"这类元指令） | 分散在各条 session 的历史消息里 | ⚠️ 仍是间接可见（只有被某条 memory entry 摘要捕捉到才算数）——这类"元指令"更适合归入方向 D 的 preferences 机制（用户可以用 `/profile set` 显式声明，不需要再等系统"恰好总结到"），本轮已经打通该入口，但依赖用户主动使用 

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

## 后续改进方向（全部已实施，见"实施记录"）

### 方向 A：growth_advisor 自维护的关注领域反哺回画像 ——已实施

`derived["growth_focus_areas"]` / `derived["growth_topic_keywords"]`
是 growth_advisor 通过分析用户行为持续积累的"agent 认为用户关注什么"，
跟画像的"summary"在语义上高度相关，但两者目前是完全独立的两条产出，
在看板上分别展示（"Agent 对你的了解" vs "关键词列表"），互相没有校准。

**实现方式**：在 `generate()` 内部直接读取本函数已经 `load()` 出来的
`profile.derived.get("growth_focus_areas")`（`{topic: [entry_id,...]}`），
不需要 import `growth_advisor` 模块，天然不会有循环依赖问题。按命中
记忆条目数降序取前 8 个主题名，只取主题名，不展开命中的 entry_id 列表
（那是诊断细节）。Prompt 里明确标注这是"规则扫描检测到的信号，权重
弱于用户显式声明的偏好/watchlist，如有冲突以其它信号为准"。

### 方向 B：目标树快照补充"最近完成的目标" ——已实施

`build_goal_tree_profile_snapshot()` 此前只读取
`report.by_status.get("active")`，完全不涉及已完成节点，画像读起来像
"正在做什么的快照"，缺少"已经做成了什么"的历史纵深。

**实现方式**：复用 `report.by_status.get("completed")` 拿到 id/title，
回源到 `GoalBacklog` 节点读取 `status_history` 里最近一次转为
`"completed"` 的时间戳做排序（`status_history` 缺失的旧数据退化用
`last_touched_at` 兜底），取最近 5 个拼成"Recently completed goals"
小节，追加在原有"Active long-running goals"之后。Prompt 里同步提示
模型可以用这段信息在 summary 里体现"进展"（如"已完成 X，目前专注于
Y"），不只是罗列在做什么。

### 方向 C：wiki / 知识库内容摘要接入 ——已实施（第一步：零成本版本）

`research/` 目录下的 wiki 条目（如用户示例里提到的
`research/agent_and_ai` 技术 wiki、`research/agent_and_ai_advice`
改进建议，对应 `AgentPaths.wiki_research_dir`）是 agent 长期产出的
结构化知识，理论上比零散的记忆摘要更能反映"用户主导的长期任务做到了
什么程度"。

**实现方式**：在 `wiki/stats.py` 新增 `build_wiki_recent_updates_
snapshot()`，复用既有的 `wiki/indexer.py::discover_pages()` +
`wiki/parser.py::parse_page()` 而不是重新写文件系统遍历逻辑；只扫描
`wiki_research_dir`（持续调研）和 `wiki_growth_dir`（成长顾问学习
素材，两者数据结构相同，只是命名空间不同）这两个跟"用户主导的长期
任务"直接相关的命名空间，不扫描 entities/decisions/experiences 等
agent 自身知识沉淀的命名空间（避免稀释）。按 `updated`（缺失时退化用
`created`）倒序取最近 8 条，只取标题（页面 `id`）+ 更新时间，不读取
正文——评估要不要接入正文摘要留给第二步（见下"未来可选的第二步"）。

**未来可选的第二步**（本次未做，暂无明确必要性信号，先不做）：如果
标题级别的信息在实际使用中被证明不够，再评估是否要对 wiki 正文做
单独摘要；不建议直接对全文调用一次额外的 LLM（成本和延迟都会明显
上升），应该先看能不能复用 `wiki/stats.py` 或 `wiki/promotion.py` 里
已有的统计/摘要能力。

### 方向 D：让 `profile.preferences` 真正可写 ——已实施

`set_preference()` 此前没有任何调用方，是发现问题时的一个"死代码"
现象——即便方向一新增的 `preferences_block` 打通了"有数据就会被使用"
这条链路，没有写入入口的话也只是空转。

**实现方式**：在 `cli/repl.py` 的 slash 命令分发里，`/profile` 原有的
`rebuild`/`scan`/无参数（默认增量刷新）三个分支之前，新增三个子命令：
- `/profile set <key> <value...>` —— 调用 `agent._profile_mgr.
  set_preference(key, value)`，value 允许包含空格（原样拼接剩余
  token）。
- `/profile unset <key>` —— 直接操作 `profile.preferences` 字典并
  `save()`。
- `/profile show [key]` —— 只读展示，不带 key 时列出全部偏好，带 key
  时只显示这一条；跟"无参数 `/profile`（触发刷新）"明确区分，`show`/
  `get` 本身不触发任何刷新。

同步更新了 `ui/terminal.py` 里 `/help` 展示的 `/profile` 子命令列表。

**已知限制（第五期已解决，见下）**：这只打通了 CLI 侧的写入入口；看板
（Streamlit `apps/mini_agent_kanban`）此前没有对应的可视化编辑区。

### 方向 E（架构性）：统一的 ProfileContextCollector ——已实施

`generate()` 内部原本有 5 段几乎一样的"try: from xxx import build_xxx_
snapshot ... except: 空串"样板代码（`goal_tree_block` / `watchlist_
block` / `preferences_block` / `growth_focus_block` / `wiki_block`）。

**实现方式**：在 `profile.py` 里新增 5 个模块级 provider 函数
（`_profile_context_goal_tree` / `_profile_context_watchlist` /
`_profile_context_preferences` / `_profile_context_growth_focus` /
`_profile_context_wiki`），统一签名 `(paths, profile) -> str`——不需要
数据的参数直接忽略；`_PROFILE_CONTEXT_PROVIDERS` 注册表按顺序收录
这 5 个函数；`_collect_profile_context_blocks(paths, profile)` 依次
调用、拼接非空结果，每个 provider 内部各自 try/except 兜底，外层再
兜一层双重保险。`generate()` 主体现在只有一行
`context_blocks = _collect_profile_context_blocks(self._paths, profile)`，
prompt 模板对应改成单个 `{{context_blocks}}` 变量（原来 4 个具名变量
`goal_tree_block`/`watchlist_block`/`preferences_block`/
`growth_focus_block`/`wiki_block` 合并为一个）。

**为什么现在可以做**：当初"本次判断不做"的顾虑是"签名不统一（`growth_
focus_block` 需要 `profile` 对象，其它几个只需要 `paths`）、强行统一
会丢失针对性上下文"——实际操作后发现这个顾虑被高估了：让所有 provider
统一接收 `(paths, profile)`，不需要 `profile` 的函数直接在参数列表里
忽略掉即可，Python 不要求用到所有形参，并不需要"丢失"任何针对性上下文。
且方向 A/C 已经把 5 个信息源全部落地，"等后续再新增第 6/7 个"的触发
条件已经不需要再等——五段样板本身已经足够重复到值得抽象。

**纯重构，不改变模型侧行为**：`_collect_profile_context_blocks()` 拼接
顺序与原来 5 段手写代码的拼接顺序完全一致（goal_tree → watchlist →
preferences → growth_focus → wiki），每个 provider 函数体内部逻辑
原样迁移，未修改任何文本内容/格式；`prompts/system/profile_
summarizer.md` 里对各类背景信息的语义说明不依赖具体变量名，不需要
改动。

---

## 优先级（实施时的实际顺序）

按文档 v1 版本给出的优先级依次实施：D → B → A → C → D 收尾（看板可视化）
→ E。实际实施顺序与建议顺序一致，E 从"按计划暂不做"改为实施是因为
操作后发现当初的顾虑（签名不统一）被高估了，详见方向 E 小节说明。

---

## 实施记录

- **第一期**（watchlist / preferences 接入）：
  - `external_input/watchlist.py` 新增 `build_watchlist_profile_snapshot()`。
  - `profile.py::generate()` 接入 `watchlist_block` 与 `preferences_block`，
    并列于既有的 `goal_tree_block`。
  - `prompts/user/profile_update_request.md` / `prompts/system/profile_summarizer.md`
    同步更新变量说明与使用指引。
  - 已知限制：`preferences_block` 当时无实际数据（第二期已通过方向 D 补上写入入口）。

- **第二期**（方向 D：preferences 写入入口）：
  - `cli/repl.py` 新增 `/profile set|unset|show` 三个子命令。
  - `ui/terminal.py` 的 `/help` 列表同步更新 `/profile` 子命令提示。
  - 已知限制（第五期已解决）：当时仅 CLI 入口，看板可视化编辑未做。

- **第三期**（方向 B：已完成目标快照）：
  - `perception/goal_tree_report.py::build_goal_tree_profile_snapshot()`
    新增"Recently completed goals"小节（按 `status_history` 完成时间
    倒序，取最近 5 个），新增 `max_completed_goals` 参数。
  - `prompts/system/profile_summarizer.md` 同步补充对这段新内容的
    使用说明。

- **第四期**（方向 A：growth_focus 反哺 + 方向 C：wiki 接入）：
  - `profile.py::generate()` 新增 `growth_focus_block`（直接读取已
    `load()` 的 `profile.derived["growth_focus_areas"]`，取命中数
    前 8 的主题名）。
  - `wiki/stats.py` 新增 `build_wiki_recent_updates_snapshot()`（扫描
    `wiki_research_dir` + `wiki_growth_dir`，取最近更新的 8 条标题）。
  - `profile.py::generate()` 接入 `wiki_block`。
  - `prompts/user/profile_update_request.md` / `prompts/system/profile_summarizer.md`
    同步更新变量说明与使用指引（含"growth_focus_block 是弱信号，冲突时
    以其它信号为准"的显式提示）。

至此，`UserProfileManager.generate()` 的输入已从最初的"仅长期记忆
摘要"扩展到"记忆 + 上一版画像 + 活跃目标 + 已完成目标 + watchlist +
显式偏好 + agent 自检测关注领域 + wiki 最近更新"共 8 类信息源，覆盖了
本文档"现状核实"表格里除方向 E（架构重构，非信息源缺口）之外的全部
已识别缺口。

- **第五期**（方向 D 收尾：看板可视化编辑）：
  - `api/routes.py` 新增三个端点：`GET /v1/user_profile/preferences`
    （读取）、`POST /v1/user_profile/preferences`（新增/覆盖一条，
    body `{"key","value"}`）、`POST /v1/user_profile/preferences/delete`
    （删除一条，body `{"key"}`——沿用本文件 `/growth/keywords/{topic}/...`
    历史 bug 的教训，key 走 body 不走路径参数，避免 `/` 等分隔符在
    URL 编解码环节出问题）。三个端点内部都直接复用
    `UserProfileManager`，跟 CLI 的 `/profile set/unset/show` 是同一份
    数据、同一条写入路径，两边改了互相可见。
  - `apps/mini_agent_kanban/client.py` 新增对应的
    `get_user_profile_preferences()` / `set_user_profile_preference()`
    / `delete_user_profile_preference()`。
  - `apps/mini_agent_kanban/app.py` 在"🌱 成长顾问"tab 的"Agent 对你的
    了解"区块下方新增"✏️ 我的偏好设置"可折叠编辑区：列出已有偏好
    （每条带删除按钮）+ 一个新增/更新表单，跟"agent 从记忆里推断出的
    画像"摆在同一处，方便对照"agent 猜的"和"我自己说的"。

至此方向 D 的"已知限制"已解决，A/B/C/D 四个方向全部端到端完成
（后端信息源接入 + 数据写入入口 + 看板可视化）。

- **第六期**（方向 E：统一 ProfileContextCollector 重构）：
  - `profile.py` 新增 5 个模块级 provider 函数 + `_PROFILE_CONTEXT_
    PROVIDERS` 注册表 + `_collect_profile_context_blocks()`，替换掉
    `generate()` 内部原有的 5 段手写 try/except 样板代码。
  - `prompts/user/profile_update_request.md` 的具名变量从
    `goal_tree_block`/`watchlist_block`/`preferences_block`/
    `growth_focus_block`/`wiki_block` 五个合并为一个
    `{{context_blocks}}`；`prompts/system/profile_summarizer.md` 无需
    改动（语义说明不依赖具体变量名）。
  - 纯重构，拼接顺序和每个信息源的文本内容与重构前完全一致，不改变
    模型侧看到的 prompt 内容。
  - 新增信息源的成本从"改 `generate()` 主体 + 改 prompt 模板变量列表"
    降到"在 `_PROFILE_CONTEXT_PROVIDERS` 里加一行注册"。

至此本文档识别的全部改进方向（A/B/C/D/E）均已实施完毕。
