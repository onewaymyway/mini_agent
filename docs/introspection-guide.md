# Agent 自感知系统指南（Introspection）

## 概述

自感知系统为 agent 提供**实时感知和动态调整自身状态**的能力，无需重启即可查看内部对象值、修改运行参数，以及深入了解任意对象对应的源代码位置与结构。

系统由 **4 个工具**组成，分三个层次：

| 工具 | 层次 | 权限 | 说明 |
|------|------|------|------|
| `agent_status` | 简报 | 只读（无需确认） | 全局快照，一次了解全貌 |
| `agent_inspect` | 详情 + 元信息 | 只读（无需确认） | 按需深查指定子系统；`include_meta=true` 时附加代码元信息 |
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

---

### `agent_inspect` — 子系统详情 + 代码元信息

```
agent_inspect(target: str, include_meta: bool = False) → JSON str
```

#### `target` 枚举（全部默认可见）

| target | 对应对象 | 返回内容 |
|--------|----------|----------|
| `config` | `AppConfig` | 完整配置（api_key 等已脱敏） |
| `history` | `list[dict]` | 所有消息的角色/长度/预览 |
| `stats` | `SessionStats` | turns/token/tool_stats/skill_activations |
| `skills` | `SkillLoader` | 每个 skill 的名称/激活状态/路径/关键词 |
| `tools` | `ToolRegistry` | 所有已注册工具及分组 |
| `memory` | `MemoryStore` | 项目记忆 + 全局记忆的条目数/最近10条 |
| `providers` | `LLMClientPool` | provider 链状态 |
| `registry` | `ToolRegistry` | 按分组的工具索引视图 |
| `session` | `Session` | Session 字段（history 用摘要替代） |
| `perception` | 感知子系统 | project_scan/file_watcher/tool_cache 状态 |
| `retry_policy` | `RetryPolicy` | max_retries/backoff 参数 |
| `mcp` | `MCPManager` | 已连接 server 列表 |
| `env` | `os.environ` | 相关环境变量（KEY/TOKEN 已脱敏）|
| `process` | os/psutil | PID/内存(RSS)/CPU/线程 |

#### `include_meta=true` — 代码元信息层

当 agent 需要理解一个对象"是什么、在哪里、怎么改"时，传入 `include_meta=true`，响应中额外包含 `meta` 字段：

```json
{
  "target": "retry_policy",
  "data": { ... },
  "meta": {
    "class_name": "RetryPolicy",
    "agent_attr": "_retry_policy",
    "source_file": "src/mini_agent/llm/retry.py",
    "source_file_abs": "/path/to/project/src/mini_agent/llm/retry.py",
    "description": "LLM 调用重试策略...",
    "agent_init_context": "agent.__init__：self._retry_policy = default_retry_policy(...)",
    "related_files": ["src/mini_agent/llm/retry.py"],
    "class_meta": {
      "class_line_start": 255,
      "class_line_end": 394,
      "class_docstring": "重试策略：持有一组重试条件和退避策略...",
      "init_signature": "(max_retries, conditions, backoff, retry_delay, retry_on_exception)",
      "dataclass_fields": [
        {"name": "max_retries", "type": "int", "default": "2"},
        {"name": "conditions", "type": "list[RetryCondition]", "default": "field(default_factory=list)"},
        {"name": "backoff", "type": "BackoffStrategy", "default": "field(default_factory=...)"}
      ],
      "methods_public": [
        {"name": "call_with_retry", "args": ["fn", "..."], "doc": "执行带重试的 LLM 调用", "line": 311, "line_end": 360},
        {"name": "add_condition", "args": ["condition"], "doc": "", "line": 362, "line_end": 365}
      ],
      "methods_private": [
        {"name": "_check_conditions", "args": ["response"], "doc": "", "line": 367, "line_end": 380},
        ...
      ]
    },
    "agent_construction": {
      "agent_dir": "src/mini_agent/agent/",
      "total": 1,
      "occurrences": [
        {
          "file": "src/mini_agent/agent/core.py",
          "line": 220,
          "is_declaration": false,
          "assignment": "self._retry_policy: RetryPolicy = (",
          "snippet": "   217:             step_or_multiplier=_backoff_step,\n   218:             max_delay=_backoff_max_delay,\n   219:         )\n>>>  220:         self._retry_policy: RetryPolicy = (\n   221:             default_retry_policy(max_retries=_retry_max, backoff=_backoff)\n   222:             if _retry_max > 0\n   223:             else no_retry_policy()\n"
        }
      ]
    }
  }
}
```

**`meta` 字段说明：**

