# Goal 模式改进计划：Stuck-Compact 联动、结构化验收、进展度量、探索/收敛节奏、Goal 重规划

> 基于 `mini_agent-master` 实际代码（`goal_mode/spec.py`、`goal_mode/runner.py`、
> `role_agents/stuck_detector.py`、`role_agents/verdict.py`、`role_agents/goal_judge.py`、
> `config/models.py`）梳理。每一节先说明现状（已经做到什么程度），再给出具体缺口和改进方案。

> **实现状态（本次改造，累计三轮）**：
> - §1.2 Dead-end 从滚动窗口升级为持久清单 —— **已实现**
> - §2.2 自验证优先（GoalRunner 强制执行 verification_command 拿客观证据）—— **已实现**
> - §1.3 NEED_COMPACT 路径共享 dead-end 注入 —— **已实现**
> - §3.1 进展分数纳入 checklist 通过数变化 —— **已实现**
> - §1.1 分级 compact（轻量/深度两级，与恢复次数挂钩）—— **已实现**
> - §2.1 过程判断 / 结果判断分离（process_flags）—— **已实现**（本轮新增）
>
> 尚未实现：§3.2（伪进展趋势识别）、§4（探索/收敛双模式）、§5（Goal 重规划提议）。
> 保留原方案文本，可作为后续迭代依据。

---

## 1. Stuck 检测 → 主动 compact 的联动

### 现状：这一环已经打通，且比"简单清空"更细致

`runner.py::_try_stuck_recovery` 已经实现了"卡住 → 定向 compact → 注入已排除路径"的完整链路：

```python
# runner.py 大致逻辑
self._do_compact()   # 压缩历史（复用 agent.compact_with_skills()）
hint = "...请不要重复上一轮的做法..."
no_gain_reasons = [
    r for r in self._recent_progress_reasons
    if r.get("progress") in ("SAME_APPROACH_NO_GAIN", "REGRESSED") and r.get("reason")
]
if attempted_paths_enabled and no_gain_reasons:
    hint += "\n\n" + pm.fragment("goal_mode", "STUCK_RECOVERY_ATTEMPTED_PATHS_BLOCK",
                                  attempted_paths_lines=...)
self._agent._hist.append_raw_dict(make_goal_context(hint))
```

`_recent_progress_reasons` 是 GoalJudge 每轮结构化输出的 `progress_reason`（见 `verdict.extra`），
按 `consecutive_same_feedback_limit` 的窗口滚动保留，compact 时会被拼成"已验证无效的方向"注入下一轮
prompt——这正是"已排除路径清单，compact 不清除"的雏形，只是**没有落在 notepad 里**，而是每次
临时从 `_recent_progress_reasons`（内存态 + `GoalState.recent_progress_reasons` 落盘）现场拼装。

`max_stuck_recoveries`（默认 3）提供了"给几次机会"的额度，用尽后才真正终止（`status=stuck`），
终止时还会把这些 `no_gain_reasons` 写入 memory（`_write_failure_lesson`）。

### 缺口 1.1：没有"轻度 vs 深度"两级 compact，只有单一强度

`_try_stuck_recovery` 每次触发都是同一个动作：`_do_compact()`（等价于 `agent.compact_with_skills()`，
一次性的完整压缩），配合同一段通用提示 + dead-end 列表。判官原本还有一个独立的 `NEED_COMPACT` 状态
（`_run_judge` 返回 `judge_status == "NEED_COMPACT"` 时也走 `_do_compact()`），但这条路径和"stuck 触发的
compact"用的是完全同一个压缩函数，没有"轻量只压缩最近一段"和"深度重新审视整个 goal spec"的区分。

**改进方案**：把 compact 强度和"已经恢复过几次"挂钩，而不是每次都用同一强度：

```python
def _try_stuck_recovery(self) -> bool:
    ...
    recovery_index = self._stuck_detector.recoveries_used  # 已发生的恢复次数（含本次）
    if recovery_index <= self._gm_cfg.light_compact_max_recoveries:  # 例如前 N 次
        self._do_compact(mode="light")   # 只压缩"最近一段探索"，保留更多细节
    else:
        self._do_compact(mode="deep")    # 更激进压缩 + 触发对 goal spec 本身的重新审视提示
```

`agent.compact_with_skills()` 目前是否支持"只压缩最近一段"取决于底层 compact 策略实现（未在
`goal_mode` 范围内确认），如果暂不支持分段压缩，退化方案是：轻量恢复只做"注入提示，不做 compact"
（第一次卡住可能只是暂时性的，不必立刻压历史）；深度恢复才真正触发 `_do_compact()`。这样至少能
把"第一次卡住"和"反复卡住"区分开，避免过早、过频地压缩掉还有用的上下文。

#### 实现记录（本次改造）

