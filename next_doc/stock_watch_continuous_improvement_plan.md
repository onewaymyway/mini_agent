# stock_watch（及任意外部项目）的持续优化迭代机制设计

> **这篇文档管什么**：daemon / 大管家 agent 如何"持续优化迭代"一个已经
> 落地的外部项目（首先是 `stock_watch`），而不只是阶段5已有的"东西坏了
> 修回去"。
>
> **不管什么**：本文档不重复 `external_projects_workspace_plan.md` 已经
> 确立的四条核心原则、`Workspace`/`project.yaml`/账本/维护流水线这些
> 已完成的机制，只在其基础上补一块——本文档默认读者已经读过那篇文档。
> 涉及具体股票业务逻辑（评分权重怎么调、用哪个技术指标）的内容不在本
> 文档范围，那是 stock_watch 自己 `PROJECT.md` 该管的事，本文档只定
> "让这类优化有据可依、有地方落地、有人把关"这层机制。

## 1. 起因：为什么阶段5的机制不够用

阶段5的 `propose_maintenance_fix`/`land_maintenance_fix` 解决的是一类
边界清晰的问题：**有客观失败信号**（health_check 不健康 / entrypoint
非零退出），**有客观验收标准**（改完之后跑得起来了 / 健康检查恢复），
**风险可控**（T2 校验：lint + 目标项目自己的 `tests/`）。

"持续优化迭代"是另一类问题：

- 候选池打分打得准不准、选股条件筛得好不好，**没有报错**，只是"效果
  一般"——没有硬失败信号可以触发阶段5的流程。
- 判断"这个改动是不是真的更好"是主观权衡，不是"能不能跑"这种是非题。
- 因此不能直接复用阶段5的触发方式（等失败）和验收标准（lint+tests
  能过就行），需要一套新的、独立的机制。

把两类问题混在一套机制里处理，大概率会导致两种失败模式之一：要么优化
诉求因为"没有失败信号"永远不会被触发，要么为了触发而放宽验收标准，
结果一个"能跑但更差"的改动被当成"修复"一样自动过检。所以本文档把
"纠错"和"优化"当成两条独立的循环来设计。

## 2. 两条循环的对照

| | 纠错循环（阶段5，已完成） | 优化循环（本文档） |
|---|---|---|
| 触发信号 | 硬失败：health_check 不健康 / entrypoint 非零退出 | 软信号：质量趋势、结果回溯、人工反馈 |
| 触发时机 | 事件驱动（失败发生后，被 `inspect_project` 发现或人工触发） | 周期性 review + 人工反馈随时可以补充 |
| 判定对错 | 客观：跑起来 / 跑不起来 | 主观：需要证据支撑，没有绝对正确答案 |
| 验收标准 | T2：lint + `tests/` | T2 之上再加一层：目标项目自己积累的"黄金案例回归测试"（见第5节），但即便测试通过也不能替代人工判断 |
| 落地方式 | `land_maintenance_fix`，独立显式调用 | 同样独立显式调用，且**不允许自动化**——见第5节 |

两条循环共享同一套底层设施（`StateRepo`/`EvolutionWorkspace` 的 git
worktree 隔离、`propose_maintenance_fix`/`land_maintenance_fix`），
区别只在"谁来触发"和"验收标准里除了能跑还要不要看证据"，因此实现上
是给现有函数加一个 `change_type` 维度，而不是另起一套并行机制（避免
重复实现的一次性方案，呼应仓库既有的"通用/架构解"约定）。

## 3. 证据从哪来：三层信号，目前一层都不存在

要让 agent 的优化提案"有据可依"而不是"感觉应该优化一下"，需要能回答
"现在做得怎么样"这个问题的持久化证据。当前 stock_watch 只有两类状态：
`run_status.jsonl`（整体成败）和 `candidate_pool.json`（当前快照，不留
历史），都不足以支撑"评估优化效果"。需要补三层：

### 3.1 执行细粒度信号（stock_watch 自己的账本，框架无需改动）

现在账本粒度是"entrypoint 整体成功/失败"，掩盖了"某个数据源持续半失
效但还没拖累整体判定"这类趋势。`hotlist_scan`/`run_stock_analysis` 内
部应该把每个数据源本次是否成功、耗时、返回条数，写进 stock_watch 自己
的补充账本（例如 `data/source_health.jsonl`，项目私有，不是框架
`run_status.jsonl` 的一部分，因为这是股票系统特有的"多数据源"结构，
框架不需要理解）。

