# Mini-Agent Workflow 机制改进方案

> 基于对 `src/mini_agent/workflow/`（schema.py / runner.py / executors.py / watchdog.py / tools.py / api_helpers.py）现状的分析，围绕**有效性、可扩展性、可控性**三个目标，梳理具体改进方向。文中标注"现状"的部分是仓库里已经实现、不需要重造的能力，标注"建议新增/修复"的部分是本次分析发现的缺口。

## 实施状态

本文档描述的改动**已在本次会话中落地实现并通过全部相关单元测试**（`tests/test_workflow_*.py`，86 + 12 用例全部通过），具体改动文件与状态如下：

| 文件 | 改动内容 | 对应章节 |
|---|---|---|
| `schema.py` | `WorkflowDef.mode`（autonomous/interactive）+ 保存期校验；`WorkflowStep.input_key`；`StepStatus.NEEDS_FIX` | §1、§4.3 |
| `executors.py` | `HumanInputStepExecutor` 优先消费 `input_key` 对应的预置输入，命中则不阻塞 | §1 |
| `runner.py` | `run()` 新增 `force_serial` 参数并落地为批次拍平；`to_summary()` 补齐非成功状态的错误信息；`_execute_step_with_error_retry` 识别结构性错误直接判 `NEEDS_FIX`、跳过无效重试 | §2、§4.1、§4.3 |
| `api_helpers.py` | `start_workflow_run` 新增 `force_serial`/`require_all_inputs_upfront` 参数与启动前输入完整性检查；阻塞型 `human_input` 自动强制转后台 | §1、§2 |
| `tools.py` | `run_workflow` 暴露 `force_serial`/`require_all_inputs_upfront`；`resume_workflow_run` 暴露 `force_rerun_from`；`get_workflow_run_status` 新增错误信息输出、`verbose`、`wait`（阻塞式看护）参数；新增 `patch_workflow_step` 单步编辑工具 | §1、§2、§3、§4.1、§4.2 |
| `prompts/reminders/workflow_run_failed.md` | 新增 reminder：`run_workflow`/`resume_workflow_run`/`get_workflow_run_status` 输出命中失败/needs_fix 时，主动提示"验证错误 → patch → force_rerun_from"处理顺序 | §4.4 |

**本轮补充完成**：`system_events` 事件驱动的完成通知（workflow 进入终态时发布 `workflow.<status>` instant 事件）；发现全局串行开关本应复用已存在的 `cfg.workflow.parallel_enabled` 字段（此前实现里误引用了一个不存在的新字段名，已修正，未引入重复配置项）；新增 `workflow_run_failed.md` reminder，在 `run_workflow`/`resume_workflow_run`/`get_workflow_run_status` 的工具输出命中失败/needs_fix 状态时，主动提醒按 `get_workflow_run_status(verbose=True)` → `patch_workflow_step` → `resume_workflow_run(force_rerun_from=...)` 的顺序处理，不依赖主 Agent 自己记得这套 SOP。

**尝试过、已回退的方向（记录风险，供后续参考）**：曾在 `turn_loop.py` 每轮用户消息进入时接入一个 `system_events` 消费者，把 `workflow.failed`/`workflow.partial` 事件自动转成对话内提醒。实现后跑全量测试（2000+ 用例，与改动前基线逐条 diff）发现会引入一个真实回归（`tests/test_undo.py::TestRetryLastTurn::test_retry_calls_llm_again_with_same_message`）——根因是多个测试共享同一个 `project_root=/tmp`，读到了其他测试残留在 `/tmp/.agent/system_events.jsonl` 里不相关的事件，说明这个消费者依赖 `project_root` 目录的"干净性"，存在跨会话/跨项目污染的脆弱性风险。已完整回退该改动（不影响其余已实现功能），事件**发布**这一半（runner.py 里 workflow 进入终态时 publish 到 system_events 总线）予以保留，因为它本身只是旁路写入、不影响对话内容，后续若要重新做消费端，需要先解决"如何确保只消费本次会话/本项目产生的事件"这个问题（比如按 `source` 前缀或workflow_session_id 白名单过滤，而不是消费整个总线）。

**尚未实现（后续可继续推进的方向）**：上述事件消费重新设计后接入主循环。`docs/workflow-guide.md` 已同步更新（mode/input_key 字段说明、force_serial/require_all_inputs_upfront 参数、patch_workflow_step 工具、出错定位与重跑一节）。这些不影响本次已实现功能的可用性，属于后续增量。

