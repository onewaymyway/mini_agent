# cron「执行命令」型 job（`run_mode="command"`）改进计划

> 状态：**已完成**（方案经确认后实现，见文末"实施记录"）。
> 关联文档：[cron_unmanaged_concurrency_plan.md](cron_unmanaged_concurrency_plan.md)、
> [docs/cron-dedicated-execution-guide.md](../docs/cron-dedicated-execution-guide.md) §3.6、
> [docs/http-api-guide.md](../docs/http-api-guide.md)、[docs/commands-and-tools-reference.md](../docs/commands-and-tools-reference.md)。

## 1. 背景与问题

此前用户创建的 cron 只有 agent 任务型（`run_mode="message"`，`task_template` 是交给 agent 的自然语言指令）。
能直接跑命令的 `external_entrypoint` 只能由外部项目的 `project.yaml` 自动注册，依赖项目注册表/manifest/账本，
不适合"一条命令定时跑"。想定时跑一个 python 脚本，只能让 agent 去调 bash：耗 LLM、占槽位、结果不确定。

## 2. 目标与非目标

**目标**：用户可以创建「到点直接执行 shell 命令」的 cron，可选不占槽位、不过仲裁，结果进现有执行记录/看板。

**非目标**：
- 不让 agent 创建命令型（见 §5）；不做 cwd 白名单（命令本身已是任意 shell）；
- 不迁移旧数据（新字段缺省值即旧行为）；
- 不为命令型引入新的执行记录存储，复用 job workspace。

## 3. 已确认的决策

| # | 问题 | 决定 |
|---|---|---|
| 1 | 默认 `concurrency` | **`managed`**（保守 opt-in），需要不占槽位时显式 `unmanaged`；非法值报错而不是静默当 managed |
| 2 | `timeout_sec` | 缺省 600（`cron.command_default_timeout_seconds`），上限 3600（`cron.command_max_timeout_seconds`），**超限直接拒绝**不截断 |
| 3 | `cwd` | 任意**已存在**的目录（存绝对路径），不加白名单 |
| 4 | agent 能否创建 | **不可以**，`/cron add-cmd` 加入 `run_slash_command` 拒绝列表 |
| 5 | 输出保留 | 每次运行 stdout/stderr 各保留尾部 4096 字节（`cron.command_output_tail_bytes` 可配） |

## 4. 方案

| 环节 | 改动 |
|---|---|
| `config/models.py` | `CronConfig` 新增 `command_default_timeout_seconds`/`command_max_timeout_seconds`/`command_output_tail_bytes` |
| `evolution/cron_scheduler.py` | `CronJob` 新增 `command`/`cwd`/`timeout_sec`（`to_dict`/`from_dict`，旧数据缺省 `""`/`""`/`None`）；`_normalize_command_fields()` 统一校验；`add_command_job()`、`update_command_job()`；`add_user_feedback()`/`update_task_template()` 对命令型返回 False |
| `evolution/cron_job_runner.py` | `_is_unmanaged()` 从仅 `external_entrypoint` 扩到 `{external_entrypoint, command}`；`submit()` 为命令型登记 `_timeout_override=timeout_sec`（watchdog 以此 + grace 为准）；`_run_job_thread()` 分派到 `_run_command_job()` |
| `_run_command_job()` | `win_subprocess.Popen(shell=True)`，stdout/stderr 重定向到 `runs/` 临时文件（结束后只读尾部并删除，不占内存）；POSIX 用独立会话、超时 `killpg`，Windows 用 `taskkill /T /F`，杀整棵进程树；注入 `MINI_AGENT_CRON_JOB_ID`；结果写 `state.json` 与 `runs/<id>.jsonl`，事件类型与 agent 型对齐（`run_started`/`step_error`/`timed_out`/`run_finished`）外加 `command_output`，因此 `recent_runs_summary()`/看板现有展示直接可用 |
| `api/routes.py` | `POST /v1/cron/jobs` 支持 `run_mode:"command"`（校验失败 400）；`PUT` 支持 `command`/`cwd`/`timeout_sec`/`concurrency`（先整体校验再改，失败不产生部分修改）；`/feedback` 对命令型返回 400 |
| `cli/commands/cron.py` | `/cron add-cmd <name> <schedule> <command...> [--cwd X] [--timeout N] [--unmanaged]`；`cron:` 表达式按 5 段重新拼合 |
| `tools/slash_command.py` | `_is_denied()`/`run_slash_command` 拒绝 `cron add-cmd`（精确匹配"首词+子命令"，不拦整个 `/cron`） |
| 看板（React `mini_agent_kanban_x`） | 新建表单加「Agent 任务/执行命令」切换及命令字段；列表显示类型/不占槽位标记与命令；详情抽屉对命令型隐藏 Prompt 编辑，事件列表展示输出尾部；命令型隐藏「提意见」 |
| 看板（Streamlit `mini_agent_kanban`） | Cron tab 新建表单加类型切换与命令字段（`client.add_cron_command_job`）；卡片显示命令/目录/超时；详情弹窗对命令型隐藏 Prompt 编辑与「提意见」；Goal 页的旧"新建 Cron Job"表单仅加指向 Cron tab 的提示 |