### 3.2 结果回溯信号（stock_watch 自己的新 entrypoint，本文档新增）

这是评价"选股/打分逻辑本身好不好"唯一站得住脚的依据：候选池当天打了
高分的标的，一周/一月后实际涨跌如何？选股条件筛出来的标的，后续表现
如何？现在候选池只有"当下快照"，没有"历史决策 + 后续结果"的对照。

新增 `entrypoints/reconcile_outcomes.py`：定期（比如每周）把 N 天前的
候选池 / 选股结果快照取出来，用 `akshare` 查这些标的期间的实际涨跌幅，
写进 `data/outcome_ledger.jsonl`（schema 见第4节 stage 详情）。这是
stock_watch 的业务逻辑，不是框架能力。

### 3.3 人工反馈信号（框架层面的"改进积压"账本，本文档新增）

用户在对话里说"这次报告漏了明显的热点"，现在说完就没了，不会变成任何
可追溯的待办，下次 review 也不会自动看到。需要一个轻量的"收件箱"，
用法和 `run_status.jsonl` 一致（外部项目自己写、daemon 被动读），但
记的是"值得被下次优化处理的问题"而不是"执行记录"。这个东西是任何外部
项目都会需要的通用能力（不只是股票系统才有"软问题需要被记住"的需求），
设计为框架能力，见第4节阶段1。

## 4. 具体改造计划

> 约定：每完成一项，回来把对应复选框打勾，并在文末"变更记录"补一行。

### 阶段 0：设计确认（本文档）
- [x] 起因、两条循环对照、三层证据信号确认

### 阶段 1：框架层 — 改进积压账本（通用能力）
- [x] 新增 `src/mini_agent/external_projects/backlog.py`：
      `BacklogItem` dataclass（`id`/`source`/`summary`/`evidence_ref`/
      `status`/`opened_at`/`resolved_at`）+ `append_item()`/
      `read_backlog()`/`update_status()`，落盘到
      `<root>/.agent/improvement_backlog.jsonl`，写法风格对齐
      `ledger.py`（`atomic_append_jsonl`、损坏行跳过不炸整份文件）。
      `source` 枚举至少含 `outcome_review`（3.2 结果回溯发现的问题）、
      `user_feedback`（人工反馈）、`health_trend`（3.1 细粒度信号里
      发现的趋势性问题，非硬失败但持续走低）。
- [x] `Workspace` 新增 `backlog_path` 属性（`<root>/.agent/
      improvement_backlog.jsonl`），与已有的 `run_status_path` 对称。
- [x] `tools/external_projects.py` 新增 `append_backlog_item` /
      `list_backlog`（`@tool`，只读的 `list_backlog` 不需要审批，
      `append_backlog_item` 只是写一条待办，不涉及执行代码，同样不需要
      审批），供大管家在对话中把用户的临场反馈落成可追溯记录。
- [x] `cli/commands/projects_cmd.py` 新增 `projects backlog <name>
      [list|add <summary>]` 子命令，供人工直接操作（不必每次都进
      agent 对话）。
- [x] 单元测试：写读容错、损坏行跳过、状态流转（open→proposed→
      landed/dismissed）——`tests/test_external_projects_backlog.py`，
      13 项全部通过，且完整外部项目测试套件（65 项）无回归。CLI
      `projects backlog <name> add/list` 端到端验证通过。

### 阶段 2：框架层 — `propose_maintenance_fix` 区分 fix / enhancement
- [x] `propose_maintenance_fix` 新增 `change_type: Literal["fix",
      "enhancement"] = "fix"` 参数，透传进
      `MaintenanceProposalResult`，纯附加字段，不改变默认行为
      （默认值 `"fix"` 保持向后兼容）。
- [x] `tools/external_projects.py::propose_fix` 工具的入参/返回里带上
      这个字段，让调用方（daemon/大管家/人）在展示提案时能区分风险
      等级；`enhancement` 类型的提案在返回的 `message` 里带上"这是
      优化类改动，不要自行落地，交给用户核对证据后决定"的提示，`fix`
      类型维持原有措辞。
