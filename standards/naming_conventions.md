# mini_claude_code 目录文件管理规范

## 目的
确保项目目录结构清晰、文件分类规范，且不会被意外提交到版本控制。

## 核心原则

1. **源码优先**：`.py`、`.md`（文档）、`.yaml`（配置模板）应提交
2. **生成物不提交**：日志、缓存、临时文件、状态文件一律不提交
3. **敏感文件必须忽略**：含 API key、凭据的文件严禁提交
4. **研究数据独立管理**：`research/agent_and_ai/` 下的全部数据由子脚本统一管理，不混入主仓库

---

## 已忽略目录/文件清单（.gitignore）

### 敏感文件
| 文件 | 说明 |
|---|---|
| `providers.json` | 含所有 LLM 提供商 API key |
| `agent_api.key` | 内部认证密钥 |
| `agent_permissions.json` | 权限配置（含会话密钥） |

### Agent 运行时状态（自动生成，不需提交）
- `.agent/logs/`、`.agent/sessions/`、`.agent/workspace.db`
- `.agent/notification/`、`.agent/cron_jobs/`、`.agent/daily_reports/`
- `.agent/raw_results/`、`.agent/hybrid_exec/runs/`
- `.agent/daemon_run_state.json`、`.agent/daemon_supervisor.pid`、`.agent/daemon_crash_history.jsonl`
- `.agent/daemon.log`、`.agent/daemon_info.json`、`.agent/cron_state.db`
- `.agent/knowledge.md`、`.agent/knowledge_timeline.jsonl`、`.agent/knowledge_index.json`
- `.agent/memory.jsonl`、`.agent/llm_call_stats.jsonl`、`.agent/timeline.jsonl`
- `.agent/work_index.json`、`.agent/project.json`、`.agent/goals.json`
- `.agent/activity_digest.jsonl`、`.agent/next_actions.json`
- `.agent/phase_g_rhythm.json`、`.agent/self_maintenance_state.json`
- `.agent/suggestion_*.jsonl`、`.agent/objective_*.jsonl`、`.agent/open_threads*.json`
- `.agent/gating_history.jsonl`、`.agent/self_model_history.jsonl`
- `.agent/capability_map*.json`、`.agent/failure_pattern_store.json`
- `.agent/handoff.json`、`.agent/hooks.json`、`.agent/improvement_backlog.json`
- `.agent/persona_candidates.json`、`.agent/monthly_trend_retrospective_state.json`
- `.agent/scheduler_heartbeat_status.json`、`.agent/judge_calibration_events.jsonl`
- `.agent/capability_questions.jsonl`、`.agent/activity_digest.jsonl.bak`
- `.agent/capability_outline_suggestions.jsonl`、`.agent/memory_backfill_index.json`
- `.agent/goal_tree_decompose_*`、`.agent/initiative_push_budget_state.json`
- `.agent/kanban_*`、`.agent/health_check_result.json`
- `.agent/external_input/relevance_threshold_state.json`、`.agent/hybrid_exec/agent_sessions/`
- `.agent/capability_state.json`、`.agent/capability_persona_drafts/`
- `.agent/affordance_weights.json`、`.agent/consolidation_rhythm.json`
- `.agent/created_dirs_registry.json`、`.agent/decision_profile_state.json`
- `.agent/reports/`、`.agent/output_registry/`、`.agent/self_narrative_log.jsonl`
- `.agent/agent_commits.jsonl`、`.agent/category_catalog.json`、`.agent/classification_tree.json`
- `.agent/decision_candidates_pending.jsonl`、`.agent/entities.json`、`.agent/events.jsonl`
- `.agent/deploy_records.jsonl`、`.agent/extraction_cursor.json`、`.agent/extraction_trigger_log.jsonl`
- `.agent/async_jobs/`、`.agent/artifacts_index.jsonl`
- `.agent/proxy/`（代理池缓存）
- `.agent/evolution/`（进化分支状态）
- `.agent/sub_agent_experience_log.jsonl`、`.agent/scripts/`
- `.agent/protected_backup/`、`.agent/last_sync`

