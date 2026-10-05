# 成长顾问 tab 概览超时治理：趋势索引（A）+ 概览拆分与板块独立加载（B）

- **状态**：✅ 全部阶段已完成（阶段一至六；方案 A 趋势索引、方案 B 服务端拆分 / 客户端 / 板块加载 / 看板接入、B8 压测脚本与文档收尾）。仍有两项需要人工在真实环境确认，见 §13"阶段六"末尾与 §12。实施取舍与验证记录见 §13
- **范围**：本轮只做 A + B。C（HTTP 并发与隔离）已确认单独立项，见 §9
- **关联文档**：
  - `next_doc/growth_summary_client_timeout_fix.md`（上一次只调客户端超时预算 6s→25s 的修复，本文是它的根治版）
  - `next_doc/growth_diagnostics_backfill_count_cache_plan.md`（同一端点上一次的慢点：会话回填计数）
  - `next_doc/http_server_blocking_call_guard_plan.md`（`run_blocking` 机制）
  - `next_doc/session_list_blocking_and_cache_fix.md`（会话列表的同类问题与 `_fetch_sessions_list_async` 缓存写法）
  - `docs/growth-advisor-guide.md`、`docs/kanban-dashboard-guide.md`

---

## 1. 背景

看板「🌱 成长顾问」tab 打开后持续报错：

```
概览数据加载失败（不影响下面各个独立板块）：HTTPConnectionPool(host='127.0.0.1', port=8765):
Max retries exceeded with url: /v1/growth/summary
(Caused by ReadTimeoutError(... Read timed out. (read timeout=25)))
```

daemon 本身没有崩溃，其它请求正常，只有这个 tab 的概览加载不出来。

这是同一个端点上第三次出现"慢"：

| 次序 | 现象 | 当时的处理 | 为什么没有根治 |
|---|---|---|---|
| 1 | 回填候选数全量扫描 session，`blocking_guard` 45s 超时 | 改成增量索引（`session_backfill_index_incremental_plan.md`） | 只修了一项 |
| 2 | 客户端 `read timeout=6` | 客户端预算提到 25s，同步 I/O 进线程池 | 只调预算，没动耗时本身 |
| 3 | 本次，`read timeout=25` 仍然超时 | 本文 | — |

前两次都是"发现一个慢点，补一个点"。本次先做了定量排查，确认主因不在"概览字段太多"，而在读盘方式。

## 2. 根因（含实测）

### 2.1 主因：趋势文件的 N+1 读盘

`evolution/growth_advisor.py::_topic_trend_series(paths, dedupe_key)` 每次调用都会 `_read_jsonl(paths.growth_topic_trend_path)` 读取并解析**整个**趋势文件，再按 `dedupe_key` 过滤。以下调用点都在循环里反复调它：

| 调用点 | 调用次数 |
|---|---|
| `growth_topic_map()`（经 `monthly_retrospective_summary()` 进入 summary） | 每个话题 1 次 |
| `pending_followups()` → `_topic_trend_rising()` | 每个到期的已采纳候选 1 次 |
| `reports_needing_refresh()` → `_recent_evidence_delta()` | 每个满足触发条件的候选 1 次 |

文件行数正比于话题数（每个话题每轮扫描一行，60 天内不降采样），读取次数也正比于话题数，所以总开销是**平方级**。

沙箱实测（合成数据：每个话题 60 天趋势快照，另含 1000 条反馈、报告索引与归档；1 核机器，绝对值仅供参考，趋势由代码结构决定）：

| 话题数 | `growth_topic_map` | `reports_needing_refresh` | `pending_followups` | `diagnostics_snapshot` 合计 |
|---|---|---|---|---|
| 50 | 0.49 s | 0.24 s | 0.08 s | 0.39 s |
| 100 | 1.9 s | 0.95 s | 0.30 s | 1.3 s |
| 200 | 8.3 s | 3.8 s | 1.6 s | 5.6 s |
| 400 | 32.7 s | 15.9 s | 7.2 s | 23.9 s |

`diagnostics_snapshot()` 里其余十几项（反馈模式、目标对齐、引用检查、会话回填计数、画像等）合计不到 50 ms。**所以"概览包含内容太多"不是耗时来源，拆分的价值在于故障隔离和不被慢板块拖累，不在于省时间。**

原型验证修法可行：单次读取并按 `dedupe_key` 分组，400 话题时 111 ms；`growth_topic_map` 使用预建索引后 6 ms（原 32.7 s）；对 30 个采样话题，结果与原函数逐项相等。

### 2.2 放大器

1. **客户端自动重试**：`client.py::_build_http_session()` 给所有 GET 配了 `Retry(read=2)`。上面报错里的 `Max retries exceeded` 就是重试耗尽后的信息：一次失败的加载等于 3 次请求，Streamlit 脚本线程被卡 75 秒以上。
2. **服务端不取消孤儿线程**：每次重试都会让服务端完整重算一遍（`run_blocking` 超时只是"不再等"，线程池里的计算会继续跑），请求越慢堆积越多。
3. **每次 rerun 都重新拉取**：`render_growth_tab()` 每次渲染无条件同步调用 `client.growth_summary()`，没有缓存。点 tab 内任何按钮都会重算。
4. **同一页重复计算**：`pending_followups` 在 summary 的诊断里算一次，又在 `GET /growth/followups` 里算一次；`reports_needing_refresh` 同理。
5. **整页被单个请求阻塞**：概览请求在脚本线程里同步等待，期间整页无法交互。

### 2.3 次要观察（本轮不处理，仅记录）

- `pending_followups()` 在 GET 路径里会写盘（已完成 Goal 的候选直接 `record_followup(..., "progressed")`），不是纯只读。TTL 缓存会降低触发频率，但语义未改。
- `GrowthBacklog.load_all()` 在一次 summary 里被调用约 10 次、反馈台账约 4 次、报告索引约 4 次。当前都是毫秒级，不是瓶颈，不在本轮优化。

## 3. 目标与非目标

### 目标

1. 消除趋势文件的 N+1 读盘，使 `growth_topic_map` / `pending_followups` / `reports_needing_refresh` 的读盘次数与话题数无关（O(1) 次文件读取）。
2. 把"概览"拆成可独立获取的子功能，每个看板板块各自加载、各自超时、各自重试，任何一个慢或失败都不影响其它板块。
3. 看板 tab 内的普通交互（点按钮、展开）不再触发重复拉取；渲染过程不被慢请求阻塞。
4. 慢读超时不再被客户端自动重试放大。
5. 全部改动向后兼容：`/growth/summary` 响应结构与现状一致，CLI 与其它调用方不受影响。

### 非目标（本轮不做）

- HTTP 服务并发能力（线程池分舱、兄弟端点进线程池、single-flight、可观测性）→ §9，单独立项。
- 其余约 250 个 `async def` 路由的事件循环阻塞审计。
- `_read_jsonl` 通用解析缓存、`GrowthBacklog` / 台账 / 报告索引的重复读取合并。
- `pending_followups()` GET 写盘语义调整。

