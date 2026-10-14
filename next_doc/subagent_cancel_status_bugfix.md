# SubAgent 取消后状态栏仍显示 running（bugfix）

> 纯缺陷修复记录，不构成功能设计变更。当前行为见 [docs/subagent-mechanism.md](../docs/subagent-mechanism.md) 6.5 节。

## 现象

调用 `cancel_task`（或 `/tasks cancel`）后工具返回 `{"cancelled": true}`，但状态栏的
`⚡ Tasks [...] N running` 以及 task tab 仍显示该任务在执行。

## 根因

1. `SubAgent.cancel()` 对 `RUNNING` 任务只执行 `_cancel_event.set()`，**不修改状态**；
   只有 `PENDING` 任务才会被置为 `CANCELLED`。
2. 该 `cancel_event` 只在 `run_turn()` **返回之后**才被检查（`_run_body` 里决定写 `CANCELLED`
   还是 `DONE`），而 `Agent._agentic_loop` 内部完全没有取消检查点。因此任务要一直跑到
   自然结束（可能是几十轮 LLM + 工具调用之后）状态才会变化。
3. 状态栏通过 `TaskManager.stats()` 按 `TaskRecord.status` 统计，状态没变，显示自然不变。
4. 取消过程中若抛出异常，`except` 分支会把状态改成 `FAILED`，取消语义丢失。

`cancel_task` 的返回值只表示「已发送取消信号」，与「任务已停止」语义不一致，属于误导。

## 修复

| 文件 | 改动 |
|---|---|
| `orchestrator/sub_agent.py` | `cancel()` 对 `RUNNING` 立即置 `CANCELLED`、写 `finished_at` 与 `result.error`；构建 Agent 后注入 `agent._cancel_check = cancel_event.is_set`；超时/异常分支在已取消时保持 `CANCELLED`；`finally` 不覆盖已记录的 `finished_at` |
| `agent/core.py` | 新增 `Agent._cancel_check`（默认 `None`，主 Agent 行为不变，保守的 opt-in） |
| `agent/turn_loop.py` | `_agentic_loop` 每轮迭代顶部检查 `_cancel_check`，为真则直接返回，不再发起新的 LLM 调用 / 工具执行 |
| `tests/test_orchestrator.py` | 新增 `test_cancel_running_becomes_cancelled_immediately`、`test_agentic_loop_stops_when_cancel_check_true` |

## 已知局限（未处理）

- 进行中的单次 LLM 请求 / 工具调用（如长时间 bash）无法被强制中断，线程需等其返回后退出，
  期间仍占用一个并发槽位。若需要硬中断，需要在 LLM 客户端 / 工具执行层引入可取消 I/O，属于单独的改进。
- 排队中（尚未获得并发槽位）即被取消的任务，线程退出时不会走 `finally` 的终态通知（`SubagentStop` hook），
  此行为是修复前就存在的，本次未改动。
