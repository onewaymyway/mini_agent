# novel_writer 使用说明（M0）

## 快速开始

```bash
cd external_projects/novel_writer
pip install -r requirements.txt
python entrypoints/health.py                       # 输出 ok 即 23 个 skill 完整、数据目录可写
python entrypoints/create_novel.py --title 青云传   # 创建工作目录 data/novels/青云传_<时间戳>/
python entrypoints/list_novels.py
streamlit run app.py --server.port 8504                              # 书架 / 新建小说 / Skill 库
```

`create_novel` 可选参数：`--workspace <任意绝对路径>`（会登记到 `data/shelf.json`）、`--gear G0..G3`、
`--quality draft|standard|premium`、`--total-chapters N`。

## 工作目录（每本书一个）

skill 标准 6 个中文目录（大纲规划/正文/一致性检查/修订记录/章节总结/项目状态）+ `推演记录/`、`导出/`
+ 系统目录 `_studio/`（manifest、settings、pipeline_state、inbox、answers、author_notes、steps、snapshots）。
路径唯一来源：`novel_writer/workspace.py::PATHS`。初始化幂等：对已有目录再次初始化只补缺失文件，
不覆盖；非空且不是本项目的目录会被拒绝。

## 设置（`_studio/settings.json`）

默认保守：`gear=G0`（手动挡）、`quality=standard`、预算有限（单次最多 3 章/60 次 LLM 调用/120 分钟）、
不豁免蓝图确认。校验见 `novel_writer/settings.py`；挡位/闸门语义见方案第 6 节（M1 起生效）。

## 测试

`python -m pytest tests -q`（113 个用例：M0 39 + M1 32 + M2 14 + M3 14 + M4 14；含入口脚本子进程测试与 Streamlit AppTest 冒烟，均使用临时数据目录，
不污染真实 `data/`）。


## M1：引擎与后台运行

### 流水线怎么走

- 步骤来自 `novel_writer/steps.py`（定义）与 `pipeline.py`（展开）。展开依赖蓝图：主要角色 = 出现在 ≥3 章 `characters_present` 中的角色；章节数 = `structure.total_chapters`。
- 流水线线性执行，**前沿**是第一个未完成的步骤。可选步骤（蓝图快进推演）在手动挡下会被跳过，不阻塞。
- 步骤状态：`pending → running → waiting_review | waiting_user | passed | failed | blocked | skipped`，另有 `stale`（上游产物变化）与 `interrupted`（上次 worker 被杀）。

### 闸门（方案 6.1–6.3）

优先级：强制检查点 > 单步覆盖 `step_overrides` > 阶段挡位 `stage_gear_overrides` > 全局挡位 `gear`。
- 强制检查点（蓝图确认、前三章确认）不能被降级为 auto/skip；只有 `checkpoints.waive_blueprint_confirm` 能豁免蓝图确认。
- 修订/推演/灵感类（category `revise`）在所有挡位下都是手动。

### 运行与控制

worker 是独立子进程，不阻塞界面。每一轮：读控制指令 → 展开流水线 → 标脏 → 取前沿 → 查闸门与预算 → 执行 → 落盘。
需要人介入时自动退出，`worker.json` 的 `reason` 写明原因：

| reason | 含义 |
|---|---|
| `completed` | 流水线全部完成 |
| `waiting_review` | 某步执行完毕，等待 approve |
| `waiting_user` | skill 回执提出了 needs_user，等待 answer |
| `manual` | 前沿是手动闸门，等待 run（step_id） |
| `budget_chapters` / `budget_calls` / `budget_time` | 预算耗尽 = **暂停**，可直接再次运行续跑 |
| `paused` / `stopped` | 收到暂停/停止（在两步之间生效，不打断正在执行的 LLM 调用） |
| `failed` / `blocked` | 步骤失败（可 rerun）/ 执行器尚未实现 |

### 命令行

```bash
cd external_projects/novel_writer
python -m novel_writer.service_cli status   <工作目录>              # 流水线视图（JSON）
python -m novel_writer.service_cli run      <工作目录> --mode until_gate   # 后台推进
python -m novel_writer.service_cli run      <工作目录> --step plan_blueprint --mode single
python -m novel_writer.service_cli approve  <工作目录> <步骤ID>
python -m novel_writer.service_cli rerun    <工作目录> <步骤ID>
python -m novel_writer.service_cli answer   <工作目录> <步骤ID> --json '{"q1": "选A"}'
python -m novel_writer.service_cli note     <工作目录> <步骤ID> --text "下一步请……"   # 作者指导，注入 prompt
python -m novel_writer.service_cli pause    <工作目录>
python -m novel_writer.service_cli stop     <工作目录>
python entrypoints/run_novel.py --workspace <工作目录> --mode single   # 前台运行一个 worker（调试）
```

