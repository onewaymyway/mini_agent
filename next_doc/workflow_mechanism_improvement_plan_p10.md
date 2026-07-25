# Workflow 机制改进方案 P10：调试闭环细化 + 看护趋势感知

> 状态：**可实施设计文档**。编号延续
> `workflow_mechanism_improvement_plan.md`（P1-P7）→
> `session_to_workflow_design.md`（P8）→
> `workflow_system_next_directions.md` / `workflow_mechanism_improvement_proposal.md`（P9，已实现：
> `mode`、`input_key`、`force_serial`、`patch_workflow_step`、
> `force_rerun_from`、`NEEDS_FIX`、`get_workflow_run_status(verbose=/wait=)`、
> `system_events` 完成通知）。本文档是 P9 之后的**下一轮**，聚焦三个
> P9 尚未覆盖的真空点，按收益/成本排序，**只做前三项，第四项仅作记录，暂不实施**。

## 0. 范围与不做什么

延续 P9 文档反复强调的原则：**不重复造轮子、不代用户做决定、没有真实数据前不臆测**。
本轮三项都满足：
- 复用现有 `StepResult` / `ControlState` / `system_events` / `error_type` 分类基础设施，
  不引入新的存储机制或新的状态机大改。
- 都是"暴露能力"而不是"替用户做判断"——`test_workflow_step` 只是把已有的单 step
  执行能力开放出来给调试用，不自动决定"改得对不对"；watchdog 升级只是把已经存在的
  `error_type` 信号更早地喂给主 Agent，不代替主 Agent 决定怎么修。

不在本轮做的（P9 文档已明确暂缓，本文档不重复展开）：
- §4 主动感知与建议（依赖真实使用样本积累）
- §5 权限与信任模型（依赖 workflow 分享/市场前提）
- step 组片段复用 / 自定义 step 类型脚手架（详见本文档 §4，仅记录，不实施）

---

## 1. `test_workflow_step`：单 step 沙箱测试

### 1.1 现状与缺口

`preview_workflow`（P7）是整个 workflow 级别的静态 dry-run（占位符替换 + condition
求值，不真正调用 Agent/工具）。`patch_workflow_step` + `resume_workflow_run
(force_rerun_from=...)`（P9）能"改定义 → 重跑"，但重跑是**接入真实 DAG 的一次正式
执行**——会落盘进 `workflow_runs/`、会计入该次 run 的统计、如果这一步之后还有下游
step 也会跟着继续跑。对"我刚改了这个 step 的 prompt，想先确认措辞对不对"这种高频
微调场景，成本偏重：必须先有一个可用的 `workflow_session_id`、且这次验证会污染
正式的执行记录。

真空点：**没有"只跑这一个 step，用手工给的假上游数据，不落盘进正式 run 历史"的
能力**。

### 1.2 设计

新增工具函数（`tools.py`，`group="workflow"`）：

```python
@tool(name="test_workflow_step", group="workflow",
      description="沙箱测试：只执行某个已保存 workflow 里的一个 step，用手工提供的"
                  "mock 上游数据代替真实依赖，不落盘进 workflow_runs 历史、不影响任何"
                  "正式 run。用于验证 patch_workflow_step 改动是否符合预期，而不必"
                  "接入完整 DAG 重跑一次。")
def test_workflow_step(
    name: str,
    step_id: str,
    mock_step_results: Optional[str] = None,   # JSON: {step_id: {"output": "...", "score": ..., "passed": ...}}
    mock_inputs: Optional[str] = None,          # JSON: workflow 级 inputs 的模拟值
    timeout_override: Optional[float] = None,   # 沙箱测试专用超时，不写回定义
) -> str:
    ...
```

实现要点：

1. **复用而非重写执行逻辑**：直接实例化目标 step 对应的 `StepExecutor`（走
   `STEP_TYPES` / `register_step_executor()` 注册表现有的查找逻辑），构造一个
   **临时的、内存态的** `WorkflowRunner`/命名空间上下文——`step_results` 命名空间
   用 `mock_step_results` 反序列化出的 `SimpleNamespace(output=..., score=...,
   status=..., passed=...)` 填充（跟 `_eval_condition()` 现有的命名空间构造逻辑
   完全一致，直接复用同一段构造代码，不新写一份），而不是真的先跑完上游 step。
