# 能力学习 wiki 内容完整性判定与刷新周期改进方案

- **触发背景**：用户反馈"看板中能力学习标签页的 wiki 一直没有更新"，
  排查后定位到 `sys:capability_learning_cycle` 每轮"处理 Track 1 个，
  检索 0 个子主题"并非执行失败，而是 `scan_outline_gaps()` 的既有规则：
  `coverage_state == "covered"` 且不 `stale` 的子主题会被永久跳过；而
  `OutlineTopic.volatility` 默认值 `"stable"` 在 `_needs_staleness_refresh()`
  里被硬编码为永不过期——只要某个子主题曾经被判定为 `covered`（哪怕
  只是检索到了一句话、内容明显不够），就再也不会被系统自己重新触达。
- **用户诉求（原话整理）**：
  1. wiki 里没有有效内容时，应该自动判定为需要更新（这一点 v0.21.1 的
     `research_empty` 修复已覆盖，本方案不重复实现）；
  2. **内容太少**（不是完全空，但明显单薄）也应该判定为需要更新——
     这一点目前完全没有判定逻辑；
  3. 创建 wiki 时应该同时记录"这份数据是否足够完整"这个信号，而不是
     只在内存里算一下就丢掉；
  4. **不应该有 `stable`** 这个"永不过期"的默认档位，大部分 wiki 都应该
     有一定的刷新周期，定期被重新检索验证。
- **定位**：这是对 v0.21 引入的"§13.2-d 知识时效性衰减"机制的一次补强，
  不是新起一套体系——延续同一套 `coverage_state`/`volatility`/
  `scan_outline_gaps()` 骨架，只是把"完整性"这个此前完全没有量化的维度
  接进去，并调整默认值取向。

---

## 方案

### 阶段 1：内容完整性评分（thin/empty/sufficient 三态）

**现状**：`run_capability_learning_cycle()` 里的 `has_real_content` 是
二元判断——`results` 里只要有一条非空 `summary`/`text` 就算"有内容"，
不管这条内容有多短。

**改动**：把二元判断扩展成三态：

| 态 | 判定条件 | `coverage_state` | 台账 action |
| --- | --- | --- | --- |
| `empty` | 所有结果摘要都是空字符串（或 `results` 本身为空） | `partial`（不变，v0.21.1 已实现） | `research_empty`（不变） |
| `thin` | 有非空摘要，但合并后总字数 `< CONTENT_SUFFICIENT_MIN_CHARS`（新增常量，默认 `120`） | `partial`（新增：此前会被错误标成 `covered`） | 新增 `research_thin` |
| `sufficient` | 合并后总字数达到阈值 | `covered` | `researched`（不变） |

阈值先写死常量（沿用本项目"P1 先写死，后续可迁移进 config_catalog"的
一贯做法），不做成配置项——避免过早引入不确定性。

**完整性信号落盘**：`make_wiki_writer()`/`make_agent_wiki_writer()`
写入页面时，在 frontmatter 新增 `content_completeness` 字段（取值
`empty`/`thin`/`sufficient`），供看板展示、也供人工排查时直接从 wiki
页面本身看出"这页内容够不够"，不用回头翻学习台账。

**接口改动（向后兼容）**：`run_capability_learning_cycle()` 调用
`wiki_writer(topic, track, results)` 时，改为优先尝试带
`completeness=` 关键字参数调用；`TypeError`（旧签名的自定义
`wiki_writer` 不接受这个参数）时自动退回不带该参数的调用——不强制
所有自定义 `wiki_writer` 实现都跟着改签名，符合本项目"失败路径回退
到宽松默认"的一贯约定。

### 阶段 2：默认刷新周期（去掉 `stable` 默认档位）

**改动**：`OutlineTopic.volatility` 默认值从 `"stable"` 改为
`"periodic"`（30 天）。`stable` 保留为可选值（确实存在极少数内容
基本不随时间变化，用户可以在看板/CLI 手动把某个子主题标注回
`stable`），但不再是新建子主题的默认值——不管是 `/capability create`
起草大纲、`accept_outline_suggestion()` 采纳建议新增子主题，还是
`OutlineTopic()` 直接实例化，全部统一走 dataclass 默认值，一处改动
全覆盖。

**存量数据迁移**：新增 `migrate_stable_volatility_to_periodic(paths)`
函数 + CLI 子命令 `/capability migrate-volatility`，批量把已持久化
Track 里 `volatility == "stable"` 的子主题改成 `"periodic"`，返回
受影响的 Track/子主题数。不做成自动迁移（不在 daemon 启动时静默改
用户数据），由用户显式触发一次，符合本项目一贯"改动用户数据前需要
显式确认"的取向。

### 阶段 3（补充）：thin 内容立即重试，不受 30 天周期限制

`thin`/`empty` 都归 `partial`，`scan_outline_gaps()` 里 `partial` 本来
就在 `covered` 之前被优先选中——也就是说内容不够的子主题下一轮就会
重新进候选池，不需要等 `periodic` 的 30 天窗口。30 天周期只对已经
`sufficient` 且判定 `covered` 的子主题生效，符合"内容不够立刻重试、
内容够了定期复检"的直觉。

---

## 不做的事（本轮刻意不做，避免过早引入不确定性）

- 不引入更复杂的内容质量判定（比如 LLM 判断"内容是否准确/是否回答了
  子主题"）——本轮只解决"量"的问题（太短/太空），"质"的判断留给
  §13.3-g 合规过滤之外的未来工作，需要先有真实误判案例再评估要不要做。
