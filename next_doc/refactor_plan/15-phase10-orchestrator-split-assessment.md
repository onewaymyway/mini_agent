# `orchestrator/` 拆分评估（Phase 10 Sprint 10-2 的前置评估）

> 日期：2026-09-29。**仅评估，未修改任何生产代码、测试或配置**；文中所有"实验"均在
> 项目的临时副本上进行并已丢弃。文中的处置建议全部是**提议，待项目所有者确认**，
> 没有修改 `11-phase10-legacy-decommission-plan.md` 的任务表与验收标准。

## 一、背景与范围

`11-phase10-legacy-decommission-plan.md`（2026-09-28 "Sprint 10-2 复核"）与 README 均已
写明：`orchestrator/` 深度 inbound=37，远超阈值 10，**不能整包移动**，若未来重启目录收敛，
必须先对它单独评估拆分方案。所有者于 2026-09-29 选择推进这项评估。

本文回答四个问题：

1. 这 37 个调用方到底依赖 `orchestrator/` 里的**什么**？（按子模块拆开，而不是看整包）
2. `orchestrator/` 里的 10 个文件是不是同一类东西？各自对应新架构里的哪个概念？
3. 每一部分该"保持原位 / 归位 / 先建 Adapter / 不动"？代价与收益分别是什么？
4. 对 Sprint 10-2 意味着什么？

方法论沿用 `03-sprint1.5-memory-perception-coupling-assessment.md` 与
`12-execution-and-doc-sync-norms.md` 第六节：**按子模块扫描而不是扫顶层包；只统计跨子系统
inbound（排除 `orchestrator/` 自身内部）；同时合并"包级再导出"与"子模块路径"两种 import 写法**。

## 二、现状盘点（全部为实测）

### 2.1 五类划分

`orchestrator/` 共 10 个模块（不含 `__init__.py`）、3868 行。按各文件自己的模块说明与
实际依赖，它们不是"一个调度器的 10 个部件"，而是**五类基本互不相关的东西**：

| 类 | 文件 | 实际职责（取自模块说明） | 包内依赖 | 依赖的其它 mini_agent 模块 |
|---|---|---|---|---|
| **A 子 Agent 执行** | `task.py`（263 行）、`task_manager.py`（565）、`sub_agent.py`（608） | 任务数据模型；线程模型的并发任务调度器；对 `Agent` 的线程包装（"每个 Task 对应一个 SubAgent"） | `task_manager`→`sub_agent`/`task`/`concurrency`；`sub_agent`→`task`/`concurrency` | `sub_agent` 依赖 12 条模块路径（`agent`/`llm`/`tools`/`skills`/`permissions`/`hooks`/`evolution.sub_agent_experience` 等） |
| **B 并发/限流/流式状态** | `concurrency.py`（442） | 两个信号量（Task 并发数、LLM 并发数）、`RateLimiter`（RPM）、`StreamTokenState`、`RetryCountdownState` | 无 | **无**（不 import 任何 mini_agent 模块） |
| **C 执行计划** | `plan.py`（458）、`plan_display.py`（293） | Agent 在 agentic loop 中的结构化"工作记忆"（任务树），模块说明明确写"**这不是 SubAgent 调度器**"；`plan_display` 是它的终端渲染 | `plan_display`→`plan` | `plan` 只依赖 `errors` |
| **D 角色/人设配置** | `agent_profiles.py`（336）、`persona_profiles.py`（382） | 从 `.agent/agents/*.md`、`.agent/personas/*.md` 加载用户自定义的子 Agent 预设与角色扮演人设（frontmatter + 提示词） | 无 | `errors`/`platform_filter`/`storage.paths`（`persona` 另有 `time_utils`） |
| **E 终端 UI** | `status_bar.py`（263）、`task_display.py`（222） | 状态栏、任务看板（Rich Live） | `status_bar`→`concurrency`/`plan_display`；`task_display`→`task` | `ui.terminal`；`status_bar` 另依赖 `tools.orchestration` |

（`plan_display.py` 按功能属于 UI，但它只服务 `plan`，本文为便于对照把它与 C 类放在一起，
在 2.3 的统计里按 C 类计入。）

