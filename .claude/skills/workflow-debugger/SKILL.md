---
name: workflow-debugger
description: 测试、调试、修改已存在的 mini_agent workflow（`.agent/workflows/<name>/`）——沙箱跑单个 step、查一次执行失败在哪、只改坏掉的那个 step、从断点续跑、看长期执行统计。当用户说"这个workflow跑失败了"、"帮我测一下这个step"、"这一步为什么一直失败"、"改一下xxx workflow的这个字段"、"从哪步继续跑"、"这个workflow靠谱吗/成功率怎么样"时使用。**不**用于从零生成新 workflow，那是 workflow-generator skill 的场景。
triggers: workflow调试, workflow失败, workflow测试, 重跑, 断点续跑, patch_workflow_step, resume_workflow_run, test_workflow_step, needs_fix, debug_log, python_step, workflow debug
---

# Workflow Debugger（测试 / 调试 / 修改已有 workflow）

用于已经用 `save_workflow`（或 workflow-generator skill）保存过的 workflow。
覆盖三类场景：**跑之前先验证**、**跑失败之后排查**、**只改坏掉的那一小块再续跑**。
不生成新 workflow——需要从零写一个新的，切到 workflow-generator skill；
不确定 YAML 字段语义时也去看 workflow-generator skill 里的字段表，这里不
重复。核心工具在 `src/mini_agent/workflow/tools.py`，纯逻辑在
`workflow/api_helpers.py`，状态机定义在 `workflow/schema.py`。

## 先判断用户想要哪一种

| 用户诉求 | 用哪个工具 | 不要用 |
|---|---|---|
| "这个 workflow 整体表现怎么样/长期靠不靠谱" | `get_workflow_stats(name)` | 逐次翻 `list_workflow_runs` 人工数 |
| "这次跑的具体哪步失败了/为什么" | `get_workflow_run_status(workflow_session_id, verbose=true)` | 直接猜、或让用户去翻文件 |
| "改完 prompt/timeout 之后不确定对不对，想先验证一下这一步" | `test_workflow_step`（沙箱，不落盘、不影响正式执行记录） | 直接 `run_workflow` 整个跑一遍（浪费 token，还会重跑已经成功的前面几步） |
| "确认要正式修 workflow 定义里的某个字段" | `patch_workflow_step`（只改一个 step 的字段，会校验、会持久化） | 让用户手工改 YAML 文件、或重新贴一份完整 YAML |
| "改完了，接着跑" | `resume_workflow_run(force_rerun_from=<step_id>)`（只重跑这步及下游，前面成功的不重来） | 重新 `run_workflow` 从头跑一遍 |
| "只是临时想放宽一下 timeout 试试是不是单纯超时问题，不想动定义" | `resume_workflow_run(step_overrides={...})`（一次性覆盖，不写回定义） | `patch_workflow_step`（那是永久改动） |
| "跑之前想看看这次大概会怎么分批/condition 会不会命中/参数是不是传全了" | `preview_workflow(name, inputs)`（dry-run，不真的执行，会给出 `unresolved_placeholders` 清单，见下文） | 直接跑一遍看结果 |
| "这一步到底实际收到了什么 prompt/子进程打印了什么/是不是真并发跑的" | 开启 `debug_log_enabled` 后看 `get_workflow_run_status(verbose=true)` 摘要，或 `/workflow debug <run_id> <step_id>` 看完整 `debug_log`（见下文"运行时调试日志"） | 凭 `inputs`+上游输出在脑子里重算一遍占位符替换 |
| "正在跑，想先停一下 / 不想跑了" | `pause_workflow_run` / `cancel_workflow_run` | 干等或杀进程 |
| "有个 human_input/审批门卡着" | `provide_workflow_step_input` / `approve_workflow_step` / `reject_workflow_step` | 让用户去改 workflow 定义绕过它 |
| "这个 `agent`/`skill_agent` step 老是不稳定/产出格式不对/偶尔跑偏" | 先判断这一步是不是本该是 `python_step`（见下文"用选型规则诊断不稳定的 agent 类型 step"），是的话 `patch_workflow_step` 把 `type` 改成 `python_step` 并配 `script_path` | 一味调大 `retry_on_error`/`max_turns` 掩盖问题 |

## 排查一次失败执行的标准流程