2. **不落盘、不计入统计**：不写入 `workflow_runs/<id>/`，不生成
   `workflow_session_id`，不触发 `watchdog`/`system_events`。执行完直接把
   `StepResult`（output/error/duration/score）作为工具返回值给主 Agent，用完即弃。
   这一点是与 `force_rerun_from` 的本质区别，必须在 docstring 里写清楚，避免主
   Agent 把它当正式重跑使用。
3. **仍然走真实执行**（会真的调用 LLM / 真的跑 `tool_call` / `script`），因为验证
   "prompt 措辞对不对"必须是真实调用才有意义——沙箱化的只是"不接入 DAG、不落盘"，
   不是"不真正执行"。`type=human_input`/`require_approval` 的 step 在沙箱模式下
   直接跳过（这类 step 本身没有"输出对不对"的验证意义），返回提示"该类型不支持
   沙箱测试，请用 force_rerun_from 实际验证"。
4. **安全边界**：`script` 类型 step 的沙箱执行仍然走现有的沙箱化子进程限制（跟
   正式执行同一套权限检查），不因为是"测试"就放宽。

### 1.3 改动文件

| 文件 | 改动 |
|---|---|
| `executors.py` | 抽出 `build_condition_namespace(mock_step_results)` 辅助函数，供 `_eval_condition()` 和新工具共用（去重复，不是新写一份逻辑） |
| `tools.py` | 新增 `test_workflow_step` 工具 |
| `docs/workflow-guide.md` | 补充"改一个 step 后如何先验证再正式重跑"一节，明确 `test_workflow_step`（临时验证）与 `resume_workflow_run(force_rerun_from=...)`（正式重跑、写入历史）的边界 |
| `prompts/reminders/workflow_run_failed.md` | 在既有"patch → force_rerun_from"提示后追加一句"如果不确定改动是否正确，可先用 `test_workflow_step` 验证" |

### 1.4 验收

- 新增单元测试：mock 一个 `agent` 类型 step，验证不落盘（`workflow_runs/` 目录
  执行前后文件数不变）、返回结果字段完整。
- 新增单元测试：验证 `human_input`/`require_approval` step 走沙箱测试时按预期
  提示跳过，不阻塞。
- 全量回归 `tests/test_workflow_*.py`，与基线 diff 一致。

---

## 2. `resume_workflow_run(step_overrides=...)`：一次性覆盖 vs 永久 patch

### 2.1 现状与缺口

`patch_workflow_step` 修改的是**workflow 定义本体**，影响这次和以后所有执行。但
调试过程中经常有"只想在这次续跑时临时放宽一下（比如把某个 step 的 timeout 临时
调大，看看是不是单纯超时问题，而不是逻辑错误）"的需求——用 `patch_workflow_step`
做这件事，等于把一次性的调试尝试永久写进了正式定义，还得记得测完再改回去，容易
遗忘导致定义被意外污染。

### 2.2 设计

给 `resume_workflow_run` 增加可选参数：

```python
def resume_workflow_run(
    workflow_session_id: str,
    background: Optional[bool] = None,
    force_rerun_from: Optional[str] = None,
    step_overrides: Optional[str] = None,   # JSON: {step_id: {"timeout": 120, "prompt": "..."}}
) -> str:
```

语义边界（必须在实现和文档里明确，避免和 `patch_workflow_step` 混淆）：

- `step_overrides` **只影响本次 resume 执行**，不写回 `WorkflowStore` 持久化的
  YAML/目录定义。实现上在 `api_helpers.resume_workflow_run()` 内部，加载
  `WorkflowDef` 之后、真正执行之前，对内存中的 `WorkflowDef` 副本做字段覆盖（用
  `dataclasses.replace()` 或等价方式生成一份新对象），不调用 `WorkflowStore.save()`。
- 覆盖字段限定在"执行参数类"（`timeout`/`retry_on_error`/`allow_parallel`/
  `model`），**不允许覆盖会改变 step 语义的字段**（比如 `prompt`/`condition`/
  `tool_name`）——这类改动本质上是"改逻辑"，应该走 `patch_workflow_step` 留痕，
  不应该以"临时覆盖"的名义绕过定义变更。这条限制在参数校验阶段直接拒绝非法字段名，
  给出清晰报错而不是静默忽略。
