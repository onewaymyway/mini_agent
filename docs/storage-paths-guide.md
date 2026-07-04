# 存储路径管理指南

> 本文档介绍 `storage/paths.py` 中 `AgentPaths` 类的架构、作用域层次和使用方式。

---

## 1. 模块概述

`storage/paths.py` 提供统一的**路径管理**，所有文件路径都从这里取，不在各模块中硬编码。

**核心类**：`AgentPaths`

**作用域层次**：

| 作用域 | 路径 | 说明 |
|--------|------|------|
| Global（用户级） | `~/.agent/` | 跨项目共享的全局数据 |
| Workdir（项目级） | `<project_root>/.agent/` | 当前项目的数据 |
| Session（会话级） | `<project_root>/.agent/sessions/<session_id>/` | 单个会话的数据 |
| Task（任务级） | `<project_root>/.agent/sessions/<session_id>/tasks/<task_id>/` | 单个 Sub-Agent 任务的数据 |

---

## 2. 使用方式

```python
from mini_agent.storage.paths import AgentPaths

paths = AgentPaths(project_root=Path.cwd())

# 获取各种路径
print(paths.workdir_memory)       # .agent/memory.jsonl
print(paths.sessions_dir)         # .agent/sessions/
print(paths.session_history("abc"))  # .agent/sessions/abc/history.json
```

---

## 3. Global 级路径

| 属性 | 路径 | 说明 |
|------|------|------|
| `global_dir` | `~/.agent/` | 全局目录 |
| `global_memory` | `~/.agent/memory.jsonl` | 全局记忆（跨项目通用经验） |
| `global_skills_dir` | `~/.agent/skills/` | 全局技能库 |
| `global_prompts_dir` | `~/.agent/prompts/` | 全局自定义 prompt 目录 |
| `global_self_profile` | `~/.agent/self_profile.json` | Agent 自我模型（W3） |
| `global_projects_index` | `~/.agent/projects_index.json` | 项目注册表（W3） |
| `global_cross_project_index` | `~/.agent/cross_project_index.json` | 跨项目模式（W3） |
| `global_activity_log` | `~/.agent/activity_log.jsonl` | 全局活动日志（W3） |
| `global_hooks_config` | `~/.agent/hooks.json` | 全局 hooks 配置 |
| `global_agents_dir` | `~/.agent/agents/` | 全局自定义子 agent 目录 |
| `profile_path(user_id)` | `~/.agent/profile.json` | 用户画像文件 |

---

## 4. Workdir 级路径

| 属性 | 路径 | 说明 |
|------|------|------|
| `workdir_dir` | `<project>/.agent/` | 项目级目录 |
| `workdir_memory` | `<project>/.agent/memory.jsonl` | 项目级记忆 |
| `workdir_prompts_dir` | `<project>/.agent/prompts/` | 项目级自定义 prompt |
| `permissions` | `<project>/.agent/permissions.json` | 权限白名单/黑名单 |
| `workdir_project_meta` | `<project>/.agent/project.json` | 项目身份证（W2） |
| `workdir_timeline` | `<project>/.agent/timeline.jsonl` | Session 时序骨架（W2） |
| `workdir_work_index` | `<project>/.agent/work_index.json` | 跨 session WorkThread 聚合（W2） |
| `workdir_open_threads` | `<project>/.agent/open_threads.json` | 跨 session 待处理线索（W2） |
| `workdir_knowledge_md` | `<project>/.agent/knowledge.md` | 项目软知识（W2） |
| `workdir_knowledge_index` | `<project>/.agent/knowledge_index.json` | 知识索引（W2） |
| `workdir_cognitive_anchor` | `<project>/.agent/cognitive_anchor.md` | 认知锚点文件（具身改进） |
| `sessions_dir` | `<project>/.agent/sessions/` | Session 根目录 |
| `cache_dir` | `<project>/.agent/cache/` | 可安全删除的缓存 |
| `tool_cache` | `<project>/.agent/cache/tool_cache.json` | 工具结果缓存 |
| `project_hooks_config` | `<project>/.agent/hooks.json` | 项目级 hooks 配置 |
| `project_agents_dir` | `<project>/.agent/agents/` | 项目级自定义子 agent 目录 |

---

## 5. Session 级路径

| 方法 | 路径 | 说明 |
|------|------|------|
| `session_dir(sid)` | `.../sessions/<sid>/` | Session 目录 |
| `session_history(sid)` | `.../history.json` | 完整对话历史 |
| `session_meta(sid)` | `.../meta.json` | Session 元信息 |
| `session_llm_debug(sid)` | `.../llm_debug.jsonl` | LLM 调试日志 |
| `session_memory_delta(sid)` | `.../memory_delta.jsonl` | Session 记忆条目（审计用） |
| `session_plan_snapshot(sid)` | `.../plan_snapshot.json` | ExecutionPlan 持久化快照（W1） |
| `session_goal_state(sid)` | `.../goal_state.json` | Goal 模式运行状态 |
| `session_traces(sid)` | `.../traces.jsonl` | 时序追踪记录（Stage 6.1） |
| `tasks_dir(sid)` | `.../tasks/` | 任务目录根 |

---

## 6. Task 级路径

| 方法 | 路径 | 说明 |
|------|------|------|
| `task_dir(sid, tid)` | `.../tasks/<tid>/` | 任务目录 |
| `task_output(sid, tid)` | `.../output.log` | SubAgent 实时输出流 |
| `task_events(sid, tid)` | `.../events.jsonl` | 任务生命周期事件 |
| `task_result(sid, tid)` | `.../result.json` | 任务完成结果 |
| `task_manifest(sid, tid)` | `.../manifest.json` | 任务全生命周期叙事文件（W1） |

---

## 7. 便捷方法

| 方法 | 说明 |
|------|------|
| `ensure_session_dir(sid)` | 确保 session 目录存在并返回路径 |
| `ensure_task_dir(sid, tid)` | 确保 task 目录存在并返回路径 |
| `ensure_workdir()` | 确保 `.agent/` 目录存在并返回路径 |
| `ensure_global_dir()` | 确保 `~/.agent/` 目录存在并返回路径 |

---

## 8. 相关文档

- [系统设计概述](system-overview.md) — 整体架构与各子系统关系
- [W2/W3 知识层指南](self-evolution-stage4-5-guide.md) — Workdir/Global 知识层详细说明
- [Plan 与 Task 指南](plan-and-task-guide.md) — 执行计划与任务管理机制

---

*最后更新：2026-07*