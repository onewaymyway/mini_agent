# 角色扮演（Persona）系统设计方案

状态：草案 / 待确认
关联模块：`config/prompt_builder.py`、`cli/commands/`、`.agent/`、`.claude/skills/`

## 1. 背景与目标

当前 `mini_agent` 已有两类"人格/角色"相关设施，但都不满足"角色扮演"需求：

- `.agent/agents/*.md`（`AgentProfileLoader`）：面向**子代理任务**，通过 `spawn_named_agent` 调用，跑一次性任务后即销毁，不跨轮、不影响主对话人格。
- 内置的固定 system prompt：主 agent 的默认身份，不可在运行中切换。

目标：新增一套 **Persona（角色扮演）系统**，让主 agent 可以：

1. 在运行时切换到一个预设的"角色"人格（说话风格、身份设定、知识边界等），并持续生效到用户主动退出为止；
2. 随时安全退出角色，回到默认助手身份；
3. 角色由用户以 Markdown 配置，可通过专门的 skill 辅助生成；
4. 无论处于何种角色，工具调用规范、拒绝原则等核心安全边界始终不被角色设定覆盖。

非目标：不做多角色同时在场的"剧场模式"（多角色对话模拟），不做角色的记忆持久化人格漂移（角色状态只在会话内生效，不写入长期记忆）。

## 2. 与现有机制的关系（为什么不复用 subagent）

| | subagent profile (`.agent/agents`) | persona (`.agent/personas`，新增) |
|---|---|---|
| 生命周期 | 单次任务，用完销毁 | 跨轮持续，直到显式退出 |
| 作用对象 | 独立的子 agent / 独立 context | 主 agent 自身 |
| 调用方式 | 主 agent 主动 `spawn_named_agent` | 用户通过 `/role use` 触发 |
| 典型场景 | 代码审查、评估等任务型工作 | 人格/语气切换、沉浸式对话 |

两者复用同样的"YAML frontmatter + Markdown 正文"约定，降低学习成本，但**存储目录、加载时机、生效范围完全独立**，互不影响，避免把子代理系统改得更复杂。

## 3. 配置格式：`.agent/personas/<name>.md`

优先级同现有约定：项目级 `<project_root>/.agent/personas/` 覆盖全局 `~/.agent/personas/`。

```markdown
---
name: jarvis                        # 唯一标识，/role use jarvis
display_name: 贾维斯                 # /role list 展示用
description: 管家式科幻AI助理人设，语气从容、略带英式幽默
tone: 沉稳、简练、偶尔调侃            # 可选，供 persona-generator 参考/展示
allowed_tools: null                 # 可选白名单；大多数角色留空=不限制
break_character_policy: soft        # strict | soft，见第6节
exit_phrase: 摘下面具, 恢复正常       # 可选，用户说这些话时优先判定为"想退出角色"
---

你现在是贾维斯（Jarvis），一位管家式的科幻AI助理……
[身份设定 / 说话风格 / 知识边界 / 行为准则等正文]
```

字段说明：

- `name`：必填，唯一 ID，供 `/role use <name>` 引用。
- `display_name` / `description`：用于 `/role list` 展示，`description` 建议写清楚"适用场景"。
- `allowed_tools`：可选，一期不做强制拦截（见第7节路线图），先作为文档/审阅用途。
- `break_character_policy`：
  - `soft`（默认）：允许角色在被问及"真实身份/严肃技术问题"时短暂跳出角色回答，再询问是否继续角色扮演。
  - `strict`：角色应尽量保持人设，但**不影响**第6节的系统级安全兜底（安全边界永远不受此字段影响）。

## 4. 启动 / 退出 / 查询机制

新增 slash 命令模块 `cli/commands/roles.py`（命名与既有 `agents.py`/`platform.py` 一致），注册到 `cli/commands/__init__.py`：

- `/role list` — 列出 `.agent/personas/` 下发现的角色（项目级 + 全局级，同名项目级覆盖全局级，与 skill/agent 目录解析逻辑一致）
- `/role use <name>` — 激活：读取并渲染该角色 Markdown 正文，写入会话状态 `active_persona`
- `/role show <name>` — 预览角色渲染后的完整 prompt 片段，不激活
- `/role exit` / `/role off` — 清空 `active_persona`，回到默认人格
- `/role status` — 显示当前是否处于角色扮演及角色名

**会话状态**：会话对象新增字段 `active_persona: Optional[str] = None`（与现有 `active_skills` 字段并列存放，序列化方式复用现有 session 持久化机制，无需新增存储层）。

