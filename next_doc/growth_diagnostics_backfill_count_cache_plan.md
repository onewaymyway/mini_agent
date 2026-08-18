# 成长顾问诊断快照——记忆回填候选数 TTL 缓存 + 手动刷新

- **触发背景**：看板打开「🌱 成长顾问」页面报错：

  ```
  blocking_guard 调用超时（45.0s）：where=growth_diagnostics_snapshot
  ```

- **根因**：`diagnostics_snapshot()`（`evolution/growth_advisor.py`）里
  "记忆回填候选数"这一项统计，调用链是：

  ```
  diagnostics_snapshot()
    → scan_sessions_for_backfill(sm, min_turns_for_backfill=4)
      → session_manager.list_sessions(limit=100000)
        → 遍历全部历史 session（新格式目录 + 旧格式文件），
          对每一个都做一次 stat + 读取 meta.json
  ```

  `list_sessions(limit=100000)` 是同步全量扫描，随 session 总数线性增长，
  没有任何缓存/增量机制——只是一个"给用户一个大概数"的诊断辅助信息，
  代价却是全量磁盘 I/O，长期运行、session 积累到一定数量后必然变慢，
  最终触发 `blocking_guard` 的 45s 超时。

  这次报错本身不影响可用性：`/growth/summary` 路由已有
  `run_blocking(..., fallback=None)` 兜底，超时后返回占位诊断，看板其它
  字段仍正常显示；但每次打开面板都要么等 45s 超时要么看不到诊断信息，
  根因需要修。

## 方案

### ① 进程内 TTL 缓存（简单方法，本轮实施）

`_backfill_candidates_count_cached(paths, force_refresh=False)`：
- 缓存 key 为 `str(paths.project_root)`（不同项目/workdir 的 session
  目录彼此独立）
- 默认 TTL 5 分钟（`_BACKFILL_COUNT_CACHE_TTL_SECONDS = 300`），窗口内
  重复请求直接复用上次扫描结果，不重新触发全量扫描
- `force_refresh=True` 无条件跳过缓存重新计算，并把新结果写回缓存
- 异常仍然静默降级为 0，跟原有行为一致，不改变"扫描失败不拖垮诊断
  面板其余部分"这个既有约定

`diagnostics_snapshot()` 新增 `force_refresh_backfill_count` 参数透传
给上面这个函数；返回值 `memory` 区块新增
`backfill_candidates_count_computed_at`（epoch 秒），供看板判断"这个
数字是多久之前算出来的"。

### ② 手动刷新入口（用户追加需求）

三层透传，默认都不强制刷新（保持缓存优先，跟其它诊断字段一样克制）：

- HTTP：`GET /v1/growth/summary?refresh_diagnostics=true`
- 看板 client：`growth_summary(refresh_diagnostics=True)`（强制刷新走
  50s 超时，略低于服务端 45s blocking_guard 上限留一点余量；默认路径
  仍是 6s，跟原来一致）
- 看板 UI：「🌱 成长顾问」→「🩺 我的数据 / 诊断信息」展开区里
  「🗄️ 记忆回填状态」小节新增「🔄 刷新诊断数据」按钮 + "N 分钟前更新"
  的时间提示；点击后强制刷新（同时把新值写回缓存），`st.rerun()`
  之后页面顶部的正常拉取会直接读到刚刷新出的新值，不需要按钮自己再渲染
  一次结果

## 不做的事（本轮刻意不做）

- 不缓存诊断快照里的其它字段（`memory_store.all_entries()`、
  `_feedback_pattern_diagnostics_summary()` 等）——这些量级通常远小于
  session 总数，不是本次报错的直接原因，等有实际证据表明它们也慢了
  再单独评估。
- 不改 `scan_sessions_for_backfill()`/`list_sessions()` 本身的实现
  （比如加索引/增量扫描）——那是更大的架构改动，缓存已经能把"每次打开
  面板都全量扫描"降到"5 分钟一次"，量级足够小的话就不需要动这块。
- 缓存 TTL（5 分钟）暂不做成配置项，先观察默认值是否合适。

## 实施状态

| 内容 | 状态 | 涉及文件 |
| --- | --- | --- |
| `_backfill_candidates_count_cached()` TTL 缓存 + `diagnostics_snapshot()` 接入 | ✅ 已实现 | `src/mini_agent/evolution/growth_advisor.py` |
| HTTP `GET /v1/growth/summary?refresh_diagnostics=true` | ✅ 已实现 | `src/mini_agent/api/routes.py` |
| 看板 client `growth_summary(refresh_diagnostics=True)` | ✅ 已实现 | `apps/mini_agent_kanban/client.py` |
| 看板「🔄 刷新诊断数据」按钮 + 更新时间提示 | ✅ 已实现 | `apps/mini_agent_kanban/app.py` |
| 单元测试（5 个新用例：TTL 内复用缓存、强制刷新绕过缓存、时间戳存在且合理、强制刷新后普通调用复用新值、不同 project_root 缓存互相隔离） | ✅ 全部通过 | `tests/test_growth_diagnostics_backfill_count_cache.py` |
