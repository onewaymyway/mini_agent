# mini_agent 自我演化改造 Stage 9 详细方案 —— Phase H：自主运行时

> 本文档承接 `next_doc/self_evolution_stage4plus_plan.md` 第 506-541 行"Stage 9（决策点，非常规人天估算）"，
> 是该节"若决定启动，应该怎么做"八步预案的**细化版**。设计依据 `next_doc/self_evolution_design.md`
> 第 7 章（Phase H：自主运行时）。
>
> **定位延续不变**：Stage 9 不是一个排期 Stage，是一个决策点。本文档把"决定启动后怎么做"
> 从八条一句话预案细化成可执行的子任务、数据结构、接口改动和验证标准，**但不改变其决策点性质**——
> 本文档的存在本身不代表 Stage 9 已经被批准启动，第九节"决策记录"留空，由实际决策时填写。
>
> 核查时间：2026-06（对应代码快照：Stage 0-8 全部完成，`tests/` 全绿）。本文档所有对现状的描述
> 均逐项核对了 `src/mini_agent/api/bridge.py`、`orchestrator/task_manager.py`、
> `evolution/state_repo.py`、`evolution/phase_g.py`、`agent.py` 的源码，不是从设计文档直接转述。

---

## 一、为什么需要这份细化文档——上级计划文档的颗粒度不够实施

`self_evolution_stage4plus_plan.md` 第 9.1 节给出的是八条"建议的内部顺序"，每条一句话。这个颗粒度
对"决策会议"够用（回答"大致要做什么、顺序对不对"），但不足以直接动手实施——例如第 3 条
"`AutonomousLoop` 调度器：先只接 Stage 8 已有的周期性任务"，没有回答：

- `AutonomousLoop` 是新进程还是现有 `AgentRunner` 线程的扩展？怎么和现有 `TaskManager._scheduler_loop`
  共存而不是重复造一个调度器？
- "只接 Stage 8 已有任务"具体是指哪几个函数？怎么从"SessionEnd 时间门控触发"变成"daemon tick 触发"？
- `passive` 档位"调度器能触发已有任务但不创建新 Goal"，这个边界在代码里具体卡在哪一行？

本文档逐条回答这类问题，把八步预案拆解为 9 个子阶段（9.1-9.9，对应原八步但拆得更细，第 7 步
探索机制独立成 9.8，第 8 步升级 autonomous 维持原状作为 9.9 开放项），每个子阶段给出：

1. **现状核对**（这一块当前代码里有没有、有多少能复用）
2. **具体改动**（新增什么文件/类/方法，改动什么现有接口）
3. **与现有机制的接口**（强调复用，不重复造轮子，呼应前两份文档"改造原则 1"）
4. **验证标准**（可独立验证，呼应"改造原则 3"）
5. **风险与边界**（这一步具体放大了什么风险，需要什么兜底）

---

## 二、启动前置条件复核（细化 9.0 清单）

`self_evolution_stage4plus_plan.md` 9.0 节给了四条清单，本节逐条核对当前代码现状，确认每一条
现在处于什么状态，而不是泛泛重复"建议评估"：

| 前置条件 | 当前状态（逐项核对结果） |
|---|---|
| Stage 4-8 全部完成并稳定运行一段观察期 | ✅ 代码层面全部完成（`tests/` 含 `test_workdir_knowledge.py`/`test_global_knowledge.py`/`test_observability.py`/`test_phase_g.py` 等全绿）。**"稳定运行的观察期"是运营判断，不是工程判断**，本文档不替代——建议决策时检查 `~/.agent/self_profile.json` 的 `evolution_state.lifetime_lessons_generated`、`activity_log.jsonl` 的条数是否已经积累到三位数量级，作为"观察期是否足够"的量化参考起点，而非凭直觉 |
| 用户/团队明确确认产品定位允许"持续存在、有自己议程的 agent" | ⬜ 待决策。本文档第九节留空白决策记录区，要求显式填写"是/否/暂缓"+ 日期 + 决策人 |
| Stage 7 任务池 13.2（降级重试链）+ 15.3（任务降级）已完成 | ✅ 已核实：`orchestrator/task_manager.py` 的 `_try_demotion()`/`_resubmit_demoted()` 已实现两阶段降级（profile fallback → scope demotion），`Task.fallback_profiles`/`demotion_scope` 字段已存在于 `orchestrator/task.py` |
| `autonomy_level` 默认从 `passive` 起步，团队认可"逐档开放、可随时降级"的升级路径 | ⬜ 待决策，但**技术上已具备条件**：Stage 5 的 `GlobalKnowledgeConfig`/`self_profile.json` schema 已经把 `autonomy_level` 字段位置留好（当前 `self_profile.json` 尚无 `operating_state.autonomy_level` 字段本身——核对 `perception/global_knowledge.py` 的 `SelfProfile` dataclass 发现目前只有 `evolution_state`/`resource_budget` 等顶层结构，`autonomy_level` 字段本身需要本 Stage 9.2 新增，**不是"已经声明只是没用"，是真的还没声明**，纠正前序文档可能给人的印象） |

**核对结论与前序文档的一处修正**：`self_evolution_stage4plus_plan.md` 5.1 节验证标准写"`autonomy_level`
默认值必须是 `passive`"，但实际查 `perception/global_knowledge.py` 的 `SelfProfile`/`OperatingState`
dataclass 定义，**当前没有 `autonomy_level` 字段**——Stage 5 完成记录里也没有提到补充这个字段。
这是本文档核查源码发现的一处偏差，9.2 节会补上这个字段，按 9.0 清单的要求处理（默认 `passive`）。

---

## 三、总体技术路线：复用清单与新增清单

延续前两份文档"不重复造轮子"的改造原则，先列清楚 Phase H 能复用什么、必须新增什么，
作为后面九个子阶段的总览（细节见各子阶段）：

### 3.1 可直接复用，不改动接口

| 现有机制 | 复用方式 |
|---|---|
| `evolution/phase_g.py` 的 `prune_skills`/`build_capability_map`/`check_scope_promotion`/`rhythm_is_allowed` | AutonomousLoop 的周期性任务直接调用这几个函数，函数签名不变 |
| `perception/workdir_knowledge.py` 的 `WorkThread` 数据结构 | Goal Backlog 的 Objective 节点直接引用 `work_thread_ref`，不重新定义"进展跟踪"的字段 |
| `orchestrator/task_manager.py`/`orchestrator/task.py` 的 `Task`/`TaskRecord`/`SubAgent` | Goal Backlog 拆解出的 Task 直接构造现有 `Task` 对象提交给现有 `TaskManager`，不重新实现任务执行 |
| `evolution/state_repo.py` 的 `StateRepo.apply()` | 9.5 节只新增一个 `initiator` 参数，不重写写入逻辑本身 |
| `api/bridge.py` 的 `InputQueue`/`AgentBridge` | 9.3 节扩展 `_TurnCommand`，不另起一套队列机制 |
| `orchestrator/agent_profiles.py` 的 `AgentProfile`/`AgentProfileLoader` | 9.1 节"autonomous-worker"角色直接用现有机制定义一个新 profile 文件，不改 profile 加载代码 |