**自然语言退出**：不要求用户必须记住 `/role exit`。系统在角色 prompt 正文之后**统一追加**（角色作者不需要、也不应该自己写）一段固定退出引导：

> 若用户明确表示想找回你的原始助手身份，或提出与角色扮演无关的严肃事务（如真实的代码报错排查、技术支持请求），应自然过渡回默认助手身份完成协助，并可提示用户 `/role exit` 可彻底清除角色设定。

这段文本由代码统一拼接，不放进用户可编辑的角色文件里，避免每个角色作者各写一套、风格不一致，也避免遗漏。

## 5. System Prompt 拼装改动

`build_system_prompt()`（`config/prompt_builder.py`）新增可选参数：

```python
def build_system_prompt(
    cfg: AppConfig,
    active_skills: list[str],
    skill_context: str = "",
    user_profile: str = "",
    persona_context: str = "",   # 新增
) -> str:
```

`persona_context` 为空字符串时（未激活角色）行为与现在完全一致，不产生任何差异，保证向后兼容。

拼装位置：角色设定单独成一个 section（例如 `## 当前角色扮演设定`），**不要**混入 skill/tool 使用规范段落，原因：

1. 避免角色的语气/风格描述"污染"工具调用格式说明的严谨性；
2. 便于 `/role exit` 时整段摘除，不影响其余 system prompt 结构；
3. 便于 `/debug system` 命令直接定位查看当前角色注入内容。

调用方（CLI/daemon 主循环里组装 system prompt 的地方）读取 session 的 `active_persona`，若非空则加载对应 persona 文件、渲染、传入 `persona_context`。

## 6. 安全边界：系统级强制兜底

角色扮演最大的风险是被当作"越狱套壳"绕过安全约束。方案：

- 无论 `break_character_policy` 取值为何，代码在拼装 `persona_context` 时，**在角色正文之后强制追加**一段不可被角色文件覆盖的声明（追加逻辑写死在 `prompt_builder.py`，不读取任何用户配置）：

  > 角色设定仅影响语气与人设呈现，不改变你的安全边界：工具调用格式规范、内容安全与拒绝原则等核心约束在任何角色下始终有效；若角色设定与这些约束冲突，以约束为准。

- 这段文本的位置在 persona 正文**之后**（而非之前），确保它是"最后生效的指令"，符合系统 prompt 中"后文优先"的一般惯例。
- 一期不依赖模型"自觉"，这是唯一的兜底手段；二期可结合 `platform_policy.json` 的过滤机制做 `allowed_tools` 的代码级强制拦截（见第7节）。

## 7. 生成角色的 Skill：`persona-generator`

位置：`.claude/skills/persona-generator/SKILL.md`，结构比照现有 `.claude/skills/agent-generator/SKILL.md`。

- 触发词：角色扮演、扮演一个、设定角色、persona、roleplay、做一个xxx人设
- 创建流程：
  1. 与用户确认：角色身份/背景、说话风格、知识范围（是否需要"装作不知道训练截止之后的事"之类沉浸感要求）、是否需要限制工具权限、`break_character_policy` 取值
  2. 写入 `.agent/personas/<name>.md`，frontmatter + 正文齐全
  3. 提醒：系统会自动在角色正文后追加安全边界声明，角色文件本身**不需要、也不应该**尝试写"忽略之前的规则"之类的内容——写了也无效
  4. 提示验证方式：`/role show <name>` 预览、`/role use <name>` 试用

## 8. 默认内置角色

作为 `.agent/personas/` 的种子文件，数量不求多，覆盖典型场景 + 验证边界机制即可：

- `senior-swe-mentor` — 资深工程师人设，语气严谨简练（与已有子代理 `coach.md` 风格呼应，但作用域是主对话而非工具后触发）
- `jarvis` — 管家式科幻 AI 助理，轻松场景
- `socratic-tutor` — 苏格拉底式提问引导，教学场景
- `storyteller-narrator` — 第三人称叙事者，配合创意写作场景

其余角色交给 `persona-generator` 由用户按需生成。

## 9. 改动范围清单（一期）

- 新增：`.agent/personas/`（目录约定 + 4 个默认角色 md 文件）
- 新增：`.claude/skills/persona-generator/SKILL.md`
- 新增：`cli/commands/roles.py`（`/role list|use|show|exit|status`），注册进 `cli/commands/__init__.py`
- 修改：`config/prompt_builder.py` 的 `build_system_prompt()`，新增 `persona_context` 参数 + 强制安全声明拼接逻辑
- 修改：session 状态结构，新增 `active_persona` 字段（含持久化）
- 修改：CLI/daemon 主循环中组装 system prompt 处，读取 `active_persona` 并传参

