# 脚本/LLM/Agent 混合执行系统（hybrid_exec）设计方案

状态：**草案，待确认**
关联现有机制：`workflow/python_step`、`workflow/py_step_runner.py`、`workflow/py_context.py`、`evolution/state_repo.py`、`evolution/eval_runner.py`、`agent_spawn.build_minimal_agent`

---

## 1. 背景与目标

`python_step` 已经打通了"脚本内可调用 LLM / 可临时拉起 Agent"的能力（`ctx.llm` / `ctx.run_agent_turn`），但目前**脚本本身是静态的、由人手写好放进 workflow 目录**。缺的是一层自动化：

> 探索用 agent/llm 找方案 → 固化成脚本 → 脚本坏了先尝试自愈 → 自愈不了再退回 agent/llm 兜底出结果。

三种执行手段的经济学关系是明确的：

| | 执行成本 | 泛化能力 |
|---|---|---|
| 脚本 (script) | 低 | 低（只覆盖写脚本时想到的情况） |
| LLM 单轮 | 中 | 中 |
| Agent（多轮+工具） | 高 | 高 |

**目标**：构建一个**独立于 workflow 之外**的执行系统 `hybrid_exec`，把"探索优先 agent/llm、执行优先脚本、脚本故障时优先修复脚本、修复不了才降级"这套决策逻辑封装成可复用的 Python 对象，使其：

1. 可以脱离 workflow 被任何代码（daemon 自主决策、CLI 工具、其它 workflow 的 python_step 内部）直接 `import` 调用；
2. 也可以作为 workflow 的一种新 step 类型（`hybrid_step`）接入，复用现有 `python_step` 的子进程隔离与 `ctx.llm`/`ctx.run_agent_turn` 基础设施；
3. 脚本产物有版本管理、成功率统计、退役机制，能沉淀成"越用越省钱"的能力库，而不是每次都重新探索。

---

## 2. 与现有基础设施的关系（复用而非重造）

- **脚本执行隔离**：直接复用 `py_step_runner.py` 的"子进程 + `run(ctx)` 入口 + 单行 JSON 结果包"协议，不重新发明脚本沙箱。`hybrid_exec` 里生成/修复出来的脚本，天然满足这个协议，可以被同一个 runner 拉起。
- **LLM 调用**：复用 `PyStepLLM` / `LLMHelper`（provider 轮转、重试、fallback 全部继承，不重复实现）。
- **Agent 探索/修复**：复用 `agent_spawn.build_minimal_agent`（与 `SkillAgentStepExecutor`、`run_agent_turn` 共用同一构造逻辑）。
- **版本与风险分级思路**：借鉴 `evolution/state_repo.py` 的"改动要落盘要可追溯、要能 revert"的思路，但**降级简化**——`hybrid_exec` 管理的脚本不属于项目核心代码（不用 T0-T3 风险分级、不用 git worktree），只需要一个轻量的本地版本目录 + 元信息 JSON，避免过度设计。
- **成功率评估思路**：借鉴 `evolution/eval_runner.py` 的"跑一批场景、统计通过率"思路，用于判断脚本是否该"退役重新探索"。

---

## 3. 核心概念与类设计

新增顶层包：`src/mini_agent/hybrid_exec/`

```
hybrid_exec/
├── __init__.py
├── spec.py          # TaskSpec / ExecutionTier / ExecutionResult / AttemptRecord 等数据结构
├── repository.py     # ScriptRepository：脚本版本存储与统计
├── runner.py          # ScriptRunner：复用 py_step_runner 协议执行脚本子进程
├── explorer.py         # Explorer 体系：LLMExplorer / AgentExplorer，负责"从0生成脚本"
├── repairer.py          # Repairer 体系：LLMRepairer / AgentRepairer，负责"脚本报错后修复"
├── fallback.py           # FallbackExecutor：LLM/Agent 直接兜底出结果（不产出脚本）
├── executor.py            # HybridExecutor：顶层编排器，对外唯一入口
└── prompts/               # 探索/修复用的提示词模板（.md，与项目现有 prompts/ 风格一致）
```

### 3.1 `ExecutionTier`（执行层级枚举）

```python
class ExecutionTier(str, Enum):
    SCRIPT = "script"
    LLM = "llm"
    AGENT = "agent"
```

### 3.2 `TaskSpec`（一个"混合任务"的定义，调用方传入）