**关键事实**：包内共 9 条 import 边（逐条核对）：`A` 内部 3 条（`task_manager`→`sub_agent`、
`task_manager`→`task`、`sub_agent`→`task`）、`A→B` 2 条（`task_manager`、`sub_agent` 使用
`concurrency` 的信号量）、`C` 内部 1 条（`plan_display`→`plan`）、`E→A/B/C` 3 条
（`task_display`→`task`、`status_bar`→`concurrency`、`status_bar`→`plan_display`）。
据此：**`concurrency`（B）、`plan`（C）、`agent_profiles`/`persona_profiles`（D）自身不依赖包内
任何其它模块**；D 类没有任何包内使用者，B 类只被 A 类与 UI 使用，C 类只被它自己的渲染
模块使用。也就是说这个包在结构上本来就是可分的，只是历史上被放进了同一个目录。

### 2.2 `__init__.py` 的"公共 API"实际无人使用

`orchestrator/__init__.py` 文档声称提供公共 API（`from orchestrator import Task,
TaskManager, ...`，再导出 21 个名字）。实测（`dep_graph.py` 结果中 `imported ==
"mini_agent.orchestrator"` 且调用方在包外）：**包外没有任何文件通过包级路径 import**，
全部 37 个调用方都直接使用子模块路径（如 `from mini_agent.orchestrator.task import Task`）。

同时，`__init__.py` 在导入时**急切**加载 `task_manager`/`sub_agent`/`task_display`/
`status_bar` 等，而 `sub_agent` 顶层就 `from mini_agent.agent import Agent`、
`from mini_agent.tools import ...`。实测：

- 只执行 `import mini_agent.llm.providers._base_mixin`（LLM 层的一个文件），就会连带加载
  `orchestrator`、`orchestrator.concurrency`、`status_bar`、`sub_agent`、`task`、
  `task_display`、`task_manager` 七个 orchestrator 模块，以及 `mini_agent.agent`、
  `mini_agent.agent.core`、`mini_agent.tools`、`mini_agent.skills`。

原因是 `llm/providers/_base_mixin.py`（顶层）与 `llm/retry.py` 使用了 B 类的
`concurrency`。也就是**LLM 层（较低层）通过 `orchestrator/__init__.py` 间接依赖了整个
Agent/工具层（较高层）**。它现在没有报循环导入错误，是因为函数内延迟导入与导入顺序
恰好错开——不是设计保证。

### 2.3 跨子系统 inbound（口径：排除 `orchestrator/` 内部；合并两种 import 写法）

复现命令：对 `orchestrator` 及 10 个子模块分别执行
`python scripts/dep_graph.py --module <name> --json`，取 `from_file` 不在
`mini_agent/orchestrator/` 下的条目；`dep_graph.py` 的模块匹配是按点号边界的
（`orchestrator.task` 不会误匹配 `orchestrator.task_manager`，已核对源码第 132 行）。

| 类 | 各模块跨子系统调用方文件数 | 该类调用方文件（去重） | 涉及子系统 |
|---|---|---|---|
| A 子 Agent 执行 | `task`=6、`task_manager`=1、`sub_agent`=3 | **6** | ensemble、evolution×2、skills、tools、ui |
| B 并发/限流 | `concurrency`=5 | **5** | api、cli×2、llm×2 |
| C 执行计划 | `plan`=4、`plan_display`=1 | **4**（`plan_display` 的调用方是 `cli/commands/plans.py`，已在其中） | agent、cli、prompts、tools |
| D 角色/人设配置 | `agent_profiles`=19、`persona_profiles`=8（两者共有 2 个） | **25** | 11 个子系统；其中 `role_agents/` 独占 8 个 |
| E 终端 UI | `status_bar`=1、`task_display`=2 | **3** | cli×2、tools |

五类并集恰好是 **37**，与 11 号文档的数字一致。构成为：**D 类 25 个，其余四类去重后 16 个，
两者重叠 4 个**（`tools/orchestration.py`、`evolution/capability_learning.py`、
`cli/app.py`、`prompts/manager.py`）。

> **对 11 号文档判断的修正/量化**：11 号文档说 37 涵盖"人设/并发/计划会话"等通用基础设施，
> 这个方向是对的，但没有量化。实测 **37 里约三分之二（25 个）来自"配置加载"这一类（D），
> 真正与"子 Agent 调度"有关的 A 类只有 6 个调用方**。"orchestrator/ 阻碍收敛"的说法
> 对 A 类基本不成立，主要是 D 类的体量造成的。

### 2.4 其它结构性发现

