# 用户输入自动捕获至记事本（LLM 判断版）

> 聚焦范围：`config/models.py`（新增开关）、`history/user_requirement_capture.py`
> （新模块，判断 + 落盘逻辑）、`tools/notepad.py`（复用 `NotepadStore`，不改工具本身）、
> `cli/repl.py` / `api/server.py`（真人输入入口埋点）、`ui/renderer.py`（复用现有
> print_info/print_success，不新增）、`docs/notepad-guide.md`。
>
> 触发背景：`tools/notepad.py` 的记事本内容常驻 system prompt、不受 history
> compact 影响，但目前完全依赖 agent 在对话过程中**主动**调用 `notepad_add`。
> 一旦 agent 当轮没有意识到用户的某句话是关键要求/约束，这条信息就只留在
> 会话历史里；后续一旦触发 compact，历史被 LLM 摘要替换，可能丢失或走样，
> 导致 agent 后续实际工作方向偏离用户原始意图。需要一个**不依赖 agent 自觉性**
> 的兜底捕获机制。

---

## 0. 结论先行

| # | 问题 | 现状 | 本方案 |
|---|---|---|---|
| 1 | 记事本更新完全靠 agent 自己判断是否调用 `notepad_add` | 无兜底 | 每次真人输入后，用 LLM 单独判断"是否要更新记事本 / 更新什么"，与 agent 自身的判断并行、互不干扰 |
| 2 | 判断逻辑若挂在 `run_turn()` 内部，会被 goal_mode/workflow/judge/sub_agent/compaction 自身等大量内部程序化调用一起触发，噪音大且有递归风险 | — | 改为挂在**真人输入入口**（CLI REPL 主循环、HTTP API 且 `initiator=="user"`），不碰 `run_turn()` 本身 |
| 3 | 系统判断和 agent 手动记录的条目可能互相覆盖/冲突 | — | 自动捕获的条目统一打专属 tag `auto_requirement`，判断 LLM 只能看到、只能新增/更新这个 tag 下的条目，不触碰 agent 手动记的其它条目；且**不允许删除**，宁可冗余不丢信息 |
| 4 | 用户想要更新时能看到确认信息 | 无提示 | 同步调用，判断完成后若确实更新了记事本，用 `ui/renderer.py` 现成的 `print_info`/`print_success` 在终端打印一行提示；未更新则静默 |
| 5 | 新功能默认要不要开 | 用户已明确要求：**agent_config.json 新增开关，默认开启，可关闭** | `AppConfig.auto_capture_user_requirement_enabled: bool = True` |

已与用户确认的关键决策：
- **同步**调用（不做后台线程），保证判断完成之后（无论成功与否）才进入正式 `run_turn`；
- 开关放在 `agent_config.json`，字段挂在 `AppConfig` 上，**默认开启**；
- LLM 调用复用现有的 `LLMHelper`（`agent.llm_helper.ask(...)`），不裸调 client、不新增 LLM 调用路径；
- 专属 tag 的条目**计入**现有"记事本总字数超阈值 → 提示 agent 调用 `notepad_summarize`"机制——不用额外改动，因为 `compaction.py::_build_notepad_compact_hint()` 里的 `store.total_chars()` 本来就是对全部条目（不分 tag）求和，天然覆盖。

---

## 1. 挂载点

**不**在 `run_turn()` 内部埋点。理由：`run_turn()` 被 `compaction.py`（`self.run_turn(compact_prompt)`）、
`goal_mode/executor.py`、`workflow/executors.py`、`role_agents/judge_factory.py`、
`orchestrator/sub_agent.py`、`evolution/*_bridge.py` 等大量内部机制复用，这些都是
程序生成的 prompt，不是"用户的要求"，混进去会造成噪音，甚至在 compaction 自己调用
`run_turn()` 时出现潜在递归。

真正的"真人输入"入口只有两处：

1. `cli/repl.py` 主循环：`_term.prompt_user()` 拿到 `user_input` 之后、
   排除 `exit/quit` 和 `/slash` 命令之后、`agent.run_turn(user_input)` 之前。
   （注意：同文件里另外两处 `agent.run_turn(...)` —— TurnEnd hook 注入的续接输入、
   以及 `_compact_and_continue` 里自动发送的 `"继续"` —— 都不是真人输入，不埋点。）

2. `api/server.py` 的 `AgentRunner._main_loop`：`cmd.initiator == "user"` 分支下、
   `bridge.agent.run_turn(cmd.message)` 之前。`initiator` 为 `"autonomous"`/`"cron"`/
   `"external"` 等的调用一律跳过。

---

## 2. 判断逻辑

新模块 `src/mini_agent/history/user_requirement_capture.py`：

```python
def maybe_capture_user_requirement(agent, user_message: str) -> None:
    """真人输入后同步调用。内部做完整判断 + 落盘 + 终端提示，
    任何异常都静默吞掉，不能影响正常的 run_turn 流程。"""
```

流程：
1. 开关检查：`agent.cfg.auto_capture_user_requirement_enabled` 为 False，或全局
   `notepad_enabled` 为 False，或 `get_current_notepad()` 返回 None（未配置/关闭）→ 直接返回；
