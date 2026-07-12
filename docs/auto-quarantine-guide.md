# 运行时自动屏蔽（Auto Quarantine）指南

**默认关闭。** 这套机制解决的问题是：某个 Skill / Tool / Agent Profile 在当前
平台上反复运行失败，且不是偶发问题，而是环境本身不兼容（缺命令、缺 Python 模块、
子进程起不来、shell 语法不支持等）——agent 自己解决不了，与其让它一遍遍白白重试，
不如自动把它记下来、下次直接不加载，同时明确告知用户发生了什么、如何撤销。

核心实现：`src/mini_agent/auto_quarantine.py`
CLI/REPL 入口：`src/mini_agent/cli/commands/quarantine.py`（`/quarantine`）

## 一、与 `platform_policy.json` 静态过滤的关系

mini_agent 已有一套**静态声明式**的平台/tag 过滤机制（见
[Skill/Agent/Hook/Tool 平台与 Tag 过滤指南](platform-tag-loading-guide.md)）：
作者在 Skill/Agent/Tool 的元数据里写 `platforms`/`tags`，用户在
`platform_policy.json` 里写 deny/allow 名单——这是**人工提前知道**"这个东西在
某平台就是不该跑"。

Auto Quarantine 补的是"作者没声明、用户也不知道，但实际跑起来发现在这台机器上
就是不行"的场景，属于**运行时自动学习**：

| | 静态过滤（`platform_policy.json`） | 运行时自动屏蔽（`runtime_quarantine.json`） |
|---|---|---|
| 触发方式 | 人工声明 | 实际运行反复失败后自动记录 |
| 稳定性 | 跨会话固定，直到手动改配置 | 动态积累，可随时被用户撤销 |
| 默认状态 | 不存在配置文件即不限制 | 总开关默认 **关闭** |
| 生效阶段 | 发现/注册阶段拦截 | 同样在发现/注册阶段拦截（复用同一个 `LoadPolicy.is_allowed()` 判定点） |

两者共用同一个 gating 入口，所以对被拦截对象的**外部表现完全一致**：不出现在
`/skills`、`/agents`、tool schema、system prompt 里，描述信息也不会被加载。

## 二、总开关（默认关闭）

在 `<project_root>/platform_policy.json` 里新增 `auto_quarantine` 配置节：

```json
{
  "auto_quarantine": {
    "enabled": false,
    "fail_threshold": 3
  }
}
```

| 字段 | 说明 |
|------|------|
| `enabled` | 总开关，**默认 `false`**。关闭时整套机制完全是 no-op：不计数、不写 `runtime_quarantine.json`、不拦截任何对象，即使历史文件里已有记录也不会生效 |
| `fail_threshold` | 同一个对象累计多少次"环境不兼容"失败后自动拉黑，默认 `3` |

也可以用 `/quarantine enable` / `/quarantine disable` 在 REPL 里直接切换（会写回
`platform_policy.json`，无需手动编辑文件）。

## 三、判定规则（避免误杀）

只有下面这几类错误才会被计入失败次数（分类逻辑复用
`src/mini_agent/perception/observability.py::classify_error()`）：

| 类别 | 含义 |
|------|------|
| `not_found` | 命令/文件/可执行程序不存在 |
| `import` | Python 模块缺失 |
| `process` | 子进程启动失败 |
| `syntax` | 当前平台的 shell/工具语法不支持 |

**显式排除** `timeout` / `network` / `permission` / `key_access` 等类别——这些
多是偶发问题或用户可自行处理的问题，不该导致"一朝失败、永久拉黑"。

拉黑条件：

1. 同一个 `(kind, name)` 在当前平台标签集合下，累计**环境不兼容类**失败达到
   `fail_threshold`（默认 3）；
2. 期间没有出现过一次成功——只要成功过一次，失败计数会清零重新开始累计。

拉黑后**不会自动解除**，即使后续环境变了、能跑通了，也需要用户显式
`/quarantine remove` 或编辑 `runtime_quarantine.json`（自动解除同样有误判风险，
必须是用户主动确认的动作）。

