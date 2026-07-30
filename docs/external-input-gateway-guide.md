# 外部输入网关（External Input Gateway）使用指南

> 设计文档：`next_doc/external_input_gateway_design.md`（含分阶段实现状态）。
> 本文档只写"怎么用"，架构取舍和设计动机见设计文档。

当前进度：P1–P8 全部完成（事件抽象、独立轮询调度、路由与告警落点、
内置 `watch`/`weather` 两个来源、`enqueue_turn` 真正执行并接入
`autonomous_loop.tick()`、看板"🔌 外部输入"面板、事件按 `channel`
分类供 daemon 分类处理）。§7 路线图已无待办阶段。看板"🔌 外部输入"面板
的全部列表（来源/路由规则/待处理告警/事件流水）已加分页展示，见 §9.1。

> **P8 变更**：`IngestionPolicy` 的 `goal_candidate` 落点已移除——外部
> 输入不会再被直接写成一个新 Goal。外部信息如果确实与某个**已有**
> Goal/Objective 相关，由 `GoalRelevanceEngine`（见
> `docs/watchlist-notification-guide.md`）独立判定后关联/推进，不走本
> 文档描述的 `policies.yaml` 路由。`policies.yaml` 里现在只有
> `notify_only`/`enqueue_turn` 两个合法 `action`。

## 0. 设计目标

外部输入网关要解决的不是"做一个监控系统"，而是"给 mini_agent 建一层
通用的外部输入接收机制"——RSS/网页监控只是第一个接入的来源，之后接
webhook、邮件、日历提醒等都复用同一套机制，不用每接一种新来源就重新
发明一遍"落地 → 判断要不要处理 → 通知/提交"的逻辑。

**背景问题**：在这个网关出现之前，mini_agent 里能产生一次 Agent 输入
的入口（用户消息、`cron_scheduler` 定时任务、`autonomous_loop` 的
tick）几乎都是"触发 = 立刻提交一次 Agent 任务"，没有"事件产生"与
"是否值得让 Agent 处理"的中间层。如果高频轮询外部世界的信号（比如
RSS 更新、价格变化）直接挂在这些入口上，会退化成"每次轮询都是一次
LLM 调用"，成本不可控。

**四条设计目标**：

1. **统一抽象**：任何"外部世界发生的、可能与用户/Agent 相关的事件"
   都通过同一套 `ExternalInputSource` 接口接入，新增一种来源不用碰
   调度、去重、路由这些通用逻辑。
2. **复用现有事件总线**：不新造一套持久化/消费机制，直接在
   `system_events.py`（`perception/system_events.py`）之上扩展一个
   `external.*` 事件命名空间，复用它已经做好的"文件优先、tier 分级、
   游标消费"语义。
3. **分层路由、按需消耗 LLM**：外部事件产生 ≠ 立刻触发 Agent 推理。
   高频、低成本的是"产生事件"这一层；是否要"通知用户"或"提交给 Agent
   处理"是可配置的路由策略，默认最省钱的路径完全不过 LLM。
4. **watch 只是其中一种 source**：RSS/JSON API/网页 diff 抓取和关键词/
   阈值匹配这类领域逻辑整体下沉为 `ExternalInputSource` 的一个具体实
   现（`watch`），网关层不重复实现这些判断。

## 1. 系统架构

