# 外部输入网关扩展设计方案：关注对象 · 分级汇报 · 通知系统 · Goal 关联执行

- **版本**: v1.0（设计确认稿，待实施）
- **背景**: 在已有的 External Input Gateway（`src/mini_agent/external_input/`）基础上，
  新增"用户关注对象识别"、"按任意粒度分级汇报"、"可扩展通知渠道"、
  "外部信号驱动 Goal 执行" 四块能力。
- **关联文档**: `next_doc/external_input_gateway_design.md`（网关本体设计）

---

## 1. 背景与目标

现有网关（P1-P7）已经做到"外部世界发生的事 → 归一化事件 → 按 policies.yaml
路由到 notify_only/goal_candidate/enqueue_turn"。本次扩展要解决的是路由之上的
两类新问题：

1. **用户真正关心的具体对象**（竞品、关键词、话题……）出现在外部信息里时，
   要能按用户设定的关注对象被识别出来，并且**用户希望被打扰的频率**是可以
   任意配置的（1分钟、30分钟、1小时、1天……而不是简单的"紧急/不紧急"二元判断）。
2. **外部信息如果确实与用户当前正在推进的某个 Goal 相关**，不应该只是"发个通知
   完事"，而是要让 **agent 在真正执行这个 Goal 的时候用得上这段信息**，必要时
   还要能**主动把这个 Goal 重新拉回执行队列**。

同时明确了几条关键设计决策（本轮讨论已拍板）：

- **是否与某个 Goal 相关，由 LLM 判断，不是让用户手工配置映射关系**——因为
  用户创建 Goal 时基本不会同步去配置"这个 Goal 对应哪些外部关键词"，配置项
  没人填约等于这个功能不存在。
- **Prompt 注入必须精确到"当前正在处理这个 Goal 的任务"，不是全局注入**——
  只有真正在分解/推进某个 Goal 的时候，才把这个 Goal 自己的外部上下文塞进去，
  别的任务、别的 Goal 都看不到。
- **汇报频率是任意个 tier**，不是预设的"紧急/一般"两档，默认给
  1分钟、30分钟、1小时、1天 四档，用户可以自己增删。
- **`advance_goal`（主动拉起执行）需要限流冷却**，避免同一个 Goal 被反复打扰式拉起。
- **enqueue_turn 之后 agent 判断"不需要推进"是正常结果**，不需要额外机制处理。
- **通知渠道第一批只做 kanban + 邮件**，微信等渠道留接口、不实现。

---

## 2. 总体架构

```
                         外部世界（RSS / JSON API / 网页diff / 天气 ……）
                                          │  ExternalInputSource.poll()
                                          ▼
                              system_events（external.* 事件，已有）
                                          │
                ┌─────────────────────────┼──────────────────────────────┐
                ▼                         ▼                              ▼
       IngestionPolicy（已有）    WatchlistMatcher（新增）      GoalRelevanceEngine（新增，两阶段）
     notify_only/goal_candidate/   纯关键词匹配，产生          Stage①候选生成（纯规则，零LLM成本）
     enqueue_turn，跟本次扩展正交    "关注命中"记录              Stage②LLM批量判定（相关性+是否值得推进）
                │                         │                              │
                │                         ▼                              ▼
                │              pending_hits.jsonl               ┌───────────────┐
                │              （按 tier 打标）                    │ context_only  │→ GoalNode.external_context
                │                         │                    │  （附加上下文） │   （只在该 Goal 自己的执行
                │                         │                    ├───────────────┤    任务里被读取，见 §5.4）
                │                         │                    │ advance_goal   │
                │                         │                    │ （限流后触发）  │→ set_status(active) 或
                │                         │                    └───────────────┘   enqueue_turn(meta=goal定向)
                │                         ▼
                │            report_tiers 对应的 N 个 cron job
                │            （每个 tier 一个 sys:watchlist_report_<tier_id>）
                │            定期取该 tier 自上次消费以来的记录 → 生成摘要
                │                         ▼
                │              NotificationDispatcher
                │              （kanban 必达 + email 可选，渠道可扩展）
                ▼
     （原有落点不变，本次不改动）
```

关键设计取舍：**"识别关注对象"（WatchlistMatcher）和"判断 Goal 相关性"
（GoalRelevanceEngine）是两个完全独立、互不依赖的消费者**，各自订阅
`external.*` 事件、各自持有独立游标——不是"先匹配关注词，命中的才去判断
Goal 相关性"这种串联关系。任何一条外部事件都会**同时**被这两套机制各自处理：

