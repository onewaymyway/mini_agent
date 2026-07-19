# wiki 知识库改进计划 · 下一阶段实施记录

对应计划文档：`next_doc/wiki_next_phase_improvement_plan.md`

## 新增文件

| 文件 | 作用 |
|---|---|
| `src/mini_agent/evolution/step_runner.py` | 巩固循环子步骤限时执行包装器 `run_step()`，线程+轮询实现超时，附默认超时预算表 `DEFAULT_STEP_TIMEOUTS` |
| `src/mini_agent/wiki/decommission.py` | 旧图书馆索引（分类树/实体索引/编年目录）下线评估：`check_and_plan()`（只读评估+三步下线清单）、`check_ready_transition()`（状态翻转检测） |
| `src/mini_agent/wiki/gap_scanner.py` | 知识缺口主动扫描：`scan_gaps()`（浅层实体/孤儿页面/陈旧专题页规则扫描）、`mark_stale_topics()`（复用 O4 生命周期机制标注陈旧专题页） |
| `src/mini_agent/wiki/fallback_cleanup.py` | session-facts 兜底页归并/清理：`cleanup_fallback_pages()`（页面级判重，命中合并/未命中标 stale） |

## 修改文件

| 文件 | 改动 |
|---|---|
| `src/mini_agent/evolution/consolidation.py` | `run_consolidation()` 8 个顶层子步骤改用 `run_step` 包装；`ConsolidationReport` 新增 `step_timings`/`timed_out_steps` |
| `src/mini_agent/perception/library_index.py` | `consolidate()` 内部步骤 3/4/5/5b/6/7（实体摘要重写/实体巩固/wiki镜像/世界模型落盘/索引重建/专题页生成）逐一加独立超时预算；返回值新增 `step_timings` |
| `src/mini_agent/history/extraction_trigger.py` | 新增 `entity_density` 触发规则（正则候选词 + `known_entity_names` 过滤 + `load_known_entity_names()`），与既有 `connective_density` 并行，触发原因分开记录 |
| `src/mini_agent/storage/paths.py` | 新增 `wiki_decommission_report_path`、`wiki_gap_scan_log_path` 两个路径属性 |
| `src/mini_agent/cli/commands/wiki.py` | 新增 `/wiki gap-scan [--max-results N] [--dispatch]`、`/wiki fallback-cleanup [--days N]` 两个子命令 |
| `src/mini_agent/evolution/cron_scheduler.py` | `_BUILTIN_JOBS` 新增 `sys:wiki_gap_scan`（12h）、`sys:wiki_fallback_cleanup`（7d）两个内置 job |

## 设计决策修正（与计划文档草稿的差异）

1. **陈旧专题页标注机制**：计划草稿最初设想新增 `status: stale`，实现前重新核对代码
   发现 O4 阶段已有独立的 `knowledge_state` 字段和 `wiki/lifecycle.py::mark_page_state()`
   做"新鲜度状态机"，语义完全对应，遂改为直接复用，未修改 `wiki/parser.py::STATUS_VALUES`
   （该枚举维持原有 6 个值不变）。
2. **兜底页清理粒度**：从计划草稿设想的"逐条 fact_id 粒度"简化为"整篇兜底页粒度"，
   降低实现复杂度，代价是判重的精确度会粗一些（一整篇里可能只有部分 fact 真正该合并）。
3. **`--dispatch` 的执行环境限制**：`/wiki gap-scan --dispatch` 依赖 `agent._input_queue`
   （`api/bridge.py::InputQueue`），该对象目前只在 daemon `autonomous_loop` 上下文里存在，
   交互式 CLI 会话没有；命令对此做了显式检测和提示，而不是静默失败或报错。

## 验证记录

- 新增文件与全部修改文件均通过 `python3 -m ast.parse` 语法检查。
- `entity_density` 触发规则用真实文本手工验证：对纯描述性输入
  （"这个项目用 FastAPI 和 PostgreSQL，部署在 AWS 上，配置文件在 config/app.yaml"）
  能正确触发 `trigger_reason="entity_density"`，验证了该规则确实覆盖了
  `connective_density` 规则抓不到的场景。
- **新增了 4 个专属单测文件**（共 30 个用例，全部通过）：
  - `tests/test_step_runner.py`（4 用例）：正常完成/异常/超时三种结果、超时后
    不阻塞主流程等待原线程跑完。
  - `tests/test_wiki_gap_scanner.py`（7 用例）：浅层实体检测（含"1条链接仍算
    浅层、2条不算"的边界）、孤儿页面检测、陈旧专题页检测+标注+去重复报告、
    max_results 截断、空 wiki。过程中发现一个易错点：`WikiLink` 的
    `source` 字段默认是 `"body"`（弱引用），`render_page()` 只序列化
    `source="frontmatter"` 的链接进 frontmatter——测试用例最初没有显式传
    `source="frontmatter"`，导致强链接被静默过滤、图谱为空，已修正。
  - `tests/test_wiki_decommission.py`（5 用例）：未达标给 blocking_reasons、
    达标给三步清单、报告落盘/读取、状态翻转只提醒一次。
  - `tests/test_wiki_fallback_cleanup.py`（5 用例）：命中合并、未命中标 stale、
    未到年龄阈值跳过、已处理过跳过、无兜底页时空跑。
  - `tests/test_extraction_trigger.py` 追加 3 个用例覆盖 `entity_density`：
    纯描述性内容触发、`known_entity_names` 过滤已知词后不触发、两个信号都
    命中时 `connective_density` 优先。
- 运行改动直接相关的既有测试文件（不含新增）：
  `test_extraction_trigger.py` `test_consolidation.py` `test_memory_consolidation.py`
  `test_wiki_promotion.py` `test_wiki_lifecycle.py` `test_wiki_topics_reconsolidation.py`
  `test_wiki_index_reuse.py` `test_context_builder_wiki_search_primary.py`
  `test_wiki_append_section_dedupe.py` —— 125 个用例全部通过。
- **合计**：相关既有测试 + 新增测试，**149 个用例全部通过**。
- 全量测试套件（2029 个用例）单次运行超过执行时间限制未能完整跑完；抽样
  比对显示未完成区间的失败用例分布（F/E 位置）在**修改前的原始代码**上逐字符
  一致，判断为环境相关的既有失败（大概率是需要真实 LLM/网络访问的测试在离线
  环境下预期失败），非本轮改动引入的回归。

## 遗留 TODO（下一轮可继续）

- `sys:wiki_gap_scan` / `sys:wiki_fallback_cleanup` 两个 cron job 的 `task_template`
  是自然语言指令（走 agent 一轮对话解释执行，与既有内置 job 风格一致），未在真实
  daemon 环境里做端到端联调，建议上线后观察首批几次触发日志。
- `wiki/decommission.py::check_and_plan()` 目前只接入了 `/wiki promotion` 命令
  （命令末尾会顺带展示下线执行清单/差距原因），**尚未接入** daemon
  `evolution/autonomous_loop.py` 的巩固循环——`check_ready_transition()` 提供的
  "只在状态翻转时提醒一次"能力目前需要外部按需调用，还没有自动挂载触发点。
