# 成长顾问：理想形态对照与改进方向 实施记录

对应计划：`growth_advisor_ideal_advisor_gap_and_roadmap_plan.md`
（第 7 节给出的优先级顺序：方向 1 → 方向 4 → 方向 5 → 方向 3 →
方向 2 → 方向 6）。本记录按落地顺序追加，每个方向落地后
在此补一节，不新开文件。

## 已完成

### 方向 1：素材参与度信号

对应方案文档第 1 节。目标：让系统知道"用户到底有没有在看"某个正在
自主推进方向的素材，作为方向 2/4/5 判断的数据地基。

- `evolution/growth_advisor.py` 新增两个函数，紧跟在 `get_pursuit_
  saturation()` 之后，复用同样的存储位置约定：
  - `record_pursuit_material_view(paths, goal_id, cycle_count)`：
    覆盖式写入 `growth_state.json` 新增的 `pursuit_material_views`
    子字典（跟 `pursuit_saturation` 平行，不新开文件），只存
    `{"last_viewed_cycle", "viewed_at"}`，不记录停留时长——看板技术上
    拿不到，也没必要为这一个信号引入额外的前端埋点体系。
  - `get_pursuit_material_engagement(paths, goal_id, current_cycle)`：
    只读查询，返回 `{"last_viewed_cycle", "current_cycle",
    "cycles_since_last_view"}`。从未查看过时 `last_viewed_cycle` 为
    `None`，`cycles_since_last_view` 等于 `current_cycle`（视为"从头
    到现在都没看过"）；`current_cycle` 小于 `last_viewed_cycle` 时
    （理论上不该发生，轮次只增不减）防御式钳制为 0，不返回负数。
- `api/routes.py`：
  - `GET /growth/pursuits` 响应每条方向新增 `engagement` 字段，调用
    `get_pursuit_material_engagement()` 拼装，纯只读聚合，不产生新的
    持久化。
  - 新增 `POST /growth/pursuits/{goal_id}/view_material`：供看板
    "📄 素材"按钮点击时调用。当前轮次由后端从 `GoalBacklog` 读取（不
    信任前端传来的轮次，也省去前端拼请求体），Goal 不存在时返回 404。
- `apps/mini_agent_kanban/client.py` 新增 `growth_pursuit_view_
  material(goal_id)`，对应新端点。
- `apps/mini_agent_kanban/app.py::_render_growth_pursuits()`：
  - "📄 素材"按钮点击时先调用 `client.growth_pursuit_view_material()`
    记一次埋点，失败不阻塞打开素材本身（`try/except` 静默吞掉）。
  - 每条方向标题下方新增一行纯展示 caption：从未查看过时提示"你还
    没查看过这份素材（已有 N 轮内容）"；查看过但又有新内容时提示
    "距你上次查看已经过了 N 轮新内容"；`cycles_since_last_view == 0`
    时不展示（刚看过，没必要提示）。不做警告样式（跟 `saturation`
    的 `st.warning` 区分），不做任何阻断，用户自己判断要不要点进去。
- 新增测试 `tests/test_growth_advisor_material_engagement.py`（5 个
  用例）：从未查看/记录后查询/二次查看覆盖式更新/跨 goal_id 隔离/
  轮次倒退时钳制为 0。
- 成本核对：零新增 LLM 调用，零新增持久化文件，`GET /growth/
  pursuits` 每条方向多一次 `_load_growth_state()` 读取（跟
  `get_pursuit_saturation()` 同一次 IO 量级），符合方案文档"成本
  极低"的预期。这条信号本身不触发任何自动决策（比如自动降频），
  下一步用途留给方向 4（跨方向全局视角）判断，对齐方案文档"先有
  数据、再谈决策"的克制顺序。

## 已完成

### 方向 4：跨方向全局视角摘要

对应方案文档第 4 节。目标：多方向并行推进时，聚合已有的饱和度信号
（B2）和方向 1 的参与度信号，回答"我现在该先看哪几个方向"，而不是
让用户逐条扫一遍列表自己判断。

- `config/models.py::GrowthAdvisorConfig` 新增
  `pursuit_long_unviewed_threshold`（默认 `5`）：判定"长期无人查看"
  的轮次差阈值。
- `evolution/growth_advisor.py` 新增 `pursuits_portfolio_summary(paths,
  goal_backlog, *, long_unviewed_threshold=5)`：
  - 只遍历打了 `growth_advisor` 标签且已落地成 Goal 的候选中
    `goal.recurring=True` 的部分——跟 `/growth/pursuits` 的"🔄 正在
    自主推进"口径完全一致，已暂停的方向不参与统计。
  - 对每个方向复用 `get_pursuit_saturation()` / `get_pursuit_material_
    engagement()` 两个既有只读函数，不重复计算、不引入新判断逻辑。
  - 分类规则：`saturated=True` 记一次"饱和未处理"；
    `cycles_since_last_view >= threshold`（且 `> 0`，避免刚创建、还
    没有过第一轮增量的方向被误判）记一次"长期无人查看"；两个原因
    可以同时命中同一个方向，"建议关注"列表按方向去重、不去重原因。
  - 返回 `{"total", "saturated_count", "long_unviewed_count",
    "attention_needed": [{"goal_id","title","reasons"}], "normal_
    count"}`，不做任何排序/推荐算法，只是分类计数 + 列出具体名单。
- `api/routes.py` 新增 `GET /growth/pursuits/portfolio_summary`：
  纯只读聚合，`long_unviewed_threshold` 从 agent 的
  `GrowthAdvisorConfig` 读取，拿不到时退化到默认值 `5`。
- `apps/mini_agent_kanban/client.py` 新增 `growth_pursuits_portfolio_
  summary()`。
