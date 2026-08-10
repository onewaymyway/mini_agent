# 成长顾问：主动检索 + 生命周期时间线 实施记录

对应计划：`growth_advisor_active_search_and_lifecycle_plan.md`。

## 已完成

### 方向一：真正的主动检索

- `config/models.py::GrowthAdvisorConfig` 新增
  `report_active_search_enabled`（默认 `False`）、
  `report_active_search_max_calls`（默认 `1`，当前实现固定用 1 次，
  字段先预留）。
- `evolution/growth_advisor.py` 新增 `_active_search_excerpts_for_topic()`：
  复用 `external_input/tech_radar_search.py` 的抽取 prompt/解析函数与
  `wiki/world_writer.py::queue_entities/queue_facts`，检索结果落盘为
  `source_kind="external_search"`，`source_entries` 前缀
  `growth_advisor_active_search:<candidate_id>:<query>`，与巡检产生的
  页面区分来源。任一环节失败（检索异常/空结果/LLM 异常/解析失败/
  import 失败）均静默返回空列表。
- `generate_growth_report()` 新增 `web_search_fn` 可选参数：仅当
  `_external_signal_count_for_topic()` 命中 0 条 **且**
  `cfg.report_active_search_enabled=True` **且** 传入了
  `web_search_fn` 时才触发主动检索；命中数 >0 时行为与改动前完全一致
  （仍走被动摘录路径）。不传 `web_search_fn` 的既有调用方（`run_daily_
  cycle()` 等）行为不变。

### 方向二：成长轨迹时间线可视化

- `evolution/growth_advisor.py` 新增 `growth_topic_lifecycle(paths,
  dedupe_key, *, goal_backlog=None)`：聚合 `discovered` /
  `report_generated`（可多条）/ `accepted` / `dismissed`（可多条）/
  `goal_linked` / `goal_completed`｜`goal_stalled`｜`goal_active` 事件，
  按时间正序返回；缺失阶段不补空事件；`goal_backlog` 未传入时静默跳过
  Goal 相关事件。
- `growth_topic_map()` 本次未改动返回结构（不内嵌完整时间线，按需
  单独调用 `growth_topic_lifecycle()`，见计划文档"非目标"一节）。

## 测试

新增 `tests/test_growth_advisor_active_search_and_lifecycle.py`：
- 开关关闭时不触发检索（回归保护）。
- 被动素材为 0 且开关开启时触发检索、报告 prompt 带外部背景、
  wiki pending 队列写入带正确来源标记的实体。
- 检索抛异常时报告生成仍然成功（不被检索失败拖垮）。
- 生命周期时间线：发现 → 报告 → 采纳 三阶段顺序正确、未落地 Goal 时
  不出现 `goal_linked`；未知主题返回空列表。

跑过 `tests/test_growth_advisor.py`、
`tests/test_growth_advisor_goal_cron_integration.py`、
`tests/test_growth_advisor_research_quality.py`、
`tests/test_external_input_tech_radar_search.py`，均通过（仅有一个跟
本次改动无关的既有失败用例
`TestTopicTrend::test_compact_topic_trend_storage_downsamples_old_points`，
是按自然周边界计算日期的既有测试，跟当前运行日期落在周边界附近有关，
改动前就会失败，本次未触碰该逻辑）。

## 未完成 / 留给后续

- 看板（Streamlit kanban）尚未接入 `growth_topic_lifecycle()` 的图形化
  时间轴展示，也未提供"手动生成报告"入口里勾选"允许现查"的开关 UI；
  当前只打通了数据层（函数可直接被 CLI/API/看板后端调用）。
- CLI `/growth report <id>` 命令尚未透传 `web_search_fn`（需要在命令
  实现处从 Agent 已注册的 `tools/builtin.py::web_search` 取一个绑定好
  cfg 的 partial 函数传进去）。
- `run_daily_cycle()`（cron 无人值守路径）按计划文档"非目标"明确不接入
  主动检索，未来如需要需单独评估调度成本。
