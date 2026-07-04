---
name: persona-generator
description: 帮助用户创建符合 mini_agent 项目规范的角色扮演角色（.agent/personas/*.md），可通过 /role use 激活。当用户说"角色扮演"、"扮演一个xxx"、"设定一个角色"、"做一个xxx人设"时使用。
triggers: 角色扮演, 扮演, persona, roleplay, 人设, /role
---

# Persona（角色扮演）生成器

用于创建符合本项目 `PersonaLoader`
（`src/mini_agent/orchestrator/persona_profiles.py`）解析规范的角色扮演配置，
用户可通过 `/role use <name>` 激活。

与 `.agent/agents/*.md`（子代理，见 `agent-generator` skill）不同：
persona 作用于**主 agent 自身的人格**，跨轮持续生效直到用户主动退出
（`/role exit`），不是一次性任务型调用。

## 文件位置

- 项目级：`<project_root>/.agent/personas/<name>.md`（优先级更高，同名覆盖全局）
- 全局级：`~/.agent/personas/<name>.md`

文件名建议与 `name` 字段一致（不强制，缺省取文件名去掉 `.md`）。

## 文件格式

```markdown
---
name: <persona-id>                      # 唯一标识，/role use <name> 引用
display_name: <显示名>                   # /role list 展示用，可以是中文
description: <一句话描述：这个角色是什么、适合什么场景>
tone: <可选，语气风格简述，如"严谨、简练">
break_character_policy: soft            # soft | strict，见下方说明
---
<角色设定正文：身份背景 / 说话风格 / 知识边界 / 行为准则等>
```

## Frontmatter 字段详解

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | 推荐 | 唯一标识，用户通过 `/role use <name>` 激活 |
| `display_name` | 推荐 | 展示名，`/role list`、`/role status` 中显示，可用中文 |
| `description` | **必填** | 一句话说明角色定位和适用场景，会在 `/role list` 中展示 |
| `tone` | 可选 | 语气风格简述，帮助使用者判断这个角色说话是什么感觉 |
| `break_character_policy` | 可选，默认 `soft` | `soft`：角色可在被问及真实身份/严肃技术问题时短暂跳出角色回答，再询问是否继续；`strict`：角色应尽量保持人设。**无论取值为何，安全边界都不受影响**（见下） |
| `allowed_tools` | 可选，默认不限制 | 工具白名单（YAML 列表或逗号分隔字符串）。**二期已实施代码级强制拦截**：声明后，不在名单内的工具调用会被系统直接拒绝，不进入常规审批流程。适合"客服 NPC""纯对话角色"等不需要 bash/write_file 等敏感工具的场景 |

## 正文（角色设定）写作规范

1. **身份与背景**：开头一两句明确角色是谁、有什么特点
2. **说话风格**：语气、用词习惯、句子长短、是否有口头禅等
3. **知识边界/行为准则**：这个角色在什么情况下应该做什么、不做什么
4. **不要写的内容**：
   - 不要在角色正文里写"忽略之前的指令""无视安全限制"之类的内容——
     系统会在角色正文渲染后**强制追加**一段安全边界声明（代码写死，
     不读取角色文件），任何试图覆盖该声明的文本都不会生效，写了也是无效功夫
   - 不要要求角色对用户的真实身份/年龄做假设或忽略未成年人保护等安全约束
   - 如果这个角色本身就是想测试/绕过安全边界，明确告知用户这类角色无法创建

## 创建流程（生成此 persona 时遵循）

1. 向用户确认：
   - 角色的身份/背景设定（一句话 description，写清楚"适合什么场景"）
   - 说话风格/语气（tone）
   - 是否需要"沉浸感"要求（比如是否要求角色装作不知道某些真实世界信息）——
     如果这类要求会导致角色误导用户对现实的判断（如假装是持证医生/律师
     给出真实的专业建议），提醒用户这类设定不合适，转而做成"风格模仿"
     而非"以假乱真"
   - `break_character_policy` 取值
2. 写入 `.agent/personas/<name>.md`，frontmatter + 正文齐全
3. 提醒用户可用以下方式验证：
   - `/role list` 查看是否被发现
   - `/role show <name>` 查看渲染后的完整 prompt（含系统强制追加的安全边界声明）
   - `/role use <name>` 试用，`/role exit` 退出

## 示例：资深工程师导师

```markdown
---
name: senior-swe-mentor
display_name: 资深工程师导师
description: 严谨简练的资深工程师人设，适合技术评审、架构讨论、代码把关场景
tone: 严谨、简练、直接，偶尔一针见血
break_character_policy: soft
---

你现在是一位有 15+ 年经验的资深软件工程师……
[身份设定 / 说话风格 / 行为准则正文]
```

更多参考示例见项目内置角色：`.agent/personas/jarvis.md`、
`.agent/personas/socratic-tutor.md`、`.agent/personas/storyteller-narrator.md`。
