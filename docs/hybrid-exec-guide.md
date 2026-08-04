# 脚本/LLM/Agent 混合执行系统（hybrid_exec）指南

> 设计方案：[next_doc/hybrid_exec_design_plan.md](../next_doc/hybrid_exec_design_plan.md)（P1-P4 均已完成）

`hybrid_exec` 是一个**独立于 workflow 之外**的执行系统，把"探索优先
agent/llm、执行优先脚本、脚本故障时优先修复脚本、修复不了才降级"这套
决策逻辑封装成可复用的 Python 对象：

- 可以脱离 workflow 被任何代码直接 `import` 调用（daemon 自主决策、CLI
  工具、其它 workflow 的 `python_step` 内部等）；
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
- **LLM/Agent 环节真实调用尚未验证**：本沙箱环境没有配置 LLM API
  Key，`LLMExplorer`/`AgentExplorer` 产出的脚本质量、提示词效果如何，
  仍缺真实数据支撑。正式投入使用前建议先挑一个真实小任务跑一次端到
  端（用 `default_executor(project_root, mini_agent_config=cfg)`，
  `cfg` 是配置好真实 provider/api_key 的 `mini_agent.config.Config`）。

## 十一、端到端可运行演示（`examples/hybrid_exec_demo.py`）

为了验证"除 LLM/Agent 调用本身之外"的整条链路（编排逻辑、脚本仓库
版本管理、真实子进程执行、run 记录落盘、看板聚合）在真实文件系统/真实
子进程环境下确实可用，仓库提供了一份可直接运行的演示脚本：

```bash
cd mini_agent-master
pip install -e . --break-system-packages   # 如果尚未安装
python examples/hybrid_exec_demo.py
```

演示不依赖任何 LLM API Key：用三个"规则版"替身
（`RuleBasedExplorer`/`RuleBasedRepairer`/`RuleBasedFallback`）实现与
`Explorer`/`Repairer`/`FallbackExecutor` 完全相同的接口，代替真实 LLM
调用（内部不发网络请求，用固定规则直接产出脚本/答案）；`HybridExecutor`
本身、`ScriptRepository`、`ScriptRunner`（真实拉起
`python -m mini_agent.workflow.py_step_runner` 子进程）、`RunRecorder`、
`kanban_summary` 全部是真实代码路径，没有被 mock。生产环境把这三个
替身换成 `LLMExplorer(app_cfg)` 等真实类即可，`HybridExecutor` 侧不需要
改动任何代码——这正是演示要验证的"可插拔"设计。

演示覆盖 5 个场景，均已实际跑通并通过断言校验：

| 场景 | 验证点 | 结果 |
|---|---|---|
| 1. 首次调用，探索新任务 | `RuleBasedExplorer` 产出脚本 → 真实子进程 dry-run 通过 → 转正为 v1 → 真实执行，`output_validator` 通过 | `ok=True tier=script version=1`，`sum=108` 计算正确 |
| 2. 复用已有脚本 | 同 `task_id` 再次调用直接命中 v1，不再探索 | `ok=True tier=script version=1`，attempts 中无 `explore_*` |
| 3. 报错后自愈修复 | 人为写入带 bug 的脚本 → 执行报错 → `RuleBasedRepairer` 修复 → dry-run 通过 → 存为 v2 → 真实执行 | `ok=True tier=script version=2`，`reversed` 字段正确 |
| 4. 修复不了 → 自动退役 → 降级 | 连续 3 次调用均失败且修不好 → `ScriptRepository` 自动 retire → 探索也失败（替身无预置脚本）→ 降级到 `RuleBasedFallback` | 第 2 次调用后仓库状态变为"无 active 版本，已全部退役"；最终 `tier=llm`（Fallback），如实给出兜底结果 |
| 5. 可观测性 | `.agent/hybrid_exec/runs/<task_id>/summary.json` 落盘统计、`build_kanban_summary()` 聚合出的结构与 `GET /v1/hybrid_exec/summary` 一致 | 三个 task 的 `summary.json` 均正确统计 `total_runs`/`tier_counts`/`last_run_*`；`.agent/hybrid_exec/scripts/` 下真实落盘 `meta.json` + 各版本 `.py` 文件 |

运行结束会打印脚本仓库的真实磁盘布局（`.agent/hybrid_exec/scripts/<task_id>/v*.py` + `meta.json`）与完整的 kanban 汇总 JSON，可直接对照检查。演示工作区落在 `examples/_hybrid_exec_demo_workspace/`（已加入 `.gitignore`，每次运行会先清空重建，可重复运行）。
