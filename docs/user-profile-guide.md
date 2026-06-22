# 用户画像（Profile）系统指南

> mini-agent 的自动用户画像系统，基于长期记忆自动生成并注入到 system prompt，实现跨 session 的个性化体验。

---

## 1. 设计目标

用户画像系统旨在：

1. **自动学习**：根据长期记忆中的 session 摘要，自动生成用户的技术栈、工作习惯和偏好
2. **个性化响应**：将画像注入 system prompt，使 Agent 的回复风格和内容更贴合用户
3. **增量刷新**：基于记忆条目数量自动触发画像更新
4. **多用户预留**：架构上预留 `user_id` 扩展点，支持未来多用户场景

---

## 2. 核心架构

```
┌─────────────────────────────────────────────────────────┐
│                    Agent (agent.py)                     │
│  - 会话结束时检查是否需要刷新画像                        │
│  - 在后台线程调用 UserProfileManager.generate()         │
└───────────────────────┬─────────────────────────────────┘
                        │ 使用
                        ▼
┌─────────────────────────────────────────────────────────┐
│              UserProfileManager (profile.py)            │
│  - load() / save() — 加载和保存 profile                 │
│  - generate() — 调用 LLM 生成/刷新画像                   │
│  - should_refresh() — 判断是否需要更新                  │
│  - set_preference() — 用户显式设置偏好                  │
└───────────────────────┬─────────────────────────────────┘
                        │ 读取
                        ▼
┌─────────────────────────────────────────────────────────┐
│              MemoryStore (memory_store.py)              │
│  - 提供长期记忆条目（summary + tags）                   │
└───────────────────────┬─────────────────────────────────┘
                        │ 注入
                        ▼
┌─────────────────────────────────────────────────────────┐
│           ContextBuilder + PromptManager                │
│  - 渲染 prompts/system/user_profile.md                  │
│  - 注入到 system prompt                                 │
└─────────────────────────────────────────────────────────┘
```

---

## 3. Profile 数据结构

### 3.1 UserProfile 数据类

```python
@dataclass
class UserProfile:
    user_id: str = "default"              # 用户 ID（多用户预留）
    display_name: Optional[str] = None    # 用户显示名称
    created_at: float                     # 创建时间戳
    updated_at: float                     # 最后更新时间戳
    preferences: dict                     # 用户显式设置的偏好
    derived: dict                         # 系统自动生成的画像
```

### 3.2 derived 字段结构

```python
derived = {
    "summary": "一段自然语言描述的用户画像总结（最多 1000 字）",
    "tech_stack": ["Python", "Rust", "CDP"],  # 技术栈列表（最多 20 项）
    "habits": ["喜欢先写测试", "偏好函数式编程"],  # 工作习惯（最多 20 项）
    "source_entry_count": 50,  # 生成时使用的记忆条目数
    "updated_at": 1700000000.0  # 最后更新时间戳
}
```

### 3.3 preferences 字段

用户通过命令显式设置的偏好，**不会被系统自动覆盖**：

```python
preferences = {
    "language": "中文",
    "model": "claude-opus-4-5",
    "tone": "简洁直接",
    # ... 用户自定义键值对
}
```

---

## 4. 存储路径

### 4.1 单用户模式（当前）

Profile 文件存储在：

```
~/.agent/profile.json
```

路径由 `AgentPaths.profile_path()` 决定。

### 4.2 多用户模式（预留）

未来支持多用户时，路径格式为：

```
~/.agent/users/<user_id>/profile.json
```

调用方只需在构造 `UserProfileManager` 时传入 `user_id` 参数即可自动切换到对应路径。

### 4.3 Profile 文件格式

```json
{
  "user_id": "default",
  "display_name": null,
  "created_at": 1700000000.0,
  "updated_at": 1700000000.0,
  "preferences": {
    "language": "中文"
  },
  "derived": {
    "summary": "用户是一名 Python 开发者，专注于 Agent 和 LLM 相关项目",
    "tech_stack": ["Python", "TypeScript", "Rust"],
    "habits": ["偏好简洁的代码", "喜欢先写测试"],
    "source_entry_count": 50,
    "updated_at": 1700000000.0
  }
}
```