---

## 0. 现状总结

现有 workflow 机制已经具备的能力，作为后续建议的基础：

- **Step 类型化**（`schema.py`）：`agent / role_agent / sub_workflow / tool_call / human_input / script / skill_agent`，并支持插件通过 `register_step_executor()` 注册自定义类型。
- **依赖与并行**：`runner._compute_parallel_batches()` 用分层 Kahn 算法把 DAG 切成"批次"，同批次内多线程并发；`allow_parallel` 可在单个 step 上关闭。
- **看护线程**（`watchdog.py`）：独立后台线程做心跳超时检测 + 累计时长/token 护栏，**本身不消耗 LLM token**，是纯脚本化实现，这部分做得对。
- **人工介入两套机制**：`require_approval`（审批门）与 `type=human_input`（要输入），两者的阻塞等待都发生在工作流自己的后台线程里，同样不占 LLM token。
- **错误信息与断点续跑的底层数据结构**：`StepResult` 记录 `error / error_type / traceback / context`；`api_helpers.resume_workflow_run(force_rerun_from=...)` 支持从某一步续跑；`override_step_output()` 支持人工改写某个已完成 step 的输出。
- **两套"机器自愈"机制**：`retry_on_error`（瞬时故障指数退避重试自己）与 `retry_on_gate_fail`（evaluator 判不达标后带反馈重跑依赖 step）。

结论：**底层状态机相对完整，主要缺口集中在"主 Agent 会话层面与后台 workflow 之间的交互协议"**——包括中途要输入、并行控制、看护轮询、错误闭环这四类问题，具体如下。

---

## 1. 全自动执行：避免 workflow 中途要求用户输入 ✅ 已实现

**问题**：`human_input` 类型 step 和 `require_approval: true` 都会真的阻塞等外部输入。如果一个 workflow 被设计为"全程自动、所有输入在最初已给全"，现在唯一的保护是"YAML 里干脆不写这两种东西"——这是**约定**而非**机制**，容易在 YAML 由 LLM 生成或他人分享时被意外带入，导致后台执行卡死到超时才失败。

**建议**：

1. **`WorkflowDef` 增加显式 `mode` 字段**（`mode: autonomous | interactive`，默认 `interactive` 保证向后兼容）。`autonomous` 模式下：
   - `validate()` 阶段直接把包含 `human_input` 或 `require_approval: true` 的 step 判为**校验错误**，而不是等运行时才卡住——在设计期拦截，而不是靠自觉。
   - `run_workflow(background=False)` 前台同步执行时，若检测到 `interactive` 模式且含阻塞型 step，应提前报错提醒，而不是仅靠文档说明"必须用 background=True"。

2. **`human_input` 增加 `input_key` 字段**：启动时若 `inputs` 字典里已能通过该 key 找到对应值，直接填充使用，不进入阻塞等待；只有既没有 `input_key` 命中、又不是 `autonomous` 模式时才真正阻塞等待。这样同一份 YAML 既能交互式跑，也能被"所有参数最初一次性传完"的自动化方式复用，不用维护两份工作流。

3. **`run_workflow` 增加运行时开关 `require_all_inputs_upfront: bool`**：开启后，runner 在真正开始执行前做一次静态扫描——凡是 `human_input` 且没有对应 `input_key` / 未能从 `inputs` 解析到值的 step，直接判校验失败并返回"缺少哪些字段"的清晰提示，把阻塞点从"运行时才暴露"提前到"启动前一次性检查"。

---

## 2. 增加"全部串行"开关，避免并行 ✅ 已实现

**问题**：`allow_parallel` 目前只能挂在单个 step 上，想让整个 workflow 串行，需要每个 step 都手写 `allow_parallel: false`，容易遗漏。

**建议**：提供三层可控性，从细到粗：

| 粒度 | 位置 | 说明 |
|---|---|---|
| 单 step | `WorkflowStep.allow_parallel` | 现状已支持 |
| 单次运行 | `run_workflow` 新增参数 `force_serial: bool = False` | 忽略 `_compute_parallel_batches()` 的分层结果，退化为 `_topological_sort()` 的扁平顺序单线程执行；不改 YAML 本身，适合调试/临时资源受限场景 |
| 全局 | `agent_config.json` 新增 `workflow.parallel_execution_enabled`（默认 true） | 运维层面一键关闭所有 workflow 的并行 |

