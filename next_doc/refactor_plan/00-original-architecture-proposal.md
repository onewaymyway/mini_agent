# mini_agent 下一代核心架构设计

## ——从能力堆叠型 Agent 到 Self-centered Personal AI Runtime

> 本文基于当前 `mini_agent` 实际代码结构，以及项目过去关于 Agent、Harness、Self Model、Memory、Self Evolution、Personal AI、World Model、万能模拟器等方向的长期讨论整理。
>
> 本文不是一次性重写方案，而是一份**可渐进实施的下一代核心架构迁移方案**。
>
> 核心目标不是继续增加 Agent 功能，而是让已有能力从“多个相互连接的子系统”逐渐收敛为一个统一的智能运行时。

---

# 一、背景

## 1.1 mini_agent 已经进入新的发展阶段

mini_agent 最初的定位是：

> 一个用 Python 实现的简化版 Claude Code。

核心任务是：

```text
用户输入
    ↓
LLM
    ↓
Tool
    ↓
结果
```

随着项目持续发展，它已经逐渐增加了：

* 多 LLM Provider
* Tool
* Skill
* MCP
* SubAgent
* Workflow
* Goal
* Goal Judge
* Memory
* Wiki
* User Profile
* Self Model
* Proprioception
* Affordance
* Behavior Perception
* Autonomous Loop
* Goal Backlog
* Objective
* Cron
* Scheduler
* Resource Arbiter
* Daily Digest
* Next Action Advisor
* Decision Profile
* External Input
* Capability Learning
* Self Maintenance
* Self Evolution
* Evaluation
* Rollback
* Hybrid Execution
* HTTP API
* Multi-user
* Kanban
* 微信
* Android
* Daemon

当前源码约：

```text
494 个 Python 文件
约 192,000 行 Python
397 个测试文件
260 个 next_doc
116 个正式 docs
67 个 evolution 模块
77 个 perception 模块
21 个 workflow 模块
```

因此，项目现在面对的问题已经发生变化。

早期的问题是：

> Agent 能不能工作？

现在的问题变成：

> **这么多能力如何形成一个统一的智能系统？**

---

# 二、当前架构的本质

当前系统大体可以抽象为：

```text
                         Agent
                           │
          ┌────────────────┼────────────────┐
          │                │                │
          ▼                ▼                ▼
       LLM / Tool       Memory          Goal
          │                │                │
          ▼                ▼                ▼
      Workflow         Wiki            Objective
          │                │                │
          ▼                ▼                ▼
      SubAgent        Self Model       Scheduler
          │                │                │
          └────────────────┼────────────────┘
                           ▼
                       Evolution
```

它已经是一个非常完整的 Agent Harness。

但是存在一个结构性问题：

> **Agent 仍然是中心，而 Self 还只是 Agent 周围的一组功能。**

当前代码中已经存在：

```text
AgentSelfModel
PersonalStateSnapshot
Memory
Goal
Profile
DecisionProfile
CapabilityLearning
SelfNarrative
SelfModelDrift
Experience
Lesson
Evolution
```

但是这些东西还没有形成一个统一的：

```text
Self
```

因此当前系统更接近：

> **拥有很多“自我相关功能”的 Agent**

而不是：

> **一个拥有持续存在的 Self 的 AI Runtime。**

---

# 三、为什么现在必须进行架构收敛

## 3.1 继续堆功能会产生复杂性问题

如果继续按照目前的方式发展，很容易出现：

```text
发现问题
  ↓
增加一个模块
  ↓
增加一个状态
  ↓
增加一个 Scheduler
  ↓
增加一个 Advisor
  ↓
增加一个 Memory
  ↓
增加一个配置项
  ↓
增加一个恢复机制
  ↓
模块之间产生新的交互问题
  ↓
继续增加治理机制
```

最终可能形成：

```text
功能越来越多
能力越来越强
代码越来越多
但是整体智能越来越难理解
```

这是 Agent 系统非常容易出现的“组织化”问题。

---

# 四、下一代架构的核心判断

下一阶段不应该再以：

> “增加什么功能？”

作为第一问题。

而应该改成：

> **“这个能力最终属于哪个核心概念？”**

下一代架构只保留八个一级核心概念：

```text
Self
World
Experience
Goal
Capability
Action
Simulation
Runtime
```

这八个概念构成下一代 mini_agent 的核心语义层。

---

# 五、八个核心对象

## 5.1 Self

回答：

> 我是谁？

包括：

```text
Identity
Preferences
Beliefs
Commitments
History
Relationships
Self Model
Decision tendencies
Known limitations
```

Self 不等于 Persona。

Persona 只是：

```text
Self 的表现形式之一
```

Self 应该是跨 session、跨 runtime、跨任务持续存在的。

---

# 六、World

回答：

> 世界现在是什么样？

World 不应该只是 Wiki。

它应该逐渐包含：

```text
Entity
State
Event
Relation
Constraint
Cause
External Signal
Environment
```

例如：

```text
GitHub
  │
  ├── depends_on → Network
  ├── affected_by → Proxy
  └── accessed_by → Git

Project
  │
  ├── depends_on → Python
  ├── uses → GitHub
  └── has_problem → Deployment
```

这意味着：

> World Model 是结构化世界状态，而 Wiki 是 World Model 的一种人类可读表达。

---

# 七、Experience

回答：

> 我经历过什么？

这是下一代系统最重要的数据资产之一。

每一次重要行为都应该逐渐沉淀为：

```yaml
experience:
  id:
  timestamp:

  context:

  state_before:

  goal:

  action:

  reason:

  prediction:

  outcome:

  state_after:

  evidence:

  lesson:

  causal_hypothesis:

  confidence:
```

