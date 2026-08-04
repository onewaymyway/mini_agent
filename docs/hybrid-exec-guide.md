# 脚本/LLM/Agent 混合执行系统（hybrid_exec）指南

> 设计方案：[next_doc/hybrid_exec_design_plan.md](../next_doc/hybrid_exec_design_plan.md)（P1-P4 均已完成）

`hybrid_exec` 是一个**独立于 workflow 之外**的执行系统，把"探索优先
agent/llm、执行优先脚本、脚本故障时优先修复脚本、修复不了才降级"这套
决策逻辑封装成可复用的 Python 对象：

- 可以脱离 workflow 被任何代码直接 `import` 调用（daemon 自主决策、CLI
  工具、其它 workflow 的 `python_step` 内部等）——独立执行时自动按项目
  `providers.json` 解析 LLM（同主 Agent），嵌入 workflow 时可直接传入
  workflow 已经拿到手的 `llm`（如 `ctx.llm`）复用，见 §1.1；
- 也可以作为 workflow 的一种新 step 类型 `hybrid_step` 接入，复用
  `python_step` 既有的子进程隔离与 `ctx.llm`/`ctx.run_agent_turn` 基础设施；
- 脚本产物有版本管理、成功率统计、退役机制，能沉淀成"越用越省钱"的能力库。

三种执行手段的经济学关系：

| | 执行成本 | 泛化能力 |
|---|---|---|
| 脚本（script） | 低 | 低（只覆盖写脚本时想到的情况） |
| LLM 单轮 | 中 | 中 |
| Agent（多轮 + 工具） | 高 | 高 |

---

## 一、快速开始（独立调用）

```python
from mini_agent.hybrid_exec import TaskSpec, default_executor

executor = default_executor(project_root="/path/to/project")
result = executor.run(TaskSpec(
    task_id="extract_entities_v1",       # 稳定标识，脚本仓库按它归档/复用
    description="从输入文本中抽取人名/机构名，返回 JSON 列表",
    input_data={"text": "..."},
))

if result.ok:
    print(result.output)          # 本次产出
    print(result.tier_used)       # ExecutionTier.SCRIPT / LLM / AGENT
    print(result.script_version)  # 若走的是脚本，记录具体版本号
```

`default_executor(project_root, mini_agent_config=None, ...)` 是便捷工厂：
不需要手动拼 `ScriptRepository`/`ScriptRunner`/`Explorer`/`Repairer` 等
组件即可拿到一个默认配置的 `HybridExecutor`。若已有加载好的
`mini_agent.config.Config` 对象，传入 `mini_agent_config=cfg` 可以复用其
provider/超时等设置（内部转换为 `RunnerAppConfig`）。

首次调用（仓库里还没有该 `task_id` 对应的脚本）会先走一轮探索
（LLMExplorer → 若失败再 AgentExplorer），探索出的脚本用本次真实
`input_data` 做一次 dry-run，通过才转正为 active 版本存入仓库；之后同一
`task_id` 的调用会优先直接跑这个脚本。

### 1.1 LLM 从哪来：独立执行自动加载 `providers.json`，嵌入 workflow 接收传入的 llm

`LLMExplorer`/`LLMRepairer`/`FallbackExecutor` 都支持一个可选的 `llm`
参数，决定探索/修复/兜底时用哪个 LLM 对象发起请求：

- **独立执行（不传 `llm`）**：真正发起 LLM 调用时，才会经
  `mini_agent.config.load_config()` 按 `project_root` 自动读取该项目的
  `providers.json`（与主 Agent、`python_step` 的 `ctx.llm` 是同一条解析
  路径，优先级同样是 CLI 参数 > `agent_config.json` > `providers.json` >
  环境变量 > 默认值），**不需要调用方手动传 model/provider/api_key**：

  ```python
  from mini_agent.hybrid_exec import default_executor, TaskSpec

  executor = default_executor(project_root="/path/to/project")  # 自动读 providers.json
  result = executor.run(TaskSpec(task_id="...", description="...", input_data={...}))
  ```

  > **实现细节（与主 Agent 对齐的关键点）**：`default_executor()` 在**构造
  > 期间**只调用一次 `build_llm_helper(app_cfg)`，得到的 `LLMHelper`（内部
  > 持有一条真正的 `LLMClientPool`——完整解析 `providers.json` 的
  > `llm_fallback_chain`、多 key 轮转、key cooldown、多 provider 故障
  > 转移，与 `agent/core.py` 启动时 `LLMClientPool.from_config(cfg)` 走的
  > 是同一份代码）后，会被 `llm_explorer`/`llm_repairer`/`fallback` 三者
  > **共用**，贯穿这一个 `HybridExecutor` 实例的整个生命周期——而不是三者
  > 各自持有 `llm=None`、在每次 `.ask()` 时才各自惰性重建一整条全新的
  > `LLMClientPool`。后者是曾经存在过的问题：那样的话每次探索/修复/兜底
  > 调用之间不共享任何 pool 状态（多 key 轮转位置、失败 key 的 cooldown
  > 记录、fallback_chain 当前用到第几条配置全部清零重来），实质上退化成
  > "每次都用 `fallback_chain` 第一条配置硬调一次"，既没有真正吃到多 key
  > 轮转的效果，也没有像 Agent 那样在某条配置连续报错时记住并暂时避开它。
  > 现在的实现在这一点上与主 Agent 完全一致。