```
┌──────────────────────────────────────────────────────────────────────┐
│                        外部世界 / 各类信号源                             │
│  实时监控(watch: RSS/JSON API/网页diff)  Webhook 回调  邮件/IM 消息      │
│  日历/提醒事件  IoT/传感器  第三方 MCP 服务的主动推送  ……（可持续扩展）   │
└───────────────────────────────┬──────────────────────────────────────┘
                                 │ 每种来源实现 ExternalInputSource 接口
                                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    External Input Gateway（网关核心）                   │
│  ┌────────────┐   ┌───────────────┐   ┌─────────────────────────┐    │
│  │  Registry   │──▶│ GatewayPoller │──▶│ 归一化 ExternalInputEvent │   │
│  │(来源注册表) │   │ (独立轮询线程) │   │  + 来源自带的前置过滤/去重  │    │
│  └────────────┘   └───────────────┘   └───────────┬─────────────┘    │
│                                                     ▼                  │
│                                    system_events.publish()             │
│                              event_type = "external.<source>.<signal>" │
│                                    tier = instant|tick|cron             │
└───────────────────────────────┬──────────────────────────────────────┘
                                 │ 复用现成的 poll_since() 游标消费模型
                                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     IngestionPolicy（路由决策）                        │
│   按事件类型/来源匹配路由规则，两种落点，成本从低到高：                    │
│     1) notify_only     → 写入 Inbox（/v1/inbox 的 external_alert 类型） │
│     2) enqueue_turn    → InputQueue.enqueue(initiator="external", ...) │
│                          真正触发一次 Agent 推理（默认关闭，需显式开启） │
└───────────────────────────────┬──────────────────────────────────────┘
                                 │ autonomous_loop.tick()::_tick_passive()
                                 │ 里的一个消费点，跟其它子系统共用同一节拍
                                 ▼
                   InputQueue / alerts.jsonl（既有子系统）
                                 │
                                 ▼
                看板"🔌 外部输入"页签（只读展示，供人工核对路由是否符合预期）
```

关键设计取舍：**"产生事件"和"消耗 LLM"之间永远隔着一层可配置的路由
策略**，轮询频率再高也只累积到 `events.jsonl` 里，不会自动放大成 LLM
调用次数；`enqueue_turn` 落地后也不重复造轮子，而是直接调用
`InputQueue.enqueue()`，进入既有的 `ResourceArbiter` 门控等消费链路。
**外部输入与 Goal/Objective 的关联不在这条链路里**——见下方"P8 变更"
说明，那部分完全由 `GoalRelevanceEngine` 独立处理。

> 上面是通用架构图（抽象层面，不代表当前项目里 `sources.yaml`/
> `policies.yaml` 实际配了什么、事件实际会流向哪个落点）。当前项目里
> 实际生效（或尚未生效）的具体路由，见 §11"当前实际数据流向"。

## 2. 核心组件

| 组件 | 文件 | 职责 |
|---|---|---|
| `ExternalInputSource` / `ExternalInputEvent` | `external_input/source.py` | 来源扩展点的抽象接口 + 标准化事件表示；`register_source()`/`get_source_class()` 构成一个轻量注册表 |
| `WatchInputSource` | `external_input/builtin/watch.py` | 内置来源实现之一：`rss`/`json_api`/`html_diff` 三种 fetcher + 关键词/字段变化/阈值匹配规则 |
| `WeatherInputSource` | `external_input/builtin/weather.py` | 内置来源实现之二：基于 Open-Meteo 免费预报 API 监控降雨概率/极端气温阈值，`channel` 默认 `weather` |
| `GatewayPoller` | `external_input/poller.py` | 每个 source 一条独立轮询线程，按 `interval_seconds` 节拍调用 `poll()`，处理连续失败退避/熔断，用 `SourceConfig.channel` 给事件回填分类，再发布到 `system_events` |
| `IngestionPolicy`（`load_policies`/`decide_action`/`run_ingestion_policy_once`） | `external_input/policy.py` | 加载 `policies.yaml`，按"首个匹配规则生效"决定事件落点；三种落点的真正执行也在这里 |
| 消费点接入 | `evolution/autonomous_loop.py::_tick_passive()` | 每个 tick 调用一次 `run_ingestion_policy_once()`，不新增独立调度循环，也不受 autonomy 档位限制（notify_only 默认档不该被挡住） |
| 看板面板 | `apps/mini_agent_kanban/app.py::render_external_input_tab()` | 只读展示 source 健康度、路由规则、待处理告警、最近事件流水 |
| REST 端点 | `api/routes.py` | `/v1/external_input/{sources,policies,events}` 供看板/脚本查询；`/v1/inbox`、`/v1/inbox/external_alerts/{id}/ack` 复用既有告警聚合机制 |

## 3. 数据与状态落盘

网关引入的落盘文件都在 `.agent/external_input/` 下（具体字段见 §5
"配置"）：`sources.yaml`/`policies.yaml` 手写配置，`state/<id>.json`/
`alerts.jsonl` 自动生成、不需要手工维护。事件本身不单独开文件，而是
写进已有的 `.agent/system_events.jsonl`（全局事件总线）——`event_type`
以 `external.` 开头的记录都是本网关产生的，这也是它能直接复用
`poll_since()` 游标消费模型、不用新造一套持久化机制的原因（详见 §4
核心概念）。