Experience 不应该等同于 Log。

区别是：

```text
Log
→ 发生了什么

Experience
→ 我做了什么
→ 为什么做
→ 预期是什么
→ 实际发生什么
→ 学到了什么
```

---

# 八、Goal

Goal 不再只是：

```text
goal_text
```

而应该逐渐变成：

```text
Current State
Ideal State
Problems
Gap
Constraints
Resources
Priority
Evidence
Deadline
```

即：

```text
Goal
 =
当前状态
+
理想状态
+
问题
+
差距
+
约束
```

这与此前万能模拟器的核心思想保持一致。

---

# 九、Capability

回答：

> 我能做什么？

Capability 不应该只是 Tool。

它应该统一描述：

```text
Tool
Skill
Workflow
SubAgent
Strategy
Knowledge
Environment Access
Learned Procedure
```

因此：

```text
Tool
Skill
Workflow
SubAgent
```

不再是架构一级概念。

它们都是：

> Capability 的不同实现形式。

---

# 十、Action

回答：

> 我现在可以做什么？

Action 是整个系统真正的执行接口。

例如：

```text
Read
Write
Search
Run
Research
Ask User
Spawn Agent
Run Workflow
Create Skill
Modify Code
Wait
Observe
Explore
Simulate
```

所有执行最终都应该转化成：

```text
Action
```

这样 Goal、Workflow、Cron、Agent、Tool 就不再拥有各自独立的执行语义。

---

# 十一、Simulation

回答：

> 如果我这么做，未来可能发生什么？

Simulation 是下一代架构中非常重要的一层。

它来自此前的“万能模拟器”设计。

输入：

```text
Current State
Goal
Candidate Actions
World Model
Uncertainty
Constraints
```

输出：

```text
Possible Futures
Causal Paths
Expected Outcomes
Risks
Side Effects
New Opportunities
```

例如：

```text
当前：
项目已经有大量调度系统

行动 A：
继续增加 Scheduler

行动 B：
统一 Scheduler

行动 C：
重构为 Runtime Event Loop
```

Simulation 不需要给出：

```text
“架构价值 = 87”
```

而应该给出：

```text
A：
短期开发成本低
但继续增加系统耦合

B：
短期重构成本较高
可以减少调度逻辑重复

C：
结构改变最大
长期可扩展性更高
但迁移风险较大
```

这才是真正有现实决策价值的模拟。

---

# 十二、Runtime

回答：

> 谁让这个 AI 持续存在？

Runtime 统一：

```text
Event Loop
Scheduling
Persistence
Recovery
Context
Resource Management
Permission
Channels
Background Execution
Lifecycle
```

因此：

```text
Daemon
Cron
AutonomousLoop
UnifiedTaskScheduler
ObjectiveExecutor
ResourceArbiter
```

最终都应该逐渐收敛到 Runtime。

---

# 十三、八大核心对象之间的关系

最终形成：

```text
                         ┌──────────────┐
                         │     SELF     │
                         └──────┬───────┘
                                │
               ┌────────────────┼────────────────┐
               │                │                │
               ▼                ▼                ▼
            WORLD            GOALS         CAPABILITIES
               │                │                │
               └────────────────┼────────────────┘
                                ▼
                         ┌─────────────┐
                         │ SIMULATION  │
                         └──────┬──────┘
                                │
                                ▼
                         ┌─────────────┐
                         │   ACTION    │
                         └──────┬──────┘
                                │
                                ▼
                           ENVIRONMENT
                                │
                                ▼
                             OUTCOME
                                │
                                ▼
                         ┌─────────────┐
                         │ EXPERIENCE  │
                         └──────┬──────┘
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
                 WORLD                    SELF
                    │                       │
                    └───────────┬───────────┘
                                ▼
                           NEXT CYCLE
```

Runtime 贯穿整个过程。

---

# 十四、下一代 Agent 的核心循环

当前 Agent 的核心循环是：

```text
User
 ↓
Prompt
 ↓
LLM
 ↓
Tool
 ↓
Result
```

下一代核心循环改成：

```text
Observe
 ↓
Update State
 ↓
Understand
 ↓
Detect Gap
 ↓
Generate Actions
 ↓
Simulate
 ↓
Decide
 ↓
Act
 ↓
Observe Outcome
 ↓
Create Experience
 ↓
Update World
 ↓
Update Self
 ↓
Repeat
```

这应该成为整个项目最核心的 Runtime Loop。

---

# 十五、LLM 的位置也必须改变

LLM 仍然是系统最重要的认知能力之一。

但：

> **LLM 不应该继续承担整个系统的控制中心角色。**

应该变成：

```text
                    Runtime
                       │
                     State
                       │
                    Policy
                       │
       ┌───────────────┼────────────────┐
       │               │                │
       ▼               ▼                ▼
     Rules            LLM          Simulator
       │               │                │
       └───────────────┼────────────────┘
                       ▼
                    Action
```

不同问题交给不同机制：

```text
确定性逻辑
→ Code

简单规则
→ Rule

已有流程
→ Workflow

知识查询
→ Retrieval

复杂推理
→ LLM

未来推演
→ Simulation

执行
→ Action Runtime

高风险决策
→ Human
```

这样可以避免：

> 所有事情都通过 Prompt → LLM → 文本 → Parser。

---

# 十六、Context Builder 的下一代定位

当前：

```text
ContextBuilder
```

主要负责：

```text
Skill
Memory
Project
History
Prompt
```

下一代应该变成：

> **State Assembly Layer**

即：

```text
Self State
+
World State
+
Goal State
+
Relevant Experience
+
Capability State
+
Current Observation
+
Uncertainty
```

再根据当前 Action 选择需要注入的最小上下文。

最终：

