# 成长顾问（Growth Advisor）改进计划 v4

- **版本**: v1（草案，方向级 + 关键设计点级规划）——**N1、N2 已实施**，
  详见 `next_doc/growth_advisor_implementation_record.md` "N1：诊断面板
  健康度趋势化" / "N2：cron 记忆回填 M3" 两节；N3-N5 尚未实施。
- **前置文档**:
  - `next_doc/growth_advisor_design.md`（原始方案）
  - `next_doc/growth_advisor_improvement_plan_v2.md`（P4-0~P4-7，已完成）
  - `next_doc/growth_advisor_improvement_plan_v3.md`（P5-0~P5-6，已完成）
  - `next_doc/growth_advisor_implementation_record.md`（逐阶段实施记录）
  - `next_doc/memory_backfill_and_profile_update_plan.md`（M1/M2 已完成，
    M3/M4 是本文档的方向一）
  - `next_doc/external_knowledge_wiki_and_self_improvement_plan.md` /
    `next_doc/external_knowledge_feedback_loop_improvement_plan.md`
    （外部输入网关的既有设计，本文档方向二是它跟成长顾问之间的桥接）
- **触发背景**: v3 的 7 个方向全部落地后，一次针对性复盘聚焦在三个此前
  文档里提过、但一直没有具体设计的点：
  1. cron/daemon 任务本身仍然是记忆覆盖率的结构性盲区（M3/M4 一直搁置）；
  2. 外部输入网关的信号（RSS 订阅、主动检索、生态定位扫描）跟成长顾问的
     关键词表/主题地图是两套完全独立的机制，用户在两边的"兴趣"互不感知；
  3. 诊断面板 `diagnostics_snapshot()` 只有"当下"，没有"变化"——用户没法
     直观看到"记忆总条数这周涨了多少""待回填候选数量是不是在下降"。
- **本文档定位**: 跟 v2/v3 一样，给出方向、设计要点、改动量级评估和分期
  建议，不是逐行实现代码。**沿用既定约束：涉及"理解/归类文本"的能力一律
  走 LLM（`llm_helper` opt-in 模式），不引入 embedding/向量检索。**

---

## 方向一：记忆回填 M3/M4——让 cron/daemon 任务本身产出记忆

### 1.0 现状回顾

`memory_backfill_and_profile_update_plan.md` 第 2.4 节已经把问题診断清楚：
`cron_agent_bridge.py` 的设计前提是"每次触发都重新构建 Agent，不跨触发
保留 session 历史"，因此 cron 任务运行完全不会经过 `Session`/`summary`
这条链路，M1 的存量回填天然扫不到它们。方案文档提出了 A/B 两个方案，
M1/M2 落地时选择"先只做 M1/M2，M3（方案 A）中等优先级，M4（方案 B）
先不做"。目前 M3 仍未实施。

本节把方案 A 的设计要点补全到可以直接开工的粒度。

### 1.1 接入点：`CronJobExecutor.run_job()` 的收尾逻辑

`src/mini_agent/evolution/cron_job_executor.py` 的 `run_job()` 已经有一个
统一的 `finally` 收尾块（约 192-223 行），无论 `final_status` 是
`idle`/`timed_out`/`needs_human_review` 都会执行到这里，并且已经在这里
调用了 `_write_output_manifest()`（写产出物清单）。这是最自然的接入点——
新增一次"记忆化"调用，跟 `_write_output_manifest()` 并列，同样是"收尾时
顺带做的感知增强，不能反过来影响主流程"。

```python
# finally 块内，_write_output_manifest(...) 调用之后
if final_status == STATUS_IDLE and last_text.strip():
    self._maybe_backfill_memory(
        job=job, run_id=run_id, last_text=last_text,
        started_at=state.last_run_started_at,
        finished_at=state.last_run_finished_at,
    )
```

**严格限定触发条件**（对齐方案文档 2.4 节"建议"）：只有
`final_status == STATUS_IDLE`（正常收尾，不含 `timed_out`/
`needs_human_review`）且 `last_text` 非空才会生成记忆。异常/卡死/超时
的运行不产出记忆——这类运行本身信息价值低，强行摘要只会污染成长顾问的
信号扫描。

### 1.2 复用离线摘要生成的纯函数，跳过 `Session` 中转

