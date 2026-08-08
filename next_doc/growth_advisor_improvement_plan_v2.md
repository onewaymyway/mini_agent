# 成长顾问（Growth Advisor）改进计划 v2

> **实施状态（2026-08 更新）**：P4-0（`profile.derived` 命名空间冲突修复）、
> P4-1（关键词表持久化 + 看板展示 profile / 关键词信息）、P4-2（关键词
> 自动学习稳定后转正）、P4-3（反馈学习细化：类别级置信度调权 + 采纳后
> 回访）、P4-4（报告质量分级 + 增量刷新）、P4-5（通知策略细化：类别静音
> + 优先级分数）、P4-6（看板概念统一说明 + 证据数走势）已完成并落地，
> 细节见 `next_doc/growth_advisor_implementation_record.md` 的 P4 章节。
> P4-7 仍是方向级规划——不过已确认 P4-1 的"🙈 隐藏内置主题"能力已经
> 覆盖了 P4-7 的诉求，细节见下面 P4-7 小节。

- **版本**: v1（草案）
- **前置文档**: `next_doc/growth_advisor_design.md`（原始方案，P1-P3 已全部完成）、
  `next_doc/growth_advisor_implementation_record.md`（逐阶段实施记录）
- **触发背景**: 两条真实用户反馈——
  1. "运行了一天，成长顾问里的数据都是 0"（已通过诊断面板解决可观测性问题，
     但没有解决"为什么关键词覆盖不到"这个根因）；
  2. "看板应该增加用户的 profile 信息；应该增加成长顾问实际使用的信息，
     比如当前的关键词列表；这些关键词应该保存到用户的 profile 里面"。
- **本文档定位**: 不是另起一个新方案，是在 P1-P3 已完成的基础上规划 **P4 里程碑**，
  优先级最高的三件事（看板展示 profile、看板展示关键词表、关键词表持久化到
  profile）直接对应上面第 2 条反馈；同时把上一轮"聚焦成长顾问思考还有哪些可以
  改进"整理出的其他方向也一并纳入规划、排出优先级，避免又变成零散的单点修复。

---

## 0. 现状回顾（写这份计划前必须先说清楚的几个事实）

1. **关键词表是硬编码常量，不属于任何用户**：`growth_advisor.py::_TOPIC_KEYWORDS`
   是模块级常量，7 个内置主题，所有用户共享同一份，改动它需要改代码、重新
   部署。P3 加的 `llm_signal_augment_enabled` 只是"临时发现、用完即弃"——
   LLM 归纳出的新主题只影响当次扫描结果，不会沉淀，下次扫描还要重新发现一遍。
2. **`profile.derived` 存在一个尚未暴露过的命名空间冲突**：`UserProfileManager
   .generate()`（LLM 定期重新生成用户画像 summary/tech_stack/habits 的入口）
   是**整体覆盖** `profile.derived`（`profile.derived = derived`，見
   `src/mini_agent/profile.py`），而不是合并式更新。`growth_advisor.py` 写入
   的 `derived["growth_focus_areas"]`/`growth_focus_areas_updated_at` 目前
   已经会被这次整体覆盖悄悄清空——这是一个**已经存在、此前没有被记录过的
   潜在 bug**，只是因为 `growth_focus_areas` 只是中间信号缓存（不是候选/
   报告的落盘数据，候选落在 `growth_backlog.jsonl` 里不受影响），后果目前
   仅限于诊断面板里"最近一次扫描命中"这一栏偶尔无声重置，用户不容易察觉。
   如果这次要把**关键词表**也放进 `profile.derived`，这个冲突会被放大
   （关键词是要长期保留的用户资产，被静默清空的影响比"命中计数被清零"严重
   得多），**必须在本计划里先修，不能绕过去**。
3. **看板目前完全不展示 `UserProfile` 本身**：`render_growth_tab()` 只读
   `GET /growth/summary`（候选/报告/复盘/诊断），从未调用任何返回
   `UserProfile.derived.summary/tech_stack/habits/preferences` 的接口——
   用户在"成长顾问"这个最该体现"Agent 懂我"的地方，反而看不到 Agent 对自己
   的画像判断是什么，也没法验证画像准不准（画像不准会直接传导成"扫描扫不到
   点子上"）。

---

## 1. 目标与非目标

