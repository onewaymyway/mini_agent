# Goal/Cron 文档体系梳理、重组建议与系统现状回顾

> 本文档是一次周期性盘点的产出，覆盖三件事：①核对 goal/cron 相关全部
> 文档与代码实现的一致性（发现并修正的问题见 §1，详细核对记录见
> `goal_cron_docs_status_audit_record.md` 第二轮）；②给出 `docs/` +
> `next_doc/` 两层文档体系的分组/重组建议，目标是"逻辑更清晰、能看出
> 历史演进轨迹"（§2）；③基于本轮通读，总结 goal/cron 子系统当前的能力
> 现状和后续可能的改进方向（§3）。

## 1. 本轮一致性核对：发现与修正

核对范围：`docs/` 下 11 篇 goal/cron 相关用户指南 + `next_doc/` 下 29 篇
方案/记录文档（`next_doc/` 部分复用了此前 `goal_cron_convergence_and_
governance_improvement_plan.md` Track 2 的核对成果，本轮只补做了 `docs/`
部分 + 针对"清单型"文档的代码逐条抽查）。

**发现 1 处真实不一致**（另有历次已修正的 1 处记录在案，见 §0 背景）：

- `docs/cron-jobs-reference.md` 自称汇总"全部 `sys:` 前缀内置 cron job"，
  但 `evolution/cron_scheduler.py::_BUILTIN_JOBS` 已经从文档记载的 9 个
  增长到 16 个，文档遗漏了 4 个后续新增的 job（`sys:wiki_quarantine_
  repair`、`sys:growth_advisor_daily`、`sys:growth_monthly_retrospective`、
  `sys:memory_backfill_scan`）。这 4 个 job 各自归属的设计方案文档
  （wiki 隔离修复、成长顾问、记忆回填）本身状态栏是准确的，问题出在
  "新增 job 时要同步这份汇总索引"这一步被跳过了——**且跳过了不止一次**
  （4 个 job 分属 3 个不同的历史改动，说明这不是一次疏忽，而是这类
  "旁支汇总文档"结构性容易被遗忘）。
- 同样的简表被复制到了 `docs/commands-and-tools-reference.md` 和
  `docs/self-evolution-stage9-guide.md` 里（历史上从 `cron-jobs-
  reference.md` 摘出去的简化版），两处也一并漏更新。

**已修正**：三处文档的数字（9→16）和清单均已补齐，`cron-jobs-reference.md`
§6 维护说明里新增了"同步检查这两处副本"的提醒。详细的核对方法、每一条
结论、根因分析见 `goal_cron_docs_status_audit_record.md`"第二轮核对"
小节，此处不重复。

**其余 39 篇文档核对一致**，包括本轮重点抽查的配置项/函数名存在性（如
`CyclePatrolConfig.dedupe_cron_skip_alert`、`priority_score`、
`GoalExecutionSpec` 各阶段字段等）——goal/cron 这条主线的文档维护质量
总体仍然是好的，两轮加起来在近 60 篇文档里只发现 2 处不一致，且都是
"汇总型/索引型"文档的同一类问题（新增内容后，源头方案文档更新了，但
汇总索引没跟着更新），不是内容本身写错。这个模式本身就是 §2 重组建议
的重要输入。

## 2. 文档重组建议

### 2.1 现状问题，不是"内容质量"而是"检索路径"

通读下来，goal/cron 相关的文档在**内容准确性**上没有系统性问题（§1 的
2 处不一致都局限在汇总索引，核心方案文档本身可靠）。真正的问题是**规模
增长后检索路径变差**：

- `next_doc/` 现有 126 篇文档，`docs/` 现有 103 篇，两个目录都**没有
  索引/README**，新读者（包括未来的 Claude 会话）只能靠文件名猜测内容，
  或者从某一篇文档的"前置阅读"链接顺藤摸瓜。
- goal/cron 这一条主线内部，`next_doc/` 里至少有 4 种不同性质的文档
  混在一起、没有区分：
  1. **仍在讨论/设计阶段**的方案（如 `growth_advisor_goal_cron_
     integration_plan.md`——"是否要打通"还没定论）
  2. **已完整实施**的方案（多数文档）
  3. **纯粹的实施记录**（`*_implementation_record.md`，跟对应的
     `*_plan.md` 是一对，但文件名排序上两者经常不相邻）
  4. **Bug 修复记录**（`*_bugfix.md`，跟功能方案文档目的完全不同，
     但混在同一个目录、同样的命名风格里）
- `docs/` 里的用户指南普遍在开头用"对应设计文档：`next_doc/xxx.md`"
  做了单向链接（指南→方案），但反向索引（"这个方案文档对应哪篇用户
  指南"）不存在，要靠全文搜索。

