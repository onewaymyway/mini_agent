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
  时间轴展示；当前只有 CLI 文字版（`/growth timeline <id>`）。
- `run_daily_cycle()`（cron 无人值守路径）按计划文档"非目标"明确不接入
  主动检索，未来如需要需单独评估调度成本。

## 追加：CLI 接入（本次新增）

- `cli/commands/growth_cmd.py` 新增 `_get_web_search_fn(agent)`：把
  Agent 已注册的 `tools/builtin.py::web_search()` 包成
  `generate_growth_report()` 期望的 `web_search_fn(query, max_results)
  -> str` 约定；`agent`/`agent.cfg` 缺失时返回 `None`（据此判断"调用
  方是否具备检索工具"，不新增检索通道）。
- `/growth report <id>` 命令生成报告时改为同时传入 `profile`、`cfg`、
  `web_search_fn`（此前只传了 `llm_helper`，导致 N4 外部背景摘录和
  本次的主动检索都无法在这条路径生效）——`report_include_external_
  context`/`report_active_search_enabled` 默认都是关闭的，接入后
  默认行为不变，用户需要显式在 `agent_config.json` 打开对应开关才会
  生效。
- 新增 `/growth timeline <id>` 子命令：调用 `growth_topic_lifecycle()`
  按时间正序打印该候选所属主题的完整轨迹（发现/报告/采纳忽略/落地
  目标/目标状态）。

新增测试 `tests/test_growth_cmd_timeline_and_active_search_wiring.py`
（6 用例，覆盖 `_get_web_search_fn` 的三种边界情况 + `/growth timeline`
命令的正常/未知候选/缺参数三种路径），全部通过。

## 追加：看板图形化时间轴接入（本次新增）

- `api/routes.py` 新增 `GET /growth/candidates/{candidate_id}/timeline`：
  根据候选反查 `dedupe_key`，调用 `growth_topic_lifecycle()`，尽力附带
  `GoalBacklog`（拿不到时静默传 `None`，事件列表相应缺失 Goal 相关
  阶段，跟 CLI 路径同一容错原则），跟 `/growth/health_trend` 一样是
  按需拉取的独立端点。
- `apps/mini_agent_kanban/client.py` 新增 `growth_candidate_timeline
  (candidate_id)`。
- `apps/mini_agent_kanban/app.py`：
  - 新增 `_render_growth_topic_timeline()`：文字版垂直时间轴（按
    stage 配图标 + 日期 + 文案），不引入图表库，跟 P4-6 证据数走势
    "简单文字箭头"是同一克制原则。
  - `_render_growth_pending_list()` 每张候选卡片新增第 4 个按钮
    "🕒 轨迹"，点击后展开该候选所属主题的完整时间线。

## 未完成 / 留给后续

- `growth_topic_map()` 展开区块（历史主题地图，按 dedupe_key 聚合，
  不含 candidate_id）尚未接入时间轴按钮——当前时间轴入口只挂在
  "待处理候选卡片"上（有 candidate_id 可用）；如需从主题地图行直接
  查看轨迹，需要新增一个按 dedupe_key 查询的端点变体或在候选池里查
  最新一条候选的 id，留给后续按需求评估。
- 拖拽式看板视图（`_render_growth_kanban_dragdrop`，`streamlit-
  sortables` 可用时的路径）尚未加轨迹按钮，只有兜底的列表视图
  （`_render_growth_pending_list`）接入了。
- 真正的图形化时间轴组件（水平/垂直可视化时间轴 UI，而非纯文字列表）
  留给看板专项迭代，本次先把数据链路（API + 客户端 + 基础展示）打通。


## 追加：主题地图 + 拖拽视图接入轨迹入口（本次新增）

- `growth_topic_map()` 返回行新增 `candidate_id`（该主题最新一条候选
  的 id，非破坏性新增字段，不影响既有消费方按 key 取值的逻辑），供
  "🗺️ 成长主题地图"展开区块每行末尾新增"🕒 查看轨迹"按钮直接调用。
- 拖拽式看板视图（`_render_growth_kanban_dragdrop`）：拖拽卡片本身是
  纯字符串标签、没有按钮承载位，补了一个"选择候选 + 查看轨迹"的下拉
  + 按钮组合，不影响原有拖拽交互本身。

至此，三处候选/主题展示入口（列表视图卡片、主题地图展开行、拖拽视图）
均已接入轨迹查看能力。仍未做的是真正的图形化时间轴 UI 组件（当前三处
入口点开后展示的都是同一个 `_render_growth_topic_timeline()` 文字版
垂直列表），留给看板专项迭代评估是否值得引入额外的可视化组件依赖。

## 追加：真正的图形化时间轴（本次新增）

- `_render_growth_topic_timeline()` 改为渲染一条手写 SVG 水平时间轴
  （`_build_growth_timeline_svg()`）：轴线 + 每个事件一个圆点（按
  stage 上色，见 `_GROWTH_TIMELINE_STAGE_COLORS`），标签/日期交替上下
  排布避免拥挤，圆点自带 `<title>` 悬停提示完整文案。SVG 渲染异常时
  静默跳过（`try/except`），不阻塞下面的兜底展示。
- SVG 下方保留一个可折叠的"查看文字版详情"区块（默认收起），完整还原
  此前纯文字版的信息密度，兼顾窄屏/打印场景下 SVG 可能显示不佳的情况。
- 纯手写字符串拼接 SVG，未引入新的可视化组件依赖（`streamlit.
  components.v1` 已在文件顶部导入但本次未使用，保持跟既有 P4-6 走势
  箭头一样"轻量自绘"的原则）。

至此，成长轨迹时间线从数据聚合（`growth_topic_lifecycle()`）到三处
入口（列表卡片/主题地图/拖拽视图）再到图形化展示均已打通，
`growth_advisor_active_search_and_lifecycle_plan.md` 方向二"看板的
时间线图形化渲染留给看板专项迭代"这条非目标本次一并完成。
