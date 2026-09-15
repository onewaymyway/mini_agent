# compact 结果有效性校验修复计划

状态：**已完成**（2026-09-14）

> **[2026-09 后续更新]** 本文档修复的是"单次直出路径 = `run_turn(compact_prompt)`"
> 架构下的一个具体故障（哨兵占位文本被当真摘要写入历史）。此后单次直出路径已
> 结构性重构为 `_compact_single_shot()`（`_llm.chat_with_retry()`，不经过
> `run_turn()`/agentic loop，详见 `docs/compact-design.md` "路径 B"一节），
> `run_turn()` 特有的 `result_sanity_check` 哨兵机制不会再被这条路径触发，本文档
> 描述的故障场景已不适用。保留本文档作为该问题的历史记录和根因分析参考；
> `last_turn_result_valid()` 本身仍然存在，服务于其它仍直接调用 `run_turn()`
> 的场景（`api/server.py`、`evolution/objective_agent_bridge.py`）。

## 背景 / 用户报告的问题

用户报告：一次视频生成任务超时后触发 auto-compact，compact 完成后
`/debug history` 显示历史被压缩成了一段系统内部的"本轮未获得有效回复"
提示文本，而不是真实的会话摘要。原始历史（含 novel-video-studio 相关
上下文、之前的工具调用结果等）永久丢失，导致后续对话完全脱节
（agent 误以为是一次全新任务，反复询问已经讨论过的信息）。

用户当时的直觉判断："compact 失败时应该放弃这次 compact、重新尝试，
而不是继续使用有问题的结果"——根因分析证实这个直觉是对的，但实际
情况比"重试"更根本：**旧代码压根没有检测出这次 compact 结果是有问题的**。

## 根因

1. `run_turn()`（`agent/turn_loop.py`）内部有一套"结果健全性校验"
   （`result_sanity_check_enabled`，来自 `daemon_autonomous_state_recovery_plan.md`
   阶段一）：当本轮最终输出被判定为畸形/半成品（未闭合的 `<tool_use>`
   标签残留、命中格式纠错重试上限等）时，会把 `final_text` **替换**为
   一段固定的哨兵占位文本：

   > `[系统提示：本轮未获得有效回复，输出内容异常（可能是未闭合的工具调用
   > 标签或半成品文本），已作废，不应被当作真实结果使用。]`

   同时置位 `self._last_turn_result_invalid = True`。**但函数本身仍然
   正常 `return final_text`，不抛异常。**

2. 这个"哨兵文本 + 标志位"的模式要求**每一个调用 `run_turn()` 的地方**
   都必须检查标志位才能正确处理。代码库里已经有两处这么做了：
   `evolution/objective_agent_bridge.py`（两处）、`api/server.py`（一处）。

3. **但 `agent/compaction.py::compact_with_skills()` 的正常路径
   （`result = self.run_turn(compact_prompt)`）没有检查这个标志位**，
   只判断了 `if not result:`（空字符串才算失败）。哨兵文本非空，于是
   被当成"本次压缩生成的真实摘要"，写入
   `make_compact_summary(result)`，历史被清空重建——原始历史永久丢失。

   用户 debug 看到的 `#1 compact_summary` 内容，就是这段哨兵文本本身，
   与根因完全吻合。

## 修复内容

### P0 — `compact_with_skills()` 正常路径增加结果有效性校验（核心修复）

文件：`src/mini_agent/agent/compaction.py`

`run_turn(compact_prompt)` 返回后，立即检查
`self.last_turn_result_valid()`：
- **有效** → 行为不变，直接使用该文本作为摘要。
- **无效**（命中哨兵文本）→ 视同"本次摘要生成失败"，**不使用该文本**，
  自动退化到 `_compact_chunked()` 分批路径重新生成一次摘要（该路径
  直接用 `chat_with_retry` 做纯文本摘要，不经过 tool-calling 循环，
  结构上不会产生同类"未闭合 tool_use"问题）。
- chunked 重试也失败（抛异常）→ 走既有的异常处理分支，`return ""`，
  **不触碰 `self._history`**——原始历史完整保留，等下一次触发时
  可以在完整历史基础上重新尝试压缩，而不是在一段已经损坏的历史上
  越修越错。

这与用户的直觉一致："放弃这次有问题的 compact，而不是继续用"。

### P1 — 统一结果有效性查询入口

文件：`src/mini_agent/agent/turn_loop.py`

新增 `TurnLoopMixin.last_turn_result_valid() -> bool`，封装对
`self._last_turn_result_invalid` 的读取。`compact_with_skills()` 通过
这个方法而不是直接读私有属性来判断结果有效性，避免以后再有新调用方
（比如未来 `goal_mode/runner.py` 如果直接调用 `run_turn()`）重复
遗漏这一检查——只要记得"调用 `run_turn()` 后检查
`last_turn_result_valid()`"这一条规则即可。

**范围说明**：`api/server.py`、`evolution/objective_agent_bridge.py`
里已有的两处 `getattr(agent, "_last_turn_result_invalid", False)`
检查本计划评估后**保持原样未改**——那两处的调用方（`FakeAgent`/
`_FakeIsolatedAgent` 等测试替身）不一定继承 `TurnLoopMixin`，改成
强制调用 `last_turn_result_valid()` 方法会在缺少该方法时抛
`AttributeError`（已通过跑测试实测确认，见"验证"一节），属于范围外
的破坏性改动，予以撤销。`last_turn_result_valid()` 目前只服务于
`compaction.py`这一个新增调用方；后续如果要统一其它两处，需要先
确认所有测试替身都实现了该方法。

### 已排查但确认不是问题的点

调查过程中怀疑 `_last_turn_result_invalid` 标志位可能跨轮次不重置
（"一次失败，之后所有轮次都被误判"），但代码检查确认
`_agentic_loop()`（每次 `run_turn()` 调用时都会执行一次）开头
无条件 `self._last_turn_result_invalid = False`
（`turn_loop.py:216`），标志位天然是"仅反映最近一次 run_turn 调用"
的语义，不存在跨轮次污染，无需修复。

## 未覆盖的范围（如实记录）

- `_compact_chunked()` 路径本身不经过 `run_turn()`/`result_sanity_check`，
  结构上不会复现同一 bug，未做额外校验。
- 本次不改变 `result_sanity_check` 本身的判定规则
  （`perception/format_correction_detector.py::is_valid_final_result`），
  只修复"调用方没有检查判定结果"这一环。
- `goal_mode/runner.py::_do_compact()` 通过调用
  `self._agent.compact_with_skills()` 间接受益于本次修复，未单独改动。

## 验证

- 新增 `tests/test_compact_result_validity_guard.py`（4 用例）：
  - 正常结果直接使用，不触发 chunked 兜底
  - **核心回归用例**：复现"run_turn 返回哨兵文本但不抛异常"场景，
    验证哨兵文本不会进入返回值/历史，且自动退化到 chunked 重新生成
  - chunked 兜底也失败时，历史完全不受影响、返回空串
  - `last_turn_result_valid()` 正确反映标志位
- 回归测试：`test_compact_audit.py` / `test_compact_autopilot_improvements.py`
  / `test_compact_trigger_resume_snapshot.py` / `test_goal_mode.py`
  / `test_daemon_autonomous_state_recovery.py` 与修改前基线对比，
  无新增失败（`test_goal_mode.py` 的 5 个失败、
  `test_skill_compact.py`/`test_format_correction_integration.py` 等的
  19 个失败均为改动前就存在的、与本次改动无关的预置环境/测试
  fixture 问题，已用未修改的原始代码复现同样的失败集合核实过）。
