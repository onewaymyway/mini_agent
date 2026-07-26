# Workflow 输入传递机制 + 调试日志改进方案（P11）

> 状态：**已实现**（§1/§2a/§3/§4/§5/§6.2/§6.3 全部完成并通过回归测试；
> §6.4 按原计划保持"仅记录，不实施"，留待 §1/§4 静态信号积累后再评估）。
> 编号延续 `workflow_mechanism_improvement_plan.md`（P1-P7）→
> `session_to_workflow_design.md`（P8）→
> `workflow_system_next_directions.md` / `workflow_mechanism_improvement_proposal.md`（P9）→
> `workflow_mechanism_improvement_plan_p10.md`（P10）之后。
>
> 实现记录：
> - 配置层新增 4 个 `WorkflowConfig` 开关（`placeholder_depends_on_check_enabled`、
>   `python_step_inputs_filtered_by_depends_on`、`debug_log_enabled`、
>   `debug_log_max_chars`），`config/loader.py` 补上对应读取逻辑。
> - §1：`WorkflowDef.validate()` 的 `_transitive_deps` 提到方法级共享作用域，
>   prompt 占位符校验新增 depends_on 范围检查，`store.py::save()` 接入新开关。
> - §2a：`api_helpers.preview_workflow_def()` 新增 `unresolved_placeholders`
>   返回字段，`tools.py` 的 `preview_workflow` 工具与生成后自动 dry-run
>   预览（`_format_dry_run_preview`）均已展示。
> - §3：新增 `{step_id.output_file}` 占位符（`runner.py::_resolve_prompt`），
>   `_write_step_output_file` 落盘成功后记录绝对路径到
>   `self._step_output_file_paths`。
> - §4：`PythonStepExecutor.execute` 默认按 `step.depends_on` 过滤传给
>   子进程的 `ctx.inputs`，关闭开关可回退旧行为。
> - §5：`api_key` 不再写入 `request.json`，改由 `MINI_AGENT_STEP_API_KEY`
>   环境变量传给子进程，`py_step_runner.py` 的 `_build_llm_helper`/
>   run_agent_turn 均优先读该环境变量。
> - §6.2/§6.3：`StepResult` 新增 `debug_log` 字段；`runner.py` 新增
>   `_scan_prompt_placeholders` 辅助函数，`_run_one_step` 组装
>   `resolved_prompt`/`unresolved_placeholders`/`upstream_step_ids_used`/
>   时间戳/线程号/`batch_index`（带 `debug_log_max_chars` 截断）；
>   `ScriptStepExecutor`/`PythonStepExecutor` 把子进程 stdout/stderr
>   无论成败都挂到 `runner._last_subprocess_debug`，由 `_execute_step`
>   合并进 `StepResult.debug_log`；`get_workflow_run_status(verbose=True)`
>   展示 debug_log 摘要；新增 CLI 子命令 `/workflow debug <run_id> <step_id>`
>   查看完整 debug_log。
> - 新增单元测试 `tests/test_workflow_p11.py`（10 用例，覆盖 §1/§3/§4/§6
>   验收点），并修正 `tests/test_python_step_subprocess_e2e.py` 中一处因
>   §4 行为变更（python_step 输入默认按 depends_on 过滤）而需要补充
>   `depends_on` 声明的既有用例。与既有 `tests/test_workflow_*.py`（含
>   `test_python_step*.py`，138 用例）合计 148 用例全部通过，0 回归。
> - §6.4（debug_log 与 NEEDS_FIX/watchdog 打通）按原计划保持"仅记录，
>   不实施"，依赖本轮 §1/§4 的静态/运行时信号先积累实际使用数据。

---

## 0. 范围与原则

延续既有文档反复强调的原则：不重复造轮子、不代用户做决定、优先做"暴露
信息/补校验"而不是"改变执行语义"的低风险项。本轮所有条目都满足：

- 不引入新的执行模型或新的 state machine，复用现有 `StepResult` /
  `WorkflowSession` / `validate()` / `system_events` 基础设施。
- 校验类改动一律"能在 save_workflow / dry-run 阶段拦下来的，不要留到
  运行期才暴露"，与 P9-3（condition 静态校验）的既有原则一致。
- 日志类改动一律"默认开启但可关闭、有落盘体积上限"，不因为调试信息
  拖垮长期运行的 workflow session 目录体积。

---

## 1. 占位符 depends_on 一致性静态校验

