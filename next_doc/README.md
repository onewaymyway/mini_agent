# next_doc/ 索引

> `next_doc/` 存放"在研/演进中"的设计方案、改进计划、实施记录，与
> `docs/`（面向读者的稳定功能指南）定位不同——具体区分规则见
> [文档治理规范](../docs/documentation-guidelines.md)。
>
> 本索引按"能力主线"分组（而非文件名字母序），每条主线内部按大致时间线
> （早 → 晚）列出文档，并标注性质：
> - `设计` = 首次方案设计（通常文件名含 `_design`）
> - `计划` = 改进/迭代计划（`_plan`/`_improvement_plan`）
> - `实施记录` = 落地后的实施总结（`_implementation_record`）
> - `修复` = 针对具体 bug 的记录（`_bugfix`/`_fix`）
> - `复盘` = 回顾/审计类（`_review`/`_audit_record`）
>
> **同一主线的文档不代表后者废弃前者**——多数是"设计 → 若干版本迭代计划 →
> 实施记录"的演进链条，读的时候建议按本索引列出的顺序从上往下看。

---

## goal_cron（Goal 与 Cron 绑定/调度，约 13 篇）

Goal（长期目标）与 Cron（定时任务）之间的绑定关系、统一调度器、可观测性。

1. `goal_cron_binding_plan.md` — 计划：Goal 与 Cron 绑定机制
2. `goal_cron_binding_implementation_record.md` — 实施记录
3. `goal_cron_unified_scheduler_improvement_plan.md` — 计划：统一调度器
4. `goal_cron_unified_scheduler_implementation_record.md` — 实施记录
5. `goal_cron_output_directory_convention_plan.md` — 计划：输出目录约定
6. `goal_cron_feedback_and_output_policy_plan.md` — 计划：反馈与输出策略
7. `goal_cron_visibility_and_intervention_improvement_plan.md` — 计划：可观测性与人工干预
8. `goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md` — 计划：周期诊断与交互式调优
9. `goal_cron_cycle_proactive_patrol_and_health_overview_plan.md` — 计划：主动巡检与健康总览
10. `goal_cron_convergence_and_governance_improvement_plan.md` — 计划：收敛与治理
11. `goal_cron_task_optimization_holistic_plan.md` — 计划：任务优化整体方案
12. `goal_cron_docs_reorganization_and_system_state_review.md` — 复盘：文档重组与系统状态审视（本次治理计划参考的前例之一）
13. `goal_cron_docs_status_audit_record.md` — 复盘：文档状态核对记录

## goal（Goal 执行本体，非 cron 绑定部分，约 13 篇）

Goal/Objective 执行阶段、公平调度、输出目录、进度判定。

1. `goal_execution_phase_improvement_plan.md` — 计划：执行阶段划分
2. `goal_execution_phase_stage_d_implementation_record.md` — 实施记录：Stage D
3. `goal_execution_spec_generation_plan.md` — 计划：执行 spec 生成
4. `goal_execution_spec_generation_implementation_record.md` — 实施记录
5. `goal_execution_fairness_improvement_plan.md` — 计划：公平性改进
6. `goal_fairness_scheduling_diagnostics_plan.md` — 计划：公平调度诊断
7. `goal_execution_scheduling_global_cap_bugfix.md` — 修复：全局并发上限
8. `goal_output_directory_and_execution_phase_redesign_plan.md` — 计划：输出目录与执行阶段重设计
9. `goal_user_output_dir_implementation_record.md` — 实施记录：用户输出目录
10. `goal_mode_completion_improvement_plan.md` — 计划：完成度判定改进
11. `goal_mode_stage2_ensemble_and_fine_grained_plan.md` — 计划：Stage2 集成与细粒度
12. `goal_mode_stuck_compact_plan.md` — 计划：卡滞时的压缩策略
13. `goal_stuck_stats_and_llm_progress_judge_plan.md` — 计划：卡滞统计与 LLM 进度判定

## growth_advisor（成长顾问，19 篇）

扫描记忆信号、生成成长方向候选、调研报告、自主搜索与生命周期管理。

