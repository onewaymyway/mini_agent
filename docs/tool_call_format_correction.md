# 功能：工具调用格式纠错自动重试

## 问题

`_agentic_loop()` 原逻辑：`if not response.has_tool_calls: break`。
模型"想"调用工具但格式写坏了（标签未闭合、标签名混用、JSON 损坏）时，
`parse_tool_calls()` 解析失败 → `tool_calls=[]` → 直接 `break`，把半成品输出
当成最终答案，对话戛然而止。

## 方案

新增一轮"解析失败后"的格式异常检测，命中则自动以 `user` 角色注入纠错提示，
`continue` 循环而不是 `break`，让模型重新输出一次。

## 可扩展性

新增检测规则只需在 `format_correction_detector.py` 的 `_RULES` 列表里加一项
`(issue_type, detector_fn, prompt_template)`，不需要改任何调用方代码。

## 新增/修改文件

| 文件 | 改动 |
|---|---|
| `perception/format_correction_detector.py` | **新增**。规则注册表 + 检测逻辑，纯正则/字符串匹配，零 LLM 调用成本 |
| `history/entry.py` | 新增 `HType.FORMAT_CORRECTION` + `make_format_correction()` |
| `history/__init__.py` | 导出 `make_format_correction` |
| `history_manager.py` | 新增 `HistoryManager.append_format_correction()` |
| `config/models.py` | 新增 `FormatCorrectionConfig`（enabled / max_retries_per_turn / verbose），聚合进 `AppConfig.format_correction` |
| `config/__init__.py` | 导出 `FormatCorrectionConfig` |
| `config/loader.py` | `load_config()` 新增 `format_correction_enabled` / `format_correction_max_retries` / `format_correction_verbose` 三个 CLI 覆盖参数 + 配置文件 `format_correction` 字段解析 |
| `agent.py` | `_agentic_loop()` 中 `if not response.has_tool_calls` 分支新增纠错重试逻辑；新增 `_detect_format_issue()` helper |

## 已覆盖的检测规则

1. **`unclosed_tool_use`**：`<tool_use>` 开标签没有匹配的 `</tool_use>`（含标签重复出现、JSON 被截断）——对应用户报告的**案例1**
2. **`tag_role_confusion`**：请求标签 `<tool_use>` 与结果标签 `<tool_result>` 混用、不闭合——对应用户报告的**案例2**
3. **`invalid_json_in_tool_use`**：标签闭合正常但 JSON 本身损坏，且 `json_repair` 也救不回有效 `name` 字段
4. **`legacy_fence_unclosed`**：兼容旧版 ` ```tool_call ` 围栏格式未闭合
5. **`orphan_close_tag`**：存在 `</tool_use>` 等闭合标签，但规范开标签 `<tool_use>` 数量不足——对应**案例3**（`<tool_call>` 开、`</tool_use>` 闭）
6. **`tool_call_alias_tag`**：出现 `<tool_call>` / `<tool_invoke>` 等别名开标签，但没有规范 `<tool_use>`——对应**案例4**（非标准标签开头、内容截断）

详见 [格式纠错检测规则扩展说明](format-correction-detector-update.md)。

## 安全设计

- **宁可漏检，不可误判**：每条规则都要求看到明确的协议关键字（`<tool_use>`/`<tool_result>`/```` ```tool_call ````），不会对着一句正常的最终回复瞎猜
- **重试次数上限**（默认 2 次/轮，`cfg.format_correction.max_retries_per_turn`）：防止模型持续输出坏格式导致死循环，超限后退回旧行为（`break`，把当前文本当最终结果返回）
- **可整体关闭**：`cfg.format_correction.enabled = False` 完全退回原有逻辑
- **纠错提示明确标注是系统反馈**（`[System Notice]`），避免模型把它当成新的用户请求来回应，并在提示里直接给出一份格式正确的示例

## 测试

- `tests/test_format_correction_detector.py`：19 个纯单元测试（含用户报告的两个真实案例 + 各规则正负例）
- `tests/test_format_correction_integration.py`：6 个端到端集成测试，真实跑通 `Agent.run_turn()` → `_agentic_loop()`：
  - 案例1 / 案例2 触发纠错并重试成功
  - 纠错重试后产生合法工具调用，后续工具执行流程正常
  - 重试超限后正确 break，不死循环
  - 关闭开关后行为退回旧逻辑
  - 正常最终回复不会被误触发

全部测试通过（全量回归测试 1345 passed，含 2 个与本次改动无关的预先存在失败用例——`TestLLMDebugLogger` 里的日志截断边界断言）。