- `get_workflow_run_status` 输出中，若某次 run 使用过 `step_overrides`，需要标注
  出来（例如 `⚠️ 本次执行使用了临时覆盖：{timeout: 120}，未写入 workflow 定义`），
  避免主 Agent 后续查看结果时误以为这是定义本身的行为。

### 2.3 改动文件

| 文件 | 改动 |
|---|---|
| `schema.py` | 新增允许覆盖的字段白名单常量 `RUNTIME_OVERRIDABLE_FIELDS` |
| `api_helpers.py` | `resume_workflow_run()` 增加 `step_overrides` 参数与内存态覆盖逻辑 |
| `session.py` | `WorkflowSession` 记录本次 run 是否使用过 override（供 status 展示，不影响定义存储） |
| `tools.py` | `resume_workflow_run` 工具透传 `step_overrides`；`get_workflow_run_status` 展示 override 标注 |

### 2.4 验收

- 单元测试：`step_overrides` 传入合法字段（`timeout`）后，`WorkflowStore` 中
  保存的定义文件内容不变（校验持久化层完全未被触碰）。
- 单元测试：传入非法字段（`prompt`）应直接报错拒绝，不静默忽略。
- 全量回归。

---

## 3. Watchdog：连续同类失败提前升级 `NEEDS_FIX`

### 3.1 现状与缺口

P9 已经实现"结构性错误（`KeyError`/`FileNotFoundError`/工具未注册等）直接跳过
`retry_on_error`，标记 `NEEDS_FIX`"——但这只覆盖"第一次失败就能从异常类型判断出
是结构性问题"的情况。还有一类失败**异常类型本身像瞬时故障**（比如 LLM 调用返回
`TimeoutError`/`APIError`），但如果连续 N 次重试都在**同一个 step、同一种
error_type** 上失败，大概率也不是"运气不好"，而是这一步的 prompt/参数本身有问题
（比如超时阈值设置得系统性偏低、或者 prompt 触发了模型稳定拒答）。目前的机制会
让它按 `retry_on_error` 预算跑满才收场，跑满之后仍然只是模糊的 `FAILED`，没有
利用"连续失败"这个信号本身。

### 3.2 设计

在 `watchdog.py` 里新增一个轻量计数器（不引入新线程、不改变现有心跳/资源护栏
逻辑，是同一个看护线程里的新检查项）：

```python
# 新增：per-step 连续失败追踪（同 error_type 计数），复用已有的
# heartbeat 上报节拍，不新增轮询频率
self._consecutive_failures: dict[str, tuple[str, int]] = {}  # step_id -> (error_type, count)
```

- `runner.py` 的 `_execute_step_with_error_retry` 每次一个 attempt 失败时（包括
  被判定为"瞬时故障"、准备走 `retry_on_error` 重试的情况），把 `(step_id,
  error_type)` 上报给 watchdog（复用已有的 `_report_step_tokens()` 同类回填模式，
  新增一个 `_report_step_attempt_failure(step_id, error_type)`）。
- watchdog 侧：若同一个 `step_id` 连续（中间没有成功）达到阈值次数（默认
  **2 次**，可通过 `WorkflowDef.defaults` 或 `WorkflowStep` 字段
  `escalate_after_n_same_failures` 配置，默认沿用全局默认值保证向后兼容）都是
  同一个 `error_type`，则**提前**把该 step 判定为 `NEEDS_FIX`，跳过剩余的
  `retry_on_error` 预算，不必等预算耗尽——理由：`retry_on_error` 的设计前提是
  "瞬时故障、重试大概率会不一样"，连续同类失败本身就在证伪这个前提。
- 提前终止时的提示信息复用 P9 已有的措辞模式："连续 N 次同类失败
  （error_type=...），已提前判定为需要修改定义，跳过剩余 M 次重试预算"，让主
  Agent 清楚这不是随机性判断，而是有具体触发依据。

### 3.3 为什么放在 watchdog 而不是 runner 内部计数