采用了方案里提到的 fallback 版本（`compact_with_skills()` 是否支持分段压缩未在 goal_mode
范围内确认，直接用"轻量不 compact / 深度才 compact"这一版）：

- 新增配置项 `cfg.goal_mode.light_compact_max_recoveries`（默认 `1`）：
  `self._stuck_detector.recoveries_used <= light_compact_max_recoveries` 时判定为"轻量恢复"，
  跳过 `_do_compact()`，只调用 `_pin_goal_context()` + 注入提示；超过之后判定为"深度恢复"，
  调用原有的 `_do_compact()`。
- 提示文本按轻量/深度分别措辞：轻量恢复不再说"历史已经压缩过"（因为确实没压缩）。
- `light_compact_max_recoveries=0` 时完全退化为升级前"每次恢复都 compact"的行为，一键回退。
- 新增测试：`test_stuck_recovery_light_then_deep_compact`、
  `test_stuck_recovery_light_max_zero_falls_back_to_always_compact`。

### 缺口 1.2：Dead-end 清单不是持久化的独立结构，是每次现场从滚动窗口拼装

`_recent_progress_reasons` 的容量固定为 `max(3, consecutive_same_feedback_limit)`（见 `runner.py`
`self._progress_reasons_cap`），**滚动窗口会覆盖旧记录**。也就是说，如果第一次 compact 恢复时排除了
路径 A，几轮之后窗口滚动，路径 A 的记录可能被新的 `progress_reason` 挤出窗口——如果 agent 后来又
绕回路径 A，可能不会再被提示"这条已经试过"，因为记录已经被冲掉了。

这本质上是"临时滚动记忆"而不是真正的"dead-end 持久化清单"。

**改进方案**：引入一个不随窗口滚动、只增不删（或按"是否已被复述过"去重）的 dead-end 记录，
建议直接落在 `GoalState` 里新增一个独立字段（而不是复用 `recent_progress_reasons`）：

```python
@dataclass
class GoalState:
    ...
    dead_ends: list[dict] = field(default_factory=list)
    # dead_ends 条目: {"round": int, "approach_summary": str, "failure_reason": str}
```

- 每次判官给出 `progress in (SAME_APPROACH_NO_GAIN, REGRESSED)` 且带 `progress_reason` 时，追加进
  `dead_ends`（去重逻辑：和已有条目做粗粒度相似度比较，`spec.py` 里已经有现成的
  `_is_near_duplicate` 工具函数可以直接复用，避免同一条路径被反复记录）。
- Compact 恢复时注入的"已排除路径"改为从 `dead_ends` 全量读取（而不是窗口内的 `_recent_progress_reasons`），
  这样无论卡住恢复发生在第 2 轮还是第 15 轮，都不会丢失更早排除的路径。
- `dead_ends` 明确标记为"不参与常规 compact 清除"——它本来就不在主 Agent 的对话历史里，而是
  `GoalRunner` 自己维护的旁路状态，天然满足这个要求，只是需要把它从"临时窗口"升级为"持久清单"。

#### 实现记录（本次改造）

已按上述方案落地：

- `GoalState` 新增 `dead_ends: list[dict]` 字段（`goal_mode/state.py`），随 `criteria_status` /
  `recent_progress_reasons` 一起落盘、恢复。
- `GoalRunner.__init__` 新增 `self._dead_ends: list[dict]`，`resume_state.dead_ends` 非空时恢复。
- 新增 `GoalRunner._record_dead_end()`：判官给出 `progress in (SAME_APPROACH_NO_GAIN, REGRESSED)`
  且带 `progress_reason` 时调用，内部复用 `spec.py::_is_near_duplicate` 去重后追加进 `self._dead_ends`
  （只增不删，不随 `_recent_progress_reasons` 的滚动窗口被冲掉）。
- 新增 `GoalRunner._render_dead_ends_block()`：把 `self._dead_ends` 渲染成"已验证无效路径"提示文本
  （复用既有的 `STUCK_RECOVERY_ATTEMPTED_PATHS_BLOCK` fragment）。
- `_try_stuck_recovery()` 改为优先调用 `_render_dead_ends_block()`；新增配置项
  `cfg.goal_mode.dead_ends_persist_enabled`（默认 `True`），关闭时退化为升级前的窗口内
  `_recent_progress_reasons` 行为，保持向后兼容、可一键回退。
- `_write_failure_lesson()` 写入失败经验时也优先使用 `self._dead_ends`（覆盖更完整）。
- 新增测试：`test_goal_state_dead_ends_roundtrip`、
  `test_goal_runner_dead_ends_survive_progress_reasons_window_eviction`（验证第一轮记录的
  dead-end 在窗口冲刷之后依然会被注入提示）、`test_goal_runner_dead_ends_dedup_near_duplicate`。