**目标**：
- 看板"🌱 成长顾问"tab 里，用户能看到 Agent 当前对自己的画像（`summary`/
  `tech_stack`/`habits`，即 `UserProfile.derived` 里 LLM 生成的部分），
  以及成长顾问实际在用的关键词表（内置 + 用户自定义/系统学习到的，分来源
  展示），把"为什么扫描扫不到"和"Agent 到底了解我什么"两件事一起说清楚。
- 关键词表从硬编码常量升级为**分层结构**：内置基础表继续留在代码里（不需要
  每个用户的 profile 里都存一份重复数据），用户自定义/系统学习到的**增量**
  持久化到 `profile.derived` 的独立命名空间下，重启、重新生成画像都不丢。
- 顺带修复第 0 节第 2 条的命名空间冲突，否则后续任何往 `profile.derived`
  里加数据的功能都会重复踩坑。
- 结合上一轮讨论整理出的其他改进方向（反馈学习、报告质量、通知策略、看板
  概念统一、自定义黑名单），排出实施优先级，作为后续迭代的参照，但**本轮
  只承诺 P4-0/P4-1 两个阶段的具体设计**，P4-2 及以后只到方向/思路级别，
  留待各自实施时再细化（避免这份文档本身变成一次性写太满、后续对不上代码
  实际长成的样子）。

**非目标**：
- 不改变 P1-P3 已经确定的核心策略（默认开启、规则式零成本扫描为主、
  LLM 增强 opt-in）。
- 不做通用的"用户画像编辑器"（如果要支持用户手动编辑 `summary`/
  `tech_stack`，是 `profile.py` 模块本身的功能范畴，不是成长顾问该管的事，
  这里只做"只读展示"）。
- 不在本轮实现关键词表的"自动稳定性判定后转正"这类需要跑一段时间积累
  数据才能验证效果的机制（列在 P4-2，先设计方向，不在本轮编码）。

---

## 2. P4-0（前置修复，**已完成**）：`profile.derived` 命名空间冲突

**问题**：`UserProfileManager.generate()` 用 LLM 输出整体替换 `profile.derived`，
`growth_advisor` 写入的字段（现有的 `growth_focus_areas`/
`growth_focus_areas_updated_at`，以及本计划要新增的关键词字段）会被覆盖清空。

**修复方向**：`generate()` 改成**合并式更新**——只替换/新增 LLM 输出对应的
固定字段集合（`summary`/`tech_stack`/`habits`/`source_entry_count`/
`updated_at`），保留其余不属于这个集合、且已经存在的 key（本质上是给
`profile.derived` 引入一个轻量的"命名空间"约定：LLM 画像生成器只管自己
那几个 key，其他模块写自己的 key，互不覆盖）。

```python
# profile.py::generate() 伪代码示意（实际实现时补全测试）
_PROFILE_GENERATED_KEYS = {"summary", "tech_stack", "habits", "source_entry_count", "updated_at"}

def generate(self, llm_client, entries) -> UserProfile:
    profile = self.load()
    ...
    new_fields = {
        "summary": ...,
        "tech_stack": ...,
        "habits": ...,
        "source_entry_count": len(entries),
        "updated_at": time.time(),
    }
    # 保留其他模块（growth_advisor 等）写入的 key，只覆盖自己负责的字段
    merged = dict(profile.derived or {})
    merged.update(new_fields)
    profile.derived = merged
    self.save()
    return profile
```

**验收**：新增/补充 `tests/test_profile.py`（如果尚不存在则新建）用例——
`generate()` 前手动写入一个 `derived["growth_focus_areas"] = {...}`，调用
`generate()` 后断言该字段仍然存在且值不变，同时 `summary`/`tech_stack` 等
字段确实被更新。

**风险**：如果历史上有其他调用方假设 `profile.derived` 在 `generate()` 后
是"纯 LLM 输出、没有其他脏数据"，这个改动可能打破那个假设——需要搜索一遍
`profile.derived` 的其他读取点（目前已知的有 `growth_advisor.py`，还需要
搜索 `self_model.py`/其他 wiki/consolidation 模块是否也读写这个字段）确认
没有类似假设。

---

## 3. P4-1（**已完成**）：关键词表持久化 + 看板展示 profile / 关键词信息

### 3.1 数据模型

在 `profile.derived` 下新增命名空间 `growth_topic_keywords`，结构：

