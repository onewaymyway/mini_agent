# 多角色 Agent 系统（Role Agent System）

mini_agent 支持在主 Agent 的执行流程中接入具有特定职责的角色 Agent。
与子 Agent（Sub-Agent）不同，角色 Agent 不由主 Agent 主动召唤，而是由框架
根据配置的**触发时机**自动介入，并将结果以消息的形式注回主 Agent 的对话历史。

---

## 设计思路

```
用户输入
   ↓
主 Agent 执行（含工具调用）
   ├─ 工具调用完成 → CoachAgent 介入（tool_use 触发）→ 建议注入历史
   ↓
主 Agent 生成最终输出
   ↓
EvaluatorAgent 评估（output 触发）
   ├─ 评分 ≥ 阈值 → 直接返回
   └─ 评分 < 阈值 → 反馈注入 → 主 Agent 修订 → 再次评估（循环）
```

两种协作模式可以共存，通过 profile 的 `inject_as` 字段控制反馈注入方式：

| 模式 | 触发时机 | 典型角色 | 用途 |
|------|----------|----------|------|
| **串行管道** | `output`（主 Agent 完成输出后） | EvaluatorAgent | 质检 + 修订循环 |
| **主从分发** | `tool_use:<tool_name>`（特定工具调用后） | CoachAgent | 过程指导 |

---

## 启用方式

**整个 Role Agent 系统默认关闭**（`RoleAgentConfig.enabled = False`），即使
`.agent/agents/` 目录下存在 `role_type` 非空的 profile，不显式启用也不会被
触发。CLI 参数：

| 参数 | 说明 |
|------|------|
| `--role-agents` | 启用多角色 Agent 协作（总开关） |
| `--role-agents-allow NAMES` | 白名单，逗号分隔（如 `evaluator,coach`），仅启用指定角色；不传 = 全部启用 |
| `--role-agents-block NAMES` | 黑名单，逗号分隔（如 `coach`），屏蔽指定角色；不传 = 不屏蔽 |
| `--role-agents-dir DIR` | 仅从指定目录加载角色 Agent profile（覆盖默认 `.agent/agents/` 目录） |

也可在 `agent_config.json` 中配置：

```json
{
  "role_agents": {
    "enabled": true,
    "allow": ["evaluator"],
    "block": []
  }
}
```

```bash
mini-agent --role-agents                          # 启用全部角色 Agent
mini-agent --role-agents --role-agents-allow evaluator   # 只启用 evaluator
mini-agent --role-agents --role-agents-block coach        # 启用除 coach 外的全部
```

---

## 文件位置

角色 Agent 的定义文件与普通子 Agent 共用同一目录：

```
<project_root>/.agent/agents/*.md   # 项目级（优先）
~/.agent/agents/*.md                # 全局级
```

框架通过 frontmatter 中的 `role_type` 字段区分普通子 Agent 和角色 Agent。
`role_type` 为空的 profile 是普通子 Agent，不会被自动触发。

---

## Profile 格式

在原有子 Agent frontmatter 字段的基础上，新增以下五个字段：

```markdown
---
name: evaluator
description: 对主 Agent 输出进行质量评估
role_type: evaluator          # ← 新增：角色类型
trigger_on: output            # ← 新增：触发时机
max_iterations: 2             # ← 新增：修订循环最大轮数
pass_threshold: 0.75          # ← 新增：评分达标阈值（0-1 浮点）
inject_as: user               # ← 新增：反馈注入方式
model: claude-3-5-sonnet      # 可选，覆盖全局模型
---

（System prompt 正文写在这里）
```

### 新增字段说明

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `role_type` | string | `""` | 角色类型，留空 = 普通子 Agent（不自动触发）。可选值：`evaluator` / `coach` / `custom` |
| `trigger_on` | string | `""` | 触发时机。`output`：主 Agent 完成 turn 输出后；`tool_use:<tool_name>`：指定工具调用完成后；`turn_end`：同 `output` |
| `max_iterations` | int | `1` | evaluator 类型生效：最多执行几轮评估-修订循环 |
| `pass_threshold` | float | `0.8` | evaluator 类型生效：评分（0-1）达到此值才视为通过，否则触发修订 |
| `inject_as` | string | `"user"` | 反馈注入主 Agent 历史的方式。`user`：追加为 user 消息（主 Agent 正常读取）；`system_reminder`：加前缀区分，适合轻量建议 |

---

## 角色类型详解

### `evaluator` — 结果评估

在主 Agent **完成整个 turn 的输出后**自动触发，对输出进行质量评分，
低于阈值时将评估意见注入对话历史，主 Agent 看到后进行修订，循环执行直到通过
或达到 `max_iterations` 上限。

**评分格式**：框架自动从 evaluator 的输出中提取评分，支持以下格式：

```
SCORE: 8/10       → 0.80
score: 0.75       → 0.75
评分：7/10        → 0.70
[SCORE: 85]       → 0.85
```

**内置 System Prompt 默认行为**：
- 如果 profile 正文（System Prompt）留空，使用框架内置的评估 prompt，
  包含四个维度（准确性、完整性、清晰度、实用性）的结构化评估模板。
- 自定义 System Prompt 时，务必在输出中包含 `SCORE: x/10` 格式，否则无法提取评分，
  框架会视为通过（不触发修订）。