### 缺口 1.3：`NEED_COMPACT`（判官触发）和"stuck 检测触发"是两条独立路径，没有共享 dead-end 注入

`_run_judge` 里判官返回 `NEED_COMPACT` 时，`run()` 直接调用 `self._do_compact()`，**不会**走
`_try_stuck_recovery` 里那段"注入已排除路径 + 换角度提示"的逻辑。这意味着如果判官主观判断"历史
太乱建议压缩"，压缩后 agent 拿到的只是干净的历史，没有"提醒你别再走某条老路"的信息——只有真正被
`StuckDetector` 判定卡住时才有这层提示。

**改进方案**：把"compact 后注入 dead-end 提示"抽成一个独立方法（而不是内嵌在 `_try_stuck_recovery`
里），`NEED_COMPACT` 分支和 `_try_stuck_recovery` 都调用它：

```python
def _do_compact_with_context(self, hint: str = "") -> None:
    self._do_compact()
    if self._dead_ends:  # 只要有历史 dead-end 记录，任何 compact 场景都应该注入
        hint = (hint + "\n\n" if hint else "") + self._render_dead_ends_block()
    if hint:
        self._agent._hist.append_raw_dict(make_goal_context(hint))
```

这样"判官主动建议 compact"和"检测器判定卡住"两条路径都能受益于已经积累的 dead-end 信息，
不需要非得先经历一次 `StuckDetector` 判定才能用上。

#### 实现记录（本次改造）

`run()` 主循环中 `NEED_COMPACT` 分支在 `_do_compact()` 之后，新增调用
`self._render_dead_ends_block()`，非空时通过 `make_goal_context()` 注入一条历史消息
（复用 `_try_stuck_recovery()` 里同一套渲染逻辑，未额外抽出独立的 `_do_compact_with_context()`
方法，而是直接在两个调用点各自调用 `_render_dead_ends_block()`，效果等价，改动面更小）。

---

## 2. 验收方式的合理化

### 现状：结构化验收标准和结构化协议都已经有，但"过程判断 vs 结果判断"没有分离，"自验证优先"没有强制

`GoalSpec`（`spec.py`）已经是结构化的：`goal_text + acceptance_criteria(list[str]) + verification_method
+ verification_command`，且 `GoalSpecBuilder` 在生成阶段就会产出这些字段（不是等 judge 事后猜），
还有"防照抄"质量兜底（`_looks_like_verbatim_echo` 触发时会带纠正提示重试）。

`role_agents/verdict.py::parse_judge_verdict` 已经是结构化 JSON 协议（`status/feedback/checklist`），
不再是靠正则从自由文本里"抠"状态关键字。

`goal_judge.py` 的 system prompt 里也确实写了"优先通过实际运行命令验证，而不是单纯相信 AI 助手的自述"，
且支持挂载只读工具（`judge_tools_enabled`）、甚至真实执行验证命令（`judge_yes_mode`）。

**但这几件事目前都停留在"判官单独决定"的层面**，没有真正做到你提出的两点：

### 缺口 2.1：没有"过程判断"和"结果判断"的分离

`verdict.py` 的 `checklist` 目前只有 `passed / evidence` 两个字段（见 `runner.py::_run_judge` 里
`raw_checklist` 解析逻辑），是纯粹的"结果是否满足"。判官 system prompt 里也完全没有要求判断
"过程是否合理"（有没有绕过测试、伪造通过、投机取巧）。

一个只关注结果的判官，容易被"表面满足但过程有问题"的产出蒙混过关——比如 agent 为了让某条标准
"通过"，直接把测试断言改成恒真、或者删掉失败用例，如果判官只看"测试现在跑通了"这个结果，
不会发现过程有问题。

**改进方案**：在判官的结构化输出里增加一个独立维度，而不是把"过程"和"结果"混在同一个
`passed` 判断里：

```json
{
  "status": "CONTINUE",
  "feedback": "...",
  "checklist": [{"index": 1, "passed": true, "evidence": "..."}],
  "process_flags": [
    {"concern": "test_weakened", "detail": "标准1对应的测试用例被改成了恒真断言，怀疑是为了让检查通过而弱化了验证力度"}
  ]
}
```

`process_flags` 为空表示过程判断无异议；一旦非空，即使 `checklist` 全部 `passed=true`，
`GoalRunner` 也不应该直接放行 `DONE`——处理策略上：

- **仅结果未满足**（`process_flags` 为空但 checklist 有未过项）→ 正常 `CONTINUE`，走现有流程
- **过程有问题**（`process_flags` 非空，无论 checklist 是否全过）→ 应该判定为一种更严格的
  `CONTINUE`（甚至可以引入新状态 `REJECTED_PROCESS`），反馈里明确指出"结果表面达标但存在投机行为，
  需要恢复真实的验证方式后重做"，而不是允许它蒙混成 `DONE`。这是防止"验收标准被自己优化掉"的
  关键一环，目前完全没有覆盖。