- WatchlistMatcher 只关心"用户配置的关键词是否出现"，服务的是"通知"。
- GoalRelevanceEngine 只关心"这条信息是否跟某个 active Goal 有关"，服务的是
  "agent 执行"。两者产生的通知最终都会汇入同一个 `NotificationDispatcher`，
  但触发逻辑完全独立。

---

## 3. 数据模型

### 3.1 `.agent/external_input/watchlist.yaml`（新增）

```yaml
watchlist:
  - id: competitor_launch
    keywords: ["某竞品发布", "CompetitorA release"]
    match_type: keyword        # keyword | regex（regex 先占位，不实现）
    report_tier: minute_1      # 引用 report_tiers.yaml 里的 tier id
    notify_channels: []        # 可选覆盖该 tier 的默认渠道；留空则用 tier 默认
    scope:
      source_channels: []      # 限定只在这些 source channel（如 rss/weather）里生效；空=不限
    enabled: true
```

> 注意：**不再包含 `related_goal_ids`/`goal_action` 字段**——Goal 相关性完全
> 交给 `GoalRelevanceEngine` 动态判断，watchlist 只负责"关键词命中 → 按什么
> 频率通知"这一件事。

### 3.2 `.agent/notification/report_tiers.yaml`（新增，任意 N 个 tier）

```yaml
tiers:
  - id: minute_1
    schedule: "interval:60"
    notify_channels: [kanban]
  - id: minute_30
    schedule: "interval:1800"
    notify_channels: [kanban]
  - id: hourly
    schedule: "interval:3600"
    notify_channels: [kanban]
  - id: daily
    schedule: "cron:0 22 * * *"
    notify_channels: [kanban, email]
  # 用户可以任意增删条目，id 全局唯一；schedule 复用 CronScheduler 已支持的
  # "interval:<seconds>" / "cron:<expr>" 两种格式，不用再造一套调度语法。
```

daemon 启动时，按这份配置动态为每个 tier 注册一个 `sys:watchlist_report_<id>`
cron job（复用 `CronScheduler` 现有的 job 模型，前缀 `sys:` 表示不可删除、
只可 disable，与现有 `sys:daily_digest` 等内置 job 同一套治理规则）。

### 3.3 `.agent/notification/config.yaml`（新增，渠道配置）

```yaml
default_channels: [kanban]     # 未指定 notify_channels 时的兜底
channels:
  kanban:
    enabled: true               # 恒真，不可关闭（兜底渠道）
  email:
    enabled: false
    smtp_host: "smtp.example.com"
    smtp_port: 465
    use_ssl: true
    username: "${ENV:MINI_AGENT_SMTP_USER}"
    password: "${ENV:MINI_AGENT_SMTP_PASSWORD}"
    from_addr: "mini-agent@example.com"
    to_addrs: ["you@example.com"]
```

密钥类字段支持 `${ENV:VAR_NAME}` 占位符，运行时从环境变量读取，避免明文写进
会被提交的 yaml。

### 3.4 待汇报队列 `.agent/external_input/pending_hits.jsonl`（新增）

```json
{"id": "hit:competitor_launch:evt123", "tier": "minute_1", "source": "watchlist",
 "watchlist_id": "competitor_launch", "title": "...", "detail": "...",
 "url": "...", "matched_at": 1234567890.0, "consumed": false}
```

每个 tier 的 cron job 只读取 `tier == 自己` 且 `consumed == false` 的记录，
发送成功后整体重写标记 `consumed: true`（复用 `alerts.jsonl` 的
"小文件、低频写、整体重写"处理方式，风格与现有 `acknowledge_alert()` 一致）。

### 3.5 `GoalNode` 新增字段（`perception/goal_backlog.py`）

```python
@dataclass
class GoalNode:
    ...
    # 本次新增：
    external_context: list[dict] = field(default_factory=list)
    # 每项: {"event_id","title","snippet","occurred_at","source_id"}
    # 只保留最近 N 条（默认 20），由 attach_external_context() 维护，
    # 供 §5.4 的 prompt 注入读取。

    last_external_advance_at: float = 0.0
    # 本次新增：上一次因外部信号被"主动拉起"(advance_goal) 的时间戳，
    # 用于 §5.5 的冷却限流判断。跟 progress_notes 不是一回事——
    # 后者是"做到哪一步了"，这个字段纯粹是限流用的时间戳。
```