## 4. 设计理念

1. **先修根因，再做拆分**：不先修 N+1，拆出来的"主题地图""回访""报告刷新"板块自己也会超时。A 是 B 的前提。
2. **默认行为不变**：所有新参数默认 `None`，不传时函数行为与改动前一致；现有测试不需要改。符合"配置/参数默认非破坏"的既有约定。
3. **拆分的边界按"数据源"而不是按"UI 位置"**：一个端点对应一类数据，多个板块可以共享同一份已加载的数据（例如"诊断信息"与"Agent 对你的了解"共用 diagnostics，不重复请求）。
4. **通用机制而不是单点补丁**：板块加载抽成可复用的 `section_loader`，成长顾问是第一个使用者，其它 tab 以后可直接复用；不在 `render_growth_tab()` 里再堆一份手写的 Future 轮询。
5. **复用已有模式**：缓存 + 后台线程池 + "有缓存先用缓存（stale-while-revalidate）"沿用 `_fetch_sessions_list_async` 已验证过的做法（它修过"按钮点击被吞"的问题）；服务端沿用 `run_blocking` + `fallback` 降级。
6. **故障隔离优先于一致性**：板块之间允许数据来自不同时刻（TTL 内），换取任一板块失败时其余正常。
7. **保留兼容出口**：`/growth/summary` 保留，内部改为由新的拆分函数拼装，保证新旧路径数据口径一致。

## 5. 方案 A：趋势索引（服务端数据层）

### 5.1 `evolution/growth_advisor.py`

- 新增 `load_topic_trend_index(paths) -> dict[str, list[dict]]`：一次 `_read_jsonl`，按 `dedupe_key` 分组，每组按 `scanned_at` 升序排好（保留全量，不在索引里截断）。畸形行（缺 `dedupe_key` / `scanned_at` / `evidence_count`）直接跳过。原函数只会在"命中该 key 的行"上访问这些字段，新实现对非目标行也不应抛错，因此是更宽容的实现。
- `_topic_trend_series(paths, dedupe_key, limit=..., *, index=None)`：传入 `index` 时直接 `index.get(key, [])` 后取最近 `limit` 条；不传时走原路径（单次文件读取），行为不变。返回结构保持 `[{scanned_at, evidence_count, confidence}]`。
- 下列函数新增可选参数 `trend_index=None`，并在**函数入口**处：若为 `None` 且确实需要趋势数据，则**本函数内只构建一次**索引，再向下传：
  - `_topic_trend_rising(paths, key, *, window_days, index=None)`
  - `_recent_evidence_delta(paths, key, *, window_days, index=None)`
  - `growth_topic_map(paths, *, trend_index=None)`
  - `pending_followups(paths, cfg=None, *, goal_backlog=None, trend_index=None)`
  - `reports_needing_refresh(paths, cfg=None, *, goal_backlog=None, profile=None, trend_index=None)`
  - `followup_question_hint(...)`：实施时检查它是否也走趋势读取，若是则同样接受 `trend_index`。
- 这样即使调用方不知道有索引，N+1 也已消除；知道的调用方（summary / diagnostics）可以传入同一份索引，整个请求内趋势文件只读一次。
- `diagnostics_snapshot(..., trend_index=None)`：入口处构建（或使用传入的）索引，传给 `pending_followups` 和 `reports_needing_refresh`。
- `monthly_retrospective_summary(paths, *, goal_backlog=None, include_topic_map=True, trend_index=None)`：`include_topic_map=False` 时返回值不含 `topic_map`（供 `/growth/overview` 使用）。默认 `True`，现有调用方行为不变。

### 5.2 预期效果

| 话题数 | `growth_topic_map` 现状 → 预期 | 趋势文件读取次数（单次 summary） |
|---|---|---|
| 200 | 8.3 s → 个位数毫秒 | 约 `N+M` 次 → 1 次 |
| 400 | 32.7 s → 个位数毫秒（索引构建约 0.1 s） | 同上 |

## 6. 方案 B：概览拆分与板块独立加载

### 6.1 服务端端点

三个新端点，均 `_require_owner`、均走 `run_blocking`、均带 `fallback`；"where" 分别独立（`growth_overview` / `growth_diagnostics` / `growth_topic_map`），熔断互不影响。

| 端点 | 返回 | 说明 |
|---|---|---|
| `GET /v1/growth/overview` | `{candidates, reports, retrospective, first_touch_notice_shown}` | `retrospective` 不含 `topic_map`；不含 diagnostics。全是简单聚合，预期毫秒级 |
| `GET /v1/growth/diagnostics?refresh_diagnostics=false` | 现有 `diagnostics` 字典（含 `cron_jobs`） | 与现有 summary 里的 diagnostics 逐字段一致；`refresh_diagnostics=true` 语义同现状（丢弃并重建会话回填索引）。超时降级仍返回 `{"_note": ...}` 占位 |
| `GET /v1/growth/topic_map` | `{topic_map: [...]}` | 供看板按需拉取 |

兄弟端点（`/growth/followups`、`/growth/reports/refresh_candidates`、`/growth/pursuits*`、`/growth/align`、`/growth/health_trend`、`/growth/candidates/{id}/timeline`）**保持不变**。它们本来就是独立端点，本轮只受益于 A（`followups` 与 `refresh_candidates` 内部已使用趋势索引）。

### 6.2 `/growth/summary` 兼容聚合

- 路由函数把原有逻辑拆成三个内部函数：`_growth_overview_payload(request)`、`_growth_diagnostics_payload(request, refresh, trend_index)`、`_growth_topic_map_payload(paths, trend_index)`；三个新端点和 `/growth/summary` 共用它们，保证口径一致，避免两份逻辑漂移。
- `/growth/summary` 在内部先构建一份 `trend_index`，传给 diagnostics 和 topic_map，整个请求只读一次趋势文件。
- 响应结构与现状**完全一致**：`{candidates, reports, retrospective（含 topic_map）, first_touch_notice_shown, diagnostics}`。
- `?refresh_diagnostics=true` 语义不变。
- 看板不再调用它；`AgentClient.growth_summary()` 保留，供其它调用方使用。

### 6.3 客户端 `apps/mini_agent_kanban/client.py`

1. 新增 `growth_overview()`、`growth_diagnostics(refresh=False)`、`growth_topic_map()`。
2. **读超时不重试**：新增模块级 `_HTTP_NO_READ_RETRY` session：`Retry(total=2, connect=2, read=0, status=0)`，即只对"连接失败"重试，读超时和 5xx（含 `blocking_guard` 返回的 504）都不重试，因为这些情况下服务端已经在算或者已经放弃，再发一次只会加重负载。`_get(..., retry=True)` 增加 `retry` 关键字，成长顾问 tab 渲染路径上的所有 GET 传 `retry=False`：`growth_summary`、`growth_overview`、`growth_diagnostics`、`growth_topic_map`、`growth_followups`、`growth_reports_refresh_candidates`、`growth_pursuits` 及 `portfolio_summary` / `related_directions`、`growth_align`、`growth_health_trend`。其余端点保持原有重试行为。
3. 超时预算：`overview` 15s、`diagnostics` 25s（refresh 为 50s）、`topic_map` 25s、`summary` 沿用 25s / 50s。因为不再重试，单次最坏等待就是这个数。
4. `_record_http_call` 日志沿用，无需改动。

