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
7. **`tool_result_used_as_request`**（2026-07 新增）：`<tool_result>` 开闭标签自身完整闭合（因此不会被 `tag_role_confusion` 捉到，因为它要求开闭标签名不一致），但内容是带 `name` + `input` 字段的请求 payload——例如 `<tool_result>\n{"name": "read_file", "input": {...}}\n</tool_result>`。这是把"发起调用"误写成了"回填结果"标签，本质仍是标签角色误用，只是更隐蔽（标签数量配对、不触发未闭合/孤立闭合等规则）
8. **`write_file_truncated`**（2026-07 新增）：`write_file` / `create_file` 的 `<tool_use>` 块在写入内容中途被截断（未闭合到文本末尾，且能在残留内容里正则出 `"name": "write_file"` / `"create_file"`，片段长度超过阈值）。与前面几条不同，根因不是"格式写错"而是"内容太大一次性写不完"，因此**不适用**"请完整重发一次"的通用提示，命中后引导模型改为**分片写入再合并**（`<path>.part1`/`.part2`… + `cat` 合并）。该规则在 `_RULES` 中优先级最高（排在 `unclosed_tool_use` 之前），避免被更笼统的规则抢先命中、给出错误的修复建议。详见下方「与 Reminder 系统打通」一节。

## 补充规则扩展：非标准标签变体（案例 3/4）

> 本节原为独立文档（`format-correction-detector-update.md`），因与上文是
> "正文 + 补充说明"关系，已按 `documentation-guidelines.md` 的要求合并到本文。

### 问题：漏检的案例 3

上面 §已覆盖的检测规则 中的第 5/6 条（`orphan_close_tag`/`tool_call_alias_tag`）
上线前，原有规则能检测到以下几类格式错误：