### 3.2 必须新增（本文档核心工作量）

| 新模块/文件 | 对应子阶段 |
|---|---|
| `perception/global_knowledge.py` 的 `SelfProfile` 补充 `operating_state.autonomy_level` 字段 | 9.2 |
| `.agent/goals.json` 数据结构 + `perception/goal_backlog.py` | 9.3 |
| `evolution/autonomous_loop.py`（`AutonomousLoop` 类） | 9.4 |
| `evolution/resource_arbiter.py`（资源仲裁：用户优先、文件锁、预算硬限制） | 9.5 |
| `.agent/activity_digest.jsonl` + 晨报生成逻辑 | 9.5 |
| `StateRepo.apply()` 新增 `initiator` 参数 + tier 上浮规则 | 9.6 |
| `evolution/experiment.py`（`Experiment` 实体 + 预注册纪律 + 反事实重放） | 9.8 |
| CLI：`/agent autonomy <level>`、`/agent daemon start|stop|status`、`/agent goals list|add` | 贯穿各节 |

---

## 四、9.1 自我模型补全：`autonomy_level` 落地

> 对应上级文档 9.1 第 1 步，细化原计划"在 Stage 5.1 `self_profile.json` 基础上补充 `passive` 档位语义"。

### 4.1 现状核对

查 `perception/global_knowledge.py`：

```python
# 当前 SelfProfile 相关结构（核实结果，省略部分字段）
@dataclass
class OperatingState:
    active_project: str = ""
    last_active_at: float = 0.0
    total_sessions_lifetime: int = 0
    total_projects_worked: int = 0
    # 没有 autonomy_level 字段
```

`autonomy_level` 字段**确实缺失**（见第二节核对结论），需要新增，不是改已有字段的默认值。

### 4.2 具体改动

1. `OperatingState` dataclass 新增字段：
   ```python
   autonomy_level: str = "passive"  # passive | maintenance | autonomous
   ```
2. `SelfProfile` 的序列化/反序列化（`to_dict()`/`from_dict()` 或等价方法）需要同步处理新字段的
   向后兼容——**已存在的 `self_profile.json`（Stage 5 已上线的用户）反序列化时这个字段不存在，
   必须有默认值兜底**，不能因为字段缺失而解析失败。这是本节真正的工程重点，而不是加字段本身。
3. 新增校验函数 `validate_autonomy_level(value: str) -> bool`，只接受三个枚举值，放在
   `perception/global_knowledge.py` 内（与该文件现有的字段级校验风格一致，不新开文件）。
4. CLI 命令 `/agent autonomy [passive|maintenance|autonomous]`：无参数时显示当前档位，
   有参数时触发修改。**修改本身的特殊性**（呼应设计文档 7.9 节）：
   - 这是 T1 量级的改动（声明式配置，改的是单个枚举字段）
   - 但**强制走人审**，不能被 Stage 8 6.7 节"T1 eval 通过可自动合并"规则覆盖——具体实现是
     `/agent autonomy` 命令本身只能由用户在 CLI 里手动输入触发，**不注册为 agent 可调用的工具
     （不放进 `tools/` 目录、不用 `@tool()` 装饰）**，从机制上排除被 agent 自己调用的可能性，
     而不是靠 prompt 约束"agent 不应该自己改这个值"。这是本子阶段最关键的安全设计决策。
   - 修改时打印明确的二次确认提示（"切换到 maintenance 将启用后台周期性任务自主触发，确认？"），
     `autonomous` 档位的确认提示需要更醒目的警告文案。
5. `identity.constraints_ref` 字段（设计文档 7.2 节）补充实现：固定写入
   `"CLAUDE.md + scripts/protected_paths.py (T3) + StateRepo tier 校验"`，作为自我模型里
   "我的行为边界写在哪"的显式引用，首次创建 `self_profile.json` 时写入，不需要动态计算。

### 4.3 与现有机制的接口

- 不改变 `self_profile.json` 现有字段的语义，只新增字段，Stage 5 已完成的读写逻辑不受影响
- `context_builder.py` 的 Global 层注入（Stage 5.5 已实现）需要补充一行：`autonomy_level`
  当前不是 `passive` 时，在注入的 `self_assessment` 精简块里附带提示
  （"当前自主等级：maintenance，daemon 会自主执行周期性维护任务"），让用户每次开 session 都能
  看到当前等级，不需要专门查命令

### 4.4 验证标准

- 新建 `self_profile.json`（或加载 Stage 5 时代产出的旧文件）后，`autonomy_level` 字段存在且为 `passive`
- 手动构造一个**没有** `autonomy_level` 字段的旧版 `self_profile.json`，加载后不抛异常，字段按默认值补全
- `/agent autonomy maintenance` 触发后二次确认，确认后字段正确写入；该命令搜索整个 `tools/` 目录
  确认未被注册为工具（防御性验证："agent 不能自己调用"这条规则不是文档声明，是真实搜不到对应工具名）

### 4.5 风险与边界

最大风险不是"字段写错"，是"agent 通过某种间接路径自己改了这个值"（比如把它当成普通配置文件用
`write_file` 工具改）。`StateRepo.apply()` 在 9.6 节会把 `~/.agent/self_profile.json` 加入
T3 受保护清单的候选——这是本节遗留给 9.6 节处理的依赖，此处先标注。

---

## 五、9.2 Goal Backlog：跨会话目标层级

> 对应上级文档 9.1 第 2 步。

### 5.1 现状核对

当前**没有任何跨 session 持久的目标层级**。`orchestrator/task_manager.py` 的调度是 session 内 DAG，
session 结束后 `TaskRecord` 仍写在 `tasks/<id>/manifest.json` 里（Stage 0.2 已完成），但没有任何
"长期目标"概念把多个 session 的多个 task 串起来。Stage 4.3 的 `work_index.json` 的 `WorkThread`
是最接近的现有结构，但语义是"项目内的工作线索聚合"，不是"目标管理"——`WorkThread` 没有
`status=blocked/abandoned`，没有用户设定 vs agent 自主 derive 的来源区分。

### 5.2 数据结构：`.agent/goals.json`

