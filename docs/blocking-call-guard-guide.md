# HTTP 路由阻塞调用防护指南（`run_blocking`）

> 背景 / 完整根因分析见 `next_doc/http_server_blocking_call_guard_plan.md`。本文只讲
> "以后写新路由时该怎么用"。

## 一句话结论

**HTTP 路由（`async def`）里，凡是要同步调用 LLM，或任何不确定执行时长、可能卡住的
操作，一律不允许直接调用，必须经过 `mini_agent.utils.blocking_guard.run_blocking()`。**

不这样做的后果：FastAPI/uvicorn 默认单进程单事件循环，所有 `async def` 路由共享同一个
循环线程。协程内一旦出现不 `await` 的同步阻塞调用，会把**整个事件循环**卡住——不是"这
一个请求慢"，而是同一进程里所有其他请求（包括跟这次调用毫无关系的看板轮询、心跳、
health check）全部要排队等它结束。LLM 调用叠加超时+重试+退避，最坏情况能卡到几分钟，
现网表现就是"HTTP server 突然连不上了，只能杀进程重启"。

## 怎么用

```python
from mini_agent.utils.blocking_guard import run_blocking

@router.post("/some/route")
async def some_route(request: Request):
    ...
    result = await run_blocking(
        some_sync_business_fn,          # 同步函数，不是 async def
        arg1, arg2,                     # 位置参数照常传
        kw1=v1,                         # 关键字参数照常传
        where="some_route_business_fn", # 必填：熔断分组 + 日志定位用的短字符串
        # timeout / failure_threshold / cooldown_seconds 可选，
        # 不传则用 cfg.http.blocking_call_* 或内置默认值
        fallback=None,                  # 可选：超时/熔断时返回什么；不传则抛 HTTPException(504/503)
    )
    return result
```

**关键点：包住的是"最外层那个同步业务函数"，不是内部某个 `lambda prompt: llm.ask(prompt)`
回调本身。** 很多业务函数（比如 `growth_advisor.goal_growth_alignment()`）内部可能会多次
调用传进去的 `llm_helper`，如果只把 `llm_helper` 这个 lambda 包一层，外层那个同步函数本身
还是在事件循环线程里跑，没有解决问题。正确做法永远是：找到路由里那一行"真正会耗时"的同步
调用，把**它**换成 `await run_blocking(...)`。

### `where` 怎么起名

用稳定的、能一眼看出是哪个路由/哪个动作的短字符串，比如 `growth_align`、
`growth_scan_daily_cycle`、`cycle_diagnostics_summarize`。同一个 `where` 共享一套熔断
计数——如果两个不同的路由用了同一个 `where`，会互相影响彼此的熔断状态，一般不要这样做。

### 要不要传 `fallback`

- **只读聚合类接口**（比如"生成一段 LLM 总结"，失败了大不了这个字段是 `null`，不影响
  接口其他内容）：传 `fallback=None`（或对应的空值），让路由能继续正常返回，只是这一个
  字段缺失。参考 `get_goal_cycle_diagnostics` 里 `report.llm_summary` 的处理。
- **动作类接口**（比如"生成一份调研报告""落地一个 Goal"，失败了整个操作就是没完成，
  没有"部分成功"的语义）：不传 `fallback`，让它抛 `HTTPException(504/503)`，路由外层
  该怎么处理异常就怎么处理（通常已有 `except HTTPException: raise` + `except Exception`
  兜底 500 的模式）。
- **调用之后还有必须执行的收尾逻辑**（比如 `post_growth_scan` 里，无论 LLM 那步成不成，
  前面信号扫描阶段的结果都要落盘）：要在 `except HTTPException` 分支里也把收尾逻辑走一遍，
  不能让 504/503 直接穿透跳过收尾——具体写法参考 `routes.py` 里 `post_growth_scan` 的实现。

### 超时/熔断参数从哪来

路由层统一用这个小 helper 从当前请求的 `AppConfig` 里取参数（拿不到 cfg 时自动退回
`run_blocking()` 内置默认值，不会报错）：

```python
result = await run_blocking(
    fn, ...,
    **_blocking_call_opts(request, "some_route_business_fn"),
)
```

`_blocking_call_opts()` 定义在 `routes.py` 里，读的是 `HttpConfig` 的三个字段：

| 配置项 | 默认值 | 含义 |
|---|---|---|
| `http.blocking_call_timeout_seconds` | `45.0` | 单次调用硬超时。留够余量给"LLM 单次 10s 超时 × 3 次重试 + 退避"这种链路，同时明显小于用户会觉得"卡死了"的心理阈值 |
| `http.blocking_call_failure_threshold` | `3` | 同一个 `where` 连续失败/超时几次后打开熔断 |
| `http.blocking_call_cooldown_seconds` | `120.0` | 熔断打开后冷却多久，冷却结束自动放行一次探测 |

行为默认开启，不需要用户手动配置就生效；有特殊需求（比如某个接口本来就该更快超时）可以
在调用时显式传 `timeout=` 覆盖，不用改全局配置。

## 熔断是怎么工作的

`run_blocking()` 内部维护一个进程内、按 `where` 分组的轻量计数器（不持久化，重启进程即
重置）：

- 连续失败（超时或抛异常）达到 `failure_threshold` 次 → 打开熔断
- 熔断打开期间，新请求**不会**再起线程尝试调用，直接短路返回 `fallback`（或
  `HTTPException(503)`）——避免"LLM 服务已经挂了，但看板还在不断点按钮，线程池被一堆
  注定超时的调用占满"
- 冷却时间（`cooldown_seconds`）结束后，下一次请求会正常放行做一次探测：成功则清零
  计数、失败则重新打开熔断并重新计时

想看当前各分组的熔断状态，用：

```python
from mini_agent.utils.blocking_guard import get_blocking_call_health_snapshot
get_blocking_call_health_snapshot()
# {"growth_align": {"consecutive_failures": 0, "circuit_open": False}, ...}
```

## 什么时候不需要用这个

- `fn` 本身就是 `async def`：直接 `await fn(...)`，不需要 `run_blocking`。
- 纯规则计算、不涉及网络/LLM 调用、耗时可预期在毫秒级（比如
  `ct.suggest_tuning_from_diagnostics()` 这种"不调用 LLM"的路径）：不需要包，正常同步调用
  即可，包一层只是徒增开销和复杂度。判断标准是"这次调用的耗时上限是否可控、是否可能因为
  外部依赖（网络/第三方服务）而失控"，不是"是不是 LLM 调用"本身。

## 现有已接入的调用点（可参考写法）

`src/mini_agent/api/routes.py`：

- `get_goal_cycle_diagnostics`（`?summarize=true` 分支）
- `suggest_tuning_proposal`（自然语言解析分支）
- `get_growth_summary`（`diagnostics_snapshot`）
- `post_growth_scan`（`run_daily_cycle`，含 HTTPException 分支下的兜底落盘）
- `get_growth_align`
- `post_growth_align_adopt_all`
- `post_growth_candidate_refresh_report`
- `post_growth_candidate_generate_material`

新写类似接口时，直接照这几处的写法抄一份改改就行。
