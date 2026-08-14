# 周期性 Goal/Cron 任务的跨轮次诊断与交互式调优设计方案

> 状态：**Stage 1（只读诊断）/ Stage 2（规则+结构化调优 draft/confirm/
> apply/reject）/ Stage 3（可选的 LLM 自然语言解析层，默认关闭）均已
> 实施**。遵循既有的"每个 Stage 完成后更新文档 + 跑回归"节奏。
> Stage 1 实现见 `src/mini_agent/perception/cycle_diagnostics.py`、CLI
> `/agent goals diagnose <goal_id>`、REST
> `GET /v1/goals/{goal_id}/cycle_diagnostics`、
> [诊断报告使用指南](../docs/goal-cycle-diagnostics-guide.md)、
> `tests/test_cycle_diagnostics.py`。
> Stage 2 实现见 `src/mini_agent/perception/cycle_tuning.py`、CLI
> `/agent goals tune ...`、REST `/v1/goals/{goal_id}/tuning_proposals*`、
> [调优使用指南](../docs/goal-cycle-tuning-guide.md)、
> `tests/test_cycle_tuning.py`。
> Stage 3 实现见 `cycle_diagnostics.py::summarize_report_with_llm()`（诊断
> 报告的可选 LLM 自然语言摘要，CLI `diagnose --summarize` / REST
> `?summarize=true`）+ `cycle_tuning.py::parse_nl_request_to_changes()` /
> `build_tuning_proposal_from_nl()`（调优草案的可选 LLM 自然语言解析，
> CLI `tune <goal_id> "<改进意见>"`（无 `=` 时）/ REST
> `POST tuning_proposals {"nl_text": ...}`），配置开关
> `cycle_tuning.diagnostics_llm_summary_enabled` /
> `cycle_tuning.tuning_llm_parse_enabled`（见 `config/models.py::
> CycleTuningConfig`，均默认 `False`，已注册进
> `param_registry.NESTED_CONFIG_BLOCKS` 并接入 `loader.py`/`config_catalog.py`，
> `agent_config.json` 里的 `cycle_tuning.*` 会被正常读取和在看板配置页
> 展示，不是"写了当没写"）。**同批修复**：排查发现 `ExecutionPhaseConfig`
> （`progress_trend_llm_enabled`，见
> `goal_stuck_stats_and_llm_progress_judge_plan.md §2`）有同样的问题——
> 一直只是 `AppConfig` 上的字段，从未接入 `loader.py`，一并修复并补充了
> `tests/test_config_nested_blocks_wiring.py` 做静态+端到端双重回归守护
> （检查 `AppConfig` 每个子配置块是否被正确加载，防止后续新增配置块时
> 重演同一疏漏）。两层测试见
> `tests/test_cycle_diagnostics.py::TestSummarizeReportWithLLM`、
> `tests/test_cycle_tuning.py::TestParseNLRequestToChanges` /
> `TestBuildTuningProposalFromNL`。
> **看板集成**：CLI/REST 之外，`apps/mini_agent_kanban` 也接上了同一套
> 能力——每张 Goal 卡片新增 `🩺 诊断与调优` 折叠区（`app.py::_render_goal_
> cycle_diagnostics_widget()` / `_render_goal_tuning_widget()` /
> `_render_tuning_proposal_card()`，`client.py` 新增 7 个方法），交互设计
> 取舍见[调优使用指南「看板里的交互方式」](../docs/goal-cycle-tuning-guide.md#看板streamlit里的交互方式)。
> 全部复用已有 REST 接口，未新增后端逻辑；REST 层此前只有模块级单元测试、
> 缺少经过真实路由的端到端覆盖，一并补上
> `tests/test_cycle_diagnostics_tuning_routes.py`。
> 触发背景：用户提出，对于周期性执行的 Goal/cron 任务，有时候需要的不是
> "某一次执行跑得怎么样"，而是**"这个任务整体跑得怎么样"**——所有轮次
> 的情况、当前是否健康、用的目录结构和机制是什么；看完这份整体状况后，
> 用户可能会提出改进建议（调度频率、执行阶段、执行规范等），希望系统能
> 支持"生成调整草案 → 交互确认 → 生效"这样一个闭环，而不是要求用户自己
> 去改配置文件。

## 0. 现状盘点：已经有什么、还缺什么

先说清楚现状，避免重复造轮子——这条主线上散落着不少"单点"能力，缺的是
**把它们聚合成一份整体视图**，以及**"看完之后怎么改"的交互闭环**。

**已经有的跨轮次数据源（分散在不同地方，没有统一入口）**：

| 数据源 | 位置 | 覆盖的信息 |
|---|---|---|
| `GoalNode.cycle_count`/`reaped_cycle_child_ids` | `goals.json` | 已完成轮次总数、最近若干轮子节点 id 列表 |
| `goal_cycle_archive.jsonl` | `<workdir>/goal_cycle_archive.jsonl` | 更早轮次的完整子节点快照（`archive_finished_cycle_children()` 归档），见 [Goal/Cron 可见性与干预能力改进方案](goal_cron_visibility_and_intervention_improvement_plan.md) Track D |
| `ExecutionPhaseState.mode_history` | `.agent/execution_phase/<goal_id>.json` | 阶段变迁历史（探索→收敛→稳定→整理）+ 每次判定的信号来源，见 [Goal 执行阶段指南](../docs/goal-execution-phase-guide.md) |
| `check_phase_health()` 产出的告警 | 同上 + 通知系统 | 已识别的健康问题（长期卡在 explore、频繁反复横跳等），见 `goal_cron_task_optimization_holistic_plan.md` 方向 B |
| `output_workspace.read_all_manifests()` | `<outputs_root>/goals/<goal_id>/cycle_*/manifest.json` | 每一轮实际产出了哪些文件，已有现成的聚合读取函数 |
| `GoalNode.progress_notes` | `goals.json` | 自由文本追加的进展说明（人工+系统事件都会写入） |
| `CronJob.consecutive_skip_count`/`run_count` | `cron_jobs.json` | 该 Goal 绑定的 cron job 的触发健康度（是否经常被跳过） |
| `GET /v1/self/scheduling_overview` 的 `goal_cycle_channel.recent` | REST 端点 | 最近几次触发的时间/结果，但只是"最近 5 条"，不是完整历史 |

**明确缺失的部分**：

1. **没有一个统一的"整体诊断报告"**——上面 7 类数据源要拼出"这个 Goal
   整体状态如何"，用户现在得自己翻好几个文件/接口，跟 P4 之前"三条通道
   状态要拼图"是同一类问题（[统一调度层指南](../docs/unified-scheduler-guide.md)
   已经解决过一次类似的拼图问题，思路可以复用）。
2. **没有面向"目录结构/机制说明"的可读文档化输出**——用户想知道"这个
   Goal 现在用的产出目录规则是什么、阶段判定规则是什么"，现在得去翻
   `docs/` 里好几篇指南，没有一个"针对这个具体 Goal 当前配置"生成的
   摘要。
3. **没有"诊断 → 改进建议 → 确认应用"的闭环**——用户能改的入口都是
   独立的命令（`_cmd_recur` 改 schedule、`_cmd_phase` 改阶段、
   `_cmd_spec_generate`/`_cmd_spec_confirm` 改执行规范），但都是"用户
   已经想清楚要改什么"之后的**执行**动作，缺一个"先看诊断、再谈要不要
   改、改了之后先给我看一眼再生效"的中间环节。

## 1. 需求拆解：两个子能力

把用户的需求拆成职责清晰的两块，分开设计（但共享同一份诊断数据）：

- **能力 A（观测）**：跨轮次诊断报告——只读聚合，回答"这个 Goal 整体
  跑得怎么样"。
- **能力 B（调整）**：基于诊断报告的交互式调优——用户给出改进意见 →
  系统生成一份"变更草案"（明确列出改哪些字段、改成什么） → 用户确认
  → 应用。**不是**让 Agent 自由地去改代码/配置文件，而是把"能改什么"
  收窄到一组白名单参数，复用现有的、已经过测试的修改入口。

这个拆分本身也是设计原则：能力 A 是纯读取，零风险，可以先独立上线；
能力 B 依赖能力 A 的数据，但改动本身有"确认"这道闸门兜底，风险可控。

## 2. 能力 A：跨轮次诊断报告

### 2.1 数据结构

新增纯函数模块 `perception/cycle_diagnostics.py`，核心是一个只读聚合
函数：

```python
def build_cycle_diagnostics(paths: AgentPaths, goal_backlog: GoalBacklog,
                             goal_id: str) -> CycleDiagnosticsReport:
    ...
```

`CycleDiagnosticsReport`（dataclass，可 `to_dict()` 供 REST/CLI 复用）：

```python
@dataclass
class CycleDiagnosticsReport:
    goal_id: str
    goal_title: str
    # ── 概览 ──
    cycle_count: int                    # 已完成轮次总数
    recurring: bool
    schedule: Optional[str]             # 当前绑定的 cron 表达式/interval
    created_at: float
    last_scheduled_at: float
    # ── 健康信号（复用 check_phase_health 的判定逻辑，不重新发明）──
    execution_phase_mode: str           # 当前有效阶段
    phase_history_summary: list[dict]   # 精简后的阶段变迁列表（时间+新阶段+原因）
    recent_health_alerts: list[dict]    # 最近触发过的健康告警（来自 check_phase_health）
    cron_health: Optional[dict]         # 绑定 cron job 的 consecutive_skip_count/run_count
    # ── 产出与进展 ──
    recent_cycle_summaries: list[dict]  # 最近 N 轮：cycle_no/status/manifest 文件数/完成时间
    output_dir: str                     # 该 Goal 的产出目录根路径（output_workspace.goal_output_base_dir）
    progress_notes_tail: str            # progress_notes 最后若干行（不是全量，避免过长）
    # ── 机制说明（面向用户的静态文本，解释"当前用的是什么规则"）──
    mechanism_notes: list[str]          # 例如"阶段判定：auto 模式，规则见……"
                                         # "产出目录：按 cycle 编号分目录，见……"
    generated_at: float
```

关键设计点：

- **不做任何新的判定逻辑**——健康信号直接复用 `check_phase_health()`
  的既有输出，阶段历史直接读 `ExecutionPhaseState.mode_history`，不
  重新发明一套"整体健康度"评分算法。这份报告是"聚合展示"，不是"新的
  决策层"。
- **`mechanism_notes` 是静态模板文本 + 少量变量替换**，不需要 LLM 生成
  ——"当前阶段判定模式是 auto 还是手动锁定""产出目录规则是什么"这类
  说明文字，规则是确定的，没有必要为了生成一段说明文字调用 LLM（零
  LLM 成本原则，与 [统一调度层指南](../docs/unified-scheduler-guide.md)
  §3"不引入新的 LLM 调用"是同一立场）。
- **`recent_cycle_summaries` 覆盖"热数据 + 冷数据"两部分**：先读
  `reaped_cycle_child_ids` 对应的活跃/最近节点，不够 N 条时再回退读
  `goal_cycle_archive.jsonl`（已归档的更早轮次），对用户呈现为一份
  连续的时间线，不暴露"热/冷数据"这种内部实现细节。
- **性能边界**：`goal_cycle_archive.jsonl` 理论上可以无限增长，报告
  只读取"最近 N 轮所需的那一段"（从文件尾部往前读，参考项目里其它
  jsonl 尾部读取的既有实现，不做全文件扫描），避免轮次一多诊断报告
  本身变慢。

### 2.2 呈现层：CLI + REST + 看板

- **CLI**：`mini-agent goals diagnose <goal_id>`（或 `/agent goals
  diagnose <goal_id>`，与现有 `_cmd_phase`/`_cmd_spec_show` 同一层级），
  格式化打印报告，复用项目已有的 `R.print_*` 渲染风格。
- **REST**：`GET /v1/goals/{goal_id}/cycle_diagnostics`，返回
  `CycleDiagnosticsReport.to_dict()`，任一子数据源缺失时对应字段返回
  空/占位（与 `scheduling_overview` 一致的降级风格），不因为某个 Goal
  从未绑定 cron 或从未触发过阶段判定就整体报错。
- **看板**：Goal 详情/看板卡片新增一个"📋 诊断报告"按钮/折叠区块，点开
  展示上述结构化内容——这是纯只读展示，不新增交互控件（交互放在能力 B）。

### 2.3 可选的 LLM 摘要层（默认关闭，类比 `rank_with_llm`）

规则层聚合出的报告本身已经是结构化数据，足够回答"整体状态如何"。但如果
用户想要一段自然语言总结（"这个 Goal 最近 5 轮进展平稳，阶段处于
stable，但 cron 触发已连续跳过 2 次，建议关注调度冲突"），可以像
`next_action_advisor.py` 的 `rank_with_llm` 一样加一层**可选**的 LLM
总结，输入就是上面的结构化报告（不额外读取原始产出内容，控制 token
成本），失败自动回退到"不生成自然语言摘要，只展示结构化字段"，不影响
报告本身的可用性。这一层留到 Stage 2 视需要再做，不是 Stage 1 的必需项。

## 3. 能力 B：交互式调优（草案 → 确认 → 应用）

### 3.1 设计原则：收窄到白名单参数，不做任意修改

这是本方案里**风险最高的部分**，必须明确边界：调优机制**只能**修改一组
预先定义好的、已有独立修改入口且已被测试覆盖的参数，不允许通过这个
机制让 Agent 去改任意代码、任意配置文件、或执行任意工具调用。白名单
（Stage 1 覆盖的范围）：

| 可调参数 | 现有修改入口（本方案复用，不重新实现） |
|---|---|
| cron 调度频率（schedule） | `make_goal_recurring()` / `stop_goal_recurrence()` |
| 优先级（priority） | `GoalBacklog.update_fields()` |
| 执行阶段（手动覆盖 mode） | `execution_phase.set_mode()` |
| 是否重新生成执行规范 | `GoalExecutionSpecBuilder.generate()` + 现有 confirm 流程 |
| task_template（cron 触发时注入的任务描述模板） | `CronJob` 字段更新（既有 `PUT /v1/cron/jobs/{id}`） |

**明确不在白名单内、本方案不覆盖**：修改 Goal 的 title/description
本体（涉及语义变化，风险不同于参数调整，留给用户直接编辑）、任何涉及
执行输出目录结构/命名规则的改动（属于系统级约定，不应该按 Goal 各自
定制）、任何超出上表范围的"自由改动"。

### 3.2 两阶段流程：draft → confirm，复用 GoalExecutionSpec 的既有模式

项目里已经有一个成熟的"草稿 → 确认"范式：`GoalExecutionSpecBuilder`
（生成 draft，`confirmed=False`；用户看过之后调用
`GoalExecutionSpecBuilder.confirm()`，`confirmed=True` 才真正生效）。
调优机制直接复用同一范式，不发明新的状态机：

```python
@dataclass
class CycleTuningProposal:
    goal_id: str
    proposed_changes: list[TuningChange]  # [{"param": "schedule", "from": ..., "to": ..., "reason": ...}]
    source: str              # "user_request" | "rule_suggested"（区分是用户直接要求的，
                              # 还是系统基于诊断报告规则推导出的建议）
    created_at: float
    status: str = "draft"    # draft -> confirmed -> applied | rejected
    confirmed_at: Optional[float] = None
    applied_at: Optional[float] = None
```

流程：

1. **生成草案**（`build_tuning_proposal()`）：输入是用户的自然语言改进
   意见（复用现有 `append_progress_note`/`feedback` 一类的用户输入通道）
   + 上面的诊断报告。规则层先尝试直接解析（比如用户明确说"改成每天一次"
   能直接映射到白名单里的 schedule 参数），解析不出结构化改动时才考虑
   LLM 辅助把自然语言意见映射到白名单参数（同样是可选、失败可回退到
   "无法自动生成草案，请使用具体命令直接修改"的降级路径，不强行让 LLM
   猜一个可能有害的改动）。
   
   **规则触发的建议**（`source="rule_suggested"`）是另一条路径：诊断
   报告本身检测到某些模式时（例如"连续 N 轮阶段一直是 explore 没有
   进展""cron 连续跳过超过阈值"），可以主动生成一份草案供用户选择是否
   采纳——这类似 P2 的 cron 跳过告警，但告警只是通知，草案是"连通知带
   一份可执行的候选修改"，用户不采纳可以直接忽略/拒绝，不会自动生效。
2. **展示 diff**：CLI/看板把 `proposed_changes` 列表渲染成"改前 → 改后"
   的对照，附上 `reason`（为什么建议这么改，来自诊断报告里的具体信号，
   不是空泛的一句话）。
3. **确认**（`confirm_tuning_proposal()`）：用户确认后 `status` 变为
   `confirmed`，**此时仍未生效**——与 `GoalExecutionSpec` 的确认语义
   一致（确认的是"这份草案本身"，不代表立即执行）。
4. **应用**（`apply_tuning_proposal()`）：真正调用上表里对应的既有
   修改入口，逐项 apply（某一项失败不影响其它项已经成功应用的部分，
   失败项在返回结果里明确标出，不静默吞掉），`status` 变为 `applied`，
   `applied_at` 记录时间，并追加一条 `progress_notes`（"根据诊断报告
   调优：schedule 从 X 改为 Y，原因：……"）留痕，与项目一贯的"关键决策
   留痕"风格一致。
5. 用户也可以直接**拒绝**（`status="rejected"`），草案作废，不产生任何
   实际改动，同样留一条 progress_notes 记录"提出过但被拒绝"（避免下次
   诊断又提出同样的建议而用户不记得已经考虑过）。

草案本身持久化在哪里：新增 `.agent/cycle_tuning_proposals/<goal_id>/
<proposal_id>.json`，与 `GoalExecutionSpec` 存放在 `.agent/
execution_specs/` 的风格一致，不塞进 `goals.json` 主文件（避免主文件
因为草稿历史膨胀）。

### 3.3 交互确认的呈现

- **CLI**：`/agent goals tune <goal_id> "<改进意见>"` 生成草案并打印
  diff；`/agent goals tune <goal_id> confirm <proposal_id>`／`reject
  <proposal_id>` 走确认流程。
- **REST**：`POST /v1/goals/{goal_id}/tuning_proposals`（生成草案）、
  `GET /v1/goals/{goal_id}/tuning_proposals`（列出历史草案，含状态）、
  `POST /v1/goals/{goal_id}/tuning_proposals/{id}/confirm`、
  `POST /v1/goals/{goal_id}/tuning_proposals/{id}/reject`。
- **看板**：诊断报告区块下方展示"待确认的调优草案"（如果有），带
  确认/拒绝按钮——这是目前项目里少数几个"看板发起写操作"的场景之一，
  参考已有的 `/self/execution_model/force_reap` 按钮（POST 触发、
  有明确的操作边界、不是自由文本输入框直接改配置）的交互模式。

## 4. 分阶段实施计划

- **Stage 1（只读诊断，零风险，优先实施）✅ 已实施**：`cycle_diagnostics.py` +
  CLI `diagnose` 命令 + REST 端点。不涉及任何改动能力，可以独立评估
  "这份报告是否真的解决了用户翻文件拼图的问题"，为 Stage 2/3 打基础。
- **Stage 2（规则触发的调优建议，不含 LLM 解析用户自然语言）✅ 已实施**：
  `CycleTuningProposal` 数据结构 + `apply/confirm/reject` 三个动作 +
  规则层从诊断报告直接生成建议（比如"连续跳过超阈值 → 建议放宽
  schedule"）。用户主动提改进意见但系统能直接结构化解析的情况（比如
  命令行直接传参数名+新值）也在这一阶段支持，不依赖 LLM。
- **Stage 3（可选）✅ 已实施**：LLM 辅助把自然语言改进意见映射到白名单
  参数（`parse_nl_request_to_changes()`）、以及诊断报告的自然语言摘要层
  （`summarize_report_with_llm()`）。两者都通过独立的配置开关默认关闭
  （`cycle_tuning.tuning_llm_parse_enabled` / `diagnostics_llm_summary_
  enabled`），关闭时 CLI/REST 行为与 Stage 1-2 完全一致；打开后失败/
  无法解析都静默回退到"提示改用结构化命令"或"不展示摘要"，不影响主
  流程可用性。

每个 Stage 完成后按项目一贯节奏更新 `docs/` 对应指南 + 跑回归测试，
不在实施过程中积压文档债务。

## 5. 明确不做的事

1. **不允许调优机制修改白名单之外的任何东西**——尤其不允许生成能直接
   编辑代码文件、执行任意 shell 命令、或修改 `output_path_policy.md`
   之类系统级约定的草案。白名单收窄是安全边界，不是临时限制，扩大
   白名单需要单独评审，不能通过"用户要求"绕过。
2. **不引入无需确认的自动应用**——即使是规则层高置信度生成的建议，也
   必须经过 Stage 2 的显式 confirm 才会 apply，不存在"诊断出问题就自动
   改配置"这种路径，这与项目对 Goal/Cron 自主性一贯的谨慎态度一致
   （参考 `growth_advisor` 系列"矛盾证据不覆盖只记录，默认关闭"的
   保守取向）。
3. **诊断报告默认不接 LLM**——规则聚合已经能回答"整体状态如何"，LLM
   摘要/自然语言解析都是可选增强层，失败要能安全回退到纯结构化展示，
   不能让诊断报告本身依赖 LLM 可用性。
4. **不在本方案内重新设计产出目录结构或阶段判定规则本身**——`mechanism_
   notes` 只是把已有规则说明清楚，不改变任何既有机制的行为。

## 6. 风险与开放问题

1. `goal_cycle_archive.jsonl` 可能已经积累相当规模（长期运行的 Goal），
   Stage 1 需要实测尾部读取的性能，必要时在文件旁维护一个轻量索引
   （类似其它模块对大文件的处理方式），避免每次诊断都要扫描整个文件。
2. Stage 2 规则触发的建议阈值（比如"连续几轮 explore 算需要提醒"）
   与 `execution_phase` 现有的健康告警阈值是否要保持一致、还是允许
   分别配置——倾向复用同一套阈值配置（避免用户在两处调同一件事的
   感知阈值），具体留待实施前确认。
3. Stage 3 的 LLM 自然语言解析层如果误判用户意图（比如把"暂停一阵子"
   误解析成"永久取消 recurring"），后果比诊断报告出错更严重——即使有
   confirm 这道闸门，也需要在草案 diff 里把"改前 → 改后"的语义写清楚
   （不能只展示字段名和原始值，要有人类可读的效果描述），降低用户在
   confirm 环节看走眼的概率。**已落地的缓解**：`parse_nl_request_to_
   changes()` 的 prompt 里显式列出白名单参数的含义和取值格式，并明确
   要求"暂停/不要再跑了"这类意图不映射为 schedule 改动而是返回空数组
   （见方案实现里的 prompt 注释）；`_print_tuning_proposal()` 打印 diff
   时仍然带上每项改动的 `reason` 字段。是否需要更进一步（比如对
   `source="user_request"` 且来自 NL 解析的草案额外加一句"请仔细核对"
   提示）已在 CLI/文档里补充，效果留待实际使用观察。
4. 是否需要给 `CycleTuningProposal` 设置有效期（比如诊断报告依据的
   数据已经过时太久后，草案要求重新生成而不能直接 confirm 生效）——
   避免用户放了很久才想起来确认一份基于旧数据生成的草案。
