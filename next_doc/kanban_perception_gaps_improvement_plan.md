# 看板感知层面改进方案：让"系统不太对劲"这件事主动被看见

- **版本**: v1（草案，方向级 + 关键设计点级规划，不是逐行实现代码）
- **前置文档**:
  - `docs/kanban-dashboard-guide.md`（看板现状全貌，15 个 Tab）
  - `docs/llm-failover-guide.md`（LLM 多配置故障转移 / 多 Key 轮转）
  - `docs/system-events-bus-guide.md`（跨子系统事件总线，本文档方向 A
    的候选复用基础设施）
  - `docs/scheduling_unification_and_kanban_visibility_improvement_plan.md`
    （"🗓️ 全局日程"Tab、`/v1/autonomous/gating_history` 的既有实现）
  - `docs/auto-quarantine-guide.md`（wiki 隔离区机制）
  - `next_doc/growth_advisor_improvement_plan_v4.md`（N1 健康度趋势——
    "每日快照 + jsonl 追加 + 折线图"模式的先例，本文档多处复用同一模式）
- **触发背景**：一次关于"看板还能怎么帮用户更好地管理/感知 agent"的讨论
  里，聚焦到"感知层面"这一类问题——不是"操作不顺手"，而是**有些信息
  已经在系统里产生了，但看板完全没有把它摆到用户面前**，用户得知道
  去哪个 Tab、点开哪个卡片，才能碰巧看到。本文档逐项排查这类"看不到的
  东西"，给出方向、设计要点、改动量级评估和分期建议。
- **方法论**：不是凭空列愿望清单，每一项都先在代码里核实"数据到底存不
  存在、存在的话是不是已经有某个不显眼的角落在展示"，避免重复造轮子或
  者提出实际上已经做了的东西。

---

## 排查方法与结论总览

逐个检查候选信号源之后，实际情况可以分三类：

| 类别 | 含义 | 本文档处理方式 |
|---|---|---|
| **完全没有暴露** | 数据在内存/日志里，但没有任何 API 端点、没有任何看板展示 | 方向 A（哨兵聚合面板的一部分）+ 方向 B |
| **有展示，但要"翻到"才能看见** | 已经在某个 Tab/某张卡片的展开区里，但需要用户主动点进那一条具体记录才会看到 | 方向 A 的核心动机——把这些"藏起来的已知信息"聚合到一个入口 |
| **有瞬时快照，没有历史/趋势** | 诊断类接口能看"现在怎样"，但看不出"这周变严重了还是变好了" | 方向 C、方向 D |

具体核实结果：

- **Cron job 连续失败次数**（`state.consecutive_failures`，
  `cron_job_workspace.py:82`）：**已展示**，但只在"⏰ Cron 任务"Tab
  里展开某一个具体 job 的执行状态卡片才能看到（`app.py` 中
  `c3.metric("连续失败次数", ...)`）。如果用户没有主动点开某个 job，
  完全不会意识到它已经连续失败了 5 次——这正是"有展示但要翻到才能看见"
  的典型例子。
- **外部输入来源连续失败**（`external_input/poller.py`）：**已经做得
  比较好**——"🔌 外部输入"Tab 本身就会展示 🔴 熔断 / 🟡 近期有失败的
  来源状态（`app.py:5627-5629`），不需要额外处理，本文档不重复建设。
- **Objective 执行步骤重试次数**（`objective_executor.py` 的
  `step.retry_count`）：单个 Objective 卡片下方能看到当前重试原因
  （"失败重试携带原因"Track F），但**没有跨 Objective 的聚合视角**——
  看不出"这轮 daemon 运行里，有几个 Objective 正卡在重试循环里"。
- **LLM 故障转移状态**（`llm/client_pool.py` 的
  `LLMClientPool.snapshot()` / `ApiKeyPool.snapshot()`）：**完全没有
  暴露**。核实过 `api/routes.py` 里唯一用到 `_client_pool` 的两处
  （约 388 行、473 行）都只是取"当前激活的模型名"用于状态栏/命令补全，
  `snapshot()` 返回的 key 级 `fail_count`/`cooldown_remaining`、
  entry 级切换状态从未被任何端点返回过，看板也没有任何地方展示。
  这意味着：如果 daemon 在后台因为某个 provider 频繁触发限流而不断
  切 key/切配置，用户**完全没有渠道知道这件事正在发生**，只能等到
  所有 fallback 都耗尽、彻底报错的那一刻才会注意到。