### 回执契约

每个 skill 步骤结束时，执行方把 `receipt.json` 写入结果目录：`status`（`done` / `needs_user`）、`summary`、`outputs`、`needs_user`。
引擎校验回执结构与声明的产出文件；不合格时在**新的 agent** 上重试（同一次执行内最多 2 次）。
每次执行的 prompt、回执都留在 `_studio/steps/<步骤>/run_<n>/`，用量记在 `_studio/usage.jsonl`。

### 真实 LLM 冒烟（需要本机密钥）

```bash
python entrypoints/smoke_real_llm.py --title 冒烟书 --intent "一个落魄少年修炼逆袭的三章短篇"
```

只跑一次 plan_blueprint。成功的标志：输出的 JSON 里 runs 的 executed 含 `plan_blueprint` 且状态为 `waiting_review`，并且工作目录的 `大纲规划/小说蓝图.json` 存在。


## M2：界面（streamlit run app.py）

导航：

- **📚 书架**：列出所有小说，「打开」进入驾驶舱。
- **➕ 新建小说**：新建向导。填书名、计划章节数、挡位、质量档，再填创作意图（题材、主题、主角、欲望、障碍、冲突、结局、创意碎片、参考风格等）。意图保存到 `_studio/intake.json`，并注入每一步的 prompt。
- **🧭 驾驶舱**（选中小说后）分四页：
  - **总控台**：挡位与预算设置；流水线表（状态、闸门及其来源、尝试次数、说明）；▶ 运行下一步 / ▶▶ 运行到检查点 / ⏸ 暂停 / ⏹ 停止；单步操作区：批准、运行此步、重跑、答复 needs_user（问题以表单呈现）、闸门覆盖、作者指导。
  - **规划**：蓝图概览、幕结构、章节表（可编辑标题、功能、字数、张力、出场角色；行数不可增减）、主题/节奏/开篇文档。保存后，受影响的已通过步骤会提示「需重跑」。
  - **设定库**：主要角色的人设卡（可编辑）、其它人设卡、人物档案与世界圣经（只读）。
  - **线索**：伏笔数据、伏笔注册表、张力曲线（只读）。
- **🧩 Skill 库**：23 个 skill 的说明与 SKILL.md 预览（M0 已有）。

典型流程：新建（填意图）→ 进入驾驶舱 → 运行到检查点 → plan_blueprint 完成后在「规划」里看蓝图、改章节 → 回到总控台批准 → 运行到下个检查点 → 批准设计步骤 → 建档完成。

后台 worker 由界面拉起，界面只写控制文件并轮询状态。测试环境可设 `NOVEL_WRITER_NO_SPAWN=1` 禁止拉起子进程。


## M3：章节循环与写作/审校/阅读

### 单章流程（第 n 章）

| 步骤 | 执行器 | 作用 |
|---|---|---|
| `chNN.word_quota` | Python | 字数配额 = 蓝图基准 ±（上一章短缺/超出的补偿，上限基准的 20%，偏差不足 15% 不补偿） |
| `chNN.plot_tasks` | Python | 本章要埋设/兑现的伏笔（来自 plot_tracker） |
| `chNN.context_pack` | Python | 确定性组装上下文包（蓝图要求、配额、情节任务、出场人设、最近 3 章概述、角色与世界摘要、作者意图），每段有字符上限 |
| `chNN.style_hint` | Python | 风格提示与禁用词（风格指纹 + 设置） |
| `chNN.write_chapter` | skill（chapter-writer） | 写正文，产出 `正文/第NN章 标题.md` |
| `chNN.machine_check` | Python | 机器校验，结果写入 `一致性检查/第NN章_机器校验.json` |
| `chNN.review` | skill（chapter-review） | 审稿；问题条目以 🔴（硬错误）或 🟡（建议）开头 |
| `chNN.revise_loop` | Python 编排 | 有 🔴 或机器硬问题时，最多 3 轮「修改 → 复核」，每轮都是新的 agent |
| `chNN.archive` | skill（chapter-review） | 章节概述与索引 |
| `chNN.sync` / `chNN.sync_plot` | skill | 同步人物状态、伏笔状态 |
| `chNN.reader_sim` | skill | 每 5 章的读者体验模拟 |