### 1.1 问题

`WorkflowDef.validate(check_placeholders=True)` 目前只检查 `prompt` 里
`{step_id.output}` / `{step_id.score}` 引用的 `step_id` 是否**存在**，
不检查是否在该 step 的 `depends_on`（直接或传递）范围内。而 `condition`
表达式的静态校验（P9-3）已经做了这层"引用的 step 是否在 depends_on 里"
的一致性检查。两处校验力度不一致：一个写漏了 `depends_on` 的 prompt
占位符，能顺利通过 `validate()`，只会在运行期因为拓扑序/并发分批导致
该 step 实际还没跑完，`step_results.get(step_id)` 为 `None`，触发
`_resolve_prompt` 里的 `KeyError`，报错时机晚、离根因（漏写
`depends_on`）远。

### 1.2 方案

复用 `schema.py::condition_referenced_names` 同一套 `ast`/正则解析思路，
在 `validate()` 的 `check_placeholders` 分支里，对每个 `{step_id.field}`
占位符额外做一次"是否在 `_transitive_deps(step.id)` 范围内"检查，命中
就追加一条 error（不是 warning——这类问题必然导致运行期崩溃，不是"建议
改进"级别）。`_transitive_deps` 目前是 `validate()` 内部的局部闭包函数，
需要提到方法作用域外或复制一份，供 `check_placeholders` 和
`check_condition` 两个分支共用，避免重复实现。

### 1.3 改动文件

- `workflow/schema.py`：`WorkflowDef.validate()` 的 `check_placeholders`
  分支。
- `tests/test_workflow_*.py`：补一个"prompt 引用了存在但未声明依赖的
  step_id"的 `validate()` 用例。

---

## 2. `_resolve_prompt` 对缺失 inputs 的静默保留问题

### 2.1 问题

`{variable}` 占位符在 `inputs` 里找不到对应 key 时，`_resolve_prompt`
直接原样保留 `{variable}` 文本，不报错也不告警。这是刻意设计（避免误伤
prompt 模板里本来就有的大括号文本），但代价是"外部参数没传全"这类高频
错误只能靠人工读输出发现，没有任何运行时信号。

### 2.2 方案（分两层，均为低风险的"暴露信息"，不改变现有兜底行为）

**a) dry-run 阶段暴露"未解析占位符"清单**

`preview_workflow`（dry-run）在跑 `_resolve_prompt` 时，额外收集每个
step 里"形如 `{xxx}`、不含 `.`、且不在 `inputs` 里"的占位符，汇总成
`unresolved_placeholders: {step_id: [var, ...]}` 一并返回。这样用户在
保存/运行前就能看到"这次调用如果不补 `inputs.xxx`，prompt 里会原样带
着大括号发出去"，而不是运行完才在输出里发现异常。

**b) 真正执行时，把"发生了未解析占位符替换"记进第 3 节新增的调试日志**
（而不是改变现有的静默保留行为本身——运行期仍然不中断，只是把这件事
显式记录下来，供事后复盘）。

### 2.3 改动文件

- `workflow/runner.py`：`preview_workflow` 相关函数、`_resolve_prompt`
  （抽出"识别未解析占位符"为可复用的辅助函数，dry-run 和真实执行两处
  共用）。

---

## 3. 结构化数据传递：`output_file` 契约的双向打通

### 3.1 问题

`output_file` 目前只是"落盘契约"（step 跑完后把 `StepResult.output`
写一份到 `session.output_dir/output_file`），下游 step 无法通过占位符
直接引用"某个上游 step 落盘文件的路径"，只能拿到 `.output` 纯文本。
`python_step` 可以用 `ctx.input_json()` 手动解析，但 `agent`/`role_agent`
/`tool_call` 类型的 step 拿不到结构化数据，遇到"上游产出 JSON、下游要
按字段取值"的场景，只能把整段 JSON 文本塞进 prompt 让 LLM 重新"读"，
浪费 token 且有解析走样风险。

### 3.2 方案（本轮只做最小闭环，不引入 schema 校验）

新增占位符 `{step_id.output_file}`，`_resolve_prompt` 命中该字段时
返回该 step 落盘文件的**绝对路径字符串**（而不是内容），供
`agent`/`role_agent` 类型 step 的 prompt 里提示"请读取 <path> 文件"
（配合 Agent 已有的文件读取工具），或者供 `tool_call`/`sub_workflow`
类型 step 的 `tool_args`/`params` 里引用。这一步不涉及给 `WorkflowStep`
加类型标注（`output_schema` 之类留给后续独立方案），只是把已经落盘的
文件路径暴露成一个可引用的占位符字段，成本低、不改变现有语义。

