# 看板 Cron 删除一致性修复（kanban_cron_delete_consistency_bugfix）

## 背景 / 问题现象

用户反馈两个问题：

1. 在看板"📌 目标看板"Tab 删除 cron job，点击确认后提示删除成功，但刷新
   看板后该 job 又出现了，看起来像没删掉。
2. 删除入口只在"📌 目标看板"Tab 有，"⏰ Cron 任务"Tab（专门展示 cron job
   执行状态/进度/prompt 编辑的那个 Tab）没有对应的删除按钮，用户想删一个
   job 还得先切换 Tab，体验割裂。

## 根因排查

对比 `src/mini_agent/api/routes.py` 里 `/cron/jobs` 相关的四个路由，发现
获取 `CronScheduler` 实例的兜底逻辑并不一致：

- `GET /cron/jobs`（`list_cron_jobs`）：优先取
  `http_server.bridge._cron_scheduler`，为 `None` 时兜底取
  `http_server.autonomous_loop._cron_scheduler`。
- `POST /cron/jobs`（`add_cron_job`）、`PUT /cron/jobs/{id}`
  （`update_cron_job`）、`DELETE /cron/jobs/{id}`（`delete_cron_job`）：
  三者都只写了 `cs = getattr(http_server.bridge, "_cron_scheduler", None)`，
  **没有**兜底到 `autonomous_loop`。

模块里其实已经有一个 `_get_cron_scheduler(http_server)` 辅助函数封装了
"bridge 优先、autonomous_loop 兜底"这套逻辑（原本只给
`/cron/jobs/{id}/workspace` 等几个新路由用），但 `list/add/update/delete`
这四个最基础的路由里，只有 `list` 手写了一份等价逻辑，其余三个都没跟上。

结果：在 `bridge._cron_scheduler` 为 `None`、真正生效的调度器实例挂在
`autonomous_loop._cron_scheduler` 上的部署形态下，`GET` 能通过兜底读到
完整的 job 列表，但 `DELETE`/`PUT`/`POST` 因为拿不到 `cs`（`cs is None`）
要么直接返回 503（被 kanban 前端捕获成 `_error` 提示，但因为
`st.rerun()` 之后 `GET` 仍然读到未被修改的 autonomous_loop 那份数据，用户
观感上就是"删除失败/刷新后又出现了"）。这就是本次 bug 的根因：**四个
路由对"当前生效的 CronScheduler 是哪个实例"的判断口径不统一**。

## 修复内容

### 1. `src/mini_agent/api/routes.py`

`add_cron_job` / `update_cron_job` / `delete_cron_job` 三个路由里手写的
`cs = getattr(http_server.bridge, "_cron_scheduler", None)`（无兜底）全部
替换为统一调用 `cs = _get_cron_scheduler(http_server)`；`list_cron_jobs`
本身的兜底逻辑也顺手改成直接调用同一个辅助函数，避免以后再出现"多份
拷贝、改一处忘了改另一处"的问题。四个路由现在永远读写同一个
`CronScheduler` 实例，不会再出现"GET 看到的和 DELETE 操作的不是同一个
对象"的情况。

### 2. `apps/mini_agent_kanban/app.py`

`render_cron_jobs_tab()`（"⏰ Cron 任务"Tab）里，在"🔢 调整优先级"
expander 之后新增一段删除 UI，与"📌 目标看板"Tab（`app.py` 里
`new_cron_job` 表单下方那段）保持完全一致的交互：

- `sys:` 前缀（或后端下发的 `is_system=True`）的内置 job 不展示删除
  按钮，只提示"系统内置任务，不可删除，只能禁用"。
- 普通自定义 job 点击"🗑️ 删除"后进入二次确认态（用
  `cron_tab_confirm_delete_<job_id>` 这个 `session_state` 标记），必须
  再点"⚠️ 确认删除"才真正调用 `client.delete_cron_job(job_id)`；也提供
  "取消"按钮退出确认态，避免误触直接删掉。

## 涉及文件

- `src/mini_agent/api/routes.py`（`list_cron_jobs` / `add_cron_job` /
  `update_cron_job` / `delete_cron_job` 四个路由，统一走
  `_get_cron_scheduler()`）
- `apps/mini_agent_kanban/app.py`（`render_cron_jobs_tab()` 新增删除 UI）
- `docs/cron-jobs-reference.md`（新增 §5.1 "删除 job（HTTP API / 看板）"）
- `docs/kanban-dashboard-guide.md`（Tab 一览表 + "⏰ Cron 任务 Tab"小节
  补充删除说明）
- 本文档

## 验证

- `python3 -m py_compile apps/mini_agent_kanban/app.py
  src/mini_agent/api/routes.py` 通过。
- 手工走查：四个路由现在共用同一份 `_get_cron_scheduler()` 兜底逻辑，
  不再存在"GET 用 A 实例、DELETE 用 B 实例"的分叉路径。

## 后续建议（未在本次改动范围内）

- 如果之后要新增别的 `/cron/jobs/...` 路由，一律直接调用
  `_get_cron_scheduler(http_server)`，不要再手写 `getattr(http_server.
  bridge, "_cron_scheduler", None)` 这种局部简化版。
- 如果想彻底杜绝"两处实例不一致"这类问题的其它变种，可以考虑把
  `CronScheduler` 实例的持有方收敛成只有 `autonomous_loop` 一处，
  `bridge._cron_scheduler` 改成一个转发 `autonomous_loop._cron_scheduler`
  的 `@property`，而不是两边各存一份引用——但这个改动面更大，建议单独
  立项评估，不在本次 bugfix 里顺手做。
