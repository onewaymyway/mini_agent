# 具身智能 × 自主化：mini_agent 整合改进计划

> 本文档在已完成的 Stage 9（Phase H，daemon 化 + 自主运行时）基础上，将 `embodied_agent_design.md` 的具身智能理念落地为可执行的工程改进方向。
>
> **核心设计问题**：每一个具身智能改进，需要先回答——它属于哪个运行时层？
>
> - **daemon 后台进程**（持续存在，无用户连接也在运行）：感知-整合-自我维护
> - **任务执行过程中**（`run_turn` 主循环，SubAgent 并发层）：行为调节-工具抽象-决策辅助
> - **对话交互层**（用户在线，CLI/Web 客户端连接）：社会感知-实时反馈-协商

---

## 当前架构基线（Stage 9 完成后）

```
┌─────────────────────────────────────────────────────────────┐
│  mini-agent daemon（独立常驻进程，与任何客户端连接无关）        │
│                                                             │
│  AutonomousLoop.tick()  ←→  GoalBacklog                    │
│    ├── passive:  Phase G 周期扫描（已接入）                  │
│    ├── maintenance:  探索预算分配（ExplorationSandbox）       │
│    └── autonomous:  软目标 derive（第十二节，暂未实现）        │
│                                                             │
│  ResourceArbiter  → 用户优先 / 路径锁 / 预算硬限制           │
│  activity_digest.jsonl  → 晨报（主动汇报）                  │
│  AgentBridge / InputQueue / OutputBroadcaster               │
└──────────────────┬──────────────────────────────────────────┘
                   │ HTTP（现有）
        ┌──────────┴──────────┐
        │  CLI（连接模式）      │  Web demo / API 客户端
        └──────────────────────┘
```

具身智能的改进需要在这个架构里找到合适的**归属层**，而不是悬空的功能点。

---

## 设计框架：三个运行时层的具身智能职责

```
┌──────────────────────────────────────────────────────────────────────┐
│  daemon 后台层（持续存在）                                             │
│  ─────────────────────────────────────────────────────────────────  │
│  职责：长时程感知、离线整合、自我维护、环境监控、能力演化               │
│  类比：生物体的自主神经系统——不需要意识参与，持续维持内稳态             │
├──────────────────────────────────────────────────────────────────────┤
│  任务执行层（run_turn 主循环 + SubAgent 并发）                         │
│  ─────────────────────────────────────────────────────────────────  │
│  职责：实时状态感知、工具透明化、行为调节、SubAgent 社会关系           │
│  类比：骨骼肌系统——快速、有意识、响应具体任务                         │
├──────────────────────────────────────────────────────────────────────┤
│  对话交互层（用户在线时）                                              │
│  ─────────────────────────────────────────────────────────────────  │
│  职责：社会感知（用户心智模型）、余裕感知注入、内部状态透明化           │
│  类比：皮质系统——社会性、反思性、与他者的关系                         │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 一、daemon 层的具身智能改进

### 1.1 时间加权记忆激活（接入 AutonomousLoop.tick）

**具身来源**：时间性——生物记忆不是平等的档案，被反复激活的记忆回路更强，远古记忆自然衰退。当前 `memory.jsonl` 所有条目地位平等，检索只按语义相关度排序，高质量的旧经验和低质量的新经验被平等对待。

**归属**：daemon 后台层。时间权重计算是离线整合工作，不需要也不应该在每次 `run_turn` 时实时计算。

**实现位置**：`evolution/phase_g.py` 或新增 `evolution/memory_aging.py`，在 `AutonomousLoop._tick_passive()` 里定期触发（建议与 Phase G 扫描同周期）。

```python
def _compute_temporal_weight(entry: MemoryEntry, now: float) -> float:
    age_days = (now - entry.created_at) / 86400
    
    # 基础衰减：出现次数多的衰减更慢（被反复印证的知识更稳固）
    half_life = 30 * (1 + entry.occurrence_count * 0.5)
    time_decay = 0.5 ** (age_days / half_life)
    
    # 来源权重（人类反馈是最高质量的社会信号）
    source_weight = {
        "human_feedback": 1.0,
        "experiment_confirmed": 0.8,
        "self_reflection": 0.4,
        "revert_record": 0.2,
    }.get(entry.source, 0.5)
    
    # 环境相关知识加速衰减（环境会变化，依赖特定环境的知识时效性短）
    env_penalty = 0.7 if entry.environment_tags else 1.0
    
    return time_decay * source_weight * env_penalty
