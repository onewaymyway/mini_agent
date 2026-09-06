# 自我意识 / 身份演化（Agent 自身价值观 · 自我叙事 · 谱系视图）

对应设计文档：`next_doc/self_awareness_identity_evolution_plan.md`（三个阶段均已完成，
含如实标注的已知限制，详见该文档"实施阶段划分"小节）。

## 是什么

此前项目里所有"画像"类机制观察的都是**用户**（`user_value_profile.md`、
`UserProfile`）或**能力实测**（`capability_map`）。本组功能补上的是"Agent 如何
理解自己"这一层——不是能力自评（我能不能做到），而是价值观、叙事、身份连续性：
agent 自己倾向于做出什么样的选择、如何把散落的自我认知数据整合成一段连贯的
自我理解、以及"我曾经以为的自己"和"我现在实际是什么样"之间有没有落差。

> 用户侧的对称机制（Personal Model：用户是谁/看重什么/边界在哪，以及在此
> 基础上的处境快照/结构化上下文/每日简报）见
> [Personal Model / State / Context Pack / Priority Briefing 指南](personal-model-context-pack-guide.md)，
> 与本文档共享同一套证据治理范式（`evolution/evidence_pattern.py`），服务
> 对象不同。

四个子机制，均为**只读聚合 + 独立周期性归纳 job**，不改变任何决策权边界——
不引入"agent 自主做出原本由用户决定的选择"，人格切换仍然由用户触发，本组
机制只负责"认识自己"，不负责"替用户做决定"。

## 1. Agent 自身价值观（`evolution/agent_value_profile_builder.py`）

与 `user_value_profile.md`（[决策画像指南](decision-profile-guide.md)）是姊妹
机制：那边归纳的是**用户**的决策价值取向，这里归纳的是 **agent 自己**的历史
选择行为。同样的三层结构（单条事实 → LLM 归纳 → 落盘 wiki 文档），同样的
`MIN_EVIDENCE_COUNT=3` 证据门槛，同样"矛盾证据不覆盖旧模式，只记录 + 降权"。

**当前证据源**：`StateRepo`（`evolution/state_repo.py`）的 commit 历史——
每次自我修改落盘时标注的风险分级（T0-T3）直接反映"更看重稳健推进还是更愿意
承担较高风险变更"这类倾向，不需要新增采集点。

**已知限制（如实记录）**：方案原文提到的另外两个证据源——Goal/Objective
优先级选择、`soft_goal_deriver` 候选取舍记录——尚未接入。`GoalNode`/
`_DeriveCandidate` 当前不持久化候选的来源标签（capability/workthread/
lesson/...），无法从落盘的 `goal_backlog.json` 可靠反查"当初是因为哪类信号
被选中"，强行从标题/优先级反推容易引入不实归因，因此没有实现，留待
`GoalNode` 补上来源标签持久化字段后再接入。

```
/agent_value_profile           # 查看当前 Agent 自身价值观
/agent_value_profile update    # 触发一次归纳（需要 agent 提供 llm_helper，否则跳过）
```

产出：`.agent/wiki/agent_value_profile.md`。

## 2. 自我叙事（`evolution/self_narrative.py`）

综合已落盘的六路证据——`self_profile`（identity/self_assessment/
operating_state）、当前 workdir `capability_map`（最近实测 top 5）、
Agent 自身价值观（上一节，最多 5 条）、自我模型漂移信号（第 3 节，最多 5 条）、
`failure_pattern_store` 反复卡住的地方（最多 5 条）、子 Agent 经历回写
（第 4 节，最多 5 条）、谱系视图（第 5 节）——生成一段第一人称叙事，回答
"我现在如何理解自己"。任一来源读取失败会降级为该来源留空，不影响其余来源；
全部来源都没有实质内容时直接跳过，不生成空洞叙事。

**存储策略是追加式存档（类似日记，不覆盖旧版本）**：每次生成都是
`self_narrative_log.jsonl` 里新增一行，旧叙事永久保留，不会被"最新理解"
悄悄抹掉——身份认识会演变，但"我曾经怎么看自己"这段历史本身也是自我认知
的一部分。每次生成还会额外提炼一句话式的 `purpose_summary`，回写
`self_profile.identity.purpose`（这是本模块唯一直接可写的 `SelfProfile`
字段，其余字段仍分别由各自的建立者维护，不越权覆写）。

```
/self_narrative              # 查看最新一条自我叙事
/self_narrative history      # 查看最近多条叙事日志（追加式存档）
/self_narrative update       # 触发一次生成（需要 agent 提供 llm_helper，否则跳过）
```

## 3. 自我模型漂移检测（`evolution/self_model_drift.py`）

只读比较 `self_assessment.confidence_by_domain`（global scope，跨 session
累积下来的历史信念）与当前 workdir `capability_map`（最近实测）——两份数据
此前完全独立、从不互相校验。只有两边都有数据、且置信度落差 ≥ 阈值（默认
`0.3`）的领域才算"值得关注的漂移信号"，按落差绝对值降序排列。

**不做任何写入**，不自动覆盖 `confidence_by_domain`——落差只作为自我叙事
（第 2 节）的上下文信号（"我曾经以为...，但最近的实测显示..."），以及
`GET /v1/self/portrait` 的 `drift_signals` 只读字段，怎么措辞呈现交给叙事
生成的 LLM 判断，不在规则层强行下结论。

无对应 CLI 命令（作为其他机制的证据源使用，本身不产出独立文档）。

## 4. 子 Agent 经历回写（`evolution/sub_agent_experience.py`）