- **LLM 调用量/token 消耗**：核实了 `llm/debug_logger.py`，这是一套
  完整的请求/响应逐条落盘机制，但默认 `enabled=False`（需要设置环境
  变量 `LLM_DEBUG=1`），且落盘内容是完整的 request/response body
  （用于调试排障，不是为统计设计的），**没有轻量级、默认开启的调用
  次数/token 计数器**。也就是说"这个 daemon 今天到底调用了多少次
  LLM、大概花了多少 token"这个最基础的问题，当前完全答不出来，除非
  临时打开调试日志再手写脚本统计。
- **仲裁状态历史**（`resource_arbiter.py` 的 `read_gating_history()`，
  已在"🗓️ 全局日程"Tab 展示）：目前只有**逐条时间线**（"什么时候从
  `full` 变成了 `degraded`"），没有任何**聚合统计**（"过去 7 天有
  百分之多少的时间处于 `degraded`/`blocked`"）。逐条时间线在条目多
  的时候，人眼很难心算出这个比例。
- **wiki 隔离区积压**（`wiki/quarantine.py` 的 `load_quarantine()`/
  `ScanReport`）：核实了 `api/routes.py` 和 `apps/mini_agent_kanban/
  app.py`，**两处都完全没有 `quarantine` 相关代码**，只有 CLI 命令
  （`cli/commands/quarantine.py`）能看。这是一个纯粹的"完全没有暴露"
  的信号源。
- **记忆库/成长趋势类快照**：`growth_health_trend.jsonl`（v4 N1）已经
  验证了"每日快照 + 降采样 + 折线图"这个模式的可行性，但目前只服务于
  成长顾问一个模块，本文档方向 D 讨论是否值得推广。

---

## 方向 A：哨兵聚合面板（Sentinel Panel）——把"藏起来的已知信息"摆到一处

### A.0 定位：跟"全局待办中心"是姊妹关系，但语义不同

看板已有的"📥 全局待办中心"（`GET /v1/inbox`）聚合的是**"需要你做一个
决定"**的事项：待审批权限、待回答交互、执行失败的 Objective。本方向
新增的哨兵面板聚合的是**"系统状态可能不太对劲，你大概率没注意到"**的
事项——两者的关键区别是：待办中心的每一条都有明确的下一步操作（批准/
拒绝/查看），哨兵面板的很多条目本身**不需要用户立即做什么**，只是
"提醒你留意"，等它自己好转，或者用户判断后决定要不要介入。

**不要把两个面板合并**：语义混在一起会让待办中心变得嘈杂（哨兵类信息
往往数量更多、更新更频繁），也会让"需要你做决定"这个高优先级信息被
稀释。哨兵面板做成顶栏一个独立的可折叠区块，跟待办中心并列，不是子集
关系。

### A.1 数据来源：全部是已有数据的重新聚合，不新增采集逻辑

新增一个后端聚合函数（建议放在 `perception/` 或者新建
`observability/sentinel.py`，跟 `system_events.py` 平级），从以下既有
来源直接读取，不引入新的采集/监控代码：

```python
def sentinel_summary(paths) -> dict:
    """哨兵聚合：把散落在各处、容易被忽略的"系统状态异常"信号收集到一处。
    每一类都只读现有落盘状态，不做任何写操作，失败降级为该类返回空列表，
    不影响其它类别的展示。"""
    return {
        "cron_jobs_with_failures": _scan_cron_consecutive_failures(paths),
        "stuck_objective_steps": _scan_objective_retry_hotspots(paths),
        "quarantine_backlog": _scan_quarantine_backlog(paths),
        "llm_failover_state": _read_llm_pool_snapshot(),  # 见方向 B
        "arbitration_recent_ratio": _gating_ratio_last_n_days(paths),  # 见方向 C
    }
```

- **`cron_jobs_with_failures`**：遍历所有 cron job 的 `state.json`，
  筛出 `consecutive_failures > 0` 的（哪怕只失败了 1 次也值得提醒，
  阈值可配置），返回 `job_id`/`consecutive_failures`/`last_error`/
  `enabled` 状态——尤其要标出"已启用但一直在失败"这种最容易被忽视的
  组合（用户以为它在正常跑，实际上每次触发都失败）。
