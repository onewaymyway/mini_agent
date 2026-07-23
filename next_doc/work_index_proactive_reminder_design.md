# work_index.json 主动提醒机制

> 背景：`update_work_thread` 工具此前完全依赖模型自主判断是否调用，没有任何
> 主动提示，实践中很容易长期是空文件（用户反馈："跑了很多任务，但一直没
> 看到里面有数据"）。本文档记录让这个机制"更主动一点"的设计与实现。

## 设计取舍：为什么不是"SessionEnd 时自动创建 WorkThread"

最直接的做法是在 SessionEnd hook 里，一旦启发式判断"这次活干得不少"，就
直接调用 `upsert_work_thread()` 自动建一条记录。但这违背了
`relate_session_to_work_thread()` 从一开始就有的保守取舍（见函数内注释：
"纯启发式不应该自由发明新工作线，避免误判导致 work_index.json 里出现大量
噪音条目"）——启发式只能判断"这次活儿看起来不小"，判断不出"这是不是真的
一个会跨多个 session 断续做的长期工作"（也可能只是一次性的大任务，做完
就彻底结束了，根本不需要跨 session 追踪）。这个判断只有模型自己能做。

因此最终方案是**"提醒，而非代劳"**：SessionEnd 时启发式判断只做记录，
把"要不要建 WorkThread"这个决策点，主动摆到下一次 session 开始时的模型
面前，决策权仍然在模型。

## 触发条件

SessionEnd 时（`agent/reflection.py::_update_workdir_knowledge_on_session_end`），
在原有 4.3（关联到已有 active WorkThread）逻辑之后，追加判断：

1. `cfg.workdir_knowledge.proactive_reminder_enabled`（默认 `True`）
2. 本次 session **没有**被关联到任何已有 active WorkThread
   （`relate_session_to_work_thread()` 返回 `None`）
3. 本次 session 达到"值得追踪"的规模阈值（两者任一满足）：
   - 时长 ≥ `cfg.workdir_knowledge.reminder_min_duration_minutes`（默认 15 分钟）
   - 真实用户轮次数 ≥ `cfg.workdir_knowledge.reminder_min_turns`（默认 6 轮）
4. 本次 session **没有**主动调用过 `update_work_thread`
   （`history.entry.history_contains_tool_call(history, "update_work_thread")`）

四者同时满足时，写入一条待提醒记录到
`.agent/work_thread_reminder.json`（`workdir_knowledge.write_work_thread_reminder()`）。
只保留最近一条（覆盖写），这是一次性便签，不是审计日志。

## 提醒的呈现与消费

`context_builder.py::_build_workdir_knowledge_block()` 在组装 system prompt
的 Workdir 知识层区块时，额外调用 `workdir_knowledge.pop_work_thread_reminder()`
——**读取的同时立即删除该文件**。这样：

- 提醒只会在下一次 session 的第一次 `build()` 调用时出现一次，不会每个
  turn 反复打扰模型；
- 如果模型看到提醒后判断"确实要追踪"，会调用 `update_work_thread`；判断
  "只是个一次性任务"，什么都不用做，提醒本身不会重复出现。

注入内容包含：上一次 session 的 id（截断）、时长、轮次数、session 开头的
用户输入摘要（截断到 120 字符），以及"如果这是跨 session 工作，考虑调用
update_work_thread；如果只是一次性任务，不需要做任何事，这条提醒不会再
重复"的说明——明确告诉模型"不采纳提醒是一个正常、无需解释的选项"，避免
诱导模型为了"配合提醒"而勉强创建不必要的 WorkThread。

## 涉及的文件

| 文件 | 改动 |
|---|---|
| `src/mini_agent/config/models.py` | `WorkdirKnowledgeConfig` 新增 `proactive_reminder_enabled`/`reminder_min_duration_minutes`/`reminder_min_turns` |
| `src/mini_agent/storage/paths.py` | 新增 `AgentPaths.workdir_work_thread_reminder` → `.agent/work_thread_reminder.json` |
| `src/mini_agent/history/entry.py` | 新增 `history_contains_tool_call(history, tool_name)`，检测 history 中是否已调用过某工具 |
| `src/mini_agent/perception/workdir_knowledge.py` | 新增 `write_work_thread_reminder()` / `pop_work_thread_reminder()` |
| `src/mini_agent/perception/__init__.py` | 导出上述两个新函数 |
| `src/mini_agent/agent/reflection.py` | SessionEnd 处理：捕获 4.3 关联结果，追加"未追踪工作"判断与写入 |
| `src/mini_agent/context_builder.py` | `_build_workdir_knowledge_block()` 追加读取+消费提醒并注入 system prompt |

## 验证

- 全部改动文件通过 `python3 -m py_compile`。
- 相关测试（`test_context_builder_global_knowledge.py`/
  `test_context_builder_wiki_search_primary.py`/
  `test_context_builder_workdir_knowledge.py`/`test_session_end_reflection.py`/
  `test_session_end_workdir_knowledge.py`/`test_workdir_knowledge.py`/
  `test_workdir_knowledge_tools.py`）共 202 个用例全部通过，无回归。
- 手工验证 `write_work_thread_reminder`/`pop_work_thread_reminder` 的
  写入→读取→自动清空→二次读取返回 `None` 全流程，以及
  `history_contains_tool_call()` 对 tool_use block 的识别。

## 配置示例（可选，写入 `agent_config.json` 覆盖默认值）

```json
{
  "workdir_knowledge": {
    "proactive_reminder_enabled": true,
    "reminder_min_duration_minutes": 15.0,
    "reminder_min_turns": 6
  }
}
```

不写入时使用上述默认值，与项目里其它"数据先于行为"的开关（如
`lesson_rules_enabled`）保持一致的默认开启取舍。

## 已知局限 / 未来可以再改进的方向

- 阈值是全局静态配置，不区分任务类型（例如"调研类"任务即使时间长也未必
  需要跨 session 追踪，但目前一视同仁）。如果后续发现噪音较多，可以考虑
  引入更细的信号（如是否有实际代码改动）而不仅是时长/轮次。
- 提醒只保留"最近一条"，如果连续好几个 session 都触发条件但用户没有让
  Agent 看到 context（比如中途 /compact 把这部分裁掉了），中间的提醒会被
  后面的覆盖掉、永久丢失。这是当前"便签"设计的有意简化，如果后续发现
  丢失提醒是个真实问题，可以改成有限长度的队列。
