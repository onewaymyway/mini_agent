# TurnJudge 自我死循环修复方案

> 背景：真实会话中观察到进入 TurnJudge 核查后，终端持续打印
> `⚠  hit 2, policy=compact_continue, compacting then continuing.` 和一连串
> `🧭 TurnJudge ❯ {...}` 判定文本，长时间不把控制权交还主 Agent / 真人，
> 且第一条已经完全正确的 `AUTO_CONTINUE` 判定也没有被外层消费。本方案记录
> 根因分析与修复点，供实施前对齐、实施后核对。

---

## 1. 根因分析

### 1.1 `hit 2` 是 TurnJudge 自己的私有会话撞的，不是主 Agent

`turn_judge.py::run_turn_judge()` 调用
`judge_factory.py::spawn_judge_agent(..., max_turns=2, tools_enabled=False, ...)`
构造 TurnJudge 的内部 Agent 实例。`spawn_judge_agent()` 只显式设置了：

```python
judge_cfg.max_turns = max_turns   # 2
```

但 `max_turns_on_limit`（撞到 `max_turns` 后的策略：`stop` / `continue` /
`compact_continue`）和 `max_turns_hard_limit`（策略为 `continue`/
`compact_continue` 时的硬顶）都**没有被重置**，而是通过
`load_config(project_root=...)` 原样从项目全局配置继承——也就是主 Agent
配的那份 `compact_continue` + 很大的 hard limit。

`agent/turn_loop.py::_agentic_loop()` 对任何 Agent（含 TurnJudge 自己）
一视同仁：

```python
if loop_count >= _turns_budget:
    if _max_turns_policy in ("continue", "compact_continue") and loop_count < _max_turns_hard_limit:
        if _max_turns_policy == "compact_continue":
            R.print_warning(f"[max-turns] hit {_turns_budget}, policy=compact_continue, compacting then continuing.")
            ...compact 当前（判官自己的）session、注入"继续"...
```

结果：TurnJudge 只要在 2 轮内没拿到"干净的最终文本"，就会对**它自己**这个
几百 token 的迷你会话做 compact + 自我注入"继续"，再判一轮——这套"续命"
机制本来是为长任务的主 Agent 设计的，套在一次性判定的判官身上没有意义，
反而制造了一个新的、更隐蔽的死循环层。

### 1.2 为什么会连续拿不到"干净的最终文本"

真正触发"TurnJudge 拿不到最终文本"的是 `llm/system_tool_call.py::postprocess_response()`：

```python
tool_calls_from_text = parse_tool_calls(text)   # 正则 <tool_use>\s*(.*?)\s*</tool_use>
```

这一步对**所有** provider 响应无差别执行，不判断这个 Agent 是否
`tools_enabled=False`（TurnJudge 正是零工具、`registry.empty()`）。而
TurnJudge 的职责恰恰需要在 `feedback` 里**引用/复述**主 Agent 那段没闭合的
`<tool_use>` 内容，并按 system prompt 要求给出"可直接复制粘贴的修复模板"
——这些模板本身就是字面的 `<tool_use>{...}</tool_use>` 文本。

于是 TurnJudge 生成的、内容完全正确的判定 JSON，只因为 `feedback` 里带了
示例性质的 `<tool_use>` 标签，就被 `parse_tool_calls()` 误判成"TurnJudge
自己发起的一次工具调用"；因为不是合法的 `{"name":..., "input":...}`
JSON（或者压根没有对应工具），触发 `Invalid tool call JSON` /
`missing 'name' field` 警告，这一轮被当成"没有产出有效结果"，
`_agentic_loop` 不 `break`，继续下一轮——新一轮大概率又要引用同样的示例，
又被同一正则误判，如此循环，直到撞上 `_turns_budget=2`，再叠加 1.1 的
`compact_continue` 问题，死循环被进一步放大。

### 1.3 为什么第一条已经正确的判定没有返回给主 Agent

`role_judge.py::_maybe_run_turn_judge()` 里 `raw = run_turn_judge(...)` 是
一次**同步阻塞调用**。终端里看到的一连串 `🧭 TurnJudge ❯ ...` 输出，全部
是**同一次调用内部** `_agentic_loop()` 的中间轮次流式打印，只有真正
`break` 出循环的那一次输出才会作为 `raw` 返回给调用方。第一条判定内容
虽然完全正确，但因为 `feedback` 文本里带了示例 `<tool_use>` 标签而被
1.2 描述的机制判定为"非最终态"，根本没有机会被 `return`，只能停留在
TurnJudge 内部继续转下去。