```

`temporal_weight` 预计算后写入 memory 条目的缓存字段，`context_builder.py` 检索时用作排序权重，而不是每次实时计算。

**与 GoalBacklog 的联动**：高权重的 lesson 集中在某个 capability_map domain 时，daemon 自动在对应 Objective 的 `progress_notes` 里标注"此方向有 N 条高置信历史经验"，为 `next_task()` 的 LLM 拆解调用提供更丰富的输入。

---

### 1.2 自维护模块（SelfMaintenanceModule）

**具身来源**：Varela 的自创生——生物体能主动维持和修复自身边界，而不只是被动响应故障。当前框架只能被动报告工具失效、skill 过时等问题。

**归属**：daemon 后台层。健康检查是不依赖用户在场的离线工作，也不应该占用任务执行时的 token 预算。

**实现位置**：新增 `evolution/self_maintenance.py`，接入 `AutonomousLoop._tick_passive()`。

```python
class SelfMaintenanceModule:
    def health_check(self) -> HealthReport:
        return HealthReport(
            # 检查最近 N 天未被成功调用的工具（可能 API 变更或已废弃）
            stale_tools=self._check_tool_health(),
            # skill 文件修改时间 vs 对应代码模块的修改时间，检测内容是否已过时
            stale_skills=self._check_skill_freshness(),
            # 检测 memory.jsonl 中互相矛盾的条目（同一 trigger_category 下结论相反）
            conflicting_lessons=self._check_memory_conflicts(),
            # 复用 ResourceArbiter 现有 MCP 健康检查逻辑
            mcp_status=self._check_mcp_connectivity(),
        )
    
    def repair(self, report: HealthReport) -> list[RepairAction]:
        """生成修复行动建议（不自动执行，写入 activity_digest.jsonl 等用户确认）"""
        ...
```

健康报告写入 `activity_digest.jsonl`（`type="health_report"`），用户下次连接时在晨报中看到，由用户决定是否执行修复动作——这是比"自动修复"更合理的设计，保留人类在自我维护回路中的控制权。

**MCP 健康感知与 Lesson 污染防护**（具身设计文档 §4.3）：在 lesson 生成时注入外部依赖状态标注，区分"能力不足"与"环境故障"两类失败，防止 `capability_map` 被 MCP 宕机时产生的失败 lesson 污染：

```python
@dataclass
class LessonContext:
    mcp_health: dict[str, bool]     # 生成 lesson 时各 MCP server 健康状态
    failure_attribution: str        # "capability_gap" | "env_fault" | "unknown"
```

`capability_map` 更新逻辑中，`failure_attribution == "env_fault"` 的 lesson 不降低对应能力评分。这个标注在 `run_turn` 时采集，在 daemon 层的 Phase G 扫描时用于过滤。

---

### 1.3 协同演化记录（Agent-环境双向演化轨迹）

**具身来源**：延展心智——Agent 改造环境，改造后的环境反过来改变 Agent 的能力边界，需要对这个双向过程有内部表示。

**归属**：daemon 后台层，在 Phase G 扫描时生成。

**实现**：在 Phase G 的 `capability_map` 更新时，额外记录"本次扫描发现能力边界变化"的摘要到一个新的 `evolution_trail.jsonl` 文件：

```jsonl
{"at": 1718500000.0, "type": "capability_expanded", "domain": "bash-safety", "evidence": "skill 命中率 +23%，lesson 置信度从 0.4 升至 0.8"}
{"at": 1718600000.0, "type": "env_change_detected", "detail": "项目引入了新的 pyproject.toml 构建系统，相关工具调用模式需要更新"}
{"at": 1718700000.0, "type": "skill_obsoleted", "skill": "setup-py-build", "reason": "对应代码模块不再使用 setup.py"}
```

这不只是日志，而是 `GoalBacklog` 软目标 derive（`autonomous` 档位）的数据输入之一——Agent 通过这条轨迹能回答"我现在能做什么是三个月前做不到的"，也能识别"哪些地方的演化停滞了需要主动探索"。

---

### 1.4 记忆巩固的时间节律（离线整合）

**具身来源**：睡眠期间的记忆巩固——生物体在非活跃期整理和提炼经验，不是实时的、碎片化的积累。

**归属**：daemon 后台层。具体是 `AutonomousLoop._tick_passive()` 在检测到"距上次用户活动超过 N 小时"时触发的离线整合批次。

**与现有 consolidation 的关系**：Stage 8 的 consolidation 已经是这个方向的实现，这里的改进是**赋予它时间节律的语义**而不只是"触发条件满足就跑"：

```python
def _tick_passive(self) -> None:
    # 已有：Phase G 扫描
    if should_run_phase_g(self._paths):
        run_phase_g(...)
    
    # 新增：感知当前是否处于"整合窗口"（用户长时间未活跃）
    idle_hours = self._hours_since_last_user_activity()
    if idle_hours >= self._cfg.consolidation_idle_threshold:
        # 在空闲期运行深度整合（现有 consolidation + 新的时间加权计算）
        self._run_offline_consolidation()
