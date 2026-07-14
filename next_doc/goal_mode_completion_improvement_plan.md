# Goal 模式改造计划：从"能跑完"到"真的把任务做完"

> **实现状态（更新于本次改造落地后）**：
> - ✅ 改造项一（LLM 判进展替代规则相似度）—— 已实现，见
>   `role_agents/goal_judge.py` / `goal_mode/runner.py::_check_stuck` /
>   `cfg.goal_mode.progress_judge_mode`
> - ✅ 改造项二（已尝试路径清单）—— 已实现，见
>   `goal_mode/runner.py::_try_stuck_recovery` /
>   `cfg.goal_mode.stuck_recovery_attempted_paths_enabled`
> - ✅ 改造项三（验收标准逐条状态追踪）—— 已实现，见
>   `goal_mode/state.py::GoalState.criteria_status` /
>   `cfg.goal_mode.criteria_tracking_enabled`
> - ✅ 改造项五（失败经验沉淀）—— 已实现，见
>   `goal_mode/runner.py::_write_failure_lesson` /
>   `cfg.goal_mode.failure_lesson_enabled`
> - ⏳ 改造项四（并行多路径择优）、改造项六（细粒度执行器）—— **未实现**，
>   仅在 `GoalModeConfig` 里占位了 `stuck_recovery_ensemble_enabled` /
>   `stuck_recovery_candidates` / `fine_grained_execution_enabled` 三个配置
>   字段。这两项工作量和风险都明显更高，需要先观察前四项上线后的真实
>   触发频率再决定优先级和具体设计，独立的后续推进计划见
>   [`next_doc/goal_mode_stage2_ensemble_and_fine_grained_plan.md`](goal_mode_stage2_ensemble_and_fine_grained_plan.md)。
>
> 使用文档见 [`docs/goal-mode-guide.md`](../docs/goal-mode-guide.md)
> "卡住恢复"及后续几节；单元测试见 `tests/test_goal_mode.py`
> （`test_goal_runner_llm_progress_*` / `test_goal_runner_tracks_criteria_checklist` /
> `test_goal_runner_stuck_recovery_hint_includes_attempted_paths` /
> `test_goal_runner_writes_failure_lesson_on_stuck` 等）。
>
> 以下是本文档的原始设计内容，保留作为设计决策记录。



> 本文档基于对 `goal_mode/`、`role_agents/goal_judge.py`、`role_agents/stuck_detector.py`、
> `role_agents/verdict.py` 现有实现的梳理，针对"Goal 模式当前跑得起来，但完成质量/效率
> 还有提升空间"这一问题，给出六项可独立落地的改造，并给出建议的实施顺序。
>
> 前置结论（避免重复造轮子）：Goal 模式**已经**实现了"卡住 → compact → 换思路重试"的
> 完整闭环（`StuckDetector` + `GoalRunner` 的卡住恢复逻辑，见 `docs/goal-mode-guide.md`
> "卡住恢复"一节）。本文档不是从零设计这个机制，而是针对它的一个具体弱点（规则式判卡住）
> 做升级，并补充另外五项配套改造。

---

## 一、现状与问题

### 1.1 现有卡住检测的工作方式

`role_agents/stuck_detector.py::StuckDetector.observe()`：

```python
ratio = difflib.SequenceMatcher(None, self._prior_output, output).ratio()
if ratio >= self.similarity_threshold:
    self._consecutive_same += 1   # 判定"雷同"
else:
    self._consecutive_same = 0    # 判定"有进展"，重置
```

`GoalRunner` 每轮拿 GoalJudge 返回的 `feedback` 文本喂给它，连续 N 轮文本相似度
达标就触发"卡住恢复"（compact + 换角度提示）。

### 1.2 核心问题：文本相似度 ≠ 是否真的有进展

这是纯字符串层面的规则算法，不理解语义，会漏判/误判：

- **假阴性（漏判真卡住）**：agent 每轮换一种说法汇报同一个失败结果（"还是报错"→
  "问题依旧存在"→"仍未解决"），文本相似度可能被差异化的措辞拉低到阈值以下，
  永远触发不了卡住恢复，一路空转到 `max_rounds` 耗尽。
- **假阳性（误判假卡住）**：agent 在稳步推进同一类修复（"测试 A 通过，B 仍失败"→
  "测试 B 通过，C 仍失败"），反馈文本结构高度相似（都是"通过/失败"的清单格式），
  会被误判为卡住，进而触发不必要的 compact，浪费 token 且可能打断正在生效的策略。
- **无法区分"表述不同但本质相同"和"表述相似但实质不同"**——而这恰恰是语言模型
  最擅长判断、规则算法完全做不到的事。