## 四、覆盖范围

| kind | 谁会"运行"、失败点在哪 | 归因方式 |
|------|------------------------|----------|
| `tool` | `tool_executor.py::execute_all()` 里工具调用抛异常 | 直接按 `tool_name` 记录 |
| `skill` | Skill 本身不直接"运行"，靠激活期间的工具调用是否顺利体现 | 工具调用失败时，同时归因给当前**所有 active** 的 skill（见下方"归因的宽松性"说明） |
| `agent` | `role_agents/dispatcher.py` 里角色 Agent（evaluator/coach/custom role）返回 `"[XxxAgent 运行失败: ...]"` 格式 | 按 `AgentProfile.name` 记录 |

**Hook 暂不接入**（hook 执行失败目前是内部静默处理，没有对外暴露统一的"执行
结果"，接入成本较高，留待后续）。

### 归因的宽松性说明（skill）

工具调用失败时，会把这次失败**同时**记给当前所有处于 active 状态的 skill，
而不是只记给"真正导致失败的那个 skill"——因为无法精确判断到底是哪个 skill
的指令导致了这次工具调用。这个策略偏宽松，可能有一定误伤，但：

- 阈值是连续 3 次，单次误判不会导致拉黑；
- 计入的错误类别（`not_found`/`import`/`process`/`syntax`）本质上大多是"当前
  平台缺依赖"，跟平台本身的关系远大于跟具体哪个 skill 的关系，归因给多个
  active skill 反而更符合实际情况。

## 五、`/quarantine` 命令

| 命令 | 说明 |
|------|------|
| `/quarantine` 或 `/quarantine status` | 显示总开关状态、失败阈值、配置文件路径、记录总数/已拉黑数 |
| `/quarantine list` | 列出当前已被拉黑的对象：kind / name / 失败次数 / 最近失败原因 / 平台标签 |
| `/quarantine remove <kind>:<name>` | 手动解除单个屏蔽，如 `/quarantine remove tool:xlsx_export` |
| `/quarantine clear` | 清空所有记录（含未拉黑的失败计数） |
| `/quarantine reload` | 重新读取 `runtime_quarantine.json`（手动改过文件后热更新） |
| `/quarantine enable` / `/quarantine disable` | 打开/关闭总开关，写回 `platform_policy.json` |

拉黑发生时会在终端打印明确提示，例如：

```
[quarantine] 工具 'xlsx_export' 在当前平台连续失败达到阈值（not_found），
已自动屏蔽。使用 /quarantine remove tool:xlsx_export 可解除。
```

## 六、`runtime_quarantine.json` 文件格式

位置：`<project_root>/runtime_quarantine.json`（与 `platform_policy.json` 同目录）。

```json
{
  "entries": {
    "tool:xlsx_export": {
      "kind": "tool",
      "name": "xlsx_export",
      "fail_count": 3,
      "first_failed_at": 1730000000.0,
      "last_failed_at": 1730000120.0,
      "last_reason": "not_found: [Errno 2] No such file or directory: 'libreoffice'",
      "platform_tags": ["termux", "linux", "android"],
      "quarantined": true,
      "quarantined_at": 1730000120.0
    }
  }
}
```

可以直接手动编辑这个文件（比如把某条 `quarantined` 改成 `false`），配合
`/quarantine reload` 立即生效。

## 七、调试

如果某个 skill/tool/agent "突然从列表里消失了"：

1. 先看 `/platform filtered` ——排除是静态 `platform_policy.json` 过滤的可能；
2. 再看 `/quarantine list` ——如果出现在这里，说明是运行时自动屏蔽的，`last_reason`
   字段会写明具体错误分类和错误文本；
3. 确认 `/quarantine status` 里总开关是不是本来就没开——没开的话不会有任何对象
   被这套机制拦截，如果对象确实消失了，问题出在别处（比如静态过滤或 tag 规则）。
