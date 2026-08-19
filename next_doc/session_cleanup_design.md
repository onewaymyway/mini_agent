# Session 清理功能 — 设计方案与实施记录

## 1. 背景

`.agent/sessions/<session_id>/` 每个 session 一个目录（`meta.json` /
`history.json` / `raw_history.jsonl` / 可能有 `goal_state.json` /
`traces.jsonl`）。长期运行（尤其 daemon 7x24 模式）后目录数量会持续增长，
其中大部分早已用不到。此前只有单个删除 `/session delete <id>`，没有批量
清理手段。

本功能新增：判断哪些 session 还"在用"、批量清理其余的、以及删除前确保
有价值的知识已经被抽取进 wiki/记忆系统，避免清理造成知识损失。

## 2. 关键前提梳理

- **cron 任务不复用主 session**：`evolution/cron_agent_bridge.py` 里
  每次触发都重新构建一个全新 Agent，不跨触发保留 session 历史，"避免和
  用户会话的 session 存储混在一起"。因此 cron 任务本身不会"占用"
  `.agent/sessions/` 下的任何一个目录，清理逻辑不需要为它单独扫描。
- **workflow_sessions/ 是独立目录树**（`.agent/workflow_sessions/`），
  不在 `.agent/sessions/` 下，天然不受本功能影响。
- **Goal Mode 复用 session 持久化**：`goal_state.json` 落在
  `sessions/<id>/goal_state.json` 里，`status ∈ {running, stuck, ...}`
  表示这个目标还挂在这个 session 上（`goal_mode/state.py`）。
- **知识抽取游标是进程内全局单调计数器**（`.agent/extraction_cursor.json`，
  `history/extraction_trigger.py`），只在当前存活进程里，随当前 session
  的 `RawHistory.entries` 增长而推进；进程退出/session 切换后，旧 session
  没有天然的"是否已抽取完"标记——这是本功能新增
  `Session.knowledge_extracted` 字段要补的一块。

## 3. 判定规则

一个 session 满足以下任一条件即视为**受保护**，`/session cleanup` 永远
不会删除它：

1. 当前正在运行的 session（`agent.session_id`，调用方显式传入 `exclude_ids`）；
2. `meta.json.pinned == true`（新增 `/session pin <id>` 手动置顶）；
3. 目录下 `goal_state.json` 存在且 `status ∈ {running, stuck}`（未终结）；
4. 按更新时间排序后最近 `keep_recent_count`（默认 **20**）个（安全网 1）；
5. `updated_at` 在最近 `keep_recent_days`（默认 **30**）天内（安全网 2）。

不满足以上任何一条的进入候选删除，候选删除的 session 还要过一次知识抽取
门槛：

- `turns < min_turns_for_extraction`（默认 **3**）→ 内容太少，无需抽取，直接删；
- `meta.json.knowledge_extracted == true` → 已抽取过，直接删；
- 否则：
  - `extract_first=False`（默认，`/session cleanup` 手动调用时的默认值，
    偏保守）→ 跳过，不删，报告里列为"待抽取"；
  - `extract_first=True`（cron 场景，已与用户确认默认打开）→
    离线跑一次抽取（复用现有的 decision/world extraction pipeline），
    成功则标记 `knowledge_extracted=true` 再删除；失败则跳过，下次重试。

## 4. 实现落点

| 文件 | 改动 |
|------|------|
| `src/mini_agent/session.py` | `Session`/`SessionMeta` 新增 `knowledge_extracted`、`knowledge_extracted_at`、`pinned` 字段；`SessionManager` 新增 `set_pinned()`、`mark_knowledge_extracted()` |
| `src/mini_agent/history_manager.py` | 新增 `is_extraction_caught_up()`（判断抽取游标是否追上当前 session 的 raw_history 末尾）；新增 `dispatch_extraction_for_entries()`（离线抽取入口，复用 `_dispatch_lightweight_extraction`，供旧 session 补跑抽取） |
| `src/mini_agent/agent/lifecycle.py` | `save_session()` 时用 `is_extraction_caught_up()` 的结果刷新 `Session.knowledge_extracted` |
| `src/mini_agent/evolution/session_cleanup.py` **（新增）** | 核心清理逻辑：`scan_sessions_for_cleanup()`（只扫描分类，不执行）、`cleanup_sessions()`（扫描 + 可选执行）、`format_report_lines()`（渲染报告，CLI/cron 共用） |
| `src/mini_agent/cli/commands/sessions.py` | 新增 `/session pin <id>`、`/session unpin <id>`、`/session cleanup [--dry-run] [--keep-days N] [--keep-count N] [--extract-first]` |
| `src/mini_agent/tools/slash_command.py` | `run_slash_command` 工具原本整体拒绝 `session` 前缀（防止自主任务乱切/乱删当前会话）；新增精细白名单，只放行 `session cleanup`/`session pin`/`session unpin` 三个子命令（不涉及切换/清空当前会话身份），`resume`/`new`/`delete`/`save` 仍然拒绝 |
| `src/mini_agent/evolution/cron_scheduler.py` | 新增内置 job `sys:session_cleanup`（`interval:604800`，7 天一次，`task_template` 为 `/session cleanup --extract-first`，默认启用） |

