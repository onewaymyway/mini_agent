# 空大纲 Track 自动起草大纲 —— 改进计划

状态：已实施完成。`run_capability_learning_cycle()` 新增超时兜底逻辑
（`empty_outline_auto_draft_enabled`/`empty_outline_auto_draft_after_hours`，
默认关闭）、`CapabilityLearningConfig` 新增对应两个字段、
`cli/commands/capability_cmd.py::/capability cycle` 已透传。新增测试
`tests/test_capability_empty_outline_auto_draft.py`（7 用例全过）。

## 背景与根因

`accept_candidate()`（`evolution/persona_candidates.py:574-594`）采纳一条
人设/能力候选时，调用 `CapabilityTrackStore.create()` 不传
`outline_names` 也不传 `llm_helper`，落地成一个**空大纲**的 Track——这是
`persona_capability_learning_design.md` §8 待确认问题 3 里已经确认过的
设计倾向："大纲之后在 Track 详情页补充"，即默认交给用户手动补。

但空大纲的 Track 一旦进入 `run_capability_learning_cycle()`，
`scan_outline_gaps()`（`evolution/capability_learning.py:1448-1497`）对着
空的 `track.outline` 遍历直接返回 `[]`，本轮该 Track 检索/问题/建议全是
0，cron 会一直"成功"但空转。更麻烦的是，`outline_revision_and_suggestion
_improvement_plan.md` 里补上的三个自动大纲建议来源都无法兜底这种情况：

- `generate_outline_suggestion_from_miss_counts()` 依赖检索未命中台账
  （`miss_observed`），而这类台账只有子主题被检索过才会产生——大纲是空的
  → 从来没有子主题被检索 → 台账永远是空的 → 这条规则式建议永远不会触发。
- `generate_outline_suggestion_from_research()` 依赖"子主题检索沉淀"，
  同理无从谈起。
- `generate_outline_suggestion_from_milestone()` 依赖覆盖率
  （covered / total），大纲为空时 `total=0`，覆盖率无意义。

结果是：如果用户采纳候选后没有立刻手动去 Track 详情页补大纲，这个 Track
会永久停留在"已创建但从未真正开始学习"的状态，没有任何机制会提醒或
兜底——这正是本次会话里"金融数据智能采集"这条 Track 卡住的原因，和
之前诊断过的"有大纲但覆盖为 0 导致空转"是同一类问题的更早阶段版本。

## 方案：cron 巡检 + 超时兜底自动起草

不改变 `accept_candidate()` 的默认行为（继续创建空大纲 Track，保留"用户
手动补"的路径和语义），只在 `run_capability_learning_cycle()` 里加一层
兜底：**大纲长期为空且用户始终没有手动处理时，自动调用已有的
`draft_outline_with_llm()` 起草一份初始大纲**，避免 Track 无限期卡在
"进不了候选池"的死循环里。

### 触发条件

在 `for track in active_tracks:` 循环最前面（消费已回答问题之前）插入
判断，同时满足才触发：

1. `not track.outline`（大纲仍为空）
2. `now - track.created_at >= empty_outline_auto_draft_after_hours * 3600`
   （给用户手动建大纲留出窗口期，默认不到 24 小时不会自动介入，避免和
   "用户正准备手动编辑"抢跑）
3. `llm_helper is not None`（沿用现有"没有 llm_helper 就跳过所有 LLM
   辅助步骤"的一贯克制——和 `draft_outline_with_llm()`/
   `generate_outline_suggestion_from_answer()` 等函数完全一致的降级方式）
4. `empty_outline_auto_draft_enabled` 为 `True`（新配置开关）

### 触发后的行为

调用已有的 `draft_outline_with_llm(track.title, track.persona_desc,
llm_helper)`（复用 `create()` 里已经在用的同一函数，不新写起草逻辑），
拿到子主题名字列表后：

- 为空列表（LLM 不可用/解析失败，`draft_outline_with_llm()` 本身的降级
  行为不变）→ 本轮不重试，记一条 `action="outline_auto_draft_skipped"`
  台账，留到下一轮再判断一次（不改 `created_at`，所以下一轮条件 2 仍然
  成立，会继续尝试，直到成功或用户手动介入）。