```json
{
  "version": 1,
  "goals": [
    {
      "id": "goal_001",
      "level": "goal",
      "title": "提升 bash 工具调用的安全性",
      "source": "user",
      "status": "active",
      "created_at": 1718000000.0,
      "last_touched_at": 1718500000.0,
      "progress_notes": "已完成 bash-safety skill 晋升，待观察实际拦截效果",
      "parent_id": null,
      "children_ids": ["obj_001"]
    },
    {
      "id": "obj_001",
      "level": "objective",
      "title": "观察 bash-safety skill 上线两周后的拦截命中率",
      "source": "agent_derived",
      "status": "active",
      "created_at": 1718100000.0,
      "last_touched_at": 1718500000.0,
      "progress_notes": "",
      "parent_id": "goal_001",
      "children_ids": [],
      "work_thread_ref": "wt_005"
    }
  ]
}
```

字段设计要点（逐条对应设计文档 7.3 节，标注哪些是本文档新增的细化）：

- `level`：`goal | objective`（Task 层级复用现有 `Task`/`TaskRecord`，不在本文件里重复定义）
- `source`：`user | agent_derived`——直接落地设计文档"硬目标/软目标"的区分
- `work_thread_ref`：**这是与 Stage 4.3 `work_index.json` 的关键接口**，Objective 节点通过
  这个字段直接引用已有的 `WorkThread`，复用其 `cumulative_progress`/`next_suggested` 字段，
  不在 `goals.json` 里重复维护进展文本——这是上级文档 9.1 第 2 步"WorkThread 是 Objective 节点
  的自然前身"这句话的具体落地方式：**不是把 WorkThread 数据复制过来，是用引用关联**

### 5.3 具体改动

1. 新增 `perception/goal_backlog.py`：
   - `Goal`/`Objective` dataclass（或统一一个 `GoalNode` dataclass 用 `level` 区分，
     与 `WorkThread` 现有的"一个 dataclass + 枚举字段区分类型"风格一致）
   - `GoalBacklog` 类：`load()`/`save()`（原子写，tmp + `os.replace`，与 Stage 4/5 现有四个
     JSON 文件的落盘方式一致，**不走 `StateRepo.apply()`**——这是纯运行时状态而非
     "代码/配置改动"，性质与 `work_index.json` 一致，不属于安全网治理范围）
   - `has_actionable_work() -> bool`：是否存在 `status=active` 且 `level=objective` 的节点，
     供 9.4 节 `AutonomousLoop.tick()` 直接调用（对应设计文档 7.4 节伪代码第 2 行）
   - `next_task() -> Optional[Task]`：从最高优先级的 active Objective 拆解出一个具体 `Task`
     对象（复用 `orchestrator/task.py` 的 `Task` 类），**这是本子阶段唯一涉及"拆解逻辑"
     需要 LLM 判断的部分**——调用一次轻量 LLM（参考 Stage 4.2 `timeline.jsonl` 反思调用的
     独立轻量调用模式），输入该 Objective 的 `title`/`progress_notes`/关联 WorkThread 的
     `next_suggested`，输出一个具体可执行的 Task 描述
2. CLI 命令 `/agent goals list|show <id>|add <title> [--level goal|objective] [--parent <id>]`：
   `add` 子命令默认 `source="user"`（用户手动添加），agent 自主 derive 的 Goal/Objective
   不通过这个命令产生，是 9.4 节 `AutonomousLoop` 内部逻辑直接写入（但必须出现在 9.5 节晨报里，
   不能静默产生）

### 5.4 与现有机制的接口

- `next_task()` 产出的 `Task` 对象提交方式与现有 `TaskManager.submit()`（或等价接口，需核对
  实际方法名）完全一致，`AutonomousLoop` 不直接操作 `TaskRecord`/`SubAgent`，只通过
  `TaskManager` 的现有公开接口提交任务——**避免在调度层之上又长出一个调度层**，
  `AutonomousLoop` 的角色是"决定提交什么任务"，不是"决定怎么执行任务"
- Task 执行完成后（`DONE`/`FAILED`），需要一个回调把结果写回对应 Objective 的 `progress_notes`
  和关联 `WorkThread` 的 `cumulative_progress`——复用 Stage 3.3 已有的 SubAgent 终态通知机制
  （`_handle_terminal()`），新增一段处理逻辑而非另起回调链

### 5.5 验证标准

- 手动在 `goals.json` 写入一个 active Objective 关联到一个已有 active WorkThread，调用
  `has_actionable_work()` 返回 `True`，调用 `next_task()` 产出的 Task 描述与 WorkThread 的
  `next_suggested` 字段语义吻合
- 提交 `next_task()` 产出的 Task 给 `TaskManager` 执行完成后，检查对应 Objective 的
  `progress_notes` 被正确更新
- `autonomy_level=passive` 时（9.4 节实现后联合验证）：`has_actionable_work()` 即使返回
  `True`，`AutonomousLoop.tick()` 也不会调用 `next_task()`——这是"档位边界卡在哪一行代码"
  的具体验证点，呼应第一节提出的问题

### 5.6 风险与边界

`next_task()` 的 LLM 拆解调用如果产出质量差（任务描述太模糊、太宏大），会导致后续 Task
执行失败率高，进而触发大量降级重试（Stage 7 的 13.2）。建议拆解 prompt 显式要求"产出可在
单个 Task 内完成、有明确验收标准的具体描述"，并在 `autonomous` 档位真正启用前，先用
`passive`/`maintenance` 档位手动跑若干次 `next_task()` 人工评估产出质量，而不是直接信任
LLM 拆解的第一版 prompt。

---

## 六、9.3 AutonomousLoop：调度器骨架

> 对应上级文档 9.1 第 3 步，要求"先只接 Stage 8 已有的周期性任务，`passive` 档位不创建新
> Goal/Objective，只验证调度器本身能正确触发已有任务"。

### 6.1 现状核对：daemon 化需要的两个前提，逐项核实当前缺什么

**前提一：长驻进程**。`api/server.py` 的 `AgentRunner` 后台线程 + `api/bridge.py` 的
`InputQueue.dequeue(timeout=1.0)` 已经是一个轮询循环（每秒检查一次队列），**这本身已经是
一个 tick 循环的雏形**，只是目前每次 tick 只做"有没有新用户消息"这一件事。

**前提二：合成任务能进队列**。`InputQueue._TurnCommand` 当前只有 `turn_id`/`message`/
`submitted_at` 三个字段（核实自 `api/bridge.py` 第 140-146 行），**没有 `initiator` 字段**，
`enqueue()` 方法签名里也没有区分"谁发起的"——这是设计文档 7.7 节要求新增、当前确实缺失的部分。

### 6.2 具体改动

**不新起一个独立进程**，复用现有 `AgentRunner` 循环，原因：避免"两套长驻循环各自轮询，
互相不知道对方状态"的架构风险，`AutonomousLoop` 作为 `AgentRunner` 循环内部的一个新分支，
与"检查用户消息"分支并列。

1. `InputQueue._TurnCommand` 新增字段 `initiator: str = "user"`（`user | scheduled | autonomous`），
   `enqueue()` 方法签名新增 `initiator` 参数，默认值 `"user"`（向后兼容现有所有调用点不需要改）