1. **拿到 `workflow_session_id`**：`run_workflow`/`resume_workflow_run` 后台
   执行时会直接返回；忘了的话用 `list_workflow_runs(name)` 找最近一次。
2. **看详细状态**：`get_workflow_run_status(workflow_session_id, verbose=true)`。
   `verbose=true` 才会带上失败步骤的 `traceback` 和出错时的上下文快照
   （prompt 预览、step 配置），排查时基本必开；默认 `false` 只是省 token
   的快速查看模式。
   - 想连续盯着一个后台执行直到跑完/卡住，用 `wait=true`（内部轮询，
     一次工具调用等到终态或超时为止），不要自己写"查一次、决定要不要
     再查"这种反复调用的循环。
3. **区分两类失败状态**（这一步决定你接下来该做什么）：
   - `failed` / `timeout`：可能是瞬时故障（网络抖动、外部服务慢、这次
     token 超时），**可以**直接 `resume_workflow_run` 不带
     `force_rerun_from` 简单重跑，或用 `step_overrides` 临时调大
     `timeout`/`retry_on_error` 再试。
   - `needs_fix`：runner 已经判定这是**结构性/配置性错误**（prompt 占位符
     写错不存在的 step、`tool_name` 没注册、`prompt_file` 路径不存在，
     或者同一 step 连续多次同类失败被 `escalate_after_n_same_failures`
     提前拦下），**重跑没有用**，`get_workflow_run_status` 的输出会显式
     提示"这是定义/配置问题，重跑无效"。这种必须先 `patch_workflow_step`
     改定义，不要在没改动的情况下反复 `resume_workflow_run`。
4. **定位到具体字段该怎么改**：结合 `verbose=true` 里的错误信息和
   `context`（一般包含解析后的 prompt、该 step 当时生效的 model/timeout
   等配置）判断问题出在哪个字段。不确定字段语义/合法取值时参考
   workflow-generator skill 里的字段表，或 `show_workflow(name)` 看完整
   当前定义。
5. **改动前先用沙箱验证**（尤其是改了 prompt 措辞、想确认新写法是否真的
   解决问题时）：
   ```
   test_workflow_step(
     name="<workflow>", step_id="<出问题的step>",
     mock_step_results='{"上游step id": {"output": "...", "score": 0.9}}',
     mock_inputs='{"参数名": "..."}'
   )
   ```
   这一步会真实调用 LLM/工具（不是假执行），但不接入 DAG、不落盘进
   `workflow_runs` 历史、不影响任何正式执行——可以反复试而不产生垃圾记录。
   `mock_step_results` 要覆盖该 step prompt 里用到的所有 `{xxx.output}`/
   `{xxx.score}` 占位符，缺了会报错并提示需要补哪些；`human_input`/
   `require_approval` 类型的 step 不支持沙箱测试，工具会直接提示跳过，
   这种只能走真实 `run_workflow`/`resume_workflow_run` 验证。
6. **确认沙箱结果符合预期后再正式修改**：
   ```
   patch_workflow_step(name="<workflow>", step_id="<step>",
                        patch='{"prompt": "改好的新 prompt", "timeout": 120}')
   ```
   `patch` 只需要传变化的字段，其余字段保持不变；字段名与 workflow-generator
   字段表一致（`prompt`/`model`/`timeout`/`retry_on_error`/
   `retry_on_gate_fail`/`allow_parallel`/`require_approval`/`tool_name`/
   `tool_args`/`workflow_name`/`script`/`skill_name`/`input_key`/
   `input_prompt`/`condition`/`depends_on` 等）。改动后会自动跑一遍
   `wf.validate()`，校验不通过**不会保存**，会把错误列表直接返回——按
   报错逐条修正，不要绕过校验手工改磁盘上的 YAML。
7. **续跑**：
   ```
   resume_workflow_run(workflow_session_id="<之前那次的id>",
                        force_rerun_from="<step_id>")
   ```
   `force_rerun_from` 会让这个 step 及其所有下游重新执行，之前已经成功、
   消耗过 token 的步骤不会重来。不传 `force_rerun_from` 时是"从断点继续
   跑未完成的部分"，语义不同，改完定义后要修某一步通常需要显式传
   `force_rerun_from`，否则如果该 step 之前状态不是"未完成"可能不会被
   重新纳入执行计划。

## 用选型规则诊断不稳定的 `agent`/`skill_agent` step：能不能改成 `python_step`

