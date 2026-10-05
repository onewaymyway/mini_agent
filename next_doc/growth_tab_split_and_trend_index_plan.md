# 成长顾问 tab 概览超时治理：趋势索引（A）+ 概览拆分与板块独立加载（B）

- **状态**：实施中——阶段一（方案 A：A1–A4 趋势索引）、阶段二（B1–B2 服务端拆分端点）、阶段三（B3–B4 客户端）已完成，阶段四起（B5 起，section_loader 与看板）待实施（见 §10 实施状态表与 §13 实施记录）
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
| B5 | `section_loader.py`（`SectionCache` + `render_section`） | `apps/mini_agent_kanban/section_loader.py` | 待实施 |
| B6 | 测试 `test_kanban_section_loader.py` | `tests/` | 待实施 |
| B7 | `render_growth_tab` 改造：各板块接入 `render_section`、写操作失效、主题地图按需加载 | `apps/mini_agent_kanban/app.py` | 待实施 |
| B8 | 压测脚本 | `scripts/bench_growth_summary.py` | 待实施 |
| D1 | 文档（阶段一已先行更新 `docs/growth-advisor-guide.md` 趋势索引小节，其余随各阶段补）：成长顾问指南（端点、性能说明）、看板指南（加载行为）、旧修复文档补"局限与后续"、端点清单 | `docs/growth-advisor-guide.md`、`docs/kanban-dashboard-guide.md`、`next_doc/growth_summary_client_timeout_fix.md`、`next_doc/kanban_feature_inventory.md`、`docs/architecture_v2/phase10-entrypoint-inventory.md` | 待实施 |
| D2 | 本文状态表更新为实施记录 | 本文 | 待实施 |
| D3 | 打包：仅改动与新增文件，保留目录结构，便于直接覆盖 | — | 待实施 |

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
- §11 验收标准第 1 条的"400 话题 < 0.5 s"需要 `scripts/bench_growth_summary.py`（B8）才能量化，本阶段以读取次数断言代替。

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