2. `TurnInfo`（`api/models.py`）同步新增 `initiator` 字段，**这是 7.6 节晨报能区分"用户做的"
   和"agent 自主做的"的数据基础**
3. 新增 `evolution/autonomous_loop.py`：

   ```python
   class AutonomousLoop:
       """
       不是独立进程，是 AgentRunner 循环内部的一个 tick 分支。
       __init__ 接收 GoalBacklog、InputQueue、AgentPaths、AppConfig 的引用，
       不持有自己的线程，由调用方（AgentRunner 循环）决定 tick 频率。
       """
       def __init__(self, *, goal_backlog, input_queue, paths, cfg): ...

       def tick(self) -> None:
           autonomy_level = self._get_autonomy_level()  # 读 self_profile.json
           if autonomy_level == "passive":
               self._tick_passive()   # 只做 9.3 范围：周期性任务，不动 Goal Backlog
               return
           if autonomy_level == "maintenance":
               self._tick_maintenance()  # 9.3 范围 + 9.8 探索预算分配
               return
           self._tick_autonomous()  # 9.9 范围，本 Stage 暂不实现内部逻辑

       def _tick_passive(self) -> None:
           """[Stage 9.3 范围] 只检查 Stage 8 已有周期性任务是否到期，
           不读取、不创建 Goal/Objective。"""
           from mini_agent.evolution.phase_g import should_run_phase_g, run_phase_g
           if should_run_phase_g(self._paths):
               report = run_phase_g(self._paths, ...)
               self._record_for_digest(report)  # 9.5 节晨报数据来源
   ```

4. **`passive` 档位的边界在代码里具体卡在哪一行**：`tick()` 方法里
   `if autonomy_level == "passive": self._tick_passive(); return`——`_tick_passive()`
   方法体内**不引用 `GoalBacklog` 任何方法**，这是边界的物理体现，不是靠注释承诺。
   `maintenance`/`autonomous` 分支才会调用 `goal_backlog.has_actionable_work()`。
5. `AgentRunner` 循环（具体改动点在 `api/server.py`，需核对实际循环方法名）的现有轮询逻辑
   新增一行：每 N 次轮询（建议可配置，默认对应"现有 1 秒轮询的第 60 次"，即约每分钟一次，
   不需要单独的计时器）调用一次 `autonomous_loop.tick()`。**轮询频率与"周期性任务的实际触发
   频率"是两个不同的概念**——`tick()` 调用频率可以是分钟级，但 `should_run_phase_g()` 内部
   的 24h 间隔判断不变，分钟级 tick 只是"检查一次是否到期"，不意味着任务真的分钟级触发。

### 6.3 与现有机制的接口

- `_tick_passive()` 直接复用 Stage 8 `evolution/phase_g.py` 的 `should_run_phase_g`/`run_phase_g`，
  **这是从"SessionEnd 时间门控"到"daemon tick 时间门控"的迁移**——`phase_g_rhythm.json` 的
  `_last_run_at` 机制不变，只是检查时机从"用户退出 session 时"变成"daemon 每分钟 tick 一次"，
  对用户而言，行为差异是：以前必须等用户主动退出 session 才可能触发，现在即使用户一直挂着
  CLI 不退出，daemon 也能在后台按时触发——**这正是 daemon 化的核心价值，不是装饰性改动**
- 是否需要新增 CLI 命令让用户感知 daemon 是否在跑：`/agent daemon status`，显示
  "daemon tick 运行中，上次 tick 于 N 秒前，autonomy_level=passive"

### 6.4 验证标准

- `autonomy_level=passive` 时，连续运行 daemon 超过 `phase_g` 的 24h 间隔（测试中可调小间隔
  参数模拟），不需要任何 session 退出动作，`phase_g_rhythm.json` 的 `_last_run_at` 自动更新——
  这是验证"不依赖用户在场也能触发"的关键测试，必须是这个子阶段的**第一个**验证项，
  而不是验证"参数传递正确"这类琐碎细节
- `autonomy_level=passive` 时，`goal_backlog.json` 即使包含 active Objective，多次 tick 后
  文件内容不变（验证边界确实生效）
- 模拟一条 `initiator="autonomous"` 的合成任务进入 `InputQueue`，与一条 `initiator="user"`
  的真实用户消息同时在队列里，验证用户消息被优先处理（为 9.5 节资源仲裁做前置验证）

### 6.5 风险与边界

最大风险是"轮询频率提高后 CPU/IO 占用增加"——`tick()` 内部除了到期判断之外不应有任何
重计算，`should_run_phase_g()` 本身只读一个小 JSON 文件的时间戳字段，开销可忽略；
真正的扫描逻辑（`run_phase_g`）只在到期判断通过后才执行，这一点与原 Stage 8 设计一致，
本节没有改变这个开销模型，只是改变了"谁来问到期了没有"。

---

## 七、9.4 并发与资源仲裁、主动汇报（晨报）

> 对应上级文档 9.1 第 4 步："在调度器开始处理真正的自主任务之前必须先有这两项，否则
> '自主任务和用户冲突'以及'用户不知道 agent 做了什么'两个风险点没有兜底"。

本节是 9.3 节 `_tick_maintenance()`/`_tick_autonomous()` 真正处理 Goal Backlog 任务之前
的**强制前置依赖**，不可跳过，原因直接引用上级文档原话。

### 7.1 资源仲裁

设计文档 7.5 节三条仲裁规则，逐条给出实现方式：

1. **用户交互优先**：9.3 节已在 `InputQueue` 层面验证了"用户消息优先"，本节补充
   "正在执行的自主任务遇到用户消息时暂停"——这需要 `TaskManager` 提供一个"暂停某个
   task_id，状态保存"的接口。**核实当前 `TaskManager` 没有暂停接口**（只有
   `CANCELLED`/`FAILED`/`DONE` 终态），这是本节需要新增的能力：
   - `TaskRecord` 新增 `PAUSED` 状态（区别于 `CANCELLED`——`PAUSED` 任务的 `goal_backlog`
     关联的 Objective 状态不变，下次 tick 可以重新提交；`CANCELLED` 任务被视为终态）
   - 触发条件：`initiator="autonomous"` 的任务执行期间，`InputQueue` 收到
     `initiator="user"` 的新消息，且两者声明的"将要触碰的路径"有重叠（见下一条资源锁）
2. **资源锁**：自主任务提交前先检查"最近 N 分钟内有用户活动的路径集合"是否重叠。
   实现方式：复用 Stage 6.1 `traces.jsonl` 已经记录的工具调用参数（文件路径常出现在
   `bash`/`write_file`/`patch_file` 等工具的 `tool_input` 里），新增一个轻量函数
   `recent_user_touched_paths(paths, window_minutes=10) -> set[str]`，扫描最近窗口内
   `initiator="user"` 的工具调用记录提取路径——**这是复用 Stage 6 观察性数据而非新建
   一套路径追踪机制**的具体体现