## 5. 风险与取舍

| 风险 | 缓解 |
|---|---|
| agent 借 cron 绕过 bash 工具的权限审批 | 创建只走 owner 的 REST/CLI/看板；`/cron add-cmd` 在 agent 工具路径被拒；`PUT` 改命令同样仅 owner REST；反馈/调优通道对命令型一律拒绝 |
| 命令继承 daemon 环境变量（含 API key） | 与外部项目 entrypoint 一致，文档明示 |
| Windows 下 `shell=True` 是 `cmd.exe` | 文档、CLI 用法与看板提示均写明 |
| 超时只杀 shell 会留孤儿进程 | 杀整棵进程树，用例验证孙进程被终止 |
| managed 命令在仲裁 blocked 时被跳过 | 预期行为（默认保守）；要无条件执行显式 `unmanaged` |
| CLI 按空白分词，复杂引号会走样 | 文档提示改用 REST/看板 |
| 不占槽位的命令数无上限 | 沿用 unmanaged 既有决定，受去重 + `timeout_sec` 约束 |

## 6. 测试

`tests/test_cron_command_job.py`（28 个用例）：序列化往返与旧数据缺省；创建/修改校验（空命令、非法 concurrency、
超限/非正/布尔 timeout、cwd 不存在、失败不产生部分修改）；重载持久化；反馈/调优不能改写命令；
runner：成功/非 0 退出（连续失败累加）/超时杀进程树（孙进程不存活）/输出尾部截断且临时文件清理/cwd 与环境变量注入/
cwd 创建后被删的失败路径/unmanaged 不占槽位而 managed 仍排队/watchdog 超时覆盖/同 job 去重；
`run_slash_command` 拒绝 `cron add-cmd` 而放行 `cron add`/`cron list`；CLI `add-cmd` 解析（含 cron 5 段与错误提示）；
REST 创建/修改/反馈守卫（含 400/404）。

回归：cron/slash/kanban/scheduler 相关 884 个用例中，13 个失败与 2 个收集错误在原始代码上同样存在
（goal 执行规范路由、kanban 拖拽、notification dispatcher、`test_session`、`test_stock_watch_optimization_loop_e2e`），与本次无关。

## 7. 回滚

纯加法：不创建命令型 job 即与现状完全一致。整体回退还原上述文件即可；`cron_jobs.json` 中多出的
`command`/`cwd`/`timeout_sec` 字段会被旧版本 `from_dict` 忽略（但旧版本遇到 `run_mode="command"` 的 job 会
当 agent 任务型处理且 `task_template` 为空，回退前请先删除命令型 job）。

## 8. 实施记录

- 已按 §4 实现并通过 §6 的测试。
- React 看板（`apps/mini_agent_kanban_x`）已跑 `tsc -b`：本次改动文件无类型错误；仅剩一条原始代码里就存在的
  `src/App.tsx` 找不到 `./pages/Sessions` 的报错，与本次无关。未做浏览器端到端验证。
- Streamlit 看板（`apps/mini_agent_kanban`）仅做了语法检查，未做浏览器端到端验证。
- 文档同步：`docs/cron-dedicated-execution-guide.md` §3.5/§3.6/§7、`docs/http-api-guide.md`、
  `docs/commands-and-tools-reference.md`、`docs/cron-jobs-reference.md`、`CLAUDE.md`。