- **A 类存在绕过 ActionExecutor 的真实生产调用方**：`evolution/capability_learning.py`、
  `evolution/research_service.py`、`skills/generative_capability/explorer_runtime.py`
  三处直接使用 `SubAgent`/`Task`/`TaskRecord`。Phase 6 Sprint 6-2 让 `ActionExecutor` 的
  `type="subagent"` 分支转发给 `workflow/agent_spawn.py::build_minimal_agent()`，
  **没有触碰这两个模块**（见台账与 07 号文档"变更记录 2026-09-27"），所以目前**没有任何
  Adapter 覆盖 A 类**。
- **包级双向依赖（靠函数内延迟导入维持）**：`orchestrator/status_bar.py` 延迟导入
  `tools.orchestration.get_task_manager`，而 `tools/orchestration.py` 又导入
  `orchestrator` 的 `task`/`task_manager`/`task_display`/`agent_profiles`；
  `ui/terminal.py:2647` 延迟导入 `orchestrator.task.TaskStatus`，而 `task_display`/
  `plan_display` 在模块顶层导入 `ui.terminal`。
- **D 类被 `role_agents/` 当作数据类型使用**：`role_agents/` 下 8 个文件导入的是
  `AgentProfile`/`AgentProfileLoader`（数据类与加载器），不是调度逻辑。
- **移动成本因素**：测试中共有 16 个文件涉及 `orchestrator`；其中**字符串式**
  `patch`/`monkeypatch` 目标共 4 个——`concurrency.concurrency_snapshot`（6 个测试文件）、
  `concurrency.get_stream_token_state`（3）、`persona_profiles.get_persona_loader`（7）、
  `task_display.console`（3）。`src/` 中没有 `importlib`/`__import__` 式动态引用；
  34 处字符串引用全部是 `log_exception(where='mini_agent.orchestrator...')` 的日志标签，
  且 `errors.py`/`scripts/`/`tests/` 中没有按这些标签做聚合或断言（已核对）。
- **导入期副作用**：10 个模块的模块级副作用只有构造自身的单例对象
  （`StreamTokenState()`、`RetryCountdownState()`、`StatusBar()`、`_C()` 等）和正则编译，
  没有向外部注册任何东西。

## 三、逐类评估与处置建议

判断标准（先于结论确定，避免事后合理化）：

1. 是"旧概念被新概念取代"（需要 Adapter），还是"通用基础设施/配置/UI"（只需要归位，不需要 Adapter）？
2. 跨子系统 inbound 与涉及的子系统数。
3. 移动成本：字符串式 patch 数、日志标签、是否有动态引用。
4. 收益：能否消除分层倒置、缩短导入链，或让概念归属更清晰。
5. Sprint 10-2 原止损条件：**Adapter 不完整时不移动**。

| 类 | 性质 | 新架构归属（原方案 §30 为整体 `capabilities/agents.py + actions/`） | 处置建议 |
|---|---|---|---|
| **A 子 Agent 执行** | 真正的"旧实现" | `capabilities/agents.py` + `actions/` | **保持原位，不建 Adapter**。三处生产调用方直接使用它，没有 Adapter；建 Adapter 需要真实消费者（目前没有，属推测性设计）。Sprint 10-2 止损条件在此类**仍然成立** |
| **B 并发/限流** | 通用基础设施，不是旧概念 | 无对应新概念；非 `orchestrator`（它只有信号量、限流、流式状态，不含调度逻辑） | **归位候选（收益最大）**：`llm/` 依赖它造成分层倒置。但**必须先做 §四 Step 1**，否则单独移动 `concurrency.py` 收益有限（见下） |
| **C 执行计划** | Agent 自己的工作记忆 | 与 `actions/planner.py` 有关，但归属**不确定**（见下） | **保持原位**；仅记录为未来"PlanTask ↔ Action 事件"接入候选（Phase 2 盘点已标为高优先级） |
| **D 角色/人设配置** | 配置加载器，不是调度 | `agent_profiles`→`capabilities/agents.py`（用户定义的子 Agent 预设）；`persona_profiles`→ 更接近 `self/`（角色扮演身份）。**两个归属都是推测，未经所有者确认** | **不动**：25 个调用方、11 个子系统，最高；移动收益仅是概念整洁，成本最大（`persona.get_persona_loader` 一个 patch 目标就涉及 7 个测试文件） |
| **E 终端 UI** | CLI 适配层 | `adapters/cli/` | **保持原位**（3 个调用方，收益仅是整洁；且 `status_bar` 与 `tools.orchestration` 存在双向依赖，移动会把延迟导入的环暴露出来） |

