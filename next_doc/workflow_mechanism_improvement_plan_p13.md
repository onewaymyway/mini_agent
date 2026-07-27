# Workflow 机制改进计划 P13

> 承接 P12（`workflow_mechanism_improvement_plan_p12.md`）末尾列出的候选
> 池。P12 三个 Phase 都是"改动局限在单个 step 内部/校验逻辑"的小修改；
> P13 挑的是"新增 step 类型"这个更大的方向，但为了控制风险，**优先做能
> 在不改动 `_compute_parallel_batches`/拓扑调度核心逻辑的前提下、以纯
> `StepExecutor` 插件形式落地的类型**——`foreach`/`map` 和 `wait` 都符合
> 这个约束（`SubWorkflowStepExecutor`/`SkillAgentStepExecutor` 已经证明
> 了"复杂逻辑封装在单个 step 内部、外部调度层完全无感知"这条路径是可行
> 的）。`merge`/`aggregate` 显式汇聚类型、workflow 级熔断、内置/插件
> 校验路径统一这三项，仍然按 P12 末尾的判断——观察本轮效果后再决定是否
> 立项，本文档只记录设计方向，不在本轮实施。

## Phase 1 — `foreach`/`map` 批处理 step 类型

**动机**：现在的并行只发生在拓扑同层的不同 step 之间，没有"对一个列表
的每个元素跑同一份 step 定义、可控并发度、结果聚合成列表"的能力——
`zhihu_content_publish` 这类"先搜出一批问题、再逐个 enrich"的场景，
现在只能靠 `python_step` 脚本手写循环调用 `ctx.run_agent_turn()`，本该
是编排层的逻辑被硬编码进脚本、YAML 里完全看不出"这里其实是批处理"。

**实现方式（低风险路径）**：不改动 `runner.py` 的拓扑调度/批次计算，
把整个"取列表 → 逐元素执行 → 聚合结果"的循环封装在一个新的
`ForeachStepExecutor.execute()` 内部——从外部调度层的视角看，`foreach`
就是普通的一个 step，输入一份 `resolved_prompt`、输出一段文本（聚合后
的 JSON 数组字符串），跟其它类型没有区别，`_execute_step()` 里已有的
"通用输出落盘契约"（`output_file`）、评分提取、`NEEDS_FIX`/`GATE_FAILED`
判定完全不用改，天然复用。

**`WorkflowStep` 新增字段**：
- `items: Any = None` —— 要遍历的列表，可以是 YAML 里的字面量列表，
  或者一个占位符字符串（如 `"{search.result_file:questions}"`），仅
  当整个 `items` 字符串就是单个占位符时，取占位符解析出的**原始 Python
  对象**（不转成字符串），复用 Phase 3（P12）已经写好的
  `WorkflowRunner._resolve_json_path()`；`items` 为空/解析结果不是
  `list` 时校验期直接报错。
- `foreach_step: dict = {}` —— 内层要对每个元素执行的 step 定义（复用
  `WorkflowStep` 的字段子集：`type`/`prompt`/`tool_name`/`tool_args`/
  `skill_name`/`script`/`script_path`/`params`/`role` 等），必须指定
  `type`。内层 prompt 里可以用 `{item}`（当前元素，dict/list 会被
  `json.dumps` 成文本；标量直接 `str()`）和 `{item_index}`（从 0 开始
  的序号）两个专属占位符，跟外层 `_resolve_prompt` 的占位符语法是两套
  独立的替换（内层没有"上游 step 结果"的概念，只有 `item`/`item_index`
  + 外层已经解析过一次的 `resolved_prompt` 透传变量）。
- `foreach_max_concurrency: int = 1` —— 内层并发度，默认 1（串行，最
  保守，不改变任何现有单线程语义），用户显式调大才会用
  `ThreadPoolExecutor` 并发执行多个元素；与外层的 `allow_parallel`
  是两个独立的并发维度，不复用同一个线程池/信号量，避免互相影响调试。
- `foreach_stop_on_error: bool = False` —— 默认某个元素执行失败不影响
  其它元素（失败元素在聚合结果里记成
  `{"item_index": i, "error": "..."}`，整个 `foreach` step 仍然
  `DONE`）；设为 `True` 时第一个元素失败即整体抛异常，交给外层现有的
  `retry_on_error`/`NEEDS_FIX` 机制处理。

