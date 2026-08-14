# Goal 主动巡检推送与健康总览（Cycle Patrol）指南

> 能力 C（主动巡检 + 推送）/ 能力 D（看板全局健康总览）均已实现（Stage 1
> + Stage 2），Stage 3（§6 开放问题中「cron_skip 与 cron 层告警去重」/
> 「总览面板细粒度排序」两项已落地）见
> `next_doc/goal_cron_cycle_proactive_patrol_and_health_overview_plan.md`。
> 与 [跨轮次诊断报告指南](goal-cycle-diagnostics-guide.md) /
> [交互式调优指南](goal-cycle-tuning-guide.md) 是同一条主线的延续——那份
> 方案是"拉模式"（要你主动去查某个 Goal），本方案补上"推模式"（系统定期
> 主动巡检、命中问题主动推送）+ 跨 Goal 的汇总视图。

## 解决什么问题

跨轮次诊断报告（`/agent goals diagnose`）虽然能回答"这个 Goal 整体跑得
怎么样"，但你必须先想起来去查某一个具体 Goal 才会触发一次诊断——Goal
一多，容易漏看。本方案补上两件事：

- **主动巡检 + 推送**：不用等你去看，系统按固定间隔自己巡检所有周期性
  （recurring）Goal，命中问题信号时主动推送提醒（并且巡检环节用了
  LLM 来生成人类可读的摘要/合并降噪，而不是只堆规则阈值）。
- **看板全局健康总览**：看板顶部新增一个跨 Goal 的健康总览区块，一眼
  看出哪些 Goal 需要关注，不用逐张展开卡片。

## 能力 C：主动巡检 + 推送

### 默认关闭，打开的方式

`agent_config.json`：

```json
{
  "cycle_patrol": {
    "enabled": true,
    "interval_hours": 6.0,
    "llm_enabled": true,
    "max_push_per_run": 3,
    "push_cooldown_hours": 24.0,
    "generate_tuning_drafts": true,
    "dedupe_cron_skip_alert": true
  }
}
```

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `false` | 总开关。关闭时这部分代码完全不执行，连状态文件都不读，对现有部署零影响。 |
| `interval_hours` | `6.0` | 巡检间隔（不是 tick 间隔）——诊断报告的时效性以"轮"为单位，不需要按分钟级重算。 |
| `llm_enabled` | `true` | 规则筛出候选后，是否用 LLM 生成推送摘要/合并降噪。**规则筛不出候选时永远不会调用 LLM**，这个开关只影响"候选存在时怎么呈现"，不影响"巡检本身要不要跑"。 |
| `max_push_per_run` | `3` | 单次巡检命中的 Goal 数超过这个值时，合并降噪成一条消息，不逐条刷屏。 |
| `push_cooldown_hours` | `24.0` | 同一个 Goal 同一轮问题信号的推送冷却时间。首次命中不推送（避免单次抖动打扰你），持续命中且过了冷却时间才真正推送。 |
| `generate_tuning_drafts` | `true` | 命中规则建议时（比如 cron 连续跳过对应"放宽间隔"）是否顺带生成一份调优草案（`draft` 状态，不自动确认/应用）。 |
| `dedupe_cron_skip_alert` | `true` | [Stage 3] 是否与 cron 层自己的连续跳过告警（`cron.skip_alert_threshold`）去重。开启时，巡检的 `cron_skip` 信号只覆盖"跨越阈值之前"的窗口（早期预警），一旦跳过次数达到/超过阈值，说明 cron 层本轮已经/即将发出它自己的告警，巡检不再对**纯 cron_skip** 信号重复推送（如果同一 Goal 还命中了其它信号类型，仍然会被巡检覆盖）。设为 `false` 退回 Stage 1/2 的原始行为（阈值-1 及以上都算命中，不设上界）。 |

### cron_skip 信号与 cron 层告警的关系（§6.2 开放问题的落地决定）

同一个 Goal 的"连续跳过"可能同时触发两层通知：

1. **cron 层**（`evolution/cron_scheduler.py::_maybe_alert_consecutive_
   skip()`）——跳过次数**恰好跨越** `cron.skip_alert_threshold` 那一刻
   发一次技术性告警，服务的是"这一次跨越"。
2. **巡检层**（本方案）——服务的是"周期性健康汇报 + 跨越阈值之前的早期
   预警"，两者定位不同、值得都保留，但如果不做任何处理，会在阈值附近
   产生两条高度重叠的通知。

`dedupe_cron_skip_alert=true`（默认）时，巡检把 `cron_skip` 信号的判定
窗口限制在 `[threshold-1, threshold)`（不含 threshold 本身）——也就是
说巡检只在 cron 层告警**即将**发生之前提前提醒一次，一旦真的跨越阈值，
就把这次通知的所有权交给 cron 层，不重复发。

### 巡检范围与判定标准

- 只巡检 `recurring=True` 的 Goal（一次性 Goal 没有"跨轮次健康状态"这个
  概念）。
- 判定标准跟看板卡片、CLI `diagnose` 用的**完全是同一套**（`recent_
  health_alerts`/`cron_health` 里的 `consecutive_skip_count`/长期卡在
  explore），不会出现"巡检说黄色，卡片显示绿色"这种两套标准打架的情况。
