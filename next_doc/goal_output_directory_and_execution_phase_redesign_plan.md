# Goal 产出目录模型与执行阶段协同改进方案

## 0. 背景与问题

`goal_execution_phase_improvement_plan.md`（explore/converge/stable/tidy 四阶段）
和 `goal_execution_spec_generation_plan.md`（GoalExecutionSpec）上线后，实际运行
反馈是：**阶段划分本身没问题，但没有真正起到预期作用**——recurring Goal 跑
多轮之后，产出目录仍然很乱，tidy 阶段也没有起到整理效果。复盘下来，根因不是
阶段的判定逻辑或 prompt 文案，而是**载体设计的问题**：

1. `output_workspace.py` 的目录分配策略是"每一轮（cycle）一个新目录
   `cycle_0001/`、`cycle_0002/`……"，产出物和过程记录（manifest）绑在了
   一起，且只是把路径作为**建议**拼进 prompt 文本，没有任何强制——explore
   阶段又天然鼓励"换目录结构"，几轮跑下来必然发散、难以收敛。
2. tidy 阶段的 prompt（`prompts/fragments/execution_phase.md::TIDY_BLOCK`）
   只是一句"请评估并整理现有产出目录"，**不会自动把实际目录内容喂给
   agent**，核对清单（`_build_tidy_checklist_hint()`）还要求先手动确认过
   `GoalExecutionSpec` 才有内容，且整理完全靠 agent 自觉，事后没有任何核查
   ——agent 哪怕什么都没做，只要报告里说"已整理"，系统就直接判定完成、
   打回 stable。
3. `GoalExecutionSpec` 只有"当前版本"，`save_spec()` 直接覆盖旧文件，没有
   历史版本可追溯；且只在"确认+近期变更"的窄条件下拼进 prompt，用户/后续
   轮次没有一个固定路径可以直接打开看"这个 Goal 现在到底遵循什么规范"。
4. 脚本类产出（大量 Goal 的核心工作是"写 Python 脚本 + 跑脚本产出数据"）
   完全没有专门规范，脚本源码、脚本产出数据、运行日志、临时实验脚本全部
   混在一起，是"目录混乱"里最常见的一类。

本方案在不改动阶段划分本身（explore/converge/stable/tidy 四阶段的定义和目的
不变）的前提下，重新设计**产出目录模型**和**阶段与目录模型的配合方式**，
让四个阶段真正"有地方生效"。

## 1. 新目录模型总览

用四个并列目录取代现有的"每轮一个 cycle 目录"：

```
.agent/daemon_run_outputs/goals/<goal_id>/
    output/                    ← 唯一的、跨轮共用的正式产出目录（§2）
    notes/                     ← 每轮一份总结笔记，过程记录，非交付物（§3）
        cycle_0001.md
        cycle_0002.md
        archive/                太多时旧的挪进来
    spec/                      ← 执行规范，当前版本 + 历史版本（§4）
        SPEC.md
        SPEC.json
        history/
            v1_2026-07-01.md / .json
            v2_2026-07-15.md / .json
    scratch/                    ← explore/converge 期的试验田，允许乱、允许推翻（§5）
```

四者不重叠：`output/` 是"内容"，`notes/` 是"过程"，`spec/` 是"规则"，
`scratch/` 是"草稿"。`cron/<job_id>/`（非 goal_cycle 的独立 cron job）沿用
原有的 `run_<run_id>/manifest.json` 模式不变——本方案只针对 recurring Goal
（`run_mode=goal_cycle`）的产出目录，因为"探索到稳定"这个语境本身就依赖
"同一个 Goal 的连续多轮"，独立 cron job 没有这个语境（与
`docs/goal-execution-phase-guide.md` 里"生效范围"一节的既有结论一致）。

一次性（非 recurring）Goal 的多个子 Objective（`run_0001/`、`run_0002/`……）
维持现状不变——那些子任务之间不是"轮次收敛"关系，套用这套模型意义不大。

## 2. `output/` 内部规范

### 2.1 固定骨架（系统级，所有 Goal 统一，不可由 spec 覆盖）

```
output/
    README.md              ← 自动生成的目录索引，代码扫描生成，不是 agent 手写
    _misc/                  ← 未分类产出的临时收容所（系统保留目录）
    _archive/               ← 被淘汰但保留价值的历史版本（系统保留目录）
    scripts/                ← 脚本源代码专用（系统保留目录，§6）
    <spec.sub_directories 声明的业务子目录...>
```