SubAgent（`orchestrator/sub_agent.py`）执行结束后此前只回写任务结果
（`TaskResult`），不回写"这次经历对主身份意味着什么"。本机制在 SubAgent
生命周期结束时（`_run_body()` 的 `finally` 块，与 `write_manifest()`/
`SubagentStop` hook 同一位置）做一次**纯规则式**信号检测：

- 任务失败且有非空 error → `signal_type: "failure"`
- turns/tool_calls 明显超出常规范围（默认阈值 15 轮 / 30 次工具调用）→
  `signal_type: "high_effort"`，即使最终成功也记录（过程本身值得关注）
- 都不满足 → **不写**，对齐"没有摩擦和洞察就返回空数组"的克制原则，不为
  凑数量强行生成

**不发起 LLM 调用**：这个 `finally` 块跑在 SubAgent 自己的线程上，是任务
生命周期的收尾路径，同步加一次可能阻塞/超时的语义反思调用风险较高，与
`agent/reflection.py` 里巩固循环从同步 session-end 路径迁移到 `CronScheduler`
的既有取舍一致。写入的是纯事实性摘要（task_id/task_name/signal_type/
error 片段/turns/tool_calls），真正"这次经历改变了我对自己哪方面认识"的
语义提炼交给第 2 节自我叙事的周期性 LLM 归纳去做。

产出：`.agent/sub_agent_experience_log.jsonl`（无独立 CLI 命令，仅作为
自我叙事的证据源）。

## 5. 谱系视图（`evolution/lineage_view.py`）

把 `EvolutionWorkspace`（git worktree 隔离）+ `StateRepo` 的 evolve 分支历史
重新表述为"我的谱系"：每条分支是一个"变体候选自己"，`StateRepo` 的风险分级
是"变异幅度"，merge 是"选择保留"。不改变底层机制，只做只读的语义重新组织。

- `active_variants`：当前仍存在的 `evolve/*` 分支（`StateRepo.
  list_branches(prefix="evolve/")`），含 commit 数与该分支上出现过的风险
  分级 tier 集合
- `merged_variants`：扫描 commit log 里 `"Merge evolve proposal: <branch>"`
  格式的 merge commit（`StateRepo.merge_branch()` 默认生成该 message）识别
  出的"被保留的变体"

**已知限制（如实记录，未实现）**：`discarded_variants`（被淘汰的变体）当前
**无法从 git 历史可靠还原**——`EvolutionWorkspace.destroy()` 只清理
worktree，`StateRepo.delete_branch()` 删除分支后 git 正常历史不会留存
"这个分支曾经存在过、为什么被放弃"的记录，项目也没有独立的"进化尝试结果"
日志把这类叙事所需的信息持久化下来。本模块返回空列表 + `discarded_note`
字段说明这个数据缺口，不用启发式或 LLM 编造"应该存在的"记录。若后续要补上，
需要在评审/丢弃动作发生时新增一条独立的落盘记录。

**评估后判断暂不需要实现**："多个 evolve 分支并行竞争、按 Agent 自身价值观
排序择优"——当前 `EvolutionWorkspace` 的实际使用场景（`skill_propose` 走
evolve 分支）仍是串行的一次一个候选，没有出现需要并行择优的实际信号，不
预先实现用不上的调度接口。

无对应 CLI 命令（作为其他机制的证据源使用）。

## 相关配置（`agent_config.json` → `digest_advisor`）

| 字段 | 默认值 | 说明 |
|---|---|---|
| `agent_value_profile_enabled` | `false` | 控制 `sys:agent_value_profile_update` cron job 的初始 enabled 状态 |
| `agent_value_profile_min_evidence_count` | `3` | 归纳一条模式所需的最少独立 commit 记录数 |
| `self_narrative_enabled` | `false` | 控制 `sys:self_narrative_update` cron job 的初始 enabled 状态 |

与 `decision_profile_enabled` 同一保守默认（opt-in）：建议先让其他画像/推荐
机制稳定运行数周、积累足够数据后，再手动 `/cron enable sys:agent_value_
profile_update` / `/cron enable sys:self_narrative_update` 开启周期性归纳。
手动执行 `/agent_value_profile update` / `/self_narrative update` 不受这两个
开关影响，随时可用。

## `GET /v1/self/portrait` 新增字段

本组功能都接入了既有的只读聚合端点（详见
[kanban-dashboard-guide.md](kanban-dashboard-guide.md)"🪞 自我画像 / 能力
地图"区块），新增：

- `agent_value_profile` — 第 1 节归纳出的模式列表
- `body_inventory` — "身体清单"：把 LLM provider 池状态、技能目录计数、
  browser_core/子 Agent 编排是否可用这几类已存在的行动接口重新组织为一份
  "我现在有哪些身体"的视图，纯展示层面重组，未新增采集逻辑
- `self_narrative` — 第 2 节最新一条叙事（完整历史用 `/self_narrative
  history` 命令查看）
- `drift_signals` — 第 3 节的漂移信号列表
- `lineage` — 第 5 节的谱系视图

**当前限制**：以上新增字段目前只在 HTTP API 层暴露，Kanban 看板的"🪞 自我
画像 / 能力地图"区块尚未渲染这几个新字段（仍是本文档改动之前的展示范围），
需要时可作为独立的看板改进项跟进。

## 明确不做的事

- 不引入"agent 自主做出原本由用户决定的选择"：人格切换维持用户触发，谱系
  里的"淘汰"是既有 eval 反馈机制的结果，不是新增的自主裁决权
- 自我模型漂移检测只生成信号，不自动覆盖 `confidence_by_domain`
- 不为了"看起来完整"编造证据不足的模式/叙事/谱系记录——这是
  `decision_profile_builder.py` 已经确立的一贯克制原则，本组功能全程延续