```text
Context
=
当前决策所需要的状态切片
```

而不是：

```text
Context
=
历史 + Memory + Skill + Prompt 的堆积
```

---

# 十七、Memory 应该重新定位

当前系统拥有：

```text
Memory
Wiki
Lesson
History
Decision
Experience
Profile
```

下一代不应该再不断增加新的 Memory 类型。

而应该建立：

```text
Experience Store
World Model
Self Model
```

三个核心知识层。

---

## 17.1 Experience Store

保存：

```text
经历
行动
结果
失败
成功
实验
反馈
```

---

## 17.2 World Model

保存：

```text
事实
实体
关系
状态
因果
约束
外部世界
```

---

## 17.3 Self Model

保存：

```text
能力
偏好
信念
习惯
局限
决策历史
当前状态
自我认知
```

---

# 十八、必须区分不同知识可信度

未来所有长期知识都应该区分：

```text
Fact
Belief
Hypothesis
Preference
Goal
Commitment
Experience
Lesson
Prediction
```

例如：

```text
Fact:
某工具当前不可用

Belief:
网络代理可能是原因

Hypothesis:
换代理可能恢复

Experience:
换代理后确实恢复

Prediction:
以后再次出现同样错误时，检查代理可能有效
```

这样系统才能避免：

> 把猜测逐渐当成事实。

---

# 十九、Goal / Objective / Task / Workflow 的收敛

当前系统中：

```text
Goal
Objective
Task
Workflow
Plan
Step
```

语义存在一定重叠。

下一代建议采用：

```text
Goal
 ↓
Intent / Gap
 ↓
Plan
 ↓
Action
```

其中：

### Goal

定义：

> 想达到什么状态。

### Plan

定义：

> 可以通过哪些行动达到。

### Action

定义：

> 现在具体执行什么。

### Workflow

变成：

> 一种可复用的 Plan/Action 模板。

### Task

变成：

> Runtime 中的 Action 实例。

### Objective

逐步降级为：

> Goal 的执行内部表示。

这样可以减少概念爆炸。

---

# 二十、Cron 不再是核心概念

Cron 本质上只是：

> 时间触发器。

因此：

```text
Cron
```

应该变成 Runtime Event：

```text
TimerEvent
```

例如：

```text
TimerEvent
  ↓
检查 Goal
  ↓
判断是否存在 Gap
  ↓
生成 Candidate Actions
  ↓
决定是否执行
```

这样定时任务就不再是一个独立的 Agent 系统。

---

# 二十一、Autonomous Loop 的重新定义

当前 AutonomousLoop 更接近：

```text
不断检查任务
 ↓
挑一个任务
 ↓
执行
```

下一代应该变成：

```text
Observe
 ↓
Assess
 ↓
Detect Gap
 ↓
Generate Opportunity
 ↓
Simulate
 ↓
Decide
 ↓
Act
```

重点从：

> “有没有任务？”

变成：

> **“当前状态与理想状态之间有什么值得解决的差距？”**

这会让自主性真正产生。

---

# 二十二、Goal 应该从“任务列表”变成“状态变化”

例如：

```text
旧模型：

Goal:
完成 mini_agent 重构

Tasks:
1. 修改 A
2. 修改 B
3. 测试 C
4. 提交
```

新模型：

```text
Current State:
系统存在多个调度入口

Ideal State:
所有后台执行统一经过 Runtime

Problems:
调度逻辑重复
状态来源分散
恢复机制不一致

Gap:
需要统一调度语义

Candidate Actions:
A. 建立统一 Scheduler API
B. 建立 Event Runtime
C. 暂时适配现有 Scheduler
```

然后：

```text
Simulation
 ↓
Action
 ↓
Outcome
 ↓
State Update
```

这比传统任务管理更接近真正的智能。

---

# 二十三、World Model 与万能模拟器的关系

此前设计的万能模拟器不应该与 mini_agent 永远保持完全独立。

长期应该形成：

```text
mini_agent
   │
   ├── Self
   ├── World Model
   ├── Goal
   │
   └── Simulator
          │
          ├── Causal Tree
          ├── Causal Lines
          ├── Uncertainty
          ├── Counterfactual
          └── Future States
```

也就是说：

> **万能模拟器最终应该成为 Personal AI 的“未来推理器”。**

而不是单纯一个外部应用。

---

# 二十四、因果模型应该逐渐进入 Agent

当前 Memory 主要回答：

> “以前发生过什么？”

World Model 应该进一步回答：

> “为什么发生？”

例如：

```text
模型调用失败
    ↓
网络错误
    ↓
代理配置异常
    ↓
当前网络环境变化
```

以后 Agent 再遇到类似问题时，不应该只是检索：

```text
以前出现过这个错误
```

而应该理解：

```text
这几个事件之间可能存在因果关系。
```

---

# 二十五、Experience → Learning

真正的学习链路应该统一成：

```text
Experience
 ↓
Pattern Detection
 ↓
Hypothesis
 ↓
Lesson
 ↓
Strategy
 ↓
Capability
 ↓
Policy
```

例如：

```text
连续 5 次失败
 ↓
发现失败模式
 ↓
推测共同原因
 ↓
形成 Lesson
 ↓
改变执行策略
 ↓
验证新策略
 ↓
能力置信度更新
```

这样：

> Self Evolution 就不再只是“修改代码”。

---

# 二十六、Self Evolution 的四级模型

建议下一代统一定义：

## L1：知识演化

```text
Experience
→ Memory
→ World Model
```

## L2：策略演化

```text
Lesson
→ Strategy
→ Plan
```

## L3：能力演化

```text
Skill
→ Tool
→ Workflow
→ SubAgent
```

## L4：结构演化