新增方法：
```python
def attach_external_context(self, goal_id: str, item: dict, max_keep: int = 20) -> bool: ...
def try_advance_goal(self, goal_id: str, cooldown_seconds: float) -> "AdvanceDecision": ...
    # 内部判断冷却期，返回是否真的允许执行"拉起"动作；
    # 无论是否允许，都会先调用 attach_external_context()。
```

### 3.6 候选队列 `.agent/external_input/goal_relevance_candidates.jsonl`（新增）

```json
{"id": "cand:evt123:goal_xxx", "event_id": "evt123", "goal_id": "goal_xxx",
 "event_title": "...", "event_detail": "...", "goal_title": "...",
 "goal_description": "...", "prefilter_score": 0.42, "judged": false}
```

Stage① 规则层写入，Stage② LLM 判定后标记 `judged: true`，避免重复判定同一对
(event, goal)。

---

## 4. 核心机制详解

### 4.1 WatchlistMatcher（新增，纯规则，零 LLM 成本）

- 独立 consumer（`consumer_name="watchlist_matcher"`），跟 `IngestionPolicy`
  一样挂在 `AutonomousLoop.tick()` 的 maintenance 档位里，各自独立游标。
- 对每条 `external.*` 事件，逐条比对 `watchlist.yaml` 里已启用的项：
  - `scope.source_channels` 非空时先按 source channel 过滤；
  - 关键词子串匹配（大小写不敏感），复用 `RuleEngine.keyword_hits()` 同款
    简单实现风格，不引入分词/语义匹配（成本可控优先）。
- 命中后：
  1. 去重（沿用 `normalize_title_key()` 做归一化，滚动窗口内同一话题不重复计入，
     避免多个 RSS 源转载同一条新闻反复触发）；
  2. 写入 `pending_hits.jsonl`，`tier` 取该 watchlist 项的 `report_tier`。
- **不做**任何 Goal 相关的判断——这是 `GoalRelevanceEngine` 的职责，两者完全独立。

### 4.2 GoalRelevanceEngine（新增，两阶段，LLM 判定相关性）

#### Stage① 候选生成（规则层，每个 tick 都跑，零 LLM 成本）

- 独立 consumer（`consumer_name="goal_relevance_candidate"`），同样挂在
  `tick()` 的 maintenance 档位。
- 对每条 `external.*` 事件，与 `goal_backlog.active_goals()`（**只看
  level=goal 且 status=active**，不含 Objective）逐一计算一个廉价的重合度
  分数——用 `normalize_title_key()` 同款归一化后做 token 重合比例，
  或简单关键词提取重合，不追求精确，**目的只是尽量不漏掉候选**（宁可让
  Stage② LLM 多判几个"不相关"，也不要在这一层就误杀掉真正相关的事件）。
- 重合度超过一个很低的阈值（默认宽松，只为过滤掉明显八竿子打不着的组合，
  控制 Stage② 的候选规模）即写入 `goal_relevance_candidates.jsonl`。
- 这一层完全对齐项目里 `soft_goal_deriver`/`next_action_advisor` 已有的
  "规则层先筛，可选 LLM 层再判"两段式风格（`next_action_advisor.py` 里
  `rank_with_llm=True` 就是同款设计），不是本次新发明的模式。

#### Stage② LLM 批量判定（有候选才跑，成本受控）

- 独立的定时任务（沿用 `CronScheduler`，比如 `sys:goal_relevance_judge`，
  默认 `interval:600`——即每 10 分钟检查一次，间隔可配置）。
- **候选队列为空则直接跳过，不产生"空转"的 LLM 调用**（对齐
  `next_action_advisor` "候选为空时返回 None，不生成凑数建议"的克制原则）。
- 候选非空时，一次 LLM 调用批量处理（设置单次上限，比如最多 20 对，超出
  部分留到下一轮，避免单次 prompt 过长）：

  ```
  请判断下列"外部信息-目标"配对是否相关，并给出结构化结果：

  [1] 目标：{goal.title}（{goal.description}）
      外部信息：{event.title} —— {event.detail}

  [2] ...

  对每一项输出 JSON：
  {"index": 1, "relevant": true/false, "advance_worthy": true/false, "reason": "..."}
  ```

  - `relevant`：这条外部信息是否确实跟这个目标有实质关联；
  - `advance_worthy`：如果相关，是否重要到值得**现在就**重新推进这个目标
    （而不只是"记一笔，以后处理这个目标时再看"）——这个判断也交给 LLM 一并
    给出，不再依赖任何人工配置的开关。
  - 输出要求结构化 JSON、且要求给 `reason`（对齐
    `next_action_advisor` 里"LLM 排序层不允许无引用理由"的既有约束风格），
    解析失败的单条记录跳过、不影响其它条目，`judged` 照常标记为 true
    （避免死循环重试一条格式有问题的候选）。
