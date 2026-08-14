# 周期性 Goal/Cron 任务的主动巡检推送与全局健康总览设计方案

> 状态：**Stage 1（能力 C：主动巡检 + 推送）/ Stage 2（能力 D：看板全局
> 健康总览）均已实施完成**。实现见 `config/models.py::CyclePatrolConfig`
> + `evolution/cycle_patrol.py` + `AutonomousLoop._tick_maintenance()`
> （接入点与方案 §2.1 描述有一处偏差，见下方"实施偏差说明"）+ REST
> `GET /v1/goals/cycle_diagnostics_overview` + 看板"🩺 健康总览"区块。
> 用户指南见 [`docs/goal-cycle-patrol-guide.md`](../docs/goal-cycle-patrol-guide.md)，
> 测试见 `tests/test_cycle_patrol.py` / `tests/test_cycle_patrol_overview_routes.py`。
>
> **实施偏差说明**：§2.1 建议巡检挂在 `AutonomousLoop._tick_passive()`，
> 但该方法体在代码层面有强制边界——不引用 `GoalBacklog` 任何方法（见
> `evolution/autonomous_loop.py` 模块头部注释）。巡检需要遍历所有
> recurring Goal，因此实际接入点改为 `_tick_maintenance()`（passive 的
> 超集档位），与同文件里 `goal_relevance_candidate`/`reap_finished_
> cycles` 是同一档位边界理由，不是遗漏。

> 与 [`goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md`](goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md)
> 是同一条主线的延续——那份方案交付的诊断报告（能力 A）/ 交互式调优
> （能力 B）已经实施完（Stage 1-3 + CLI + REST + 看板集成，见该文档
> 状态栏），但目前整条链路**完全是拉模式**：不管 CLI、REST 还是看板，
> 都要用户主动去查某一个具体 Goal 才会触发一次诊断。本方案补上两个
> 用户提出的方向：
>   - **能力 C：主动巡检 + 推送**——不用等用户去看，系统自己定期巡检、
>     命中问题信号时主动推送，且巡检环节要用上 LLM（而不是只堆规则
>     阈值）。
>   - **能力 D：看板全局健康总览**——现在看健康状态要一张张展开 Goal
>     卡片，Goal 一多就不现实，需要一个跨 Goal 的汇总视图。
>
> 两者刻意放在同一份方案里，因为**能力 D 的数据源直接复用能力 C 的巡检
> 产出**（详见 §3.1）——不是简单的"顺带一起做"，而是设计上有真实的依赖
> 关系：没有 C 提供的定期快照，D 要么每次打开看板都对所有 Goal 现算一遍
> （成本问题），要么另起一套独立的缓存机制（重复造轮子）。

## 0. 现状盘点

已经有的（均见上一份方案文档状态栏）：

- `perception/cycle_diagnostics.py::build_cycle_diagnostics()`——单个 Goal
  的规则聚合诊断报告，纯只读、零 LLM 成本，包含 `recent_health_alerts`/
  `cron_health`/`execution_phase_mode` 等结构化健康信号。
- `perception/cycle_diagnostics.py::summarize_report_with_llm()`——可选
  LLM 自然语言摘要层，输入只有已聚合的结构化字段，失败静默回退。
- `perception/cycle_tuning.py::suggest_tuning_from_diagnostics()`——基于
  诊断报告的规则信号（cron 连续跳过、长期卡在 explore）生成候选调优
  草案，**不调用 LLM**，命中才生成，是纯规则的"如果...就建议..."。
- CLI `/agent goals diagnose <id> [--summarize]` / `tune ...`，REST
  `GET .../cycle_diagnostics` / `POST .../tuning_proposals*`，看板
  `🩺 诊断与调优` 折叠区——三端都已经打通，但**都需要先定位到某个具体
  Goal**。

已经有、可以直接复用的"周期性主动检查 + 状态跟踪 + 节流推送"参考实现
（不是本方案首创，是同一套模式在项目里的第三次出现）：

1. `evolution/cron_scheduler.py::CronScheduler._maybe_alert_consecutive_skip()`
   ——`consecutive_skip_count` 恰好跨越阈值那一刻，通过
   `NotificationDispatcher` 发一次告警，失败静默，不影响 `tick()` 主流程。
2. `evolution/next_action_advisor.py::check_persistent_attention_mismatch()`
   ——由 `AutonomousLoop._tick_passive()` 周期性调用，用一个状态文件
   （`AgentPaths.attention_mismatch_state_path`）跟踪每个信号的
   `first_detected_at`/`push_count`，"持续超过阈值时长 + 未超过单 session
   推送上限"才真正推送一次，推送内容通过 `InputQueue.enqueue()` 注入，
   像普通一轮对话一样经现有多客户端 SSE 推送流转发给看板/微信/移动端。