## 4. 核心概念

- **ExternalInputSource**：一种外部信号来源的实现（比如 `watch`）。
- **ExternalInputEvent**：来源产生的一次标准化事件，落到
  `system_events.jsonl` 的 `external.<source_type>.<signal>` 命名空间。
- **IngestionPolicy**：决定一个事件该"只通知"（`notify_only`）还是
  "直接触发 Agent 处理"（`enqueue_turn`，直接提交 `InputQueue`）。默认
  （未匹配任何规则）是 `notify_only`——网关永远不会因为轮询频率高就
  意外放大成大量 LLM 调用；`enqueue_turn` 需要在 `policies.yaml` 里
  显式配置命中规则才会触发。外部输入如果与某个已有 Goal/Objective
  相关，不经过这里的路由，而是由 `GoalRelevanceEngine`（见
  `docs/watchlist-notification-guide.md`）独立判定后关联/推进。
- 两个落点已经在 `autonomous_loop.tick()`（`_tick_passive()` 阶段）自动
  消费，不需要手动调用 `run_ingestion_policy_once()`——只要 Agent daemon
  在跑（任意 autonomy 档位），`sources.yaml`/`policies.yaml` 就会持续生效。
- **channel（分类频道）**：daemon 处理一批事件之前，先按 `channel` 把
  它们分好类——每个 source 在 `sources.yaml` 里可以配一个 `channel`
  字段，缺省时自动等于 `type`（比如所有 `type: weather` 的 source 默认
  都在 `weather` 频道，不用手动配置）。`policies.yaml` 里可以直接按
  `channel` 写路由规则，`run_ingestion_policy_once()` 内部也会先按频道
  分组再处理，方便 daemon／看板／诊断按类别观察"这一批外部输入里，
  天气类处理了几条、资讯类处理了几条"。详见 §5.3、§10。

## 5. 配置

需要手写的只有 `sources.yaml`（配来源）和 `policies.yaml`（配路由规则），
两者都放在 `.agent/external_input/` 下（完整目录结构见 §3）。

### 5.1 `sources.yaml`

```yaml
sources:
  - id: hn_rss
    type: watch
    enabled: true
    interval_seconds: 300
    params:
      source_id: hn_rss   # 必须和上面的 id 保持一致，见下方"watch 来源"说明
      fetcher: rss
      url: "https://example.com/feed.xml"
      keywords: ["claude", "anthropic"]   # 可选：标题命中才产生事件

  - id: btc_price
    type: watch
    enabled: true
    interval_seconds: 60
    params:
      source_id: btc_price
      fetcher: json_api
      url: "https://example.com/api/price"
      field_path: "data.price"
      mode: threshold
      op: lt
      threshold: 50000

  - id: product_page
    type: watch
    enabled: true
    interval_seconds: 600
    params:
      source_id: product_page
      fetcher: html_diff
      url: "https://example.com/product/123"
      keywords: ["现货", "补货"]

  - id: home_weather
    type: weather
    enabled: true
    interval_seconds: 1800   # 天气预报不需要高频轮询，半小时一次足够
    channel: weather          # 可省略，type: weather 默认就落在 weather 频道
    params:
      latitude: 39.9042
      longitude: 116.4074
      rain_probability_threshold: 60
      temperature_high_threshold: 35
      temperature_low_threshold: 0
      lookahead_hours: 12
      daily_summary: true
```

字段说明：

| 字段 | 说明 |
|---|---|
| `id` | 来源实例的唯一标识，同时也是 state 文件名 |
| `type` | 实现类型，目前内置有 `watch`/`weather`（自定义来源见 §7） |
| `enabled` | `false` 时 `GatewayPoller` 不会为它起轮询线程 |
| `interval_seconds` | 轮询间隔，默认 300 秒，非法值会回退成默认值 |
| `channel` | 该来源产生的事件归属的分类频道，供 `policies.yaml`/daemon 按类别处理（见 §5.3）；缺省等于 `type` |
| `params` | 传给 `poll(params, state)` 的来源自定义配置 |

### 5.2 `policies.yaml`

