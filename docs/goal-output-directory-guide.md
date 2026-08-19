# Goal 产出目录规范（output/notes/spec/scratch 四目录模型）

> 设计背景与实施记录：
> `next_doc/goal_output_directory_and_execution_phase_redesign_plan.md`
>
> 前置阅读：[Goal 与 Cron 绑定指南 · 10. 产出目录规范](goal-cron-binding-guide.md#10-产出目录规范周期性goalcronjob--一次性-goal)
> （那一节描述的是**通用**的 `cycle_NNNN/`/`run_NNNN/` 每次一目录模型，
> 本文档描述的是**仅 recurring Goal**（`run_mode=goal_cycle`）专用的、
> 覆盖了那套模型的新版本）
>
> 另见：[Goal 执行阶段指南](goal-execution-phase-guide.md)——本文档描述
> "产出物放在哪、遵循什么规则"，执行阶段指南描述"现在该用什么心态执行"，
> 两者配合驱动同一个触发流程。

## 1. 为什么要有这份规范

recurring Goal 跑多轮之后，产出目录容易变乱：旧模型是"每轮一个新目录
`cycle_0001/`、`cycle_0002/`……"，产出物和过程记录混在一起，且 explore
阶段天然鼓励"换目录结构"，几轮跑下来必然发散。新模型用四个**跨轮共用**
的固定目录取代它，让"探索 → 收敛 → 长期执行 → 整理"这四个执行阶段（见
[执行阶段指南](goal-execution-phase-guide.md)）真正对应到目录规则上，而
不是只停留在 prompt 文案里。

## 2. 目录总览

```
.agent/daemon_run_outputs/goals/<goal_id>/
    output/                    ← 唯一的、跨轮共用的正式产出目录
    notes/                     ← 每轮一份总结笔记，过程记录，非交付物
        cycle_0001.md
        cycle_0002.md
        archive/                太多时旧的挪进来
    spec/                      ← 执行规范，当前版本 + 历史版本
        SPEC.md
        SPEC.json
        history/
            v1_2026-07-01.md / .json
    scratch/                    ← explore/converge 期的试验田，允许乱、允许推翻
```

四者不重叠：`output/` 是"内容"，`notes/` 是"过程"，`spec/` 是"规则"，
`scratch/` 是"草稿"。**只对 recurring Goal 生效**——一次性 Goal 的多个子
Objective（`run_0001/`、`run_0002/`……）和独立 cron job
（`cron/<job_id>/run_<run_id>/`）继续使用旧模型不变，见
[绑定指南 §10](goal-cron-binding-guide.md#10-产出目录规范周期性goalcronjob--一次性-goal)。

> **`output/` 的位置可以被用户覆盖**：默认在
> `.agent/daemon_run_outputs/goals/<goal_id>/output/`，但如果这个 Goal
> 设置了 `user_output_dir`（比如 `research/stock_analyse`），`output/`
> 会解析到 `<project_root>/research/stock_analyse` 而不是默认位置；
> `notes/`/`spec/`/`scratch/` 三个不受影响，永远在默认位置。详见
> [§11 用户自定义输出路径怎么处理](#11-用户自定义输出路径怎么处理)。

## 3. `output/` 内部规范

固定骨架（系统级，所有 Goal 统一）：

```
output/
    README.md              ← 自动生成的目录索引，代码扫描生成，不是 agent 手写
    _misc/                  ← 未分类产出的临时收容所
    _archive/               ← 被淘汰但保留价值的历史版本，不直接删除
    scripts/                ← 脚本源代码专用（见 §5）
    <业务子目录...>          ← 由 GoalExecutionSpec 声明
```

根目录下只允许出现 `README.md`、三个系统保留目录（`_misc/`/`_archive/`/
`scripts/`）、以及执行规范里明确声明过的业务子目录。

- **`_misc/`**：agent 一时拿不准某个产出该归到哪个业务子目录时，允许先
  扔进这里，但**不允许跨轮留存**——tidy 阶段的第一优先级任务就是清空它。
- **`_archive/`**：converge 阶段淘汰的备选方案、tidy 阶段清理的旧版本
  文件都挪进这里，不直接删除。归档规模较大（默认 200 项以上）时 tidy
  阶段会提示"是否可以彻底删除某些过老的归档"，但默认不自动删。
- **业务子目录**：由 `GoalExecutionSpec.sub_directories` 声明，每个子
  目录可以带 `retention`（`latest_only`/`append`/`unbounded`）和
  `naming_pattern` 两个元信息（见
  [执行规范指南](goal-execution-spec-guide.md)），tidy 阶段据此做确定性
  核查。
- **`README.md`**：每次 tidy 阶段结束、以及每个 running（[Stage 8b]
  原名 `stable`）轮次结束时，由
  代码扫描 `output/` 实际内容机械生成——不是 agent 手写，用户和后续轮次
  可以直接信任这份索引反映的是客观事实。

## 4. `notes/`：每轮总结笔记

每轮（不管处于哪个阶段）结束时，agent 会写一份 `notes/cycle_NNNN.md`：
做了什么、为什么这么做、遇到的问题、给下一轮的建议。下一轮 prompt 会
自动带上最近 2~3 轮 `notes/*.md` 的原文，取代旧模型里只取最后一轮、
内容格式相对简陋的 `manifest.json` 机制。`notes/` 文件数超过阈值（默认
30 篇）时，tidy 阶段会把较旧的挪进 `notes/archive/`。

## 5. `spec/`：执行规范当前版本 + 历史版本

`spec/SPEC.md`/`spec/SPEC.json` 是当前生效版本的落盘快照，可以直接在
文件系统里打开查看，不用跑命令。每次 `GoalExecutionSpecBuilder` 保存新
版本时，旧版本会先被复制进 `spec/history/v{旧version}_{时间}.md/.json`
形成审计轨迹。

**只要 spec 已确认，running 阶段每一轮 prompt 都会自动带上 `spec/SPEC.md`
全文**——这是相对旧模型的关键变化，之前只在特定条件下才会出现规范内容。

converge 阶段收尾时，如果最近两轮的"方案对比说明"结论一致，系统会**主动
生成一份 spec 草稿**（不自动确认，仍需你手动用 `/agent goals spec
confirm` 确认），降低"卡在 converge 没人管、忘记手动生成规范"的概率；
生成后会收到一条通知，也会在这个 Goal 的进展记录里留一条说明。

## 6. `scratch/`：探索期试验田

explore/converge 阶段只允许写 `scratch/`，**不允许直接写 `output/`**
（converge 期的脚本草稿统一放在 `output/scripts/_experiments/`，而不是
`scratch/` 本身）。converge 阶段的任务是"从 scratch/ 里现存的几个方案中
选一个，搬进 output/，并在总结笔记里写清楚搬运理由 + 淘汰了哪些方案"；
未选中的连同其数据一起挪进 `_archive/`。进入 running 前，`scratch/`
必须清空——这是 tidy 阶段的强制核查项之一。[Stage 8d] 如果这个 Goal
声明了 `hardening_target`（能力固化型 Goal 的外部固化目标路径，见
[执行规范指南](goal-execution-spec-guide.md)），converge 阶段收尾时会
额外提示"搬迁的最终落点是这里，而不是（或不仅是）本 Goal 私有的
output/scripts/"。

## 7. 脚本类产出规范（`output/scripts/`）

```
output/scripts/
    README.md          每个脚本做什么、怎么调用、输入输出约定
    requirements.txt   依赖清单
    CHANGELOG.md        脚本改动历史
    lib/                多个脚本共享的工具函数
    fetch_metrics.py
    _run_logs/          每次运行的日志/报错，与业务数据分开存放
    _experiments/        临时实验脚本
```

- 正式脚本一个文件只做一件事，动词开头的 snake_case 命名，禁止
  `xxx_v2.py`/`xxx_final.py` 这类版本后缀——演进历史交给 `CHANGELOG.md`。
- 新装依赖必须同步更新 `requirements.txt`；tidy 阶段会扫描 `scripts/*.py`
  （不含 `_experiments/`）的 `import` 语句，对照 `requirements.txt` 提示
  疑似遗漏的第三方包（启发式核查，可能有误判，仅供参考）。
- 所有临时/实验性脚本一律写在 `scripts/_experiments/`，不允许出现在
  `scripts/` 根目录。如果某个实验脚本被最近几轮总结笔记反复提及、但一直
  没有搬迁转正，tidy 阶段会提示"评估是否需要转正"。
- explore/converge 阶段允许并鼓励使用 `_experiments/`；**running 阶段不应
  该再产生新的 `_experiments/` 内容**——如果又需要先写个临时脚本探索，
  本身就是一个信号，说明这个 Goal 可能没有真正收敛。

## 8. 各阶段与目录模型的配合（速查）

| 阶段 | 可写目录 | 收尾要求 |
|---|---|---|
| explore | `scratch/`（脚本草稿走 `output/scripts/_experiments/`） | 总结笔记写清楚试了什么、结论 |
| converge | `scratch/` → 搬进 `output/`；选定脚本 → 搬进 `scripts/` 根目录 | "方案对比说明"单独成段；连续两轮结论一致会自动生成 spec 草稿 |
| running | 仅 `output/`（含 `scripts/` 根目录的增量修改） | 每轮开头带上 `spec/SPEC.md` 全文；总结笔记记录增量变化 |
| tidy | 全目录只读审查 + 归档整理，不产出新内容 | 按代码算出的问题清单逐一处理，见下 |

## 9. tidy 阶段的问题清单

tidy 阶段不要求 agent 自己从零判断"哪里乱了"，而是先由代码扫描
`output/` 实际内容，算出一份确定性问题清单（散落在根目录的文件、
`_misc/` 未清空、`scripts/` 根目录混入疑似临时脚本、`_run_logs/` 超量、
`_archive/` 归档规模提示、`scratch/` 未清空、`requirements.txt` 疑似遗漏
的依赖、`_experiments/` 里应转正但未转正的脚本），再喂给 agent，让它专注
于"怎么处理这些具体问题"。业务子目录的 `retention`/`naming_pattern` 规则
核对（需要结合 `GoalExecutionSpec` 判断）暂未覆盖到代码检查，留给后续
版本。

**[Stage 8f] 三种 `output_mode` 的差异化默认模板**：如果这个 Goal 的
`GoalExecutionSpec.output_mode` 声明为 `capability_hardening`，
`_experiments/` 转正提示的触发阈值更低（脚本在最近笔记里被提及一次即
提示，其余模式需要提及两次才提示）；声明为 `accretive` 时，额外扫描
`output/` 顶层是否存在疑似未去重的重复累积文件（常见版本/副本后缀模式，
如 `report.md` 与 `report_v2.md` 并存）；默认的 `converging` 行为与
`output_mode` 字段引入之前完全一致。

## 10. 迁移与兼容性

已存在的 `cycle_0001/`、`cycle_0002/`……历史目录**保留原样，不做自动
迁移**，视为 legacy 目录。新逻辑生效后，新产出一律走这套
`output/`/`notes/`/`spec/`/`scratch/` 结构；一次性 Goal 和独立 cron job
不受影响。

具体到"这个 Goal 已经在旧模型下跑过好几轮了，切到新模型会不会丢历史"：
不会完全丢——第一次按新模型触发时，系统会检测这个 Goal 名下是否存在旧的
`cycle_NNNN/` 目录，如果有且能读到 `manifest.json`，会自动生成一份迁移
摘要（最近几轮各自的 `progress_note`），写进 `notes/cycle_0000.md`（用
0 号占位，不占用真实轮次编号）。之后几轮的 prompt 会像读取任何一篇正常
总结笔记一样带上这份摘要，agent 能看到"这个 Goal 之前大致做了什么"，不
会毫无上下文地重新开始。旧的 `cycle_NNNN/` 目录本身不会被删除或搬迁，
仍然可以直接去 `.agent/daemon_run_outputs/goals/<goal_id>/` 下查看完整
历史。这个迁移摘要只在"第一次切到新模型"那一轮生成一次，不会每轮重复。

### 10.1 主动迁移：把旧数据真正搬进新模型（"迁移轮"）

10.0 节说的是"被动摘要"——只是让 agent 看到一段文字提示，旧目录里的
文件本身从不移动。如果你想要更进一步，真正把旧 `cycle_NNNN/` 目录里
还有价值的产出搬进新的 `output/` 结构，可以主动触发一次专门的"迁移轮"：

- **CLI**：`/agent goals migrate-legacy <goal_id>`
- **API**：`POST /v1/goals/{goal_id}/migrate_legacy`

两者都只是打一个一次性标记（跟"跳过下一轮"`skip_next_cycle`是同一种
机制），不会立刻执行——真正的搬迁工作会在这个 Goal **下一次触发**时
（自然到期，或 daemon 模式下用 `/cron run <job_id>` 立即触发这一轮）
由 agent 完成。触发条件：Goal 必须是周期性的（`recurring=true`），且
名下确实存在还没处理过的 legacy `cycle_NNNN/` 目录，否则会提示或报错。

那一轮触发时，agent 会额外收到一段搬迁指令（叠加在正常的阶段提示之上，
不影响当前处于 explore/converge/running/tidy 哪个阶段），要求它：

1. 依次打开每个待处理的旧目录，判断产出物是否还有价值（不确定就倾向
   保留）；
2. 有价值的产出：如果这个 Goal 已经有已确认的执行规范，按规范里声明的
   业务子目录归类；没有规范就先统一放进 `output/_misc/`（后续 tidy
   阶段会要求正式归类）；
3. 明显没价值的（失败的尝试、临时数据）：挪进 `output/_archive/`，
   不直接删除；
4. 每处理完一个旧目录，把它重命名加上 `__migrated` 后缀（比如
   `cycle_0001` → `cycle_0001__migrated`）作为"已处理"标记——目录本身
   **不会被删除**，仍然保留在原地作为审计痕迹；
5. 在本轮的 `notes/cycle_NNNN.md` 里专门写一段"历史数据迁移"说明，
   记录搬了什么、搬去了哪里、跳过了哪些及原因。

如果旧目录数量很多，一轮处理不完也没关系——没加 `__migrated` 后缀的
目录会在下次再触发迁移轮时自动被识别为"还没处理"，继续处理剩余部分，
不需要一次性做完。tidy 阶段的问题清单也会顺带提醒"还有 N 个旧目录尚未
迁移"（纯提示，不影响 tidy 本身的判定结果）。

## 11. 用户自定义输出路径怎么处理

[goal_user_output_dir_plan.md] 早期版本（见下方历史说明）这里只有一段
"软性提醒"，实际产出路径仍然强制在 `.agent/daemon_run_outputs/goals/
<goal_id>/output/` 下，无法真正指向用户想要的位置。现在支持**真正生效**
的用户自定义产出目录，机制如下。

### 11.1 `GoalNode.user_output_dir`：唯一生效的开关

`GoalNode` 上新增了 `user_output_dir` 字段（相对 `project_root` 的路径，
如 `research/stock_analyse`；也接受绝对路径，用于指向项目外的目录）。
一旦设置：

- `output/` 会解析到 `<project_root>/<user_output_dir>`，**不再**是
  `.agent/daemon_run_outputs/goals/<goal_id>/output/`；
- `notes/`、`spec/`、`scratch/` **三个内部目录不受影响**，始终留在默认的
  `.agent/daemon_run_outputs/goals/<goal_id>/` 下——用户想改的是"产出物
  放哪"，不是"agent 自己的过程记账放哪"，二者是正交的；
- 本轮 prompt 会直接写明"本 Goal 已由用户明确指定产出目录为
  `<user_output_dir>`（解析后的绝对路径：……）"，不再需要 agent 自己
  从描述文字里猜测；
- `output/` 内部规范（§3~§9：`README.md`/`_misc/`/`_archive/`/
  `scripts/`/业务子目录/tidy 问题清单等）完全不变，只是整个 `output/`
  的物理位置换了地方。

未设置 `user_output_dir` 时，行为与之前完全一致（默认路径），对已有
Goal 零影响。

### 11.2 从描述里自动检测 → 看板确认，而不是直接生效

创建 Goal（`GoalBacklog.add_goal()`）时，系统仍会跑一遍启发式检测
（`detect_user_specified_output_hint()`，正则匹配"放到"/"写入"/"输出到"
等关键词附近的路径片段），但检测结果**不会自动写入 `user_output_dir`**，
只是存进一个建议字段 `user_output_dir_suggested`，原因很直接：纯规则
匹配不可靠，直接拿去当正式产出目录风险太高（比如误把一句无关的话里的
斜杠内容当成路径）。

真正生效需要经过人工确认：

- **看板**：Goal 编辑表单（`✏️ 编辑标题/描述/优先级`）里有一个"产出
  目录"输入框。如果用户还没手动设置过，输入框会自动填入检测到的建议值
  并提示"根据描述自动检测到疑似产出路径……确认无误可直接保存；如果
  检测不准，请改写或清空"；用户点保存后才真正写入 `user_output_dir`，
  之后立即在下一轮触发时生效。
- **API**：`PATCH /v1/goals/{goal_id}`，body 里带 `user_output_dir`
  字段（字符串）。传空字符串等价于"清除设置，改回默认路径"。

如果创建 Goal 时描述里没有检测到任何路径提示，`user_output_dir_suggested`
为空，看板输入框就是空的，用户可以随时手动填写，不依赖检测结果。

### 11.3 未确认状态下仍保留旧的软性提醒

`user_output_dir` 还没被设置（无论是完全没检测到，还是检测到了但用户
还没确认）的情况下，行为退回 §11（旧版）的软性提醒机制：prompt 里会
提示"检测到描述里提到了这些路径片段：……，如果本意是 `output/` 之外的
绝对路径，看板上有一个「产出目录」建议（已根据这段描述自动检测），
确认后 agent 会直接把该路径作为正式产出目录"——引导用户去看板走 §11.2
的确认流程，而不是继续指望 agent 自己从文字里"理解对"。

### 11.4 仍然保留的边界

`user_output_dir` 只影响 `output/` 一个目录的物理位置，`output/` 内部
规范（固定骨架、tidy 确定性核查等）不变，这是有意为之——四个固定目录
（换个位置后是"三个固定位置 + 一个用户指定位置"）之间的分工关系才是
让 tidy 阶段能做确定性核查的前提，不提供"整个 Goal 完全脱离这套规范"
的开关。如果确实需要产出物散落在多个不相关的路径、或者不希望有
`_misc/`/`scripts/` 这类系统保留目录，建议改用独立 cron job（`run_mode`
非 `goal_cycle`）或一次性 Goal，两者仍沿用旧的 `cycle_NNNN/`/`run_NNNN/`
模型，目录分配更灵活。