- `tag_role_confusion`：`<tool_result>` 和 `</tool_use>` 混用
- `unclosed_tool_use`：`<tool_use>` 数量多于 `</tool_use>`
- `invalid_json_in_tool_use`：标签内 JSON 损坏
- `legacy_fence_unclosed`：` ```tool_call ` 未闭合

但以下案例全部漏检：

```
路径不对，让我确认：<tool_call>bash<arg_key>command": "pwd && find . -name "*.py"",
    "timeout": 10
}
</tool_use>
```

特征：
- 使用了非标准开标签 `<tool_call>`（而非 `<tool_use>`）
- 闭合用的是 `</tool_use>`
- 结果：`<tool_use>` 计数 = 0，`</tool_use>` 计数 = 1，所有原有规则均不命中

### 新增规则

**`orphan_close_tag`**——触发条件：存在任意闭合标签（`</tool_use>`、
`</tool_call>`、`</tool_invoke>` 等），但规范开标签 `<tool_use>` 的数量少于
闭合标签数量。典型场景：模型用 `<tool_call>` 开头、用 `</tool_use>` 闭合
（案例 3）；或闭合标签写了两次但开标签只有一次。错误提示：
> A `</tool_use>` (or similar closing tag) was found, but there is no matching `<tool_use>` opening tag before it. This usually means the opening tag used a non-standard name (e.g. `<tool_call>`) or was accidentally omitted.

**`tool_call_alias_tag`**——触发条件：存在非标准开标签变体（`<tool_call>`、
`<tool_invoke>`），且没有任何规范 `<tool_use>` 开标签。典型场景：模型用
`<tool_call>` 开头，内容还未写完就截断（无闭合标签）；或模型持续混用非
标准标签名。错误提示：
> A non-standard tag variant such as `<tool_call>` or `<tool_invoke>` was used instead of `<tool_use>`. Only `<tool_use>` is recognized — please resend the tool call using the correct tag.

> **注意**：若模型同时写了 `<tool_use>` 开标签，则交由其他规则处理，
> `tool_call_alias_tag` 不重复触发。

### 辅助正则

模块级新增两个复用正则，供现有规则和新规则共同使用：

```python
# 任意 tool 系列开标签（use / call / invoke 变体）
_ANY_TOOL_OPEN_RE  = re.compile(r"<tool_(?:use|call|invoke)\b[^>]*>", IGNORECASE)
# 任意 tool 系列闭合标签
_ANY_TOOL_CLOSE_RE = re.compile(r"</tool_(?:use|call|invoke)>",         IGNORECASE)
```

### 规则触发矩阵

| 场景 | `<tool_use>` | `</tool_use>` | `<tool_call>` | 命中规则 |
|------|:---:|:---:|:---:|----------|
| 正常完整标签 | 1 | 1 | 0 | — |
| 重复开标签 | 2 | 1 | 0 | `unclosed_tool_use` |
| 标签角色混淆 | 0 | 1 | 0 | `tag_role_confusion` |
| **案例 3**：call 开 + use 闭 | 0 | 1 | 1 | **`orphan_close_tag`** |
| **案例 4**：纯 call 开无闭合 | 0 | 0 | 1 | **`tool_call_alias_tag`** |

### 本次改动涉及文件

`perception/format_correction_detector.py`：新增 `_ANY_TOOL_OPEN_RE` /
`_ANY_TOOL_CLOSE_RE` 正则；新增 `_detect_orphan_close_tag()` /
`_detect_tool_call_alias_tag()` 检测函数；在 `_RULES` 列表追加两条规则。

### 扩展方式

如需增加新的格式错误检测，仍只需在 `_RULES` 末尾追加一个三元组：

```python
(
    "my_new_rule",          # issue_type 标识符
    _detect_my_new_rule,    # 检测函数：(text: str) -> bool
    "提示模型的错误说明文字\n",  # 注入到对话的纠错提示
),
```

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

## 补充修复：`system_tool_call.py` 解析正则过严（2026-07）

上面这套纠错重试机制解决的是"模型格式写坏了，引导它重写"；但还有一类问题更基础：
**模型格式其实是对的，只是解析器的正则太严格，把合法输出误判成了解析失败**，
根本不会触发上面任何一条纠错规则（因为规则本身也是靠正则识别 issue 类型的，
同样会被这类边界情况绕过）。

### 具体 bug

`_TOOL_USE_RE`（以及同款的 `_TOOL_CALL_LEGACY_RE`、`_TOOL_RESULT_RE`）原先写成：

```python
_TOOL_USE_RE = re.compile(r"<tool_use>\s*\n(.*?)\n\s*</tool_use>", re.DOTALL)
```

要求闭合标签 `</tool_use>` 前面**必须**有一个字面换行符。但模型偶尔会把闭合标签
紧贴在 JSON 末尾输出，中间没有换行：

```
<tool_use>
{"name": "bash", "input": {"command": "..."}}</tool_use>
```

这种情况下正则完全匹配不上，`parse_tool_calls()` 返回空列表，工具调用被整段
当成普通文本吞掉，模型这一轮的调用直接失效——且不会进入本文档描述的纠错重试
流程，因为标签本身是"闭合"的，触发不了 `unclosed_tool_use` 等规则。

### 修复

三个正则两端统一从 `\s*\n`（可选空白 + 强制换行）放宽为 `\s*`（可选空白，
零个或多个换行都行）：

```python
_TOOL_USE_RE = re.compile(r"<tool_use>\s*(.*?)\s*</tool_use>", re.DOTALL)
_TOOL_CALL_LEGACY_RE = re.compile(r"```tool_call\s*(.*?)\s*```", re.DOTALL)
_TOOL_RESULT_RE = re.compile(r"<tool_result>\s*(.*?)\s*</tool_result>", re.DOTALL)
```

`prompts/system/tool_call_protocol.md` 里给模型的格式说明（标签各自独占一行）
**保持不变**——那是对模型的规范性要求，仍然应该引导模型按标准格式输出；这次
只是让解析器对"格式基本正确但换行细节有出入"的情况更宽容，属于防御性解析，
不代表协议本身放宽了。

验证覆盖：原始换行紧贴闭合标签的 case、多行 JSON + 紧贴闭合、标准带换行格式、
前后夹杂其他文字、旧版 ` ```tool_call ` 围栏格式，共 5 种场景，均解析正确且
无回归。

