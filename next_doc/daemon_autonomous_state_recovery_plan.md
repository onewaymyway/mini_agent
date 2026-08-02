# Daemon 自主任务错误状态识别与恢复改进计划

> 背景：daemon 自主任务（`autonomous` objective 执行 / `cron` 周期任务）偶尔会进入
> "把未解析成功的 `<tool_use>` 原始协议文本当作步骤结果"的错误状态，并把这段脏内容
> 当作"前序步骤结果"继续喂给后续步骤，导致状态越跑越歪，且目前没有自动/半自动的
> 重置手段。同时，所有自主任务与真人交互共用同一个 Agent Session，容易互相污染
> 上下文。本文档记录根因分析、四项改进的设计方案，以及分阶段实施记录。

## 0. 根因回顾

- `agent/turn_loop.py::_agentic_loop()` 在两条路径下可能把"半成品/畸形"的
  `response.text`（可能是没解析成功的 `<tool_use>...</tool_use>` 原始协议文本）
  当作 `final_text` 返回：
  1. 格式纠错重试用尽（`format_correction_retries >= max_retries_per_turn`）后
     仍未获得合法输出，直接 `break`；
  2. 命中 `max_turns` 硬顶被迫跳出循环时，`final_text` 停留在"最后一次带工具调用
     的 response.text"上（工具调用轮的 `response.text` 在文本协议模式下可能就是
     `<tool_use>` 块本身）。
- `api/server.py` 里 `result = bridge.agent.run_turn(...)` 之后，只取结果首行/前
  200 字，无校验地传给 `ObjectiveExecutor.on_turn_done()`。
- `evolution/objective_executor.py::on_turn_done()` 无条件把 `step.status` 置为
  `"done"`，并把 `result_summary` 写入 `step`；随后 `_build_prompt()`（约第 1162
  行）把所有前序步骤的 `result_summary` 原样拼进下一步 prompt——脏内容被当作"事实"
  一路传递下去。
- 所有 `initiator in ("user", "autonomous", "cron")` 的 turn 目前共用同一个
  `bridge.agent`（同一个 Session/对话历史），自主任务之间、自主任务与真人交互之间
  没有上下文隔离。

## 1. 四项改进设计

### P0-A　结果健全性校验（产出侧 + 消费侧）

- **产出侧**：`_agentic_loop()` 返回前，对 `final_text` 做一次"是否仍是畸形协议
  文本"的校验（复用 `perception/format_correction_detector.py::detect_format_issue()`，
  外加"内容为空"判断）。命中时不直接把脏文本当结果返回，而是：
  - 返回一个明确标注"本轮未获得有效回复"的哨兵文本；
  - 在 agent 实例上设置 `self._last_turn_result_invalid = True` 及
    `self._last_turn_invalid_reason`，供上层判断。
- **消费侧**：`on_turn_done()` 新增 `valid: bool = True` 入参。当 `valid=False`
  时：
  - 不把内容写入 `step.result_summary`，改写为结构化的"本步骤结果无效，已重试"
    说明；
  - **不**推进 `current_step_idx`，按现有 `retry_count`/`MAX_STEP_RETRIES` 机制
    重新提交同一 step（与 `on_turn_failed()` 的重试路径一致），而不是错误地
    `status="done"` 继续往下走。
- 配置项：`FormatCorrectionConfig.result_sanity_check_enabled`（默认 `True`），
  关闭时完全回退到升级前行为（一键回退）。

### P0-B　Step 级重置能力

- `ObjectiveExecutor` 新增 `reset_step(exec_id, step_idx, reason)`：清空该 step
  的 `result_summary`/`artifacts`/`turn_id` 映射，`status` 置回 `pending`，
  `current_step_idx` 拨回该步；重新提交时在 prompt 里显式声明"前序结果已重置，
  以下为更正后的上下文"，避免模型继续沿用已被污染的记忆。
