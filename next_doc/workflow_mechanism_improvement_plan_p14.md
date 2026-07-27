# Workflow 机制改进计划 P14

> 承接 P13（`workflow_mechanism_improvement_plan_p13.md`）末尾暂缓的三项：
> `merge`/`aggregate` 汇聚类型、workflow 级熔断、内置/插件校验路径统一。
> `foreach`（P13）已经落地，`merge` 正是"foreach 产出结构化结果后如何
> 汇总"的自然下一步，三项都具备可以立项实施的条件。

## Phase 1 — `merge`/`aggregate` 汇聚 step 类型

**动机**：多分支/多并发结果汇总现在只能靠某个 step 的 prompt 里手写
`{a.output}{b.output}{c.output}` 拼接，或者靠 `python_step` 脚本读多个
`result_file`。语义上这其实是 workflow 图里一个真实的汇聚节点，但不是
一等公民，调试时也看不出"这一步就是在合并"。`foreach`（P13）落地后这个
需求更具体了——`foreach` 产出一份 JSON 数组，往往还需要跟另一个 step 的
结果合并成最终输出。

**实现方式**：与 `foreach`/`wait` 同样的低风险路径，纯 `StepExecutor`
插件，不改动 `runner.py` 拓扑调度。`WorkflowStep` 新增字段：
- `merge_sources: list[str]` —— 要汇聚的上游 step id，顺序即聚合顺序。
- `merge_strategy: str = "concat_text"` —— `concat_text`（拼接 output
  文本，默认，向后兼容"手写拼接"最常见的用法）/ `json_array`（各来源值
  组成 JSON 数组）/ `json_merge`（各来源须为 JSON object，按顺序
  `dict.update`，后者覆盖前者同名 key）。
- `merge_separator: str = "\n\n"` —— `concat_text` 用的分隔符。
- `merge_use_result_file: bool = False` —— `json_array`/`json_merge`
  时是否从各来源的 `result_file` 读取（否则尝试 `json.loads(output)`，
  解析失败按原始文本处理）。

**校验**：`merge_sources` 非空且无重复；`merge_strategy` 合法；
`merge_sources` 里每个 id 复用 condition/prompt 占位符已有的"引用是否
存在/是否在 depends_on 范围内"检查逻辑（`_transitive_deps`），不重新
写一遍。

> **已实施**（本次改动）：
> - `schema.py`：`STEP_TYPES` 新增 `"merge"`；`WorkflowStep` 新增
>   `merge_sources`/`merge_strategy`/`merge_separator`/
>   `merge_use_result_file` 四个字段；空 prompt 豁免列表加入 `"merge"`；
>   `validate()` 新增 `merge_sources` 非空/无重复、`merge_strategy` 合法
>   性校验，以及复用 `_transitive_deps` 的 `merge_sources` 引用存在性/
>   depends_on 范围校验（单独一段，紧跟在 `check_placeholders` 块后面，
>   不依赖该开关，因为它不是"占位符文本扫描"而是"字面量 id 列表字段"）。
> - `executors.py` 新增 `MergeStepExecutor`：`concat_text` 直接拼接
>   `sr.output`；`json_array`/`json_merge` 按 `merge_use_result_file`
>   决定读 `result_file` 还是 `json.loads(sr.output)`（失败时
>   `json_array` 退化为原始文本元素，`json_merge` 遇到非 dict 直接
>   报错）。注册进 `_EXECUTORS` 分发表。
> - 新增测试（`tests/test_workflow_p14.py`）：`TestMergeExecution`
>   （5 条，覆盖三种策略、`result_file` 来源、`json_merge` 非 dict 报错）、
>   `TestMergeValidation`（6 条，覆盖必填/重复/非法策略/未知来源/缺少
>   depends_on/合法定义通过）。
> - 测试结果：新增 11 条用例全部通过。

## Phase 2 — workflow 级熔断

**动机**：`escalate_after_n_same_failures` 现在只统计**同一个 step**
内部连续同类型失败，没有跨 step 的"整个 workflow 是不是在系统性地
失败"的信号（比如某个外部 API 挂了，导致 5 个不同 step 都在各自重试）。