`memory_backfill.py::generate_summary_offline()` 已经是"输入 history/
文本、输出 summary，不依赖存活 Agent 实例状态"的纯函数版本，但它的输入
是 `list[dict]`（session.history 格式）。cron 任务这边没有这种结构，只有
`last_text`（最后一步的完整输出文本）——需要新增一个更轻量的变体，直接
对一段文本做摘要，不需要先构造伪造的 history：

```python
# memory_backfill.py 新增
def generate_summary_from_text(
    text: str, llm_client: "LLMClient", *, max_chars: int = 4000,
) -> str:
    """对一段已有文本（不是完整对话历史）做摘要，供 cron 任务收尾场景
    复用。跟 generate_summary_offline() 共享同一套 prompt 模板
    （user/session_summary_request + system/summarizer），只是输入侧从
    "用户发言列表"换成"单段任务产出文本"，避免为 cron 场景另建一套
    摘要 prompt。"""
```

`task_template`（job 本身的任务描述）也应该拼进摘要输入，不能只看最后
一步输出——否则摘要读起来会是"做了后续处理"这种没有上下文的碎片。

### 1.3 `session_id` 合成规则：延续已确认的方案

沿用方案文档第 4 节风险项 2 已经核实过的结论：`cron:<job_id>:<run_id>`
前缀 + 冒号的格式，跟真实 `Session.id`（`uuid.uuid4().hex[:8]`，纯十六
进制无分隔符）取值空间不相交，且 `memory_store.py`/`growth_advisor.py`
对 `session_id` 全部是字符串相等比较或展示切片，不做格式解析——可以直接
复用这个结论，不需要重新审计。

### 1.4 配置项：独立开关 + 复用已有 `MemoryBackfillConfig`

```python
@dataclass
class MemoryBackfillConfig:
    ...  # 已有字段不变
    cron_session_backfill_enabled: bool = True  # 方案文档已经预留了这个字段名，
                                                  # 只是至今没有代码实际读取它——
                                                  # 本节把它接上
```

`CronJobExecutor` 需要能读到这个配置（当前构造函数只接受
`circuit_breaker`），新增一个可选的 `memory_backfill_cfg` 参数，未传入时
（比如测试里直接构造 `CronJobExecutor` 的场景）默认为 `None`，`None` 时
`_maybe_backfill_memory()` 直接跳过——保持向后兼容，不强制所有调用方都
升级构造参数。

### 1.5 幂等与去重

cron 任务每次触发都是独立的 `run_id`，`session_id` 天然唯一（`run_id` 本
身已经保证不重复），不存在"同一次运行被重复写入两条记忆"的问题。但需要
注意：**同一个 job 的连续多次触发如果任务模板高度相似**（比如"每小时
检查一次待办"这类几乎不变的任务），会持续产生高度雷同的记忆条目，可能
反而稀释成长顾问信号扫描的信噪比。

**处理方式**：复用 `StuckDetector` 同款的"文本相似度"判断思路，但不引入
新依赖——`_maybe_backfill_memory()` 在生成摘要前，先跟该 job 最近一条
已生成的记忆摘要做一次简单的归一化文本比对（复用 `wiki` 模块已有的
`normalize_title_key`/字符串相似度工具，如果这类工具本身就是规则实现，
不是 embedding），高度雷同则跳过本次记忆生成——**只是跳过写入，不影响
`_write_output_manifest()` 等其它收尾逻辑**。这个去重不追求完美，只是
避免"日报级别的高频重复 cron 任务"把记忆库刷成同质化内容。

### 1.6 与 growth_advisor 的联动

cron 产出的记忆条目 `session_id` 带有 `cron:` 前缀，成长顾问的信号扫描
（`growth_signal_scan`）本身按内容关键词匹配、不关心 `session_id` 格式，
不需要改动。但**诊断面板可以顺带展示一个新维度**："当前记忆里有多少条
来自 cron 自动产出" vs "来自真实交互 session"——帮助用户判断成长顾问的
候选主要是被哪一类活动驱动的（这一点跟方向三的"诊断面板趋势化"是同一批
改动，见方向三 3.4 节）。

**改动量级**：中偏大（对齐方案文档原有评估）。核心逻辑不复杂，但涉及
`CronJobExecutor` 构造签名变化（需要过一遍所有实例化点，包括测试里的
直接构造）、以及"超时/卡死场景不产出记忆"这条规则需要专门的回归测试
覆盖（对齐方案文档"建议先只做方案 A，且限定为正常收尾才生成"的要求）。

