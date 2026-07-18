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

> **误判防护（2026-07 新增）**：`TopicShiftTrigger._heuristic_check()` 在判断前先过滤两类情况，
> 两档（heuristic/llm）均生效：
> 1. 当前消息命中续接短语白名单（`_CONTINUATION_PHRASES`，如"继续"/"continue"/"go on"/
>    "好的"，需整句匹配，去除首尾标点后比对）——直接判定非切换，不进入后续信号；
> 2. 当前消息分词后关键词数 `< _MIN_KEYWORDS_FOR_OVERLAP`（默认 2）——跳过关键词重合度信号，
>    因为短文本重合度天然趋近 0，不具备判断力。
> 起因：`"关键词重合度 0% 低于阈值 15%"` 曾把"继续"这类续接指令误判为话题切换。

所有开关**默认关闭**（`TokenThresholdTrigger` 由已有的 `compress.enabled` 控制，默认也是关闭），完全向后兼容旧行为。

**触发后执行什么策略（2026-07 二次更新：默认改为复用路径 B）**：每个触发器可以给出
`suggested_strategy`（见下表），`_auto_compress_history()` 会算出
`effective_strategy = trigger.suggested_strategy or cfg.compress.strategy`：

- 若 `effective_strategy == "compact_with_skills"`（**新默认值**）：不再走
  `HistoryManager.auto_compress()`，而是直接调用 `compact_with_skills()`——
  即与手动 `/compact` **完全相同**的实现（LLM 生成结构化摘要 + skill 重附，
  超限时自动降级为 `_compact_chunked()` 分批摘要）。
- 若 `effective_strategy` 是其它值（`turn_aligned` / `sliding_window` /
  `llm_summary` / `selective`）：退回旧路径，临时切换 `cfg.compress.strategy`
  为该值，委托给 `HistoryManager.auto_compress()` 执行，执行完毕后恢复原配置。

| 触发器 | 建议策略 | 原因 |
|---|---|---|
| Token 阈值 | 使用 `cfg.compress.strategy`（默认 `compact_with_skills`） | 硬约束，按用户配置执行 |
| 轮次 / 工具调用计数 | `selective` | 常规维护性压缩，逐条按价值裁剪，不需要很激进 |
| 冗余检测 | `selective` | 只需要清理噪音（tool_result 权重低），不动用户意图部分 |
| 话题切换 | `compact_with_skills`（2026-07 三次更新：原为 `llm_summary`，现改为与手动 `/compact` 一致） | 天然的压缩边界，且应享有与手动 `/compact` 相同的 skill 重附与压缩质量自检 |

**冷却期**：`compress.compact_cooldown_turns`（默认 3 轮）——compact 后这么多轮内，
除 `TokenThresholdTrigger` 外的其他触发器不生效，避免短时间内被反复触发。

**执行方式（2026-07 二次更新）**：`_auto_compress_history()` 现在优先复用
`compact_with_skills()`（默认策略 `compact_with_skills`），保证自动触发与手动
`/compact` 的压缩质量完全一致；仅当显式配置为其它轻量策略时，才委托给
`HistoryManager.auto_compress()` 执行（该路径由 `cfg.compress.strategy` 指定的
可插拔 `CompressionStrategy`：`turn_aligned` / `sliding_window` / `llm_summary` /
`selective` 真正执行压缩，`_auto_compress_history()` 不再自己实现切割逻辑）。

**结果打印**：无论走哪条路径，`_auto_compress_history()` 都会在压缩完成后打印
本次生成的完整摘要文本（不截断），前缀标明触发原因和消息数变化，例如：
```
[compact] Auto-compact 完成（触发原因: token_threshold，182 → 4 条消息）。摘要：
[Summary of earlier conversation: ...]
```

**用户确认开关**：`compress.require_confirmation`（默认 `False`，全自动静默压缩）。
设为 `True` 时，压缩前会通过终端 `confirm()` 询问用户 `(y)es/(n)o`，用户拒绝则本次跳过
（下一轮循环还会再次检查触发条件）。非交互环境（daemon/HTTP 等）下确认会自动降级为执行。

适合：默认即与手动 `/compact` 同等质量的语义压缩；如需更低延迟/开销，可将
`compress.strategy` 显式设为 `turn_aligned` / `sliding_window` 等轻量策略退回旧路径。

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

### Skill 重附前的自动卸载（垃圾回收）

