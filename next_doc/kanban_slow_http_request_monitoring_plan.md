# 看板慢请求监控（kanban_slow_http_request_monitoring_plan）

## 背景 / 问题

看板（Streamlit）经常出现"卡死"的情况，但目前缺少直接证据定位是卡在
哪一次 HTTP 请求上：

1. **看板→API 方向**：`apps/mini_agent_kanban/client.py` 里所有请求统一
   走 `_get/_post/_patch/_put/_delete/_get_bytes` 这几个入口，但没有记录
   每次请求实际耗时。看板卡死时，唯一线索是"页面转圈很久"，没法知道是
   哪个接口慢、慢了多久、是不是某个特定 path 反复超时。
2. **API 服务端方向**：`src/mini_agent/api/http_log.py` 的
   `HttpAccessLogMiddleware` 其实已经在记录每个请求的
   `request_start`/`request_end`（含 `duration_ms`）到
   `~/.agent/logs/http_access.jsonl`，但只是落盘，没有任何查询接口，
   看板上也看不到，排查时只能手动登服务器 `grep` 日志文件。

两边都缺一个"超过阈值的慢请求列表"，阈值还需要能改（默认 5 秒，不同
排查场景需要的粒度不一样，5 秒对日常巡检合适，定位一次严重卡死可能要
拉到 1 秒甚至更细）。

## 方案

### 1. 看板客户端计时（新增）

**`apps/mini_agent_kanban/client.py`**：

- 新增内部模块级记录器：
  - 内存环形缓冲区 `collections.deque(maxlen=500)`，进程内实时可读，
    重启即丢（够用于"当前这次运行期间"的排查，不需要额外依赖）。
  - 同时把每条记录 append 一份到本地 JSONL 文件
    `~/.agent/logs/kanban_client_http.jsonl`（写法复用
    `mini_agent.api.http_log` 里 `RotatingFileHandler` 的模式：10MB
    轮转、保留 5 份），这样即使 Streamlit 进程被卡死后强制杀掉重启，
    历史记录仍然可以从文件里翻出来，能看到"卡死前最后几个请求发往
    哪里"。
  - 记录字段：`ts`（本地时间字符串）、`method`、`path`、`params`（去除
    可能的敏感字段后的摘要）、`duration_ms`、`ok`（是否 2xx）、
    `status_code` 或 `error`（异常文本）。
- 在 `_get/_post/_patch/_put/_delete/_get_bytes` 六个方法内部，用
  `time.monotonic()` 包住实际的 `requests` 调用，无论成功/HTTP 错误/
  抛异常，都会记一条（复用一个 `_record_http_call()` 辅助函数，六个
  入口各自在 try/finally 里调用，不改变原有返回值/异常语义）。
- 新增只读方法：
  - `client_http_call_records(threshold_ms: float) -> list[dict]`：从
    内存缓冲区里过滤出 `duration_ms >= threshold_ms` 的记录，按时间倒序。
  - 这个方法读的是内存，不发起网络请求，纯本地操作。

### 2. API 服务端慢请求查询（新增端点，不改动记录逻辑）

**`src/mini_agent/api/http_log.py`**：

- 新增查询函数 `http_access_log_query(threshold_ms, scope="all", limit=200)`，
  写法对齐 `mini_agent.errors.error_log_stats()`：
  - 逐行读取 `resolve_log_path()` 指向的 `http_access.jsonl`。
  - 先按 `type == "request_start"` 建立 `(pid, thread, method, path,
    query)` 到"是否已经等到对应 request_end"的粗略配对（同一 pid+thread
    在同一时刻通常只处理一个请求，用这个近似配对足够定位问题，不追求
    100% 精确的请求级关联）。
  - 收集 `type == "request_end"` 且 `duration_ms >= threshold_ms` 的记录，
    按 `scope`（`all`/`today`）过滤，按耗时倒序取前 `limit` 条。
  - 额外找出"有 `request_start` 但扫到文件末尾都没等到对应
    `request_end`"的记录，标记为 `possibly_hung`（疑似仍在处理中或进程
    异常终止），这类记录对排查"卡死"本身价值最高。
  - 返回：`{"total_requests", "slow_count", "slow_requests": [...],
    "possibly_hung": [...], "by_path": [...], "log_path", "log_exists"}`。

**`src/mini_agent/api/routes.py`**：

- 新增端点 `GET /v1/self/http_access_log/slow`（登录态校验用
  `_require_owner`，不走 `_bridge`——日志文件是进程级全局的，跟
  `error_log_stats` 端点的处理方式一致），参数
  `threshold_ms: float = 5000`、`scope: str = "all"`、`limit: int = 200`，
  内部直接调用上面的查询函数。

**`apps/mini_agent_kanban/client.py`**：

- 新增方法 `http_access_log_slow(threshold_ms, scope="all", limit=200)`，
  封装对上述端点的 `_get` 调用。

### 3. 看板新增 Tab："🐢 慢请求"

**`apps/mini_agent_kanban/app.py`**：

- 新增 `render_slow_requests_tab(client)`，接入 `_BASE_TAB_DEFS`（放在
  `diagnostics` 和 `error_log` 之间）。
- 页面结构：
  - 顶部一个"耗时阈值（秒）"的 `st.number_input`，默认 `5`，存
    `st.session_state["slow_req_threshold_sec"]`，两个子区块共用同一个
    阈值（内部换算成 ms 分别传给两处查询）。
  - 用 `st.tabs(["看板 → API（客户端）", "API 服务端"])` 分两个子视图：
    - **客户端**：调用 `client.client_http_call_records(threshold_ms)`，
      表格展示 时间/方法/路径/参数摘要/耗时ms/结果；上方加一个"清空内存
      记录"按钮（清掉 deque，不影响本地日志文件）。
    - **服务端**：调用 `client.http_access_log_slow(threshold_ms,
      scope, limit)`，`scope` 用一个"全部/仅当天"的 `st.radio`；先展示
      "疑似卡住/未正常结束"的请求（如果有，用 `st.error` 高亮），再展示
      普通慢请求列表，最后展示按 path 聚合的分布表。
  - 都提供"🔄 刷新"按钮（Streamlit 场景下即 `st.rerun()`）。

### 影响范围小结

| 文件 | 改动类型 | 内容 |
|---|---|---|
| `apps/mini_agent_kanban/client.py` | 修改 | 六个 HTTP 入口加计时记录；新增 `client_http_call_records()` / `http_access_log_slow()` |
| `src/mini_agent/api/http_log.py` | 修改 | 新增 `http_access_log_query()` 查询函数 |
| `src/mini_agent/api/routes.py` | 修改 | 新增 `GET /v1/self/http_access_log/slow` |
| `apps/mini_agent_kanban/app.py` | 修改 | 新增 `render_slow_requests_tab()` 并接入 tab 列表 |

不涉及任何数据库 schema 变更，不改变已有接口的返回格式；`http_log.py`
原有的中间件记录逻辑（`_SLOW_THRESHOLD_MS=3000` 的 `slow` 标记）保持不
变，新查询函数只是"读"，阈值由调用方（看板）在查询时传入，跟中间件写
日志时用的固定阈值是两回事，互不影响。
