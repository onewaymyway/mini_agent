# 自我进化：效果回填指南（Outcome Tracking）

> 实现：`src/mini_agent/evolution/outcome_tracker.py`
> 接入点：`tools/evolution.py::skill_propose`（记录基线）、
> `evolution/consolidation.py::run_consolidation()`（周期性判定）、
> `cli/commands/evolution.py`（`/evolution outcomes` 命令、`/evolution revert` 联动）
> 对应设计方案：`next_doc/priority_improvements_implementation_plan.md` 方案三

## 1. 解决什么问题

现有自我进化验证链路（`evolution/validators.py` T0~T3、`evolution/eval_runner.py`
的 `mini-agent eval`）比较的都是**过程指标**：schema 校验、lint/类型检查、单测、
eval 场景对比（tool 失败率/turns/token）。这些指标回答的是"这次自我修改有没有
引入明显的技术性回归"，但都没有回答另一个更根本的问题：

> **这次修改是否真的解决了它声称要解决的问题？**

即触发这次 `skill_propose` 的那个 lesson group，在提案落地之后，是否真的不再
高频出现。效果回填机制补的就是这条正交的信号。

**与 T0~T3 验证流水线的关系**：T0~T3 是 merge **前**的门槛，继续保持不变；
效果回填是 commit 落地**后**的异步观察，两者互补，不冲突。

## 2. 工作流程

1. **记录基线**：`skill_propose` 成功产生 commit 时，若 `source_lessons` 参数
   带有 lesson group id（`perception/lesson_review.py::LessonGroup.key`），
   自动为每个 id 记录一条追踪记录，包含当前该 lesson group 的触发次数
   （`baseline_trigger_count`）和默认 14 天的观察期截止时间。
2. **周期性判定**：`evolution/consolidation.py::run_consolidation()` 每次运行时（手动
   `/evolve consolidate` 或 SessionEnd 时间门控触发），顺带调用
   `outcome_tracker.tick()`，检查所有已到观察期截止时间的记录：
   - 重新统计该 lesson group 当前的触发次数（`post_trigger_count`）
   - 与基线对比，产出判定（`verdict`）
3. **展示与建议**：`/evolution outcomes` 命令列出所有记录；判定为 `worsened`
   的记录会额外提示"建议复核是否要 revert"。

## 3. 判定规则

| verdict | 条件 |
|---|---|
| `improved` | 触发次数下降 ≥ 50%，或降为 0 |
| `worsened` | 触发次数上升 ≥ 20% |
| `no_change` | 变化幅度介于两者之间 |
| `insufficient_data` | 基线触发次数 < 3（样本太小，不参与 revert 建议） |
| `reverted_by_user` | 观察期内该 commit 已被 `/evolution revert` 撤销，提前结束观察 |

## 4. 命令

```bash
/evolution outcomes              # 列出所有效果回填记录
/evolution outcomes --worsened   # 只看判定为 worsened、建议复核的记录
```

输出示例：

```
 Commit     Lesson Group          Status      Baseline → Post   Verdict    Committed
 abc1234    忘记先-git-status     resolved    5 → 1             improved   2026-06-20
 def5678    文件路径拼接错误      resolved    3 → 6             worsened   2026-06-25

1 commit(s) judged 'worsened' after their observation window — consider reviewing:
/evolution show <commit> or /evolution revert <commit>
```

**重要**：`worsened` 判定只产生建议，**不会自动执行 revert**。是否 revert
的最终决策权始终留给用户——这与 `SoftGoalDeriver` 推导出的 Goal 需要用户
显式 `/goals accept`/`reject` 是同一套设计哲学：自动化到"提出建议"为止。

## 5. 数据存储

追踪记录持久化在 `<project_root>/.agent/outcome_tracking.json`，与 巩固循环
的 `consolidation_rhythm.json` 同级、同样的原子写入方式（写临时文件后 `os.replace`）。
不属于 git commit 元信息的一部分，纯粹是本地统计数据，可随时删除该文件重置
所有追踪记录（不影响已经落地的 skill/commit 本身）。

## 6. 边界情况

- **基线样本太小**：`baseline_trigger_count < 3` 时直接判定为 `insufficient_data`，
  不参与 revert 建议展示，避免小样本噪声误导用户。
- **lesson group 完全消失**（30 天无触发被 `lesson_review.py` 标记过时）：
  `post_trigger_count` 记为 0，按 `improved` 处理——触发次数降为 0 本身就是
  最强的正面信号。
- **查询失败**（`memory_backend` 不可用等）：该条记录保持 `observing` 状态，
  留待下次 `tick()` 重试，不会被误判。
- **失败静默降级**：`record_commit_baseline()` / `tick()` / `mark_reverted()`
  内部任何异常都不会阻断 `skill_propose`、巩固循环 主流程或 `/evolution revert`
  本身。

## 7. 相关文档

- `docs/self-evolution-stage2-guide.md` — T0~T3 安全网三件套（merge 前门槛）
- `docs/self-evolution-stage3-1-guide.md` — lesson → skill 闭环（`skill_propose` 触发路径）
- `docs/self-evolution-consolidation-guide.md` — 巩固循环 后台循环（`tick()` 的调用宿主）
- `docs/commands-and-tools-reference.md` — `/evolution outcomes` 命令参考