- **嵌入 workflow（比如在 `python_step` 脚本内部把 `hybrid_exec` 当库用）**：
  把 workflow 已经拿到手的 `ctx.llm`（`PyStepLLM` 实例）或任何一个已构造好
  的 `LLMHelper` 直接传给 `llm=`，`hybrid_exec` 会原样复用它，不再重新
  `load_config()`：

  ```python
  # 在某个 python_step 脚本的 run(ctx) 里：
  def run(ctx):
      from mini_agent.hybrid_exec import default_executor, TaskSpec

      executor = default_executor(ctx.project_root, llm=ctx.llm)
      result = executor.run(TaskSpec(
          task_id="summarize_v1",
          description="把输入文本压缩成一句话摘要",
          input_data={"text": ctx.params.get("text", "")},
      ))
      return result.output
  ```

  只要传入对象实现 `ask(prompt, *, system=...) -> str`（`LLMHelper`/
  `PyStepLLM` 均满足，鸭子类型判断，不要求继承任何基类），就会被直接复用，
  从而沿用 workflow 当前这次运行已经解析好的 provider/模型/重试与
  fallback 策略，避免重复解析配置、也不会绕过 workflow 对本次运行做的
  provider 覆盖。

  `hybrid_step`（workflow 内置 step 类型，见 §5）已经按这个规则接好：会
  按 `step.model → wf.defaults.model → 全局 cfg.model` 三层查找（与其它
  step 类型同一套 `_effective_step_field` 规则）解析出一个 `LLMHelper`，
  在本次 step 执行内传给 `llm_explorer`/`llm_repairer`/`fallback` 共用，
  不必手动传参。

  > 说明：这个 `llm` 只影响 `LLMExplorer`/`LLMRepairer`/
  > `FallbackExecutor.llm_direct` 这几处**单轮**调用。`AgentExplorer`/
  > `AgentRepairer`/`FallbackExecutor.agent_direct` 需要的是一个可多轮、
  > 能调用工具的完整 Agent（不是单次问答），因此固定通过
  > `build_minimal_agent()` 按 `RunnerAppConfig` 现起一个临时 Agent，不受
  > `llm` 参数影响；如果需要让 Agent 层也对齐 workflow 的模型选择，通过
  > `mini_agent_config=cfg` 或直接构造 `RunnerAppConfig` 传入相应的
  > model/llm_provider 字段。

---

## 二、核心概念

### 2.1 `ExecutionTier`（执行层级）

```python
class ExecutionTier(str, Enum):
    SCRIPT = "script"
    LLM = "llm"
    AGENT = "agent"
```

### 2.2 `TaskSpec`（一次混合执行任务的定义）

| 字段 | 说明 |
|---|---|
| `task_id` | 任务稳定标识，脚本仓库按它归档/复用 |
| `description` | 自然语言目标描述，用于 Explorer/Repairer 的 prompt |
| `input_data` | 本次调用的具体输入（脚本 `run(ctx)` 能拿到） |
| `output_validator` | 可选，`Callable[[Any], tuple[bool, str]]`，校验产出是否合格；不传时默认"不抛异常即视为成功" |
| `context_files` | 可选，探索/修复时可参考的资料路径 |
| `allow_tiers` | 允许使用的层级，默认 `(SCRIPT, LLM, AGENT)`，可裁剪以控制成本（如禁用 `AGENT`） |
| `max_script_repair_attempts` | 脚本报错后先尝试修复几次再降级，默认 2 |
| `force_reexplore` | 强制忽略已有脚本、重新探索 |
| `agent_fs_write_enabled` | Explorer/Repairer 拉起的 Agent 是否允许写文件系统，默认 `False`（走只读沙箱） |
| `script_timeout_seconds` | 单次脚本执行（含 dry-run）超时时间，默认 60 秒 |

