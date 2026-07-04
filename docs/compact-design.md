# 历史压缩（Compact）设计

mini_agent 对话历史会随时间增长，最终超出模型上下文窗口限制。
历史压缩（Compact）机制负责在保留关键信息的前提下缩减历史长度。

## 两条压缩路径

### 路径 A：`_auto_compress_history()`（自动压缩，由触发器体系驱动）

**触发条件（`history/triggers.py::CompositeTrigger`，2026-07 起）**：
每轮循环开头会检查一组独立开关控制的触发器，任一命中即可能触发压缩：

| 触发器 | 开关 | 默认阈值 | 说明 |
|---|---|---|---|
| `TokenThresholdTrigger` | `compress.enabled` | `threshold=0.7` | token 占用率超过阈值（原有逻辑，硬约束，无视冷却期） |
| `TurnCountTrigger` | `compress.turn_count_trigger_enabled` | `max_turns_before_compact=20` | 距上次 compact 满 N 轮 |
| `ToolCallCountTrigger` | `compress.tool_call_count_trigger_enabled` | `max_tool_calls_before_compact=50` | 距上次 compact 累计 N 次工具调用 |
| `RedundancyTrigger` | `compress.redundancy_detection_enabled` | `redundancy_tool_result_ratio=0.6` | `tool_result` 消息占比过高，历史信息冗余 |
| `TopicShiftTrigger` | `compress.topic_shift_detection`（`"off"/"heuristic"/"llm"`） | `topic_shift_keyword_overlap_threshold=0.15` | 检测到用户话题切换（关键词重合度低 / 切换语关键词 / LLM 二次确认） |

所有开关**默认关闭**（`TokenThresholdTrigger` 由已有的 `compress.enabled` 控制，默认也是关闭），完全向后兼容旧行为。

**触发后执行什么策略**：每个触发器可以给出 `suggested_strategy`（见下表），
`_auto_compress_history()` 会临时切换 `cfg.compress.strategy` 为该建议值来执行压缩，
执行完毕后恢复原配置。若触发器未给出建议（如 `TokenThresholdTrigger`），
则使用 `cfg.compress.strategy` 配置的默认策略。

| 触发器 | 建议策略 | 原因 |
|---|---|---|
| Token 阈值 | 使用 `cfg.compress.strategy`（默认 `turn_aligned`） | 硬约束，按用户配置执行 |
| 轮次 / 工具调用计数 | `selective` | 常规维护性压缩，逐条按价值裁剪，不需要很激进 |
| 冗余检测 | `selective` | 只需要清理噪音（tool_result 权重低），不动用户意图部分 |
| 话题切换 | `llm_summary` | 天然的压缩边界，最适合生成干净的"旧话题收尾摘要" |

**冷却期**：`compress.compact_cooldown_turns`（默认 3 轮）——compact 后这么多轮内，
除 `TokenThresholdTrigger` 外的其他触发器不生效，避免短时间内被反复触发。

**执行方式（2026-07 起改为委托）**：`_auto_compress_history()` 不再自己实现切割逻辑，
而是委托给 `HistoryManager.auto_compress()`，由 `cfg.compress.strategy` 指定的
可插拔 `CompressionStrategy`（`turn_aligned` / `sliding_window` / `llm_summary` / `selective`）
真正执行压缩。旧版本中 `_auto_compress_history()` 是一段独立于策略注册表之外的硬编码
turn-aligned 切割实现——这导致除 `token_threshold` 外配置的策略从未真正生效；
现已修复，触发器的 `suggested_strategy` 能够按预期切换实际执行的策略。

**用户确认开关**：`compress.require_confirmation`（默认 `False`，全自动静默压缩）。
设为 `True` 时，压缩前会通过终端 `confirm()` 询问用户 `(y)es/(n)o`，用户拒绝则本次跳过
（下一轮循环还会再次检查触发条件）。非交互环境（daemon/HTTP 等）下确认会自动降级为执行。

适合：日常对话中的预防性/维护性压缩，可选是否需要 LLM 参与（取决于选中的策略）。

### 路径 B：`compact_with_skills()`（主动语义压缩）

触发条件：
1. 用户手动执行 `/compact`
2. agent 工具调用 `compact_history`
3. 上下文超限（`LLMContextWindowError`）时 auto-compact 自动触发

此方法内部自动选择两种实现：