### 修改循环与升级

- 第 1–3 轮：chapter-writer 按 🔴 清单做**最小修改**，随后 chapter-review 复核，同时重跑机器校验。
- 三轮后仍有硬问题：步骤挂起为 `waiting_user`，写入 `_studio/inbox/chNN_escalation.json`，**下一章不会开始**。
- 审校页提供两个决定：「重新核查（我已手动修改）」（不再修改，直接复核）与「接受风险继续」（写入修订记录，流程继续）。两者都要你主动点击。

### 写作台 / 审校 / 阅读

- **写作台**：选章后显示字数与配额、蓝图要求、情节任务与步骤状态、上下文包；可以直接编辑正文并保存。保存后，已通过的下游步骤（审稿、归档、同步）会标为需重跑。
- **审校**：🔴 数量、机器校验结果、审稿报告、修改记录；升级时在这里做决定。
- **阅读**：章节目录与字数，预览正文，导出全书合稿到 `导出/`。

### 命令行

升级时的决定要用 `answer`（不是 `approve`）：

```bash
python -m novel_writer.service_cli status <工作目录>
python -m novel_writer.service_cli answer <工作目录> ch02.revise_loop --json '{"decision": "接受风险继续"}'
```


## M4：幕末、修订、灵感、度量与设置

### 幕末步骤

蓝图 `structure.acts[].chapter_range` 的最后一章完成后，依次出现：深度审稿（consistency-checker）、风格幕间对比（style-guardian）、节奏复盘（pacing-architect）、主题一致性（theme-architect）、世界设定巡检（world-builder）、灵感复查（idea-catcher），以及**可选**的中层推演（plot-simulator，始终手动，不阻塞流水线）。
每 10 章还有一个全局快照（`项目状态/memory/snapshots/snapshot_chNN.json`）：人物档案、伏笔账本、截至该章的全书字数。

### 修订（L1–L4）

在审校页「发起修订」中选择级别并写明要求，系统生成一个可选的手动步骤（`rev.<id>`），在总控台或审校页点 ▶ 运行：

| 级别 | 做什么 | 下游影响 |
|---|---|---|
| L1 | revision-editor 诊断 → chapter-writer 改句段 → 复核 | 无 |
| L2 | 同上，并重写本章概述 | 概述变化使依赖它的步骤（如下一章上下文包）自动标为需重跑 |
| L3 | 同 L2，并同步人物状态与伏笔状态 | 同上，且人物/伏笔变化会传播 |
| L4 | **不改写任何文本**。先挂起要你确认；确认后，第 n 章及之后已通过的步骤标为「需重跑」，不自动执行 | 需要你之后手动重跑 |

### 灵感箱

记一条灵感 → ▶ 评估（idea-catcher 给出 整合 / 暂存 / 丢弃 的结论，整合时指出章号）。结论显示在灵感箱，不会自动改写正文。

### 度量与步骤详情

度量页汇总：每章字数与配额、🔴 数量、修改轮数、机器硬问题；技能用量与失败次数；最近的步骤记录；worker 日志尾部。
选择任一步骤可以看到最近一次执行的 prompt 与回执（保存在 `_studio/steps/<步骤>/run_<n>/`）。

### 设置矩阵

设置页的表格每行是一个步骤：挡位预设、覆盖（可选 manual / review / auto / skip）、生效闸门及来源。
阶段挡位可以按 p1 / p2 / p3 / act 分别设置。检查点（蓝图确认、前三章确认）只能更严格，不能被覆盖放松；只有「豁免蓝图确认」能让蓝图确认自动通过。
预算（章数、LLM 调用数、分钟数）与禁用词也在这里设置，保存后从下一步起生效。


## M5：挡位落地、批量推进、收件箱、手写旁路与 AI 选段辅助

### 挡位的运行范围

| 挡位 | 运行范围 | 实际停止点 |
|---|---|---|
| G0 手动 | 单步 | 每一步都要点 ▶ |
| G1 引导 | 到下个检查点 | 设计类与每章写作之后等你批准 |
| G2 半自动 | 每批 N 章 | 第 4 章起自动；达到批大小（`semi_auto_batch_chapters`）即暂停，可续跑 |
| G3 全自动 | 到预算上限或全书完成 | 第 4 章起自动；遇到设计、前三章、幕末深度审稿的审批即停 |

