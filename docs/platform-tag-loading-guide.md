# Skill / Agent Profile / Hook / Tool 平台与 Tag 过滤指南

mini_agent 里的四类"可加载对象"——**Skill**、**Agent Profile**（自定义子 agent）、
**Hook**、**Tool**——都可以声明只在特定平台可用（例如只在 Termux / 只在 PC 桌面），
或打上 tag 由项目级策略统一放行/禁止。不满足条件的对象在**发现/注册阶段**就会被
跳过：它不会出现在任何 catalog、system prompt、或 Anthropic tool schema 里，
即"连描述也不会加载，功能也不会生效"。

默认行为完全向后兼容：**不声明 = 不限制**，所有平台可用，不受任何 tag 规则管辖。

核心实现：`src/mini_agent/platform_filter.py`

## 一、平台标签

内置平台标签（后续可在 `platform_filter.py` 里扩展）：

| 标签 | 含义 |
|------|------|
| `termux` | Android / Termux 环境 |
| `pc` | 桌面聚合标签，windows / macos / linux（非 termux）会自动带上此标签 |
| `windows` | Windows |
| `macos` | macOS |
| `linux` | Linux（非 Termux） |
| `android` | 预留：非 Termux 的其他 Android 壳 |

当前平台的探测优先级：

1. 环境变量 `MINI_AGENT_PLATFORM_TAGS`（逗号分隔）显式覆盖，最高优先级，用于测试 / CI / 容器场景
2. Termux 特征探测（`$TERMUX_VERSION` 或 `$PREFIX` 含 `com.termux`）→ 打上 `termux` + `linux` + `android`
3. `platform.system()` 常规判断 → `windows`/`macos`/`linux` 均会同时带上 `pc`

一个对象声明的 `platforms` 只要与当前平台标签集合有交集即视为放行；不声明则不限制。

## 二、Tag 机制

每个对象可以声明多个 `tags`（自由字符串，不需要预先注册）。是否放行由项目级
`platform_policy.json` 里的 `tags.deny` / `tags.allow` 决定，对象自身不决定"是否允许自己"：

- `tags.deny` 命中任意一个 → 拒绝（优先级最高）
- 配置了 `tags.allow`（非空）且对象的 tag 与 allow 无交集 → 拒绝
- **对象没有声明任何 tag** → 不受 allow/deny 管辖，默认放行（保证旧对象不被误伤）
- 都未配置 `tags` → 不限制

## 三、`platform_policy.json` 配置文件

位置：`<project_root>/platform_policy.json`（与 `agent_config.json` 同目录）。不存在
时完全是 no-op（不做任何限制）。

```json
{
  "platform_override": null,
  "tags": {
    "deny": ["experimental"],
    "allow": []
  }
}
```

| 字段 | 说明 |
|------|------|
| `platform_override` | 可选。手动强制指定当前平台标签（list 或逗号字符串），覆盖自动探测，一般不填 |
| `tags.deny` | tag 黑名单，命中即拒绝，优先级高于 allow |
| `tags.allow` | tag 白名单，非空时启用；只有 tag 命中 allow 的对象才放行（无 tag 的对象不受影响） |

## 四、各类对象如何声明

### Skill（`SKILL.md` frontmatter）

```yaml
---
name: termux-notify
description: 通过 termux-api 发送安卓通知
platforms: [termux]
tags: [mobile, notification]
---
```

`platforms` / `tags` 支持 YAML list 或逗号分隔字符串。

### Agent Profile（`.agent/agents/*.md` frontmatter）

```yaml
---
name: mobile_helper
description: 面向移动端场景的子 agent
platforms: [termux, android]
tags: [mobile]
---
```

### Hook（`hooks.json` 条目，含 skill/agent 自带的动态 hook）

```json
{
  "PreToolUse": [
    {
      "matcher": "bash",
      "command": "termux-check.sh",
      "platforms": ["termux"],
      "tags": ["mobile"]
    }
  ]
}
```

未声明 `platforms`/`tags` 的条目行为不变。

### Tool（Python 代码注册）

```python
from mini_agent.tools import get_default_registry

def termux_vibrate():
    """震动手机"""
    ...

get_default_registry().register_fn(
    termux_vibrate,
    name="termux_vibrate",
    platforms=["termux"],
    tags=["mobile"],
)
```

`@tool(...)` 装饰器同样支持 `platforms=` / `tags=` 参数：

```python
from mini_agent.tools import tool

@tool(platforms=["termux"], tags=["mobile"])
def termux_vibrate():
    """震动手机"""
    ...
```

## 五、CLI / REPL 命令

> 实现位置：`src/mini_agent/cli/commands/platform.py`

| 命令 | 说明 |
|------|------|
| `/platform` 或 `/platform status` | 显示当前探测到的平台标签、`platform_policy.json` 路径与 tag deny/allow 列表、本次运行被过滤对象数 |
| `/platform filtered` | 列出本次运行中因平台/tag 不匹配被过滤掉的 skill/agent/hook/tool，含具体原因 |
| `/platform reload` | 重新读取 `platform_policy.json` 并触发一次热重载 |

`/platform reload` 的生效范围：

- **Skill / Agent Profile**：立即生效（内部通过 `HotReloader.force_reload()` 重新扫描磁盘，
  discover 逻辑会用最新的策略重新过滤）
- **Tool / Hook**：是进程启动时一次性注册的，`reload` 无法撤销已经注册成功的对象，
  策略变化要在这两类对象上生效需要重启 `mini-agent` 进程

## 六、生效链路

过滤发生在"发现/注册"阶段，而不是"使用"阶段：

```
SkillLoader._discover / rediscover      → 不满足条件的 skill 不进入 _all
AgentProfileLoader._discover / rediscover → 不满足条件的 profile 不进入 _all
HookManager._load_hooks_file / register_dynamic_from_dict → 不满足条件的 hook 不进入 specs
ToolRegistry.register                   → 不满足条件的 tool 不进入 _tools
```

因此被过滤掉的对象：
- 不会出现在 `/skills`、`/agents`、`/hooks list` 的列表里
- 不会出现在注入模型的 system prompt / catalog 里
- 不会出现在 Anthropic API 的 tool schema 里（模型完全看不到）
- 对应的 hook 不会在任何事件上被触发

## 七、调试

如果某个 skill/agent/hook/tool "莫名其妙没有生效"，优先检查：

1. `/platform status` — 看当前探测到的平台标签是否符合预期
2. `/platform filtered` — 看该对象是否被过滤，以及具体原因（平台不匹配 / tag 命中 deny / tag 不在 allow 内）
3. 确认 `<project_root>/platform_policy.json` 的内容和位置正确
4. 确认不是被**运行时自动屏蔽**机制拉黑的（原因会显示为 `runtime-quarantined: ...`）——
   见 [运行时自动屏蔽（Auto Quarantine）指南](auto-quarantine-guide.md)。这是与本文档描述的
   静态声明式过滤完全独立的一套机制：默认关闭，只有显式开启后，才会因为对象在当前平台
   反复运行失败而被自动记录进 `runtime_quarantine.json` 并拦截加载。