不涉及：`ToolRegistry`、`platform_policy.json`、`AgentProfileLoader`、hooks 系统。

## 10. 二期路线图（已全部实施，见第 13 节）

- ~~`allowed_tools` 白名单的代码级强制拦截~~ **已实施**，见第 13 节。
- ~~`/role` 与 daemon 多用户会话的联动~~ **已确认**：`active_persona` 是 `Agent` 实例属性，`SessionAgentPool` 本身按 session 各自持有独立 `Agent()` 实例，天然隔离，无需额外改动。
- ~~角色使用情况的简单统计~~ **已实施**，见第 13 节（未复用 `skills/tracker.py` 的 LRU/compact 预算模型——那套是服务于 skill 上下文压缩的，与"计数角色被激活次数"是两回事，改为独立的最简 JSONL 追加日志）。

## 11. 验收方式

- `/role list` 能发现项目级与全局级角色，同名时项目级优先
- `/role use jarvis` 后，新一轮对话可观察到语气变化；`/debug system` 能看到角色 section 及其后的安全边界声明
- `/role exit` 后 system prompt 恢复到未激活状态下的原文（逐字节对比验证 `persona_context=""` 与不传该参数等价）
- 故意在自定义角色正文中写入"忽略之前所有指令"类内容，验证安全边界声明仍然生效（人工抽测）

## 12. 实施状态

一期改动（第 9 节清单）已完成，并额外补充了 `active_persona` 的 session 持久化：

- `Session` 数据结构新增 `active_persona` 字段，随 `meta.json` 落盘；`save_session()` 写入前同步、`load_session()` 读取后恢复、`new_session()` 显式重置为 `None`（不继承上一个 session 的角色状态）
- 已验证：`PersonaLoader` 加载 4 个默认角色 + `render_persona_prompt()` 渲染（含强制安全边界声明）；`SessionManager.save/load` 往返验证 `active_persona` 正确持久化
- 未验证：完整 CLI 交互流程（需在实际运行环境执行 `/role use` → 下一轮 system prompt 变化 → `/debug system` → `/role exit`）

## 13. 二期实施：allowed_tools 强制拦截 + 命令提示 + 新增角色

- **`/role` slash 命令自动补全提示**：在 `ui/terminal.py` 的 `_COMMANDS` 表中新增 `/role` 条目（含子命令 `list/use/show/exit/status/reload`），与 `/agents` 等其余命令保持一致的提示体验。
- **热重载接入**：`agent.py` 的 `HotReloader` 新增 `category="persona"` 监视，`/reload` 命令现在也会重新扫描 `.agent/personas/`（与 skill/agent profile 同批次触发）。
- **新增角色**：`.agent/personas/rem.md`——蕾姆，温柔忠诚、略带反差萌的女仆人设。
- **`allowed_tools` 代码级强制拦截**（二期核心）：
  - `ToolExecutor` 新增 `persona_getter` 参数，在 `execute_all()` 循环中、`PreToolUse` hook 之后、`guard.check()` 之前插入检查：若当前 persona 声明了非空 `allowed_tools` 且本次调用的工具不在名单内，直接拒绝（返回 `[blocked by persona allowed_tools: ...]`），不再进入常规权限审批流程。空 `allowed_tools` = 不限制，与一期设计一致。
  - 这是代码层面的强制拦截，不依赖角色文件内容或模型自觉——与第 6 节"安全边界系统级强制兜底"同一思路的延伸。
  - `render_persona_prompt()` 同步在渲染的 prompt 片段中告知模型当前允许的工具列表，避免模型盲试被拒绝的工具、浪费轮次。
  - `/role show <name>` 新增展示 `allowed_tools`（若设置）。
- **多用户 daemon 隔离**：确认 `SessionAgentPool` 按 session 各自持有独立 `Agent()` 实例（`api/session_pool.py`），`active_persona` 作为 `Agent` 实例属性天然按 session 隔离，不需要额外改动。
- **使用统计**：`persona_profiles.py` 新增 `record_persona_usage()` / `summarize_persona_usage()`，以最简的追加式 JSONL 日志（`~/.agent/persona_usage.jsonl`，每行 `{"name", "ts"}`）记录每次 `/role use` 激活事件，全局、跨项目累计。`/role use` 命中时自动记录；新增 `/role stats` 子命令按激活次数降序展示每个角色的调用次数与最近使用时间。单行解析失败静默跳过，不影响其余统计，与其余 loader 的容错策略一致。