---

## 2. 修复点

### 修复 1：判官类内部 Agent 不应继承主 Agent 的"续命"策略

`judge_factory.py::spawn_judge_agent()` 显式覆盖：

```python
judge_cfg.max_turns = max_turns
judge_cfg.max_turns_on_limit = "stop"
judge_cfg.max_turns_hard_limit = max_turns
```

判官（TurnJudge / GoalJudge / EvaluatorAgent / CoachAgent / 自定义角色）
统一变成"预算内拿不到干净结果就直接停，交回调用方按既有的保守兜底处理"，
不会再对自己的私有会话做 compact + 自我续跑。

### 修复 2：`tools_enabled=False` 的 Agent 不应被 `<tool_use>` 正则误伤

`postprocess_response()` 新增 `parse_tool_use: bool = True` 参数，为 `False`
时跳过 `parse_tool_calls()` 提取步骤，只做 `<think>` 等标签清理。调用方
`_base_mixin.py::_postprocess()` 已经拿到了本次请求实际的 `tools` 列表
（`tools_enabled=False` 时这里必然是空列表，因为 `_prepare_tools()` 对空
`tools` 直接跳过协议注入），据此传参：

```python
def _postprocess(self, response, original_tools):
    return postprocess_response(response, parse_tool_use=bool(original_tools))
```

零工具的判官类 Agent 从此不会再因为"引用/复述问题文本里的 `<tool_use>`
标签"而被误判成自己发起了工具调用，从根子上解决 1.2、1.3 的问题。

### 修复 3（对应用户「4」）：进入/退出 TurnJudge 增加更明显的日志

- `turn_judge.py::run_turn_judge()` 开头/结尾打印带层级标记的日志：
  `┌─ [TurnJudge] 进入判官子会话（第 N 次核查）` /
  `└─ [TurnJudge] 退出判官子会话，status=...`。
- `turn_loop.py::_agentic_loop()` 的 `[max-turns] hit N, policy=...` 日志加上
  `self.cfg.agent_name` 前缀，例如 `[max-turns][🧭 TurnJudge] hit 2, ...`，
  从而一眼区分这是主会话还是某个判官/子 Agent 自己的私有会话撞的预算，
  不再需要靠"猜"。

### 修复 4（对应用户「turn_judge 的 max_turns 是不是可以改大一点」）

新增 `TurnJudgeConfig.judge_max_turns: int = 6`（与 `GoalModeConfig.judge_max_turns`
的先例一致——GoalJudge 之前也是硬编码 2/6，文档记录过"6 轮往往不够收敛
到最终 JSON 判定，会撞顶导致空输出、被迫保守判定"的坑），`turn_judge.py`
里 `spawn_judge_agent(..., max_turns=tj_cfg.judge_max_turns, ...)` 改为读取
这个配置项而不是硬编码 `2`。有了修复 1、2 之后，TurnJudge 本就不太可能
再需要多轮重试，这里只是把"1~2 轮太紧张，一次网络抖动/一次格式纠错就
可能被迫提前 NEED_USER"的余量留出来，属于防御性调整，不改变默认行为
（`judge_show_prompt`/`max_auto_rounds` 等其它字段不变）。

---

## 3. 影响范围与兼容性

- 修复 1、4 只影响判官类内部 Agent（`spawn_judge_agent` 的调用方：
  turn_judge / goal_judge / evaluator / coach / 自定义角色），不影响主
  Agent 的 `max_turns_on_limit` 行为。
- 修复 2 只在 `original_tools` 为空列表时改变行为（跳过 `<tool_use>` 正则
  提取）；只要 Agent 挂载了至少一个工具，行为与改动前完全一致。
- 修复 3 只增加打印，不改变任何判定逻辑或控制流。
- 均为默认开启、无新增配置开关的行为修正（除 4 新增的
  `turn_judge.judge_max_turns` 配置项本身，默认值 6，向后兼容旧配置
  文件——缺省时自动取新默认值，不需要用户改配置）。

## 4. 需要同步更新的文档

- `docs/turn-judge-guide.md`：
  - 配置字段表补充 `judge_max_turns`
  - "实现细节 / 已修复的坑" 一节补充本次修复（判官不再继承
    `max_turns_on_limit`/`max_turns_hard_limit`；零工具判官不再被
    `<tool_use>` 正则误伤）
- `docs/role-agents-guide.md`：`judge_factory.py` 小节同步补充
  `max_turns_on_limit`/`max_turns_hard_limit` 的显式重置说明
