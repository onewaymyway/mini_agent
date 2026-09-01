# 大纲修订（LLM 生成/手动编辑）与自动建议机制优化 —— 改进计划

背景：`persona_capability_learning_design.md` 描述的能力学习看板里，Track
创建时如果既没有传 `outline_names` 也没勾选 `llm_draft`，会落地成一个**空
大纲**，导致 `run_capability_learning_cycle()` 每轮 `scan_outline_gaps()`
天然空转（`处理 Track：N 个` 但检索/问题/建议全是 0），cron 会一直"成功"
但用户看不到任何进展。根因分析见本次会话记录，不在本文档重复。

本文档是用户已确认的施工方案，实施前先落盘、实施后同步更新，保持
"改动前先有文档、改动后文档不过期"的一贯要求。

## 用户已确认的三个决策

1. 「重新生成大纲」走**用户勾选确认的 diff 流程**（预览 KEEP/ADD/RENAME/
   REMOVE 建议，用户勾选后才应用），不是一键整体替换。
2. 手动编辑大纲只做**增 / 改名 / 删**，不做拖拽排序。
3. 自动大纲建议的三个新来源里，**miss_counts 驱动**（规则式、零成本）
   **默认开启**；另外两个（检索沉淀驱动、覆盖率里程碑驱动，都要调 LLM）
   **默认关闭**，看板加开关手动开。

## 一、大纲修订：从"整体替换"改成"基于旧大纲的 diff"

### 数据结构

新增（`evolution/capability_learning.py`）：

- `revise_outline_with_llm(track, llm_helper) -> list[dict]`：把当前完整
  大纲（每个子主题名字 + coverage_state + 关联 wiki 页数）连同 Track
  标题/描述一起交给 LLM，要求只输出变更（每行一个操作，`KEEP name` /
  `ADD name` / `RENAME old -> new` / `REMOVE name`），解析后只返回
  `ADD`/`RENAME`/`REMOVE` 三种 op（`KEEP` 不产出、无需用户处理）。
  每个 op 是一个 dict：
  `{"op": "add"|"rename"|"remove", "topic_id": str|None, "name": str, "old_name": str|None}`
  （`add` 没有 `topic_id`；`rename`/`remove` 必须匹配到现有子主题才会
  出现在结果里，匹配不到的 `RENAME`/`REMOVE` 行会被丢弃，不报错）。
  LLM 不可用/解析失败/输出为空时返回 `[]`（不是异常），与
  `draft_outline_with_llm()` 同款"起草辅助，不是关键路径"的克制。
  `ADD` 建议如果与现有子主题/其它建议高度相似（复用
  `_topic_name_similarity()` 同一阈值）会被丢弃，避免建议里出现重复项。

- `apply_outline_revision(paths, track_id, ops) -> Optional[CapabilityTrack]`：
  在**当前**大纲基础上按顺序应用一组 op（`add` 追加新 `OutlineTopic`
  `coverage_state="uncovered"`；`rename` 只改 `name`，`topic_id`/
  `coverage_state`/`wiki_page_ids`/`last_touched_at` 全部不变；`remove`
  从大纲摘除，但不删除已经沉淀的 wiki 页面本身，对齐现有"删除 Track
  不级联删 wiki"的原则）。这一个函数是"重新生成大纲（LLM diff）"和
  "手动编辑大纲"两条路径的共同落地点，保证两条路径行为一致。
  `CapabilityTrackStore` 新增三个薄封装方法
  `add_outline_topic`/`rename_outline_topic`/`remove_outline_topic`，
  内部都调用 `apply_outline_revision`，供手动编辑单个操作时直接用。

### API

- `POST /v1/capability/tracks/{track_id}/outline/revise`：调用
  `revise_outline_with_llm`，返回 `{"ops": [...]}`（**不落盘**，纯预览）。
- `POST /v1/capability/tracks/{track_id}/outline/apply_revision`：请求体
  `{"ops": [...]}`（前端把用户勾选保留的 op 原样传回），调用
  `apply_outline_revision` 落盘，返回更新后的 Track。
- `POST /v1/capability/tracks/{track_id}/outline/topics`：请求体
  `{"name": str}`，手动新增子主题。
- `PATCH /v1/capability/tracks/{track_id}/outline/topics/{topic_id}`：
  请求体 `{"name": str}`，手动改名。
- `DELETE /v1/capability/tracks/{track_id}/outline/topics/{topic_id}`：
  手动删除。

### 看板（Streamlit，`apps/mini_agent_kanban/app.py::render_capability_tab`）

