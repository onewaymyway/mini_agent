# Workflow 机制改进计划 P12

> 编号延续：P1-P7（`workflow_mechanism_improvement_plan.md`）→ P8（
> `session_to_workflow_design.md`）→ P9 候选池（`workflow_system_next_
> directions.md`，1a/1b/2/3 已实施）→ P10/P11（同名 plan 文档）→
> 本文档是 **P12**，来源于一次针对 workflow 体系的独立走查（见对话中
> "分析当前项目的 workflow 相关机制…" 的分析结论），覆盖三个互相独立、
> 可逐项验收的小改动。**不包含** `foreach`/`map` 批处理类型 —— 那个改动
> 会触碰 `_compute_parallel_batches`/并发调度核心逻辑，风险和范围都明显
> 大于本轮其它三项，留作单独的下一轮（P13）设计，不在本轮实施。

## 背景 / 走查结论

对 `src/mini_agent/workflow/` 做了一轮走查（结合 `schema.py`/`runner.py`/
`executors.py` 及 P9-P11 已有文档），过滤掉"已经在做/已经有明确暂缓理由"
的部分后，剩下三个足够小、足够独立、能在本轮直接实施的缺口：

1. **condition 表达式运行期异常被误判为"业务上不满足"**——`_eval_condition`
   捕获所有异常后统一返回 `False`，调用方因此把"表达式本身写错了"
   （比如引用了一个此刻 `output` 类型不对导致 `AttributeError`）和
   "表达式语法/引用都对、只是求值结果确实是 False"混在一起，都表现为
   `StepStatus.SKIPPED`。这与项目里已经给普通 step 失败做的
   `NEEDS_FIX` vs `FAILED` 区分（P4/P10）是同一个思路，只是还没延伸到
   condition 求值上。
2. **`tool_call` 的 `tool_args` 不支持占位符**——只能是字面量或者退化成
   "把整个 prompt 塞给函数的第一个参数"，想把上游 step 输出的某个字段
   作为某个具体参数传入做不到，只能整段转成文本再让工具自己解析，
   绕开了工具函数本来的参数结构。
3. **`{step_id.result_file}` 占位符只能拿到文件绝对路径，拿不到文件里
   某个具体字段**——`result_file` 契约（P11）已经保证下游能稳定读到
   结构化 JSON 文件，但 prompt 里想引用其中某个字段（如问题列表的第一条
   标题）仍然只能整个文件路径传给下游 Agent，靠它自己读文件解析，
   没有复用到"这份 JSON 结构其实是已知的"这个信息。

三项改动都不改变现有 YAML 的默认行为（未使用新语法的 workflow 定义
运行结果不变），只是新增能力，属于纯增量修改，符合项目"config 默认不
破坏现有行为"的一贯约定。

## Phase 1 — condition 求值异常 → `NEEDS_FIX`

**改动范围**：`schema.py`（新增异常类）、`runner.py`
（`_eval_condition` + `_run_one_step` 里紧邻 `_eval_condition` 调用的
分支）。

**设计**：
- 在 `schema.py` 新增 `class ConditionEvalError(RuntimeError)`，携带
  `condition`（原始表达式文本）和 `original_exception`（被捕获的异常
  对象）两个属性。
- `_eval_condition` 内部对求值抛出的任何异常，不再吞掉后返回 `False`，
  而是包装成 `ConditionEvalError` 重新抛出（日志打印逻辑保留在这里，
  跟原来一样先 `log_exception`）。
- `_run_one_step` 里调用 `_eval_condition` 的地方改成
  `try/except ConditionEvalError`：捕获到时，把该 step 标记为
  `StepStatus.NEEDS_FIX`（而不是 `SKIPPED`），`error` 字段写清楚是
  condition 求值失败、`error_type` 记录原始异常类名，供后续
  `resume_workflow_run`/`patch_workflow_step` 的既有处理路径直接复用
  （这条路径 P10 已经打通，`NEEDS_FIX` 的 step 会提示"应先修正定义再
  续跑"，不会被 `retry_on_error` 误判成"重试可能有用"而反复空转）。
  条件求值确实返回 `False`（没有异常）的分支行为不变，仍然是
  `SKIPPED`。
