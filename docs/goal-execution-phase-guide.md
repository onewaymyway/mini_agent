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
5. **[Stage D] 进展趋势信号**：即使满足上面第 3 条的 `stable` 条件，系统
   还会额外检查这个 Goal 最近 3 轮已完成周期的 `progress_notes`（每轮完成
   后留下的进展摘要）——默认用文字相似度（≥0.85）判断是否高度雷同；如果
   开启了 LLM 判断（见下方"进展趋势信号：相似度 vs LLM"一节），改用 LLM
   结合语义判断。命中"疑似伪进展"时，会把判定从 `stable` 降级为
   `converge`，倒逼 agent 在收敛阶段交代清楚"这一轮到底推进了什么"，而
   不是被静默判定为已经稳定。历史轮次不足 3 轮、或某一轮进展摘要为空时，
   这条信号不参与判定（不影响原有规则）。

自动判定只影响"本轮拼给 agent 的 prompt 片段"，不会自动帮你确认或修改
`GoalExecutionSpec` 本身；进展趋势信号也只是辅助信号，不代表真实产出质量
的最终结论。

## 进展趋势信号：相似度 vs LLM（可选）

[goal_stuck_stats_and_llm_progress_judge_plan.md §2] 默认（`AppConfig.
execution_phase.progress_trend_llm_enabled=False`）用纯文本相似度
（difflib）判断最近几轮 `progress_notes` 是否雷同。这个判断有一个明显的
盲区：**分不清"内容确实雷同但属于正常重复"（比如周期性巡检类 Goal，每轮
产出格式相同）和"真的在原地打转"**。

> 修复记录：`progress_trend_llm_enabled` 这个开关从引入起有一段时间只是
> `models.py` 里的 dataclass 字段，`config/loader.py` 从未提取/加载它——
> `agent_config.json` 里配置 `execution_phase.progress_trend_llm_enabled`
> 完全不起作用，实际永远是默认值 `False`。现已修复：注册进
> `param_registry.NESTED_CONFIG_BLOCKS` 并接入 `loader.py`，配置文件里的
> 值会被正常读取（同时补了 `tests/test_config_nested_blocks_wiring.py`
> 防止同类问题在其他子配置块上再次出现）。

打开 `progress_trend_llm_enabled` 后，改为把最近几轮的进展摘要交给 LLM，
让它结合语义判断这几轮到底是"原地打转"还是"有实质推进（哪怕措辞相似）"
或"雷同但属于任务本身该有的正常重复"。LLM 判断不出结果、响应异常、或
配置未开启时，静默退回原有的 difflib 判断，不会因为这层增强导致 Goal
触发主流程报错。

这是一个可选增强，默认关闭；开启后会给对应的 recurring Goal 每轮多引入
一次轻量 LLM 调用（只在这个 Goal 已积累够 3 轮历史时才会触发，不是每轮
都调用）。

## 与 GoalExecutionSpec 的关系

两者是独立但互相配合的机制：

- `GoalExecutionSpec` 回答"每一轮该产出什么、用什么标准核对"。
- Execution Phase 回答"现在该用什么心态去执行"（试错 vs 收敛 vs 严格遵循）。

`explore`/`converge` 阶段不会关闭 `GoalExecutionSpec` 的轻量核对提示，但
建议在探索期先不急于 `spec confirm`，等收敛出相对稳定的方式后再确认，
这样自动判定也会更快从 explore 过渡到 stable。

## tidy 阶段的行为细节

- 手动/自动进入 `tidy` 后，只会维持**一轮**：这一轮结束、下一次触发时会
  自动回到 `stable` 并解除锁定，不需要手动切回。
- 如果这个 Goal 已经确认了执行规范，tidy 阶段会自动附带一份"整理核对
  清单"（基于执行规范里声明的 deliverables/sub_directories），帮助 agent
  依据既定规范而不是凭空判断"哪些算冗余"。
- `auto` 模式默认不会周期性插入 tidy（需要显式配置 `tidy_every_n_cycles`
  才会生效，当前版本尚未暴露为用户可配置项，留待后续版本）。

## converge 阶段与执行规范的联动

如果一个 Goal 处于 `converge` 阶段、但还没有确认执行规范
（`GoalExecutionSpec`），系统会额外提示 agent："如果本轮已经能给出结论，
建议把产出规则、目录结构、验收标准写清楚"，方便你后续用
`/agent goals spec generate` 生成对应草稿并确认。这只是提示，不会自动帮你
生成或确认规范。