根目录下只允许出现：`README.md`、三个系统保留目录（`_misc/`/`_archive/`/
`scripts/`）、以及 spec 里明确声明过的业务子目录。tidy 阶段可以用固定代码
逻辑判断"根目录下是否存在白名单之外的文件/目录"，不需要 LLM 主观判断
"这里乱不乱"。

### 2.2 `_misc/`：允许存在，但不允许长期存在内容

agent 一时拿不准某个产出该归到哪个业务子目录时，允许先扔进 `_misc/`，避免
为了"当场决定归属"而随手新建一个临时目录名。但规则是：**`_misc/` 里的
内容不允许跨轮留存**——tidy 阶段第一优先级任务就是清空它：能归类的挪进
对应业务子目录（顺手更新 spec 把这类产出正式纳入规范），确实没归属的挪进
`_archive/` 并注明原因。

### 2.3 `_archive/`：淘汰产出的去处，不直接删除

- converge 阶段淘汰的备选方案、tidy 阶段清理的旧版本文件，都挪进
  `_archive/`，不直接删除，避免误判不可逆。
- 内部按"归档时间 + 原因"分子目录，如 `_archive/2026-07-20_converge_淘汰
  方案B/`，允许内部结构随意（反正不会再被后续轮次引用），只要求归档时留
  一句归档原因（写进目录名或旁边一个 `REASON.md`）。
- 超过一定大小/文件数阈值（后续可配置，第一版先给一个保守的硬编码值，如
  200 个文件或 50MB）时，tidy 阶段提示"是否可以彻底删除某些过老的归档"，
  **默认不自动删**，只提示，决定权留给用户。

### 2.4 业务子目录：由 spec 定义，补充两个元信息字段

`GoalExecutionSpec.SubDirectory` 现有 `name`/`purpose` 基础上新增：

- `retention`：`"latest_only"`（只保留最新一份，每轮覆写）/ `"append"`
  （按轮次累积保留）/ `"unbounded"`（不做特殊管理，人工决定）
- `naming_pattern`：与 `Deliverable` 已有的同名字段语义一致，如
  `"YYYY-MM-DD_<主题>.md"`，避免命名风格逐轮漂移

`retention` 让 tidy 阶段的核查变成确定性代码逻辑：`append` 类超量未归档、
`latest_only` 类却存在多个历史版本，都可以直接扫描文件系统判断，不需要
每次都靠 LLM 主观判断。

### 2.5 `README.md`：代码生成，不是 agent 手写

每次 tidy 阶段结束、以及每个 stable 轮次结束时，由代码扫描 `output/` 实际
内容机械生成（结构示例）：

```markdown
# <Goal 标题> — 产出目录索引
最后更新：第 N 轮（自动生成，非 agent 手写）

## 业务子目录
- `reports/`（12 个文件，最新：2026-07-20）：按 spec 声明的用途
- `data/`（3 个文件，最新：2026-07-18）

## 待整理
- `_misc/`：0 个文件 ✅ / 3 个文件 ⚠️（下次 tidy 请处理）
- `scripts/`：见 `scripts/README.md`

## 历史归档
- `_archive/`：共 4 次归档，详见各子目录
```

用代码生成而不是让 agent 写，是为了让"目录长什么样"这份客观事实与 agent
自己写的主观整理报告（notes 里）分开，用户和后续轮次都可以先信任这份索引，
不用相信 agent 嘴上说的"已经整理好了"。

## 3. `notes/`：每轮总结笔记

- 每轮（不管处于哪个阶段）结束时都要求 agent 写一份 `notes/cycle_NNNN.md`，
  结构建议：
  ```markdown
  # 第 N 轮（<阶段>）
  - 做了什么：...
  - 为什么这么做 / 相比上一轮的变化：...
  - 遇到的问题：...
  - 给下一轮的建议：...
  ```
  explore 期重点写"试了什么、结论是什么"；converge 期重点写"选定方案 + 淘汰
  了哪些"；stable 期重点写"这轮的增量修改"；tidy 期就是整理报告本身。
- 下一轮 prompt 自动带上最近 2~3 轮 `notes/*.md` 的原文（不再依赖现有
  `read_latest_manifest()` 那种只取最后一轮、内容格式相对简陋的机制），
  explore 期能直接看到"之前试过什么、不用重复踩坑"，stable 期能看到
  "上几轮的增量脉络"。