```
compact_with_skills()
    │
    ├─ 正常路径（历史未超限）
    │     run_turn(compact_prompt)
    │     → LLM 在完整历史上看到所有上下文，生成高质量摘要
    │     → 用摘要替换历史 + 重附 skill 块
    │
    └─ 超限路径（LLMContextWindowError）── 自动切换
          _compact_chunked()
          → 把历史按 turn 边界分批
          → 每批独立调用 _llm.chat_with_retry（绕开 run_turn）
          → 多批摘要合并为最终摘要
          → 用最终摘要替换历史 + 重附 skill 块
```

## 分批摘要（Chunked Compact）详解

### 为什么需要分批

当 `run_turn(compact_prompt)` 触发 `LLMContextWindowError` 时，说明当前历史本身
已超过模型上下文窗口。此时不能再用 `run_turn`（它需要把完整历史发给模型），
必须绕开它，把历史切小后分批处理。

### 算法步骤

```
1. 估算 chunk budget
   - 从 _llm / cfg 读取模型最大上下文（默认 100K token）
   - 每 chunk 目标：上下文的 50%（另 50% 留给 prompt + system + 输出）
   - 字符估算：1 token ≈ 3 chars（保守值）

2. 按 turn 边界切分
   - 收集所有真实用户输入（is_turn_boundary）的起始索引
   - 依次把 turn 累积到 current_chunk，超过 budget 时提交并开新 chunk
   - 单个 turn 超限时单独成 chunk（不再细拆，让 LLM 自行截断）

3. 每 chunk 独立 LLM 调用
   - 消息列表 = to_llm_messages(chunk) + [compact_chunk_request prompt]
   - 直接调用 _llm.chat_with_retry（tools=[], max_retries=3）
   - 单 chunk 失败时降级为 _build_summary_text() 字符串摘要，不中断整体流程

4. 合并摘要
   - chunk 数为 1：直接使用该 chunk 的摘要
   - chunk 数 > 1：再发一次 LLM 调用（compact_merge_request prompt），
     把所有 chunk 摘要归并为结构化最终摘要
   - 合并调用失败时：字符串拼接所有 chunk 摘要（保底）

5. 原地替换历史
   [session_resume("[Previous session summary — chunked compact]")]
   [compact_summary(final_summary)]
   → 调用方追加 skill_block（如果有）
```

### Prompt 文件

| Prompt | 路径 | 用途 |
|---|---|---|
| `compact_history` | `prompts/user/compact_history.md` | 正常路径：发给 run_turn 的 compact 指令 |
| `compact_chunk_request` | `prompts/user/compact_chunk_request.md` | 分批路径：每 chunk 的摘要指令 |
| `compact_merge_request` | `prompts/user/compact_merge_request.md` | 分批路径：多 chunk 合并摘要指令 |
| `compress_summary_request` | `prompts/user/compress_summary_request.md` | `LLMSummaryStrategy` 使用 |
| `compress_summarizer` (system) | `prompts/system/compress_summarizer.md` | 分批路径和 `LLMSummaryStrategy` 的 system prompt |

## 摘要内容设计

所有 compact prompt 都要求 LLM 生成包含以下内容的摘要，以确保后续任务不丢失关键信息：

| 内容类别 | 保留原因 |
|---|---|
| **用户目标（Goal）** | 后续任务需要理解意图 |
| **已完成工作（Work Completed）** | 包含文件路径、命令、工具调用结果——无法从摘要外重建 |
| **关键发现（Critical Findings）** | 错误信息、API 响应、测试结果——精确文本对 debug 不可或缺 |
| **技术决策（Key Decisions）** | 避免重复踩坑，保留选择依据 |
| **当前状态（Current State）** | 明确已完成 vs 进行中 vs 待做 |
| **Lessons & Guardrails（教训与守则）** | 从本次对话中提炼出可执行的规则：踩过的坑及根因、用户明确纠正过的地方（视为后续硬约束）、验证有效可复用的做法、仍未消除的风险点。要求写成简短的祈使句规则（如"务必先 X 再 Y"），不是叙事复述；无内容时写 "None noted." |
| **待做事项（Pending）** | 避免遗漏未完成任务 |

**不保留的内容**：纯礼貌性对话、重复的状态确认、已被后续步骤覆盖的中间状态。