### 1.7 M4（方案 B：cron 全面持久化 session）——依然不建议现在做

M3 落地后建议观察至少一个迭代周期，确认"cron 记忆去重"（1.5 节）的效果
是否符合预期，再决定要不要推进 M4。M4 改变的是 `cron_agent_bridge.py`
"不跨触发保留历史"这个核心设计前提，影响面评估成本本身就很高，本文档
不展开设计，维持 v3 之前的判断：**先不做**。

---

## 方向二：外部输入网关与成长顾问关键词表打通

### 2.0 现状回顾

项目里已经有两套完全独立的"用户兴趣/关注点"表达机制：

| | 数据来源 | 存储位置 | 用途 |
|---|---|---|---|
| 成长顾问关键词表 | 用户在看板手动添加 / LLM 从记忆里学到 | `profile.derived["growth_topic_keywords"]` | 驱动 `growth_signal_scan()` 扫描记忆、生成成长候选 |
| 外部输入配置 | 用户在 `agent_config.json` 手工配置 | `TechRadarConfig.keywords` / `EcosystemPositioningConfig.seeds` / RSS 源的关键词过滤规则 | 驱动 `tech_radar_search.py`/`knowledge_extractor.py` 检索外部世界、沉淀进 wiki |

两者本质上都是"用户关注什么"的表达，但完全没有互相反哺：

- 用户在成长顾问关键词表里确认了一个主题（比如"Rust 异步运行时"），
  `TechRadarConfig.keywords` 不会自动收到这个信号，外部检索这边对这个
  新兴趣"看不见"，除非用户再手动去配置文件里加一遍。
- 反过来，`tech_radar_search`/`knowledge_extractor` 持续抓到的外部资讯，
  即使命中了用户已经在成长顾问里关注的主题，这些"外部世界的动态"也不会
  作为一种新的"证据"参与到该主题的置信度计算里——`growth_signal_scan()`
  的证据完全来自 `MemoryEntry`（用户自己的会话记忆），外部世界发生了什么
  跟"用户自己是不是在这方面有持续投入"是两回事，但至少可以作为**辅助
  信号**（比如"你关注的这个方向，外部世界这周有 N 条相关资讯"）。

### 2.1 设计原则：单向桥接，不做双向自动同步

**决策：只做"成长顾问关键词 → 外部输入配置种子"这一个方向的自动桥接，
不做反向自动同步。**

理由：
- 成长顾问关键词表的主题来自用户自己的行为（记忆/手动确认），信噪比高，
  适合作为外部检索的种子来源；
- 外部输入产生的信号（RSS 标题、检索结果）数量大、噪声也大，如果反向
  自动往成长顾问关键词表里加主题，容易把"外部世界很火但用户毫不关心"的
  话题错误地当成用户兴趣，违背成长顾问一贯"证据来自用户自己的记忆/行为"
  的克制原则。
- 外部信号如果要参与成长顾问，走"辅助信号/展示补充"（见 2.3 节），而不是
  "自动生成新的关注主题"。

### 2.2 桥接点一：关键词表 → tech_radar 种子

`growth_topic_map()`（现有函数，聚合看板"成长主题地图"）已经能拿到
"用户当前活跃关注、且有持续证据支持"的主题列表。新增一个桥接函数：

```python
# growth_advisor.py 新增
def sync_confirmed_topics_to_tech_radar(paths, profile, cfg) -> int:
    """把成长顾问里"已确认"（confirmed_by_user=True）且当前未被隐藏的
    自定义/学习到主题，同步进 TechRadarConfig.keywords，供
    tech_radar_search.py 的主动检索种子池使用。

    - 只同步 confirmed 状态的主题（`user_added` 或已转正的 `llm_learned`），
      待确认的不同步——避免把还在观察期的候选主题也拉去消耗外部检索配额。
    - 幂等：已经在 TechRadarConfig.keywords 里的主题不重复添加。
    - 不做反向删除：用户在成长顾问里隐藏/删除一个主题，不会自动从
      tech_radar 种子池移除——用户可能仍然希望持续关注外部动态，即使
      不想让这个主题再出现在成长顾问的候选生成里；两者语义不同，不能
      混为一谈。删除 tech_radar 种子仍然需要用户去配置里手动做。
    - 返回本次新增的种子数量，供 cron/CLI 展示"本轮同步了 N 个新种子"。
    """
```

