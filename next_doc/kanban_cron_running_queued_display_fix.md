# 看板 Cron 计数口径修复：运行中 / 排队中拆分

## 1. 现象

看板"🎛️ 任务并发上限"面板显示 `目标 1/2　Cron 5/2`，而顶栏
"⚙️ daemon 正在执行 2 项任务"里只有 1 个 Goal + 1 个 Cron（每日融合日报
`sys:daily_digest`）。Cron 数字超过上限，也找不到另外几个 Cron 在哪里。

## 2. 根因

不是 Cron 并发管理出错，而是**两处统计口径不一致 + 排队任务没有展示入口**：

| 位置 | 口径 |
|---|---|
| 并发面板 `Cron x/y`（`GET /v1/self/task_concurrency`） | `CronJobRunner.managed_running_count` = 占用槽位 **加** 正在排队等槽位的 managed job 数 |
| 顶栏"正在执行 N 项" | `execution_phase == "running"`，只算已拿到槽位、真正在执行的 job |

`5/2` 实际是 1 个在跑 + 4 个排队。`/v1/self/execution_model` 的 `cron_channel`
早已把 running / queued / unmanaged 分开计数（注释里明确要避免"3/2"假象），只有
`task_concurrency` 这个接口没有同步。顶栏则只取了 running，排队的被丢弃，用户无处查看。

## 3. 改动

### 后端
- `src/mini_agent/evolution/cron_job_runner.py`
  - 新增 `CronJobRunner.managed_job_ids_by_phase()`：在同一把锁内一次返回
    (执行中 managed job id 列表, 排队中 managed job id 列表)，不含 unmanaged；
    判定口径与 `execution_phase()` 一致。
- `src/mini_agent/api/routes.py`
  - `GET /v1/self/task_concurrency` 的 `cron` 字段拆分：
    - `running`：真正在执行的 managed job 数（不再包含排队）
    - `queued`：排队等槽位的数量（新增）
    - `unmanaged_running`：不占槽位的 unmanaged 运行数（新增）
    - `managed_total`：旧口径（占槽位 + 排队），保留供调试（新增）
    - `running_job_ids` / `queued_job_ids`：具体 job id（新增）
  - 旧字段 `current_cap` / `running` 保持存在，`running` 语义变为"真正在跑"。
  - Cron 走旧路径（未启用 CronJobRunner）时各新字段为 `None`，行为不变。

### 看板
- `apps/mini_agent_kanban/app.py`
  - `_render_concurrency_control`：标题改为 `Cron 1/2（排队 4）`；Cron 指标下方显示
    排队数、unmanaged 数，以及执行中 / 排队中的 job id。
  - `_render_daemon_current_tasks`：新增**默认折叠**的
    "🟡 另有 M 个 Cron 任务在排队等待并发槽位（点击查看）"，每条带
    "🔍 查看并控制"按钮，跳转到 Cron 管理 tab 并定位该 job。仅有排队任务、
    没有执行中任务时也会显示。
- `apps/mini_agent_kanban/client.py` 无需改动（直接透传接口返回）。

## 4. 兼容性与默认值

- 保守的向后兼容：新增字段均为追加；旧看板读取 `running` 时只会看到更准确的数字。
- 新增的排队展示区默认折叠，不改变现有顶栏的主体布局；没有排队任务时完全不出现。
- 不改变任何调度、并发上限、排队行为，纯展示/统计口径修复。

## 5. 测试

- 新增 `tests/test_cron_running_queued_split.py`（4 个用例）：复现 1 个运行 + 4 个排队
  不再被算成 5 个运行；unmanaged 不进入任一分组；与 `execution_phase()` /
  `managed_running_count` 口径互相一致。
- 已回归 `tests/test_cron_unmanaged_concurrency.py`，共 20 个用例通过。

## 6. 排查提示

若面板长期显示较大的排队数且槽位里的 job 迟迟不结束，说明不是口径问题，而是
job 卡死或槽位泄漏，可在"📋 执行总览"点"🚨 立即回收卡死任务"，或查看 watchdog 回收记录。