### 6.4 看板加载机制：`section_loader`

新增 `apps/mini_agent_kanban/section_loader.py`，分两层，便于单测：

- **纯逻辑层 `SectionCache`**：操作一个普通 `dict`（运行时传入 `st.session_state`），不 import streamlit。
  - 状态：`loading` / `ready` / `error`，带 `data`、`error`、`fetched_at`。
  - `get(key, fetch_fn, ttl)`：
    - 无缓存且无在途请求 → 提交后台任务（`client.submit_async`，复用已有 8 线程池），返回 `loading`；
    - 有缓存且未过期 → 直接返回 `ready`，不发请求；
    - 有缓存但已过期 → 返回旧数据（`ready`，标记 `stale=True`），同时后台刷新（stale-while-revalidate）；
    - 请求失败：有旧缓存则继续展示旧数据并带 `error` 提示；无缓存则返回 `error`；
    - `fetch_fn` 返回 `{"_error": ...}` 视同失败。
  - `invalidate(*keys)` / `refresh(key)`：写操作后使缓存失效；失效不丢旧数据，下一次 `get` 返回旧数据并后台刷新，避免界面闪空。
  - 同一 key 同时只允许一个在途请求。
- **Streamlit 包装层 `render_section(key, label, fetch_fn, ttl, render_fn)`**：
  - `loading` 时在本板块原地显示"⏳ 正在加载 {label}…"，并用 `st.rerun(scope="fragment")` 做轻量轮询，只重跑本板块；
  - `error` 时在本板块显示错误信息和 "🔄 重试" 按钮（只重试本板块，调用 `refresh(key)`）；
  - `ready` 时调用 `render_fn(data)`；`stale` 时在板块角落显示小字"数据可能略有延迟"。

### 6.5 看板布局与数据源映射

| 板块 | 数据源（section key） | TTL |
|---|---|---|
| 概览指标（候选总数/已采纳/已忽略/调研报告）、采纳率、采纳/忽略排行、报告质量待改进 | `growth_overview` | 15 s |
| 诊断信息 | `growth_diagnostics` | 30 s |
| Agent 对你的了解 / 关键词 | `growth_diagnostics`（与上一板块共享，不额外请求） | 30 s |
| 健康度趋势 | `growth_health_trend` | 30 s |
| 🗺️ 成长主题地图 | `growth_topic_map`，**按需**：默认折叠，用 `st.toggle("显示成长主题地图")` 打开时才拉取 | 60 s |
| 该回访一下了 | `growth_followups` | 15 s |
| 有兴趣但还没建目标 | `growth_align` | 30 s |
| 正在自主推进 | `growth_pursuits`（及 portfolio / related） | 30 s |
| 报告可以更新一下了 | `growth_refresh_candidates` | 30 s |
| 调研报告查看器、待处理候选 | `growth_overview.candidates` | 15 s |

其它要点：

- **首次触达提示**来自 `growth_overview`，展示后仍调用 `growth_first_touch_ack()`，且只调用一次（用 session 标志防止重复）。
- **🔄 刷新诊断数据**按钮改为 `refresh_section("growth_diagnostics", refresh_diagnostics=True)`，不再在按钮回调里同步等 50s。
- **写操作失效**：采纳/忽略、立即扫描完成、落地为 Goal、刷新报告、关键词增删/确认/恢复、回访记录之后，统一调用 `invalidate_growth_sections(...)` 使相关 key 失效，保证操作后看到新数据。具体 key 对照表在实施时按现有 `st.rerun()` 调用点逐个核对。
- `render_growth_tab()` 中现有的 `_safe_growth_section()` 保留（兜底渲染异常），与 `render_section` 互补：前者保护渲染代码，后者保护数据加载。
- 原来的"概览加载失败"整行警告和"🔄 重试概览"按钮删除，由各板块自己的错误提示取代。

## 7. 兼容性与回滚

| 项 | 兼容性 |
|---|---|
| `/v1/growth/summary` | 响应结构不变；内部口径与新端点一致 |
| `growth_advisor.py` 公共函数 | 新增参数全部有默认值，原调用方无需修改 |
| `monthly_retrospective_summary` | 默认仍含 `topic_map` |
| 新端点 | 纯新增，旧客户端不会调用 |
| 看板 | 仅 `app.py`、`client.py` 与新模块；老版 daemon 上没有新端点时，看板会在各板块显示 404 错误提示（需要同时更新 daemon 与看板，见 §11） |
| 回滚 | 覆盖回旧版 `app.py` / `client.py` 即回到旧路径；服务端新增内容无需回滚 |

不新增任何配置项。

## 8. 测试计划

新增测试，沿用 `tests/test_growth_advisor*.py` 的临时目录夹具写法。

1. `tests/test_growth_trend_index.py`
   - 索引与原 `_topic_trend_series` 在随机数据上逐 key 等价（含 `limit` 截断、乱序行、同 `scanned_at`）。
   - 畸形行（缺字段 / 非 dict / 非法 JSON 行）被跳过，不影响其它 key。
   - **N+1 回归**：mock/计数 `_read_jsonl`，断言 `growth_topic_map`（N 个话题）对趋势文件只读 1 次；`pending_followups` 与 `reports_needing_refresh` 同理；`diagnostics_snapshot` 整体读 1 次。这是判定"已根治"的核心断言，用调用次数而不是耗时，避免时间类测试不稳定。
   - 传入 `trend_index` 与不传时，`pending_followups` / `reports_needing_refresh` / `growth_topic_map` 输出相同。
   - `monthly_retrospective_summary(include_topic_map=False)` 无 `topic_map`，其余字段与默认值调用一致。
2. `tests/test_growth_split_endpoints.py`
   - 同一份数据下，`overview` + `diagnostics` + `topic_map` 拼起来与 `/growth/summary` 逐字段一致（忽略 `backfill_candidates_count_computed_at` 这类时间戳）。
   - `overview` 不含 `topic_map` 与 diagnostics；`diagnostics` 超时 / 熔断时返回占位且其它端点不受影响。
   - `refresh_diagnostics=true` 透传。
3. `tests/test_kanban_client_retry.py`
   - 用本地桩 HTTP server：读超时场景下 `retry=False` 只收到 1 次请求，`retry=True` 收到多次；连接失败场景两者都重试。
