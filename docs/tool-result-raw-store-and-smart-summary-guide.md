# 工具结果智能截断：原始输出留存 + LLM 智能摘要

对应改进点：[SYS-RAWSTORE] 原始输出留存、[SYS-SMARTTRIM] LLM 智能摘要。

## 背景 / 解决的问题

工具调用结果超长时，`ToolExecutor` 会截断结果再写入历史，避免撑爆上下文。
在这次改进之前，截断是**纯规则**的（bash 保留头尾+关键行、read_file 滑动窗口、
grep/glob 截断行数），并且截断掉的部分**直接丢弃**：

- 规则截断无法理解语义，可能把真正有用的信息切掉，保留的是噪音；
- 原始内容一旦丢弃，agent 之后即使意识到自己需要更多上下文，也无法再看到完整原文。

本次改进解决这两个问题，但**默认行为向后兼容**：不开启任何新配置时，行为与之前完全一致
（只是截断后原文默认会被留存，可回看）。

---

## 能力一览

| 能力 | 默认状态 | 说明 |
|---|---|---|
| 原始输出留存（RawResultStore） | **默认开启** | 只要发生截断/摘要，原文都会存一份，几乎零成本 |
| `view_raw_result` 工具 | 随留存功能一起可用 | agent 用它按 `result_id` 回看完整原文，支持行号范围 |
| LLM 智能摘要（smart_summary） | **默认关闭** | 需要显式开启；结果超大时用 LLM 提炼关键信息，而不是规则截断 |

---

## 一、原始输出留存（RawResultStore）

### 工作流程

```
工具输出超过 tool_trim.threshold
        │
        ▼
  _trim_result() 生成截断/摘要后的文本 trimmed
        │
        ▼
  trimmed != 原文 ?  ──否──▶ 直接返回 trimmed（未截断，无需留存）
        │ 是
        ▼
  RawResultStore.put(原文) → 得到 result_id（内容 md5 短哈希，天然去重）
        │
        ▼
  返回 trimmed + 提示：
  "[full output stored — N chars total.
    Use view_raw_result(result_id="xxxxx") to inspect the original,
    optionally with start_line/end_line.]"
```

agent 看到这条提示后，如果判断截断/摘要后的内容不够用，可以主动调用
`view_raw_result` 工具取回完整原文——用法与 `read_file` 一致，支持
`start_line` / `end_line`，避免一次性把整段原文重新塞回上下文。

> **[FIX] `view_raw_result` 的结果绝不会被再次截断。**
> `view_raw_result` 存在的意义就是"取回完整原文"，因此它的返回结果会跳过
> `_trim_result` 的截断/摘要流程（`tool_executor.py::_trim_result` 对
> `tool_name == "view_raw_result"` 直接原样返回），命令行渲染
> （`renderer.py::print_tool_result`）也不会再套用默认的 2000 字符预览截断。
> 无论通过 agent 取回的结果，还是命令行里直接打印出来的结果，看到的都是
> 完整原文，不会出现"取回的原文其实还是被截断过的一段"这种情况。

### 存储特性

- **session 内内存 LRU**，不做跨进程持久化（session 结束随进程释放）。
- 双重容量限制，任一超限从最久未访问的条目开始淘汰：
  - `raw_store_max_entries`（条目数，默认 128）
  - `raw_store_max_total_chars`（总字符数，默认 5,000,000）
- 同一段原文被多次截断只存一份（按内容 md5 去重）。
- 线程安全（一把锁，session 内调用频率低，开销可忽略）。

### 相关配置（`ToolTrimConfig`）

```python
raw_store_enabled: bool = True             # 是否留存原始输出
raw_store_max_entries: int = 128           # LRU 容量上限（条目数）
raw_store_max_total_chars: int = 5_000_000 # 总字符数上限
```

对应配置文件 key：`raw_store_enabled` / `raw_store_max_entries` / `raw_store_max_total_chars`。

### `view_raw_result` 工具

```json
{
  "name": "view_raw_result",
  "input": {
    "result_id": "a1b2c3d4e5f6",
    "start_line": 100,
    "end_line": 150
  }
}
```

- `result_id`：必填，来自某次被截断/摘要过的工具结果末尾的提示文本。
- `start_line` / `end_line`：可选，不传则返回全部原文。
- `result_id` 不存在（已被淘汰或输入错误）时返回
  `[error: no stored raw result found for result_id=... ]`，不会抛异常。
- 该工具本身是只读、幂等的：已加入权限白名单（`_SAFE_TOOLS`，无需审批）
  和 turn 内去重集合（`_DEDUP_TOOLS`）。

---

## 二、LLM 智能摘要（smart_summary）

### 动机

规则截断是"一刀切"：bash 保留头尾行、read_file 滑动窗口，无法判断"这段输出里
哪些内容和当前调用真正相关"。当单次工具结果特别长（比如几万字符的构建日志、
测试报告）时，规则截断要么保留太多噪音，要么切掉关键错误信息。

开启 `smart_summary` 后，超过 `smart_summary_threshold` 的结果会先尝试
调用 LLM，用专门的 prompt（`prompts/system/tool_result_summarizer.md`）
提炼出"与本次工具调用目的相关的关键信息"——具体的报错、路径、数字、命令名
等细节要求原样保留，不能被泛化转述成"输出有一些日志"这种空话。

### 触发条件与降级

