# Goal 执行阶段（Execution Phase）指南

多轮执行的 Goal（尤其是 recurring Goal）往往经历不同阶段：先探索出合理的
执行方式，再收敛到一种方案，最后稳定重复执行；期间也可能需要周期性整理
产出目录。本功能让你可以手动或自动控制一个 Goal 当前处于哪个阶段，Agent
会据此调整每一轮的行为基调。

设计背景见 `next_doc/goal_execution_phase_improvement_plan.md`；recurring
Goal 的产出目录模型见 [产出目录规范](goal-output-directory-guide.md)。

## 五种阶段

- **explore（探索）**：鼓励尝试不同实现路径、目录结构、数据存储方式，允许
  推翻上一轮的做法。
- **converge（收敛）**：要求对比已探索过的方式，选定一种方案并说明理由，
  不再引入全新方案。
- **running（运行，[Stage 8b] 原名 `stable`）**：严格遵循已确定的方式重复
  执行，只做增量，不做结构性变更。旧数据/旧脚本里的 `"stable"` 仍会被
  自动识别并归一化为 `"running"`，不需要手动迁移历史文件；命令行/看板
  展示均已统一改用新名。
- **tidy（整理）**：不产出新内容，只评估并整理现有产出目录，输出整理报告，
  完成后自动回到 running。
- **auto（自动，默认）**：系统按规则信号自动在 explore/converge/running 间
  判定（不会自动进入 tidy，tidy 需手动触发）。

## 命令

```
/agent goals phase show <goal_id>
/agent goals phase set <goal_id> explore|converge|running|tidy|auto [--lock]
/agent goals phase unlock <goal_id>
```

- `phase set` 接受 `stable` 作为 `running` 的别名（兼容旧脚本），内部会
  自动归一化，不会报错。
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
3. 如果 spec 已确认、近期未变更、轻量核对连续命中，判定为 `running`。
4. 其余情况判定为过渡态 `converge`。
5. **[Stage D] 进展趋势信号**：即使满足上面第 3 条的 `running` 条件，系统
   还会额外检查这个 Goal 最近 3 轮已完成周期的 `progress_notes`（每轮完成
   后留下的进展摘要）——默认用文字相似度（≥0.85）判断是否高度雷同；如果
   开启了 LLM 判断（见下方"进展趋势信号：相似度 vs LLM"一节），改用 LLM
   结合语义判断。命中"疑似伪进展"时，会把判定从 `running` 降级为
   `converge`，倒逼 agent 在收敛阶段交代清楚"这一轮到底推进了什么"，而
   不是被静默判定为已经稳定。历史轮次不足 3 轮、或某一轮进展摘要为空时，
   这条信号不参与判定（不影响原有规则）。**[Stage 8c]** 如果这个 Goal 的
   `GoalExecutionSpec.new_topic_discovery` 被显式声明为 `"intrinsic"`
   （见下方"产出模式与规范层收敛"一节），本条信号直接不参与判定——内容层
   天然常新，"跨轮进展文本雷同"对这类 Goal 不构成有效信号。
6. **[Stage 8c] 规范层收敛信号（`routine_stability`）**：如果第 4 条把
   Goal 判定为 `converge`，但原因只是"`GoalExecutionSpec` 轻量核对刚好有
   一次未命中"（而不是被第 5 条从 `running` 降级下来），系统会额外检查
   `GoalExecutionSpec.execution_routine`（"每轮该走的标准动作序列"，见
   `docs/goal-execution-spec-guide.md`）最近几个历史版本之间的文本相似度
   是否足够高——如果标准动作本身已经反复稳定，个别一次软核查未命中不足以
   把这一轮打回收敛状态，会把判定从 `converge` **提升**回 `running`。这个
   信号只影响"轻量核对偶发未命中"这一种 `converge`，不会拉动 `explore`
   前进，也不会覆盖第 5 条刚做出的降级（避免两个信号互相拉扯出抖动）。

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

## 产出模式与规范层收敛（`output_mode` 等 Stage 8 新字段）