**实现方式**：`watchdog.py` 新增跨 step 失败追踪——`error_type ->
曾经因该 error_type 失败过的不同 step_id 集合`（本次运行全程累计，
不做滑动窗口）。`WorkflowConfig` 新增
`circuit_breaker_distinct_step_threshold: Optional[int] = None`（默认
`None` = 不启用，行为与改造前完全一致）。达到阈值时：标记
`circuit_breaker_tripped=True`、记录原因、调用现有的
`control.request_cancel()`（与 `max_total_duration`/`max_total_tokens`
超限时用的是同一套信号，不新造一套熔断响应机制），运行中/待执行的
step 会在下一次批次边界照既有逻辑被取消，不需要改动 `run()` 主循环。
上报入口是 `runner._execute_step_with_error_retry`——在
`report_attempt_failure`（同 step 连续失败）之外，每次 `FAILED` 都
额外调一次 `report_workflow_level_failure(step.id, error_type)`（覆盖
`retry_on_error=0` 的单次失败场景，不依赖是否进入重试循环）。

> **已实施**（本次改动）：
> - `config/models.py::WorkflowConfig` 新增
>   `circuit_breaker_distinct_step_threshold: Optional[int] = None`；
>   `config/loader.py` 同步补上从 `workflow.circuit_breaker_distinct_
>   step_threshold` 读取的映射（未设置时保持 `None`，行为不变）。
> - `watchdog.py`：`WorkflowWatchdog.__init__` 新增
>   `circuit_breaker_distinct_step_threshold` 参数；新增
>   `_error_type_step_ids: dict[str, set[str]]`（复用 `_failure_lock`）、
>   `circuit_breaker_tripped`/`circuit_breaker_reason` 只读属性、
>   `report_workflow_level_failure(step_id, error_type) -> bool`——记录
>   `step_id` 到该 `error_type` 的集合，集合大小达到阈值且尚未触发过时，
>   置位 `tripped`、写 `circuit_breaker_tripped` 事件到
>   `watchdog.jsonl`、调用 `control.request_cancel()`（复用现有资源护栏
>   同款响应方式，不新造机制）。
> - `runner.py`：构造 `WorkflowWatchdog` 时透传新配置；
>   `_execute_step_with_error_retry` 里在初次执行失败后、以及每次重试
>   执行失败后，各加一处
>   `if watchdog and sr.status==FAILED and sr.error_type:
>   watchdog.report_workflow_level_failure(...)`，覆盖
>   `retry_on_error=0` 的单次失败场景。
> - 新增测试（`tests/test_workflow_p14.py`）：`TestCircuitBreakerUnit`
>   （4 条，覆盖达阈值触发、同一 step 重复失败不重复计数、不同
>   error_type 独立计数、`threshold=None` 时禁用）、
>   `TestCircuitBreakerIntegration`（1 条，端到端跑一个 3-step
>   workflow，`s1`/`s2` 各失败一次同一 `error_type`、阈值=2 触发熔断后
>   验证依赖它们的 `s3` 没有被判定为 `DONE`）。
> - 测试结果：新增 5 条用例全部通过；额外用
>   `load_config()` 冒烟验证了新配置字段在真实配置加载路径下默认值为
>   `None`（未在 JSON 里配置时行为不变）。

## Phase 3 — 内置类型 `validate()` 校验路径统一（scoped-down）

**背景**：走查时提出的原始设想是"内置类型也统一改成每个 Executor 自带
`validate_step()`，`WorkflowDef.validate()` 只负责调度"。实际动手后
发现 `sub_workflow` 的自引用递归检查（`step.workflow_name ==
self.name`）需要"当前 WorkflowDef 自己的 name"这个上下文，而
`StepExecutor.validate_step(step)` 的签名只有 `step` 一个参数——要做到
完全对称，得改这个公开扩展点的签名（影响所有已注册的插件类型），
这个改动收益（纯重构、不新增能力）配不上它的影响面。

**本轮实际做的（scoped-down 版本）**：只把"缺了某个标量字段就报错"这
一类最简单、格式高度重复的校验（`sub_workflow.workflow_name`/
`tool_call.tool_name`/`script.script`/`skill_agent.skill_name`/
`python_step.script_path`）收进一张 `_SIMPLE_REQUIRED_FIELD` 表，用一个
循环统一处理，消除格式不完全一致的重复代码；`sub_workflow` 的自引用
递归检查、`foreach`/`wait`/`merge` 涉及多字段联动的校验，仍然保留原来
"写在 `WorkflowDef.validate()` 里"的方式——这些本来就不是"插件类型
`validate_step()` vs 内置类型硬编码"这两条路径不对称造成的问题，是
校验逻辑本身复杂度决定的，不适合、也没必要塞进一个只接受 `step` 的
单参数钩子。"完全对称"这个目标本轮不做，记录在这里供以后需要新增内置
类型、且这类内置类型也有跨字段/跨 step 上下文校验需求时，再重新评估是
否值得扩展 `validate_step()` 的签名。

