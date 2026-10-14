# cancel_task 硬取消：真正中断 bash 与进行中的 LLM 请求

接续 `next_doc/subagent_cancel_status_bugfix.md`「已知局限」的第一条。

## 1. 问题

此前 `cancel_task` 是**协作式取消**：状态立即变为 `cancelled`，但执行线程只在
`_agentic_loop` 每轮迭代顶部检查取消信号，因此：

- 进行中的 LLM 请求要等它返回才会停；
- 进行中的 bash 命令要等它自己跑完（或超时）才会停，期间仍可能写文件、跑命令；
- 线程退出前，该任务仍占用一个并发槽位。

## 2. 方案

新增**任务级取消令牌** `CancelToken`（`utils/cancel_scope.py`），在原有取消 Event 之上
增加"被取消时主动执行的 killer 列表"。阻塞型 I/O 开始前登记 killer，`cancel()` 时立即执行，
而不是等它们自然结束。令牌通过 thread-local 传递（与 `llm/deadline.py` 同一风格），
不需要层层改函数签名。

| 环节 | 取消时的行为 |
|---|---|
| bash（流式 / 非流式两条路径） | 对整个进程组 SIGKILL（Windows 为 `taskkill /T /F`），孙子进程一并杀掉；返回已产生的输出 + `[cancelled by cancel_task — partial output above, process killed]` |
| bash 尚未启动时已取消 | 不启动子进程，直接返回 `[cancelled by cancel_task — command was not started]` |
| LLM 请求（chat / stream，所有走 `ProviderMixin` 的 provider） | 请求放入 daemon 工作线程，调用线程立即抛 `TaskCancelled`，不再等 LLM 返回；流式请求在下一个 token 到达时抛出，让 SDK 的流式 context manager 退出并关闭连接 |
| 取消发生时 LLM 并发槽位 | 调用线程抛出时随 `with sem.acquire` 立即释放，不再被挂死的请求占着 |

`TaskCancelled` 继承 **`BaseException`**（而不是 `Exception`）：LLM 重试层、`client_pool`
的 fallback 轮换、`SubAgent._run_with_capture` 的瞬时错误重试等处的 `except Exception`
都不会把"用户主动取消"当成可重试/可降级的失败而继续发起新请求。只有 `SubAgent._run_body`
明确捕获它并保持 `CANCELLED` 终态。

## 3. 改动文件

| 文件 | 改动 |
|---|---|
| `src/mini_agent/utils/cancel_scope.py`（新增） | `CancelToken` / `cancel_scope` / `current_token` / `TaskCancelled` |
| `src/mini_agent/tools/builtin.py` | `bash()` 入口检查已取消；非流式、流式两条路径都登记 killer、取消后返回部分输出 + 标记 |
| `src/mini_agent/llm/providers/_base_mixin.py` | 新增 `_call_cancellable()`；`_traced_chat` / `_traced_stream` 通过它调用 provider；流式 `on_token` 取消后抛出 |
| `src/mini_agent/orchestrator/sub_agent.py` | 持有 `CancelToken`（复用原 `_cancel_event`）；`cancel()` 改为 `token.cancel()`；执行线程用 `cancel_scope` 绑定令牌；`_run_body` 捕获 `TaskCancelled` 保持 `CANCELLED` |
| `tests/test_cancel_hard_interrupt.py`（新增） | 19 个用例，见下 |
| `docs/commands-and-tools-reference.md`、`next_doc/subagent_cancel_status_bugfix.md` | 同步更新 `cancel_task` 的行为描述与"已知局限" |

## 4. 保守的默认值（opt-in）

令牌只在 `SubAgent` 执行线程内绑定。主 Agent、cron、Objective、后台判定等**一切没有令牌的路径**，
`current_token()` 返回 `None`，bash 与 LLM 调用完全走旧逻辑（不起额外线程、不多一次判断以外的开销）。
没有新增任何配置项。

## 5. 测试

`tests/test_cancel_hard_interrupt.py`：

- `CancelToken`：killer 执行 / 已取消时登记立即执行 / 注销 / killer 异常不影响取消；`TaskCancelled` 不被 `except Exception` 吞掉。
- bash：`sleep 20` 在取消后 8 秒内返回、保留已输出内容、事后 marker 文件不存在（进程确实被杀）；孙子进程一并被杀；已取消时不启动；无令牌时行为不变（流式与非流式各一套）。
- LLM：无令牌直通；有令牌时快速结果 / 异常正常传递；取消后不等慢请求返回；已取消时不调用 impl。
- SubAgent：`cancel()` 置位令牌并执行 killer。

另用本地"故意 15 秒才响应"的假 OpenAI 服务做过端到端验证：chat 与 stream 两条路径取消后都在取消发起的瞬间返回 `TaskCancelled`。
回归：`test_llm*`、`test_orchestrator*`、`test_retry`、`test_sub_agent_experience`、`test_subagent_inheritance`、
`test_tick_dispatch_phase4_guard_and_deadline`、`test_cron_*`、`test_concurrency` 等共 370+ 个用例通过。

## 6. 仍存在的局限

- **LLM 请求线程无法强杀**：被放弃的请求线程会在请求自然结束 / 触达超时后自行退出，结果丢弃；
  期间这个 HTTP 请求在服务端仍可能继续（并产生计费）。流式请求在下一个 token 到达时会主动断开。
- 取消发生在"排队等待 LLM 并发槽位 / RPM 限速"阶段时，仍要等该等待返回；等待受 `_slot_wait_limit` 约束，本次未改。
- 仅覆盖 `cancel_task`（SubAgent）。看板对 Objective / cron job 的"终止"走各自的取消链路，未接入令牌，
  行为不变；如需同样的硬中断，可在其执行线程外层同样用 `cancel_scope` 绑定令牌（bash 与 LLM 侧已就绪）。
- 除 bash 外，`tools/` 下没有其它直接起子进程的工具；其它网络类工具（如 web 抓取）的 I/O 未接入令牌。