- `notes/` 文件数超过阈值（如 30 篇）时，tidy 阶段把较旧的挪进
  `notes/archive/`，只在主目录保留最近若干篇，保持"下一轮直接读取"的
  开销可控。

## 4. `spec/`：执行规范当前版本 + 历史版本

- `spec/SPEC.md` + `spec/SPEC.json`：当前生效版本，`SPEC.md` 是
  `GoalExecutionSpec.render_prompt_block()`/`describe()` 的落盘渲染结果，
  用户可以直接在文件系统里打开查看，不用跑命令。
- `save_spec()` 改为：写入新版本前，先把旧的 `SPEC.md`/`SPEC.json` 复制进
  `spec/history/v{旧version}_{confirmed_at 或 revise 时间}.md/.json`，再写
  当前版本。历史版本形成审计轨迹，配合 `notes/` 能回答"这个 Goal 什么时候、
  为什么改变了产出规则"。
- **只要 spec 已确认，每一轮（不限于 converge/tidy）prompt 都自动带上
  `spec/SPEC.md` 全文**——这是相对现状的关键变化：目前只在特定条件下
  （converge 未确认时提示生成、tidy 且已确认时给核对清单）才出现，改为
  "确认后每轮开头都能看到"，stable 期尤其需要，因为 stable 的核心要求就是
  "严格遵循已确认规范"，而目前 stable 阶段的 prompt 片段（`STABLE_BLOCK`）
  完全没有把 spec 内容带出来，只是一句"严格遵循已确认的执行规范（如有）"
  的空泛提示。
- converge 阶段收尾时，如果连续 2 轮的"方案对比说明"结论一致（相似度判断，
  复用现有 `compute_progress_trend_signal` 的 difflib/LLM 双模式基础设施），
  系统**主动生成一份 spec 草稿**（不自动确认，仍需用户手动 `spec confirm`），
  降低"卡在 converge 没人管、迟迟进不了 stable"的概率——这是对"没有真正从
  探索走到稳定"这个反馈的直接回应：目前的硬性门槛是必须有人手动跑一次
  `spec generate` + `spec confirm`，很容易被忘记。

## 5. `scratch/`：探索期试验田

- explore/converge 阶段只允许写 `scratch/`，**不允许直接写 `output/`**
  （新增硬约束，第一版先在 prompt 里明确路径限制 + tidy 阶段事后核查
  "output/ 是否有 explore 期写入痕迹"兜底，暂不追求工具层强制拦截）。
- converge 阶段任务变为："从 `scratch/` 里现存的几个方案中选一个，**搬进**
  `output/`（含 §6 的 `output/scripts/`），并在 `notes/cycle_NNNN.md` 里写
  清楚搬运理由 + 淘汰了哪些方案"；未选中的连同其数据一起挪进 `_archive/`。
- 进入 stable 前，`scratch/` 必须清空或仅保留明确标注"仅存档不再维护"的
  内容——这是 tidy 阶段的强制核查项之一，不满足时不允许 tidy 报告"整理
  完成"。

## 6. 脚本类产出专项规范（`output/scripts/`）

Goal 里大量场景是"写 Python 脚本 + 执行脚本产出数据"，脚本代码和脚本产出
必须物理分开，否则几轮下来分不清哪个是工具、哪个是数据。

```
output/scripts/
    README.md          每个脚本做什么、怎么调用、输入输出约定
    requirements.txt   依赖清单（新装依赖必须同步更新，tidy 阶段核查）
    CHANGELOG.md        脚本改动历史（人写，简短）
    lib/                多个脚本共享的工具函数
    fetch_metrics.py
    generate_report.py
    _run_logs/          每次运行的日志/报错，与业务数据分开存放
    _experiments/        临时实验脚本（§6.4）
```

### 6.1 正式脚本的命名与设计约定

- 一个脚本只做一件事，文件名用动词开头的 snake_case（`fetch_xxx.py`、
  `generate_xxx.py`、`check_xxx.py`），不用 `script.py`、`main2.py` 这类
  无信息量命名。
- 禁止 `xxx_v2.py`/`xxx_final.py` 这类版本后缀命名——脚本要改进就直接改
  `xxx.py` 本体，演进历史交给 `CHANGELOG.md`；正式脚本一旦进入 `scripts/`
  根目录就代表"当前在用的唯一版本"。