```yaml
- match:
    source_type: watch
    signal: threshold
  action: notify_only

- match:
    source_type: watch
    signal: new_item
    fields.priority: high
  action: enqueue_turn      # 高优先级新条目直接提交给 Agent 判断，成本较高，注意匹配条件要收紧

- match:
    source_type: watch
    signal: page_changed
  action: enqueue_turn      # 直接提交 InputQueue，成本最高，请谨慎配置匹配条件
  enqueue:
    initiator: external
    task_template: "监控页面发生变化：{title}\n{detail}"
```

未显式配置路由的事件类型，默认按 `notify_only` 处理。

### 5.3 按 `channel` 路由（P7）

除了 `source_type`/`signal`/`fields.<key>`，`match` 还支持 `channel`
维度，可以不区分具体 source，直接按频道整体配置路由：

```yaml
- match:
    channel: weather
  action: notify_only   # 天气类事件统一走通知，不生成 Goal/不触发 Agent

- match:
    channel: weather
    signal: high_temperature
  action: enqueue_turn   # 极端高温单独直接触发一次 Agent 判断（比如提醒检查作物/设备）；
                          # 如果只是想让"已有的相关 Goal 知道这条天气信息"，不需要在这里配置
                          # 任何规则——GoalRelevanceEngine 会独立判定并自动关联/推进
```

规则匹配顺序不变（第一条命中的生效），`channel` 只是多了一种可以用来
写更"粗粒度"规则的匹配条件，不需要跟 `source_type`/`signal` 一起写。

## 6. watch 来源（内置）

`WatchInputSource`（`src/mini_agent/external_input/builtin/watch.py`）提供
三种 `fetcher`：

| fetcher | 用途 | 产生的 signal |
|---|---|---|
| `rss` | 抓取 RSS/Atom feed，检测新条目 | `new_item` |
| `json_api` | 抓取 JSON API，取一个字段比对 | `field_changed`（默认）/ `threshold`（`mode: threshold`） |
| `html_diff` | 抓取网页纯文本、比对内容摘要 | `page_changed` |

通用可选参数：

- `keywords`：字符串列表，命中标题/正文关键词才产生事件（大小写不敏感）；
  不设置则不做关键词过滤。
- `timeout`：HTTP 超时秒数，默认 10。
- `suggested_tier`：事件建议 tier（`instant`/`tick`/`cron`），默认 `tick`。

**重要**：`params.source_id` 必须和 `sources.yaml` 里该条目的顶层 `id`
保持一致——`GatewayPoller` 调用 `poll(cfg.params, state)` 时不会自动
注入 `cfg.id`，如果 `source_id` 缺失，事件依然会正常产生，但
`system_events` 里的 `source` 标签会是 `external:`（空），不便于按来源
筛查。

`threshold` 模式下，命中阈值只在"从未命中 -> 命中"的边沿发一次事件，
持续命中不会每轮都重复告警；等数值回到阈值范围外再重新跌入/超出时，
会重新触发一次。

## 7. weather 来源（内置，P7）

