# History 条目类型化与 Raw History 设计

> 本文说明 mini_agent 对对话历史（history）的类型化改造：每条条目携带 `_type` 字段和时间戳 `_ts`，并引入独立的 Raw History 层保存完整未裁剪历史。

---

## 1. 背景与动机

### 改造前的问题

改造前，history 中的消息仅有 `role` 和 `content` 字段。压缩策略、反思机制、会话恢复等子系统需要识别消息来源时，依赖字符串前缀猜测：

```python
# 旧代码：靠前缀猜测消息种类（脆弱）
if content.startswith("<tool_result"):    # 工具结果
if content.startswith("[Previous"):       # 压缩占位符
if content.startswith("[Compressed"):     # 摘要占位符
```

这带来以下问题：

- **前缀可变**：任何格式变化都会悄然破坏依赖它的所有地方
- **压缩切割不精确**：turn 边界靠猜，容易切出孤立工具结果或截断 turn 中间
- **反思质量差**：反思 prompt 无法区分"用户真实意图"和"工具噪音"，导致摘要质量不稳定
- **丢失历史**：`/compact` 后原始信息永久丢失，无法追溯

### 改造目标

1. 每条 history 条目携带 `_type` 字段，明确标注消息来源
2. 引入 Raw History：只追加、不删除的事件日志，保存所有原始信息
3. Raw History 每条条目携带 `_ts`（UTC 时间戳），支持时序分析
4. 提供 `replay()` 函数：输入 raw history，精确还原任意时刻的 active history
5. 发给 LLM 的消息通过唯一出口 `to_llm_messages()` 剥离内部字段，保持 API 兼容

---

## 2. 核心概念

### 两层 History

```
Active History（当前状态）              Raw History（完整事件日志）
────────────────────────────            ────────────────────────────────────
用户消息 A          [user_input]        用户消息 A        [user_input, _ts=T1]
Assistant 回复      [assistant_reply]   Assistant 回复    [assistant_reply, _ts=T2]
工具结果            [tool_result]       工具结果          [tool_result, _ts=T3]
用户消息 B          [user_input]        用户消息 B        [user_input, _ts=T4]
                                        ── compact_event ──  [_ts=T5]  ← /compact 事件
压缩占位符          [compressed]        压缩占位符        [compressed, _ts=T5]
压缩摘要            [compact_summary]   压缩摘要          [compact_summary, _ts=T5]
用户消息 C          [user_input]        用户消息 C        [user_input, _ts=T6]
```

- **Active History**（`history.json`）：当前对话状态，经历压缩后只保留压缩后的内容。含 `_type`，不含 `_ts`。
- **Raw History**（`raw_history.json`）：完整事件流，只追加从不删除，压缩前的原始消息永久保留，外加 `compact_event` 记录压缩操作。含 `_type` 和 `_ts`。

**关键性质**：Active History 是 Raw History 的确定性函数，`replay(raw_history)` 可以精确还原。

---

## 3. 类型枚举（`HType`）

定义于 `src/mini_agent/history/entry.py`：

| 类型值 | 含义 | role |
|--------|------|------|
| `user_input` | 真实用户输入 | `user` |
| `user_correction` | 用户纠正（(e)dit 审批编辑产生，Stage 1.5） | `user` |
| `tool_result` | 工具执行结果回注 | `user` |
| `skill_context` | skill 上下文重附 | `user` |
| `reminder` | 动态 reminder 注入 | `user` 或 `assistant` |
| `role_agent` | role agent 反馈注入 | `user` 或 `assistant` |
| `goal_context` | [Goal 模式](goal-mode-guide.md) 目标+验收标准的"钉住"消息，每轮/每次 compact 后重新附加，防止被压缩策略稀释 | `user` |
| `session_resume` | 跨 session 恢复标记 | `user` |
| `hook_context` | hook 注入的额外上下文 | `user` |
| `file_change` | 文件变化感知通知 | `user` |
| `assistant_reply` | 正常 assistant 回复 | `assistant` |
| `compressed` | auto-compress 占位符 | `user` |
| `compact_summary` | /compact 摘要占位符 | `assistant` |
| `compact_event` | **Raw History 专用**：compact 操作记录 | `user` |

`compact_event` 只出现在 Raw History 中，`to_llm_messages()` 遇到它会直接跳过。