- 触发来源：
  1. 自动——P0-A 判定结果无效且重试到达上限时自动调用；
  2. 人工——新增 CLI 子命令 `/goals reset-step <exec_id> <step_idx> [reason]`。

### P1　自主任务独立上下文

- 区分"交互式主 session"（真人 `user` turn，维持长期累积上下文）与"自主任务
  session"（`autonomous`/`cron`）。
- 每个 Objective 执行、每次 cron job 触发时，创建一个独立的临时 Session/Agent
  实例，只携带任务描述 + 必要的记忆检索结果，不复用主线完整历史；任务结束后
  归档/丢弃，不污染主 session。
- 跨任务需要保留的信息已经由 `ObjectiveExecution`/`GoalState` 等结构化状态承载，
  不依赖"共享同一段对话历史"传递。

### P2　看护模式（GuardianRunner）

- 抽取一个不依赖 `GoalSpec`/`GoalJudge`（不要求验收标准）的轻量监督层，复用
  `goal_mode` 里已经和"验收判定"解耦的能力：`StuckDetector`、`ProgressTracker`、
  分级 compact、dead-end 持久清单。
- 用于 `autonomous`/`cron` 任务执行过程中的"卡住检测 → 恢复 → 必要时终止"，
  不做 DONE/CONTINUE 裁定，改为"执行完预定步骤 / 达到最大轮次 / 多次恢复无效"
  等客观终止条件。
- 默认关闭（`cfg.daemon.guardian_mode_enabled`），先在 `autonomous`/`cron` 任务
  上灰度。

## 2. 优先级与阶段划分

| 阶段 | 内容 | 状态 |
|---|---|---|
| 阶段一 | P0-A 结果健全性校验 | ✅ 已实现 |
| 阶段二 | P0-B Step 级重置能力（自动 + 手动命令） | ✅ 已实现 |
| 阶段三 | P1 自主任务独立上下文 | ✅ 已实现 |
| 阶段四 | P2 看护模式 GuardianRunner | ✅ 已实现 |

后续每完成一个阶段，在下面"实现记录"追加对应小节，并把上表状态更新为
"✅ 已实现"。

---

## 实现记录

### 阶段一：结果健全性校验

已按设计落地：

- `perception/format_correction_detector.py` 新增公开函数
  `is_valid_final_result(text) -> bool`：复用既有的 `detect_format_issue()`
  规则表，额外判定"空文本"为无效，不新增检测规则本身。
- `config/models.py::FormatCorrectionConfig` 新增字段
  `result_sanity_check_enabled: bool = True`。
- `agent/turn_loop.py::_agentic_loop()`：
  - `run_turn()` 一开始重置 `self._last_turn_result_invalid = False` /
    `self._last_turn_invalid_reason = ""`；
  - 在 `return final_text` 前做最后一道校验：`result_sanity_check_enabled`
    开启且 `is_valid_final_result(final_text)` 为 `False` 时，把 `final_text`
    替换成一段明确标注"本轮未获得有效回复"的哨兵文本，并置位
    `_last_turn_result_invalid`/`_last_turn_invalid_reason`。
  - 该校验统一覆盖两条已知的脏结果路径（格式纠错重试用尽 / 命中
    `max_turns` 硬顶跳出循环），不需要在两处分别打补丁。
- `api/server.py`：`_main_loop` 里 `on_turn_done` 回调新增
  `valid=not getattr(bridge.agent, "_last_turn_result_invalid", False)` 透传。
- 回退方式：`cfg.format_correction.result_sanity_check_enabled=False` 时
  行为与升级前完全一致。
- 新增测试：`tests/test_daemon_autonomous_state_recovery.py::TestIsValidFinalResult`
  （4 个用例：正常文本判定有效、空文本判定无效、未闭合 `<tool_use>` 判定
  无效、标签角色混淆判定无效）。

### 阶段二：Step 级重置能力

已按设计落地：

