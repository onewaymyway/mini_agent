# Workdir 知识层与 Global 知识层指南（Stage 4 & 5）

> 对应 `next_doc/self_evolution_stage4plus_plan.md` Stage 4（Phase W2）与 Stage 5（Phase W3），
> 设计依据 `next_doc/self_evolution_design.md` 第 8.2 节（W2）与第 8.3 节（W3）。

---

## 1. 两层知识层概述

```
W2  Workdir 知识层（项目级）
  .agent/
  ├── project.json          # 项目身份证 + 环境指纹（4.1）
  ├── timeline.jsonl        # session 时间线（4.2）
  ├── work_index.json       # 工作线索索引（4.3）
  ├── open_threads.json     # 跨 session 待处理线索池（4.4）
  └── knowledge_index.json  # 结构化知识索引（4.5）

W3  Global 知识层（用户级，跨项目）
  ~/.agent/
  ├── self_profile.json          # Agent 自我画像（5.1）
  ├── projects_index.json        # 已知项目注册表（5.2）
  ├── cross_project_index.json   # 跨项目模式（5.3）
  └── activity_log.jsonl         # 全局活动日志（5.4）
```

两层之间的关系：**Workdir 层记录"这个项目正在发生什么"，Global 层记录"我（agent）在多个项目里学到了什么"**。Stage 4-5 是纯粹的观察和数据沉淀层，所有写操作都是"增量追加 + 原子替换"，不修改 agent 对话逻辑，不产生自主行为。

---

## 2. W2：Workdir 知识层（Stage 4）

### 2.1 project.json — 项目身份证

**路径**：`.agent/project.json`

**触发时机**：每次 session 启动时调用 `ensure_project_meta()`，首次自动创建，后续更新 `last_active_at` 和 `session_count`。

**核心字段**：

```json
{
  "project_id": "sha256前8位",
  "name": "mini_agent",
  "language": "python",
  "created_at": "2026-06-20T10:00:00Z",
  "last_active_at": "2026-06-22T14:30:00Z",
  "session_count": 42,
  "environment_fingerprint": {
    "python_version": "3.12.3",
    "dependencies": ["anthropic", "fastapi", "rich"],
    "os": "Linux",
    "captured_at": "2026-06-22T14:30:00Z"
  }
}
```

**环境漂移检测（12.2）**：`detect_environment_drift(old_fp, new_fp)` 比较两次 fingerprint，若 Python 版本或主要依赖发生变化，会在 context 里注入漂移警告，提示旧经验可能失效。

### 2.2 timeline.jsonl — session 时间线

**路径**：`.agent/timeline.jsonl`

每次 session 结束时追加一条记录，记录"这个项目的发展历史"：

```json
{
  "ts": "2026-06-22T14:30:00Z",
  "session_id": "abc123",
  "duration_min": 8.3,
  "theme": "重构 auth 模块",
  "tool_calls": 24,
  "tokens": 58000,
  "key_decisions": ["使用 async/await 替换同步 IO"]
}
```

`theme` 字段由 LLM 从对话历史里简短总结（一句话），是 timeline 的核心。

### 2.3 work_index.json — 工作线索索引

**路径**：`.agent/work_index.json`

记录项目里正在进行的"工作线索"（WorkThread）——类似 GitHub Issue 的轻量版：

```json
{
  "threads": [
    {
      "id": "wt-001",
      "title": "auth 模块重构",
      "status": "in_progress",
      "created_at": "2026-06-20T10:00:00Z",
      "last_active_at": "2026-06-22T14:30:00Z",
      "related_sessions": ["abc123", "def456"],
      "summary": "将同步 IO 改为 async/await，目前已完成 login 流程"
    }
  ]
}
```

### 2.4 open_threads.json — 跨 session 待处理线索池

**路径**：`.agent/open_threads.json`

记录"上次 session 没处理完、下次需要跟进"的线索，是 **W2 最重要的 context 注入来源**之一：

```json
{
  "items": [
    {
      "id": "ot-001",
      "priority": "high",
      "summary": "task_manager.py 第 300 行疑似有竞态条件",
      "source": "session_abc123",
      "created_at": "2026-06-22T14:30:00Z",
      "status": "open"
    }
  ]
}
```