`_execute_step_with_error_retry` 本身的重试循环是**同步阻塞**在执行线程里的，
watchdog 是独立线程、职责就是"观察执行过程并做出局部干预决定"（P9 文档 §3 已经
定性："看护线程本身不消耗 LLM token，是纯脚本化实现"）——把"连续失败计数"放进
watchdog 而不是重试循环本体，是延续同一个职责边界：**执行逻辑本身只管"这一次
重试要不要做"，"要不要提前终止重试"这类跨越多次尝试的趋势判断，交给看护线程**，
两者通过已有的回调接口（`_report_step_tokens` 同类模式）通信，不引入新的耦合
路径。

### 3.4 改动文件

| 文件 | 改动 |
|---|---|
| `schema.py` | `WorkflowStep` 新增可选字段 `escalate_after_n_same_failures: Optional[int] = None`（None 表示用全局默认值 2） |
| `watchdog.py` | 新增 `_consecutive_failures` 追踪 + `report_attempt_failure()` 方法 + 达阈值时置位可读取的升级标记 |
| `runner.py` | `_execute_step_with_error_retry` 每次失败尝试后调用 `watchdog.report_attempt_failure()`；重试前检查升级标记，命中则短路进入 `NEEDS_FIX` 分支 |

### 3.5 验收

- 单元测试：构造一个连续 2 次抛出同一 `error_type`（非结构性异常，比如手工
  `TimeoutError`）的 mock step，验证第 2 次失败后不再进入第 3 次重试，直接
  `NEEDS_FIX`，且提示信息包含触发依据。
- 单元测试：验证 `error_type` 不同（比如第一次 `TimeoutError`、第二次
  `ValueError`）时不触发提前升级，按原有逻辑走满重试预算。
- 单元测试：`escalate_after_n_same_failures` 字段自定义阈值生效。
- 全量回归。

---

## 4. 暂不实施（仅记录）：step 组片段复用 / 自定义 step 类型脚手架

上一轮讨论中提到的两个扩展性方向——YAML 片段库（`fragments/` + `use_fragment`）
和自定义 step 类型脚手架（`workflow-step-generator` skill）——本文档**不纳入本轮
实施**，原因：

- 片段复用的收益取决于"实际有多少重复片段"，目前 `templates/` 下只有 3 个模板
  文件，样本量不足以验证"哪些组合真的高频重复到值得抽象成片段"，做早了容易设计
  出一套没人用的抽象。
- 自定义 step 类型脚手架的收益取决于"用户实际造过几个自定义 step 类型"，`
  register_step_executor()` 目前是否已经被使用过、遇到的具体门槛是什么，没有真实
  样本佐证，属于"能加但不知道具体缺口在哪"的一类，按 P9 文档 §0 的筛选标准应该
  继续放在思考稿里观察，不直接立项。

按 P9 文档一贯做法：这两项等有真实使用信号（比如确实攒了多个重复片段、或确实有
人尝试写自定义 step 类型遇到具体困难）之后再回来单独展开设计文档。

---

## 5. 实施顺序与依赖关系

```
1. test_workflow_step          — 独立，无依赖，优先实施
2. resume_workflow_run(step_overrides=...) — 独立，无依赖
3. watchdog 连续同类失败升级    — 依赖 runner.py 现有的 error_type 分类逻辑（P9 已实现），
                                   在此基础上增量，无需等待 1/2
```

三项互相独立，可以任意顺序或并行实施，不要求全部做完才发布；每项都应各自跑一遍
`tests/test_workflow_*.py` 全量回归并与基线 diff，确认 0 新增失败后再合入下一项。

## 6. 汇总表

| 方向 | 关键改动点 | 解决的问题 |
|---|---|---|
| 单 step 沙箱测试 | `tools.py`(`test_workflow_step`) + `executors.py`(命名空间构造抽取复用) | patch 之后要不要真跑一次正式 run 才能验证，成本重、污染历史 |
| 一次性执行覆盖 | `api_helpers.py`(`resume_workflow_run(step_overrides=...)`) + 白名单校验 | 临时调试参数被迫写成永久 patch，容易忘记改回去污染定义 |
| 看护趋势感知 | `watchdog.py`(连续失败计数) + `runner.py`(短路重试) | 连续同类失败仍按瞬时故障走满重试预算，浪费时间和 token |