本方案的"能力 C"就是同一套模式的第三次应用，只是巡检对象换成"所有
recurring Goal 的诊断报告"，并且这次要接入 LLM（用户明确要求）。**不
新发明状态机，不新发明推送通道**，复用上面两个函数已经验证过的模式。

## 1. 需求拆解：两个子能力

- **能力 C（主动巡检 + 推送）**：系统定期（不需要用户触发）对所有
  recurring Goal 跑一次诊断，规则先筛出"值得关注"的候选，LLM 负责把
  候选信号变成人话摘要、并在同一轮命中多个 Goal 时做合并降噪，然后
  通过已有的通知/推送通道送达用户。
- **能力 D（全局健康总览）**：看板顶部新增一个跨 Goal 的健康总览区块，
  按 🔴🟡🟢 排序列出所有 recurring Goal 的健康状态，点击跳转到对应卡片
  （复用已有的 `kanban_focus_node_id` 跳转机制），不需要逐张展开。

## 2. 能力 C：主动巡检 + LLM 辅助推送

### 2.1 触发时机与节流

接入 `AutonomousLoop._tick_passive()`，与 `check_persistent_attention_
mismatch()` 挂在同一个方法体里、同一层级（不新增一个 tick 入口）。但
巡检本身不应该跟主 tick 一样按秒级/分钟级跑——诊断报告的时效性以"轮"
为单位，不需要那么高频。用一个独立的时间间隔控制：

- 新增 `AgentPaths.cycle_patrol_state_path`
  （`<project_root>/.agent/cycle_patrol_state.json`），跟
  `attention_mismatch_state_path` 同级、同风格，记录：
  - `last_run_at`：上次巡检时间戳，`_tick_passive()` 里先检查
    `now - last_run_at >= interval_hours * 3600`，不到间隔直接跳过，
    避免每次 tick 都重算一遍所有 Goal 的诊断报告。
  - `signals`：按 `goal_id` 记录每个 Goal 当前"命中中"的问题信号
    （`first_detected_at`/`last_pushed_at`/`push_count`），跟
    `_load_mismatch_state()` 的结构完全同构，用于 §2.5 的去重节流。

### 2.2 巡检范围与规则先行

1. 只巡检 `recurring=True` 的 Goal（一次性 Goal 没有"跨轮次健康状态"
   这个概念，不在本方案范围）。
2. 对每个 recurring Goal 调用 `build_cycle_diagnostics()`（零 LLM 成本，
   跟看板卡片一样便宜），按已有的健康信号规则筛出候选：
   - `recent_health_alerts` 非空
   - `cron_health` 显示 `consecutive_skip_count` 接近或达到
     `cron.skip_alert_threshold`
   - `execution_phase_mode == "explore"` 且已经连续多轮（复用
     `execution_phase.py` 里判定"长期卡在 explore"的既有阈值，不新造
     一套）
3. **规则先行，LLM 兜底**——先用规则筛出候选集合（可能为空），只有
   非空时才进入 §2.3 的 LLM 环节。规则筛不出任何候选的这一轮巡检，
   直接更新 `last_run_at` 后返回，不产生任何 LLM 调用，不推送任何消息。
   这是"零 LLM 成本"原则在巡检场景下的延续：LLM 是"候选已经存在，帮我
   把候选变成更好的呈现"，不是"帮我判断有没有候选"。

### 2.3 LLM 的角色（用户要求：巡检要用 LLM）

规则筛出候选后，LLM 承担两件规则做不好的事，都是"锦上添花"而不是"决策
判断"——最终推不推、推给谁，仍然由规则算出的候选集合决定，LLM 不能
凭空让候选消失或凭空新增候选：

1. **把候选信号变成人类可读的推送摘要**——直接复用/调用
   `summarize_report_with_llm()`（不新写一个摘要函数），对每个候选
   Goal 生成一段自然语言总结，作为推送文案的正文。
2. **多 Goal 同时命中时的合并降噪**——如果这一轮巡检同时有 3、5 个
   Goal 命中信号，逐条推送 3、5 条消息骚扰感很强（类似
   `growth_advisor` 系列"矛盾证据不覆盖只记录"的保守取向，这里对应的
   保守取向是"宁可合并成一条，不要刷屏"）。命中数量超过
   `max_push_per_run`（见 §2.7）时，把所有候选的结构化摘要一次性交给
   LLM，让它生成**一条**合并推送文案（"本次巡检发现 N 个 Goal 需要关注：
   ...，其中最值得优先处理的是 ..."），而不是分别调用 N 次单 Goal 摘要
   再简单拼接——拼接得到的是"N 段摘要堆在一起"，合并生成得到的是"帮你
   排了优先级的一段话"，这才是 LLM 在这里的真实价值。