`user_correction` 是 `user_input` 的"子类"——`is_real_user_input()`/`is_turn_boundary()` 把两者**同等对待**（都算真实用户意图），区分出独立类型值只是为了审计时能追溯"这条用户消息是直接输入的，还是审批编辑产生的"。由 `PermissionGuard` 在 `(e)dit` 分支检测到编辑后，通过 `make_user_correction()` 构造并经 `Agent._on_edit_detected()` 写入 history；同时是 Stage 1.4"人类反馈纠正检测"的高质量信号来源之一（详见 [记忆管理指南](memory-management-guide.md#lesson-memory)）。

---

## 4. 时间戳设计

### 格式

`_ts` 采用 ISO 8601 UTC 格式，精确到毫秒：

```
"2026-06-17T08:51:13.775Z"
```

### 注入位置

**`make_*()` 构造函数不注入 `_ts`**，由 `RawHistory.append()` 统一注入：

```python
# make_* 只设 _type，不设 _ts
msg = make_user_input("帮我写个测试")
# msg = {"role": "user", "content": "...", "_type": "user_input"}
# 无 _ts

# 写入 raw 时自动注入 _ts
raw.append(msg)
# raw 条目 = {"role": "user", "content": "...", "_type": "user_input", "_ts": "2026-...Z"}
# 原始 msg 对象不被修改
```

### 时间戳语义

- `_ts` 记录的是**写入 raw history 的时刻**，而非用户发送消息的时刻
- 同一个 compact 操作产生的多条条目，`_ts` 相同或极接近
- `compact_event` 的 `_ts` 标记了压缩发生的时间点

### 字段位置

| | `_type` | `_ts` |
|--|:-------:|:-----:|
| Active History | ✅ | ❌ |
| Raw History | ✅ | ✅ |
| 发给 LLM | ❌ 剥离 | ❌ 剥离 |
| 存储（history.json） | ✅ | ❌ |
| 存储（raw_history.json） | ✅ | ✅ |

---

## 5. 文件结构

```
src/mini_agent/history/
├── __init__.py          # 统一导出（含 HType, make_*, to_llm_messages, RawHistory, replay）
├── entry.py             # HType 枚举、make_* 构造函数、to_llm_messages、判断辅助
├── raw_history.py       # RawHistory 类、replay() 函数
└── compression.py       # 压缩策略（已改用 _type 判断，消除字符串前缀依赖）
```

新增文件（本次改动）：
- `src/mini_agent/history/entry.py`
- `src/mini_agent/history/raw_history.py`

存储层新增文件（运行时生成）：
- `.agent/sessions/<id>/raw_history.json`

---

## 6. 关键 API

### 构造消息

```python
from mini_agent.history.entry import (
    make_user_input, make_user_correction, make_tool_result, make_assistant_reply,
    make_compressed, make_compact_summary, make_session_resume,
    make_skill_context, make_reminder,
)

msg = make_user_input("帮我写个测试")
# {"role": "user", "content": "帮我写个测试", "_type": "user_input"}

correction_msg = make_user_correction("[edited bash call] original: 'rm -rf /tmp/x' → edited: 'rm -rf /tmp/x --dry-run'")
# {"role": "user", "content": "...", "_type": "user_correction"}
```

### 判断消息类型

```python
from mini_agent.history.entry import (
    is_real_user_input,       # 真实用户输入（非工具结果/占位符）
    is_tool_result,           # 工具结果回注
    is_turn_boundary,         # turn 边界（可作为保留段起点）
    is_compressed_placeholder, # 压缩产生的占位符
)

# 向后兼容：无 _type 字段时自动降级到字符串前缀判断
if is_turn_boundary(msg):
    ...
```

### 发给 LLM（唯一出口）

```python
from mini_agent.history.entry import to_llm_messages

# 必须通过此函数，不能直接传含 _type/_ts 的列表
llm_messages = to_llm_messages(active_history)
response = llm_client.chat(messages=llm_messages, ...)
```

或通过 `HistoryManager.for_llm` 属性：

```python
response = llm_client.chat(messages=self._hist.for_llm, ...)
```

### Raw History 操作

```python
from mini_agent.history.raw_history import RawHistory, replay

raw = RawHistory()
raw.append(msg)                                    # 追加，自动注入 _ts
raw.append_compact_event(before=10, after=3, strategy="turn_aligned")
raw.save_to_file(path / "raw_history.json")        # 原子写入

# 从 raw 还原 active history
active = replay(raw.entries)                       # 精确还原当前状态
```

### HistoryManager

```python
hm = HistoryManager(cfg)

hm.append_user("用户消息")          # _type=user_input，同步追加到 raw（含 _ts）
hm.append_assistant(response)       # _type=assistant_reply
hm.append_tool_results(calls, strs) # _type=tool_result
hm.append_skill_context(block)      # _type=skill_context
hm.append_reminder(role, content)   # _type=reminder

hm.history       # active history 列表（含 _type，不含 _ts）
hm.for_llm       # 剥离 _type/_ts 后的 LLM 格式
hm.raw_history   # RawHistory 实例
```

---

## 7. replay() 工作原理

```python
def replay(raw_history: list[dict]) -> list[dict]:
    active = []
    for msg in raw_history:
        if msg["_type"] == "compact_event":
            active.clear()   # 压缩事件：清空 buffer（接下来是压缩后新内容）
            continue
        active.append(dict(msg))   # 其他条目保留（含 _ts）
    return active
```

**语义**：`compact_event` 是时间线上的"重置点"——它之前的所有内容在 Active History 中已被压缩替代，`clear()` 正确反映了这一点。`compact_event` 之后紧跟的条目（`compressed`、`compact_summary`、新的用户消息）则是压缩后的新起点。

---

## 8. 向后兼容

所有判断辅助函数（`is_real_user_input`、`is_tool_result`、`is_turn_boundary`）在 `_type` 字段缺失时，自动降级到旧的字符串前缀判断：

```python
def is_real_user_input(msg: dict) -> bool:
    t = msg.get("_type")
    if t is not None:
        return t in (HType.USER_INPUT, HType.USER_CORRECTION)   # 新格式：精确判断
    # 向后兼容：无 _type 时用字符串前缀
    content = msg.get("content", "")
    return (
        msg.get("role") == "user"
        and not content.startswith("<tool_result")
        and not content.startswith("[Previous")
        and not content.startswith("[Compressed")
    )
```

已有 session 的 `history.json` 在加载后不会因缺少 `_type` 而出错；新的追加操作会自动带上 `_type`，逐渐"染色"整个历史。

---

## 9. 压缩策略改造

`compression.py` 中所有字符串前缀判断已替换为类型判断：

```python
# 旧：字符串前缀猜测 turn 边界
user_indices = [
    i for i, m in enumerate(history)
    if m["role"] == "user"
    and not m["content"].startswith("<tool_result")
    and not m["content"].startswith("[Previous")
]

# 新：_type 精确判断（含向后兼容）
from mini_agent.history.entry import is_turn_boundary
user_indices = [
    i for i, m in enumerate(history)
    if is_turn_boundary(m)
]
```

三个内置策略（`TurnAlignedStrategy`、`SlidingWindowStrategy`、`LLMSummaryStrategy`）均已更新，压缩产生的占位符条目自动携带 `_type`：

```python
# 压缩结果条目使用 make_* 构造，自带 _type
return [
    make_compressed(),                               # _type=compressed
    make_compact_summary(f"[Compressed: {text}]"),   # _type=compact_summary
] + list(keep)
```

---

## 10. 存储文件

每个 session 目录下新增 `raw_history.json`：

```
.agent/sessions/<session_id>/
├── meta.json           # session 元数据
├── history.json        # active history（含 _type，不含 _ts）
└── raw_history.json    # raw history（含 _type 和 _ts，只增不减）
```

`raw_history.json` 结构示例：

```json
[
  {
    "role": "user",
    "content": "帮我写个 hello world",
    "_type": "user_input",
    "_ts": "2026-06-17T08:30:00.123Z"
  },
  {
    "role": "assistant",
    "content": [{"type": "text", "text": "好的，以下是..."}],
    "_type": "assistant_reply",
    "_ts": "2026-06-17T08:30:02.456Z"
  },
  {
    "role": "user",
    "content": "{\"event\": \"compact\", \"before_count\": 20, \"after_count\": 4, \"strategy\": \"turn_aligned\"}",
    "_type": "compact_event",
    "_ts": "2026-06-17T08:35:10.789Z"
  },
  {
    "role": "user",
    "content": "[Previous conversation compressed]",
    "_type": "compressed",
    "_ts": "2026-06-17T08:35:10.791Z"
  }
]
```

---

## 11. 对反思机制的意义

本次改造是**反思机制**的基础设施前提。有了类型化 history，反思 prompt 可以：

- 精确提取"用户真实意图"（`_type=user_input` 的条目）
- 排除工具噪音（`_type=tool_result`）
- 识别 turn 边界，统计每轮对话用了多少工具调用
- 利用 `_ts` 计算每轮响应时间、工具调用耗时
- 从 Raw History 获取完整上下文，不受压缩影响

这些能力是基于字符串前缀猜测无法稳定实现的。

> ✅ **已落地**（2026-06，Stage 1.3）：`agent.py` 的 `_reflect_and_save_lessons()`
> 正是用 `is_turn_boundary()` 精确截取最后若干轮用户意图轮次，喂给反思 LLM
> 调用生成结构化 lesson 候选。`user_correction`（本文档第 3 节新增类型）则是
> Stage 1.4/1.5 人类反馈通道的载体——`is_turn_boundary()` 把它和 `user_input`
> 同等对待，使纠正性编辑也能被反思机制正确识别为一轮用户意图。
> 详见 [记忆管理指南](memory-management-guide.md#lesson-memory)。

---

## 12. 相关文档

- [`storage-design.md`](storage-design.md) — session 存储结构总体设计
- [`agent-design.md`](agent-design.md) — agent 主循环与 HistoryManager 集成
- [`unit-testing-guide.md`](unit-testing-guide.md) — 测试 helper 中如何构建带 `_hist` 的 agent
- [`memory-management-guide.md`](memory-management-guide.md) — Lesson Memory：规则触发 / SessionEnd 反思 / 人类反馈纠正检测如何消费类型化 history
- [`permission-guide.md`](permission-guide.md) — `(e)dit` 审批编辑如何产生 `user_correction` 消息
- [`goal-mode-guide.md`](goal-mode-guide.md) — `goal_context` 类型的产生方与消费场景（GoalRunner 钉住目标上下文）

---

*最后更新：2026-06（新增 `user_correction` 类型，对应 self_evolution_implementation_plan.md Stage 1.5）*
