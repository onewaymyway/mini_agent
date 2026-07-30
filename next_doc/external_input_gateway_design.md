# 外部输入网关（External Input Gateway）设计方案

- **版本**: v1.0（草案）
- **背景任务**: 在实时外部事件监控系统（watch）之上，抽象出一层通用的"外部输入接收机制"，让 watch 只是众多外部输入源之一
- **关联文档**: 上一轮讨论的"实时外部事件监控系统"设计（watch 子系统）

---

## 1. 背景与问题

### 1.1 现状：mini_agent 里"能产生一次 Agent 输入"的入口盘点

| 入口 | 触发方式 | initiator | 是否直接消耗 LLM |
|---|---|---|---|
| 用户在看板/CLI 里发消息 | 人工输入 | `user` | 是（本来就该消耗） |
| `cron_scheduler` 定时 job | 固定 interval/cron 表达式 | `cron` | 是，每次触发都提交一整条任务给 `InputQueue` |
| `autonomous_loop` 的 tick / 主动执行 | 固定节拍 | `scheduled` / `autonomous` | 是 |
| `system_events`（内部事件总线，`perception/system_events.py`） | 各子系统在"状态边沿"时 `publish()`，消费者按自己节拍 `poll_since()` | 视消费者而定（如 `soft_goal_deriver` 把命中事件转成 GoalBacklog 候选，不直接进 InputQueue） | 视消费者实现 |

**共性问题**：以上四类入口里，只有 `system_events` 具备"轻量事件 + 消费者自行决定处理节奏"的解耦设计；其余三类（尤其是 `cron_scheduler`）本质上是"触发 = 立刻提交一次 Agent 任务"，没有中间的"事件产生"与"是否值得让 Agent 处理"的分离。

而"实时外部事件监控系统"要做的事——**高频轮询外部世界、纯脚本判断、命中才可能需要 Agent 介入**——如果直接挂在 `cron_scheduler` 上，会退化成"每次轮询都是一次 LLM 调用"，这是不可接受的成本模型；如果单独另起一套通知机制，又会导致以后每接入一种新的外部输入（webhook 回调、邮件触发、IM 消息、IoT 传感器、日历提醒……）都要重新发明一遍"落地 → 判断要不要让 Agent 处理 → 通知/提交"这一整套逻辑。

### 1.2 设计目标

不只是做"一个监控系统"，而是做"一层通用的外部输入接收机制"，监控系统只是第一个接入的 source：

1. **统一抽象**：任何"外部世界发生的、可能与用户/Agent 相关的事件"都通过同一套接口接入，新增一种来源只需实现一个 `ExternalInputSource`，不用碰调度、去重、路由这些通用逻辑。
2. **复用现有事件总线**：不新造一套持久化/消费机制，直接在 `system_events.py` 之上扩展一个 `external.*` 事件命名空间，复用它已经做好的"文件优先、tier 分级、游标消费"语义。
3. **分层路由、按需消耗 LLM**：外部事件产生 ≠ 立刻触发 Agent 推理。高频、低成本的是"产生事件"这一层；是否要"通知用户"或"提交给 Agent 处理"是可配置的路由策略，默认最省钱的路径不过 LLM。
4. **watch 只是其中一种 source**：上一轮设计的抓取/规则匹配逻辑整体下沉为 `ExternalInputSource` 的一个具体实现，网关层不重复实现"关键词/阈值匹配"这类领域逻辑。

---

## 2. 整体架构

```
┌──────────────────────────────────────────────────────────────────────┐
│                        外部世界 / 各类信号源                             │
│  实时监控(watch: RSS/JSON API/网页diff)  Webhook 回调  邮件/IM 消息      │
│  日历/提醒事件  IoT/传感器  第三方 MCP 服务的主动推送  ……（可持续扩展）   │
└───────────────────────────────┬──────────────────────────────────────┘
                                 │ 每种来源实现 ExternalInputSource 接口
                                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    External Input Gateway（新增，本设计核心）           │
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
│                     IngestionPolicy（路由决策，新增）                    │
│   按事件类型/来源匹配路由规则，三种落点，成本从低到高：                    │
│     1) notify_only     → 写入 Inbox（/v1/inbox 新增 external_alert 类型）│
│     2) goal_candidate  → 写入 GoalBacklog 候选（复用 soft_goal_deriver  │
│                          的落地方式，用户/自主循环再决定是否升级为任务）  │
│     3) enqueue_turn    → InputQueue.enqueue(initiator="external", ...) │
│                          真正触发一次 Agent 推理（默认关闭，需显式开启） │
└──────────────────────────────────────────────────────────────────────┘
```

关键设计取舍：**"产生事件"和"消耗 LLM"之间永远隔着一层可配置的路由策略**，轮询频率再高也只累积到 `events.jsonl` 里，不会自动放大成 LLM 调用次数。

---

## 3. 核心组件设计

### 3.1 `ExternalInputSource`：来源扩展点

新增模块 `src/mini_agent/external_input/`，风格对齐 `workflow/` 与 `env_info/` 已有的注册表模式。

```python
# src/mini_agent/external_input/source.py

@dataclass
class ExternalInputEvent:
    """一次外部输入的标准化表示，供后续路由/展示统一处理。"""
    id: str                  # 来源内部唯一 id，用于去重
    source_id: str           # 具体的 source 实例 id（用户配置时起的名字）
    source_type: str         # 实现类型，如 "watch" / "webhook" / "calendar"
    signal: str              # 具体信号名，如 "new_item" / "price_drop" / "message"
    title: str
    detail: str = ""
    url: Optional[str] = None
    fields: dict = field(default_factory=dict)   # 结构化字段，供路由规则匹配
    occurred_at: float = field(default_factory=time.time)
    suggested_tier: str = "tick"   # instant | tick | cron，来源自己给个建议值，
                                    # 网关会结合路由规则做最终裁决


class ExternalInputSource(ABC):
    """所有外部输入来源的统一接口。子类只需要关心"怎么拿到信号"，
    不需要关心调度节奏、去重、路由——这些都由网关统一处理。"""

    source_type: str  # 注册名

    @abstractmethod
    def poll(self, params: dict, state: dict) -> tuple[list[ExternalInputEvent], dict]:
        """
        单次轮询。要求：
          - 禁止在此处调用 LLM/Agent（保持轮询成本可控、可预测）；
          - state 用于跨轮询的增量状态（比如 watch 的去重游标、webhook 的
            last_delivery_id），返回新的 state 由网关落盘保存；
          - 只返回"确实是新增/变化"的事件，重复信号的过滤是来源自己的职责
            （网关只做兜底的 event.id 级去重，不重复实现来源特定的语义去重）。
        """


# 注册表：新增来源只需要 @register_source("xxx") 装饰一个实现类
_REGISTRY: dict[str, type[ExternalInputSource]] = {}

def register_source(source_type: str):
    def _wrap(cls):
        _REGISTRY[source_type] = cls
        return cls
    return _wrap

def get_source_class(source_type: str) -> type[ExternalInputSource]:
    ...
```