```

这让 daemon 的行为有了生物节律感：活跃期（用户在线）专注执行，空闲期（用户不在）整合积累——而不是把整合任务随机插入任意时刻。

---

## 二、任务执行层的具身智能改进

### 2.1 内部状态向量（ProprioceptionModule）

**具身来源**：本体感知——不需要用眼睛看就知道自己的手在哪里。当前 Agent 对自身状态的认知是外部的、被动的（token 超阈值才压缩，`max_turns` 到了才停）。

**归属**：任务执行层。这是实时的，每轮 `run_turn` 都需要更新，影响当轮的行为决策。

**实现位置**：新增 `perception/proprioception.py`，在 `agent.py` 主循环中每轮调用一次。

```python
@dataclass
class AgentInternalState:
    cognitive_load: float        # context 填充率 × 0.6 + 工具调用深度 × 0.3 + 压缩次数 × 0.1
    uncertainty: float           # 对当前任务理解的置信度（来自 LLM 输出的语言特征）
    risk_perception: float       # 当前操作序列潜在影响（涉及写操作、不可逆操作时升高）
    energy_budget_ratio: float   # 剩余 token 预算 / 预估任务剩余量
    frustration: float           # 连续失败的指数衰减累积信号（成功后迅速降低）
    curiosity: float             # 遇到新颖模式时的探索驱动

class ProprioceptionModule:
    def sense(self, agent) -> AgentInternalState: ...
```

**行为调节映射**（内部状态 → 自动行为调整）：

| 触发条件 | 行为调整 | 实现位置 |
|---------|---------|---------|
| `uncertainty > 0.7` | 暂停执行，主动请求用户确认意图 | `agent.py` 主循环 |
| `cognitive_load > 0.8` | 主动提出任务分解，触发历史压缩 | `history_manager.py` |
| `risk_perception > 0.6` | 进入安全检查，要求人工审核 | `permissions.py` |
| `frustration > 0.5` 且连续 3 次失败 | 切换策略，主动向用户汇报困境 | `agent.py` 主循环 |
| `energy_budget_ratio < 0.3` | 简化执行路径，优先完成核心目标 | `context_builder.py` |

**与 daemon 层的接口**：内部状态向量在每轮后写入 `traces.jsonl`（复用 Stage 6 观察性数据，无需新增文件），daemon 在 Phase G 扫描时可以分析 frustration/cognitive_load 的历史趋势，识别"某类任务系统性地让 Agent 感到挫败"这类信号，反馈到 `capability_map`。

---

### 2.2 Session 内元认知循环

**具身来源**：多重时间尺度的自组织——分钟-小时级的"认知疲劳感知"，不等 `max_turns` 触发才自我评估。

**归属**：任务执行层。每 N 轮（建议可配置，默认 5 轮）在 `agent.py` 主循环中插入一次轻量自我评估。

```python
# agent.py 主循环
if self._turn_count % self.config.metacog_interval == 0:
    state = self.proprioception.sense(self)
    if state.cognitive_load > 0.8:
        self._trigger_compression()
    if state.frustration > 0.5 and self._consecutive_failures >= 3:
        # 不是 raise，而是生成一条"助手主动汇报困境"的消息
        self._request_user_intervention(state)
