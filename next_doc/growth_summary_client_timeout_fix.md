# 看板"🌱 成长顾问"总是报错 `Read timed out (read timeout=6)` — 排查记录

## 1. 现象

看板"🌱 成长顾问"tab 频繁报错：

```
HTTPConnectionPool(host='127.0.0.1', port=8765): Read timed out. (read timeout=6)
```

daemon 进程本身没有异常/崩溃，只是这个 tab 打不开。

## 2. 根因

跟 `session_list_blocking_and_cache_fix.md` 记录的问题**同一类**，只是
出现在另一个端点：**客户端超时预算比服务端自己的容忍上限窄了一大截**。

- `apps/mini_agent_kanban/app.py` 里"🌱 成长顾问"tab 每次渲染都会无条件
  调用一次 `client.growth_summary()`（不带 `refresh_diagnostics`）。
- `client.py::growth_summary()` 修复前默认（非强制刷新）路径的超时是
  **6 秒**。
- 但服务端 `GET /v1/growth/summary`（`routes.py::get_growth_summary`）
  内部通过 `run_blocking()` 调用 `diagnostics_snapshot()`，允许的默认
  预算是 **45 秒**（`blocking_guard.DEFAULT_TIMEOUT_SECONDS`，可经
  `cfg.http.blocking_call_timeout_seconds` 调整）。此外该路由里还有一段
  **完全没有 `await`、也没进线程池**的同步文件 I/O：
  `GrowthBacklog.load_all()`、`list_reports()`、
  `monthly_retrospective_summary()`、cron 任务列表拼装、
  `first_touch_notice_shown()`——候选/报告/goal 数据越多，这几步本身
  也会越来越慢，而且跟事件循环上其它并发请求互相阻塞。

综合下来：候选、报告数量稍多，或者 daemon 上同时有其它 `run_blocking`
调用占着线程池（比如 cron 任务正在跑 LLM 调用）时，`GET /v1/growth/
summary` 一次"正常"（非强制刷新）请求耗时超过 6 秒是很常见的——服务端
远没到自己的超时线，请求还在正常处理，**客户端却先掉线了**，前端只能
看到网络层的 `Read timed out`，看起来像是"服务连不上"，其实 daemon
一切正常。

## 3. 修复

### 3.1 客户端超时对齐服务端预算（直接止血）

`apps/mini_agent_kanban/client.py::growth_summary()`：非强制刷新路径的
超时从 6s 提到 **25s**（强制刷新路径维持 50s，仍留出跟默认路径的区分
度，方便用户理解"点🔄刷新诊断数据会更慢一些是正常的"）。

### 3.2 根治：把剩余同步 I/O 也丢进线程池

`src/mini_agent/api/routes.py::get_growth_summary()`：

- `backlog.load_all()` / `ga.list_reports(paths)` /
  `ga.monthly_retrospective_summary(paths)` 合并成一个闭包，通过一次
  `run_blocking()` 调用执行（不在已经运行在线程池里的
  `diagnostics_snapshot()` 内部再嵌套 `run_blocking`，避免线程池
  嵌套占用）。
- `ga.first_touch_notice_shown(paths)` 单独一次 `run_blocking()` 调用。
- 两处都带 `fallback`（分别是 `([], [], {})` 和 `True`），熔断/超时时
  优雅降级，不会让整个 `/growth/summary` 500——其它字段仍能正常返回。

`cs.list_jobs()`（内存态 cron 任务列表，开销很小）维持原样，未做改动。

## 4. 效果

- 客户端超时预算不再显著小于服务端自身的处理预算，避免"服务端还在
  正常处理、客户端先掉线"的误报。
- 该路由里原本裸跑在事件循环上的同步磁盘 I/O 全部移到线程池，候选/
  报告数据增长后也不会拖慢其它并发请求（心跳、其它 tab 的轮询等）。

## 5. 涉及文件

- `apps/mini_agent_kanban/client.py` — `growth_summary()` 超时调整
- `src/mini_agent/api/routes.py` — `get_growth_summary()` 同步 I/O
  改为 `run_blocking()`
