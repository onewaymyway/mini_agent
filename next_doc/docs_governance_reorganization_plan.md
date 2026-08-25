# 文档体系治理与重组计划

> 本文档是对 `docs/`（105 篇）、`next_doc/`（150 篇）、`release_logs/`
> （8 篇）当前状态的一次全面梳理，给出规范落地后的具体整理步骤。规范
> 本身（三个目录各自的定位、命名规则、维护要求）已经沉淀为
> [docs/documentation-guidelines.md](../docs/documentation-guidelines.md)，
> 本文档不重复规则内容，只规划"如何把现状调整到符合规范"的执行步骤。
>
> 沿用项目里已有的两次同类盘点（`goal_cron_docs_reorganization_and_
> system_state_review.md`、`growth_advisor_docs_reorganization_and_
> system_state_review.md`）验证过的结论：**不做大规模迁移**（229 篇
> 文档之间存在大量相对路径互相引用，一次性重命名/搬移风险高、收益
> 未必对得上成本），优先加索引层 + 修复已发现的具体问题，命名规范
> 只对新增文档强制生效，存量文档视情况渐进修正。

## 0. 背景：本次盘点发现的问题清单

1. `next_doc/` 内同一子系统的文档分散、缺少索引：`growth_advisor_*` 前缀 19 篇、`goal_*`/`goal_cron_*` 前缀约 25 篇，均无索引说明阅读顺序与文档间关系。
2. `next_doc/`、`docs/` 均无 `README.md` 索引文件。
3. 汇总型文档漏同步：`docs/cron-jobs-reference.md` 记载的内置 cron job 数量（9 个）落后于代码实际数量（16 个），且同一份简表被复制到 `docs/commands-and-tools-reference.md`、`docs/self-evolution-stage9-guide.md`，三处均未同步更新。
4. `docs/cron-dedicated-execution-guide.md` 第 6 行存在错误链接：链接文字写"具身智能改进指南"，实际指向 `autonomous-daemon-design.md`（自主 Daemon 设计），与链接文字不符。
5. `docs/project-readme.md`（248 行）与根目录 `README.md` 内容高度重复，权威版本不明确。
6. `docs/tool_call_format_correction.md` 与 `docs/format-correction-detector-update.md` 是"正文 + 补充说明"关系，违反 `docs/` 不应拆分为补丁文档的规则，应合并。
7. 命名风格不一致：`docs/autonomous-daemon-design.md`、`docs/tool_call_format_correction.md` 使用 snake_case，混入以 kebab-case 为主的 `docs/`。
8. 中文文件名：`docs/` 1 篇（`mini-agent-philosophy-and-roadmap.md`）、`next_doc/` 14 篇，存在跨平台打包/传输时的编码乱码风险。
9. 部分单篇 `docs/` 文档体量失衡：`growth-advisor-guide.md`（1932 行）、`workflow-guide.md`（2323 行），已有可参考的拆分方案（见 `next_doc/growth_advisor_docs_reorganization_and_system_state_review.md` §2.2：拆成"稳定核心骨架"+"按时间线组织的演进日志"两篇）。

## 1. 执行步骤

按风险从低到高排序，建议按顺序推进，每步之间可以独立验收，不互相阻塞。

### 步骤一：新增两份索引文件（零风险，纯增量，不改动任何现有文件）

- 新增 `next_doc/README.md`：按"能力主线"（而非文件名字母序）分组，每条主线内部按时间线列出该主线全部文档，并标注每篇的性质（`_design`/`_plan`/`_implementation_record`/`_bugfix`）。优先覆盖文档数量较多的主线：`goal_cron_*`（约 25 篇）、`growth_advisor_*`（19 篇）、`wiki` 相关（约 12 篇）、`daemon` 相关（约 9 篇）、`embodied_agent_*`（3 篇，注意标注 v2/v3 的迭代关系）、`external_input_*`（约 5 篇）、`generative_capability_*`（约 4 篇）。其余零散文档可以先归入"其他"分组，后续按需细化。
- 新增 `docs/README.md`：给 `docs/` 里超过 5 篇同前缀的主题群加导读，明确列出：`goal-*`（10 篇）、`self-evolution-stage*` + `self-evolution-consolidation-guide.md` + `self-evolution-outcome-tracking-guide.md`（7 篇）、`daemon-*`（4 篇）、wiki/记忆相关（`memory-management-guide.md`/`library-index-guide.md`/`wiki-knowledge-base-guide.md`/`memory-and-self-evolution-complete-reference.md`，4 篇，说明后者是入口文档）。