### 3.3 改动文件

- `workflow/runner.py`：`_resolve_prompt` 增加 `output_file` 字段分支；
  需要 `StepResult` 能拿到对应的落盘路径（目前 `output_file` 落盘逻辑
  在 runner 里，需要把写入路径存回某个可查的映射，比如
  `self._step_output_file_paths[step_id]`）。
- `workflow/schema.py`：`validate()` 的占位符字段白名单从
  `("output", "score")` 扩展为 `("output", "score", "output_file")`。

---

## 4. `python_step` 输入未按 `depends_on` 过滤

### 4.1 问题

`PythonStepExecutor.execute` 把 `runner._current_step_results`（**全部**
已跑完的 step 结果，不区分是否声明依赖）整体序列化传给子进程，脚本可以
通过 `ctx.inputs["未声明依赖的 step_id"]` 读到数据，`validate()` 对
脚本内容不做静态分析、完全看不到这类"隐式依赖"，拓扑排序/并发分批的
"同层可并发"假设可能被脚本悄悄破坏（读到了同层另一个不保证已完成的
step 的结果）。

### 4.2 方案

`PythonStepExecutor.execute` 序列化 `inputs_payload` 时，按
`step.depends_on` 过滤 `upstream`，只传递声明过依赖的 step 结果
（`ctx.inputs` 只包含 `depends_on` 里的 key）。如果脚本确实需要访问
未声明依赖的历史 step（比如读取更早阶段的产物），应该显式把该 step_id
加进 `depends_on`——这本来就是"依赖关系应该在 workflow 定义里可见"的
应有语义，不应该靠脚本内容里"偷看"字典绕过。

这是一个**行为变更**（不是纯新增），需要在文档更新说明里标注："升级
后 `python_step` 脚本只能读取到 `depends_on` 里声明的上游 step 结果，
若现有脚本依赖了未声明的 step，需要补充 `depends_on` 后才能继续工作"，
并在 `tests/test_zhihu_workflow_steps.py` 等既有测试里确认现有脚本的
`depends_on` 声明是否完整。

### 4.3 改动文件

- `workflow/executors.py`：`PythonStepExecutor.execute` 的
  `inputs_payload` 构造逻辑。
- 受影响的既有 workflow 定义（`zhihu_content_publish/workflow.yaml` 等）
  需要人工核对 `depends_on` 是否完整，避免升级后脚本读不到预期数据。

---

## 5. 子进程敏感信息传递方式

### 5.1 问题

`PythonStepExecutor.execute` 把 `app_cfg.api_key` 明文写进
`tempfile.TemporaryDirectory()` 下的 `request.json`，供子进程
`py_step_runner.py` 读取后构造 `LLMHelper`。这不是"绕过封装直接调用
API"（`ctx.llm` 仍然是唯一的调用入口，参见上一轮讨论），而是"封装本身
需要 key 才能在子进程里正常转发"，但落盘明文文件仍然存在窗口期风险
（同机其他进程/崩溃转储可能读到）。

### 5.2 方案

把 `api_key` 从 `request.json` 中摘出来，改为通过**环境变量**
（`subprocess.run(..., env={**os.environ, "MINI_AGENT_STEP_API_KEY": ...})`）
传给子进程，`py_step_runner.py` 里 `_build_llm_helper` 优先读环境变量、
`request.json` 里不再包含该字段。其余非敏感的 `app_cfg` 字段
（`project_root`/`model`/`provider` 等）继续走文件方式不变。环境变量
相比临时文件不落盘、生命周期与子进程绑定，是更合适的敏感信息传递方式。

### 5.3 改动文件

- `workflow/executors.py`：`PythonStepExecutor.execute` 的 `request`
  构造与 `subprocess.run` 调用。
- `workflow/py_step_runner.py`：`_build_llm_helper` 读取 key 的来源。

---

## 6.（新增）运行时调试日志：让"这一步到底看到了什么"可追溯

### 6.1 现状缺口

`StepResult` 目前记录的是"结果侧"信息（`output`/`score`/`error`/
`duration_seconds`/`retries_used`/`error_type`/`traceback`/`context`），
`context` 是**出错时**才快照的 step/workflow 配置。也就是说：

