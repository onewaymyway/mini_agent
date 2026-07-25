---
name: workflow-debugger
description: 测试、调试、修改已存在的 mini_agent workflow（`.agent/workflows/<name>/`）——沙箱跑单个 step、查一次执行失败在哪、只改坏掉的那个 step、从断点续跑、看长期执行统计。当用户说"这个workflow跑失败了"、"帮我测一下这个step"、"这一步为什么一直失败"、"改一下xxx workflow的这个字段"、"从哪步继续跑"、"这个workflow靠谱吗/成功率怎么样"时使用。**不**用于从零生成新 workflow，那是 workflow-generator skill 的场景。
triggers: workflow调试, workflow失败, workflow测试, 重跑, 断点续跑, patch_workflow_step, resume_workflow_run, test_workflow_step, needs_fix
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
| "跑之前想看看这次大概会怎么分批/condition 会不会命中" | `preview_workflow(name, inputs)`（dry-run，不真的执行） | 直接跑一遍看结果 |
| "正在跑，想先停一下 / 不想跑了" | `pause_workflow_run` / `cancel_workflow_run` | 干等或杀进程 |
| "有个 human_input/审批门卡着" | `provide_workflow_step_input` / `approve_workflow_step` / `reject_workflow_step` | 让用户去改 workflow 定义绕过它 |

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

## 与 workflow-generator 的分工

- 从自然语言描述**从零生成**一个新 workflow → workflow-generator skill。
- 已有 workflow 想**测试某一步**、**排查一次失败**、**改某个字段**、
  **换个方式续跑** → 本 skill。
- 两者会共用同一份 `WorkflowStep`/`WorkflowDef` 字段语义（见
  workflow-generator skill 的字段表），本 skill 不重复维护字段说明，只
  聚焦"已有 workflow 出问题/要验证时该调哪个工具、按什么顺序调"。