workflow-generator skill 里的强制排序是：能用 `python_step`（配合
`ctx.llm`/`ctx.llm.ask_json`）解决的步骤应该优先用 `python_step`，
`agent`/`role_agent` 是其它方案都覆盖不了时才用的兜底方案（`skill_agent`
介于两者之间）。调试时凡是遇到下面这几类症状，第一反应不应该是调大
`retry_on_error`/`max_turns`/`timeout`，而应该先判断这一步是不是当初生成
时选型选错了、本该是 `python_step`：

- **产出格式不稳定**：这一步要的是结构化结果（下游 `python_step` 消费的
  JSON），但 `agent`/`skill_agent` 没配 `result_file`，靠对话原文解析，
  时好时坏——如果这一步本身是"给定输入、按固定规则产出判断"（没有真正的
  临场应变需求），正确修法不是补 `result_file`，而是直接改成
  `python_step` + `ctx.llm.ask_json`，从根上消除"模型要不要按格式说话"这个
  不确定性。
- **同一类失败反复出现、被 `escalate_after_n_same_failures` 升级成
  `needs_fix`**：先用 `get_workflow_run_status(verbose=true)` 看错误内容，
  如果是"模型输出解析失败"“漏判/多判”这类而不是外部依赖报错，大概率是
  选型问题而不是 prompt 措辞问题。
- **`get_workflow_stats(name)` 里某个 `agent`/`skill_agent` step 的平均
  重试次数/失败率明显偏高，且它的职责描述读起来像"过滤/打分/重排/解析"**：
  这是选型错误的强信号。

**诊断步骤**：

1. `show_workflow(name)` 看这一步当前的 `prompt`/`prompt_file`，判断它是不是
   纯粹在要求模型"读取一段结构化输入、按规则给出结构化判断"——如果 prompt
   里完全没有要求模型自主决定调用哪些工具/自主探索下一步该干嘛，这是可以
   转成 `python_step` 的强信号。
2. 确认转换后仍能满足需求：`python_step` 脚本里用 `ctx.llm.ask_json()`
   把原来 prompt 的判断要求原样喂给模型，`ctx.inputs`/`ctx.input_json()`
   替代原来的 `{step_id.output}` 占位符读取上游数据。
3. 写好 `steps/<step_id>.py`（暴露 `def run(ctx) -> str|dict`），用
   `test_workflow_step` 沙箱验证新脚本产出是否符合下游预期（沙箱同样会
   真实拉起子进程、真实调用 LLM，见下文"沙箱测试 `python_step`"一节）。
4. 验证通过后 `patch_workflow_step(name, step_id, patch='{"type": '
   '"python_step", "script_path": "steps/xx.py"}')`——同时清掉这一步不再
   需要的 `agent` 专属字段（比如 `skill_name`/`max_turns`），`patch` 只需要
   传变化的字段，多余的旧字段留着通常不影响校验，但会造成阅读混淆，建议
   一并清理。
5. 提醒用户确认 `agent_config.json` 里 `python_step_enabled` 已开启，否则
   转换后这一步会被直接拦截，反而制造新的失败。

**什么时候不该转换**：如果这一步的失败原因明确是"需要临场应变"（比如
浏览器页面结构变了、需要根据中间结果动态决定下一步调用哪个工具），说明
选型本身没错，应该按原有的 `skill_agent`/`result_file` 排查路径处理（见
下一节），硬转成 `python_step` 只会让这类步骤更脆弱。

## `skill_agent` 步骤失败或"文件已生成但迟迟不结束"：先看 `result_file` 契约

`skill_agent`（声明了 `result_file`）是当前 workflow 里最容易出现"看起来卡住"
或"重试多次仍失败"的 step 类型，排查前先确认这一点，再决定怎么改：

1. **先看 `result_file` 是否声明、`result_file_required_keys` 是否列全**：
   `show_workflow(name)` 里没有 `result_file` 字段的 `skill_agent` step，
   意味着下游只能靠对话原文解析——如果失败信息看起来像"下游 JSON 解析出
   错"而不是这个 step 本身报错，很可能是漏配了 `result_file`，应该先给
   `patch_workflow_step` 补上，而不是一味调大 `retry_on_error`。