`priority` 枚举：`high` / `medium` / `low`。Session 启动时自动把 `high` 优先级的 open_threads 注入 system prompt，帮助 agent 记起上次未完成的事。

**更新时机**：
- `session_end` 时，agent 从 task manifest 的 `unresolved` 字段自动导入未解决问题
- 工具 `add_open_thread` / `resolve_open_thread` 可在对话中手动管理

### 2.5 knowledge_index.json — 结构化知识索引

**路径**：`.agent/knowledge_index.json`

`update_knowledge` 工具写入的知识片段同时在这里建立索引，支持不读全文就能快速扫描。实际字段（与 `KnowledgeIndexEntry` 一致）：

```json
{
  "last_indexed": 1750000000.0,
  "entries": [
    {
      "id": "kn_001",
      "heading": "auth 模块 API 说明",
      "summary": "描述了 login/logout/refresh 三个端点的参数和返回值",
      "topic": "auth",
      "decision_type": "convention",
      "affected_modules": ["auth/api.py"],
      "created_at": 1750000000.0
    }
  ]
}
```

**检索**：索引建立之后并非只能被动等待 always-on 注入——`search_knowledge` 工具（对应
`perception/workdir_knowledge.py` 的 `search_knowledge_index()`）让 agent 按关键词主动检索：

```python
search_knowledge(query="为什么用 SQLite 而不是 Postgres")
# → {"ok": true, "results": [{"id": "kn_003", "heading": "数据库选型",
#     "summary": "...", "score": 2.31, ...}]}

search_knowledge(query="鉴权 token 刷新", include_content=True)
# include_content=True 时额外带出 knowledge.md 里该 section 的完整正文
# （通过 read_knowledge_section() 按标题取出，避免一次性把全文塞进结果）

search_knowledge(query="集成方式", topic="mcp")
# topic 做精确过滤，缩小到指定主题范围内再排序
```

评分用 TF-IDF（复用 `perception/memory_store.py` 的中英混合分词器，含中文 n-gram），
不需要向量数据库；候选条目数量级通常只有几十到几百条，关键词检索已经够用。

### 2.6 context 注入时机

以下 W2 数据会在 session 启动时注入 system prompt：

| 数据 | 注入条件 | 字段 |
|------|---------|------|
| project.json 基本信息 | 始终注入 | `WorkdirKnowledgeConfig.enabled` |
| timeline 最近 N 条 | 始终注入 | `WorkdirKnowledgeConfig.timeline_inject_limit`（默认 5）|
| open_threads（high 优先级） | 有 high 线索时 | `WorkdirKnowledgeConfig.open_threads_inject_limit`（默认 5）|
| 环境漂移警告 | fingerprint 有变化时 | 自动 |

`knowledge.md` / `knowledge_index.json` **不在**上表的 always-on 注入范围内——
内容量级（可能积累到几十甚至上百个 section）不适合每个 turn 都塞进 system
prompt，而是按 8.4 节设计的"按本次 session 意图检索后注入"模式：agent 需要
时主动调用 `search_knowledge` 工具去查，而不是依赖 system prompt 自动出现。

---

## 3. W3：Global 知识层（Stage 5）

### 3.1 self_profile.json — Agent 自我画像

**路径**：`~/.agent/self_profile.json`

记录"我是谁"——跨项目的、关于 agent 自身能力和状态的持久化认知：

```json
{
  "identity": {
    "agent_name": "mini_agent",
    "version": "0.1.0",
    "created_at": "2026-06-01T00:00:00Z"
  },
  "assessment": {
    "total_sessions": 156,
    "total_projects": 8,
    "capability_summary": "擅长 Python 后端开发和重构，对 Docker 部署有中等把握",
    "confidence_by_domain": {
      "python": 0.92,
      "bash_scripting": 0.78,
      "devops": 0.61
    }
  },
  "operating_state": {
    "last_active_project": "mini_agent",
    "consecutive_days": 12
  },
  "evolution_state": {
    "pending_evolve_branches": ["evolve/20260620-skill-bash-safety"],
    "last_lesson_review_at": "2026-06-21T10:00:00Z"
  }
}
```