无论走正常路径还是超限路径，`compact_with_skills()` 在拿到摘要之后、重附 skill
块**之前**，都会先做一次 skill 垃圾回收：`SkillLoader.auto_unload_idle()` 检查
所有当前 active 的 skill，把满足以下任一条件的直接从 `_active` 移除：

1. 激活以来 tracker 从未记录过一次实际调用（纯粹占着 context 预算没用上）；
2. 有调用记录，但最近一次调用距今超过 `skill_auto_unload_idle_seconds`。

卸载在**当前这一轮**就生效：`_build_skill_compact_block()` 会把刚被回收的
skill 名字传给 `build_compact_context(exclude_names=...)`，排除在本轮重附内容
之外，不需要等到下一次 compact 才体现出差别。

卸载之后，这些 skill 只能再被**显式** `skill_activate` 工具或 `/skill on`
重新拉起；[关键词自动激活](skill-system-guide.md#33-关键词辅助激活)不会再命中
它们，直到显式激活成功、解除这个屏蔽标记为止（详见
[skill-system-guide.md 6.5 节](skill-system-guide.md)）。这一行为受
`skill_auto_unload_enabled` 开关控制（默认 `true`），阈值为
`skill_auto_unload_idle_seconds`（默认 `1800` 秒）。

### 与记事本（Notepad）的联动

记事本（`tools/notepad.py`，见 [记事本机制说明](notepad-guide.md)）本身**不参与**
compact 的输入/输出流程——它常驻 system prompt，不需要被"摘要进"compact 结果里。

但 `compact_with_skills()` 在走**正常路径**（`run_turn(compact_prompt)`）时，会检查当前
记事本总字数：若超过 `CompactionMixin.NOTEPAD_COMPACT_HINT_THRESHOLD`（默认 20000 字符），
会在 `compact_prompt` 末尾追加一段提示，建议模型在生成完对话摘要后，调用
`notepad_summarize` 合并冗余/过时的记事本条目。这只是**建议性提示**，不会自动截断或删除
任何记事本内容——是否总结、总结成什么样，仍由模型在该轮工具调用中自行决定。

**超限路径（`_compact_chunked`）不追加该提示**：该路径直接调用 `_llm.chat_with_retry`，
绕开 `run_turn`，模型在这个调用里无法执行工具调用，所以提示放了也没有意义。

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

> 2026-07 二次更新后，默认配置下路径 A 在执行层面**直接复用**路径 B
> （`compact_with_skills()`），下表中"默认策略"列即为此时的行为；仅当
> 显式把 `compress.strategy` 设为轻量策略时，路径 A 才会走独立的
> `HistoryManager.auto_compress()` 实现，此时行为见"轻量策略"列。

| 维度 | `_auto_compress_history`（路径 A，默认策略） | `_auto_compress_history`（路径 A，轻量策略） | `compact_with_skills`（路径 B） |
|---|---|---|---|
| 触发时机 | 触发器体系（token / 轮次 / 工具调用 / 冗余 / 话题切换） | 同左 | 手动 / 工具 / 超限（响应性） |
| 实际执行的实现 | 直接调用 `compact_with_skills()` | `HistoryManager.auto_compress()` + 可插拔策略 | 自身 |
| LLM 调用 | 有（1 次或 N+1 次，同路径 B） | 取决于策略（`turn_aligned`/`sliding_window` 无 LLM；`llm_summary` 有） | 有（1 次或 N+1 次） |
| 摘要质量 | 高（语义理解，保留关键细节，同路径 B） | 取决于策略；`llm_summary` 质量高，其余为字符串拼接 | 高（语义理解，保留关键细节） |
| 超限处理 | 自动切换分批路径（同路径 B） | 不适用 | 自动切换分批路径 |
| Skill 重附 | 有 | 有 | 有 |
| 结果打印 | 压缩完成后打印完整摘要文本（不截断） | 同左 | `/compact` 命令自身打印 |
| 用户确认 | 可选（`compress.require_confirmation`） | 可选（`compress.require_confirmation`） | 手动触发，天然是用户发起 |
| 延迟 | 有（1–N 次 LLM 调用） | 视策略而定（0 或 1 次 LLM 调用） | 有（1–N 次 LLM 调用） |

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

## Compact 机制主动化改进（`next_doc/compact_mechanism_improvement_plan.md`）

2026-07 三次更新新增六项独立改造，全部遵循"配置默认关闭 → 不改变现有行为 →
用户按需开启"的原则；来源设计文档见 `next_doc/compact_mechanism_improvement_plan.md`
（含每项的取舍说明和实施状态表）。

### P0-A：目标相关性动态权重（goal_mode 卡住恢复）

Goal 模式下，`GoalRunner` 判定卡住（stuck）触发恢复性 compact 时，会把当前
尚未通过的验收标准拼成一段提示（`_build_goal_aware_compact_hint()`），通过
`compact_with_skills(goal_hint=...)` 传给压缩 prompt，让 LLM 生成摘要时优先
保留与未完成目标相关的信息，而不是等权重摘要所有内容。

> 实际落地路径与最初设计（改造 `SelectiveStrategy` 打分函数）不同：因为项目
> 默认压缩策略已经是 `compact_with_skills`（`SelectiveStrategy` 只是保留的
> 轻量退回路径），改造默认路径收益更大，实现上更简单。

开关：`compress.goal_aware_weighting_enabled`（默认 `false`）。

### P0-B：Compact 兼做经验沉淀检查点

`compact_with_skills()` 生成摘要时，若开关打开，会要求 LLM 在摘要末尾追加一段
`===DECISIONS_JSON===...===END_DECISIONS_JSON===` 结构化块，描述本轮对话里做过的
关键技术决策（`topic` / `options_considered` / `chosen` / `rejected_because`）。
`CompactionMixin._extract_and_queue_decisions_from_compact_result()` 剥离并解析
这个块，转成 `DecisionCandidate` 列表，复用 `wiki/decision_writer.py` 已有的
pending 队列 + 巩固循环批量落盘（不新增落盘路径）；解析/入队失败静默跳过，
不影响摘要文本本身（该块总会被剥离，避免格式指令泄漏进最终摘要）。

沉淀下来的决策可以通过 `recall_decisions` 工具（见 `tools/builtin.py`）由 agent
主动检索——这是 P0-B 与 P2-B（`recall_from_raw_history`）的分工边界：前者检索
"提炼过的决策"，后者检索"原始对话片段"。

开关：`compress.decision_extraction_on_compact_with_skills_enabled`（默认 `false`）。

### P1-A：安全点判定（`history/triggers.py::SafePointGate`）

问题：非 token 硬阈值的触发命中（轮次计数/工具调用计数/冗余检测/话题切换）如果
恰好落在"一次多步骤有副作用操作执行到一半"（例如连续几次 `bash`/`write_file`
调用还没执行完），被打断可能导致执行上下文断裂。

`SafePointGate.is_safe_point(ctx)` 判定当前是否处于安全点：
- history 为空，或最后一条消息本身就是真实用户输入（turn 边界）→ 安全；
- 否则回溯最近若干条消息（`_SAFE_POINT_LOOKBACK=8`，直到遇到 turn 边界为止），
  只要出现过 `permissions.py::_RISKY_TOOLS`（`bash`/`write_file`/`patch_file`/
  `delete_file`/`create_file`/`skill_propose`）里的工具调用，就判定不安全。

`CompositeTrigger` 命中软触发但落在不安全点时，会把这次命中挂起（保存在
`CompositeTrigger._pending` 实例字段上，**不落盘**，因为 Agent 生命周期内
持有的是同一个 `CompositeTrigger` 实例），本轮不执行 compact；下一次
`check()` 调用时若已到达安全点，直接放行挂起结果，不需要重新满足触发条件。
`token_threshold`（`bypass_cooldown=True`）不受此限制，始终立即执行。

开关：`compress.safe_point_gating_enabled`（默认 `false`）。

### P1-B：触发信号强度叠加

问题：多个软触发器可能"各自都接近但都没达到自身阈值"（例如轮次计数达到
60%、工具调用计数达到 70%），传统 OR 组合逻辑下谁都不会触发，直到某一个
单独越线，可能错过更早的"综合信号已经很强"的时机。

每个 `CompactTrigger` 新增 `intensity_hint(ctx, cfg) -> float`（0~1，默认 0，
不参与叠加）钩子，`TurnCountTrigger` / `ToolCallCountTrigger` /
`RedundancyTrigger` 提供了各自"接近阈值程度"的实现（`TopicShiftTrigger`
本身是布尔判断，没有天然的连续度量，不参与叠加）。所有硬触发都未命中时，
若 `composite_intensity_enabled` 开启，`CompositeTrigger.check()` 会把各触发器
的 `intensity_hint()` 求和，超过 `composite_intensity_threshold`（默认 `1.2`）
也视为命中一次 `composite_intensity` 软触发，建议策略 `selective`。

开关：`compress.composite_intensity_enabled` / `compress.composite_intensity_threshold`（默认 `false` / `1.2`）。

### P2-A：压缩质量事后自检 + lesson 反馈闭环

`history/compact_audit.py::audit_compact_quality()`：单次 LLM 调用，输入
"压缩后的摘要 + 压缩前原始片段（从最新往前截断到预算内，默认 6000 字符）"，
判断摘要是否遗漏了决定性信息（约束条件 / 失败原因 / 用户明确要求）。这是
**事后**校验，不阻塞主流程——任何异常都静默降级为"无遗漏"。

`CompactionMixin._maybe_audit_compact_quality()` 只对
`compress.audit_compact_reasons` 白名单里的触发原因生效（默认
`topic_shift_heuristic` / `topic_shift_llm` / `stuck_recovery_deep`，即非高频
触发），避免 `turn_count`/`tool_call_count` 这类高频触发都额外增加一次 LLM
调用成本；挂在 `_auto_compress_history()` 里 `compact_with_skills()` 调用之后，
默认在后台线程异步执行（`compress.audit_async=True`），不阻塞当前轮次返回。

发现遗漏时，`_apply_compact_audit_issue()` 做两件事：
1. 追加一条 `compact_supplement` 历史条目（新增 `HType.COMPACT_SUPPLEMENT`，
   见 [历史类型化设计](history-typed-design.md)），把遗漏信息补回当前历史，
   模型下一轮就能看到；
2. 写入 `<project_root>/.agent/activity_digest.jsonl`（`type=compact_audit_issue`，
   含 `trigger_reason` / `missing_info`），累计"哪种触发原因遗漏率更高"的统计，
   供后续 `evolution/soft_goal_deriver.py` 等 lesson/软目标衍生链路消费
   （本次只负责把数据写出来，消费端是后续独立的小改造）。

可能运行在后台线程里的写操作（历史追加、raw history 追加）用
`Agent._compact_audit_lock` 保护并发。

开关：`compress.audit_enabled` / `compress.audit_compact_reasons` / `compress.audit_async`（默认 `false` / 见上 / `true`）。

### P2-B：Raw history 按需找回工具

`raw_history.jsonl` 一直全量持久化压缩前的所有原始记录，但此前只是"死档案"，
agent 自己运行中无法主动检索找回。新增只读、免审批工具
`recall_from_raw_history(query, max_results=5)`（`tools/recall_history.py`）：

- 复用 `history/triggers.py::_simple_keywords` 做关键词匹配（不引入向量检索
  依赖），按重合度降序 + 时间倒序返回命中片段，附带近似 turn 编号
  （`turn_index` / `turns_ago`）；
- 跳过 `compact_event`/`compressed`/`compact_summary`/`session_resume` 等占位符
  类型条目；
- 沿用 `tools/notepad.py::configure_notepad_store` 的线程本地 provider 注入
  模式（`configure_recall_history()`，在 `agent/lifecycle.py` 里紧邻 notepad
  那行调用），注入的是"当前 session 的 raw history 条目列表"懒引用（活的
  `list` 引用，每次调用都拿最新数据，无需重新解析磁盘文件）；
- 工具本身**始终**注册在全局 registry（与 `notepad_enabled` 等既有取舍
  一致），未启用/未配置时调用直接返回错误提示字符串，不抛异常。

意义：给更激进的压缩策略兜底——反正删掉的东西找得回来，压缩策略可以更敢于
"压狠一点"，把"怕删错"这个心理负担从压缩阶段转移到"按需找回"阶段。

开关：`recall_history_enabled` / `recall_history_mode`（AppConfig 顶层字段，默认 `false` / `"keyword"`；`"embedding"` 档预留未实现）。

配套还提供了 `/recall <query>` / `/recall --max N <query>` slash 命令
（`cli/commands/recall.py`），走同一套底层实现，给用户一个不用等模型决定
调不调用、随时手动查的入口——和 `/notepad show` 之于 `notepad_*` 工具是
同一种关系；已加入 `_COMMANDS` 补全提示（`ui/terminal.py`）和 `/help`
帮助文本（`cli/parser.py`）。

> P3（预测式压缩节奏）仍停留在设计阶段，依赖 P0-B / P2-A 积累的真实数据，
> 详见 `next_doc/compact_mechanism_improvement_plan.md` 第 7 节。

## 配置项

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `auto_compress_enabled` / `compress.enabled` | `false` | `TokenThresholdTrigger` 总开关 |
| `auto_compress_threshold` / `compress.threshold` | `0.7` | token 使用率阈值，超过时触发 |
| `auto_compress_strategy` / `compress.strategy` | `"compact_with_skills"` | 默认压缩策略（无触发器给出建议时使用）；默认值即复用手动 `/compact` 实现，设为 `turn_aligned`/`sliding_window`/`llm_summary`/`selective` 可退回轻量策略 |
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
| `compress.goal_aware_weighting_enabled`（P0-A） | `false` | goal_mode 卡住恢复 compact 时是否传入目标相关性 hint |
| `compress.decision_extraction_on_compact_with_skills_enabled`（P0-B） | `false` | compact 摘要是否额外提取决策候选并入队沉淀 |
| `compress.decision_recall_tool_enabled` | `true` | 是否注册 `recall_decisions` 只读工具（沉淀决策检索，区别于 P2-B 的原始片段检索） |
| `compress.safe_point_gating_enabled`（P1-A） | `false` | 软触发命中是否需要等到安全点才真正执行 compact |
| `compress.composite_intensity_enabled`（P1-B） | `false` | 硬触发均未命中时是否叠加各触发器的"接近阈值程度" |
| `compress.composite_intensity_threshold`（P1-B） | `1.2` | 强度叠加总和达到此值即视为命中 `composite_intensity` 软触发 |
| `compress.audit_enabled`（P2-A） | `false` | deep compact 完成后是否执行一次压缩质量事后自检 |
| `compress.audit_compact_reasons`（P2-A） | `["topic_shift_heuristic", "topic_shift_llm", "stuck_recovery_deep"]` | 审计只对这些触发原因生效 |
| `compress.audit_async`（P2-A） | `true` | 审计是否在后台线程异步执行（不阻塞当前轮次） |
| `recall_history_enabled`（P2-B，AppConfig 顶层） | `false` | `recall_from_raw_history` 工具总开关 |
| `recall_history_mode`（P2-B，AppConfig 顶层） | `"keyword"` | `"keyword"`（已实现）/ `"embedding"`（预留） |
| `model_context_window` | `None`（从 LLM 读取） | 覆盖模型上下文大小估算（token 数） |
| `skill_compact_budget` | `25000` | compact 后重附 skill 的总字符预算 |
| `skill_compact_per_skill` | `5000` | 单个 skill 重附字符上限 |
| `skill_auto_unload_enabled` | `true` | compact 时是否自动卸载长期未用的 active skill |
| `skill_auto_unload_idle_seconds` | `1800` | 判定 skill「长期未用」的空闲时间阈值（秒） |
| `forget_orphan_tool_results` | 策略相关 | 是否丢弃孤立的 tool_result 消息 |

> 所有触发器开关均只支持 JSON 配置文件平坦 key（例如 `compact_turn_count_trigger_enabled`、
> `compact_topic_shift_detection`；P0/P1/P2 新增字段对应
> `compact_goal_aware_weighting_enabled` / `compact_decision_extraction_enabled` /
> `compact_safe_point_gating_enabled` / `compact_composite_intensity_enabled` /
> `compact_composite_intensity_threshold` / `compact_audit_enabled` /
> `compact_audit_reasons` / `compact_audit_async`），暂无对应 CLI 参数，
> 详见 [配置系统指南](config-guide.md#compressconfig)。

## 相关代码位置

| 文件 | 内容 |
|---|---|
| `agent.py::compact_with_skills()` | 主入口，路径选择，skill 重附，session 保存 |
| `agent.py::_compact_chunked()` | 分批摘要核心实现 |
| `agent.py::_maybe_run_compact()` | 触发器命中后的统一入口，处理确认开关 |
| `agent/compaction.py::_auto_compress_history()` | 默认直接复用 `compact_with_skills()`；轻量策略时委托给 `HistoryManager.auto_compress()`；压缩完成后打印完整摘要；P2-A 审计钩子挂在此处 |
| `agent.py::_agentic_loop()` | 组合触发器检查点 + `LLMContextWindowError` 捕获（触发路径 B） |
| `history/triggers.py` | `CompactTrigger` / `CompositeTrigger` 及内置触发器实现；`SafePointGate`（P1-A）；`intensity_hint()`（P1-B） |
| `history/compression.py` | `CompressionStrategy` 及内置策略类（`turn_aligned`/`sliding_window`/`llm_summary`/`selective`） |
| `history/compact_audit.py` | `audit_compact_quality()`（P2-A 压缩质量事后自检） |
| `history_manager.py::auto_compress()` | 委托给 `CompressionStrategy` 执行压缩的统一入口 |
| `agent/compaction.py::_extract_and_queue_decisions_from_compact_result()` | P0-B 决策候选提取与入队 |
| `agent/compaction.py::_maybe_audit_compact_quality() / _apply_compact_audit_issue()` | P2-A 审计触发门控与落地（历史条目 + activity_digest.jsonl） |
| `goal_mode/runner.py::_build_goal_aware_compact_hint()` | P0-A goal-aware compact hint 构建 |
| `tools/recall_history.py` | P2-B `recall_from_raw_history` 只读工具 |
| `cli/commands/recall.py` | P2-B `/recall` slash 命令（手动 CLI 入口，同一套底层实现） |
| `tools/builtin.py`（`recall_decisions`） | P0-B 沉淀决策的检索工具（与 P2-B 分工：一个查决策，一个查原始片段） |
| `prompts/user/compact_history.md` | 正常路径 compact prompt |
| `prompts/user/compact_chunk_request.md` | 分批路径 chunk prompt |
| `prompts/user/compact_merge_request.md` | 分批路径合并 prompt |
| `agent/compaction.py::_build_notepad_compact_hint()` | 记事本超阈值时追加的 compact 提示语 |
| `tools/notepad.py` | 记事本数据结构与工具实现 |

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

> 2026-07 二次更新：`_auto_compress_history()` 默认策略下会在内部调用
> `compact_with_skills()`，此时 `PreCompact`/`PostCompact` **依然会触发**
> （因为是从 `_auto_compress_history()` 发起的）；只有用户手动执行 `/compact`
> 命令、或 agent 主动调用 `compact_history` 工具时——即完全绕开触发器体系、
> 直接调用 `compact_with_skills()`——才不会触发这两个 hook 事件。

详见 [Hooks 机制](hooks.md#context-compact-生命周期)。

---

*最后更新：2026-07（新增 Compact 触发器体系 `history/triggers.py`：轮次计数 /
工具调用计数 / 冗余检测 / 话题切换（heuristic + llm 两档）触发器，均带独立开关；
新增触发-确认开关 `compress.require_confirmation`；修复 `_auto_compress_history()`
未委托给 `CompressionStrategy` 注册表导致 `compress.strategy` 配置实际不生效的问题；
`compact_event` 新增 `trigger_reason` 字段用于事后统计各触发器命中效果）*

*二次更新：2026-07（`compress.strategy` 默认值由 `turn_aligned` 改为
`compact_with_skills`；`_auto_compress_history()` 在该默认值下直接复用
`compact_with_skills()`，使自动触发的 compact 与手动 `/compact` 压缩质量完全一致，
仅在显式配置为 `turn_aligned`/`sliding_window`/`llm_summary`/`selective` 时才退回
`HistoryManager.auto_compress()` 轻量路径；`_auto_compress_history()` 压缩完成后
新增打印本次生成的完整摘要文本（不截断），无论走哪条路径）*

*三次更新：2026-07（落地 `next_doc/compact_mechanism_improvement_plan.md` 全部
六项改造，见"Compact 机制主动化改进"一节：P0-A 目标相关性动态权重、P0-B
compact 兼做经验沉淀检查点、P1-A 安全点判定（`SafePointGate`）、P1-B 触发信号
强度叠加（`intensity_hint()`）、P2-A 压缩质量事后自检 + lesson 反馈闭环
（新增 `history/compact_audit.py`、`HType.COMPACT_SUPPLEMENT`）、P2-B Raw
history 按需找回工具（新增 `tools/recall_history.py::recall_from_raw_history`）；
全部默认关闭，且补齐了此前遗漏的 `config/loader.py` 平坦 key 映射——这批新增
字段此前虽然在 `CompressConfig`/`AppConfig` 里存在，但未接入 JSON 配置文件
解析，实际上不可通过 `agent_config.json` 配置，现已修复）*

*四次更新：2026-07（为 P2-B 新增的 `recall_from_raw_history` 工具补上对应的
`/recall <query>` \| `/recall --max N <query>` slash 命令：`cli/commands/recall.py`
+ `cli/repl.py` 分发 + `ui/terminal.py::_COMMANDS` 补全提示 + `cli/parser.py`
`/help` 帮助文本，四处一起改，与项目里"工具 + 手动 CLI 入口"成对出现的既有
惯例（如 `notepad_*` 工具与 `/notepad` 命令）保持一致）*
