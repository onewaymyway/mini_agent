# Goal/Cron 相关方案文档状态核对记录

> 归属：`goal_cron_convergence_and_governance_improvement_plan.md` Track 2
> 的实施记录。核对范围：`next_doc/` 下文件名或内容主题涉及 goal/cron 的
> 全部方案文档（29 篇），核对方式：读取状态栏（或开头的实现状态描述）与
> 正文"实施记录"/"实现状态"小节是否一致，并对提到的具体函数名/配置项
> 做代码存在性抽查。

## 核对结论汇总

| 文档 | 核对前状态描述 | 核对结论 | 处理 |
|---|---|---|---|
| `cron_dedicated_execution_improvement_plan.md` | 已落地（第一至四轮） | 一致 | 无需修改 |
| `cron_dedicated_execution_implementation_record.md` | 记录型文档，无独立状态栏 | 一致 | 无需修改 |
| `cross_goal_experience_reuse_plan.md` | 正文标注"已实现：`find_similar_confirmed_goals()`" | 一致（代码存在） | 无需修改 |
| `goal_cron_binding_plan.md` | 已完成（Track A-E 全部落地） | 一致 | 无需修改 |
| `goal_cron_binding_implementation_record.md` | 记录型文档 | 一致 | 无需修改 |
| `goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md` | Stage 1/2/3 已完成 | 一致 | 无需修改 |
| `goal_cron_cycle_proactive_patrol_and_health_overview_plan.md` | Stage 1/2/3 已完成 | 一致（本轮刚更新过） | 无需修改 |
| `goal_cron_feedback_and_output_policy_plan.md` | 已实施完成（Track A-J） | 一致 | 无需修改 |
| **`goal_cron_output_directory_convention_plan.md`** | **设计草案 / 待评审（尚未实施）** | **不一致**——§6"实施记录"、§7 后续补充均显示已完整落地并测试通过 | **已修正**（见本文档改动记录） |
| `goal_cron_task_optimization_holistic_plan.md` | 方向 A/B/C/D 分别标注完成度 | 一致（分方向标注本身已经是准确的细粒度状态） | 无需修改 |
| `goal_cron_unified_scheduler_implementation_record.md` | 记录型文档 | 一致 | 无需修改 |
| `goal_cron_unified_scheduler_improvement_plan.md` | P0-P4 已完成；P5 第 1-3 步已完成，第 4 步部分完成（Goal 通道 `execute()` 未实现） | 一致（代码抽查确认 `ObjectiveChannelAdapter.execute()` 确实 `raise NotImplementedError`） | 无需修改（Track 1 正在解决这个缺口本身，不是文档描述问题） |
| `goal_cron_visibility_and_intervention_improvement_plan.md` | Track A/B/C/D 已完成，Phase 5 按计划不实现 | 一致 | 无需修改 |
| `goal_execution_fairness_improvement_plan.md` | P1-P5 全部完成（P4 默认关闭） | 一致 | 无需修改 |
| `goal_execution_phase_improvement_plan.md` | 无独立状态栏，需通读判断 | 内容与 `goal_stuck_stats_and_llm_progress_judge_plan.md` 中"Stage D 已完成"的引用一致 | 无需修改 |
| `goal_execution_phase_stage_d_implementation_record.md` | 记录型文档 | 一致 | 无需修改 |
| `goal_execution_scheduling_global_cap_bugfix.md` | Bug 修复记录，无状态栏 | 正文描述根因和修复方式，未见"未完成"遗留项 | 无需修改 |
| `goal_execution_spec_generation_plan.md` / `_implementation_record.md` | 无独立状态栏 | 与后续引用它的文档（如 `goal_execution_phase_improvement_plan.md` 开头）描述一致 | 无需修改 |
| `goal_fairness_scheduling_diagnostics_plan.md` | 含实施记录小节 | 一致 | 无需修改 |
| `goal_mode_completion_improvement_plan.md` | 改造项一/二/三均标注"已实现" | 一致 | 无需修改 |
| `goal_mode_stage2_ensemble_and_fine_grained_plan.md` | 标注前置状态已完成，本文档自身范围待评审 | 一致（未被误读为已实施） | 无需修改 |
| `goal_mode_stuck_compact_plan.md` | 累计五轮改造，各项标注"已实现" | 一致 | 无需修改 |
| `goal_stuck_stats_and_llm_progress_judge_plan.md` | 含实施记录小节，引用其它文档"Stage D 已完成" | 一致 | 无需修改 |
| `growth_advisor_cron_search_and_status_history_plan.md` | 方向一/二/三均已实施完成 | 一致 | 无需修改 |
| `growth_advisor_goal_cron_integration_plan.md` | 无独立状态栏，问题陈述型开篇 | 通读后确认是当前仍在讨论"要不要打通"的方案性文档，未见误导性的完成度表述 | 无需修改 |
| `kanban_cron_delete_consistency_bugfix.md` | Bug 修复记录 | 未见状态描述问题 | 无需修改 |
| `watchlist_notification_goal_design.md` | §12.1 + §6 状态列跟踪评审改进点吸收情况 | 一致 | 无需修改 |
| `goal_cron_convergence_and_governance_improvement_plan.md` | 设计草案 / 待评审 | 一致（本文档自身，随 Track 推进逐步更新） | 随实施推进更新 |

