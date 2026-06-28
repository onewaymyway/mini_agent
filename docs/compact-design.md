# 历史压缩（Compact）设计

mini_agent 对话历史会随时间增长，最终超出模型上下文窗口限制。
历史压缩（Compact）机制负责在保留关键信息的前提下缩减历史长度。

## 两条压缩路径

### 路径 A：`_auto_compress_history()`（轻量自动压缩）

触发条件：每轮结束后 token 估算达到阈值（`auto_compress_threshold`，默认 80%）。

策略：按 turn 边界切割历史，保留后半段 + 字符串拼接摘要。不调用 LLM，零延迟。

适合：日常对话中的预防性压缩，不需要高质量摘要。

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
| **待做事项（Pending）** | 避免遗漏未完成任务 |

**不保留的内容**：纯礼貌性对话、重复的状态确认、已被后续步骤覆盖的中间状态。

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

`raw_history` 同步追加 `compact_event` 记录（含 before_count / after_count / strategy），
用于审计和 `/diagnostics`。

## 与 `_auto_compress_history()` 的区别

| 维度 | `_auto_compress_history` | `compact_with_skills` |
|---|---|---|
| 触发时机 | token 达阈值（预防性） | 手动 / 工具 / 超限（响应性） |
| LLM 调用 | 无（纯字符串） | 有（1 次或 N+1 次） |
| 摘要质量 | 低（用户消息列表 + 工具计数） | 高（语义理解，保留关键细节） |
| 超限处理 | 不适用（本身不调 LLM） | 自动切换分批路径 |
| Skill 重附 | 有 | 有 |
| 延迟 | 无 | 有（1–N 次 LLM 调用） |

## 配置项

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `auto_compress_enabled` | `true` | 是否开启阈值触发的轻量自动压缩 |
| `auto_compress_threshold` | `0.80` | token 使用率阈值，超过时触发 |
| `model_context_window` | `None`（从 LLM 读取） | 覆盖模型上下文大小估算（token 数） |
| `skill_compact_budget` | `25000` | compact 后重附 skill 的总字符预算 |
| `skill_compact_per_skill` | `5000` | 单个 skill 重附字符上限 |
| `forget_orphan_tool_results` | 策略相关 | 是否丢弃孤立的 tool_result 消息 |

## 相关代码位置

| 文件 | 内容 |
|---|---|
| `agent.py::compact_with_skills()` | 主入口，路径选择，skill 重附，session 保存 |
| `agent.py::_compact_chunked()` | 分批摘要核心实现 |
| `agent.py::_auto_compress_history()` | 轻量自动压缩（无 LLM） |
| `agent.py::_agentic_loop()` | auto-compact 触发点（`LLMContextWindowError` 捕获） |
| `history/compression.py` | `LLMSummaryStrategy` 等策略类（`/compact` 旧路径） |
| `prompts/user/compact_history.md` | 正常路径 compact prompt |
| `prompts/user/compact_chunk_request.md` | 分批路径 chunk prompt |
| `prompts/user/compact_merge_request.md` | 分批路径合并 prompt |

---

*最后更新：2026-06（新增分批摘要路径 `_compact_chunked`，解决历史超限时无法 compact 的问题；强化摘要 prompt 以保留工具调用结果等关键成果信息）*
