# 多角色 Agent 系统测试说明

## 功能概述

测试 mini_agent 的多角色 Agent 协作机制，验证：
- EvaluatorAgent（串行管道）：输出后自动质检 + 评分不达标触发修订循环
- CoachAgent（主从分发）：工具调用后自动介入提供策略建议
- Profile 解析：`role_type / trigger_on / pass_threshold` 等新字段正确读取
- 反馈注入：角色 Agent 输出以正确格式注入主 Agent 历史

---

## 前置条件

1. 已完成 role_agents 模块安装（`src/mini_agent/role_agents/` 目录存在）
2. 示例 profile 已创建：
   - `.agent/agents/evaluator.md`（`role_type: evaluator`）
   - `.agent/agents/coach.md`（`role_type: coach`，`trigger_on: tool_use:bash`）
3. 启动 agent（建议开启 verbose 模式以看到 `[RoleAgent:xxx]` 日志）：
   ```bash
   cd <project_root>
   PYTHONPATH=src python -m mini_agent --verbose
   ```
4. 启动日志中应出现：
   ```
   [RoleAgent] 注册 evaluator 'evaluator' → trigger: output
   [RoleAgent] 注册 coach 'coach' → trigger: tool_use:bash
   Role agents ready: RoleAgentDispatcher(output_roles=['evaluator'], tool_roles={'bash': ['coach']})
   ```

---

## 单元测试（代码层，不调用 LLM）

### 测试一：AgentProfile 新字段解析

验证 profile loader 正确读取新增字段。

```bash
cd <project_root>
PYTHONPATH=src python3 -c "
from mini_agent.orchestrator.agent_profiles import _parse_profile
from pathlib import Path

# 测试 evaluator profile
p = _parse_profile(Path('.agent/agents/evaluator.md'))
assert p.role_type == 'evaluator', f'期望 evaluator，得到 {p.role_type}'
assert p.trigger_on == 'output', f'期望 output，得到 {p.trigger_on}'
assert p.max_iterations == 2, f'期望 2，得到 {p.max_iterations}'
assert p.pass_threshold == 0.75, f'期望 0.75，得到 {p.pass_threshold}'
assert p.inject_as == 'user', f'期望 user，得到 {p.inject_as}'
print('✅ evaluator profile 字段解析正确')
print(f'   role_type={p.role_type}, trigger_on={p.trigger_on}')
print(f'   max_iterations={p.max_iterations}, pass_threshold={p.pass_threshold}')

# 测试 coach profile
p2 = _parse_profile(Path('.agent/agents/coach.md'))
assert p2.role_type == 'coach'
assert p2.trigger_on == 'tool_use:bash'
print('✅ coach profile 字段解析正确')
print(f'   role_type={p2.role_type}, trigger_on={p2.trigger_on}')
"
```

**期望输出**：
```
✅ evaluator profile 字段解析正确
   role_type=evaluator, trigger_on=output
   max_iterations=2, pass_threshold=0.75
✅ coach profile 字段解析正确
   role_type=coach, trigger_on=tool_use:bash
```

---

### 测试二：评分提取（多种格式）

验证 `extract_score` 能正确解析各种评分格式。

```bash
PYTHONPATH=src python3 -c "
from mini_agent.role_agents.feedback import extract_score

cases = [
    ('SCORE: 8/10',          0.8),
    ('SCORE: 9 / 10',        0.9),
    ('score: 0.75',          0.75),
    ('评分：7/10',            0.7),
    ('评分：7',               0.7),
    ('[SCORE: 85]',           0.85),
    ('综合评分：80/100',       0.8),
    ('这是一段文字，没有评分。', None),
]

all_pass = True
for text, expected in cases:
    result = extract_score(text)
    ok = abs((result or 0) - (expected or 0)) < 0.01 if expected else result is None
    status = '✅' if ok else '❌'
    print(f'{status} {text!r:30s} → {result}（期望 {expected}）')
    if not ok:
        all_pass = False

print()
print('✅ 所有评分格式解析通过' if all_pass else '❌ 部分格式解析失败')
"
```

**期望**：8 个 ✅，无 ❌。

---

### 测试三：反馈格式化

验证 `format_feedback` 生成正确格式的注入消息。