**关于 B 类"必须先做 Step 1"**：即使把 `concurrency.py` 物理移出 `orchestrator/`，只要 `llm/`
仍从 `mini_agent.orchestrator.concurrency`（哪怕是兼容 shim）导入，Python 仍会先执行
`orchestrator/__init__.py`，那条急切导入链就还在。真正消除倒置需要 `llm/` 改为从新位置
导入。此外，新位置的选择要避开重包——实测 `import mini_agent.runtime` 会连带加载
`goal_mode`，不适合作为 LLM 层依赖；`mini_agent.utils` 实测是轻量的（不加载
`agent`/`tools`/`orchestrator`/`llm`），可作为候选，命名由所有者决定。

**关于 C 类归属不确定**：`ExecutionPlan`/`PlanTask` 是 Agent 通过 `tools/plan.py` 维护的
任务树，语义上既像 `actions/planner.py` 的计划，又像 `goals/` 的子目标。没有真实需求
就把它对应到某个新概念，会重复 Phase 10 前面已经出现过的"为对齐术语而对齐"问题
（例如 `core/capability.py` 空占位）。因此本文不给 C 类指定目标位置。

## 四、可执行的最小步骤（提议；未执行）

按"收益/风险比"从高到低排序。**每一步都是独立的，可以只做前几步**；Step 1 之后到 Step 2 之间
没有强依赖关系，但 Step 2 的收益依赖 Step 1。

### Step 1：把 `orchestrator/__init__.py` 改为惰性再导出（推荐；**已于 2026-09-29 执行，见 §十**）

- **内容**：用 PEP 562 的模块级 `__getattr__` 保留原有 21 个名字的再导出，但按需加载对应
  子模块；`__all__` 保持不变。**不移动任何文件，不修改任何调用方。**
- **已在临时副本上验证（非仅推断）**：
  - 旧写法 `from mini_agent.orchestrator import Task, TaskManager, SubAgent,
    TaskDashboard, StatusBar, CountingSemaphore, TaskStatus` 全部可用，
    `__all__` 21 项逐项可解析；
  - `import mini_agent.llm.providers._base_mixin` 后，被加载的 orchestrator 模块从
    **7 个降为 2 个**（`orchestrator` 与 `orchestrator.concurrency`），
    `agent`/`tools`/`skills` **不再被连带加载**；
  - 涉及 `orchestrator` 的 16 个测试文件：失败集合与原树**逐条一致**（均为既有的
    12 个失败，见 §六）；
  - 此前的 69 个文件定向回归集：**878 passed / 5 failed，与当前树完全一致**。
- **收益**：消除"LLM 层导入会拉起整个 Agent/工具层"的隐性耦合；缩短任何只需要
  `orchestrator.concurrency`/`orchestrator.plan` 的调用方的导入时间；为后续 Step 2 铺路。
- **风险与止损**：风险点是"依赖 `import mini_agent.orchestrator` 的副作用"——已核对模块级
  副作用只有单例构造，没有外部注册，风险低；**若任何回归出现，止损方式是直接还原
  `__init__.py`（单文件改动）**。这个 Step 不构成"目录收敛"，与 D5 搁置的 Sprint 10-2
  不冲突。
- **需要所有者确认的一点**：`__init__.py` 文档把它称作"公共 API"。惰性方案保留了这个 API
  的全部用法，只改变加载时机；若所有者认为第三方可能依赖"import 即加载全部子模块"的
  副作用顺序，请在确认时说明。

### Step 2：`concurrency.py` 归位到轻量中性位置（可选，依赖 Step 1 才有意义）

- **内容**：移到一个不依赖重包的位置（候选 `mini_agent/utils/`，命名待定），
  `llm/`（2 文件）、`cli/`（2）、`api/`（1）改为从新位置导入；原位置保留一行
  `from <new> import *` 式 shim 以兼容外部旧路径。