- 输入输出路径必须作为参数或脚本内常量显式声明，不写死绝对路径，方便下一
  轮 agent 直接读代码就知道"这个脚本读哪里、写哪里"。
- 可重复执行、不重复产生副作用：同样的输入重跑一次，行为应该是幂等的
  （覆盖同一份产出，而不是每次都新增一份），除非业务上确实需要按次追加
  （这种情况要在 `scripts/README.md` 里写清楚，并让对应产出走
  `retention: "append"` 的业务子目录）。

### 6.2 依赖管理

- 不为每个 Goal 单独建虚拟环境（成本高、易漂移），默认直接用项目现有
  Python 环境。
- 新装依赖必须同步更新 `requirements.txt`。tidy 阶段做确定性核查：扫描
  `scripts/*.py`（不含 `_experiments/`）的 `import` 语句，对照
  `requirements.txt` 有没有遗漏。

### 6.3 运行日志

- 脚本执行的标准输出/报错统一写进
  `scripts/_run_logs/<脚本名>_<时间戳>.log`，不散落在业务子目录或根目录。
- `_run_logs/` 按 `retention: latest_N`（如最近 10 次）处理，tidy 阶段清理
  旧日志。
- 脚本执行失败时，`notes/cycle_NNNN.md` 必须写清楚"哪个脚本、什么错误"，
  不能只留在日志文件里等着被忽略。

### 6.4 临时/实验性脚本（新增）

写代码时经常需要"先写个一次性脚本探索一下数据格式/验证一个想法"，这类脚本
不该和正式脚本混在一起，也不该直接污染 `scripts/` 根目录，但也不适合完全
不留痕迹（可能后面还要参考"当时是怎么探索出这个结论的"）。约定：

- **所有临时/实验性脚本一律写在 `output/scripts/_experiments/` 下，不允许
  出现在 `scripts/` 根目录或任何业务子目录里**。这是 tidy 核查的一条明确
  规则：`scripts/` 根目录下如果出现明显带"测试/临时/一次性"性质的命名
  （如 `test_xxx.py`、`try_xxx.py`、`tmp_xxx.py`、`debug_xxx.py` 等模式）
  或者没有被 `README.md` 提及的脚本，判定为应该在 `_experiments/` 而不是
  根目录，tidy 阶段应挪动归位。
- `_experiments/` 内部**不做命名规范强制**，允许 `try_1.py`、
  `test_parse_v3.py` 这种随手命名，探索阶段就该是自由的。
- `_experiments/` 按轮次分子目录归档（如
  `_experiments/cycle_0007_探索xxx方案/`），而不是所有实验脚本平铺在一起
  ——避免几轮下来又变成一堆无法区分归属的散文件。
- **判断一个实验脚本是否要"转正"**：如果某次实验验证成功、其产出/逻辑被
  正式采纳（尤其是在 converge 阶段做方案选择时），对应脚本应该被**搬迁
  并重命名**进 `scripts/` 根目录（遵循 §6.1 的正式命名约定），而不是简单
  复制一份——避免根目录和 `_experiments/` 里同时存在两份功能重复的代码。
  搬迁时要在 `CHANGELOG.md` 补一条记录"脚本 X 源自第 N 轮 `_experiments/`
  下的某次实验"。
- explore/converge 阶段允许并鼓励使用 `_experiments/`；**stable 阶段不应
  该再产生新的 `_experiments/` 内容**——如果 stable 期又需要"先写个临时
  脚本探索一下"，本身就是一个信号，说明这个 Goal 可能没有真正收敛，值得
  在 `notes/` 里如实指出，必要时考虑退回 converge。
- tidy 阶段对 `_experiments/` 的处理：不要求清空（探索痕迹本身有价值），
  但要求检查是否有明显失败/已废弃且确定不会再参考的实验，可以挪进
  `_archive/`；同时检查是否存在"应该转正但一直没转正"的情况（比如某个
  实验脚本被后续多轮 notes 反复引用，但迟迟没有正式迁移）。

## 7. 各阶段与新目录模型的配合（汇总）