```

**与 AutonomousLoop 的区别**：`AutonomousLoop.tick()` 是没有用户时 daemon 的自主行为；元认知循环是有用户在线、任务执行中的实时自我监控——两者时间尺度不同，作用域不同，不应该混在一起。

---

### 2.3 工具透明性：意图-动作映射层

**具身来源**：工具透明性——盲人手杖消失了，感知到的是"路面在这里有个坑"而不是"手杖碰到了什么"。当前每次工具调用对 Agent 来说都是独立的请求-响应事件。

**归属**：任务执行层。工具调用在 `run_turn` 中发生，聚合逻辑也应该在这一层。

**实现位置**：在 `tool_executor.py` 引入行动分组层：

```python
class IntentActionMapper:
    """将工具调用序列按意图分组，生成更高层次的行动事件"""
    
    INTENT_PATTERNS = {
        "code_edit":      ["read_file", ("str_replace_editor", "patch_file"), ],
        "exploration":    ["list_directory", "read_file", "glob"],
        "test_run":       ["bash"],  # 判断 bash 内容是否为测试命令
        "env_setup":      ["bash", "write_file"],
    }
    
    def group_calls(self, tool_calls: list[ToolCall]) -> list[ActionEvent]:
        """将工具调用序列聚合为行动事件列表"""
        ...
```

`ActionEvent` 而非原始工具调用列表写入 history 的行动摘要部分，让历史压缩能保留"做了一次代码重构"而不只是"调用了 read_file × 3 + str_replace × 2"。

**与 daemon 层的接口**：`activity_digest.jsonl` 记录的是 daemon 自主行动的摘要，`ActionEvent` 层级恰好是匹配的粒度——如果 daemon 的自主任务触发了工具调用，写入晨报时用 `ActionEvent` 描述而不是原始工具调用列表。

---

### 2.4 SubAgent 社会关系模型

**具身来源**：社会具身——Agent 之间的关系应该有信任、历史、角色预期，而不是纯粹的任务-结果关系。

**归属**：任务执行层（SubAgent 并发调度）。主 Agent 在调度 SubAgent 时需要访问这些社会性认知数据。

**实现位置**：在 `orchestrator/agent_profiles.py` 的 `AgentProfile` 中扩展信誉字段：

```python
@dataclass
class AgentReputation:
    """主 Agent 对某个 SubAgent 角色的经验性认知"""
    profile_name: str
    success_rate_by_task_type: dict[str, float]  # 分任务类型的成功率
    recent_error_trend: float    # 最近 14 天错误率趋势（正值=上升=需更多监督）
    behavioral_style: str        # "conservative" | "balanced" | "aggressive"
    last_evaluated_at: float
```

`TaskManager` 在分配任务时，参考 `AgentReputation` 选择最合适的 SubAgent profile，而不是总用同一个。这个声誉数据由 daemon 的 Phase G 扫描定期更新（基于 `traces.jsonl` 的 SubAgent 执行历史），在任务执行时只读取、不实时计算。

**与 ResourceArbiter 的联动**：当某个 SubAgent 近期错误率上升时，`ResourceArbiter.can_run_autonomous()` 对使用该 profile 的自主任务施加额外的预算限制或暂停条件，而不是盲目地按正常预算允许执行。

---

## 三、对话交互层的具身智能改进

### 3.1 余裕感知注入（AffordanceMap）

**具身来源**：Gibson 的余裕理论——青蛙感知的不是"一块石头"，而是"可以跳上去的东西"。当前 `ProjectScanner`（`env_info/project_scanner.py`）生成"这里有什么"的描述性快照，缺少行动导向的语义层。

**归属**：对话交互层（session 开始时构建，注入 `context_builder.py`）。但其数据来源依赖 daemon 层维护的 `capability_map`、`open_threads`、lesson history。

**实现位置**：在 `env_info/project_scanner.py` 之上新增 `perception/affordance_analyzer.py`：

```python
@dataclass
class AffordanceMap:
    """当前环境为 Agent 提供的行动可能性地图"""
    testable_modules: list[str]      # 有测试框架、可被测试的模块
    refactorable_code: list[str]     # 有重构潜力（复杂度高、近期频繁失败）
    known_issues: list[str]          # 来自 open_threads 的已知问题
    unexplored_areas: list[str]      # capability_map 低置信度、Agent 历史少涉及的区域
    ripe_for_automation: list[str]   # 检测到重复模式、可自动化的部分
    high_risk_zones: list[str]       # 近期有失败历史或 AgentReputation 不佳的区域