- 非空列表 → 组装为 `OutlineTopic` 列表（`coverage_state="uncovered"`），
  通过 `track_store.update(track.track_id, outline=...)` 落盘；记一条
  `action="outline_auto_drafted"` 台账（`summary` 里带上生成的子主题
  数量），`summary["outline_auto_drafted"] += 1`（新增统计字段）；用
  更新后的 Track 对象替换循环变量 `track`，让本轮后续的
  `scan_outline_gaps()` 等步骤能立刻用上新大纲，不用等到下一轮。

不重新发明"起草"逻辑本身——`draft_outline_with_llm()` 的 prompt/解析/
降级行为完全不动，本方案只是多了一个"谁在什么条件下调用它"的路径。

### 与看板的关系

看板"能力学习"Tab 的 Track 卡片本身已经会显示"大纲子主题：0 个"
（空大纲的现状展示不变）。本方案不额外新增看板 UI——用户仍可以随时手动
点「🤖 生成/刷新大纲建议」提前处理（那条路径走的是
`revise_outline_with_llm()`，和这里的 `draft_outline_with_llm()` 是两个
独立函数，互不冲突：手动路径先触发的话，`track.outline` 就不再为空，
下一轮 cron 判断条件 1 就不成立，自动兜底不会重复起草）。

唯一新增的展示点：cron 巡检摘要（`run_capability_learning_cycle()` 返回
的 `summary` dict）新增 `outline_auto_drafted` 计数字段，看板"⏰ Cron
任务"Tab 现有的"最近一次执行摘要"展示会自动带出这个新字段（复用现有的
摘要 dict 渲染逻辑，不用改渲染代码）。

## 配置项（`config/models.py::CapabilityLearningConfig` 新增字段）

```python
# 大纲长期为空（超过 empty_outline_auto_draft_after_hours 小时，默认
# 给用户留出手动补充的窗口期）时，自动调用 draft_outline_with_llm()
# 兜底起草一份初始大纲，避免 Track 永久卡在"进不了候选池"的空转状态。
# 和项目一贯"新机制先保守默认"的取向一致，默认关闭；且即使开启，也仍然
# 需要调用方传入 llm_helper 才会真正生效（没有 llm_helper 时这一步整体
# 跳过，与其它 LLM 辅助步骤的降级方式一致）。
empty_outline_auto_draft_enabled: bool = False
empty_outline_auto_draft_after_hours: float = 24.0
```

`run_capability_learning_cycle()` 新增对应两个关键字参数（默认值同上，
向后兼容——不传时行为与此前完全一致，即"永不自动起草"）；
`cli/commands/capability_cmd.py` 的 `/capability cycle` 从
`cfg.capability_learning` 读取并透传，与现有三个 `outline_suggestion_*`
开关的接线方式保持一致。

## 不做的事

- 不改 `accept_candidate()` / `CapabilityTrackStore.create()` 本身——空
  大纲仍然是默认创建结果，手动补充路径不受影响。
- 不改 `draft_outline_with_llm()` 的 prompt/解析/降级逻辑。
- 不新增看板 UI（复用现有的 Track 卡片展示 + cron 摘要展示）。
- 不处理"大纲非空但覆盖率长期为 0"的情况——那属于
  `outline_revision_and_suggestion_improvement_plan.md` 已经覆盖的范畴
  （miss_counts/research/milestone 三个建议来源），本方案只补"大纲从
  一开始就是空的"这一段更早的死角。

## 验收方式

- 单测覆盖：
  - 大纲为空但未超过 `empty_outline_auto_draft_after_hours` → 不触发。
  - 超过阈值但 `empty_outline_auto_draft_enabled=False` → 不触发。
  - 超过阈值且开启但 `llm_helper=None` → 不触发。
  - 超过阈值、开启、有 `llm_helper`，`draft_outline_with_llm()` 返回
    非空列表 → 大纲落盘、`outline_auto_drafted` 计数、本轮
    `scan_outline_gaps()` 能立刻用上新子主题。
  - `draft_outline_with_llm()` 返回空列表（LLM 不可用）→ 记
    `outline_auto_draft_skipped` 台账，不抛异常，下一轮还会再判断一次。
  - 大纲非空的 Track 完全不受影响（回归）。
- 手动验证：把某个空大纲 Track 的 `created_at` 改到 24 小时前，开启新
  配置项后跑一轮 `/capability cycle`，看板上该 Track 应该出现非空大纲，
  且不再是"人设覆盖 0/?"。