```
len(result) > threshold                      # 未超过，原样返回
        │
        ▼
smart_summary_enabled=True
  且 len(result) > smart_summary_threshold    # 否则走原有规则截断
        │ 是
        ▼
len(result) > smart_summary_max_input_chars ?  ──是──▶ 原文太大，摘要也不现实，直接走规则截断
        │ 否
        ▼
调用 LLM 摘要
        │
   ┌────┴────┐
  成功       失败/异常/空响应
   │           │
返回摘要文本   静默降级为规则截断（不阻塞、不抛异常）
```

**任何环节失败都会自动降级到原有的规则截断**，不会因为 LLM 调用超时/报错/
返回空内容而影响工具调用主流程——这是刻意设计：摘要是"增强"，不能变成
新的失败点。

### 相关配置（`ToolTrimConfig`）

```python
smart_summary_enabled: bool = False          # 默认关闭
smart_summary_threshold: int = 12000         # 触发摘要的字符数阈值（应 >= threshold）
smart_summary_max_input_chars: int = 60000   # 喂给摘要模型的原文上限
smart_summary_model: str = ""                # 摘要用的模型名，留空则复用当前主模型
```

对应配置文件 key：`smart_summary_enabled` / `smart_summary_threshold` /
`smart_summary_max_input_chars` / `smart_summary_model`。

开启方式（`~/.mini_agent/config.json` 或项目级配置文件）：

```json
{
  "smart_summary_enabled": true,
  "smart_summary_threshold": 12000,
  "smart_summary_model": "claude-haiku-4-5"
}
```

> `smart_summary_model` 是可选的模型切换点：如果当前 `LLMClient` 实现了
> `with_model(model_name)` 方法，摘要调用会优先用这个更便宜/更快的模型；
> 没有实现该方法或未配置时，直接复用当前主模型（会产生一次与主模型同规格
> 的额外调用，请评估费用后再开启）。

### 摘要文本格式

摘要成功后，写入历史的文本形如：

```
[LLM-extracted summary of bash output (18234 chars original)]
<LLM 提炼出的关键信息文本>

[full output stored — 18234 chars total. Use view_raw_result(result_id="...") to inspect the original, optionally with start_line/end_line.]
```

即：摘要文本本身 + 原始输出留存提示，两者总是配套出现（前提是
`raw_store_enabled=True`）。

---

## 三、与已有截断机制的关系

- **默认行为完全不变**：`smart_summary_enabled` 默认 `False`，未开启时走的
  仍然是原来的规则截断逻辑（`ToolExecutor._rule_trim`，代码逻辑与之前一致，
  只是从 `_trim_result` 内部拆出来复用）。
- 规则截断（`_rule_trim`）继续作为：
  1. `smart_summary` 关闭时的默认策略；
  2. `smart_summary` 开启但结果长度介于 `threshold` 和 `smart_summary_threshold`
     之间时的策略（避免中等长度的结果也去调用 LLM，控制成本）；
  3. LLM 摘要失败时的降级兜底策略。
- 原始输出留存（`raw_store_enabled`）独立于 `smart_summary`，两条路径
  （规则截断 / LLM 摘要）只要发生了实质性截断都会留存原文。

---

## 四、代码位置

| 文件 | 作用 |
|---|---|
| `src/mini_agent/perception/raw_result_store.py` | `RawResultStore`：原始结果的 session 内 LRU 仓库 |
| `src/mini_agent/tool_executor.py` | `_trim_result` / `_remember_raw` / `_smart_summarize` / `_rule_trim` |
| `src/mini_agent/tools/builtin.py` | `view_raw_result` 工具 + `configure_raw_result_store` 注入入口 |
| `src/mini_agent/prompts/system/tool_result_summarizer.md` | 摘要模型的 system prompt |
| `src/mini_agent/prompts/user/tool_result_summary_request.md` | 摘要请求的 user prompt 模板 |
| `src/mini_agent/config/models.py` | `ToolTrimConfig` 新增字段 |
| `src/mini_agent/config/loader.py` | 新增字段的配置文件/CLI 加载 |
| `src/mini_agent/agent.py` | 初始化 `RawResultStore`、注入到 `ToolExecutor` 和 `builtin` 模块 |
| `tests/test_raw_result_and_smart_summary.py` | 单元测试 |

---

## 五、测试覆盖

`tests/test_raw_result_and_smart_summary.py` 覆盖：

- `RawResultStore` 基本存取、内容去重、按条目数/总字符数淘汰；
- 规则截断路径下原文可留存、可通过 `result_id` 取回；
- 关闭 `raw_store` 时截断文本不包含留存提示；
- 短结果不截断、不留存；
- `smart_summary` 开启且超过其阈值时优先调用 LLM，未达阈值时不调用；
- LLM 调用异常时自动降级为规则截断，不抛异常；
- 原文超过 `smart_summary_max_input_chars` 时跳过 LLM 直接走规则截断；
- `view_raw_result` 工具的正常取回、行号范围、未知 id、未配置仓库三种情形。

---

## 六、后续可扩展方向（未在本次改动范围内）

- 跨 session 持久化（当前原始结果只在单个 session 进程内有效）；
- 对特别大的原文分块摘要，突破 `smart_summary_max_input_chars` 限制；
- 按工具类型定制摘要 prompt（例如测试报告 vs 构建日志用不同侧重点）。