```json
{
  "growth_topic_keywords": {
    "摄影": {
      "keywords": ["摄影", "构图", "用光"],
      "source": "llm_learned",       // "user_added" | "llm_learned"
      "added_at": 1733600000.0,
      "confirmed_by_user": false      // 用户在看板上点过"确认保留"才为 true
    }
  },
  "growth_topic_keywords_removed": ["项目管理"]  // 用户主动隐藏的内置主题
}
```

设计取舍：
- **内置表继续留在代码里**（`_TOPIC_KEYWORDS`），不整表复制进每个用户的
  profile——避免"以后要改内置词条，还要写一次性脚本迁移所有用户 profile"
  这种维护负担。`profile.derived["growth_topic_keywords"]` 只存**增量**
  （用户自定义 + LLM 学习到的新主题）。
- 有效关键词表 = 运行时合并 `_TOPIC_KEYWORDS`（代码内置） ∪
  `profile.derived["growth_topic_keywords"]`（用户增量），减去
  `growth_topic_keywords_removed` 里标记要隐藏的内置主题——由新增的
  `_effective_topic_keywords(profile)` 函数在 `growth_signal_scan()` 内部
  计算，替换掉现在直接用模块常量 `_TOPIC_KEYWORDS` 的地方。
- `confirmed_by_user`：LLM 学习到的新主题默认写入但不算"已确认"，看板上
  展示为"待确认"状态，用户点一下"保留"才转正（`confirmed_by_user=True`）；
  这是刻意的克制机制，避免 LLM 一次误判就永久污染用户的关键词表——呼应
  方案原文第 -1 节"克制机制要下狠功夫做对"的原则。未确认的新主题**仍然
  参与**下次扫描（不确认不代表不生效，只是需要用户看一眼），但如果连续
  N 次扫描都没有新证据支持，可以被静默移除（具体阈值留到实现时定，不在
  本文档写死）。

### 3.2 后端改动

- `growth_advisor.py`：
  - 新增 `_effective_topic_keywords(profile) -> dict[str, list[str]]`：合并
    内置表 + `profile.derived["growth_topic_keywords"]`，排除
    `growth_topic_keywords_removed`。
  - `growth_signal_scan()` 改用 `_effective_topic_keywords(profile)` 替代
    直接引用模块常量 `_TOPIC_KEYWORDS`。
  - `_llm_augment_topics()` 归纳出的新主题，除了合并进本次 `hits` 返回值，
    还要写入 `profile.derived["growth_topic_keywords"][topic]`
    （`source="llm_learned", confirmed_by_user=False`）——这是"关键词应该
    保存到 profile 里"这条反馈的核心改动点。
  - 新增两个面向用户操作的函数：`add_custom_topic_keyword(profile, topic,
    keywords)`（用户手动添加，`source="user_added"`，直接
    `confirmed_by_user=True`）、`remove_topic_keyword(profile, topic)`
    （从增量表或 `removed` 列表里处理，视 topic 是内置还是自定义分别处理）。
  - `diagnostics_snapshot()` 的 `signal_scan.topics_tracked` 改为调用
    `_effective_topic_keywords()` 而不是直接读模块常量，顺带把每个主题的
    `source`/`confirmed_by_user` 一起带出去，供看板区分展示"内置/系统学到
    待确认/用户自定义"三种状态。
- `api/routes.py`：
  - `GET /growth/summary` 的 `diagnostics.signal_scan.topics_tracked` 从
    简单字符串列表升级为带 source/confirmed 信息的对象列表（需要评估是否
    是 breaking change——如果看板是唯一消费方，直接改；如果要保守，加一个
    新字段 `topics_detail` 而不改现有 `topics_tracked` 的形状）。
  - 新增 `POST /growth/keywords/{topic}/confirm`、
    `POST /growth/keywords/{topic}/remove`、`POST /growth/keywords`
    （新增自定义主题，body 带 `topic`/`keywords`）三个路由，对应
    3.2 里后端的三个函数。
  - 新增 `GET /growth/profile_snapshot`（或者直接并入 `/growth/summary`
    的 `diagnostics` 里加一个 `user_profile` 字段）：返回
    `UserProfile.derived` 里的 `summary`/`tech_stack`/`habits`/
    `updated_at`，**不返回 `preferences`**（`preferences` 是用户显式设置
    的偏好，跟"Agent 对我的画像判断"是两回事，混在一起展示容易让用户
    误解）。

### 3.3 看板改动

