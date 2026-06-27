# 隐私信息保护机制说明

> `PrivacyGuard` 确保 API key、token 等敏感值在整个 LLM 调用链中始终以占位符形式出现，真实值永远不离开本地进程。

---

## 1. 设计原理

```
用户输入 / 工具结果（含真实 key）
        │
        ▼  redact()：替换为 {{SECRET_N}}
   发送给 LLM
        │
        ▼  LLM 生成回复（含 {{SECRET_N}}）
   接收 LLM 回复
        │
        ▼  restore()：还原为真实值
 agent 执行 / 展示（含真实 key）
```

**对上层透明**：`Agent.run_turn()` 无需感知任何隐私处理，全部在 `_call_llm()` 内部完成。

---

## 2. 隐私值来源

### 2.1 环境变量自动采集（默认开启）

匹配以下正则模式的环境变量名，其值自动纳入保护：

```
.*_API_KEY$       .*_API_TOKEN$      .*_SECRET$
.*_SECRET_KEY$    .*_ACCESS_TOKEN$   .*_AUTH_TOKEN$
.*_PRIVATE_KEY$   ^OPENAI_API_KEY$   ^ANTHROPIC_API_KEY$
^GITHUB_TOKEN$    ^GITLAB_TOKEN$     ^HF_TOKEN$
```

设置 `auto_env_patterns: []` 可完全禁用自动采集。

### 2.2 显式配置（agent_config.json）

```json
{
  "privacy": {
    "enabled": true,
    "secrets": [
      {"name": "MY_DB_PASSWORD", "value": "actual-password-here"},
      {"name": "STRIPE_KEY",     "value": "sk_live_..."}
    ]
  }
}
```

### 2.3 代码传入

```python
cfg = load_config(
    privacy_secrets=[
        {"name": "REDIS_URL", "value": "redis://:password@host:6379"}
    ]
)
```

---

## 3. 占位符规则

- 格式：`{{SECRET_1}}`、`{{SECRET_2}}`…（每次进程启动重新编号）
- 同一个值只分配一个占位符（多次出现复用同一个）
- **值长度 < 4 或为空字符串的条目跳过**（防止误替换普通词汇）
- 替换时按值长度**降序处理**，避免短值先替换导致长值的子串无法匹配

---

## 4. 还原范围

`_call_llm()` 收到 `LLMResponse` 后，对以下字段做占位符还原：

| 字段 | 说明 |
|------|------|
| `response.text` | 模型回复文本（包含 `{{SECRET_N}}` 的命令、解释等） |
| `response.tool_calls[*].input` | 工具调用参数（JSON 反序列化后，key 的值已被还原） |

---

## 5. 配置字段

`PrivacyConfig`（`config/models.py`）：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | `True` | 总开关；`False` 时使用 `_NullGuard` 空操作实现 |
| `secrets` | list | `[]` | 显式隐私条目，每项 `{"name": str, "value": str}` |
| `auto_env_patterns` | list\|null | `null` | `null` = 使用内置默认模式，`[]` = 禁用自动采集 |
| `placeholder_prefix` | str | `"SECRET"` | 占位符前缀，生成 `{{SECRET_N}}` |
| `verbose` | bool | `False` | 启动时打印已注册的隐私条目摘要（不含真实值） |

`load_config()` 新增参数：

| 参数 | 说明 |
|------|------|
| `privacy_enabled` | 覆盖配置文件的 `privacy.enabled` |
| `privacy_secrets` | 追加到配置文件 secrets 列表（不覆盖，合并） |
| `privacy_verbose` | 覆盖配置文件的 `privacy.verbose` |

---

## 6. verbose 模式输出示例

```
[privacy] active — registered secrets:
  {{SECRET_1}} ← OPENAI_API_KEY (51 chars)
  {{SECRET_2}} ← GITHUB_TOKEN (40 chars)
  {{SECRET_3}} ← MY_DB_PASSWORD (16 chars)
```

---

## 7. 运行示例

```
# 用户请求
帮我写一个 curl 命令测试 OpenAI API

# 发给 LLM 的 messages（已屏蔽）
帮我写一个 curl 命令测试 OpenAI API
[历史中若有 key 出现也会被替换]

# LLM 回复（含占位符）
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer {{SECRET_1}}"

# agent 执行 / 展示的内容（已还原）
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer sk-proj-abc123..."
```

---

## 8. 实现文件

| 文件 | 改动 |
|------|------|
| `perception/privacy_guard.py` | **新增**。`PrivacyGuard` 主类、`_NullGuard` 空操作类、`SecretEntry` 数据类、`from_config()` 工厂方法 |
| `config/models.py` | 新增 `PrivacyConfig` 子配置类；`AppConfig` 增加 `privacy: PrivacyConfig` 字段 |
| `config/loader.py` | `load_config()` 新增 `privacy_enabled` / `privacy_secrets` / `privacy_verbose` 参数；新增 `PrivacyConfig` 组装块 |
| `agent.py` | `__init__` 初始化 `self._privacy_guard`；`_call_llm()` 发送前调 `redact_messages()`/`redact_system()`，收到后调 `restore()` 还原 text 和 tool_calls |