> **已实施**（本次改动）：
> - `schema.py`：把 `sub_workflow.workflow_name`/`tool_call.tool_name`/
>   `script.script`/`skill_agent.skill_name`/`python_step.script_path`
>   五个"缺了就报错"的简单必填字段检查，收进一张
>   `_SIMPLE_REQUIRED_FIELD = {etype: (field_name, msg)}` 表，用一个
>   `if etype in _SIMPLE_REQUIRED_FIELD: ...` 分支统一处理，替换原来
>   五个格式不完全一致的 `if etype == "x" and not step.y: errors.append(...)`
>   独立语句；`sub_workflow` 的自引用递归检查（需要 `self.name`）保留
>   原样，紧跟在表驱动检查之后单独判断，报错文案与改造前逐字一致。
> - 未改动 `StepExecutor.validate_step(step)` 的公开签名，插件类型的
>   校验路径不受影响；`foreach`/`wait`/`merge` 的多字段联动校验同样保留
>   原实现，不纳入这张表（如上所述，这类校验的复杂度决定了它们本来就
>   不适合塞进"缺了单个字段就报错"这个统一形状）。
> - 新增测试（`tests/test_workflow_p14.py`）：
>   `TestSimpleRequiredFieldValidationUnchanged`（6 条），逐一验证
>   `sub_workflow`/`tool_call`/`script`/`skill_agent`/`python_step` 五种
>   类型缺少各自必填字段时的报错文案关键信息（类型名 + "未指定 xxx"）
>   与改造前一致，以及 `sub_workflow` 自引用递归检查依然生效。
> - 测试结果：新增 6 条用例全部通过。

## 实施与交付方式

Phase 1 → 2 → 3 顺序实施（`merge` 是新能力、价值最高且承接 P13 的
`foreach`；熔断次之；validate 重构收益最低、放最后）。每个 Phase 完成
后更新本文档对应小节 → 打包 → 进入下一个 Phase；全部完成后补总结。

---

## 总结（P14 三个 Phase 全部完成）

| Phase | 改动 | 状态 |
|---|---|---|
| 1 | `merge`/`aggregate` 汇聚 step 类型 | 已完成 |
| 2 | workflow 级熔断（跨 step 同 `error_type` 失败达阈值 → 主动 cancel） | 已完成 |
| 3 | 内置类型 `validate()` 必填字段校验表 refactor（scoped-down） | 已完成 |

三项改动共同修改了 `schema.py`/`executors.py`/`watchdog.py`/
`runner.py`/`config/models.py`/`config/loader.py` 六个文件，新增
`tests/test_workflow_p14.py`（22 条用例）。全量 workflow 相关测试
（P10-P14 + 既有回归文件，合计 12 个测试文件）连续 3 轮重跑稳定在
183-184 条通过——唯一的失败是此前 P13 阶段就已确认、与本轮改动无关的
`test_workflow_p11.py` 里一条依赖真实线程调度时序的既有用例
（`TestDependencyMismatchDetection::test_undeclared_prompt_reference_
recorded_when_check_disabled`），独立重跑同样会偶发失败，不是本轮
引入的回归。

至此，P12（走查里三个"改动局限在单个 step 内部"的小修改）→ P13
（`foreach`/`wait` 两个新 step 类型）→ P14（`merge`/熔断/校验重构）
三轮迭代，把最初走查提出的全部候选项（`foreach`/`map`、`merge`/
`aggregate`、`wait`、`tool_call` 占位符、`result_file` 字段访问、
condition 异常分类、workflow 级熔断、内置/插件校验路径统一）逐项落地
或做出了有记录、有理由的取舍（校验路径统一选择了 scoped-down 版本而非
完全对称重构）。当前 workflow 系统内置 step 类型从最初的 7 种扩展到
10 种（`agent`/`role_agent`/`sub_workflow`/`tool_call`/`human_input`/
`script`/`skill_agent`/`python_step`/`foreach`/`wait`/`merge`，共 11
种），建议后续在真实场景里跑一段时间，观察 `foreach`/`merge`/熔断的
实际使用情况后，再决定是否需要新一轮迭代。