## 结论

本轮核对（29 篇）只发现 **1 处**状态栏与实际实施进度不一致：
`goal_cron_output_directory_convention_plan.md`。已在该文档中订正状态栏，
并将 §4 小标题中"本文档暂不实施"的表述同步修正为"实际实施为一次性完成，
见 §6"，避免同一份文档内部出现状态描述自相矛盾。

其余文档的状态描述与正文实施记录/代码实际情况核对一致，未发现需要修正的
问题。这与 Track 2 设计时的预期（"这类脱节大概率不止一处"）有出入——
实际核对下来 goal/cron 这条线的文档维护质量总体是好的，只是恰好被本次
抽查的第一个案例带偏了预期。基于这个结果，**不建议**投入自动化核对工具
（§2.3 的判断依据成立），后续按需人工抽查即可。

## 第二轮核对（扩大范围到 `docs/` 用户指南 + 代码抽查）

首轮核对范围限定在 `next_doc/`（方案文档自身状态栏 vs 正文），本轮把范围
扩大到 `docs/` 下的用户指南，并对其中列举\"具体数量/清单\"这类容易随代码
演进而静默腐化的表述做了逐条代码抽查（状态栏式的\"已完成/待评审\"表述在
首轮已核对过，本轮不重复）。

### 核对方法

`docs/` 下 goal/cron 相关指南共 11 篇（`cron-dedicated-execution-guide.md`、
`cron-jobs-reference.md`、`goal-cron-binding-guide.md`、
`goal-cycle-diagnostics-guide.md`、`goal-cycle-patrol-guide.md`、
`goal-cycle-tuning-guide.md`、`goal-execution-fairness-config.md`、
`goal-execution-phase-guide.md`、`goal-execution-spec-guide.md`、
`goal-mode-guide.md`、`goal-provenance-guide.md`）。逐篇检查开头的状态/
版本声明是否与其引用的 `next_doc/` 方案文档、以及正文里列举的具体配置项/
函数名是否在代码里仍然存在。其中 `cron-jobs-reference.md` 因为正文本身是
一份\"全部 `sys:` job 清单\"，具备可用代码直接核验的具体数字（而不是笼统的
\"已完成\"），额外用 `grep` 把文档里出现的全部 `sys:*` job_id 与
`evolution/cron_scheduler.py::_BUILTIN_JOBS` + 全代码库 `grep '"sys:'` 结果
做了逐条 diff。

### 核对结论