- `tests/test_workflow_step_types.py` 或新增测试文件里补一条用例：
  写一个 condition 引用一个此刻类型不匹配的字段（触发 `AttributeError`/
  `TypeError`），断言最终 `StepStatus` 是 `NEEDS_FIX` 而不是 `SKIPPED`，
  且 `error_type` 不为空；同时保留一条"condition 语法/引用都对、只是
  结果为 False"的用例断言仍然是 `SKIPPED`，防止把两种情况的行为混淆。

**验收标准**：全量测试跑一遍，新增用例通过，且不影响任何既有测试
（`test_workflow_parallel.py` 里 mock `_eval_condition` 返回 `False` 的
用例应保持原样通过，因为那是直接 mock 返回值、不触发异常路径）。

> **已实施**（本次改动）：
> - `schema.py` 新增 `ConditionEvalError(RuntimeError)`，携带
>   `condition`/`original_exception`。
> - `runner.py::_eval_condition` 求值异常分支改为记录日志后
>   `raise ConditionEvalError(condition, e) from e`，不再吞掉返回
>   `False`。
> - `runner.py::_run_one_step` 里 condition 判断分支改为
>   `try/except ConditionEvalError`：捕获到时写入
>   `StepResult(status=NEEDS_FIX, error=str(e), error_type=<原始异常类名>)`；
>   未捕获到异常但结果为 `False` 时行为不变，仍是 `SKIPPED`。
> - 新增 `tests/test_workflow_p12.py`：覆盖
>   `_eval_condition` 直接抛出 `ConditionEvalError`（含"引用不存在的
>   step"和"字段类型不匹配导致 TypeError"两种触发方式）、
>   `_eval_condition` 正常求值为 `False` 时不抛异常、
>   `_run_one_step` 对两种情况分别落到 `NEEDS_FIX`/`SKIPPED`。
> - 测试结果：新增 5 条用例全部通过；连同既有 workflow 相关测试文件
>   （`test_workflow_p10.py`/`test_workflow_p11.py`/
>   `test_workflow_parallel.py`/`test_workflow_step_types.py`/
>   `test_workflow_directory_mode.py`/`test_workflow_step_session_dir.py`/
>   `test_session_to_workflow.py`/`test_zhihu_workflow_steps.py`）合计
>   136 条全部通过，0 新增失败。
> - 涉及文件：`src/mini_agent/workflow/schema.py`、
>   `src/mini_agent/workflow/runner.py`（新增）
>   `tests/test_workflow_p12.py`。

## Phase 2 — `tool_call.tool_args` 支持占位符

**改动范围**：`executors.py`（`ToolCallStepExecutor`）、`runner.py`
（把已有的占位符解析能力暴露成一个可复用的辅助方法）。

**设计**：
- `runner.py` 的 `_resolve_prompt` 已经实现了"把 `{step_id.output}` 等
  占位符替换成真实值"的完整逻辑，但只接受一个字符串模板、返回替换后的
  字符串。新增一个轻量包装 `_resolve_value(self, value, step_results,
  inputs)`：如果 `value` 是字符串，直接复用 `_resolve_prompt`；如果是
  `dict`/`list`，递归对内部的字符串值做同样替换；其它类型原样返回。
  不重新实现占位符语法，只是把现有能力从"只能用于 prompt 整段文本"
  扩展到"能用于任意嵌套结构里的字符串字段"。
- `ToolCallStepExecutor.execute()` 里，在构造 `tool_input` 之后、真正
  调用 `registry.call()` 之前，对 `tool_input` 整体跑一遍
  `runner._resolve_value()`。`tool_args` 里没有占位符的值（原来的用法）
  经过 `_resolve_prompt` 处理后原样返回（没有 `{...}` 可替换），完全
  向后兼容。
- `WorkflowDef.validate()` 里 `check_placeholders` 分支目前只扫描
  `step.prompt`；同步扩展成也扫描 `step.tool_args`（递归到字符串叶子
  节点），复用已有的"引用的 step 是否存在/是否在 depends_on 范围内"
  校验逻辑，保持"占位符错误在 `save_workflow` 阶段就能拦下来"这条既有
  原则一致，不给 `tool_args` 开一个校验洼地。