3. **预算硬限制**：`self_profile.json` 的 `resource_budget.daily_token_budget`/`used_today`
   字段已存在（Stage 5 已实现"按 UTC 日历日做真实跨日重置"），本节只需新增判断逻辑：
   `AutonomousLoop._tick_maintenance()` 提交自主任务前检查
   `used_today < daily_token_budget`，超出则跳过本次 tick 的自主部分。
   **探索预算切分**（设计文档"固定比例、独立核算"）：`resource_budget` 新增
   `exploration_budget_ratio: float = 0.1`（默认 10%），`used_today` 拆分为
   `used_today_goals` + `used_today_exploration` 两个计数器分别累加，互不挪用——
   这是本节需要新增的字段，留给 9.8 节探索机制使用，本节先把字段和"互不挪用"的检查逻辑建好

### 7.2 主动汇报：活动摘要（晨报）

1. 新增 `.agent/activity_digest.jsonl`，每条自主 Task 完成后追加一条精简记录：
   ```json
   {"at": 1718500000.0, "type": "task_completed", "task_id": "...", "objective_id": "...",
    "summary": "完成 bash-safety skill 拦截效果观察，未发现误拦截", "initiator": "autonomous"}
   {"at": 1718500100.0, "type": "evolve_proposal", "branch": "evolve/...", "summary": "..."}
   {"at": 1718500200.0, "type": "soft_goal_created", "goal_id": "...", "title": "..."}
   ```
2. 晨报展示逻辑：用户下次打开任意客户端（CLI `/agent digest` 命令，或 Web demo 启动时），
   第一屏展示"自上次交互以来"的摘要——**"上次交互"的判断**：扫描 `InputQueue` 历史
   `initiator="user"` 的最近一条 `TurnInfo.ended_at`，取之后的 `activity_digest.jsonl` 记录
3. **分组展示，不混在一起**（设计文档原话）：
   - evolve 分支提案单独列出"有 N 个待审的进化提案"
   - 软目标创建（`type=soft_goal_created`）单独列出
   - 其余自主任务完成归为"日常自主活动"
4. CLI 命令：`/agent digest [--since <timestamp>]`

### 7.3 与现有机制的接口

- `PAUSED` 状态的引入需要同步更新 `task_display.py`/`status_bar.py`（Task Tab 栏的状态显示
  逻辑），否则 UI 层会把 `PAUSED` 误判为未知状态——这是容易被遗漏的细节，列在此处提醒
- `activity_digest.jsonl` 与 Stage 5 `activity_log.jsonl` 是两个不同文件，不要合并：
  `activity_log.jsonl` 记录的是"每次 session 的摘要"（粒度=session），
  `activity_digest.jsonl` 记录的是"每次自主行为的摘要"（粒度=自主 task/proposal/goal），
  两者粒度和读者意图都不同（前者服务于跨项目分析，后者服务于"我错过了什么"），
  不应该因为"都是 jsonl 追加记录"而强行合并成一个文件

### 7.4 验证标准

- 构造一个正在执行的自主任务（mock），同时提交一条用户消息，且两者声明的路径重叠，验证
  自主任务进入 `PAUSED` 而非被杀掉，且 `goal_backlog.json` 对应 Objective 状态不受影响
- 构造 `used_today` 已达 `daily_token_budget` 的场景，验证 `_tick_maintenance()` 跳过自主部分
  但仍执行 `_tick_passive()` 的轻量周期任务（如 consolidation）
- 构造三条 `activity_digest.jsonl` 记录（task_completed/evolve_proposal/soft_goal_created），
  调用 `/agent digest` 验证三类分组展示正确

### 7.5 风险与边界

`recent_user_touched_paths()` 依赖 Stage 6 `traces.jsonl` 的数据完整性——如果用户关闭了
`tracing_enabled`（`ObservabilityConfig`），资源锁判断会退化为"无法判断重叠，保守起见
一律视为重叠"（宁可错误暂停自主任务，不可错误覆盖用户文件），这是本节需要显式处理的
降级路径，不能假设 tracing 一定开启。

---

## 八、9.5 安全网调整：`initiator` 字段与 tier 上浮

> 对应上级文档 9.1 第 5 步："这一步必须在 `autonomy_level` 从 `passive` 升到 `maintenance`
> 之前完成"。

### 8.1 现状核对

`StateRepo.apply()` 当前签名（核实自 `evolution/state_repo.py` 第 227-235 行）：

```python
def apply(self, changes, message, meta, tier, validators=None, auto_validators=False) -> ApplyResult
```

**确认没有 `initiator` 参数**，`meta` 是自由字典（commit message 里携带
`source_lessons`/`session_id`/`confidence`/`occurrence_count`/`proposed_by`），理论上
`initiator` 可以临时塞进 `meta` 字典，但这样做**无法实现"tier 上浮"这个核心要求**——
`meta` 只影响 commit message 的展示内容，不参与 `resolve_tier()` 的判定逻辑，必须是
显式参数才能影响校验路径。

### 8.2 具体改动

1. `apply()` 新增显式参数：
   ```python
   def apply(self, changes, message, meta, tier,
             validators=None, auto_validators=False,
             initiator: str = "user") -> ApplyResult:
   ```
2. `resolve_tier()`（当前签名 `resolve_tier(paths, tier) -> (effective_tier, forced)`）
   新增 `initiator` 参数，上浮规则：
   ```python
   def resolve_tier(self, paths, tier, initiator="user"):
       effective_tier, forced_by_path = self._resolve_tier_by_path(paths, tier)
       if initiator in ("autonomous", "scheduled") and effective_tier == "T0":
           effective_tier = "T1"
           forced = forced_by_path or "initiator_upgrade"
       return effective_tier, forced
   ```
   **只处理 T0→T1 这一档上浮**，T1/T2/T3 不因 `initiator` 改变——理由：设计文档原话
   "用户主动要求的 T0 改动可以直接 apply；同等改动若由自主 tick 发起，至少要走 evolve 分支
   留痕——区别只在是否需要人审"，T1 本身已经是"走 evolve 分支"的最低档，T0 是唯一
   "完全不留痕直接落盘"的档位，所以上浮的关键卡点就是 T0，不需要对更高档位做二次上浮
3. `meta` 字典里新增 `initiator` 字段（与 tier 判定逻辑解耦，单纯用于 commit message 的
   可追溯展示，呼应"是否留痕、是否可 revert 不能因发起方是自主而降低标准"）