- 处理结果：
  - `relevant=true` → 调用 `goal_backlog.attach_external_context(goal_id, ...)`，
    不管 `advance_worthy` 是否为真都会执行这一步（信息至少要能被看到）。
  - `relevant=true and advance_worthy=true` → 额外调用
    `goal_backlog.try_advance_goal(goal_id, cooldown_seconds=...)`，
    由该方法内部判断冷却期后决定是否真的执行"拉起"动作（见 §4.4）。
  - `relevant=false` → 跳过，仅标记 candidate 为已判定。

### 4.3 分级汇报（Report Tiers，任意 N 个）

- `report_tiers.yaml` 定义的每个 tier，daemon 启动时动态注册一个
  `sys:watchlist_report_<tier_id>` cron job（复用 `CronScheduler`，
  `schedule` 字段直接透传给现有的 `interval:`/`cron:` 解析逻辑，不新增
  调度语法）。
- 每次运行：读取 `pending_hits.jsonl` 里 `tier == 自己` 且未消费的记录——
  **没有新记录就直接跳过，不发送空消息**；有记录则按 `watchlist_id` 分组，
  生成一份 Markdown 摘要（标题+条数+链接列表），交给
  `NotificationDispatcher.dispatch()`，发送成功后整体标记为已消费。
- 默认内置四档（`minute_1` / `minute_30` / `hourly` / `daily`），用户可以在
  `report_tiers.yaml` 里任意增删——新增一个 tier id、改一下
  `.agent/cron_jobs.json`（或者由代码在读取 `report_tiers.yaml` 时自动
  补一条 `sys:watchlist_report_<新id>` job，见 §6 P3 阶段）即可生效，
  不需要碰核心调度代码。

### 4.4 Goal 关联执行（`context_only` + `advance_goal`，含限流）

- **`context_only`（任何 `relevant=true` 的判定都会执行这一步，默认行为，
  不需要用户配置任何东西）**：把该事件的摘要 append 进
  `GoalNode.external_context`（保留最近 20 条），不改变 Goal 的调度/状态。
- **`advance_goal`（`relevant=true and advance_worthy=true` 时才可能触发，
  需要通过冷却期检查）**：
  - `try_advance_goal(goal_id, cooldown_seconds)` 先检查
    `now - last_external_advance_at < cooldown_seconds`——**在冷却期内直接
    跳过拉起动作**（`context_only` 那一步已经执行过，信息不会丢，只是不重复
    触发执行），避免同一个 Goal 被反复打扰式拉起；`cooldown_seconds` 默认
    建议 6 小时，做成配置项（比如 `.agent/notification/config.yaml` 里加
    `goal_advance_cooldown_seconds: 21600`）。
  - 不在冷却期内时，按 Goal 当前状态分两种处理：
    - **`status != active`（如 paused）**：调用现有的
      `goal_backlog.set_status(goal_id, "active")` 恢复活跃，并在
      `progress_notes` 追加一笔"因外部信号《{event.title}》于
      {date} 被自动重新激活"。恢复后自然重新进入
      `has_actionable_work()`/`next_task_description()` 的候选池，按既有的
      maintenance/autonomous 档位节奏被处理，**不需要额外的调度机制**。
    - **`status == active`**：走现有的 `enqueue_turn` 落点机制（复用
      `IngestionPolicy._enqueue_turn()` 同款实现，或者在
      `GoalRelevanceEngine` 里直接调用同一个 `InputQueue.enqueue()`），
      消息模板明确带上 `meta: {"target_goal_id": goal_id, "trigger_event_id": event_id}`：

      ```
      外部信号显示与你正在跟踪的目标『{goal.title}』相关的新进展：
      {event.title}
      {event.detail}
      请结合这条信息判断目标是否需要推进、以及下一步该做什么。
      ```

      提交后跟普通任务一样正常消耗 LLM、正常受 `ResourceArbiter`/预算等既有
      门控约束——**完全不绕过任何现有的资源控制**。
  - 执行了"拉起"动作（不管是哪一种）之后，更新
    `last_external_advance_at = now`，供下次冷却判断使用。
  - **agent 拿到这条 enqueue_turn 任务后判断"其实不需要推进"，属于正常的
    一次任务执行结果，不需要任何额外处理**——冷却计时器已经在提交时启动，
    这就是防止骚扰的全部机制，不需要感知任务执行的最终结论。

