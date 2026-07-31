# 外部输入网关：去重持久化 · 新颖信号通道 · 可观测性 · 长期归档 实施记录

对应方案：`next_doc/external_input_reliability_observability_archive_plan.md`

四个子问题互不依赖，本次全部按方案实施完毕（§1 → §3 → §4 → §2 顺序落地，
跟方案里的成本/复杂度排序一致）。每个阶段都补了对应单测，全部通过。

---

## §1 兜底去重缓存持久化 —— 已完成

- `storage/paths.py` 新增 `external_input_gateway_dedup_cache` 属性
  （`.agent/external_input/state/gateway_dedup_cache.json`）。
- `external_input/gateway.py::_RecentIdCache` 新增 `to_list()` /
  `from_list()` / `load()` / `save()` / `maybe_save()`；节流写
  （`_SAVE_EVERY_N=20` 次新增 或 `_SAVE_INTERVAL_SECONDS=30s`）、原子替换
  写文件（临时文件 + `os.replace`）。
- `GatewayPoller.__init__()` 懒加载一次快照（`ensure_dedup_cache_loaded`）；
  `GatewayPoller.stop()` 正常关闭路径下强制落盘一次
  （`flush_dedup_cache`）。
- 测试：`tests/test_external_input_gateway_dedup_persistence.py`（6 条，
  含 to_list/from_list 往返、跨重建命中、损坏文件容错、节流写边界）。

## §3 外部输入网关可观测性（成功率/延迟趋势）—— 已完成

- `storage/paths.py` 新增 `external_input_poll_history` 属性
  （`.agent/external_input/state/poll_history.jsonl`）。
- 新增 `external_input/poll_history.py`：
  - `append_poll_record()`：每次轮询结果追加一条精简记录，超过
    `_MAX_LOG_LINES=5000` 行做滚动截断（每 200 次追加检查一次，控制
    截断本身的开销）。
  - `summarize_poll_history()`：纯读取聚合，返回
    `total_polls/success_count/failure_count/success_rate/
    avg_duration_ms/p50_duration_ms/p95_duration_ms/timeline`（按天分桶
    的时间序列），支持 `source_id` 单查 / 全量分组、`since_days` 边界过滤。
- `poller.py::SourceHealth` 新增 `last_duration_ms` 字段；
  `_run_source_loop()` 用 `time.monotonic()` 计时，成功/失败两条路径都
  记录耗时并调用 `_record_poll_history()`。
- REST：`GET /v1/external_input/health_history?source_id=&since_days=7`。
- 看板：外部输入 tab 新增"📈 来源健康趋势"面板（按 source 展开，
  metric + `st.line_chart` 趋势图，pandas 不可用时降级为文字列表）。
- 测试：`tests/test_external_input_poll_history.py`（6 条，含空文件、
  单条聚合、跨天分桶、since_days 边界、分组、滚动截断）。

## §4 长期归档 / 回顾式查询 —— 已完成

- `storage/paths.py` 新增 `archive_dir` 属性和 `archive_file(subdir,
  file_stem, year_month)` 方法（`.agent/archive/<subdir>/
  <file_stem>-YYYY-MM.jsonl`）。
- 新增 `mini_agent/archive/gc.py`：
  - `ArchiveTarget` 描述一个"热文件 → 归档"的迁移目标，内置 4 个默认
    target：`external_input_alerts`（字段 `acknowledged`/`created_at`）、
    `external_input_pending_hits`（字段 `consumed`/`matched_at`）、
    `external_input_goal_relevance_candidates`（字段
    `judged`/`created_at`）、`notification_reports`（字段
    `acknowledged`/`created_at`）。
  - `run_archive_gc_once()`：独占锁读取热文件 → 按
    `settled_field=true 且 time_field < now - retention_hours` 拆分
    "迁出"/"保留" → 迁出记录按自然月分片追加到归档文件 → 热文件原子
    重写为剩余记录。单 target 失败不抛出，`run_archive_gc_all()` 里
    继续处理下一个 target。
  - `query_archive()`：`GET /v1/archive/query` 的底层实现，按
    `since`/`until`（自然月粒度）枚举归档文件、`keyword` 简单子串匹配、
    按时间倒序分页返回，归档数据只读。
  - `ensure_archive_gc_job()`：daemon 启动时补注册 `sys:archive_gc`
    （默认每天凌晨 3 点，零 LLM 成本），已在 `api/server.py`
    `_build_autonomous_loop()` 里接入。
- REST：`GET /v1/archive/query?category=&since=&until=&keyword=&limit=&offset=`。
- 看板：外部输入 tab 新增"🗄️ 归档查询"面板（类别/起止月份/关键词 +
  查询按钮，只读展示，无操作按钮）。
- 测试：`tests/test_archive_gc.py`（5 条，含混合记录拆分、跨月分片、
  查询过滤+分页、越界空结果、单 target 失败不影响其它 target）。

## §2 "新颖重要事件"受控出口（独立通道）—— 已完成

- `storage/paths.py` 新增 `external_input_novelty_candidates_raw`
  （`.agent/external_input/novelty_candidates_raw.jsonl`，Stage① 产出）
  和 `notification_novelty_candidates`
  （`.agent/notification/novelty_candidates.jsonl`，Stage② 产出、人工
  确认队列）。