1. `growth_advisor_design.md` — 设计：初始方案
2. `growth_advisor_implementation_record.md` — 实施记录：初版
3. `growth_advisor_improvement_plan_v2.md` — 计划 v2
4. `growth_advisor_improvement_plan_v3.md` — 计划 v3
5. `growth_advisor_improvement_plan_v4.md` — 计划 v4
6. `growth_advisor_autonomous_search_and_material_improvement_plan.md` — 计划：自主搜索与素材改进
7. `growth_advisor_autonomy_deepening_plan.md` — 计划：自主性深化
8. `growth_advisor_autonomy_deepening_plan_v2.md` — 计划：自主性深化 v2
9. `growth_advisor_active_search_and_lifecycle_plan.md` — 计划：主动搜索与候选生命周期
10. `growth_advisor_active_search_and_lifecycle_implementation_record.md` — 实施记录
11. `growth_advisor_cron_search_and_status_history_plan.md` — 计划：Cron 搜索与状态历史
12. `growth_advisor_goal_cron_integration_plan.md` — 计划：与 Goal/Cron 集成
13. `growth_advisor_research_quality_plan.md` — 计划：调研报告质量
14. `growth_advisor_diagnostics_and_language_fix_plan.md` — 计划：诊断与语言修复
15. `growth_advisor_ideal_advisor_gap_and_roadmap_plan.md` — 计划：理想顾问差距与路线图
16. `growth_advisor_ideal_advisor_gap_and_roadmap_implementation_record.md` — 实施记录
17. `growth_diagnostics_backfill_count_cache_plan.md` — 计划：诊断回填计数缓存
18. `growth_summary_client_timeout_fix.md` — 修复：摘要客户端超时
19. `growth_advisor_docs_reorganization_and_system_state_review.md` — 复盘：文档重组与系统状态审视（本次治理计划参考的前例之一）

## wiki（Wiki 式知识库，约 14 篇）

知识提取、组织层、缺口扫描、兜底清理。

1. `wiki-style-knowledge-base-improvement-plan.md` — 计划：初版改进
2. `wiki-style-knowledge-base-refactor-plan.md` — 计划：重构
3. `wiki-knowledge-base-extraction-and-organization-plan.md` — 计划：提取与组织层
4. `wiki-extraction-layer-plan-e1-record.md` ~ `-e3-record.md`、`-o1-record.md` ~ `-o4-record.md` — 提取层改进计划分批（E/O 两条子方案）各阶段实施记录，共 7 篇
5. `wiki_next_phase_improvement_plan.md` — 计划：下一阶段
6. `wiki_next_phase_implementation_record.md` — 实施记录
7. `capability_wiki_freshness_improvement_plan.md` — 计划：能力 wiki 新鲜度（与 `sys:wiki_gap_scan`/`sys:wiki_fallback_cleanup` 两个内置 cron job 对应，见 [定时任务完整参考](../docs/cron-jobs-reference.md)）
8. `external_knowledge_wiki_and_self_improvement_plan.md` — 计划：外部知识 wiki 化与自我改进闭环

## daemon（常驻守护进程，9 篇）

daemon 执行模型、多用户隔离、稳定性、状态恢复、任务卡死看护。

1. `autonomous_daemon_design.md` — 设计：自主 Daemon（注意：`docs/` 下同名的
   `docs/autonomous-daemon-design.md` 是本篇设计落地后的实现说明，本轮已将
   `docs/` 下那份改名为 kebab-case，`next_doc/` 这份维持原文件名不变，见文末
   "本轮已处理事项"）
2. `daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md` — 计划：执行模型与调度心跳
3. `global_daemon_isolation_design.md` — 设计：全局隔离
4. `global_daemon_isolation_implementation_plan.md` — 计划：隔离实施
5. `daemon-multiuser-architecture.md` — 设计：多用户架构
6. `daemon-multiuser-implementation-design.md` — 设计：多用户实施细化
7. `daemon_autonomous_state_recovery_plan.md` — 计划：自主状态恢复
8. `daemon_stability_and_ux_improvement_plan.md` — 计划：稳定性与体验改进
9. `daemon_stability_and_ux_improvement_implementation_record.md` — 实施记录
10. `daemon_task_hang_recovery_and_watchdog_hardening_plan.md` — 计划：任务卡死恢复与看门狗加固

## embodied_agent（具身智能，3 篇，注意版本迭代关系）

**注意**：v2/v3 是对同一 `embodied_agent_design.md` 的连续迭代计划，不是互相独立的分支；阅读顺序即版本顺序。

1. `embodied_agent_design.md` — 设计：初版
2. `embodied_agent_improvement_plan_v2.md` — 计划 v2
3. `embodied_agent_improvement_plan_v3.md` — 计划 v3

## external_input（外部输入网关，约 5 篇）

外部消息接入的分页、可靠性、可观测性、归档。

1. `external_input_pagination_plan.md` — 计划：分页
2. `external_input_reliability_observability_archive_plan.md` — 计划：可靠性/可观测性/归档
3. `external_input_reliability_observability_archive_implementation_record.md` — 实施记录
4. `external_knowledge_feedback_loop_improvement_plan.md` — 计划：外部知识反馈闭环（与 [定时任务完整参考 §3.2](../docs/cron-jobs-reference.md) 的 P1-P5 job 对应）

## generative_capability（生成式能力探索，约 4 篇）