对应地，`goal_judge.md` 的核查原则需要新增一条："除了核对每条标准是否通过，还要核查达成方式是否
正当——如果发现测试被弱化、检查被绕过、结果被人为伪造等投机行为，即使表面标准满足，也不能判定为
真正达成，应在 process_flags 中明确指出"。

#### 实现记录（本次改造）

按方案落地，未额外引入 `REJECTED_PROCESS` 新状态（沿用现有 `DONE/CONTINUE/NEED_COMPACT`
三态协议，改动面更小），而是采用"process_flags 非空时强制把 DONE 降级为 CONTINUE"的处理策略：

- `prompts/fragments/goal_mode.md` 新增 `PROCESS_INTEGRITY_INSTRUCTIONS` 片段：独立于
  `GOAL_JUDGE_EXTENDED_OUTPUT_INSTRUCTIONS`（progress/checklist），要求判官在同一个 JSON
  对象里额外输出 `"process_flags"`（默认空数组，仅在发现测试被弱化/检查被绕过/结果被伪造/
  验收范围被悄悄缩小等**具体证据**时才添加条目，格式 `{"concern": "...", "detail": "..."}`），
  并明确要求"process_flags 非空时即使 checklist 全部 passed 也不能判 DONE"。
- `prompts/system/goal_judge.md` 核查原则新增第 7 条，呼应上述要求。
- `role_agents/goal_judge.py::run_goal_judge` 新增 `process_integrity_enabled` 参数，
  控制是否把 `PROCESS_INTEGRITY_INSTRUCTIONS` 拼进 system prompt；与
  `extended_output_enabled`（progress/checklist）是两个完全独立的开关，可任意组合。
- `runner.py::_run_judge`：
  - 新增配置项 `cfg.goal_mode.process_integrity_check_enabled`（默认 `True`），控制是否
    请求/解析 `process_flags`。
  - 解析出的 `process_flags` 写入 `progress_info["process_flags"]`（默认 `[]`），供调用方
    与展示层使用。
  - **核心行为**：`process_integrity_check_enabled=True` 且判官给出 `status=="DONE"`、且
    `process_flags` 非空时，强制把 `status` 降级为 `"CONTINUE"`，并在展示给主 Agent 的
    `feedback` 末尾追加一段"【系统提示】...结果不能视为真正达成..."的说明（含每条
    process_flag 的 concern/detail），要求恢复真实的验证方式后重做。`status` 本来就是
    `CONTINUE` 或 `NEED_COMPACT` 时，`process_flags` 只是透传出来，不做额外处理（不影响
    现有状态机分支）。
  - `process_integrity_check_enabled=False` 时完全不请求也不解析 `process_flags`，DONE
    判定只取决于 checklist（行为与升级前完全一致，一键回退）。
- **关联修复**：排查过程中发现 `prompts/manager.py::_BLOCK_FRAGMENT_PATTERN` 存在一个
  预先存在的 bug——多段落 `KEY: |` 片段（段落间用空行分隔）在遇到第一个空行时就会被正则
  提前截断，导致 `GOAL_JUDGE_EXTENDED_OUTPUT_INSTRUCTIONS` 这类多段说明此前只有开头一段
  被真正拼进 system prompt、后面的字段说明和示例全部被静默丢弃。这个 bug 直接影响本节新增
  的 `PROCESS_INTEGRITY_INSTRUCTIONS`（同样是多段落），因此一并修复：正则从
  `(?:[ \t]+.*\n?)*`（只接受"以空白开头的行"）改为 `(?:[ \t]+.*\n|\n)*`（额外接受纯空行），
  使多段落 fragment 能被完整渲染。修复后重新跑了完整 `tests/test_goal_mode.py` /
  `test_judge_verdict.py` / `test_judge_dispatcher_unification.py` 套件确认无回归。
- 新增测试：`test_goal_runner_process_flags_downgrade_done_to_continue`、
  `test_goal_runner_process_flags_empty_allows_done`、
  `test_goal_runner_process_integrity_disabled_allows_done_despite_flags`、
  `test_goal_runner_process_flags_continue_status_unaffected`、
  `test_run_goal_judge_includes_process_integrity_instructions_in_system_prompt`。

### 缺口 2.2："自验证优先"没有强制，验证命令的执行权完全在判官手里、且默认关闭

`GoalSpec.verification_command` 目前**只是渲染成文本**给主 Agent 看（`render_context_block` /
`render_summary_for_user`）和给判官看（拼进 `goal_judge_request` 的 prompt），**没有任何代码路径
会程序化地执行它**——全局搜索 `verification_command` 的使用只有赋值、传递、渲染成字符串，没有
`subprocess` / 执行器调用。

