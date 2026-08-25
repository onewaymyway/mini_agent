# Workflow 机制演进日志

> 本文档是 [workflow-guide.md](workflow-guide.md) 的配套演进日志，收录
> workflow 系统各能力方向按批次（P1-P15 及各改进方案）落地的具体细节：
> Session 数据目录、后台执行/暂停/取消、审批门、失败重试、全自动执行、
> 强制串行、出错定位编辑重跑、调试闭环细化（P10）、Step 类型化（P5）、
> 编写规范、`python_step`（P11）、`foreach`/`wait`/`merge`（P13/P14）、
> `output_file` 契约、运行时调试日志、`defaults`/`workflow_snippets`（P7）、
> 自定义 Step 类型插件化（P7）、文件夹模式 Workflow、生命周期 Hook（P5）、
> 引用完整性校验（P6）、内置模板库（P6）。
>
> 主指南只保留稳定的架构说明、核心概念、YAML 格式定义、内置工具列表、
> 示例工作流与当前配置/CLI 参考；某个能力**何时因为什么方案落地、具体
> 怎么实现**都在这里，按原文档章节顺序排列（大致即落地的时间先后）。
>
> 拆分依据同 [growth-advisor-directions-history.md](growth-advisor-directions-history.md)
> 的拆分原则（见
> [next_doc/growth_advisor_docs_reorganization_and_system_state_review.md §2.2](../next_doc/growth_advisor_docs_reorganization_and_system_state_review.md#22-建议拆分--保留时间线索引而不是简单加索引层)，
> 该原则本轮一并套用到 workflow 这份体量同样失衡的指南上）。

## Workflow Session：执行会话与数据目录（P1/P2）

自本次改进起，**每一次 `run_workflow` 调用都会创建一个 WorkflowSession**，
不再是"跑完即焚"的一次性调用。所有相关数据聚合在：

```
.agent/workflow_sessions/<workflow_session_id>/
  ├── session.json          # 执行状态：status/当前批次/control_flags/待审批step
  ├── workflow_def.yaml     # 执行时使用的工作流定义快照（防止运行中途原文件被改）
  ├── events.jsonl          # 结构化事件流（workflow_start/step_start/step_end/paused/...）
  ├── watchdog.jsonl        # 看护线程的心跳超时/资源护栏告警记录
  └── step_<step_id>/       # 该 step 对应 Agent 的完整数据
      └── <session_id>/     # history / meta / traces / temp / output / artifacts
```

`workflow_session_id` 在调用 `run_workflow` 时自动生成并在返回结果里给出
（后台模式下直接在返回文本里）；`list_workflow_runs` / `get_workflow_run_status`
可以查询任意一次执行的实时进度。

**`events.jsonl` 事件类型**：每行一条 JSON，字段固定含 `ts`（unix 时间戳）/
`event`/`workflow_session_id`，其余字段按事件类型而定：

| `event` | 触发时机 | 额外字段 |
|---|---|---|
| `workflow_start` | `run()` 开始（含 resume） | `workflow_name`、`resumed`（是否是续跑） |
| `step_start` | 一个 step 通过依赖检查/condition/审批门，真正开始执行前 | `step_id`、`step_name`、`type`、`batch_index` |
| `step_end` | 一个 step 执行完毕（含失败/gate_failed） | `step_id`、`status`、`duration_seconds`、`retries_used` |
| `approval_requested` / `approved` / `rejected` | 人工审批门 | `step_id` |
| `paused` / `cancelled` | 主动暂停/取消 | `at_batch` |
| `workflow_end` | 整个工作流结束 | `status`、`error` |

按 `step_id` 把同一个 step 的 `step_start`/`step_end` 配对，可以算出真实墙钟
耗时（跟 `step_end.duration_seconds` 对照，能发现"排队等锁"之类的额外开销）；
只有 `step_end` 而没有对应 `step_start` 通常意味着该 step 是从旧版本落盘的
`events.jsonl`（`step_start` 是后补的事件，之前版本的 session 里不会有）。

**断点恢复**：进程崩溃或主动暂停后，用 `resume_workflow_run(workflow_session_id)`
即可从已完成的批次之后继续跑，不会重跑已经 `done` 的步骤。**resume 时会用
`workflow_def.yaml` 快照重新解析 `WorkflowDef`**（不是重新走一次
`WorkflowStore.load(name)`），因此对**文件夹模式**的 workflow，resume 路径
需要额外按 workflow 名字重新定位一次原始目录，把 `source_dir`（继而
`workflow_dir`）以及 `prompt_file`/`script_path` 展开重新解析一遍——这一步
如果遗漏，`python_step` 里调用 `ctx.load_prompt_file()` 会报
`workflow_dir 未设置`。这是历史上出现过的一个真实 bug，当前实现已修复；
自己给 `resume_workflow_run` 相关逻辑做二次开发/插件化时，注意保持这个
"重新解析相对路径资源"的步骤不要丢。

---

## 后台执行、暂停、取消（P3）

`run_workflow` 新增 `background` 参数：

- `background=False`（默认，或 `agent_config.json` 里
  `workflow.background_execution_default=false`）：**前台同步执行**，
  工具调用会一直阻塞到工作流跑完，返回完整的结果摘要，行为与改进前一致。
- `background=True`：立即返回 `workflow_session_id`，工作流在**后台线程**
  继续执行。此时可以用：
  - `get_workflow_run_status(workflow_session_id)` 查看进度
  - `pause_workflow_run(workflow_session_id)`：请求暂停，会在当前批次
    跑完后停下，可用 `resume_workflow_run` 续跑
  - `cancel_workflow_run(workflow_session_id)`：请求取消，正在跑的步骤
    尽快中止，未开始的步骤标记为 `cancelled`

**看护线程**（`workflow.watchdog_enabled`，默认开启）在后台监控：
- 每个步骤的心跳是否超过其 `timeout` 未更新，超时后强制标记该步骤为
  `timeout` 状态并继续推进（已知限制：Python 线程无法被安全强杀，超时
  后底层线程可能仍在后台跑完，但 runner 不再等待）；
- 累计执行时长是否超过 `WorkflowDef.max_total_duration` 或全局配置
  `workflow.max_total_duration_seconds`，超过则主动请求取消；
- 累计 token 用量（`P7-②1`）是否超过 `WorkflowDef.max_total_tokens` 或全局
  配置 `workflow.max_total_tokens`，超过则同样主动请求取消。只统计
  `type: agent` / `type: skill_agent` 这两类步骤的 `input_tokens +
  output_tokens`（能拿到独立 `Agent.stats` 的类型），`role_agent` /
  `sub_workflow` / `tool_call` 等类型暂不计入。

---

## 人工审批门（P4）

在步骤定义里加上 `require_approval: true`，工作流跑到该步骤前会暂停，
等待人工调用 `approve_workflow_step(workflow_session_id)` 放行，或
`reject_workflow_step(workflow_session_id, reason="...")` 拒绝（该步骤会
被标记为 `rejected` 并跳过，下游依赖它的步骤按 `SKIPPED`/`FAILED` 语义
处理）。

```yaml
- id: send_notification
  role: main
  prompt: "..."
  require_approval: true   # 高风险/有外部副作用的步骤，默认要求人工放行
```

**必须配合 `background=True` 使用**：前台同步执行时没有其它线程能在
阻塞期间调用 approve/reject，等待会在
`workflow.approval_wait_timeout_seconds`（默认 600 秒）后自动判定为拒绝。
若工作流里检测到任何 `require_approval` 步骤，`run_workflow`/`resume_workflow_run`
会自动切换为后台执行，无需手动传 `background=True`。

---

## 通用失败重试（P4）

`retry_on_error`（区别于原有的 `retry_on_gate_fail` 质检门重试）用于处理
网络超时、工具报错等**普通异常**：

```yaml
- id: fetch_external_api
  role: main
  prompt: "..."
  retry_on_error: 2   # 失败后最多重试 2 次，每次退避时长递增
```

退避时长由 `workflow.retry_on_error_backoff_seconds`（默认 5 秒）线性递增
（第 N 次重试等待 N × backoff 秒）。

---

## 全自动执行模式（`mode: autonomous`，改进方案 §1）

默认 `mode: interactive`，行为与之前完全一致。当一个 workflow 被设计为
"全程自动执行、所有参数在启动时就已给全，中途不应该等任何人工输入"时，
把 `mode` 设为 `autonomous`：

```yaml
name: nightly_report
mode: autonomous
steps:
  - id: ask_scope
    type: human_input
    input_key: report_scope   # 必须设置，否则 save_workflow 时校验失败
    prompt: "请说明本次报告范围"
```

`autonomous` 模式下，`save_workflow`/`patch_workflow_step` 保存前的校验会
拒绝以下两种"会导致运行时阻塞"的写法：

- `type: human_input` 但没有设置 `input_key`（无法判断值从哪里来）；
- `require_approval: true`（需要人工审批放行）。

`human_input` 步骤设置了 `input_key` 后，行为变为：启动 `run_workflow` 时若
`inputs` 里能通过该 key 找到对应值，直接使用、不进入阻塞等待；同一份
`human_input` 定义因此既能在 `mode: interactive` 下交互式使用（人工临场
输入），也能在全自动调用里被复用（所有参数最初一次性传完）。

配合 `run_workflow(..., require_all_inputs_upfront=true)` 使用时，启动前会
一次性扫描所有 `human_input` 步骤，缺少可解析输入直接报错列出缺哪些字段，
不会等跑到一半才发现卡住。

---

## 强制串行执行（`force_serial`，改进方案 §2）

`allow_parallel` 之前只能在单个 step 上设置。如果想让**整个 workflow 这次
运行**都串行（不改动 workflow 定义本身），有两种粒度：

- **单次运行**：`run_workflow(name, ..., force_serial=true)`，本次调用忽略
  所有 step 的 `allow_parallel`，把拓扑分层结果拍平成逐个执行；
  `resume_workflow_run(..., force_serial=true)` 同理。
- **全局**：`agent_config.json` 里的 `workflow.parallel_enabled=false`，
  对所有 workflow 生效，适合"这台机器资源紧张/纯排障模式"这类整体性诉求。

`force_serial` 不传时，实际是否串行取决于全局 `workflow.parallel_enabled`
（默认 `true`，即默认允许并行，具体每个 step 是否并发仍受该 step 自己的
`allow_parallel` 与拓扑分层约束）。

---

## 出错定位、编辑与重跑（`patch_workflow_step` / `force_rerun_from`，改进方案 §4）

### 查看错误详情

`get_workflow_run_status` 现在会输出每个失败/超时/被拒绝/需要修复的步骤的
`error`/`error_type`；传 `verbose=true` 额外输出 `traceback` 与出错时的
`context`（prompt 预览、step 配置）：

```
get_workflow_run_status(workflow_session_id="wfs_xxx", verbose=true)
```

`wait=true` 时本次调用会在**内部**轮询，直到该次执行到达终态或超过
`timeout`（默认 300s）才返回——用于看护一个后台 workflow 时只需一次
工具调用，不需要自己反复"查一次、决定要不要再查"：

```
get_workflow_run_status(workflow_session_id="wfs_xxx", wait=true, timeout=600)
```

### 区分"能不能重试"

失败步骤的状态里，`gate_failed`/`failed`（含 `retries_used`）通常是瞬时性
问题，直接续跑即可；`needs_fix` 状态专指结构性/配置性错误（prompt 占位符
写错、`tool_name` 未注册、`prompt_file` 路径不存在等）——这类错误重试多少
次结果都一样，`runner` 检测到后会跳过 `retry_on_error` 直接判 `needs_fix`，
提示需要先修改 workflow 定义。

### 只改一个 step、只重跑一段

```
patch_workflow_step(
  name="nightly_report",
  step_id="analyze",
  patch='{"prompt": "修正后的 prompt", "timeout": 120}'
)

resume_workflow_run(
  workflow_session_id="wfs_xxx",
  force_rerun_from="analyze"
)
```

`patch_workflow_step` 只修改指定字段（未出现在 `patch` 中的字段保持不变），
保存前会跑一次 `WorkflowDef.validate()`，校验不通过则不落盘。
`resume_workflow_run(force_rerun_from=<step_id>)` 会让该 step 及其所有下游
重新执行，`step_id` 之前已经成功、消耗过 token 的步骤不会重来；
`force_rerun_from` 自身的输入沿用 `patch_workflow_step` 改过之后的新定义。

若不确定该改哪里，工具输出命中失败状态时会自动附带一条提醒，按
"`get_workflow_run_status(verbose=true)` → `patch_workflow_step` →
`resume_workflow_run(force_rerun_from=...)`" 的顺序处理即可。

---

## 调试闭环细化 + 看护趋势感知（`workflow_mechanism_improvement_plan_p10.md`）

P9 之后的下一轮改进，聚焦"改一个 step 后如何低成本先验证"、"临时调试参数
如何不污染正式定义"、"连续同类失败该不该继续傻等重试"三个真空点。

### `test_workflow_step`：改完先验证，不必接入正式 DAG 重跑

`patch_workflow_step` + `force_rerun_from` 是"改定义 → 正式重跑"，会落盘进
`workflow_runs/`、计入统计、下游 step 也会跟着继续跑。对"我刚改了这个
step 的 prompt，想先确认措辞对不对"这种高频微调场景，成本偏重。

`test_workflow_step` 提供一个更轻的验证方式：只执行某个已保存 workflow
里的一个 step，用手工给的 mock 上游数据代替真实依赖，**不落盘进
`workflow_runs` 历史、不创建 `WorkflowSession`、不启动 watchdog、不触发
hooks/system_events**——跑完直接把结果返回，用完即弃：

```
test_workflow_step(
  name="nightly_report",
  step_id="analyze",
  mock_step_results='{"fetch": {"output": "原始数据...", "passed": true}}',
  mock_inputs='{"lang": "zh"}'
)
```

要点：

- **仍然是真实执行**：会真的调用 LLM / 真的跑 `tool_call` / `script`，
  因为验证"prompt 措辞对不对"必须是真实调用才有意义。沙箱化的只是
  "不接入 DAG、不落盘"，不是"不真正执行"。
- `mock_step_results` 里每个 step 的字段：`output`（模拟输出文本，填充
  `{step_id.output}` 占位符）、`score`（0~1 浮点，填充 `{step_id.score}`
  占位符）、`passed`（决定内部状态是 done 还是 failed，影响 condition
  求值）；未提供 mock 数据但 prompt 引用了对应 `{step_id.output}` 时会
  直接报错，提示需要补哪个 step 的 mock 数据。
- `timeout_override` 只影响这一次沙箱调用，不写回 step 定义。
- `type=human_input` 或 `require_approval=true` 的 step **不支持**沙箱
  测试（这类 step 本身没有"输出对不对"的验证意义），会直接提示跳过，
  建议改用 `resume_workflow_run(force_rerun_from=...)` 实际验证。
- `script` 类型 step 的沙箱执行仍然走与正式执行相同的权限检查
  （`cfg.workflow.script_step_enabled` 开关），不因为是"测试"就放宽。

与 `force_rerun_from` 的边界：`test_workflow_step` 是**临时验证**，跑完
即弃、无痕迹；`resume_workflow_run(force_rerun_from=...)` 是**正式重跑**，
会写入 `workflow_runs` 历史、真正推进该次执行的进度。改完 step 后，先用
`test_workflow_step` 验证措辞/逻辑没问题，再用 `force_rerun_from` 正式
续跑，是推荐的调试顺序。

### `resume_workflow_run(step_overrides=...)`：一次性覆盖 vs 永久 patch

`patch_workflow_step` 改的是 workflow **定义本体**，会影响这次和以后所有
执行。调试时经常只想"这次续跑临时放宽一下（比如把某个 step 的 timeout
临时调大，看看是不是单纯超时问题）"，用 `patch_workflow_step` 做这件事
等于把一次性尝试永久写进了正式定义，还得记得测完改回去。

```
resume_workflow_run(
  workflow_session_id="wfs_xxx",
  step_overrides='{"analyze": {"timeout": 120}}'
)
```

- `step_overrides` **只影响本次 resume 执行**，不写回 `WorkflowStore`
  持久化的 YAML/目录定义——下一次 `run_workflow`/`resume_workflow_run`
  不会带上这次的覆盖。
- 只允许覆盖"执行参数类"字段：`timeout` / `retry_on_error` /
  `allow_parallel` / `model` / `escalate_after_n_same_failures`。
  **不允许**覆盖会改变 step 语义的字段（`prompt`/`condition`/
  `tool_name` 等）——这类改动本质上是"改逻辑"，必须走
  `patch_workflow_step` 留痕；传入白名单外的字段会直接报错拒绝，不会
  静默忽略。
- `get_workflow_run_status` 若查到某次 run 使用过 `step_overrides`，会
  额外标注一行 `⚠️ 本次执行使用了临时覆盖：{...}，未写入 workflow 定义`，
  避免误以为这是定义本身的行为。

### Watchdog：连续同类失败提前升级 `NEEDS_FIX`

P9 已经能识别"第一次失败就能从异常类型判断出是结构性问题"（`KeyError`/
`FileNotFoundError`/工具未注册等直接跳过 `retry_on_error` 判 `needs_fix`）。
但还有一类失败**异常类型本身像瞬时故障**（比如 `TimeoutError`/
`APIError`），如果连续 N 次重试都在**同一个 step、同一种 error_type** 上
失败，大概率也不是"运气不好"，而是这一步的 prompt/参数本身有问题。

现在看护线程（`watchdog.py`）会追踪每个 step 的连续同类失败次数：默认
连续 **2 次**同一个 `error_type` 就提前判定 `NEEDS_FIX`，跳过剩余的
`retry_on_error` 预算，不必等预算耗尽：

```yaml
steps:
  - id: call_api
    prompt: "..."
    retry_on_error: 5
    escalate_after_n_same_failures: 3   # 可选，覆盖默认阈值 2
```

- `escalate_after_n_same_failures` 未设置时，继承 `defaults` 里的同名配置，
  再没有则用全局默认值 `2`，完全向后兼容旧 YAML。
- 提前终止时的错误信息会写明触发依据："连续 N 次同类失败
  （error_type=...），已提前判定为需要修改定义，跳过剩余 M 次重试预算"，
  可通过 `get_workflow_run_status(verbose=true)` 查看。
- 若中途出现了不同 `error_type` 的失败，连续计数会重新从 1 开始——只有
  "连续、同类"才会触发提前升级，正常的"这次超时、那次别的错误"仍按原有
  逻辑走满重试预算。

### workflow 级熔断（P14）

上面的 `escalate_after_n_same_failures` 只统计**同一个 step 内部**的连续
同类失败，无法捕捉"整个 workflow 是不是在系统性地失败"（比如某个外部
API 挂了，导致 5 个不同 step 都在各自重试）。`WorkflowConfig` 新增
`circuit_breaker_distinct_step_threshold`（默认 `null`=不启用，行为与
改造前完全一致）：

```json
{"workflow": {"circuit_breaker_distinct_step_threshold": 3}}
```

- watchdog 全程累计"同一个 `error_type` 曾经导致失败过的**不同 step_id**
  集合"（本次运行全程累计，不做滑动窗口），集合大小达到阈值时触发熔断：
  标记 `circuit_breaker_tripped=True`、记录原因写入 `watchdog.jsonl`、
  调用与 `max_total_duration`/`max_total_tokens` 超限时**同一套**
  `control.request_cancel()` 信号，运行中/待执行的 step 会在下一次批次
  边界照既有逻辑被取消，不需要额外的处理路径。
- 与"同一个 step 内部连续失败"的区别：即使每个 step 都只失败了一次（没有
  触发各自的 `escalate_after_n_same_failures`），只要**不同 step** 因为
  **同一个 `error_type`** 失败的数量达到阈值，也会被判定为系统性问题提前
  熔断，不必等每个 step 各自耗尽重试预算。
- 触发原因可通过 `get_workflow_run_status(verbose=true)` 查看。

---

## Step 类型化（P5）

`WorkflowStep.type` 显式声明该步骤"怎么被执行"，未设置时按旧语义自动推断
（`role` 非空 → `role_agent`，否则 → `agent`），**完全向后兼容旧 YAML**：

| `type` | 说明 | 专属字段 |
|---|---|---|
| `agent`（默认） | 独立主 Agent 实例执行 | — |
| `role_agent` | 指定角色 Agent 执行（`role` 非空时的旧默认行为） | `role` |
| `sub_workflow` | 把另一个已保存的工作流当作一个 step 执行 | `workflow_name` |
| `tool_call` | 直接调用一个已注册工具，不启动整个 Agent 会话 | `tool_name`, `tool_args`（P12 起支持占位符） |
| `human_input` | 阻塞等待人工通过 `provide_workflow_step_input` 送入文本 | `input_prompt`, `input_key` |
| `script` | 执行一段 shell 命令（P15 起可选 `result_file` 结构化模式，见下方结构化结果契约一节） | `script`（可选 `result_file`/`result_file_required_keys`） |
| `skill_agent` | 独立主 Agent 实例执行，且强制预加载指定 skill（不走关键词触发判断） | `skill_name`（可选 `result_file`/`result_file_required_keys` 声明结构化结果契约，见下方专节） |
| `python_step`（P11） | 在**独立子进程**里跑一段外置的 Python 脚本，不启动 Agent，适合"给定输入产出结构化 JSON"这类确定性数据加工，见下文"`python_step`：脚本化 step"一节 | `script_path`, `params` |
| `foreach`（P13） | 对一个列表的每个元素执行同一份内层 step 定义（可控并发度），结果聚合成 JSON 数组，见下文"批处理与汇聚"一节 | `items`, `foreach_step`, `foreach_max_concurrency`, `foreach_stop_on_error` |
| `wait`（P13） | 等待指定秒数，可被 `pause`/`cancel` 信号打断（不同于 `python_step` 里 `time.sleep` 会被子进程超时机制误伤） | `wait_seconds` |
| `merge`（P14） | 把多个上游 step 的结果按策略汇聚成一个（拼接文本 / JSON 数组 / JSON 合并），把"手写拼接"升级成一等公民 step | `merge_sources`, `merge_strategy`, `merge_separator`, `merge_use_result_file` |

> **内置类型现共 11 种**（`agent`/`role_agent`/`sub_workflow`/`tool_call`/
> `human_input`/`script`/`skill_agent`/`python_step`/`foreach`/`wait`/
> `merge`），插件还可以在此基础上注册自定义类型（见下文"自定义 Step
> 类型：插件化扩展"一节）。

> **选型优先级**：能用 `python_step` 解决的步骤（含需要调用一次
> `ctx.llm`/`ctx.llm.ask_json` 做判断的场景）应优先用 `python_step`；
> `agent`/`role_agent` 是确定性执行方式都无法覆盖时的兜底方案，不是默认
> 选项。详见下文"`python_step`：脚本化 step"一节里的"优先级规则：
> `python_step` 优先，`agent` 是兜底"。

```yaml
- id: notify
  type: tool_call
  tool_name: send_slack_message
  tool_args:
    channel: "#eng"
    text: "审查通过"

- id: ask_reviewer
  type: human_input
  input_prompt: "请输入本次发布的审批意见"

- id: sub_report
  type: sub_workflow
  workflow_name: research_report   # 引用另一个已保存的工作流
  depends_on: [sub_report_input]

- id: build
  type: script
  script: "npm run build"
```

**安全默认值**（均可在 `agent_config.json` 的 `workflow` 节里配置，见下文）：
- `sub_workflow` 有递归深度保护（`max_sub_workflow_depth`，默认 3），避免
  A 引用 B、B 又引用 A 造成的无限递归。
- `tool_call` 默认视为高风险步骤，即使没有显式写 `require_approval: true`，
  也会走人工审批门（除非把 `tool_call_step_auto_approve` 显式设为 `true`）。
- `script` **默认关闭**（`script_step_enabled: false`），工作流 YAML 可能来自
  LLM 生成或他人分享，默认不允许执行任意 shell 命令，需要显式打开开关。
- `human_input` 等待有超时保护（`human_input_wait_timeout_seconds`，默认
  1800 秒），超时后该步骤标记为 `FAILED`。
- `python_step` **默认关闭**（`python_step_enabled: false`，语义与
  `script_step_enabled` 一致），防止分享出去的 workflow YAML 变成任意 Python
  代码执行入口，需要在 `agent_config.json` 里显式开启。
- `foreach` **不允许嵌套**（`foreach_step.type` 不能是 `foreach`），
  `validate()` 保存期直接拒绝，避免批处理资源控制/调试复杂度失控。
- `merge_sources` 里的每个 step id 复用与 prompt 占位符相同的
  "是否存在/是否在 `depends_on` 范围内"校验，写漏 `depends_on` 同样会在
  保存期报 error。

在 CLI 里对正在等待人工输入的执行送入文本：

```
/workflow input <workflow_session_id> <要送入的文本>
```

Agent 侧对应的工具是 `provide_workflow_step_input(workflow_session_id, input_text)`。

---

## 编写规范：prompt / 脚本外置（`next_doc/workflow_authoring_guide.md`）

从 P11 起，**建议**（非强制阻断，向后兼容旧 workflow）遵循以下写法，尤其是新建的
文件夹模式 workflow：

1. **prompt 一律外置到 `prompts/*.md`**：`workflow.yaml` 的 `steps[].prompt`
   不写超过 3 行的内联文本。内联 `prompt` 超过阈值（5 行）时，
   `WorkflowDef.validate()` 会给出一条 **warning**（记录在
   `wf.last_validate_warnings`，不阻断保存/运行），建议改用：

   ```yaml
   - id: analyze_doc
     type: agent
     prompt_file: prompts/01_analyze_doc.md   # 相对 workflow 目录解析
   ```

   `prompt_file` 支持和内联 `prompt` 完全相同的占位符语法，加载阶段先读文件
   内容再走占位符替换，两者语义等价，只是来源不同。

2. **`python_step` 的脚本代码外置到 `steps/*.py`**，`workflow.yaml` 只写
   `script_path`（见下文"`python_step`：脚本化 step"一节）。

3. **每个 step 声明 `output_file`**，产出统一由 runner 落盘到当前 workflow
   session 的 `output/` 目录（`.agent/workflow_sessions/<wfs_id>/output/
   <output_file>`），不管这个 step 是哪种 executor 类型产生的输出，都不需要
   agent prompt 或脚本自己拼路径（见下文"`output_file` 输出落盘契约"一节）。

标准目录结构（目录化 workflow）：

```
<workflow_name>/
├── workflow.yaml          # 骨架：id/type/depends_on/prompt_file/script_path/output_file 等
├── prompts/
│   └── *.md                # 每个 agent/skill_agent step 的 prompt
├── steps/
│   └── *.py                 # 每个 python_step 的脚本代码
├── agents/                  # 已有：本地角色 profile（如果用到）
└── skills/                  # 已有：本地 skill（如果用到）
```

**安全边界**：`prompt_file`/`script_path` 的相对路径解析严格限制在 workflow
目录内（`store.py::_resolve_prompt_files()`/`_resolve_script_paths()` 都会做
路径穿越校验，越界的会被忽略并打印警告）。

`.agent/workflows/zhihu_content_publish/` 是按本规范落地的完整参考实现（4 个
step、4 个 prompt 文件、2 个 python_step 脚本），可以直接参考其目录结构和
`workflow.yaml` 写法。

---

## `python_step`：脚本化 step（P11）

### 优先级规则：`python_step` 优先，`agent` 是兜底

**编写/生成/修改 workflow 时的强制性排序规则**：一个步骤的功能如果能用
`python_step`（配合 `ctx.llm`/`ctx.llm.ask_json` 调用模型做判断性子任务）
解决，就**应该优先用 `python_step`**，而不是直接写成 `agent`/`role_agent`/
`skill_agent`。`agent` 系列（不带 `skill_name` 强制约束、允许模型自主决定
调用哪些工具、自主决定要跑多少轮）是**其它方案都无法覆盖时才用的兜底
方案**，不是默认选项。

判断顺序建议：

1. 这一步是纯确定性加工（解析/过滤/重排/校验上游 JSON，或调用一次
   `ctx.llm.ask`/`ctx.llm.ask_json` 就能拿到结构化判断结果）→ `python_step`。
   即使需要模型参与判断（比如"从 30 条里筛选符合要求的"），只要能把"要
   模型做什么判断"讲清楚成一次或几次 `ask_json` 调用，也应该写成
   `python_step`，而不是因为"要用到 LLM"就直接升级成 `agent`——
   `ctx.llm`/`ctx.llm.ask_json` 本身就是 `python_step` 里调用模型的正规
   方式，不是只有 `agent`/`skill_agent` 才能碰模型。
2. 这一步需要模型在**明确边界内**多轮调用某个特定工具/技能完成一个有限
   任务（比如按固定套路调用一个 skill）→ 优先 `skill_agent`（强制挂载单
   一 skill，行为边界比 `agent` 更可控），而不是无约束的 `agent`。
3. 只有当步骤确实需要**临场应变**——页面结构、外部状态、可用工具在运行
   时才能确定，需要模型自主探索/试错/决定下一步动作（比如浏览器交互、
   开放式调研、无法提前枚举的多轮工具调用）——才使用 `agent`/`role_agent`。
   这是**其它方案（`python_step`/`tool_call`/`skill_agent`）都无法解决时
   的兜底方案**，不应该是生成 workflow 时的默认选择。

反过来也要注意：不要为了"图省事、不想开 `python_step_enabled` 开关"而把
本该用 `python_step` 的确定性加工步骤硬写成 `agent`——那样既浪费 token/轮次
预算，稳定性也更差（模型可能不按预期格式产出，还要靠下游脆弱解析兜底）。

### 何时用 `python_step` 而不是 `agent`/`skill_agent`

`python_step` 适合"给定确定性输入、产出结构化 JSON"的加工步骤——不需要
"随机应变"的判断力（比如批量过滤候选数据、解析/重排上游 JSON）。真正需要
临场应对（比如页面结构会变的浏览器交互）的步骤，仍然应该用 `skill_agent`/
`agent`，脚本硬编码反而稳定性更差。

### 脚本入口约定

脚本文件（`script_path` 指向）必须暴露：

```python
def run(ctx: PyStepContext) -> str | dict:
    ...
```

`ctx`（`src/mini_agent/workflow/py_context.py::PyStepContext`）提供的接口：

| 接口 | 说明 |
|---|---|
| `ctx.llm.ask(prompt, *, system="", max_retries=3, override_model=None, override_provider=None)` | 转发到 `LLMHelper` 的单次问答调用 |
| `ctx.llm.ask_json(prompt, *, system="", schema_hint="", max_retries=3)` | 约定返回 JSON，内部用 `json_repair` 宽松解析 + 解析失败重试 |
| `ctx.run_agent_turn(prompt, *, skill_name=None, max_turns=6)` | 临时起一个最小 Agent，处理需要判断力的子任务（与 `skill_agent` 执行器共用同一段构造逻辑，`runner.py::_spawn_minimal_agent`） |
| `ctx.params` | `workflow.yaml` 里 step 级 `params` 透传的自定义参数 |
| `ctx.inputs[step_id]` / `ctx.input_output(step_id)` / `ctx.input_json(step_id)` | 读取上游 step 的 `StepResult`/纯文本输出/JSON 解析结果 |
| `ctx.load_prompt_file(path)` | 读取 `prompts/*.md`，供脚本内自己拼 prompt 用 |
| `ctx.write_output(...)` | 往 session `output_dir` 落盘中间产物 |

### 执行方式与安全边界

- **子进程隔离**：`runner` 起 `subprocess.Popen([sys.executable, "-m",
  "mini_agent.workflow.py_step_runner", ...])`，`runpy.run_path(script_path)`
  加载脚本执行 `run(ctx)`，超时/进程组管理对齐 `script` 类型 step（Windows
  `CREATE_NEW_PROCESS_GROUP`/Unix `start_new_session`），watchdog 能杀掉整个
  子进程树。
- **默认关闭**：`cfg.workflow.python_step_enabled` 默认 `false`（语义与
  `script_step_enabled` 一致），需要在 `agent_config.json` 里显式开启：

  ```json
  {"workflow": {"python_step_enabled": true}}
  ```

- **LLM 调用**：子进程内 `ctx.llm` **不**共享主进程 `Agent` 的
  `LLMClientPool` 运行时状态（不跟随主 Agent 运行期 `/model` 切换），而是
  独立构造一条 `LLMHelper`（P11 简化版实现，惰性构造——脚本不调用
  `ctx.llm` 时完全不会尝试建 provider 连接）。
- **敏感信息传递**：`api_key` 不写入子进程临时目录下的 `request.json`
  明文文件，改为通过环境变量 `MINI_AGENT_STEP_API_KEY` 传给子进程，落盘
  文件里不再包含该字段。
- **输入按 `depends_on` 过滤**（行为变更，见下文 P11 §4）：`ctx.inputs`
  默认只包含该 step `depends_on` 里声明过的上游 step 结果，不是全部已跑完
  的 step——脚本要读取某个未声明依赖的历史 step，必须先把它加进
  `depends_on`，不能靠"偷看"完整字典绕过。可用
  `cfg.workflow.python_step_inputs_filtered_by_depends_on=false` 回退旧行为
  （不建议，仅用于临时排查）。

### 批量处理建议

多条候选数据的判断类场景（比如"从 30 条候选问题里筛选符合要求的"），建议
脚本内部做**分批 + 结构化 JSON 输出**（`ctx.llm.ask_json`），而不是逐条调用
——一次批量调用能省掉重复的 system prompt 开销，也减少请求数。`ask_json`
返回的判断数量明显少于批次数量时（漏判），建议拆成更小的子批重试而不是
直接丢弃，参考 `.agent/workflows/zhihu_content_publish/steps/03_filter.py`
的实现（`BATCH_SIZE`/`MISS_RATIO_THRESHOLD`/`MIN_SUB_BATCH` 三个可调常量）。

---

## 批处理与汇聚：`foreach` / `wait` / `merge`（P13/P14）

三种新增 step 类型都以纯 `StepExecutor` 插件形式落地，**不改动**
`runner.py` 的拓扑调度/并发分批核心逻辑——从外部调度层的视角看，它们跟
其它类型没有区别：输入一份 `resolved_prompt`（或直接读 `items`/
`merge_sources`），输出一段文本，`output_file`/评分提取/`NEEDS_FIX`/
`GATE_FAILED` 判定完全复用现有机制。

### `foreach`：对列表逐元素批处理

解决"先搜出一批候选、再逐个 enrich"这类场景——之前只能在 `python_step`
脚本里手写循环调用 `ctx.run_agent_turn()`，YAML 完全看不出"这里其实是
批处理"；现在可以让编排层显式声明：

```yaml
- id: enrich_each_question
  type: foreach
  depends_on: [search_zhihu]
  items: "{search_zhihu.result_file:questions}"   # 引用上游 result_file 里的字段，解析为原始 list
  foreach_max_concurrency: 3                        # 内层并发度，默认 1（串行）
  foreach_stop_on_error: false                      # 默认单元素失败不影响其它元素
  foreach_step:
    type: skill_agent
    skill_name: browser-cdp
    prompt: "打开问题详情页并提取正文：{item}（第 {item_index} 条）"
    max_turns: 15
```

- **`items`**：要遍历的列表，可以是 YAML 字面量列表，也可以是**单个**
  占位符字符串（如上例），此时取占位符解析出的**原始 Python 对象**（不是
  文本），复用 `{step_id.result_file:path}` 的解析逻辑；`items` 为空或
  解析结果不是 `list` 时，保存期直接报错。
- **`foreach_step`**：内层要对每个元素执行的 step 定义子集（`type`/
  `prompt`/`tool_name`/`tool_args`/`skill_name`/`script`/`script_path`/
  `params`/`role` 等），必须指定 `type`，**不能是 `foreach`**（禁止嵌套，
  `validate()` 直接拒绝）。内层 prompt 里可以用两个专属占位符：
  - `{item}`：当前元素（`dict`/`list` 会被 `json.dumps` 成文本，标量直接
    `str()`）
  - `{item_index}`：从 0 开始的序号

  内层没有"上游 step 结果"的概念，只有 `item`/`item_index`，这是一套跟
  外层 `_resolve_prompt` 独立的替换逻辑。
- **`foreach_max_concurrency`**：默认 `1`（串行，最保守），显式调大才会用
  线程池并发执行多个元素；与外层 `allow_parallel` 是两个独立的并发维度，
  不共享同一个线程池，避免互相干扰调试。
- **`foreach_stop_on_error`**：默认 `false`——某个元素执行失败不影响其它
  元素，失败元素在聚合结果里记成 `{"item_index": i, "error": "..."}`，
  整个 `foreach` step 仍然 `DONE`；设为 `true` 时第一个元素失败即整体抛
  异常，交给外层现有的 `retry_on_error`/`NEEDS_FIX` 机制处理。
- 输出：按原始下标顺序聚合成一段 **JSON 数组文本**，下游可以直接
  `{enrich_each_question.output}` 或用 `python_step` 的
  `ctx.input_json("enrich_each_question")` 消费。

### `wait`：可中断的等待

以前只能在 `python_step` 里 `time.sleep`，会跟子进程超时/watchdog 硬超时
的语义打架。`wait` 类型独立处理等待期间的控制信号：

```yaml
- id: rate_limit_pause
  type: wait
  wait_seconds: 30
```

- 内部拆成 0.5 秒一片循环 `sleep`，每片之间检查 `pause`/`cancel` 控制
  信号——收到 `cancel_workflow_run` 时提前退出并报告，收到
  `pause_workflow_run` 时阻塞在原地直到 `resume`，与其它 step 类型遇到
  暂停/取消时的响应方式完全一致，不是被子进程超时机制"误杀"。
- `wait_seconds` 必须是正数，`validate()` 会校验。

### `merge`：把多分支/多并发结果汇聚成一等公民 step

以前多分支/多并发结果汇总只能靠某个 step 的 prompt 里手写
`{a.output}{b.output}{c.output}` 拼接，或靠 `python_step` 脚本读多个
`result_file`；现在 `merge` 把这个"汇聚节点"变成 workflow 图里看得见、
调得动的一等公民：

```yaml
- id: final_report
  type: merge
  depends_on: [summary_a, summary_b, enrich_each_question]
  merge_sources: [summary_a, summary_b, enrich_each_question]  # 顺序即聚合顺序
  merge_strategy: concat_text     # concat_text（默认）/ json_array / json_merge
  merge_separator: "\n\n---\n\n"  # concat_text 用的分隔符
  merge_use_result_file: false    # true 时 json_array/json_merge 从 result_file 读取
```

`merge_strategy` 三种取值：

| 策略 | 行为 |
|---|---|
| `concat_text`（默认） | 按 `merge_sources` 顺序拼接各来源 `output` 文本，用 `merge_separator` 分隔——向后兼容"手写拼接"最常见的用法 |
| `json_array` | 各来源的值组成一个 JSON 数组（`merge_use_result_file=true` 时读 `result_file`，否则 `json.loads(output)`，解析失败则原始文本作为字符串元素） |
| `json_merge` | 各来源须为 JSON object，按 `merge_sources` 顺序 `dict.update`，后者覆盖前者同名 key（来源解析出非 dict 时直接报错） |

- `merge_sources` 必须非空、无重复；每个 id 复用 prompt 占位符已有的
  "引用是否存在/是否在 `depends_on` 范围内"校验逻辑——写漏 `depends_on`
  同样会在 `save_workflow` 阶段报 error。
- `foreach` 产出一份 JSON 数组后，常见的下一步就是跟另一个 step 的结果
  用 `merge`（`json_array`/`json_merge`）合并成最终输出，两者是天然的
  上下游组合。

---

## `output_file` 输出落盘契约（P11 §A3）

任意类型的 step 都可以声明 `output_file`，该 step 执行完成后，runner 统一把
`StepResult.output` 写一份到当前 workflow session 的
`.agent/workflow_sessions/<wfs_id>/output/<output_file>`，不依赖 agent prompt
或脚本自己拼路径：

```yaml
- id: analyze_doc
  type: python_step
  script_path: steps/01_analyze_doc.py
  output_file: doc_analysis.json
```

下游 step 引用该文件有两种方式：

- 直接读 `.output`（文本内容，`agent`/`role_agent` 通过占位符
  `{analyze_doc.output}`，`python_step` 通过 `ctx.input_output("analyze_doc")`/
  `ctx.input_json("analyze_doc")`）。
- 引用落盘文件的**绝对路径**（占位符 `{analyze_doc.output_file}`），适合
  "上游产出的 JSON 较大、下游只需要提示 Agent 去读文件"的场景，避免把整段
  JSON 塞进 prompt 浪费 token。

---

## 运行时调试日志：`StepResult.debug_log`（P11 §6）

### 是什么、解决什么问题

`StepResult` 原本只在**出错**时才快照 `context`（prompt/step 配置），正常
执行完成的 step 完全没有留痕它实际收到的最终 prompt——事后想复盘"为什么
这一步给出了奇怪的回答"，只能凭 `inputs` + 各上游输出在脑子里重新做一遍
占位符替换。P11 新增可选字段 `debug_log`，由 runner 在每个 step 执行完
（无论成功失败）统一填充：

```json
{
  "resolved_prompt": "...",            // _resolve_prompt 替换后的最终文本
                                        // （仅 prompt 驱动的 step 类型有值）
  "unresolved_placeholders": [],        // inputs 里没找到对应 key 的占位符列表
  "upstream_step_ids_used": [],         // 实际引用到的上游 step_id，可与
                                        // depends_on 声明直接 diff
  "started_at": "2026-07-26T10:00:00Z",
  "finished_at": "2026-07-26T10:00:03Z",
  "thread_id": 140234,                 // 并发批次里实际执行的线程标识
  "batch_index": 2,                    // 属于第几个拓扑分批
  "subprocess_stdout": "...",          // 仅 python_step/script，成功时也保留
  "subprocess_stderr": "...",
  "undeclared_dependency_usage": {...} // 实际引用的上游与 depends_on 声明
                                        // 不一致时的记录（见下文）
}
```

`resolved_prompt`/`subprocess_stdout`/`subprocess_stderr` 都按
`cfg.workflow.debug_log_max_chars`（默认 4000 字符）截断保护，超出部分标注
`"...(truncated, N more chars)"`。

### 开关与查看方式

- **默认关闭**（`cfg.workflow.debug_log_enabled=false`），避免长期运行的
  workflow session 目录体积膨胀；开启后作为 `StepResult` 的一个字段自然
  跟着 `session.json` 落盘，不需要额外存储格式。
- `get_workflow_run_status(name, run_id, verbose=true)` 会展示 `debug_log`
  的关键字段摘要（`resolved_prompt` 摘要、`unresolved_placeholders`、
  `upstream_step_ids_used` 是否与 `depends_on` 一致）。
- CLI 新增子命令 `/workflow debug <workflow_session_id> <step_id>`，直接
  打印某个 step 的**完整** `debug_log`（不受 verbose 展示长度限制），是
  "调试时明确要看某一步细节"的专用入口。

### 与结构性问题识别打通

`upstream_step_ids_used` 与 `depends_on` 声明不一致时（实际引用/读取到了
未声明依赖的上游 step），`debug_log_enabled` 开启的情况下 runner 会写入
`debug_log["undeclared_dependency_usage"]`，同时调用
`WatchdogReporter.report_dependency_mismatch()` 上报（与 P10"连续同类失败
提前升级"共用同一套 `_log_event` 机制）。这一路径只做记录、不改变当前
step 的执行结果——保存阶段的静态校验（占位符 `depends_on` 一致性检查、
`python_step` 输入按 `depends_on` 过滤）已经把大部分这类问题拦在运行之前，
只有显式关闭 `placeholder_depends_on_check_enabled`/
`python_step_inputs_filtered_by_depends_on` 才会在运行期触发这条兜底记录。

---

## workflow 级默认配置（`defaults`，P7-③1）

`WorkflowDef` 顶层可以加一个 `defaults` 字段，为 `model` / `timeout` /
`max_turns` / `retry_on_error` / `allow_parallel` / `escalate_after_n_same_failures`
（P10 新增，见"看护趋势感知"一节）这 6 个 step 级字段提供统一默认值。
查找顺序是**三层**："step 显式写的值 → `defaults` 里的值 → 运行时硬编码
兜底"（`max_turns` 兜底 10，`retry_on_error` 兜底 0，`allow_parallel`
兜底 `true`，`escalate_after_n_same_failures` 兜底 2，`model`/`timeout`
兜底不限制/走全局配置）：

```yaml
name: data_pipeline
defaults:
  model: gpt-4.1-mini      # 除非某个 step 单独写了 model，否则都用这个
  max_turns: 6
  retry_on_error: 1

steps:
  - id: fetch
    prompt: "抓取原始数据"
    # 没写 max_turns/model/retry_on_error → 继承上面 defaults 里的值

  - id: analyze
    prompt: "深度分析 {fetch.output}"
    max_turns: 20            # 显式写了 → 优先于 defaults，只对这一个 step 生效
```

**这是完全向后兼容的改动**：没写 `defaults` 的旧 YAML 行为不变（直接落到
硬编码兜底，和改进前完全一样）。保存（`to_dict`）时只有 step **显式**
写过的字段才会出现在 YAML 里——哪怕显式值恰好等于硬编码默认值（例如显式
写 `allow_parallel: true`）也会被保留下来，这样才能和"没写、跟随
`defaults` 走"区分开。

---

## 可复用 step 片段（`workflow_snippets`，P7-③2）

多个工作流经常需要重复同一段 step（例如"打分 → 生成报告"这套质检
组合），P7-③2 允许把这样一段 `steps` 提取成独立文件复用，通过
`include:` 字段引用，纯**加载期展开**，不改变 runner 的执行逻辑，展开
之后跟手写完整 YAML 完全一样。

**片段文件位置**：`<project_root>/.agent/workflow_snippets/<n>.yaml`，
格式是一段 `steps:` 列表（与 workflow YAML 里的 `steps` 字段同构，不含
`name`/`description` 等顶层字段）：

```yaml
# .agent/workflow_snippets/quality_check.yaml
steps:
  - id: score
    prompt: "对上面的产出打分（0-100）"
  - id: report
    depends_on: [score]
    prompt: "根据 {score.output} 生成质检报告"
```

在工作流 YAML 里引用：

```yaml
steps:
  - id: analyze
    prompt: "分析 {input}"

  - id: qc                 # 这个 id 会作为片段内所有 step 的命名空间前缀
    include: quality_check  # 引用上面那个片段文件（不带 .yaml 后缀）
    depends_on: [analyze]   # 挂到片段"入口" step 上（片段内没有依赖的 step）

  - id: final
    depends_on: [qc]        # 引用 include 条目本身的 id，自动指向片段
    prompt: "汇总：{qc.output}"   # 展开后自动指向片段里最后一个 step 的输出
```

展开后实际生效的 step 是 `analyze` → `qc__score` → `qc__report` →
`final`：片段内每个 step 的 `id` 会加上 `"qc__"` 前缀（避免同一片段被
多处 `include` 时 id 冲突），片段内部的 `depends_on` 与 prompt 占位符
引用会同步改写为加前缀后的 id；片段里"没有依赖"的入口 step（这里是
`score`）自动接上 `include` 条目自己声明的外部 `depends_on`
（这里是 `analyze`）；工作流里其它 step 对 `qc` 这个 id 的引用
（`depends_on: [qc]`、`prompt` 里的 `{qc.output}`）会被自动改写为指向
片段展开后的最后一个 step（这里是 `qc__report`）。

管理片段可以直接读写 `.agent/workflow_snippets/*.yaml` 文件，也可以用
`WorkflowStore` 的 `list_snippets()` / `load_snippet(name)` /
`save_snippet(name, steps)` / `delete_snippet(name)` 几个方法（目前还
没有对应的 CLI/工具封装，需要在 Python 代码里调用）。

---

## 自定义 Step 类型：插件化扩展（P7-④1/④2）

内置的 11 种 step 类型（`agent`/`role_agent`/`sub_workflow`/`tool_call`/
`human_input`/`script`/`skill_agent`/`python_step`/`foreach`/`wait`/
`merge`）之外，可以通过插件注册新的 step 类型，不需要改动本包源码。

**1. 实现一个 `StepExecutor`**：

```python
from mini_agent.workflow.executors import StepExecutor, register_step_executor

class HttpStepExecutor(StepExecutor):
    def execute(self, runner, step, prompt: str) -> str:
        # runner: 当前 WorkflowRunner 实例（可用 runner._effective_step_field()
        #         读取 defaults 合并后的字段、runner._cfg 读取全局配置）
        # step:   WorkflowStep 定义（自定义字段可以放 tool_args）
        # prompt: 占位符替换后的 prompt 文本
        ...
        return "该 step 的输出文本"

    def validate_step(self, step) -> list[str]:
        # 可选：自定义类型专属的必填字段校验，返回错误文案列表（空=合法）。
        # 内置 11 种类型的必填字段校验写死在 schema.py 里，不走这个钩子；
        # 只有通过 register_step_executor() 注册的自定义类型才会调用这里。
        errors = []
        if not (step.tool_args or {}).get("url"):
            errors.append(f"步骤 {step.id!r} 是 http 类型但未指定 url")
        return errors
```

**2. 通过 `myplugins/` 目录自动注册**：项目根目录下的
`myplugins/*.py`（文件名不以 `_` 开头）会在启动时被自动扫描、逐个
`import`，如果模块定义了顶层 `register(cfg)` 函数就会被调用——这是插件
的统一入口：

```python
# myplugins/my_http_step.py
def register(cfg):
    register_step_executor("http", HttpStepExecutor())
```

之后 workflow YAML 里就能直接写 `type: http` 了：

```yaml
- id: fetch
  type: http
  tool_args:
    url: "https://example.com/api/status"
  prompt: "（http 类型不使用 prompt，占位即可）"
```

仓库自带一个可参考的完整示例：`myplugins/example_http_step.py`（不需要
就直接删掉，不影响其它插件或核心功能）。

**已知边界**：
- 单个插件加载失败（import 报错）或 `register(cfg)` 调用失败，只会打印
  一条警告并跳过，不影响其余插件与主程序启动。
- `register_step_executor()` 覆盖内置 11 种类型的实现会打印警告但仍然
  允许（便于测试环境替换实现），生产场景不建议这么做。
- 自定义类型没有 `retry_on_gate_fail` 之外的内置安全默认值（不像
  `script`/`tool_call` 那样有专门的开关保护），插件作者需要自行评估
  该类型的风险面。

---

## 文件夹模式 Workflow：私有 Agent / Skill / Prompt 文件

（workflow_directory_mode_design.md）复杂 workflow 可以组织成一个文件夹，
携带只属于自己的 agent profile、skill、prompt 模板文件，与单文件模式
完全向后兼容、可共存。

### 目录结构

```
.agent/workflows/
  code_review.yaml            单文件模式（原有，继续可用）
  my_pipeline/                 文件夹模式（新增）
    workflow.yaml              主入口，字段结构与单文件模式一致
    agents/
      reviewer.md              工作流私有 agent profile（同 .agent/agents/*.md 格式）
    skills/
      pdf-diff/
        SKILL.md                工作流私有 skill（同 .claude/skills 目录格式）
    prompts/
      analyze.md                抽出来的 prompt 模板文件
```

用 `/workflow to-dir <name>` 把已有的单文件工作流一键升级为文件夹模式
（自动创建 `agents/`、`skills/`、`prompts/` 空目录，原 YAML 移入
`workflow.yaml`，删除旧的单文件）。

> 仓库里带了一个可以直接运行的完整示例：
> `.agent/workflows/doc_change_review/`，一个"文档变更审查"流水线，
> 覆盖了本节讲到的全部能力——`prompt_file`（`prompts/*.md`）、
> `type: role_agent` 调用私有 agent（`agents/reviewer.md`）、
> `type: skill_agent` 调用私有 skill（`skills/changelog-diff/SKILL.md`），
> 以及一个普通的 `type: agent` 主 Agent step。可以用
> `/workflow run doc_change_review` 结合 `{old_path}`/`{new_path}` 两个
> 文档路径直接跑起来，也可以照着它的结构改成自己的 workflow。

### Prompt 文件引用（`prompt_file`）

step 里的 `prompt` 字段可以换成 `prompt_file`，值是相对 workflow 所在
目录的相对路径：

```yaml
steps:
  - id: analyze
    name: 静态分析
    prompt_file: prompts/analyze.md   # 相对路径，便于连同文件一起迁移项目
```

- `prompt_file` 与 `prompt` 二选一，都填时 `prompt_file` 优先。
- 加载时会把文件内容读出来填充到运行时的 `step.prompt`；保存
  （`to_dict`/YAML 序列化）时只写 `prompt_file`，不会把展开后的文本重复
  写回 YAML——编辑 prompt 文件、迁移项目都只需要处理这一份文件。
- `prompt_file` 指向的文件不存在时，加载阶段会打印警告，`step.prompt`
  保留为空串（不会阻断该工作流的查看/编辑），执行到该 step 时会用空
  prompt 运行，建议尽快补上文件。

### 直接调用本地 Agent / Skill

- **调用本地 agent**：跟调用全局角色 Agent 写法一样，`type: role_agent` +
  `role: <name>`。工作流有 `agents/<name>.md` 时优先匹配这个本地文件，
  没有则退回全局 `.agent/agents/` 目录里同名的 profile。

  ```yaml
  - id: review
    type: role_agent
    role: reviewer          # 优先匹配 my_pipeline/agents/reviewer.md
    prompt_file: prompts/review.md
  ```

- **调用本地 skill**：新增 `type: skill_agent`，指定 `skill_name`，会
  临时启动一个只强制挂载该 skill（不走关键词触发判断）的最小 Agent 执行
  该 step 的 prompt。优先匹配工作流 `skills/` 目录，没有则退回全局
  `skills_dir`。

  ```yaml
  - id: diff_check
    type: skill_agent
    skill_name: pdf-diff
    prompt: "对比这两份 PDF 的差异：{a_path} vs {b_path}"
  ```

### `result_file` 结构化结果契约：`skill_agent` / `script`（及声明了 `result_file` 的 `agent`/`role_agent`）

`skill_agent` 这类步骤本质是"临时起一个完整 Agent 自主跑若干轮工具调用"，
它的对话输出是非结构化文本，直接靠 `{step_id.output}` 占位符或
`ctx.input_output()` 给下游 `python_step` 用并不可靠（模型经常在 JSON 前后
夹杂解释文字）。需要下游按结构化数据消费时，给该 step 声明：

```yaml
- id: search_zhihu
  type: skill_agent
  skill_name: browser-cdp
  prompt_file: prompts/02_search_zhihu.md
  output_file: search_results.json        # 仍保留：对话原文存档，供人工排查
  result_file: search_results_data.json   # 真正被下游消费的结构化产物
  result_file_required_keys: [questions]  # 校验：JSON 必须包含这些顶层字段
  max_turns: 25
  timeout: 900
```

- **`result_file`**：一个文件名（相对本次 workflow session 的 `output/` 目录）。
  声明后，runner 会在 prompt 末尾自动追加一段指令，明确告诉 agent"最终结果
  必须用文件写入工具写到这个绝对路径，不是靠对话回复交付"，并在 agent 这
  一轮结束后校验该文件是否存在、是否是合法 JSON。
- **`result_file_required_keys`**：校验文件内容必须包含的顶层字段列表，
  缺失任一字段都算校验失败。
- **失败重试预算**：校验失败不会立刻判定 step 失败，而是：
  1. 先原地 `resume` 同一个 agent（沿用其上下文/浏览器状态）最多 3 次，
     直接点名"你没写文件"；
  2. resume 仍救不回来，整个重开一个全新 agent 从头再来，最多 3 次；
  3. 两轮预算都耗尽仍未产出合法结果文件，才判该 step 失败，交给
     `retry_on_error` 机制重跑整个 step。
- **`ctx.input_json(step_id)` / `ctx.input_output(step_id)` 会优先读
  `result_file`**：下游 `python_step` 里这两个便捷方法，如果上游 step 声明
  了 `result_file` 且校验通过，会优先读文件内容而不是对话原文，因此凡是
  `skill_agent` 的产出要喂给下游 `python_step` 做结构化处理，都应该配一个
  `result_file`，不要指望从 `output` 文本里解析。
- **别忘了在 prompt 里也提醒 agent"写完文件后立即收尾"**：`result_file`
  校验只在 agent 这一轮**自然结束后**才执行，如果 agent 写完文件后继续做
  额外的浏览、反复自我确认，即使结果早就写好了，step 也要等它自己主动
  结束这一轮才会往下走——已知会明显拖慢整体执行时间。生成 prompt 时建议
  显式加一句"确认文件写入且内容合法后立即用一句话收尾，不要再进行任何
  与写文件无关的操作"。

- **主 Agent（`type: agent`）执行的 step**：执行期间会自动带上该工作流的
  本地 `skill_loader` 和 `agent_profile_loader`，因此主 Agent 在该 step
  内通过 `spawn_named_agent` 能看到本地 `agents/` 里定义的 sub-agent，
  技能触发 / `skill_activate` 工具也能看到本地 `skills/` 里定义的 skill，
  不需要额外配置。

#### `script` 的 structured 模式（P15）

`result_file`/`result_file_required_keys` 不是 `skill_agent` 专属——`script`
类型也可以声明它们，用**环境变量**而不是 prompt 文字告知目标路径：

```yaml
- id: count_lines
  type: script
  script: |
    python3 -c "
    import os, json
    total = sum(1 for _ in open('data.csv'))
    json.dump({'total': total}, open(os.environ['WORKFLOW_RESULT_FILE_PATH'], 'w'))
    "
  result_file: count_result.json
  result_file_required_keys: [total]
```

- 声明了 `result_file` 时，`ScriptStepExecutor` 会先算出目标绝对路径，
  通过环境变量 `WORKFLOW_RESULT_FILE_PATH` 传给子进程，脚本内自行读取
  该环境变量、把结构化结果写成合法 JSON 落到这个路径。
- 子进程 `returncode == 0`（脚本自身判定的"成功"）之后，还会额外校验
  `result_file`——**校验不通过时，即使 `returncode == 0` 也判定整个 step
  失败**，抛异常交给外层既有的 `retry_on_error`/`NEEDS_FIX` 机制处理。
- 与 `skill_agent` 的区别：`script` 是一次性子进程，没有可 `resume` 的
  会话上下文，所以校验失败**不会**像 `skill_agent` 那样原地补写重试，
  只能整个重跑该 step（配 `retry_on_error` 使用）。
- **未声明 `result_file` 时行为完全不变**（`stdout` 即结果），向后兼容
  所有既有 `script` step，不强制迁移。
- 与 `skill_agent`/`foreach`/`merge` 一样，下游 `python_step` 用
  `ctx.input_output(step_id)`/`ctx.input_json(step_id)` 消费时不需要关心
  上游到底是哪种类型产出的 `result_file`，用法完全统一。

### 边界与限制

- 子工作流（`type: sub_workflow`）**不继承**父工作流的本地资源包：被引用
  的子工作流若自己是文件夹模式，按它自己的目录解析本地资源；若是单文件
  模式，则完全不使用本地资源，行为与改动前一致。这是为了避免跨工作流的
  隐式资源泄漏。
- 单文件模式的工作流（`source_dir` 为 `None`）完全不受本节功能影响，
  `role`/`prompt`/`type` 等字段的既有行为保持不变。

---

## 生命周期 Hook（P5）

Workflow 执行时会触发以下 Hook 事件（复用项目现有的 `mini_agent.hooks` 体系，
在 `.agent/hooks.json` 里声明命令即可挂钩，无需改动源码）：

| 事件 | 触发时机 |
|---|---|
| `WorkflowStart` | 一次 `run()` 开始执行 |
| `WorkflowStepStart` | 每个 step 实际开始执行前 |
| `WorkflowStepEnd` | 每个 step 执行结束后（无论成功/失败/跳过） |
| `WorkflowGateFailed` | evaluator 质检门判定 `GATE_FAILED` 时 |
| `WorkflowEnd` | 一次执行结束（`done`/`failed`/`partial`/`paused`/`cancelled`） |

受 `workflow.hooks_enabled` 开关控制（默认开启）；Hook 触发失败不会影响
工作流主流程（异常会被吞掉并记录日志）。

---

## 保存前引用完整性校验（P6）

`save_workflow` / `create_workflow_from_template` 保存前会调用
`WorkflowDef.validate()`，除了原有的 id 重复/依赖缺失检查，新增：

- **类型专属必填字段**：`sub_workflow` 缺少 `workflow_name`、`tool_call`
  缺少 `tool_name`、`script` 缺少 `script` 命令都会被拒绝；`sub_workflow`
  引用自身（导致无限递归）也会被拒绝。插件通过 `register_step_executor()`
  注册的自定义类型（见"自定义 Step 类型：插件化扩展"一节）不走这套写死
  的内置检查，改为调用对应 `StepExecutor.validate_step(step)`，由插件
  自己定义必填字段规则。
- **占位符引用完整性**（受 `workflow.validate_placeholders_on_save` 控制，
  默认开启）：扫描 `prompt` 中 `{step_id.output}` / `{step_id.score}` 形式
  的占位符，检查 `step_id` 是否真的存在于工作流里，避免笔误导致运行时
  才发现引用了不存在的步骤。`{param_name}` 这种不带 `.` 的占位符属于运行时
  `inputs`，不受此项检查。
- **角色引用校验**（受 `workflow.validate_role_refs_on_save` 控制，默认
  开启）：校验 `role` 字段是否为已注册的角色 Agent profile（需要调用方
  传入 `role_checker`，未传入时自动跳过，不影响单测/无 dispatcher 环境）。

---

## 内置工作流模板库（P6）

不想从零手写 YAML 或依赖 LLM 生成时，可以直接基于内置模板创建：

```
/workflow templates                          列举内置模板
/workflow from-template code_review my_review   基于模板创建新工作流
```

对应 Agent 工具：`list_workflow_templates()` / `create_workflow_from_template(template_name, new_name)`。

当前内置模板：

| 模板名 | 说明 |
|---|---|
| `code_review` | 静态分析 → 深度审查 → 质量评估（evaluator 打分门）→ 生成报告 |
| `research_report` | 资料收集 → 要点提炼 → 交叉验证（evaluator 打分门）→ 生成报告 |
| `multi_perspective_debate` | 正方/反方论证并行展开 → 综合裁决（evaluator 打分门）→ 生成结论 |

模板本身只是随包分发的只读 YAML（`workflow/templates/*.yaml`），
`create_workflow_from_template` 只是把模板加载出来、替换 `name` 字段后
交给普通的 `save()` 落盘——会经过和手写 YAML 完全一样的校验路径。

---

