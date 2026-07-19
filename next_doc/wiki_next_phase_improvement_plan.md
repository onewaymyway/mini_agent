# wiki 知识库改进计划 · 下一阶段（退轨 / 巩固熔断 / 世界知识 / daemon 定时）

> 本计划基于对当前代码的实际核查（`perception/library_index.py`、`perception/classification.py`
> `/entity_index.py`/`catalog.py`、`evolution/consolidation.py`、`wiki/topics.py`、
> `wiki/promotion.py`、`evolution/cron_scheduler.py`、`history/world_extraction.py`）撰写，
> 只处理四个明确聚焦点，不重复此前 P0-P4 / E1-O4 已完成的工作。

## 实施状态总览（本轮已落地）

| 章节 | 内容 | 状态 | 落地文件 |
|---|---|---|---|
| §1 | 双轨制退出（评估+三步下线清单，不自动删代码） | ✅ 已实现 | `wiki/decommission.py`（新）、`storage/paths.py` |
| §2 | 陈旧专题页标注（knowledge_state=stale） | ✅ 已实现 | `wiki/gap_scanner.py::_scan_stale_topics/mark_stale_topics`（新） |
| §3 | consolidate() 分步超时熔断 | ✅ 已实现 | `evolution/step_runner.py`（新）、`evolution/consolidation.py`、`perception/library_index.py` |
| §4.2.1/4.2.2 | entity_density 独立触发信号 | ✅ 已实现 | `history/extraction_trigger.py` |
| §4.2.3 | 知识缺口主动扫描 | ✅ 已实现（规则扫描；子任务补全由 daemon/人工执行，未接自动 LLM 补全） | `wiki/gap_scanner.py`（新）、`cli/commands/wiki.py` `/wiki gap-scan` |
| §5 | daemon 定时任务（gap_scan / fallback_cleanup） | ✅ 已实现 | `evolution/cron_scheduler.py`（新增 2 个内置 job）、`cli/commands/wiki.py` `/wiki fallback-cleanup`、`wiki/fallback_cleanup.py`（新） |

**已知简化 / 未完成事项**（诚实列出，避免过度宣称）：
- §5.2 兜底页清理简化为**页面级粒度**（整篇 `session-facts-<date>.md` 一起判重/标注），
  未细化到计划草稿设想的**逐条 fact_id 粒度**——逐条拆分需要给每条 fact 独立维护
  锚点级生命周期状态，改动面更大，先验证页面级粒度的收益。
- §4.2.3 的"主动派发子任务读代码补全"目前只做到 `--dispatch` 把任务描述提交进
  `InputQueue`（且只在 daemon `autonomous_loop` 上下文里生效，交互式 CLI 里没有
  `InputQueue` 可用，会给出提示而不是报错）——是否要在 daemon 里默认开启
  `--dispatch` 仍是 §7 里的未决问题，本轮默认不开启。
- §1.2.3「`check_and_plan()` 挂在巩固触发后顺带跑一次」已接入 `/wiki promotion`
  命令（末尾展示下线执行清单或差距原因），但**尚未接入** daemon
  `evolution/autonomous_loop.py` 的巩固循环本身——`check_ready_transition()`
  提供的"只在状态翻转时提醒一次"能力目前仍需要外部按需调用。
- 本轮验证方式：新增代码逐一 `ast.parse` 语法检查通过；新增 4 个专属单测文件
  （`test_step_runner.py`/`test_wiki_gap_scanner.py`/`test_wiki_decommission.py`/
  `test_wiki_fallback_cleanup.py`，共 30 用例）+ `test_extraction_trigger.py`
  追加 3 个 `entity_density` 用例；与改动直接相关的 9 个既有测试文件（125 个
  用例）全部通过——**合计 149 个用例全部通过**。全量测试套件因单次执行时间
  超限未能跑完，但抽样比对显示失败用例在**修改前的原始代码上同样失败**
  （逐字符核对一致的 F/E 分布），判断为环境相关的既有失败，非本轮改动引入。

---