2. 输入长度过短（如去空白后 < 6 字符）→ 直接返回，不调 LLM，省成本；
3. 取出当前 notepad 里 `tag == "auto_requirement"` 的条目列表（复用
   `NotepadStore.to_list()` 后按 tag 过滤，不新增 NotepadStore 方法）；
4. 拼 prompt，通过 `agent.llm_helper.ask(prompt, system=SYSTEM_PROMPT)` 单轮请求
   （复用现有 `LLMHelper`，走 Agent 当前 provider/model + 现有重试/fallback 链，
   不做任何裸调）；
5. 解析 LLM 返回的 JSON（容错：允许 ```json 代码块包裹，解析失败 → 静默跳过，
   不落盘、不报错）；
6. 按判断结果调用 `NotepadStore.add(...)` / `NotepadStore.update(...)`（只允许
   `action: "add" | "update"`，不支持 `"remove"`）；
7. 落盘成功后，用 `ui.renderer.print_info` 打印一行确认信息（截断预览，避免刷屏）；
   `should_update=False` 或任何环节异常 → 完全静默（异常仍按仓库惯例
   `errors.log_exception` 记录，但不打印到终端打扰用户）。

### LLM 判断的 prompt 设计要点
- 明确说明：只看得到 `auto_requirement` tag 下的条目，只能新增或更新这些条目；
- 明确说明：这是一个"兜底捕获"机制，**宁可多记、不可漏记**——任何用户提出的
  目标/约束/偏好/明确指令都算数，闲聊、纯提问、简单确认（"继续""好的"）不算；
- 输出严格 JSON：
  ```json
  {"should_update": true, "action": "add", "entry_id": null, "content": "..."}
  ```
  `action="update"` 时 `entry_id` 必须是已给出的现有条目 id 之一；否则退化为 `add`。

---

## 3. 新增配置项

`config/models.py`，紧邻现有 `notepad_enabled` 字段：

```python
# [next_doc/user_requirement_notepad_capture_plan.md] 每次真人输入后，是否
# 用 LLM 同步判断"是否需要把这条输入的关键要求补记到记事本"。属于
# notepad_enabled 的兜底子功能，独立开关、默认开启；关闭后完全 no-op
# （不调用 LLM，不影响正常 run_turn 流程和已有的 agent 自主 notepad_add）。
auto_capture_user_requirement_enabled: bool = True
```

不新增 override 模型配置项——直接复用 agent 当前 model/provider（用户未要求单独配置判官模型，保持最简）。

---

## 4. 对现有机制的影响面

- `compaction.py::_build_notepad_compact_hint()`：无需改动，`store.total_chars()`
  本来就对全部条目求和，`auto_requirement` 条目自动计入；
- `tools/notepad.py`：不改动，只是被新模块以"内部直接调用 `NotepadStore` 方法"
  的方式复用（不经过 `notepad_add` 这个面向 agent 的 tool 包装，因为这次的调用方
  不是 agent 自己，是系统判断逻辑）；
- `notepad_list` 等工具返回的条目列表里会看到 `[auto_requirement] ...` 的条目，
  agent 自己也能读到、也可以在需要时用 `notepad_summarize` 把它们和别的条目一起
  瘦身（没有特殊豁免）；
- 完全不影响 `run_turn()` 内部任何现有逻辑（compact 触发、judge、goal_mode 等）。

---

## 5. 测试计划

`tests/test_user_requirement_capture.py`：
- 开关关闭 → 不调用 LLM（用 mock 断言未调用）；
- 输入过短 → 不调用 LLM；
- LLM 返回 `should_update=false` → 不落盘、不打印；
- LLM 返回 `should_update=true, action=add` → 新增条目，tag 正确，打印被调用；
- LLM 返回 `action=update` 且 `entry_id` 存在 → 更新对应条目；
- LLM 返回 `action=update` 但 `entry_id` 不存在 → 退化为 add；
- LLM 返回格式错误 JSON / 抛异常 → 静默吞掉，不影响调用方；
- 判断 LLM 看到的条目列表确实只含 `auto_requirement` tag（隔离性验证）。

---

## 6. 文档更新

- `docs/notepad-guide.md`：新增"自动捕获（LLM 判断）"一节，说明开关、tag、
  与已有阈值提示机制的关系、以及"不做删除"的边界。

---

## 实施状态

- [x] 配置项：`config/models.py::AppConfig.auto_capture_user_requirement_enabled`（默认 True）+ `config/loader.py` JSON 加载
- [x] 判断/落盘模块：`history/user_requirement_capture.py::maybe_capture_user_requirement()`
- [x] CLI 埋点：`cli/repl.py` 主循环真人输入处
- [x] API server 埋点：`api/server.py::AgentRunner._main_loop`，仅 `cmd.initiator == "user"`
- [x] 测试：`tests/test_user_requirement_capture.py`（11 用例全过，未见回归）
- [x] 文档：`docs/notepad-guide.md` 新增 §7.5 + 代码位置表 3 行

> **[已实施完成]** 与用户确认的关键决策均已落地：同步调用、开关默认开启且可在
> `agent_config.json` 关闭、LLM 调用复用现有 `LLMHelper`（`agent.llm_helper.ask()`）、
> `auto_requirement` tag 条目天然计入既有超阈值提示机制（未改动 `compaction.py`）。