**触发时机**：不做成实时同步（避免每次用户点"确认"都触发一次配置写入的
副作用，让"确认主题"这个操作变重），而是接入 `run_daily_cycle()`（跟
`compact_topic_trend_storage()` 同样的"每日 cron 顺带做一次"模式），
在配置项 `cfg.sync_confirmed_topics_to_tech_radar_enabled`（新增，默认
**关闭**——这会实际修改 `agent_config.json` 的内容，属于有实际外部效果
的写操作，不应该默认开启）开启时才执行。

### 2.3 桥接点二：外部资讯命中 → 成长顾问候选展示补充信号

`knowledge_extractor.py` 沉淀进 wiki 的 entity/fact 带有
`source_kind="external_watch"`/`"external_search"` 标记。新增一个只读
聚合函数：

```python
# growth_advisor.py 新增
def _external_signal_count_for_topic(paths, topic: str, keywords: list[str],
                                      *, window_days: int = 30) -> int:
    """粗略统计：最近 window_days 天内，wiki 里有多少条
    source_kind in (external_watch, external_search) 的条目标题/摘要
    命中了该主题的关键词（复用现有的规则匹配逻辑，跟
    growth_signal_scan() 对记忆做关键词匹配是同一套简单规则，不引入
    新的匹配算法）。只读聚合，不改变任何置信度计算，只作为附加展示
    信息。"""
```

**用途仅限于展示，不参与置信度计算**——这是本节最关键的克制点：外部
世界的资讯量本身跟"用户自己是否感兴趣"没有必然关系（可能是行业普遍
在讨论，但用户完全不关心），如果把这个数字混进 `_confidence_from_
evidence()`，会破坏"置信度只反映用户自己证据"这个此前一直坚持的语义。

看板/报告里可以在候选卡片上加一句"外部世界最近 N 条相关资讯"作为背景
信息（"用户关注的这个方向，最近业界也有动态"），但明确不影响排序/推送
判断。

### 2.4 桥接点三：调研报告生成时可选纳入外部资讯作为背景

`generate_growth_report()` 目前的证据来源全部是用户自己的记忆摘要。
`report_quality_llm_enabled` 开启时已经会用 LLM 生成更高信息密度的报告
正文——可以在这个已有的 opt-in 路径上，额外把 2.3 节统计到的外部资讯
标题（只给标题，不展开内容，控制 prompt 长度）作为"背景参考"一并喂给
LLM，明确要求"这些只是外部背景信息，报告的核心判断仍然要基于用户自己的
记忆证据"。

**新增独立开关**：`cfg.report_include_external_context`（默认关闭），
不跟 `report_quality_llm_enabled` 强绑定（用户可能想要更好的报告质量，
但不想引入外部资讯作为背景，两者应该能独立控制）。

### 2.5 风险与开放问题

1. **`TechRadarConfig.keywords` 写入是修改 `agent_config.json` 的副作用
   操作**——需要确认 2.2 节的同步函数走的是跟"看板保存配置"完全一致的
   配置写入路径（`config_catalog`/`param_registry`），不能绕过去直接
   改 JSON 文件，否则会重新引入 P5-5 修复过的"字段类型不校验"风险。
2. **种子池膨胀问题**：如果用户在成长顾问里确认了大量主题，2.2 节的
   同步会让 `TechRadarConfig.keywords` 持续增长，而 `daily_seed_limit`
   （默认 5）不变，意味着种子池覆盖一轮的周期会越来越长。这个问题
   `tech_radar_search.py` 本身的轮转游标机制已经能兜住（不会丢种子，
   只是变慢），可以接受，但建议在文档里明确写清楚这个权衡，不算 bug。
3. **2.3 节的关键词匹配复用 growth_signal_scan 的规则**——需要确认
   wiki 条目的标题/摘要字段命名跟成长顾问关键词匹配的输入格式兼容，
   实现时如果字段结构差异较大，可能需要一层适配而不是直接复用。

**改动量级**：中——三个桥接点都是纯新增的可选函数/开关，不修改任何
现有函数的行为（默认关闭时零副作用），但涉及跨模块（`growth_advisor.py`
+ `external_input/*` + 配置写入路径）的联调测试。

---

## 方向三：诊断面板历史趋势化

### 3.0 现状回顾