**前三章逐章审批**（方案 6.2）：G1 起，前三章的章节写作一律需要你批准。G2 / G3 原本会自动写前三章，M5 起会被提升为审批。这意味着 G3 在前三章每章写完都会停下，预算的章数计数是按**每次运行**累计的，所以预算主要在第 4 章起生效。

**幕末深度审稿**（`act_review`）：G3 下仍需批准。若你确认要自动，在设置矩阵里对该步骤显式覆盖为 `auto`。

优先级不变：强制检查点 > 单步覆盖 > 阶段挡位 > 挡位预设。上面两条「最低审批」规则只会把 `auto` 提升为 `review`，不会覆盖你的显式覆盖。

### 收件箱

驾驶舱的「总控台」顶部与书架顶部都会显示收件箱，汇总需要你处理的事项：

| 类型 | 含义 | 严重度 |
|---|---|---|
| 待批准 | 有步骤在等你批准 | 黄 |
| 待答复 | 某一步的 needs_user 问题需要回答 | 黄 |
| 升级人工 | 修改循环 3 轮后仍有 🔴，已停止推进 | 红 |
| 失败 / 未实现 | 步骤执行失败或执行器未实现 | 红 |
| 需重跑 | 上游变化导致的 stale 步骤（合并为一条） | 蓝 |
| 预算暂停 | 本次运行达到章数 / 调用 / 时长上限，可直接续跑 | 蓝 |

收件箱由状态文件推导，worker 每次退出时把快照写入 `_studio/inbox/index.json`。

### 手写旁路（我自己写 / 上传）

总控台的「单步操作」里，任何有产出文件的步骤都可以展开「✍ 我自己写 / 上传了产出」，然后点「标记为已通过」。系统只检查产出文件是否存在，然后把本步标为通过（记录 `source: manual`），后续流程照常运转。**内容质量由你负责**；没有声明产出的步骤（例如 `ch01.machine_check`）不能旁路。

### AI 选段辅助（润色 / 扩写 / 续写）

写作台底部的「✨ AI 辅助」：粘贴要处理的原文段落（必须与正文完全一致），选择方式，点「提交并在后台生成候选稿」。

- 候选稿写入 `正文/_drafts/assist_<id>.md`，**不会直接改正文**。
- 确认候选稿后点「采用此候选稿」：润色 / 扩写要求选段在正文中恰好出现一次才替换；续写接在选段之后。
- 采用走正文保存，下游已通过的步骤会按输入哈希标为需重跑。同一候选稿只能采用一次。

### 批量推进（advance_all_auto）

只推进挡位为 G2 / G3 的小说，每本最多推进 1 章（默认），G0 / G1 的书不会被触碰，已有 worker 运行的书会跳过。

```bash
python entrypoints/advance_all_auto.py --dry-run          # 只预览，不调用 LLM
python entrypoints/advance_all_auto.py                    # 每本最多推进 1 章
python entrypoints/advance_all_auto.py --max-chapters 2
```

**默认不接入定时任务。** `project.yaml` 只登记了入口，没有 cron 行。需要定时推进时，请在 daemon 配置里手动添加一行，并注意每次运行会产生 LLM 费用。界面的「⚡ 批量推进」按钮会在后台启动同一个入口。

### 命令行（新增）

```bash
python -m pytest tests/test_m5.py -q        # M5 测试（挡位表驱动、批量、收件箱、手写旁路、AI 辅助、界面）共 60 个
```


## M6：结局与终审、质量档位、best-of-N、导出

### 结局设计与全书终审

- **结局设计**（`design_ending`，ending-designer）：插在最后一幕的第一章之前；没有幕结构时插在最后一章之前。G1 起需要批准（设计类）。
- **全书终审**（`final_review`，consistency-checker）：所有章节与幕末步骤之后执行，报告写入 `一致性检查/全书终审报告.md`。G3 下仍需批准，与幕末深度审稿一致。

### 质量档位（草稿 / 标准 / 精品）

| 档位 | 步骤层面的差异 |
|---|---|
| 草稿 | 伏笔同步并入人物同步（每章少一次 LLM 调用）；关闭读者模拟 |
| 标准（默认） | 与 M5 相同；读者模拟每 5 章 |
| 精品 | 每章多一个「候选稿 B」与「择优」步骤（见下）；读者模拟每 3 章；每 10 章一次 8 维巡检（`patrol:<n>`，只出报告）；「巡检反馈」默认全自动：报告自动整理成整改项并注入后续写作（设置 `patrol_feedback`，可单项关闭，见审校页「巡检整改项」） |