### 2.2 建议：不做大规模迁移，只加"索引层"

**不建议**把现有 229 篇文档重新分文件夹、重命名——那样的一次性大改动
风险高（大量互相引用的相对路径链接需要同步改，参考 `goal_cron_output_
directory_convention_plan.md` 这类"目录规范"类文档过去处理迁移问题时
的谨慎程度），收益也未必对得上成本。更合适的做法是**在现有平铺结构上
加一层索引**，成本低、可以渐进式补全、不破坏任何现有链接：

1. **`next_doc/README.md`（新增）**——按"能力主线"而不是按文件名字母
   序组织的索引，goal/cron 这条主线示例结构：

   ```markdown
   ## Goal/Cron 主线

   ### 时间线（早→晚，体现演进轨迹）
   1. goal_cron_binding_plan.md — Goal 与 Cron 绑定基础机制
   2. goal_execution_phase_improvement_plan.md — 执行阶段状态机
   3. goal_execution_spec_generation_plan.md — 执行规范生成
   4. goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md — 诊断 + 调优（拉模式）
   5. goal_cron_cycle_proactive_patrol_and_health_overview_plan.md — 主动巡检 + 总览（推模式，依赖 4）
   6. goal_cron_task_optimization_holistic_plan.md — 综合优化（依赖 2/4/5 的信号）

   ### 配对的实施记录
   - goal_cron_binding_plan.md ↔ goal_cron_binding_implementation_record.md
   - goal_execution_spec_generation_plan.md ↔ goal_execution_spec_generation_implementation_record.md
   - ...

   ### Bug 修复（独立于功能演进主线）
   - goal_execution_scheduling_global_cap_bugfix.md
   - kanban_cron_delete_consistency_bugfix.md

   ### 尚未定论/讨论中
   - growth_advisor_goal_cron_integration_plan.md
   ```

   这份索引本身就是"历史改进轨迹"的直接呈现——按时间线排列 + 标注
   依赖关系，比翻文件名字母序更能看出"系统是怎么一步步长成现在这样的"。

2. **`docs/README.md`（新增）**——按"用户想做什么"而不是按功能模块
   组织，比如"我想看某个周期性 Goal 跑得怎么样" → 依次链接诊断报告
   指南→调优指南→巡检与总览指南，形成一条阅读路径，而不是三篇平级
   罗列。

3. **每篇 `next_doc/` 方案文档的状态栏，统一补一行"用户指南"反向链接**
   （多数已经有，个别缺失的补上），让"方案→指南"和"指南→方案"两个
   方向都能一步跳转，不需要全文搜索。

4. **维持现有文件命名约定**（`<主题>_plan.md` / `_implementation_
   record.md` / `_bugfix.md`），不引入新的分类前缀——命名约定本身已经
   传达了文档性质，问题只是缺一层聚合视图，不是命名本身有问题。

### 2.3 索引层如何防止自己也腐化

`docs/cron-jobs-reference.md` 这次的教训（§1）本质是"汇总文档没有和
它汇总的内容绑在同一次改动里更新"。给索引层加两条防腐化约定：

- 新建 `next_doc/*_plan.md` 时，在同一次改动里给 `next_doc/README.md`
  加一行——就像现在要求"新增 `sys:` job 要同步 `cron-jobs-reference.md`"
  一样的纪律，只是把范围从"某一类汇总文档"扩大到"文档索引本身"。
- 索引本身**不复制内容**，只做"标题 + 一句话定位 + 链接"，避免出现
  第二份"简化版清单"（`commands-and-tools-reference.md` 那种复制品）
  ——这是本轮发现的腐化模式的根因，索引层设计时要主动避开。

### 2.4 是否需要工具化核对

`goal_cron_docs_status_audit_record.md` 首轮结论是"文档维护质量总体好，
不建议投入自动化核对工具"。本轮（第二轮）新发现的问题类型比较specific
——"汇总型清单文档遗漏新增条目"，这类问题理论上可以写一个简单脚本
（提取文档里的 `sys:*`/函数名/配置项 token，跟代码 grep 结果 diff，
本轮就是手动做的这件事）在 CI 里跑，但目前只发现这一类、一处实例，
**仍然不建议**为此新增强制流程；更符合项目现有风格的做法是把这次的
核对方法本身记录下来（已经在 `goal_cron_docs_status_audit_record.md`
里），下次人工抽查时可以直接复用这个方法，而不是每次重新摸索。

## 3. Goal/Cron 子系统现状与后续方向

### 3.1 现状：能力已经形成完整闭环

把 goal/cron 相关文档按能力串起来看，当前系统已经形成一个从"发现问题→
理解问题→采取行动→验证效果"的完整闭环，不再是孤立的功能点：

