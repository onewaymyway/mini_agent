# Agent 自感知系统指南（Introspection）

## 概述

自感知系统为 agent 提供**实时感知和动态调整自身状态**的能力，无需重启即可查看内部对象值、修改运行参数。

系统由 **4 个工具**组成，分三个层次：

| 工具 | 层次 | 权限 | 说明 |
|------|------|------|------|
| `agent_status` | 简报 | 只读（无需确认） | 全局快照，一次了解全貌 |
| `agent_inspect` | 详情 | 只读（无需确认） | 按需深查指定子系统 |
| `agent_patch` | 修改 | 写（需用户确认） | 运行时热修改白名单字段 |
| `agent_policy` | 策略 | 读写（无需确认） | 调整可见性/可改性范围 |

所有工具注册在 `introspection` 分组，在 `_init_components` 末尾自动注册。

---

## 工具详解

### `agent_status` — 全局简报

```
agent_status() → JSON str
```

返回所有关键子系统的一行摘要，覆盖：

- **llm**: provider、model、max_tokens、stream、fallback chain 长度
- **runtime**: sandbox、auto_approve、verbose、max_turns、max_llm_calls、is_subagent
- **session**: id、title、created_at、project_root
- **stats**: turns、input/output tokens、tool_calls、elapsed
- **history**: 消息条数、估算 token 用量
- **skills**: 启用状态、激活列表、可用数量
- **tools**: 注册总数、分组列表
- **subsystems**: memory/compress/project_scan/file_watch/tool_cache/mcp/reminder/profile/web_search 开关
- **retry_policy**: max_retries、backoff 策略
- **process**: PID、活跃线程数
- **introspection_policy**: 当前的隐藏/锁定配置

典型用途：agent 在执行复杂任务前先自检状态。

---

### `agent_inspect` — 子系统详情

```
agent_inspect(target: str) → JSON str
```

`target` 枚举（全部默认可见）：

| target | 返回内容 |
|--------|----------|
| `config` | 完整 AppConfig（api_key 等已脱敏） |
| `history` | 所有消息的角色/长度/预览（前 120 字符） |
| `stats` | SessionStats 完整字段 + tool_stats + skill_activations 明细 |
| `skills` | 每个 skill 的名称/激活状态/路径/关键词/描述 |
| `tools` | 所有已注册工具的名称/描述/分组/requires_approval |
| `memory` | 项目记忆 + 全局记忆的条目数/路径/最近10条内容 |
| `providers` | LLMClientPool 状态（provider 链、当前活跃 index） |
| `registry` | ToolRegistry 按分组的完整工具索引 |
| `session` | Session 对象字段（history 用摘要替代） |
| `perception` | project_scan/file_watcher/tool_cache 详细状态 |
| `retry_policy` | RetryPolicy 完整字段（max_retries/backoff 参数） |
| `mcp` | MCP manager 已连接的 server 列表 |
| `env` | 相关环境变量（KEY/TOKEN 类已脱敏）+ cwd |
| `process` | PID/内存(RSS)/CPU 时间/线程列表 |

---

### `agent_patch` — 运行时热修改

```
agent_patch(target: str, field: str, value: str) → JSON str
```

**修改立即生效，不持久化到配置文件**（重启后恢复原值）。此工具 `requires_approval=True`，需用户确认后执行。

**白名单字段：**

#### `target="config"`

| field | 类型 | 说明 |
|-------|------|------|
| `auto_approve` | bool | 切换自动批准模式（同步到 PermissionGuard） |
| `sandbox` | bool | 切换沙箱模式（同步到 PermissionGuard） |
| `model` | str | 切换模型名 |
| `max_tokens` | int > 0 | 修改单次最大 token 数 |
| `temperature` | float [0,1] | 调整温度（需 provider 支持） |
| `verbose` | bool | 切换详细日志 |
| `stream` | bool | 切换流式输出 |
| `max_turns` | int > 0 | 修改最大 turn 数 |
| `max_llm_calls` | int > 0 | 修改单 turn 最大 LLM 调用次数 |

#### `target="retry_policy"`