在 `_render_growth_diagnostics()` 现有的"配置"/"最近一次信号扫描"/
"记忆数据"/"后台定时任务"四块之上（或者拆成一个新的、默认展开的独立区块，
跟诊断面板分开，因为这个是"用户想看"而不是"用户想排查问题时才看"），新增：

- **"Agent 对你的了解"**：展示 `user_profile.summary`（一段话）+
  `tech_stack`（标签形式）+ `habits`（标签形式）+ 最近更新时间；如果
  `derived` 还是空的（新用户/还没攒够记忆），提示"还在观察中，攒够
  `profile_min_entries` 条记忆后会生成"。
- **"当前关键词列表"**：按来源分三组展示——内置（灰色标签，不可删除，
  可以"隐藏"）、系统学到待确认（黄色标签，带"✅ 保留"/"❌ 不要"两个按钮）、
  用户自定义（蓝色标签，带"❌ 删除"按钮）；再加一个"➕ 添加自定义主题"
  的小表单（主题名 + 逗号分隔的关键词）。

### 3.4 测试

- `tests/test_growth_advisor.py` 新增 `TestPersistedKeywords`：验证
  `_effective_topic_keywords()` 合并/排除逻辑、`_llm_augment_topics()` 归纳
  结果确实写入 `profile.derived`、`add_custom_topic_keyword`/
  `remove_topic_keyword` 的增删改逻辑、`confirmed_by_user` 状态流转。
- 新建 `tests/test_profile.py`（如尚不存在）覆盖 P4-0 的合并式 `generate()`。
- `tests/test_kanban_growth_dragdrop.py` 或新文件里补充关键词展示相关的纯
  函数测试（如果新增了看板侧的标签分组辅助函数）。

---

### 3.5 实施记录摘要（与 3.2/3.3 设计的实际落地差异）

- `topics_tracked` **未做 breaking change**：保持原有的字符串列表形状，
  新增独立的 `topics_detail`（带 `source`/`confirmed_by_user`/`keywords`）
  字段，看板改读新字段，旧字段继续保留，兼容任何其他潜在消费方。
- `GET /growth/profile_snapshot` **未新增独立端点**，采用 3.2 节里提到的
  备选方案：直接在 `diagnostics_snapshot()` 里加一个 `user_profile` 字段，
  随 `GET /growth/summary` 一并返回，减少一次网络往返。
- 新增的三个路由（`POST /growth/keywords`、`.../confirm`、`.../remove`）
  与设计一致；输入清洗（`_clean_keywords`）统一处理了半角/全角逗号、顿号、
  换行分隔与大小写不敏感去重，落实第 6 节\"关键词清洗\"的待确认事项。
- LLM 学到的新主题目前用**主题名自身**作为持久化后的关键词（P3 阶段的
  `_llm_augment_topics` 只返回主题名+命中的 entry_id，不返回关键词列表），
  确保下次纯规则扫描也能命中同一批记忆；如果后续想要更精细的关键词，需要
  同步升级 `_llm_augment_topics` 的 LLM 输出格式，属于后续可选优化，未列入
  本轮范围。

## 4. P4 后续阶段（方向级规划，未细化，按优先级排列）

这些是上一轮讨论识别出的方向，结合本次反馈的实际紧迫度重新排了序。除
P4-0/P4-1 外，其余每个阶段开工前都应该单独过一遍设计（细化数据结构、
API、验收标准），不要直接照这里的一段话开始写代码。

### P4-2：关键词表"自动学习稳定后转正"机制（**已完成**）
如果 LLM 学到的同一个主题连续多次扫描都有新证据支持（比如连续 3 次扫描
都命中 ≥1 条新记忆），且用户没有主动删除过，可以自动把 `confirmed_by_user`
置为 `True`（不需要用户手动点确认）——降低"用户忘记去确认"导致好不容易
学到的主题又被静默清理掉的概率。需要先有 P4-1 的数据结构落地、跑一段时间
积累真实数据后再决定具体阈值。

> **实施记录**：`_update_keyword_learning_streaks()` 已实现，阈值取
> `_AUTO_CONFIRM_STREAK = 3`（未经真实数据验证的经验值，后续如发现
> "转正太快/太慢"可直接调整这个常量）。命中要求"连续"，中断一次即清零，
> 不做"累计命中次数"式的宽松计数。转正后额外打上 `auto_confirmed=True`
> 标记，供看板区分"用户手动保留"和"系统自动保留"。细节见
> `next_doc/growth_advisor_implementation_record.md` P4 章节。