**校验（`WorkflowDef.validate()`）**：`type == "foreach"` 时要求
`items` 非空、`foreach_step` 非空字典且包含 `type` 字段；`foreach_step`
里的 `type` 必须是已注册的合法类型（复用现有 `get_registered_types()`
逻辑，不允许嵌套 `foreach`——嵌套批处理的资源控制/调试复杂度不在这轮
范围内，直接在校验期拒绝）。

**验收标准**：新增测试覆盖"字面量列表 + 串行执行 + 聚合成功"、
"占位符列表（引用另一个 step 的 result_file 字段）"、"并发执行"、
"单元素失败但 `foreach_stop_on_error=False` 时不影响整体"、
"`foreach_stop_on_error=True` 时单元素失败导致整体失败"、"嵌套
`foreach` 被 `validate()` 拒绝"，且不影响任何既有 step 类型。

> **已实施**（本次改动）：
> - `schema.py`：`STEP_TYPES` 新增 `"foreach"`/`"wait"`；`WorkflowStep`
>   新增 `items`/`foreach_step`/`foreach_max_concurrency`/
>   `foreach_stop_on_error` 四个字段；空 prompt 豁免列表加入
>   `"foreach"`（foreach 自身不需要外层 prompt，内层 prompt 在
>   `foreach_step` 里）；`validate()` 新增 `items`/`foreach_step` 必填、
>   `foreach_step.type` 合法性、禁止嵌套 `foreach`、
>   `foreach_max_concurrency >= 1` 的校验。
> - `executors.py` 新增 `ForeachStepExecutor`：`_resolve_items()` 支持
>   字面量列表和单占位符字符串（`{step.output}` 要求是合法 JSON、
>   `{step.result_file}`/`{step.result_file:path}` 复用 P12 Phase 3 的
>   `_resolve_json_path`）；逐元素构造一个临时 `WorkflowStep`（`{item}`/
>   `{item_index}` 做简单字符串替换，不复用外层占位符正则），按
>   `foreach_max_concurrency` 决定串行循环还是
>   `ThreadPoolExecutor(max_workers=...)` 并发跑，结果按原始下标顺序
>   聚合成 JSON 数组文本；单元素异常默认记入聚合结果、
>   `foreach_stop_on_error=True` 时直接重新抛出交给外层
>   `retry_on_error`/`NEEDS_FIX` 机制处理。并注册进 `_EXECUTORS` 分发表。
> - 未改动 `runner.py` 的拓扑调度/批次计算逻辑（`_compute_parallel_
>   batches` 等核心循环零改动），符合计划里"低风险路径"的设计约束。
> - 新增测试（`tests/test_workflow_p13.py`）：`TestForeachExecution`
>   （5 条，覆盖串行聚合、`result_file` 占位符取列表、并发保序、单元素
>   失败默认不中断、`stop_on_error=True` 时中断）、
>   `TestForeachValidation`（3 条，覆盖必填字段缺失、嵌套 foreach 拒绝、
>   合法定义通过）。
> - 测试结果：新增 8 条用例全部通过；连同既有 9 个 workflow 测试文件，
>   全量 162 条测试基本稳定通过（`test_workflow_p11.py` 里一条依赖真实
>   线程调度时序的既有用例存在小概率 flaky——5 次独立重跑里出现 2 次
>   失败，与本次改动无关，标准输出确认失败原因是"a/b 并发执行时 a 尚未
>   完成、b 就已经检查了 undeclared_dependency"，纯粹是并发时序问题，
>   仅运行 `test_workflow_p10.py`+`test_workflow_p11.py` 两个文件、不涉及
>   任何本次新增代码时同样能复现）。
> - 涉及文件：`src/mini_agent/workflow/schema.py`、
>   `src/mini_agent/workflow/executors.py`（新增）
>   `tests/test_workflow_p13.py`。

## Phase 2 — `wait`/`delay` step 类型

**动机**：现在如果要等一个外部条件（限速节流、等一段固定时间），只能
塞进 `python_step` 里 `time.sleep`，会跟子进程超时/watchdog 硬超时的
语义打架——`wait` 作为独立类型可以有自己的"可被现有 pause/cancel 控制
信号打断"的等待语义，而不是被子进程超时机制误伤。