### 1.3 关联问题（本文档一并处理）

在梳理这个问题的过程中，一并发现四个相关的、值得同批改造的缝隙（详见第三节）：

1. 卡住恢复时的"换思路"提示是通用话术，没有指出"哪些路径已经试过"
2. GoalJudge 每轮对全部验收标准整体核查，没有逐条状态追踪，判定容易抖动
3. 卡住恢复目前是"单路径重来"，没有利用已有的 ensemble 基础设施做并行择优
4. 卡住/耗尽终止后，本次失败经验没有沉淀，下次同类目标会重新踩坑
5. 执行粒度停留在"整个 run_turn 跑完才评审"，方向错了也要等预算耗尽才发现

---

## 二、改造项一（核心）：用 LLM 判断"是否有实质进展"替代规则相似度

### 2.1 设计原则

**不新增一次独立的 LLM 调用**，而是把"是否有实质进展"作为 GoalJudge 本来就要做的
判断的一个**结构化输出字段**——GoalJudge 每轮本来就要看"agent_output + 上一轮反馈"
来决定 DONE/CONTINUE/NEED_COMPACT，让它顺手多判一个维度，不额外增加成本。

这也比造一个新的"独立 LLM 卡住检测器"更合理：GoalJudge 本身已经掌握全部上下文
（验收标准、本轮产出、上一轮反馈），比任何外部检测器更有判断资格。

### 2.2 JudgeVerdict 结构扩展

`role_agents/verdict.py::JudgeVerdict` 已经支持 `extra: dict` 字段承载
`status`/`feedback` 之外的任意结构化字段，不需要改动 `parse_judge_verdict` 本身，
只需要：

1. 修改 GoalJudge 的输出 schema 提示词（`prompts/system/goal_judge.md` /
   `prompts/user/goal_judge_request.md`），要求额外输出一个 `progress` 字段：

```json
{
  "status": "CONTINUE",
  "feedback": "...",
  "progress": "SUBSTANTIVE_ADVANCE",
  "progress_reason": "上一轮测试 A/B 均失败；本轮 A 已修复通过，B 仍失败但报错信息从 NPE 变为断言失败，说明修复方向对但未完成"
}
```

   `progress` 取值三态（避免二元判断丢失"部分进展"这一常见情况）：

   | 值 | 含义 | GoalRunner 行为 |
   |----|------|-----------------|
   | `SUBSTANTIVE_ADVANCE` | 相比上一轮有实质推进（哪怕验收标准仍未全部通过） | 重置卡住计数，正常 CONTINUE |
   | `SAME_APPROACH_NO_GAIN` | 本轮和上一轮本质是同一个策略/同一个错误，没有新进展 | 计入"卡住"信号 |
   | `REGRESSED` | 本轮反而比上一轮更差（引入新错误、破坏了已通过的标准） | 计入"卡住"信号，且优先级高于 SAME_APPROACH_NO_GAIN（提示词里单独说明"退步"情况需要指出具体退步点） |

   `progress_reason` 是给人看的（终端展示 + 落盘排查用），不参与状态机判断，
   但要求模型必须给出具体理由，能倒逼判断不是随手打的标签。

2. Prompt 层面明确要求 LLM 参照的判断依据（写入
   `prompts/user/goal_judge_request.md` 新增小节）：
   - 对比本轮 `agent_output` 与 `prior_feedback` 中提到的失败点/错误信息是否发生变化
   - 如果 `judge_tools_enabled=true`，鼓励结合自己跑的验证结果（而不仅是 agent 自述）
   - 明确排除"纯粹换了措辞但内容相同"这种情况计入 SUBSTANTIVE_ADVANCE

### 2.3 GoalRunner 侧改造

`goal_mode/runner.py` 中原本喂给 `StuckDetector.observe(judge_feedback_text)`
的调用，改为直接读取 `JudgeVerdict.extra["progress"]`：

```python
progress = verdict.extra.get("progress")
if progress == "SUBSTANTIVE_ADVANCE":
    stuck_detector.reset()
    signal = StuckSignal.NONE
elif progress in ("SAME_APPROACH_NO_GAIN", "REGRESSED"):
    signal = stuck_detector.observe_signal()  # 见 2.4，不再传文本，只计数
else:
    # progress 字段缺失/解析失败（如模型没有按新 schema 输出）→ 保守回退到
    # 原有的文本相似度规则，不因为升级而降低鲁棒性
    signal = stuck_detector.observe(verdict.feedback)
```