### 2.3 `ExecutionResult`（返回值）

```python
@dataclass
class ExecutionResult:
    ok: bool
    output: Any
    tier_used: ExecutionTier          # 最终靠 script/llm/agent 完成
    script_version: Optional[int]     # 若用脚本，记录版本号
    attempts: list[AttemptRecord]     # 完整决策轨迹，便于事后复盘
    duration: float
```

`ExecutionResult.to_dict()` 可直接序列化为 JSON。

---

## 三、执行流程（决策逻辑）

```
HybridExecutor.run(task)
│
├─ 1. 若存在 active 脚本（且未 force_reexplore）→ 直接跑
│      ├─ 通过 output_validator → 记 success，返回 (tier=SCRIPT)
│      └─ 失败（异常 / 校验不过）→ 进入【修复阶段】
│
├─ 2. 若无可用脚本（或强制重探索）→ 进入【探索阶段】
│      LLMExplorer 优先，dry-run 不过才升级 AgentExplorer；
│      dry-run 通过才存入仓库、转正为 active 版本
│
├─【修复阶段】（针对已有脚本执行失败）
│      前 N-1 次用 LLMRepairer，最后一次（若预算允许且允许 AGENT）用
│      AgentRepairer；每次修复后重新 dry-run，通过则存为新版本
│      （`max_script_repair_attempts` 控制轮数）
│      全部失败 → 连续失败计数达阈值时 ScriptRepository 自动 retire
│      （下次 run 强制重新探索）→ 走【Fallback】
│
└─【Fallback】（脚本彻底不可用时的兜底，不阻塞任务）
       LLM 直接给答案 → 不满足 output_validator → Agent 直接给答案
       （Agent 已是最高能力层级，如实返回结果 + 校验结论，不再降级）
```

成本优先级体现在两处：探索时"先 LLM 后 Agent"；执行时"先脚本"，
脚本坏了"先修脚本（LLM 修复 → Agent 修复）"，都失败了才整体降级到
"跳过脚本直接问 LLM/Agent 要答案"。

任意 Explorer/Repairer/Fallback 实现抛异常（含 `NotImplementedError`）
时，`HybridExecutor` 统一捕获、记一条失败 attempt 后继续按流程往下走，
不会导致整个 `run()` 崩溃。

---

## 四、脚本仓库（`ScriptRepository`）

存储位置：`.agent/hybrid_exec/scripts/<task_id>/`

```
<task_id>/
├── meta.json          # 各版本元信息 + 当前 active 版本号
├── v1.py
├── v2.py
└── ...
```

`meta.json` 每个版本记录：`version`/`created_at`/
`created_by`（`llm_explorer`/`agent_explorer`/`llm_repairer`/
`agent_repairer`/`manual`）/`success_count`/`fail_count`/
`consecutive_fail`/`status`（`active`/`retired`）/`last_error`。

脚本代码本身满足 `py_step_runner.py` 的"子进程 + `run(ctx)` 入口 + 单行
JSON 结果包"协议——`hybrid_exec` 不新写一套脚本沙箱，`ScriptRunner`
只是把 `TaskSpec` 组装成 `py_step_runner` 认识的 request JSON，拉起同一
个 `python -m mini_agent.workflow.py_step_runner` 子进程执行。

连续失败达到 `retire_after_consecutive_fail`（`ScriptRepository`
构造参数，默认 3）会自动退役该版本，下次 `run()` 透明地重新走探索
流程；一次修复成功会重置连续失败计数，不会被误退役。

---

## 五、接入 workflow：`hybrid_step`

新增 workflow step 类型 `hybrid_step`，与 `python_step` 平级：