Explorer 重构、原始结果与混合合并、三层改进、trace 回放。

1. `generative-capability-skill-plan.md` — 计划：Skill 化
2. `generative_capability_explorer_rearch_plan.md` — 计划：Explorer 重构
3. `generative_capability_raw_result_and_hybrid_merge_plan.md` — 计划：原始结果与混合合并
4. `generative_capability_three_tier_improvement_plan.md` — 计划：三层改进
5. `generative_capability_trace_replay_and_allowlist_plan.md` — 计划：trace 回放与白名单

## kanban（看板，约 14 篇 + 2 篇中文文件名）

看板前端、异步任务机制、并发控制、配置管理、感知缺口。

1. `kanban_feature_inventory.md` — 功能盘点（作为其余 kanban 文档的入口）
2. `kanban_react_spa_replacement_plan.md` — 计划：React SPA 重写
3. `kanban_async_job_mechanism_plan.md` — 计划：异步任务机制
4. `kanban_concurrency_control_plan.md` — 计划：并发控制
5. `kanban_config_management_plan.md` — 计划：配置管理
6. `kanban_cron_delete_consistency_bugfix.md` — 修复：Cron 删除一致性
7. `kanban_goal_delete_and_bulk_delete_plan.md` — 计划：Goal 删除与批量删除
8. `kanban_execution_visibility_and_control_plan.md` — 计划：执行可见性与控制
9. `kanban_scheduling_mode_visibility_improvement.md` — 计划：调度模式可见性
10. `kanban_perception_gaps_improvement_plan.md` — 计划：感知缺口改进
11. `kanban_perception_gaps_implementation_record.md` — 实施记录
12. `kanban_and_autonomy_improvement_plan.md` — 计划：看板与自主性联动改进
13. `kanban_and_autonomy_improvement_implementation_record.md` — 实施记录
14. `scheduling_unification_and_kanban_visibility_improvement_plan.md` — 计划：调度统一与看板可观测性（P1-P5，见 [定时任务完整参考 §7](../docs/cron-jobs-reference.md)）
15. `kanban-main-interaction-ui-improvement-plan.md` — 计划：主交互界面
16. `kanban-large-data-pagination-improvement-plan.md` — 计划：大数据量分页

## workflow（工作流引擎，约 15 篇）

工作流机制的多轮迭代（P9-P15）、目录模式、Python 步骤。

1. `session_to_workflow_design.md` — 设计：从 session 到 workflow 的起点
2. `workflow_mechanism_improvement_proposal.md` — 计划：机制改进提案（早期）
3. `workflow_mechanism_improvement_plan.md` — 计划：机制改进（基础版）
4. `workflow_directory_mode_design.md` — 设计：目录模式
5. `workflow_authoring_guide.md` — 编写指南（计划性质，非 `docs/` 稳定指南）
6. `workflow_system_p9_implementation_record.md` — 实施记录：P9
7. `workflow_mechanism_improvement_plan_p10.md` — 计划：P10
8. `workflow_input_passing_and_debug_logging_improvement_plan.md` — 计划：输入传递与调试日志
9. `workflow_mechanism_improvement_plan_p12.md` — 计划：P12
10. `workflow_mechanism_improvement_plan_p13.md` — 计划：P13
11. `workflow_mechanism_improvement_plan_p14.md` — 计划：P14
12. `workflow_mechanism_improvement_plan_p15.md` — 计划：P15
13. `workflow_python_step_and_zhihu_publish_plan.md` — 计划：Python 步骤与知乎发布
14. `workflow_python_step_and_zhihu_publish_implementation_record.md` — 实施记录
15. `workflow_system_next_directions.md` — 复盘：后续方向
16. `workflow-mechanism-improvement-plan-early.md` — 计划：早期版本（`workflow_mechanism_improvement_proposal.md` 之前的最初方案）

---

## 其他（零散文档，未细分主线）

以下文档暂未归入以上任一主线分组，按主题松散排列，后续按需细化：

**自我演化与能力学习**
- `self_evolution_design.md` — 设计
- `self_evolution_implementation_plan.md` — 计划
- `self_evolution_stage4plus_plan.md` — 计划：Stage4+
- `self_evolution_stage9_plan.md` — 计划：Stage9
- `persona_capability_learning_design.md` — 设计：人设能力学习
- `roleplay_persona_design.md` — 设计：角色扮演人设
- `decision-tradeoff-knowledge-extraction-plan.md` — 计划：决策取舍知识提炼
- `self_diagnosis_feedback_loop_deepening_plan.md` — 计划：自诊断闭环深化（见 [定时任务完整参考 §3.3](../docs/cron-jobs-reference.md)）
- `system_connectivity_gaps_and_missing_capabilities_plan.md` — 计划：系统关联性断点与缺失能力
- `proactive-recommendation-and-digital-persona-design.md` — 设计：主动推荐与数字分身机制
- `watchlist_notification_goal_design.md` — 设计：关注对象分级通知
- `work_index_proactive_reminder_design.md` — 设计：工作索引主动提醒
- `memory_backfill_and_profile_update_plan.md` — 计划：记忆回填与用户画像更新