- **必须同步处理的成本**：
  - 测试里 4 个字符串式 patch 目标中的 2 个属于这里
    （`concurrency_snapshot`：6 个文件；`get_stream_token_state`：3 个文件）。
    **只搬代码不改 patch 目标会静默失效**——`patch("mini_agent.orchestrator.concurrency.X")`
    改的是旧模块对象上的属性，而调用方已改为从新模块取用，被测代码不会看到 patch，
    测试可能仍然"通过"却不再验证原来的行为。必须把这些 patch 目标改到调用方实际查找的位置；
  - 模块内有全局单例（`_stream_token_state`、`_retry_countdown_state`、信号量全局），
    shim 必须保证只存在**一份**状态，不能因为两个导入路径而各持一份。
- **收益**：彻底消除 `llm/` → `orchestrator/` 的倒置，而不是只靠 Step 1 遮蔽。
- **建议**：Step 1 完成后先观察一段时间，再决定是否值得做 Step 2。Step 1 已解决"导入链"
  这个实际痛点，Step 2 主要是概念整洁。

### 不建议做的

- **整包移入 `legacy/`**：A 类无 Adapter、D 类调用方最多，且 `legacy/` 的语义是"已被
  Adapter 完全代理的旧实现"，`orchestrator/` 的大部分内容不是旧概念，标成 `legacy` 反而
  误导。
- **为 A 类补一个 SubAgent Adapter**：没有新代码需要消费它，属于推测性设计
  （与 `MemoryAdapter.to_old`、A4 `goal_mode/executor.py` 同一原则）。
- **移动 D 类（角色/人设配置）**：见上表，成本最高、收益仅是命名。

## 五、对 Phase 10 的影响

- **Sprint 10-2 的止损条件对 `orchestrator/` 整包继续成立**（A 类无 Adapter），不因本评估而变化；
  本评估的结论是"**分类归位而非整体降级**"，且其中真正有实际收益的只有 Step 1（不移动文件）
  与可选的 Step 2。
- 11 号文档的完成标志第 2 条（"旧模块全部通过 Adapter 接入且已移动到 `legacy/`"）
  对 `orchestrator/` **本来就不适用**：它的大部分内容不是"待 Adapter 代理的旧概念"。
  这与所有者 2026-09-28 决定不追求该标志一致。
- **未修改** 11 号文档的任务表、验收标准与四条完成标志（按 12 号规范第四节，
  修订需所有者确认）；仅在其"变更记录"追加一条指向本文的记录。
- D1/D5 的决定不受影响：本评估不启动 Sprint 10-2。

## 六、验证与实验记录（如实）

- **分析方法**：`scripts/dep_graph.py --json` 对整包与 10 个子模块共 11 次扫描；合并统计脚本
  为一次性脚本，未入库（结果可用 §2.3 的说明复现）。
- **实验一（空壳 `__init__`）第一次结果无效，已重做**：第一次只拷贝了 `src/` 与 `tests/`，
  缺少项目中的 `.agent/agents/*.md` 数据文件，导致 `test_evolution_agent_profile`、
  `test_evolve_cli` 大量失败——这是实验环境缺文件，**不是**空壳 `__init__` 造成的。
  改用完整项目副本后，16 个文件的失败集合与原树一致。此处记录，避免日后有人误读那次
  失败数（42 vs 12）。
- **实验二（惰性 `__getattr__`）**：见 §四 Step 1 的验证结果。
- **16 个涉及 `orchestrator` 的测试文件的既有失败（12 个，当前树与实验树完全一致）**：
  `test_explorer_runtime_subagent.py` 7 个、`test_goal_mode.py::test_build_from_history_*`
  5 个。二者均已在 `10-phase9-self-evolution-sprint-plan.md`（第 429 行）记录为既有失败；
  本次额外在**未做任何改动的原始压缩包**上单独复跑了 `test_explorer_runtime_subagent.py`，
  同样是 7 failed / 15 passed（失败原因是探索子 agent 未调用 `finish`），确认与本阶段及
  上一阶段的改动无关，本文未追查根因。
- `pyflakes`、依赖图脚本等对代码的检查不适用（本次没有改代码）。

## 七、已知局限

- 依赖图是静态 `ast` 解析，**看不到 `getattr`/字符串拼接式的间接使用**；已额外核对 `src/`
  没有 `importlib`/`__import__` 指向 `orchestrator`，但无法排除仓库外（第三方插件、用户脚本）
  的使用。
