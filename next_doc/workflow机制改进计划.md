# Workflow 机制改进计划

> 状态：提案 / 待评审
> 关联模块：`src/mini_agent/workflow/`、`src/mini_agent/storage/paths.py`
> 参考文档：`docs/workflow-guide.md`、`docs/storage-paths-guide.md`

---

## 一、历史与现状

### 1.1 现有能力

`workflow/` 模块由五个文件构成，职责划分清晰：

| 文件 | 职责 |
|---|---|
| `schema.py` | 数据层：`WorkflowDef` / `WorkflowStep` / `StepResult`，支持 `depends_on`、`condition`、`role`、`retry_on_gate_fail`、`allow_parallel` |
| `runner.py` | 执行引擎：拓扑分层 + 同层并发（ThreadPoolExecutor）、`{step.output}` 占位符替换、evaluator 质检门重跑（[具身改进 B3]） |
| `store.py` | 持久化：`.agent/workflows/<name>.yaml` 单文件存储，CRUD |
| `generator.py` | LLM 根据自然语言描述生成工作流定义 |
| `tools.py` | 暴露给主 Agent 的 6 个工具：generate / save / run / list / show / delete |

已经具备的能力包括：DAG 依赖建模、条件分支、并发批次执行、角色 Agent 绑定（`role_agents` 体系）、evaluator 质检门 + 反馈重跑。这些构成了一个可用的"工作流即代码"雏形。

### 1.2 核心缺口

对照项目中已经成熟的 `AgentPaths` 会话分层体系（Global / Workdir / Session / Task，见 `storage/paths.py`），workflow 执行目前是**裸跑**：

1. **不可观测**：执行过程只有 `print_info` 打到控制台，没有结构化事件流，进程一断连信息全部丢失。
2. **不可恢复**：`WorkflowRunResult` 只存在于内存里，跑完即焚；进程崩溃后无法知道跑到哪一步、能否续跑。
3. **不可审计**：每个 step 内部由 `_execute_with_main_agent` / `_execute_with_role_agent` 临时创建一个 `Agent` 实例，既没有绑定持久化 session，也没有约定数据落在哪个目录——工具调用记录、中间产出文件全部无据可查。
4. **不可控**：唯一的运行时干预点是 `retry_on_gate_fail`（只针对 evaluator 门），没有暂停 / 取消 / 人工审批 / 超时强制中断能力。普通异常（网络超时、工具报错）直接判 `FAILED`，没有通用重试。
5. **扩展性受限**：`step.role: Optional[str]` 是"主 Agent / 角色 Agent"的隐式二选一，无法表达"调用子工作流""调用外部脚本""等待人工输入"等更多 step 类型；`condition` 用裸 `eval()` 执行字符串表达式，命名空间收窄了 builtins 但本质仍是运行时字符串求值。
6. **数据分散**：`step_cfg` 每次都是 `load_config()` 现造一份，各 step 的 Agent 数据该落到哪个目录完全没有约定，容易互相污染或事后找不到。

---

## 二、新的理念

围绕这次改进，确立四条设计原则：

### 理念 1：一次执行 = 一个有身份的实体（Session 化）

任何一次 `run_workflow` 调用都不应是一次性的函数调用，而应对应一个有唯一 ID、有状态、可查询、可恢复的 **WorkflowSession**。这与项目里 Agent Session / Task 的设计哲学完全一致，只是把"会话"的概念从单 Agent 提升到多 Agent 编排的粒度。

### 理念 2：目录即边界（数据聚合）

一次 workflow 执行涉及的所有 Agent 数据（历史、日志、临时文件、产出物），都应该聚合在同一个 workflow 数据目录下，而不是散落在全局 `sessions/` 里靠时间戳去猜哪几个 session 属于同一次编排。目录结构本身就是排查、归档、清理的天然边界。

### 理念 3：看护而非放养（Watchdog）

复杂度更高的编排意味着更高的失控风险（卡死、无限重跑、资源耗尽）。执行引擎不应只是"跑步骤"，还应该有一个持续在旁监督的看护线程——检测心跳、强制超时、响应外部的暂停/取消信号、在高风险步骤前拦截等待人工批准。这条思路直接复用项目里 goal mode 的 stuck-detection 经验，而不是重新发明。

### 理念 4：扩展点是"新增"而不是"改核心"（类型化 + Hook 化）

新增一种 step 类型（子工作流、外部脚本、人工输入）或一种干预逻辑，不应该要求改动 `runner.py` 的核心循环。应该通过 **Step 类型分发**（每种类型一个 Executor）和**对称的生命周期 Hook**（`WorkflowStart/StepStart/StepEnd/GateFailed/WorkflowEnd`，复用现有 15 个 hook 事件的注册体系）把定制点开放出去。

---

## 三、改进方案

### 3.1 Workflow 数据目录（对齐 AgentPaths 分层约定）

