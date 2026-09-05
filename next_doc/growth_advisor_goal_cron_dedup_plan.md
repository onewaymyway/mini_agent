# 成长顾问候选生成 × Goal/Cron 去重改进方案

> 前置阅读：`next_doc/growth_advisor_goal_cron_integration_plan.md`（阶段
> A/B/C 的既有对齐/落地/回访机制，本方案是对阶段 A 遗留问题的补丁）、
> `docs/growth-advisor-guide.md`、
> `src/mini_agent/evolution/growth_advisor.py::growth_candidate_derive()`、
> `src/mini_agent/evolution/growth_advisor.py::extract_spinoff_topics_from_pursuits()`、
> `src/mini_agent/evolution/goal_cron_bridge.py`。

## 0. 问题

成长顾问现在会对"已经是 Goal 的方向"和"已经挂了 cron 在自动推进的
方向"重复生成候选，用户体验上是噪音：一个方向既然已经立成 Goal 在
处理，就不需要成长顾问再"发现"一次、提醒一次。

排查后定位到两个具体成因：

1. **Goal 标题去重目前完全挂在 LLM opt-in 开关下，默认不生效。**
   `growth_candidate_derive()` 里，`active_goal_titles`（第 2126-2133
   行）只有在 `cfg.duplicate_direction_llm_check_enabled=True` 且调用方
   传入了 `llm_helper` 时才会被收集，并且只有这时才会传给
   `GrowthBacklog.add_or_merge()` 参与去重（`existing_goal_titles`
   参数只在 `llm_helper is not None` 分支里被读取，见 `add_or_merge()`
   第 804-856 行）。`duplicate_direction_llm_check_enabled` 默认
   `False`，所以**默认配置下 Goal 标题完全不参与候选去重**——
   `add_or_merge()` 唯一生效的去重是候选之间的 `dedupe_key` 精确匹配
   （第 785-802 行），跟 GoalBacklog 里已经存在的 Goal 毫无关系。
   只要 memory 里某个已经立项的方向证据数还在增长（用户在 Goal 里
   推进时几乎必然会留下相关 memory），成长顾问就会不断为同一个方向
   生成候选。

2. **spinoff 挖出来的话题没有经过 Goal 标题过滤。**
   `extract_spinoff_topics_from_pursuits()` 只按"是否已被同一 Goal
   的 `covered_subtopics` 吸收过"过滤（`ever_covered` 集合），不检查
   挖出来的 `open_questions` 文本是不是恰好和某个 Goal 标题（或另一个
   Goal 标题）撞车。虽然设计初衷是挖"衍生子话题"，但撞车时同样会
   生成一条名义上"新"、实际上跟已有 Goal 高度重合的候选。

3. **cron 没有独立的抑制信号，但间接放大了问题 1**：`recurring=True`
   的 Goal 每轮 cron 触发都会产出 progress note / manifest，天然比
   非 recurring 的 Goal 留下更多 memory 证据，在信号扫描里更容易
   持续达标、持续触发（因为问题 1，本该被过滤掉却没被过滤）候选生成。
   本方案不需要单独读 cron 执行日志（`goal_cron_bridge` 现状那份计划
   明确把这个列为非目标、留给后续），只要把问题 1 修好，`recurring`
   Goal 作为 `active_goals()` 的一员自然一并被挡住。

## 1. 目标（本轮范围）

- 把"命中 Goal 标题 → 不生成候选"从 LLM opt-in 增强步骤降级为**默认
  开启的零成本规则式过滤**，独立于 `duplicate_direction_llm_check_enabled`。
- signal_scan 和 pursuit_spinoff 两条候选来源都过这层过滤，口径一致。
- 保留现有 LLM 语义判重（`duplicate_direction_llm_check_enabled`）作为
  叠加增强层，处理"标题字面不同、语义相同"的情况——规则式精确匹配和
  LLM 语义匹配不是替代关系，是两层递进的过滤。
- 诊断面板/CLI 增加"本轮因命中 Goal 被抑制的话题数"计数，让用户能
  验证过滤确实在生效，而不是"看起来没生成候选，但不知道是没信号
  还是被过滤了"。

非目标：
- 不新增 cron 执行日志读取（读 cron 执行历史来反向生成候选，跟本方案
  方向相反，且 `growth_advisor_goal_cron_integration_plan.md` 已明确
  列为后续单独排期）。
- 不改动阶段 A `goal_growth_alignment()` 本身的行为——它是"只读展示
  对齐情况"的诊断入口，跟"候选生成时要不要抑制"是两个不同的调用点，
  但两者的匹配逻辑会抽成同一个共享 helper，避免逻辑分叉。