| 阶段 | 可写目录 | 脚本相关行为 | 收尾要求 |
|---|---|---|---|
| explore | `scratch/`（含 `scratch/scripts_attempt_N/` 或直接对应 `output/scripts/_experiments/`，两种草稿定位方式二选一，倾向统一用 `_experiments/`，见下方"待决策"） | 允许多套实现并存、随意推翻 | `notes/cycle_NNNN.md` 写清楚试了什么、结论 |
| converge | `scratch/` → 搬迁进 `output/`；`_experiments/` 中选定脚本 → 搬迁进 `scripts/` 根目录 | 选定实现，淘汰的连同数据一起挪 `_archive/` | 写"方案对比说明"；spec 草稿自动生成条件达成时触发 §4 |
| stable | 仅 `output/`（含 `scripts/` 根目录的增量修改） | 只允许对已有正式脚本做增量修改，不允许新增平行实现；不应再新增 `_experiments/` 内容 | 每轮开头带上 `spec/SPEC.md` 全文；notes 记录增量变化 |
| tidy | 全目录只读审查 + 归档整理，不产出新内容 | 见 §6.4 tidy 处理规则 | 见 §7.1 完整核查清单 |

### 7.1 tidy 阶段完整核查清单（尽量代码化，减少纯 LLM 主观判断）

1. `output/` 根目录是否只有白名单内容（`README.md`/`_misc/`/`_archive/`/
   `scripts/`/spec 声明的业务子目录）？—— 代码直接判断
2. `_misc/` 是否为空？不为空必须处理
3. 各业务子目录内容是否符合 `retention` 规则？—— 代码判断
4. 文件命名是否匹配 `naming_pattern`？—— 代码判断
5. `scratch/` 是否已清空/归档？
6. `scripts/` 根目录是否存在应属于 `_experiments/` 的临时脚本、或未在
   `README.md` 提及的脚本？—— 代码判断
7. `requirements.txt` 是否与 `scripts/*.py`（不含 `_experiments/`）实际
   `import` 一致？—— 代码判断
8. `scripts/_run_logs/` 是否超过保留数量？
9. `_experiments/` 是否存在应转正但一直未转正的脚本（结合 notes 里的
   引用频率判断）？
10. 生成/刷新 `output/README.md`
11. **事后核查**：对比这一轮开始前后 `output/`、`scratch/` 的文件清单
    差异；如果 agent 的整理报告声称做了大量整理但目录几乎没有变化，触发
    健康告警（复用现有 `check_phase_health()`/`_notify_phase_health_issue()`
    通知机制）

第 1/3/4/6/7/8 条都是确定性代码检查，tidy 阶段的 prompt 应该直接把代码算
出来的"问题清单"喂给 agent，让它专注于"怎么处理这些具体问题"，而不是从零
判断"哪里乱了"——这是解决"tidy 没有真正起作用"最直接的一步。

## 8. 待决策/留待实现时细化的问题

- **explore 期草稿脚本到底放 `scratch/` 下还是 `output/scripts/_experiments/`
  下**：倾向后者（统一由 `_experiments/` 承担"脚本类草稿"的角色，
  `scratch/` 只承担"非脚本类产出的草稿"），因为脚本经常需要跨轮延续
  （这次没写完，下次接着改），放在 `output/` 内部更符合"下一轮直接能看到
  上一轮实验代码"的直觉；但这样 `output/` 就不是"纯稳定产出"了，需要在
  `README.md` 生成逻辑里明确标注 `_experiments/` 为"探索中，非最终产出"。
  实现时需要最终敲定，本文档先记录两种方案供参考。
- `_archive/` 的自动提示阈值（文件数/大小）第一版先给保守硬编码值，后续
  视实际使用情况考虑是否要做成可配置项（参考 `execution_phase` 现有一批
  `DEFAULT_*` 常量的做法，先上线观察）。
- 是否需要给 `output/scripts/` 增加"沙箱执行"限制（比如禁止脚本访问项目
  根目录之外的路径）：本方案不涉及执行安全层面的改动，如有需要另开独立
  方案讨论。

## 9. 迁移与兼容性

- 已存在的 `cycle_0001/`、`cycle_0002/`……历史目录**保留原样，不做自动
  迁移**，避免误删用户产出；文档里注明为"legacy 目录"。新逻辑生效后，新
  产出一律走新的 `output/`/`notes/`/`spec/`/`scratch/` 结构。
- `output_workspace.py` 里 `allocate_cycle_dir()` 等函数标记废弃（保留
  函数体一段时间以防有历史调用方依赖，行为不变），新增
  `goal_output_dir()`/`goal_notes_dir()`/`goal_spec_dir()`/
  `goal_scratch_dir()` 等对应新模型的分配函数。