`diagnostics_snapshot()` 是纯只读聚合，每次调用只反映调用瞬间的状态。
`growth_topic_trend.jsonl`（P4-6）已经证明了"定期记快照 + 时间序列展示"
这个模式在成长顾问里是可行的（且 P5-0 已经补上了降采样机制），但目前只
覆盖"单个主题的证据数/置信度走势"，没有覆盖"全局健康度指标"这个维度。

### 3.1 新增：全局健康度快照

新建 `growth_health_trend.jsonl`（复用 `growth_topic_trend.jsonl` 的
"只追加 + 定期降采样"模式，不是全新的存储范式），每次 `run_daily_cycle()`
结束时记一条：

```python
# growth_advisor.py 新增
def _record_health_snapshot(paths, cfg, profile, memory_store) -> None:
    """在 run_daily_cycle() 每轮结束时记一条全局健康度快照，供看板画
    趋势图。字段选取原则：只记诊断面板已经在展示的数字（不引入新的
    统计口径），避免"趋势图上的数字"和"诊断面板上的数字"来源不一致
    造成用户困惑。"""
    snap = diagnostics_snapshot(paths, cfg, profile, memory_store)
    _append_jsonl(paths.growth_health_trend_path, {
        "recorded_at": time.time(),
        "total_entries": snap["memory"]["total_entries"],
        "entries_in_scan_window": snap["memory"]["entries_in_scan_window"],
        "backfill_candidates_count": snap["memory"]["backfill_candidates_count"],
        "pending_followups_count": snap["pending_followups_count"],
        "reports_needing_refresh_count": snap["reports_needing_refresh_count"],
        "topics_tracked_count": len(snap["signal_scan"]["topics_tracked"]),
        # [方向一 1.6 节联动] 如果 M3 落地，这里补一个
        # "cron_originated_entries" 字段，跟 total_entries 对照展示
        # "记忆里有多少比例来自 cron 自动产出"。
    })
```

**接入点**：`run_daily_cycle()` 末尾（跟 `_maybe_dispatch_notification`
同级），每天最多一条快照，不需要更高频率——诊断面板本来就是"排障用"，
不是实时监控。

**降采样**：直接复用 `_compact_topic_trend_rows()` 的思路写一个平行
实现（`_compact_health_trend_rows()`），按天分桶，超过窗口期的旧快照
每天只保留一条（本身已经是每天一条，所以这个降采样主要是为未来"提高
记录频率"预留的安全网，当前阶段可以先不做实际压缩，只留函数接口）。

### 3.2 查询函数与看板展示

```python
def health_trend_series(paths, *, limit: int = 30) -> list[dict]:
    """返回最近 limit 天的健康度快照，按时间正序，供看板画折线图。"""
```

看板"🌱 成长顾问"tab 的诊断区块新增一个可折叠的"📈 健康度趋势"区块：
用 `st.line_chart`（Streamlit 内置，不需要新依赖）画 `total_entries`/
`backfill_candidates_count`/`topics_tracked_count` 三条线（复用现有
`_render_growth_diagnostics` 附近的展示位置）。

### 3.3 API 端点

新增 `GET /growth/health_trend`，独立于 `/growth/summary`（趋势数据
不需要每次打开 tab 都拉取，看板可以在用户展开趋势区块时才请求，减少
默认加载的数据量）。

### 3.4 与方向一/方向二的联动展示

- 方向一（M3）落地后，`total_entries` 的构成里会混入 cron 产出的记忆，
  健康度趋势图正好是观察"M3 上线后记忆总条数是否明显回升"的天然验收
  工具——不需要额外开发验收脚本，趋势图本身就是效果验证。
- 方向二（外部信号桥接）如果落地，可以在健康度快照里附带一个
  `external_signal_topics_count`（有外部资讯命中的主题数），跟
  `topics_tracked_count` 对照，观察"外部信号覆盖了多少比例的关注主题"。

### 3.5 风险与开放问题

1. **不要让趋势图本身成为新的性能负担**：`diagnostics_snapshot()` 内部
   已经有几处"扫描记忆全量"的计算（比如 `backfill_candidates_count` 的
   `scan_sessions_for_backfill()`），每天一次调用可以接受，但如果未来
   有人提高快照频率，需要重新评估这些全量扫描的开销——建议在
   `_record_health_snapshot()` 的 docstring 里明确写清楚"只应该在
   run_daily_cycle 这个既有的每日调用点触发，不应该被其它地方高频调用"。