- §2.3 的"调用方文件数"按文件计，不按调用点计；一个文件多次使用同一模块只算一次。
- 新架构归属（D 类→`capabilities/agents.py`/`self/`，C 类→`actions/planner.py`）是**根据文件
  自述做的判断**，原方案 §30 只给了整包的映射，没有子模块级映射；这些归属均未经所有者确认，
  本文也没有据此提出任何需要这些归属的动作。
- 惰性 `__getattr__` 最初只在临时副本上验证；2026-09-29 已正式落地（§十），仍**没有**跑全量测试，只跑了定向回归；此前各 Sprint 同样只跑定向回归。
- `tests/test_session.py` 在原始压缩包中存在收集错误（`_flock` 导入失败），与本评估无关，未处理。

## 八、重评触发条件

- A 类：出现需要通过 `ActionExecutor`/新概念统一调度 `SubAgent` 的真实新消费者。
- B 类 Step 2：Step 1 之后仍观察到 `llm/` 依赖 `orchestrator` 引起的实际问题（循环导入、
  导入耗时），或 `llm/` 需要独立打包。
- D 类：`role_agents/` 或 `capabilities/agents.py` 的落地需要一个不依赖 `orchestrator` 的
  `AgentProfile` 类型来源时。
- 整体：所有者决定重启 Sprint 10-2（D5）。

## 九、`MIGRATION_STATUS.md` 是否已同步更新

是。新增一行登记本评估文档，并在既有的
"`orchestrator/task_manager.py` + `orchestrator/sub_agent.py`"一行追加 2026-09-29 说明；
状态词保持"未开始"（没有任何迁移发生）。

## 十、Step 1 执行记录（2026-09-29）

- **触发**：所有者要求按 README 继续后续修改；本次把它作为对 Step 1 的确认执行。**Step 2 未做**（§四 建议先观察，且需同步改测试 patch 目标）。
- **改动（仅 2 个文件，无调用方改动、无文件移动）**：
  - `src/mini_agent/orchestrator/__init__.py`：急切 `from .x import ...` 改为 PEP 562 惰性再导出（`_LAZY_EXPORTS` 名字→子模块表 + 模块级 `__getattr__` + `__dir__`，首次访问后缓存进模块命名空间；`TYPE_CHECKING` 分支保留静态导入供 IDE 识别）；`__all__` 的 21 项不变。
  - `tests/test_orchestrator_lazy_init.py`（新增，31 用例，含子进程隔离断言）：旧写法兼容、`import *`、未知属性抛 `AttributeError`、`_LAZY_EXPORTS` 与 `__all__` 一致且目标子模块真有该名字、LLM 层导入不再连带加载 `agent`/`tools`/`skills`、只 import 包不加载任何子模块、首次访问只加载所属子模块并缓存、惰性访问不产生第二份 `concurrency` 全局状态。
- **验证**：
  - 导入链实测（改动前后同一探针脚本）：`import mini_agent.llm.providers._base_mixin` 后被加载的 orchestrator 模块 **7 → 2**（仅 `orchestrator` 与 `orchestrator.concurrency`），`mini_agent.agent`/`tools`/`skills` 由“已加载”变为“未加载”，与 §四 的预测一致。
  - 变异检查：把 `__init__.py` 换回原始急切版，新增测试 **5 个失败**（导入链/惰性/一致性类）；还原后 31 个全过。
  - 定向回归：涉及 `orchestrator` 的 16 个既有测试文件，改动后 **387 passed / 12 failed**，未改动的原始压缩包同批 **387 passed / 12 failed**，失败集合逐条一致（`test_explorer_runtime_subagent.py` 7 个 + `test_goal_mode.py::test_build_from_history_*` 5 个，均为 §六 已记录的既有失败）。
  - 核对：`src/`、`tests/` 中没有任何“包级名字”形式的字符串式 patch 目标（`patch("mini_agent.orchestrator.<Name>")`），惰性化不会让 patch 静默失效。
- **未验证**：全量测试；`orchestrator` 之外的第三方/用户脚本是否依赖“import 即加载全部子模块”的副作用（静态分析看不到，已核对模块级副作用仅有单例构造）。
- **回退方式**：把 `__init__.py` 还原为急切 import 版本（单文件），测试文件可一并删除。
- **对 Phase 10 的影响**：无。Sprint 10-2 的止损结论、D1/D5 决定、11 号文档任务表与完成标志均未改变；这不是“目录收敛”。