```text
Runtime
→ Architecture
→ Core Code
```

风险逐级增加。

---

# 二十七、Evolution 必须引入实验机制

下一代 Self Evolution 不应该：

```text
发现问题
 ↓
修改代码
 ↓
测试
 ↓
完成
```

而应该：

```text
Observation
 ↓
Hypothesis
 ↓
Prediction
 ↓
Experiment
 ↓
Outcome
 ↓
Evaluation
 ↓
Belief Update
 ↓
Adoption
```

例如：

```text
Hypothesis:
新的 Context Assembly 可以减少重复解释。

Prediction:
未来 20 次任务中用户纠正次数下降。

Experiment:
A/B 两套 Context Builder。

Outcome:
记录真实执行结果。

Evaluation:
判断是否存在混杂变量。

Adoption:
只有持续有效才正式内化。
```

---

# 二十八、当前 StateRepo 应该继续保留

当前项目的：

```text
StateRepo
T0
T1
T2
T3
Protected Path
Validation
Git Commit
Rollback
```

是下一代 Self Evolution 非常重要的基础。

不应该删除。

它应该升级成：

> **Evolution Governance Layer**

即：

```text
Evolution Proposal
 ↓
Risk Classification
 ↓
Sandbox
 ↓
Validation
 ↓
Experiment
 ↓
Commit
 ↓
Observe
 ↓
Promote / Revert
```

尤其应该保留：

> **自主发起的修改必须自动提升风险等级。**

---

# 二十九、下一代目录结构建议

不要求立即迁移。

目标结构可以逐渐收敛为：

```text
src/mini_agent/
│
├── runtime/
│   ├── runtime.py
│   ├── event_loop.py
│   ├── scheduler.py
│   ├── lifecycle.py
│   ├── persistence.py
│   └── recovery.py
│
├── self/
│   ├── model.py
│   ├── identity.py
│   ├── beliefs.py
│   ├── preferences.py
│   ├── commitments.py
│   └── capabilities.py
│
├── world/
│   ├── model.py
│   ├── entities.py
│   ├── relations.py
│   ├── events.py
│   ├── state.py
│   └── causal.py
│
├── experience/
│   ├── model.py
│   ├── store.py
│   ├── recorder.py
│   ├── retrieval.py
│   ├── patterns.py
│   └── learning.py
│
├── goals/
│   ├── model.py
│   ├── gap.py
│   ├── planner.py
│   └── evaluator.py
│
├── capabilities/
│   ├── model.py
│   ├── registry.py
│   ├── tools.py
│   ├── skills.py
│   ├── workflows.py
│   └── agents.py
│
├── actions/
│   ├── model.py
│   ├── planner.py
│   ├── executor.py
│   └── permissions.py
│
├── simulation/
│   ├── model.py
│   ├── engine.py
│   ├── causal_tree.py
│   ├── scenarios.py
│   └── uncertainty.py
│
├── cognition/
│   ├── llm.py
│   ├── reasoning.py
│   ├── context.py
│   └── decision.py
│
├── evolution/
│   ├── experiments.py
│   ├── proposals.py
│   ├── validation.py
│   └── governance.py
│
└── adapters/
    ├── cli/
    ├── http/
    ├── streamlit/
    ├── weixin/
    └── android/
```

这只是目标结构。

**第一阶段绝对不要直接移动所有文件。**

---

# 三十、当前模块如何映射到新架构

| 当前模块                        | 下一代归属                                 |
| --------------------------- | ------------------------------------- |
| `agent/`                    | `runtime/` + `cognition/`             |
| `context_builder.py`        | `cognition/context.py`                |
| `history/`                  | `experience/`                         |
| `perception/memory_*`       | `experience/`                         |
| `wiki/experience_*`         | `experience/`                         |
| `wiki/world_*`              | `world/`                              |
| `perception/self_model.py`  | `self/`                               |
| `AgentSelfModel`            | `self/model.py`                       |
| `capability_learning.py`    | `self/` + `experience/learning.py`    |
| `goal_mode/`                | `goals/`                              |
| `goal_backlog.py`           | `goals/` + `runtime/`                 |
| `workflow/`                 | `capabilities/workflows.py`           |
| `skills/`                   | `capabilities/skills.py`              |
| `tools/`                    | `capabilities/tools.py`               |
| `orchestrator/`             | `capabilities/agents.py` + `actions/` |
| `evolution/cron_*`          | `runtime/`                            |
| `unified_task_scheduler.py` | `runtime/scheduler.py`                |
| `autonomous_loop.py`        | `runtime/runtime.py`                  |
| `StateRepo`                 | `evolution/governance.py`             |
| `role_agents/`              | `cognition/`                          |
| `ensemble/`                 | `cognition/decision.py`               |
| `external_input/`           | `world/`                              |
| `hybrid_exec/`              | `actions/`                            |
| `permissions.py`            | `actions/permissions.py`              |
| `llm/`                      | `cognition/llm.py`                    |
| CLI/HTTP/微信/Android         | `adapters/`                           |

---

# 三十一、最重要的迁移原则

## 原则 1：不做 Big Bang Rewrite

禁止：

```text
旧架构
 ↓
全部删除
 ↓
新架构重写
```

采用：

```text
旧系统
 ↓
建立新核心语义层
 ↓
Adapter
 ↓
旧模块逐渐接入
 ↓
新代码优先使用新接口
 ↓
旧模块逐步降级
```

---

# 三十二、建立 Compatibility Layer

第一阶段建立：

```text
mini_agent/core/
```

或者：

```text
mini_agent/domain/
```

定义核心对象：

```python
SelfState
WorldState
Experience
GoalState
Capability
Action
SimulationResult
RuntimeState
```

