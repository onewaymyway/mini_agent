# 看板大数据量分页显示改进计划

## 背景

`apps/mini_agent_kanban` 目前在三类数据上都是"全量拉取 + 前端截断"的模式，
数据量小的时候没问题，但 session 跑得足够久 / session 数量足够多之后会出现
明显的性能和体验问题：

1. **对话历史**：`GET /history`（`src/mini_agent/api/routes.py:655`）没有任何
   分页参数，`bridge.agent.history` 里有多少条消息就全部序列化返回。前端
   `_render_chat_messages_body`（`app.py:1207`）拿到全量数据后只渲染
   `entries[-60:]`——网络传输和 JSON 反序列化的开销已经发生了，而且这个
   函数是 2 秒一次的 `st.fragment` 轮询（`app.py:1202`），历史越长，每次
   轮询越慢。用户也完全没法往上翻看更早的历史。
2. **事件流**：`GET /events` 后端本身已经支持 `since_id`/`limit`
   （`routes.py:852-880`，底层是有界的 ring buffer），但前端三处调用
   （`app.py:1099`、`app.py:1312`、`app.py:1486`）全部是每次都从
   `since_id=0` 拉最近 100/30 条，没有利用增量拉取——等于每 2-5 秒重复
   拉取、重复渲染同一批数据。
3. **Session 列表**：`GET /sessions` 只支持 `limit`（≤200，`routes.py:987`），
   没有 `offset`，`SessionManager.list_sessions()`（`session.py:293`）内部
   也是 `entries[:limit]`，超过 200 个 session 之后更早的完全不可见，也
   没有"下一页"的概念。同时 `render_sessions_tab`（`app.py:1414`）为每个
   session 渲染一个 `st.expander`，数量一大 Streamlit 页面本身也会变卡。

## 目标

- 后端接口层面提供真正的分页参数（不再是"全量拉取，前端再截断"）。
- 前端按需加载：默认只加载最近一页，用户主动"加载更早 / 翻页"时才发起
  新请求。
- 事件流改为增量拉取 + 本地滚动窗口，避免重复拉取重复数据。
- 全部改动保持向后兼容：不传新参数时行为与现在完全一致（默认值对齐现状），
  不影响现有 CLI / daemon 的其它调用方。

## 具体方案

### P1：后端分页参数

**`GET /history`**（`routes.py`, `models.py::HistoryResponse`）

新增 Query 参数：
- `limit: int = Query(default=100, le=1000)` —— 本页最多返回多少条
- `before_seq: Optional[int] = Query(default=None)` —— 分页游标，不传表示
  "从最新的一条开始往前取一页"；传了表示"取 seq 小于该值的、最近的
  `limit` 条"（用于"加载更早"）

`HistoryResponse` 新增字段：
- `total: int` —— 该 session 历史消息总条数
- `has_more: bool` —— 是否还有更早的消息未返回
- 给每条消息补一个稳定的 `seq`（返回时按原始下标编号即可，内存里的
  `agent.history` 本身是有序 list，用下标做游标足够，不需要引入额外存储）

实现上因为 `agent.history` 本来就是内存里的一份浅拷贝 list（见
`history_manager.py:82`），分页只是切片，没有 IO 层面的改动，改动集中在
`routes.py` 一个函数里。

**`GET /sessions`**（`routes.py`, `session.py::SessionManager.list_sessions`,
`models.py::SessionsListResponse`）

- `SessionManager.list_sessions()` 增加 `offset: int = 0` 参数，切片改成
  `entries[offset:offset+limit]`，并让方法额外能拿到切片前的总数（新增
  返回一个 `(metas, total)` 的重载或者加一个 `count_sessions()` 方法，
  避免破坏现有调用方对返回类型的假设——采用后者，新增
  `list_sessions_page()` 方法返回 `(metas, total)`，旧的 `list_sessions()`
  保持不变继续给别的调用方用）
- `/sessions` 路由新增 `offset: int = Query(default=0)`，透传下去
- `SessionsListResponse` 新增 `total: int` 字段（现有 `count` 字段含义
  不变，继续表示"本页返回了几条"）

### P2：前端交互改造

**对话历史**（`_render_chat_messages_body`）
- `session_state` 里维护每个 session 的"已加载条数" 
  `chat_loaded_count_<sid>`，默认等于一页（100）
- 默认调用 `client.history(session_id, limit=100)` 只取最新一页
- 对话框顶部放一个"⬆️ 加载更早消息"按钮，点击后 `limit` 加 100 重新请求
  （因为是内存切片，重新请求整页比维护增量缓存更简单可靠，避免深挖
  Streamlit rerun 语义带来的状态同步问题）
- 按钮只在 `has_more=True` 时显示

**Session 列表**（`render_sessions_tab`）
- `session_state["sessions_page"]` 记录当前页码，默认 0
- 底部加"⬅️ 上一页 / 下一页 ➡️"，用 `total` 计算总页数并显示
  "第 X / Y 页"
- 翻页只重新拉当前页的 `limit` 条，不再一次性拉 200 条

**事件流**（`_render_events_panel_body` 及并排对比区）
- `session_state["events_seen_<sid>"]` 维护本地已缓存的事件列表 + 最大
  `since_id`
- 每次 fragment 触发时用 `client.events(since_id=last_id, ...)` 只拉增量，
  `append` 到本地缓存后，本地缓存超过 300 条时从头部裁掉（保持"最近
  N 条"的窗口语义不变，只是不再重复拉取已经看过的部分）
- 切换 session 或清空历史时重置该 session 对应的本地缓存

### P3（本轮不做，记录备查）

- 历史极长时按"轮次"分页（配合 `/turns` 接口），比按消息条数分页更符合
  用户心智模型；本轮先用消息条数分页验证机制，跑一段时间有需要再做。
- `/events` 的 SSE 长连接替代轮询（前面 `_render_sessions_change_banner`
  的注释里已经讨论过原因和阻力，这里不重复）。

## 兼容性 / 回归风险

- 所有新增 query 参数都给了默认值，且默认值让行为与当前一致
  （`/history` 默认 `limit=100` 会比现在的"全量返回"更省流量，但前端渲染
  逻辑本来就只画最后 60 条，100 条完全覆盖，不影响可见内容；如后续发现
  100 不够可以调大默认值）。
- `SessionsListResponse.total` 是新增字段，旧客户端忽略即可，不影响解析。
- 不改动 `/events` 的接口签名，只改前端调用方式，后端零改动、零风险。
- `SessionManager.list_sessions()` 保留不动，新增方法不影响任何现有调用方
  （CLI、daemon 等）。

## 验证方式

- 构造一个消息数 > 200 的长历史 session，验证"加载更早"按钮能正确翻出
  更早消息，且不重复。
- 构造 > 50 个 session，验证列表分页页码、上一页/下一页按钮状态正确。
- 验证事件流增量拉取下不出现同一条事件重复渲染，且 session 切换后本地
  缓存正确重置。