- 不改动 `duplicate_direction_llm_check_enabled` 现有语义。

## 2. 共享 helper：`_active_goal_topic_keys()`

新增模块级函数：

```python
def _active_goal_topic_keys(goal_backlog) -> dict[str, "GoalNode"]:
    """遍历 goal_backlog 里所有 level=="goal" 且 status=="active" 的
    节点，返回 {normalize_title_key(goal.title): goal} 的映射。

    - `goal_backlog` 为 None，或 `all_nodes()` 调用异常 → 返回空 dict，
      调用方据此完全跳过 Goal 去重（等价于当前"拿不到 goal_backlog
      就不去重"的行为，向后兼容）。
    - 只看 `status == "active"` 的 Goal——已经 `completed`/`abandoned`/
      `cancelled`/`failed` 的 Goal 不再是"正在处理"，不应该继续压制
      对应话题的候选生成（比如一个方向做完了，用户可能想重新审视
      "接下来还要不要继续深入"，这时候成长顾问重新发现这个话题反而
      是合理的）。`paused` 视为仍然"正在处理但暂停"，同样纳入抑制
      （区别于 completed/abandoned 等终态）。
    - 同一 `normalize_title_key` 命中多个 Goal 时保留先遍历到的一个，
      跟现有 `goal_by_key.setdefault(...)` 的"先到先得"惯例一致——
      这里只是拿来做"存在与否"的布尔判断，取哪个 Goal 不影响过滤
      结果本身。
    """
```

`goal_growth_alignment()` 里现有的等价内联逻辑（第 3412-3421 行,
`goal_keys_by_id` / `goal_by_key` 的构造）改为直接调用这个共享 helper，
行为完全不变（原逻辑本来就是遍历全部 `level=="goal"` 节点不筛
`status`——这里顺带对齐一下，阶段 A 的"未匹配兴趣"展示本身不受
`status` 筛选影响太大，因为 `goal_by_key` 只在"能不能找到对应 Goal"
时使用；改成只看 active 反而更准确地反映"这个方向现在是否仍在被
Goal 追踪"，是本方案顺带修的一个小的一致性问题，不是本方案的核心
改动，出现测试差异时以两处调用点行为对齐为准）。

## 3. `growth_candidate_derive()` 改动

在函数开头、`focus_areas` 合并 spinoff 之前，新增：

```python
goal_topic_keys: dict[str, Any] = {}
if goal_backlog is not None:
    try:
        goal_topic_keys = _active_goal_topic_keys(goal_backlog)
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.growth_advisor.growth_candidate_derive_goal_keys")
        goal_topic_keys = {}
```

主循环（`for topic, refs in sorted(focus_areas.items(), ...)`）里，在
`excluded_topics` 判断之后、生成 `rationale` 之前，新增：

```python
key = normalize_title_key(topic)
if key in goal_topic_keys:
    suppressed_goal_topics.append(topic)   # 用于诊断计数，见第 5 节
    continue
```

（`key` 的计算位置从原来"生成 rationale 之后"提前到这里，供两处
共用，不重复计算。）

这一步在 spinoff 话题合并进 `focus_areas` **之后**才执行，所以
signal_scan 和 pursuit_spinoff 两条来源天然共享同一层过滤，不需要
分别处理——这也顺带解决了第 0 节问题 2（spinoff 话题撞上 Goal 标题
的情况）。

`existing_goal_titles=active_goal_titles` 这一路 LLM 语义判重逻辑
保持不动：规则式精确匹配先过滤掉字面相同的，剩下过不了字面匹配、
但语义上可能相同的话题，才轮到 `duplicate_direction_llm_check_enabled`
开启时的 LLM 判重兜底。两层叠加，互不冲突。

## 4. 配置开关

新增 `GrowthAdvisorConfig.goal_topic_dedup_enabled: bool = True`。

- 默认开启——这是"默认零成本、默认生效"的规则式去重，跟项目一贯
  "默认可用、可选增强关掉不影响基础功能"的原则一致（对比
  `goal_alignment_enabled` 同样默认 `True` 的先例）。
  关掉这个开关时，`growth_candidate_derive()` 完全跳过第 3 节新增的
  过滤逻辑，退化到本方案改动前的行为（连同 LLM 语义判重那条已有的
  独立开关，两者互不影响，用户可以只关掉规则式过滤但保留 LLM 语义
  判重，或者反过来）。
