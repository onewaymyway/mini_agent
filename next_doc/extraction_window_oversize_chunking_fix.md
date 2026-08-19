# 轻量抽取窗口超限（LLMContextWindowError）— 自动分片重试 + 游标保底推进

## 1. 现象

```
LLMContextWindowError: ... The input (623348 tokens) is longer than the
model's context length (524288 tokens). ...
where: mini_agent.history_manager.HistoryManager._dispatch_lightweight_extraction
```

## 2. 先纠正一个误判：游标其实没有卡死

排查这条报错时最初怀疑"游标卡在原地、每次都重放同一个必然失败的超大
窗口"——实际验证后发现**不是**：`_dispatch_lightweight_extraction()`
内部本来就有 `try/except` 把异常吞掉（`log_exception()` 默认
`reraise=False`，不会重新抛出），所以调用方 `_maybe_trigger_extraction_
impl()` 里紧跟着的 `save_extraction_cursor()` 一直能正常执行——游标
是会推进的。

真正的问题是：**这一整段窗口的内容会被直接静默丢弃、再也不会被抽取**，
而不是"卡住重放"。丢弃本身不会导致死循环，但会造成知识抽取出现永久性
的空洞（这段时间的 decision/entity/fact 全部漏抽）。

## 3. 根因：窗口没有任何大小上限

`history/extraction_trigger.py::scan_for_extraction_window()`：

```python
end_index = len(raw_entries)   # 永远是"游标之后的全部新增内容"
```

无论是常规触发（连接词密度 / 实体密度 / 轮次计数三条规则任一命中）
还是 session 结束时的 `force=True` 兜底，窗口的终点始终是"当前
raw_entries 的末尾"，**没有任何逐次调用的大小上限**。如果这段时间里
三条规则一直没命中——比如长时间在跑工具、用户输入轮次很少但工具输出
本身很大（`turn_count` 靠 `is_turn_boundary` 数的是用户输入轮次，不是
工具调用次数）——未处理的原始条目会持续累积，直到终于触发（或者
session 结束强制兜底）时，一次性把攒了很久的全部内容打包成一个窗口，
就可能像这次一样远超模型的上下文上限（623348 > 524288 tokens）。

不是"取历史记录取错了"（切片逻辑 `raw_entries[start_index:end_index]`
本身没有 bug，语义就是"游标之后的全部新增内容"，这是设计上刻意的），
而是**从设计上就没有对单次窗口大小做过任何拆分/上限控制**，长期未触发
的场景下必然会攒出超大窗口。

## 4. 修复

### 4.1 超限时自动按条目数二分，递归重试

`history_manager.py`：

- `_dispatch_lightweight_extraction()` 现在只是入口，实际逻辑委托给新增的
  `_dispatch_extraction_window(paths, raw_entries, start_index, end_index,
  trigger_reason, llm_client, depth)`。
- 捕获 `LLMContextWindowError`（而不是笼统的 `Exception`）时：
  - 若窗口宽度（`end_index - start_index`）已经缩到 1，或递归深度达到
    `_MAX_EXTRACTION_SPLIT_DEPTH`（6，最多切成 64 份），说明缩无可缩
    （多半是单条内容本身极端巨大，比如一次性读了个超大文件的工具结果），
    放弃这个片段，记录清楚跳过了哪个 `[start, end)` 范围方便事后排查，
    不影响其它片段。
  - 否则按条目数取中点二分成两半，各自递归调用自己重试——尽量多抢救
    一部分内容，而不是"只要有一部分超限就整段放弃"。
- 其它异常（非上下文超限）维持原样：记录日志后直接放弃，不做二分（因为
  拆分窗口大小无助于解决限流/网络等其它类型的错误）。

### 4.2 游标推进从"隐式安全"改成显式保证

`_maybe_trigger_extraction_impl()` 里原来是两条顺序语句：

```python
self._dispatch_lightweight_extraction(paths, raw_entries, candidate, llm_client)
save_extraction_cursor(paths, candidate.end_index)
```

游标能否推进完全依赖"上一行不会让异常逃逸"这个隐含前提——一旦未来
改动不小心在某个分支漏加 try/except，这里就会直接抛出，导致游标永远
推进不到，下次触发时重新算出同一个（只会更大不会更小）的窗口，陷入
真正的死循环。改成：

```python
try:
    self._dispatch_lightweight_extraction(paths, raw_entries, candidate, llm_client)
finally:
    save_extraction_cursor(paths, candidate.end_index)
```

不管抽取内部是正常返回、吞掉异常后返回、还是（未来某天）意外让异常
逃逸，游标推进这一步都保证会执行。抽取失败顶多是"这段内容没抽到"，
不应该连累游标卡死。

## 5. 效果与验证

隔离测试验证了两种场景（不依赖真实 LLM，用假的 `chat_with_retry` 模拟
"窗口宽度 > 2 就报 LLMContextWindowError"）：

1. 窗口从 8 条二分到 2 条以内即可成功——验证递归二分确实发生、确实能
   在缩小后成功抽取到内容（而不是整段放弃）。
