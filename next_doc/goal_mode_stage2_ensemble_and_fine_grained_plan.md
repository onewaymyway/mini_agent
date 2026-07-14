# Goal 模式改造 Stage 2：并行多路径择优 + 细粒度执行器

> 本文档是 [`goal_mode_completion_improvement_plan.md`](goal_mode_completion_improvement_plan.md)
> 改造项四（卡住恢复并行多路径择优）与改造项六（细粒度执行器）的独立后续推进
> 计划。这两项在上一批改造（改造项一/二/三/五）中被**刻意搁置**，原因见下节；
> 本文档给出重新评估的前置条件、具体设计、以及分阶段落地路径。
>
> 前置状态：改造项一（LLM 判进展）/ 二（已尝试路径清单）/ 三（验收标准逐条
> 追踪）/ 五（失败经验沉淀）已经实现并有单元测试覆盖（见
> `tests/test_goal_mode.py` 中 `test_goal_runner_llm_progress_*` 等），使用
> 文档见 [`docs/goal-mode-guide.md`](../docs/goal-mode-guide.md)。
> `GoalModeConfig` 里已经为本文档两项占位了配置字段（默认关闭，无调度逻辑）：
> `stuck_recovery_ensemble_enabled` / `stuck_recovery_candidates` /
> `fine_grained_execution_enabled`。

---

## 一、为什么上一批改造没有一并做这两项

1. **工作量和风险明显更高**。改造项一/二/三/五都是"在现有 GoalJudge 结构化
   输出里多要几个字段 + GoalRunner 侧解析/记录"，不涉及新的执行路径；改造项
   四要接入 `ensemble/` 的并发调度，改造项六要侵入 `_agentic_loop` 内部的
   工具调用循环——两者都涉及新的控制流路径，出错的影响面更大。
2. **需要真实数据支撑参数设定，而不是凭空定数字**。改造项四"卡住恢复时并行
   几个候选、怎么选"、改造项六"每隔几次工具调用检查一次、判断阈值多严格"
   都需要"卡住恢复实际触发频率有多高""平均跑偏发生在第几次工具调用之后"这
   类经验数据，在改造项一~三/五还没有实际运行数据之前定下来的参数大概率是
   拍脑袋的。
3. **改造项四直接依赖改造项一的判断质量**。并行探索的触发时机（"什么时候
   算卡住,该并行探索了"）现在完全由改造项一的 `progress` 判断决定，如果这
   个判断本身还不稳定，在上面叠加一层更贵的并行调度只会放大误判的成本。

**结论**：这不是"不重要"，而是"现在做的性价比不够高"。本文档给出的是"什么
时候可以重新启动这两项、启动前需要看哪些数据、以及具体怎么做"，而不是立刻
开工的实现清单。

---

## 二、重新评估的前置条件（先观察，再决策）

在正式启动本文档任一项之前，建议先让改造项一/二/三/五运行一段时间（建议
至少覆盖数十次真实 `/goal` 执行），收集以下数据：

| 需要观察的数据 | 从哪里拿 | 用来决定什么 |
|----------------|----------|--------------|
| 卡住恢复（`StuckSignal.RECOVER`）实际触发频率 | `goal_state.json` 里 `stuck_recoveries_used` 的分布 | 改造项四值不值得做——如果卡住恢复本来就很少触发，并行探索的收益面很小 |
| 卡住恢复后，最终是 `done` 还是 `stuck`/`max_rounds_exhausted` 的比例 | 同上 + `GoalRunResult.status` | 如果恢复后大多数最终还是能 `done`，说明"压缩+提示换思路"已经够用，并行探索的边际收益有限 |
| 平均在第几次工具调用后 GoalJudge 才会发现方向跑偏（`progress=SAME_APPROACH_NO_GAIN`/`REGRESSED`） | 需要临时打点：在 `GoalStepResult.tool_calls_made` 与对应轮次的 `progress` 之间建立关联并记录 | 改造项六"每隔几次工具调用检查一次"的合理取值 |
| `progress_reason` 的信息量是否足够支撑"已验证无效路径"判断 | 人工抽查 `recent_progress_reasons` | 改造项四的候选择优 judge 能否复用现成信号，还是需要专门再跑一次判断 |