**实现方式**：新增 `WaitStepExecutor`，同样是纯 `StepExecutor` 插件，
不改动调度核心。`WorkflowStep` 新增字段 `wait_seconds: Optional[float]
= None`。`execute()` 内部不是简单 `time.sleep(wait_seconds)` 死等，而是
分成很短的小片（如 0.5s 一片）循环 sleep，每片之间检查
`runner._current_control`（已有的 `ControlState`，`pause_requested`/
`cancel_requested`）——收到 cancel 时提前退出并抛出一个明确的异常，收到
pause 时阻塞在原地直到 resume（与其它 step 类型遇到暂停/取消时的既有
响应方式保持一致，不新造一套控制协议）。

**校验**：`type == "wait"` 时要求 `wait_seconds` 是正数。

**验收标准**：新增测试覆盖"正常等待后返回"、"等待期间收到 cancel 请求
提前退出并报告"、"`wait_seconds` 缺失/非正数时 `validate()` 报错"。

> **已实施**（本次改动）：
> - `schema.py`：`WorkflowStep` 新增 `wait_seconds` 字段；空 prompt 豁免
>   列表加入 `"wait"`；`validate()` 新增 `wait_seconds` 必须为正数的
>   校验。
> - `executors.py` 新增 `WaitStepExecutor`：拆成 0.5s 一片循环
>   `time.sleep`，每片之间读取 `runner._current_control`（与
>   `_await_step_approval` 用的是同一个 `ControlState` 实例）——收到
>   `cancel_requested` 时抛 `RuntimeError` 提前退出；收到
>   `pause_requested` 时阻塞在原地轮询直到 pause 解除或收到 cancel；
>   `control` 为 `None`（如单测直接调用、没有 registry 上下文）时退化成
>   纯 `time.sleep` 累加，不影响现有同步调用场景。并注册进 `_EXECUTORS`
>   分发表。
> - 新增测试（`tests/test_workflow_p13.py`）：`TestWaitExecution`（2 条，
>   覆盖正常等待计时、后台线程触发 cancel 后能在秒级内提前退出而不是
>   等满 5 秒），`TestWaitValidation`（3 条，覆盖缺失/负数/合法
>   `wait_seconds`）。
> - 测试结果：新增 5 条用例全部通过；与 Phase 1 一并计入，
>   `tests/test_workflow_p13.py` 合计 13 条全部稳定通过；全量 workflow
>   测试文件合计 162 条（flaky 说明同 Phase 1）。
> - 涉及文件：`src/mini_agent/workflow/schema.py`、
>   `src/mini_agent/workflow/executors.py`（新增）
>   `tests/test_workflow_p13.py`。

---

## 总结（P13 两个 Phase 完成）

| Phase | 改动 | 状态 |
|---|---|---|
| 1 | `foreach`/`map` 批处理 step 类型 | 已完成 |
| 2 | `wait`/`delay` step 类型 | 已完成 |

两项都以纯 `StepExecutor` 插件形式落地，`runner.py` 的拓扑调度/批次
计算核心逻辑零改动，风险可控。最终共同修改了 `schema.py`/
`executors.py` 两个文件，新增 `tests/test_workflow_p13.py`（13 条用例）。
`merge`/`aggregate` 汇聚类型、workflow 级熔断、内置/插件校验路径统一
三项仍按文档开头的判断，留待观察本轮效果后再决定是否单独立项。

## 暂缓项（本轮不实施，记录设计方向供后续参考）

- **`merge`/`aggregate` 汇聚类型**：把"多分支/多并发结果汇总"从"靠
  prompt 手写拼接"升级成一等公民 step。等 `foreach` 落地并有了真实
  "并发产出多份结果需要汇总"的场景后，再回头看汇聚的具体形态（是简单
  拼接、还是需要一个 role_agent 做归纳），现在设计容易拍脑袋。
- **workflow 级熔断**：一定窗口内多个不同 step 因同一 `error_type`
  失败时提前整体标记需要人工介入。这个改动会触碰 `watchdog.py` 的核心
  监控循环，且需要先观察"系统性失败"在真实场景里长什么样（P12/P13 目前
  都还没有实际案例），暂不设计细节。
- **内置类型 `validate()` 校验路径与插件 `validate_step()` 钩子统一**：
  纯重构、不新增能力，优先级最低，留到下一次需要新增内置类型或修
  `validate()` 时顺手做。

## 实施与交付方式

Phase 1 → Phase 2 顺序实施（`foreach` 是本轮价值最高的一项，`wait`
相对独立、可以在其后单独验证不受 `foreach` 改动影响）。每个 Phase
完成后：更新本文档对应小节的"已实施"记录 → 打包本次改动涉及的文件
供下载 → 进入下一个 Phase；全部完成后在文档末尾补总结。