- 规则先行、LLM 兜底：规则筛出候选（可能为空）才会进入 LLM 环节；LLM
  失败时静默回退到规则拼接的模板文本，不会因为 LLM 不可用就不推送。

### 推送渠道

两条通道都会尝试，各自失败互不影响：

1. **`NotificationDispatcher`**——面向"你可能不在看对话"的场景，走已
   配置的通知渠道（默认含看板）。
2. **`InputQueue`**——面向"你正在跟 Agent 对话"的场景，推送内容像普通
   一轮对话一样出现，可以直接追问。

### 明确不做的事

- **不自动应用调优草案**——即使巡检顺带生成了草案，仍然停在 `draft`
  状态，确认/应用仍然是你的显式动作。
- **不巡检一次性 Goal**。
- **不新造健康判定标准**——完全复用看板卡片/CLI `diagnose` 已有的判定。

## 能力 D：看板全局健康总览

看板 Kanban Tab 顶部新增"🩺 健康总览"折叠区块，按 🔴🟡🟢 汇总所有
recurring Goal 的健康状态，红/黄逐条列出，绿色数量多时只显示统计数字。
每行有一个"🔍"按钮，点击后跳转到该 Goal 在正常分栏视图里的卡片位置
（复用已有的 `kanban_focus_node_id` 跳转机制）。

### REST 接口

```
GET /v1/goals/cycle_diagnostics_overview
```

返回：

```json
{
  "data_source": "patrol_snapshot",
  "generated_at": 1234567890.0,
  "goals": [
    {
      "goal_id": "...", "title": "...", "severity": "yellow",
      "alert_count": 2, "cron_consecutive_skip": 0,
      "execution_phase_mode": "explore", "next_run_at": 1234567890.0,
      "has_pending_tuning_proposal": true, "priority_score": 13
    }
  ]
}
```

`goals` 数组已经按 severity（红→黄→绿）、同一 severity 内再按
`priority_score` 降序排好序（[Stage 3 / §6.4 开放问题落地]，
`priority_score = alert_count * 10 + cron_consecutive_skip * 5 +
(3 if 长期卡在 explore else 0)`），前端可以直接按数组顺序渲染，不需要
自己再排一遍。这个权重**不改变** 🔴🟡🟢 三档判定标准本身（仍然是看板
卡片/CLI `diagnose` 那一套），只是在同一档位内提供更细的优先级参考，
帮助你在大量 Goal 同处 yellow（比如都在 explore 阶段，属正常状态）时
更快定位"真正紧急"的那几个。

### 数据来源：优先复用巡检快照，无快照时按需现算

- **能力 C 已开启且至少跑过一轮巡检**：`data_source="patrol_snapshot"`，
  总览面板直接读巡检产出的快照（`.agent/cycle_patrol_state.json`），
  不重新计算——这样总览面板显示的状态跟你实际收到的推送通知基于同一份
  数据，不会自相矛盾，Goal 数量多时看板首屏也不会被拖慢。
- **能力 C 未开启（或还没跑过一轮）**：`data_source="live"`，对所有
  recurring Goal 现跑一次纯规则诊断（不含 LLM 摘要），保证总览面板不
  强依赖巡检开关——巡检是"锦上添花的主动推送"，总览面板本身是"看板核心
  功能"。

### 交互约束

- 不做实时刷新/自动轮询——需要更新数据时手动刷新页面（Streamlit 本身的
  刷新机制），与看板其它数据展示方式（比如完成率趋势图）保持一致的交互
  预期。

## 状态文件

`.agent/cycle_patrol_state.json`：

```json
{
  "last_run_at": 1234567890.0,
  "signals": {
    "<goal_id>": {"first_detected_at": ..., "last_pushed_at": ..., "push_count": 1}
  },
  "overview": {
    "generated_at": 1234567890.0,
    "goals": [ ... ]
  }
}
```

`signals` 跟踪每个 Goal 当前"命中中"的问题信号，用于去重节流；信号消失
（下一轮巡检该 Goal 不再命中任何规则）时对应记录会被清理，下次重新计时。
`overview` 是能力 D 复用的健康快照，每次巡检（不管有没有命中推送）都会
更新。

## 实施记录

- **Stage 1**（能力 C）：`config/models.py::CyclePatrolConfig` +
  `evolution/cycle_patrol.py::run_cycle_patrol()` + 接入
  `AutonomousLoop._tick_maintenance()`。

  与方案文档字面描述的一处偏差：方案建议挂在 `_tick_passive()`，但
  `_tick_passive()` 有代码层面强制的边界——方法体内不引用 `GoalBacklog`
  任何方法（见 `evolution/autonomous_loop.py` 模块头部注释）。巡检需要
  遍历所有 recurring Goal，因此实际接入点改为 `_tick_maintenance()`
  （passive 的超集档位），与同文件里 `goal_relevance_candidate`/
  `reap_finished_cycles` 是同一档位边界理由。
- **Stage 2**（能力 D）：`evolution/cycle_patrol.py::build_overview_live()`
  / `load_overview()` + REST `GET /v1/goals/cycle_diagnostics_overview`
  + 看板 `_render_cycle_health_overview()`。

测试：`tests/test_cycle_patrol.py`（模块级，含节流/去重/合并降噪/LLM
失败回退/一次性 Goal 排除）、
`tests/test_cycle_patrol_overview_routes.py`（REST 端到端）。
