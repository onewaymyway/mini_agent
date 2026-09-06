# 目标树节点「📄 相关调研」看不到报告 / 报告存储位置改造

- **状态**：**已实施**。
- **触发**：用户在看板"🌳 目标树"某节点的"⚙️ 管理"→"📄 相关调研"区块里
  点击"🔍 立即调研"后反馈——"为什么这里看不到生成的调研报告"，并建议
  "具体节点下点击立即调研，对应生成的调研报告，也应该在这个地方有个
  查看入口，列出这个节点下的所有报告"；同时指出报告的保存位置应该在
  该节点对应的 goal 目录下（或专门的目标树结果目录），方便统一查看和
  管理。

## 1. 根因

`FocusResearchTrigger.trigger()`（`goal_tree_research_and_action_
recommendation_plan.md` §4.2 阶段二引入）原本**只生成一条 `GrowthCandidate`
候选**，从不调用 `generate_growth_report()`——调研报告只在用户于「🌱 成长」
tab 手动点"✅ 采纳"、触发 `auto_pursue_candidate()` 的第 1 步时才第一次
生成。所以"🔍 立即调研"按钮点完之后，除了一条候选记录，什么实质内容都
没有——用户看不到报告是因为**报告压根还不存在**，不是查看入口缺失
（虽然入口确实也缺，`_render_goal_tree_research_section()` 原来只列
pending 候选标题，从不展示报告）。

另外，即使报告存在（比如候选被采纳之后），落盘位置也是全局平铺的
`.agent/wiki/growth/<slug>.md`——所有方向（不管是不是来自目标树某个
节点）的报告混在同一个目录里，不按目标树结构组织，跟用户"应该保存到
这个节点对应的 goal 目录下"的直觉不符。

## 2. 改动

### 2.1 报告落盘目录可定制（`growth_advisor.py::generate_growth_report()`）

新增可选参数 `report_dir`：传入时报告正文写到 `<report_dir>/<slug>.md`，
不传时保持原有的全局 `paths.wiki_growth_dir` 行为——`run_daily_cycle()`/
`auto_pursue_candidate()`/`refresh_candidate_report()` 等其余所有现有
调用方都不受影响。元数据索引（`growth_reports.jsonl`）仍然是同一份
全局索引，只有正文文件的物理位置变化；`list_reports()`/`get_report_
by_id()`/`GET /v1/growth/reports/{id}` 都按 `GrowthReport.body_path`
读取，天然兼容新旧两种目录，不需要改任何读取路径。

### 2.2 焦点驱动调研触发时立即生成报告（`focus_research_trigger.py`）

- 新增 `_default_report_dir_for_node(paths, node_id)`：复用已有的
  `output_workspace.goal_output_base_dir()` 目录约定，返回
  `<outputs_root>/goals/<node_id>/research/`——不新增一套目录规则，
  跟该节点将来可能有的执行产出（`cycle_0001/` 等）分开放，一眼区分
  "调研阶段的东西"和"执行任务的东西"。不要求 `node_id` 是叶子 Goal，
  域/阶段等结构节点同样适用（这个函数本身只是拼路径）。
- `FocusResearchTrigger.trigger()` 新增 `generate_report: bool = True`
  参数：候选生成/合并成功后，如果这条候选身上还没有报告，立即用零成本
  规则模板生成一份并挂上（`report_dir` 用上面的节点专属目录）。生成
  失败只记日志、不影响候选本身的返回结果。重复触发命中 `add_or_merge`
  合并逻辑时不会重新生成报告（`candidate.report_id` 已非空）。
- 新增 `list_research_items_for_node(paths, node_id)`：跟原有
  `list_pending_research_candidates()`（只返回 pending）不同，返回该
  节点**全部**（含 accepted/dismissed/expired）调研候选，每条带上
  报告摘要信息（`report_id`/`report_summary`/`report_slug`，没有报告
  则为 `None`），按触发时间倒序——这是"这个节点历史上做过哪些调研"的
  完整视图，候选被采纳后不会从列表里消失。