- `apps/mini_agent_kanban/app.py::_render_growth_pursuits()`：分区
  展开时（跟批量操作入口同一个位置）额外拉取一次摘要，命中"建议
  关注"时用 `st.info` 展示"💡 N 个方向可能需要你看一眼：「A」、
  「B」…"，点开分区后仍能在具体那一条上看到饱和度/参与度详情；没有
  命中时展示一句平淡的"都在正常推进"，不做任何自动排序/暂停——
  用户拥有最终决定权，对齐方案文档"不做系统自动决定优先级"的
  明确取舍。
- 新增测试 `tests/test_growth_advisor_pursuits_portfolio_summary.py`
  （7 个用例）：空列表/正常方向不命中/饱和命中/长期无人查看命中/
  刚查看过不误判/暂停方向被排除在统计外/同一方向同时命中两个原因时
  只计入一次"建议关注"。
- 成本核对：零新增 LLM 调用，零新增持久化，`GET /growth/pursuits/
  portfolio_summary` 是独立端点、按需拉取（不放进 `/growth/
  pursuits` 默认响应），跟 `/growth/health_trend` 等既有"展开时才
  请求"的接入模式一致。

## 已完成

### 方向 5：学习效果自测环节

对应方案文档第 5 节。目标：`growth_pursuit` 模板此前只会持续增厚
读书笔记，从不检验"用户是不是真的理解/能应用这些内容"。这里复用
C1（`reorganize_hint_for_cycle()`）已验证的"按累计轮次追加 prompt
指令"模式，往同一次执行循环里追加自测题产出。

- `config/models.py::GrowthAdvisorConfig` 新增
  `pursuit_self_check_every_n_cycles`（默认 `5`，`<=0` 视为关闭）——
  比 `reorganize_every_n_cycles` 的默认 `10` 更小，对齐方案文档"自测
  环节的价值在于及时发现没跟上，间隔太长意义打折"的取舍。
- `evolution/growth_advisor.py` 新增 `self_check_hint_for_cycle(goal,
  cycle_no, cfg=None)`，跟 `reorganize_hint_for_cycle()` 同一套判断
  结构（只对 `growth_advisor` 标签生效、纯轮次号取模、零 LLM 成本、
  不读取执行历史）：满足轮次条件时返回一段提示，要求当轮除正常新增
  内容外，基于 `covered_subtopics` 额外生成 3~5 道自问自答检验题
  （附简短参考答案要点），追加到 wiki 页面末尾独立小节
  「## 自测：第 N 轮小结」。明确要求"不需要用户当场提交答案""不要
  对用户的掌握程度做任何评价或判分"，对齐 `growth_advisor_design.md`
  "不做心理评估/主观判断"的非目标边界。
- `evolution/goal_cron_bridge.py` 新增 `_append_growth_self_check_
  hint()`，跟 `_append_growth_reorganize_hint()` 在同一处（`_trigger_
  cycle`/组装子 Objective description 的位置）串联调用，任何环节
  异常静默跳过、不影响 Goal 触发主流程；不产生额外的执行循环或 LLM
  调用点——复用当轮已经在跑的那一次执行。
- 新增测试 `tests/test_growth_advisor_pursuit_self_check.py`
  （7 个用例）：非 `growth_advisor` 标签不生效/轮次不整除不生效/
  默认阈值生效且包含关键措辞/自定义阈值生效/阈值 `<=0` 关闭/轮次 0
  不生效/生成的提示不要求打分（非目标校验）。
- 成本核对：零新增 LLM 调用点（复用同一次执行循环已有的调用），零
  新增持久化，符合方案文档"可以考虑默认开启（而不是像 B1 LLM 复核
  那样需要 opt-in）"的建议，默认值直接设为开启（`5`）。刻意不做
  自动判分、不做交互提交，避免引入测验式的心理负担。

## 已完成

### 方向 3：Goal 执行内容反哺信号扫描

对应方案文档第 3 节。目标：候选 → Goal 此前是单向的，持续调研过程中
在 `open_questions` 里反复冒出、但从未被吸收的衍生话题，此前只能永远
沉默地躺在 manifest 里；这里补一条规则式的反哺路径，把这类衍生话题
并入下一轮候选生成的输入。

- `evolution/growth_advisor.py`：
  - `GrowthCandidate` 新增 `origin` 字段（默认 `"signal_scan"`，旧数据
    反序列化时自然落到这个默认值，无需迁移）——只在**创建**候选时
    写入，之后任何合并证据的操作都不覆盖它（先到先得：一个话题最初
    是从对话记忆发现的，后来恰好也被 spinoff 命中，仍然保留
    `"signal_scan"`，不因为后到的信号改写来源标记）。
  - `GrowthBacklog.add_or_merge()` 新增 `origin` 关键字参数（默认
    `"signal_scan"`），透传给新建的 `GrowthCandidate`。
  - 新增 `extract_spinoff_topics_from_pursuits(paths, goal_backlog)`：
    口径跟 `pursuits_portfolio_summary()` 完全一致——遍历
    `GrowthBacklog` 里 `linked_goal_id` 指向、且 `goal.recurring=True`
    的方向（不是直接扫 `goal_backlog` 全表按 tags 过滤，避免口径
    跟"🔄 正在自主推进"分区不一致）；对每个方向读全部历史 manifest，
    先算出"曾经出现在任意一轮 `covered_subtopics` 里的文本"全集
    （只要曾被覆盖过就不算沉默线索，不局限于"后续轮次"），再在最近
    `_SPINOFF_LOOKBACK_CYCLES`（3）轮的 `open_questions` 里统计每段
    文本出现次数，`>= _SPINOFF_MIN_OCCURRENCES`（2）次且从未被吸收
    的，才算一条"衍生话题"信号。返回
    `{topic_text: [合成 evidence_refs...]}`，跟 `growth_focus_areas`
    同构，`evidence_refs` 形如 `pursuit_spinoff:{goal_id}:{窗口内
    相对轮次编号}`（不是真实 memory entry_id，只用于计数/去重）。
  - `growth_candidate_derive()` 新增可选 `goal_backlog` 参数：传入时
    调用 `extract_spinoff_topics_from_pursuits()`，把结果并入本轮
    `focus_areas`（同名主题取证据并集，不覆盖已有证据），走同一套
    `min_evidence_count`/置信度乘子计算；新建候选时把
    `origin="pursuit_spinoff"` 传给 `add_or_merge()`（仅对"此前未被
    memory 信号命中过"的主题打这个标记）。不传 `goal_backlog`（沿用
    旧调用点）时行为与改动前完全一致；内部调用
    `extract_spinoff_topics_from_pursuits()` 出错时静默降级为空，不
    影响原有 memory 信号路径。
  - `run_daily_cycle()` 通过既有的 `_load_goal_backlog_safely(paths)`
    拿到 `goal_backlog`（拿不到时为 `None`，自然退化）并传给
    `growth_candidate_derive()`。