```python
@dataclass
class TaskSpec:
    task_id: str                       # 任务的稳定标识，脚本仓库按它归档/复用
    description: str                   # 自然语言目标描述，用于 Explorer/Repairer 的 prompt
    input_data: dict                   # 本次调用的具体输入（脚本 run(ctx) 能拿到）
    output_validator: Optional[Callable[[Any], tuple[bool, str]]] = None
        # 校验产出是否合格：返回 (是否通过, 原因)。不传则"不抛异常即算成功"。
    context_files: list[str] = field(default_factory=list)   # 探索/修复时可参考的资料路径（可选）
    allow_tiers: tuple[ExecutionTier, ...] = (SCRIPT, LLM, AGENT)  # 允许使用的层级（可裁剪，比如禁用 AGENT 控制成本）
    max_script_repair_attempts: int = 2      # 脚本报错后，先尝试修复几次再降级
    force_reexplore: bool = False            # 强制忽略已有脚本、重新探索（人工触发用）
    agent_fs_write_enabled: bool = False     # Explorer/Repairer 拉起的 Agent 是否允许写文件系统，默认关闭
```

### 3.3 `ScriptRepository`（脚本仓库：版本 + 统计）

存储位置：`.agent/hybrid_exec/scripts/<task_id>/`
```
<task_id>/
├── meta.json          # 当前 active 版本号、各版本统计
├── v1.py
├── v2.py
└── ...
```

`meta.json` 记录（每个版本一条）：`version, created_at, created_by(llm_explorer/agent_explorer/llm_repairer/agent_repairer/manual), success_count, fail_count, consecutive_fail, status(active/retired), last_error`。

关键方法：
```python
class ScriptRepository:
    def get_active_script(self, task_id: str) -> Optional[ScriptRecord]: ...
    def save_new_version(self, task_id: str, code: str, created_by: str) -> ScriptRecord: ...
    def save_repaired_version(self, task_id: str, code: str, created_by: str) -> ScriptRecord: ...
    def record_success(self, task_id: str, version: int) -> None: ...
    def record_failure(self, task_id: str, version: int, error: str) -> None: ...
    def retire(self, task_id: str, version: int, reason: str) -> None: ...
        # 连续失败达到阈值（如 3 次）自动退役，下次 run 强制重新探索
```

### 3.4 `ScriptRunner`（脚本执行，复用 py_step_runner 协议）

包一层薄的适配器：把 `TaskSpec.input_data` 组装成与 `py_step_runner.py` 相同的 request JSON（`step_id`/`session_dir`/`output_dir`/`inputs`/`params`/`app_cfg`），拉起同一个 `python -m mini_agent.workflow.py_step_runner` 子进程执行，解析同样的结果包协议。**不新写一套脚本执行器**，只是从 `hybrid_exec` 侧构造请求、调用同一入口。

```python
class ScriptRunner:
    def run(self, script_path: Path, task: TaskSpec, timeout: float) -> ScriptOutcome:
        # ScriptOutcome: ok, output, error, error_type, traceback, duration
        ...
```

### 3.5 `Explorer`（从 0 生成脚本 —— 优先 agent/llm 探索）

```python
class Explorer(ABC):
    @abstractmethod
    def explore(self, task: TaskSpec) -> str:
        """返回符合 run(ctx) 协议的脚本源码。"""
```

- `LLMExplorer`：单轮 `ctx.llm.ask()`（或独立 `LLMHelper.ask`），把 `TaskSpec.description` + `input_data` 样例 + `run(ctx)` 协议说明拼进 prompt，直接要求模型产出脚本源码。**成本低，适合规则清晰、输入结构稳定的任务**。
- `AgentExplorer`：拉起 `build_minimal_agent`，允许多轮 + 工具（比如先读一段样例数据、试跑几次再定稿）。**成本高但泛化强，适合任务描述模糊、需要先探查环境/数据形状的场景**。

策略默认：**先 LLMExplorer，产出的脚本过一遍 dry-run（用当前这次的 `input_data` 真跑一次）；dry-run 都过不了再升级 AgentExplorer**。这本身也是"优先低成本"的一次具体应用。

### 3.6 `Repairer`（脚本报错后修复 —— 优先修脚本而不是绕过脚本）

```python
class Repairer(ABC):
    @abstractmethod
    def repair(self, task: TaskSpec, broken_code: str, outcome: ScriptOutcome) -> str:
        """返回修复后的脚本源码。"""
```