### P4-3：反馈学习细化 + 采纳后回访（**已完成**）
- 把"用户对某一类主题的整体倾向"学出来，而不是只有单主题级别的置信度
  衰减（比如连续忽略多个"管理类"主题，应该影响同类新主题的初始置信度，
  而不是各自独立衰减）。
- 采纳后的候选增加"回访"：类似
  `self_diagnosis_feedback_loop_deepening_plan.md` P2 的建议采纳率回看
  思路，定期（比如 30 天后）问一次"这个方向后续有没有真的推进"，反馈进
  `GrowthFeedbackLedger`，作为置信度调权的额外信号源。

> **实施记录**：类别级调权新增 `_TOPIC_CATEGORIES`（内置主题粗分"技术类/
> 管理类/表达类"，未登记主题归"其他类"）+ `_category_feedback_multiplier`
> （衰减因子 0.95、下限 0.7，比单主题的 0.85/0.4 温和很多），与原有单
> 主题乘子相乘，互不覆盖。采纳后回访新增 `GrowthCandidate.accepted_at`/
> `followup_status` 字段、`pending_followups()`/`record_followup()`、
> 新配置项 `GrowthAdvisorConfig.followup_review_days`（默认 30）、API
> `GET /growth/followups`、`POST /growth/followups/{id}/{progressed|
> stalled}`，看板新增"📮 该回访一下了"折叠区块。细节见
> `next_doc/growth_advisor_implementation_record.md` P4 章节。

### P4-4：报告质量分级 / 增量更新（**已完成**）
- 默认模板报告保持零成本，但给一个可选的"质量优先"档位（消耗一次 LLM
  调用换取更高信息密度），呼应已经在用的 `llm_signal_augment_enabled`
  opt-in 模式。
- 已采纳方向的报告支持"增量刷新"（有新证据积累时提示"要不要更新一下这份
  报告"），而不是一次生成后永远不变。

> **实施记录**：新增独立开关 `GrowthAdvisorConfig.report_quality_llm_
> enabled`（默认 `False`），只影响 `run_daily_cycle()` 生成报告正文时
> 是否传入 `llm_helper`，与控制扫描阶段的 `llm_signal_augment_enabled`
> 完全独立（此前发现 `run_daily_cycle()` 实际上从未把 `llm_helper` 传给
> `generate_growth_report()`，即使调用方传了 `llm_helper` 也一直只走
> 模板——这是本轮顺带修的一个既有 gap，现在由新开关显式控制）。增量
> 刷新新增 `GrowthReport.evidence_count_at_generation` 快照字段、
> `reports_needing_refresh()`（阈值 `report_refresh_min_new_evidence`，
> 默认 3）、`refresh_growth_report()`（生成新报告并把候选 `report_id`
> 指向新报告，旧报告保留在历史记录里不删除）。细节见
> `next_doc/growth_advisor_implementation_record.md` P4 章节。

### P4-5：通知策略细化（**已完成**）
- 按主题类型区分推送偏好（不同主题类别可以有不同的 `notification_frequency`）。
- 引入"重要程度分级"而不是单一置信度阈值，证据充分 + 历史高采纳率的方向
  应该比刚好卡线的方向有更高的推送优先级。

> **实施记录**：类别偏好新增 `GrowthAdvisorConfig.category_notification_
> frequency`（`dict[类别名, str]`），目前只识别 `"kanban_only"` 这一种
> 覆盖值（完全静音某个类别，只看板展示不主动推送），不支持给类别单独
> 设置和全局不同的 daily/weekly_digest 频率（那需要拆分按类别独立节流
> 状态，判断本轮没有明确需求支撑这个复杂度，先不做）。重要程度分级新增
> `_category_acceptance_rate()`（按类别统计历史 accept/dismiss 决策的
> 采纳率）+ `_notification_priority_score()`（置信度 × 采纳率加权，
> 0.7~1.3 倍区间，无历史数据按中性 0.5 处理），`_maybe_dispatch_
> notification()` 用优先级分数替代原来单纯比置信度的排序逻辑，
> `_maybe_dispatch_weekly_digest()` 同步接入类别静音过滤。细节见
> `next_doc/growth_advisor_implementation_record.md` P4 章节。