## 5. 安全性设计

- 所有删除最终都走已有的 `SessionManager.delete()`（`shutil.rmtree`），
  `session_cleanup.py` 只负责"算出该删哪些 id"，不直接碰文件系统，
  便于单测/复用/审计。
- `scan_sessions_for_cleanup()` 与 `cleanup_sessions()` 分离：前者纯扫描
  分类，`dry_run` 模式复用同一套判定代码路径，不会出现"dry-run 报告"和
  "实际执行"逻辑不一致的问题。
- 保护规则里 4 项静态判定（exclude/pinned/goal/最近窗口）全部先于知识
  抽取判定执行，`extract_first` 只影响"候选删除"里未抽取过的那一部分，
  不会绕过保护规则误删在用 session。
- `updated_at` 解析失败时保守按"刚刚"处理（不参与删除判定），避免因脏
  数据误删。
- CLI 手动执行默认**不带** `--extract-first`，避免用户第一次尝试就产生
  意料之外的 LLM 调用；cron 定时任务经用户确认后默认带上。

## 6. 已知局限

- 抽取游标 `is_extraction_caught_up()` 是进程内单调计数器，`new_session()`
  / `load_session()` 切换到另一个 session 后不会归零。极端情况下，刚创建
  的小 session 可能被"乐观"地标记为 `knowledge_extracted=true`——不会导致
  误删（这类会话轮次很少，本身也会被 `min_turns_for_extraction` 判定为
  "无需抽取"），但不是一个精确的跨进程/跨 session 抽取完整性证明。
- 旧格式（非目录格式）的 session 文件不支持 `pinned`/`knowledge_extracted`
  标记（读取时统一按默认值 `False` 处理，`cleanup` 会把它们当作候选删除
  的普通旧 session 处理，不会被特殊保留）。

## 7. 冒烟测试记录

在临时目录下用 `SessionManager` 手动构造 7 个 session（当前 / 近期 /
太旧内容少 / 太旧未抽取 / 太旧已抽取 / pinned / goal running），跑
`cleanup_sessions(..., keep_recent_count=1, keep_recent_days=30,
extract_first=False, dry_run=True)`：

```
Session 清理（dry-run，不会实际删除）：共扫描 7 个，保留 4 个，将删除 2 个，待抽取跳过 1 个，失败 0 个。
  [将删除] s_old_big_extracted  hi  — 已抽取过知识
  [将删除] s_old_small  hi  — 内容过少（turns=1 < 3），无需抽取
  [跳过] s_old_big_unextracted  hi  — 尚未抽取知识，且本次未启用 --extract-first，保守跳过
```

`dry_run=False` 实际执行后，`s_current`/`s_recent`/`s_pinned`/
`s_goal_running` 四个目录原样保留，其余按报告删除，行为符合预期。

## 8. 后续可选优化（未实现，供参考）

- `--extract-first` 目前对每个候选 session 各触发一次独立 LLM 调用，
  session 很多时单次 cron 触发耗时可能较长；如需要可以加一个
  "单次最多处理 N 个候选"的批量上限，分多轮 cron 触发慢慢处理完。
- 目前没有为清理动作单独写审计日志（比如 `session_cleanup_log.jsonl`）；
  如果需要事后追溯"哪个 session 什么时候被删的"，可以在 `cleanup_sessions()`
  里加一个可选的落盘记录。

## 9. HTTP API + 看板集成（新增）

原本 `pin`/`unpin`/`cleanup` 只能通过 CLI slash 命令使用，daemon-connected
（多用户/远程）场景下没有对应入口。新增：