LLM 失败时的回退（与诊断摘要层同一原则，"失败静默回退到纯规则展示"，
**不阻塞推送本身**）：

- 单 Goal 摘要失败 → 退化为规则拼接的文本（"Goal「X」cron 已连续跳过
  N 次" 这类模板句子，`_maybe_alert_consecutive_skip()` 现在的文案就是
  这个风格，直接复用同一套模板）。
- 合并降噪失败 → 退化为"逐条推送"（不因为合并失败就整体放弃推送）。

配置项 `cycle_patrol.llm_enabled` 默认 `True`（受 `cycle_patrol.enabled`
总开关约束——总开关默认 `False`，见 §2.7；只有巡检本身开启的前提下，
`llm_enabled` 才有意义），对应用户"巡检时应该用 LLM"的要求；但即使
`llm_enabled=True`，规则没筛出候选时依然不会产生任何 LLM 调用（§2.2）
——"默认用 LLM"指的是"候选存在时默认用 LLM 去呈现"，不是"巡检这件事
本身依赖 LLM 才能跑"，两者是不同的默认值语义，需要在实现和文档里都
写清楚，避免被误解为"每次巡检都要调 LLM"。

### 2.4 与 `suggest_tuning_from_diagnostics()` 的联动

规则筛出的候选 Goal，除了生成推送摘要，**顺带调用一次
`suggest_tuning_from_diagnostics()`**（这个函数本身零 LLM 成本，命中
才生成草案，不调用不会"浪费"）——如果规则信号本身就对应一条可行的
调优建议（比如"cron 连续跳过 → 建议放宽 interval"），直接在巡检阶段
把草案生成好（`source="rule_suggested"`，状态仍是 `draft`，**不自动
confirm/apply**，与 Stage 2 已有边界完全一致），推送文案里提示"已经
生成一份调优草案待确认，可以在看板里查看"。

这样用户收到推送后，不需要再手动点"🔍 基于诊断规则生成建议"——巡检已经
替他跑过一次，看到通知直接去看草案即可。没命中规则建议（比如信号是
"长期卡在 explore"但没有对应的规则改动方案）时，推送文案仍然只是提醒
关注，不强行生成一份"凑数"的草案。

### 2.5 推送去重/状态跟踪

跟 `check_persistent_attention_mismatch()` 同一套节流逻辑，状态存在
`cycle_patrol_state.json` 的 `signals[goal_id]` 里：

- 某个 Goal 第一次被规则命中：记录 `first_detected_at`，本次**不**
  推送（避免偶发的单次抖动就推送——比如 cron 因为一次性资源竞争跳过了
  一轮，下一轮就恢复正常，这种情况不该打扰用户）。
- 持续命中且满足 `push_cooldown_hours` 冷却时间（距上次推送/首次发现
  已经过去足够久）：真正推送，`last_pushed_at` 更新，`push_count += 1`。
- 信号消失（这轮巡检该 Goal 不再命中任何规则）：清除该 Goal 的跟踪
  记录，下次重新计时——与 `_load_mismatch_state()` 里"信号消失即清理"
  的策略一致。
- 单次巡检最多推送 `max_push_per_run` 条独立消息，超过时触发 §2.3
  的合并降噪，退化成一条消息。

### 2.6 推送渠道

两条通道都接，跟 `_tick_passive()` 里两种已有推送各自的定位一致，
不是二选一：

1. **`NotificationDispatcher`**（`notification/dispatcher.py`）——面向
   "用户可能不在看对话"的场景，走已配置的 `channels`（默认含
   `kanban`，也可能配了 email 等）。`source="cycle_patrol"`，
   `meta={"goal_ids": [...], "signal_types": [...]}`，与
   `_maybe_alert_consecutive_skip()` 用同一个 dispatcher 实例创建方式。
2. **`InputQueue.enqueue()`**（跟 `attention_mismatch_push` 一致）——
   面向"用户正在跟 Agent 对话"的场景，推送内容作为一轮普通对话出现，
   用户可以直接追问（"这个 Goal 具体是什么问题？"），不需要先去看板/
   通知渠道再回来问。

两条通道各自独立失败不影响另一条——`NotificationDispatcher.dispatch()`
本身已经对每个 channel 做了失败隔离，`InputQueue.enqueue()` 失败按
`_tick_passive()` 现有风格 try/except 吞掉、记日志，不影响本轮 tick
其它逻辑。