## 生效范围

当前版本（Stage A/B/C/D）只对通过 `goal_cron_bridge`（即 recurring Goal /
`run_mode=goal_cycle` 的 cron job）触发的轮次生效；未绑定 Goal 的独立 cron
job（`cron_job_executor.py` 直接执行）**明确不接入**——阶段概念（探索/
收敛/稳定）依赖"是否属于同一个 Goal 的连续多轮"这个语境，独立 cron job
没有 Goal 归属，套用这套机制意义不大，这是已评估后的决定，非遗留缺口。

## 调度联动：阶段感知的资源估算（只读预览）

除了调节 agent 行为、驱动归档门禁，阶段状态还会反映到调度层的只读
预览里。`GET /v1/self/unified_scheduler_preview`（见
[Cron 独立执行链路指南](cron-dedicated-execution-guide.md#8-rest-api)）
的 `goal` 通道中，每个任务的 `resource_estimate` 不再恒为 `1.0`，而是
按该 Goal 当前的"最近一次已知有效阶段"换算出的相对倍率：

| 阶段 | 倍率 | 直觉 |
|---|---|---|
| explore | 1.3 | 还在摸索，给更宽松的资源预算 |
| converge | 1.15 | 接近收敛，适度宽松 |
| stable | 1.0 | 已跑顺，维持基线 |
| tidy | 0.85 | 收尾整理，收紧成本 |

响应里同时带一个 `extra.phase_mode` 字段标出具体阶段名，方便直接展示
不用再反查一次。这套倍率是启发式初始值，改默认值只需要调整
`execution_phase.DEFAULT_PHASE_RESOURCE_MULTIPLIERS` 常量，不涉及调用方
代码改动。

**这仍然只是诊断展示，不改变任何实际执行行为**——`resource_estimate`
目前唯一的消费方就是这个只读预览端点本身，尚未接入
`allocate_weighted_slots()` 的真实槽位分配计算（那部分因为还没有真实
使用数据支撑"具体倍率该怎么消费"，仍记录在
`next_doc/goal_cron_task_optimization_holistic_plan.md` §5，留待后续
排期）。读不到阶段状态（Goal 从未触发过、`paths` 未注入、任何异常）时
保守回落到 `1.0`，与引入本机制之前的行为一致。

## 看板操作

打开 Streamlit 看板，Goal 卡片（非 Objective）上会展示一个可折叠的
"执行阶段"区域：

- 折叠标题显示当前阶段徽章（🔍探索/⚖️收敛/✅稳定/🧹整理/🤖自动）和是否锁定
  （🔒）。
- 展开后可看到 `stability_score`、已在当前阶段的轮数、最近几次切换记录。
- 下拉框选择目标阶段 + "应用"按钮完成切换（等价于 CLI `phase set`）。
- 已锁定时额外出现"解除锁定"按钮（等价于 CLI `phase unlock`）。

## 数据存储

每个 Goal 的阶段状态独立存储在 `.agent/goal_execution_phase/<goal_id>.json`，
不会修改 `goals.json` 中的 `GoalNode` 结构。没有该文件时视为默认状态
（`mode="auto"`, `locked=False`），行为与未使用本功能之前完全一致。

## 产出目录模型重构（进行中）

recurring Goal 的产出目录正在从"每轮一个 `cycle_NNNN/` 目录"迁移到"四个
固定目录（`output/`/`notes/`/`spec/`/`scratch/`）跨轮共用"的新模型，设计
动机是现有模型下 explore 阶段允许换目录结构、tidy 阶段又缺乏实质核查手段，
导致产出目录实际很难真正收敛。完整设计见
`next_doc/goal_output_directory_and_execution_phase_redesign_plan.md`。

当前进度：`evolution/output_workspace.py` 已提供新目录模型的路径分配、
骨架创建、结构扫描、README 自动生成、轮次笔记读写等基础函数（Stage 1），
但**尚未接入 `goal_cron_bridge.py` 的实际触发流程**——recurring Goal 触发
时目前仍使用原有的 `cycle_NNNN/` 分配逻辑，行为未变化。后续阶段完成、
正式接入生产触发流程后，本文档会同步更新阶段行为细节（尤其是 stable 阶段
将固定带上 `spec/SPEC.md` 全文、tidy 阶段的核对清单将改为代码驱动）。
