# 看板 Cron 详情：外部项目任务"有累计运行次数却看不到执行记录"

## 1. 现象

Cron 任务详情（如 `⏰ [外部项目] stock_watch/telegraph_scan`，job id `ext:stock_watch:telegraph_scan`）
里「累计运行次数」为 19，但「执行记录」区域什么都不显示，也没有任何解释。

## 2. 根因（三处叠加）

1. **记录写在别处。** `ext:*` job 的 entrypoint 由
   `external_projects.scheduler._run_entrypoint()` 以子进程方式执行，执行结果写进**外部项目自己的**
   `<项目目录>/.agent/run_status.jsonl` 账本（`external_projects/ledger.py`），
   并不写入 Cron 任务自己的 workspace（`runs/<run_id>.jsonl`）。看板详情里的「执行记录」
   读的正是 workspace（`recent_runs_summary`），所以这类 job 永远是空的。
2. **"累计运行次数"不是"执行完成次数"。** `CronScheduler._trigger_and_record()` 在 `_fire()`
   返回成功（= 已派发给 `CronJobRunner`）时就 `run_count += 1`。任务仍在排队（本例就是
   🟠 排队中）、或项目被禁用 / entrypoint 已从 project.yaml 删除而被静默跳过
   （`_run_external_entrypoint_job` 里直接 return），次数照样增加，但账本不会有记录。
3. **空列表时界面什么都不显示。** `recent_runs_summary` 返回的是 `[]`（不是 `None`），
   原来的 `if recent_runs_summary is not None:` 分支里只处理了"非空"，空列表既不展示记录，
   也不走原本写好的"为什么没有记录"说明（那段说明挂在最后的 `else` 上，空列表走不到）。
   这是所有类型 job 共有的缺口。

## 3. 改动（仅看板，`apps/mini_agent_kanban/app.py`）

- 新增 `_render_external_job_ledger()`：`ext:*` job 的详情改为通过已有接口
  `GET /v1/external_projects/{name}/ledger`（`client.external_project_ledger()`）读取项目账本，
  按本 job 的 `entrypoint` 过滤，**最新在前**展示最近 20 条：成功/失败徽标、本地时间、耗时、触发方式
  （daemon 定时 / 手动 / 外部 OS cron）；失败时显示 `error_summary`，并把子进程输出尾部
  `detail` 放进可展开区（最新一次失败默认展开）。
- 账本里没有记录时，明确解释"累计运行次数按成功派发计，不代表已执行完成"，
  并列出常见原因（仍在排队 / 被静默跳过）；累计次数大于账本条数时同样给出提示。
- 抽出 `_render_cron_no_record_note()`，并补上 `recent_runs_summary == []` 时的说明，
  不再出现"累计运行次数 N、却什么提示都没有"。
- 后端、接口、调度、记账逻辑均**未改动**；`run_count` 的统计口径不变，只是把含义在界面上说清楚。

## 4. 保守的默认值

纯展示层改动：非 `ext:*` 的 job 展示完全不变（只在原来"空白"的情况下多一句说明）。
账本读取失败只显示一行提示，不影响详情页其它部分。无新增配置项。

## 5. 测试

`tests/test_kanban_ext_cron_ledger_view.py`（7 个用例，用记录输出的假 `st` 验证）：
按 entrypoint 过滤且最新在前、失败详情展示、空账本说明、接口报错不抛异常、
累计次数大于账本条数的提示、耗时计算（时区不一致不硬算）、无记录说明按通道区分。

## 6. 本例说明

该 job 当前 🟠 排队中，说明 Cron 并发槽位被占满（见 `kanban_cron_running_queued_display_fix.md`）。
若账本里仍没有它的记录，说明已派发的 19 次里没有一次真正执行并落账，应结合排队 / 槽位占用情况排查。