- `GoalExecutionSpec.SubDirectory` 新增 `retention`/`naming_pattern` 两个
  可选字段，缺省时按 `"unbounded"`/无命名约束处理，向后兼容旧的已保存
  spec 文件（`from_dict()` 用 `d.get(..., 默认值)` 兜底）。
- 大量涉及模块和测试需要同步更新（见 §10 实施顺序），逐步推进，不要求
  一次性切换。

## 10. 建议的实施顺序

> **实施进度**（随实现推进更新，最新状态见文末"实施记录"）：
> - ✅ Stage 1：`output_workspace.py` 目录模型改造 —— 已完成
> - ✅ Stage 2：`GoalExecutionSpec` 版本历史归档 —— 已完成
> - ⬜ Stage 3：`goal_cron_bridge.py` 阶段 prompt 重新拼接
> - ⬜ Stage 4：converge 自动生成 spec 草稿
> - ⬜ Stage 5：`output/scripts/` 专项规范落地（骨架分配已随 Stage 1 完成，
>   核查/审计逻辑待 Stage 3 的 tidy 改造一并接入）
> - ⬜ Stage 6：文档全面同步

1. `output_workspace.py` 目录模型改造（`output/`/`notes/`/`spec/`/
   `scratch/` 四件套 + `output/` 内部固定骨架分配函数）+ 对应单测——这是
   地基，后续步骤都依赖它。
2. `goal_execution_spec.py`：`SubDirectory` 新增 `retention`/
   `naming_pattern` 字段；`save_spec()` 加历史版本归档逻辑；落盘
   `spec/SPEC.md`/`SPEC.json`。
3. `goal_cron_bridge.py` 按新阶段职责重新拼 prompt：
   - explore/converge 限定写 `scratch/`（含脚本走 `_experiments/`）
   - converge 增加"搬运 + 清理"的明确要求
   - stable 每轮固定带上 `spec/SPEC.md` 全文，限定只写 `output/`
   - tidy 改为"代码算出问题清单 + 事后核查"模式（§7.1）
4. converge 满足"连续 2 轮方案对比说明一致"条件时自动生成 spec 草稿。
5. `output/scripts/` 专项规范落地：`README.md`/`requirements.txt`/
   `CHANGELOG.md`/`_run_logs/`/`_experiments/` 的分配与核查函数。
6. 文档同步更新：`docs/goal-execution-phase-guide.md`、
   `docs/goal-execution-spec-guide.md`、`docs/unified-scheduler-guide.md`
   中涉及 `output_workspace` 的部分，以及新增一份面向用户的"产出目录规范"
   说明文档。

## 11. 实施记录

### Stage 1（已完成）：`output_workspace.py` 目录模型改造

新增函数（均为 recurring Goal 专用，不影响一次性 Goal / 独立 cron job 的
既有 `allocate_cycle_dir()`/`allocate_run_dir()`/`allocate_objective_dir()`
行为，两套机制并存）：

- `goal_output_dir()`/`goal_notes_dir()`/`goal_spec_dir()`/
  `goal_scratch_dir()`：四个并列目录的路径函数。
- `ensure_output_skeleton()`：幂等创建 `output/` 固定骨架（`README.md`/
  `_misc/`/`_archive/`/`scripts/` 及其 `lib/`/`_run_logs/`/`_experiments/`
  子目录 + `requirements.txt`/`CHANGELOG.md`/`README.md` 占位文件）。
- `scan_output_structure()`：扫描 `output/` 实际内容，返回结构化统计
  （散落根文件、`_misc/` 内容、各业务子目录文件数与最新 mtime、脚本相关
  统计、归档条目数）。**目录一律不算"未分类"**（业务子目录是否与 spec
  一致，留给后续 Stage 3 的 tidy 逻辑结合 `GoalExecutionSpec` 核对），只有
  散落在根目录的**文件**才会被标记，避免这个函数越权做本该由 spec 层做的
  判断。
- `render_output_readme()`：基于 `scan_output_structure()` 机械生成
  `output/README.md`，不经过 LLM。
- `write_cycle_note()`/`read_recent_notes()`/`archive_old_notes()`：
  `notes/cycle_NNNN.md` 的写入、按轮次倒序读取最近 N 篇、超过阈值时归档
  进 `notes/archive/`。