### 3.2 watch 系统作为第一个 source 实现

上一轮设计的 `WatchSource`（RSS/JSON API/网页 diff 抓取）+ `RuleEngine`（新增/字段变化/关键词/阈值匹配）整体作为 `ExternalInputSource` 的具体实现：

```python
@register_source("watch")
class WatchInputSource(ExternalInputSource):
    source_type = "watch"

    def poll(self, params: dict, state: dict) -> tuple[list[ExternalInputEvent], dict]:
        # 1. 用 params 里配置的具体 fetcher（rss/json_api/html_diff）抓取
        # 2. 用 params 里配置的 RuleEngine 规则做领域内的前置过滤
        #    （"新番更新""价格降了20%"这类判断在这一层完成，不是网关的职责）
        # 3. 只把命中规则的条目转成 ExternalInputEvent 返回
        ...
```

这样网关本身完全不关心"RSS 怎么解析""价格阈值怎么算"，这些留在 source 内部；网关只负责"轮询节奏、去重兜底、落盘发布、路由决策"这一层通用逻辑。未来接入 webhook 接收器、邮件轮询、日历提醒，同样只需要写一个新的 `ExternalInputSource` 子类，不用碰网关代码。

### 3.3 `GatewayPoller`：独立轮询调度

```
src/mini_agent/external_input/
  __init__.py
  source.py          # 上面的抽象基类 + registry
  poller.py          # 独立后台线程，按每个 source 各自的 interval 调用 poll()
  policy.py          # IngestionPolicy：事件 -> 路由决策
  config.py          # source 配置 + 路由规则的 YAML 加载
  builtin/
    watch.py         # WatchInputSource（复用上一轮 watch 设计）
```

`GatewayPoller` 的调度模型完全对齐项目现有风格（"轮询 + 状态文件"，见 `system_events.py` 设计说明里的三条硬约束）：

- 每个 source 一个 `interval_seconds`，独立线程池按各自节奏调用 `poll()`；
- 连续失败自动退避 + 熔断（复用 `workflow/watchdog.py` 的"连续同类失败提前判定"思路），并把 source 健康状态本身也作为一条 `tier="cron"` 的事件发布出去，供看板展示"该来源疑似失效"；
- `poll()` 返回的每个 `ExternalInputEvent` 经过网关兜底去重后，调用：

```python
system_events.publish(
    paths, source=f"external:{source_id}",
    event_type=f"external.{source_type}.{signal}",
    tier=event.suggested_tier,
    payload=event_dict,
)
```

不新增第二套持久化格式，`events.jsonl` 里天然就能看到所有外部输入的历史，`poll_since()` 的游标机制也直接复用，不用重新写"谁消费到哪了"的状态管理。

### 3.4 `IngestionPolicy`：路由决策（新增的唯一"业务判断层"）

```yaml
# .agent/external_input/policies.yaml
- match:
    source_type: watch
    signal: price_drop
  action: notify_only          # 默认最省钱：只通知，不惊动 LLM

- match:
    source_type: watch
    signal: new_episode
    fields.priority: high      # 来源自己打的标，比如"追更提醒"这种用户主动关心的
  action: goal_candidate       # 生成一个 GoalBacklog 候选，走现有自主循环的
                                # 评估/门控流程，而不是绕过门控直接执行

- match:
    source_type: webhook
    signal: urgent_message
  action: enqueue_turn         # 只有明确需要"马上处理"的信号才直接进 InputQueue
  enqueue:
    initiator: external
    task_template: "收到紧急外部消息：{title}\n{detail}\n请判断是否需要处理。"
```

三种落点各自的实现：

1. **`notify_only`**：写入一个新的 `external_alerts` 存储（复用 `/v1/inbox` 的聚合方式，新增 `type: external_alert`），看板顶栏"全局待办中心"直接就能展示，不用新建 UI。
2. **`goal_candidate`**：复用 `soft_goal_deriver.py` 已经跑通的模式——消费 `system_events`、生成 GoalBacklog 候选、交给现有的目标评估/资源门控（`ResourceArbiter`）流程决定要不要真正执行，好处是**外部输入天然享受现有的"用户在场/预算/挫败感"三条门控**，不需要在网关这层重新实现一遍节流逻辑。
3. **`enqueue_turn`**：直接调用 `InputQueue.enqueue(message, initiator="external", meta={"source_id":..., "event_id":...})`，这是成本最高、也是默认关闭的路径，只用于"确实需要 Agent 马上看一眼"的场景（比如网关自己检测到某个高优先级 source 连续失败，需要 Agent 帮忙诊断）。

`IngestionPolicy` 的消费方式对齐现有风格：作为 `autonomous_loop.tick()` 里新增的一个 `poll_since(consumer_name="external_input_policy", event_types=["external.*"])` 消费点，跟 `soft_goal_deriver` 挂在同一个节拍上，不新增额外的调度循环。

### 3.5 `channel`：daemon 处理前的分类（P7 新增）

**动机**：P1–P6 跑通之后，网关的落地闭环已经是"source 产生事件 → daemon（`autonomous_loop.tick()`）统一消费 → policy 路由"，但所有 `external.*` 事件在被 daemon 处理之前是完全同质的一批——`IngestionPolicy` 逐条按 `source_type`/`signal`/`fields.*` 匹配规则，没有一个"这条事件大致属于哪一类信息"的显式分类维度。随着 source 种类增多（watch 抓取的资讯、天气预报、未来可能的 webhook/日历……），daemon 端"按类型处理"的诉求会越来越明显：例如看板想按类别筛选事件流水、诊断脚本想只看"天气类"事件处理了多少条、未来某个频道可能需要频道级的节流或不同的默认落点。

**做法**：给 `ExternalInputEvent` 新增一个 `channel: str` 字段，作为"daemon 处理前"的分类标签：

- Source 自己可以在 `poll()` 里显式设置 `channel`（比如把同一个 source 的不同信号分进不同频道）；
- 更常见的做法是完全不管它——`sources.yaml` 里每个 source 配置可以带一个 `channel` 字段，缺省时直接回退成 `type`（一种 source 类型默认就是一个频道，不强制要求使用者理解这个概念才能用）；
- `GatewayPoller` 在发布事件前统一回填：`event.channel` 为空时，用该 source 的 `cfg.channel` 补上（`poller.py::_run_source_loop`）。

这依然是"复用现有事件总线，不新造一套持久化/消费机制"的延续（对齐设计目标 2）：`channel` 只是 `ExternalInputEvent.to_payload()` 里的一个新字段，随事件一起进 `events.jsonl`，不引入额外的存储文件或消费游标。

**daemon 侧怎么用**：