- **正常执行（未出错）的 step，完全没有留痕它实际收到的 prompt
  内容**——`_resolve_prompt` 替换占位符之后的最终文本，跑完就丢了，
  只有 `WorkflowStep.prompt` 这个**模板**（未替换）留在 workflow 定义
  里。事后如果想复盘"为什么这一步 LLM 给出了奇怪的回答"，唯一办法是
  手动用当时的 `inputs` + 各上游 `StepResult.output` 在脑子里重新做一遍
  占位符替换，容易对不上（尤其是 workflow 后来又被改过、上游输出也
  不一定还留着的情况下）。
- 第 2 节提到的"未解析占位符"目前完全不落任何痕迹。
- `python_step` 子进程的 `stdout`/`stderr`（脚本里 `print()` 的调试信息）
  目前只在**失败**时被塞进 `RuntimeError` 消息里；成功时 `proc.stdout`/
  `proc.stderr` 直接丢弃，脚本作者想边跑边看中间过程只能改成失败才能看，
  或者自己额外用 `ctx.write_output()` 落盘。
- 并发批次执行时，没有记录"这一批里各 step 具体在哪个线程、什么时刻
  开始/结束"，排查"两个 step 是否真的并发跑了/是不是被串行化了"目前
  只能靠肉眼估算 `duration_seconds` 的时间戳重叠，没有直接证据。

### 6.2 方案：新增 `StepResult.debug_log`（可选、可关闭）

在 `StepResult` 上新增一个可选字段 `debug_log: dict`，默认不填充（受
`cfg.workflow.debug_log_enabled` 开关控制，默认关闭，避免长期运行的
workflow session 目录体积膨胀），开启后由 runner 在每个 step 执行完
（无论成功失败）统一填充：

```python
debug_log = {
    "resolved_prompt": "...",       # _resolve_prompt 替换后的最终文本
                                      # （仅 prompt 驱动的 step 类型有值）
    "unresolved_placeholders": [],   # 第 2 节提到的"inputs 里没找到"的占位符列表
    "upstream_step_ids_used": [],    # 实际引用到的上游 step_id（从占位符/
                                      # python_step inputs 解析得到），便于
                                      # 反查"这一步实际依赖了哪些数据"，
                                      # 与 depends_on 声明的是否一致可以直接 diff
    "started_at": "2026-07-26T10:00:00Z",
    "finished_at": "2026-07-26T10:00:03Z",
    "thread_id": 140234,             # 并发批次里实际执行的线程标识，
                                      # 用于事后核对"是否真的并发执行了"
    "batch_index": 2,                # 属于第几个拓扑分批
    "subprocess_stdout": "...",      # 仅 python_step/script 类型，成功时也保留
                                      # （截断到配置的最大长度，避免日志爆炸）
    "subprocess_stderr": "...",
}
```

`resolved_prompt` 和 `subprocess_stdout` 都可能很长，需要有
`cfg.workflow.debug_log_max_chars`（比如默认 4000 字符）截断保护，
超出部分截断并标注 `"...(truncated, N more chars)"`，不无限增长
`session.json`。

### 6.3 落盘位置与查看方式

不新建独立的日志文件类型，复用 `WorkflowSession` 已有的增量写回机制
（`step_results[step.id] = StepResult(...)` 之后，runner 已经会把结果
写回 `workflow_sessions/<id>/session.json`），`debug_log` 作为
`StepResult` 的一个字段自然跟着落盘，不需要额外设计存储格式。

查看方式：

- `get_workflow_run_status(name, run_id, verbose=True)` 已有的 verbose
  模式里，追加展示 `debug_log` 的关键字段（`resolved_prompt` 摘要、
  `unresolved_placeholders`、`upstream_step_ids_used` 是否与
  `depends_on` 一致）。
- CLI `/workflow debug <run_id> <step_id>` 新增一个子命令，直接打印某个
  step 的完整 `debug_log`（不受 verbose 模式的展示长度限制），这是
  "调试时明确要看某一步细节"的专用入口，跟 `verbose=True` 的"看全局
  概览"场景区分开。

### 6.4 与"结构性问题识别"打通