- [x] 明确一条不通过代码强制、而是约定层面的规则并写进文档（见本节
      `propose_maintenance_fix` 的 docstring 与本文档第5节）：
      `land_maintenance_fix` 对 `change_type="enhancement"` 的分支，
      **禁止**任何自动化脚本或 agent 自主调用，只能是人工在看过
      提案 diff 和证据（第3节的三层信号）之后手动执行。当前
      `land_maintenance_fix` 本来就是独立显式调用，未被
      `propose_maintenance_fix` 自动串联，这一条本质是"维持现状，
      不新增自动化捷径"，不是要新写校验代码去拦截。

### 阶段 3：stock_watch — 细粒度执行信号 + 结果回溯任务
- [x] `stock_watch/source_health.py`：数据源级别成败记录的轻量封装，
      `hotlist_scan` 内部改用它记录每个数据源本次成败（对应 3.1）。
      `run_stock_analysis.py` 暂未接入（它本来就已经把三类材料各自的
      成败记进 `StockAnalysis.errors` 并体现在报告里，粒度已经够用，
      接入 `source_health.py` 留到真的需要跨次趋势分析时再做，避免
      为了统一而统一）。
- [x] 新增 `entrypoints/reconcile_outcomes.py` + `stock_watch/
      outcomes.py`：从候选池历史快照（`candidate_pool.py` 新增
      `save_pool_snapshot`/`load_pool_snapshot`/`list_snapshot_dates`，
      `hotlist_scan` 每次运行额外归档一份）取 N 天前的记录，查实际
      涨跌幅，写入 `data/outcome_ledger.jsonl`（对应 3.2）。已加进
      `project.yaml` 的 `entrypoints`（`cron: 0 18 * * 1`，每周一
      盘后）。涨跌幅超过 `outcomes.notable_gain_pct` 阈值的案例，
      通过 `entrypoints/_common.py::append_backlog()` 自动写入改进
      积压账本（该函数遵循与 `tracked_run` 相同的降级约定：检测不到
      mini_agent 框架时静默跳过，不影响 entrypoint 本身的执行结果）。
- [x] `candidate_pool.py::save_pool` 补一份归档快照写入——落地为
      独立函数 `save_pool_snapshot()`（`hotlist_scan` 在 `save_pool`
      之后额外调用一次），而不是改 `save_pool` 本身的行为，因为两者
      语义不同（当前状态 vs 历史存档），保持职责分离比在一个函数里
      做两件事更清楚。
- [x] 单元测试：`tests/test_outcomes_and_source_health.py`（9 项，
      快照归档读写容错、`outcomes.py` 结果拼装/阈值筛选/分桶汇总、
      `source_health.py` 记录/失败率统计），加上原有 7 项，stock_watch
      离线单测共 16 项全部通过。用 mock 涨跌幅数据端到端跑通了一遍
      `reconcile_outcomes.py`（快照归档 → 回溯 → 报告 → 改进积压账本
      写入），确认涨跌幅 22% 的案例被正确判定为"值得关注"并写入
      backlog；框架 `manifest.py::load_manifest()` 确认新增的
      `reconcile_outcomes` entrypoint 能被正常解析（`cron`/
      `timeout_sec` 均正确）。