- **`stuck_objective_steps`**：遍历当前活跃 Objective 的执行状态，
  筛出 `retry_count` 达到一定比例（比如 `>= MAX_STEP_RETRIES - 1`，
  快要放弃前的最后一次）的 step，提前给用户一个"这个 Objective 快要
  卡死了"的信号，而不是等到它彻底失败、进入待办中心才知道。
- **`quarantine_backlog`**：直接调用 `wiki/quarantine.py` 已有的
  `load_quarantine()`，返回积压条数 + 最早一条的时间，方向 E 会
  详细展开。
- **`llm_failover_state`**：见方向 B，读取 `LLMClientPool.snapshot()`
  当前状态（是否已经切离首选 provider、各 key 的冷却状态）。
- **`arbitration_recent_ratio`**：见方向 C，对 `read_gating_history()`
  的结果做一次聚合统计。

### A.2 展示位置

顶栏新增一个折叠区块"⚠️ 系统状态哨兵"，紧挨着现有的"📥 全局待办中心"
下方。默认折叠，但当任意一类非空时自动展开（跟现有权限/交互请求非零
自动展开的交互模式一致，看板里已经有这个先例，不需要新发明交互范式）。
每一类一行摘要 + 展开看明细，明细里对 cron job 提供"跳转"按钮（复用
顶栏"⚙️ daemon 正在执行 N 项任务"已经实现的 tab 跳转 + 高亮机制，见
`kanban-dashboard-guide.md` 顶部状态条一节），点击直接跳到"⏰ Cron
任务"Tab 并定位到那个 job。

### A.3 API 端点

新增 `GET /v1/sentinel/summary`，返回 A.1 的聚合结果。跟"🗓️ 全局日程"
Tab 一样的加载策略：顶栏轮询这个端点（低频，比如每 30-60 秒一次，
不需要跟对话事件流一样的高频轮询），不需要用户展开区块才请求（哨兵
面板的意义就在于"不用你主动找就能看见"，如果要展开才请求，跟现状的
"藏起来"没有本质区别）。

### A.4 风险与开放问题

1. **不要做成第二个"全量扫描每次轮询都重新算一遍"**：`cron_jobs_with_
   failures` 需要遍历所有 job 的 state 文件，`quarantine_backlog` 需要
   读一个 JSON 文件，量级都不大，可以接受每次轮询都重新计算；但如果
   未来 cron job 数量变得很大（几十上百个），需要重新评估是否要加缓存。
2. **阈值需要可配置**：`consecutive_failures > 0` 就提醒可能对某些
   偶发失败率高但无害的 job（比如依赖外部网络的检索类任务）太敏感，
   建议阈值可配置（默认比如 `>= 2`），避免"哨兵面板天天有内容、用户
   审美疲劳直接不看了"这种反效果。
3. **不要在哨兵面板里引入新的"已读/已忽略"状态机**：这会让本来是
   "轻量提醒"的东西变成又一套需要维护状态的工作流，跟 A.0 节"不需要
   用户立即做什么"的定位冲突。如果某一类信号持续存在但用户判断"不需要
   处理"，让它持续显示是符合预期的（就像浏览器一直显示"3 个标签页
   有未读消息"一样，不是 bug）。

**改动量级**：中——纯聚合，不修改任何现有函数的行为，但需要新增一个
模块 + 一个 API 端点 + 顶栏一个新区块，涉及 5 个不同数据源的适配。

---

## 方向 B：LLM 调用可观测性——故障转移状态 + 轻量调用计数

这是排查过程中发现的最意外的空白：一个把"故障转移"作为核心卖点写进
`docs/llm-failover-guide.md` 的机制，运行时状态却完全没有任何观测
入口。分两块，互相独立、可以分开做。

### B.1 故障转移状态暴露（小改动，优先做）

`LLMClientPool.snapshot()` 和 `ApiKeyPool.snapshot()` **已经实现好了**
——这不是新开发，是"接上一根已经焊好的线"：

- 新增只读端点 `GET /v1/self/llm_pool_status`，内部直接调用
  `bridge.agent._client_pool.snapshot()`（对齐 `api/routes.py` 现有
  取 `_client_pool` 的两处写法），`_client_pool` 不存在时返回空结构，
  不报错。