```
.agent/
└── workflow_sessions/
    └── <workflow_session_id>/
        ├── session.json          # WorkflowSession 元信息：status/进度/control_flags
        ├── workflow_def.yaml     # 执行时使用的工作流定义快照（防止运行中途原文件被改）
        ├── step_results.json     # 增量落盘的 step_results，断点恢复用
        ├── events.jsonl          # 结构化事件流：step_start/step_end/gate_failed/paused/...
        ├── watchdog.jsonl        # 看护线程心跳/告警记录
        └── <agent_session_id>/   # 该 workflow 中每个 step 对应的 Agent 完整数据
            ├── history.json
            ├── meta.json
            ├── traces.jsonl
            ├── temp/
            ├── output/
            └── artifacts/
```

实现方式：给 `AgentPaths` 增加对称的一组路径方法，并让 `sessions_dir` 的根路径变为**可覆盖参数**（而不是硬编码 `.agent/sessions/`），workflow 场景下把根路径重定向到 `workflow_session_dir(wf_id)`。这样现有 `session_dir(sid)` / `session_temp_dir(sid)` / `task_dir(sid, tid)` 等所有下游方法可以直接复用，无需为 workflow 场景重复实现一整套。

```python
def workflow_session_dir(self, wf_session_id: str) -> Path:
    """.agent/workflow_sessions/<wf_session_id>/"""
    return self.workdir_dir / "workflow_sessions" / wf_session_id

def workflow_session_meta(self, wf_session_id: str) -> Path:
    return self.workflow_session_dir(wf_session_id) / "session.json"

def workflow_session_events(self, wf_session_id: str) -> Path:
    return self.workflow_session_dir(wf_session_id) / "events.jsonl"

def workflow_step_agent_dir(self, wf_session_id: str, agent_session_id: str) -> Path:
    return self.workflow_session_dir(wf_session_id) / agent_session_id

def ensure_workflow_session_dir(self, wf_session_id: str) -> Path:
    d = self.workflow_session_dir(wf_session_id)
    d.mkdir(parents=True, exist_ok=True)
    return d
```

`agent_session_id` 建议取 `f"{workflow_session_id}_{step.id}"`，同 workflow 内可读性强、跨 workflow 不冲突。

### 3.2 WorkflowSession：状态与断点恢复

```python
class WorkflowRunStatus(str, Enum):
    RUNNING = "running"
    PAUSED = "paused"
    AWAITING_APPROVAL = "awaiting_approval"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class WorkflowSession:
    workflow_session_id: str
    workflow_name: str
    status: WorkflowRunStatus
    started_at: float
    updated_at: float
    current_batch_index: int
    step_results: dict[str, StepResult]
    control_flags: dict  # {"pause_requested": bool, "cancel_requested": bool}
```

`WorkflowRunner.run()` 开始时创建（或按 `workflow_session_id` 加载已有的）`WorkflowSession`，**每完成一个 step 就增量落盘** `step_results.json`，而不是只在内存里攒到最后返回。带来两个直接收益：

- 进程崩溃后可 `resume_workflow(workflow_session_id)`，跳过已 `DONE` 的步骤，只重跑未完成部分；
- 外部（CLI / 看板 / 另一个对话）可随时读取该文件查看实时进度，不依赖 stdout。

### 3.3 看护机制（Watchdog）

在 `WorkflowRunner.run()` 内部起一个轻量后台线程，职责：

1. **心跳与卡死检测**：每个 step 执行时更新 `last_heartbeat`（不是"结果"，而是"存活信号"，如一次工具调用发生）。看护线程定期检查，超过 `step.timeout` 仍无心跳则判定疑似卡死，触发硬中断（`future.result(timeout=...)`），而不是无限等待——这是对当前 `step.timeout` 形同虚设（只设置了请求超时，管不住多轮工具调用循环）的直接修复。
2. **外部控制信号**：轮询 `control_flags`（写入 `session.json` 或独立控制文件），支持：
   - `pause`：当前 batch 跑完后不再开始下一层，进入 `PAUSED`，可随时 `resume`；
   - `cancel`：尽快中止，正在跑的 step 标记 `CANCELLED`，未开始的标记 `SKIPPED`。
   暂停/取消不需要杀进程，通过调用新工具（如 `pause_workflow_run`）写入信号即可，天然适合被主 Agent 或用户在另一轮对话里触发。
3. **资源/成本护栏**：累计整个 workflow 的 token 消耗与总时长，超过 `WorkflowDef` 可选的 `max_total_tokens` / `max_total_duration` 阈值主动叫停，防止设计有误的循环型工作流（尤其 gate-retry 场景）无限消耗。
4. **人工审批门**：新增 `step.require_approval: bool`。看护机制在该 step 执行前置为 `AWAITING_APPROVAL` 并暂停，等待外部调用 `approve_workflow_step` / `reject_workflow_step`。高风险 step（涉及外部副作用的 `tool_call` 类型）默认要求人工放行。

### 3.4 通用失败重试