另需确认 `WorkflowDef.defaults.allow_parallel: false`（workflow 级默认值）在 `runner._effective_step_field()` 的三层查找链路（`step 字段 → defaults → 硬编码兜底`）中确实生效，避免文档写了但代码没接通。

---

## 3. 看护机制：把"等待"从 LLM 轮次里挪出去 ✅ 已实现

**问题澄清**：`watchdog.py` / `_await_step_approval()` / `HumanInputStepExecutor` 里的 `time.sleep` 都运行在**工作流自己的后台线程**中，不产生任何 LLM 调用，这部分本来就是"脚本化"的，不需要改。真正浪费 token 的是**主 Agent 会话层面**——目前只有 `get_workflow_run_status` 这个一次性快照工具，没有阻塞等待原语。要"看护"一个后台 workflow，主 Agent 只能自己反复决定"再查一次"，每次查询都是一整轮 LLM 推理（带全部上下文），这才是真正烧 token 的地方。

**建议**：

1. **新增阻塞式工具 `wait_workflow_run(workflow_session_id, timeout=None, poll_interval=3.0)`**：在**这一次工具调用内部**用普通 Python 循环轮询 `WorkflowSession.load()`，直到终态或超时才返回。对主 Agent 而言只消耗**一次**工具调用/一轮 token，无论背后实际等了多久。也可以直接给现有 `get_workflow_run_status` 加 `wait: bool` 参数复用同一工具入口，减少工具面数量，默认 `False` 保持向后兼容。

2. **事件驱动、零轮询开销的方案**：复用仓库已有的 `perception/system_events.py` 事件总线（`tier="instant"` 语义）。workflow 进入终态（done/failed/awaiting_approval/human_input）时主动 `publish()` 一条事件；主 Agent 不需要主动等待，正常处理其他事务，等下一次经过既有的事件检查节拍（现有机制的"顺便查一下"，接近零开销）时自动把"workflow_X 已完成/失败"作为提醒注入下一轮上下文。

3. **两种方案搭配使用**：短时任务（几秒到几十秒能跑完）用方案 1 的阻塞 `wait_workflow_run`；长时间后台任务（几分钟到几十分钟）用方案 2 的事件通知，发起 `run_workflow(background=True)` 后主 Agent 直接把控制权还给对话，完成时被动收到提醒，全程不占"干等"的 token。

---

## 4. 出错闭环：主 Agent 知情 → 定位 → 修改 → 重跑 ✅ 已实现

**问题**：底层数据（`StepResult.error/error_type/traceback/context`）和状态机（`force_rerun_from`、`override_step_output`）已经比较完整，但**没有完全暴露给主 Agent**，导致"出错→自动闭环处理"这条链路目前是断的。具体三个缺口：

### 4.1 状态查询不显示错误信息

`get_workflow_run_status`（`tools.py`）当前实现完全没有读取 `sr.error`，只输出 `step_id: status（耗时 评分 重试）`。后台 workflow 失败后主 Agent 查到的只有 `xxx: failed`，不知道原因，只能去翻原始 session 文件。

前台同步执行的 `WorkflowRunResult.to_summary()` 同样只在 `status == FAILED` 时打印 error，`GATE_FAILED / TIMEOUT / CANCELLED / REJECTED` 均未打印错误信息，而这几种状态同样需要错误信息才能决定下一步动作。

**修复**（数据已落盘，只是没输出，改动成本低）：

```python
for step_id, sr in s.step_results.items():
    ...
    if sr.status in (StepStatus.FAILED, StepStatus.TIMEOUT,
                      StepStatus.GATE_FAILED, StepStatus.REJECTED) and sr.error:
        lines.append(f"  ⚠️ {sr.error_type or 'Error'}: {sr.error}")
```

同时给 `get_workflow_run_status` 增加 `verbose: bool = False` 参数，开启后把 `context`（step 配置、prompt 预览）与 `traceback` 一并输出，默认关闭以控制 token 成本，需要深挖时由主 Agent 自行决定。

### 4.2 无法单独修改一个 step 的定义

当前唯一能改工作流定义的入口是 `save_workflow(yaml_content)`，要求把整份 YAML 重新贴一遍再保存——对多步骤 workflow 而言，只想改一个 step 的 prompt/timeout 也要重贴全文，费 token 且容易误改其他地方。同时 `force_rerun_from` 只存在于 `api_helpers.resume_workflow_run()` 内部，没有透传到 `@tool` 层，主 Agent 拿不到这个参数。