| 层 | 改动 |
|------|------|
| `src/mini_agent/api/models.py` | `SessionInfo` 新增 `pinned: bool` 字段（回显 `Session.pinned`）；新增 `SessionCleanupRequest`（`dry_run`/`keep_recent_days`/`keep_recent_count`/`extract_first`）、`SessionCleanupItem`、`SessionCleanupResponse` |
| `src/mini_agent/api/routes.py` | 新增 `POST /v1/sessions/{id}/pin`、`POST /v1/sessions/{id}/unpin`（薄封装 `SessionManager.set_pinned()`）；新增 `POST /v1/sessions/cleanup`（owner only，内部复用 `evolution/session_cleanup.py::cleanup_sessions()`，不重新实现判定逻辑），单用户/多用户两种模式都支持，多用户模式下每个用户只清理自己 `.agent/users/<id>/sessions/` 下的 session；`extract_first=True` 时复用当前 bridge/owner 的 LLM client；调用统一走 `run_blocking()` 丢进线程池（`extract_first=True` 时超时放宽到 180s，避免多次 LLM 调用被 45s 默认超时打断），避免长时间扫描/抽取阻塞事件循环 |
| `apps/mini_agent_kanban/client.py` | `AgentClient` 新增 `pin_session()`/`unpin_session()`/`cleanup_sessions()` |
| `apps/mini_agent_kanban/app.py` | `render_sessions_tab()`：每个 session 卡片新增"🔒保护（防清理）/🔓取消保护"按钮和"🔒已保护"徽标（措辞上刻意避开"固定"，因为看板本身已有一个含义完全不同的"📎并排对比固定"概念，用同一个词会混淆两件事）；分页控件下方新增"🧹 批量清理旧会话"折叠面板：keep-days/keep-count 参数输入、extract_first 勾选、"预览"按钮跑 dry-run 并列出将删除/待抽取跳过/失败三张表，确认勾选后才能点亮"确认执行清理"按钮 |
| `docs/http-api-guide.md` | 新增"Session 清理保护 / 批量清理"一节，含三个端点的请求/响应示例和保留优先级说明 |
| `docs/kanban-dashboard-guide.md` | "大数据量下的分页显示"一节补充说明；使用场景补充"批量清理"入口 |

设计上延续本文档 §5 的安全性原则：所有删除仍然只走
`SessionManager.delete()`，HTTP 层不直接碰文件系统；`dry_run` 默认
`True`（看板按钮比 CLI 更容易误触，比 CLI 默认值更保守一档）；
`extract_first=True` 触发的抽取失败会体现在响应的 `failed` 列表里，
保守跳过删除，不会因为抽取失败而误删有价值的内容。

## 10. 孤儿目录清理（新增）

### 10.1 问题

看板"会话管理"分页统计数量与 `.agent/sessions/` 下实际的目录数对不上
（例如实际 2281 个目录，看板/清理都只看到 1128 / 1127 个）。

根因：`_list_session_entries()`（`session.py`）只认"有 `meta.json`"的目录：

```python
for d in self.session_dir.iterdir():
    if d.is_dir() and (d / "meta.json").exists():
        entries.append((d.stat().st_mtime, d, "dir"))
```

而 session 目录本身在 `RawHistory.set_path()`（`agent/lifecycle.py` 里
`_init_session()`/`load_session()`/`new_session()` 都会调用）时就已经
在磁盘上创建，`meta.json` 却要等一轮对话跑完（`turn_loop.py` 里的
`save_session()` 调用点）才会写入。只要一个 session 一轮都没跑完就
中断——daemon 重启、进程被杀、cron 触发的子 agent 提前失败、goal 循环
半路异常退出——它的目录就会永久停留在"有 `raw_history.jsonl`、没
`meta.json`"的状态，对 `list_sessions()`/`scan_sessions_for_cleanup()`
完全不可见，占磁盘但不出现在任何统计口径里。

### 10.2 判定规则（孤儿目录专用，独立于 §3 的正常 session 判定）

一个"有目录、没 `meta.json`"的目录满足以下任一条件即保留，不删：

1. `exclude_ids`（当前正在使用的 session id）；
2. 目录下 `goal_state.json` 存在且 `status ∈ {running, stuck}`（复用
   `_running_goal_session_ids`，它本身按 `goal_state.json` 扫描，不依赖
   `meta.json`，对孤儿目录同样适用）；
