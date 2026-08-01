# 自诊断信号闭环深化 改进计划

- **版本**: v1.1
- **变更记录**:
  - v1.0：初版，规划 P1-P4，均未实现。
  - v1.1：P1（`sys:improvement_backlog_merge`）已实现，见该节内的"实现记录"标注。
- **背景任务**: 代码复核确认，当前自诊断/自我感知类基础设施（`perception/self_model.py`
  的 `AgentSelfModel`、`evolution/self_maintenance.py` 的健康巡检、`wiki/gap_scanner.py`
  的知识缺口扫描、`wiki/decommission.py` 的退役评估、`external_knowledge_feedback_loop_
  improvement_plan.md` P1-P5 的外部知识闭环）**信号采集层已经相当完备**，但存在两类空隙：
  (a) 各信号源互相独立产报告，没有统一排序的改进候选清单；(b) "提了建议"和"建议是否真的让
  情况变好"之间没有回看机制，即"改进"本身缺乏验证信号。本计划针对这两类空隙做补齐，不新增
  数据采集基础设施，只在现有信号上叠加一层聚合与回看。
- **设计边界（沿用既有原则，明确写出以防后续误解）**：
  1. 本计划**不涉及让 agent 自主生成新目标或自主决定探索方向**——所有信号仍然是"发现问题/
     缺口 → 生成人工可审阅的建议 → 人决定是否采纳"，不改变现有"保留人类控制权"的巡检哲学。
  2. 所有新增 cron job 默认零 LLM 成本优先，走"低频批量巡检"节奏。
  3. 复用现有存储格式（jsonl/state 文件 + `ExclusiveFileLock`），不新建独立的候选/知识存储体系。
  4. 新 job 采用"缺失才补"注册模式（`ensure_*_job`），不破坏用户已手动调整的 schedule/enabled。
- **关联文档**:
  - `next_doc/embodied_agent_improvement_plan_v3.md`（`AgentSelfModel`/`self_maintenance` 的前置设计）
  - `next_doc/external_knowledge_feedback_loop_improvement_plan.md`（P1-P5，本计划是同一"巡检-反馈-
    校准"思路在自诊断领域的延伸）
  - `docs/memory-and-self-evolution-complete-reference.md`

---

## 1. 现状复盘：两类空隙

1. **信号分散、无统一排序**：`self_maintenance.health_check()` 产出工具/技能/lesson 三类健康
   报告，`gap_scanner.scan_gaps()` 产出知识缺口，`wiki_utility_audit` 产出低效页面，
   `self_model.py` 产出能力弱点——四路信号分别写入各自的报告文件/晨报条目，用户需要自己在
   四个地方来回看、自己排优先级，没有一个"这周最值得先处理什么"的汇总视图。
2. **只提建议、不回看效果**：`self_maintenance` 和 `decommission` 明确是"报告-建议"模式，
   但没有机制追踪"N 周前提的某条建议，用户是否采纳、采纳后对应指标是否真的改善"。这导致
   两个问题：(a) 无法验证巡检本身的建议质量是不是在变好或变差；(b) `AgentSelfModel` 的能力
   快照目前是单次读取式的，没有时间序列，无法回答"这次改动后能力弱点清单是变短了还是变长了"。

## 2. 分阶段实施计划

### P1 —— `sys:improvement_backlog_merge`（改进信号聚合器）✅ 已实现

> 实现记录：新增 `src/mini_agent/evolution/improvement_backlog_merge.py`
> （`run_improvement_backlog_merge_once()` + `ensure_improvement_backlog_merge_job()`），
> 在 `api/server.py` daemon 启动流程里注册 `sys:improvement_backlog_merge` job
> （`interval:86400`，零 LLM 成本，本地回调 handler，跟 `candidate_queue_triage.py`/
> `wiki_utility_audit.py` 同构）。四路信号读取方式：`self_maintenance` 只读最近一条
> `activity_digest.jsonl` 里 `type="health_report"` 记录（不重新触发健康检查，因为
> 本 job 不持有 `skill_loader`/`memory_backend` 等运行时对象）；`gap_scanner`/
> `self_model` 现算（纯只读、无 LLM/网络调用，成本低，没有可读的"上次结果"文件）；
> `decommission` 只读 `load_last_report()`。打分规则：新鲜度（14 天窗口线性衰减）+
> 跨信号源命中加分（每多一路信号源 +2 分）+ 长期滞留提权（同一 subject 连续 21 天
> 未被信号源移除，+1 分）。结果落盘 `AgentPaths.improvement_backlog_path`
> （`.agent/improvement_backlog.json`），并写入 `activity_digest.jsonl`
> type=`improvement_backlog`（Top 5）供晨报展示。新增 `tests/test_improvement_
>_backlog_merge.py`（6 个用例，覆盖空输入、单信号源解析、跨源加分、单信号源异常
> 隔离性、状态落盘可读回、cron job 幂等注册），全部通过；同时对 `test_self_model.py`/
> `test_wiki_utility_audit.py`/`test_candidate_queue_triage.py` 做了回归测试，无破坏。
> 待讨论问题 1（打分权重系数）仍未精调，先用保守的固定系数上线，留待积累真实分布数据
> 后再调整。