验收标准：两份 README 能让一个第一次接触项目的读者，在不打开任何具体文档的情况下，说出"我想了解 XX 子系统应该按什么顺序读哪几篇"。

### 步骤二：修复已发现的具体错误（低风险，改动范围明确）

1. 核对 `evolution/cron_scheduler.py::_BUILTIN_JOBS` 实际数量与内容，同步更新 `docs/cron-jobs-reference.md`、`docs/commands-and-tools-reference.md`、`docs/self-evolution-stage9-guide.md` 三处的 cron job 清单，并在 `cron-jobs-reference.md` 维护说明里注明这三处是同步副本，以后变更需一并检查（对齐 `documentation-guidelines.md` §2.3 的要求）。
2. 修正 `docs/cron-dedicated-execution-guide.md` 第 6 行的错误链接，改为指向正确的 `embodied-agent-guide.md`（如果原意确实是想链接具身智能相关内容），或修正链接文字使其与 `autonomous-daemon-design.md` 相符（需先确认作者原意）。

验收标准：`grep` 全部 `docs/*.md` 中的相对链接，逐条确认目标文件存在且链接文字与目标内容相符。

### 步骤三：合并明确重复的 `docs/` 文档（中等风险，需要处理反向引用）

1. `docs/project-readme.md` 与根 `README.md` 合并：核对 `project-readme.md` 里是否有 `README.md` 当前没有覆盖的内容（如更细的分层职责说明），有价值的部分并入 `README.md` 或迁移到 `docs/code-structure-guide.md`/`docs/agent-design.md`，随后删除 `project-readme.md`，并检查是否有其他文档链接到它。
2. `docs/format-correction-detector-update.md` 合并进 `docs/tool_call_format_correction.md`，合并时按 `documentation-guidelines.md` 的要求将文件一并改名为 kebab-case：`docs/tool-call-format-correction.md`。需要检查现有链接（至少 `docs/tool_call_format_correction.md` 自身已知有反向链接）并同步更新。

验收标准：`grep -r "project-readme\|tool_call_format_correction\|format-correction-detector-update" docs/ next_doc/ README.md` 确认无残留失效链接。

### 步骤四：命名规范修正（低风险，仅涉及 2 篇文件 + 反向链接更新）

将 `docs/autonomous-daemon-design.md` 重命名为 `docs/autonomous-daemon-design.md`（步骤三合并后 `tool_call_format_correction.md` 已处理，此处仅剩这一篇）。重命名后同步更新所有反向引用（已知至少 `docs/cron-dedicated-execution-guide.md` 引用了这篇）。

### 步骤五：中文文件名评估（低风险，可选，建议单独排期）

评估将 `docs/mini-agent-philosophy-and-roadmap.md` 及 `next_doc/` 下 14 篇中文文件名文档改为英文 kebab-case/snake_case 文件名（标题正文保持中文不变）。由于反向引用较多（`mini-agent-philosophy-and-roadmap.md` 是 README 首篇必读链接），建议单独排期，改名后需要全项目搜索确认无遗漏引用。

### 步骤六（可选，视精力排期）：拆分体量失衡的单篇文档

对 `docs/growth-advisor-guide.md`、`docs/workflow-guide.md` 等明显超长的文档，参照已有方案（`next_doc/growth_advisor_docs_reorganization_and_system_state_review.md` §2.2）拆分为"稳定核心骨架"+"按时间线组织的演进日志"两篇。这一步优先级最低，且需要对每篇文档单独评估拆分方式，不纳入本轮统一执行范围，作为后续独立计划处理。

## 2. 后续维护

步骤一、二完成后，`documentation-guidelines.md` 里的规则（同前缀文档超过 3 篇需同步更新 `next_doc/README.md`、同一清单有多处副本需一并更新等）即可正式作为新增文档的强制要求生效。建议在下一次涉及 goal/cron 或 growth_advisor 主线的改动时，作为规则落地后的第一次实际检验。

## 3. 执行进度（本轮）