4. `tests/test_kanban_section_loader.py`（只测 `SectionCache`，不依赖 streamlit）
   - 状态流转 loading → ready；TTL 内不重复请求；过期后返回旧数据并后台刷新；失败时有旧缓存继续展示；`_error` 视同失败；`invalidate` 后旧数据仍可见且触发刷新；同 key 并发只有一个在途请求。
5. `scripts/bench_growth_summary.py`：复现 §2.1 的合成数据压测，供人工对比前后耗时（不进 CI）。

## 9. 范围外：C（HTTP 并发与隔离，单独立项）

已确认本轮不做，仅记录结论，避免丢失：

1. **线程池分舱**：专用 `io` 池（看板读取）与 `slow` 池（LLM、subprocess、`AsyncJobRegistry`），`run_blocking` 增加 `pool=` 参数，大小进 `HttpConfig`。依据：`run_blocking` 与 `AsyncJobRegistry` 当前共用事件循环默认线程池 `min(32, CPU核数+4)`，一次长扫描可能占满。
2. **成长顾问兄弟端点进线程池**：`followups`、`refresh_candidates`、`pursuits` 系列、`health_trend`、`timeline`。A 之后它们是毫秒级，对事件循环的阻塞已大幅缓解，但数据继续增长后仍有风险。
3. **single-flight + 秒级 TTL 缓存**：相同 `where + 参数` 的并发请求共享一次计算。
4. **可观测性**：在 `/v1/self/execution_model_status` 暴露各线程池活跃数与排队数。
5. 另立"事件循环阻塞审计"：`routes.py` 中 290 个路由全部是 `async def`，只有 29 处使用 `run_blocking`，需要先用脚本找出疑似同步读盘的路由，再决定处理顺序。
6. 明确不做：多 uvicorn worker（bridge/agent、cron、async_jobs、各类内存缓存都在进程内）、`limit_concurrency`。

B 完成后 `section_loader` 已经让看板不再同时发起大量重复请求，对 C 里"孤儿线程堆积"问题有间接缓解，但不能替代 C。

## 10. 实施清单与状态

| 序号 | 内容 | 文件 | 状态 |
|---|---|---|---|
| A1 | `load_topic_trend_index` + `_topic_trend_series(index=)` | `src/mini_agent/evolution/growth_advisor.py` | ✅ 已完成（阶段一） |
| A2 | 五个函数接入 `trend_index`，`diagnostics_snapshot` 透传 | 同上 | ✅ 已完成（阶段一） |
| A3 | `monthly_retrospective_summary(include_topic_map=)` | 同上 | ✅ 已完成（阶段一） |
| A4 | 测试 `test_growth_trend_index.py` | `tests/` | ✅ 已完成（阶段一） |
| B1 | 路由拆出三个内部 payload 函数 + 三个新端点 + summary 改为拼装 | `src/mini_agent/api/routes.py` | ✅ 已完成（阶段二） |
| B2 | 测试 `test_growth_split_endpoints.py` | `tests/` | ✅ 已完成（阶段二） |
| B3 | 客户端新方法、`_HTTP_NO_READ_RETRY`、`retry` 参数、成长顾问 GET 关闭读重试 | `apps/mini_agent_kanban/client.py` | ✅ 已完成（阶段三） |
| B4 | 测试 `test_kanban_client_retry.py` | `tests/` | ✅ 已完成（阶段三） |
| B5 | `section_loader.py`（`SectionCache` + `render_section`） | `apps/mini_agent_kanban/section_loader.py` | ✅ 已完成（阶段四） |
| B6 | 测试 `test_kanban_section_loader.py` | `tests/` | ✅ 已完成（阶段四） |
| B7 | `render_growth_tab` 改造：各板块接入 `render_section`、写操作失效、主题地图按需加载（另：`SectionCache.refresh(key, fetch_fn)`） | `apps/mini_agent_kanban/app.py`、`section_loader.py` | ✅ 已完成（阶段五） |
| B7t | 测试 `test_kanban_growth_tab_sections.py`（方案 §8 未单列，B7 实施时补充） | `tests/` | ✅ 已完成（阶段五） |
| B8 | 压测脚本 | `scripts/bench_growth_summary.py` | ✅ 已完成（阶段六） |
| D1 | 文档（随各阶段补；阶段六补压测脚本说明并修正阶段五遗留的一处残句、入口清单 `/growth/summary` 行号；阶段五已更新 `docs/kanban-dashboard-guide.md` 接入小节、`docs/growth-advisor-guide.md`、`next_doc/kanban_feature_inventory.md`、`next_doc/growth_summary_client_timeout_fix.md`）：成长顾问指南（端点、性能说明）、看板指南（加载行为）、旧修复文档补"局限与后续"、端点清单 | `docs/growth-advisor-guide.md`、`docs/kanban-dashboard-guide.md`、`next_doc/growth_summary_client_timeout_fix.md`、`next_doc/kanban_feature_inventory.md`、`docs/architecture_v2/phase10-entrypoint-inventory.md` | ✅ 已完成（阶段一、五、六） |
| D2 | 本文状态表更新为实施记录 | 本文 | ✅ 已完成（阶段六） |
| D3 | 打包：仅改动与新增文件，保留目录结构，便于直接覆盖 | — | ✅ 每阶段均已打包（阶段六为最后一包） |

建议顺序：A1→A4（先用测试锁住等价性）→ B1→B2 → B3→B4 → B5→B6 → B7 → B8 → D。

## 11. 验收标准

1. 合成数据（400 话题 × 60 天趋势）下：`growth_topic_map` < 0.5 s；`/growth/summary` 的趋势文件读取次数 = 1；与改动前输出逐项一致。
2. 看板成长顾问 tab：首屏各板块独立出现；人为让某个端点超时，只有对应板块显示错误与重试按钮，其它板块正常；点击 tab 内按钮不重复拉取 TTL 内的数据。
3. 客户端在读超时场景下对成长顾问端点只发 1 次请求。
4. 新增测试全部通过，现有 `test_growth_advisor*.py` 与看板相关测试无回归。
5. 文档与代码一致，打包内容仅含改动与新增文件。

## 12. 风险与已知限制

1. **数据未在真实环境实测**：§2.1 的耗时来自 1 核沙箱 + 合成数据，绝对值与实际不同；平方级趋势由代码结构决定。实施完成后建议在真实数据上用 `scripts/bench_growth_summary.py` 与 `~/.agent/logs/kanban_client_http.jsonl` 的耗时记录复核。若真实趋势文件很小而仍然超时，说明还有未测到的因素，需要先查清。
2. **Streamlit 嵌套 fragment**：现有部分板块已经是 `@st.fragment`，`render_section` 内部用 `st.rerun(scope="fragment")` 轮询。实施时先在目标 Streamlit 版本（`apps/mini_agent_kanban/requirements.txt` 要求 `>=1.39`）上验证嵌套行为；若有问题，回退为"板块内 `time.sleep` 短轮询 + 整页 `st.rerun()`"，逻辑层 `SectionCache` 不受影响。
3. **共享后台线程池**：`client._EXECUTOR` 只有 8 个 worker，成长顾问 tab 首屏会并发提交约 8 个请求，可能占满并与会话列表等其它 tab 竞争。必要时对首屏请求分批提交（先 overview/diagnostics，再其余），实施时观察。
4. **数据来自不同时刻**：板块间 TTL 不同，同一屏上的"候选总数"与"待处理候选"理论上可能相差一个 TTL。通过写操作主动失效缓解，不追求强一致。
5. **C 未做**：兄弟端点仍在事件循环上执行，A 把它们压到毫秒级但没有消除结构性问题；`pending_followups()` 的 GET 写盘也未处理。
6. **新旧版本需配套**：看板依赖新端点，升级时 daemon 与看板需同时覆盖。