## 统一化：与 Reminder 系统打通 + 新增 `write_file_truncated` 规则（2026-07）

### 背景

在此之前，`format_correction_detector.py` 每条规则的纠错提示文案都硬编码在
`_RULES` 列表里（Python 字符串），和 `reminders/` 目录下"工具出错/用户意图/
assistant 输出模式"那套已经支持 `.md` 文件自定义的 reminder 机制是两套互不
相通的系统。这带来两个问题：

1. 文案改动必须改代码、重新发版，用户无法自定义；
2. 出现了一类新场景——**大文件写入被截断**（`write_file`/`create_file` 的
   `<tool_use>` 块因为内容太大，输出到一半就断了）——虽然能被已有的
   `unclosed_tool_use` 规则捕捉到"没闭合"，但它给出的"请完整重发一次"建议
   对这个场景是错的：重发大概率还会在同样的位置被截断，陷入死循环。需要一条
   更具体的规则 + 一套"分片写入再合并"的针对性提示，而这套提示注定需要经常
   按项目实际情况调整（分片大小、是否有 append 类工具等），天然应该走可自定义
   的 reminder 文案，而不是写死在代码里。

### 方案：新增第 6 种 trigger_event —— `format_issue`

在原有 5 种 trigger_event（`tool_error` / `post_tool` / `user_intent` /
`pattern` / `pre_tool`）之外，新增：

| 类型 | 触发时机 | 常用 condition 字段 |
|------|----------|---------------------|
| `format_issue` | `format_correction_detector` 命中某条检测规则时 | `issue_type` |

`condition.issue_type` 是一个正则，匹配 `FormatIssue.issue_type`（即 `_RULES`
里每条规则的第一个元素，如 `unclosed_tool_use` / `write_file_truncated`）。

与其它 5 种 trigger_event 的关键区别：`format_issue` 命中后，调用方
（`agent/turn_loop.py`）不仅会注入对应 reminder 的内容，还会让 agentic loop
**自动 `continue` 到下一轮**，而不是把当前这个"半成品"输出当成最终答案直接
`break`——这一点由 `format_correction_detector` 检测 + `FormatCorrectionConfig`
的重试次数上限（`max_retries_per_turn`）控制，`format_issue_enabled` 开关只
影响"用哪份文案"，不影响"要不要自动续跑"。

### 调用链路

```
turn_loop.py: response.has_tool_calls == False
    └─> agent/reminders_correction.py: _detect_format_issue(response.text)
            ├─> perception/format_correction_detector.detect_format_issue(text)
            │       → 纯正则判定，返回 FormatIssue(issue_type, message=<内置默认文案>)
            └─> 若 self._reminder_mgr 存在：
                    ReminderManager.check_format_issue(issue_type)
                        → matcher.match_format_issue()
                        → 按 trigger_event=format_issue + condition.issue_type 匹配
                    命中则用 reminder 内容替换默认文案（PROMPT_HEADER + reminder.content）
                    未命中/未启用则保留 detect_format_issue 自带的内置默认文案
    └─> self._hist.append_format_correction(issue.message)
    └─> continue（回到循环顶部重新调用一次 LLM）
```

检测规则本身（"是否命中某个 issue_type"）**仍然只在**
`format_correction_detector.py` 里维护，不受 reminder 系统影响——reminder
系统只负责"命中之后展示什么文案"，职责边界清晰：**检测逻辑属于代码，提示
文案属于配置**。

### 新增规则：`write_file_truncated`

判定条件（`_detect_incomplete_large_write`）：

1. 存在未闭合的 `<tool_use>`（或别名 `<tool_call>`/`<tool_invoke>`）开标签
   （开标签数 > 闭标签数，兼容三种标签名，覆盖面比只认 `<tool_use>` 更广）；
