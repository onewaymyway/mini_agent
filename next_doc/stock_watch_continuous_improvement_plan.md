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
- [ ] 设计"review session"的触发方式：**不是** `project.yaml` 的
      `entrypoints.schedule`（那是"跑既定代码的子进程"，不涉及 LLM
      判断），而是 daemon 侧新的、独立的调度概念——按周期为已注册
      项目发起一次真实 mini_agent 会话，工具集限定为
      `list_projects`/`inspect_project`/`list_backlog`/
      `append_backlog_item`/`propose_fix`（`change_type=
      "enhancement"`），以目标项目的 `Workspace` 为根（复用原则四）。
- [ ] 会话读写目标项目自己的 memory（`Workspace.memory_store_path`），
      把"问财周一早高峰容易被限流"这类经验沉淀下来，供以后的 review
      session 和日常诊断复用，不用每次从零发现——这是本文档第一次
      让 review session 真正用上 `Workspace` 早就预留、但一直没有
      使用方接入的 memory 隔离能力。
- [ ] 系统提示词设计：读最近的账本/细粒度信号/结果回溯/改进积压，
      判断有没有值得处理的项；机械性、有回归测试兜底的直接走
      `propose_fix(change_type="enhancement")` 生成可审核分支；判断
      不了或者影响面大的，写成 backlog 条目或者直接生成一份给用户看
      的文字建议，不擅自决定。
- [ ] 调度频率与开关：`project.yaml` 新增可选字段 `review:
      {cadence: "weekly", enabled: true}`（不放进 `entrypoints`，
      避免和"跑代码"的语义混在一起）；daemon 主循环按此周期触发，
      默认关闭（`enabled: false`），需要项目自己显式打开。

### 阶段 5：stock_watch — 黄金案例回归测试沉淀
- [ ] `reconcile_outcomes` 跑出的"预测 vs 结果"里，误判幅度大的典型
      案例，定期（人工或 review session 判断后）挑选固化进
      `tests/test_golden_cases.py`：给定某个历史时点的行情快照，跑
      当前评分/筛选逻辑，断言不能比历史已知的"应该选中/不应该选中"
      结论差太多。这样阶段2里"enhancement 改动至少要过 T2"里的
      `tests/` 会自动越来越有效，不需要框架改代码。
- [ ] 明确黄金案例只做"回归护栏"（不能变得更差），不做"自动判断更
      好"——是否更好仍然是第5节意义上的人工判断，测试通过只是"没有
      引入已知的历史型错误"这个更弱的保证。

### 阶段 6：端到端验证
- [ ] 模拟场景：`reconcile_outcomes` 发现某类打分逻辑长期高估某个
      数据源来源的标的 → 写入 backlog → review session 读到 → 生成
      `enhancement` 类型提案（带 `change_type` 标记）→ 人工查看提案
      与证据 → 手动 `land_maintenance_fix` 落地 → 后续黄金案例测试
      纳入这个案例。验证整条链路（可以是单测层面的端到端，不要求
      接入真实 daemon 主循环的定时调度）。

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