3. **目录年龄 < `orphan_min_age_hours`（默认 6 小时）**——安全网：
   `meta.json` 要等一轮跑完才写，太新的目录很可能是正在进行中的第一轮，
   不是真孤儿。"目录年龄"取目录内所有文件的最新 `mtime`（不能只看目录
   自身 `mtime`——在已存在文件里 append 不会更新父目录的 `mtime`，会把
   "还在持续写"的目录误判成"早就不动了"）。

不满足以上任何一条 → 判定为孤儿目录，候选删除。**孤儿目录没有
`turns`/`knowledge_extracted` 等元信息可读，因此不走知识抽取门槛**——
一轮都没跑完，本身也没有值得抽取的内容。

### 10.3 实现落点

| 文件 | 改动 |
|------|------|
| `src/mini_agent/evolution/session_cleanup.py` | 新增 `OrphanItem`、`scan_orphan_session_dirs()`（只扫描分类）、`cleanup_orphan_session_dirs()`（扫描 + 可选执行删除，走 `shutil.rmtree`）、`DEFAULT_ORPHAN_MIN_AGE_HOURS`；`CleanupReport` 新增 `orphan_kept`/`orphan_deleted`/`orphan_failed`/`orphan_total_scanned`（独立统计，不并入原有 `total_scanned`，向后兼容旧调用方）；`cleanup_sessions()` 新增 `include_orphans`/`orphan_min_age_hours` 参数（默认 `include_orphans=False`）；`format_report_lines()` 追加孤儿目录汇总段落 |
| `src/mini_agent/cli/commands/sessions.py` | `/session cleanup` 新增 `--include-orphans`、`--orphan-min-age-hours N` |
| `src/mini_agent/api/models.py` | `SessionCleanupRequest` 新增 `include_orphans`/`orphan_min_age_hours`；新增 `OrphanCleanupItem`；`SessionCleanupResponse` 新增 `orphan_total_scanned`/`orphan_kept_count`/`orphan_deleted`/`orphan_failed` |
| `src/mini_agent/api/routes.py` | `POST /v1/sessions/cleanup` 透传新增字段给 `cleanup_sessions()`，响应回填孤儿目录结果 |
| `apps/mini_agent_kanban/client.py` | `AgentClient.cleanup_sessions()` 新增 `include_orphans`/`orphan_min_age_hours` 参数 |
| `apps/mini_agent_kanban/app.py` | 清理面板新增"含孤儿目录"勾选框 + "孤儿目录最小年龄（小时）"输入；预览结果新增孤儿目录汇总 + 将删除/失败两张表；"确认执行清理"按钮的启用条件从"有 deleted"改为"有 deleted 或 orphan_deleted" |
| `docs/http-api-guide.md` | `POST /v1/sessions/cleanup` 请求/响应示例补充孤儿目录字段 |
| `docs/kanban-dashboard-guide.md` | "批量清理"入口说明补充孤儿目录一段 |

### 10.4 安全性设计（孤儿目录专用）

- 默认 `include_orphans=False`——不管 CLI、API 还是看板，都要显式打开
  才会扫描/清理孤儿目录，不影响任何已有调用方的默认行为。
- 孤儿目录本身**没有** `pinned`/`goal 状态` 这类元数据可读（没有
  `meta.json`），保护规则退化为"当前 session id + goal_state.json 扫描 +
  最小年龄安全网"三条，其中最小年龄安全网是防止误删"正在跑的第一轮"
  的关键防线，默认给了 6 小时的余量（远超正常一轮对话耗时）。
- 删除动作用 `shutil.rmtree(path, ignore_errors=False)`（显式关闭
  `ignore_errors`，删除失败会被捕获记录进 `report.orphan_failed`，不会
  被静默吞掉）。
- `dry_run` 语义与正常 session 清理完全一致，复用同一个开关。

### 10.5 冒烟测试记录

在临时目录下构造 3 个目录：`s1`（有 `meta.json`，正常 session）、
`s2`（无 `meta.json`，文件与目录 `mtime` 均设为 20 小时前）、`s3`
（无 `meta.json`，刚创建）。跑 `scan_orphan_session_dirs(..., min_age_hours=6)`：

```
s2 delete 有目录无 meta.json（一轮对话未跑完即中断），超过安全窗口，判定为孤儿目录
s3 keep 目录年龄 < 6 小时，可能是正在进行中的第一轮，暂不判定为孤儿
```

`dry_run=False` 实际执行后：`s2` 目录被删除，`s3`（安全网内）和 `s1`
（正常 session，本来就不在孤儿扫描范围内）原样保留，行为符合预期。


