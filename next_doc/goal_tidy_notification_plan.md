# Goal tidy 阶段执行通知方案

> 状态：**已实施完成**。
> 触发背景：`goal_output_directory_tidy_enforcement_plan.md` 已经让 tidy
> 阶段"有代码扫描出的问题清单、有收尾核查、有连续不达标上限"，但整个过程
> 对用户是不可见的——tidy 什么时候被触发、这一轮打算解决什么问题、解决
> 得怎么样，用户只能自己翻看 output/ 目录或 notes/ 才能知道。用户希望
> tidy 一旦触发/执行完，就强制通过现有通知系统推一条完整通知，而不需要
> 主动去看。

## 1. 设计目标

一条通知里，讲清楚这几件事：

1. **哪个 Goal** 执行了 tidy（标题里带 `goal.title`，`meta` 里带
   `goal_id`/`cycle`）。
2. **当前的执行规范**（`GoalExecutionSpec` 的压缩摘要：`output_mode`、
   已声明的业务子目录、`hardening_target`）。
3. **触发时刻的目录现状**（`scan_output_structure()` 的扫描结果）。
4. **tidy 的目标**（即触发时刻喂给 agent 的问题清单原文，
   `_build_tidy_problem_checklist()` 的输出——"目标"和"现状"在这套机制
   里是同一份扫描结果的两种呈现，不重复扫描/不重复定义）。
5. **tidy 后的成果**（tidy 轮真正跑完后重新扫描一次，与触发时刻的现状
   做"前 → 后"对比，并给出代码复查结论：是否仍判定为脏乱）。

**强制发送**：只要检测到"这一轮是 tidy 轮"，不论这一轮子 Objective 最终
是 `completed`/`failed`/`cancelled`，也不论 tidy 复查是否通过，都发送——
是否清理干净本身也是通知要传达的信息之一，不作为"发不发"的过滤条件。

## 2. 实现方式：触发时记"纸条"，收尾时取出来拼通知

tidy 的"目标"在触发那一刻就已确定（问题清单 + 当时的扫描现状），但
"成果"必须等这一轮真正跑完、agent 已经动手整理过之后重新扫描才有意义。
两个时间点分别位于：

- **触发时刻**：`goal_cron_bridge._append_output_workspace_context()`
  拼 tidy 阶段 prompt 的那一刻（`elif mode == "tidy":` 分支）。
- **收尾时刻**：`goal_cron_bridge.reap_finished_cycles()` 侦测到这一轮
  对应的子 Objective 进入终态的那一刻（由 `AutonomousLoop` 被动 tick
  周期性调用，触发时刻和收尾时刻之间通常隔着一整轮 agent 执行）。

两者之间没有直接的函数调用关系，因此用一个落盘的小 JSON"纸条"搭桥：

- `output_workspace.write_tidy_notice_pending()`：触发时刻调用，把
  `cycle_no`/`out_dir`/`checklist_text`（tidy 目标 = 触发时的问题清单）/
  `stats_before`（触发时的 `scan_output_structure()` 原始结果）/
  `spec_summary`（执行规范压缩摘要）写入
  `.agent/daemon_run_outputs/goals/<goal_id>/_tidy_notice_pending.json`。
- `output_workspace.read_and_clear_tidy_notice_pending()`：收尾时刻调用，
  读到即删（"一次性纸条"，不管后续通知发送是否成功都不重试，避免发送
  失败导致纸条一直残留、下次又拼出一份过时通知）。
- 同一个 `goal_id` 只保留最新一张纸条（覆盖写）——tidy 是"一次性插入"
  的维护动作，正常不会两轮交叠；万一出现异常交叠，覆盖成最新一轮的记录
  也好过两张纸条互相打架。

收尾时刻在 `_notify_tidy_cycle_result()` 里：

1. 读纸条，读不到（说明这一轮不是 tidy 轮，或已经发送过）直接返回，
   不发送任何通知——这是"要不要发"的唯一判断依据。
2. 重新跑一次 `scan_output_structure()` 拿到"tidy 后"的现状。
3. `output_workspace.format_stats_diff_for_notification()` 把纸条里的
   `stats_before` 和刚扫描出的 `stats_after` 拼成"前 → 后"对比文本
   （`_misc/` 文件数、根目录散落文件、`scripts/` 疑似临时脚本、
   `_archive/` 归档项数，以及复用 `is_output_messy()` 给出的复查结论）。
4. 拼出完整通知正文（执行规范 + 触发时现状/目标 + 前后对比），通过
   现有 `notification/dispatcher.py` 的 `NotificationDispatcher` 发送，
   `source="goal_cycle_tidy"`，`kanban` 渠道恒真兜底（与
   `_notify_cycle_failed`/`_notify_phase_health_issue` 同一套约定）。
5. 挂载点：`reap_finished_cycles()` 里 `record_cycle_completed()` 命中
   后立即调用，与按 `failed`/`completed` 分支各自独立判断的既有通知
   互不干扰（判断维度不同：一个看"是否有纸条"，一个看"终态是什么"）。

## 3. 非目标 / 边界

- 不改变 tidy 阶段本身的触发条件、收尾判定（`tidy_verified`/
  `tidy_max_consecutive_rounds`），只是把已有的判断结果如实转述给用户。
- 不新增扫描逻辑——触发时的 `stats_before` 直接复用
  `_build_tidy_problem_checklist()` 内部已经算过的 `scan_output_structure()`
  调用口径（这里为了不改动该函数签名，接受一次轻量重复扫描）。
- 纸条文件写入/读取失败（磁盘异常等）一律静默跳过，不影响 Goal 触发
  主流程或 `reap_finished_cycles()` 的计数主流程，与本代码库其余"通知是
  感知增强，不能反过来影响主流程"的约定一致。
- 非 recurring（一次性）Goal 和独立 CronJob 不受影响——tidy 阶段本身
  只存在于 recurring Goal 的 `explore/converge/running/tidy` 状态机里。

## 4. 涉及文件

- `src/mini_agent/evolution/output_workspace.py`：新增
  `write_tidy_notice_pending()` / `read_and_clear_tidy_notice_pending()` /
  `format_stats_diff_for_notification()`。
- `src/mini_agent/evolution/goal_cron_bridge.py`：
  - `_append_output_workspace_context()` 的 tidy 分支里落纸条；
  - 新增 `_render_spec_summary_for_notification()`（执行规范压缩摘要）；
  - 新增 `_notify_tidy_cycle_result()`（取纸条 + 重新扫描 + 发通知）；
  - `reap_finished_cycles()` 里挂载调用点。