4. **T3 受保护路径清单扩展**（呼应第四节 4.5 节遗留问题）：`scripts/protected_paths.py`
   新增规则，把 `~/.agent/self_profile.json` 中 `operating_state.autonomy_level` 字段的
   修改路径纳入保护——但这里有个实现细节问题：`protected_paths.py` 当前是按**文件路径**
   粒度判断（`is_protected_path(path)`），**不是按字段粒度**。如果把整个 `self_profile.json`
   标记为 T3，会导致 9.1 节"SessionEnd 时自动更新 `operating_state.last_active_at`"这类
   高频轻量写入也被迫走 T3 流程（强制人审），这显然不对——**这是本节发现的一个真实设计冲突，
   需要解决而非掩盖**：
   - 方案：不把整个文件路径纳入 T3 清单，而是在 9.1 节 `/agent autonomy` 命令的实现里，
     **直接绕开 `StateRepo.apply()`**，用更简单的直接文件写入 + 强制 CLI 交互确认作为
     安全网（已在 9.1 节描述："不注册为工具，只能 CLI 手动触发"）。`StateRepo.apply()`
     的 T3 保护机制是为"agent 自己提议的改动"设计的，`/agent autonomy` 从一开始就不是
     agent 提议的改动，不需要套用同一套机制，**两种不同性质的写入不应该被强行塞进同一个
     安全网路径**——这是本节对前序文档措辞的一处修正（前序文档笼统写"`StateRepo.apply()`
     加参数，自主发起的改动 tier 上浮"，本节明确这条规则只适用于 agent 自主发起的改动，
     不适用于用户通过专用命令直接做的配置修改）

### 8.3 与现有机制的接口

- `evolution/phase_g.py` 当前所有调用 `StateRepo.apply()` 的地方（如果有，需核对
  `check_scope_promotion` 是否直接调用或只是"输出候选交给人工"）——核实 Stage 8 完成记录
  "8.4 节只输出候选列表，不直接调用 `skill_propose`"，**说明当前 `phase_g.py` 内部还没有
  真正的 `initiator="scheduled"` 调用点**，这是 9.3 节 `AutonomousLoop` 接入后才会第一次
  出现的真实调用场景——`_tick_passive()` 触发的 `run_phase_g()` 本身不直接写文件
  （Stage 8 设计如此），但如果未来 8.4 节晋升候选改为自动调用 `skill_propose`
  （目前设计文档和计划文档都倾向于保持"人工确认"），那个调用点必须传 `initiator="scheduled"`
- `tools/skill_manager.py` 的 `skill_propose` 工具内部调用 `StateRepo.apply()` 时，
  正常用户对话触发的走 `initiator="user"`（默认值，无需改动调用点）；
  若未来由 `AutonomousLoop` 直接触发（而非通过用户对话），调用点需要显式传
  `initiator="autonomous"`——**这是唯一需要改动现有调用点的地方**，其余调用点不变

### 8.4 验证标准

- 构造一个 T0 级改动，`initiator="user"` 时直接 apply 成功不经过额外校验；
  同样的改动 `initiator="autonomous"` 时被上浮为 T1，触发对应校验流水线
- 构造一个原本就是 T3（受保护路径）的改动，`initiator="autonomous"` 时仍然是 T3，
  不因为已经是最高档而出现任何降级或异常
- `/agent autonomy maintenance` 命令的写入路径完整走一遍，确认**没有**调用
  `StateRepo.apply()`（grep 调用栈或加日志验证），而是直接文件写入 + CLI 确认

### 8.5 风险与边界

`resolve_tier()` 新增参数属于内部接口改动，需要检查所有现有调用点（`skill_propose`、
`evolution-agent` 相关代码路径）是否因为新增的位置参数/关键字参数产生兼容性问题——
建议 `initiator` 一律用关键字参数且放在最后，保证现有调用点不传这个参数时行为完全不变
（默认 `"user"`），这是本节对"修改公共接口默认不破坏现有调用"这条工程纪律的具体落实。

---

## 九、9.6 升级到 `maintenance`：周期性任务自主触发的"开闸"

> 对应上级文档 9.1 第 6 步："启用真正的周期性任务自主触发（不再需要 `/evolve review` 手动调用）"。

### 9.1 本节不是新功能开发，是"开闸"

到 9.5 节为止，所有机制已经就位（调度骨架 9.3、资源仲裁与晨报 9.4、安全网调整 9.5），
本节只是把 9.3 节 `AutonomousLoop._tick_maintenance()` 之前因为"按依赖关系必须先做 9.4/9.5"
而暂时空着的方法体填上：

```python
def _tick_maintenance(self) -> None:
    self._tick_passive()  # 周期性任务，行为不变
    # 9.6 新增：探索预算分配（如果 9.8 已实现）
    if self._exploration_budget.has_remaining():
        experiment = self._experiment_log.next_candidate()
        if experiment:
            self._submit_with_arbitration(experiment, initiator="autonomous")
    # 注意：maintenance 档位本身仍然不 derive 新 Goal/Objective，
    # 这是与 autonomous 档位的边界，由 9.9 节处理，此处不实现
```

### 9.2 验证标准

- 升级到 `maintenance` 后，不需要任何 `/evolve review` 或 `/evolve phase-g` 手动命令，
  daemon 在 24h 间隔到达后自动触发 Phase G 扫描，且产出正确写入 `activity_digest.jsonl`
- `maintenance` 档位下 `goal_backlog.json` 经过多次 tick 仍不产生新节点（验证边界仍然守住，
  这一条延续 9.3 节验证标准，确认升档后边界没有意外松动）

---

## 十、9.7（原 9.1 第 7 步）探索与实验机制

> 对应上级文档 9.1 第 7 步："在 `maintenance` 档稳定后再做，因为依赖独立核算的探索预算，
> 预算仲裁机制需要 7.5 节已经跑顺"。

### 10.1 数据结构：`Experiment` 实体

直接落地设计文档 7.10 节给出的 dataclass，本节补充实现细节：

```python
@dataclass
class Experiment:
    id: str
    hypothesis: str
    motivation: str
    method: str
    status: str  # designed | running | completed
    trials: list[dict] = field(default_factory=list)
    outcome: str = ""  # confirmed | rejected | inconclusive
    conclusion: str = ""
    follow_up: Optional[str] = None
    # 本节补充字段（设计文档未列出但实现必需）：
    created_at: float = 0.0
    frozen_at: Optional[float] = None  # 预注册冻结时间戳，冻结后 hypothesis/method 不可改
    cooldown_until: Optional[float] = None  # rejected 时设置
```