### 4.5 Prompt 精确注入（只在处理这个 Goal 的任务里注入）

这是本次修正的重点之一：`external_context` **绝不做全局注入**，只接入两个
明确的、本来就是"正在处理这个 Goal"的入口：

1. `objective_executor._default_llm_decompose(llm_helper, objective)`——
   在现有的 "当前进展：{objective.progress_notes}" 之后，追加一段：
   ```
   相关外部信息（最近 {N} 条）：
   - [{occurred_at}] {title}：{snippet}
   - ...
   ```
   只取 `objective.external_context`（或其所属顶层 Goal 的 `external_context`，
   取决于 Objective/Goal 的层级关系，具体见 §6 P5 实施细节）里的记录，
   **不会把其它 Goal 的上下文混进来**。
2. `objective_executor._default_llm_redecompose(...)`——同一份数据，同一个
   注入位置（紧跟已有的"已完成步骤摘要"之后）。
3. `advance_goal` 触发的 `enqueue_turn` 消息本身自带该事件的 title/detail——
   这条消息天然就是"只讲这一个事件、针对这一个 Goal"，不存在"全局注入"的
   问题，不需要额外处理。

明确排除（不注入）：
- 普通对话 turn 的 system prompt / 上下文。
- `next_action_advisor`、`soft_goal_deriver` 的 LLM 排序层 prompt（如果它们
  以后需要用到，再单独评估接入，本次不动）。
- 其它 Goal/Objective 的分解 prompt（只看自己的 `external_context`）。

---

## 5. NotificationDispatcher（通知系统，可扩展渠道）

```python
# src/mini_agent/notification/dispatcher.py（新增模块）

@dataclass
class NotificationMessage:
    title: str
    body: str
    source: str            # "watchlist_report" | "gateway" | ...
    url: Optional[str] = None
    meta: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

class NotificationChannel(ABC):
    channel_type: str
    @abstractmethod
    def send(self, message: NotificationMessage, cfg: dict) -> bool: ...

_REGISTRY: dict[str, type[NotificationChannel]] = {}
def register_channel(name: str): ...
def get_channel_class(name: str) -> type[NotificationChannel]: ...

class NotificationDispatcher:
    def dispatch(self, message: NotificationMessage, channels: Optional[list[str]] = None) -> dict:
        """channels 为 None 时用 default_channels；kanban 永远尝试发送（兜底），
        其它渠道逐个 try/except，一个渠道失败不影响其它渠道，失败记
        log_exception，不重试（跟项目里"单点故障不拖垮整体"的一贯风格一致）。"""
```

**第一批实现两个渠道**：

- **`KanbanChannel`**：直接复用现有 `alerts.jsonl` + `/v1/inbox` 机制——落成
  一条跟现有 `external_alert` 同构的记录，看板"待处理告警"面板直接就能看到，
  不需要新造 UI。
- **`EmailChannel`**：走标准 SMTP（`smtplib` + `email.mime`），配置见 §3.3，
  密钥支持 `${ENV:...}` 占位符。发送失败（连接超时/认证失败等）记
  `log_exception`，不重试、不阻塞其它渠道。

**渠道注册表模式**跟 `ExternalInputSource` 的 `@register_source` 完全一致
风格，以后要加企业微信 webhook/Telegram/钉钉机器人等渠道，只需要新增一个
`NotificationChannel` 子类 + 装饰器注册，不用碰 `NotificationDispatcher` 本体。

---

## 6. 改造实施计划（分阶段）