例如：

```python
@dataclass
class GoalState:
    current_state: dict
    ideal_state: dict
    problems: list[str]
    gap: list[str]
    constraints: list[str]
```

但第一阶段这些对象不需要立即替代旧系统。

旧模块可以：

```text
GoalState Adapter
Memory Adapter
Workflow Adapter
```

逐渐把旧数据映射进来。

---

# 三十三、Phase 0：冻结架构膨胀

这是第一阶段。

目标：

> **先停止继续产生新的一级概念。**

规则：

除非属于：

```text
Self
World
Experience
Goal
Capability
Action
Simulation
Runtime
```

否则新功能不得创建新的一级系统。

例如：

```text
NewAdvisor
NewScheduler
NewMemory
NewManager
NewObjectiveSystem
```

原则上都应该先问：

> 它到底属于哪个核心域？

---

# 三十四、Phase 1：建立 Domain Model

新增：

```text
core/domain/
```

定义：

```text
SelfState
WorldState
Experience
GoalState
Capability
Action
SimulationScenario
SimulationResult
RuntimeState
```

要求：

* 纯 Python 数据结构
* 不依赖 LLM
* 不依赖 CLI
* 不依赖 Streamlit
* 不依赖具体存储
* 不依赖具体 Provider

这一步建立整个项目未来的“共同语言”。

---

# 三十五、Phase 2：建立统一 Event Model

这是非常重要的一步。

所有重要变化逐渐统一成：

```text
Event
```

例如：

```text
UserMessage
GoalCreated
GoalUpdated
ActionStarted
ActionCompleted
ActionFailed
ToolCalled
ToolFailed
ExperienceCreated
WorldChanged
SelfChanged
CapabilityChanged
SimulationCompleted
EvolutionProposed
EvolutionApplied
```

统一事件结构：

```yaml
event:
  id:
  type:
  timestamp:
  actor:
  context:
  payload:
  causation_id:
  correlation_id:
```

这样未来可以真正做到：

```text
事件
 ↓
State
 ↓
Experience
 ↓
Learning
```

---

# 三十六、Phase 3：建立 Experience Layer

优先把已有：

```text
history
lesson
decision
experience
failure_pattern
outcome
```

统一接入 Experience。

不要求立即删除原来的存储。

建立：

```text
ExperienceRecorder
ExperienceStore
ExperienceRetriever
ExperienceAnalyzer
```

验收标准：

> 一个 Goal 执行结束后，可以生成结构化 Experience，并且下一个类似 Goal 可以检索到它。

---

# 三十七、Phase 4：统一 State

建立：

```text
StateManager
```

统一维护：

```text
SelfState
WorldState
GoalState
CapabilityState
RuntimeState
```

注意：

> State 不等于数据库。

它是：

> **当前系统对现实的结构化认知。**

---

# 三十八、Phase 5：统一 Goal

逐渐让现有：

```text
GoalSpec
GoalState
GoalBacklog
Objective
Task
Workflow
```

全部围绕：

```text
Current State
Ideal State
Problem
Gap
Plan
Action
Outcome
```

重新组织。

不需要一次删除 Objective。

可以先：

```text
Objective
→ GoalAction / InternalGoalStep
```

逐渐降级。

---

# 三十九、Phase 6：统一 Action

建立：

```text
ActionSpec
ActionExecutor
ActionResult
```

统一：

```text
Tool
Workflow
SubAgent
Script
Research
UserAsk
```

例如：

```python
ActionSpec(
    type="tool",
    capability="bash",
    arguments={...},
    expected_outcome=...,
)
```

然后：

```text
ActionExecutor
```

负责：

```text
permission
resource
execution
timeout
retry
outcome
experience
```

---

# 四十、Phase 7：建立 Decision + Simulation

这一步才开始真正改变 Agent 的“思考方式”。

流程：

```text
Current State
 ↓
Goal Gap
 ↓
Candidate Actions
 ↓
Simulation
 ↓
Decision
 ↓
Action
```

第一版 Simulation 不需要复杂。

可以先：

```text
LLM scenario generation
+
规则约束
+
历史经验
```

逐渐发展成真正的：

```text
Causal Tree
Causal Lines
Uncertainty
Counterfactual
```

---

# 四十一、Phase 8：重构 Autonomous Runtime

最后再把：

```text
Daemon
AutonomousLoop
Cron
UnifiedTaskScheduler
ObjectiveExecutor
ResourceArbiter
```

逐渐收敛到：

```text
AgentRuntime
```

Runtime 的核心逻辑：

```python
while running:

    events = observe()

    state = state_manager.update(events)

    gaps = goal_engine.detect_gaps(state)

    actions = planner.generate_actions(
        state,
        gaps,
    )

    candidates = simulator.evaluate(
        state,
        actions,
    )

    action = decision_engine.select(
        candidates,
    )

    result = executor.execute(action)

    experience = experience_recorder.record(
        state,
        action,
        result,
    )

    learner.update(
        experience,
    )
```

这才是下一代 Autonomous Agent。

---

# 四十二、Phase 9：把 Self Evolution 接入统一 Experience

最终演化闭环：

```text
Experience
 ↓
Pattern
 ↓
Problem
 ↓
Hypothesis
 ↓
Experiment
 ↓
Evaluation
 ↓
Evolution Proposal
 ↓
Sandbox
 ↓
Validation
 ↓
Deploy
 ↓
Observe
 ↓
Promote / Rollback
```

其中：

```text
StateRepo
EvolutionWorkspace
Validators
EvalRunner
```

继续作为底层安全设施。

---

# 四十三、Phase 10：旧系统降级

当新架构成熟后：