**保留降级路径**：`progress` 字段解析失败时自动回退到原来的 `difflib` 规则，
而不是让整个卡住检测失效——这是升级期间必须保留的安全网，也符合项目一贯的
"解析失败保守处理，绝不静默变成更危险的状态"的约定（参考 `verdict.py` 里
`parse_ok=False` 时恒定回退到 `fallback_status` 的做法）。

### 2.4 StuckDetector 接口新增一个"仅计数"模式

`StuckDetector` 目前的 `observe(text)` 同时承担"比较文本"和"计数"两件事。
新增一个轻量方法，跳过文本比较，只做计数与恢复额度管理：

```python
def observe_signal(self, *, is_same: bool) -> StuckSignal:
    """跳过内部的 difflib 比较，直接接受调用方（如 GoalRunner 基于 LLM
    progress 字段）判断好的"本轮是否等同于卡住"结果，复用既有的连续计数 /
    恢复额度 / GIVE_UP 逻辑。TurnJudge 等仍用文本比较的调用方不受影响，
    继续使用原 observe(text)。"""
    if is_same:
        self._consecutive_same += 1
    else:
        self._consecutive_same = 0
        self._recoveries_used = 0
    if self._consecutive_same >= (self.consecutive_limit - 1):
        if self._recoveries_used >= self.max_recoveries:
            return StuckSignal.GIVE_UP
        self._recoveries_used += 1
        self._consecutive_same = 0
        return StuckSignal.RECOVER
    return StuckSignal.NONE
```

这样改造只新增一个方法，不改动 `observe()` 原有行为，`TurnJudge` 一侧（主 Agent
输出的卡住检测，没有 GoalJudge 这种结构化 progress 判断基础）完全不受影响，
仍用原来的文本相似度路径——**这条改造只作用于 Goal 模式，不影响 TurnJudge**。

### 2.5 配置新增

```json
{
  "goal_mode": {
    "progress_judge_mode": "llm",  // "llm"（默认，新行为）| "text_similarity"（旧行为，一键回退）
  }
}
```

`text_similarity` 保留原有 `same_feedback_similarity_threshold` 规则路径完全不变，
供不信任 LLM 判断或想复现旧行为的场景使用。默认给 `llm`，因为本身没有额外调用成本，
只是同一次 GoalJudge 调用多输出一个字段。

### 2.6 风险与兜底

- **模型没有按新 schema 输出 `progress` 字段**：走 2.3 中的自动回退，不阻断执行
- **模型判断本身也可能错**（比如把退步误判成推进）：这是"用 LLM 判断"相比规则算法
  必然要接受的权衡，但通过 `progress_reason` 强制给理由、并把这段理由展示在终端
  （复用现有 `format_feedback()` 展示逻辑），可以让用户在 `judge_show_prompt=true`
  时人工核查判断依据，而不是完全黑箱
- **不会增加 LLM 调用次数**：这是相对"新增一个独立卡住检测 LLM 调用"方案的关键
  优势，只是让已有的 GoalJudge 调用多承担一项判断

---

## 三、改造项二至六（配套改造）

### 改造项二：卡住恢复提示携带"已尝试路径清单"，而非通用话术

**问题**：现有"疑似卡在同一问题上，请换角度"提示是固定模板，没有说明具体
哪些路径已经验证无效，agent 换个说法后可能绕回同一个思路。

**改造**：
1. `GoalRunner` 在触发 `StuckSignal.RECOVER` 时，除了执行既有的 compact，
   额外把最近 N 轮（建议 `consecutive_same_feedback_limit` 轮）的
   `progress_reason`（改造项一新增字段）拼接成一段"已尝试路径及失败原因"文本
2. 新增一个 prompt 片段模板（`prompts/fragments/goal_mode.md` 新增
   `STUCK_RECOVERY_ATTEMPTED_PATHS_BLOCK`），把这段文本嵌入卡住恢复提示：

   ```
   你连续几轮的尝试没有取得实质进展，以下是已经验证无效的方向，请不要重复：
   1.（第 N-2 轮）...
   2.（第 N-1 轮）...
   3.（第 N 轮）...
   请基于以上信息，明确选择一个不同于以上的新方向，并说明为什么这次会不同。
   ```

3. 这一项直接依赖改造项一的 `progress_reason` 字段，建议同批实现。

**收益**：把"换个角度"从一句空话变成有具体依据的约束，减少"换汤不换药"的重试。

---

### 改造项三：验收标准逐条状态追踪，降低判定抖动、聚焦反馈

**问题**：GoalJudge 每轮对全部验收标准整体核查，容易因表述差异导致同一条标准
在不同轮次判定结果抖动，且每轮都要重新论证已经通过的条目，浪费上下文。