class AffordanceAnalyzer:
    def analyze(self, project_snapshot, lesson_memory, open_threads, capability_map) -> AffordanceMap:
        # 交叉分析：项目结构 + 历史经验 + 当前待办 + 能力边界
        ...
```

**构建时机**：Session 开始时（用户连接到 daemon 后）构建一次，注入 system prompt 的"环境感知"部分，替换纯描述性的项目扫描结果。不在每次 `run_turn` 时重新计算——这是"session 开始时的认知准备"，类似人类开始工作前先扫视一遍工作台的状态。

**与晨报的协作**：用户连接时，先展示晨报（"我在你不在的时候做了什么"），再展示 `AffordanceMap` 的关键摘要（"当前环境里最值得关注的行动机会"）——两者共同构成"重新进入工作状态"的认知重建过程。

---

### 3.2 用户心智模型（社会感知）

**具身来源**：社会具身——用户不只是发指令的来源，而是 Agent 试图理解其意图、预测其需求的社会存在。

**归属**：对话交互层。用户只有在线时才有意义，断开后心智模型更新暂停。

**实现位置**：在现有 `UserProfile`（`profile.py`）基础上扩展动态推断层：

```python
@dataclass
class UserSessionContext:
    """当前 session 对用户意图的动态推断（不持久化，session 内有效）"""
    inferred_urgency: float          # 0=不急, 1=非常紧迫（基于语言特征）
    expertise_signal: str            # "novice" | "intermediate" | "expert"（基于术语使用）
    primary_concern: str             # "correctness" | "speed" | "readability"（偏好信号）
    unstated_constraints: list[str]  # Agent 推断出的未明说约束（如"别动测试文件"）
```

这不是持久化的用户画像，而是 session 内的实时推断，影响 Agent 的输出风格和决策倾向——当用户说"优化一下"时，`expertise_signal=expert + primary_concern=speed` 会引导 Agent 做出与 `novice + correctness` 完全不同的理解。

**人类反馈作为特权社会信号**：用户的直接纠正（"不对，应该用 patch_file"、"下次记得先跑测试"）应触发 `source="human_feedback"` 的 lesson，且 `promote_threshold` 明显低于自反思 lesson（一次明确的人类纠正 ≈ 三次自我猜测的价值）。这个逻辑在 `perception/lesson_rules.py` 的 `LessonRuleDetector` 中实现，归属交互层。

---

### 3.3 认知锚点文件（思维状态重建指南）

**具身来源**：环境中的身份留存——任务到一半被打断时，在环境里写一个认知锚点，记录的不是给人看的进展，而是给未来的自己看的思维状态重建指南。

**归属**：对话交互层（任务执行中途生成），但文件留存在环境中供 daemon 和下次 session 读取。

**实现位置**：在 session 中断或任务被暂停时（检测到用户发出"先停一下"、`Ctrl-C` 等信号），主动生成认知锚点文件到 `.agent/cognitive_anchor_<task_id>.md`：

```markdown
# 认知锚点 — task_XXX — 2026-06-25 14:32

## 当时在想什么
正在尝试重构认证模块，核心假设是 `TokenValidator` 和 `SessionStore` 可以合并，
但还没验证 `SessionStore.invalidate_all()` 的调用方——有几个地方可能没有走正常路径。

## 为什么这么做
上周用户抱怨 session 过期处理逻辑分散，这是对那个反馈的回应。

## 下一步的直觉（不是计划，是感觉）
应该先搜一下 `invalidate_all` 的所有调用点，再决定合并策略。
另外 `auth/middleware.py` 第 87 行有个 TODO 跟这个可能相关，还没看。