- `apps/mini_agent_kanban/app.py`：`_render_growth_pending_list()`（列表
  视图）和 `_growth_card_label()`（拖拽视图）在 `origin="pursuit_
  spinoff"` 的候选标题旁加一个"🔗 来自你正在推进的方向"标记——纯展示，
  帮用户理解"为什么会突然冒出这个建议"，不影响任何排序/操作逻辑。
- 新增测试 `tests/test_growth_advisor_pursuit_spinoff.py`（9 个用例）：
  无推进方向时返回空/反复出现且未吸收的话题被发现（含 evidence_refs
  格式校验）/单次提及不命中/被任意一轮吸收后不再命中/暂停方向的口径
  确认（`recurring` 不变则仍参与统计，跟 `pursuits_portfolio_summary`
  同口径）/窗口外的旧轮次不参与计数；`growth_candidate_derive` 侧：
  spinoff 话题正确打标签/不传 `goal_backlog` 时行为不变/同名话题被
  memory 信号先命中时 origin 不被覆盖且证据取并集。
- 回归：`tests/test_growth_advisor.py` +
  `test_growth_advisor_pursuits_portfolio_summary.py` +
  `test_growth_advisor_material_engagement.py` +
  `test_growth_advisor_pursuit_self_check.py` +
  `test_growth_advisor_saturation_and_pursuit_visibility.py` +
  `test_growth_advisor_pursuit_increment_llm_review.py` 共 233 个用例
  全部通过，无回归（`test_kanban_growth_dragdrop.py` 因为环境缺
  `streamlit` 包无法收集，是预置的环境缺口，与本次改动无关）。
- 同步更新 `docs/growth-advisor-guide.md` 第 7 节"当前局限"里
  "候选 → Goal 是单向的"这条描述，补充说明方向 3 已经覆盖了"衍生话题"
  这一种更窄的反哺场景，但不是"Goal 进展本身反哺候选证据"这种更通用
  的双向同步（仍然是已知局限）。