2. **`growth_health_trend.jsonl` 是第 4 个只追加文件**——延续 P5-0 的
   审计习惯，本节新增文件从设计时就带上降采样机制（3.1 节已经预留），
   不要重蹈"先上线、后面再补 P5-0 治理"的覆辙。

**改动量级**：小——纯新增的只读聚合 + 展示，不修改任何现有函数行为，
风险主要在"新增第 4 个 jsonl 文件要不要一起治理"这个存量问题上，建议
一次做好，不要留给未来的 P6。

---

## 优先级与分期建议

| 序号 | 方向 | 优先级 | 理由 | 改动量级 | 依赖 |
|---|---|---|---|---|---|
| N1 | 方向三 3.1-3.3（健康度趋势记录 + 展示） | 高 | 改动量级最小、风险最低，且能作为 N2 的验收工具，建议第一个做 | 小 | 无 | **已实施** |
| N2 | 方向一 M3（cron 记忆回填） | 高 | 用户最初反馈里"daemon 跑的任务没有 memory"更核心的一半，健康度趋势图（N1）能直接验收上线效果 | 中偏大 | 建议排在 N1 之后 | **已实施** |
| N3 | 方向二 2.2 节（关键词 → tech_radar 种子同步） | 中 | 改动集中、默认关闭零风险，但收益依赖用户本身已经在用 tech_radar 功能 | 中 | 无强依赖，可与 N2 并行 |
| N4 | 方向二 2.3/2.4 节（外部资讯作为展示/报告背景） | 低 | 明确"仅展示、不影响判断"的克制设计，收益是锦上添花性质，不紧急 | 中 | 建议排在 N3 之后（依赖 2.2 节跑出的种子同步先验证数据链路通畅） |
| N5 | 方向一 M4（cron 全面持久化 session） | 低，先不做 | 同 v3 判断，观察 N2 效果后再评估 | 大，需单独立项 | 依赖 N2 |

---

## 验收标准

- **N1**：跑满 3 天以上的 `sys:growth_advisor_daily`，看板"📈 健康度
  趋势"区块能展示至少 3 个数据点的折线；`growth_health_trend.jsonl`
  文件存在且格式符合预期。
- **N2**：对一个正常收尾（非 timed_out/needs_human_review）且有实质
  产出的 cron job，触发一次运行后，`memory_store` 里应该出现一条
  `session_id` 形如 `cron:<job_id>:<run_id>` 的新记忆；对一个
  timed_out 收尾的运行，触发后不应该产生新记忆（负向用例，需要专门
  测试覆盖）；N1 的健康度趋势图应该能观察到 `total_entries` 的回升。
- **N3**：在成长顾问看板确认一个此前待确认的自定义主题后，开启
  `sync_confirmed_topics_to_tech_radar_enabled` 并跑一轮
  `run_daily_cycle()`，`TechRadarConfig.keywords` 里应该出现该主题名；
  重复跑一轮不应该产生重复项（幂等）。
- **N4**：开启 `report_include_external_context` 后生成一份调研报告，
  报告正文应该能看到"外部背景"相关表述，且候选的置信度数值跟开启前
  相比不发生变化（验证"仅展示、不影响判断"这条设计约束真正生效）。

---

## 已知风险汇总（跨方向）

1. N2（cron 记忆回填）如果去重机制（1.5 节）设计不当，高频重复的
   cron 任务可能持续产生同质化记忆，反而稀释成长顾问信号扫描的信噪比
   ——这是本文档里唯一一个"做了可能比不做更糟"的风险点，实现时需要
   优先把 1.5 节的去重逻辑落实，不能只做"生成记忆"这一半。
2. N3 涉及修改 `agent_config.json`（`TechRadarConfig.keywords`），必须
   走配置系统既有的校验路径（P5-5 的类型校验兜底），不能绕过去直接写
   JSON 文件，否则会重新引入已经修复过的配置健壮性问题。
3. N1 新增的 `growth_health_trend.jsonl` 是第 4 个只追加文件，需要从
   一开始就规划降采样（哪怕先只是预留接口），避免重蹈
   `growth_feedback_ledger.jsonl` 至今未治理的覆辙。
4. 三个方向都不同程度依赖"外部世界的信号不能污染基于用户自己证据的
   判断"这条既有原则（尤其方向二），实现时任何一处如果不小心让外部
   信号参与了置信度计算/排序，都是对这条原则的破坏，需要在 code review
   环节重点检查。