质量档在「设置」或驾驶舱的挡位面板里修改，从下一步起生效。

### best-of-N（精品档）

每章写完后，chapter-writer 另写一版候选稿 B（`正文/_drafts/第nn章_候选B.md`）。择优步骤对 A（正文）和 B 各做一次机器校验，评分为「硬问题数 → 提示数 → 字数偏离」，**分数更低者成为正文**；平手保留 A。结果写入 `一致性检查/第nn章_择优.json`。择优只看机器指标，不评判文笔。

### 导出

阅读页可选格式导出全书合稿：Markdown、纯文本（txt）、Word（docx）、电子书（epub）。docx 与 epub 只使用标准库生成，结构最小化（无封面、无图片），输出在 `导出/` 目录，页面提供下载按钮。

命令行：`tool_api.export(workspace, fmt)`，`fmt ∈ {md, txt, docx, epub}`。

### 命令行（新增）

```bash
python -m pytest tests/test_m6.py -q        # M6 测试（15 个）
```




## 运行输出与完成提示

驾驶舱「总控台」顶部的「🔵 后台运行中」面板每 2 秒刷新一次，显示：

- 当前步骤、已用时、第几次尝试；
- 「📜 运行输出」：`_studio/run.log` 的最近 160 行。包括每步的开始与结束（✔ 通过 / ⏸ 待批准 / ❓ 待答复 / ✖ 失败）、耗时与回执摘要，以及子 agent 的工具调用与结果。日志是追加的，每次运行以「═══ 运行开始」分隔，上一次的输出会一直保留，直到你手动清理文件；
- 运行结束时：弹出一次「后台运行已结束」提示，整页刷新，并在面板上显示结束横幅（完成 / 待批准 / 失败 等）；
- 「上一次运行结果」表：每一步的状态、耗时与摘要，保存在 `_studio/last_run.json`。运行期间每完成一步就更新一次（标题显示「本次运行进度（进行中）」），整轮结束后变为最终结果。
- 非 UTF-8 的文本文件（UTF-16、带 BOM 的 UTF-8、GBK）读取时会自动转存为 UTF-8，原文件备份为 `*.原编码.bak`，运行日志里会有「🔧 已将…转存」；无法转存时步骤失败并给出详细诊断。

注意：模型自己的文字回复要等本轮结束才会完整出现（框架在轮结束时才保存会话、且非流式），实时部分主要是工具调用与结果。

## 下一步与单步运行

- **运行下一步**：按钮上方的文字写明它将运行哪一步（例如「下一步将运行：生成全书蓝图」）。点击时明确运行这一步，不再由后台临时决定，所以显示的步骤就是运行的步骤。
- **待批准 / 待答复时**：下一步就是那个等待的步骤，按钮会被禁用，并提示需要先批准或回答。之前这种情况会静默返回、什么也不做。
- **手动步骤**：点击后会运行它（提示里会注明「手动步骤」）。
- **单步选择框**：默认选中下一步（第一个未完成的步骤；待审核的步骤会排在最前，因为它阻塞流水线）。下一步变化后会自动跟随；你手动选过的值在下一步不变时保持不变。
- **流水线表**：「下一步」列用 ▶ 标出同一个步骤。

「下一步」的唯一定义在 `tool_api.next_step`，与 worker 选步骤使用同一个前沿函数。

## 失败自动重跑

步骤执行失败（`failed`）时，worker 会在同一次运行内自动重跑，**最多 3 次**（即原始执行 1 次 + 自动重跑 3 次，共 4 次）。

- 每次重跑前等待 5 秒、15 秒、30 秒，给接口限流之类的临时故障一点恢复时间。
- 每次重跑都会在运行日志里写一行「↻ 步骤 失败，N 秒后自动重跑（第 k/3 次）」，状态里记录 `auto_retries`。
- 重跑成功则正常进入后续流程（如待批准）；3 次都失败则停下，收件箱显示「已自动重跑 3 次仍失败」。
- **不重跑**的情况：执行器未实现（blocked）、需要你回答（waiting_user）、待批准（waiting_review）。
- 收到暂停或停止指令时不再重跑；本次运行的预算用尽时不再重跑（每次重跑都计入 LLM 调用次数）。

重跑次数目前是固定的 3 次，不在界面里配置。
