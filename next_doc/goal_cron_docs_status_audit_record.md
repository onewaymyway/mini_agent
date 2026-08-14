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

## 后续维护建议

不新增强制流程，但建议以下情况发生时顺带检查一下状态栏：

- 一份文档新增"实施记录"/"实现状态"小节时，检查最上方状态栏是否也需要
  同步更新（就是这次唯一发现的问题类型）。
- 一份文档被其它文档引用"某某已完成"时，被引用方也应该自查一下状态栏
  是否支持这个说法（本次核对采用的方法，效果不错，值得作为习惯延续）。
