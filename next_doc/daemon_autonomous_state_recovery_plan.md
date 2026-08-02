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
| 阶段三 | P1 自主任务独立上下文 | ⏳ 设计已定，待实现 |
| 阶段四 | P2 看护模式 GuardianRunner | ⏳ 设计已定，待实现 |

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

（待实现）

### 阶段四：看护模式 GuardianRunner

（待实现）