如果收集到的数据显示"卡住恢复触发很少、触发后大多能恢复"，建议改造项四
直接降低优先级（收益有限），把精力放在改造项六上；反之则优先做改造项四。

---

## 三、改造项四：卡住恢复从"单路径重来"升级为"并行多路径择优"

### 3.1 现状

`GoalRunner._try_stuck_recovery()` 目前的恢复动作是：compact 一次 + 注入
"换角度"提示（含已尝试路径清单，见改造项二）+ 重置计数，让**同一个** agent
在下一轮单线程再试一次。本质仍是串行试错。

### 3.2 设计：复用 `ensemble/` 而非新造调度器

项目已有 `ensemble/runner.py::run_subagent_ensemble()`——用多个 SubAgent
（不同 prompt/persona）并行跑同一个任务，再用 `ensemble/judge.py` 里的策略
（`first_success` / `vote` / `llm_judge`）择优。卡住恢复要做的事情本质上和
这个能力完全对应：

```python
# goal_mode/runner.py（示意，非最终实现）
from mini_agent.ensemble.runner import run_subagent_ensemble

def _try_stuck_recovery_ensemble(self) -> bool:
    variant_prompts = self._build_recovery_variant_prompts()  # 见 3.3
    result = run_subagent_ensemble(
        cfg=self._cfg,
        prompt=self._build_prompt(),
        n=self._gm_cfg.stuck_recovery_candidates,
        execution="parallel",
        strategy="llm_judge",
        variant_prompts=variant_prompts,
        session_id=self._agent.session_id,
    )
    if result.chosen_idx is None:
        return False  # 全部候选失败，交回调用方走正常终止流程
    # 把选中的候选内容当作本步产出，走正常的 GoalJudge 评审流程
    ...
```

### 3.3 候选多样性从哪里来

直接对同一个 prompt 跑 n 次意义不大（大概率仍是同一种思路的变体，尤其是
`temperature` 较低时）。候选多样性应该显式构造，复用改造项二已经积累的
"已尝试路径清单"数据：

- 候选 1：不加约束的默认 continuation（基线）
- 候选 2~n：在 prompt 里显式要求"不要使用以下已经验证无效的方法：
  {已尝试路径清单}，请给出一个明确不同的思路"（每个候选可以额外指定不同的
  切入角度提示，如"优先做诊断性检查而非直接修复"“检查是否是环境/前提假设
  问题”，具体角度提示词本身也是需要设计和迭代的部分，本文档不预先穷举）

### 3.4 择优方式

优先复用 GoalJudge 本身作为择优 judge（而不是 `ensemble/judge.py` 里通用
的 `llm_judge`），因为 GoalJudge 已经掌握验收标准这一最直接的评判依据——
让每个候选都过一遍 GoalJudge 评审，选验收标准通过数最多、或 `progress`
判断最正向的那个。这意味着这一步实际上是"n 次候选 continuation + n 次
GoalJudge 评审"，成本是单路径重试的 n 倍，因此：

- 仅在触发 `StuckSignal.RECOVER` 时启用（不影响正常轮次的成本）
- 默认关闭（`stuck_recovery_ensemble_enabled=false`），需要用户显式开启并
  承担额外成本

### 3.5 配置

```json
{
  "goal_mode": {
    "stuck_recovery_ensemble_enabled": false,
    "stuck_recovery_candidates": 3
  }
}
```

（这两个字段已经在上一批改造中占位，本项只需要实现调度逻辑本身。）

### 3.6 依赖与前置条件

- 依赖 `tools/orchestration.py` 的全局 `TaskManager` 已初始化（
  `run_subagent_ensemble` 内部会检查，未初始化时返回明确的 error 结果，
  `GoalRunner` 需要对这种情况做兜底——直接回退到单路径恢复，而不是报错
  中断整个 goal 执行）