也就是说，"运行测试通过"这类验证命令目前完全靠：
1. 主 Agent 自己在执行过程中可能顺手跑了（不是强制的，agent 可能压根没跑）
2. 判官如果 `judge_tools_enabled=True` 才可能自己决定要不要跑（默认关闭，且即使开启也是
   判官 LLM 自主决定，不是强制按 `verification_command` 执行）

你提出的"自验证优先"——即**主 Agent 在提交结果前，先被要求执行一遍可执行验证，把结果作为判官的
输入**——目前完全没有实现。

**改进方案**：
1. 在 `_build_prompt()` 组装的 prompt 里，如果 `GoalSpec.verification_command` 非空，
   显式要求主 Agent 在本轮结束前主动执行一次该命令，并把输出粘贴/总结进它的最终回复里
   （这一步不需要新代码，只是 prompt 层面的强约束，成本很低，是最快能上线的一版）。
2. 更进一步，在 `GoalStepExecutor.execute()`（`goal_mode/executor.py`）拿到主 Agent 本轮输出之后、
   送进判官之前，`GoalRunner` 自己（不经过任何 LLM）程序化地执行一次 `verification_command`
   （复用主 Agent 已有的受限命令执行能力，比如现有的 bash 工具执行链路，但由 `GoalRunner` 直接调用，
   不依赖判官 LLM 主动决定），把 `{returncode, stdout_tail, stderr_tail}` 作为结构化证据，拼进
   `prior_checklist_lines` 或新增一个 `verification_result_block`，一并传给判官。
   这样即使 `judge_tools_enabled=False`，判官依然能拿到程序化验证的结果，而不是完全依赖自述文本。
3. 这一步和"判官自己挂工具验证"（`judge_tools_enabled=True`）不冲突，是互补关系：
   **`GoalRunner` 强制跑一次预设命令拿到确定性证据（低成本、无歧义）**，
   **判官挂工具是探索式验证的兜底（针对没有预设命令的标准）**。两者都可以开启。

#### 实现记录（本次改造）

方案的两步都已落地：

1. `GoalRunner._build_prompt()` 在 `GoalSpec.verification_command` 非空时追加一段"自验证要求"
   提示，要求主 Agent 结束本轮前主动执行一次该命令并总结结果。
2. 新增 `GoalRunner._run_verification_command()`：在 `run()` 主循环拿到 `step.output` 之后、
   调用 `_run_judge()` 之前，用 `subprocess.run(shell=True, cwd=cfg.project_root)` 程序化执行一次
   `verification_command`，返回 `{command, returncode, stdout_tail, stderr_tail}`（超时/异常时
   `returncode=None`，把异常信息放进 `stderr_tail`，不静默吞掉）。
   - 新增配置项：`auto_verify_enabled`（默认 `True`）、`auto_verify_timeout`（默认 120 秒）、
     `auto_verify_output_tail_lines`（默认 40 行，避免长输出污染 judge 上下文）。
   - `build_goal_judge_prompt()` / `run_goal_judge()` 新增 `verification_result` 参数，
     结果通过新增的 prompt fragment `VERIFICATION_RESULT_BLOCK`（`prompts/fragments/goal_mode.md`）
     渲染进 `prompts/user/goal_judge_request.md` 模板新增的 `{{verification_result_block}}` 占位。
   - `judge_tools_enabled=True`（判官自己挂工具验证）完全不受影响，两者可同时开启。
3. 新增测试：`test_goal_runner_auto_verify_executes_command_and_passes_result_to_judge`、
   `test_goal_runner_auto_verify_disabled_by_config`、`test_goal_runner_auto_verify_no_command_returns_none`、
   `test_build_prompt_includes_self_verify_hint_when_command_set`。

---

## 3. 进展度量：从二元卡住判断到细粒度进展信号

### 现状：已经有"语义进展判断"，但仍是三态分类，不是连续的进展分数

`progress_judge_mode="llm"`（默认）下，判官每轮输出 `progress` 字段，取值
`SUBSTANTIVE_ADVANCE / SAME_APPROACH_NO_GAIN / REGRESSED`（`verdict.extra["progress"]`），
`stuck_detector.py::observe_signal(is_same: bool)` 把它简化成一个布尔量喂进卡住计数。

相比更早的纯文本相似度方案（`text_similarity` 模式，`difflib.SequenceMatcher`），这已经是一次
质的提升——能识别"表述不同但本质相同"和"表述相似但确有进展"。但离你提出的"进展分数"仍有距离：

- 三态分类本质上还是把连续的"进展程度"压缩成了粗粒度的三档，**没有量化的分数**，无法做"长期平缓
  但不为零"这种趋势判断——判官这一轮说 `SUBSTANTIVE_ADVANCE`，下一轮再问，可能又是
  `SUBSTANTIVE_ADVANCE`，但如果这些"进展"都是些无关痛痒的小修小补，目前的机制识别不出这是伪进展，
  因为每一轮单独看都"有进展"，只有连起来看趋势才会发现是原地打转。