- 看板"🧠 自我状态"Tab（已经有"⚙️ 执行模型"这类只读观测区块，风格一致）
  新增一个"🔀 LLM 故障转移状态"区块：展示当前激活的 provider/model 是
  不是首选项（`current != 0` 时高亮提示"已切换到备用配置"）、每个
  configured key 的可用性和冷却剩余时间。

**这一步不需要新增任何持久化**，纯粹是把已经在内存里的状态通过 API
读出来展示，改动量级很小，性价比高。

### B.2 轻量调用计数（新增持久化，量级更大）

`llm/debug_logger.py` 太重（默认关闭、记录完整报文），不适合作为
"今天调用了多少次 LLM"这种基础问题的答案来源。建议新增一个独立的、
**默认开启**的轻量计数器，跟调试日志是两套东西：

```python
# llm/call_stats.py 新增（独立于 debug_logger.py）
@dataclass
class CallStatsRecord:
    ts: float
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    duration_ms: int
    outcome: str  # "success" / "error" / "key_switch" / "config_switch"
```

- 每次 `LLMClientPool.call_with_pool()` 成功或失败后追加一条轻量记录
  （不含 request/response 正文，只有数字和结果分类），写入
  `.agent/llm_call_stats.jsonl`；
- 复用 `growth_health_trend.jsonl` 的降采样思路：按天聚合（总调用数/
  总 token 数/失败次数/切换次数），原始逐条记录只保留最近 N 天，超期
  的压缩成每日汇总，避免无限增长；
- API：`GET /v1/self/llm_call_stats?days=7`，返回按天聚合的序列；
- 看板"🧠 自我状态"Tab 新增"📊 LLM 调用统计"折线图/柱状图（调用次数、
  失败率、切换次数按天展示），回答"这周是不是比上周更依赖备用配置"
  这类问题。

### B.3 风险与开放问题

1. **B.2 涉及给 `call_with_pool()` 的每次调用增加一次文件写入**，
   需要评估高频调用场景（比如密集的工具循环）下这个额外 I/O 的开销
   ——建议参考 `system_events.py` 已经验证过的"文件追加 + 跨平台锁"
   写法，或者先在内存里攒一小批（比如每 10 次调用或每 30 秒）再落盘，
   降低写入频率。
2. **B.1 优先级明显高于 B.2**：B.1 是纯读取现有内存状态，零新增存储、
   零性能影响；B.2 需要新的落盘格式和降采样治理，属于"值得做但不是
   最紧急"的部分，可以分两期。
3. token 计数目前只是"调用了多少次/多少 token"，**不等于成本**（不同
   provider/model 的单价不同）。如果要做到"大概花了多少钱"，需要一份
   provider/model 到单价的映射表，这个映射表本身需要用户维护（价格会
   变），本文档不建议现在就做单价换算，先把"调用量"这个更客观、不需要
   用户额外维护数据的指标做出来。

**改动量级**：B.1 小，B.2 中。

---

## 方向 C：仲裁状态聚合统计——从"时间线"到"这周有多正常"

`read_gating_history()` 已经能看到逐条状态变化，但人眼很难从一串
"07-20 10:03 full→degraded，07-20 14:22 degraded→full，07-22 09:01
full→blocked..."这样的列表里心算出"过去 7 天处于 degraded 的时间占
比"。新增一个纯计算函数：

```python
def gating_ratio_summary(paths, *, window_days: int = 7) -> dict:
    """基于 read_gating_history() 的记录重建状态区间，计算窗口期内
    full/degraded/blocked 三态各自的累计时长占比。纯计算，不新增
    任何落盘文件——所有输入数据 read_gating_history() 已经持久化好了。
    """
```

- 需要注意 `read_gating_history()` 当前只在"变化时"记一条（3145 行
  附近的实现），要还原出"某个时间点处于什么状态"需要用相邻两条记录的
  时间差做区间累加，最后一条记录到"现在"的这段区间也要计入当前状态；
- 展示位置：直接加在"🗓️ 全局日程"Tab 现有仲裁时间线的上方，一行摘要
  （比如"过去 7 天：🟢 正常 92% · 🟡 降级 6% · 🔴 阻塞 2%"），点开
  仍是现有的逐条时间线，不新增 Tab；
- API：在现有 `GET /v1/autonomous/gating_history` 的响应里顺带加一个
  `ratio_summary` 字段（不新增端点，因为数据来源和调用方完全一致，
  没必要拆两次请求）。