- 提供开关而不是直接改成"无条件生效、不可关闭"，是因为不排除有用户
  故意想让成长顾问对"已经是 Goal 的方向"也继续观察证据数走势（比如
  想知道"这个 Goal 关联的话题在 memory 里的热度有没有持续上升"），
  关掉开关、只依赖阶段 C 回访机制里的 `_goal_progress_signal()` 去
  判断"要不要打扰用户"也是一种合理配置。

## 5. 诊断可观测性

`growth_candidate_derive()` 返回值不变（仍是 `list[GrowthCandidate]`，
不引入 breaking change），但内部收集的 `suppressed_goal_topics` 列表
通过新增的轻量落盘函数记一条快照：

```python
def _record_goal_dedup_suppression(paths, suppressed_topics: list[str]) -> None:
    """追加一行到 growth_goal_dedup_suppressions.jsonl：
    {"recorded_at": ts, "count": len(suppressed_topics), "topics": [...]}。
    只在 suppressed_topics 非空时才写，避免每轮空跑都追加一行空记录。
    复用 `_compact_topic_trend_storage()` 同款的按天分桶降采样策略，
    在 `growth_candidate_derive()` 末尾跟现有的
    `compact_topic_trend_storage(paths)` 调用放在一起顺带压缩。
    """
```

`diagnostics_snapshot()` 新增字段
`goal_dedup.last_cycle_suppressed_count`（读最近一条快照的 `count`，
拿不到文件/为空 → `None`，不影响诊断面板其余部分——跟
`goal_alignment.*` 两个既有计数字段同款的"缺省不报错"惯例）。

`/growth scan` 命令的输出末尾，如果本轮 `suppressed_goal_topics`
非空，追加一行提示：

```
本轮有 N 个话题因为已经是你正在处理的目标（{标题, ...}），未生成新候选。
```

（列出的标题数量做个上限，比如最多列 5 个，超出用"等 N 个"收尾，
避免话题多的时候这一行刷屏。）

## 6. 数据结构变更小结

- `GrowthAdvisorConfig` 新增字段：`goal_topic_dedup_enabled: bool = True`。
- 新增模块级函数：`_active_goal_topic_keys(goal_backlog)`、
  `_record_goal_dedup_suppression(paths, suppressed_topics)`。
- `goal_growth_alignment()` 内部改为复用 `_active_goal_topic_keys()`，
  对外返回结构不变。
- 新增只追加文件 `growth_goal_dedup_suppressions.jsonl`（结构见第 5
  节），走既有降采样压缩机制，不会无限增长。

## 7. 实施顺序

1. 抽取 `_active_goal_topic_keys()`，`goal_growth_alignment()` 切换过去
   调用，跑通现有测试（`test_growth_advisor_goal_cron_integration.py`）
   确认行为等价。
2. `growth_candidate_derive()` 接入过滤逻辑 + `goal_topic_dedup_enabled`
   开关，补测试：
   - 有一个 active Goal 标题和某个 focus_area 话题归一化后相同 → 不
     生成候选。
   - 同上但 Goal 状态是 `completed`/`abandoned` → 正常生成候选。
   - `goal_topic_dedup_enabled=False` → 即使标题相同也正常生成（退回
     旧行为）。
   - spinoff 挖出的话题命中 Goal 标题 → 同样被过滤，不进
     `add_or_merge`。
   - 不传 `goal_backlog`（旧调用点）→ 完全不受影响。
3. 诊断字段 + `/growth scan` 提示行 + `growth_goal_dedup_suppressions.jsonl`
   落盘/压缩，补测试。
4. 更新 `docs/growth-advisor-guide.md`，在既有"候选生成"小节里补充
   "已是 Goal 的方向默认不再重复生成候选"的说明，并说明
   `goal_topic_dedup_enabled` 开关位置。

每一步独立可跑测试、独立提交，第 1 步是纯重构（不改变外部行为），
第 2 步是本方案的核心修复，第 3、4 步是可观测性和文档，不阻塞前两步
上线。

## 8. 与现有设计哲学的一致性检查

- **默认可用、可选关闭**：规则式 Goal 去重默认开启且零 LLM 成本，
  跟 `goal_alignment_enabled` 同款默认值语义一致；LLM 语义判重仍然
  是独立的、默认关闭的增强层，两者可以分别开关，不互相耦合。
- **克制**：本方案只做"少生成不必要的候选"，不新增任何自动写
  Goal/cron 状态的行为，不改变阶段 A/B/C 已有的读写边界。
- **向后兼容**：不传 `goal_backlog`（现有测试/调用点里大量存在）时
  行为完全不变；`GrowthCandidate`/`GrowthAdvisorConfig` 只新增带默认值
  的字段，旧数据反序列化不受影响。