| 文档 | 核对结论 | 处理 |
|---|---|---|
| `cron-jobs-reference.md` | **不一致**——§1 声称\"固定内置 9 个\"，实际\n  `_BUILTIN_JOBS` 已增长到 16 个；§2\"固定内置 job\"表格、§4\"零 LLM\n  成本\"速查列表均漏列 4 个后续新增的内置 job：`sys:wiki_quarantine_\n  repair`（wiki 问题页面自动修复）、`sys:growth_advisor_daily` /\n  `sys:growth_monthly_retrospective`（成长顾问日常扫描/月度复盘）、\n  `sys:memory_backfill_scan`（记忆回填）——这 4 个 job 各自的设计方案\n  文档（`growth_advisor_*`、`memory_backfill_and_profile_update_plan.md`\n  等）落地时都提到了要\"同步更新本文档\"（见文档 §6 的维护约定），但\n  实际都没有做，是本轮唯一一处真正的\"实现已超前于文档\"案例 | **已修正**：\n  §1 数字改为 16；§2 补齐 4 行（含 schedule/LLM 成本/默认启用/目的，\n  依据 `_BUILTIN_JOBS` 源码注释逐一核实，其中 `growth_advisor_daily`/\n  `memory_backfill_scan` 判定为\"含 LLM\"——前者为高置信度候选生成调研\n  报告时可选调用 `llm_helper`，后者对待补摘要的 session 逐个调用摘要\n  生成，均非无条件调用）；§4 速查表同步补齐分类 |\n| `docs/commands-and-tools-reference.md` / `docs/self-evolution-stage9-\n  guide.md` | 两篇文档里各自嵌了一份\"固定内置 job\"简表，同样停留在\n  \"9 个\"、缺失上述 4 个 job（两处是 `cron-jobs-reference.md` 撰写时\n  复制出去的简化版本，后续 4 次新增 job 都只更新了源头一处） | **已\n  修正**：两处的数字和简表同步补齐，与 `cron-jobs-reference.md` 保持\n  一致 |\n| `cron-dedicated-execution-guide.md` / `goal-cron-binding-guide.md` /\n  `goal-cycle-diagnostics-guide.md` / `goal-cycle-patrol-guide.md` /\n  `goal-cycle-tuning-guide.md` / `goal-execution-fairness-config.md` /\n  `goal-execution-phase-guide.md` / `goal-execution-spec-guide.md` /\n  `goal-mode-guide.md` / `goal-provenance-guide.md` | 一致——开头状态\n  声明与所引用的 `next_doc/` 方案文档状态栏吻合，正文提到的配置项/\n  函数名（如 `dedupe_cron_skip_alert`、`priority_score`、\n  `CyclePatrolConfig` 各字段）经代码抽查确认存在 | 无需修改 |

### 根因与后续建议

`cron-jobs-reference.md` 这类\"汇总/索引型\"文档天然比\"单一方案的状态栏\"
更容易腐化：新增一个 `sys:` job 时，开发者会记得更新自己那份设计方案
文档的状态栏（因为改动就在那份文档描述的范围内），但容易忘记同步一份
\"汇总所有 job\"的旁支文档——尤其是当同一份汇总内容被复制到了第二、第三
个地方（`commands-and-tools-reference.md`/`self-evolution-stage9-guide.md`
里的简表）时，遗漏概率随复制份数增加。

建议：`commands-and-tools-reference.md`/`self-evolution-stage9-guide.md`
里的两份简表本身就是重复维护的根源，后续如果再次出现遗漏，可以考虑把
这两处简表精简为\"仅列不含 LLM 调用成本判断必要性的核心几条 + 一句话
指向 `cron-jobs-reference.md` 查看完整清单\"，把\"完整清单\"这个单一职责
彻底收敛到一个文件，而不是指望三处手动保持同步。`cron-jobs-reference.md`
§6 已有的\"新增 job 时同步更新本文档\"提醒，建议扩展为\"同步更新本文档 +
检查是否有其它文档复制了简化版清单\"。

## 第三轮核对：成长顾问（Growth Advisor）专题

范围：`docs/growth-advisor-guide.md`（唯一的用户指南，1700+ 行，是这条
主线里体量最大、迭代次数最多的单篇文档）+ `next_doc/` 下 16 篇
`growth_advisor_*` 方案/记录文档。核对方式沿用第二轮的方法——对"清单型"
内容（本文档的核心是 §5 配置项表、§2 各子节"新增/变更文件"清单）做
代码逐条抽查，而不是只读状态栏文字。

