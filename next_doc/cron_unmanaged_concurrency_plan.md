# cron「不占槽位」档位（`concurrency: unmanaged`）改进计划

> 状态：**已完成**（方案经确认后实现，见文末"实施记录"）。
> 关联文档：[external_projects_cron_dispatch_plan.md](external_projects_cron_dispatch_plan.md)、
> [daemon_task_hang_recovery_and_watchdog_hardening_plan.md](daemon_task_hang_recovery_and_watchdog_hardening_plan.md)、
> [docs/cron-dedicated-execution-guide.md](../docs/cron-dedicated-execution-guide.md) §3、
> [docs/external-projects-guide.md](../docs/external-projects-guide.md) §2.2。

## 1. 背景与问题

`CronJobRunner` 的并发槽位（`cron.max_concurrent_jobs`，默认 2；degraded 时收紧到
`cron.degraded_max_concurrent`）设计目的是**控制 LLM 并发**，避免多个 cron 任务同时打满
LLM 调用额度/CPU。

但外部项目 entrypoint（`run_mode="external_entrypoint"`）里有相当一部分是**纯脚本**——抓数据、
算指标、写账本——完全不调 LLM。此前 `_run_job_thread()` 对所有 job 一律先 `_acquire_slot()`，
导致：

- 槽位被两个 LLM 型 job 占满时，不调 LLM 的脚本也只能排队，白白延后；
- degraded 时容量收紧到 1，脚本任务更是被无意义地串行；
- 提交前的 `ResourceArbiter.gating_state()` 仲裁（"用户在场/预算紧张别抢资源"）对脚本同样无意义，
  `blocked` 时脚本会被跳过本次触发。

## 2. 目标与非目标

**目标**：让声明了"不涉及 LLM"的外部项目 entrypoint 直接起独立线程执行，不占 cron 槽位，也不过仲裁。

**非目标**（刻意不做）：

- 不动 `message` / `goal_cycle` 类 job——它们必然涉及 LLM，仍占槽位（即使字段被误写也忽略）。
- 不迁移旧数据，旧 job 行为不变（缺省 `managed`）。
- **不加独立的总量上限**（已与用户确认）：不占槽位的 job 数量只受"同一 job 同时只跑一个实例"与
  各自 `timeout_sec` + watchdog 约束。若日后出现脚本风暴再单独评估。
- 不为 `unmanaged` 引入新的资源维度（网络/磁盘）分档；字段取值保留扩展空间。

## 3. 方案

### 3.1 声明方式（权威来源仍是 `project.yaml`）

```yaml
entrypoints:
  kline_batch:
    cmd: "python entrypoints/run_kline_batch.py"
    schedule: "cron: 0 16 * * 1-5"
    timeout_sec: 900
    concurrency: unmanaged     # 可选；缺省 managed（占槽位、受仲裁，即既有行为）
```

- 字段名 `concurrency`（已确认，不用 `uses_llm: false`，为将来按别的资源分档留余地）；
- 取值 `managed`（默认）/ `unmanaged`，大小写不敏感，其它值在解析时抛 `ProjectManifestError`
  （保守的 opt-in：必须显式写才会生效，写错直接报错而不是静默当成 managed）。

### 3.2 数据流

| 环节 | 改动 |
|---|---|
| `external_projects/manifest.py` | `EntrypointSpec.concurrency`（默认 `managed`）+ 解析/校验 + 常量 `CONCURRENCY_*` |
| `evolution/cron_scheduler.py` | `CronJob.concurrency`（`to_dict`/`from_dict`，旧数据缺省 `managed`）；`upsert_external_entrypoint_job(..., concurrency=)` 新建与原地更新都会同步 |
| `external_projects/scheduler.py` | `ensure_external_project_cron_jobs()` 把 `ep.concurrency` 传入 upsert；yaml 里去掉字段后下次对齐自动回到 `managed` |
| `evolution/cron_job_runner.py` | 见 3.3 |
| `api/routes.py` / 看板 | 运行数统计拆分，见 3.4 |
| `tools/external_projects.py` | `inspect_project` 的 entrypoint 摘要带出 `concurrency` |

### 3.3 `CronJobRunner` 行为

判定函数 `_is_unmanaged(job)`：`run_mode == "external_entrypoint"` **且** `concurrency == "unmanaged"`。