```
Goal 产生（自动发现/用户创建）
  → goal-provenance-guide / goal-cron-binding-guide
Goal 绑定 Cron 周期性触发
  → goal-cron-binding-guide
执行阶段状态机（探索→收敛→稳定→整理）
  → goal-execution-phase-guide
执行规范生成（怎么执行、产出到哪）
  → goal-execution-spec-guide
执行公平性调度（多 Goal 并发时的资源分配）
  → goal-execution-fairness-config
【拉模式】单 Goal 诊断 + 交互式调优
  → goal-cycle-diagnostics-guide / goal-cycle-tuning-guide
【推模式】主动巡检 + 推送 + 全局健康总览
  → goal-cycle-patrol-guide
```

从"能力 C/D"（主动巡检 + 总览）这份最新的方案文档状态看，Stage 1-3
已经全部落地，§6 明确标注为"仍然开放、不阻塞"的只剩两项，且都是需要
**真实使用数据**才能决策的问题（不是"还没来得及做"）：

1. 多 Goal 合并降噪时 LLM 排优先级的效果，需要观察一段时间真实巡检
   数据后再决定是收窄成纯规则排序，还是保留 LLM 判断。
2. 多用户模式下巡检推送目标（owner vs 订阅者），需要有明确的多用户
   使用场景后再设计，目前单用户/默认广播够用。

这两项本质上都是"设计上已经预留了退路（LLM 失败可退化为规则排序/规则
拼接文案、多用户场景目前退化为现有单播广播），缺的是观察窗口，不是
缺技术方案"——不建议在没有真实数据前强行收敛。

### 3.2 值得关注的结构性观察（非 bug，是后续可以考虑的方向）

1. **"汇总型文档"这一类结构性弱点**——不只是 `cron-jobs-reference.md`
   一篇，本方案 §2 已经提出用索引层缓解，但如果未来 goal/cron 主线上
   出现更多"跨多个方案的汇总视图"（现有例子：cron job 清单、`goal_
   cron_task_optimization_holistic_plan.md` 这种"综合优化"类文档本身
   也带有汇总性质），值得在新建这类文档时就明确标注"本文档是汇总型，
   新增上游能力时需要同步"，而不是事后靠核对发现。

2. **能力 C/D 与更早期"能力 A/B"（诊断/调优）文档在语言上称"拉模式/
   推模式"，这个命名在后续如果还有新能力接入（比如未来可能的"能力 E"）
   时，建议延续这套"拉/推"或类似的模式化命名，方便新读者快速定位一个
   新方案属于哪一类交互模式，是当前文档体系里少有的、已经自然形成的
   分类维度，值得在 §2 的索引层里显式保留（"拉模式"/"推模式"可以作为
   `next_doc/README.md` goal/cron 分组下的二级标签）。

3. **§6 遗留的"多用户推送目标"开放问题**，如果 `multi_user_enabled`
   场景后续有实际用户在用，这条会从"低优先级的开放问题"变成"必须回答
   的设计缺口"——值得在多用户相关的其它方案文档（如果存在）动工时，
   回头检查一下这条是否需要一并解决，避免多个文档各自独立地重新发明
   一套"这个功能在多用户模式下该推给谁"的处理方式。

4. **诊断/调优/巡检三层能力目前的"零 LLM 成本"边界线一致**（§5 第 2 条
   明确"不新造健康判定标准"），这是一个刻意维持的好习惯，建议在后续
   任何新增 goal/cron 健康信号判定逻辑时都延续——保持三层用同一套
   `recent_health_alerts`/`cron_health`/execution_phase 规则，不要因为
   某一层新增需求就单独造一套新标准，避免出现"总览是黄色，但诊断报告
   是绿色"这种两套标准打架的情况（方案文档里已经明确写了这条边界，
   这里只是重申其重要性，供后续改动时对照）。

### 3.3 简要建议：如果要继续往前推进，优先级排序

结合 §1 的发现和 §3.1/3.2 的现状分析，如果接下来要在 goal/cron 这条
主线继续投入，建议的优先级（仅供参考，非强制）：

1. **最低成本、立即见效**：落地 §2 的索引层（`next_doc/README.md` +
   `docs/README.md`），一次性投入，长期降低检索成本，且不影响任何
   现有功能代码。
2. **等待真实数据再决策**：`goal_cron_cycle_proactive_patrol_and_
   health_overview_plan.md` §6 剩余两条开放问题，建议先运行一段时间
   收集数据，不建议现在就动代码。
3. **按需触发**：多用户推送目标设计，只在真的出现多用户部署诉求时
   再启动，不建议提前设计一套没有真实约束条件的方案（容易设计过度或
   设计不准）。