`WeatherInputSource`（`src/mini_agent/external_input/builtin/weather.py`）
监控某个经纬度的天气预报，数据来自 [Open-Meteo](https://open-meteo.com)
（免费、不需要 API key）。可配置参数（都放在 `params` 下）：

| 参数 | 说明 |
|---|---|
| `latitude` / `longitude` | 必填，监控地点的经纬度 |
| `rain_probability_threshold` | 可选，默认 60；未来 `lookahead_hours` 内命中该降雨概率（%）即发 `rain_alert` |
| `temperature_high_threshold` | 可选；命中即发 `high_temperature` |
| `temperature_low_threshold` | 可选；命中即发 `low_temperature` |
| `lookahead_hours` | 可选，默认 12；预报向前看多少小时 |
| `daily_summary` | 可选，默认 false；为 true 时每天第一次轮询额外发一条 `daily_forecast` 摘要事件 |

跟 `watch` 的 `threshold` 模式一样，阈值告警只在"未命中 -> 命中"的边沿
触发一次，持续命中不会每轮重复告警；数值先回落到阈值外、再次命中时才
会再触发一次。`channel` 默认落在 `weather`（除非在 `sources.yaml` 里
显式覆盖），配合 §5.3 的例子可以把所有天气类事件统一路由。

由于天气预报本身更新不频繁，建议 `interval_seconds` 设置成 30 分钟以上
（示例见 §5.1），没有必要跟资讯类 source 一样高频轮询。

## 8. 自定义来源

新增一种来源不需要碰网关代码，只需要实现接口并注册：

```python
from mini_agent.external_input.source import ExternalInputSource, ExternalInputEvent, register_source

@register_source("my_source")
class MySource(ExternalInputSource):
    def poll(self, params: dict, state: dict) -> tuple[list[ExternalInputEvent], dict]:
        # 禁止在这里调用 LLM/Agent；跨轮询状态只能放在 state 里返回
        ...
        return events, new_state
```

只要这个模块在 `GatewayPoller` 启动前被 import 过（放进
`src/mini_agent/external_input/builtin/` 并在其 `__init__.py` 里 import，
或者由调用方自行 import），`sources.yaml` 里配置 `type: my_source` 即可
被识别。

## 9. 查看告警与事件

- `GET /v1/inbox` 会聚合未处理的 `external_alert`（来自 `notify_only` 落点）。
- `POST /v1/inbox/external_alerts/{alert_id}/ack` 标记一条告警已处理。
- 原始事件历史在 `.agent/system_events.jsonl` 里，`event_type` 以
  `external.` 开头的都是外部输入网关产生的。
- 看板（`apps/mini_agent_kanban`）的"🔌 外部输入"页签把上面这些信息
  可视化展示，同时新增四个只读 REST 端点供页面调用（也可以直接
  `curl` 查看，均需要 owner 权限）：
  - `GET /v1/external_input/sources` — 已配置 source 的类型/启用状态/
    运行状态/健康度（连续失败次数、是否熔断、上次轮询时间）；如果当前
    进程没有在跑 `GatewayPoller`（比如非 daemon 模式），健康相关字段
    全部是 `null`，响应里的 `poller_available: false` 会说明这一点。
    数量多时看板用纯前端"上一页/下一页"分页展示（每页 10 条），接口
    本身仍然全量返回。
  - `GET /v1/external_input/policies` — `policies.yaml` 里的规则，按
    文件顺序返回（即匹配优先级，第一条命中的生效）。同上，接口全量
    返回，看板前端分页展示（每页 10 条），页面显示的序号是规则在
    文件里的真实下标，不受翻页影响。
  - `GET /v1/external_input/events?limit=50&offset=0` — 最近的
    `external.*` 事件流水（只读尾读，不会推进任何消费者的游标，`limit`
    上限 200）。`offset` 配合看板"⬇️ 加载更多"按钮分页；响应里的
    `has_more` 表示是否还有更早的事件未返回。
  - `GET /v1/external_input/alerts?limit=20&offset=0` — 分页返回未处理
    的 `notify_only` 告警（`alerts.jsonl`），响应含 `total`/`has_more`。
    看板"待处理告警"面板用这个端点而不是 `/v1/inbox`（`/v1/inbox` 同时
    服务顶栏待办徽标等其它场景，聚合结果本身不分页）。

## 10. 已知限制

- `watch`/`weather` 是目前仅有的两个内置来源；webhook/邮件/日历等来源
  尚未实现，需要时可以参考 `builtin/watch.py` 或 `builtin/weather.py`
  的写法新增一个 `ExternalInputSource` 子类。
- 看板面板和四个 REST 端点都是只读展示，`sources.yaml`/`policies.yaml`
  仍然只能通过直接编辑配置文件来修改（没有在线编辑表单）。
- `channel` 目前只在 `policies.yaml` 路由匹配和 `run_ingestion_policy_once()`
  的 `by_channel` 统计里生效；`GET /v1/external_input/events` 尚不支持
  按 `channel` 过滤（只能拿到最近事件流水再自行按 `payload.channel`
  筛选），看板页面也还没有"按频道筛选"的 UI，这些留给后续有需要时再补。

## 11. 当前实际数据流向

§1 的架构图是通用抽象图，本节画的是**当前项目里 `sources.yaml` +
`policies.yaml` 实际生效的具体路由**，随这两个配置文件变化会过期，
改配置时请同步更新本节。

### 11.1 前提开关：GatewayPoller 是否真的在跑

`GatewayPoller` 在 `api/server.py::HttpServer._build_autonomous_loop()`
里无条件构造并 `start()`，不受 autonomy 档位限制；但 `HttpServer` 本身
只有在 HTTP API 服务开启时才会被构造（`HttpConfig.enabled` 默认
`false`）：

```
agent_config.json 里 http_enabled: true   （或启动加 --http）
              │
              ▼ 是
    HttpServer 被构造 → GatewayPoller(paths).start()
              │                    │
              │                    ▼
              │        sources.yaml 里 enabled: true 的每个 source
              │        各起一条独立轮询线程，按 interval_seconds 轮询
              ▼
      否 → HttpServer 不存在 → GatewayPoller 从未被构造
            → sources.yaml 配了也不会跑，events.jsonl 不会有新记录
```

### 11.2 实际配置下的路由分支

当前 `.agent/external_input/sources.yaml` 有 5 个 source：
`beijing_weather`（channel: `weather`）+ 4 个 RSS（`arxiv_cs_ai` /
`hn_frontpage` / `sspai_feed` / `ithome_feed`，统一打了
`channel: agent_watch`，并在 `params.keywords` 里做了标题前置过滤——
只有标题命中 agent 相关关键词才会产生事件）。配合当前
`.agent/external_input/policies.yaml`，实际流向如下：

```
                        sources.yaml（5 个 source，均 enabled: true）
                                        │
        ┌───────────────────────────────┼───────────────────────────────┐
        ▼                               ▼                               ▼
 beijing_weather                 arxiv_cs_ai / hn_frontpage        （其余未来新增的
 type: weather                   sspai_feed / ithome_feed           source，走兜底）
 channel: weather                 type: watch, channel: agent_watch
        │                        keywords 已过滤（标题命中才发事件）
        ▼                               ▼
 signal: rain_alert /            signal: new_item
 high_temperature /                     │
 low_temperature /                      │
 daily_forecast                         │
        │                               │
        ▼                               ▼
 system_events.jsonl（event_type = "external.weather.*" / "external.watch.new_item"）
        │                               │
        └───────────────┬───────────────┘
                         │
        ┌────────────────┴────────────────────────────────┐
        ▼                                                  ▼
 autonomous_loop.tick()                          （goal_relevance.py 的 cron 任务，
 → _tick_passive()                                独立于 autonomous_loop.tick()，
 → run_ingestion_policy_once()                    见 §4/`docs/watchlist-notification-guide.md`）
（按 policies.yaml 第一条命中规则路由，                → Stage①规则粗筛 active_goals()
  逐 channel 分组处理；当前两条规则都是                  → Stage②LLM 判定确有相关的 Goal
  notify_only，兜底也是 notify_only）                   → attach_external_context()/
        │                                              try_advance_goal()（只挂载/推进
        ▼                                              已有 Goal，从不创建新 Goal）
 alerts.jsonl（待确认告警）
        │
        ▼
 GET /v1/inbox 聚合展示
 POST .../ack 标记已读
        │
        ▼
 看板"🔌 外部输入"页签（只读，人工核对路由是否符合预期）
```

**当前配置里没有任何 `enqueue_turn` 规则**（成本最高、需要显式配置的
落点），也就是说无论天气还是 RSS 事件，都不会自动触发一次 Agent 推理；
两条路由规则命中后都只是写进 `alerts.jsonl` 供人工核对。**外部输入与
Goal/Objective 的关联完全不经过 `policies.yaml`**——如果某条事件的内容
确实与某个已有 Goal 相关，由独立运行的 `GoalRelevanceEngine` 判定后
关联/推进，`IngestionPolicy` 不再有任何"生成候选 Goal"的落点（P8 已
移除，见 `next_doc/external_input_gateway_design.md` §P8）。

### 11.3 目前仍未生效的前提

即使上面两份配置文件已经就位，只要满足以下任一条件，这套流程仍然是
静止的，`system_events.jsonl` 不会新增 `external.*` 记录：

- `agent_config.json` 未设置 `http_enabled: true` 且启动时未加 `--http`
  （§11.1 的前提开关未打开）；
- 项目目录下没有以 daemon/长驻方式运行 Agent 进程（`GatewayPoller`
  和 `autonomous_loop.tick()` 都需要进程持续存活才能轮询/消费）。