- 完全依赖判官的主观语义判断，**没有客观的、程序化可计算的信号**参与（文件 diff 量、
  checklist 通过数变化这类"数出来的"信号），单一信号源存在被误判带偏的风险（第一版文档里提到过，
  这里重申是因为它直接关系到"伪进展识别"这个诉求）。

### 缺口 3.1：没有把已有的 `_criteria_status` 通过数变化纳入进展判断

这是最容易补的一块——数据已经存在，只是没被用上。`_criteria_status` 每轮都会被判官返回的
`checklist` 更新（`passed / evidence / last_updated_round`），完全可以算出"本轮相对上一轮，
新增通过了几条标准"这个客观数字，作为进展分数的一个分量：

```python
def _compute_progress_score(self) -> float:
    """粗粒度进展分数：结合 checklist 客观通过数增量 + judge 主观 progress 判断。"""
    passed_now = sum(1 for c in self._criteria_status if c.get("passed"))
    delta = passed_now - self._last_passed_count  # 需要新增一个字段记录上一轮通过数
    self._last_passed_count = passed_now

    subjective = {
        "SUBSTANTIVE_ADVANCE": 1.0,
        "SAME_APPROACH_NO_GAIN": 0.0,
        "REGRESSED": -1.0,
        None: 0.0,
    }.get(progress_info.get("progress"), 0.0)

    # 客观信号（checklist 增量）作为主观判断的校验/加权，而不是替代
    if delta > 0:
        return max(subjective, 0.3 * delta)   # 哪怕主观判空进展，客观有硬指标增量也不该判 0
    if delta < 0:
        return min(subjective, -0.5)          # 标准从"通过"退化为"未通过"，明确的倒退信号
    return subjective
```

#### 实现记录（本次改造）

按方案落地，函数签名/逻辑与方案里的示例基本一致（细节：`subjective` 缺失 progress 字段时不再
强行取 0.0，而是先看是否有客观 delta 可用，两个信号源都不可用才返回 `None`，调用方据此判断
"这一轮是否有可用的进展分数"，不强行伪造一个 0.0）：

- `GoalState` 新增 `last_passed_count` / `progress_scores` 字段，随其它状态落盘/恢复。
- `GoalRunner.__init__` 新增 `self._last_passed_count` / `self._progress_scores`（有上限，避免
  无界增长，供未来 §3.2 直接复用这份序列）。
- 新增 `GoalRunner._compute_progress_score()`，在 `_run_judge()` 里 checklist 解析完成之后调用，
  结果写入 `progress_info["progress_score"]`（`None` 时不写入这个 key，不影响现有调用方）。
- 新增配置项 `progress_score_enabled`（默认 `True`）。
- 本次只落地"分数计算与记录"，**不改变**现有 `_check_stuck()` 的卡住判定逻辑——分数序列已经
  持久化在 `self._progress_scores` / `GoalState.progress_scores` 里，为 §3.2（伪进展趋势识别）
  预留了直接可用的数据基础，但 §3.2 本身（`ProgressTracker` 及接入 `_check_stuck`）尚未实现。
- 新增测试：`test_progress_score_objective_delta_overrides_no_gain_subjective`、
  `test_progress_score_regression_caps_negative`、`test_progress_score_disabled_by_config`、
  `test_goal_state_progress_score_fields_roundtrip`。

### 缺口 3.2：伪进展（长期平缓但不为零）的识别完全空缺

目前 `StuckDetector` 只有"是否等同于卡住"这一个二元判断，累积的是"连续相同次数"，**没有任何机制
识别"每轮都有一点点进展，但累积起来毫无实质意义"这种模式**（比如连续 10 轮都是
`SUBSTANTIVE_ADVANCE`，但 checklist 通过数始终是 0，只是在同一处代码反复微调）。

**改进方案**：把 `StuckDetector` 的判定逻辑从"当前是否等同于上一次"扩展为"最近 K 轮的进展分数
是否呈现有意义的上升趋势"：

```python
@dataclass
class ProgressTracker:
    """在 StuckDetector 之外新增一层，跟踪最近 N 轮进展分数序列，识别"平缓但非零"的伪进展。"""
    window: int = 5
    stagnation_score_threshold: float = 0.15   # 累积斜率低于此值视为"平缓"

    _scores: list[float] = field(default_factory=list)

    def observe(self, score: float) -> bool:
        """返回 True 表示"检测到伪进展趋势"，调用方应据此触发和 stuck 同等级别的干预。"""
        self._scores.append(score)
        if len(self._scores) > self.window:
            self._scores = self._scores[-self.window:]
        if len(self._scores) < self.window:
            return False
        # 简单线性趋势估计：早期均值 vs 后期均值的差
        half = self.window // 2
        early_avg = sum(self._scores[:half]) / half
        late_avg = sum(self._scores[half:]) / (self.window - half)
        # checklist 通过数长期没有实质累积增长，即便主观 progress 一直非负
        return (late_avg - early_avg) < self.stagnation_score_threshold and max(self._scores) < 0.5
```

