# Session 列表接口阻塞事件循环问题 — 排查与修复记录

## 1. 现象

daemon 长时间运行后（`http-server` 线程，单进程 asyncio 事件循环跑
uvicorn），看板前端开始报：

```
无法连接到 Agent 服务，请检查地址/Token
请先在左侧确认 API Base URL / Token，并确保 mini-agent daemon 已启动。
```

但命令行确认 daemon 进程本身仍在正常运行、没有崩溃。

日志线索：

- `error.jsonl`：`asyncio:1771` 报 `ConnectionResetError [WinError 10054]
  远程主机强迫关闭了一个现有的连接`，发生在
  `_ProactorBasePipeTransport._call_connection_lost`。
- `http_access.jsonl`：
  ```
  {"type": "request_end", ..., "method": "GET", "path": "/v1/sessions",
   "query": "limit=50&offset=0", "status_code": 200,
   "duration_ms": 264766.0, "slow": true}
  ```
  同一个 `GET /v1/sessions` 请求耗时 **264766ms（约 4.4 分钟）**。

## 2. 根因

mini-agent 的 HTTP 服务是**单个 `uvicorn.Server` 跑在单一 asyncio 事件
循环**里（`api/server.py`，后台线程里 `self._uvicorn_server.run()`，
没有开多 worker）。asyncio 是协作式单线程模型：只要有一个协程在做
同步阻塞操作而不让出控制权，事件循环上的**所有**请求（包括看板的
`/v1/health` 心跳轮询）都会被卡住，谁也处理不了。

`GET /v1/sessions` → `routes.py::list_sessions()` → 
`session.py::SessionManager.list_sessions_page()` → 
`_list_session_entries()`：

```python
for d in self.session_dir.iterdir():
    if d.is_dir() and (d / "meta.json").exists():
        entries.append((d.stat().st_mtime, d, "dir"))
...
```

这是**纯同步磁盘 I/O**：对 `.agent/sessions/` 做 `iterdir()`，再对每个
候选条目 `stat()`，随后 `_read_metas()` 还要逐个打开、读取、`json.loads()`
每一个 `meta.json`。全程没有一次 `await`，也没有丢进线程池——这段代码
直接写在 `async def list_sessions()` 路由函数体里，同步执行期间**独占**
事件循环。

daemon 跑得越久，`.agent/sessions/` 下 session 目录数量越多（该项目里
多处状态设计是"只追加、不清理"，参见 `session_cleanup_design.md`
提到的背景），`_list_session_entries()` + `_read_metas()` 的全量扫描 +
逐个解析耗时就越长，最终能长到几分钟。这几分钟里：

- 看板的 `/v1/health` 心跳请求排在同一个事件循环队列后面，迟迟拿不到
  响应 → 前端心跳检测超时 → 判定"服务不可达"，弹出连接失败提示。
- Windows `ProactorEventLoop` 处理 TCP 连接时，客户端因为等太久而先行
  断开/重连，之后底层 socket 在事件循环空出手来清理连接时，对端早已
  重置，抛出 `[WinError 10054]`。这只是长时间阻塞的**副作用**，不是
  根因。

`RawHistory.append()` 抛 `MemoryError`（同一批日志里的另一个问题）与
本问题同源：项目里多处状态设计成"随会话/时间只增不减"
（`RawHistory._raw` 内存列表、`.agent/sessions/` 目录），短期没问题，
daemon 常驻 + 看板高频轮询的场景下会逐渐放大成性能/内存问题。

## 3. 修复方案

### 3.1 线程池隔离（避免慢请求卡死事件循环）

项目里已有专门为这个场景设计的助手
`mini_agent/utils/blocking_guard.py::run_blocking()`：把同步函数丢进
线程池执行，带硬超时（默认 45s）+ 连续失败熔断（避免熔断打开期间还在
反复起线程做无谓的慢调用）。改造前该助手已经用在 LLM 调用等场景，
本次把它接到 session 相关路由：

- `GET /v1/sessions`（`routes.py::list_sessions`，多用户模式 + 单用户
  模式两条分支）：
  ```python
  metas, total = await run_blocking(
      mgr.list_sessions_page, limit=limit, offset=offset,
      where="list_sessions_page",
  )
  ```
- `GET /v1/sessions/{id}`（`routes.py::get_session_detail`，两条分支）：
  `mgr.load(session_id)` 会读取完整 `history.json`，长会话文件不小，
  同样的问题模式，一并改为
  `await run_blocking(mgr.load, session_id, where="session_load")`。

### 3.2 进程内缓存（从根本上减少重复扫盘）

只做线程池隔离，`_list_session_entries()` 本身的开销并没有消失——
session 数量持续增长下，这个函数还是会越来越慢，长期看线程池也可能被
拖满。因此在 `session.py` 里给 `SessionManager` 加了一层**跨实例**的
进程内缓存：

```python
_METAS_CACHE_TTL = 5.0  # 秒
_metas_cache: dict[str, tuple[float, list[SessionMeta], int]] = {}
_metas_cache_lock = threading.Lock()
```

- key 是 `session_dir` 的绝对路径字符串——之所以不能用实例属性做缓存，
  是因为多用户模式下 `_user_session_manager()` 每次请求都会 `new` 一个
  `SessionManager` 实例，缓存必须挂在模块级、按目录路径 keyed 才能跨
  实例复用。
- `list_sessions()` / `list_sessions_page()` 统一走新增的
  `_all_metas_cached()`：TTL（5 秒，比看板轮询间隔稍长）内直接返回缓存
  的浅拷贝，不重新扫盘；过期后才重新 `_list_session_entries()` +
  `_read_metas()` 一次并回填缓存。
- 所有会改变 session 列表内容的写路径——`save()`、`delete()`、
  `set_pinned()`、`mark_knowledge_extracted()`、
  `mark_summary_backfilled()`——成功后主动调用
  `_invalidate_metas_cache(self.session_dir)`，保证"自己刚做的修改"
  能立刻在下一次查询里看到，不必等 TTL 自然过期。

## 4. 效果与局限

- **短期**：即使缓存过期、确实需要读盘，也只占用一个线程池工作线程，
  事件循环不再被卡住，心跳和其它并发请求正常处理，看板不会再误判
  "无法连接"。
- **中期**：看板轮询命中缓存时完全不碰磁盘，大幅降低重复扫盘频率。
- **局限**：缓存过期瞬间仍然是一次全量目录扫描 + 逐个 JSON 解析，
  session 数量特别大（几千条以上）时这一次的开销依然会随数量线性增长。
  彻底解决需要把 `_list_session_entries()` 换成增量维护的索引（比如
  单独的 sqlite 库或索引文件，创建/更新/删除时增量更新，而不是每次
  全量扫描），这部分留待后续按需实现；短期可配合
  `next_doc/session_cleanup_design.md` 的清理/归档策略控制 session
  目录规模。

## 5. 涉及文件

- `src/mini_agent/session.py` — `SessionManager` 缓存层
  （`_all_metas_cached` / `_invalidate_metas_cache` / `_metas_cache`）
- `src/mini_agent/api/routes.py` — `/v1/sessions`、
  `/v1/sessions/{id}` 路由接入 `run_blocking()`
- `docs/kanban-dashboard-guide.md` — "大数据量下的分页显示"一节补充
  本次修复说明 + 新增"故障排查"小节
