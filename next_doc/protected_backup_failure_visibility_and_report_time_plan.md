# 受保护文件备份失败原因可见性 + 待处理汇报显示时间

> 状态：已实施完成。新增/修改文件清单见文末"实施记录"。
> 前置背景：`next_doc/protected_files_manifest_and_delete_guard_plan.md`（受保护文件
> 三层防护）、`next_doc/goal_cron_paused_semantics_and_status_provenance_plan.md`
> （跳过原因分类）。

## 1. 问题

线上告警：

```
🔴 [执行失败] cron job「受保护文件定期备份」长期未能触发
job：受保护文件定期备份（sys:protected_files_backup）  interval:86400
连续跳过：31190 次   上次成功触发：09-04 12:46:37（25 天 18 小时前）
最近一次未触发原因：本地 handler 返回 False，且未给出具体原因（local_handler_returned_false）
```

### 1.1 根因：失败原因被丢弃

`ensure_protected_files_backup_job` 注册的 handler 只做 `return summary.ok`，而
`run_backup_once` 里 `mkdir_failed / backup_failed(<路径>) / manifest_write_failed /
prune_failed` 任意一条错误都会让 `ok=False`。错误内容只留在局部变量 `summary.errors`
里，没有进入 `job.last_skip_reason/detail`，于是告警和看板只能显示兜底原因码
`local_handler_returned_false`——看不出是哪个路径、什么异常。

### 1.2 更严重的副作用：失败重试会清光历史快照

调度器对 `False` 不推进 `next_run_at`，失败的备份从"每天一次"变成"每分钟一次"。而
`run_backup_once` 每次都会新建一个带时间戳的快照目录，**即使本次有错误也照常执行保留
策略**（只留最近 `keep_count=5` 份，不区分成功/残缺）。复现结果：先成功备份一次，再让
某个文件复制失败并每分钟重试，第 5 次重试时那份唯一的成功快照被清理，之后剩下的 5 份
全是残缺快照。也就是说备份任务在失败状态下会**销毁**历史备份，同时每分钟把所有受保护
文件复制一遍。

### 1.3 汇报缺少时间

"待处理汇报"记录里有 `created_at`（汇报落盘时间）和 `occurred_at`（事件发生时间），
但 Streamlit 看板完全不显示时间，React 看板（`mini_agent_kanban_x`）显示的是原始
epoch 数字。分析"这条是什么时候报的"很不方便。React 端还有一个同面板的既有缺陷：
`PendingReportItem` 类型声明的主键是 `id`，而后端记录的主键是 `report_id`，导致
"标记已读"发出的请求路径里是 `undefined`。

## 2. 改动

### 2.1 失败详情（`evolution/protected_files_backup.py`）

- 所有错误带异常类型：`backup_failed(/path/b.txt): PermissionError: [Errno 13] …`。
  `mkdir_failed`/`manifest_write_failed` 现在也带目录路径。
- `BackupSummary.failure_detail()`：`共 N 处失败：① … ② …[；另有 M 处未列出][；已回滚
  本次残缺快照，历史快照未受影响]`，最多逐条列 5 条。
- handler 失败时 `set_skip_reason(job, "protected_backup_failed", detail)`，并写一条
  warning 日志。
- 新原因码 `protected_backup_failed`（`cron_skip_reasons.py`），归 `retry_backoff` 类：
  失败后 `next_run_at` 指数退避（60s、120s、…，上限为一个调度周期即一天），不再每分钟
  重试。
- `last_skip_detail` 上限 300 → 600 字符；`cron_skip_alert` 正文上限 1200 → 1600，
  并在正文末尾新增"本次汇报时间"一行（纯文本渠道也能对上时间）。

### 2.2 不再销毁历史快照

- 本次运行有任何打包错误 → **回滚**自己新建的残缺目录，且**跳过保留清理**。历史完整快照
  原样保留；失败运行不再留下任何目录。
- 回滚本身失败时保留残缺目录，并把 `rollback_failed(...)` 写进 errors，不静默吞掉。
- 缺失核对只拿"有 `manifest.txt` 的最近一份快照"作对比，历史上遗留的残缺目录不再
  干扰结论。