存储：`.agent/experiments.jsonl`（追加写，每个 Experiment 完整状态变更追加一条新记录，
**不是原地修改**——这是实现"预注册不可篡改"纪律的具体方式：与其靠代码逻辑禁止修改
`hypothesis`/`method` 字段，更彻底的方式是存储层面只追加不修改，`frozen_at` 之后的状态
变更只能通过追加新记录体现，查询时取每个 `id` 的最新记录作为当前状态，但
`hypothesis`/`method` 字段的值通过比对该 `id` 第一条记录（`frozen_at` 设置时的记录）
确保未被覆盖——**这是本文档对"预注册纪律"给出的具体工程实现，而非停留在设计文档的
"应该不允许修改"这种声明层面**。

### 10.2 假设来源的具体实现

设计文档列了四个优先级来源，逐条给出查询实现：

1. **capability_map 低置信度区域**：查 `memory.jsonl` 中 `entry_type="capability_map"`
   的最新条目（Stage 8.3 产出），筛选 `confidence_by_domain` 中数值低于阈值
   （建议默认 0.6，可配置）的 domain
2. **半成形 lesson**：查 `memory.jsonl` 中 `occurrence_count` 在 1 到 T1 阈值（默认 3）
   之间的条目——已经出现但还不够触发自动 skill 化的 lesson
3. **新接入未充分使用的能力**：复用 Stage 7 14.2 节的 `SkillUsageTracker`，筛选
   "已激活但 `last_used_at` 为空或样本数 < N"的 skill/MCP server
4. **用户直接提出**：不需要统计门槛，对应一个新工具 `propose_experiment(hypothesis, method)`
   ——**这个工具需要注册给主 agent**（与 9.1 节的 `/agent autonomy` 不同，"用户在对话中
   提出"这个来源本质上是用户通过自然语言对话表达意图，再由 agent 调用工具转化为
   `Experiment` 草稿，符合现有"工具响应用户意图"的模式，不需要绕开 `StateRepo.apply()`
   那一套限制——这条来源产生的是 `status=designed` 的草稿，不直接执行，仍需走后续
   预算分配才会真正运行）
- `next_candidate()` 方法按上述优先级顺序查询，第一个命中且不在冷却期的候选作为下一个实验

### 10.3 反事实重放的具体实现

1. 选取依据：`.agent/sessions/` 中产生过 lesson 或落在低置信度 capability_map 类别的
   历史 session——查询方式是反向关联 `memory.jsonl` 条目的 `session_id` 字段（Stage 1
   lesson 数据结构已有此字段）
2. 重放机制：**复用 Stage 3.2 `eval_runner.py` 的执行框架**——`eval_runner.py` 当前的场景
   来源是 `test_cases/*.txt`，本节新增一种场景来源"历史 session 关键节点重放"，需要新增
   一个适配函数把历史 session 的 `raw_history.jsonl` 截取关键节点（用户的原始请求 +
   触发问题的那几轮）转换成 `eval_runner.py` 能消费的场景格式，**执行引擎本身不重写**，
   只是新增一种输入源的适配层
3. confirmed 的实验关联场景沉淀为新 `test_cases/`：直接把适配后的场景文件保存到
   `test_cases/` 目录，复用现有命名规范

### 10.4 执行规格：最保守一档

1. 复用 Stage 2 `EvolutionWorkspace`（worktree 隔离）+ Stage 3.2 `eval_runner`（对比执行），
   **不新增执行环境**
2. "外部副作用类操作全部 mock"：复用现有的 `--sandbox` flag（Stage 2 `EvolutionWorkspace`
   已支持），Experiment 执行时强制启用，不提供关闭选项
3. "被抢占时不计入打断统计"：9.4 节的 `PAUSED` 状态机制里，新增一个标记字段
   `is_experiment: bool`，资源仲裁逻辑对该字段为 `True` 的任务直接暂停而不触发
   降级重试链（Stage 7 13.2 的降级重试是为正常任务设计的，实验任务被抢占是预期行为，
   不应该触发"失败后降级换 profile"这种逻辑）

### 10.5 outcome 处理

```python
def finalize_experiment(exp: Experiment, outcome: str, conclusion: str) -> None:
    if outcome == "confirmed":
        # 触发 skill_propose 或开 evolve 分支，tier 判定走既有逻辑，不因来源是实验而改变门槛
        ...
        # 更新 capability_map 相关条目置信度（复用 Stage 8.3 的 memory 写入路径）
    elif outcome == "rejected":
        # 生成负面 lesson，entry_type="lesson"，source="experiment"
        # 设置冷却期：cooldown_until = now + cooldown_days * 86400
        # cooldown_days 建议默认 14，比照 Stage 8.5 节奏治理的 7 天再放宽一倍
        # （理由：被否定的方向比"提案频率限流"更需要更长的沉默期，避免反复试错同一死胡同）
        ...
    elif outcome == "inconclusive":
        # 记录但不设冷却期，标记低优先级
        ...
```

### 10.6 验证标准

- 构造一个 capability_map 低置信度 domain，验证 `next_candidate()` 优先选中对应假设来源
- 完整跑一次"设计 → 冻结 → 执行（mock 沙箱）→ outcome=rejected → 冷却期生效"链路，
  验证冷却期内 `next_candidate()` 不会再次选中同一方向的候选
- 验证 `frozen_at` 之后任何尝试修改 `hypothesis`/`method` 的代码路径（如果存在）会被
  追加写存储机制天然挡住——这是验证存储设计本身的正确性，不是验证某个校验函数

### 10.7 风险与边界

预算仲裁（9.4 节）必须先稳定运行，否则探索预算和 goal backlog 预算的"互不挪用"无法验证
——这是本节排在 9.4/9.5 之后的直接原因，与上级文档判断一致，本节不重复论证，只强调
"稳定运行"的判断标准建议是：`maintenance` 档位下连续运行至少覆盖 7 天的资源仲裁数据
（对应 Stage 8.5 的冷却期量级，便于横向比较）。

---

## 十一、9.8（原 9.1 第 8 步）升级到 `autonomous`：软目标 derive

> 上级文档原话："是否要做这一档，留给届时重新评估"。本文档同样不在此处提前给出实现方案，
> 但补充两点细化（与"不提前给答案"不矛盾——这两点是"如果做，需要先回答什么"，不是
> "怎么做"）：

1. **软目标 derive 的触发判断本身需要明确的"证据强度"门槛**，不能是"agent 觉得应该做就做"。
   建议届时讨论时，参照本文档 9.7 节"假设来源"的同一套数据基础（capability_map 低置信度、
   半成形 lesson、`open_threads.json` 高优先级条目），定义清晰的数值门槛，而不是让
   `_tick_autonomous()` 内部用一次性的 LLM 判断"要不要 derive 一个新目标"——后者的不可预测性
   与"持续存在、有自己议程的 agent"这一档本身的高风险定位不匹配