**更新时机**：每次 session 结束时通过 `update_self_profile_on_session_end()` 自动更新。

### 3.2 projects_index.json — 已知项目注册表

**路径**：`~/.agent/projects_index.json`

首次进入一个新项目时，自动把项目信息注册进来：

```json
{
  "projects": [
    {
      "project_id": "abc8de12",
      "name": "mini_agent",
      "path": "/home/user/mini_agent",
      "language": "python",
      "last_active_at": "2026-06-22T14:30:00Z",
      "session_count": 42,
      "is_dormant": false
    }
  ]
}
```

`is_dormant`：超过 `GlobalKnowledgeConfig.dormant_after_days`（默认 30 天）未活跃的项目会被标记为休眠，在 context 注入时降低权重。

### 3.3 cross_project_index.json — 跨项目模式

**路径**：`~/.agent/cross_project_index.json`

由 Stage 5.4 的 `scan_cross_project_patterns()` 扫描并聚合产生，记录"在多个项目里都出现的规律"：

```json
{
  "cross_project_patterns": [
    {
      "id": "cxp-001",
      "description": "所有 Python 项目都需要 .env 文件保存密钥",
      "observed_in_projects": 5,
      "confidence": 0.90,
      "global_skill_candidate": true,
      "first_observed_at": "2026-06-10T10:00:00Z",
      "last_observed_at": "2026-06-22T10:00:00Z"
    }
  ]
}
```

`global_skill_candidate: true` 表示这个模式达到了"值得提炼成 global skill"的证据门槛，会被 巩固循环（Stage 8）的 Scope 晋升扫描捡到。

### 3.4 activity_log.jsonl — 全局活动日志

**路径**：`~/.agent/activity_log.jsonl`

记录所有项目的 session 活动流水，是 **Stage 6.3 异常检测的基线数据来源**：

```json
{"ts": "2026-06-22T14:30:00Z", "record_type": "session_end", "project": "mini_agent", "session_id": "abc123", "theme": "重构 auth 模块", "duration_min": 8.3}
{"ts": "2026-06-22T14:30:01Z", "record_type": "session_metrics", "session_id": "abc123", "tool_count": 24, "total_tokens": 58000, "duration_min": 8.3}
```

`session_metrics` 行由 Stage 6 的 `_run_observability_on_session_end()` 写入，供 `detect_anomalies()` 读取。

### 3.5 context 注入时机

以下 W3 数据在 session 启动时注入：

| 数据 | 注入条件 | 配置项 |
|------|---------|--------|
| self_profile 基本信息 | 始终 | `GlobalKnowledgeConfig.enabled` |
| activity_log 最近 N 条 | 始终 | `activity_log_inject_limit`（默认 5）|
| 跨项目相关模式 | 当前项目的 cross_project 有匹配时 | 自动 |

---

## 4. 配置

### WorkdirKnowledgeConfig（W2）

```json
{
  "workdir_knowledge": {
    "enabled": true,
    "timeline_inject_limit": 5,
    "work_thread_relation_days": 7.0,
    "open_threads_inject_limit": 5
  }
}
```

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `true` | 整体开关 |
| `timeline_inject_limit` | `5` | 注入最近几条 timeline 记录 |
| `work_thread_relation_days` | `7.0` | `N` 天内有活动的 work_thread 才关联到新 session |
| `open_threads_inject_limit` | `5` | 最多注入几条 high-priority open_thread |

### GlobalKnowledgeConfig（W3）

```json
{
  "global_knowledge": {
    "enabled": true,
    "dormant_after_days": 30.0,
    "activity_log_inject_limit": 5
  }
}
```

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `true` | 整体开关 |
| `dormant_after_days` | `30.0` | 超过此天数未活跃的项目标记为休眠 |
| `activity_log_inject_limit` | `5` | 注入最近几条 activity_log 条目 |

---

## 5. 代码入口速查

### W2 Workdir 知识层