`GoalRunner._check_stuck()` 里在原有的 `StuckSignal` 判断基础上，增加对 `ProgressTracker.observe()`
的检查——**任一个判定为"卡住/伪进展"都应该触发恢复流程**，而不是只依赖 `StuckDetector` 的
"连续相同"这一种模式。这直接对应你提出的"进展分数长期平缓但不为零时也应该触发干预"。

---

## 4. Compact 时机的"探索 vs 收敛"双模式

### 现状：完全没有这个概念，compact 触发只有"被动响应"，没有"主动节奏控制"

目前所有 compact 触发点都是被动的：
- `hit_max_turns` 撞硬顶
- 判官返回 `NEED_COMPACT`
- `StuckDetector` 判定卡住

**没有任何机制根据"当前处于探索阶段还是收敛阶段"主动调整 compact 的频率**。也就是说，如果
agent 正在顺利推进（每轮都有实质进展），和 agent 正在到处试错但还没触发 stuck 阈值，走的是
完全一样的 compact 节奏（除非撞上述三个触发点，否则压根不 compact，直到 `max_turns` 硬顶）。

### 改进方案

引入一个"阶段判断"，依据第 3 节新增的进展分数序列来划分：

```python
class GoalPhase(str, Enum):
    EXPLORING = "exploring"    # 尚未出现稳定正向进展（早期，或伪进展中）
    CONVERGING = "converging"  # 最近连续出现正向进展（checklist 通过数在稳定增长）
```

- **`EXPLORING` 阶段**：缩短"主动 compact"的判断窗口——比如原本 `consecutive_same_feedback_limit=3`
  才触发卡住恢复，探索阶段可以更激进一点，用一个更短的"轻量 compact"周期（对应第 1 节的分级
  compact 里的"轻量"档）主动做一次压缩，哪怕还没被正式判定为"卡住"，也定期给 agent 一次跳出
  当前上下文重新审视的机会——这不需要等 `StuckDetector` 判定，可以是纯粹按轮次节奏触发
  （比如每 `M` 轮，`M < consecutive_same_feedback_limit`）。
- **`CONVERGING` 阶段**：暂停"主动"这一层触发，只保留"被动"的三个安全阀（撞硬顶/判官建议/真正卡住），
  避免把正在起效的上下文过早压缩掉。

具体接入点：`runner.py::run()` 主循环里，在拿到 `progress_info` 之后、判断是否 `_check_stuck` 之前，
维护一个 `self._phase` 状态；`_build_prompt()` 或专门的一个 `_maybe_proactive_compact()` 方法
根据 `self._phase` 决定是否在本轮追加一次主动 compact。这是一个新增的旁路判断，不影响现有
`hit_max_turns` / `NEED_COMPACT` / stuck 三条已有路径,是纯增量。

对应配置项建议新增：

```python
# config/models.py GoalModeConfig 新增
proactive_compact_enabled: bool = False     # 默认关闭，避免默认行为突变
exploring_compact_interval: int = 2         # 探索阶段每 N 轮主动触发一次轻量 compact
phase_convergence_window: int = 3           # 连续 N 轮正向进展分数才切换到 converging
```

默认关闭是必要的——这是一个行为改动较大的新机制，应该让用户显式开启，观察一段时间效果后再考虑
调整默认值。

---

## 5. Goal 分解与重规划的显式支持

### 现状：目标一旦 `confirmed` 就完全冻结，没有运行期修正入口

`revise()` 方法（`spec.py`）只在协商阶段（confirm 之前）调用；`GoalRunner.run()` 内部
从始至终使用同一个 `self._spec`，没有任何代码路径允许在执行期间修改 `goal_text` /
`acceptance_criteria`。多次卡住恢复用尽额度后，唯一的结局是 `status="stuck"` 直接终止
（`_finish()`），把诊断信息写进 memory（`_write_failure_lesson`），然后需要用户手动重新
走一遍 `/goal` 协商流程从头开始。

这完全符合"不能让 agent 悄悄改验收标准"的安全原则（这一点必须保留，不能退步），但代价是
**没有一个"提议 → 人工审阅 → 批准后生效"的中间态**——你提出的"允许 agent 主动提议拆分 goal
或修改 acceptance criteria，但标记出来给人工审阅"这个方向，目前完全空缺。

### 改进方案