**改造**：

1. `GoalSpec`（`goal_mode/spec.py`）的验收标准从 `list[str]` 改为在 `GoalState`
   侧新增一个平行的追踪结构（不改 `GoalSpec` 本身，`GoalSpec` 冻结后不应变化）：

```python
# goal_mode/state.py 新增
@dataclass
class CriterionStatus:
    index: int
    text: str
    passed: bool = False
    last_evidence: str = ""      # 判定通过/失败时 Judge 给出的具体依据
    last_updated_round: int = 0

# GoalState 新增字段
criteria_status: list[CriterionStatus] = field(default_factory=list)
```

2. GoalJudge 的输出 schema 扩展 `checklist` 字段（`verdict.py` 的 `extra` 已经
   预留了透传任意字段的能力，不需要改 `parse_judge_verdict`）：

```json
{
  "status": "CONTINUE",
  "feedback": "...",
  "progress": "SUBSTANTIVE_ADVANCE",
  "checklist": [
    {"index": 1, "passed": true,  "evidence": "pytest 全部通过"},
    {"index": 2, "passed": false, "evidence": "lint 仍有 3 处报错：..."}
  ]
}
```

3. Prompt 层面规则：**已经标记 `passed: true` 的条目，除非本轮有明确证据表明
   被破坏（比如新代码改动波及），否则不应回退为 false**——这条规则写入
   `prompts/system/goal_judge.md`，并在 `goal_judge_request.md` 里把上一轮
   `checklist` 状态作为输入传给模型（"以下是上一轮各条标准的通过情况，若本轮
   没有相反证据，请保持一致"），从根源上减少抖动，而不只是靠后处理"锁定已通过项"
   （后处理锁定的风险是万一真的被破坏了却因为规则强制锁定而检测不到，所以
   优先做"prompt 提示 + 保留证据可回退"，而不是代码层面硬锁定）。

4. 每轮的 `feedback` 生成规则收紧：`CONTINUE` 时只要求描述"尚未通过的条目"，
   不必重复已通过的部分——反馈更短、更聚焦，也间接降低了 token 消耗。

**收益**：判定更稳定，反馈更聚焦"还差什么"，与改造项一的 `progress` 字段
配合（`checklist` 通过数增加即是最直接的"实质进展"证据来源之一，也可以反过来
喂给 progress 判断做交叉验证）。

---

### 改造项四：卡住恢复从"单路径重来"升级为"并行多路径择优"

**问题**：现有卡住恢复是 compact 后同一个 agent 单线程再试一次，本质仍是串行
试错，恢复额度（`max_stuck_recoveries`）用完后大概率仍是同一种思维定式的变体。

**改造**：项目已有 `ensemble/`（`runner.py` + `judge.py` + `strategies.py`，
best-of-n 生成与择优）基础设施，卡住恢复触发时改为：

1. 以提高 `temperature`/在 prompt 中显式要求"给出与之前不同的策略"为条件，
   并行生成 2~3 个候选 continuation（复用 `ensemble/runner.py` 的并发执行能力）
2. 用一个轻量 judge（可直接复用 GoalJudge 本身，或复用 `ensemble/judge.py`
   的择优逻辑）对候选结果打分，选择"验收标准通过数最多 / 最有希望"的一个
   作为本轮实际结果继续
3. 仅在触发 `StuckSignal.RECOVER` 时启用（正常轮次不并行，避免无谓的成本增加）

**配置新增**：

```json
{
  "goal_mode": {
    "stuck_recovery_ensemble_enabled": false,  // 默认关闭，按需开启（有额外 token 成本）
    "stuck_recovery_candidates": 3
  }
}
```

**收益**：把探索从"重复同一策略换个说法"变成"真正的多路径并行探索"，是对
"自主探索"能力最直接的复用，且限定只在卡住时触发，成本可控。

**依赖顺序**：建议在改造项一（LLM 判进展）稳定之后再做，因为"是否触发卡住恢复"
的判断质量直接决定这项改造的触发时机是否准确。

---

### 改造项五：终止时把失败经验沉淀为 lesson，供未来同类目标复用

**问题**：`stuck` / `max_rounds_exhausted` 终止后只是"如实汇报"，经验留在
本次 session 里，下次面对同一个 workdir 的类似目标会重新踩同样的坑。

**改造**：

1. GoalRunner 在终止分支（`stuck`/`max_rounds_exhausted`）新增一步：把
   `GoalState` 中累计的失败路径记录（改造项二中已经在积累的 `progress_reason`
   历史 + 最终 `checklist` 中长期未通过的条目）整理成一条结构化 lesson，
   复用 `evolution/lesson_to_reminder.py` 的既有落盘/去重机制写入
   lesson store，标记来源 `source="goal_mode_failure"`