- `scratch_is_empty()`：判断 `scratch/` 是否已清空（tidy 阶段核查用）。

`OUTPUT_RESERVED_NAMES`/`SCRIPTS_RESERVED_NAMES` 两个常量集中定义了 §2.1/
§6 的系统保留名单，后续 tidy 核查、`render_output_readme()` 复用同一份，
避免出现两处名单不一致。

测试：`tests/test_output_workspace_new_layout.py`（13 个用例），覆盖路径
函数、骨架幂等创建、结构扫描（含散落文件/临时脚本命名/实验脚本识别）、
README 生成、notes 读写归档、scratch 清空判断。

尚未接入 `goal_cron_bridge.py` 的实际触发流程（这部分是 Stage 3 的工作）
——目前 recurring Goal 触发时仍走原有的 `allocate_cycle_dir()` 路径，新函数
已就绪但还未被生产逻辑调用，这是有意为之的分阶段推进，避免一次性大改动
影响现有正在运行的 Goal。

### Stage 2（已完成）：`GoalExecutionSpec` 版本历史归档

`perception/goal_execution_spec.py`：

- `SubDirectory` 新增 `retention`（`"latest_only"` / `"append"` /
  `"unbounded"`，缺省 `"unbounded"`）/ `naming_pattern`（缺省空字符串）两个
  可选字段。`from_dict()` 用 `d.get(..., 默认值)` 兜底，非法 `retention`
  值也会回退为 `"unbounded"`，向后兼容旧的已保存 spec 文件（缺这两个字段
  或值非法都不报错）。`render_summary_for_user()`/`render_prompt_block()`
  同步把 `retention`/`naming_pattern` 渲染进子目录说明，方便用户和 agent
  都能看到"这个子目录该怎么管理"。
- `save_spec()` 保持权威存储路径
  （`.agent/goal_execution_specs/<goal_id>.json`）不变，在此基础上追加两个
  动作，且都包在 try/except 里、失败只记录日志不影响主流程（落盘快照/
  归档是锦上添花的可见性能力，不应因为产出目录一时不可写导致 spec 保存
  本身失败）：
  1. `_archive_prior_spec_version()`：写入新版本前，若 `spec/SPEC.json`
     已存在（即"即将被覆盖的旧版本"），复制进
     `spec/history/v{旧version}_{confirmed_at 或 generated_at 对应日期}.md`
     / `.json`（同一天重复保存同一版本号时自动加数字后缀避免互相覆盖）。
  2. `_write_spec_snapshot()`：把当前版本渲染落盘到 `spec/SPEC.md`
     （`render_summary_for_user()` 的落盘结果）+ `spec/SPEC.json`
     （`to_dict()` 结构化数据），供用户直接在文件系统里打开查看。
  两者都通过 `evolution/output_workspace.py::goal_spec_dir()` 定位
  `spec/` 目录路径，为避免循环 import（`output_workspace.py` 不依赖本
  模块），采用函数体内延迟 import。
- 新增 `list_spec_history()`：列出某 Goal 的历史 spec 版本摘要（文件名/
  version/confirmed_at/generated_at/confirmed），按时间倒序，供 CLI/看板
  未来展示"这个 Goal 什么时候、为什么改变了产出规则"使用（本阶段只提供
  数据函数，尚未接入 CLI/看板 UI，留给后续阶段或独立小改动）。

测试：`tests/test_goal_execution_spec_versioning.py`（13 个用例），覆盖
`SubDirectory` 新字段的默认值/序列化往返/非法值兜底/旧数据兼容、
`render_summary_for_user()`/`render_prompt_block()` 是否带出新字段、
`save_spec()` 落盘 SPEC.md/SPEC.json、首次保存不产生历史记录、二次/三次
保存正确归档、`list_spec_history()` 排序与空结果、以及"产出目录被文件占位
导致快照写入失败时 save_spec() 本身不崩溃"的容错路径。

尚未做的事（留给后续阶段）：`goal_cron_bridge.py` 仍未在每轮 prompt 里
固定带上 `spec/SPEC.md` 全文（这是 Stage 3 的工作，方案 §4 提到的"确认后
每轮开头都能看到"目前还未生效）；converge 阶段"连续 2 轮方案对比说明一致
时自动生成 spec 草稿"是 Stage 4 的工作，本阶段未涉及。
