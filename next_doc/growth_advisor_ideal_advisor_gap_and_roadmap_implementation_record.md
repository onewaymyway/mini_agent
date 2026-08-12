# 成长顾问：理想形态对照与改进方向 实施记录

对应计划：`growth_advisor_ideal_advisor_gap_and_roadmap_plan.md`
（第 7 节给出的优先级顺序：方向 1 → 方向 4 → 方向 5 → 方向 3 →
方向 2 → 方向 6（不排期））。本记录按落地顺序追加，每个方向落地后
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

## 待推进（按方案文档第 7 节优先级）

1. 方向 6：主题类型分化的调研/呈现风格——方案文档明确本轮不建议
   排期，留待后续视情况重新评估。
2. 方向 2 第二步（LLM 归纳反馈模式、接入排序）——方案文档明确建议
   先验证第一步（纯统计展示）确实有用之后再做，本轮未排期。
