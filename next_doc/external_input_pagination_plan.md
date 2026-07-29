# External Input Gateway / Watchlist 分页改进计划

## 背景

`external_input_gateway_design.md`（P1-P7）和 `watchlist_notification_goal_design.md`
（P1-P7）落地的两个看板 tab——"🔌 外部输入"（`render_external_input_tab`）
和"🔔 关注与通知"（`render_notification_tab`），以及目标看板卡片上的
"🔗 相关外部信息"折叠面板——目前全部是"一次性拉全部/拉固定 N 条 + 前端
直接渲染"，没有分页机制：

1. **`GET /v1/external_input/events`**：固定 `limit`（默认 50，硬上限 200），
   每次刷新只能看最近一批，翻不到更早的事件。
2. **`GET /v1/notification/dispatch_log`**：同上，固定 `limit=50`。
3. **"待处理告警"面板**：直接复用 `/v1/inbox` 聚合结果里过滤出
   `type=="external_alert"` 的条目，`list_pending_alerts()` 不传 `limit`
   时是全量返回——告警积累多了这里会一次性把全部未读告警渲染出来。
4. **"已注册来源" / "路由规则" / "关注对象" / "分级汇报 tier"** 四个
   配置驱动的列表：接口本身返回全量配置，前端 `for ... in items` 直接
   全部渲染，没有分页。这几类通常不大，但既然是"外部数据"的展示位，
   数量上不封顶（比如 `sources.yaml` 配几十个监控源、`watchlist.yaml`
   配几十个关注对象都是合理场景），应当一并加上分页。
5. **Goal 卡片"🔗 相关外部信息"折叠面板**：`external_context` 服务端已经
   限制最多保留 20 条，但前端展开后是一次性 `for item in reversed(...)`
   全部渲染，20 条一次性展开也偏多。

## 目标

- 天然增长、无上限的数据（事件流水、通知发送记录、待处理告警）：后端
  提供真正的分页参数（`limit`/`offset`），前端用"加载更多"模式按需拉取，
  不再一次性把当前上限内的数据全部渲染。
- 配置驱动、体量有限但仍可能变大的数据（来源、路由规则、关注对象、
  分级汇报 tier）：后端保持全量返回（分页会打乱"路由规则按文件顺序
  即优先级"这类语义），前端加统一的"上一页/下一页"客户端分页。
- Goal 卡片外部信息面板：服务端 20 条上限不变，前端加小页大小
  （5 条/页）的客户端分页。
- 全部改动向后兼容：不传新参数时行为与现状一致。

## 方案

### P1：后端——真正无上限增长的数据加真实分页参数

- `GET /v1/external_input/events`：新增 `offset: int = 0`；响应新增
  `has_more: bool`。
- `GET /v1/notification/dispatch_log`：响应新增 `has_more: bool`
  （`limit` 参数已存在，不用新增）。
- 新增 `GET /v1/external_input/alerts?limit=20`：把"待处理告警"从
  `/v1/inbox` 全量聚合结果里过滤的方式，改成专用的分页只读端点，响应
  `{alerts, total, has_more}`。`/v1/inbox` 本身保持不变（它同时服务顶栏
  待办徽标等其它场景，不适合在这里加分页语义）。
- `external_input/policy.py` 新增 `count_pending_alerts()` 辅助函数
  （复用 `list_pending_alerts()` 同样的"体量不大、全量扫描可接受"取舍，
  不因为加分页就顺带引入索引/游标机制）。

### P2：前端——两种分页交互

**"加载更多"（events / dispatch_log / alerts）**：`session_state` 里维护
每个面板当前的 `limit`（默认等于原先固定值），点击"⬇️ 加载更多"后
`limit` 增加一个步长（events/dispatch_log +50，alerts +20）并重新整页
请求——数据源本身就是"按时间倒序取最近 N 条"，重新请求整页比维护增量
缓存更简单可靠。`has_more=False` 或已达到后端上限时隐藏按钮并提示。

**"上一页/下一页"（sources / policies / watchlist / tiers）**：新增
`_client_side_page()` 辅助函数，对已经全量拉取到的列表做纯前端切片
分页，不改变接口语义（路由规则的匹配优先级顺序等不受影响）。

**Goal 卡片外部信息面板**：复用 `_client_side_page()`，页大小 5。

## 兼容性 / 回归风险

- 所有新增/改动的接口参数都有默认值，默认行为与当前一致。
- 新增 `/v1/external_input/alerts` 端点，不影响 `/v1/inbox` 现有调用方
  （顶栏待办徽标、其它可能的脚本消费者）。
- 前端改动集中在 `render_external_input_tab`/`render_notification_tab`/
  `_render_goal_card`，不影响其它 tab。

## 验证方式

- 构造 > 100 条 `external.*` 事件，验证事件流水"加载更多"能正确翻出
  更早事件、不重复，且到达后端 200 上限后按钮消失并提示。
- 构造 > 100 条通知发送记录，验证同上。
- 构造 > 30 条未处理告警，验证"加载更多"分页正确，`ack` 后列表刷新
  不出现分页错位。
- 构造 > 10 个 source / watchlist 条目，验证"上一页/下一页"翻页正确，
  页码/总数显示准确。
- 构造某个 Goal 挂满 20 条 `external_context`，验证卡片面板分页正确。