**Cron 专属执行机制**
- `cron_dedicated_execution_improvement_plan.md` — 计划
- `cron_dedicated_execution_implementation_record.md` — 实施记录
- `cron_run_debug_detail_improvement_plan.md` — 计划：运行调试详情

**执行/压缩/统一化基础设施**
- `compact_mechanism_improvement_plan.md` — 计划：压缩机制
- `hybrid_exec_design_plan.md` — 设计+计划：混合执行
- `hybrid_exec_improvement_directions.md` — 分析：外部项目复用与执行载体生成方向
- `hybrid_exec_external_integration_implementation_record.md` — 实施记录：A1/A2/B1/A3/B2 落地
- `judge_unification_design.md` — 设计：判定器统一
- `judge_profile_unification_migration_plan.md` — 计划：判定画像统一迁移
- `llm_helper_unification_plan.md` — 计划：LLM helper 统一
- `flat_nested_config_unification_migration_plan.md` — 计划：扁平/嵌套配置统一迁移
- `dataclass_field_migration_checklist.md` — 迁移检查清单
- `four_priority_improvements_design.md` — 设计：四项优先改进
- `priority_improvements_implementation_plan.md` — 计划：优先改进实施
- `p8_p9_config_toggle_and_cli_hint_record.md` — 实施记录：P8/P9 配置开关与 CLI 提示
- `cross_goal_experience_reuse_plan.md` — 计划：跨 Goal 经验复用
- `cycle_tuning_nl_continuity_fix_implementation_record.md` — 实施记录：周期调优自然语言连续性修复
- `extraction_window_oversize_chunking_fix.md` — 修复：抽取窗口超限分块
- `session_cleanup_design.md` — 设计：Session 清理（对应 `sys:session_cleanup`）
- `session_list_blocking_and_cache_fix.md` — 修复：session 列表阻塞与缓存

**运维/稳定性小修复**
- `browser_cdp_stability_fixes.md` — 修复：浏览器 CDP 稳定性
- `errors_tool_executor_log_toggle_plan.md` — 计划：工具执行器日志开关
- `http_server_blocking_call_guard_plan.md` — 计划：HTTP server 阻塞调用守卫

**接入与其他**
- `weixin_mini_agent_design.md` — 设计：微信接入
- `future_todos.md` — 待办清单（无固定结构，持续追加）
- `docs_governance_reorganization_plan.md` — 本次文档治理与重组计划本身

---

## 已处理事项（对应 `docs_governance_reorganization_plan.md`）

- 步骤二：同步了 `docs/cron-jobs-reference.md`、
  `docs/commands-and-tools-reference.md`、
  `docs/self-evolution-stage9-guide.md` 三处内置 cron job 清单（16→18 个，
  补齐 `sys:capability_learning_cycle`/`sys:capability_question_sweep` 等
  遗漏条目）；修正了 `docs/cron-dedicated-execution-guide.md` 的错误链接
  文字。
- 步骤三：`docs/format-correction-detector-update.md` 已合并进
  `docs/tool-call-format-correction.md`；`docs/project-readme.md` 内容已
  被根 `README.md` + `docs/code-structure-guide.md` 完整覆盖，已删除。
- 步骤四：`docs/autonomous_daemon_design.md` 已重命名为
  `docs/autonomous-daemon-design.md`，所有反向引用已更新（`next_doc/` 下
  同名但内容不同的 `autonomous_daemon_design.md` 未改动，两者是"设计方案
  → 实现说明"关系，不是同一份文件）。
- 步骤五：`next_doc/` 下 15 篇 + `docs/` 下 1 篇中文文件名文档，已全部
  改为英文 kebab-case 文件名（标题正文保持中文不变），本索引里出现的文件
  名即改名后的最终结果。改名的直接动因：这批中文文件名在部分压缩/传输
  环境（尤其是不区分大小写或用非 UTF-8 文件名编码处理 zip 的工具链）下会
  被转成 `#Uxxxx` 形式的 URL 编码转义乱码——`docs_governance_reorganization_
  plan.md` §0 第 8 条记录的问题，实测比该条目最初记录的 14 篇多一篇（应为
  15 篇）。全项目所有反向引用（含 `README.md`"必读"链接、`CLAUDE.md`、
  `docs/wiki-knowledge-base-guide.md` 等）已同步更新为新文件名。