`upstream_step_ids_used` 与 `depends_on` 声明的 diff，可以作为一种新的
`error_type`/warning 信号并入现有的 `NEEDS_FIX`/watchdog 机制（P10 已有
"连续同类失败提前升级"的基础设施）——如果实际使用的上游 step 集合与
`depends_on` 声明持续不一致，大概率是 workflow 定义写漏了依赖，属于
"结构性问题"而不是"瞬时故障"，可以复用同一套分类逻辑提示用户用
`patch_workflow_step` 修复。这一项依赖第 1/4 节的静态校验先落地，
放在本轮实施顺序的靠后位置。

### 6.5 改动文件

- `workflow/schema.py`：`StepResult` 新增 `debug_log` 字段 + `to_dict`/
  `from_dict`。
- `workflow/runner.py`：`_run_one_step`/`_execute_step` 填充
  `debug_log`（`resolved_prompt`/`unresolved_placeholders`/时间戳/
  线程标识/batch_index）。
- `workflow/executors.py`：`PythonStepExecutor`/`ScriptStepExecutor`
  把 `subprocess_stdout`/`stderr` 透传回调用方（而不是只在异常分支拼进
  错误消息），由 runner 侧决定是否写入 `debug_log`。
- `workflow/tools.py`：`get_workflow_run_status(verbose=...)` 展示
  `debug_log` 摘要。
- `cli/commands/workflow_cmd.py`：新增 `/workflow debug` 子命令。
- `agent_config.json` schema：新增 `workflow.debug_log_enabled`（默认
  `false`）、`workflow.debug_log_max_chars`（默认 `4000`）两个开关。

---

## 7. 优先级与实施顺序建议

```
低风险、纯增量、无需等待其它项：
  1. 占位符 depends_on 一致性校验（§1）
  2. 未解析占位符的 dry-run 暴露（§2a）
  5. 子进程 api_key 改走环境变量（§5）
  6. debug_log 基础字段（resolved_prompt/unresolved_placeholders/
     时间戳/subprocess 输出）（§6.2-§6.3，不含 §6.4）

中等改动，涉及行为变更，需要人工核对既有 workflow 定义：
  4. python_step 输入按 depends_on 过滤（§4）——需要先跑一遍现有
     workflow 定义（尤其 zhihu_content_publish）确认 depends_on 完整，
     避免升级后脚本读不到数据

新增能力，改动面稍大：
  3. output_file 占位符打通（§3）

依赖前面几项先落地：
  6.4 debug_log 与 NEEDS_FIX/watchdog 打通（依赖 §1/§4 的静态信号）
```

建议顺序：**1 → 2a → 6.2/6.3 → 5 → 4 → 3 → 6.4**。理由：

- 1/2a/5/6.2-6.3 都是"只增不改"的低风险项，且 6.2-6.3（debug_log 基础
  能力）越早上线，越能在后续几项改动（尤其是 4）的验证阶段直接派上
  用场——用 `resolved_prompt`/`upstream_step_ids_used` 字段去核对
  "升级 python_step 输入过滤后，现有 workflow 是否还能拿到预期数据"，
  比凭空猜测更可靠。
- 4 是唯一一个需要人工核对既有 workflow 定义、有真实行为变更风险的项，
  放在 debug_log 落地之后做，用新日志能力辅助验证升级前后行为一致性。
- 3（output_file 占位符）是纯新增能力，不影响现有行为，但改动面比
  1/2/5 稍大（涉及 runner 记录落盘路径映射），放在中间。
- 6.4 明确依赖 1/4 的静态/运行时信号先就位，排在最后。

---

## 8. 不做的部分（延续既有原则，避免过度设计）

- **不引入强类型 IO schema**（比如给每个 step 声明 JSON Schema 校验
  输出格式）——第 3 节的 `output_file` 占位符只解决"路径可引用"，
  不解决"格式是否符合预期"，后者需要真实使用场景积累后再评估，属于
  "现在缺什么"而不是"还能加什么"的判断范畴，参照
  `workflow_system_next_directions.md` §0 的筛选标准。
- **不对 `python_step`/`script` 脚本做静态扫描拦截直接调用 LLM API 的
  import**（上一轮讨论提到的方案）——这个改动的收益（防止绕过封装）
  相对改动成本（可能误伤合法的非 LLM 网络调用场景）目前不够清晰，
  先记录在案，不纳入本轮实施范围。
- **不做 debug_log 的默认全量落盘**——默认关闭是刻意选择，避免长期
  运行、高频调用的 workflow 因为调试信息把 session 目录撑爆；只有
  用户主动开启 `debug_log_enabled` 时才产生额外体积。