```yaml
steps:
  - id: extract_entities
    type: hybrid_step
    depends_on: [fetch_text]
    params:
      task_id: extract_entities_v1        # 仓库 key，可跨 workflow 复用
      description: "从输入文本中抽取人名/机构名，返回 JSON 列表"
      input:                               # 可选：额外字面量输入，会与上游输出合并
        hint: "只要中文人名"
      allow_tiers: [script, llm, agent]     # 可选，默认三层都允许
      max_script_repair_attempts: 2         # 可选，默认 2
      agent_fs_write_enabled: false         # 可选，默认 false
      result_required_keys: [entities]      # 可选：产出应是 dict 时的必填 key 校验
      force_reexplore: false                # 可选：忽略已有脚本，强制重新探索
      reexplore_enabled: false              # 可选：是否启用跨 run 主动重探索（见 §七）
```

step 输出即 `HybridExecutor.run()` 的 `output`（若是 dict 会序列化成
JSON 文本），可用 `{extract_entities.output}` 占位符或下游
`ctx.input_json()` 消费。step 失败（脚本/LLM/Agent 全部手段耗尽）时会
抛出 `RuntimeError`，并提示完整决策轨迹落盘位置
（`hybrid_step_<id>_trace.json`）。

`hybrid_step` 用的 llm 来自 workflow 本次运行，不是每次都重新解析：按
`step.model → wf.defaults.model → 全局 cfg.model` 三层查找（与其它 step
类型同一套 `_effective_step_field` 规则，可在这个 step 上单独用
`model: xxx` 覆盖模型）解析出一份 `RunnerAppConfig`，据此构造一个
`LLMHelper`，供本次 `hybrid_step` 执行内的 `llm_explorer`/
`llm_repairer`/`fallback.llm_direct` 共用（见 §1.1）；`AgentExplorer`/
`AgentRepairer`/`fallback.agent_direct` 仍按同一份 `RunnerAppConfig`
现起临时 Agent。

### 5.1 启用方式：插件文件，不是配置开关

`hybrid_step` **不**通过 `cfg.workflow.python_step_enabled` 那类
配置开关控制，而是靠 `myplugins/hybrid_step.py`（薄插件文件）是否被
插件发现机制扫描到——**删除该文件即等效于禁用**，不需要额外改
`agent_config.json`。真正的注册逻辑在
`mini_agent.hybrid_exec.workflow_integration.register()` 里，通过
`workflow/executors.py` 已有的公开扩展点 `register_step_executor()`
完成注册（**未修改 workflow 包任何源码**，与
`myplugins/example_http_step.py` 演示的机制完全一致）。

---

## 六、可观测性

### 6.1 run 记录落盘

每次 `run()` 的完整决策轨迹（`attempts` + 最终结果）由
`RunRecorder`（`recorder.py`）落盘到
`.agent/hybrid_exec/runs/<task_id>/<run_id>.json`；同目录下维护一份
滚动聚合 `summary.json`（`total_runs`/`success_runs`/`fail_runs`/
`tier_counts`/`last_run_*`），供事后统计"这个 task 的脚本命中率/tier
分布"而不必扫描全部 run 文件。**独立调用**（`default_executor()`）与
**workflow 场景**（`hybrid_step`）共享同一份统计口径（同一个
`.agent/hybrid_exec/runs/` 目录）。

### 6.2 Kanban 面板

只读端点 `GET /v1/hybrid_exec/summary`（`api/routes.py`）汇总所有
`task_id` 的脚本仓库状态 + run 统计（`kanban_summary.py::build_kanban_summary()`）；
Kanban 看板新增 "🧪 混合执行" Tab
（`apps/mini_agent_kanban/app.py::render_hybrid_exec_tab`），展示每个
task 的当前生效版本、累计成功率、连续失败次数、执行次数、tier 命中
分布。这是纯只读展示，不影响任何执行路径。沿用项目里
`feedback_loop_summary` 那一套"只读汇总端点 + 看板 Tab"的既有模式。

对应客户端方法：`AgentClient.hybrid_exec_summary()`
（`apps/mini_agent_kanban/client.py`）。

---

## 七、跨 run 主动重探索（`ReexplorePolicy`）

`ScriptRepository` 现有的 retire 机制只在"连续失败达到阈值"时才会
强制退役重探索，对付不了"脚本时好时坏、成功率一直不高但从没连续失败
够阈值"这种慢性问题。`ReexplorePolicy` 用于在还没触发强制 retire 之前
主动"顺手"探索一版新脚本：

```python
@dataclass
class ReexplorePolicy:
    enabled: bool = False
    min_samples: int = 5              # 样本数不足时不判断，避免运气误判
    success_rate_threshold: float = 0.6  # 累计成功率低于此阈值触发探索
```