## 13. 实施记录

### 阶段一：方案 A 趋势索引（A1–A4，已完成）

**改动文件**

| 文件 | 内容 |
|---|---|
| `src/mini_agent/evolution/growth_advisor.py` | 新增 `load_topic_trend_index()`；`_topic_trend_series(..., index=)`；`_topic_trend_rising` / `_recent_evidence_delta` / `followup_question_hint` / `pending_followups` / `reports_needing_refresh` / `growth_topic_map` / `diagnostics_snapshot` / `monthly_retrospective_summary` 新增可选 `trend_index`；`monthly_retrospective_summary` 新增 `include_topic_map`（默认 `True`） |
| `tests/test_growth_trend_index.py`（新增） | 16 用例 |
| `docs/growth-advisor-guide.md` | 新增"趋势索引（性能说明）"小节 |

**与方案文字的小差异（实施时的取舍）**

1. `pending_followups` / `reports_needing_refresh` 不传索引时是**懒构建**（第一次真正需要趋势数据时才读一次），而不是函数入口无条件构建：没有到期候选或没有候选通过触发条件时，一次都不读趋势文件。`growth_topic_map` 与 `diagnostics_snapshot` 按方案在入口构建，恒读 1 次。
2. `_topic_trend_series(index=...)` 返回的是索引行的**拷贝**，调用方改返回值不会污染共享索引（有测试覆盖）。
3. 索引对 `scanned_at` 不是数字的行也会跳过（含 bool）——比原函数更宽容，原函数遇到这种命中行，与数字混排时会在排序处抛 `TypeError`。

**验证**

- `tests/test_growth_trend_index.py` 16 用例全过：索引与原函数逐 key 逐 limit 等价；畸形行跳过；`growth_topic_map` / `pending_followups` / `reports_needing_refresh` / `diagnostics_snapshot` 的趋势文件读取次数均为 1（用 `_read_jsonl` 调用计数，不用耗时）；读取次数与话题数无关；传入与不传索引输出相同；`include_topic_map=False` 不含 `topic_map` 且其余字段与默认一致。
- 人为破坏实现（让 `growth_topic_map` 忽略索引）后 4 个用例转红，确认测试有效。
- 回归：`tests/test_growth_advisor*.py` 与其它 `*growth*` 测试共 484 例，483 通过。唯一失败 `test_growth_advisor.py::TestTopicTrend::test_compact_topic_trend_storage_downsamples_old_points` 在**未改动的原始代码上同样失败**（该用例按"周"分桶，结果依赖运行当天落在哪个周边界），与本阶段无关，未处理。
- 看板相关测试（需要 streamlit）本环境未安装，未运行；本阶段未改动任何看板代码。

**已知遗留，留给后续阶段**

- `routes.py` 约 10888 行 `GET /growth/followups` 对每个待回访候选调用一次 `followup_question_hint()`，不传索引时每次读一遍趋势文件。待回访数量通常很小，但阶段二（B1）改路由时顺手在路由里构建一份索引传入即可。
- §11 验收标准第 1 条的"400 话题 < 0.5 s"需要 `scripts/bench_growth_summary.py`（B8）才能量化，本阶段以读取次数断言代替（B8 已在阶段六完成，结果见"阶段六"）。

### 阶段二：方案 B 服务端拆分（B1–B2，已完成）

**改动文件**

| 文件 | 内容 |
|---|---|
| `src/mini_agent/api/routes.py` | 新增 `_growth_overview_payload` / `_growth_diagnostics_payload` / `_growth_topic_map_payload`；新增 `GET /v1/growth/overview`、`/diagnostics`、`/topic_map`；`/growth/summary` 改为拼装；`/growth/followups` 的提示语共用一份趋势索引 |
| `tests/test_growth_split_endpoints.py`（新增） | 10 用例 |
| `docs/growth-advisor-guide.md` | API 清单与"趋势索引"小节补充拆分端点 |
| `docs/architecture_v2/phase10-entrypoint-inventory.md`、`next_doc/kanban_feature_inventory.md` | 端点清单补三行 |

**实施取舍**

1. **`run_blocking` 的 where 名变化**：`/growth/summary` 原先的 `growth_summary_backlog_reports`、`growth_diagnostics_snapshot` 改为与新端点共用的 `growth_overview`、`growth_diagnostics`（新增 `growth_topic_map`、`growth_trend_index`）。summary 与对应拆分端点现在共享熔断状态；这符合"口径一致"的目标，但意味着 summary 与新端点并非互相隔离。`growth_summary_first_touch` 保持不变。
2. **topic_map 降级**：方案没写超时行为。实现为 `/growth/topic_map` 超时/熔断时返回 `{"topic_map": [], "_note": ...}`；`/growth/summary` 里对应降级为空列表。
3. **summary 的概览降级**：概览部分降级为 `{}` 时，`retrospective` 保持 `{}`，不凭空塞入 `topic_map`，与拆分前降级行为一致。
4. **`/growth/followups`**：没有到期候选时不读趋势文件；有则 `pending_followups` 读 1 次 + 提示语共用索引读 1 次，合计 ≤ 2，与候选数无关（原先为 1 + 候选数）。该端点仍是 `async def` 中的同步调用，未进线程池——属于方案 §9 的 C 范围，本阶段不动。
5. 为让 summary 与 diagnostics 的 `trend_index` 在线程池里构建，summary 先单独 `run_blocking(load_topic_trend_index)`，失败则退化为下游各自读取，不影响可用性。

**验证**

- `test_growth_split_endpoints.py` 10 用例全过：三个端点拼起来与 `/growth/summary` 逐字段一致（忽略 `backfill_candidates_count_computed_at`）；overview 无 `topic_map`/diagnostics 且不读趋势文件；diagnostics 超时返回占位、overview/topic_map/summary 仍正常；topic_map 超时降级；`refresh_diagnostics` 透传（含经 summary）；summary 整个请求趋势文件只读 1 次；followups 读取 ≤ 2 次。
- 人为去掉 summary 的索引透传、followups 的索引透传，对应用例转红。
- 回归：growth / api / `*routes*` 相关测试 674 例，672 通过。2 个失败均在**原始代码上同样失败**，与本阶段无关：`test_growth_advisor.py::...downsamples_old_points`（按周分桶，依赖运行日期）、`test_capability_routes_mount.py::TestPersonaDraftRoutes::test_draft_show_publish_full_flow`。
- 看板未改动（仍调用 `/growth/summary`，行为不变）；看板相关测试需 streamlit，本环境未运行。

