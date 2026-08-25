# wiki 提取层改进计划 · E2 方案B 实施记录

> 对应 `wiki-knowledge-base-extraction-and-organization-plan.md` §2（问题 E2：抽取任务耦合度过高）
> 的方案B，`[可直接排期]`，已实施。本文件记录具体改动、验收方式和后续待办，
> 不重复计划原文已有的分析。

## 1. 改动内容

### 1.1 JSON schema 字段顺序调整

- `src/mini_agent/prompts/system/compress_summarizer.md`：
  JSON schema 字段顺序由 `{compact_summary, decisions, entities, facts}`
  调整为 `{decisions, entities, facts, compact_summary}`，并新增一段
  "processing order" 显式说明：要求模型先完整识别并填充
  `decisions`/`entities`/`facts`，最后再写 `compact_summary`。
- `src/mini_agent/prompts/user/compress_summary_request.md`：
  在原有摘要要求之前新增一段指令，同样强调"先做结构化抽取、再写摘要"的
  处理顺序。

这两处改动都是纯 prompt 文本调整，**不改变输出 JSON 的 schema 本身**
（字段名、含义、可选性均不变），因此 `history/decision_extraction.py::
parse_decision_response` 与 `history/world_extraction.py::
parse_world_response` 均按 dict key 取值，不受字段顺序影响，无需改动
解析代码。

### 1.2 抽取充分性观测基础设施（新增，用于验收）

- `src/mini_agent/storage/paths.py`：新增
  `AgentPaths.extraction_stats_log` 属性
  （`<project_root>/.agent/extraction_stats.jsonl`）。
- `src/mini_agent/history/compression.py`：
  - 新增 `_log_extraction_stats(cfg, num_decisions, num_entities, num_facts)`，
    每次 `LLMSummaryStrategy` 与摘要同一次 LLM 调用解析出
    decisions/entities/facts 后追加一条 JSONL 记录
    （`{ts, decisions, entities, facts}`）。
  - 纯观测、append-only，写入失败静默跳过，不影响 compact 主流程
    （符合项目"失败不阻断主流程"风格）。
- `src/mini_agent/wiki/stats.py`：新增
  - `ExtractionStats` 数据类
    （`total_batches` / `avg_decisions_per_extraction` /
    `avg_entities_per_extraction` / `avg_facts_per_extraction` /
    `zero_entities_and_facts_ratio`）。
  - `compute_extraction_stats(paths, *, last_n=None)`：读取
    `extraction_stats_log` 计算上述均值指标，支持 `last_n` 截断（用于
    "改动前后各跑 20 次 compact 对比"这类验收场景）；对损坏行/文件不存在
    均静默降级为空统计，不抛异常。
- `src/mini_agent/cli/commands/wiki.py::_handle_stats`：`/wiki stats`
  命令新增一个"抽取批次统计"表格，展示上述指标，方便直接在 CLI 里做
  改动前后对比，不需要额外脚本。

## 2. 验收方式（对应原计划 §2.3）

1. 改动前先跑一批（建议 ≥20 次）触发 `LLMSummaryStrategy` 的 compact，
   记录当时的 `/wiki stats` 输出（或直接读一次
   `extraction_stats.jsonl` 存档）作为基线。
2. 部署本次 schema 顺序调整后的版本，再跑同等规模的一批 compact。
3. 对比 `compute_extraction_stats(paths, last_n=20)` 前后的
   `avg_entities_per_extraction` / `avg_facts_per_extraction` /
   `zero_entities_and_facts_ratio`，方向上应有提升（entities/facts 均值
   上升、"两者皆空"批次占比下降），只要方向对即视为有效，因为改动成本
   几乎为零，无需等待显著统计量。
4. 单测覆盖见 `tests/test_extraction_stats.py`（`_log_extraction_stats`
   写入格式、`compute_extraction_stats` 均值/`last_n`/容错行为）。

## 3. 未在本次实施范围内的项

原计划 §0 问题清单中除 E2 方案B 之外的其余项（O1、E1、E3、O2、O3、O4，
以及 E2 方案 A/C）均**尚未实施**，原因和顺序建议见原计划 §8"总体实施
排期建议"。本次改动不影响它们的后续落地：

- E2 方案 A 已在原计划 §2.2.1 中说明"E1 落地后自然被解决"，无需单独实现。
- E2 方案 C（`extract_world_model_via_compact` 开关）依赖 E1 先落地并观测
  稳定，本次未新增该开关。
- O1/E3/O2/O3/O4 均为更大改动面的设计项，按原计划建议放在后续批次，
  遵循"每批完成后跑一段真实使用周期再进入下一批"的执行纪律。