- Track 展开卡片"能力大纲覆盖状态"区块上方新增：
  - **「🤖 生成/刷新大纲建议」**按钮：调用 revise 端点，把返回的 ops
    以带复选框的清单展示（"建议新增 N 个 / 建议改名 N 个 / 建议移除 N 个"
    分组展示，默认全部勾选新增、不勾选改名/移除——新增是纯增量、风险
    最低，改名/移除会影响既有数据，默认更保守），用户可逐条取消勾选，
    点「应用」调用 apply_revision 端点。
  - **手动编辑小表单**：新增子主题输入框 + 提交；每个已有子主题旁加
    ✏️（内联改名输入框）和 🗑️（二次确认删除）。
  - 空大纲场景（本次问题的起因）复用同一套"生成/刷新大纲建议"入口，
    等价于全部是 `ADD`。

## 二、自动大纲建议机制：现状局限与三个新来源

现状唯一触发点是 `generate_outline_suggestion_from_answer()`——cron 消费
一条已回答问题时才可能生成建议，链路是
`大纲非空 → 生成异步问题 → 用户回答 → 消费时判断`，空大纲的 Track
永远走不到这一步；且只从"用户主动补充的信息"里挖，不看检索本身的内容，
也没用上已有的 `miss_counts` 信号，也没有"大纲快学完了要不要往深了扩"
的节奏型建议。

新增三个独立信号源，全部复用同一条 `OutlineSuggestion` 队列和现有
看板"💡 大纲扩展建议"区 UI，不引入新的交互范式：

1. **miss_counts 驱动**（规则式，零 LLM 成本，**默认开启**）：
   `record_wiki_miss()` 记录的 `miss_observed` 台账里，`summary` 字段是
   `"检索未命中：{query}"`；`generate_outline_suggestion_from_miss_counts()`
   统计最近 200 条台账里各 query 文本的出现次数，达到阈值
   （`outline_suggestion_miss_count_threshold`，默认 3）且与现有大纲/
   pending 建议都不相似时，直接生成一条建议（不调用 LLM）。每轮循环
   每个 Track 最多生成 1 条（避免同一轮里同一堆未命中查询生成多条
   高度相似的建议）。
2. **检索沉淀驱动**（要调 LLM，**默认关闭**）：`topics_researched`
   （completeness=sufficient）时，把本次检索结果摘要 + 现有大纲交给
   LLM，判断"这次检索到的内容里有没有明显该独立开的子主题"。为避免
   每轮都调 LLM，加一个"每个 Track 每天最多触发一次"的节流
   （`CapabilityTrack.outline_research_suggestion_last_at` 时间戳字段）。
3. **覆盖率里程碑驱动**（要调 LLM，**默认关闭**）：大纲覆盖率
   （covered / total）首次达到阈值（`outline_suggestion_milestone_
   threshold`，默认 0.8）时，触发一次"要不要往深/往新方向扩展"的
   建议；`CapabilityTrack.outline_milestone_notified` 布尔标记去重，
   每个 Track 只在跨越阈值那一刻触发一次，不会每轮重复问。

三个来源各自独立可开关（`CapabilityLearningConfig` 新增字段），互不
影响；`OutlineSuggestion` 新增可选字段 `source`（`"answer"`/
`"miss_counts"`/`"research"`/`"milestone"`，默认 `"answer"` 保持向后
兼容），供看板展示建议来源。

## 三、配置项（`config/models.py::CapabilityLearningConfig` 新增字段）

```
outline_suggestion_miss_count_enabled: bool = True
outline_suggestion_miss_count_threshold: int = 3
outline_suggestion_research_enabled: bool = False
outline_suggestion_milestone_enabled: bool = False
outline_suggestion_milestone_threshold: float = 0.8
```

`run_capability_learning_cycle()` 新增对应关键字参数（默认值与上面一致，
向后兼容——不传这些参数时行为等价于"miss_counts 开、其余关闭"，与新的
默认配置一致）；`cli/commands/capability_cmd.py` 的 `/capability cycle`
从 `cfg.capability_learning` 读取并透传。

## 四、不做的事（避免范围蔓延）

- 不做大纲子主题的拖拽排序（用户已确认"够用"）。
- 不做"重新生成大纲"的一键整体替换路径（旧接口调用方如果依赖它，
  只能拿到新的 diff 流程；`CapabilityTrackStore.create()` 起草初始
  大纲的行为不受影响，仍是"空大纲 + 传入 llm_helper 时整段起草"，
  因为那时还没有旧大纲可言，不存在"替换 vs 修订"的语义问题）。
- 不改动 `draft_outline_with_llm()`（创建 Track 时的初始起草）本身。

## 五、验收方式

- 单测覆盖：`revise_outline_with_llm()` 对合法/异常 LLM 输出的解析；
  `apply_outline_revision()` 对 add/rename/remove 的落盘正确性（尤其是
  rename/remove 不影响 coverage_state/wiki_page_ids）；
  `generate_outline_suggestion_from_miss_counts()` 的阈值判断与去重。
- 手动验证：对着本次"金融数据智能采集"这个空大纲 Track，点「生成/刷新
  大纲建议」应该能看到一批 `ADD` 建议，勾选应用后大纲不再为空，下一轮
  `/capability cycle` 应该能看到检索/覆盖率开始非零。