- ✅ **步骤一**：新增 `next_doc/README.md`（按能力主线分组，覆盖 goal_cron/goal/growth_advisor/wiki/daemon/embodied_agent/external_input/generative_capability/kanban/workflow 十条主线 + "其他"零散分组）与 `docs/README.md`（goal-*/self-evolution-*/daemon-*/wiki 记忆相关四组导读 + 汇总型文档同步提醒）。根 `README.md` 文档索引章节已补上指向这两份索引的入口。
- ✅ **步骤二**：核对 `evolution/cron_scheduler.py::_BUILTIN_JOBS` 实际为 **18 个**（不是 16 个，更不是最初发现问题时的 9 个），补齐 `sys:wiki_gap_scan`/`sys:wiki_fallback_cleanup`/`sys:capability_learning_cycle`/`sys:capability_question_sweep` 等遗漏条目，同步更新了 `docs/cron-jobs-reference.md`、`docs/commands-and-tools-reference.md`、`docs/self-evolution-stage9-guide.md` 三处清单（含 §4 按 LLM 成本分类速查表）。修正了 `docs/cron-dedicated-execution-guide.md` 的错误链接文字。
- ✅ **步骤三**：
  - `docs/format-correction-detector-update.md` 已合并进 `docs/tool-call-format-correction.md`（原 `docs/tool_call_format_correction.md`，合并时一并按规范改名）；反向引用（`docs/reminder-system-guide.md`）已更新。
  - `docs/project-readme.md` 内容已确认被根 `README.md`（项目结构/快速开始/扩展开发）与 `docs/code-structure-guide.md`（各层职责说明）完整覆盖且更新更及时，已删除；核对无其他文档反向引用。
- ✅ **步骤四**：`docs/autonomous_daemon_design.md` 重命名为 `docs/autonomous-daemon-design.md`，更新了全部反向引用（`docs/daemon-autonomous-state-recovery-guide.md`、`docs/execution-mechanisms-overview.md`、`docs/cron-dedicated-execution-guide.md`、`docs/kanban-dashboard-guide.md`、`release_logs/v0.9.3.md`、`release_logs/v0.8.1.md`）。注意：`next_doc/autonomous_daemon_design.md` 是另一篇内容不同的设计文档（"设计方案" vs "docs/ 里的实现说明"关系），未改名。
- ✅ **步骤五**（中文文件名评估→执行）：原计划标注"建议单独排期、仅评估"，实际执行时一并完成了改名。`next_doc/` 下 15 篇（原文档 §0 第 8 条记录为 14 篇，实测多 1 篇）+ `docs/mini_agent_核心理念与长期规划.md` 共 16 篇中文文件名文档，已全部改为英文 kebab-case 文件名（标题正文保持中文不变），全项目反向引用（含 `README.md`"必读"链接、`CLAUDE.md`、`docs/wiki-knowledge-base-guide.md`、`docs/kanban-dashboard-guide.md`、`docs/daily-digest-guide.md`、`docs/next-action-advisor-guide.md`、`docs/decision-profile-guide.md`、`release_logs/v0.9.3.md`、`release_logs/v0.9.4.md`、若干 `next_doc/` 内部互引）已同步更新为新文件名。改名清单见 `next_doc/README.md` 末尾"已处理事项"。触发改名的直接原因：这批中文文件名在部分压缩/解压工具链下会被转成 `#Uxxxx` URL 编码转义乱码，英文文件名从根本上规避了这个问题。
- ✅ **步骤六**（拆分体量失衡的单篇文档）：`docs/growth-advisor-guide.md`（1932 行）按 §2.2 方案拆分为骨架 `docs/growth-advisor-guide.md`（545 行，保留 §1/1.5/2.1-2.4/3/4/5/6/7）+ 演进日志 `docs/growth-advisor-directions-history.md`（1444 行，原 §2.5-2.24a、§5.5-5.9 按方案批次分十组重排）。`docs/workflow-guide.md`（2323 行，无现成拆分方案）按同一原则拆分为骨架 `docs/workflow-guide.md`（1172 行，保留架构/核心概念/YAML 格式/内置工具/示例/配置参考/CLI）+ 演进日志 `docs/workflow-directions-history.md`（1181 行，Session 目录～内置模板库共 21 个 P1-P15 能力方向章节按原顺序整体迁移）。两组拆分均已排查并修复文档内部"见下文/见上文"等因拆分产生的跨文件失效指代，全局链接完整性核查（`broken count`）与拆分前一致，未引入新的失效链接。`docs/README.md` 新增"拆分文档"对照表。

验收：`grep -r "project-readme\|tool_call_format_correction\|format-correction-detector-update" docs/ next_doc/ README.md` 已确认无残留失效链接（`next_doc/docs_governance_reorganization_plan.md` 本文档内的历史问题记录性引用除外，属正常存档描述）。