2. **`autonomous` 档位的降级路径（"紧急刹车"）应该先于这一档实际启用前就实现并测试**，
   即设计文档开放问题 5 提到的"降级是否需要更轻量的流程"——本文档建议：不管最终
   `passive → maintenance → autonomous` 升级流程定得多严格，`autonomous → passive` 的降级
   必须是**单条命令、无需二次确认、立即生效**（`/agent autonomy passive --emergency`），
   理由是"刹车"场景下要求用户在恐慌或紧急情况下还要走复杂确认流程是设计错误。
   这一条建议本身不依赖"是否启动 autonomous 档位"的决策，可以提前在 9.1 节
   `/agent autonomy` 命令实现时一并加上 `--emergency` 选项，**降级路径的工程实现不需要
   等到真正讨论是否启用 autonomous 档位**——这是本文档对原计划"留给届时重新评估"的
   唯一一处主动建议提前做的部分，因为它是纯粹的安全兜底，不涉及"该不该让 agent 有议程"
   这个产品定位问题本身。

---

## 十二、子任务依赖图与建议顺序

```
9.0 前置条件核对（决策点，需人工确认两项 ⬜）
  │
  └─→ 9.1 自我模型补全（autonomy_level 字段落地）
        │
        ├─→ 9.2 Goal Backlog 数据结构 + work_index 互通
        │     │
        │     └─→ 9.3 AutonomousLoop 骨架（passive 档位验证）
        │           │
        │           └─→ 9.4 资源仲裁 + 晨报（强制前置，不可跳过）
        │                 │
        │                 └─→ 9.5 安全网 initiator 字段 + tier 上浮
        │                       │
        │                       └─→ 9.6 升级 maintenance（开闸，无新功能）
        │                             │
        │                             └─→ 9.7 探索与实验机制
        │                                   │
        │                                   └─→ 9.8 升级 autonomous（决策点，暂不实现）
        │
        └─→（9.8 节"紧急刹车"建议可在 9.1 完成后随时提前实现，不在主链路上）
```

与上级文档 9.1 节八步顺序完全对应（本文档 9.1-9.8 对应原八步，编号方式不同仅为配合
本文档的章节组织，内容映射关系：本文档 9.1=原第1步，9.2=原第2步，9.3=原第3步，
9.4=原第4步，9.5=原第5步，9.6=原第6步，9.7=原第7步，9.8=原第8步）。

---

## 十三、测试覆盖建议（对应各节验证标准的工程化）

延续 Stage 4-8 一致的测试组织风格（`tests/test_phase_g.py`/`tests/test_global_knowledge.py`
等单文件覆盖单 Stage 的模式），建议：

| 测试文件 | 覆盖范围 |
|---|---|
| `tests/test_autonomy_level.py` | 9.1：字段新增、向后兼容、`/agent autonomy` 命令边界（不可被工具调用） |
| `tests/test_goal_backlog.py` | 9.2：`GoalBacklog` 读写、`has_actionable_work`/`next_task`、与 WorkThread 关联 |
| `tests/test_autonomous_loop.py` | 9.3：`tick()` 三档位分支、`passive` 边界验证（多次 tick 不产生 Goal）、daemon 化的 Phase G 触发（不依赖 session 退出） |
| `tests/test_resource_arbitration.py` | 9.4：用户优先暂停、路径重叠检测、预算硬限制、探索预算隔离 |
| `tests/test_activity_digest.py` | 9.4：晨报分组展示、"上次交互"判断逻辑 |
| `tests/test_state_repo_initiator.py` | 9.5：`initiator` 参数、tier 上浮（仅 T0→T1）、现有调用点默认值兼容性 |
| `tests/test_experiment.py` | 9.7：预注册冻结机制（追加写验证不可篡改）、假设来源优先级查询、冷却期 |

**核心验证原则延续前两份文档"改造原则 3"**：每个测试文件都必须包含至少一条"验证确实被
消费/生效"的测试（而非只验证"数据被正确写入"）——例如 `test_autonomous_loop.py` 必须有
一条测试验证"不依赖 session 退出，daemon 自己能触发 Phase G"，这是本 Stage 与前序
Stage 4-8 最大的行为差异点，必须有对应测试覆盖，不能只测"参数传递正确"这类表面细节。

---

## 十四、本文档对前序文档的修正与补充清单（汇总）

为方便后续核对，汇总本文档在细化过程中发现的、与 `self_evolution_stage4plus_plan.md`
原文存在偏差或需要补充澄清的地方：

1. **`autonomy_level` 字段实际不存在**（前序文档 5.1 节验证标准暗示已声明，实际核查
   `perception/global_knowledge.py` 后发现需要在本 Stage 新增，见第二节核对结论与第四节）
2. **`StateRepo.apply()` 新增 `initiator` 参数的适用范围需要明确边界**：只适用于 agent
   自主发起的改动，不适用于用户通过专用命令（如 `/agent autonomy`）做的配置修改——后者
   应该绕开 `StateRepo.apply()` 走更简单的直接写入 + 强制确认（见第八节 8.2 第 4 条）
3. **`activity_digest.jsonl`（本 Stage 新增）与 `activity_log.jsonl`（Stage 5 已有）
   是两个不同粒度的文件，不应合并**（见第七节 7.3 节）
4. **`TaskManager` 当前没有任务暂停接口，只有终态**，9.4 节资源仲裁需要新增 `PAUSED` 状态，
   这是对现有任务状态机的一次扩展，不是简单复用（见第七节 7.1 第 1 条）
5. **探索机制的"预注册不可篡改"建议用存储层面的追加写实现，而非仅靠代码逻辑禁止字段
   修改**——更彻底地落实设计文档"先写后跑"的纪律要求（见第十节 10.1 节）
6. **`autonomous → passive` 的紧急降级命令建议提前在 9.1 节实现**，不需要等待
   "是否启用 autonomous 档位"这个产品决策本身（见第十一节第 2 条）——这是本文档唯一
   一处主动建议突破"严格按 9.1-9.8 顺序"的地方，因为安全兜底机制的工程实现与
   "是否启用更高自主档位"是两个独立的决策维度，没有必要互相阻塞。

---

## 十五、决策记录（启动前必须填写，当前留空）

> 本节呼应 `self_evolution_stage4plus_plan.md` 9.0 节"建议作为决策会议的议程，而非工程
> 任务"的定位。在以下空白被实际填写之前，本文档的存在**不代表 Stage 9 已被批准启动**，
> 任何人/agent 不应跳过本节直接开始实施第四至第十一节的具体改动。

- [ ] 决策日期：__________
- [ ] 决策人/团队：__________
- [ ] 决策结论（是否启动 / 启动到哪一档为止 / 暂缓）：__________
- [ ] 若启动，`autonomy_level` 计划在多长观察期后从 `passive` 升至 `maintenance`：__________
- [ ] 是否同时批准 9.7 探索机制（可与 `maintenance` 升级分开决策）：__________
- [ ] `autonomous` 档位（9.8 节）：明确"留待届时重新评估"，本次决策不涉及：（默认勾选）