判断依据是当前 active 版本**自诞生以来的全部历史**成功率（简化实现，
非滑动窗口）。探索成功则直接采用新版本；探索失败不影响现有脚本，
继续正常使用。

- **默认不启用**（`enabled=False`），不会改变现有脚本的使用行为。
- 独立调用：`default_executor(project_root, reexplore_policy=ReexplorePolicy(enabled=True))`
- workflow 场景：`hybrid_step` 的 `params.reexplore_enabled: true`
  （另可通过 `reexplore_min_samples`/`reexplore_success_rate_threshold`
  覆盖默认阈值）

---

## 八、配置与安全考量

`hybrid_exec` 本身**没有** `agent_config.json` 全局开关（不同于
`python_step_enabled`）；对外可见的两个"开关"：

1. **独立调用**：只要 `import mini_agent.hybrid_exec` 并调用即可使用，
   无需任何配置项开启——与直接调用 LLM/Agent API 的心智模型一致。
2. **workflow 接入**：靠 `myplugins/hybrid_step.py` 插件文件是否存在
   （见 §5.1）。

Agent 探索/修复默认权限收紧：`TaskSpec.agent_fs_write_enabled` /
`hybrid_step` 的 `params.agent_fs_write_enabled` 默认 `False`——
Explorer/Repairer 拉起的 Agent 只在显式打开时才允许写文件系统，关闭时
走只读沙箱（复用 `agent_spawn.build_minimal_agent` 的 `sandbox` 参数）。

脚本仓库不属于项目核心代码，不使用 T0-T3 风险分级、不使用 git
worktree（与 `evolution/state_repo.py` 的重量级机制区分开），只是一个
轻量的本地版本目录 + 元信息 JSON。

转正为 active 版本完全自动化：只靠 dry-run（以及 `output_validator`，
若提供）判定，不引入人工审核步骤。

---

## 九、模块结构

```
src/mini_agent/hybrid_exec/
├── __init__.py            # 对外导出（TaskSpec/HybridExecutor/default_executor 等）
├── spec.py                 # TaskSpec / ExecutionTier / ExecutionResult / AttemptRecord / ScriptOutcome
├── repository.py            # ScriptRepository：脚本版本存储与统计
├── runner.py                 # ScriptRunner：复用 py_step_runner 协议执行脚本子进程
├── explorer.py                 # Explorer 体系：LLMExplorer / AgentExplorer
├── repairer.py                  # Repairer 体系：LLMRepairer / AgentRepairer
├── fallback.py                   # FallbackExecutor：LLM/Agent 直接兜底出结果
├── executor.py                    # HybridExecutor：顶层编排器 + default_executor 工厂
├── recorder.py                     # RunRecorder：run 记录落盘 + summary.json 聚合
├── policy.py                        # ReexplorePolicy：跨 run 主动重探索策略
├── kanban_summary.py                 # build_kanban_summary()：供看板一次性拉取的只读汇总
├── workflow_integration.py           # hybrid_step 接入 workflow（register_step_executor）
├── _llm.py / _agent.py               # LLM/Agent 具体实现细节
└── prompts/                          # 探索/修复用的提示词模板
    ├── explore_script.md
    ├── explore_script_agent.md
    ├── repair_script.md
    ├── repair_script_agent.md
    └── fallback_agent.md

myplugins/hybrid_step.py    # 薄插件文件：注册 hybrid_step，删除即禁用
```

对应测试：`tests/test_hybrid_exec.py`（P1 主干流程）、
`test_hybrid_exec_p2.py`（Agent 探索/修复 + workflow 接入）、
`test_hybrid_exec_p3.py`（run 记录 + 退役联调）、
`test_hybrid_exec_p4.py`（kanban 汇总 + `ReexplorePolicy`）、
`test_hybrid_exec_summary_route.py`（`/v1/hybrid_exec/summary` 端点）。

---

## 十、当前状态与已知限制

- P1-P4 均已完成，全部单元/集成测试通过，且已验证对现有
  workflow/kanban 相关测试无回归。
- Kanban 面板、`ReexplorePolicy` 都是"能力已具备但默认关闭/低调"的
  状态，不影响任何现有执行路径。
- MVP 范围内的简化取舍（详见设计文档 §9）：一个 `task_id` 只对应
  仓库里一个 active 版本，不按输入结构指纹再细分分支版本；
  `ReexplorePolicy` 用的是版本全部历史成功率而非滑动窗口。
