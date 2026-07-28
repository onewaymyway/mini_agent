# 外部输入网关（External Input Gateway）使用指南

> 设计文档：`next_doc/external_input_gateway_design.md`（含分阶段实现状态）。
> 本文档只写"怎么用"，架构取舍和设计动机见设计文档。

当前进度：P1–P5 已完成（事件抽象、独立轮询调度、路由与告警落点、
内置 `watch` 来源、`goal_candidate`/`enqueue_turn` 真正执行并接入
`autonomous_loop.tick()`）。P6（看板面板）尚未开始。

## 1. 核心概念

- **ExternalInputSource**：一种外部信号来源的实现（比如 `watch`）。
- **ExternalInputEvent**：来源产生的一次标准化事件，落到
  `system_events.jsonl` 的 `external.<source_type>.<signal>` 命名空间。
- **IngestionPolicy**：决定一个事件该"只通知"（`notify_only`）、
  "生成目标候选"（`goal_candidate`，写入 `GoalBacklog`）还是"直接触发
  Agent 处理"（`enqueue_turn`，直接提交 `InputQueue`）。默认（未匹配任何
  规则）是 `notify_only`——网关永远不会因为轮询频率高就意外放大成大量
  LLM 调用；`goal_candidate`/`enqueue_turn` 都需要在 `policies.yaml` 里
  显式配置命中规则才会触发。
- 三个落点已经在 `autonomous_loop.tick()`（`_tick_passive()` 阶段）自动
  消费，不需要手动调用 `run_ingestion_policy_once()`——只要 Agent daemon
  在跑（任意 autonomy 档位），`sources.yaml`/`policies.yaml` 就会持续生效。

## 2. 配置

```
.agent/external_input/
  sources.yaml     # 来源配置
  policies.yaml     # 路由规则
  state/<id>.json   # 每个来源的增量状态（自动生成，不需要手写）
  alerts.jsonl       # notify_only 落点的持久化记录（自动生成）
```

### 2.1 `sources.yaml`

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
```

字段说明：

| 字段 | 说明 |
|---|---|
| `id` | 来源实例的唯一标识，同时也是 state 文件名 |
| `type` | 实现类型，目前内置只有 `watch`（自定义来源见 §4） |
| `enabled` | `false` 时 `GatewayPoller` 不会为它起轮询线程 |
| `interval_seconds` | 轮询间隔，默认 300 秒，非法值会回退成默认值 |
| `params` | 传给 `poll(params, state)` 的来源自定义配置 |

### 2.2 `policies.yaml`

```yaml
- match:
    source_type: watch
    signal: threshold
  action: notify_only

- match:
    source_type: watch
    signal: new_item
    fields.priority: high
  action: goal_candidate   # P5 落地前会被识别但跳过（见 §3）

- match:
    source_type: watch
    signal: page_changed
  action: enqueue_turn      # P5 落地前会被识别但跳过（见 §3）
  enqueue:
    initiator: external
    task_template: "监控页面发生变化：{title}\n{detail}"
```

未显式配置路由的事件类型，默认按 `notify_only` 处理。

## 3. watch 来源（内置，P4）

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

## 4. 自定义来源

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

## 5. 查看告警与事件

- `GET /v1/inbox` 会聚合未处理的 `external_alert`（来自 `notify_only` 落点）。
- `POST /v1/inbox/external_alerts/{alert_id}/ack` 标记一条告警已处理。
- 原始事件历史在 `.agent/system_events.jsonl` 里，`event_type` 以
  `external.` 开头的都是外部输入网关产生的。

## 6. 已知限制（P5/P6 之前）

- `goal_candidate`/`enqueue_turn` 目前只是"被识别但跳过"（游标照常推进，
  不会重复处理，也不会被静默降级成 `notify_only`），真正执行留到 P5。
- 没有看板可视化面板（P6），目前只能通过 `/v1/inbox`、
  `run_ingestion_policy_once()` 或直接读 `alerts.jsonl`/`events.jsonl` 查看。
- `watch` 是唯一内置来源；webhook/邮件/日历等来源尚未实现。