- `LLMRepairer`：把 `broken_code` + `traceback` + `TaskSpec.description` 拼进 prompt，单轮请求"定位并修复"。适合语法错误、边界条件遗漏等局部问题。
- `AgentRepairer`：LLM 修复仍不过，或错误看起来涉及"需要理解外部环境/多文件协作"（比如依赖了不存在的文件路径、需要先探查数据结构），升级到 Agent 修复——允许多轮 + 读文件 + 反复试跑。

`max_script_repair_attempts` 控制"脚本层面"最多修几轮（先 LLMRepairer 若干次，仍不行再 AgentRepairer 一次），超过后才彻底放弃脚本、走 `FallbackExecutor`。

### 3.7 `FallbackExecutor`（彻底放弃脚本时的兜底）

```python
class FallbackExecutor:
    def llm_direct(self, task: TaskSpec) -> str: ...     # 单轮直接产出答案，不生成脚本
    def agent_direct(self, task: TaskSpec) -> str: ...   # 多轮 agent 直接产出答案，不生成脚本
```

这一层**不产出脚本、不写回仓库**——它是"这次先把事办了"的应急通道，避免整个任务因为脚本反复失败而彻底卡死。是否要把这次 agent/llm 直接兜底出的解法**回灌**成新脚本（相当于触发一次 Explorer），由 `HybridExecutor` 的策略决定（见 4.3）。

### 3.8 `HybridExecutor`（顶层编排器，唯一对外入口）

```python
class HybridExecutor:
    def __init__(self, repo: ScriptRepository, script_runner: ScriptRunner,
                 llm_explorer: Explorer, agent_explorer: Explorer,
                 llm_repairer: Repairer, agent_repairer: Repairer,
                 fallback: FallbackExecutor) -> None: ...

    def run(self, task: TaskSpec) -> ExecutionResult: ...
```

`ExecutionResult`：
```python
@dataclass
class ExecutionResult:
    ok: bool
    output: Any
    tier_used: ExecutionTier          # 最终这次是靠 script/llm/agent 完成的
    script_version: Optional[int]     # 若用脚本，记录版本号
    attempts: list[AttemptRecord]     # 完整决策轨迹，便于事后复盘/写入 traces.jsonl
    duration: float
```

---

## 4. 执行流程（决策逻辑）

```
HybridExecutor.run(task)
│
├─ 1. 若 task.force_reexplore=False 且仓库中存在 active 脚本
│      → 直接用 ScriptRunner 跑该脚本
│      ├─ 通过 output_validator → 记 success，返回 (tier=SCRIPT)
│      └─ 失败（异常 / 校验不过）→ 进入【修复阶段】
│
├─ 2. 若无可用脚本（或强制重探索）→ 进入【探索阶段】
│
├─【探索阶段】（仅在 SCRIPT/LLM/AGENT 都被允许时才产出脚本）
│      a. LLMExplorer.explore(task) → 脚本草稿
│      b. 用本次真实 input_data 做一次 dry-run
│         ├─ 通过 → 存入仓库为新 active 版本，按脚本执行路径返回
│         └─ 不通过 → 升级 AgentExplorer.explore(task)（若 allow_tiers 含 AGENT）
│              ├─ dry-run 通过 → 存入仓库，返回
│              └─ 仍不通过 → 不产出脚本，直接走【Fallback】
│
├─【修复阶段】（针对已有脚本执行失败的情况）
│      for attempt in range(max_script_repair_attempts):
│          ├─ 前 N-1 次用 LLMRepairer
│          ├─ 最后一次用 AgentRepairer（若允许 AGENT）
│          修复后重新 dry-run（用同一份 input_data）：
│          ├─ 通过 → 存为新版本（版本号 +1，保留修复前版本历史），
│          │         按脚本执行路径返回
│          └─ 不通过 → 继续下一次尝试
│      全部尝试失败 → 该脚本 consecutive_fail 计数，
│                     达到阈值则 repo.retire(task_id, version)（下次强制重探索）
│                     → 走【Fallback】
│
└─【Fallback】（脚本彻底不可用时的兜底，不阻塞任务）
       ├─ 若 allow_tiers 含 LLM → FallbackExecutor.llm_direct(task)
       │      ├─ 通过 output_validator → 返回 (tier=LLM)
       │      └─ 不通过 → 继续
       └─ 若 allow_tiers 含 AGENT → FallbackExecutor.agent_direct(task)
              → 返回 (tier=AGENT)，无论校验是否通过都作为最终结果
                （已经是最高能力层级，没有再降级的空间，如实返回结果+校验结论）
```

