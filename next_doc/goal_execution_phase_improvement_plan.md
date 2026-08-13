# Goal/Cron 执行阶段（Execution Phase）改进方案

## 0. 背景与动机

现状：`GoalExecutionSpec`（见 `goal_execution_spec_generation_plan.md`）解决了"每轮
该产出什么、用什么标准核对"，但它是**静态**的——一旦确认（`confirmed=True`），
所有后续轮次都用同一套标准检查，不区分"这个 Goal 目前是刚起步还是已经跑
熟了"。

现实中，需要多轮执行的 Goal/cron 任务往往经历不同阶段：

```
探索期（尝试不同实现路径） → 收敛期（选定方案） → 稳定期（重复执行、只做增量）
                                                    ↕
                                          整理期（周期性维护，不产出新内容）
```

不同阶段应该有不同的 agent 行为基调、不同的评判标准、不同的自由度。本方案
在 `GoalExecutionSpec` 之上新增一层**执行阶段（execution phase）**，独立于
"执行规范"回答"现在该怎么干"这个问题。

## 1. 阶段定义

| 阶段 | 目的 | agent 行为基调 | 典型触发 |
|---|---|---|---|
| `explore` 探索 | 尝试不同实现路径，允许试错 | 鼓励对比多方案、允许新建目录/新脚本、不必复用上一轮产物 | Goal 新建、或用户主动要求重新探索 |
| `converge` 收敛 | 从已有候选中选定一种 | 要求对比探索期产物、选定方案并说明理由，不再引入全新方案 | 探索轮次达阈值 / 产物出现重复模式信号 |
| `stable` 稳定 | 按已定方式重复执行 | 严格遵循 GoalExecutionSpec，不做结构性变更，重点是产出质量一致性 | 收敛完成 / 用户手动锁定 |
| `tidy` 整理 | 评估现状 + 清理产出目录，不产出新内容 | 只读审查 + 归档/合并/删除冗余，输出整理报告 | 周期性触发 / 用户手动要求 |
| `auto` 自动 | 系统按规则信号自动在以上阶段间切换 | 视解析结果而定 | 默认模式 |

`explore → converge → stable` 是主线；`tidy` 是可插入稳定期的维护动作，
不算主线的一环。用户可随时手动指定并"锁定"某阶段，锁定后自动判定不生效，
直到显式解锁或切回 `auto`。

## 2. 自动判定信号（规则版，第一版不引入额外 LLM 判断）

1. **轮次窗口**：默认前 `explore_min_cycles`（默认 3）轮为 explore；超过后
   若无强"仍在剧烈变化"信号则进入 converge。
2. **GoalExecutionSpec 变更频率**：spec 连续 `spec_stable_cycles`（默认 2）轮
   未被 revise 且轻量核对（§5.1 miss-streak）未命中未匹配 → 收敛/稳定信号。
3. **用户干预**：用户在某轮给出较大修改意见（`skip_next_cycle`/feedback 中
   出现结构性调整）视为"打回 explore"的信号（第一版先只做手动打断，不做
   NLP 识别）。
4. **tidy 周期**：进入 stable 后，每 `tidy_every_n_cycles`（默认 0=关闭）轮
   自动插入一次 tidy（执行完当轮后自动切回 stable，不需要用户干预）。

判定结果落在 `stability_score`（0~1，规则打分，用于看板展示"离稳定还有多
远"），不追求精确，只作为参考信号。

"执行方式是否合理"不在系统内自动评判——converge 阶段强制 agent 产出一份
"方案对比说明"，由 goal_judge / 用户确认，避免第一版做过度主观的自动化
判断。

## 3. 数据模型与存储

新增 `.agent/goal_execution_phase/<goal_id>.json`（独立文件，与
`GoalExecutionSpec` 同样的隔离存储方式，不改 `goals.json` 的 `GoalNode`
结构）：

```json
{
  "version": 1,
  "goal_id": "...",
  "mode": "explore",        // explore | converge | stable | tidy | auto
  "locked": false,
  "stability_score": 0.0,
  "cycles_in_mode": 0,
  "last_tidy_cycle": null,
  "mode_history": [
    {"at": 1234567.0, "from": "explore", "to": "converge", "reason": "..."}
  ],
  "updated_at": 1234567.0
}
```

## 4. 与 GoalExecutionSpec / goal_cron_bridge 的接入

在 `evolution/goal_cron_bridge.py::_fire_goal_cycle` 组装本轮子 Objective
description 的链路中，新增 `_append_execution_phase_context`：

- 读取/推进 `ExecutionPhaseState`（`auto` 模式下按 §2 规则重新计算一次
  effective mode，并在需要时记录一条 `mode_history`）。
- 根据 effective mode 从 `prompts/fragments/execution_phase.md` 中选取对应
  文本片段拼进 description。
- 与 GoalExecutionSpec 联动：
  - `explore`：允许 spec 被频繁 revise，不做轻量核对惩罚。
  - `converge`：额外要求输出"方案对比说明"，作为收敛完成的标志之一。
  - `stable`：spec 视为冻结，轻量核对逻辑保持现状不变（向后兼容）。
  - `tidy`：跳过 spec 产出检查，走独立的整理 checklist 提示。