- 新增 `external_input/novelty_judge.py`：
  - **Stage①**（规则粗筛，零 LLM 成本）：独立 `consumer_name=
    "novelty_judge"`、独立游标，消费全部 `external.*` 事件，只用
    `.agent/notification/novelty_judge.yaml` 里的 `exclude_channels`
    排除明显噪音 channel；候选按 `candidate_id` 去重；总量止损
    `MAX_RAW_CANDIDATES_TOTAL=500`。已接入
    `evolution/autonomous_loop.py::_tick_passive()`（跟
    `IngestionPolicy`/`WatchlistMatcher` 同级、各自独立游标）。
  - **Stage②**（LLM 批量重要性判定，唯一引入 LLM 调用的环节）：判定
    问题明确区分于 `GoalRelevanceEngine`（"是否足够重要/新颖，值得单独
    追踪"而非"是否与已有 Goal 相关"）；prompt 对外部内容做分隔符包裹
    防注入；只有 `importance == "high"` 才写入人工确认队列，
    `medium`/`low` 直接丢弃、不落任何持久化记录。`ensure_
    novelty_importance_judge_job()` 已在 `api/server.py`
    `_build_autonomous_loop()` 里接入（`sys:novelty_importance_judge`，
    默认 10 分钟一次，`llm_helper_provider` 惰性获取）。
  - **人工确认/忽略**：`confirm_novelty_candidate()`——唯一允许创建新
    Goal 的入口，调用 `GoalBacklog.add_goal()` 并
    `attach_external_context()`；`dismiss_novelty_candidate()`——只标记
    `status=dismissed`，不做任何执行动作。
- REST：`GET /v1/external_input/novelty_candidates`、
  `POST /v1/external_input/novelty_candidates/{id}/confirm`、
  `POST /v1/external_input/novelty_candidates/{id}/dismiss`。明确不聚合
  进 `/v1/inbox`——这是独立的"系统主动发现的新方向建议"通道。
- 看板：外部输入 tab 新增"🌟 新颖信号候选"面板（待确认数量 + 展开卡片，
  "✅ 创建目标"/"✖️ 忽略"两个按钮）。
- 测试：`tests/test_external_input_novelty_judge.py`（9 条，覆盖
  Stage①候选生成/排除/去重、Stage②无 helper 时不调用/high 写入/
  medium-low 丢弃、confirm 建 Goal、dismiss 标记、confirm 不存在候选）。

---

## 涉及文件清单

**新增：**
- `src/mini_agent/external_input/poll_history.py`
- `src/mini_agent/external_input/novelty_judge.py`
- `src/mini_agent/archive/__init__.py`
- `src/mini_agent/archive/gc.py`
- `tests/test_external_input_gateway_dedup_persistence.py`
- `tests/test_external_input_poll_history.py`
- `tests/test_archive_gc.py`
- `tests/test_external_input_novelty_judge.py`
- `next_doc/external_input_reliability_observability_archive_plan.md`（方案原文，本次拷入）
- `next_doc/external_input_reliability_observability_archive_implementation_record.md`（本文档）

**修改：**
- `src/mini_agent/storage/paths.py`（新增 6 个 path 属性/方法）
- `src/mini_agent/external_input/gateway.py`（§1 缓存持久化）
- `src/mini_agent/external_input/poller.py`（§1 挂载 + §3 计时/记录）
- `src/mini_agent/evolution/autonomous_loop.py`（§2 Stage① 接入 `_tick_passive`）
- `src/mini_agent/api/server.py`（§2/§4 cron job 注册）
- `src/mini_agent/api/routes.py`（§2/§3/§4 共 5 个新端点）
- `apps/mini_agent_kanban/client.py`（对应 5 个客户端方法）
- `apps/mini_agent_kanban/app.py`（3 个新看板面板：来源健康趋势/新颖信号候选/归档查询）
- `docs/external-input-gateway-guide.md`（§2/§3/§9/§10/§11.2 补充去重持久化/
  可观测性/归档/NoveltyJudge 相关说明与端点）
- `docs/watchlist-notification-guide.md`（新增 §6.4 `NoveltyJudge` 完整说明，
  §8/§10 补充对应端点与文件）

## 测试结果

```
tests/test_external_input_gateway_dedup_persistence.py  6 passed
tests/test_external_input_poll_history.py                6 passed
tests/test_archive_gc.py                                  5 passed
tests/test_external_input_novelty_judge.py                9 passed
（既有回归：external_input_poller/source/policy/watch/channel_p7/
  config/reload/routes_p6、notification_routes_p7）           84 passed
------------------------------------------------------------------
合计                                                      116 passed
```

## 已知限制 / 后续可做

- §2 Stage① 的 `exclude_channels` 目前只支持精确匹配 channel 名，不支持
  通配符/正则；如果后续噪音源变多可以再加。
- §4 `query_archive()` 的 `keyword` 是简单子串匹配，量大后可能需要换成
  更高效的索引方式（目前预期归档量级不大，暂不做）。
- §3 看板趋势图依赖 `pandas`；未安装时会降级为文字列表展示，不阻断功能。