```bash
PYTHONPATH=src python3 -c "
from mini_agent.role_agents.feedback import RoleFeedback, format_feedback, build_inject_message

# evaluator 反馈
fb = RoleFeedback(
    role_name='evaluator',
    role_type='evaluator',
    raw_output='内容准确，结构清晰，建议补充示例。',
    score=0.8,
    passed=True,
    inject_as='user',
)
msg = build_inject_message(fb)
assert msg['role'] == 'user'
assert '📊 质量评估' in msg['content']
assert '80/100' in msg['content']
assert '✅ 通过' in msg['content']
print('✅ evaluator 反馈格式正确')
print(msg['content'])
print()

# coach 反馈（system_reminder）
fb2 = RoleFeedback(
    role_name='coach',
    role_type='coach',
    raw_output='建议使用 set -e 防止脚本失败时继续执行。',
    inject_as='system_reminder',
)
msg2 = build_inject_message(fb2)
assert '[system_reminder]' in msg2['content']
assert '🎯 策略建议' in msg2['content']
print('✅ coach 反馈（system_reminder）格式正确')
print(msg2['content'])
"
```

---

### 测试四：Dispatcher 自动发现角色

验证 Dispatcher 能从 profile loader 中识别并分类角色 Agent。

```bash
PYTHONPATH=src python3 -c "
from mini_agent.orchestrator.agent_profiles import AgentProfileLoader
from pathlib import Path

loader = AgentProfileLoader([Path('.agent/agents')])
print('所有 profiles:', loader.available)

# 模拟 dispatcher 的发现逻辑
output_roles = []
tool_roles = {}

for name in loader.available:
    p = loader.get(name)
    if not p or not p.role_type:
        continue
    trigger = p.trigger_on.strip().lower()
    if trigger in ('output', 'turn_end', ''):
        output_roles.append(name)
    elif trigger.startswith('tool_use:'):
        tool = trigger[len('tool_use:'):]
        tool_roles.setdefault(tool, []).append(name)

print('output_roles:', output_roles)
print('tool_roles:', tool_roles)

assert 'evaluator' in output_roles, '期望 evaluator 在 output_roles 中'
assert 'bash' in tool_roles, '期望 tool_roles 中有 bash 键'
assert 'coach' in tool_roles['bash'], '期望 coach 在 tool_roles[bash] 中'
print('✅ Dispatcher 发现逻辑正确')
"
```

---

## 场景测试（对话层）

### 场景一：EvaluatorAgent 质检通过

**目的**：验证主 Agent 输出后，EvaluatorAgent 自动介入并评分通过时的完整流程。

**测试步骤**：
1. 启动 agent，确认 evaluator 已注册（见启动日志）
2. 输入一个有明确标准答案的问题：
   ```
   请解释什么是 Python 的 GIL（全局解释器锁），以及它对多线程编程的影响
   ```

**观察日志**（终端）：
```
[RoleAgent:evaluator] 评估 第 1/2 轮...
[RoleAgent:evaluator] 评分 XX/100 ✅ 通过
```

**期望行为**：
- 主 Agent 给出回答后，控制台出现 `[RoleAgent:evaluator]` 相关日志
- 如果评分 ≥ 75/100（`pass_threshold: 0.75`），直接返回，无修订
- 对话历史中出现 `[📊 质量评估 · evaluator]` 消息

**验证对话历史（可用 `show_session` 命令查看）**：
```
user: 请解释什么是 Python 的 GIL...
assistant: [主 Agent 回答]
user: [📊 质量评估 · evaluator]
      ...
      综合评分：XX/100  ✅ 通过
```

---

### 场景二：EvaluatorAgent 评分不达标 → 触发修订循环

**目的**：验证评分低于阈值时，EvaluatorAgent 将反馈注入历史，主 Agent 进行修订，再次评估。

**测试方法一（临时降低阈值触发修订）**：

临时修改 `.agent/agents/evaluator.md`，将 `pass_threshold` 调高到 `0.99`：

```yaml
---
pass_threshold: 0.99   # 几乎不可能通过，强制触发修订
max_iterations: 2
---
```

**测试步骤**：
1. 修改 profile 后重启 agent（profile 在启动时加载）
2. 输入任意问题：
   ```
   用一句话解释机器学习
   ```

**观察日志**：
```
[RoleAgent:evaluator] 评估 第 1/2 轮...
[RoleAgent:evaluator] 评分 XX/100 ⚠️ 需修订
[RoleAgent:evaluator] 反馈已注入，主 Agent 修订中...
[RoleAgent:evaluator] 评估 第 2/2 轮...
[RoleAgent:evaluator] 评分 XX/100 ✅ 通过（或达到上限）
```