---

## 5. 画像生成机制

### 5.1 生成触发条件

系统通过 `should_refresh()` 判断是否需要生成/刷新画像：

**触发条件**：
1. 记忆条目数 `>= cfg.profile_min_entries`（默认 1 条）
2. 且满足以下任一条件：
   - 尚未生成过画像（`is_new == True`）
   - 自上次生成以来新增的条目数 `>= cfg.profile_refresh_interval_entries`（默认 3 条）

### 5.2 生成流程

```python
# 1. 检查是否需要刷新
if profile_mgr.should_refresh(len(entries), cfg):
    # 2. 取最近 N 条记忆条目（N = cfg.profile.max_entries_for_profile，默认 20）
    entries = sorted(entries, key=lambda e: e.created_at)[-cfg.profile.max_entries_for_profile:]

    # 3. 在后台线程中生成画像
    profile = profile_mgr.generate(llm_client, entries)
```

### 5.3 Prompt 模板

画像生成使用两个 prompt 模板：

**a) `prompts/system/profile_summarizer.md`** - System prompt

```
You are an assistant that builds a concise user profile from a list of past
session summaries. Respond with ONLY a JSON object...

{
  "summary": "2-4 sentence natural-language profile of the user",
  "tech_stack": ["..."],
  "habits": ["..."]
}
```

**b) `prompts/user/profile_update_request.md`** - User prompt

```
Below are summaries of this user's recent sessions, most recent last.
Based on these, build (or update) a profile of the user...

Recent session summaries:
{{memory_text}}
```

### 5.4 画像注入 System Prompt

当存在用户画像时，`prompts/system/user_profile.md` 会被注入到 system prompt：

```
## User profile (from past sessions)

The following is a profile of this user, derived from their past sessions.
Use it to tailor your tone, level of detail, and assumptions — but always
defer to what the user says in the current conversation if it conflicts.

{{ user_profile }}
```

---

## 6. 配置方式

### 6.1 配置文件（`agent_config.json`）

```json
{
  "profile_enabled": true,
  "profile_min_entries": 1,
  "profile_refresh_interval_entries": 3,
  "profile_max_entries_for_profile": 20
}
```

没有对应的 CLI flag，必须通过配置文件开启（`profile_enabled` 默认为 `false`）。

### 6.2 配置字段说明

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `profile_enabled` | bool | `false` | 是否启用用户画像功能 |
| `profile_min_entries` | int | `1` | 生成画像所需的最小记忆条目数 |
| `profile_refresh_interval_entries` | int | `3` | 两次刷新之间的最小条目增量 |
| `profile_max_entries_for_profile` | int | `20` | 生成画像时取用的最近记忆条目数量上限 |

> 这几个默认值都偏小（相比早期设计的 10/20/50），意味着默认配置下画像会很早、很频繁地刷新。如果发现画像质量不稳定或刷新过于频繁消耗 LLM 调用，可在 `agent_config.json` 中调大这三个值。

### 6.3 代码中使用

```python
from mini_agent.profile import UserProfileManager, UserProfile
from mini_agent.config import load_config

# 1. 创建管理器
paths = AgentPaths(project_root=Path("."))
profile_mgr = UserProfileManager(paths)

# 2. 加载 profile
profile = profile_mgr.load()
print(f"用户画像：{profile.derived.get('summary', '尚未生成')}")

# 3. 用户显式设置偏好
profile_mgr.set_preference("language", "中文")
profile_mgr.set_display_name("Alice")

# 4. 生成画像（在后台线程中）
if profile_mgr.should_refresh(len(entries), cfg):
    profile = profile_mgr.generate(llm_client, entries)
```

---

## 7. 用户偏好设置

### 7.1 preferences 与 derived 的分离

系统设计将用户偏好分为两部分：

| 类型 | 字段 | 修改方式 | 用途 |
|------|------|----------|------|
| **显式偏好** | `preferences` | 用户手动设置 | 语言、模型、语气等明确偏好 |
| **自动画像** | `derived` | 系统自动生成 | 技术栈、工作习惯等推断信息 |