- `prune_failed` 仍算错误（会体现在告警里），但本次新建的完整快照保留。
- `_rmtree_force`：清理旧快照时遇到只读文件（Windows 上 `.git` 对象等）先清除只读位
  再重试，避免旧快照永远删不掉。

### 2.3 待处理汇报显示时间

- `notification/reports_store.py`：`list_pending_reports()` 返回时附带只读的
  `created_at_text`（汇报时间）和 `occurred_at_text`（事件发生时间），格式
  `YYYY-MM-DD HH:MM:SS`（服务端本地时间）；不落盘、不改存储 schema，旧记录缺时间时为空串。
- Streamlit 看板（`apps/mini_agent_kanban/app.py::_render_pending_reports_panel`）：
  折叠标题以 `🕒 <汇报时间>` 开头，展开后正文上方一行 `汇报时间：… 事件发生：…`
  （事件发生时间与汇报时间相同时不重复显示）。
- React 看板（`apps/mini_agent_kanban_x/src/pages/Watchlist/index.tsx`）：折叠标题显示
  汇报时间 + 分类标签 + 标题；展开后同样有汇报时间/事件发生时间一行；顺带修复
  `report_id`/`id` 不一致（改用 `report_id`，兼容旧的 `id`）。

## 3. 兼容性与取舍

- 不新增配置项：改动只作用于"备份失败"这一异常路径，且是纯保护性的（不删好快照、
  不空转重试），没有需要 opt-in 的行为变化。
- 已经被污染的存量备份目录（失败重试期间留下的残缺快照）不会被自动清理，也**无法找回
  已被清掉的完整快照**。升级后下一次成功备份会产生一份完整快照，之后保留策略照常。
  残缺目录（无 `manifest.txt` 的）可手动删除。
- 存量 job 的 `consecutive_skip_count`（31190）在下一次成功后清零；失败时按新规则退避。
- React 看板改动只做了语法检查（`tsc` 转译无诊断），没有跑完整类型检查和构建（仓库
  未安装前端依赖）。

## 4. 排查手册

1. 看板"待处理汇报"里该条 `cron_skip_alert` 的"补充：共 N 处失败：① backup_failed(<路径>):
   <异常类型>: …"就是具体原因。
2. 需要立刻复现：`POST /v1/protected-files/backup`，响应里的 `errors` 是同一份信息。
3. 常见原因对应：`PermissionError`（文件被占用/无权限）、`OSError: [Errno 28]`（磁盘满）、
   `mkdir_failed`（备份目录不可写）、`prune_failed`（旧快照无法删除，多为只读文件/占用）。

## 5. 实施记录

新增文件：
- `next_doc/protected_backup_failure_visibility_and_report_time_plan.md`（本文档）
- `tests/test_protected_backup_failure_detail_and_report_time.py`（19 个用例）

修改文件：
- `src/mini_agent/evolution/protected_files_backup.py`：错误带类型/路径、`failure_detail()`、
  失败回滚 + 跳过清理、`_rmtree_force`、缺失核对只用带 manifest 的快照、handler 写原因。
- `src/mini_agent/evolution/cron_skip_reasons.py`：`protected_backup_failed`（retry_backoff）；
  detail 上限 600。
- `src/mini_agent/evolution/cron_scheduler.py`：告警正文加"本次汇报时间"，上限 1600。
- `src/mini_agent/notification/reports_store.py`：`format_report_time()`、
  `created_at_text`/`occurred_at_text`。
- `apps/mini_agent_kanban/app.py`：待处理汇报面板显示汇报时间。
- `apps/mini_agent_kanban_x/src/pages/Watchlist/index.tsx`、`src/api/types.ts`：显示汇报
  时间，修复 `report_id`。
- `tests/test_cron_skip_alert_detail.py`：正文截断用例改为检查补充说明完整保留。
- 文档：`docs/protected-files-guide.md`、`docs/watchlist-notification-guide.md`。

验证：相关 950 个测试中 16 个失败，全部在原始仓库里同样失败（其中
`test_notification_dispatcher::test_kanban_writes_alert_record` 期望写 alerts.jsonl，
与本次改动无关）；本次新增 19 个用例全部通过。