### 阶段 4：框架层 — daemon 侧周期性 review session
- [x] 设计"review session"的触发方式：**不是** `project.yaml` 的
      `entrypoints.schedule`（那是"跑既定代码的子进程"，不涉及 LLM
      判断），而是复用本仓库已经验证过的 `evolution/cron_scheduler.py
      ::CronJob.task_template` 模式（daemon 自身的 `sys:
      growth_advisor_daily` 等内置任务走的就是这条路：定时把一段任务
      描述文本提交进输入队列，由带着相应工具的 agent 去执行）——不是
      发明一套新的调度概念。工具集限定为 `list_projects`/
      `inspect_project`/`list_backlog`/`append_backlog_item`/
      `propose_fix`（`external_projects/review.py::
      REVIEW_SESSION_TOOLS`），以目标项目的 `Workspace` 为根（复用
      原则四）。**已落地**：`project.yaml` 新增可选 `review:
      {cadence, enabled}` 块（`manifest.py::ReviewSpec`），
      `external_projects/review.py` 提供 `gather_review_briefing()`
      （读目标项目自己的 `run_status.jsonl`/`improvement_backlog.
      jsonl`）+ `build_review_task_template()`（拼成任务描述文本）+
      `cadence_to_cron()`（"weekly"/"daily"/"monthly" → cron 表达式，
      供未来接线用）；CLI 新增 `projects review <name>` 打印生成的
      任务模板。**未落地、明确留到需要时再做**：把生成的任务模板
      实际注册进正在运行的 daemon 的 `CronScheduler`——当前
      `DaemonClient` 只暴露了 `list_cron_jobs`/`run_cron_job`，没有
      "新增 job"的远程接口，接线需要先给运行中的 daemon 加一个 HTTP
      端点，风险/工作量都不小，且不影响本阶段其余产出的可验证性
      （`build_review_task_template_for()` 全程离线可测，`projects
      review` 命令端到端手测已验证），因此按文档第5节"刻意留白"的
      原则明确推迟，接线方式已经在 `review.py` 顶部 docstring 里写清楚
      （用 `CronScheduler.add_job(schedule=f"cron:{cadence_to_cron(...)}",
      task_template=build_review_task_template_for(...), tags=
      ["external_project_review", name])`）。
- [ ] 会话读写目标项目自己的 memory（`Workspace.memory_store_path`）
      ——**明确推迟**：这一项依赖"真的有一次会话在跑"，本阶段既然
      还没接线真实的 review session 触发，就没有可验证的落点；留到
      真正接线（上一条的"未落地"部分）完成、有真实会话产生时一并
      验证，避免写一段没有调用方、无法端到端验证的代码。
- [x] 系统提示词设计：见 `build_review_task_template()`——读最近执行
      记录/改进积压账本，交代任务边界（不是纠错、证据不足不要臆断）、
      交代权限边界（enhancement 提案只生成分支不落地）、交代产出物
      要求（可以是提案分支、可以是待办账本条目、可以是给用户的总结，
      不强求每次都要有一个具体动作）。已用固定材料单元测试拼装结果
      （含关键短语断言：`change_type`/`enhancement` 一定出现，确保
      权限边界不会在未来改动中被无意间删掉）。
- [x] 调度频率与开关：`project.yaml` 新增可选字段 `review:
      {cadence: "weekly", enabled: true}`（不放进 `entrypoints`，
      避免和"跑代码"的语义混在一起）；stock_watch 自己的
      `project.yaml` 已经加上这个块作为真实用例（`enabled: true`，
      `mini-agent projects review stock_watch` 端到端验证通过，能正确
      读到候选池反馈类待办并拼进任务模板）。daemon 主循环按此周期
      真正触发，等上面"未落地"部分完成后才会生效——`enabled: true`
      目前只影响 CLI 命令的一句提示文案，不会导致任何自动行为，这一点
      在 `project.yaml` 里已经写成注释，避免用户误以为现在已经在自动
      跑了。

### 阶段 5：stock_watch — 黄金案例回归测试沉淀
- [x] 新增 `stock_watch/golden_cases.py`：`GoldenCase`/`GoldenCaseResult`
      dataclass + `run_pipeline()`（复现
      `run_hotlist_scan.main()` 的"`ensure_seeds` → `merge_hot_items`
      → `apply_decay` → `enforce_max_size`"流水线顺序）+ `evaluate()`
      （对照 `expected_included`/`expected_excluded`/`min_score` 判定
      是否通过）+ `load_golden_cases()`（从 `tests/golden_cases/
      cases.json` 读取，文件不存在时返回空列表，容错约定与仓库其它
      账本一致）。案例数据用 JSON fixture 而不是写死在 Python 里，
      方便今后 review session / 人工往里追加新案例时不用碰代码。