### P4-6：看板概念统一 + 趋势视图（**已完成**）
- `growth_topic_map`（历史累计）和诊断面板 `topic_hit_counts`（最近一次
  扫描）目前是两个独立区块、口径不同，容易让用户觉得数字对不上——考虑
  在诊断面板里显式说明"这是最近一次扫描的数字，历史累计看下面的主题地图"，
  或者干脆合并成一个视图。
- `growth_topic_map` 目前只有峰值/当前两个值，没有中间时间序列，加一个
  简单的按扫描轮次的证据数走势（不需要复杂图表，文字/简单折线都可以）。

> **实施记录**：选择"两处都加提示"而不是硬合并成一个视图（两者语义
> 确实不同：诊断面板是"最新一轮扫描快照"，主题地图是"历史累计"，合并
> 会丢掉其中一个维度的信息）——`diagnostics_snapshot()` 新增
> `topic_hit_counts_note` 文字字段，看板在两处都展示了对应提示。趋势
> 视图新增独立文件 `growth_topic_trend.jsonl`（`growth_candidate_derive()`
> 每处理一个主题就追加一条 `evidence_count`/`confidence` 快照，不管这
> 轮是否达标生成候选），`growth_topic_map()` 每行新增 `evidence_trend`
> 字段，看板用文字箭头（↗/↘/→）渲染成一行走势说明，没有引入图表库。
> 细节见 `next_doc/growth_advisor_implementation_record.md` P4 章节。

### P4-7：自定义黑名单细化（**已完成——发现后端早已支持，补齐了 UI**）
`excluded_topics` 目前只能排除内置的 7 个固定主题；P4-1 落地后，用户能
自定义添加主题，也应该能对**任意**主题（内置或自定义）设置黑名单，这个
其实是 P4-1 里 "🙈 隐藏内置主题" 按钮的自然延伸，等 P4-1 落地后一起复核
是否已经覆盖到，如果覆盖到了这一项可以直接从后续清单划掉。

> **实施记录**：复核发现后端在 P3 阶段就已经完整支持（`remove_topic_
> keyword()` 会把内置主题写入 `growth_topic_keywords_removed` 黑名单，
> API 文档也一直写着"🙈 隐藏"），但看板从来没有真正渲染过内置主题的
> 隐藏按钮，也没有地方能看到/恢复已隐藏的内置主题——是纯 UI 缺口，不
> 是需要重新设计的功能。补上了对称的恢复操作
> `restore_builtin_topic_keyword()`（不是复用 `add_custom_topic_
> keyword()`，避免把内置主题误转成需要用户重填关键词的自定义条目）+
> `hidden_builtin_topics()` 查询 + API `POST /growth/keywords/{topic}/
> restore` + 看板的隐藏/恢复 UI。细节见
> `next_doc/growth_advisor_implementation_record.md` P4 章节。

---

## 5. 实施顺序建议

1. **P4-0**（命名空间冲突修复）——必须先做，否则 P4-1 存的数据会被静默
   清空，问题比现在更隐蔽。
2. **P4-1**（关键词持久化 + 看板展示）——直接响应本次两条用户反馈，
   优先级最高。
3. P4-2 ~ P4-7 按 P4 后续阶段一节列出的顺序，每个阶段开工前单独细化设计、
   写完整的测试、更新文档，遵循此前 P1-P3 阶段一直在用的节奏（一个阶段
   一次交付、一次回归、一次打包）。

---

## 6. 已知风险 / 待确认事项

- P4-0 的合并式 `generate()` 需要先搜索确认没有其他模块假设
  `profile.derived` 在画像生成后是"纯净、无外部写入"的。
- P4-1 的 `topics_tracked` API 形状变更需要确认看板是不是唯一消费方，
  决定是直接改形状还是新增字段保持向后兼容。
- `confirmed_by_user=False` 的待确认主题要不要参与推送节流的置信度计算，
  还是只参与候选生成不参与推送——本文档倾向于"参与候选生成（用户能在
  看板候选卡片里看到），但推送时降权"，具体降权系数留到实现阶段定。
- 关键词是否需要多语言/大小写归一化处理（目前 `_TOPIC_KEYWORDS` 里中英文
  混杂，子串匹配是大小写不敏感的），用户自定义关键词加入后如果格式不规范
  （多余空格、全角逗号分隔等）需要做基本的清洗，避免脏数据进 profile。