| 功能 | 位置 |
|------|------|
| 全部函数 | `src/mini_agent/perception/workdir_knowledge.py` |
| `ensure_project_meta()` | 项目身份证初始化/更新 |
| `append_timeline_entry()` | 写入 timeline 条目 |
| `load_open_threads()` / `save_open_threads()` | 跨 session 线索管理 |
| `import_unresolved_from_manifest()` | 从 task manifest 导入未解决问题 |
| `upsert_knowledge_index_entry()` | 更新知识索引 |
| `search_knowledge_index()` | 按关键词 TF-IDF 检索知识索引（检索侧补全） |
| `read_knowledge_section()` | 按标题取出 knowledge.md 某节完整正文 |
| `capture_environment_fingerprint()` | 采集环境指纹 |
| `detect_environment_drift()` | 对比指纹检测漂移 |

### W3 Global 知识层

| 功能 | 位置 |
|------|------|
| 全部函数 | `src/mini_agent/perception/global_knowledge.py` |
| `ensure_self_profile()` | 自我画像初始化 |
| `update_self_profile_on_session_end()` | SessionEnd 时更新画像 |
| `register_or_touch_project()` | 注册/刷新项目索引 |
| `scan_cross_project_patterns()` | 扫描跨项目重复模式 |
| `merge_cross_project_patterns()` | 合并扫描结果到索引 |
| `append_activity_log()` | 写入全局活动记录 |

### 路径定义

| 路径 | `AgentPaths` 方法 |
|------|-----------------|
| `.agent/project.json` | `workdir_project_meta()` |
| `.agent/timeline.jsonl` | `workdir_timeline()` |
| `.agent/work_index.json` | `workdir_work_index()` |
| `.agent/open_threads.json` | `workdir_open_threads()` |
| `.agent/knowledge_index.json` | `workdir_knowledge_index()` |
| `~/.agent/self_profile.json` | `global_self_profile()` |
| `~/.agent/projects_index.json` | `global_projects_index()` |
| `~/.agent/cross_project_index.json` | `global_cross_project_index()` |
| `~/.agent/activity_log.jsonl` | `global_activity_log()` |

---

## 6. 工具接口（供对话中使用）

### update_knowledge

```python
update_knowledge(
    section="数据库选型",                 # 渲染为 "## 数据库选型" 标题，也是索引的 key
    content="选择了 SQLite 而不是 Postgres，因为单机部署更简单。",
    summary="单机部署优先，选 SQLite",      # 可选；省略时取 content 前 200 字
    topic="storage",                       # 可选，供 search_knowledge 的 topic 过滤
    decision_type="architecture",          # 可选，如 architecture/gotcha/tradeoff/convention
    affected_modules=["db/engine.py"],     # 可选
)
```

写入 `.agent/knowledge.md`（按 `section` 标题替换/追加，走 `StateRepo.apply()`
产生 git commit），并同步更新 `knowledge_index.json` 里对应的索引条目。

### search_knowledge

```python
search_knowledge(query="为什么用 SQLite 而不是 Postgres")
# → 按 TF-IDF 相关度排序，返回匹配的索引条目（含 summary，不含正文）

search_knowledge(query="鉴权 token 刷新", include_content=True)
# include_content=True 时额外取出 knowledge.md 里对应 section 的完整正文

search_knowledge(query="集成方式", topic="mcp", k=3)
# topic 精确过滤 + k 限制返回条数
```

`update_knowledge` 把内容写进去之后，这是**唯一**能把内容检索出来的方式——
不会自动出现在 system prompt 里，需要 agent 主动调用。建议在开始一个非trivial
任务前先 `search_knowledge` 一下，看看项目是否已经踩过相关的坑或做过相关决策。

### add_open_thread / resolve_open_thread

```python
add_open_thread(
    summary="task_manager.py 第 300 行疑似有竞态条件",
    priority="high",
)

resolve_open_thread(thread_id="ot-001")
```

---

## 7. 相关文档

- [存储设计](storage-design.md) — 完整路径约定与目录结构
- [观察性系统指南](observability-guide.md) — activity_log.jsonl 的消费方（异常检测基线）
- [巩固循环 后台循环指南](self-evolution-consolidation-guide.md) — cross_project_index 的晋升消费方
- [配置指南](config-guide.md) — `WorkdirKnowledgeConfig` / `GlobalKnowledgeConfig` 详细参数
- [计划与任务指南](plan-and-task-guide.md) — `open_threads` 与 task manifest 的联动