- [x] `tests/golden_cases/cases.json` 固化 3 个案例（`tests/
      test_golden_cases.py` 覆盖）：① `multi_source_consensus_
      outranks_single_mention`——多数据源共识标的应稳定跑赢单来源
      低热度标的，`max_size` 收紧时后者先被淘汰；② `seed_stock_
      merged_even_without_hot_mentions`——种子标的即使本次没有任何
      热点数据源提及也必须进候选池，且基础分保持 1.0；③
      `seed_not_exempt_from_max_size_trim_known_gap`——**固化案例过程
      中发现的一个真实差异**：`ensure_seeds()` 的 docstring 写"种子
      标的...不受淘汰影响（...淘汰时另行豁免）"，但 `enforce_max_size()`
      的实际实现并没有对种子做豁免，分数垫底的种子和普通标的一样会
      被 `max_size` 截掉。本次**只固化当前真实行为**（不代表这是期望
      行为），是否要修 `enforce_max_size()` 补豁免逻辑还是改 docstring
      措辞，留给未来一次独立的 enhancement 决策——本身正是这套优化
      循环机制该处理的问题类型，不在阶段5顺手改掉。
- [x] 明确黄金案例只做"回归护栏"（不能变得更差），不做"自动判断更
      好"——是否更好仍然是第5节意义上的人工判断，测试通过只是"没有
      引入已知的历史型错误"这个更弱的保证；`test_golden_cases.py`
      顶部 docstring 与 `golden_cases.py` 模块 docstring 都重申了这条
      边界。stock_watch 离线单测新增 7 项（16→23），全部通过。

### 阶段 6：端到端验证
- [x] 新增 `tests/test_stock_watch_optimization_loop_e2e.py`（仓库
      顶层，因为要串起框架层 `backlog`/`review`/`maintenance` 和
      stock_watch 自己的 `outcomes`/`golden_cases`），模拟完整链路：
      ① 用 mock 涨跌幅数据构造 `outcomes.build_outcome_records` +
      `notable_outcomes`，复现"eastmoney_hot_rank 单来源打分标的
      （000002）4 周后大跌 23.4%，多来源共识标的（600519）继续上涨"
      这类模式 → ② `append_item(source="outcome_review", ...)` 写入
      改进积压账本 → ③ `gather_review_briefing`/
      `build_review_task_template_for` 确认 review session 能读到这条
      待办并在任务模板里带上 `change_type=`/`enhancement` 措辞 →
      ④ `tools/external_projects.py::propose_fix(change_type=
      "enhancement")` 生成提案分支，确认返回的 `message` 里带着
      "Do not land it yourself"提示、主分支不受影响 → ⑤ 用
      `git diff` 模拟人工核对证据，确认差异符合预期后手动调用
      `land_maintenance_fix` 落地，`update_status(..., "landed")`
      标记待办 → ⑥ 断言阶段5固化的 `multi_source_consensus_
      outranks_single_mention` 黄金案例仍然通过——呼应"后续黄金案例
      测试纳入这个案例"：本次 outcome_review 发现的"单来源高分 vs
      多来源共识"模式，已经有对应的回归护栏在守着。全程单测层面
      模拟，不接入真实 daemon 主循环定时调度（阶段4"未落地"部分），
      测试内对此有明确注释说明。完整外部项目测试套件（76 项，含本
      文件）与 stock_watch 离线单测（23 项）全部通过，无回归。

## 5. 风险与刻意留白

- **不做"自动判断优化改动是否应该落地"**：无论测试覆盖率多高，评分
  权重、数据源取舍这类改动本质是权衡，本机制刻意不提供"agent 自主
  落地 enhancement" 的路径，第2节已经写明这是设计选择，不是当前能力
  不足。
- **review session 的调度接入 daemon 主循环** 留到阶段4实现时才做
  真正接线（同阶段3当年"调度判断逻辑先做、主循环接入留到需要时"的
  处理方式一致），不阻塞其余阶段的验收。
- **`source_health.py`/`outcomes.py` 是否需要上收成框架能力**，等
  出现第二个"多数据源 + 需要结果回溯"的外部项目时再决定，本次先在
  stock_watch 内验证，不提前抽象（呼应主文档第7节的一贯态度）。

## 6. 变更记录

- 2026-08-26：文档创建。设计确认阶段（阶段0）完成，来源于"如何从机制
  上让 daemon/agent 持续优化迭代 stock_watch"的讨论。核心结论：纠错
  与优化是两条独立循环，优化循环的合法性建立在三层证据信号
  （执行细粒度/结果回溯/人工反馈）之上，且任何优化类改动的最终落地
  永远保留给人工决定，不因为有了自动化提案流水线就跳过这一步。
  阶段1（改进积压账本）待开始。