2. `goal_mode/spec.py::GoalSpecBuilder` 生成新目标草案时，新增一步检索：
   如果检测到当前 workdir 存在相关的 `goal_mode_failure` lesson（可用现有
   memory 的语义检索能力匹配目标文本相似度），在生成的验收标准草案里附带
   一条提示（不阻断流程，只是提前告知用户/agent）："检测到之前类似目标
   曾因 XX 失败终止，建议确认这次是否要规避同样的方法"

**收益**：这是把 Goal 模式和既有记忆系统（`evolution/lesson_to_reminder.py`、
`perception/hybrid_memory_backend.py`）真正打通的具体钩子，避免"失败了但没学到
东西"。

---

### 改造项六：细粒度执行器（`FineGrainedStepExecutor`）

**问题**：`goal_mode/executor.py` 已经预留了 `GoalStepExecutor` 抽象接口，
但目前只有粗粒度实现 `CoarseStepExecutor`——一次完整 `run_turn`（可能几十次
工具调用）跑完才评审一次，方向错了也要等整个 `max_turns` 预算耗尽才被发现。

**改造**（工作量最大，建议放在最后，且可以先只做最小验证版本）：

1. 新增 `FineGrainedStepExecutor(GoalStepExecutor)`，在 `_agentic_loop()` 内部
   每完成一定数量的工具调用（可配置，如每 5 次）后，插入一次轻量判断
   （不是完整 GoalJudge 调用，而是更便宜的"方向是否明显偏离"二分类判断）
2. 判断为"明显偏离"时，中断当前 `run_turn`，直接进入正常的 GoalJudge 评审 +
   反馈注入流程，不必等到 `max_turns` 耗尽
3. `GoalStepResult` 中已有的 `tool_calls_made`/`turns_used` 字段直接可用，
   不需要改动数据结构

**建议**：这一项复杂度和收益都最高，但也最容易引入新的不稳定因素（过于敏感的
中断判断可能打断正在推进的合理长任务），建议：
- 先做成默认关闭的实验性开关（`goal_mode.fine_grained_execution_enabled`）
- 中断阈值宁可保守（只在非常明显偏离时才中断），避免"半成品的东西被过早打断"
- 放在改造项一~三稳定运行、有实际数据支撑"平均多少轮/多少工具调用后开始跑偏"
  之后再决定具体的检查频率参数，而不是凭空定一个数字

---

## 四、建议实施顺序

```
改造项一（LLM 判进展，核心）
  │  收益最大、改动集中在 prompt + JudgeVerdict.extra 字段读取，
  │  不改变现有架构，且有完整的规则算法回退路径，风险最低
  │
  ├─→ 改造项三（验收标准逐条追踪）
  │     与改造项一共享"利用结构化 checklist 做交叉验证"的设计，建议同批或
  │     紧随其后实现
  │
  ├─→ 改造项二（已尝试路径清单）
  │     直接依赖改造项一的 progress_reason 字段，实现成本很低，可以和
  │     改造项一同一个 PR 完成
  │
  ├─→ 改造项五（失败经验沉淀）
  │     依赖改造项二/三积累的结构化数据，且是独立的收尾逻辑，不影响主循环，
  │     可以随时并行推进
  │
  ├─→ 改造项四（并行多路径择优）
  │     依赖改造项一判断卡住的准确度已经过验证，且涉及成本更高的 ensemble
  │     调用，建议在前面几项跑一段时间、拿到真实触发频率数据后再评估是否需要
  │
  └─→ 改造项六（细粒度执行器）
        工作量最大、风险最高，建议最后做，且先做成默认关闭的实验开关
```

---

## 五、验证方式建议

- 改造项一：找 3~5 个此前在旧规则下被判定为 `stuck` 的真实历史 `goal_state.json`
  记录（如果有留存的话），用新 prompt 重放对应轮次的 GoalJudge 调用，人工核查
  `progress` 判断是否符合预期（尤其关注"表述不同但本质相同"和"表述相似但有
  实质进展"这两类此前规则算法处理不好的边界情况）
- 改造项三：构造一个"验收标准 3 条，其中 1 条历史上下文本已经证明通过但表述
  变化导致误判回退"的测试用例，验证 prompt 约束是否生效
- 全部改造项：`tests/test_goal_mode.py` 中应补充对应的单元测试，尤其是
  `parse_judge_verdict` 对新增 `progress`/`checklist` 字段的解析容错测试
  （字段缺失、类型错误、非法枚举值等边界情况）