- `evolution/objective_executor.py`：
  - `on_turn_done()` 新增 `valid: bool = True` 入参；`valid=False` 时不再
    无条件把 `step.status` 置为 `"done"`，而是转发给新增的
    `_handle_invalid_step_result()`。
  - `_handle_invalid_step_result()`：复用 `on_turn_failed()` 同一套"重试
    未超限则重新提交 / 超限则尝试重新分解 / 再不行则判 Objective failed"
    的逻辑，失败原因固定为"结果健全性校验未通过"。**不会**清空/覆盖
    `step.result_summary` 之外的历史记录，也**不会**推进
    `current_step_idx`。
  - 新增 `reset_step(exec_id, step_idx, reason="") -> bool`：清空目标 step
    及其之后所有 step 的 `result_summary`/`artifacts`/`turn_id`/
    `retry_count`/`error_msg`/`finished_at`；把 `current_step_idx` 拨回该
    step；通过 `step.pending_guidance` 注入"本步骤已被重置，请忽略此前
    看到的旧结果"的说明（复用现有 `_build_prompt()` 里对
    `pending_guidance` 的拼装逻辑，不需要新增 prompt 拼装代码路径）；
    立即调用 `_submit_step()` 重新提交。
- `api/routes.py` 新增 `POST /v1/objectives/{execution_id}/steps/{step_index}/reset`
  （body 可选 `{"reason": str}`），复用既有的 `_objective_executor_or_404()`
  鉴权/取实例逻辑，与 `/retry`、`/guidance` 风格一致。
- `cli/commands/goals.py` 新增 `/agent goals reset-step <exec_id> <step_idx> [reason]`
  子命令：优先直接调用本进程内 `agent._objective_executor`（非 daemon 场景），
  否则回退到通过 `DaemonClient` 发起上面的 HTTP 请求。
- 新增测试：`tests/test_daemon_autonomous_state_recovery.py`
  - `TestOnTurnDoneInvalidResult`（3 个用例）：无效结果不推进/不写脏数据且
    触发重试；重试耗尽后进入失败态；`valid=True`（默认）时行为与升级前
    完全一致（回归保护）。
  - `TestResetStep`（3 个用例）：重置已完成的 step 后清空自身与后续 step
    进度并带着"已重置"说明重新提交；未知 execution/越界 step_idx 均返回
    `False`。
- 验证：`python3 -m pytest tests/test_daemon_autonomous_state_recovery.py`
  10 个用例全部通过；另跑了
  `test_format_correction_detector.py` / `test_objective_executor_kanban_tracks*.py`
  / `test_objective_executor_adaptive_concurrency.py` 等既有套件，未发现由
  本次改动引入的新增失败（`test_format_correction_integration.py` 与
  `test_objective_executor_kanban_tracks_r3/r4.py` 的少量失败在改动前的
  干净代码副本上同样存在，确认是环境/依赖版本问题，与本次改动无关）。

### 阶段三：自主任务独立上下文

已按设计落地，复用了 cron 任务已有的"独立 Agent 实例"模式
（`evolution/cron_agent_bridge.py`），没有重新发明一套：