2. **`verbose=true` 里如果错误信息形如"resume×3 + 重开×3 次尝试后仍未产出
   合法的 result_file"**：说明 `result_file` 契约本身校验失败了 3+3=6 次，
   通常是 prompt 指令不够明确（模型没意识到必须写文件）或
   `result_file_required_keys` 列的字段模型理解有偏差。先用
   `test_workflow_step` 单独跑这一步，观察它对话里到底有没有尝试写文件、
   写的字段跟要求的是否对得上，再决定改 prompt 还是改
   `result_file_required_keys`。
3. **"result_file 已经生成但这一步迟迟不结束"不是 bug，是预期行为**：
   `result_file` 只在 agent 这一轮**自然结束**后才会被校验——如果 agent
   写完文件后还在继续浏览/反复自我确认，step 就会一直等到它自己收尾或
   撞到 `max_turns`/`timeout`。这种情况改法是在 prompt 里补一句"写完文件
   并自检通过后立即收尾，不要再做任何其它操作"，或者适当调低 `max_turns`
   逼它更早收敛，而不是去调 `timeout`（调大 `timeout` 只会让"卡住的感觉"
   变得更久，治标不治本）。
4. **`ctx.input_json(step_id)`/`ctx.input_output(step_id)` 优先读
   `result_file`**：如果下游 `python_step` 报"字段缺失"一类的错，先确认
   上游 `result_file` 那次校验到底有没有通过（`get_workflow_run_status`
   里能看到该 step 的 `status`），通过了才会读文件，没通过会退回读对话
   原文——这两种数据源的字段完整性可能完全不同，排查时不要想当然。

## 用 `events.jsonl` 排查"卡在哪一步"/耗时异常

`.agent/workflow_sessions/<id>/events.jsonl` 里每个 step 正常应该有一对
`step_start`（开始执行前）+ `step_end`（执行完毕，带 `duration_seconds`/
`status`/`retries_used`）。怀疑某次执行"卡住了"但还没到能看
`get_workflow_run_status` 终态的地步时，直接读这个文件比反复轮询状态更
直接：

```bash
cat .agent/workflow_sessions/<id>/events.jsonl | python3 -c \
  "import json,sys; [print(json.loads(l)['event'], json.loads(l).get('step_id'), json.loads(l).get('status','')) for l in sys.stdin]"
```

- 只有 `step_start` 没有对应 `step_end` 的 step，就是当前正卡着的那一步——
  再结合它的 `type` 判断：`skill_agent`/`agent` 大概率是还在跑多轮工具
  调用（参考上一节），`python_step` 卡住通常是脚本本身死循环/外部调用
  没超时，可以直接去看 `script_path` 对应脚本有没有网络调用忘了设
  timeout。
- 如果同一个 `step_id` 出现了多组 `step_start`/`step_end`，说明这一步被
  重跑过（`retry_on_error` 内部重试，或多次 `resume_workflow_run`），按
  时间顺序看最后一组的 `status` 才是最终结果，前面几组只是历史记录。
- 只有 `step_end` 没有 `step_start` 的记录，是旧版本（`step_start` 事件是
  后补的）留下的历史 session，不代表当前这次执行有问题。

## 只是想临时调参数、不想动定义（`step_overrides`）

`resume_workflow_run` 的 `step_overrides` 参数用于"这次先试试放宽点限制，
但不确定要不要永久改"这类场景：

```
resume_workflow_run(
  workflow_session_id="<id>",
  step_overrides='{"analyze": {"timeout": 300, "retry_on_error": 2}}'
)
```

- 只影响**这一次** resume 执行，不写回 `workflow.yaml`，下次
  `run_workflow`/`resume_workflow_run` 不会带上这次的覆盖；
  `get_workflow_run_status` 的输出会带一行提示标注"本次使用了临时覆盖"，
  避免误以为这是 workflow 定义本身的行为。
- 只允许覆盖**执行参数类**字段的白名单：`timeout` / `retry_on_error` /
  `allow_parallel` / `model` / `escalate_after_n_same_failures`。**不允许**
  覆盖 `prompt`/`condition`/`tool_name` 等会改变 step 语义的字段——那类改动
  本质是"改逻辑"，必须走 `patch_workflow_step` 留痕，不能用"临时覆盖"的
  名义绕过；传了白名单外的字段会被拒绝。
- 想验证"是不是单纯超时"、"并发是不是导致了资源竞争"这类假设时，这个
  参数比 `patch_workflow_step` 更合适，因为不需要事后再 patch 回去。
  `force_serial=true`（`run_workflow`/等价场景）同理，是"这一次强制串行"
  而不改 `allow_parallel` 定义本身。