```text
旧 Goal
→ Adapter

旧 Memory
→ Adapter

旧 Workflow
→ Capability

旧 Scheduler
→ Runtime adapter

旧 Advisor
→ Decision policy

旧 Objective
→ Goal internal step
```

最终：

> 用户看不到旧系统。

但旧实现可以在内部继续运行很长时间。

---

# 四十四、下一代系统的最小 MVP

不要一开始就实现完整 World Model。

第一版只需要：

```text
SelfState
WorldState
GoalState
Experience
Capability
Action
Runtime
```

实现一个完整闭环：

```text
用户提出目标
 ↓
生成 GoalState
 ↓
读取 SelfState
 ↓
读取相关 Experience
 ↓
读取 WorldState
 ↓
生成 Candidate Actions
 ↓
LLM 选择/规划
 ↓
执行 Action
 ↓
记录 Outcome
 ↓
生成 Experience
 ↓
更新 State
```

只要这个闭环跑通：

> 下一代架构就已经成立。

---

# 四十五、必须建立新的测试模型

传统测试主要测试：

```text
函数是否正确
API 是否正确
Workflow 是否正确
```

下一代还需要测试：

## State Test

输入事件后：

```text
State 是否正确更新？
```

## Experience Test

执行完成后：

```text
是否产生正确 Experience？
```

## Learning Test

重复失败后：

```text
是否形成正确 Pattern？
```

## Simulation Test

不同 Action：

```text
是否生成不同未来？
```

## Continuity Test

跨 session：

```text
是否仍然保持 Self 连续性？
```

## Recovery Test

进程被 kill：

```text
是否可以恢复 Runtime？
```

---

# 四十六、需要增加四类架构级测试

## 1. Continuity Test

```text
Session A
 ↓
产生经验

Session B
 ↓
能否正确使用 Session A 的经验
```

---

## 2. Self Consistency Test

```text
Self Model
Capability
Experience
Decision
```

是否互相矛盾。

---

## 3. World Consistency Test

```text
World State
Experience
Goal
Simulation
```

是否出现明显冲突。

---

## 4. Learning Effectiveness Test

给 Agent 重复相似问题：

```text
第一次：
失败

第二次：
减少相同错误

第三次：
主动采用正确策略
```

这才真正证明：

> Agent 学会了。

---

# 四十七、架构治理原则

以后每一个新模块都必须回答：

### 问题 1

它属于哪个核心对象？

```text
Self
World
Experience
Goal
Capability
Action
Simulation
Runtime
```

如果回答不了：

> 暂缓。

---

### 问题 2

它是否创造新的状态源？

如果是：

> 为什么不能复用已有 State？

---

### 问题 3

它是否创造新的 Scheduler？

如果是：

> 为什么 Runtime 不能承担？

---

### 问题 4

它是否创造新的 Memory？

如果是：

> 为什么不能进入 Experience / World / Self？

---

### 问题 5

它是否真的降低用户认知负担？

如果不能：

> 优先级降低。

---

# 四十八、一个非常重要的原则：减少“系统中的系统”

当前架构中容易出现：

```text
System
 ├── Goal System
 ├── Memory System
 ├── Evolution System
 ├── Scheduler System
 ├── Workflow System
 ├── Capability System
 └── Self System
```

下一代应该变成：

```text
Runtime
 │
 └── Domain Model
       ├── Self
       ├── World
       ├── Experience
       ├── Goal
       ├── Capability
       ├── Action
       └── Simulation
```

也就是：

> **从“很多系统协作”转向“一个系统拥有很多能力”。**

---

# 四十九、关键设计原则

## 原则 1：Self First

所有长期能力最终都必须回答：

> 对 Self 有什么影响？

---

## 原则 2：State First

不要直接从：

```text
Prompt
→ Action
```

跳过去。

必须经过：

```text
State
→ Action
```

---

## 原则 3：Experience First

任何重要行动都应该产生可复用经验。

---

## 原则 4：Prediction Before Action

对于有明显副作用的 Action：

```text
先预测
再执行
```

---

## 原则 5：Outcome Before Learning

不能：

```text
执行
→ 立即认为学到了
```

必须：

```text
执行
→ 观察结果
→ 判断结果
→ 学习
```

---

## 原则 6：Uncertainty Is State

不知道不是：

```text
null
```

而应该是：

```text
unknown
confidence
evidence
hypothesis
```

---

## 原则 7：LLM Is Cognitive Engine, Not Entire Agent

LLM：

```text
负责认知
```

Runtime：

```text
负责存在
```

Self：

```text
负责连续性
```

World：

```text
负责现实
```

Experience：

```text
负责学习
```

---

## 原则 8：Capability Is Not Identity

Agent 拥有：

```text
Tool
Skill
Workflow
```

不意味着这些就是 Agent 本身。

能力可以替换。

Self 应该保持连续。

---

## 原则 9：Evolution Must Be Evidence Driven

任何重要自我修改都应该有：

```text
Evidence
Hypothesis
Experiment
Result
```

---

## 原则 10：架构优先收敛，不优先扩张

未来增加功能时：

> 优先把能力放进现有核心对象。

而不是：

> 再创造一个新的系统。

---

# 五十、下一代系统的北极星指标

不再主要使用：

```text
Tool 数量
Skill 数量
代码量
Agent 数量
Workflow 数量
```

衡量成长。

应该关注：

## 1. User Explicit Instruction Ratio

用户需要明确告诉 Agent 的事情占比。

目标：

```text
持续下降
```

---

## 2. Experience Reuse Rate

历史经验被再次有效使用的比例。

---

## 3. Prediction Accuracy

Agent 对自身行动结果的预测质量。

---

## 4. Self Model Accuracy

Agent 对自己能力边界的认知准确程度。

---