**关键设计点**：`preferences` 不会被系统自动覆盖，确保用户的手动设置持久有效。

### 7.2 设置偏好示例

```python
# 设置语言和语气
profile_mgr.set_preference("language", "中文")
profile_mgr.set_preference("tone", "简洁直接")
profile_mgr.set_preference("preferred_model", "claude-opus-4-5")

# 设置显示名称
profile_mgr.set_display_name("Alice")
```

---

## 8. 代码结构

### 8.1 核心文件

- **`src/mini_agent/profile.py`** — `UserProfile` 数据类 + `UserProfileManager` 管理类
- **`src/mini_agent/prompts/system/user_profile.md`** — 画像注入 system prompt 的模板
- **`src/mini_agent/prompts/system/profile_summarizer.md`** — 画像生成的 system prompt
- **`src/mini_agent/prompts/user/profile_update_request.md`** — 画像生成的 user prompt

### 8.2 与 Agent 的集成

在 `agent.py` 的会话结束逻辑中：

```python
# 1. 加载配置中的画像参数
min_entries = cfg.profile_min_entries
refresh_interval = cfg.profile_refresh_interval_entries

# 2. 检查是否需要刷新画像
if profile_mgr.should_refresh(len(memory_entries), cfg):
    # 3. 按 created_at 升序，取最近 N 条记忆条目
    entries = sorted(memory_entries, key=lambda e: e.created_at)[-cfg.profile.max_entries_for_profile:]

    # 4. 在后台线程中异步生成画像
    threading.Thread(
        target=profile_mgr.generate,
        args=(llm_client, entries)
    ).start()
```

---

## 9. 扩展方向

### 9.1 多用户支持

当前架构已预留多用户扩展点：

1. `UserProfile` 包含 `user_id` 字段
2. `UserProfileManager` 接收可选的 `user_id` 参数
3. `AgentPaths.profile_path(user_id)` 支持多用户路径

只需在构造 `UserProfileManager` 时传入实际 `user_id`，即可自动切换到对应路径。

### 9.2 自定义画像字段

可以在 `derived` 中扩展更多字段：

```python
derived = {
    "summary": "...",
    "tech_stack": [...],
    "habits": [...],
    "common_tasks": ["修复 bug", "编写测试", "代码审查"],  # 新增
    "work_hours": "9am-6pm UTC+8",  # 新增
    "source_entry_count": 50,
    "updated_at": 1700000000.0
}
```

只需修改 `profile_summarizer.md` 的 prompt 即可让 LLM 生成额外字段。

### 9.3 手动编辑画像

可以添加命令行工具允许用户手动编辑画像：

```bash
/ profile edit          # 手动编辑画像
/ profile reset         # 重置画像（强制重新生成）
/ profile show          # 显示当前画像
```

---

## 10. 常见问题

### Q1: 画像什么时候首次生成？

当记忆条目数达到 `profile_min_entries`（默认 1 条，即默认配置下几乎立即触发）时，系统会自动触发首次生成。

### Q2: 画像更新会覆盖手动设置的偏好吗？

不会。`preferences` 和 `derived` 分离存储，系统只会更新 `derived` 部分。

### Q3: 画像生成失败怎么办？

`generate()` 方法有错误处理：
- JSON 解析失败时，会使用原始文本作为 `summary`
- LLM 调用失败不会阻断主流程，画像保持旧值

### Q4: 如何强制重新生成画像？

可以手动删除 `derived` 字段，系统会将其视为 `is_new` 状态，下次满足条件时重新生成。

```python
profile = profile_mgr.load()
profile.derived = {}  # 清空画像
profile_mgr.save()
```

### Q5: 画像中的语言和记忆语言不一致怎么办？

`profile_summarizer.md` 中要求 LLM 使用与记忆条目相同的语言生成画像，确保一致性。

---

## 11. 相关文档

- [记忆管理指南](memory-management-guide.md) — 长期记忆系统的设计与使用
- [配置指南](config-guide.md) — 完整配置说明
- [系统架构总览](system-overview.md) — Agent 整体架构

---

*最后更新：2026-06（新增用户画像系统）*
