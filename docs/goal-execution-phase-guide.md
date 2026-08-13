# Goal 执行阶段（Execution Phase）指南

多轮执行的 Goal（尤其是 recurring Goal）往往经历不同阶段：先探索出合理的
执行方式，再收敛到一种方案，最后稳定重复执行；期间也可能需要周期性整理
产出目录。本功能让你可以手动或自动控制一个 Goal 当前处于哪个阶段，Agent
会据此调整每一轮的行为基调。

设计背景见 `next_doc/goal_execution_phase_improvement_plan.md`。

## 五种阶段

- **explore（探索）**：鼓励尝试不同实现路径、目录结构、数据存储方式，允许
  推翻上一轮的做法。
- **converge（收敛）**：要求对比已探索过的方式，选定一种方案并说明理由，
  不再引入全新方案。
- **stable（稳定）**：严格遵循已确定的方式重复执行，只做增量，不做结构性
  变更。
- **tidy（整理）**：不产出新内容，只评估并整理现有产出目录，输出整理报告，
  完成后自动回到 stable。
- **auto（自动，默认）**：系统按规则信号自动在 explore/converge/stable 间
  判定（不会自动进入 tidy，tidy 需手动触发）。

## 命令

```
/agent goals phase show <goal_id>
/agent goals phase set <goal_id> explore|converge|stable|tidy|auto [--lock]
/agent goals phase unlock <goal_id>
```

- `phase set` 指定一个非 `auto` 的阶段时，默认会**隐式锁定**（`locked=True`），
  避免下一轮自动判定立刻把你刚设置的阶段覆盖掉；如果想让某个非 auto 阶段
  仍然可以被自动判定覆盖，显式加 `--lock=false`（即不传 `--lock`，只有传
  `--lock` 才会锁定为 true；已锁定时用 `phase unlock` 解锁）。
- `phase set <goal_id> auto` 会解除锁定，交回自动判定。
- `phase show` 会显示当前阶段、是否锁定、`stability_score`（0~1，越接近 1
  越接近"可以稳定执行"，仅供参考）以及最近几次阶段切换记录。

## 自动判定规则（auto 模式）

系统使用简单的规则信号判断当前该处于哪个阶段，不涉及额外的 LLM 调用：

1. Goal 处于前几轮（默认前 3 轮）时，判定为 `explore`。
2. 如果这个 Goal 还没有确认 `GoalExecutionSpec`（见
   `docs/goal-execution-spec-guide.md`）、或者 spec 最近刚被确认/修订、或
   者 `GoalExecutionSpec` 的"轻量核对"连续多轮未匹配（`miss_streak >= 2`），
   判定为 `explore`（说明执行方式还不稳定）。
3. 如果 spec 已确认、近期未变更、轻量核对连续命中，判定为 `stable`。
4. 其余情况判定为过渡态 `converge`。

自动判定只影响"本轮拼给 agent 的 prompt 片段"，不会自动帮你确认或修改
`GoalExecutionSpec` 本身。

## 与 GoalExecutionSpec 的关系

两者是独立但互相配合的机制：

- `GoalExecutionSpec` 回答"每一轮该产出什么、用什么标准核对"。
- Execution Phase 回答"现在该用什么心态去执行"（试错 vs 收敛 vs 严格遵循）。

`explore`/`converge` 阶段不会关闭 `GoalExecutionSpec` 的轻量核对提示，但
建议在探索期先不急于 `spec confirm`，等收敛出相对稳定的方式后再确认，
这样自动判定也会更快从 explore 过渡到 stable。

## 生效范围

当前版本（Stage A）只对通过 `goal_cron_bridge`（即 recurring Goal /
`run_mode=goal_cycle` 的 cron job）触发的轮次生效；未绑定 Goal 的独立 cron
job（`cron_job_executor.py` 直接执行）暂不接入，留待后续版本扩展。看板
（kanban）可视化（阶段徽章、`stability_score` 展示、一键切换）也留待后续
版本，当前请使用 `/agent goals phase` 命令行操作。

## 数据存储

每个 Goal 的阶段状态独立存储在 `.agent/goal_execution_phase/<goal_id>.json`，
不会修改 `goals.json` 中的 `GoalNode` 结构。没有该文件时视为默认状态
（`mode="auto"`, `locked=False`），行为与未使用本功能之前完全一致。