`WorkflowStep` 新增 `retry_on_error: int`（区别于现有只针对 evaluator 质检门的 `retry_on_gate_fail`），复用同一套重跑框架，配合指数退避处理网络超时、工具报错等普通异常，避免一次瞬时失败拖垮整条依赖链。

### 3.5 Step 类型化（扩展性）

把 `role: Optional[str]` 的隐式二分显式化为：

```python
step.type: Literal["agent", "role_agent", "sub_workflow", "tool_call", "human_input", "script"]
```

`runner.py` 的 `_execute_step` 从 if/else 堆叠改为按类型分发到独立的 `StepExecutor` 子类。新增一种 step 类型（比如子工作流复用、外部脚本执行）只需新增一个 Executor，无需触碰核心调度循环。其中 `sub_workflow` 类型（`workflow_name` 字段引用另一个已保存的工作流）让常用片段可以被抽象成子流程、被多个业务工作流组合复用，是"更容易定制"的关键一环。

### 3.6 生命周期 Hook 对称化

复用项目里已有的 hooks 注册体系，补充 workflow 对称事件：`WorkflowStart` / `WorkflowStepStart` / `WorkflowStepEnd` / `WorkflowGateFailed` / `WorkflowEnd`。定制方无需改 runner 源码，通过现成 hooks 机制即可插入通知、埋点、拦截逻辑。

### 3.7 新增工具（tools.py 对称补充）

| 工具 | 作用 |
|---|---|
| `list_workflow_runs(name=None)` | 列出历史执行记录（含 running / paused） |
| `get_workflow_run_status(workflow_session_id)` | 查看某次执行的详细进度 |
| `pause_workflow_run` / `resume_workflow_run` | 暂停 / 续跑 |
| `cancel_workflow_run` | 取消执行 |
| `approve_workflow_step` / `reject_workflow_step` | 人工审批门放行 / 拒绝 |

### 3.8 定制性增强（次要，可延后）

- **保存前引用完整性校验**：`save_workflow` 阶段校验 `inputs` 占位符是否齐全、`role` 是否存在于当前已注册的 role_agent profile，把错误挡在保存时而非运行时。
- **模板库**：`WorkflowStore` 增加 `templates/` 子目录，内置常见模式（code_review / research_report / multi_perspective_debate），`generate_workflow` 优先"套模板+填参"，提升生成稳定性。

---

## 四、改进计划（分阶段落地）

遵循"提案先行、diff 化实现、零回归、保守默认开启"的一贯节奏，建议按以下顺序推进，每阶段独立可评审、独立可回滚：

### P1 — 目录结构先行（纯增量，不改变现有默认行为）

- `AgentPaths` 新增 workflow_session 相关路径方法
- `sessions_dir` 根路径支持覆盖参数（默认行为不变）
- 验收标准：新增方法有单测覆盖，现有 session/task 相关测试全绿

### P2 — Runner Session 化（断点恢复能力）

- 引入 `WorkflowSession` 数据结构 + `session.json` / `step_results.json` 落盘
- `_execute_with_main_agent` / `_execute_with_role_agent` 改为使用 `workflow_step_agent_dir` 绑定 Agent session
- 新增 `resume_workflow(workflow_session_id)` 能力
- 验收标准：中途 kill 进程后可从断点续跑，已完成 step 不重跑

### P3 — 看护线程（心跳超时 + 暂停/取消）

- 心跳检测 + 硬超时中断
- `control_flags` 轮询与响应
- 验收标准：模拟卡死 step 能被看护线程强制中断并正确记录状态

### P4 — 人工审批门 + 通用失败重试

- `step.require_approval` + `AWAITING_APPROVAL` 状态
- `retry_on_error` 通用重试框架
- 新增 5 个对称工具（3.7 节）
- 验收标准：高风险 step 默认阻塞等待审批；瞬时失败可自动恢复

### P5 — Step 类型化重构（改动面最大，单独提案）

- `StepExecutor` 分发架构
- `sub_workflow` 类型支持
- 生命周期 Hook 对称化
- 验收标准：现有 workflow YAML 无需修改即可继续运行（向后兼容）

### P6 — 定制性增强（可并行，优先级最低）

- 保存前引用完整性校验
- 内置模板库

---

## 五、风险与兼容性说明

- P1/P2 阶段涉及 `AgentPaths` 改动，需确保默认（非 workflow 场景）的 `session_dir` 行为完全不变，覆盖参数仅在显式传入时生效。
- P2 阶段改变了 step 内部 Agent 的数据落盘位置，需要留意是否有依赖旧的裸 Agent 行为（无持久化 session）的现有测试/逻辑，逐一核对 `tests/test_workflow_parallel.py`。
- P5 的 step 类型化是破坏性程度最高的重构，必须保证旧版 `role: Optional[str]` 字段的 YAML 定义在不修改的情况下继续可执行（内部自动映射为 `type="agent"` 或 `type="role_agent"`）。
- 看护线程（P3）引入的后台线程需要在 workflow 正常结束、异常结束、进程退出三种路径下都能正确清理，避免线程泄漏。
