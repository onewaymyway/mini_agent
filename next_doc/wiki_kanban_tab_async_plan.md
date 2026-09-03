# wiki 看板标签页改造为异步任务

## 背景 / 问题

看板 wiki 标签页（`/wiki stats|promotion|quarantine` 对应的三个子页）此前
是这次排查一个"获取 wiki 统计失败：ReadTimeout"报错时顺带发现的历史遗留：
跟 `kanban_async_job_mechanism_plan.md` 里点名批评的 LLM 调用点是同一个
反模式，只是触发条件从"LLM 耗时不可控"换成了"wiki 页面数变大后全量扫描
耗时不可控"：

1. `GET /wiki/stats` 的 handler 是 `async def`，但内部直接同步调用
   `compute_stats()`——**不只是前端会等到超时，这段时间会整个堵住
   FastAPI 唯一的事件循环，拖慢 daemon 对其它所有请求的响应**，比最初
   诊断的"客户端 6s 超时太短"更严重一层。`GET /wiki/promotion` 是同样的
   写法（`evaluate_promotion_readiness()`），量小时无感，但同一个模式。
2. `POST /wiki/quarantine/repair`、`POST /wiki/quarantine/retry` 已经用
   `run_blocking()` 扔进线程池，但带了固定超时（60s / 30s+60s 两段）——
   隔离区记录一多、或 repair 走了 LLM 修复分支，照样会被截断失败。

`GET /wiki/quarantine`（隔离区列表）只是读文件，`POST /wiki/quarantine/
purge`（清理确定救不回来的记录）命中记录数通常有限，这两个保持同步，
不引入不必要的轮询开销。

## 方案

不新增机制，复用 `kanban_async_job_mechanism_plan.md` 已经建好的通用异步
任务 registry（`src/mini_agent/api/async_jobs.py::AsyncJobRegistry`）和
前端封装（`apps/mini_agent_kanban/async_job_ui.py` 的
`start_async_job`/`run_async_job`），跟 `/growth/scan` 等 6 个此前改造过
的端点走同一套路径：

**后端**（`src/mini_agent/api/routes.py`）：

- `GET /wiki/stats` → `POST /wiki/stats`：`compute_stats()` +
  `compute_extraction_stats()` 包进 `_do_compute()`，`_async_jobs(request)
  .start(_do_compute, key="wiki_stats")` 立即返回 `{job_id, key}`。
- `GET /wiki/promotion` → `POST /wiki/promotion`：同样包一层，
  `key="wiki_promotion"`。
- `POST /wiki/quarantine/repair`：去掉 `run_blocking(timeout=60.0)`，改成
  `_async_jobs(request).start(_do_repair, key="wiki_quarantine_repair")`，
  后台线程里跑到底，不再有硬超时。
- `POST /wiki/quarantine/retry`：原来的"reset（30s）+ repair（60s）"两段
  `run_blocking` 合并进同一个后台任务函数 `_do_retry()`（`reset_count ==
  0` 时直接返回，不触发 repair 段），`key="wiki_quarantine_retry"`。

CLI（`cli/commands/wiki.py`）直接调用 `compute_stats()`/
`evaluate_promotion_readiness()` 等函数，不经过这几个 HTTP 端点，所以把
它们从 `GET` 改成 `POST` 不影响 CLI 行为，只影响看板前端这一个消费者。

**前端**（`apps/mini_agent_kanban/client.py` + `apps/mini_agent_kanban/
app.py`）：

- `client.py` 对应的 5 个方法（`wiki_stats`/`wiki_promotion`/
  `wiki_quarantine_repair`/`wiki_quarantine_retry`）从直接返回结果的
  `_get()`/`_post(timeout=...)` 改成不带 `timeout` 参数的 `_post()`，
  返回值统一变成 `{"job_id", "key"}`，交给调用方轮询。
- `_render_wiki_stats_section()`/`_render_wiki_promotion_section()`：这两
  个面板不是"点按钮才触发"，而是一进 tab 就要有数据，跟其它 async 用法
  （必须先点按钮）不同——所以增加了"首次渲染、`st.session_state` 里既没
  有缓存结果也没有正在跑的 job 时，自动 `start_async_job()` 一次"的逻辑
  （`_wiki_stats_auto_started`/`_wiki_promotion_auto_started` 这两个
  session_state key 做去重，避免每次 `st.rerun()` 重复提交），另外加了
  "🔄 重新统计"/"🔄 重新评估"按钮用于手动刷新。拿到的结果缓存进
  `st.session_state["_wiki_stats_cache"]`/`["_wiki_promotion_cache"]`，
  这样轮询期间、以及切到别的 sub-tab 再切回来时，上一次的结果还能继续
  展示，不会每次重新渲染都清空成"正在加载"。
- `_render_wiki_quarantine_section()` 里的两个按钮（"立即扫描 + 修复"/
  "重试已转人工记录"）改成标准的"点按钮 `start_async_job` → `st.rerun()`
  → 下面紧跟 `run_async_job()` 渲染等待/结果"两步走，跟看板其它按钮式
  操作（比如成长顾问候选的"采纳"）写法一致。

## 不在这次范围内

- `compute_stats()`/`evaluate_promotion_readiness()` 本身的全量扫描逻辑
  不改——这次只是把"谁来等这个耗时操作"从"HTTP 请求 + 前端固定超时"换成
  "后台任务 + 轮询"，不引入缓存/增量计算（如果页面数继续涨、异步任务本身
  也开始要跑几分钟，那是下一步"给 compute_stats 加缓存/复用 `_index/`
  派生索引"要解决的问题）。
- `GET /wiki/quarantine`、`POST /wiki/quarantine/purge` 保持同步。