**Lessons & Guardrails 的特殊之处**：与其它章节不同，它不是对历史事实的摘录，而是面向
*未来*的行为约束——摘要替换掉原始历史后，后续任务将无法看到当时具体发生了什么，但仍需要遵守
从中提炼出的规则（例如"这个工具在 Windows 上需要额外参数"或"用户已经明确拒绝过某种方案"）。
`compact_chunk_request.md` 在分批路径下要求每个 chunk 先记录原始的"错误与纠正"事实（不做归纳），
再由 `compact_merge_request.md` 在最终合并阶段去重、归纳为统一的 Lessons & Guardrails 列表，
避免同一条教训在多个 chunk 摘要里重复出现。

## 历史替换机制

所有 compact 路径最终都原地替换 `self._history`（保持共享引用有效）：

```python
self._history.clear()
self._history.extend(new_history)
```

替换后的历史结构：

```
[session_resume]     # role: user,      _type: session_resume
[compact_summary]    # role: assistant, _type: compact_summary
[skill_context]      # role: user,      _type: skill_context   （可选，有 skill 时附加）
```

`raw_history` 同步追加 `compact_event` 记录（含 `before_count` / `after_count` / `strategy` /
`trigger_reason`，用于审计和 `/diagnostics`）。`trigger_reason` 取值对应触发器的
`reason_key`：`token_threshold` / `turn_count` / `tool_call_count` / `redundancy` /
`topic_shift_heuristic` / `topic_shift_llm`（`compact_with_skills` 手动路径不写入
`trigger_reason`，因为不经过触发器体系）。

## 与 `compact_with_skills` 的区别

| 维度 | `_auto_compress_history`（路径 A） | `compact_with_skills`（路径 B） |
|---|---|---|
| 触发时机 | 触发器体系（token / 轮次 / 工具调用 / 冗余 / 话题切换） | 手动 / 工具 / 超限（响应性） |
| LLM 调用 | 取决于选中的策略（`turn_aligned`/`sliding_window` 无 LLM；`llm_summary` 有） | 有（1 次或 N+1 次） |
| 摘要质量 | 取决于策略；`llm_summary`（话题切换默认建议）质量高，其余为字符串拼接 | 高（语义理解，保留关键细节） |
| 超限处理 | 不适用 | 自动切换分批路径 |
| Skill 重附 | 有 | 有 |
| 用户确认 | 可选（`compress.require_confirmation`） | 手动触发，天然是用户发起 |
| 延迟 | 视策略而定（0 或 1 次 LLM 调用） | 有（1–N 次 LLM 调用） |

## 触发器架构（`history/triggers.py`）

```
CompactTrigger（抽象基类）
    ├─ is_enabled(cfg)       读取对应开关字段
    └─ should_trigger(ctx, cfg) → TriggerResult

CompositeTrigger
    - 持有一组 CompactTrigger 实例（build_default_triggers()）
    - check(ctx, cfg)：依次调用每个子触发器，命中多个时取 priority 最高的一个
    - 冷却期内屏蔽非硬约束触发器（TriggerResult.bypass_cooldown=False 的触发器）
```

`TriggerResult` 字段：

| 字段 | 说明 |
|---|---|
| `triggered` | 是否命中 |
| `reason` | 机器可读标识（写入 `compact_event.trigger_reason`） |
| `message` | 人类可读说明（打印提示 / 确认弹窗展示） |
| `suggested_strategy` | 建议使用的压缩策略，`None` 表示使用 `cfg.compress.strategy` |
| `priority` | 多触发器同时命中时的仲裁优先级 |
| `bypass_cooldown` | 是否无视冷却期（仅 `TokenThresholdTrigger` 为 `True`） |

**扩展新触发器**：继承 `CompactTrigger`，实现 `is_enabled()` / `should_trigger()`，
加入 `build_default_triggers()` 返回的列表即可，无需修改 `agent.py` 主循环逻辑
（与 `history/compression.py` 的 `CompressionStrategy` 注册表是同一设计哲学）。