## 5. World Model Accuracy

Agent 对现实状态的理解准确程度。

---

## 6. Learning Effectiveness

重复面对相似问题时：

```text
错误是否下降？
```

---

## 7. Continuity

跨 session：

```text
身份
经验
目标
状态
```

是否保持连续。

---

## 8. Autonomous Value

Agent 主动执行的事情：

```text
是否真的减少了用户工作？
```

而不是：

```text
主动执行次数
```

---

# 五十一、第一阶段实际开发任务

建议马上建立一个新的设计目录：

```text
docs/architecture_v2/
```

第一批文件：

```text
00-overview.md
01-domain-model.md
02-self-model.md
03-world-model.md
04-experience-model.md
05-goal-model.md
06-capability-model.md
07-action-model.md
08-simulation-model.md
09-runtime-model.md
10-event-model.md
11-migration-plan.md
12-evolution-governance.md
13-testing-strategy.md
```

其中：

```text
00-overview.md
```

作为唯一总纲。

---

# 五十二、第一阶段代码目录

先不要移动旧代码。

增加：

```text
src/mini_agent/core/
```

第一批：

```text
core/
├── __init__.py
├── types.py
├── events.py
├── state.py
├── self_state.py
├── world_state.py
├── experience.py
├── goal.py
├── capability.py
├── action.py
├── simulation.py
└── runtime_state.py
```

全部保持：

> 纯数据模型 + 最少逻辑。

---

# 五十三、第一条真实迁移链

不要同时改所有系统。

先选择：

```text
Goal
```

做第一个完整试验。

实现：

```text
Old Goal
   ↓
Goal Adapter
   ↓
GoalState
   ↓
Planner
   ↓
Action
   ↓
Outcome
   ↓
Experience
```

如果这条链稳定：

> 再迁移 Memory。

然后：

```text
Memory
 ↓
Experience
```

再迁移：

```text
Self Model
```

再迁移：

```text
Autonomous Runtime
```

---

# 五十四、建议的迁移顺序

优先级：

```text
P0
Domain Model
Event Model
Experience Model

P1
Goal Model
Action Model
Self Model

P2
World Model
Capability Model

P3
Decision Engine
Simulation Engine

P4
Runtime Convergence

P5
Self Evolution Convergence
```

---

# 五十五、明确暂时不做的事情

下一代架构阶段明确暂缓：

```text
大规模自主修改核心代码
自主生成自身终极目标
无限 Agent 自我复制
复杂多 Agent 社会
过度复杂的 World Simulation
大规模手机行为采集
大量新的外部数据源
新的独立 Scheduler
新的独立 Memory 系统
```

原因不是这些东西没有价值。

而是：

> **当前最重要的问题是架构收敛，而不是能力扩张。**

---

# 五十六、最终理想状态

最终的 mini_agent 不应该让开发者首先看到：

```text
GoalManager
ObjectiveExecutor
CronScheduler
GrowthAdvisor
MemoryManager
EvolutionManager
WorkflowManager
CapabilityLearning
SelfMaintenance
...
```

而应该看到：

```text
AgentRuntime
```

下面是：

```text
Self
World
Experience
Goal
Capability
Action
Simulation
Cognition
```

例如：

```text
runtime.run()
```

内部：

```text
event = observe()

state = understand(event)

gap = goals.detect_gap(state)

options = planner.generate(gap)

future = simulator.predict(options)

decision = policy.decide(future)

result = action.execute(decision)

experience = learn.record(
    state,
    decision,
    result,
)

self.update(experience)
world.update(experience)
```

这才是真正意义上的：

> **AI Runtime。**

---

# 五十七、与当前 mini_agent 的关系

下一代架构不是：

```text
mini_agent 1.0
        ↓
mini_agent 2.0
        ↓
完全重写
```

而应该是：

```text
                    ┌─────────────────────┐
                    │  Next Core Domain   │
                    │                     │
                    │ Self / World /      │
                    │ Experience / Goal   │
                    │ Capability / Action │
                    │ Simulation / Runtime│
                    └──────────┬──────────┘
                               │
                         Compatibility
                             Layer
                               │
          ┌────────────────────┼──────────────────┐
          ▼                    ▼                  ▼
       Current Agent       Current Goal       Current Workflow
          │                    │                  │
          ▼                    ▼                  ▼
       Current Tool        Current Memory     Current Evolution
```

新核心逐渐成为“上层语义”。

旧模块逐渐成为“底层实现”。

最终：

```text
旧系统
 ↓
Compatibility
 ↓
新核心
 ↓
Runtime
```

---

# 五十八、最终目标不是做一个更复杂的 Agent

这是整个架构迁移最重要的一句话。

当前方向很容易变成：

> “如何做一个能力更多的 Agent？”

下一代真正应该问：

> **“如何构建一个能够持续存在、理解自己、理解环境、追踪目标、积累经验、预测未来、采取行动并从结果中改变自己的 AI 个体？”**

二者的区别非常大。

前者最终容易得到：

```text
Tool + Skill + Workflow + Agent + Memory + Scheduler + Evolution
```

后者最终得到：

```text
Self
+
World
+
Experience
+
Goal
+
Capability
+
Action
+
Simulation
+
Runtime
```

---

# 五十九、最终架构哲学

可以把下一代 mini_agent 的整个架构浓缩成一句话：

> **模型提供认知能力，Runtime 提供持续存在，Self 提供身份连续性，World Model 提供现实理解，Experience 提供成长，Goal 提供方向，Simulation 提供未来推演，Action 提供现实改变。**

于是：

```text
Model
   ↓
Cognition

Harness
   ↓
Runtime

Memory
   ↓
Experience

Self Model
   ↓
Self

World Model
   ↓
World

Planner
   ↓
Goal + Action

Simulator
   ↓
Future

Evolution
   ↓
Learning
```