**成本优先级体现在两处**：
- 探索时"先 LLM 后 Agent"（探索能力：Agent > LLM，但先试便宜的）；
- 执行时"先脚本"，脚本坏了"先修脚本（LLM 修复 → Agent 修复）"，都失败了才整体降级到"跳过脚本直接问 LLM/Agent 要答案"。

---

## 5. 与 workflow 的接入方式

新增 workflow step 类型 `hybrid_step`（与 `python_step` 平级，同样受 `cfg.workflow.hybrid_step_enabled` 开关保护）：

```yaml
- id: extract_entities
  type: hybrid_step
  task_id: extract_entities_v1        # 对应 ScriptRepository 里的仓库 key，可跨 workflow 复用
  description: "从输入文本中抽取人名/机构名，返回 JSON 列表"
  depends_on: [fetch_text]
  params:
    max_script_repair_attempts: 2
    allow_tiers: [script, llm, agent]
```

`HybridStepExecutor`（新增，挂在 `executors.py` 里，与 `PythonStepExecutor` 平级）负责：把 workflow 的 `step.params` + 上游 `inputs` 组装成 `TaskSpec`，调用 `HybridExecutor.run()`，把 `ExecutionResult.output` 作为 step 输出、`attempts` 写入 step 的调试日志（复用现有 workflow 调试日志基础设施）。

**同时保留脱离 workflow 的独立调用方式**（这是本次的核心诉求）：

```python
from mini_agent.hybrid_exec import HybridExecutor, TaskSpec, default_executor

result = default_executor(project_root).run(TaskSpec(
    task_id="daily_report_summarize",
    description="把今日 traces.jsonl 汇总成一段摘要",
    input_data={"traces": [...]},
))
```

供 daemon 自主循环（`AutonomousLoop`）、cron job、CLI 命令等任意场景直接使用，不必套一层 workflow yaml。

---

## 6. 存储与可观测性

- 脚本仓库：`.agent/hybrid_exec/scripts/<task_id>/`（同 §3.3）。
- **（P3 已实现）** 每次 `run()` 的完整决策轨迹（`attempts` + 最终结果）由 `recorder.py::RunRecorder` 落盘到 `.agent/hybrid_exec/runs/<task_id>/<run_id>.json`；同目录下维护一份滚动聚合 `summary.json`（`total_runs`/`success_runs`/`fail_runs`/`tier_counts`/`last_run_*`），供事后统计"这个 task 的脚本命中率/tier 分布"而不必扫描全部 run 文件，也是未来做"跨 run 自动触发重探索"（P4）的数据基础。独立调用（`default_executor()`）与 workflow 场景（`hybrid_step`）共享同一份统计口径。
- 可选：kanban 增加一个轻量面板展示各 `task_id` 的当前 tier 分布（脚本命中率），复用现有 kanban SSE/面板基础设施（P4 范围，尚未实现）。

---

## 7. 配置项（`agent_config.json` 新增 `hybrid_exec` 节）

```json
{
  "hybrid_exec": {
    "enabled": false,
    "hybrid_step_enabled": false,
    "default_max_script_repair_attempts": 2,
    "retire_after_consecutive_fail": 3,
    "script_dry_run_timeout_seconds": 60,
    "default_allow_tiers": ["script", "llm", "agent"]
  }
}
```

默认关闭，与 `python_step_enabled` 一致的安全考量（脚本生成/执行本质上是"允许 LLM 产出可执行代码并跑起来"，需要显式开启）。

---

## 8. 分阶段实施计划

