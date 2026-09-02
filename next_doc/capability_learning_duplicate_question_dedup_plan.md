# 能力学习重复提问修复方案

> 状态：已实施完成。新增/修改文件清单见文末"实施记录"一节。
> 前置背景：`evolution/capability_learning.py` 的 `run_capability_learning_
> cycle()` 会对 `target_type == "persona"` 的 Track 按大纲子主题
> （`OutlineTopic`）逐个判断 `needs_user_context()`，命中就通过
> `CapabilityQuestionStore.raise_question()` 生成一条待用户回答的问题。

## 1. 现象

同一个问题（甚至文案完全一致）被反复问：

```
[answered] 关于「数据采集技术基础」……　→ A股股票以及ETF数据……
[answered] 关于「A股数据源类型」……　　　→ A股股票以及ETF数据……
[answered] 关于「数据采集技术基础」……　→ A股股票以及ETF数据……
[answered] 关于「A股数据源类型」……　　　→ A股股票以及ETF数据……
[answered] 关于「数据采集技术基础」……　→ A股股票以及ETF数据……
```

用户已经给过完全一样的答案，系统还是继续问。

## 2. 根因（两个独立问题叠加）

### 2.1 根因一：已回答的问题从未让对应子主题"结项"

`raise_question()` 只是在问题台账（`capability_questions.jsonl`）里插入
一条记录，`OutlineTopic.coverage_state` 完全不受影响。用户回答后，
`run_capability_learning_cycle()` 里"消费已回答问题"那一段只做了两件事：

1. 记一条 `action="question_answered"` 的台账（`CapabilityLedgerEntry`）；
2. 可选：调 `llm_helper` 生成大纲外新关注点建议；

**从未把 `topic.coverage_state` 置为 `"covered"`。** 而 `scan_outline_
gaps()` 挑选本轮候选子主题时，唯一会跳过的条件是
`coverage_state == "covered" and not stale`——一个 `persona` 型 Track 的
子主题只要没被显式标记为 covered，就会一直留在 `uncovered` 状态，每一轮
只要还有预算、还没到 `max_pending_questions`，就会被 `scan_outline_
gaps()` 重新选中，`needs_user_context()` 对 `persona` Track 又是硬编码
`True`，于是 `raise_question()` 用**同一个模板**（`f"关于「{topic.name}」
，能告诉我更多你的具体偏好/背景吗？这会影响后续推进的方向。"`）**再问一
遍一字不差的问题**——这就是「数据采集技术基础」反复出现 3 次的直接原因。

### 2.2 根因二：大纲里存在语义重复的子主题，各自独立提问

「数据采集技术基础」和「A股数据源类型」是大纲里两个不同的 `topic_id`，
名字不同，但本质在问同一件事——用户也确实给了完全相同的答案。当前代码
唯一的"子主题去重"机制是 `find_cross_track_reuse()`（字符 2-gram
Jaccard 相似度），但它只用于**跨 Track 的检索结果复用**（`knowledge`
型子主题的检索/wiki 环节），根本没有接入 `persona` 型 Track 的"要不要
问用户"这条路径——两个语义重复的子主题各自独立触发 `needs_user_
context()` → 各自独立 `raise_question()`，对用户来说就是被换了个说法
反复问同一件事。

## 3. 修复方案

### 3.1 修根因一：回答被消费后，子主题标记为 covered

`run_capability_learning_cycle()` 消费一条 `answered` 问题时，同步把
`track.outline` 里 `topic_id` 匹配的那个 `OutlineTopic.coverage_state`
置为 `"covered"`、`last_touched_at` 刷新为当前时间，再通过
`track_store.update(track.track_id, outline=track.outline)` 落盘。这样
`scan_outline_gaps()` 下一轮自然会跳过它（除非 `volatility` 标注为
`periodic`/`volatile` 且过了对应时效阈值，那是预期内的"过一段时间重新
确认一下"，不是本次要修的 bug）。

这一步是规则式的、确定性的，不需要 LLM，优先级最高、必须做——即使不做
根因二的语义去重，光这一条就能让"同一个子主题不会被反复问"成立。

### 3.2 修根因二：提问前用 LLM 检查历史已回答问题里有没有语义重复

按你的思路，在真正 `raise_question()` 之前插入一步：把这个 Track 下所有
`status == "answered"` 且有 `answer` 内容的历史问题列出来，连同即将要问
的新问题一起丢给 LLM，让它判断"新问题是不是在问历史里已经回答过的同一件
事"。命中就直接复用历史答案，不再打扰用户——不创建新的 `pending`
问题，直接：

1. 把这个子主题标记为 `covered`（同 3.1 的落盘方式）；
2. 记一条新的台账 `action="question_reused"`，内容写清楚复用了哪条历史
   问题/答案，方便事后审查"这次为什么没问、是不是判断错了"；
3. 汇总里新增 `questions_reused` 计数，供 cron 日志/看板观察这个机制到
   底生效了多少次。

用 LLM 而不是字符串相似度（`_topic_name_similarity()`），是因为"两个
问题是不是在问同一件事"本质上是语义理解，而不是字面相似——"数据采集技术
基础"和"A股数据源类型"字面上重叠度并不高，字符 2-gram 相似度大概率过不
了 `CROSS_TRACK_REUSE_SIMILARITY_THRESHOLD` 这个阈值，但语义上确实是一
回事。

`llm_helper` 为 `None`（未接线）或该 Track 下暂无任何已回答问题时，直接
跳过判断（早退，不产生无意义的 LLM 调用），退化为"照常提问"——不会因为
这一步的缺失而阻塞主流程，是纯增强、不是必经关卡，风格上和这个文件里其
它 LLM 辅助函数（`generate_outline_suggestion_from_answer()` 等）一致：
LLM 判断失败/解析不出结果时都保守地按"不是重复"处理，宁可多问一次，也
不要因为误判漏问该问的问题。

Prompt 设计上明确要求"只在有把握时才判定为同一件事"，并且让 LLM 只输出
一个数字（命中的历史问题序号）或 `NONE`，不是结构化 JSON——延续本文件里
`generate_outline_suggestion_from_answer()` 的单行文本输出 + 宽松解析
风格，不引入新的解析约定。

## 4. 影响范围与兼容性

- `knowledge` 型 Track 不受影响（`needs_user_context()` 对它们本来就
  返回 `False`，走的是检索路径，不会触发这两个问题）。
- `llm_helper` 未接线的部署：根因一（结构性 bug）照常修复；根因二的语义
  去重跳过，行为等价于"这个增强能力还没打开"，不会比改动前更差。
- 已经存在的、历史上被反复问过的重复问题不会被回溯清理（`goals.json`/
  `capability_questions.jsonl` 里的历史记录不变），本方案只保证"从现在
  开始不会再发生"。如果需要清理某个 Track 已经淤积的历史重复问题，可以
  手动用 `CapabilityTrackStore.update()` 把对应子主题的 `coverage_state`
  批量置为 `covered`。

## 5. 实施记录

新增文件：
- `next_doc/capability_learning_duplicate_question_dedup_plan.md`（本文档）

修改文件：
- `src/mini_agent/evolution/capability_learning.py`：
  - 新增 `find_reusable_answered_question()`。
  - 新增 `_mark_topic_covered()` 小工具，供两处复用。
  - `run_capability_learning_cycle()`：消费 `answered` 问题时调用
    `_mark_topic_covered()`；`needs_user_context()` 分支在
    `raise_question()` 之前先调 `find_reusable_answered_question()`，
    命中则复用答案、标记 covered、跳过真正提问。