- 2026-08-26：阶段1、阶段2 完成。新增
  `src/mini_agent/external_projects/backlog.py`（改进积压账本读写 +
  状态流转）、`Workspace.backlog_path`、`tools/external_projects.py`
  新增 `list_backlog`/`append_backlog_item` 两个只读/轻写工具、CLI
  新增 `projects backlog <name> [list|add]` 子命令；
  `propose_maintenance_fix`/`propose_fix` 新增 `change_type` 参数
  （"fix"|"enhancement"，默认 "fix"，纯附加、不改变既有默认行为）。
  新增 `tests/test_external_projects_backlog.py`（13 项，覆盖 backlog
  读写容错/状态流转/`change_type` 透传与校验），完整外部项目测试套件
  （65 项）无回归；CLI `projects backlog add/list` 做了端到端手测。
  阶段3（stock_watch 细粒度信号 + 结果回溯任务）待开始。
- 2026-08-26：阶段3 完成。`candidate_pool.py` 新增
  `save_pool_snapshot`/`load_pool_snapshot`/`list_snapshot_dates`
  （归档快照，修补此前"只有当下状态、没有历史"的缺口）；新增
  `stock_watch/source_health.py`（数据源级别成败记录，`hotlist_scan`
  已接入）、`stock_watch/outcomes.py`（结果回溯纯逻辑）、
  `entrypoints/reconcile_outcomes.py`（结果回溯 entrypoint，已加入
  `project.yaml`，每周一盘后跑）；`data_sources.py` 新增
  `fetch_price_change_pct`；`report.py` 新增
  `render_outcome_report`；`_common.py` 新增 `append_backlog()`
  （与 `tracked_run` 同样的框架降级约定）。新增
  `tests/test_outcomes_and_source_health.py`（9 项），stock_watch
  离线单测共 16 项全部通过。用 mock 涨跌幅数据端到端跑通了
  `reconcile_outcomes.py` 全流程（归档快照 → 回溯 → 报告 → 改进
  积压账本），22% 涨幅案例被正确判定为"值得关注"并写入 backlog；
  `manifest.py` 确认新 entrypoint 解析正确。阶段4（daemon 侧周期性
  review session）待开始。
- 2026-08-26：阶段4 部分完成（设计落地 + 可验证部分已实现，daemon 实际
  接线明确推迟，见阶段4 详情）。`manifest.py` 新增 `ReviewSpec` +
  `project.yaml` 的可选 `review: {cadence, enabled}` 块解析；新增
  `src/mini_agent/external_projects/review.py`
  （`gather_review_briefing`/`build_review_task_template`/
  `build_review_task_template_for`/`cadence_to_cron`/
  `REVIEW_SESSION_TOOLS`）；CLI 新增 `projects review <name>`。核心
  设计决定：review session 复用本仓库已有的 `CronJob.task_template`
  调度模式（而非新造机制），但把生成的任务模板实际注册进运行中
  daemon 的 `CronScheduler` 需要先给 daemon 加一个"新增 job"的 HTTP
  端点，这部分工作量与风险超出本阶段范围，明确推迟，接线方式已经写进
  `review.py` 的模块 docstring。stock_watch 自己的 `project.yaml` 已
  加上 `review: {cadence: weekly, enabled: true}` 作为真实用例。新增
  `tests/test_external_projects_review.py`（10 项），完整外部项目
  测试套件（75 项）全部通过；`mini-agent projects review stock_watch`
  端到端手测通过（正确读到候选池反馈类待办并拼进任务模板文本）。
  阶段5（黄金案例回归测试沉淀）、阶段6（端到端验证）待开始；阶段4
  剩余的 daemon 接线部分，等真正需要跑起来（而不只是设计验证）时
  再回来做。