**注意（新旧版本配套）**：本阶段仅新增服务端端点，`/growth/summary` 响应不变，单独覆盖 daemon 侧不会影响现有看板。

### 阶段三：方案 B 客户端（B3–B4，已完成）

**改动文件**

| 文件 | 内容 |
|---|---|
| `apps/mini_agent_kanban/client.py` | 新增 `_build_http_session_no_read_retry()` / `_HTTP_NO_READ_RETRY`；`_get(..., *, retry=True)`；新增 `growth_overview()` / `growth_diagnostics(refresh=)` / `growth_topic_map()`；成长顾问渲染路径上的 GET 传 `retry=False` |
| `tests/test_kanban_client_retry.py`（新增） | 11 用例 |
| `docs/kanban-dashboard-guide.md` | "AgentClient 封装的 API 端点"补读超时不重试说明 |
| `next_doc/growth_summary_client_timeout_fix.md` | 补"局限与后续"小节 |

**实施取舍**

1. 只改了方案 §6.3 列出的方法。各方法的**超时预算未变**（`growth_followups` 等仍用 `_get` 默认 6s），只是不再被重试放大；若实际使用中这些端点在 6s 内也常超时，应另行评估（它们在阶段一之后是毫秒级）。
2. `retry` 为关键字参数、默认 `True`，其余调用方（含 `_post` 等）行为不变。
3. `growth_candidate_timeline` 与 `growth_pursuit_saturation_trend` 不在方案列表中，未改。

**验证**

- 本地桩 HTTP server 计数：读超时 `retry=False` 只收到 1 次请求，默认 `retry=True` 收到多次；504 同理（1 次 vs 3 次）；连接失败两种模式都重试（各 3 次连接尝试，通过 patch `HTTPConnection.connect` 计数）；两个 Session 的 `Retry` 配置断言（`read=0`、`status=0`、无 `status_forcelist`）；成长顾问 11 个方法均传 `retry=False`，无关端点保持 `retry=True`；新方法的路径与超时预算符合方案。
- 把 `retry=False` 路径改回共享 Session 后，读超时与 504 两个用例转红。
- 回归：workflow editor / phase10 / 本测试共 183 例通过。
- 看板 `app.py` 未改动，仍调用 `growth_summary()`；本阶段无 UI 变化。

### 阶段四：方案 B 板块加载机制（B5–B6，已完成）

**改动文件**

| 文件 | 内容 |
|---|---|
| `apps/mini_agent_kanban/section_loader.py`（新增） | `SectionCache`（`get` / `refresh` / `invalidate` / `invalidate_all` / `peek` / `is_pending`）+ `render_section()` / `get_cache()` / `invalidate_sections()` |
| `tests/test_kanban_section_loader.py`（新增） | 20 用例 |
| `docs/kanban-dashboard-guide.md` | 新增"板块独立加载（section_loader）"一节与相关文件条目 |

本阶段**不接入看板界面**（`app.py` 未改），接入在 B7。

**实施取舍（与方案文字的差异）**

1. **轮询方式**：方案写"用 `st.rerun(scope="fragment")` 做轻量轮询"。但 `app.py` 里已有记录（`_dialog_sync_fetch` / `_render_goal_tree_report_fragment` 注释）：`scope="fragment"` 在"全量重跑阶段首次进入 fragment"时会抛 `StreamlitInvalidLayoutContextError`，永远进不了真正的局部重跑。因此改用 `@st.fragment(run_every=poll_seconds)`：loading / 后台刷新时才带 `run_every`；加载结束后在 fragment 内调用一次整页 `st.rerun()`，让下一轮换成无 `run_every` 的 fragment，停止轮询。代价是每个板块加载完成会触发一次整页重跑（多个板块同时完成时相互合并/打断，开销很小）。这是方案 §12 风险 2 里"需要验证的点"，**真实浏览器里的定时重跑行为仍需人工确认**（见下）。
2. **失败不自动重试**：方案写"请求失败…无缓存则返回 error"，未明确是否自动重试。实现为失败后不自动重试（沿用 `_dialog_sync_fetch` 的约定），避免后端有问题时无限刷屏；`refresh` / `invalidate` 才会重新请求。
3. **失效与在途请求**：方案只说"失效不丢旧数据"。补充了在途期间失效的语义（见指南表格）：用 `gen` / `fresh_gen` 两个计数实现，避免写操作之前发出的请求结果被当成最新。
4. 增加 `ui_key`：同一份数据被两个板块共享（相同 `key`）时，按钮 widget key 不冲突。

**验证**

- `SectionCache` 纯逻辑：状态流转 loading→ready；TTL 内不重复请求；过期返回旧数据并后台刷新；失败时保留旧数据；`_error` 视同失败；失败后不自动重试；`refresh` 只重试本板块；`invalidate` 保留旧数据并刷新；在途期间失效会再刷新一次；提交失败转为 error；共享 key 单在途；真实线程池下并发 `get` 只发 1 次请求。
- 变异检查：让失败后自动重试 → 对应用例转红；让在途失效不触发再刷新 → 对应用例转红。
- Streamlit 包装层用 `streamlit.testing.v1.AppTest` 烟雾测试（Streamlit 1.65 下）：ready 渲染；loading 时不阻塞页面其余内容；error 显示错误与重试按钮、点击不抛异常；`@st.fragment` 外层 + 循环内多个板块嵌套时各板块独立渲染。
- **未验证**：`AppTest` 不会触发 `run_every` 定时重跑，也不是真实浏览器，所以"loading → 自动出现数据 → 轮询停止"的完整链路没有在真实 Streamlit 服务里跑过；也只在 Streamlit 1.65 上测过，`requirements.txt` 的最低版本是 1.39。若真实环境发现问题，方案 §12 的回退是"板块内 `time.sleep` 短轮询 + 整页 `st.rerun()`"，`SectionCache` 不受影响。B7 接入后需要在真实页面点一遍。

### 阶段五：方案 B 看板接入（B7，已完成）

**改动文件**