- 成本核对：零新增 LLM 调用，零新增持久化文件（`origin` 只是
  `growth_backlog.jsonl` 里已有 `GrowthCandidate` 记录新增的一个字段），
  `growth_candidate_derive()` 每轮 cron 多一次"遍历 backlog 里已落地
  方向 + 读取它们的历史 manifest"的 IO，量级与 `pursuits_portfolio_
  summary()` 相当，符合方案文档"复用现有信号扫描输入管道"的克制预期；
  规则式初筛准确度依赖窗口/阈值取值是否合适，先上线观察，暂不引入
  LLM 归一化（方案文档"两步走"里明确的第二步，本轮未做）。

## 已完成

### 方向 2 第一步：反馈模式统计展示

对应方案文档第 2 节第一步。目标：`_dismiss_counts_by_dedupe_key()` /
`_category_dismiss_counts()` 只是把反馈拿去调权重的具体数值，回答不了
"用户到底更容易忽略什么样的方向"这个更高层的问题。这里只做**统计
展示，不做决策接入**——方案文档明确的最低成本、最不会破坏现有排序
逻辑的切入方式。

- `evolution/growth_advisor.py` 新增 `growth_feedback_pattern_summary
  (paths, profile=None)`：
  - 只看最近 `_FEEDBACK_PATTERN_RECENT_WINDOW`（20）条 dismiss 反馈
    （按时间排序取尾部），反映"最近的倾向"而不是"从有记录以来的
    全部历史"——用户兴趣会变化，陈年记录不该继续影响"最近是不是有
    共性"的判断。
  - 按 `dismiss_reason` 分组统计一次（`reason_distribution`），再按
    候选标题对应的类别（复用既有 `_category_of()`）分组统计一次
    （`category_distribution`）。
  - 样本数低于 `_FEEDBACK_PATTERN_MIN_SAMPLE`（5）时不给"摘要文字"
    （`has_enough_data=False`），但计数本身仍然照常返回，供看板按需
    展示原始分布——凑不出有意义的共性判断时，硬给摘要反而可能误导。
  - 样本数达标后，某个原因/类别占比达到
    `_FEEDBACK_PATTERN_DOMINANT_RATIO`（0.5）才写进摘要文字，否则给
    "没有看出明显的共性模式"这句平淡的话——避免样本刚好凑够 5 条、
    其中 3 条随手点了同一个原因就被解读成"模式"。
  - 只产出一段人类可读的摘要文字，**不产出任何用于排序/加权的数值**，
    对齐方案文档"第一步只做统计展示，不做决策接入"的明确取舍。
- `diagnostics_snapshot()` 新增 `feedback_pattern` 字段，内部通过
  `_feedback_pattern_diagnostics_summary()` 包一层 try/except——反馈
  模式统计失败不该拖垮整个诊断面板，失败时退化为"看不出模式"的默认
  结构。
- `apps/mini_agent_kanban/app.py::_render_growth_diagnostics()`：诊断
  面板新增"反馈模式"区块，展示摘要文字；详细的原因/类别分布放进一个
  嵌套的可折叠区块（`查看详细分布`），避免默认就把一堆数字堆在诊断
  面板正文里。新增 `_DISMISS_REASON_DIAGNOSTICS_LABELS`（短标签，跟
  已有的 `_GROWTH_DISMISS_REASON_OPTIONS` 长句子分开，诊断区块更适合
  短词拼进一句 caption）。
- 明确不做的部分（对齐方案文档"第一步只做统计展示"）：这段摘要**不
  自动调整任何候选排序/置信度公式**，`_feedback_multiplier()` /
  `_category_feedback_multiplier()` 等既有衰减公式完全不受影响；
  方案文档提到的"第二步（LLM 归纳）"本轮**未实施**，留待验证第一步
  确实有用之后再排期。
- 新增测试 `tests/test_growth_advisor_feedback_pattern_summary.py`
  （8 个用例）：无 dismiss 记录/accepted 记录不计入/样本不足不给
  摘要断言但计数仍返回/主导原因被正确识别并写进摘要/多个原因和类别
  分散时给出"无共性"的平淡摘要/窗口只看最近 N 条（挤出旧记录）/
  类别分布正确统计/`diagnostics_snapshot()` 正确带上这个新字段。
- 回归：`tests/test_growth_advisor.py` +
  `test_growth_advisor_pursuits_portfolio_summary.py` +
  `test_growth_advisor_material_engagement.py` +
  `test_growth_advisor_pursuit_self_check.py` +
  `test_growth_advisor_saturation_and_pursuit_visibility.py` +
  `test_growth_advisor_pursuit_increment_llm_review.py` +
  `test_growth_advisor_pursuit_spinoff.py` +
  `test_growth_advisor_feedback_pattern_summary.py` 共 250 个用例
  全部通过，无回归。
- 同步更新 `docs/growth-advisor-guide.md` 第 7 节"当前局限"里"各类
  衰减/加权系数都是经验取值"这条描述，补充说明方向 2 第一步已经能
  展示"最近更容易忽略什么"的统计模式，但明确止步于展示、不接入任何
  自动校准。
- 成本核对：零新增 LLM 调用，零新增持久化文件（直接复用既有
  `growth_feedback_ledger.jsonl` / `growth_backlog.jsonl`），
  `diagnostics_snapshot()` 每次调用多一次"读取最近 dismiss 记录 +
  查询候选标题"的只读聚合，量级与既有的 `_dismiss_counts_by_dedupe_
  key()` 相当。

## 已完成

### 方向 2 第二步：反馈模式 LLM 归纳

对应方案文档第 2 节第二步，在第一步（纯统计展示）已经落地并验证可用
之后补上。目标：把规则式统计出来的原因/类别分布数字，在有 agent 上下
文时额外调一次 LLM，组织成一句更自然、更好读的归纳文字——跟第一步
同样明确止步于展示，不接入任何排序/加权计算。

- `config/models.py::GrowthAdvisorConfig` 新增 `feedback_pattern_llm_
  enabled: bool = False`：跟 `goal_alignment_llm_enabled`/
  `llm_signal_augment_enabled` 同款默认关闭、opt-in 的取舍，保持
  "默认零 LLM 成本"的基线。
- `evolution/growth_advisor.py`：
  - `growth_feedback_pattern_summary()` 新增可选关键字参数 `cfg`/
    `llm_helper`，返回值新增 `llm_insight` 字段（默认空字符串）。只有
    `cfg.feedback_pattern_llm_enabled=True` 且传了 `llm_helper` 且
    规则式统计已经 `has_enough_data=True` 时才会真正触发那次 LLM
    调用——样本不够时数字本身就没有可归纳的东西，不值得多花一次调用。
    不传 `cfg`/`llm_helper`（沿用旧调用点）时行为与改动前完全一致。
  - 新增 `_llm_summarize_feedback_pattern()`：把 `reason_distribution`/
    `category_distribution`/`sample_size` 组织进 prompt，要求 LLM 只
    基于给出的数字说一两句自然语言、看不出规律就直说看不出，不做任何
    JSON 解析（只要一段自然语言，比 `_llm_match_interests_to_goals`
    的结构化匹配简单）；输出防御性截断到 300 字；空响应/异常都静默
    退化为空字符串，不影响规则式 `summary_text` 的返回，也不向上抛出。
  - LLM 调用结果通过既有的 `_record_llm_call_status(paths,
    "feedback_pattern_insight", ...)` 记录，跟其它 LLM 增强调用共用
    同一份"最近一次调用状态"诊断信息。
  - `diagnostics_snapshot()` 新增可选关键字参数 `llm_helper`，透传给
    `growth_feedback_pattern_summary()`；`_feedback_pattern_diagnostics_
    summary()` 同步接收 `cfg`/`llm_helper` 并在异常兜底结构里补上
    `llm_insight: ""`。
- `api/routes.py`：`/growth/summary` 路由跟 `/growth/align` 同款写法——
  只有 `self_agent` 存在且 `cfg.feedback_pattern_llm_enabled=True` 时
  才取 `self_agent.llm_helper` 包一层传给 `diagnostics_snapshot()`。
- `apps/mini_agent_kanban/app.py::_render_growth_diagnostics()`："反馈
  模式"区块里，规则式 `summary_text` 照常展示，`llm_insight` 存在时
  额外用一行 `💡` 前缀的 caption 并列展示在下方（弱化视觉权重，提示
  这是"补充解读"而不是更权威的结论，不替换规则式摘要）。
- 新增测试（追加进 `tests/test_growth_advisor_feedback_pattern_
  summary.py`，新增 `TestGrowthFeedbackPatternLlmInsight` 测试类，7 个
  用例）：默认关闭时即便传了 helper 也不调用/开启但没传 helper 仍为
  空/开启且传了 helper 时正确产出 insight（并验证 prompt 里带上了
  正确的分布数字）/样本不足时不触发 LLM 调用/LLM 空响应优雅降级/LLM
  抛异常不向上传播/`diagnostics_snapshot()` 正确透传 `llm_helper`。
  文件总用例数从 8 增至 15，全部通过。
- 回归：`tests/test_growth_advisor.py` +
  `test_growth_advisor_pursuits_portfolio_summary.py` +
  `test_growth_advisor_material_engagement.py` +
  `test_growth_advisor_pursuit_self_check.py` +
  `test_growth_advisor_saturation_and_pursuit_visibility.py` +
  `test_growth_advisor_pursuit_increment_llm_review.py` +
  `test_growth_advisor_pursuit_spinoff.py` +
  `test_growth_advisor_feedback_pattern_summary.py` 共 257 个用例
  全部通过，无回归。
- 成本核对：默认关闭，零增量成本；开启后每次 `/growth/summary`
  刷新诊断面板、且规则式统计样本达标时，多一次 LLM 调用，量级与
  `goal_alignment_llm_enabled` 开启后 `/growth/align` 每次调用多一次
  LLM 语义匹配相当，符合方案文档"opt-in、成本可控"的预期。

## 待推进（按方案文档第 7 节优先级）

（原方案文档六个方向已全部落地。以下两项是后续讨论中新增的候选方向，
已完成实现，见下方"已完成"对应小节。）

## 已完成

### 方向 6：调研风格智能分类（规则默认 + LLM opt-in）

对应方案文档第 6 节。目标：`growth_pursuit` 模板此前对技术类、理论类、
习惯养成类方向一视同仁，产出方式完全相同。方案文档原建议"先做用户
手动选择、暂不做自动判断"，后续与用户讨论后改为直接做**自动智能
分类**——跳过手动选择这一中间态，规则式关键词匹配作为零成本默认
路径（总是可用），LLM 复核作为 opt-in 增强（默认关闭）。

- `evolution/growth_advisor.py` 新增：
  - `_PURSUIT_STYLE_LABELS = ("技能实操类", "知识理论类", "习惯养成类")`——
    跟 P5-3 的 `_TOPIC_CATEGORY_LABELS`（"是什么话题"）是两个正交维度，
    这里回答的是"这类话题该怎么调研/呈现"。
  - `_PURSUIT_STYLE_KEYWORDS`：只登记"技能实操类"（编程/开发/工程/
    框架/代码/api 等）和"习惯养成类"（习惯/打卡/坚持/作息/锻炼等）
    两类的高置信度关键词，刻意不穷举——跟 `_TOPIC_CATEGORIES` 只登记
    7 个高置信度主题同一种"宁可漏判归入默认值，不强行猜测引入噪音"
    的取舍。
  - `_infer_pursuit_style_rule(topic, extra_text="")`：关键词命中数
    最多的类别胜出，全不命中或平局兜底"知识理论类"（读书笔记式持续
    调研是 `growth_pursuit` 模板最初、也是最通用的产出形态，作为
    默认值最保守）。零 LLM 成本，总是可用。
  - `classify_pursuit_style_llm(topic, keywords, llm_helper, paths=None)`：
    跟 `classify_topic_category_llm()` 同款"opt-in、宽松吸收"模式，
    3 选 1，解析失败/异常/空响应一律返回 `None`，调用方兜底沿用规则式
    结果，不会倒退现有行为。LLM 调用结果通过既有的
    `_record_llm_call_status(paths, "pursuit_style", ...)` 记录。
  - `determine_pursuit_style(topic, extra_text="", keywords=None, cfg=None,
    llm_helper=None, paths=None)`：统一入口，规则式结果总是先算出来；
    `cfg.pursuit_style_llm_enabled=True` 且传了 `llm_helper` 时才额外
    调一次 LLM 复核，命中合法标签就覆盖，否则静默沿用规则式结果——这
    一步失败绝不影响返回值的可用性。
  - `_PURSUIT_STYLE_PROMPT_ADDENDUM`：每种风格对应一段 prompt 追加
    指令（技能实操类多给可复现操作步骤/代码示例；知识理论类维护
    结构化知识脉络；习惯养成类以短小打卡式记录为主、不追求持续增厚
    知识库）。
  - `pursuit_style_hint(goal, cfg=None)`：只对打了 `growth_advisor`
    标签、且 `goal.growth_pursuit_style` 非空（已被分类过）的 Goal
    返回对应的风格提示；未分类（旧 Goal、或非自动推进路径创建的
    Goal）时返回 `None`，不影响任何现有行为。跟 C1/方向 5 的"按累计
    轮次触发"不同，这里**每一轮都带上**——风格是这个方向的持续属性，
    不是某个特定轮次才需要的提醒。
- `config/models.py::GrowthAdvisorConfig` 新增 `pursuit_style_llm_
  enabled: bool = False`：跟 `topic_category_llm_enabled`/
  `feedback_pattern_llm_enabled` 等同款默认关闭、opt-in 的取舍。
- `perception/goal_backlog.py::GoalNode` 新增 `growth_pursuit_style:
  Optional[str] = None` 字段（同步补齐 `to_dict`/`from_dict`）：只影响
  `growth_pursuit` 模板每一轮 prompt 里追加的风格提示，不影响任何
  排序/执行判定；旧数据反序列化缺该字段时落到 `None`，等价于"未分类"，
  不需要额外迁移。
- `evolution/growth_advisor.py::auto_pursue_candidate()`：落地成 Goal
  之后（步骤 2 与步骤 3 之间新增步骤 2.5），若 Goal 尚未分类过
  （`growth_pursuit_style` 为空），调用一次 `determine_pursuit_style()`
  并写回 `goal_backlog.update_fields(goal.id, growth_pursuit_style=...)`——
  只在**首次**自动持续推进落地时判定一次（风格是持续属性，不需要每次
  自动推进都重算），失败静默跳过、不影响主流程（`report`/`goal`/
  `spec`/`cron_job` 各步骤照常推进）。
- `evolution/goal_cron_bridge.py` 新增 `_append_growth_pursuit_style_
  hint()`，跟 `_append_growth_reorganize_hint()` / `_append_growth_
  self_check_hint()` 在同一处（`_trigger_cycle`/组装子 Objective
  description 的位置）串联调用，任何环节异常静默跳过、不影响 Goal
  触发主流程；不产生额外的执行循环或 LLM 调用点。
- `api/routes.py`：`GET /growth/pursuits` 每条方向新增 `pursuit_style`
  字段（`getattr(goal, "growth_pursuit_style", None)`），纯只读透出，
  不产生新的持久化或计算。
- `apps/mini_agent_kanban/app.py::_render_growth_pursuits()`：调度信息
  这一行 caption 里追加 `🧭 <风格>` 标记（未分类时不展示，不影响既有
  布局）——纯展示，帮用户理解"这个方向的素材会偏实操案例、还是偏
  结构化脉络、还是偏打卡提醒"。
- 新增测试 `tests/test_growth_advisor_pursuit_style.py`（18 个用例）：
  规则式分类（技能类关键词命中/习惯类关键词命中/无命中兜底知识理论类/
  extra_text 参与匹配/平局取默认）；LLM 分类（合法标签/非法标签返回
  None/空响应返回 None/异常返回 None）；统一入口 `determine_pursuit_
  style()`（默认只用规则/关闭时忽略 helper/开启但无 helper 时降级为
  规则/开启且有效时覆盖规则/LLM 返回非法值时降级为规则）；
  `pursuit_style_hint()`（非 growth_advisor 标签不生效/未分类不生效/
  三种风格都能正确生成提示/非法风格值返回 None）。
- 回归：`tests/test_growth_advisor.py` +
  `test_growth_advisor_pursuits_portfolio_summary.py` +
  `test_growth_advisor_material_engagement.py` +
  `test_growth_advisor_pursuit_self_check.py` +
  `test_growth_advisor_saturation_and_pursuit_visibility.py` +
  `test_growth_advisor_pursuit_increment_llm_review.py` +
  `test_growth_advisor_pursuit_spinoff.py` +
  `test_growth_advisor_feedback_pattern_summary.py` +
  `test_growth_advisor_pursuit_style.py` 共 275 个用例全部通过；另外
  单独跑了 `test_goal_backlog.py` + `test_goal_cron_bridge.py` +
  `test_growth_advisor_goal_cron_integration.py`（`GoalNode` 新字段 /
  `goal_cron_bridge` 新 hint 函数所在模块的既有测试）共 48 个用例，
  全部通过，无回归。`test_kanban_growth_dragdrop.py` 因为环境缺
  `streamlit` 包无法收集，`test_system_connectivity_routes.py` 等
  少数路由测试因环境缺 `fastapi` 包无法收集，均是预置的环境缺口，
  与本次改动无关。
- 成本核对：默认（`pursuit_style_llm_enabled=False`）零增量 LLM 调用，
  规则式分类只是一次字符串关键词匹配，量级可忽略；`GoalNode` 新增
  一个字段不引入新的持久化文件；开启 LLM 复核后，只在
  `auto_pursue_candidate()` 首次落地一个 Goal 时触发一次 LLM 调用
  （不是每轮 cron 都调），量级与 `topic_category_llm_enabled` 开启后
  单个主题首次归类时的调用频率相当，符合方案文档"opt-in、成本可控"
  的预期。
- 同步更新 `docs/growth-advisor-guide.md`：新增 2.20 节完整记录方向 6
  的现状问题/改动/新增变更文件；5 节配置表新增 `pursuit_style_llm_
  enabled` 行；7 节"当前局限"补一条方向 6 的已知边界（分类只在 Goal
  首次落地时判定一次、不会随后续实际产出动态修正；规则式关键词表
  覆盖面有限，边界情况容易被兜底成默认的"知识理论类"）。

### 方向 6 动态修正：调研风格按实际产出内容周期性重判

对应聊天中新增候选（源自方向 6 局限分析：分类只在 Goal 首次落地时
基于候选标题判定一次，不会随后续实际产出动态修正）。目标：把"猜
风格"这件事从"只看一句话标题"升级为"定期看实际已经产出了什么"。

- `evolution/growth_advisor.py` 新增：
  - `_recent_covered_subtopics_text(paths, goal_id, lookback)`：汇总
    最近 `lookback`（默认 5）轮 manifest 里 handoff 的
    `covered_subtopics`，拼成一段文本；读取失败/没有数据返回空
    字符串，调用方据此自然退化为"跳过重判"。
  - `maybe_reclassify_pursuit_style(paths, goal_backlog, goal, cycle_no,
    cfg=None, llm_helper=None)`：只对打了 `growth_advisor` 标签、且
    轮次号能整除 `cfg.pursuit_style_reclassify_every_n_cycles`（默认
    8，`<=0` 关闭）的 Goal 触发；用最近几轮实际产出内容重新跑一次
    `determine_pursuit_style()`（复用方向 6 已有的规则默认 + LLM
    opt-in 逻辑，不是另起一套分类器），结果与当前值不同才写回
    （`goal_backlog.update_fields()` + 直接更新 `goal.growth_pursuit_
    style` 双重保险，不依赖调用方传入对象与 backlog 内部节点是否
    同一引用），相同则不产生任何写入。没有可用的 `covered_subtopics`
    文本时直接跳过，不会用"没有新内容"误判成某个具体风格。
- `config/models.py::GrowthAdvisorConfig` 新增
  `pursuit_style_reclassify_every_n_cycles: int = 8`——取值介于
  `pursuit_self_check_every_n_cycles`（5）和 `reorganize_every_n_
  cycles`（10）之间，"有足够内容可供判断、但不会拖太久才修正"的
  折中值。
- `evolution/goal_cron_bridge.py` 新增
  `_maybe_reclassify_growth_pursuit_style()`，调用点放在
  `_append_growth_pursuit_style_hint()` 之前（同一处组装子
  Objective description 的位置），保证同一轮里如果风格被修正了，
  紧接着追加的风格提示用的是修正后的新值；任何环节异常静默跳过，
  不影响 Goal 触发主流程。这一步本身不产生新的 LLM 调用点（复用
  `determine_pursuit_style()` 已有的规则默认路径，桥接函数目前没有
  透传 `llm_helper`，即便全局开启 `pursuit_style_llm_enabled` 也只走
  规则式重判——避免在 cron 触发路径上引入新的隐式 LLM 成本，后续如果
  需要在这个接入点也支持 LLM 复核，需要单独评估调用频率）。
- 新增测试（见下方方向 7 同一测试文件）：`_recent_covered_subtopics_
  text()` 空数据/正常聚合；`maybe_reclassify_pursuit_style()` 非
  growth_advisor 标签跳过/轮次不整除跳过/配置关闭跳过/结果未变化时
  不写入且返回 `None`/结果确实变化时正确写回 `GoalNode` 与
  `goal_backlog` 持久化/没有可用文本时跳过。

### 方向 7：报告质量自动闭环

对应聊天中新增候选（源自反馈维度局限分析：`report_not_useful` 反馈
此前只是记录下来给人看，没有反过来指导报告生成策略）。目标：某个
方向的报告被反复标"内容太笼统"之后，下一次生成能自动换一种更详细
的生成方式，而不是继续用固定模板反复产出同样质量的内容。

- `evolution/growth_advisor.py` 新增：
  - `_should_auto_upgrade_report_quality(paths, candidate, cfg=None)`：
    只读判断，`cfg.report_quality_auto_upgrade_enabled=False`（默认）
    时直接返回 `False`，零成本、零行为变化；开启后，复用既有的
    `_report_quality_dismiss_counts()` 统计，命中
    `cfg.report_quality_auto_upgrade_threshold`（默认 2，`<=0` 视为
    关闭）才返回 `True`。只回答"要不要"，"能不能"（是否真的有
    `llm_helper` 可用）由调用方判断。
  - `generate_growth_report()` 新增 `quality_auto_upgraded: bool =
    False` 参数：为 `True` 时在正文开头追加一句"这个方向之前的报告
    被反馈过内容太笼统，这一份自动换成了更详细的生成方式"（跟
    `is_exploration` 的"探索位"标注同一种"管理预期、不悄悄换生成
    方式却不告诉用户"的展示风格），并透传进 `GrowthReport.quality_
    auto_upgraded` 字段。只影响这句提示和字段值，不影响是否真的走
    LLM 路径（那由 `llm_helper` 是否非 `None` 决定）。
  - `GrowthReport` 新增 `quality_auto_upgraded: bool = False` 字段，
    区别于 `source="llm"`（可能只是全局 `report_quality_llm_enabled`
    打开导致，不代表这里有过负反馈）；旧数据反序列化缺该字段落到
    `False`，等价于"不是因为质量信号被升级的"，不需要额外迁移。
  - `run_daily_cycle()`：报告生成循环从列表推导式改成显式
    `for` 循环，逐个候选判断——全局模板路径（`report_quality_llm_
    enabled=False`）下，如果当前上下文确实拿得到 `llm_helper`（比如
    cron 触发）且 `_should_auto_upgrade_report_quality()` 命中，临时
    把这一份报告的 `llm_helper` 换成真实的 `llm_helper`、并传
    `quality_auto_upgraded=True`；不修改全局开关本身，只影响这一次
    调用，同一轮里其余方向仍走各自原来的路径。
- `config/models.py::GrowthAdvisorConfig` 新增
  `report_quality_auto_upgrade_enabled: bool = False`（默认关闭，
  对齐"增加调用成本的能力默认关闭"的一贯原则）和
  `report_quality_auto_upgrade_threshold: int = 2`。
- 新增测试 `tests/test_growth_advisor_pursuit_style_reclassify_and_
  report_upgrade.py`（15 个用例，覆盖上面方向 6 动态修正与方向 7
  两部分）：
  - `_recent_covered_subtopics_text()`：无 manifest 返回空/正常聚合
    多轮内容；
  - `maybe_reclassify_pursuit_style()`：非 `growth_advisor` 标签跳过/
    轮次不整除跳过/`cfg` 显式关闭跳过/规则式重判结果未变化时返回
    `None` 且不写入/结果确实变化（标题猜的"知识理论类" vs 实际内容
    明显偏"习惯养成类"）时正确写回 `GoalNode` 与持久化存储/没有可用
    文本时跳过；
  - `_should_auto_upgrade_report_quality()`：默认关闭返回 `False`/
    开启但未达阈值返回 `False`/开启且达到阈值返回 `True`/阈值为 0
    时按关闭处理；
  - `generate_growth_report()`：`quality_auto_upgraded=False`（默认）
    时正文不含升级提示；`quality_auto_upgraded=True` 时正文含提示且
    字段正确回填；`GrowthReport.from_dict()` 缺该字段时兜底 `False`。
- 回归：`tests/test_growth_advisor.py` +
  `test_growth_advisor_pursuits_portfolio_summary.py` +
  `test_growth_advisor_material_engagement.py` +
  `test_growth_advisor_pursuit_self_check.py` +
  `test_growth_advisor_saturation_and_pursuit_visibility.py` +
  `test_growth_advisor_pursuit_increment_llm_review.py` +
  `test_growth_advisor_pursuit_spinoff.py` +
  `test_growth_advisor_feedback_pattern_summary.py` +
  `test_growth_advisor_pursuit_style.py` +
  `test_growth_advisor_pursuit_style_reclassify_and_report_upgrade.py` +
  `test_goal_backlog.py` + `test_goal_cron_bridge.py` +
  `test_growth_advisor_goal_cron_integration.py` 共 352 个用例，351
  通过；唯一失败的
  `TestHealthTrend::test_compact_health_trend_storage_downsamples_
  old_points` 是跟本次改动完全无关的既有用例（跟当天的日期边界分桶
  有关，单独隔离运行同样失败，确认是预置的时间敏感型环境问题，不是
  本次改动引入的回归）。
- 成本核对：两个方向默认都关闭（`pursuit_style_reclassify_every_n_
  cycles=8` 本身零 LLM 成本，因为桥接函数目前不透传 `llm_helper`；
  `report_quality_auto_upgrade_enabled=False`），默认零增量成本；
  开启后，动态修正每 8 轮做一次规则式重判（可忽略），报告质量自动
  升级只在命中阈值的方向上多花一次 LLM 调用，不是无差别升级全部
  报告，符合"opt-in、成本可控"的一贯原则。

### 推送的情境感知（软性节流）

对应聊天中新增候选（源自感知维度局限分析：推送节流此前完全是静态
规则，不会感知"用户最近是不是明显没那么活跃"）。目标：在不引入
心理/精力评估的前提下，用一个间接、可观测的信号（对话密度）对推送
门槛做一次软性调整，而不是硬性阻断整轮推送。

- `evolution/growth_advisor.py` 新增：
  - `_recent_conversation_density_ratio(memory_store, recent_days=7,
    baseline_weeks=4, now=None)`：最近一周的记忆条目数，相对于再往
    前 4 周的周均值，算出一个密度比值；`memory_store` 缺失、或基线
    窗口内条目数为 0（没有足够历史数据）时返回 `None`——不强行算出
    一个可能是噪音的比值，调用方遇到 `None` 视为"没有可用信号"。
  - `_effective_notification_min_confidence(paths, cfg,
    memory_store=None)`：在配置的 `notification_min_confidence`
    基础上，只有 `cfg.notification_context_aware_throttle_enabled=
    True` 且算出的密度比值低于 `notification_low_activity_ratio_
    threshold`（默认 0.3）时，才额外加上 `notification_low_
    activity_confidence_boost`（默认 0.15，封顶 1.0）——软性抬高
    门槛，依然可能推送，只是需要更高置信度；其余情况原样返回配置值，
    跟改动前完全一致。
  - `_maybe_dispatch_notification()` 新增 `memory_store=None` 参数，
    内部改用 `_effective_notification_min_confidence()` 计算门槛
    （原来直接读 `cfg.notification_min_confidence`）；`run_daily_
    cycle()` 调用处透传已有的 `memory_store`。
- `config/models.py::GrowthAdvisorConfig` 新增
  `notification_context_aware_throttle_enabled: bool = False`（默认
  关闭）、`notification_low_activity_ratio_threshold: float = 0.3`
  （`<=0` 视为关闭）、`notification_low_activity_confidence_boost:
  float = 0.15`。
- 新增测试 `tests/test_growth_advisor_notification_context_aware_
  throttle.py`（12 个用例）：`_recent_conversation_density_ratio()`
  （无 `memory_store` 返回 `None`/无基线数据返回 `None`/最近更安静时
  比值 `<1`/活跃度持平时比值约等于 1）；`_effective_notification_
  min_confidence()`（关闭时原样返回/开启但无信号原样返回/开启且
  命中"更安静"时正确抬高/开启但活跃度持平时不抬高/抬高结果封顶
  1.0/阈值为 0 时按关闭处理）；`_maybe_dispatch_notification()`
  集成验证（不传 `memory_store` 时行为不受影响/安静期确实能把一条
  中等置信度报告过滤掉）。
- 回归：加上这批新测试后，`test_growth_advisor.py` +
  `test_growth_advisor_pursuits_portfolio_summary.py` +
  `test_growth_advisor_material_engagement.py` +
  `test_growth_advisor_pursuit_self_check.py` +
  `test_growth_advisor_saturation_and_pursuit_visibility.py` +
  `test_growth_advisor_pursuit_increment_llm_review.py` +
  `test_growth_advisor_pursuit_spinoff.py` +
  `test_growth_advisor_feedback_pattern_summary.py` +
  `test_growth_advisor_pursuit_style.py` +
  `test_growth_advisor_pursuit_style_reclassify_and_report_upgrade.py` +
  `test_growth_advisor_notification_context_aware_throttle.py` +
  `test_goal_backlog.py` + `test_goal_cron_bridge.py` +
  `test_growth_advisor_goal_cron_integration.py` 共 350 个用例全部
  通过（`TestHealthTrend::test_compact_health_trend_storage_
  downsamples_old_points` 这一个跟本次改动无关的既有时间敏感用例
  单独排除，理由同上一节）。
- 成本核对：默认关闭，零行为变化；开启后不产生任何新的 LLM/网络
  调用，只是多扫一次已经在内存里的 `memory_store.all_entries()`——
  这个调用在 `growth_signal_scan()` 里本来就会做一次，量级可忽略。