新增一种 agent 可以主动发起、但**只能是提议、不能自动生效**的动作，建议设计为新的判官状态或
独立触发点，而不是让主 Agent 直接调用 `revise()`（那样等于绕过审阅直接改标准，违反安全原则）：

**触发条件**：多次 stuck 恢复后（比如 `recoveries_used >= max_stuck_recoveries - 1`，
即将耗尽额度但还没真正终止的最后一次机会），`GoalRunner` 在触发 compact 恢复的同时，
额外要求主 Agent（或判官）输出一份"重规划提议"：

```python
# _try_stuck_recovery 的最后一次恢复机会时，额外拼一段提示
if self._stuck_detector.recoveries_used == self._gm_cfg.max_stuck_recoveries - 1:
    hint += (
        "\n\n这是最后一次自动恢复机会。如果你认为反复卡住的根本原因是目标定义本身"
        "有问题（比如某条验收标准依赖了不存在的前提、目标范围过大难以一次性完成"
        "等），请在本轮输出中明确提出【重规划提议】，包括：建议如何拆分目标为更小的"
        "子目标，或建议修改/放宽哪条验收标准及理由。这只是提议，不会自动生效，"
        "会展示给用户决定是否采纳。"
    )
```

`GoalRunner` 解析主 Agent 或判官输出里的"重规划提议"标记段落（可以复用 `verdict.py` 的结构化
JSON 协议思路，让判官在最后一次恢复窗口的输出里增加一个可选字段
`replan_proposal: {suggested_split: [...], suggested_criteria_changes: [...], reason: str}`），
如果非空：

1. **不自动应用**——`self._spec` 依然不变，继续跑完这最后一次恢复机会。
2. 如果最终仍然 `stuck` 终止，`_finish()` 时把 `replan_proposal` 一并展示给用户（不只是写进
   memory，而是 CLI 里明确打印出来），格式类似：

   ```
   [GoalRunner] 目标未达成（状态：stuck）。
   Agent 提出了以下重规划建议，供参考：
     - 建议拆分为子目标：[...]
     - 建议调整验收标准：标准 3（xxx）建议放宽为...，理由：...
   是否要基于以上建议重新协商目标？可执行 `/goal revise` 查看详情。
   ```

3. 用户确认后，走现有的 `/goal revise` 命令（复用 `spec.py::revise()`），但这次
   `user_feedback` 参数直接注入 agent 的 `replan_proposal` 内容作为参考起点，而不需要用户
   自己重新组织语言描述问题——用户只需要"确认/调整/拒绝"，而不是从零开始。

**关键约束（必须写进实现)**：
- `replan_proposal` 全程只是数据，不能有任何代码路径让它绕过用户确认直接改写 `GoalSpec`。
- 这个提议动作**只在即将耗尽恢复额度时触发一次**，不应该让 agent 在每一轮都尝试"申请改标准"——
  否则会变成一个更隐蔽的"说服判官放宽标准"的漏洞,这是要严格避免的。

---

## 优先级建议（仅限本次讨论的五个方向内部排序）

| 优先级 | 改进项 | 理由 | 状态 |
|---|---|---|---|
| P0 | §1.2 Dead-end 从滚动窗口升级为持久清单 | 成本低（复用现有 `_is_near_duplicate` 工具），直接修复"排除路径被窗口冲掉"的实际缺陷 | ✅ 已实现 |
| P0 | §2.2 自验证优先（GoalRunner 强制执行 verification_command 拿客观证据） | `verification_command` 目前完全没被执行，是最明显的"写了但没用上"的缺口，且改动集中、风险低 | ✅ 已实现 |
| P1 | §3.1 进展分数纳入 checklist 通过数变化 | 数据已存在，只是没被使用，改动小、见效快 | ✅ 已实现 |
| P1 | §1.1 / §1.3 分级 compact + NEED_COMPACT 路径共享 dead-end 注入 | 依赖 §1.2 的持久清单先落地 | ✅ 均已实现 |
| P2 | §2.1 过程判断 / 结果判断分离（process_flags） | 需要改判官 prompt + 新状态语义,改动面稍大,但风险收益都高,建议紧随 P1 | ✅ 已实现（复用 DONE→CONTINUE 降级，未新增状态） |
| P2 | §3.2 伪进展趋势识别（ProgressTracker） | 依赖 §3.1 先有分数化的进展信号 | 未实现 |
| P3 | §4 探索/收敛双模式主动 compact | 新机制，默认关闭，建议在前面几项稳定后再引入，避免同时改动太多变量难以定位问题 | 未实现 |
| P3 | §5 Goal 重规划提议 | 收益明确但涉及新交互流程（CLI 展示 + `/goal revise` 联动），建议放在最后，且严格限制"只能在耗尽额度前提议一次" | 未实现 |