最终形成：

```text
             ┌───────────────┐
             │     SELF      │
             └───────┬───────┘
                     │
          ┌──────────┼──────────┐
          ▼          ▼          ▼
        WORLD       GOAL    CAPABILITY
          │          │          │
          └──────────┼──────────┘
                     ▼
                 SIMULATION
                     │
                     ▼
                  ACTION
                     │
                     ▼
                  REALITY
                     │
                     ▼
                 EXPERIENCE
                     │
             ┌───────┴───────┐
             ▼               ▼
           WORLD            SELF
             │               │
             └───────┬───────┘
                     ▼
                  RUNTIME
                     │
                     └────→ NEXT CYCLE
```

这将成为 mini_agent 从：

> **“一个越来越强的 Agent 框架”**

走向：

> **“一个真正具有长期连续性、学习能力和自主行动能力的 Personal AI Runtime”**

的关键架构转折点。

---

# 六十、实施总路线图

```text
                    当前 mini_agent
                           │
                           ▼
                ┌──────────────────┐
                │ Phase 0          │
                │ 架构冻结/概念收敛 │
                └────────┬─────────┘
                         ▼
                ┌──────────────────┐
                │ Phase 1          │
                │ Domain Model     │
                └────────┬─────────┘
                         ▼
                ┌──────────────────┐
                │ Phase 2          │
                │ Event Model      │
                └────────┬─────────┘
                         ▼
                ┌──────────────────┐
                │ Phase 3          │
                │ Experience       │
                └────────┬─────────┘
                         ▼
                ┌──────────────────┐
                │ Phase 4          │
                │ Unified State    │
                └────────┬─────────┘
                         ▼
                ┌──────────────────┐
                │ Phase 5          │
                │ Goal Convergence │
                └────────┬─────────┘
                         ▼
                ┌──────────────────┐
                │ Phase 6          │
                │ Action Model     │
                └────────┬─────────┘
                         ▼
                ┌──────────────────┐
                │ Phase 7          │
                │ Simulation       │
                └────────┬─────────┘
                         ▼
                ┌──────────────────┐
                │ Phase 8          │
                │ Runtime          │
                └────────┬─────────┘
                         ▼
                ┌──────────────────┐
                │ Phase 9          │
                │ Self Evolution   │
                └────────┬─────────┘
                         ▼
                ┌──────────────────┐
                │ Personal AI      │
                │ Runtime          │
                └──────────────────┘
```

---

# 六十一、第一阶段完成的判定标准

不要用“新架构代码写了多少”衡量。

第一阶段真正完成的标志是：

1. 新代码不再随意创建新的一级概念。
2. `Self / World / Experience / Goal / Capability / Action / Simulation / Runtime` 成为统一术语。
3. 一个 Goal 可以完整产生：

   ```text
   Goal
   → Action
   → Outcome
   → Experience
   ```
4. Experience 可以被下一个类似任务检索。
5. Self 可以看到 Experience 带来的变化。
6. 当前旧 Agent 可以通过 Adapter 使用新 Domain Model。
7. 不需要一次性迁移现有全部模块。
8. 旧系统仍然可以正常运行。

达到这些条件后，mini_agent 才真正开始进入：

> **架构收敛阶段。**

---

# 六十二、最终判断

当前 mini_agent 最大的问题不是“能力不够”。

恰恰相反：

> **它已经拥有足够多的能力，开始需要解决“如何把能力组织成智能”的问题。**

因此下一阶段最重要的工作不是：

```text
再增加一个 Tool
再增加一个 Skill
再增加一个 Agent
再增加一个 Scheduler
再增加一个 Memory
```

而是：

```text
统一状态
统一经历
统一世界模型
统一行动
统一决策
统一运行时
统一自我模型
```

最终让所有已有能力汇聚到一个真正的核心：

> **Self-centered Runtime。**

这应该成为 mini_agent 下一阶段最重要的架构方向。

---

# 补充（Sprint 3 复盘新增，2026-09-26）

> 以下内容是 Sprint 1-2 实际执行后，按
> `next_doc/refactor_plan/02-executable-sprint-plan.md` Sprint 3
> "更新映射表"任务追加的补充说明，**不修改上方"三十、当前模块如何映射到
> 新架构"表格的原文**（避免静默篡改用户提供的原始方案），只在此追加
> 实际踩坑后发现的隐藏依赖数据，供后续 Phase 决策参考。完整复盘见
> `02-executable-sprint-plan.md` 末尾"Sprint 3 执行记录"。

对"三十"表格里三行的补充数据（`scripts/dep_graph.py --module <name>`
实测结果，inbound 指"直接 import 具体符号/类"的文件数，止损阈值为
10+）：

| 当前模块 | 表格标注的下一代归属 | 实测 inbound 深度依赖文件数 | 是否已超止损阈值 |
| --- | --- | --- | --- |
| `goal_mode/` | `goals/` | 6（Sprint 1 后，含新增的 `core/goal_adapter.py`） | 否，远低于阈值，Sprint 1 已验证可行 |
| `history/`（含 `history_manager.py`） | `experience/` | 12 | **是**（Sprint 3 复盘新发现） |
| `perception/`（含 `perception/memory_store.py`、`perception/self_model.py`） | `experience/` + `self/` | 69 | **是，远超阈值**（Sprint 3 复盘新发现） |

结论见 `02-executable-sprint-plan.md` Sprint 3 执行记录"正式决策"一节：
Memory → Experience 这条链**不能**直接照搬 Goal 迁移链"单一接入点 +
Adapter 直接转换"的模式，需要先做耦合拆解。

