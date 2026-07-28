# mechanism_validation_demo

验证 workflow 机制改进 **P12 → P15** 四轮迭代全部新特性的端到端 demo。
文件夹模式 workflow，路径：`.agent/workflows/mechanism_validation_demo/`。

## 覆盖的特性 / 对应 step

| 特性 | 来源 | 对应 step |
|---|---|---|
| `script` 的 `result_file` structured 模式 | P15 | `generate_words`、`compute_stats` |
| `foreach` 批处理（含并发、`result_file` 字段访问取列表） | P13 | `enrich_each_word` |
| `wait` 可中断等待 | P13 | `pause_before_batch` |
| `merge`：`json_merge` 策略 | P14 | `merge_words_and_stats` |
| `merge`：`json_array` 策略 | P14 | `merge_array_demo` |
| `merge`：`concat_text`（默认）策略 | P14 | `merge_final_report` |
| `tool_call` 的 `tool_args` 占位符 | P12 Phase2 | `notify_tool_call` |
| `{step_id.result_file:a.b[0].c}` 字段级占位符（prompt 与 tool_args 两处都用到） | P12 Phase3 | `notify_tool_call`、`optional_conclusion` |
| `condition` 正常求值（写对表达式、走 `True`/`SKIPPED` 分支） | P12 Phase1 | `optional_conclusion` |
| `python_step` 消费上游 `result_file`/`foreach` 聚合结果 | 既有能力，用于串联 | `summarize_report` |

`condition` 求值异常 → `NEEDS_FIX`（P12 Phase1）与 workflow 级熔断
`circuit_breaker_distinct_step_threshold`（P14 Phase2）这两项是"错误路径"
特性，不适合放进一条正常应该跑通的 happy-path workflow 里，验证方法见本
文档末尾"如何单独验证错误路径"一节。

## 运行前置条件

`generate_words`/`compute_stats` 是 `type: script`，`summarize_report` 是
`type: python_step`，两者默认都被关闭，运行前需要在项目的
`agent_config.json` 里显式打开：

```json
{
  "workflow": {
    "script_step_enabled": true,
    "python_step_enabled": true
  }
}
```

`enrich_each_word`/`optional_conclusion` 是 `type: agent`，会真实调用一次
LLM（`enrich_each_word` 会因为 `foreach_max_concurrency: 2` 并发调用两次），
运行会产生真实 token 消耗。

## 如何运行

```
# Agent 对话内
run_workflow("mechanism_validation_demo", "{}")

# 或独立 CLI（不进交互 REPL）
mini-agent workflow run mechanism_validation_demo '{}' --project <项目根目录>
```

跑完后建议依次检查：

1. `show_workflow("mechanism_validation_demo")` / `/workflow show` 确认
   `validate()` 无报错（保存时已经过一次校验，这里是二次确认展开后的
   定义符合预期）。
2. `get_workflow_run_status(workflow_session_id, verbose=true)`：
   - `generate_words`/`compute_stats` 状态应为 `done`，`result_file`
     字段非空。
   - `enrich_each_word` 输出应为一段 JSON 数组文本，长度为 4（对应
     `["alpha","beta","gamma","delta"]`），每个元素形如
     `{"item_index": i, "output": "..."}`。
   - `summarize_report` 的 `output_file`（`summary.json`）里
     `total_words` 应为 4，`enriched_count` 通常也是 4（除非某个元素
     LLM 调用失败，那种情况下 `error_count` 会 > 0，属于
     `foreach_stop_on_error=false` 的预期容错行为，不代表 workflow 本身
     出错）。
   - `merge_words_and_stats` 的输出应是一个同时包含 `words`/
     `generated_by`/`timestamp` 三个字段的 JSON object。
   - `merge_array_demo` 的输出应是一个长度为 2 的 JSON 数组。
   - 项目根目录下应出现 `mechanism_validation_notify.txt`，内容里能看到
     被替换成真实值的 `alpha`（`generate_words.result_file:words[0]`）
     和 `summarize_report` 落盘文件的绝对路径。
   - `optional_conclusion` 状态应为 `done`（`condition` 求值为 `True`，
     不是 `SKIPPED`，因为 `merge_final_report.output` 非空）。
3. `preview_workflow("mechanism_validation_demo", "{}")` 可以在正式跑之前
   先看一遍并发分批结果和占位符替换预览，尤其适合改动过这份 YAML 之后
   先确认没有 `unresolved_placeholders`。

## 如何单独验证错误路径（不建议混进本 workflow）

### `condition` 求值异常 → `NEEDS_FIX`（P12 Phase1）

临时用 `patch_workflow_step` 把 `optional_conclusion` 的 `condition` 改成
一个引用不存在字段的表达式，比如：

```
patch_workflow_step(
  name="mechanism_validation_demo", step_id="optional_conclusion",
  patch='{"condition": "merge_final_report.no_such_field.x"}'
)
```

重新执行到这一步时，应该看到该 step 状态是 `needs_fix`（不是
`skipped`），`error_type` 是 `AttributeError` 一类，且
`get_workflow_run_status(verbose=true)` 会提示"这是定义/配置问题，重跑
无效"。验证完记得 `patch_workflow_step` 改回
`"merge_final_report.output != ''"`。

### workflow 级熔断 `circuit_breaker_distinct_step_threshold`（P14 Phase2）

这是全局配置，不是这份 workflow 定义能单独控制的字段。临时验证方法：

1. 在 `agent_config.json` 里设置一个很低的阈值，比如
   `{"workflow": {"circuit_breaker_distinct_step_threshold": 2}}`。
2. 临时把 `generate_words`/`compute_stats` 的 `script` 字段改成一个必定
   失败、且失败原因相同（同一个 `error_type`）的命令（比如都改成
   `python3 -c "raise ValueError('boom')"`），跑一次。
3. 两个不同 step 因同一 `error_type` 失败达到阈值后，
   `get_workflow_run_status(verbose=true)` 应该能看到
   `circuit_breaker_tripped=true` 和熔断原因，且整次执行会被提前
   `cancel`，而不是等每个 step 各自耗尽 `retry_on_error` 预算。
4. 验证完记得把 `script` 字段和 `circuit_breaker_distinct_step_threshold`
   都改回来，不要把这次破坏性改动留在正式 workflow 定义里。