| 文件 | 内容 |
|---|---|
| `apps/mini_agent_kanban/app.py` | `render_growth_tab` 改为各板块独立加载；新增 `_GROWTH_TTL`、`_growth_section`、`invalidate_growth_sections`、`_growth_write_rerun`、`_fetch_growth_diagnostics` / `_fetch_growth_topic_map` / `_fetch_growth_pursuits_bundle`、`_render_growth_overview_block`、`_render_growth_topic_map`、`_render_growth_pending_candidates`、`_render_growth_first_touch_notice`、`_render_growth_health_trend_section`；followups / align / pursuits / refresh_candidates 拆成"包装 + `_body`"；写操作处统一失效 |
| `apps/mini_agent_kanban/section_loader.py` | `SectionCache.refresh(key, fetch_fn=None)`：`fetch_fn` 仅用于紧接着的一次请求 |
| `tests/test_kanban_growth_tab_sections.py`（新增） | 12 用例（`AppTest` + 带调用计数的假 client） |
| `tests/test_kanban_section_loader.py` | 新增 4 用例（`refresh(key, fetch_fn)`），共 24 例 |
| `docs/kanban-dashboard-guide.md`、`docs/growth-advisor-guide.md`、`next_doc/kanban_feature_inventory.md`、`next_doc/growth_summary_client_timeout_fix.md` | 同步更新加载行为说明 |

**板块与数据源**：按 §6.5 接入，TTL 取方案值（见 `docs/kanban-dashboard-guide.md` "成长顾问 tab 的接入"）。原来整行的"概览数据加载失败"警告和"🔄 重试概览"按钮已删除，由各板块自己的错误提示取代。看板不再调用 `growth_summary()`。布局顺序与改动前一致（诊断 → 健康度趋势 → 画像/关键词 → 概览指标 → 主题地图 → 回访 → 对齐 → 自主推进 → 报告刷新 → 报告查看器 → 待处理候选）。

**实施取舍（与方案文字的差异）**

1. **只有"拥有者"板块用 `render_section`，共享数据的依赖板块读缓存**：报告查看器、待处理候选与概览共用 `growth_overview`，但它们直接 `peek` 概览已加载的缓存，不再各自 `render_section`——否则概览失败时同一条错误会出现 3 次（指标、查看器、待处理）。代价：这两块依赖"概览板块每次渲染都先跑"（它负责拉取/刷新），概览未加载完成时显示一行提示。诊断信息与"画像/关键词"仍各自 `render_section`（共享 key、不同 `ui_key`），因为后者有大量 widget 交互，必须自成 fragment（防止回到"勾一下关键词整页重跑"的旧问题），所以诊断失败时会出现两条错误提示。
2. **`{"_note": ...}` 占位按错误处理**：服务端诊断/主题地图超时降级返回的占位，不当作成功数据缓存（否则会在 TTL 内一直显示空数据），而是转成 `{"_error": ...}`，板块显示错误与重试。概览的降级结果是空列表/空字典，与"确实没有数据"无法区分，仍按原行为显示 0。
3. **首次触达提示在本 session 内保持可见**：方案要求"展示后仍调 ack、且只调一次"。但概览加载完成会触发一次整页重跑，如果提示只在当次渲染显示，用户会看不到。实现为 session 标志：`ack` 成功才置位（失败下次渲染重试），`ack` 后失效概览缓存；提示在同一 session 内一直显示，刷新页面后消失（此时后端已记为已展示）。
4. **`SectionCache.refresh` 增加可选 `fetch_fn`**（方案未写）：为"🔄 刷新诊断数据"服务——该按钮需要带 `refresh_diagnostics=true` 重取一次，之后的普通刷新不能再带。
5. **移除了板块函数上的 `@st.fragment`**：followups / align / pursuits / refresh_candidates / 画像与关键词现在由 `render_section` 内部的 fragment 承载，效果相同（板块内交互只重跑本板块）；而且数据每次从缓存取最新，`_render_growth_profile_and_keywords` 旧注释里"diagnostics 是首次渲染快照"的滞后问题随之消失（已改注释）。系统 tab 里的 `_render_growth_health_trend` 保持原来的同步版本，另加 `_render_growth_health_trend_section` 供成长顾问 tab 使用。
6. **主题地图**：由默认折叠的 expander 改为 `st.toggle`（方案 §6.5 的"按需"写法），打开才请求；展示内容不变。切换开关会触发一次整页重跑。
7. **自主推进板块的组合摘要、关联方向**与主数据在同一个后台任务里顺序取（`_fetch_growth_pursuits_bundle`），后两项失败静默降级，与改动前一致。
8. **失效策略**：`_GROWTH_AFTER_CANDIDATE_CHANGE`（候选状态/Goal 变化）= overview、diagnostics、topic_map、followups、align、pursuits、refresh_candidates；回访记录、对齐、暂停/恢复、报告刷新完成、关键词操作各自只失效相关板块；"立即为我看看"完成后失效全部。方案 §6.5 写的是"按现有 `st.rerun()` 调用点逐个核对"，这里按调用点逐个归类，未在代码里做自动推导。

**验证**

- `test_kanban_growth_tab_sections.py` 12 用例全过：各板块独立出现；一个板块失败（followups 返回 `_error`）只有它显示错误与重试，其余正常；多次重跑后每个端点只请求 1 次（TTL 内不重复拉取）；诊断与画像共用 1 次请求；主题地图未打开时 0 次请求、打开后 1 次；主题地图/诊断的 `_note` 占位显示错误；首次触达提示展示且 `ack` 只调 1 次、已展示过则不弹；强制刷新诊断请求序列为 `[False, True]` 且之后不再带 `True`；自主推进的组合摘要与关联方向各取 1 次。
- 变异检查：取消 `_note` 转错误、去掉 `ack` 的 session 标志、让主题地图不按需、把概览 TTL 置 0、让 `refresh` 忽略 `fetch_fn`，各自让对应用例转红。
- 回归：`test_kanban_section_loader.py` 24 例、`test_kanban_client_retry.py` 11 例、`test_kanban_cron_detail_dialog_delete.py` 等其它看板测试通过。`test_kanban_growth_dragdrop.py` 有 2 个用例失败，**在未改动的原始代码上同样失败**（测试里的假 client 缺少 `get_latest_async_job` / `get_user_profile_preferences`），错误信息与原始代码逐字一致，与本阶段无关，未处理。`test_kanban_config_routes.py`、`test_kanban_realtime_and_status_bugfix.py` 在本环境因缺少 `fastapi` 无法收集，未运行。
- 本阶段没有改服务端；`test_growth_*` 服务端测试同样因缺依赖未在本环境重跑（阶段一、二已通过）。

**未验证 / 已知局限**

- **真实浏览器未验证**：`AppTest` 不触发 `run_every`，所以"loading → 自动出现数据 → 轮询停止"的完整链路、板块加载完成时的整页重跑体验、nested fragment 在真实页面里的表现，都没有在真实 Streamlit 服务里跑过；只在 Streamlit 1.65 上测过，`requirements.txt` 最低版本是 1.39。**需要人工在真实页面点一遍**；若有问题，回退方案仍是 §12 风险 2 的"板块内 `time.sleep` 短轮询 + 整页 `st.rerun()`"（`SectionCache` 不受影响）。
- **首屏并发 7 个后台任务**（overview、diagnostics、health_trend、followups、align、pursuits、refresh_candidates；打开主题地图再加 1 个），占用共享 `_EXECUTOR`（8 个 worker）中的 7 个，会与其它 tab（如会话列表）竞争。没有实现分批提交，上线后观察；需要时先 overview/diagnostics、其余延后。
- 板块内仍有少量同步小请求（只阻塞所在板块的 fragment）：自主推进里每个进行中方向的"饱和度走势"（expander 折叠时也会执行）、"素材"展开里的执行规范、"我的偏好设置"。
- 写操作后的失效范围靠人工归类，新增写操作时需要自己补 `_growth_write_rerun(...)`，否则最多显示一个 TTL 的旧数据。
- 报告查看器 / 待处理候选在写操作之后会短暂显示旧状态，直到概览后台刷新完成（毫秒到秒级）。