- 本文档（`next_doc/turn_judge_self_loop_fix_plan.md`）落盘作为实施前的
  方案记录；实施完成后不改名，保留作为该问题的设计追溯依据（与仓库里其它
  `*_plan.md` / `*_implementation_record.md` 的惯例一致，如需要可以后续
  另建一份 `*_implementation_record.md` 记录落地细节）。

## 5. 验证方式

- 复现场景：让主 Agent 输出一次未闭合 `<tool_use>` 的半成品，开启
  `turn_judge.enabled=true`，观察：
  1. 日志能看到 `┌─ [TurnJudge] 进入判官子会话` / `└─ ... 退出判官子会话`
     成对出现，且中间不再出现 `[max-turns][🧭 TurnJudge] hit N,
     policy=compact_continue` 的自我续命日志。
  2. 第一次 TurnJudge 判定（哪怕 feedback 里引用了 `<tool_use>` 示例）
     能够正常作为 `raw` 返回给 `_maybe_run_turn_judge()`，并按其 `status`
     正确进入 `AUTO_CONTINUE`/`NEED_COMPACT`/`NEED_USER` 分支。
- 回归验证：正常挂了工具的主 Agent 会话，确认 `<tool_use>` 提取行为无
  变化（修复 2 的判断条件是"本次请求 tools 是否为空"，主 Agent 场景下
  `tools` 非空，行为不变）。

---

## 6. 补充修复：`tools` 非空场景下的引用/示例误判（2024 后续）

### 6.1 背景

用户提出两个问题：

1. 修复 2 是否对"会用工具的判官"（如 `judge_tools_enabled=True` 时的
   `GoalJudge`）还起作用，需不需要额外加一个"是否是判官类"的角色参数来
   区分是否要做 `<tool_use>` 格式检测？
2. `<tool_use>` 格式检测本身能不能优化，从根子上减少这类误识别？

### 6.2 问题 1 的结论：不需要加角色参数，现状已经是正确的

修复 2 的判据是 `_base_mixin.py::_postprocess()` 里"本次请求实际传给 LLM
的 `tools` 列表是否为空"（`parse_tool_use=bool(original_tools)`），而不是
按 Agent/角色类型打静态标签。这个粒度天然覆盖了"判官也可能挂工具"的情况：

- `TurnJudge` / `EvaluatorAgent` / `CoachAgent`：固定 `tools_enabled=False`，
  `tools` 恒为空 → 跳过检测，解决的正是本文档第 1～3 节的问题。
- `GoalJudge`：`tools_enabled = bool(cfg.goal_mode.judge_tools_enabled)`，
  一旦用户把这个开关打开，`judge_factory.py` 会给它挂真实只读工具白名单，
  此时它发起请求 `tools` 非空 → 检测**照常生效**，该判官自己发起的真实
  工具调用、以及它自己产生的格式错误依旧会被正常捕获。

如果改成按"角色类型"关闭检测（比如加一个 `is_judge` 参数），反而会有退化
风险：`GoalJudge` 一旦打开 `judge_tools_enabled`，如果检测是按角色类型
关掉的，它自己发起的格式错误工具调用也会检测不出来——这是新的隐患。现状
"看本次请求 tools 是否为空"的实现是跟随实际配置自动走的，不需要为每个新
增判官角色单独维护一份"要不要检测"的名单，更鲁棒，**这一部分不用改**。

### 6.3 问题 2：残留的误判场景与修复方案

修复 2 管不到的场景：只要 `tools` 非空（`judge_tools_enabled=True` 的
`GoalJudge`、或任何正常挂了工具的主 Agent），它在输出里"引用/复述"一段
示例性质或问题片段的 `<tool_use>...</tool_use>` 文本时，仍会被
`parse_tool_calls()` 当成一次真实调用去解析。

评估过的方案：

- **按工具名白名单过滤（已否决）**：思路是"解析出的 `name` 不在已知工具
  列表里就丢弃"。但判官引用的示例经常就是复述一段引用了**真实、已注册**
  工具名的问题文本（比如原样复述主 Agent 那段没闭合的、引用了
  `bash`/`write_file` 的半成品），白名单挡不住这种情况，故放弃。
- **用 ``` 代码块围栏包裹示例（已否决）**：`prompts/reminders/
  format_issue_*.md` 里的示例本来就已经用 ``` 包裹了，但 `_TOOL_USE_RE`
  从不区分是否在代码块内，围栏挡不住已经观察到的误判；而且围栏是模型日常
  组织回答就会用的通用格式，容易被模型无意间也套在真实调用外面，导致真实
  调用被连带跳过，故放弃。