- `submit()`：unmanaged 跳过 `ResourceArbiter.gating_state()` 仲裁；仍做 `already_running` 去重；
  提交时即登记进 `_unmanaged_running`（没有排队阶段，避免 `execution_phase()` 瞬间误报 `queued`）。
- `_run_job_thread()`：unmanaged 不调用 `_acquire_slot()`/`_release_slot()`，不进 `_sem_acquired`，
  不计入 `_held_slots`；仍是独立线程，不堵 tick。
- `execution_phase()`：`_sem_acquired` 或 `_unmanaged_running` 命中即 `running`。
- watchdog（`_reap_one_if_stale()`）：unmanaged 超时同样按 `timeout_sec` + grace 回收，文案为"卡死"
  （而不是"排队超时"），**绝不替它归还槽位**（否则会把别的 job 的槽位还掉）；孤儿线程迟到收尾时
  因 token 不符直接跳过，同样不动槽位。
- `_reconcile_slots()` 槽位账实核对：只比较 `_held_slots` 与 `_sem_acquired`，unmanaged 不在其中，
  不会触发误校正。
- 新增只读属性 `managed_running_count` / `unmanaged_running_count`；`running_count` 语义不变（全部）。

### 3.4 可观测性

- `GET /v1/self/scheduling_overview` 的 `cron_channel`：`running`/`queued` 只算占槽位的 job，
  新增 `unmanaged_running`，避免出现"运行中 3 / 上限 2"的假象；看板对应面板追加一行说明。
- `GET /v1/self/task_concurrency` 的 `cron.running` 改用 `managed_running_count`。
- 账本记录（`ledger`）不变；状态仍显示"运行中"，不会出现"排队中"。

## 4. 保留的保护

1. 同一 job 同一时刻只有一个实例（`already_running` 去重）。
2. `timeout_sec` 子进程超时 + watchdog（`timeout_sec` + `cron.stale_job_watchdog_grace_seconds`）。
3. `project.yaml` 未声明 `timeout_sec` 的 unmanaged entrypoint 子进程不限时、watchdog 退回全局默认——
   **建议所有 unmanaged entrypoint 显式写 `timeout_sec`**。

## 5. 风险与取舍

| 风险 | 说明 / 缓解 |
|---|---|
| 误标：把实际会调 LLM 的脚本标成 unmanaged | LLM 并发控制对它失效。字段为显式 opt-in，默认 managed；文档里明确"只给不调 LLM 的脚本用" |
| 脚本风暴（多个 unmanaged 同时到点） | 不设总量上限是已确认的决定；仅受去重与超时约束，观测看 `unmanaged_running` |
| degraded/blocked 时脚本仍运行 | 预期行为：仲裁目的是保护 LLM 预算/用户体验，脚本不消耗 LLM。若某脚本重 CPU/IO 需要避让，保持 `managed` 即可 |
| 手动触发路径 | `trigger_run()`（CLI/看板「▶️ 手动触发」）本来就不经过 cron 槽位，不受影响 |

## 6. 测试

`tests/test_cron_unmanaged_concurrency.py`（16 个用例）：

- manifest：缺省 managed、`unmanaged` 解析、非法值报错；
- `CronJob` 往返与旧数据缺省；
- `ensure_external_project_cron_jobs()` 同步字段，yaml 去掉字段后回到 managed；
- runner：槽位占满时 unmanaged 立即执行且不动槽位计数；managed 的 external job 仍排队；
  message/goal_cycle 忽略该字段；unmanaged 跳过仲裁而 managed 不跳过；去重仍生效；
  watchdog 回收不误还槽位（含孤儿迟到收尾）；槽位账实核对不误校正；运行数拆分统计。

## 7. 回滚

纯加法改动：删掉 `project.yaml` 里的 `concurrency` 行即回到现状；整体回退只需还原上述文件，
`cron_jobs.json` 里多出的 `concurrency` 字段会被旧版本 `from_dict` 忽略。

## 8. 实施记录

- 已按 §3 实现；新增 16 个用例，相关回归（cron runner/槽位泄漏/仲裁/外部项目/调度概览）全部通过。
- 文档同步：`docs/external-projects-guide.md` §2.2、`docs/cron-dedicated-execution-guide.md` §3.5、
  `.claude/skills/external-project-manager/reference/project_yaml_schema.md` 与 `project.yaml.tmpl`。
- 后续扩展：用户自建的执行命令型 job（`run_mode="command"`）同样适用本档位，见 [cron_command_job_plan.md](cron_command_job_plan.md)。