### 阶段六：B8 压测脚本与收尾（B8、D，已完成）

**改动文件**

| 文件 | 内容 |
|---|---|
| `scripts/bench_growth_summary.py`（新增） | 合成数据压测，人工运行，不进 CI（见下） |
| `docs/growth-advisor-guide.md` | "趋势索引（性能说明）"后新增"压测脚本"小节；修正阶段五改文档时留下的一处残句（"……只读 1 次。看板按 / 看板已按板块独立加载……"） |
| `docs/architecture_v2/phase10-entrypoint-inventory.md` | 仅把 `GET /v1/growth/summary` 一行的行号 10446→10715（阶段二把它改成拼装后位置变了）。其余行不动，原因见下 |
| `next_doc/growth_tab_split_and_trend_index_plan.md` | 状态表、§11 验收结论、本节 |

**压测脚本**

- 合成模式（默认）：对 50/100/200/400 话题（`--topics` 可改）× 60 点（`--points`）造数据，对 `growth_topic_map` / `pending_followups` / `reports_needing_refresh` / `diagnostics_snapshot` 计时并统计趋势文件读取次数。造数据与 `test_growth_trend_index.py::_seed_topics` 同构；`HOME` 指向临时目录，不会碰真实 `~/.agent`。
- "改动前基线"：逐话题不带索引调用 `_topic_trend_series`，即修复前 `growth_topic_map` 的行为；话题数超过 `--baseline-max`（默认 200）自动跳过，`--no-baseline` 完全跳过。
- `--check`：`growth_topic_map` 超过 `--threshold`（默认 0.5 s）或任一函数趋势读取次数不为 1 时退出码 1。
- `--project-root`：真实数据模式，对已有项目目录只读计时。已验证：对一份造好的数据运行前后，33 个文件的内容和 mtime 均无变化。

**实测结果**（1 核沙箱，合成数据，每话题 60 点，绝对值仅供参考）

| 话题数 | `growth_topic_map` | `reports_needing_refresh` | `diagnostics_snapshot` | 趋势读取次数 | 改动前 N+1 基线 |
|---|---|---|---|---|---|
| 50 | 0.015 s | 0.014 s | 0.033 s | 1 | 0.54 s |
| 200 | 0.057 s | 0.064 s | 0.070 s | 1 | 8.9 s |
| 400（趋势文件 24000 行 / 3.3 MiB） | 0.116 s | 0.117 s | 0.144 s | 1 | 未跑（§2.1 旧实测 32.7 s） |

- 与 §2.1 的旧实测相比，400 话题 `growth_topic_map` 从 32.7 s 降到约 0.12 s；耗时随话题数近似线性增长，不再是平方级。
- `pending_followups` 在合成数据上返回 0 条：合成趋势的证据数一直在涨，被判为"顺延一轮"（逻辑本就如此）。趋势读取路径已被走到（读取次数 1），脚本会打印一行说明。
- 验证 `--check` 能失败：阈值设为 0.00001 s 时退出码为 1 并列出原因。

**§11 验收对照**

| # | 标准 | 结论 | 依据 |
|---|---|---|---|
| 1 | 400 话题 × 60 天：`growth_topic_map` < 0.5 s；summary 趋势读取次数 = 1；输出与改动前逐项一致 | ✅ | 0.116 s；读取次数 1（脚本 + `test_growth_trend_index.py` / `test_growth_split_endpoints.py`）；等价性由阶段一的随机数据对照测试保证 |
| 2 | 看板：首屏各板块独立出现；人为让某端点超时只有对应板块报错并可重试；TTL 内点按钮不重复拉取 | ⚠️ 部分 | `AppTest` 用例覆盖了逻辑（失败隔离、TTL 内请求次数）。**真实页面里"加载中 → 自动出现数据"的体验没有验证**，需人工点一遍 |
| 3 | 读超时场景下对成长顾问端点只发 1 次请求 | ✅ | `test_kanban_client_retry.py`（本地桩 server） |
| 4 | 新增测试通过；现有 growth 与看板测试无回归 | ✅（附 3 个既有失败） | 见下 |
| 5 | 文档与代码一致，打包内容仅含改动与新增文件 | ✅ | 每阶段对比原始包打包 |

**回归结果**：`tests/test_*growth*.py` + `tests/test_kanban_*.py` + 入口清单测试，共 646 例：643 通过、3 失败。这 3 个在**未改动的原始包上同样失败**，与本方案无关，未处理：

- `test_kanban_growth_dragdrop.py` 2 例：测试假 client 缺 `get_latest_async_job` 等方法（阶段五已记录）。
- `test_growth_advisor.py::TestTopicTrend::test_compact_topic_trend_storage_downsamples_old_points`：错误为 `1 != 2`。该用例按周分桶压缩旧快照，我推测与运行日期落在周内哪一天有关，**这只是推测，没有验证**。

本阶段为跑服务端测试在沙箱里额外安装了 `fastapi`、`httpx`、`rich`（项目环境本就有）；阶段二、三因缺依赖未重跑的服务端测试这次一并跑过（`test_growth_split_endpoints.py` 等，均在上面 643 例之内）。

**为什么没有整份重新生成入口清单**：该文件前半是人工分析，只有附录和汇总表是 `scripts/entrypoint_inventory.py` 生成的，整份覆盖会丢人工内容。其它入口（如 `/growth/materials`）的行号落后属于既有状况，与本方案无关，本阶段只改了本方案涉及的 `/growth/summary` 一行；`overview` / `diagnostics` / `topic_map` 三行行号与当前代码一致。

**仍需人工确认（方案收尾时的未决项）**

1. **真实 Streamlit 页面**：板块加载、轮询停止、嵌套 fragment 的实际表现（§12 风险 2，测试环境 Streamlit 1.65，项目最低 1.39）。
2. **真实数据复核**：在真实环境用 `python scripts/bench_growth_summary.py --project-root <项目目录>` 看趋势文件行数与耗时，并对照 `~/.agent/logs/kanban_client_http.jsonl`（§12 风险 1）。若真实趋势文件很小仍然超时，说明还有未测到的因素，需要先查清。
3. **首屏并发**：7 个后台请求占共享线程池 8 个 worker 中的 7 个（§12 风险 3），上线后观察会话列表等 tab 是否被拖慢。