| 字段 | 说明 |
|------|------|
| `class_name` | Python 类名 |
| `source_file` | 相对项目根的源文件路径 |
| `source_file_abs` | 绝对路径（方便直接打开） |
| `agent_attr` | 在 agent 实例上的属性名（`agent.<attr>`） |
| `description` | 对象职责说明 |
| `agent_init_context` | 在 agent 中的构造方式描述 |
| `related_files` | 与该对象强相关的其他源文件 |
| `class_meta.class_line_start/end` | 类定义起止行 |
| `class_meta.class_docstring` | 类 docstring（最多 400 字符）|
| `class_meta.init_signature` | `__init__` 参数列表 |
| `class_meta.dataclass_fields` | dataclass 字段名/类型/默认值（非 dataclass 则无此字段）|
| `class_meta.methods_public` | 公开方法列表（名称/参数/首行 doc/行号）|
| `class_meta.methods_private` | 私有方法列表（同上）|
| `agent_construction.occurrences` | `mini_agent/agent/` 包内（core.py 及各 Mixin 文件，Stage 12 起由单文件 agent.py 拆分而来）所有赋值位置，每条附带来源 `file` 字段，含带 `>>>` 标注的上下文代码片段 |
| `agent_construction.occurrences[].is_declaration` | `true` 表示仅类型声明，`false` 表示实际构造 |

**元信息采集机制：** 纯静态 AST 分析，不执行 import，无副作用，不触发被分析模块的任何代码。

---

### `agent_patch` — 运行时热修改

```
agent_patch(target: str, field: str, value: str) → JSON str
```

**修改立即生效，不持久化**（重启后恢复原值）。`requires_approval=True`，需用户确认。

**白名单字段：**

| target | field | 类型 | 说明 |
|--------|-------|------|------|
| `config` | `auto_approve` | bool | 切换自动批准（同步到 PermissionGuard）|
| `config` | `sandbox` | bool | 切换沙箱模式（同步到 PermissionGuard）|
| `config` | `model` | str | 切换模型名 |
| `config` | `max_tokens` | int > 0 | 单次最大 token 数 |
| `config` | `temperature` | float [0,1] | 温度参数 |
| `config` | `verbose` | bool | 详细日志 |
| `config` | `stream` | bool | 流式输出 |
| `config` | `max_turns` | int > 0 | 最大 turn 数 |
| `config` | `max_llm_calls` | int > 0 | 单 turn 最大 LLM 调用次数 |
| `retry_policy` | `max_retries` | int >= 0 | 重试次数 |
| `stats` | `reset` | — | 清零所有统计 |
| `tool_cache` | `clear` | — | 清空工具结果缓存 |
| `skill` | `<name>:active` | bool | 激活/停用指定 skill |

---

### `agent_policy` — 策略调整

```
agent_policy(action: str, target?: str, field?: str) → JSON str
```

| action | 参数 | 说明 |
|--------|------|------|
| `show` | — | 显示当前完整策略 |
| `hide_target` | target | 隐藏某 inspect target |
| `show_target` | target | 取消隐藏 |
| `lock_target` | target | 锁定（禁止 patch 整个 target）|
| `unlock_target` | target | 解锁 |
| `lock_field` | target + field | 锁定具体字段 |
| `unlock_field` | target + field | 解锁 |

策略存储在 `agent._introspection_policy`，运行时立即生效。

---

## 可见性与可改性控制

### 默认策略

| 维度 | 默认值 |
|------|--------|
| 所有 inspect target | **全部可见** |
| 所有 patch target | **白名单内字段可改** |
| api_key 等敏感字段 | 结构性保护，不在白名单，无法通过 patch 修改 |

### 通过代码收紧

```python
policy = agent._introspection_policy
policy.hidden_targets.add("env")           # 隐藏 env
policy.locked_targets.add("config")        # 整个 config 只读
policy.locked_fields.setdefault("config", set()).add("sandbox")  # 只锁 sandbox
```

### 通过 agent_policy 工具动态调整

```
agent_policy(action="hide_target", target="env")
agent_policy(action="lock_target", target="config")
agent_policy(action="lock_field", target="config", field="model")
```

---

## 敏感信息保护

- `api_key`、`token`、`secret`、`password` 等字段自动替换为 `***(<N> chars)`
- 环境变量中 KEY/TOKEN/SECRET/PASSWORD 类同样脱敏
- `api_key` 不在 `agent_patch` 白名单，结构性禁止修改

---

## 典型使用场景

**任务前自检**
```
agent_status()  →  当前 model/tokens/skills 全貌
```

**修改代码前了解目标对象**
```
agent_inspect(target="retry_policy", include_meta=true)
→ 拿到 RetryPolicy 的源文件路径、类结构、agent/ 包中的构造代码片段
→ 直接定位到需要修改的行，无需手动搜索
```

**调试 memory 内容**
```
agent_inspect(target="memory")  →  最近记忆条目
```

**临时切换模型**
```
agent_patch(target="config", field="model", value="claude-haiku-4-5")
```

**遭遇频繁失败，临时关闭重试**
```
agent_patch(target="retry_policy", field="max_retries", value="0")
```

**清空缓存后重试工具**
```
agent_patch(target="tool_cache", field="clear", value="")
```

---

## 实现文件

| 文件 | 说明 |
|------|------|
| `src/mini_agent/tools/introspection.py` | 全部实现：策略类、AST 元信息提取、采集函数、注册函数 |
| `src/mini_agent/agent/lifecycle.py` | `_init_components` 末尾调用 `register_introspection_tools` |