2. 最后一个未闭合开标签之后的内容里，能正则匹配到
   `"name": "write_file"` 或 `"name": "create_file"`（不要求 JSON 合法——内容
   本来就是被从中间截断的）；
3. 该片段长度超过 `_LARGE_WRITE_MIN_CHARS`（默认 2000 字符），排除"刚开了个
   头就中断"这种明显不是"内容太大"导致的小片段，避免和 `unclosed_tool_use`
   抢命中。

在 `_RULES` 中的优先级**高于** `unclosed_tool_use`（排在最前面），因为二者
判定范围有重叠（"截断的大文件写入"本身也满足"未闭合"），必须让更具体的规则
先命中，否则会被笼统的"请完整重发一次"提示盖掉。

默认（无 reminder 系统 / 未匹配到自定义文案时的）兜底文案，引导模型改为
分片写入再合并；正式展示给模型的文案由
`src/mini_agent/prompts/reminders/format_issue_write_file_truncated.md`
提供，可在 `reminder.custom_dir` 下放同名 `issue_type` 的文件覆盖（例如
调整分片大小、补充项目里实际存在的 `append_file` 之类工具的用法）。

### 已迁移的 8 条旧规则文案

其余 8 条已有规则（`tag_role_confusion` / `tool_result_used_as_request` /
`bare_name_after_tag` / `unclosed_tool_use` / `invalid_json_in_tool_use` /
`legacy_fence_unclosed` / `orphan_close_tag` / `tool_call_alias_tag`）的文案
也一并迁移为 `prompts/reminders/format_issue_<issue_type>.md`，`_RULES` 里
原来的硬编码字符串保留作为**兜底默认值**（reminder 系统被整体禁用，或用户
手滑删了对应文件时仍能正常工作，不会因为缺文件就失去纠错能力）。

### 新增/修改文件（本次）

| 文件 | 改动 |
|---|---|
| `reminders/loader.py` | 新增 `TRIGGER_FORMAT_ISSUE` 常量；`ReminderCondition` 新增 `issue_type` 字段；`_parse_file()` 接受 `format_issue` 作为合法 `trigger_event` |
| `reminders/matcher.py` | 新增 `match_format_issue(issue_type)` |
| `reminders/manager.py` | 新增 `check_format_issue(issue_type)` |
| `config/models.py` | `ReminderConfig` 新增 `format_issue_enabled` 开关 |
| `config/loader.py` | 配置文件 `reminder.format_issue_enabled`（及此前遗漏接入的 `pre_tool_enabled`）解析进 `ReminderConfig` |
| `perception/format_correction_detector.py` | 新增 `_detect_incomplete_large_write()` + `write_file_truncated` 规则（插入 `_RULES` 最前）；`PROMPT_HEADER` 改为公开导出，供调用方拼接自定义文案时复用；`write_file_truncated` 不再套用"请完整重发一次"的通用 footer |
| `agent/reminders_correction.py` | `_detect_format_issue()` 改为优先查询 `self._reminder_mgr.check_format_issue(issue_type)`，命中则替换默认文案 |
| `prompts/reminders/format_issue_*.md`（9 个） | 新增：8 条旧规则文案迁移 + 1 条新规则 `write_file_truncated` |

### 测试

- 单元测试覆盖：`_detect_incomplete_large_write` 的正例（截断的大文件写入）、
  反例（正常闭合的 write_file 调用、片段过短的截断、非写文件类工具的截断）；
  `_RULES` 优先级（同时满足 `write_file_truncated` 和 `unclosed_tool_use` 条件
  的输入应命中前者）。
- `reminders` 子系统：`ReminderLoader` 能正确加载 `trigger_event: format_issue`
  的文件；`ReminderMatcher.match_format_issue()` 按 `issue_type` 精确/正则匹配；
  `ReminderManager.check_format_issue()` 受 `format_issue_enabled` 开关控制。
- 集成测试：`format_issue_enabled=False` 时自动退回 `format_correction_detector`
  内置默认文案，检测和自动 `continue` 续跑逻辑不受影响。