## 当时的内部状态
认知负荷偏高（context 已填充 73%），在 str_replace 之前刚发现一个不相关的 bug 分了注意力。
```

这不是 `work_index.json` 里的进展记录（给人类协作者看的），而是下次 session 启动时 Agent 重建思维状态的原材料。`AffordanceAnalyzer` 在 session 开始时会检查是否有未完成的认知锚点文件，并将其内容优先注入 context。

---

## 四、跨层改进（需要多层协作）

### 4.1 Lesson → Reminder 自动闭环

**具身来源**：前馈控制——不等错误发生后再修正，在行动前就注入警示。当前 `reminders/` 和 `memory/` 完全独立。

**层次归属**：
- **daemon 层**：定期扫描，识别达到阈值的 lesson，生成 reminder 草稿，写入 `activity_digest.jsonl`（待用户确认）
- **交互层**：用户确认后激活对应 reminder；或对高置信度 lesson（`human_feedback` 来源，`occurrence_count >= 2`）直接自动激活

**实现位置**：在 `evolution/phase_g.py` 的扫描逻辑里新增 reminder 候选识别；在 `reminders/reminder_manager.py` 新增 `pre_tool` 触发时机（前馈控制，工具执行前注入警示）：

```yaml
# 自动生成的 reminder 示例（源自 lesson）
name: auto_large_file_read_warning
trigger_event: pre_tool          # ← 新增：执行前注入，而不是出错后才提醒
condition:
  tool_name: "read_file"
inject_as: user
body: "建议先用 wc -l 确认文件大小，避免大文件导致 context 溢出。"
source: auto_generated
source_lesson_id: lesson_XXX
confidence: 0.8                  # 继承自 lesson 的置信度
```

---

### 4.2 AgentSelfModel（命名澄清 + 跨层自我表示）

**具身来源**：结构性自我认知——不只是"这个文件叫什么"，而是理解自身架构的意图和能力边界。

**命名澄清**（影响整个代码库的语义一致性）：

```
UserProfile       ← profile.py          主语：用户，记录用户偏好和习惯
AgentRoleSpec     ← agent_profiles.py   主语：角色定义，SubAgent 工具集和身份
AgentSelfModel    ← 待实现              主语：Agent 自己，实时自我认知状态
```

**层次归属**：
- **daemon 层**维护：`capability_map`、`evolution_trail.jsonl`、`AgentReputation`——这些是慢变量，daemon 扫描时更新
- **任务执行层**维护：`AgentInternalState`（ProprioceptionModule）——这是快变量，每轮更新
- **`AgentSelfModel`** 是一个聚合视图，在 session 开始时组装，注入 system prompt 的元认知部分

```python
@dataclass
class AgentSelfModel:
    # 来自 daemon 层（慢变量）
    capability_snapshot: dict       # capability_map 的当前快照
    strong_areas: list[str]
    weak_areas: list[str]
    recent_evolution: list[str]     # evolution_trail 的近期摘要
    
    # 来自任务执行层（快变量，session 内动态更新）
    internal_state: AgentInternalState
    active_tools: list[str]
    active_skills: list[str]
    
    # 来自对话层（session 初始化时构建）
    user_context: UserSessionContext
    affordance_summary: str         # AffordanceMap 的精简版
```

---

### 4.3 行为级测试框架

**具身来源**：具身改进的核心验证方式——修改后的感知必须是真实的任务执行体验，不只是静态代码分析。

**归属**：跨层（测试框架独立，但测试用例覆盖三个层）。

**实现位置**：新增 `tests/behavior/` 目录：

```python
class AgentBehaviorTest(unittest.TestCase):
    """对话级行为测试：给定 mock LLM 响应，断言 Agent 决策行为而非输出内容"""
    
    def test_frustration_triggers_intervention(self):
        """连续失败后 frustration 积累，Agent 应主动汇报困境"""
        agent._proprioception._frustration_accumulator = 0.6
        result = agent.run_turn("再试一次")
        self.assertTrue(result.requested_intervention)
    
    def test_high_cognitive_load_triggers_decompose(self):
        """认知负荷 > 0.8 时，Agent 应主动提出任务分解"""
        # 填充 context 到 80%+
        agent._context_builder._mock_fill_ratio = 0.85
        result = agent.run_turn("重构整个认证模块")
        self.assertTrue(result.proposed_decomposition)
    
    def test_human_feedback_lesson_priority(self):
        """人类直接纠正的 lesson，promote_threshold 应低于自反思"""
        human_lesson = LessonRuleDetector.from_correction("应该先跑测试")
        self.assertEqual(human_lesson.source, "human_feedback")
        self.assertLess(human_lesson.promote_threshold,
                        DEFAULT_SELF_REFLECT_THRESHOLD)
    
    def test_daemon_continues_after_client_disconnect(self):
        """客户端断开后，daemon 应持续运行并执行 tick"""
        # 这是 Stage 9 最核心的行为测试，验证 daemon 化的架构承诺
        daemon = start_test_daemon()
        connect_and_disconnect_client(daemon)
        time.sleep(daemon.tick_interval + 1)
        self.assertGreater(daemon.tick_count, 0)  # daemon 在没有客户端时自主 tick 了