- `CONTENT_SUFFICIENT_MIN_CHARS` 阈值不做成配置项，先观察默认值 120
  是否合适，避免一上来就暴露一个用户也不知道怎么调的参数。
- 不自动把存量 `stable` 数据迁移，需要用户显式触发 `/capability
  migrate-volatility`。

### 阶段 4（补充，用户追加需求）：一键"刷新所有存量"

用户反馈"应该有个按钮，可以刷新所有存量，让所有存量进入需要刷新的
状态"——阶段 2 的 `migrate-volatility` 只解决"以后按周期自动刷新"，
不解决"现在立刻让存量内容重新进候选池"这个更直接的诉求，两者不冲突，
是互补的两条路径。

新增 `CapabilityTrackStore.force_refresh_all_topics(track_id=None)`：
把 `coverage_state=="covered"` 的子主题批量重置为 `"partial"`（不清空
`wiki_page_ids`，旧内容在重新检索出新内容前仍可读），立刻重新进入
`scan_outline_gaps()` 候选池，不用等 `volatility` 的周期性窗口。
`track_id` 为空对所有 Track 生效，传入具体 id 只影响该 Track。幂等。

三个接入点：
- CLI：`/capability refresh-all [track_id]`
- HTTP：`POST /v1/capability/tracks/refresh_all?track_id=...`
- 看板：「🎓 能力学习」Tab 顶部「🔄 刷新所有存量」全局按钮 + 每个 Track
  详情展开区里的「🔄 刷新此 Track」按钮

## 实施状态

| 阶段 | 状态 | 涉及文件 |
| --- | --- | --- |
| 阶段 1：完整性三态判定 + frontmatter 落盘 | ✅ 已实现 | `src/mini_agent/evolution/capability_learning.py` |
| 阶段 2：默认 volatility 改为 periodic + 迁移函数/命令 | ✅ 已实现 | `src/mini_agent/evolution/capability_learning.py`、`src/mini_agent/cli/commands/capability_cmd.py` |
| 阶段 3：thin 内容立即重试（复用既有 partial 优先级逻辑，无需额外改动） | ✅ 已实现（随阶段 1 一并生效） | `src/mini_agent/evolution/capability_learning.py` |
| 阶段 4：一键刷新所有存量（CLI + HTTP + 看板按钮） | ✅ 已实现 | `src/mini_agent/evolution/capability_learning.py`、`src/mini_agent/cli/commands/capability_cmd.py`、`src/mini_agent/api/capability_routes.py`、`apps/mini_agent_kanban/client.py`、`apps/mini_agent_kanban/app.py` |
| 文档同步 | ✅ 已实现 | `next_doc/persona_capability_learning_design.md`（§14.6 小节） |
| 测试 | ✅ 已实现 | `tests/test_capability_wiki_freshness.py`（18 个用例，含阶段 4 的 6 个）；`tests/test_capability_learning_p1.py`/`tests/test_capability_learning_empty_retrieval_fix.py` 中因阈值语义变化需要调整的既有用例（调大测试用摘要文本长度、显式标注 `volatility="stable"`）已同步修正 |

### 阶段 1 落地细节

- **常量**：`CONTENT_SUFFICIENT_MIN_CHARS = 120`（写死，未做成配置项）。
- **三态判定**：`run_capability_learning_cycle()` 里把合并后的有效摘要
  字数分成 `empty`（0 字）/ `thin`（< 120 字）/ `sufficient`（≥ 120 字）
  三档；只有 `sufficient` 才允许 `coverage_state="covered"`，`thin`/
  `empty` 都保持/回退 `partial`。学习台账新增 `research_thin` action；
  `run_capability_learning_cycle()` 返回值新增 `topics_research_thin`
  计数（`topics_research_empty` 含义不变）。
- **frontmatter**：`make_wiki_writer()`/`make_agent_wiki_writer()`
  新增可选关键字参数 `completeness`，写入页面 frontmatter 的
  `content_completeness` 字段（`empty`/`thin`/`sufficient`）；调用方
  不传时按同一套阈值口径自行兜底计算，保证任何调用路径产出的页面都
  带这个字段。`thin` 状态下正文末尾会追加一句"内容偏少，后续会继续
  补充"的提示文案。
- **向后兼容**：`run_capability_learning_cycle()` 调用 `wiki_writer`
  时优先带 `completeness=` 关键字调用，`TypeError`（不接受该参数的
  旧式三参数签名）时自动退回旧式调用——不强制所有自定义 `wiki_writer`
  跟着改签名。已用 `test_legacy_three_arg_wiki_writer_still_works`
  覆盖。

### 阶段 2 落地细节

- `OutlineTopic.volatility` 默认值从 `"stable"` 改为 `"periodic"`
  （`from_dict()` 缺省值同步改动），大纲创建/建议采纳等所有实例化
  路径统一走 dataclass 默认值，一处改动全覆盖，不需要逐个调用点修改。
- 新增 `CapabilityTrackStore.migrate_stable_volatility_to_periodic()`
  批量迁移存量 `volatility=="stable"` 的子主题为 `"periodic"`，幂等
  （返回 `{"tracks_affected": 0, "topics_migrated": 0}` 表示无需迁移）。
- 新增 CLI 子命令 `/capability migrate-volatility`，由用户显式触发；
  不做成 daemon 启动时自动迁移。