2. 即使子窗口无论怎么切都始终失败（模拟"单条内容本身就超限"的极端
   情况），`try/finally` 依然保证游标推进到位——不会卡死。

## 6. 涉及文件

- `src/mini_agent/history_manager.py`
  — `_dispatch_lightweight_extraction()` 改为入口 + 新增
    `_dispatch_extraction_window()` 递归二分实现
  — `_maybe_trigger_extraction_impl()` 游标推进改为 `try/finally`

## 7. 局限 / 后续可选优化

- 二分是按**条目数**而不是按 token 数切的——如果某个窗口里前一半明显
  比后一半大很多（比如前半段有个巨型工具输出），第一次二分后不一定
  刚好落在能过关的大小，会继续递归，但最终仍会收敛（每次至少减半，
  `_MAX_EXTRACTION_SPLIT_DEPTH=6` 对应最多 64 份，通常足够）。
- ~~没有从根源上限制"单次窗口最多包含多少条目/多少估算 token"~~——
  已在 §8 补上，见下文。

## 8. 补充修复：窗口预算上限（源头预防，而不是超限后二分）

### 8.1 起因

排查一次相关问题时发现：`_dispatch_lightweight_extraction()` /
`_dispatch_extraction_window()` 用的是 raw history（`self._raw.entries`，
完整未截断的原始事件日志），而 compact 用的是处理后的 `self._history`——
两者本来就不是同一份数据，raw 天然更大。这不是 bug，是
`extraction_trigger.py` 顶部注释里说明过的既定设计（抽取要跟 raw history
走，坐标才不会因为 compact 清空/重置 `self._history` 而失效）。

但 §6 的二分重试仍然只是"超限了再二分"的事后补救，没有对齐 compact 那边
`agent/compaction.py::_compact_chunked()` 已经在用的"提前按 token 预算
主动切分"（`chunk_budget_chars = model_ctx_tokens * 0.50 * CHARS_PER_TOKEN`）
思路。

### 8.2 修复

- `history/extraction_trigger.py::scan_for_extraction_window()` 新增
  `max_window_chars` 参数：游标之后新增内容的估算字符数一旦超过这个预算，
  不再等待连接词/实体/轮次三条规则是否命中，直接按预算截断，返回一个
  `trigger_reason="size_cap"` 的候选窗口（新增 `_cap_window_by_chars()`
  按条目边界从游标处累加字符数，至少纳入一条，超预算即停）。
  `ExtractionWindowCandidate` 新增 `truncated: bool` 字段标记这次窗口是否
  被提前截断——截断不等于丢内容，游标只是推进到截断处，剩余部分下次
  扫描时仍会被看到。
- `history_manager.py` 新增 `_extraction_window_max_chars()`：显式配置
  `CompressConfig.extraction_trigger_max_window_chars`（默认 0）时直接
  采用；否则按 `llm_client.context_window` / `cfg.model_context_window`
  动态换算（`ctx_tokens * 0.5 * 3 chars/token`），与 `_compact_chunked()`
  的 `chunk_budget_chars` 同一套估算口径，取不到模型上下文时退化为
  100K token 默认值。计算失败不影响触发判断本身，静默返回 `None`（等价于
  "不设预算上限"，退回本次改动前的行为）。
- `_maybe_trigger_extraction_impl()` 里非 `force` 路径调用
  `scan_for_extraction_window()` 时传入这个预算；`force=True`（session
  结束兜底）路径不受影响，仍然是"一次性收尾剩余全部内容"，超限风险继续
  由 §6 的递归二分兜底吸收（session 结束时优先保证不丢内容，超限了二分
  兜底比预算截断更合适——截断会把"这次没抽完"的状态留到下一次运行，但
  session 已经结束就没有下一次了）。

### 8.3 效果

常规触发路径下，窗口在累积阶段就会被主动限制在预算内，`_dispatch_
extraction_window()` 的递归二分理论上只会在"单条内容本身极端巨大"这类
预算估算失准的场景下才会被触发，而不再是"长期不触发攒出天文数字窗口"
的常态路径。

### 8.4 涉及文件（本次新增/修改）

- `src/mini_agent/history/extraction_trigger.py`
  — 新增 `_entries_char_len()` / `_cap_window_by_chars()`
  — `ExtractionWindowCandidate` 新增 `truncated` 字段
  — `scan_for_extraction_window()` 新增 `max_window_chars` 参数与
    `"size_cap"` 触发分支
- `src/mini_agent/history_manager.py`
  — 新增 `_extraction_window_max_chars()`
  — `_maybe_trigger_extraction_impl()` 非 force 路径接入该预算
- `src/mini_agent/config/models.py`
  — `CompressConfig` 新增 `extraction_trigger_max_window_chars: int = 0`
- `tests/test_extraction_trigger.py`
  — 新增 4 个 `size_cap` 相关用例（超预算截断 / 预算过小仍保底纳入一条 /
    预算充足不误触发 / 预算充足时正常让位给 connective_density）