**建议新增两个工具**：

```python
@tool(name="patch_workflow_step", group="workflow",
      description="只修改某个已保存 workflow 里指定 step 的部分字段（prompt/timeout/model/...），"
                  "不用重贴整份 YAML。")
def patch_workflow_step(name: str, step_id: str, patch: str) -> str:
    # patch: JSON 字符串，如 '{"prompt": "...", "timeout": 120}'
    ...

@tool(name="resume_workflow_run", ...)
def resume_workflow_run(workflow_session_id: str, background: Optional[bool] = None,
                         force_rerun_from: Optional[str] = None) -> str:
    # 补上该参数，直接透传给 api_helpers.resume_workflow_run
    ...
```

配合 4.1 的错误信息，形成完整闭环：主 Agent 看到某 step 失败原因 → `patch_workflow_step` 修正该 step 的定义（改动会保留在工作流本体里，后续所有执行都受益）→ `resume_workflow_run(force_rerun_from=该step)` 只重跑这一步及其下游，已成功、已消耗 token 的前序步骤不用重来。

### 4.3 重试机制分不清"重试有用"和"重试没用"的失败

`retry_on_error` 目前对所有 `FAILED` 一视同仁地重试。但有一类失败本质是**定义/配置错误**（prompt 占位符引用了不存在的 step、`tool_name` 未注册、`prompt_file` 路径不存在、`script` 命令语法错误等），这类错误重试 N 次结果完全一样，纯粹浪费时间和 token；更重要的是，这类错误恰恰是"需要主 Agent 修改定义"的信号，不应被当作瞬时故障悄悄重试后仍以模糊的 `FAILED` 收场。

**建议**：在 `_execute_step_with_error_retry` 中按 `error_type` 分类，对结构性异常（如 `KeyError` / `FileNotFoundError` / 工具未注册等）跳过 `retry_on_error` 重试逻辑，直接标记一个新状态（如 `NEEDS_FIX`），摘要中明确提示"这是定义问题，重跑无效，请先 `patch_workflow_step` 修改后再用 `force_rerun_from` 续跑"，让主 Agent 无需读 traceback 猜测即可判断处理路径。

### 4.4 主动通知，闭环体验

结合第 3 节的看护机制建议：workflow 后台执行进入 `FAILED / PARTIAL / NEEDS_FIX` 终态时，除了被动等待 `get_workflow_run_status` 查询，也应主动向 `system_events` 总线或 reminder 机制（复用仓库已有的 `prompts/reminders/` "检测到某类问题即注入提示"模式）推送一条事件/提醒，内容包含错误摘要与建议处理路径（"可用 `patch_workflow_step` + `resume_workflow_run(force_rerun_from=...)` 处理"），让主 Agent 在下一轮自然获知，而不必依赖其自行记得这套 SOP。

---

## 5. 汇总表

| 方向 | 关键改动点 | 备注 |
|---|---|---|
| 全自动禁止中途要输入 | `schema.py`(`WorkflowDef.mode`) + `validate()` + `human_input.input_key` + `run_workflow(require_all_inputs_upfront)` | 校验期拦截，输入统一在启动时给全 |
| 强制串行 | `run_workflow(force_serial)` + `agent_config.json`(`workflow.parallel_execution_enabled`) | 与已有的 step 级 `allow_parallel` 组成三层粒度 |
| 看护更高效 | 新增/复用 `wait_workflow_run` + `system_events` 完成通知 | 把轮询挪进单次工具调用或事件总线，`watchdog.py` 本身无需改动 |
| 出错信息透明 | `get_workflow_run_status` / `to_summary()` 输出 `error/error_type`，加 `verbose` 参数 | 数据已落盘，纯输出层修复 |
| 单步编辑重跑 | 新增 `patch_workflow_step` 工具 + `resume_workflow_run` 补 `force_rerun_from` 参数 | 底层 `api_helpers` 已支持，缺的是工具层透传 |
| 失败分类 | `_execute_step_with_error_retry` 区分结构性错误 vs 瞬时错误 | 避免对必然失败的配置错误做无意义重试 |
| 失败主动通知 | 结合 `system_events`/reminder 机制 | 与"看护机制"共用同一套基础设施 |