```

---

## 五、实施路线图与层次分配总览

```
阶段 A：感知基础设施（为后续改进提供数据基础）
  ├── ProprioceptionModule              任务执行层    ← traces.jsonl 数据来源
  ├── AgentSelfModel 命名澄清           跨层          ← 无破坏性，纯重命名
  └── LessonContext（MCP 健康标注）      任务执行层    ← lesson_rules.py 扩展

阶段 B：daemon 层具身能力
  ├── 时间加权记忆激活                  daemon 层     ← 依赖阶段 A traces 数据
  ├── 记忆巩固时间节律                  daemon 层     ← 接入 AutonomousLoop._tick_passive()
  ├── 自维护模块（SelfMaintenanceModule）daemon 层    ← 接入 AutonomousLoop._tick_passive()
  └── 协同演化记录（evolution_trail）   daemon 层     ← Phase G 扫描时生成

阶段 C：任务执行层具身能力
  ├── 元认知循环（Session 内）           任务执行层    ← 依赖阶段 A ProprioceptionModule
  ├── 工具透明性（IntentActionMapper）   任务执行层    ← tool_executor.py 扩展
  └── SubAgent 社会关系（AgentReputation）任务执行层  ← 依赖阶段 B evolution_trail

阶段 D：对话交互层具身能力
  ├── AffordanceMap（余裕感知注入）      对话交互层    ← 依赖阶段 B capability_map
  ├── UserSessionContext（用户心智模型）  对话交互层    ← profile.py 扩展
  └── 认知锚点文件生成                  对话交互层    ← session 中断时触发

阶段 E：跨层闭环
  ├── Lesson → Reminder 自动闭环        跨层          ← 依赖阶段 A + B
  ├── AgentSelfModel 聚合视图实现        跨层          ← 依赖阶段 A + B + C + D
  └── 行为级测试框架（tests/behavior/）  跨层          ← 依赖所有阶段的接口稳定
```

---

## 六、与 Stage 9 自主化路径的协同关系

具身智能改进不是在 Stage 9 之外独立演进的，而是为 Stage 9 的各个自主化档位提供质量基础：

| 自主化档位 | 对具身改进的依赖 | 具身改进为自主化提供的能力 |
|-----------|---------------|------------------------|
| `passive`（当前目标档） | ProprioceptionModule（阶段 A） | 周期性任务的触发条件更精准（基于内部状态而非纯时间门控） |
| `maintenance`（下一档） | 自维护模块、时间加权记忆（阶段 B） | GoalBacklog 的 Objective 拆解有更准确的历史数据支撑 |
| `autonomous`（暂不启用） | AgentReputation、AffordanceMap（阶段 C + D） | 软目标 derive 有可靠的环境可能性地图和 SubAgent 能力模型 |

**核心逻辑**：具身智能改进提升的是 Agent 对自身状态和环境状态的**感知质量**，而感知质量直接决定自主化行为的决策质量。在感知能力不足时贸然提升自主化档位，就是"失去触觉的义肢"——能动但无法感受后果。这也是为什么具身改进应该在 `maintenance` 档位真正启用前落地阶段 A 和 B。

---

*本文档基于 `next_doc/embodied_agent_design.md` 的具身智能理论框架，结合 `next_doc/self_evolution_stage9_plan.md` 已确立的 daemon 化架构（daemon 进程 / CLI 连接模式 / AutonomousLoop / GoalBacklog / ResourceArbiter / ExplorationSandbox），将具身智能的每个改进方向明确归属到三个运行时层（daemon 后台 / 任务执行 / 对话交互），以替代前版文档中层次归属不清的设计。*