### 核对结论

| 检查项 | 结论 | 处理 |
|---|---|---|
| §2.1-2.24 各子节列举的函数名/配置项/测试文件（重点抽查 2.20/2.22/2.23/2.24 这几个"本次新增"标注的最新小节） | 全部与代码一致（`_infer_pursuit_style_rule`、`_should_auto_upgrade_report_quality`、`_recent_conversation_density_ratio`、`related_pursuit_directions` 等逐一在 `evolution/growth_advisor.py` 核实存在，对应测试文件也都存在） | 无需修改 |
| `GrowthAdvisorConfig`（`config/models.py`，49 个字段）与 §5"常用配置项"表格逐字段 diff | **不一致**——4 个字段在代码里真实存在且被消费，但完全没有出现在 §5 表格里，其中 3 个（`pursuit_long_unviewed_threshold`/`pursuit_self_check_every_n_cycles`/`report_external_drift_min_changes`）在全文任何位置都没有提及，对应的两个能力（方向 4"跨方向全局视角摘要"、方向 5"学习效果自测环节"，均来自 `growth_advisor_ideal_advisor_gap_and_roadmap_plan.md`，该方案文档状态栏明确写着"已落地"）在用户指南里完全没有对应小节——即"文档说这两个方向已经做完了，但去用户能看到的地方找，根本找不到" | **已修正**：新增 §2.24a 小节说明这两个能力（含 API/看板入口），§5 表格补齐 4 个字段 |
| `growth_advisor_*_plan.md`/`_implementation_record.md` 状态栏与彼此的引用关系（如 v2/v3/v4 之间、`ideal_advisor_gap_and_roadmap_plan.md` 与其 `_implementation_record.md`） | 一致，状态栏描述准确，方向级完成度标注与 `docs/growth-advisor-guide.md` 对应小节吻合（除上面提到的方向 4/5 那 2 个例外） | 无需修改 |

### 根因

跟第二轮"汇总型文档遗漏新增条目"是同一类问题的变种：`growth_advisor_
ideal_advisor_gap_and_roadmap_plan.md` 一次性规划了 6 个方向，落地时
分批次把内容写回 `docs/growth-advisor-guide.md`——方向 1/2/3/6 都各自
有对应小节（2.19/2.20/2.9 第二步等），但方向 4/5 在合并到用户指南这一步
被漏掉了，只在方案文档自己的状态栏和 `_implementation_record.md` 里
留了记录。这是"批量规划、分批落地回写文档"这种工作模式下容易出现的
遗漏模式——**不是**忘记更新索引/汇总（第二轮那种情况），而是一次改动
产出的多个子能力里，有一部分连"写回用户指南"这一步都没有执行，比第二轮
发现的问题更隐蔽（第二轮至少方案文档自己的状态栏和实现代码是同步的，
只是没同步到旁支汇总文档；这次是用户指南本身就没有这部分内容，不是
"内容存在但没被索引到"）。

### 后续维护建议

`docs/growth-advisor-guide.md` 已经积累到 24+ 个子小节、1700+ 行，单篇
文档追踪"哪些方向已经写进指南、哪些还没"本身开始变得容易出错。建议在
`growth_advisor_ideal_advisor_gap_and_roadmap_plan.md` 这类"一次规划
多个方向"的文档里，状态栏除了标注"方向 N 已落地"，可以额外加一句"已
写入用户指南 §2.X 节"/"尚未写入用户指南"，把"代码已实现"和"用户指南
已覆盖"两件事分开跟踪——本轮暴露的问题恰恰是两者被默认等同了。

## 后续维护建议（汇总）

不新增强制流程，但建议以下情况发生时顺带检查一下状态栏：

- 一份文档新增"实施记录"/"实现状态"小节时，检查最上方状态栏是否也需要
  同步更新（就是这次唯一发现的问题类型）。
- 一份文档被其它文档引用"某某已完成"时，被引用方也应该自查一下状态栏
  是否支持这个说法（本次核对采用的方法，效果不错，值得作为习惯延续）。
