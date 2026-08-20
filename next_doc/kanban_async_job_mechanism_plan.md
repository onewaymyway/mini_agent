# 看板 LLM 调用点的通用异步任务机制

## 背景 / 问题

看板上所有涉及 LLM 调用的按钮——生成执行规范草稿（`execution_spec/generate`）、
补充意见重新生成（`execution_spec/revise`）、手动重判整体是否可以关闭
（`execution_spec/close_check`）、成长顾问"立即为我看看"（`growth/scan`）、
候选"采纳"（`growth/candidates/{id}/accept`）、报告"刷新"
（`growth/candidates/{id}/report/refresh`）——过去都是同一个反模式：

1. `async def` 路由 handler 里直接同步调用 LLM/Agent 逻辑（`build_draft()`
   这类函数是普通 `def`，在协程里被直接调用会整段阻塞 FastAPI 唯一的事件
   循环，拖垮 daemon 对其它请求的响应能力——不只是"这个按钮体验差"）。
2. 前端各自猜一个超时时间（15s 默认，`growth/scan`/`growth/candidates/
   {id}/accept` 手动加大到过 90s），猜大了用户要在原地干等，猜小了直接
   超时失败——而且不管猜多大，LLM/Agent 探索路径的实际耗时是分钟级的、
   不可预测的，任何固定超时早晚都会不够用。

## 方案

**后端**：新增通用异步任务 registry（`src/mini_agent/api/async_jobs.py` 的
`AsyncJobRegistry`），POST 接口不再同步跑完再返回，而是把业务逻辑丢进
`asyncio.to_thread` 后台执行，立即返回 `{"job_id", "key"}`：

- 任务状态（`running`/`done`/`error`）落盘在
  `<project_root>/.agent/async_jobs/<job_id>.json`（`AgentPaths.async_job_record()`），
  daemon 重启后仍可查到。
- 每个业务操作有一个稳定的 `key`（如
  `"execution_spec_generate:{goal_id}"`、`"growth_scan"`），指向"最近一次
  job_id"的指针文件落在 `async_jobs/latest_by_key/`（
  `AgentPaths.async_job_latest_pointer()`），前端刷新页面丢了
  `st.session_state` 后可以通过 key 找回上一次任务的进度——**任务本身
  从不因为前端有没有在看而受影响**，后台跑到底为止。
- 通用轮询端点：`GET /v1/async_jobs/{job_id}` 和
  `GET /v1/async_jobs/latest/by_key?key=...`，所有任务类型共用，不需要
  每个功能各开一个查询接口。
- 已完成/失败超过 1 小时（`RETENTION_SECONDS`）的任务记录会在下次
  `start()` 时被顺手清理，避免 `async_jobs/` 目录无限增长。

**前端**：新增 `apps/mini_agent_kanban/async_job_ui.py`，封装两个函数：

- `start_async_job(client, key, submit_fn)`：点击按钮时调用，提交任务并把
  `job_id` 记进 `st.session_state`。
- `run_async_job(client, key, label=...)`：应该在**每次渲染**都调用（不只
  是点击那一次）。任务还在跑时渲染一条"⏳ ..."提示并 `st.rerun()`（每
  ~1.5s 轮询一次）；拿到终态后返回 `result`（成功）或 `{"_error": ...}`
  （失败），并清掉 `session_state` 里的记录，避免同一个结果被重复消费。
  `session_state` 里没有记录时会先尝试 `client.get_latest_async_job(key)`
  找回后端"最近一次任务"，处理"页面被整个刷新"的场景。

调用方式统一是这两步：

```python
if st.button("生成执行规范草稿"):
    if start_async_job(client, key, lambda: client.generate_execution_spec(goal_id, ...)):
        st.rerun()

result = run_async_job(client, key, label="正在生成执行规范草稿")
if result is not None:
    if "_error" in result:
        st.error(f"生成失败：{result['_error']}")
    else:
        # 用 result 渲染最终结果
        st.rerun()
```

## 影响范围（6 个改造点）