### 2.7 配置项

新增 `CyclePatrolConfig`（挂在 `AppConfig.cycle_patrol`，走
`param_registry.NESTED_CONFIG_BLOCKS` 通用加载机制，吸取上一份方案里
"新增子配置块忘了接入 loader.py"的教训，实现时第一步就要注册，不是
最后补）：

```python
@dataclass
class CyclePatrolConfig:
    enabled: bool = False              # 总开关，默认关闭
    interval_hours: float = 6.0        # 巡检间隔（不是 tick 间隔）
    llm_enabled: bool = True           # 命中候选时是否用 LLM 生成摘要/合并降噪
    max_push_per_run: int = 3          # 超过则合并降噪为一条
    push_cooldown_hours: float = 24.0  # 同一 Goal 同一信号的推送冷却时间
    generate_tuning_drafts: bool = True  # 命中规则建议时是否顺带生成调优草案（§2.4）
```

`enabled=False` 时 `_tick_passive()` 里这一段代码完全不执行（连状态
文件读取都不做），对现有部署零影响，与 `next_action_push_enabled`/
`digest_advisor` 系列的默认关闭策略一致。

## 3. 能力 D：看板全局健康总览

### 3.1 数据来源：优先复用巡检快照，无快照时按需现算

`cycle_patrol_state.json` 已经存了"每个 Goal 当前是否命中信号"的
最新判定（§2.5），总览面板**优先直接读这份快照**，而不是打开看板就对
所有 recurring Goal 现跑一遍 `build_cycle_diagnostics()`——这样有两个
好处：

1. 性能：Goal 数量多时，看板首屏不会因为总览面板卡住。
2. 一致性：总览面板显示的状态与用户实际收到的推送通知基于同一份
   数据，不会出现"总览说没事，但你刚收到一条巡检推送"这种自相矛盾
   （两条链路如果各自现算，理论上可能因为巡检间隔和看板打开时机不同
   而出现短暂不一致）。

`cycle_patrol.enabled=False`（巡检功能没开）时快照文件不存在，总览
面板退化为**按需现算**：打开看板 Tab 时对所有 recurring Goal 跑一次
纯规则诊断（不含 LLM 摘要，成本跟现在每张卡片渲染时已经在做的诊断
读取相当，Goal 数量正常规模下可接受），保证总览面板不严格依赖巡检
开关——巡检是"锦上添花的主动推送"，总览面板本身是"看板核心功能"，
两者耦合但不应该产生"不开巡检就完全看不到总览"这种强依赖。

### 3.2 REST 接口

新增 `GET /v1/goals/cycle_diagnostics_overview`：

- 有巡检快照：直接读快照拼装返回，标注 `data_source="patrol_snapshot"`
  和快照的 `generated_at`，前端据此展示"数据更新于 X 分钟前"，让用户
  知道这不是实时数据。
- 无快照：现算（只跑规则层，不调 LLM），标注
  `data_source="live"`。

返回结构（示例）：

```json
{
  "data_source": "patrol_snapshot",
  "generated_at": 1234567890.0,
  "goals": [
    {
      "goal_id": "...", "title": "...", "severity": "yellow",
      "alert_count": 2, "cron_consecutive_skip": 0,
      "execution_phase_mode": "explore", "next_run_str": "...",
      "has_pending_tuning_proposal": true
    }
  ]
}
```

### 3.3 看板 UI

Kanban Tab 顶部（`_render_objective_completion_trend()` 之后、"➕ 新建
目标"之前）新增一个"🩺 健康总览"区块：

- 按 `severity` 降序（红→黄→绿，绿色数量多时默认折叠只显示统计数字，
  不逐条列出）列一个紧凑表格：Goal 标题 / 徽章 / 告警条数 / 下次触发 /
  是否有待处理调优草案。
- 每行提供一个"🔍 定位"按钮，复用已有的
  `st.session_state["kanban_focus_node_id"]` 机制，点击后跳到该 Goal
  在正常分栏视图里的卡片位置（`focus_node` 那段代码已经存在，直接
  设置这个 session_state 即可，不需要新写跳转逻辑）。
- 顶部标注数据来源和更新时间（"最近一次巡检：X 分钟前" 或"实时计算"），
  避免用户误以为总览是绝对实时的。
- 巡检功能未开启（`data_source="live"`）时，总览区块标题旁加一句提示
  "开启主动巡检后，这里会显示最近一次后台巡检的结果，无需每次打开
  看板都重新计算"，引导用户去了解 §2 的功能，但不强制。

## 4. 两个能力的依赖关系与实施顺序