## 常见坑

- **调大 `retry_on_error`/`max_turns` 不是万能修法**：如果失败原因是"这一步
  本该是 `python_step` 却生成成了 `agent`/`skill_agent`"，调重试次数只会
  多花 token、不会提升成功率，见上文"用选型规则诊断不稳定的 agent 类型
  step"，先判断选型对不对，再决定要不要调参数。
- **`needs_fix` 状态重跑没有用**：这是设计上的"保存期/运行期区分"——
  结构性错误（占位符写错、`tool_name` 不存在等）无论重试多少次结果都一样，
  runner 会直接跳过 `retry_on_error` 预算改判 `needs_fix`。看到这个状态
  别再无脑 `resume_workflow_run`，先改定义。
- **`test_workflow_step` 不是"假执行"**：它仍然会真的调用 LLM/工具，
  只是不接入 DAG、不落盘历史——如果 step 里有副作用（写文件、调用外部
  API），沙箱测试一样会触发这些副作用，只是"不算作正式一次 workflow
  执行"。有强副作用的 step 沙箱测试前要想清楚。
- **`mock_step_results` 结构**：key 是上游 step 的 id，value 是一个包含
  `output`/`score` 等字段的 dict，不是直接给字符串；`condition` 用到的
  `.passed` 之类自定义字段如果 prompt 没用到可以不用补。
- **`force_rerun_from` vs 不传**：不传时是"接着跑断点之后没完成的部分"，
  如果目标 step 之前已经是 `done` 状态，不会被重新执行；确实要重跑某个
  已经"成功过"的 step（比如它的输出其实是错的，只是没被判定为失败），
  必须显式传 `force_rerun_from=<该step_id>`。
- **`step_overrides` 不持久化**：改完发现确实需要长期生效，要额外补一次
  `patch_workflow_step` 把同样的值写进定义，`step_overrides` 只管这一次。
- **`get_workflow_run_status` 默认 `verbose=false`**：调试时几乎总是要开
  `verbose=true`，否则看不到 `traceback`/`context`，只能看到状态和粗略
  错误信息。
- **`patch_workflow_step` 的 `patch` 字段名必须是 `WorkflowStep` 已有属性**：
  传入未知字段名会被直接拒绝并报出具体是哪个字段名不认识，不会静默忽略；
  不确定某个字段是不是这个 step 类型该有的（比如给 `agent` 类型 step 传了
  `tool_name`），可以传，`patch_workflow_step` 本身不按 `type` 过滤，但
  `wf.validate()` 未必会报错——这种"类型不匹配但字段合法"的情况不会被
  自动拦下来，改完最好用 `show_workflow`/`test_workflow_step` 确认真的
  生效了预期的效果。
- **文件夹模式 workflow 报 `workflow_dir 未设置`**：如果是在 `resume_workflow_run`
  之后才出现（首次 `run_workflow` 没问题），说明 resume 走的是
  `workflow_def.yaml` 快照重新解析，而不是重新 `WorkflowStore.load(name)`；
  当前实现已经会在 resume 时按 workflow 名字重新定位一次原始目录、补齐
  `source_dir`/展开 `prompt_file`/`script_path`。如果你在自己的插件或二次
  开发里绕过了 `resume_workflow_run`、自己拼装 `WorkflowDef` 去调
  `WorkflowRunner.run()`，要记得这一步不能省，否则 `python_step` 里
  `ctx.load_prompt_file()` 会直接报错。
- **改动前后都可以 `preview_workflow`**：不确定这次改动会不会影响并发
  分批、condition 求值结果，`patch_workflow_step` 保存成功后可以再
  `preview_workflow(name, inputs)` 看一眼 dry-run 结果，比直接跑一遍正式
  执行更省成本。

## 长期健康度：`get_workflow_stats`

用户问"这个 workflow 稳不稳"、"哪一步经常出问题"、"要不要调整某个 step
的配置"时，不要靠翻 `list_workflow_runs` 人工数，直接：

```
get_workflow_stats(name="<workflow>")
```