| 阶段 | 内容 | 涉及文件（新增/修改） |
|---|---|---|
| **P1** | `NotificationDispatcher` 骨架 + kanban/email 两个渠道 | 新增 `src/mini_agent/notification/{__init__,dispatcher,channels/kanban.py,channels/email.py}.py`；新增 `.agent/notification/config.yaml` 加载逻辑 |
| **P2** | `watchlist.yaml` 加载 + `WatchlistMatcher`（纯规则匹配 + 去重 + 写 pending_hits） | 新增 `src/mini_agent/external_input/watchlist.py`；新增 `.agent/external_input/watchlist.yaml` |
| **P3** | `report_tiers.yaml` 加载 + 动态注册 `sys:watchlist_report_<id>` cron job + 消费 pending_hits 生成摘要并 dispatch | 新增 `src/mini_agent/external_input/report_tiers.py`；修改 `evolution/cron_scheduler.py`（支持"按配置动态追加内置 job"）；新增 `.agent/notification/report_tiers.yaml` |
| **P4** | `GoalRelevanceEngine` Stage①（候选生成，规则层，接入 `tick()`） | 新增 `src/mini_agent/external_input/goal_relevance.py`；修改 `evolution/autonomous_loop.py`（`_tick_maintenance()` 里新增一个消费点，跟 `run_ingestion_policy_once` 同级） |
| **P5** | `GoalRelevanceEngine` Stage②（LLM 批量判定 + `attach_external_context`/`try_advance_goal`） | 修改 `perception/goal_backlog.py`（新增字段+方法）；修改 `src/mini_agent/external_input/goal_relevance.py`；新增 `sys:goal_relevance_judge` cron job；修改 `api/server.py`（提供 llm_helper 给判定函数，风格对齐 `_llm_decompose` 的现有接线方式） |
| **P6** | Prompt 精确注入（decompose/redecompose） | 修改 `evolution/objective_executor.py::_default_llm_decompose/_default_llm_redecompose` |
| **P7** | 看板展示（关注对象列表、tier 配置只读展示、Goal 详情页"🔗相关外部信息"、通知发送记录） | 修改 `apps/mini_agent_kanban/{app.py,client.py}`；新增/修改 `api/routes.py` 只读端点 |
| **P8** | 测试补齐（对齐现有 `tests/test_external_input_*.py` 风格，每个新模块独立测试文件） | 新增 `tests/test_watchlist_matcher.py`、`test_report_tiers.py`、`test_goal_relevance_engine.py`、`test_notification_dispatcher.py` |

P1-P3 之间、P4-P6 之间基本互相独立，可以分开小步提交验证；P7/P8 依赖前面
阶段跑通后再补。

---

## 7. 成本与安全边界（汇总）

- **零 LLM 成本的部分**：WatchlistMatcher 全程、GoalRelevanceEngine Stage①
  候选生成——跟现有 `poll()`/`RuleEngine` 一样，纯脚本、可以任意提高轮询
  频率也不会放大 LLM 调用。
- **唯一引入 LLM 调用的环节**：GoalRelevanceEngine Stage②，且：
  - 候选队列为空时不调用；
  - 单次调用有候选数量上限（默认 20 对/次）；
  - 调用频率本身是可配置的 cron 间隔（默认 10 分钟），不是每个 tick 都调；
  - 解析失败的单条候选跳过，不阻塞整批、不无限重试。
- **`advance_goal` 的执行动作**（`set_status`/`enqueue_turn`）本身不调用
  LLM，且有冷却限流（默认 6 小时/Goal），避免同一个 Goal 被反复自动拉起。
- **`enqueue_turn` 提交后是否真的推进，完全由 agent 自己在该轮任务里决定**，
  网关只负责"提上日程"，不代表任何强制执行，也不需要额外的结果回传机制。
- **通知发送失败**（邮件 SMTP 连不上等）不影响 kanban 落地——kanban 作为
  兜底渠道永远尝试发送且几乎不会失败（本地文件写入）。

---

## 8. 遗留的小开放项（不影响开工，实施中顺手定即可）

以下几点不需要现在决策，实施到对应阶段时按项目一贯风格（"默认最省事、
出问题就近处理"）顺手定：

1. Stage①的重合度阈值/token 归一化方式具体怎么调，先给个宽松默认值，
   跑一段时间观察 Stage②的"相关判定命中率"再调整（不需要现在敲定精确算法）。
2. `report_tiers.yaml` 新增一个 tier 后，`sys:watchlist_report_<id>` job 是
   在 daemon 启动时自动补注册，还是需要用户在 `cron_jobs.json` 里手动加一条——
   倾向"自动补注册、缺失才补，已存在不重复"，跟现有内置 job 的初始化逻辑
   一致，实施时直接照搬即可。
3. `GoalNode.external_context` 里的记录要不要在 Goal 完成/归档时一并清理——
   建议跟随 Goal 本身的生命周期自然过期，不需要单独的清理 job。

---

（本文档为设计确认稿，实施前若有新的调整意见，请在对应章节直接反馈。）