- 新增 `evolution/objective_agent_bridge.py`：
  - `build_objective_agent(base_cfg, objective_title, execution_id,
    inner_max_turns=None) -> Agent`：与 `cron_agent_bridge.build_cron_agent()`
    结构保持一致（`auto_approve=True`，registry 留空回退到全局默认工具集，
    独立 `system_extra` 声明"这是无人值守的独立会话，只能依赖本条消息里的
    结构化上下文，不要假设记得任何未出现在消息中的内容"）。
  - `ObjectiveIsolatedRunner`：可以直接赋给 `ObjectiveExecutor._submit_fn`
    的 drop-in 替代——`submit(message, initiator, meta) -> turn_id` 签名
    与既有的 `_obj_submit` 完全一致。内部用 `ThreadPoolExecutor` 管理并发
    （`cfg.autonomy.objective_isolated_max_workers`，安全阀，不是主要并发
    控制手段——真正的并发上限仍由 `max_concurrent_objectives_cap` 等既有
    机制决定），每个 step 提交后在专属线程里：构建全新 Agent → 跑
    `run_turn()` → 复用 P0-A 的 `is_valid_final_result()` 做二次确认
    （`agent._last_turn_result_invalid` 已经在 `run_turn()` 内部判过一次，
    这里只是防御性兜底）→ 通过 `on_done`/`on_failed` 回调把结果交回
    `ObjectiveExecutor.on_turn_done()`/`on_turn_failed()`（签名完全一致，
    直接传方法引用即可，不需要 `ObjectiveExecutor` 关心"这个 turn 是不是
    隔离上下文跑的"）。执行完毕（无论成功/失败）立即丢弃这个 Agent 实例，
    不跨 step 复用、不保留对话历史——"上一步做到哪了"完全靠
    `_submit_step()` 已有的 `[前序步骤结果]`/`[前序步骤产出文件]` 结构化
    摘要拼接传递。
  - `shutdown(wait=False)`：daemon 优雅关闭时停止接受新 step，不强行打断
    正在跑的线程。
- `config/models.py::AutonomyConfig` 新增三个字段：
  `objective_isolated_context_enabled: bool = False`（默认关闭，按需灰度）、
  `objective_isolated_inner_max_turns: int = 15`、
  `objective_isolated_max_workers: int = 4`。
- `api/server.py::HttpServer._build_autonomous_loop()`：`objective_executor`
  构造并 `load()` 完毕后，若开关开启，构造 `ObjectiveIsolatedRunner`（回调
  直接指向 `objective_executor.on_turn_done`/`on_turn_failed`），把
  `objective_executor._submit_fn` 换成 `isolated_runner.submit`——必须放在
  `objective_executor` 构造之后接线，因为回调需要引用这个尚未创建时不存在
  的对象；不开启时行为与升级前完全一致（一键回退）。
  `HttpServer.stop()` 里新增对 `_objective_isolated_runner.shutdown(wait=False)`
  的调用，与 `_session_pool.stop_all()` 同一批优雅关闭逻辑。
- 回退方式：`cfg.autonomy.objective_isolated_context_enabled=False`
  （默认值）时，`_submit_fn` 保持原来提交进 Self 共享 `bridge.input_queue`
  的路径，行为与升级前完全一致。
- 新增测试：`tests/test_daemon_autonomous_state_recovery.py::TestObjectiveIsolatedRunner`
  （7 个用例）：成功结果触发 `on_done(valid=True)`；判定无效的结果触发
  `on_done(valid=False)`（而不是当作真实结果）；`run_turn()` 抛异常触发
  `on_failed`；构建 Agent 失败触发 `on_failed`；`shutdown()` 后再
  `submit()` 返回 `None`；`ObjectiveExecutor._submit_fn` 可以被
  `ObjectiveIsolatedRunner.submit` 直接替换（接线验证）。
- 验证：`python3 -m pytest tests/test_daemon_autonomous_state_recovery.py`
  16 个用例全部通过；`test_objective_executor_kanban_tracks_r3/r4.py` 的
  失败在改动前的环境（缺 `uvicorn` 依赖）里同样存在，与本次改动无关。

### 阶段四：看护模式 GuardianRunner

已按设计落地，复用了 `goal_mode` 里已经和"验收判定"解耦的
`StuckDetector`/`ProgressTracker`（`role_agents/stuck_detector.py`），没有
重新发明相似度比较/恢复额度计数逻辑：