- 依赖改造项一的 `progress` 判断已经稳定运行一段时间（见第二节的前置数据）

### 3.7 风险

- 并行候选可能都失败（都无法真正解决问题）——`chosen_idx is None` 时的
  行为应该是回退到"正常的单路径恢复"（即退化为改造前的行为），而不是
  直接判定整个 goal 失败
- token 成本明显上升，且并行执行对 `TaskManager` 的并发上限、API 速率
  限制更敏感，需要在小规模场景先验证（比如先只对 `n=2` 做实验）

---

## 四、改造项六：细粒度执行器（`FineGrainedStepExecutor`）

### 4.1 现状

`goal_mode/executor.py::GoalStepExecutor` 抽象接口已经预留好，`GoalStepResult`
也已经把 `tool_calls_made` / `turns_used` 字段填上，但只有粗粒度实现
`CoarseStepExecutor`——一次完整 `agent.run_turn()`（内部是
`turn_loop.py::_agentic_loop()` 的 `while loop_count < cfg.max_turns` 循环，
每次迭代对应一次 LLM 调用，可能包含多次工具调用）跑完才评审一次。

### 4.2 关键困难：现在没有"从外部中途打断 `run_turn`"的机制

调研现有代码发现，工具调用层面唯一的拦截点是 `tool_executor.py` 里的
`PreToolUse` hook（`hook_mgr.run("PreToolUse", ...)`），但它的语义是
**"是否阻止这一次具体的工具调用"**（`pre.blocked` → 该工具调用被替换成
一条 `[blocked by hook: ...]` 的错误结果，`_agentic_loop` 循环本身继续跑），
不是"提前结束整个 `run_turn`"。要做到"每 K 次工具调用检查一次方向是否跑偏，
跑偏就提前结束本步"，需要新增一种"中途主动结束当前 `run_turn`"的机制，这在
现有架构里是缺失的，必须先补上，而不能只在 `goal_mode/` 内部实现。

### 4.3 设计方向 A（推荐）：基于回调的提前退出信号

给 `Agent.run_turn()` 增加一个可选参数（不影响现有调用方，默认 `None`）：

```python
def run_turn(self, user_message: str, step_checkpoint: Optional[Callable[[dict], bool]] = None) -> str:
    ...
```

`step_checkpoint` 在 `_agentic_loop` 每完成一次工具调用批次后被调用一次，
传入一个轻量上下文（`{"tool_calls_since_start": int, "loop_count": int,
"last_tool_names": [...]}`），返回 `True` 表示"继续跑"，返回 `False` 表示
"请求提前结束本次 `run_turn`"（`_agentic_loop` 检测到 `False` 后跳出循环，
返回目前已有的 `final_text` 或明确标记"提前中断"的占位文本）。

`FineGrainedStepExecutor` 实现 `GoalStepExecutor.execute()` 时传入一个
`step_checkpoint`，内部逻辑：

```python
class FineGrainedStepExecutor(GoalStepExecutor):
    def __init__(self, check_every_n_tool_calls: int = 5):
        self._check_every_n = check_every_n_tool_calls

    def execute(self, agent, prompt) -> GoalStepResult:
        tool_calls_at_last_check = 0

        def _checkpoint(ctx: dict) -> bool:
            nonlocal tool_calls_at_last_check
            n = ctx["tool_calls_since_start"]
            if n - tool_calls_at_last_check < self._check_every_n:
                return True  # 还没到检查节点，继续跑
            tool_calls_at_last_check = n
            return self._quick_direction_check(agent, ctx)  # 见 4.4

        turns_before = agent.stats.turns
        tool_calls_before = agent.stats.tool_calls
        output = agent.run_turn(prompt, step_checkpoint=_checkpoint)
        ...
        return GoalStepResult(output=output, ...)
```