### 研究数据目录（独立管理）
- `research/agent_and_ai/` — Agent & AI 知识库，由 cron 脚本自动维护，不提交
- `research/agent_and_ai_advice/` — 建议文件，不提交

### 临时/输出目录
- `temp/`、`temp_data/`、`output/`、`output_projects/`、`daily_reports/`
- `.agent/temp/`、`.agent/output/`

### 测试相关
- `test_result/`、`work_cmds/`
- `test_cases/inputs/character-*.png`、`test_cases/inputs/keyframe_first.png`

### 其他生成物
- `__pycache__/`、`src/mini_agent.egg-info/`
- `.agent/archive/`、`.agent/capability_ledger/`、`.agent/cycle_tuning_proposals/`
- `.agent/goal_execution_phase/`、`.agent/goal_execution_specs/`
- `.agent/external_input/state/`、`.agent/workflow_sessions/`
- `.claude/logs/`、`.claude/skills/browser-cdp/temp_*`、`.claude/skills/wiki-auto-maintenance/`
- `.claude/skills/skill-creator/`、`.agent/knowledge.md`、`.agent/event_cursors/`
- `.agent/wiki/`、`.agent/workflows/zhihu_content_publish_v2/`
- `examples/_hybrid_exec_demo_workspace/`（含真实 providers.json 副本）
- `apps/mini_agent_kanban/.kanban_prefs.json`、`external_projects/novel_writer/.agent/`
- `external_projects/stock_watch/reports/`

---

## 规范化的 `.agent/` 结构（应提交）

以下子目录被规范地保留并提交（作为 Agent 行为模板，非生成数据）：

| 路径 | 用途 | 类型 |
|---|---|---|
| `.agent/agents/*.md` | 子 Agent 角色定义 | 源码 |
| `.agent/personas/*.md` | 人设模板 | 源码 |
| `.agent/external_input/policies.yaml` | 外部输入策略 | 配置 |
| `.agent/external_input/sources.yaml` | 数据源定义 | 配置 |
| `.agent/external_input/watchlist.yaml` | 关注列表配置 | 配置 |
| `.agent/hooks/auto_reply_inputs.txt` | 自动回复输入模板 | 源码 |
| `.agent/hooks/turn_end_auto_reply.py` | 轮次结束钩子逻辑 | 源码 |
| `.agent/hooks/turn_end_notify.py` | 通知钩子逻辑 | 源码 |
| `.agent/notification/report_tiers.yaml` | 通知分层配置 | 配置 |
| `.agent/notification/config.yaml.example` | 配置示例 | 源码 |
| `.agent/last_sync` | 同步时间戳 | 配置 |

---

## 提交前检查清单

1. `git status --short` 查看新增/修改文件
2. 确认不含 `agent_api.key`、`providers.json`、`.agent/notification/config.yaml`（实际配置，非示例）
3. 确认不含 `.agent/cron_jobs/`、`.agent/daily_reports/`、`.agent/raw_results/` 等生成物目录
4. 确认不含 `research/agent_and_ai/` 下的任何文件
5. 新代码目录遵循上述规范化结构

---

## 违规处理流程

若发现敏感文件被错误提交：

1. 从索引移除（保留本地文件）：
   ```bash
   git rm --cached <file>
   ```
2. 在 `.gitignore` 中添加该文件规则
3. 若已推送到远程，使用 `git filter-repo` 或 `git filter-branch` 清除历史中的泄露记录
4. 立即轮换受影响的 API key

---

## 更新记录

| 日期 | 内容 |
|---|---|
| 2026-11-02 | 首次建立：整理全量 `.gitignore` 条目并归类，定义 `.agent/` 规范化结构，添加提交前检查清单和违规处理流程 |