- 新增 `evolution/guardian.py::GuardianRunner`：
  - `observe_step(step_idx, result_summary, progress_score=None) ->
    StuckSignal`：内部用一个专属的 `StuckDetector` 判定"这一步结果是否
    和最近几步高度相似"；额外提供 `progress_score` 时，同时喂给内部的
    `ProgressTracker` 识别"平缓但非零"的伪进展趋势，命中时通过
    `StuckDetector.trigger_recovery()` 复用同一份恢复额度。不做
    DONE/CONTINUE 语义裁定，只回答"是不是在原地打转"。
  - `should_terminate_by_rounds()`：客观终止条件之一，达到
    `max_rounds` 上限即返回 `True`（`max_rounds<=0` 表示不限制）；不代表
    "失败"，只是"到点了"，具体怎么收尾由调用方决定。
  - `record_dead_end(step_idx, reason)` / `render_dead_ends_block()`：与
    `goal_mode/runner.py::_record_dead_end`/`_render_dead_ends_block` 同一套
    "已验证无效路径"去重（`difflib` 近似度）+ 渲染哲学，但这里不落盘、
    不拼进 prompt——`ObjectiveExecutor` 目前只在判定 GIVE_UP 时记一条
    dead-end 作为诊断信息，未来如果需要把它拼进下一次 `_submit_step()`
    的 prompt，可以直接调用 `render_dead_ends_block()`，本次改动不强行
    加这一层（YAGNI，见"未来可扩展点"）。
  - 每个 Objective execution 一个独立实例，互不共享内部状态。
  - 与 `evolution/cron_job_executor.py` 的关系：cron 任务已经在
    `run_job()` 内联直接使用 `StuckDetector` 完成了同等效果的卡住检测
    （`detector = StuckDetector(...)` + `if signal is StuckSignal.GIVE_UP`
    分支），迁移到 `GuardianRunner` 收益很小、改动面不小，本次不动 cron
    这一条路径——`GuardianRunner` 主要补给此前完全没有跨 step 卡住检测
    能力的 `autonomous` Objective 路径。
- `config/models.py::AutonomyConfig` 新增五个字段：
  `guardian_mode_enabled: bool = False`（默认关闭，纯增量观察层，关闭时
  `ObjectiveExecutor` 行为与升级前完全一致）、`guardian_max_rounds: int =
  20`、`guardian_stuck_similarity_threshold: float = 0.92`、
  `guardian_stuck_consecutive_limit: int = 3`、`guardian_max_recoveries:
  int = 2`。
  与计划文档草稿的差异：草稿写的是 `cfg.daemon.guardian_mode_enabled`，
  但代码库里不存在 `DaemonConfig` 这个类（同样是设计草稿和代码库实际结构
  的常见偏差，参见 `api/session_pool.py` 模块 docstring 里记录的类似
  情况）。改为放进已有的 `AutonomyConfig`，与阶段三的
  `objective_isolated_context_enabled` 等字段同一命名空间，更符合代码库
  现有的配置组织方式。
- `evolution/objective_executor.py`：
  - `ObjectiveExecutor.__init__` 新增 `self._guardians: dict[str,
    GuardianRunner]`（execution_id → 实例，不持久化——重启后惰性重建，
    代价是重启瞬间"卡住检测的连续计数"归零，与其它内存态计数器
    `_active_step_paths` 的取舍一致）。
  - 新增 `_get_guardian(exec_id)`：`cfg.autonomy.guardian_mode_enabled`
    关闭时恒返回 `None`；开启时惰性创建/复用该 execution 专属的
    `GuardianRunner`，构造参数从 `cfg.autonomy` 对应字段读取。
  - `on_turn_done()` 成功路径（`valid=True` 且完成当前 step）新增看护
    观察：`guardian is None` 时（默认）整段跳过；开启时把
    `step.result_summary` 喂给 `guardian.observe_step()`——
    - `GIVE_UP`：记一条 dead-end，然后**复用**既有的
      `_attempt_redecompose()`（先试重新分解，成功则继续；不成功/不可用
      则判 `ex.status = "failed"`，`progress_notes` 里带上 `"guardian:"`
      前缀，便于看板/日志区分触发来源），不新增一套终止/收尾逻辑；
    - `RECOVER`：不终止，给下一个 step 的 `pending_guidance` 追加一条
      "最近几步结果高度相似，请换一种思路"的提示（复用
      `_submit_step()` 已有的 `pending_guidance` 拼装逻辑，与
      `reset_step()` 的 guidance 注入方式一致）；
    - 都不是时额外检查 `should_terminate_by_rounds()`，达到轮次上限同样
      走"判 failed，`progress_notes` 说明原因"的收尾（轮次耗尽不再尝试
      重新分解——重新分解本身也会消耗新的轮次预算，无限重新分解会绕开
      这道安全阀的本意）。