| field | 类型 | 说明 |
|-------|------|------|
| `max_retries` | int >= 0 | 修改 LLM 调用失败重试次数 |

#### `target="stats"`

| field | 说明 |
|-------|------|
| `reset` | 清零 SessionStats（turns/tokens/tool_calls 全部归零） |

#### `target="tool_cache"`

| field | 说明 |
|-------|------|
| `clear` | 清空工具调用结果缓存 |

#### `target="skill"`

| field 格式 | 说明 |
|-----------|------|
| `<skill_name>:active` | value="true"/"false" 激活或停用指定 skill |

返回格式示例：
```json
{"success": true, "target": "config", "field": "verbose", "old": "False", "new": "True"}
```

---

### `agent_policy` — 策略调整

```
agent_policy(action: str, target?: str, field?: str) → JSON str
```

控制哪些子系统对 agent 可见、哪些字段可修改。策略存储在 `agent._introspection_policy`，运行时立即生效。

| action | 参数 | 说明 |
|--------|------|------|
| `show` | — | 显示当前完整策略 |
| `hide_target` | target | 隐藏某 inspect target（agent_inspect 拒绝访问） |
| `show_target` | target | 取消隐藏 |
| `lock_target` | target | 锁定某 target（agent_patch 拒绝修改整个 target） |
| `unlock_target` | target | 解锁 |
| `lock_field` | target + field | 锁定 target 内的具体字段 |
| `unlock_field` | target + field | 解锁 |

---

## 可见性与可改性控制

### 默认策略

| 维度 | 默认值 |
|------|--------|
| 所有 inspect target | **全部可见** |
| 所有 patch target | **白名单内字段可改** |
| 不可 patch 的字段 | 白名单外的所有字段（例如 `api_key` 不在白名单） |

### 收紧示例

```python
# 在 agent 实例化后，通过代码收紧策略
policy = agent._introspection_policy

# 隐藏敏感子系统（agent 无法 inspect）
policy.hidden_targets.add("memory")
policy.hidden_targets.add("env")

# 锁定不允许热修改的 target
policy.locked_targets.add("config")

# 锁定具体字段
policy.locked_fields.setdefault("config", set()).add("sandbox")
```

或通过 `agent_policy` 工具在对话中动态调整：
```
agent_policy(action="hide_target", target="memory")
agent_policy(action="lock_target", target="config")
```

### 放开示例（扩展白名单）

如需允许修改更多字段，直接编辑 `tools/introspection.py` 顶部的 `_PATCH_WHITELIST` 字典：
```python
_PATCH_WHITELIST["config"]["new_field"] = (str, None)
```

---

## 敏感信息保护

- `api_key`、`api_keys`、`token`、`secret`、`password` 等字段在 `agent_inspect("config")` 和 `agent_inspect("providers")` 中自动替换为 `***(<N> chars)`
- `agent_inspect("env")` 中包含 KEY/TOKEN/SECRET/PASSWORD 的环境变量同样脱敏
- `api_key` 不在 `agent_patch` 的白名单内，无法通过工具修改

---

## 实现文件

| 文件 | 说明 |
|------|------|
| `src/mini_agent/tools/introspection.py` | 全部实现（策略类、采集函数、注册函数） |
| `src/mini_agent/agent.py` | `_init_components` 末尾调用 `register_introspection_tools` |

---

## 典型使用场景

**场景 1：任务开始前自检**
```
agent_status()  →  了解当前 model/tokens/skills 状态
```

**场景 2：调试 memory 内容**
```
agent_inspect(target="memory")  →  查看最近记忆条目
```

**场景 3：临时切换模型**
```
agent_patch(target="config", field="model", value="claude-haiku-4-5")
```

**场景 4：遇到频繁失败，临时关闭重试**
```
agent_patch(target="retry_policy", field="max_retries", value="0")
```

**场景 5：清空缓存后重试工具**
```
agent_patch(target="tool_cache", field="clear", value="")
```

**场景 6：临时关闭自动批准**
```
agent_patch(target="config", field="auto_approve", value="false")
```

**场景 7：隐藏 env 信息（安全收紧）**
```
agent_policy(action="hide_target", target="env")
```