### 2.3 REST（`api/routes.py`）

- `GET /v1/goals/{node_id}/research` 响应新增 `items` 字段（上面
  `list_research_items_for_node()` 的结果），`pending_candidates` 字段
  保留、行为不变，向后兼容。
- `POST /v1/goals/{node_id}/research/trigger` 行为跟随
  `FocusResearchTrigger.trigger()` 的新默认值变化，返回的 `candidate`
  现在通常带有非空 `report_id`（除非报告生成失败）。

### 2.4 看板（`apps/mini_agent_kanban/app.py::_render_goal_tree_research_section()`）

- 改读 `items`（全部历史）而不是只读 `pending_candidates`，每条历史
  展示状态徽标（⏳ 待处理 / ✅ 已采纳 / 🚫 已忽略 / ⌛ 已过期）；有
  `report_id` 时给一个"📄 查看报告"展开按钮（复用「🌱 成长」tab 现成
  的 `client.growth_report(report_id)` 调用方式），没有报告时说明原因
  并给出手动采纳的提示，不误导用户以为出了故障。
- 有历史记录时额外提示报告的实际落盘位置
  （`.agent/daemon_run_outputs/goals/<node_id>/research/`）。
- 「🔍 立即调研」触发成功后的提示文案区分"生成了报告"和"候选生成了但
  报告没生成成功"两种情况。
- 兼容旧后端：`items` 字段缺失时退化为用 `pending_candidates` 拼一份
  等价列表（报告信息为空），不会因为后端还没升级就报错。

## 3. 测试

`tests/test_focus_research_trigger.py` 新增两个测试类：
- `TestTriggerGeneratesReportImmediately`：验证 `trigger()` 默认会
  生成报告、报告文件确实落在该节点的 `goals/<id>/research/` 目录（不是
  `wiki_growth_dir`）、`generate_report=False` 能跳过、重复触发不会
  重新生成报告。
- `TestListResearchItemsForNode`：验证候选+报告摘要能查到、accepted
  状态候选也会出现在列表里、没有调研历史的节点返回空列表。

回归：`test_focus_research_trigger.py`（原有 11 个 + 新增 8 个，共 19
个全过）、`test_goal_tree_research_action_phase4.py`（`list_pending_
research_candidates` 未受影响）、`test_growth_advisor.py`/`test_growth_
advisor_auto_pursue.py`/`test_growth_advisor_research_quality.py`
（`generate_growth_report()` 新增的可选参数未影响任何现有调用方）全部
通过。

## 4. 开放问题 / 有意不做的事

- **报告用规则模板还是 LLM**：本次改动只解决"有没有报告""报告在哪"，
  不涉及报告内容生成质量——`trigger()` 内部调用 `generate_growth_
  report()` 时沿用调用方传入的 `llm_helper`（REST/CLI 手动触发路径
  通常拿不到 `llm_helper`，走零成本规则模板；cron/daemon 上下文若传了
  `llm_helper` 则会用 LLM 起草），跟其它调用方的既有约定一致，不单独
  为这条路径引入新的质量策略。
- **旧数据迁移**：改动前已经生成、还留在全局 `wiki/growth/` 目录下的
  历史报告**不做迁移**——`get_report_by_id()`/`GET /v1/growth/reports/
  {id}` 都按 `body_path` 读取，旧报告的 `body_path` 仍然指向旧目录，
  照常可读；只有本次改动之后新触发的焦点调研才会写进节点专属目录。
- **专门的"目标树结果目录"**：用户原话提到两种可能方案之一是"有一个
  专门保存目标树相关结果的目录"，本次选择了"该节点对应的 goal 目录下"
  这一种（`goals/<node_id>/research/`），因为它复用了已有的
  `output_workspace` 目录约定、不需要新增顶层目录规则，且跟节点未来
  真正执行产生的输出天然放在一起，浏览体验上更连贯。