- 回退方式：`cfg.autonomy.guardian_mode_enabled=False`（默认值）时，
  `_get_guardian()` 恒返回 `None`，`on_turn_done()` 里新增的整段看护逻辑
  完全不执行，行为与升级前完全一致。
- 新增测试：`tests/test_daemon_autonomous_state_recovery.py`
  - `TestGuardianRunnerUnit`（6 个用例）：内容各异的结果不触发信号；连续
    完全相同的结果先触发 `RECOVER` 再触发 `GIVE_UP`；轮次上限判定
    （含 `max_rounds=0` 表示不限制）；dead-end 去重与渲染；`reset()`
    清空全部内部状态。
  - `TestGuardianModeObjectiveExecutorIntegration`（3 个用例）：默认关闭
    时连续相同结果也不会被提前判失败（回归保护）；开启且未提供
    `llm_redecompose_fn` 时，连续相同结果最终被判定 `failed` 且
    `progress_notes` 带 `guardian` 标记；开启且提供了
    `llm_redecompose_fn` 时，命中 GIVE_UP 后成功走重新分解路径，
    `ex.redecompose_attempted` 为 `True` 且新步骤描述确实生效。
- 验证：`python3 -m pytest tests/test_daemon_autonomous_state_recovery.py`
  25 个用例全部通过；额外跑了
  `test_objective_executor_adaptive_concurrency.py` /
  `test_objective_executor_kanban_tracks.py` /
  `test_objective_executor_kanban_tracks_r2.py`（不依赖 `uvicorn`/
  `fastapi` 的子集）共 61 个用例全部通过，未发现新增失败；
  `_r3`/`_r4` 两个文件在这次环境里仍然因为缺 `uvicorn` 依赖在 import 阶段
  就失败，与阶段一/二记录的情况相同，与本次改动无关。

至此，`daemon_autonomous_state_recovery_plan.md` 规划的四个阶段（P0-A/
P0-B/P1/P2）全部完成，均带有开关可一键回退到升级前行为。

### 文档

面向使用者的说明文档已补齐：新增
[`docs/daemon-autonomous-state-recovery-guide.md`](../docs/daemon-autonomous-state-recovery-guide.md)
（四个阶段各自的"做什么/怎么开/怎么用"+ 常见问题），并在
`docs/autonomous_daemon_design.md`（实现状态表）、
`docs/config-guide.md`（`AutonomyConfig` 章节 + 相关文档列表）、
`docs/commands-and-tools-reference.md`（补上此前未文档化的
`/agent goals reset-step` 命令）三处加了交叉引用。

未来可扩展点（本次改动范围之外，不属于本计划四个阶段，仅记录以供后续
规划参考）：
  - `GuardianRunner.render_dead_ends_block()` 目前只在判定 GIVE_UP 时记录，
    没有拼进 `_submit_step()` 的 prompt——如果后续观察到同一个 execution
    多次触发 RECOVER 后仍然复发同样的死路，可以考虑把这段渲染结果接入
    `progress_ctx`，让模型显式看到"已验证无效"的路径列表。
  - `_guardians` 目前不持久化；如果 daemon 频繁重启导致看护效果打折扣，
    可以考虑把 `GuardianRunner` 的最小必要状态（`_round`/
    `recoveries_used`）序列化进 `ObjectiveExecution`，参考
    `StuckDetector.to_dict()`/`load_counts()` 已经提供的落盘接口。