- **目标**：把 self_maintenance 健康报告、gap_scanner 知识缺口、wiki_utility_audit 低效页面、
  self_model 能力弱点四路信号，按统一 schema 汇总成一份排序过的 `improvement_backlog.jsonl`。
- **排序维度**（规则打分，不引入 LLM）：信号新鲜度（越新越靠前）、跨信号源重复出现次数
  （同一工具/技能/主题同时被多路信号提及，优先级上浮）、距上次被处理/采纳的时间。
- **不做**：不自动执行任何改进动作，只产出排序列表；不合并信号本身的判断逻辑，各信号源
  仍独立产生原始报告，本 job 只做"读取四份已有报告 + 排序 + 写汇总"。
- **触发**：与 `self_maintenance` 同款"时间门控"模式，日频批量，daemon 模式下注册为 cron
  `sys:improvement_backlog_merge`（interval:86400），晨报新增一个"本周最值得关注的 N 项"摘要块。

### P2 —— `sys:suggestion_outcome_review`（建议采纳率回看）

- **目标**：回溯 2-4 周前 `self_maintenance`/`decommission` 产出的建议，检查对应的工具调用
  失败率/技能使用情况/wiki 页面状态是否已经改善，产出一份"建议有效性回顾"。
- **判定方式**：复用 `self_maintenance` 已有的信号采集逻辑（traces.jsonl 失败率、skill
  tracker、wiki usage_stats），对比"建议提出时"和"回看时"两个时间点的同一指标，标记
  `improved` / `unchanged` / `worse` / `no_action_taken`（无法判断是否采纳还是采纳了没生效，
  这一区分本身也作为输出的一部分，不强行下结论）。
- **不做**：不据此自动调整任何阈值或策略，纯报告，供人工判断"这类建议是否值得继续产出"。
- **触发**：低频，注册为 cron `sys:suggestion_outcome_review`（interval: 每 2 周一次）。

### P3 —— 能力自画像时间序列快照

- **目标**：`self_model.py` 的能力弱点快照目前是即时计算、不落盘的读取式接口。新增按周期
  （建议与 P1 同频，日频或周频）写入带时间戳的快照到 `self_model_history.jsonl`，并提供一个
  轻量 diff 函数，比较任意两个快照之间弱点清单的增减。
- **用途**：为 P2 的"建议有效性回顾"提供能力层面的佐证数据；长期积累后可用于回答"过去一个月
  能力弱点整体是在收敛还是在扩散"，作为月度回顾（已有的 `sys:monthly_trend_retrospective`）
  的补充数据源，而不是新建一个独立的回顾入口。
- **不做**：不引入预测/趋势外推，只做历史快照存档与两两 diff。

### P4 —— skill 结果有效性审计（`wiki_utility_audit` 的姊妹版）

- **目标**：现有 `self_maintenance` 对技能的巡检是"多久没用→建议复核"的新鲜度启发式；本项
  补充"用了之后任务是否因此成功率提升"的结果信号，与 P2 类似地做前后对比，而不是替换现有
  新鲜度信号——两者并存，分别标注在同一份技能健康报告里，供人工综合判断。
- **数据来源**：复用 `SessionStats`/`traces.jsonl` 里已有的 phase="tool_call"/skill 激活记录，
  不新增埋点基础设施（与 `self_maintenance.py` 文档里"复用已有数据源"的取舍原则一致）。

## 3. 明确不做的事（写清楚边界，避免后续误解为"自主目标"雏形）

- 不做"agent 自主生成终极目标"或"自主决定探索方向"的任何形式；本计划所有输出都是给人看的
  报告/排序列表，执行与否、采纳与否始终是人工决定。
- 不做自动阈值调整、自动技能剪枝、自动知识退役——这些执行动作已有的人工确认环节（如
  `decommission.check_and_plan`）保持不变，本计划不改变执行侧，只加强"发现问题"和"回看效果"
  两侧的信号质量。
- P2/P4 的"有效性回顾"结论仅作为报告呈现，不反馈进任何自动化的策略调整逻辑——这一点留待
  有了足够回顾数据、且经过明确讨论之后再决定是否要做下一步。

## 4. 待讨论问题（留空，实施前需要确认）

1. P1 的排序打分权重（新鲜度 vs 跨信号重复次数 vs 距上次处理时间）具体系数，建议先跑一段
   时间收集实际分布再定，而不是一开始就精调。
2. P2 的"建议提出时"指标快照目前依赖能否从历史报告里反查到当时的原始数值——需要先确认
   `self_maintenance`/`decommission` 现有报告文件里是否已经记录了足够的原始指标值，若没有
   可能需要给这两个模块的报告 schema 补一个字段，这属于本计划实施前的一个小前置调研项。
3. P3 快照落盘频率与 P1 是否共用同一触发周期，还是各自独立配置——待实施时再定。
