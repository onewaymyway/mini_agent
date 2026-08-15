# HTTP Server 阻塞调用防护机制（计划）

## 背景 / 问题现场

daemon 长时间运行后，主 HTTP server 突然无法连接，日志里只留下：

```
Exception in callback _ProactorBasePipeTransport._call_connection_lost(None)
ConnectionResetError: [WinError 10054] 远程主机强迫关闭了一个现有的连接。
```

这条日志本身不是根因，是事件循环在处理一个已经异常关闭的连接时的收尾报错。真正的
触发点是：用户在看板上做了一次"用 LLM 生成成长建议 / 调优建议"的操作。

## 根因

`src/mini_agent/api/routes.py` 里所有"调用 LLM 生成建议"的路由，统一是这个写法：

```python
async def some_route(request: Request):
    ...
    llm_helper = lambda prompt: helper.ask(prompt)   # 同步、阻塞
    return ga.some_business_fn(..., llm_helper=llm_helper)  # 同步调用，内部会真正执行 llm_helper(prompt)
```

`LLMHelper.ask()`（`src/mini_agent/llm/service.py`）是纯同步实现：单次请求
`timeout=10.0`（`LLMConfig.timeout` 默认值），`RetryPolicy` 默认 `max_retries=3`，
退避策略从 `FixedBackoff(10.0)` 到部分调用点的
`ExponentialBackoff(initial=5.0, multiplier=2.0, max_delay=120.0)` 不等。也就是说单次
"生成建议"请求，最坏情况下会在 `async def` handler 内部**同步阻塞几十秒到几分钟**，
期间不 `await` 任何东西。

uvicorn 默认单进程单事件循环，所有 `async def` 协程共享同一个循环线程。协程内一旦出现
不 `await` 的同步阻塞调用，**不是占用某个线程池 worker，而是直接把整个事件循环卡住**：
同一进程里其他所有请求（包括跟 LLM 完全无关的看板轮询、心跳、health check）全部要排队
等这次调用结束才能被处理。如果调用期间正好远端网络异常，退避+重试链会被拉得更长，看
起来就是"server 彻底连不上了"，需要杀进程重启。

## 已确认的调用点（同一模式，共 8 处）

`src/mini_agent/api/routes.py`：

- `_agent_llm_ask()` 构造出的 `lambda prompt: helper.ask(prompt)`，被
  - `get_goal_cycle_diagnostics`（`?summarize=true` 分支，L4219）
  - `suggest_tuning_proposal` 的自然语言解析分支（L4306，经 `build_tuning_proposal_from_nl`）
  使用
- 另外 6 处内联 `lambda prompt: helper.ask(prompt)` / `lambda prompt, _h=helper: _h.ask(prompt)`：
  L6728、L6828、L7334、L7374、L7549、L7652，分别对应
  growth advisor 相关的若干路由（`get_growth_align`、
  `post_growth_align_confirm_match`、`post_growth_candidate_refresh_report`、
  `post_growth_candidate_generate_material` 等）。

这些路由的共同点：`llm_helper` 只是一个回调，真正发起阻塞调用的是外层那个同步业务函数
（`ga.goal_growth_alignment(...)` 等），它可能在内部多次调用 `llm_helper(prompt)`。所以
**修复要包住整个同步业务调用，而不是只包住 lambda 本身**——否则业务函数本身还是在事件
循环线程里同步跑完才返回。

## 方案：通用阻塞调用防护工具

新增 `src/mini_agent/utils/blocking_guard.py`，提供一个通用的
`await run_blocking(fn, *args, timeout=None, where="", fallback=_UNSET, **kwargs)`：

1. **移线程**：用 `asyncio.to_thread(fn, *args, **kwargs)` 把同步调用挪到线程池执行，
   事件循环本身不再被占用——其他请求（看板轮询、health check 等）不受影响。
2. **硬超时**：外面套 `asyncio.wait_for(..., timeout=...)`。超时后：
   - 若调用方提供了 `fallback`（哨兵 `_UNSET` 区分"没传"和"传了 None"），直接返回
     `fallback`，不让路由报 500；
   - 否则包成 `HTTPException(504, ...)` 抛给调用方处理。
   - 超时不会真正杀死线程池里那次调用（Python 线程不可强制中断），但至少不会再拖累
     事件循环、也不会让 HTTP 响应无限挂起。
3. **失败检测 / 熔断**：模块内维护一个按 `where` 分组的轻量计数器
   （`_BlockingCallHealth`）：连续超时/异常达到阈值（默认 3 次）后，短时间
   （默认 120s）内新请求直接走 `fallback` 短路，不再尝试起线程调用——避免"LLM 服务已经
   挂了，但看板还在不断点按钮，线程池被一堆注定超时的调用占满"。冷却时间结束后自动放行
   一次做探测，成功则清零计数、失败则重新进入冷却。
4. 所有异常/超时都经由现有 `mini_agent.errors.log_exception` 落盘，方便事后在
   `~/.agent/logs/error.jsonl` 里 grep。

默认超时、失败阈值、冷却时间可配置（`HttpConfig` 新增
`blocking_call_timeout_seconds` / `blocking_call_failure_threshold` /
`blocking_call_cooldown_seconds`），默认值分别为 `45.0` / `3` / `120.0`——45s 是因为单次
LLM 调用本身可能就有 10s×3 次重试+退避，需要给够余量，同时又要明显小于"用户会觉得卡死"
的心理阈值。

## 实施步骤

1. `src/mini_agent/utils/blocking_guard.py`：`run_blocking()` + 熔断状态类 + 单元测试。
2. `HttpConfig` 新增三个字段（有默认值，行为默认开启，不需要用户手动配置）。
3. 把 routes.py 里全部 8 个调用点，从"同步内联调用"改为
   `await run_blocking(ga.some_fn, ..., where="growth_align", fallback=...)`，路由函数本身
   已经是 `async def`，只是把内部这一步同步调用换掉。
4. `docs/` 新增一篇 `blocking-call-guard-guide.md`，说明这个通用机制、为什么需要它、
   新路由要怎么接入（"以后任何在 HTTP 路由里同步调用 LLM / 其他可能长时间阻塞的操作，
   一律要经过 `run_blocking`，不允许直接同步调用"）。
5. 补单元测试：正常返回、超时走 fallback、连续失败触发熔断、冷却后恢复。
6. `python -m pytest` 全量跑一遍，确认不破坏现有测试。
7. diff-only 打包 zip。

## 不做的事

- 不改 `LLMHelper.ask()` 内部的重试/超时策略本身（那是"跟 LLM provider 对话时该怎么重试"
  的问题，跟"HTTP server 会不会被拖死"是两个层面）。
- 不引入额外的进程/多 worker 部署方式，本次修复在单进程单事件循环的现状下解决问题。