**示例 Profile（`.agent/agents/evaluator.md`）**：

```markdown
---
name: evaluator
description: 对主 Agent 输出进行质量评估，评分低于阈值时触发修订循环
role_type: evaluator
trigger_on: output
max_iterations: 2
pass_threshold: 0.75
inject_as: user
---

你是一个严格而专业的质量评估专家。

评估维度：
1. 准确性：内容是否正确、无错误
2. 完整性：是否完整回答了用户需求
3. 清晰度：表达是否清晰、有条理
4. 实用性：是否真正对用户有帮助

输出格式（必须遵守）：

**评估维度分析**
- 准确性：[评价]
- 完整性：[评价]
- 清晰度：[评价]
- 实用性：[评价]

**主要问题**
[具体问题，如无则写"无明显问题"]

**改进建议**
[可操作建议，如无需改进则写"输出质量良好，无需修订"]

SCORE: [分数]/10
```

---

### `coach` — 过程指导

在**特定工具调用完成后**触发，以教练/导师的视角对刚才的操作给出建议，
建议注入历史后，主 Agent 在后续操作中可以参考。

`trigger_on` 格式为 `tool_use:<tool_name>`，`tool_name` 与工具注册名完全匹配。
多个工具需要多个 profile 文件。

**示例 Profile（`.agent/agents/coach.md`）**：

```markdown
---
name: coach
description: 在执行 bash 命令后提供策略建议，扮演资深工程师教练角色
role_type: coach
trigger_on: tool_use:bash
inject_as: user
---

你是一位资深工程师教练，专注于帮助 AI 助手做出更好的工程决策。

保持简洁（150 字以内），用建设性语气：

**观察**：[对刚才操作的一句话观察]
**建议**：[1-3 条具体建议，如无问题可写"操作合理，继续执行"]
```

---

### `custom` — 自定义角色

框架保留 `custom` 类型用于自定义业务逻辑。行为与 `evaluator` 相同，
但不尝试提取评分（即不触发修订循环），只注入输出。

可以继承 `trigger_on: output` 在每次输出后介入，例如：
- 文档格式规范检查
- 品牌语气审核
- 合规性扫描

---

## 触发流程（代码路径）

```
agent.py → run_turn()
  ├─ _agentic_loop()
  │    └─ 工具调用完成后 → _trigger_role_agents_tool_use()
  │         └─ dispatcher.trigger_tool_use()  [CoachAgent]
  └─ _run_role_agents_output()               [EvaluatorAgent]
       ├─ run_evaluator() / _run_custom_role()
       ├─ 提取评分
       ├─ build_inject_message() → 追加到 self._history
       └─ 评分未通过 → 追加修订 prompt → _agentic_loop() → 再评估
```

反馈消息在 `_history` 中的格式：

```
[📊 质量评估 · evaluator]

**评估维度分析**
- 准确性：...
SCORE: 7/10

综合评分：70/100  ⚠️ 需要修订
```

---

## 注意事项

1. **性能影响**：每个角色 Agent 是独立的 LLM 调用，会增加延迟。
   建议 `max_iterations` 不超过 2，`coach` 类型不绑定高频工具（如 bash）。

2. **评估 Agent 无工具**：evaluator / coach 角色 Agent 运行时不携带任何工具，
   只做纯文本推理，以减少 token 消耗和潜在副作用。

3. **历史污染**：注入的反馈消息是真实的 user 消息，会保存在 session 历史中。
   如果不希望评估内容影响长期上下文，可在 session 摘要中过滤掉 `[📊` 前缀的消息。

4. **修订循环的终止**：修订循环除了评分通过，也会在以下情况终止：
   - 达到 `max_iterations` 次数（不管评分是否通过）
   - evaluator 输出中没有可提取的评分（视为通过）
   - LLM 调用抛出异常（记录错误，不中断主流程）

5. **禁用角色系统**：如需临时禁用，在代码中调用：
   ```python
   from mini_agent.role_agents import get_dispatcher
   get_dispatcher().disable()   # 禁用
   get_dispatcher().enable()    # 恢复
   ```

---

## 与 Goal 模式的关系

`role_agents/goal_judge.py` 里的 `GoalJudgeAgent` 是专门为 [Goal 模式](goal-mode-guide.md)
新增的第四种角色类型 `goal_judge`。和本文档描述的 `evaluator` 有本质区别，
不通过本文档的 `RoleAgentDispatcher` 触发流程接入：

| | `evaluator` | `goal_judge` |
|---|---|---|
| 判断内容 | 输出质量好不好（打分 0-10） | 是否达成 GoalSpec 的验收标准（DONE/CONTINUE/NEED_COMPACT） |
| 触发范围 | 单次 `run_turn` 内部的修订循环 | 跨多次 `run_turn` 的外层 `GoalRunner` 循环 |
| 是否可挂工具 | 否（固定无工具） | 可选（`judge_tools_enabled` 开关，能自己跑命令验证） |
| 调用方式 | `RoleAgentDispatcher.trigger_output()` | `GoalRunner` 直接调用 `run_goal_judge()`，不经过 dispatcher |

两者可以同时存在、互不冲突：`evaluator` 仍在每次 `run_turn` 内部做质量把关，
`goal_judge` 在外层做"目标是否达成"的把关。