- **固定中文标记句 + 就近窗口检测（采用）**：见下。

#### 落地方案

1. `llm/system_tool_call.py` 新增常量：

   ```python
   TOOL_USE_EXAMPLE_MARKER = "【以下为工具格式示例并非实际工具调用】"
   _EXAMPLE_MARKER_WINDOW = 300
   ```

   `parse_tool_calls()` 对 `_TOOL_USE_RE` / `_TOOL_CALL_LEGACY_RE` 的每个
   匹配，往前回溯一个窗口（匹配起点前最多 300 字符，且不跨越"上一个
   `<tool_use>`/```` ```tool_call ```` 匹配的结束位置"，避免窗口穿透到更早、
   不相关的一次真实调用之后）。窗口内出现 `TOOL_USE_EXAMPLE_MARKER` 即整体
   跳过这次匹配——不解析、不计入 `tool_calls`、不产生任何警告。没有标记的
   匹配，解析逻辑完全不变。

2. 在所有会生成"举例/引用"类 `<tool_use>` 文本的地方，前面加上这行标记：

   - `prompts/reminders/format_issue_*.md` 中带示例块的 8 个文件（
     `tag_role_confusion` / `tool_call_alias_tag` / `orphan_close_tag` /
     `bare_name_after_tag` / `invalid_json_in_tool_use` /
     `tool_result_used_as_request` / `legacy_fence_unclosed` /
     `unclosed_tool_use`；`write_file_truncated` 本身没有 `<tool_use>`
     示例块，不需要改）。
   - `prompts/system/turn_judge.md`、`prompts/system/goal_judge.md`：
     在核查原则里加一条，要求判官引用/复述问题片段或自己现写修复模板时，
     必须先原样输出这一行标记再给出示例内容。
   - `perception/format_correction_detector.py::_PROMPT_FOOTER`
     **故意不加**标记：这段文字是以 `user` 身份注入回主 Agent 上下文、
     要求它下一轮真的发起一次这样的调用的格式纠错提示，本身不会被
     `parse_tool_calls()` 处理（该函数只解析模型自己的响应文本，不解析
     注入的 user 消息）；只有当某个 Agent 在自己的输出里引用/复述这段
     模板时才需要带标记，那是引用方自己的责任，已经在上面 turn_judge /
     goal_judge 的提示词里覆盖。

3. 标记文案是唯一权威定义在 `TOOL_USE_EXAMPLE_MARKER`；reminder 加载器
   （`reminders/loader.py`）不走 `{{var}}` 模板渲染，所以 prompts 里是
   分别硬编码同一句中文字符串，不是共享变量。为防止后续改动漂移，新增
   了一条测试（`tests/test_system_tool_call_and_debug.py::
   TestToolUseExampleMarkerSyncedWithPrompts`）逐个校验这些提示词文件是否
   包含与常量逐字一致的文案。

4. `tool_call_protocol.md`（教模型"你应该怎么发起真实调用"的系统提示级
   示例）**不用改**：它活在 system prompt 里，从来不会被
   `parse_tool_calls()` 处理，不存在被误伤的问题；也不需要给真实调用加
   标记——协议文档里已通过判官提示词的措辞明确"这个标记只用于引用/示例
   场景，发起真实调用时不能带这一行"。

### 6.4 影响范围与兼容性

- 只在匹配前的窗口内出现标记时才改变行为（跳过该次匹配）；没有标记的
  `<tool_use>` 块解析逻辑与改动前完全一致，不影响任何正常工具调用。
- 不依赖判官角色类型，`tools` 是否为空的判据（修复 2）与标记机制（本节）
  是两层独立、互补的防护：前者管"零工具判官的整轮输出"，后者管"tools 非空
  时引用/复述示例文本的具体某一段"。
- 新增测试：`TestToolUseExampleMarker`（标记跳过、窗口边界、标记不影响
  后续真实调用）、`TestToolUseExampleMarkerSyncedWithPrompts`（提示词文案
  与常量一致性）。

### 6.5 验证方式

- 单测：`pytest tests/test_system_tool_call_and_debug.py -k
  ToolUseExampleMarker`。
- 人工复现：让 `GoalJudge` 在 `judge_tools_enabled=True` 下核查一次带有
  未闭合 `<tool_use>` 的主 Agent 输出，观察它在 `feedback` 里按提示词要求
  带上标记后引用示例，确认该轮不会被错误地当成 `GoalJudge` 自己发起的
  工具调用、不再触发"没有产出有效结果"式的重试。