**风险**：`read_gating_history()` 目前有 `_GATING_HISTORY_MAX_ENTRIES
= 200` 的裁剪上限（超过会丢弃最旧的记录），如果窗口期（比如 7 天）
内的状态变化次数超过这个上限，会导致"重建区间"缺失最早的一段——这种
场景本身也说明系统在这段时间里状态变化异常频繁，属于"数据本身就不
稳定"，建议在展示上加一句"数据不完整，可能因为期间状态变化过于频繁"
的提示，而不是静默给出一个不准确的比例。

**改动量级**：小——纯计算函数 + 一个字段透传，不新增持久化。

---

## 方向 D：把"每日快照 + 折线图"模式推广到更多领域

`growth_health_trend.jsonl`（成长顾问 N1）已经跑通了"`run_daily_cycle`
收尾时记一条快照 → 降采样 → API → 看板折线图"这一整套模式。排查下来，
至少还有两类信息适合套用同一个模式，而不是每次都重新发明一套存储/
展示逻辑：

### D.1 Goal/Objective 完成率趋势

当前"📌 目标看板"Tab 只有"当下"的状态分列展示，看不出"这周完成的
Objective 比上周多还是少""平均一个 Objective 要重试几次才能完成"这类
趋势。可以在 daemon 每日收尾（复用 `growth_advisor.run_daily_cycle()`
同一个每日调用点，或者新建一个平行的每日 cron，取决于代码归属更适合
放在哪个模块）记一条快照：`objectives_completed_today` /
`objectives_failed_today` / `avg_retry_count` / `active_goals_count`。

### D.2 记忆库增长趋势（与 N2 cron 记忆回填联动）