D 依赖 C 提供的快照数据结构（`cycle_patrol_state.json` 的 schema），
但 D 本身有"无快照时现算"的降级路径，所以**两者可以分两个 Stage 实施，
不要求 C 完全上线才能开始 D**：

- **Stage 1**：能力 C 的巡检 + 状态跟踪 + 推送（`CyclePatrolConfig` +
  `cycle_patrol.py`（新模块，放 `perception/` 或 `evolution/`，倾向
  `evolution/`——巡检行为更接近 `cron_scheduler`/`next_action_advisor`
  这类"主动跑起来做事"的模块，不是纯只读聚合）+ 接入
  `AutonomousLoop._tick_passive()`）。产出快照文件供 Stage 2 使用。
- **Stage 2**：REST `cycle_diagnostics_overview` 端点（读快照 + 现算
  降级两条路径都要实现和测试）+ 看板总览区块。

每个 Stage 完成后按项目既有节奏更新文档（本文件状态栏 + 可能需要新增
`docs/goal-cycle-patrol-guide.md`）、补充测试、跑回归，与上一份方案的
落地节奏保持一致。

## 5. 明确不做的事

1. **巡检不自动应用调优草案**——即使巡检顺带生成了草案（§2.4），仍然
   停在 `draft` 状态，confirm/apply 仍然是用户的显式动作，延续 Stage 2
   "不引入无需确认的自动应用"这条边界，巡检不能成为绕开这条边界的
   新入口。
2. **不新造一套健康判定标准**——巡检用的规则信号（`recent_health_
   alerts`/`cron_health`/execution_phase 阈值）跟看板卡片、CLI
   `diagnose` 展示的完全是同一套，只是多了"定期主动跑一遍 + 推送"这个
   动作，不会出现"巡检说黄色，但你打开卡片看到的是绿色"这种两套标准
   打架的情况。
3. **不巡检一次性 Goal**——一次性 Goal 没有"跨轮次健康趋势"的概念，
   巡检范围明确限定在 `recurring=True`。
4. **总览面板不做"实时刷新/自动轮询"**——用户需要更新数据时手动刷新
   页面（Streamlit 本身的刷新机制），不引入 WebSocket/定时轮询之类的
   前端复杂度，与项目现有看板的其它数据展示方式（比如目标完成趋势图）
   保持一致的交互预期。

## 6. 风险与开放问题

1. **多 Goal 合并降噪的 prompt 设计**——§2.3 第 2 点的"一次性把多个
   候选摘要交给 LLM 生成一条排优先级的合并推送"，实际效果需要实测：
   如果候选之间完全不相关（一个是 cron 跳过、一个是长期 explore），
   LLM 排出的"优先级"是否真的有意义，还是不如老老实实按 severity 排序
   展示、只把摘要文案交给 LLM——实施时先做小范围试用再决定是否保留
   "LLM 排优先级"这部分，退化到"只生成摘要不排序"不影响核心功能。
2. **`push_cooldown_hours` 与 `cron.skip_alert_threshold` 的关系**——
   如果一个 Goal 同时触发 cron 层已有的 `_maybe_alert_consecutive_
   skip()` 和本方案的巡检推送，用户可能在短时间内收到两条内容高度
   重叠的通知。需要在实施前确认：是巡检直接跳过"已经由 cron 层
   `skip_alert_threshold` 覆盖的信号类型"（避免重复），还是两条通知
   本来就服务不同目的（cron 层是"技术性告警"，巡检是"整体健康周期性
   汇报"）保留两者——倾向后者但需要在文案上明确区分定位，避免用户
   觉得是重复骚扰。
3. **多用户模式下巡检推送给谁**——当前 `NotificationDispatcher`/
   `InputQueue` 的推送目标沿用现有机制（未特别区分多用户场景），
   `multi_user_enabled=True` 时巡检产生的推送应该推给谁（owner？所有
   订阅了对应 Goal 的用户？）不在本方案首次实现范围内讨论，先按现有
   单播/广播的默认行为处理，后续如果多用户场景有明确诉求再单独评审。
4. **总览面板的 `severity` 判定要不要比单 Goal 卡片更细**——现在单 Goal
   卡片只有 🔴🟡🟢 三档，总览面板汇总几十个 Goal 时，如果大部分都是黄色
   （比如很多 Goal 都在 explore 阶段，这是正常状态而非问题），三档可能
   不够用户快速定位"真正紧急"的那几个。是否需要在总览面板引入更细的
   排序权重（比如告警条数、cron 连续跳过次数分别加权）留待 Stage 2
   实施时结合实际数据分布再定，不阻塞 Stage 1 落地。