## 配置项

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `auto_compress_enabled` / `compress.enabled` | `false` | `TokenThresholdTrigger` 总开关 |
| `auto_compress_threshold` / `compress.threshold` | `0.7` | token 使用率阈值，超过时触发 |
| `auto_compress_strategy` / `compress.strategy` | `"turn_aligned"` | 默认压缩策略（无触发器给出建议时使用） |
| `compress.turn_count_trigger_enabled` | `false` | 轮次计数触发器开关 |
| `compress.max_turns_before_compact` | `20` | 距上次 compact 满 N 轮触发 |
| `compress.tool_call_count_trigger_enabled` | `false` | 工具调用计数触发器开关 |
| `compress.max_tool_calls_before_compact` | `50` | 距上次 compact 累计 N 次工具调用触发 |
| `compress.topic_shift_detection` | `"off"` | `"off"` / `"heuristic"` / `"llm"` |
| `compress.topic_shift_keyword_overlap_threshold` | `0.15` | 关键词重合度低于此值视为疑似话题切换 |
| `compress.redundancy_detection_enabled` | `false` | 冗余检测触发器开关 |
| `compress.redundancy_tool_result_ratio` | `0.6` | `tool_result` 占比超过此值触发 |
| `compress.compact_cooldown_turns` | `3` | compact 后冷却轮数，期间非硬约束触发器不生效 |
| `compress.require_confirmation` | `false` | 触发后是否需要用户 y/n 确认才执行 |
| `model_context_window` | `None`（从 LLM 读取） | 覆盖模型上下文大小估算（token 数） |
| `skill_compact_budget` | `25000` | compact 后重附 skill 的总字符预算 |
| `skill_compact_per_skill` | `5000` | 单个 skill 重附字符上限 |
| `forget_orphan_tool_results` | 策略相关 | 是否丢弃孤立的 tool_result 消息 |

> 所有触发器开关均只支持 JSON 配置文件平坦 key（例如 `compact_turn_count_trigger_enabled`、
> `compact_topic_shift_detection`），暂无对应 CLI 参数，详见 [配置系统指南](config-guide.md#compressconfig)。

## 相关代码位置

| 文件 | 内容 |
|---|---|
| `agent.py::compact_with_skills()` | 主入口，路径选择，skill 重附，session 保存 |
| `agent.py::_compact_chunked()` | 分批摘要核心实现 |
| `agent.py::_maybe_run_compact()` | 触发器命中后的统一入口，处理确认开关 |
| `agent.py::_auto_compress_history()` | 委托给 `HistoryManager.auto_compress()` 执行实际压缩 |
| `agent.py::_agentic_loop()` | 组合触发器检查点 + `LLMContextWindowError` 捕获（触发路径 B） |
| `history/triggers.py` | `CompactTrigger` / `CompositeTrigger` 及内置触发器实现 |
| `history/compression.py` | `CompressionStrategy` 及内置策略类（`turn_aligned`/`sliding_window`/`llm_summary`/`selective`） |
| `history_manager.py::auto_compress()` | 委托给 `CompressionStrategy` 执行压缩的统一入口 |
| `prompts/user/compact_history.md` | 正常路径 compact prompt |
| `prompts/user/compact_chunk_request.md` | 分批路径 chunk prompt |
| `prompts/user/compact_merge_request.md` | 分批路径合并 prompt |

---

## Hooks 集成

`_auto_compress_history()` 执行时会触发两个 hook 事件：

| 事件 | 触发时机 | 可阻止 | payload |
|---|---|---|---|
| `PreCompact` | 压缩逻辑执行前 | ✅ | `{"history_len": N, "strategy": "<trigger_reason 或 auto_compress>"}` |
| `PostCompact` | 压缩完成后 | ❌ | `{"history_len": N, "strategy": "<trigger_reason 或 auto_compress>", "before_count": N, "after_count": N}` |

> `strategy` 字段自 2026-07 起改为传入触发器的 `reason`（例如 `"turn_count"`、
> `"topic_shift_heuristic"`），未经过触发器体系时（理论上不应发生）回退为
> `"auto_compress"`。

`PreCompact` 返回 exit code 2 或 `{"decision": "block"}` 可跳过本次压缩。
`compact_with_skills()`（`/compact` 命令路径）和分批路径（`_compact_chunked`）
目前不经过 `_auto_compress_history()`，不触发这两个事件。

详见 [Hooks 机制](hooks.md#context-compact-生命周期)。

---

*最后更新：2026-07（新增 Compact 触发器体系 `history/triggers.py`：轮次计数 /
工具调用计数 / 冗余检测 / 话题切换（heuristic + llm 两档）触发器，均带独立开关；
新增触发-确认开关 `compress.require_confirmation`；修复 `_auto_compress_history()`
未委托给 `CompressionStrategy` 注册表导致 `compress.strategy` 配置实际不生效的问题；
`compact_event` 新增 `trigger_reason` 字段用于事后统计各触发器命中效果）*