`GoalExecutionSpec` 除了负责"每轮产出什么/怎么核对"这套内容层字段，还有
一组**规范层**字段，用来刻画"这个 Goal 反复执行时，标准动作序列本身是
什么样子、收敛之后该怎么判断"，详见
[执行规范指南 §2](goal-execution-spec-guide.md#2-数据结构)：

| 字段 | 作用 |
| --- | --- |
| `output_mode` | `converging`（默认）/`accretive`（内容持续累积）/`capability_hardening`（能力固化）/`hybrid`（混合） |
| `execution_routine` | 收敛后"每一轮该走的标准动作序列"，本文档上面第 6 条信号据此判断规范层是否已经稳定 |
| `new_topic_discovery` | `"intrinsic"` 时关闭上面第 5 条"进展趋势信号"，避免内容天然常新的 Goal 被误判成"原地打转" |
| `hardening_target` | `capability_hardening` 型 Goal 的外部固化目标路径，见下方"converge 阶段与执行规范的联动" |
| `sub_exploration` | 声明一条独立生命周期的子探索，不参与主轨的阶段判定 |
| `cadence` | 纯文本节奏说明，不参与任何判定，仅展示 |

这些字段均可选、缺省即为 Stage 8 上线前的行为（`output_mode` 默认
`"converging"`，其余默认空），不影响任何未使用这套新模型的存量 Goal；
`GoalExecutionSpecBuilder` 生成草稿时会尝试主动判断并填写，也可以在
`spec revise` 时用自然语言反馈让它调整。

## 与 GoalExecutionSpec 的关系

两者是独立但互相配合的机制：

- `GoalExecutionSpec` 回答"每一轮该产出什么、用什么标准核对"。
- Execution Phase 回答"现在该用什么心态去执行"（试错 vs 收敛 vs 严格遵循）。

`explore`/`converge` 阶段不会关闭 `GoalExecutionSpec` 的轻量核对提示，但
建议在探索期先不急于 `spec confirm`，等收敛出相对稳定的方式后再确认，
这样自动判定也会更快从 explore 过渡到 running。

## tidy 阶段的行为细节

- 手动/自动进入 `tidy` 后，只会维持**一轮**：这一轮结束、下一次触发时会
  自动回到 `running` 并解除锁定，不需要手动切回。
- recurring Goal 的 tidy 阶段不要求 agent 自己从零判断"哪里乱了"：系统
  会先扫描 `output/` 实际内容，算出一份确定性问题清单（散落文件、
  `_misc/` 未清空、疑似临时脚本、`_run_logs/` 超量、`requirements.txt`
  疑似遗漏依赖、`_experiments/` 应转正未转正的脚本等），拼进本轮 prompt，
  agent 只需要决定"怎么处理"。完整目录模型与核查项见
  [产出目录规范](goal-output-directory-guide.md)。**[Stage 8f]** 这份
  问题清单会按 `GoalExecutionSpec.output_mode` 做差异化调整：
  `capability_hardening` 型 Goal 的"实验脚本应转正"提示阈值更低（提及
  一次即提示，其余模式需要提及两次）；`accretive` 型 Goal 额外检查
  `output/` 顶层是否存在疑似未去重的重复累积文件（如 `report.md` 和
  `report_v2.md` 并存）；`converging`（默认）行为与引入 `output_mode`
  之前完全一致。
- 如果这个 Goal 已经确认了执行规范，tidy 阶段还会额外附带一份基于
  `GoalExecutionSpec`（deliverables/sub_directories）的核对清单，与上面
  "代码扫描问题清单"是互补关系：一个纯粹扫描文件系统，一个需要结合规范
  内容判断，两者共同提示 agent 依据既定规范整理，而不是凭空判断"哪些算
  冗余"。
- `auto` 模式默认不会周期性插入 tidy（需要显式配置 `tidy_every_n_cycles`
  才会生效，当前版本尚未暴露为用户可配置项，留待后续版本）。

## converge 阶段与执行规范的联动

如果一个 Goal 处于 `converge` 阶段、但还没有确认执行规范
（`GoalExecutionSpec`），系统会额外提示 agent："如果本轮已经能给出结论，
建议把产出规则、目录结构、验收标准写清楚"，方便你后续用
`/agent goals spec generate` 生成对应草稿并确认。**[Stage 8d]** 如果这个
Goal 已经声明了 `hardening_target`（能力固化型 Goal 的外部固化目标路径），
converge 阶段收尾时会额外提示"搬迁的最终落点是这里，而不是（或不仅是）
本 Goal 私有的 output/scripts/"；如果声明了 `sub_exploration`，会提示这条
子探索独立生命周期、不参与本 Goal 的阶段判定。

此外，如果这个 Goal 此前**完全没有**生成过任何 spec（草稿或已确认），
系统会检查最近两轮的"方案对比说明"结论是否高度一致（复用上面"进展趋势
信号"同一套 difflib/LLM 判断基础设施）——一致的话会**自动生成一份未确认
的 spec 草稿**并落盘 + 推送通知，降低"卡在 converge 没人管、忘记手动生成
规范"的概率；**不会自动确认**，仍需要你用 `/agent goals spec confirm`
或看板对应操作确认。一旦生成过草稿（不管是否确认），后续轮次不会再重复
自动生成，不会覆盖你可能正在手动编辑的内容。



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
| running | 1.0 | 已跑顺，维持基线 |
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

- 折叠标题显示当前阶段徽章（🔍探索/⚖️收敛/✅长期执行/🧹整理/🤖自动，与看板
  `_PHASE_LABELS` 展示文案一致）和是否锁定（🔒）。
- 展开后可看到 `stability_score`、已在当前阶段的轮数、最近几次切换记录。
- 下拉框选择目标阶段 + "应用"按钮完成切换（等价于 CLI `phase set`）。
- 已锁定时额外出现"解除锁定"按钮（等价于 CLI `phase unlock`）。

## 数据存储

每个 Goal 的阶段状态独立存储在 `.agent/goal_execution_phase/<goal_id>.json`，
不会修改 `goals.json` 中的 `GoalNode` 结构。没有该文件时视为默认状态
（`mode="auto"`, `locked=False`），行为与未使用本功能之前完全一致。

## 产出目录模型

recurring Goal 的产出目录已从"每轮一个 `cycle_NNNN/` 目录"迁移为"四个
固定目录（`output/`/`notes/`/`spec/`/`scratch/`）跨轮共用"的新模型（不再
是"进行中"，`goal_cron_bridge.py` 的实际触发流程已经切换到新模型），设计
动机是旧模型下 explore 阶段允许换目录结构、tidy 阶段又缺乏实质核查手段，
导致产出目录实际很难真正收敛。完整规范见
[产出目录规范](goal-output-directory-guide.md)，设计过程见
`next_doc/goal_output_directory_and_execution_phase_redesign_plan.md`。

一次性 Goal 和独立 cron job 不受影响，继续使用
[绑定指南 §10](goal-cron-binding-guide.md#10-产出目录规范周期性goalcronjob--一次性-goal)
描述的旧模型。已存在的历史 `cycle_NNNN/` 目录保留原样，不做自动迁移。