- **P1（已完成）**：`spec.py` / `repository.py` / `runner.py`（复用 py_step_runner 协议）/ `explorer.py`（LLMExplorer）/ `repairer.py`（LLMRepairer）/ `fallback.py` / `executor.py` 主干流程跑通，单元测试覆盖"脚本成功/脚本失败修复成功/脚本失败修复失败降级"三条主路径。独立 Python API（`default_executor()`），未接 workflow。
- **P2（已完成）**：补齐 `AgentExplorer` / `AgentRepairer` / `FallbackExecutor.agent_direct`（`_agent.py`，复用 `agent_spawn.build_minimal_agent`，`agent_fs_write_enabled` 换算成 `sandbox` 参数）；接入 `hybrid_step` workflow 类型——**未修改 workflow 包任何源码**，通过 `register_step_executor()` 公开扩展点注册（`workflow_integration.py` + 薄插件文件 `myplugins/hybrid_step.py`，删除该插件文件即等效禁用）。
- **P3（已完成）**：`run` 记录落盘 + 统计聚合（`recorder.py::RunRecorder`，`.agent/hybrid_exec/runs/<task_id>/{summary.json, <run_id>.json}`，独立调用与 workflow 场景共享同一份统计口径）；退役策略联调——补充集成测试验证"脚本反复失败触发 `ScriptRepository` 自动退役后，下一次 `run()` 调用透明地重新走探索流程"以及"修复成功会重置连续失败计数、不会被误退役"两条链路。
- **P4（已完成）**：
  - **kanban 面板**：`kanban_summary.py::build_kanban_summary()` 汇总所有 `task_id` 的脚本仓库状态 + run 统计；新增只读端点 `GET /v1/hybrid_exec/summary`（`api/routes.py`）、`AgentClient.hybrid_exec_summary()`（`apps/mini_agent_kanban/client.py`）、新 Tab "🧪 混合执行"（`apps/mini_agent_kanban/app.py::render_hybrid_exec_tab`），展示每个 task 的当前生效版本/累计成功率/连续失败次数/执行次数/tier 命中分布。这是本次唯一触碰 `hybrid_exec` 包之外文件的部分（`api/routes.py`/`apps/mini_agent_kanban/{client,app}.py`），沿用项目里 `feedback_loop_summary` 那一套"只读汇总端点 + 看板 Tab"的既有模式，未改动其它端点/Tab 的行为。
  - **跨 run 自动重探索触发**：`policy.py::ReexplorePolicy`，基于当前 active 脚本版本的累计成功率（简化实现：用该版本自诞生以来的全部历史，非滑动窗口）判断是否"机会主义地"主动探索一版新脚本——探索成功则直接采用，探索失败不影响，继续使用现有脚本。默认不启用（`enabled=False`），独立调用通过 `default_executor(..., reexplore_policy=...)` 开启，workflow 场景通过 `step.params.reexplore_enabled` 等参数开启。滑动窗口式的"最近 N 次成功率"因为需要更精细的数据结构，留给有实际使用数据后再细化。

---

## 9. 待确认问题（已确认）

1. **脚本仓库的 key 粒度**：**确认**——MVP 按最简单的"一个 `task_id` 一个 active 版本"处理，不做输入结构指纹分支。后续如果实际使用中发现同一 `task_id` 输入形状差异很大导致脚本反复失配，再在此基础上加分支版本，不在 MVP 范围内。
2. **`output_validator`**：**确认**——不要求调用方必须传。不传时默认"脚本/LLM/Agent 执行过程中不抛异常、且（脚本场景）dry-run/run 正常返回即视为成功"。传了则以 validator 结果为准。
3. **AgentExplorer/AgentRepairer 的执行权限**：**确认**——允许读写项目文件（复用 `build_minimal_agent` 的 `sandbox` 参数，非受限沙箱），但在 `TaskSpec`/`hybrid_step` 的 params 里提供显式开关 `agent_fs_write_enabled`（默认 `False`），只有显式打开才允许 Explorer/Repairer 拉起的 Agent 具备写权限；关闭时仍可用 Agent 探索/修复但走只读沙箱（`sandbox=True` 且限制写工具，具体对接方式在 P2 落地时结合 `PermissionGuard` 细化）。
4. **人工审核环节**：**确认**——完全自动化，只靠 dry-run（以及 `output_validator`，若提供）判定是否转正为 active 版本，不引入人工审核步骤。

---

以上方案已确认，进入 P1 实施：落地独立的 `hybrid_exec` 包和单元测试，不改动现有 `workflow` 代码，确认稳定后再做 P2 的 workflow 接入与 Agent 探索/修复。

---

## 10. 当前状态（P1-P4 完成后）

- P1-P4 全部完成，54 个单元/集成测试全部通过（`tests/test_hybrid_exec.py` / `_p2.py` / `_p3.py` / `_p4.py` / `test_hybrid_exec_summary_route.py`），且逐一验证过对现有 workflow/kanban 相关测试无回归。
- **仍未做的事**：所有测试都用 fake/mock 组件隔离，还没有接一次真实 LLM/Agent 跑一个真实任务做端到端验证——`LLMExplorer`/`AgentExplorer` 产出的脚本质量、提示词效果如何，都还没有实测数据支撑。建议在正式投入使用前，先挑一个真实小任务（比如某个抽取/摘要类需求）跑一次端到端，确认 prompt 效果和整条链路在真实环境里可用。
- kanban 面板、`ReexplorePolicy` 目前都是"能力已具备但默认关闭/低调"的状态：kanban Tab 是纯只读展示，不影响任何执行路径；`ReexplorePolicy` 默认 `enabled=False`，不会改变现有脚本的使用行为，需要显式开启。