- `tests/test_workflow_step_types.py` 补一条用例：`tool_call` 的
  `tool_args` 里写 `{"query": "{search.output}"}`，断言实际调用工具时
  收到的是替换后的真实文本；再补一条 `validate()` 用例，`tool_args`
  引用了不存在的 step，断言 `validate()` 能报出错误。

**验收标准**：新增用例通过，且不影响任何未使用 `tool_args` 占位符的
既有 `tool_call` 用例（回归测试全量跑一遍）。

## Phase 3 — `result_file` 占位符支持字段访问

**改动范围**：`runner.py`（`_resolve_prompt` 里 `.result_file` 分支）、
`schema.py`（`validate()` 里占位符字段名校验的正则/分支）。

**设计**：
- 现状：`{step_id.result_file}` 只能替换成落盘文件的绝对路径本身。
  新增语法 `{step_id.result_file:jsonpath}`（用冒号分隔，避免跟已有的
  点号字段分隔符冲突），其中 `jsonpath` 是一个极简子集——只支持
  `a.b.c`/`a.b[0].c` 这种"属性访问 + 数字下标"的链式路径，不引入完整
  JSONPath 语法（跟 `condition` 表达式"沿用现有沙箱 eval、不重新发明
  DSL"的既有取舍原则一致，这里同样优先复用而不是新造一套解析器——直接
  用 `re` 把路径拆成 token，逐层 `dict.get`/`list[index]` 取值，取不到
  时保留原始占位符文本不替换并记录到 `debug_log.unresolved_
  placeholders`，不抛异常中断整个 step，交给下游 Agent/脚本自己发现
  "这个值没填上"，与现有"占位符缺失时保留原样"的容错风格保持一致）。
- 解析时机：在 `_resolve_prompt` 现有的"发现 `.result_file` 字段"分支
  基础上，先判断占位符里有没有 `:`，有则读取对应 `StepResult.result_
  file` 指向的 JSON 文件、按路径取字段值（取到后用 `str()` 转成文本
  插入 prompt）；没有 `:` 则保持原有行为（替换成路径本身）。
- `schema.py::validate()` 的占位符字段名正则需要放宽，允许
  `result_file` 后面带 `:xxx` 后缀，且对 `xxx` 部分只做"是否是合法的
  属性/下标链路径"的语法级检查（不读文件、不知道字段是否真的存在，
  这点在文档里要写清楚——这是"语法检查"不是"语义检查"，跟 condition
  的静态检查同一个定位）。
- `tests/test_workflow_step_types.py` 补一条用例：`skill_agent` step
  声明了 `result_file`，写入的 JSON 是 `{"questions": [{"title": "x"}]}`，
  下游 step 的 prompt 用
  `{search.result_file:questions[0].title}`，断言替换后的文本里出现
  `"x"`；再补一条"路径取不到值时占位符原样保留、不抛异常"的用例。

**验收标准**：新增用例通过，且不影响任何仅使用 `{step_id.result_file}`
（不带 `:` 后缀）的既有用例。

## 实施与交付方式

按 Phase 1 → Phase 2 → Phase 3 顺序实施（风险从低到高、且 Phase 2/3
都复用 Phase 结束时已经在 `runner.py` 里验证过的占位符解析路径，顺序
本身也是复用关系，不是任意排列）。每个 Phase 完成后：

1. 跑一遍相关测试文件确认改动本身及既有回归不受影响；
2. 在本文档对应 Phase 小节末尾补一条"**已实施**"状态记录（实际改了
   哪些文件、测试结果如何，仿照 `workflow_system_next_directions.md`
   顶部"进度更新"的写法）；
3. 打包本次改动涉及的文件供下载；
4. 再进入下一个 Phase。

三个 Phase 全部完成后，在文档末尾补一段总结，并把 P13（`foreach`/
`map` 类型，以及走查里提到的 `merge` 汇聚类型、`wait` 类型、
workflow 级熔断等）列为独立的后续候选，不在本轮展开设计细节。