任何环节异常静默跳过（与现有 `_append_*` 系列函数一致的防御性风格），
不影响 Goal 触发主流程；`ExecutionPhaseState` 不存在时视为默认
`mode="auto"`、`locked=False`，行为与引入本机制之前完全一致（不确认/不
配置就不改变现有行为）。

cron 侧：`cron_job_executor.py` 的"独立 cron"（未绑定 Goal）执行链路本轮
不接入（超出 Goal 语境，阶段概念意义不大），仅 `goal_cycle` 类型的 cron
（通过 `goal_cron_bridge` 触发）接入。

## 5. 用户手动控制

CLI（`cli/commands/goals.py`，追加到 `/agent goals` 下）：

```
/agent goals phase show <goal_id>
/agent goals phase set <goal_id> explore|converge|stable|tidy|auto [--lock]
/agent goals phase unlock <goal_id>
```

看板（Streamlit kanban）：Stage A 暂不做可视化改造（留待后续 Stage 做
`stability_score` 展示 + 徽章 + 一键切换），先通过 CLI 验证机制有效性。

## 6. 分阶段落地

- **Stage A（已实现）**：数据模型（`perception/execution_phase.py`）+
  CLI 手动切换（explore/converge/stable/tidy/auto + lock/unlock）+
  `goal_cron_bridge.py` 接入（prompt 片段注入 + 简单规则自动判定）+
  单元测试 + 文档。
- **Stage B（已实现）**：
  - tidy 阶段执行一轮后自动回到 stable 并解锁（不需要用户手动切回）。
  - `auto` 模式下支持按 `tidy_every_n_cycles` 周期性自动插入 tidy（默认
    关闭，`resolve_effective_mode(..., tidy_every_n_cycles=N)`）。
  - converge 阶段若执行规范尚未确认，追加提示引导用户后续
    `spec generate`/`spec confirm`，把 converge 与 GoalExecutionSpec 确认
    流程串起来（不自动调用，仅提示）。
  - tidy 阶段若已有确认的执行规范，自动生成"整理核对清单"（基于 spec 的
    deliverables/sub_directories），而不是让 agent 凭空判断哪些是冗余。
- **Stage C（已实现）**：看板可视化——REST 端点
  `GET/POST /v1/goals/{goal_id}/execution_phase`、
  `POST /v1/goals/{goal_id}/execution_phase/unlock`（`api/routes.py`）+
  Streamlit 看板 Goal 卡片上的阶段徽章折叠区（`apps/mini_agent_kanban/
  app.py::_render_goal_execution_phase_widget`，展示 mode/locked/
  stability_score/mode_history，并提供切换下拉框 + 解锁按钮）+
  `AgentClient` 对应方法 + 路由层单元测试。
- **Stage D（已实现，范围有调整）**：
  - 更细的自动判定信号：新增跨轮"进展趋势"信号
    `execution_phase.compute_progress_trend_signal()`——比较这个 Goal 最近
    3 轮已完成周期（`GoalNode.reaped_cycle_child_ids` 末尾）的
    `progress_notes` 文本相似度（复用 `evolution/guardian.py` 同款
    difflib 思路），高度雷同时判定为"伪进展"，把 `resolve_effective_mode`
    原本会给出的 `stable` 降级为 `converge`（只在"本来会判 stable"时才
    降级，不影响 explore/converge 本身的判定）；历史不足 3 轮、或某轮
    进展摘要缺失时该信号不参与判定（返回 `None`，等价于关闭）。
    `goal_cron_bridge._append_execution_phase_context` 新增可选
    `goal_backlog` 参数用于取数，未传入时行为与 Stage D 之前完全一致。
  - 独立 cron（非 goal_cycle）场景的阶段支持：**评估后决定不实现**——阶段
    概念依赖"同一 Goal 的连续多轮"这个语境，独立 cron job 没有 Goal 归属，
    套用意义不大，已在 `docs/goal-execution-phase-guide.md` 明确写为设计
    决定而非遗留缺口。
  - 原方案提到的"接入 goal_judge 的 progress 趋势"：`goal_judge` 是单个
    Goal 执行内部（`goal_mode/runner.py`）逐轮判定 DONE/CONTINUE 的模块，
    不覆盖 `goal_cron_bridge` 的跨周期 recurring 场景，因此改为直接从
    `GoalNode.progress_notes` 历史构造信号（效果等价，链路更短，不新增
    对 `goal_judge` 内部状态的跨模块依赖）。

## 7. 兼容性与风险

- 全部新增文件、新增字段，不修改现有 `GoalNode`/`GoalExecutionSpec` 结构，
  不引入新的必填配置项；未主动使用的 Goal 行为不变。
- 自动判定规则是启发式的，可能与用户预期不符——因此 Stage A 优先保证
  "手动控制路径"完整可用，`auto` 模式的规则质量可以后续迭代而不影响
  已有的手动工作流。
