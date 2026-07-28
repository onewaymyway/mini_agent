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
| P4 | 迁移上一轮 watch 设计为 `builtin/watch.py`（第一个 source 实现），验证端到端闭环 |
| P5 | `goal_candidate` 落点（对接 `soft_goal_deriver` 同款模式）+ `enqueue_turn` 落点（默认关闭，显式开启） |
| P6 | 看板"🔌 外部输入"面板 |

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

### P2–P6 — 待开发

尚未开始，按 §7 路线图顺序推进：`GatewayPoller` 独立调度线程 + `sources.yaml` 加载（P2）→ `IngestionPolicy` 路由，先跑通 `notify_only`（P3）→ 迁移 `watch` 为内置 source（P4）→ `goal_candidate`/`enqueue_turn` 落点（P5）→ 看板面板（P6）。