1. `PolicyRule.matches()` 新增 `channel` 作为第四种匹配维度（跟 `source_type`/`signal`/`fields.<key>` 并列），`policies.yaml` 可以直接按频道配路由规则，不需要针对每个 `source_type`/`signal` 组合重复写。
2. `run_ingestion_policy_once()`（daemon 在 `autonomous_loop.tick()` 里调用的消费入口）内部先用新增的 `group_events_by_channel()` 把这一批事件按频道分组，再按频道、组内按原顺序逐条 `decide_action()` 路由——路由结果本身不变（依然是逐事件独立判断），但处理顺序和统计口径显式按频道组织，为将来"频道级特殊处理"（节流、频道级默认落点、频道级熔断）预留了改动点，不需要再回头改 `decide_action()`/三个落点函数的调用方式。
3. `PolicyRunSummary` 新增 `by_channel: dict[str, int]`，记录这一轮每个频道处理了多少条事件，供看板/诊断观察 daemon 的处理分布。

**边界**：`channel` 是分类标签，不是路由动作本身——一个事件的最终落点仍然由 `policies.yaml` 里第一条匹配的规则决定（`channel` 只是可选的匹配条件之一）。没有配置任何按 `channel` 匹配的规则时，行为与 P1–P6 完全一致（默认 `notify_only`），这是刻意保持的向后兼容边界。

### 3.6 天气监控示例来源（P7 新增）