- **LLM/Agent 环节真实调用**：`examples/hybrid_exec_demo.py` 已改为使用
  真实 `providers.json` + 真实 `LLMExplorer`/`LLMRepairer`/
  `FallbackExecutor`（不再用规则版替身），本仓库沙箱环境验证过它确实会
  发起真实的 provider API 请求（网络层/鉴权层报错也会如实透出，不吞掉不
  伪装）。但探索/修复产出的脚本质量、提示词效果如何随不同 provider/model
  表现会有差异，正式投入使用前建议用目标 provider/model 实际跑一遍
  `examples/hybrid_exec_demo.py`（配置好 `providers.json` 后）观察产出。
  `AgentExplorer`/`AgentRepairer`/`FallbackExecutor.agent_direct` 这几个
  多轮 Agent 路径本演示未覆盖（成本更高，默认场景把 `allow_tiers` 限制在
  `(SCRIPT, LLM)`），如需验证可自行调整 `allow_tiers` 加入 `AGENT`。

## 十一、端到端可运行演示（`examples/hybrid_exec_demo.py`）

为了验证整条链路（编排逻辑、脚本仓库版本管理、真实子进程执行、run 记录
落盘、看板聚合，以及 **LLM 探索/修复/兜底本身**）在真实环境下确实可用，
仓库提供了一份可直接运行的演示脚本。**演示全程使用真实
`providers.json` 解析出的真实 LLM**（`LLMExplorer`/`LLMRepairer`/
`FallbackExecutor.llm_direct`），不使用任何规则版/模拟替身——如果没有
配置好可用的 `providers.json`（或对应环境变量），脚本会在开头明确检测
出来、打印配置指引后直接退出，不会用假数据硬撑着"跑通"。

```bash
cd mini_agent-master
pip install -e . --break-system-packages   # 如果尚未安装
cp providers.json.example providers.json   # 若项目根目录还没有
# 编辑 providers.json，填入至少一个 provider 的真实 api_key
python examples/hybrid_exec_demo.py
```

演示会把项目根目录真实的 `providers.json` 复制进独立的演示工作区
`examples/_hybrid_exec_demo_workspace/`（内容是同一份真实配置，只是隔离
存放，避免演示产生的 `.agent/hybrid_exec/` 数据污染真实项目目录），然后
用 `default_executor(DEMO_ROOT)`——不传 `llm=`，走§一.1 描述的"独立执行
自动加载 `providers.json`"默认路径——组装出真实的 `HybridExecutor`。
`ScriptRunner`（真实拉起 `python -m mini_agent.workflow.py_step_runner`
子进程）、`RunRecorder`、`kanban_summary` 同样全部是真实代码路径，没有
被 mock。

演示覆盖以下场景：

| 场景 | 验证点 | 说明 |
|---|---|---|
| 0. 环境检测 | `load_config()` 解析 `provider`/`api_key` 是否齐全 | 不可用则打印配置指引后直接退出，其余场景全部跳过，不用假数据顶替 |
| 1. 首次调用，探索新任务 | 真实 `LLMExplorer` 产出脚本 → 真实子进程 dry-run → 通过则转正为新版本 → 真实执行 | LLM 输出本身有不确定性，脚本按"是否通过 `output_validator`"给出对应提示，不强行断言必然成功 |
| 2. 复用已有脚本 | 场景一成功后，同 `task_id` 再次调用应直接命中，不再触发探索、不再消耗 LLM 调用 | 仅在场景一产出可用脚本时执行 |
| 3. 报错后自愈修复 | 人为写入带已知 bug 的脚本 → 执行报错 → 真实 `LLMRepairer` 修复 → dry-run 通过 → 存为新版本 → 真实执行 | 验证"脚本坏了先修脚本"这条路径 |
| 4. 强制走 Fallback | `allow_tiers=(LLM,)`（不含 SCRIPT/AGENT）→ 真实 `FallbackExecutor.llm_direct` 直接给答案，不产出脚本 | 验证兜底通道 |
| 5. 可观测性 | `.agent/hybrid_exec/runs/<task_id>/summary.json` 落盘统计、`build_kanban_summary()` 聚合出的结构与 `GET /v1/hybrid_exec/summary` 一致 | 展示脚本仓库真实磁盘布局（脚本内容来自真实 LLM 产出/修复，非编造） |
| 6. 嵌入 workflow 场景模拟 | 传入一个不发网络请求的假 `llm` 对象（`_FakeCountingLLM`），验证 `default_executor(project_root, llm=...)` 确实原样复用了传入对象、未重新 `load_config()` | **不依赖 `providers.json`/网络**，无论场景一~五是否因未配置真实 provider 而被跳过，场景六都会运行；对应 python_step 脚本里 `default_executor(ctx.project_root, llm=ctx.llm)` 的用法 |