**期望行为**：
- 日志显示两轮评估过程
- 对话历史中出现两条 `[📊 质量评估]` 消息
- 第二条 assistant 回复比第一条更完整/详细（修订效果）
- 测试完成后恢复 `pass_threshold: 0.75`

---

### 场景三：CoachAgent 在工具调用后介入

**目的**：验证执行 bash 命令后，CoachAgent 自动提供策略建议。

**测试步骤**：
1. 确认 `coach.md` 配置了 `trigger_on: tool_use:bash`
2. 输入会触发 bash 工具调用的指令：
   ```
   列出当前目录下所有 Python 文件，并统计每个文件的行数
   ```

**观察日志**：
```
[RoleAgent:coach] tool_use:bash 触发...
[RoleAgent:coach] 建议已注入
```

**期望行为**：
- bash 工具执行完成后，控制台出现 `[RoleAgent:coach]` 日志
- 对话历史中出现 `[🎯 策略建议 · coach]` 消息
- 主 Agent 的后续操作可能参考建议（如使用更简洁的命令、注意潜在问题等）

**注意**：CoachAgent 只在每次 bash 调用后触发一次，不影响 bash 执行结果，
只是追加建议消息到历史。

---

### 场景四：coach.md 不存在时降级正常运行

**目的**：验证角色系统不影响正常功能（无角色 profile 时静默跳过）。

**测试步骤**：
1. 临时重命名 `.agent/agents/coach.md` 为 `.agent/agents/coach.md.bak`
2. 重启 agent，确认启动日志中**不再显示** coach 相关内容
3. 执行 bash 命令，观察**不出现** `[RoleAgent:coach]` 日志
4. 功能完全正常运行
5. 恢复文件名

---

### 场景五：多工具调用场景下 coach 选择性触发

**目的**：验证 coach 只在 `trigger_on` 指定的工具调用后触发，其他工具不触发。

**测试步骤**：
输入同时触发多种工具调用的指令：
```
读取 src/mini_agent/agent.py 的第一行，然后用 bash 统计它有多少字符
```

**期望行为**：
- `read_file`（或类似读文件工具）调用后：**不触发** coach（日志中无 coach 相关内容）
- `bash` 调用后：**触发** coach（日志出现 `[RoleAgent:coach] tool_use:bash 触发...`）

---

## 自定义角色 Agent 测试

### 测试六：创建自定义 custom 角色

**目的**：验证 `role_type: custom` 的角色能正确触发并注入反馈。

**步骤**：

1. 创建 `.agent/agents/tone-checker.md`：

   ```markdown
   ---
   name: tone-checker
   description: 检查回复的语气是否专业友好
   role_type: custom
   trigger_on: output
   inject_as: user
   ---

   你是一个语气检查专家。请对以下 AI 回复的语气进行简短评价（50字以内），
   指出是否专业友好，如有问题请具体说明。
   不需要评分，只需文字评价。
   ```

2. 重启 agent，确认启动日志出现：
   ```
   [RoleAgent] 注册 custom 'tone-checker' → trigger: output
   ```

3. 输入任意问题，观察是否出现 `[💬 角色反馈 · tone-checker]` 消息。

---

## 验证 Checklist

| 测试项 | 验证方式 | 通过标志 |
|--------|----------|----------|
| Profile 新字段解析 | 单元测试一 | 5 个字段值全部正确 |
| 评分格式多样性 | 单元测试二 | 8 种格式全部通过 |
| 反馈消息格式 | 单元测试三 | `[📊 质量评估]` 标签 + 评分百分比 |
| Dispatcher 发现 | 单元测试四 | evaluator 在 output_roles，coach 在 tool_roles |
| Evaluator 触发 | 场景一 | 启动后首次对话出现评估日志 |
| 修订循环 | 场景二 | 日志出现两轮评估，第二条 assistant 更完整 |
| Coach 触发 | 场景三 | bash 调用后出现建议日志 |
| 工具选择性 | 场景五 | 只有 bash 触发 coach，read_file 不触发 |
| 无 Profile 降级 | 场景四 | 删除 profile 后功能正常，无报错 |
| 自定义角色 | 场景六 | custom 角色输出被正确注入 |