为了让"新增一种外部输入来源"这件事有一个可以直接跑起来的参照，新增 `builtin/weather.py`：基于 [Open-Meteo](https://open-meteo.com)（免费、无需 API key）的小时级预报，监控某个经纬度未来 N 小时内的降雨概率与极端气温阈值，命中时产生 `signal="rain_alert"` / `"high_temperature"` / `"low_temperature"` 事件；可选开启每日一条的 `signal="daily_forecast"` 摘要事件。`channel` 默认落在 `"weather"`。

实现上延续 `watch.py` 已经定型的分工方式：抓取失败统一包装成 `WeatherFetchError` 向上抛给 `GatewayPoller` 走既有的退避熔断；阈值告警只在"未命中 → 命中"的边沿触发一次（跟 `watch.py` 的 `threshold` 模式同一语义），持续命中不会每轮重复告警。

---

## 4. 数据与存储

```
.agent/
  system_events.jsonl                 # 复用现有事件总线，external.* 命名空间共用同一份文件
  external_input/
    sources.yaml                      # source 配置：id/type/params/interval_seconds/enabled
    policies.yaml                     # 路由规则
    state/<source_id>.json            # 每个 source 的增量状态（去重游标/ETag等），来源私有
    alerts.jsonl                      # notify_only 落点的持久化记录，供 /v1/inbox 与看板查询
```

---

## 5. 与现有模块的关系

| 能力 | 处理方式 |
|---|---|
| 事件传输/持久化 | **复用** `perception/system_events.py`，新增 `external.*` 事件命名空间，不改动其实现 |
| 高优先级信号需要门控评估 | **复用** `evolution/resource_arbiter.py` + GoalBacklog 现有流程（走 `goal_candidate` 落点） |
| 连续失败熔断 | **复用**（借鉴）`workflow/watchdog.py` 的"连续同类失败提前判定"思路 |
| 定时轮询 | **不复用** `cron_scheduler`（它的语义是"周期性提交 Agent 任务"，与"高频轮询、按需触发"的成本模型冲突），网关自建独立轻量调度 |
| 真正需要 Agent 处理 | **复用** `api/bridge.py::InputQueue.enqueue()`，新增 `initiator="external"` 取值，向后兼容现有 `user/cron/scheduled/autonomous` 四种 |
| 通知展示 | **扩展** `/v1/inbox` 与看板"全局待办中心"（`_render_global_inbox`），新增 `external_alert` 类型，UI 改动量很小 |
| watch 监控系统本身 | 作为 `ExternalInputSource` 的第一个具体实现（`builtin/watch.py`），复用上一轮设计的 Fetcher/RuleEngine |

---

## 6. 看板改动点（后续实现阶段）

1. 新增"🔌 外部输入"tab 或挂在现有"📌 目标看板"下的一个分区，展示：
   - 已注册的 source 列表（类型/状态/上次轮询时间/健康度），风格对齐工作流 tab 的运行记录列表；
   - `policies.yaml` 的可视化编辑（复用 reminders 那种"YAML frontmatter + 表单辅助"思路）；
   - 最近的 `external.*` 事件流水（本质是过滤后的 `poll_since()` 结果），供人工核对路由是否符合预期。
2. 顶栏"全局待办中心"新增 `external_alert` 图标（🌐），与现有 permission/interaction/objective_failed 并列。

---

## 7. 分阶段路线图

| 阶段 | 内容 |
|---|---|
| P1 | `ExternalInputSource` 抽象 + registry + `ExternalInputEvent`；先接入 `system_events.publish()` 的 `external.*` 命名空间 |
| P2 | `GatewayPoller` 独立调度线程 + 退避熔断 + `sources.yaml` 加载 |
| P3 | `IngestionPolicy` 路由（`notify_only` 优先实现，跑通 → `/v1/inbox`） |
| P4 ✅ | 迁移上一轮 watch 设计为 `builtin/watch.py`（第一个 source 实现），验证端到端闭环 |
| P5 ✅ | `goal_candidate` 落点（对接 `soft_goal_deriver` 同款模式）+ `enqueue_turn` 落点（默认关闭，显式开启） |
| P6 ✅ | 看板"🔌 外部输入"面板 |
| P7 ✅ | `channel` 分类字段（daemon 处理前先分频道）+ `policies.yaml` 按频道路由 + 天气监控示例 source（`builtin/weather.py`） |

---

## 8. 风险与边界

- **不做"真正的推送/中断"**：延续项目一贯的"轮询 + 状态文件"哲学（见 `system_events.py` 的三条硬约束），所谓"实时"是"下一次已有节拍里顺带查一下"，不引入新的并发/中断复杂度。
- **默认路径永远最省钱**：任何新增 source、任何未显式配置路由规则的事件类型，默认落点是 `notify_only`，不会意外地把高频轮询放大成高频 LLM 调用。
- **门控复用而非绕过**：凡是可能导致 Agent 真正执行动作的路径（`goal_candidate`/`enqueue_turn`），都走现有的 GoalBacklog/ResourceArbiter/InputQueue，不在网关层单独造一套"要不要执行"的判断逻辑，避免出现两套门控互相打架。

---

## 9. 实现状态

跟踪 §7 路线图各阶段的完成情况，每完成一个阶段更新本节。

### P1 — 已完成 ✅

**范围**：`ExternalInputSource` 抽象 + registry + `ExternalInputEvent`；接入 `system_events.publish()` 的 `external.*` 命名空间。

**新增文件**：

| 文件 | 内容 |
|---|---|
| `src/mini_agent/external_input/__init__.py` | 包入口，导出 P1 公开 API |
| `src/mini_agent/external_input/source.py` | `ExternalInputEvent`（含 `event_type()`/`to_payload()`/`from_payload()`）、`ExternalInputSource` 抽象基类、`register_source`/`get_source_class`/`registered_source_types` registry |
| `src/mini_agent/external_input/gateway.py` | `publish_event()`/`publish_events()`：把 `ExternalInputEvent` 归一化发布到 `system_events`，带进程内兜底去重（`_RecentIdCache`）；`poll_external_events()`：按 `external.` 前缀过滤消费，封装 `SystemEvent.payload` → `ExternalInputEvent` 的还原 |
| `src/mini_agent/external_input/builtin/__init__.py` | 内置 source 实现的占位包，P4 阶段填充 `watch.py` |
| `tests/test_external_input_source.py` | 12 个用例：`ExternalInputEvent` 校验与序列化往返、registry 注册/查找/报错、`publish_event`/`publish_events`/`poll_external_events` 与 `system_events` 的接入正确性、去重、游标推进 |

**变更文件**：

| 文件 | 变更 |
|---|---|
| `src/mini_agent/storage/paths.py` | 新增 `AgentPaths.external_input_dir` / `external_input_sources_config` / `external_input_policies_config` / `external_input_state_dir` / `external_input_alerts` 五个 `@property`，对应 §4 存储布局；不改动任何已有属性 |

**关键实现说明**：

- `poll_since()`（`system_events.py`）本身只做 `event_type` 精确匹配，不支持 §3.3 里写的 `event_types=["external.*"]` 这种 glob 语法。为了不改动 `system_events.py`（设计目标 2 明确要求"不新造一套持久化/消费机制"），`poll_external_events()` 改为拿到该 consumer 游标之后的**全部**事件后，在网关侧按 `event_type.startswith("external.")` 过滤，`event_types` 参数仍支持传入完整事件名做精确子集过滤。P3 的 `IngestionPolicy` 会直接复用这个封装，而不是自己再实现一遍过滤逻辑。
- 网关级去重（`gateway._RecentIdCache`）明确定位为"进程内兜底"，不是权威去重——按设计文档 §3.1/§3.3 的分工，语义去重是来源自己在 `state` 里维护游标的职责；重启后网关侧缓存清空是预期行为，不影响正确性（重复事件顶多被 `system_events.jsonl` 记录到两次，不会导致漏读）。
- `ExternalInputSource.poll()` 目前只有 docstring 层面的约束（"不要调 LLM""不要用实例属性存跨轮询状态"），没有运行时强制检查——这类约束依赖 code review 而非代码强制，与项目里其它"硬约束"（如 `system_events.py` 头部注释里的三条）的落地方式一致。

### P2 — 已完成 ✅

**范围**：`GatewayPoller` 独立轮询调度线程 + 退避熔断 + `sources.yaml` 加载。

**新增文件**：

| 文件 | 内容 |
|---|---|
| `src/mini_agent/external_input/config.py` | `SourceConfig` 数据类 + `load_sources_config()`（解析 `sources.yaml`，容错策略见下）+ `get_source_config()` 按 id 查找 |
| `src/mini_agent/external_input/poller.py` | `GatewayPoller`：每个 `enabled=true` 的 source 一条后台线程，按各自 `interval_seconds` 循环调用 `poll()`；`SourceHealth` 运行时健康视图（`get_health()`/`get_all_health()`）；连续失败退避（翻倍封顶 15 分钟）+ 熔断（达到 `failure_threshold` 后 `circuit_open=True` 并发布 `external.<type>.source_unhealthy` 健康事件，`tier=cron`）；state 落盘在 `external_input/state/<source_id>.json` |
| `tests/test_external_input_config.py` | 7 个用例：加载/默认值填充/结构校验/单条容错/interval 非法回退/按 id 查找 |
| `tests/test_external_input_poller.py` | 7 个用例：基本轮询发布、state 跨轮询传递与落盘、未注册 source type 的快速失败、熔断触发与恢复、disabled source 不起线程、`stop()` 正确终止线程 |

**变更文件**：`src/mini_agent/external_input/__init__.py` — 导出 `SourceConfig`/`load_sources_config`/`GatewayPoller`。

**关键实现说明**：

- 调度模型延续项目"轮询 + 状态文件"哲学（§8 风险与边界）：每个线程在两次 `poll()` 之间用 `stop_event.wait(backoff)` 睡眠，`stop()` 只是设置 stop 标志再 `join()`，不强杀线程——与 `workflow/watchdog.py` 里"Python 线程无法被安全强杀"的已知限制一致，正在执行中的 `poll()` 调用会跑完这一轮才退出。
- 熔断阈值判定使用"恰好等于阈值那一次"触发健康事件（而不是"每次超过阈值都发"），避免同一个持续故障的 source 每隔一个轮询间隔就刷一条健康事件到 `events.jsonl`；`consecutive_failures` 之后仍会继续增长、`circuit_open` 保持 `True`，直到某次 `poll()` 成功才整体清零复位。
- `sources.yaml` 缺失或 PyYAML 未安装都返回空列表而非报错（网关此时"没有任何 source"是合法状态）；只有顶层结构明显不对（不是 `{sources: [...]}` 形状）才抛 `SourcesConfigError`，单条记录缺 `id`/`type` 只跳过那一条，不拖累其余已经配好的 source。
- `GatewayPoller` 构造时若不传 `configs`，会调用 `load_sources_config()` 自动加载；测试和未来"看板临时试跑一个 source"场景可以直接传 `configs` 跳过文件加载。
- 未注册的 `source_type`（配置了网关不认识的类型）被视为配置错误而非运行时故障：线程记录健康状态（`circuit_open=True`）后立即退出，不会对一个必然失败的类型查找做无意义的无限重试。

### P3 — 已完成 ✅

**范围**：`IngestionPolicy` 路由（`notify_only` 优先实现，跑通 → `/v1/inbox`）。

**新增文件**：

| 文件 | 内容 |
|---|---|
| `src/mini_agent/external_input/policy.py` | `PolicyRule`（`match`/`action`/`enqueue`，`matches()` 支持 `source_type`/`signal`/`fields.<key>` 三种匹配维度）+ `load_policies()`（解析 `policies.yaml`）+ `decide_action()`（首个匹配规则生效，都不匹配回退 `notify_only`）+ `notify_only` 落地（`_notify_only()` 写 `alerts.jsonl`）+ `list_pending_alerts()`/`acknowledge_alert()` + `run_ingestion_policy_once()`（消费 `external.*` 事件并路由的主入口，`goal_candidate`/`enqueue_turn` 被识别但暂不执行，计入 `skipped` 计数，P5 落地） |
| `tests/test_external_input_policy.py` | 16 个用例：匹配维度、首个匹配优先级、默认兜底、`policies.yaml` 加载容错、`notify_only` 写入/ack/游标推进、`goal_candidate`/`enqueue_turn` 不误当 `notify_only` 处理 |

**变更文件**：

| 文件 | 变更 |
|---|---|
| `src/mini_agent/api/routes.py` | `GET /v1/inbox` 新增聚合 `external_alert` 类型（读取未 `acknowledged` 的 alert）；新增 `POST /v1/inbox/external_alerts/{alert_id}/ack` 端点用于标记已处理；同步更新文件头的路由索引注释 |
| `src/mini_agent/external_input/__init__.py` | 导出 `policy.py` 的公开 API |

**关键实现说明**：

- `PolicyRule.matches()` 对未识别的匹配维度（既不是 `source_type`/`signal`，也不是 `fields.` 前缀）判定为"不匹配"而不是忽略该条件，避免规则实际生效范围比配置者书写时预期的更宽松。
- `run_ingestion_policy_once()` 就是 §3.4 末尾描述的"挂在 `autonomous_loop.tick()` 里的消费点"的实现本体，但**本阶段没有改动 `autonomous_loop.py`**——先把路由逻辑本身做完、测试跑通，接入 `tick()` 只是外部再加一行调用，放在 P4/P5 跟 `goal_candidate`/`enqueue_turn` 的真正落地一起做，避免在 `goal_candidate`/`enqueue_turn` 还没有实际行为之前就改动核心调度循环。当前可以通过诊断脚本或测试直接调用 `run_ingestion_policy_once(paths)` 驱动。
- `alerts.jsonl` 走"小文件、低频写、`acknowledge_alert()` 整体重写"的模式（象设计文档里没有明确要求持久化游标，选择用 `acknowledged` 字段而不是消费游标，这样"标记已处理"是显式动作而不是"被看板刷新过一次就自动消失"，跟 `/v1/inbox` 里 permission/interaction 需要显式 respond 才消失的语义一致）。
- `goal_candidate`/`enqueue_turn` 命中时**不会**被静默处理成 `notify_only`，也不会丢事件——游标照常推进（事件已经被消费），只是计入 `PolicyRunSummary.goal_candidate_skipped`/`enqueue_turn_skipped`，等 P5 实现后同一批事件不会重复处理；这是有意的取舍：配置了还没实现的 action，应该"可见地什么都不做"，而不是被悄悄降级成另一种行为。

**[BUGFIX，实际使用中发现]** `_notify_only()` 最初没有基于 `alert_id`
（`f"alert:{source_id}:{event_id}"`）做任何去重，只是单纯追加写入
`alerts.jsonl`。§2/P1 里已经写明网关级去重（`gateway._RecentIdCache`）
只是"进程内兜底，不是权威去重"，"重复事件顶多被 `system_events.jsonl`
记录到两次"——也就是说下游消费者本来就应该能容忍同一个 `event_id`
被重复投递（比如某个 source 自身的增量状态被重置、或网关重启后缓存清空），
但 `_notify_only()` 当时没有兑现这个"能容忍重复"的隐含约定，导致真实
使用中出现了 `alerts.jsonl` 里 `alert_id` 完全相同的多条记录——看板
拿 `alert_id` 当 Streamlit 组件 key 使用，直接触发
`StreamlitDuplicateElementKey` 崩溃、整个看板页面渲染不出来。

修复：新增 `_load_existing_alert_ids()`，`_notify_only()` 写入前先检查
`alert_id` 是否已经在 `alerts.jsonl` 里出现过（不论是否已 `acknowledged`），
出现过就直接跳过，不重复追加——从根源上保证同一个 `alert_id` 在文件里
只会存在一条记录。同时在
`apps/mini_agent_kanban/app.py::render_external_input_tab()` 加了一层
防御：渲染"已读"按钮的 `key` 额外拼上循环下标，即便未来出现其它未预料
到的重复场景，也只会是显示上偶发重复，不会再让整个页面崩溃。新增
`tests/test_external_input_policy.py::test_same_event_id_republished_does_not_duplicate_alert`/
`test_duplicate_not_reintroduced_after_acknowledge` 两个回归用例锁定。

### P4 — 已完成 ✅

**范围**：迁移上一轮 watch 设计为 `builtin/watch.py`（第一个 `ExternalInputSource` 实现），验证端到端闭环。

**新增文件**：

| 文件 | 内容 |
|---|---|
| `src/mini_agent/external_input/builtin/watch.py` | `WatchInputSource`：三种 fetcher（`rss`/`json_api`/`html_diff`，纯标准库 + `requests`，不引入 `feedparser` 等新依赖）；`RuleEngine`（`find_new_items`/`keyword_hits`/`threshold_hit` 三个无状态静态方法，对应设计里"新增/字段变化/关键词/阈值匹配"四种规则的前三种，字段变化本身用值比较即可，不需要单独的规则方法）；`WatchFetchError`（抓取失败统一异常，直接向上抛给 `GatewayPoller` 走既有退避熔断，不在本文件重试） |
| `docs/external-input-gateway-guide.md` | 面向使用者的配置指南：`sources.yaml`/`policies.yaml` 示例、watch 三种 fetcher 参数说明、自定义来源写法、告警查看方式、已知限制 |
| `tests/test_external_input_watch.py` | 21 个用例：rss 新条目检测/关键词过滤/`seen_ids` 累积与封顶、json_api 的 `field_changed`/`threshold`（含"命中一次不重复、退出后再次命中会再触发"）、html_diff 摘要变化检测与关键词过滤、首次轮询不误报、未知 fetcher 报错、`get_by_path` 边界情况、registry 注册验证 |

**变更文件**：

| 文件 | 变更 |
|---|---|
| `src/mini_agent/external_input/builtin/__init__.py` | 从"P4 占位包"改为实际 import 并重导出 `watch.py` 的公开 API |
| `src/mini_agent/external_input/poller.py` | 新增 `_ensure_builtin_sources_registered()`，在 `GatewayPoller.__init__` 里尽力而为地 `import mini_agent.external_input.builtin.watch`（失败则忽略），这样只要配置了 `type: watch` 就不需要业务代码手动 import 一次才能注册成功 |
| `src/mini_agent/external_input/__init__.py` | 更新包顶部文档字符串，P4 范围移入"已完成"，路线图剩余项收窄为 P5/P6 |

**关键实现说明**：

- **不引入新依赖**：RSS/Atom 用标准库 `xml.etree.ElementTree` 解析（没有用 `feedparser`），HTML 转纯文本用简单的正则去标签（没有引入 `beautifulsoup4`）；HTTP 请求复用项目已声明的 `requests` 依赖。这是刻意的取舍——§3.2 只要求"命中规则的条目转成 `ExternalInputEvent`"，没有要求功能对等于成熟的 feed/HTML 解析库，优先保持依赖面不变。
- **`source_id` 的传递方式**：`GatewayPoller._run_source_loop()`（P2 已实现）调用的是 `source.poll(cfg.params, state)`，不会额外传入 `cfg.id`。为了不改动 P2 已经定稿的调用签名，`WatchInputSource` 要求使用者在 `sources.yaml` 的 `params.source_id` 里重复写一遍与顶层 `id` 相同的值（文档已在 `docs/external-input-gateway-guide.md` §3 里显式提示这个"必须保持一致"的约束）。缺失时不会报错，只是 `system_events` 里的 `source` 标签退化成 `external:`（空），不影响事件本身的产生和路由。
- **`json_api` 的 threshold 模式**：命中阈值只在"未命中 → 命中"的边沿发一次事件（`state["threshold_hit"]` 记录当前是否命中），持续命中不会每轮重复告警；数值先回到阈值范围外、再重新跌入/超出时才会再次触发——这是为了让阈值告警的语义对齐"事情发生了变化"而不是"每次轮询都在命中"，避免 `notify_only` 落点的 `alerts.jsonl` 被同一个持续状态刷屏。
- **首次轮询不误报**：`json_api` 的 `field_change` 模式用 `"last_value" in state` 判断是否有历史值，而不是"上次值是否是 `None`"——如果远端字段本来就是 `null`，第一次轮询也不应该被当成"从 `None` 变成 `null`"报出来；`html_diff` 同理用 `state.get("digest") is None` 判断首次。
- **失败即向上抛出**：三个 `_poll_*` 方法遇到抓取/解析失败都不吞异常，统一包装成 `WatchFetchError` 抛出，交给 `GatewayPoller`（P2）已经实现的连续失败计数、退避、熔断、健康事件发布处理，本文件不重复实现任何重试/退避逻辑。

### P5 — 已完成 ✅

**范围**：`goal_candidate`/`enqueue_turn` 落点真正落地（对接 `soft_goal_deriver` 同款模式）+ 接入 `autonomous_loop.tick()`。

**变更文件**：

| 文件 | 变更 |
|---|---|
| `src/mini_agent/external_input/policy.py` | 新增 `_goal_candidate()`：写入 `GoalBacklog.add_goal(source="external_input", tags=["needs_review","external_input"])`，用 `objective_outcome_tracker.normalize_title_key()` 归一化标题做去重；新增 `_enqueue_turn()`：渲染 `rule.enqueue.task_template`（`{title}`/`{detail}` 占位符，缺省有内置默认模板）后调用 `InputQueue.enqueue(initiator=...)`；`run_ingestion_policy_once()` 新增 `goal_backlog`/`input_queue` 两个可选参数，不传时保持 P3 阶段"可见地跳过"行为（向后兼容）；`PolicyRunSummary` 新增 `goal_candidate`/`goal_candidate_deduped`/`enqueue_turn` 三个计数字段 |
| `src/mini_agent/evolution/autonomous_loop.py` | `_tick_passive()` 尾部新增一段 `run_ingestion_policy_once(self._paths, goal_backlog=self._goal_backlog, input_queue=self._input_queue)` 调用（try/except 包裹，`ImportError` 时静默跳过，不影响没有安装可选依赖时的现有行为） |
| `src/mini_agent/external_input/__init__.py` | 导出 `PolicyRunSummary`/`EXTERNAL_GOAL_SOURCE`；包文档字符串 P5 范围移入"已完成" |
| `tests/test_external_input_policy.py` | 新增 4 个用例：`goal_candidate` 真正写入 GoalBacklog 且带 `needs_review` 标签、同标题去重不重复写入、`enqueue_turn` 真正调用 `InputQueue.enqueue` 且 `task_template` 占位符渲染正确、缺省模板兜底 |

**关键实现说明**：

- **为什么挂在 `_tick_passive()` 而不是 `_tick_autonomous()`**：`soft_goal_deriver` 只在 autonomous 档位 derive，是因为"凭空生成新意图"本身需要更高的信任档位；但 `notify_only`（IngestionPolicy 三个落点里成本最低、默认档）不应该被 autonomy_level 挡住——外部世界发生的事件不该因为用户把档位调到 passive 就完全看不见。真正会消耗资源的两个落点各自已经有节流：`enqueue_turn` 默认关闭、需要在 `policies.yaml` 显式配置命中规则才会触发；`goal_candidate` 写入的 Goal 只在 maintenance/autonomous 档位才会被 `_ensure_goal_objectives()`/`ObjectiveExecutor` 进一步拆解执行，passive 档位下顶多是"记了一个候选"，不产生 LLM 调用。这与 `_tick_passive()` 里已有的 `attention_mismatch_push` 走的是同一条"复用 InputQueue、不受档位限制"的先例。
- **`goal_candidate` 去重复用 `objective_outcome_tracker.normalize_title_key()`**：与 `soft_goal_deriver._DeriveCandidate.dedupe_key()` 底层调用的是同一个函数，保证"同一主题"的判定口径在 agent 自己 derive 的候选和外部输入候选之间完全一致，不会出现"两套归一化规则各自维护、逐渐漂移"的问题。去重范围限定在当前 `active_goals()`，不做全历史比对——已经完成/放弃的同名 Goal 允许再次被外部信号重新提起（跟 `soft_goal_deriver.record_rejected()` 的 30 天 TTL 语义不同，这里没有引入类似的"外部输入专属拒绝列表"，属于 YAGNI：真正需要时可以在 P6 看板加"忽略此来源"操作再补）。
- **`goal_candidate`/`enqueue_turn` 各自的失败隔离**：`_goal_candidate()`/`_enqueue_turn()` 内部异常都在 `run_ingestion_policy_once()` 里被单独 catch，不会因为某一条事件路由失败（比如 `GoalBacklog` 磁盘写入失败）连带影响同一批次里其他事件的处理；`enqueue_turn` 失败时退回计入 `enqueue_turn_skipped`，不会把失败误记成成功。
- **`task_template` 的容错**：`str.format()` 遇到模板里写了 `{title}`/`{detail}` 之外的占位符会抛 `KeyError`，被 `_enqueue_turn()` 捕获后退化为内置默认拼接，不会因为用户在 `policies.yaml` 里写错一个占位符名字就导致整条外部信号被吞掉。
- **向后兼容边界**：`run_ingestion_policy_once(paths)`（不传新参数）的行为与 P3 阶段完全一致——这也是为什么 P3 阶段遗留的
  `test_goal_candidate_and_enqueue_turn_do_not_create_alerts` 用例不用改就能继续通过。

### P6 — 已完成 ✅

**范围**：看板"🔌 外部输入"面板（对应 §6 罗列的四块内容：source 列表/健康度、policies.yaml 路由规则、待处理 notify_only 告警、最近事件流水）。

**前置修复（P6 开工前发现的缺口）**：`GatewayPoller`（P2 产出）在 P2–P5 期间从未被任何非测试代码构造/`start()` 过——`sources.yaml` 配了 source 也不会真的跑起来轮询，看板要展示的"健康度"根本无从谈起。这个缺口在 P6 里一并补上：

| 文件 | 变更 |
|---|---|
| `src/mini_agent/api/server.py` | `HttpServer._build_autonomous_loop()` 新增构造+`start()` 一个 `GatewayPoller` 实例，挂到 `self._bridge._external_input_poller`（与 `_cron_scheduler` 同一处、同一挂载风格）；`HttpServer.stop()` 新增对应的 `gateway_poller.stop()` 优雅关闭 |

**变更文件（P6 本体）**：

| 文件 | 变更 |
|---|---|
| `src/mini_agent/api/routes.py` | 新增三个只读 GET 端点：`/v1/external_input/sources`（已配置 source + 运行时健康度，`poller_available=false` 时优雅降级为只读静态配置）、`/v1/external_input/policies`（policies.yaml 规则，按文件顺序即匹配优先级）、`/v1/external_input/events`（`system_events.jsonl` 里 `external.*` 前缀事件的只读尾读，不消费游标，跟真实消费者互不干扰）；更新文件头路由索引注释 |
| `apps/mini_agent_kanban/client.py` | 新增 `external_input_sources()`/`external_input_policies()`/`external_input_events()`/`ack_external_alert()` 四个客户端方法 |
| `apps/mini_agent_kanban/app.py` | 新增 `render_external_input_tab()`：source 卡片（类型/启用状态/运行状态/熔断与失败计数/上次轮询时间）、路由规则可读化展示、待处理告警列表 + "已读" ack 按钮（复用已有的 `/v1/inbox` 聚合 + `/v1/inbox/external_alerts/{id}/ack`）、最近事件流水；接入 `main()` 的 tab 列表（新增"🔌 外部输入"页签） |
| `tests/test_external_input_routes_p6.py` | 新增 10 个用例，覆盖三个新端点：无配置时返回空列表、poller 不可用时静态降级、poller 可用时健康度正确透出、规则按文件顺序返回、单条规则 action 非法时静默跳过（非 fatal error，无 `_error` 字段）、顶层结构错误时才有 `_error` 字段、事件按 `external.*` 前缀过滤且新的排在前面、`limit` 参数生效且有上限保护 |

**关键实现说明**：

- **只读端点，不做"配置管理 API"**：`sources.yaml`/`policies.yaml` 都是纯文本配置文件，改配置直接编辑文件最直接；三个新端点全部是只读展示，不提供在线编辑/热加载的写端点，避免重新发明一套配置管理 UI（YAGNI，真正需要在线编辑时可以单独立项）。
- **poller 不可用时的降级路径**：`/v1/external_input/sources` 在 `bridge._external_input_poller is None`（非 daemon 模式，或构造阶段异常被吞掉）时，退化为只读 `load_sources_config()` 的静态结果，健康相关字段一律为 `null`，并在响应里带上 `poller_available: false`——看板据此展示一条警告而不是让整个页面报错，遵循项目里"配置/运行时状态分离，任一层不可用不拖垮另一层"的一贯风格。
- **事件流水端点的性能取舍**：`/v1/external_input/events` 从文件尾部往前逐行扫描、凑够 `limit` 条 `external.*` 事件就提前停止，而不是像 `list_pending_alerts()` 那样整份反序列化——因为 `system_events.jsonl` 承载了全部子系统的事件（不只是外部输入），体量可能远大于 `alerts.jsonl`，"体量不大就全量扫描"的取舍在这里不成立。`limit` 硬上限 200，防止看板一次请求传入超大值时读放大。
- **告警 ack 复用已有端点，未新增**：`POST /v1/inbox/external_alerts/{alert_id}/ack` 在 P3 阶段（`/v1/inbox` 聚合）就已经实现，P6 只是让看板客户端第一次真正调用它（此前没有任何调用方），因此本轮没有改动这个端点本身的实现。
- **§7 路线图 P1–P6 完成**：从 source 轮询产生事件，到 policy 路由到三种落点，到 `autonomous_loop.tick()` 自动消费，到看板可视化人工核对——不再有"配置了但没人跑"或"跑了但看不见"的断点。P7 在这个闭环之上补充"daemon 处理前先分类"的能力，见下节。

### P7 — 已完成 ✅

**范围**：`channel` 分类字段（`ExternalInputEvent.channel` / `SourceConfig.channel`）+ `policies.yaml` 按 `channel` 路由 + `PolicyRunSummary.by_channel` 统计 + 天气监控示例 source（`builtin/weather.py`）。设计动机与取舍见 §3.5/§3.6。

**新增文件**：

| 文件 | 内容 |
|---|---|
| `src/mini_agent/external_input/builtin/weather.py` | `WeatherInputSource`：基于 Open-Meteo 免费预报 API（无需 key）监控降雨概率/极端气温阈值，`rain_probability_threshold`/`temperature_high_threshold`/`temperature_low_threshold` 均为可选，命中即发 `rain_alert`/`high_temperature`/`low_temperature`（边沿触发，持续命中不重复）；可选 `daily_summary` 每日一条 `daily_forecast` 摘要；`WeatherFetchError` 统一包装抓取失败，交给 `GatewayPoller` 既有退避熔断处理，不重复实现重试 |
| `tests/test_external_input_channel_p7.py` | 9 个用例：`SourceConfig.channel` 缺省回退/显式配置、`GatewayPoller` 用 `cfg.channel` 回填事件 channel、`PolicyRule.matches` 的 `channel` 匹配维度、`group_events_by_channel` 分组保序与未设置频道归入 `"default"`、`run_ingestion_policy_once` 的 `by_channel` 统计、天气 source 降雨告警边沿触发/每日摘要一天一次/缺经纬度报错 |

**变更文件**：

| 文件 | 变更 |
|---|---|
| `src/mini_agent/external_input/source.py` | `ExternalInputEvent` 新增 `channel: str = ""` 字段，`to_payload()`/`from_payload()` 同步读写，缺省为空串（由网关回填，不在这里替调用方猜测默认值） |
| `src/mini_agent/external_input/config.py` | `SourceConfig` 新增 `channel: str` 字段；`from_dict()` 解析 `sources.yaml` 里的 `channel:`，缺省回退成 `type` |
| `src/mini_agent/external_input/poller.py` | `_run_source_loop()` 在 `publish_events()` 之前，把 `event.channel` 为空的事件统一回填成 `cfg.channel`；`_publish_unhealthy_event()` 的健康事件同样打上来源的 `channel`（缺省 `"health"`）；`_ensure_builtin_sources_registered()` 新增尝试 import `builtin.weather`（失败即忽略，跟 `watch` 同样的"尽力而为"策略） |
| `src/mini_agent/external_input/policy.py` | `PolicyRule.matches()` 新增 `channel` 匹配维度；新增 `group_events_by_channel()`；`run_ingestion_policy_once()` 改为先分组再按频道处理；`PolicyRunSummary` 新增 `by_channel: dict` 字段 |
| `src/mini_agent/external_input/builtin/__init__.py` | 新增 import + 重导出 `weather.py` 的公开 API（`WeatherInputSource`/`WeatherFetchError`） |
| `src/mini_agent/external_input/__init__.py` | 导出 `group_events_by_channel`；包文档字符串新增 P7 范围说明 |
| `docs/external-input-gateway-guide.md` | 新增"频道分类"一节 + 天气 source 的 `sources.yaml` 配置示例 |

**关键实现说明**：

- **`channel` 是分类标签，不是新的路由通道**：三种落点（`notify_only`/`goal_candidate`/`enqueue_turn`）的实现完全没有改动，`channel` 只是 `PolicyRule.matches()` 多出的一个可选匹配维度，以及 `run_ingestion_policy_once()` 内部的分组统计维度——这是刻意的克制：设计目标 3（"分层路由、按需消耗 LLM"）已经由三种落点覆盖，`channel` 解决的是另一个问题（"daemon 按什么类别去处理/展示这些信息"），两者正交，不应该混在一起变成"频道决定动作"这种新的隐式规则。
- **为什么在 `SourceConfig` 而不是只在 `ExternalInputEvent` 上加字段**：如果只在事件上加 `channel`，每个 source 实现（尤其是像 `watch.py` 这种一次可能产生多种 `signal` 的 source）都要自己决定并硬编码 channel 值；把默认值下放到 `sources.yaml` 配置层，绝大多数用户完全不需要碰这个概念——不写 `channel:` 字段时效果等同于"按 source 类型自动分类"，跟没有这个功能之前的行为在观感上是一致的，只是多了一个可选的精细化分类入口。
- **`group_events_by_channel()` 不改变路由结果，只改变处理顺序和统计**：分组本身是`dict` 保序（Python 3.7+ 字典保持插入顺序），组内事件严格保持它们在原始事件列表里的相对顺序；这保证了改成"按频道处理"之后，单个频道内部的事件处理顺序、以及每个事件各自的路由结果，跟 P1–P6 阶段逐条处理完全一致，唯一变化的是"不同频道之间"的事件现在会先按频道聚成组再处理——这个改动不影响任何既有测试（P1–P6 阶段的测试大多只用一个 channel/一个事件，或不关心处理顺序）。
- **天气 source 复用 `watch.py` 的抓取/阈值判断分工，不重复发明**：`WeatherFetchError`、"边沿触发一次、状态回落后再次触发"的阈值语义、"failed → 向上抛出交给 GatewayPoller 处理"的容错策略，均直接照搬 `watch.py` 里已经定型的模式，本文件不重新实现一遍退避/去重逻辑。
- **不引入新依赖**：复用项目已声明的 `requests`；Open-Meteo 返回结构简单的 JSON，不需要专门的 SDK。选择 Open-Meteo 而非需要注册 key 的天气 API，是为了让这个"示例来源"本身开箱即用，不需要用户先去申请密钥才能看到网关端到端跑起来的效果。
- **§7 路线图至此全部完成（P1–P7）**：如果后续要继续演进（比如频道级的节流/默认落点、看板里按频道筛选事件流水、更多内置 source），应作为独立的新一轮迭代规划，而不是继续在本设计文档里叠加。

### P8 — 已完成 ✅（架构修正：移除 `goal_candidate` 落点）

**背景**：`goal_relevance.py`（见 `next_doc/watchlist_notification_goal_design.md`）
上线后，项目里出现了两条同时处理"外部输入与 Goal 关系"的链路：

1. 本文档 P5 引入的 `goal_candidate` 落点——命中即调用
   `GoalBacklog.add_goal()`，凭空创建一个新 Goal（打 `needs_review`
   标签，未经验证）。
2. `GoalRelevanceEngine`——独立订阅同一批 `external.*` 事件，对
   `active_goals()` 做规则粗筛 + LLM 判定，命中后调用
   `attach_external_context()`/`try_advance_goal()`，只把外部信号关联到
   *已存在*的 Goal/Objective 上，从不创建新节点。

两条链路职责重叠且语义冲突：同一条外部事件可能一边被 `goal_relevance`
挂到已有 Goal 上，一边又被 `goal_candidate` 凭空建了个新 Goal，且后者
创建的 Goal 完全没有经过相关性判定，质量不可控。结论：**外部输入不应该
被直接当成 Goal 或拆解成 Objective，只能关联到相关的已有 Goal/
Objective，让它知道"有这个输入、可以用"**——这正是 `GoalRelevanceEngine`
已经在做的事，`IngestionPolicy` 不应该重复实现一遍。

**变更文件**：

| 文件 | 变更 |
|---|---|
| `src/mini_agent/external_input/policy.py` | 移除 `_goal_candidate()`、`EXTERNAL_GOAL_SOURCE`；`VALID_ACTIONS` 收窄为 `{notify_only, enqueue_turn}`；`PolicyRunSummary` 去掉 `goal_candidate`/`goal_candidate_skipped`/`goal_candidate_deduped` 三个字段；`run_ingestion_policy_once()` 不再接受 `goal_backlog` 参数 |
| `src/mini_agent/external_input/__init__.py` | 不再导出 `EXTERNAL_GOAL_SOURCE`；包文档字符串新增 P8 范围说明 |
| `src/mini_agent/evolution/autonomous_loop.py` | `_tick_passive()` 调用 `run_ingestion_policy_once()` 时不再传 `goal_backlog` |
| `.agent/external_input/policies.yaml` | 原先 `action: goal_candidate` 的规则改为 `notify_only` |
| `apps/mini_agent_kanban/app.py` | 路由规则展示的 `_ACTION_LABEL` 去掉 `goal_candidate` 条目 |
| `tests/test_external_input_policy.py` | 移除 `goal_candidate` 落地相关的 3 个用例，新增"`goal_candidate` 不是合法 action"、"历史配置里残留该 action 会被 `load_policies()` 按非法规则跳过而不是整份报错"两个用例 |
| `tests/test_external_input_routes_p6.py` | 示例配置里的 `goal_candidate` 改为 `notify_only` |
| `scripts/cleanup_external_goal_candidates.py`（新增） | 一次性清理脚本：删除 `goals.json` 里历史由 `goal_candidate` 创建的 Goal（`source == "external_input"`）及其子 Objective，写入前自动备份原文件 |

**关键实现说明**：

- **不是"新增关联机制"，而是"移除重复/错误的创建机制"**：关联机制
  （`attach_external_context()`/`try_advance_goal()`）在 `goal_relevance.py`
  里已经存在且独立运行，本次变更只是把 `IngestionPolicy` 里那条会创建
  新 Goal 的错误路径去掉，没有新增代码路径。
- **历史配置容错，不做迁移强校验**：`policies.yaml` 里残留
  `action: goal_candidate` 的规则，走 `load_policies()` 既有的"单条规则
  非法即跳过"策略，不会导致整份配置加载失败，也不会被误当成任何一个
  现有合法 action 处理——这与新增/废弃一个 action 时项目里其它地方的
  容错哲学一致（配置格式错误应该显式可见地"什么都不做"，而不是被悄悄
  降级成另一种行为）。
- **存量数据用独立脚本清理，不在 `policy.py`/`autonomous_loop.py` 里
  自动触发**：删除已经创建的 Goal 属于一次性、有风险（需要人工确认/
  备份）的操作，不适合埋进日常自动运行的 tick 循环里静默执行，因此做成
  显式调用的运维脚本（`scripts/cleanup_external_goal_candidates.py`），
  默认交互确认 + 自动备份，`--dry-run` 可先预览再决定是否真正执行。