返回整体成功率、每个 step 的出现次数/失败率/平均耗时/平均评分/平均重试
次数，以及有 `condition` 的 step 的命中率（未被跳过的比例）。这是纯粹对
已落盘的历史执行记录做聚合，不会触发任何新执行。看到某个 step 失败率/
平均重试次数明显偏高，是"该考虑给它调 `timeout`/`retry_on_error`/拆分
这一步"的信号——但具体怎么改还是要结合 `get_workflow_run_status(verbose=
true)` 里的具体错误原因，不要只凭统计数字瞎猜。

## 运行时调试日志：`debug_log` 与 `/workflow debug`（P11 §6）

`failed`/`needs_fix` 之外，还有一类排查诉求是"这一步**执行成功**了，但结果
不对/看着奇怪，想知道它到底实际收到了什么"——`StepResult` 默认只在出错时
留痕上下文，正常执行完的 step 不会保留替换占位符后的最终 prompt。

**开启方式**：`agent_config.json` 里 `{"workflow": {"debug_log_enabled":
true}}`（默认关闭，避免长期运行的 workflow session 目录体积膨胀）。开启后
每个 step 执行完（无论成功失败）都会在 `StepResult.debug_log` 里记录：

- `resolved_prompt`：`_resolve_prompt` 替换占位符后的**最终文本**（仅
  prompt 驱动的 step 类型有值），事后复盘"为什么这一步给出了奇怪的回答"
  不再需要人工重算占位符替换。
- `unresolved_placeholders`：这次执行里"形如 `{xxx}`、在 `inputs` 里没找到
  对应 key"的占位符列表——即使 `_resolve_prompt` 本身会静默保留原样不报错，
  这里也能看到确实发生过。
- `upstream_step_ids_used`：实际引用/读取到的上游 step_id，可以直接跟
  `depends_on` 声明 diff，判断是不是"用到了没声明的依赖"。
- `started_at`/`finished_at`/`thread_id`/`batch_index`：并发排查"这两个
  step 是不是真的并发跑了/是不是被串行化了"的直接证据，不用再靠肉眼估算
  `duration_seconds` 时间戳是否重叠。
- `subprocess_stdout`/`subprocess_stderr`：仅 `python_step`/`script` 类型，
  **成功时也会保留**（此前只有失败时才会把子进程输出塞进错误消息里），脚本
  里用 `print()` 打的调试信息现在不需要失败才能看到。

**查看方式**：

1. `get_workflow_run_status(workflow_session_id, verbose=true)` 会展示
   `debug_log` 的关键字段摘要（按 `debug_log_max_chars`，默认 4000 字符
   截断）。
2. 想看某一步**完整**的 `debug_log`（不受 verbose 摘要长度限制），用
   `/workflow debug <workflow_session_id> <step_id>` CLI 子命令（REPL 内
   或 `mini-agent workflow debug ...` 独立命令行都可以）。

**排查 `python_step` 脚本问题时特别有用**：升级到 P11 之后，`python_step`
的 `ctx.inputs` 默认按 `depends_on` 过滤（不再是全部已跑完的 step），如果
脚本报"读不到预期的上游数据"，先看 `debug_log.upstream_step_ids_used` 和
该 step 的 `depends_on` 是否一致——大概率是脚本引用了一个没写进
`depends_on` 的 step_id，应该先 `patch_workflow_step` 把它加进
`depends_on`，而不是关掉 `python_step_inputs_filtered_by_depends_on` 开关
绕过（那是给排查用的临时回退，不建议作为正式修复手段）。

**与 `undeclared_dependency_usage` 的关系**：保存阶段的静态校验（占位符
`depends_on` 一致性检查、`python_step` 输入过滤）已经把大部分"用了未声明
依赖"的问题拦在运行之前；只有显式关闭对应开关时，运行期才会在
`debug_log["undeclared_dependency_usage"]` 里留下兜底记录（同时上报给
watchdog），这条记录只做记录、不改变该 step 的执行结果。

---

## 沙箱测试 / 续跑时如何验证 `python_step`

`test_workflow_step` 对 `python_step` 同样适用（会真实拉起子进程执行脚本，
不是假执行），但要注意：

- `mock_step_results` 需要覆盖脚本 `depends_on` 里声明的所有上游 step，
  key 要和 `depends_on` 列表完全对应——沙箱测试同样遵循"`ctx.inputs` 按
  `depends_on` 过滤"这条 P11 规则，mock 数据里多给的 key 不会被脚本看到。
- 脚本如果调用了 `ctx.llm`，沙箱测试会真实触发 LLM 调用（子进程独立建一条
  `LLMHelper`，不跟随主 Agent 当前 `/model` 状态），产生真实 token 消耗，
  这点和 `agent` 类型 step 的沙箱测试是一致的代价。
- 脚本内 `print()` 输出想在沙箱测试时也能看到，需要先开启
  `debug_log_enabled`，否则子进程 stdout/stderr 默认不落进沙箱测试的返回
  结果里（沙箱测试本身不写入 `workflow_runs` 历史，但仍然复用同一套
  `debug_log` 填充逻辑）。

## 命令行直接跑（不进 Agent 对话）

以上所有工具在 Agent 对话之外还有一条等价路径：`mini-agent workflow ...`
独立命令行（`src/mini_agent/cli/commands/workflow_cmd.py::run_workflow_cli`，
在 `cli/app.py` 里与 `daemon`/`user`/`self`/`eval` 一样按 `sys.argv[1]`
短路，不进入交互 REPL、不构造 Agent，只 `load_config()`）。子命令名和参数
与 REPL 里的 `/workflow ...` 完全一致，前缀换一下即可：

```
mini-agent workflow run <name> ['{"key":"value"}'] [--background] [--project <path>]
mini-agent workflow status <workflow_session_id> [--project <path>]
mini-agent workflow resume <workflow_session_id> [--background] [--project <path>]
mini-agent workflow stats <name> [--project <path>]
```

用户想要"脚本里/cron/systemd 里直接跑一个已保存的 workflow，不想为此启动
一整个交互式 Agent 会话"时，这是该走的路径，不是让用户手写 Python 调
`WorkflowRunner`，也不是建议他们进 REPL 敲 `/workflow run`。

- 不传 `--project`/`-p` 时默认用当前工作目录作为项目根，找不到 workflow
  时排查的第一件事是确认 `cwd`/`--project` 对不对。
- **`--background` 在独立 CLI 下的语义和 REPL 里不一样**：REPL 里
  `--background` 是"这个长期存活的进程内起一个后台线程"；独立 CLI 一次性
  命令跑完就退出，同样起线程的话线程会被一起杀掉、工作流根本跑不完——
  所以这里改成 spawn 一个完全独立的 OS 子进程（`start_new_session=True`，
  不继承父进程的进程组），父进程立刻返回打印 `workflow_session_id` 和
  一个日志文件路径（子进程 stdout/stderr 重定向到
  `<workflow_sessions目录>/<id>/cli_detached.log`），即使触发它的 shell/
  cron 已经退出，子进程也会独立跑完。**用户要的是"挂后台，进程退出后还在
  跑"时，一定要提醒用户加 `--background`**，不加的话是前台同步阻塞执行，
  命令不会提前返回。
- 后续查进度/续跑/审批，全部继续用同一套工具（`get_workflow_run_status`/
  `patch_workflow_step`/`resume_workflow_run` 等，走 Agent 对话）或对应的
  `mini-agent workflow status|resume|...` 命令行子命令都可以，两条路径共享
  同一份落盘的 `WorkflowSession` 状态，互相看得到彼此的执行记录，不是两套
  隔离的东西。
- `mode: autonomous` 的 workflow 天生适合配这条路径：所有参数在启动时
  通过 `inputs_json` 一次性给全，配合 `--background` 挂到独立子进程里跑，
  适合直接写进 crontab/systemd timer，不需要人在旁边盯着交互式会话。
  **注意**：目前 CLI 子命令的 `run` 只暴露了 `inputs_json`/`--background`
  两个参数，`run_workflow` 工具里的 `require_all_inputs_upfront`/
  `force_serial` 这两个开关暂时没有对应的 CLI flag——想用这两个开关，还是
  要走 Agent 对话调工具，或者先确认这次真的需要它们再考虑要不要给 CLI
  补上（工具层参数不会自动透传到命令行）。



- 从自然语言描述**从零生成**一个新 workflow → workflow-generator skill。
- 已有 workflow 想**测试某一步**、**排查一次失败**、**改某个字段**、
  **换个方式续跑** → 本 skill。
- 两者会共用同一份 `WorkflowStep`/`WorkflowDef` 字段语义（见
  workflow-generator skill 的字段表），本 skill 不重复维护字段说明，只
  聚焦"已有 workflow 出问题/要验证时该调哪个工具、按什么顺序调"。