## 11. LLM 来源：独立执行自动加载 `providers.json` / 嵌入 workflow 接收传入的 llm（已完成）

补充需求：`hybrid_exec` 单独执行时应像主 Agent 一样自动加载 `providers.json`；嵌入 workflow（尤其是被 `python_step` 脚本当库调用）时应能接收 workflow 已经解析好的 `llm` 对象直接复用，而不是每次都重新解析配置。

落地方式：

- `LLMExplorer`/`LLMRepairer`/`FallbackExecutor` 新增可选构造参数 `llm`（鸭子类型，只要求实现 `ask(prompt, *, system=...) -> str`，`LLMHelper`/`PyStepLLM` 均满足）：
  - 不传（独立调用默认路径）→ 真正发起调用时才 `build_llm_helper(app_cfg)` → `mini_agent.config.load_config()` 按 `project_root` 自动读取 `providers.json`，与主 Agent/`python_step` 的 `ctx.llm` 同一条解析路径。
  - 传了 → 直接复用，不再重新 `load_config()`。`default_executor(project_root, llm=ctx.llm)` 把这个参数透传给三者，可在 `python_step` 脚本里把 `hybrid_exec` 当库调用、直接把 `ctx.llm` 传进去。
  - `AgentExplorer`/`AgentRepairer`/`FallbackExecutor.agent_direct` 需要完整 Agent（多轮 + 工具），不受此参数影响，仍按 `RunnerAppConfig` 现起临时 Agent。
- `workflow_integration.py::HybridStepExecutor.execute`：不再无条件用全局 `cfg.model`，改为按 `step.model → wf.defaults.model → 全局 cfg.model` 三层查找（`runner._effective_step_field`，与其它 step 类型同一套规则）解析出 `effective_model`，据此构造一次 `LLMHelper`，供本次 `hybrid_step` 执行内的 `llm_explorer`/`llm_repairer`/`fallback` 共用（避免三处各自重新解析配置，且都对齐同一次 workflow 运行的模型选择）。
- `examples/hybrid_exec_demo.py` 新增场景六，用一个不发网络请求的假 `llm` 对象验证 `LLMExplorer(app_cfg, llm=...)` 确实直接复用了传入对象。
- 相关说明已补充进 `docs/hybrid-exec-guide.md` §1.1、§5。
- 54 个既有单元/集成测试全部保持通过，未引入回归。

## 12. `examples/hybrid_exec_demo.py`：改用真实 `providers.json`（已完成）

原 P1-P4 阶段的演示脚本用三个"规则版"替身（`RuleBasedExplorer`/
`RuleBasedRepairer`/`RuleBasedFallback`）模拟 LLM 行为，只验证了"除 LLM/
Agent 调用本身之外"的编排链路。按新要求改为：

- 不再有任何规则版/模拟替身，`Explorer`/`Repairer`/`FallbackExecutor` 全部
  用 §11 新增的真实类（经 `default_executor(project_root)`，不传 `llm=`，
  走独立执行默认路径）。
- 演示开头新增环境检测：用真实 `load_config()` 判断 `project_root` 下是否
  有可用的 provider + api_key；不可用则打印"如何配置 `providers.json`"的
  指引后直接退出，不用假数据把演示硬撑"跑通"。
- 演示会把项目根目录真实的 `providers.json`（如果存在）复制进独立的
  `examples/_hybrid_exec_demo_workspace/` 工作区，避免演示产生的
  `.agent/hybrid_exec/` 数据污染真实项目目录，但用的是同一份真实配置内容。
- 已在本仓库沙箱环境验证：填入一个格式正确但无效的 api_key 后运行，
  确实会真实请求到 `api.anthropic.com` 并收到真实的 401 鉴权错误（而不是
  被任何模拟逻辑拦截、假装成功）——证明改动后的演示走的是真实网络请求
  路径。真正配置好有效 api_key 后即可端到端验证探索/修复/兜底的真实产出
  质量。
- `.gitignore` 补充 `examples/_hybrid_exec_demo_workspace/`（此前文档声称
  已忽略但实际条目缺失）。
- `docs/hybrid-exec-guide.md` §十一 同步重写，去掉规则版替身的描述。