场景六验证的是"嵌入 workflow 传入 llm"这条路径本身能正确复用传入对象；
关于"独立执行不传 `llm` 时，三者内部如何共享同一条 `LLMClientPool`"的
说明见 §一.1 的实现细节说明框。

运行结束会打印脚本仓库的真实磁盘布局（`.agent/hybrid_exec/scripts/<task_id>/v*.py` + `meta.json`）与完整的 kanban 汇总 JSON，可直接对照检查。演示工作区落在 `examples/_hybrid_exec_demo_workspace/`（已加入 `.gitignore`，每次运行会先清空重建，可重复运行）。

> 网络环境限制：若运行环境有出网白名单，请确认 `providers.json` 里配置
> 的 provider 对应的 API 域名（如 Anthropic 是 `api.anthropic.com`）在
> 白名单内，否则真实请求会在网络层被拦截，报错信息会体现在 LLM 调用的
> 重试日志里。

## 十二、已修复：独立执行路径下 LLM 未真正共享 `LLMClientPool`（P0）

**现象**：运行 `examples/hybrid_exec_demo.py`（独立执行、不传 `llm=`）时，
探索/修复/兜底报错信息里的 provider/model 与 `providers.json` 里配置的
内容对不上重试节奏，且每一次调用看起来都像是"从零开始"的一次性请求，
没有体现出多 key 轮转 / 故障转移的效果。

**根因**：`executor.py::default_executor()` 此前把 `llm=None` 原样分别
传给 `LLMExplorer`/`LLMRepairer`/`FallbackExecutor` 三个实例；这三者内部
在 `llm=None` 时，会在**每一次** `.ask()` 调用时才各自惰性调用一次
`build_llm_helper(app_cfg)`——即每次探索/修复/兜底请求都独立新建一整条
`LLMClientPool`（重新 `load_config()`、重新解析 `providers.json`、多 key
轮转位置清零、之前记录的 key cooldown/故障 provider 清零）。这与主 Agent
的行为不一致：`agent/core.py` 只在 Agent 启动时 `LLMClientPool.from_config
(cfg)` **一次**，之后整个会话生命周期里持续复用同一个 pool，重试/多 key
轮转/多 provider fallback 的状态是连续累积的。

**修复**：`default_executor()` 现在在**构造期间**（而不是每次调用时）就
调用一次 `build_llm_helper(app_cfg)`，得到的 `LLMHelper` 被
`llm_explorer`/`llm_repairer`/`fallback` 三者共用，行为与
`workflow_integration.py::HybridStepExecutor.execute`（一直以来就是这么
做的，见 §五）以及主 Agent 保持一致。`build_llm_helper()` 构造失败（比如
确实没有配置 `providers.json`）时退回 `llm=None`，不让 `default_executor()`
本身崩溃，交给三者在真正发起调用时各自报出更明确的错误。

**验证**：新增单元测试 `tests/test_hybrid_exec_llm_pool_sharing.py`（3
个用例：不传 `llm` 时只构造一次共享对象、传入 `llm` 时完全不触碰
`build_llm_helper`、构造失败时优雅降级为 `None`）；`examples/
hybrid_exec_demo.py` 新增场景六（见 §十一），用不发网络请求的假 `llm`
对象验证复用路径本身工作正常。

> 这个改动**不解决**"`providers.json` 里配置了一个 provider 不支持的
> model 名称"这类用户侧配置错误（比如把 model 填成了 provider 实际不
> 存在的名字，上游会直接返回 404 "model does not exist"，这本身是需要
> 按 provider 文档核对 model 名称的配置问题，不是 `hybrid_exec` 的重试/
> 轮转逻辑能绕过的）——但如果 `providers.json` 的 `llm_fallback_chain`
> 里配置了多条 provider/model，或单个 provider 配置了多个 `api_keys`，
> 修复后的 `hybrid_exec` 现在能像主 Agent 一样，在同一次 `run()` 内的
> 多次探索/修复/兜底调用之间正确累积、共享故障转移与轮转状态。