**为什么选这个方向而不是"改造 `PreToolUse` hook 语义"**：`PreToolUse` 是
全局 hook 机制，服务于所有场景（权限拦截、输入改写等），语义已经固定为
"针对单次工具调用的阻止/放行"，不应该为了 Goal 模式一个场景改变它的通用
语义；`step_checkpoint` 是 `run_turn` 级别的可选回调，默认 `None` 不影响
任何现有调用方，风险面更小、职责更清晰。

### 4.4 "轻量方向判断"具体做什么

不是每次都跑一次完整的 GoalJudge（那样和粗粒度版本"评审成本"没有本质区别，
只是评审更频繁），而应该是更便宜的二分类判断，例如：

- 一次简短的 LLM 调用（用便宜的模型），只问"最近几次工具调用的结果和错误
  信息，是否明显偏离了目标 {goal_text}？只回答 是/否 + 一句话理由"
- 或者更便宜的规则判断：连续 K 次工具调用都是失败结果（`error is not
  None`）且没有任何文件被改动（可以用 `git diff --stat` 判断），这种情况
  大概率是方向错了

**建议第一版只做规则判断，不引入额外的 LLM 调用**——理由：这是"中断"这个
高风险操作的触发条件，规则判断至少是确定性的、可解释的，而且不会因为一次
判断失误的 LLM 调用而错误地打断一个正在推进的合理任务。等有了运行数据、
确认规则判断的误报率可以接受后，再考虑升级为 LLM 判断。

### 4.5 中断阈值必须保守

- `check_every_n_tool_calls` 默认建议不低于 5（避免过于敏感地打断刚开始
  尝试的任务）
- 触发中断后，`GoalStepResult` 需要一个新字段标记"提前中断"（而不是和
  `hit_max_turns` 复用同一语义——两者原因不同：`hit_max_turns` 是预算耗尽，
  这里是主动判断跑偏），`GoalRunner.run()` 对这种情况的处理应该是"直接进入
  正常的 GoalJudge 评审流程"（用已有的部分产出去评审，让 GoalJudge 用
  `progress`/`checklist` 机制客观判断这一步是否真的没有价值），而不是自己
  下结论"这一步作废"

### 4.6 配置

```json
{
  "goal_mode": {
    "fine_grained_execution_enabled": false,
    "fine_grained_check_every_n_tool_calls": 5
  }
}
```

（`fine_grained_execution_enabled` 已占位，`fine_grained_check_every_n_tool_calls`
是本项新增，需要在实现时补充到 `GoalModeConfig`。）

### 4.7 依赖与验证方式

- 依赖 `Agent.run_turn()` / `_agentic_loop()` 的改造（新增 `step_checkpoint`
  可选参数），这是本项工作量的主体部分，涉及 `agent/turn_loop.py`
- 建议先做一个不依赖真实 LLM 的集成测试：用 `FakeAgent`（参考
  `tests/test_goal_mode.py` 里现有的写法）模拟"连续 N 次工具调用失败且无
  文件变化"的场景，验证 `FineGrainedStepExecutor` 确实会提前结束，且
  `GoalRunner` 能正确处理提前中断的 `GoalStepResult`
- 默认关闭上线，观察一段时间的误中断率之后再考虑默认开启

---

## 五、建议的启动顺序

```
先观察改造项一/二/三/五的真实运行数据（第二节）
  │
  ├─→ 数据显示"卡住恢复触发频繁 + 恢复后仍常失败"
  │     → 优先做改造项四（第三节），收益更直接
  │
  └─→ 数据显示"方向跑偏经常发生在预算耗尽前很久才被发现"
        → 优先做改造项六（第四节），但注意它需要先改动
          agent/turn_loop.py 这个更底层、影响面更广的模块，
          建议单独拆分成"run_turn 增加 step_checkpoint 参数"
          （风险小、可独立测试、不影响任何现有调用方）和
          "FineGrainedStepExecutor 具体实现"两个可以分别评审、
          分别合并的改动
```

两项都不建议同批实现——即使数据显示两者都有必要，也应该先落地一项、观察
运行数据（比如改造项四的实际并发成本、择优效果；改造项六的误中断率）之后，
再决定第二项的具体参数和是否需要调整设计。