- 2026-08-26：阶段5、阶段6 完成，全部改造计划（阶段0-6）落地完毕。
  新增 `stock_watch/golden_cases.py`（黄金案例回归护栏纯逻辑）+
  `tests/golden_cases/cases.json`（3 个固化案例）+
  `tests/test_golden_cases.py`（7 项）。固化案例过程中发现一个真实
  的文档/实现差异：`candidate_pool.py::ensure_seeds()` 的 docstring
  声称种子标的"淘汰时另行豁免"，但 `enforce_max_size()` 并未实现这层
  豁免——已作为 `seed_not_exempt_from_max_size_trim_known_gap` 案例
  如实固化当前行为（不代表这是期望行为），修不修留给未来一次独立的
  enhancement 决策，呼应本机制"发现问题不等于立刻顺手改掉"的设计
  态度。新增顶层 `tests/test_stock_watch_optimization_loop_e2e.py`
  验证完整优化循环：outcome_review 发现问题（mock 涨跌幅：单来源高分
  标的大跌、多来源共识标的上涨）→ 写入改进积压账本 → review session
  材料收集/任务模板正确带上待办与 `enhancement` 权限提示 → `propose_
  fix(change_type="enhancement")` 生成提案分支（返回消息正确提示
  "不要自行落地"）→ 模拟人工用 `git diff` 核对证据后手动 `land_
  maintenance_fix` 落地、标记待办为 `landed` → 断言阶段5固化的"多来源
  共识 vs 单来源噪声"黄金案例仍然通过，代表这类问题模式已经有回归
  护栏在守着。全程单测层面模拟，不接入真实 daemon 主循环定时调度。
  完整外部项目测试套件（76 项）、stock_watch 离线单测（23 项）全部
  通过，无回归。至此本文档第4节的改造计划全部完成；后续该做什么
  （比如真的把 review session 接进运行中的 daemon，或者处理阶段5
  发现的种子豁免差异）留给下一份独立的迭代文档决定，不在本文档继续
  堆叠。

- 2026-08-27：第3.1节提到的"账本粒度掩盖数据源级失败趋势"缺口，先在
  框架层做了一个更基础的前置改进：`run_status.jsonl` 新增 `detail`
  字段（承载失败时的完整诊断信息），时间戳改为本地时间。这不等价于
  3.1 节要求的"每数据源细粒度信号"（那仍需要 stock_watch 自己的
  `data/source_health.jsonl`，尚未实施），但让`entrypoint`整体失败时
  至少能看到具体原因，不用再去翻 daemon 日志。详见
  `external_projects_workspace_plan.md` 阶段 4 2026-08-27 条目。
- 2026-08-27（续）：用户实测反馈——账本能看到详情后，定位到 `screener`
  失败的真实原因是问财（`www.iwencai.com/customized/chart/get-robot-
  data`）4 条查询全部返回 `401 Unauthorized`（不是网络不通/解析失败），
  推断该接口现在要求带 `hexin-v` 反爬 cookie。已改造
  `stock_watch/data_sources.py`：
  - `fetch_html()` 新增可选 `session` 参数，传入时复用调用方给的
    `requests.Session`（保留原来"不传就用一次性 `requests.get()`"的
    行为，不影响其它调用方）
  - 新增 `_get_iwencai_session()`/`_warm_up_iwencai_session()`：用
    `requests.Session()` 先请求一次问财首页，让服务端按正常浏览器
    握手流程下发 `hexin-v` cookie，session 按进程内缓存复用（20 分钟
    强制刷新兜底），不涉及逆向任何加密算法——如果未来验证升级成必须
    跑 JS 才能算出 token，这个办法会失效，需要换 Selenium/Playwright
    或参考 `pywencai` 之类现成库
  - `_fetch_iwencai_web()` 改为自己实现"最多两轮尝试，第 2 轮前强制
    刷新令牌"的重试逻辑（新增 `_is_unauthorized()` 顺着异常链判断根因
    是不是 HTTP 401），不复用 `fetch_html()` 默认的"对任何请求异常都
    原样重试"语义——同一个过期令牌重试 3 次没有意义
  - 用 mock 验证了"第一次 401 → 刷新 session → 第二次成功"这条路径；
    `external_projects/stock_watch/tests/` 43 个既有测试全部通过
  - **未在真实网络下验证**：本次改造所在环境无法访问
    `iwencai.com`，用户反馈"自己有网络环境可以测"，需要用户在真实
    环境跑一次 `projects run stock_watch screener` 确认能拿到
    `hexin-v` 且请求成功；如果问财这次改的验证机制比"首页
    Set-Cookie"更复杂（比如真的需要跑 JS 算 token），这个方案会仍然
    401，需要回来换方案