`growth_health_trend.jsonl` 里已经有 `total_entries` 字段，其实已经
覆盖了"记忆总条数走势"这个需求（详见 `docs/growth-advisor-guide.md`
5.5 节 N1/N2）。这里单独提出来是想强调：**不需要为"记忆增长趋势"这个
需求单独另起一份存储**，它已经被 growth advisor 的健康度趋势覆盖了，
只是这个入口目前挂在"🌱 成长顾问"Tab 下，如果希望在别的地方（比如
"🧠 自我状态"Tab）也能看到，做法应该是"复用同一份 `growth_health_
trend.jsonl` 数据、换一个展示位置"，而不是重新采集一遍。

### D.3 风险与开放问题

1. **不要为每个新领域都新建一个独立的 jsonl 文件**：`growth_health_
   trend.jsonl` 的降采样/治理经验值得复用，但如果 D.1、方向 B.2、
   未来还有更多领域都各自建一个"每日快照"文件，会重蹈 v4 文档里
   提到的"`growth_feedback_ledger.jsonl` 尚未纳入数据生命周期管理"
   这类问题——建议评估是否值得抽一个通用的"每日快照存储"小工具函数
   （`_append_daily_snapshot(path, fields, *, compact_fn)`），被
   growth_health_trend / D.1 / B.2 共用降采样逻辑，而不是各自平行
   实现一份几乎相同的读写代码。
2. D.1 涉及新的每日调用点，需要明确挂在哪个现有的每日调度节奏上
   （避免新增一个独立线程/cron，参考 `system-events-bus-guide.md`
   "不新增线程，延续轮询+状态文件风格"这条既有约束）。

**改动量级**：D.1 中（新增快照 + 展示，但可以照抄 N1 的实现模式，
开发成本主要在"选取哪些字段、挂在哪个调用点"上，不是技术难点）；
D.2 已经被覆盖，只是展示位置的产品决策，几乎零成本。

---

## 方向 E：wiki 隔离区积压——从"CLI 专属"到"看板可见"

`wiki/quarantine.py` 的 `load_quarantine()`/`ScanReport` 目前是一个
完全独立于看板/API 之外的孤岛，只有 `cli/commands/quarantine.py` 能
访问。这类"格式损坏/解析失败被隔离的 wiki 页面"如果持续积压，用户
除非记得定期敲 CLI 命令检查，否则永远不会知道。

- 新增只读端点 `GET /v1/wiki/quarantine_status`，直接调用
  `load_quarantine()`，返回积压条数、每条的 `page_path`/`issue`/
  记录时间；
- 这一项已经并入方向 A（A.1 的 `quarantine_backlog`），不需要单独
  展示位——积压条数进哨兵面板即可，明细仍然通过 CLI 处理（隔离区的
  修复本身已经有 `wiki-knowledge-base-guide.md`/`auto-quarantine-
  guide.md` 描述的 LLM 修复流程，看板这一步只负责"让用户知道有积压"，
  不需要重新做一套看板端的修复交互）。

**改动量级**：小——一个只读端点 + 并入方向 A 的聚合，不新增修复流程。

---

## 优先级与分期建议

| 序号 | 方向 | 优先级 | 理由 | 改动量级 |
|---|---|---|---|---|
| S1 | B.1（LLM 故障转移状态暴露） | 高 | 数据已经在内存里现成可用，纯新增只读端点+展示，是本文档里性价比最高的一项 | 小 |
| S2 | 方向 E（wiki 隔离区暴露） | 高 | 同样是"数据已存在、纯暴露"，且逻辑简单，建议跟 S1 一起做完垫底方向 A | 小 |
| S3 | 方向 A（哨兵聚合面板） | 高 | 本文档的核心诉求，S1/S2 做完后正好作为其中两个数据源接入，形成合力 | 中 |
| S4 | 方向 C（仲裁状态聚合统计） | 中 | 改动集中在一个计算函数，收益是让已有时间线更好读 | 小 |
| S5 | B.2（轻量调用计数） | 中 | 收益明确但需要新的落盘格式和降采样治理，建议排在 S1 验证完"有没有人真的关心这个数据"之后再做 | 中 |
| S6 | 方向 D.1（Goal/Objective 完成率趋势） | 低 | 锦上添花，建议等方向 D.3 的"通用每日快照小工具"抽出来后再做，避免重复实现降采样逻辑 | 中 |

建议第一期做 S1+S2+S3（哨兵面板先接入两个"数据已存在"的数据源上线，
验证整体交互模式），第二期视用户反馈决定是否推进 S4/S5，S6 排在
更后面且依赖第二期抽出的通用小工具。

---

## 验收标准

- **S1**：`GET /v1/self/llm_pool_status` 能返回 `LLMClientPool.
  snapshot()` 的完整结构；手动触发一次 key 切换（比如临时改小
  `key_cooldown` 并连续请求触发限流）后，看板"🧠 自我状态"Tab 能
  在不刷新代码的情况下看到对应 key 的 `fail_count`/冷却状态更新。
- **S2**：往 `.agent/wiki/quarantine.json`（或实际存储路径）手动写入
  一条测试记录后，`GET /v1/wiki/quarantine_status` 能返回该条目；
  清空后返回空列表，不报错。
- **S3**：让某个非 `sys:` cron job 连续失败 2 次（覆盖默认阈值）后，
  顶栏"⚠️ 系统状态哨兵"区块应自动展开并展示该 job；点击对应条目的
  "跳转"按钮应定位到"⏰ Cron 任务"Tab 并高亮该 job（复用现有跳转
  机制）。
- **S4**：`GET /v1/autonomous/gating_history` 响应新增的
  `ratio_summary` 字段，三态占比之和应约等于 100%（考虑到"重建区间"
  的边界处理，允许有小的舍入误差，但不应出现负数或明显超过 100% 的
  异常值）。

---

## 已知风险汇总（跨方向）

1. 方向 A 的哨兵面板如果阈值设置不当（比如"失败一次就提醒"），容易
   造成审美疲劳、用户直接忽略整个区块——这是本文档里最需要在实现时
   反复调参、而不是一次定死的部分。
2. 方向 B.2 涉及给 LLM 调用主链路增加落盘 I/O，需要压测高频调用场景
   下的开销，必要时改成"攒批写入"而不是"每次调用都落盘"。
3. 方向 D 如果不先抽出通用的"每日快照"小工具，后续每新增一个领域的
   趋势展示都会重新实现一遍降采样逻辑，是本文档里最容易"越做越乱"的
   一个方向，建议在做 S6 之前先花小成本把这个小工具抽出来。
4. 所有新增的 API 端点都是纯只读聚合，理论上不修改任何现有数据/状态，
   但仍建议在 code review 阶段确认每一个新增函数真的没有写操作——
   一旦"哨兵/观测"类端点意外产生副作用，会破坏用户对"看一眼不会有
   任何影响"这个预期的信任。
