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