| 功能 | 端点 | key |
|---|---|---|
| 生成执行规范草稿 | `POST /goals/{id}/execution_spec/generate` | `execution_spec_generate:{goal_id}` |
| 补充意见重新生成 | `POST /goals/{id}/execution_spec/revise` | `execution_spec_revise:{goal_id}` |
| 手动重判整体关闭 | `POST /goals/{id}/execution_spec/close_check` | `execution_spec_close_check:{goal_id}` |
| 成长顾问"立即为我看看" | `POST /growth/scan` | `growth_scan` |
| 候选"采纳"/"忽略" | `POST /growth/candidates/{id}/{action}` | `growth_candidate_action:{id}:{action}` |
| 候选报告"刷新" | `POST /growth/candidates/{id}/report/refresh` | `growth_candidate_report_refresh:{id}` |

`execution_spec/close_check` 有一个例外：Goal 不是 `active` 状态时，判定
根本不会触发（不涉及任何调用），这条快速路径维持同步直接返回
`{"outcome": None, "reason": ...}`，不必要地绕一圈轮询反而拖慢体验。

## 接口形状变化（破坏性变更）与影响范围排查

这 6 个 POST 端点的响应体从"直接返回结果"变成"返回 `{job_id, key}`"，是
破坏性接口改动。排查了全代码库所有调用方：

- **CLI**（`cli/commands/goals.py`、`cli/commands/growth_cmd.py`）：直接
  in-process 调用 `GoalExecutionSpecBuilder`、`run_daily_cycle()` 等函数
  本体，不走 HTTP，**不受影响**。
- **`apps/mini_agent_kanban`（Streamlit 看板）**：本次改造对象，`client.py`
  + `app.py` 的 6 个调用点均已同步改造为新接口形状。
- **`apps/mini_agent_kanban_x`**（React/TS 实验性看板）：`endpoints.ts`
  里确实调用了这 6 个端点，会因为这次改动而失效（拿到 `{job_id}` 而不是
  预期的 `{spec}`/`{report}`）。按项目既有约定（该项目 README 明确写着
  "今后的看板相关改动一律只改 Streamlit 一侧，除非用户明确要求同步"），
  **本次不改这一侧**，保持现状（它本来就还没做完、不是日常在用的看板）。

## 持久化任务记录

除了内存里的 `AsyncJobRegistry`，job 状态会额外落一份到磁盘（见上文
"后端"一节），不依赖 `st.session_state`，也不需要挂在 Goal 节点自己的字段
上——磁盘落盘 + "按 key 查最近一次任务"这个组合已经能覆盖"daemon
重启"、"用户清了浏览器 session"、"用户刷新了整个页面"这几种场景，且不需要
改动 `GoalNode`/`GrowthCandidate` 的持久化 schema。

## 后续新增 LLM 调用点的标准做法

**任何新的 LLM/长耗时调用点，都必须走这套异步任务机制，不允许再手写
"HTTP handler 里同步调用 + 前端猜一个超时时间"的端点。** 标准写法：

```python
@router.post("/some/llm_backed_endpoint")
async def some_endpoint(request: Request):
    body = await request.json()  # 需要提前读的请求体/校验放在这里，同步函数
                                  # 内部不能再 await 请求对象

    def _do_work() -> dict:
        # 真正的业务逻辑（可以是同步阻塞调用，比如 LLM/Agent），
        # 在这里做异常捕获 + log_exception，让异常原样抛出，
        # AsyncJobRegistry 会把它转成任务的 error 状态。
        ...
        return {"some": "result"}

    key = f"some_endpoint:{some_id}"  # 稳定的业务 key，用于"按 key 查最近任务"
    job_id = _async_jobs(request).start(_do_work, key=key, meta={...})
    return {"job_id": job_id, "key": key}
```

前端对应调用 `start_async_job()` + `run_async_job()`（见 `async_job_ui.py`
顶部 docstring 的完整示例）。不要在新端点里引入自己的超时/轮询逻辑。

`run_blocking()`（`utils/blocking_guard.py`）仍然保留，用于"调用方需要在
这次 HTTP 响应里就拿到结果，但又要防止彻底卡死事件循环"的场景（比如带
硬超时上限的短耗时同步调用）；LLM/Agent 这类耗时不可预测的调用应该优先
用这里的异步任务机制，而不是 `run_blocking()`。
