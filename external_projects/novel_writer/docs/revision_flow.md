# 完稿后修改流程（revision_flow）

> 设计依据：`next_doc/novel_writer_post_completion_revision_plan.md`（仓库根目录 next_doc/）。
> 本流程只在全书终审通过、书已写完之后使用，**独立于写作流水线**：不新增流水线步骤，不写
> `pipeline_state.json`，不被 worker、挡位、G5 自动重跑调用。现有章节修订、构思修订、巡检逻辑不变。

## 核心约定

- **原稿只读**：完稿快照 `v001` 只读，每个文件有 sha256 清单（`_versions/manifests/v001.json`），`verify` 可随时校验。
- **修改出新版本**：每次执行先从父版本整体复制出 `vNNN`，所有改写只写入新目录。定稿（校验 + 只读）后才成为 head。
  未成功的运行留下未定稿的派生目录，可续跑或丢弃，不影响 head。
- **两种时间语义，由作者逐次选择**：
  - `retcon` 追溯式：全书一直如此，涉及旧设定的章节全部改为新设定。
  - `from_chapter` 从第 N 章起：N 之前的章节不动；N 之后涉及设定的章节改写。N 之前若出现新设定关键词，会给出警告。
- **先预览，后执行**：变更 → 预览（受影响章节、命中词、警告、预计调用数）→ 作者确认 → 执行。未确认的预览不能执行。

## 数据布局

```
<小说目录>/
├── _versions/
│   ├── index.json            {"head": "v002", "versions": [{id, parent, kind, created_at, note}]}
│   ├── manifests/vNNN.json   文件哈希清单（baseline / derived）
│   ├── vNNN/                 完整内容副本（大纲规划、正文、章节总结、一致性检查、修订记录；另含 项目状态/workspace_config.json）
│   └── exports/vNNN/         该版本的导出文件（全书合稿、最终稿）
└── _revision/
    ├── settings.json         可配置上限（见下）
    ├── changes/chg_*.json    变更
    ├── preview/prv_*.json    预览（confirmed 标志）
    ├── runs/run_*.json       运行记录（状态、已完成章节、LLM 调用数、日志、派生物建议）
    ├── runs/run_*.lock       运行期间的归属锁（记录持有进程号；进程结束后残留，用于识别中断）
    ├── runs/<run>/chNNN/attemptKK/out/   每次尝试的执行器产出（便于排查）
    ├── review/cards/chNNN.<sha8>.json   R0 章节事实卡缓存（键＝章节文件 sha256 前 8 位 + schema 版本）；review/work/ 为每次尝试的产出
    ├── review/rev_<版本>/findings.json、report.md   R1/R2 问题池与可读报告（按版本一份；确定性与模型来源的问题每次重建，已做的处理保留；候选的仲裁结论随问题记录）
    ├── review/rev_<版本>/llm_state.json   R2 缓存：仲裁结论（按候选编号）、衔接窗口与全书判定（按章节 sha 键）；续跑从这里断点继续
    ├── review/rev_<版本>/outline_check_state.json   R5 缓存：大纲偏离的批次判定（键含章节 sha 与大纲内容）
    ├── review/rev_<版本>/export.md        R6 评估报告导出（Markdown，只读整理问题池，不改它）
    ├── outline/ol_<id>/candidate.json     R4 候选大纲（基于某版本；状态 candidate / adopted / discarded；不进入版本）
    ├── triage/tri_*.json     章节反馈分诊记录
    ├── jobs/<任务>.log       实时进度日志（逐行，带时间戳）；<任务>.status.json 为 running / done / failed
    └── jobs/ops.log          操作日志：界面每个按钮的开始、结果（含摘要）或失败原因
```

评估与大纲相关的问题统一记在 `review/rev_<版本>/findings.json`，按来源区分：`deterministic`（规则候选，R1）、`llm`（模型复核，R2）、`outline`（大纲偏离，R5）。
各来源互不误删：确定性检查只重算 `deterministic`，LLM 复核只重建 `llm`，大纲偏离检查只重建 `outline`。问题的处理状态按问题编号跨重算保留。

## 可配置上限（`_revision/settings.json`，当前通过 CLI 修改，界面后续接入）

| 键 | 默认 | 含义 |
|---|---|---|
| `max_chapters_per_run` | 20 | 单次运行最多改写的章数，超出则暂停，续跑继续 |
| `max_llm_calls_per_run` | 60 | 单次运行最多 LLM 调用次数（含重试） |
| `max_attempts_per_chapter` | 2 | 单章最多尝试次数（首次 + 带反馈重试） |
| `length_ratio_min` / `length_ratio_max` | 0.7 / 1.3 | 改写后字数与原字数之比的允许范围 |
| `stop_on_failure` | true | 某章失败即停止，后续章节保持原文 |
| `review_max_llm_calls` | 0 | 全书评估单次最多 LLM 调用数；**0 = 不设上限**（默认） |
| `review_dimensions` | 人设不符、逻辑硬伤、矛盾、衔接、时间线、人物逻辑、伏笔 | 启用的评估维度。`character_profile`（人设不符）与 `logic`（逻辑硬伤）由 LLM 对照人物档案与原文判断，是评估的重点；`pacing`（节奏）、`redundancy`（冗余）、`style_drift`（文风漂移）主观、误报多，默认关闭；`rule_legacy`（规则式的「死亡后再出场」「相邻章地点衔接」）误判多，默认关闭，需手动开启 |
| `review_window` | 5 | 衔接审阅窗口章数（相邻窗口重叠 1 章，R2 使用） |
| `review_snippet_chars` | 300 | 仲裁时每处原文片段的前后长度（R2 使用） |
| `review_scope` | hits | 语义判定的候选范围：`hits` 只判定命中定位词的章节；`all` 全书逐章判定（成本高，能找到纯指代的章节） |

## 命令行

```
python entrypoints/post_revision.py --novel <dir> baseline            # P0 创建 v001（终审需已通过）
python entrypoints/post_revision.py --novel <dir> status
python entrypoints/post_revision.py --novel <dir> settings --set max_chapters_per_run=10
python entrypoints/post_revision.py --novel <dir> change-add --json change.json   # P1
python entrypoints/post_revision.py --novel <dir> preview --base v001 --changes chg_xxx [--review]
python entrypoints/post_revision.py --novel <dir> exclude --preview prv_xxx --chapter 3 [--include]  # 确认前调整章节范围
python entrypoints/post_revision.py --novel <dir> confirm --preview prv_xxx
python entrypoints/post_revision.py --novel <dir> run --preview prv_xxx           # P2（调用 LLM）
python entrypoints/post_revision.py --novel <dir> resume --run run_xxx            # 因上限暂停或停止后续跑
python entrypoints/post_revision.py --novel <dir> stop --run run_xxx              # 请求停止（当前章节结束后生效）
python entrypoints/post_revision.py --novel <dir> triage --base v002 --chapter 12 --feedback "..."  # P3（调用 LLM）
python entrypoints/post_revision.py --novel <dir> accept --triage tri_xxx --mode from_chapter --from 12
python entrypoints/post_revision.py --novel <dir> diff --a v001 --b v002          # P4
python entrypoints/post_revision.py --novel <dir> export --version v002           # P4
python entrypoints/post_revision.py --novel <dir> verify --version v001
python entrypoints/post_revision.py --novel <dir> discard --version v003          # P5 撤销（仅叶子版本）
python entrypoints/post_revision.py --novel <dir> review-estimate [--version v00N]  # R0 评估成本估算（不调用 LLM）
python entrypoints/post_revision.py --novel <dir> review-cards [--version v00N] [--chapters 1 2] [--force]  # R0 生成章节事实卡（调用 LLM）
python entrypoints/post_revision.py --novel <dir> review-checks [--version v00N]   # R1 确定性检查，更新问题池与报告（不调用 LLM）
python entrypoints/post_revision.py --novel <dir> review-findings [--version v00N] [--set f_xxx --status rejected]  # R1 查看 / 标记问题
python entrypoints/post_revision.py --novel <dir> review-llm [--version v00N]   # R2 LLM 复核：候选仲裁、衔接审阅、全书维度审阅（调用 LLM，达上限暂停，再次运行续跑）
python entrypoints/post_revision.py --novel <dir> review-options --finding f_xxx [--version v00N]   # R3 某问题可选的处理方式（矛盾类为两个方向）
python entrypoints/post_revision.py --novel <dir> adopt --finding f_xxx [--option 0|1] [--instruction "…"] [--chapters 2 3] [--allow-unverified]  # R3 采纳为 chapter 变更
python entrypoints/post_revision.py --novel <dir> adopt-batch --findings f_a f_b [--choose f_x=1]   # R3 批量采纳（跳过矛盾未选方向、证据未核实）
python entrypoints/post_revision.py --novel <dir> review-increment   # R3 增量复评：基于 head 重算，与父版本对照（调用 LLM）
python entrypoints/post_revision.py --novel <dir> outline-candidate [--version v00N]   # R4 反推实际大纲，生成候选（调用 LLM，不进入版本）
python entrypoints/post_revision.py --novel <dir> outline-list                         # R4 列出候选
python entrypoints/post_revision.py --novel <dir> outline-diff --id ol_xxx             # R4 候选与当前蓝图的差异
python entrypoints/post_revision.py --novel <dir> outline-adopt --id ol_xxx [--chapters 1 3] [--set 3:function=...] [--no-structure]  # R4 采纳为仅大纲的新版本
python entrypoints/post_revision.py --novel <dir> outline-discard --id ol_xxx          # R4 放弃候选
python entrypoints/post_revision.py --novel <dir> review-pipeline [--auto-fix | --no-auto-fix]   # 评估全流程：失败自动重试、步骤自动衔接；--auto-fix 本次开启自动修改
python entrypoints/post_revision.py --novel <dir> outline-check [--version v00N] [--llm]   # R5 以大纲为准的偏离检查（--llm 调用模型；否则只做确定性比对）
```

变更 JSON 示例（设定变更）：

```json
{"kind": "setting", "title": "玉佩换成银钥匙", "before": "玉佩", "after": "银钥匙",
 "reason": "情节需要", "terms": ["玉佩"], "after_terms": ["银钥匙"],
 "mode": "from_chapter", "effective_from": 2}
```

章节反馈变更（`kind: "chapter"`）通常由 `triage` + `accept` 生成，也可手写：`{"kind": "chapter", "title": "...", "instruction": "压缩开头", "chapters": [1]}`。

## 各阶段实现状态

| 阶段 | 状态 | 模块 | 说明 |
|---|---|---|---|
| P0 完稿版本基线 | 完成 | `versions.py`、`common.py`、`config.py` | 终审门禁、只读快照、哈希清单、校验 |
| P1 变更与预览 | 完成 | `changes.py` | 两种时间语义、受影响章节（子串匹配）、预览与确认 |
| P1+ LLM 语义判定 | 完成 | `changes.py` | `preview --review`：对每个候选章节判定是否真涉及变更（识别指代、排除同字误报）；判定为不涉及的默认排除，确认前可用 `exclude` 调整；判定失败的章节保留并警告 |
| P1+ 候选发现 | 完成 | `changes.py` | `review_scope=all` 时，设定变更对全书（from_chapter 时为生效章及之后）逐章判定，找回纯指代的章节 |
| P2 滚动改写 | 完成 | `executor.py` | 章序滚动、单章校验与重试、失败隔离、上限暂停与续跑、请求停止（章节间生效）；只改写未被排除的章节 |
| P2+ 中断恢复 | 完成 | `executor.py` | 运行期间写 pid 锁；`recover_interrupted` 把「running 但持有进程已不存在」的运行标记为 interrupted，可续跑（界面进入运行页时自动执行） |
| P3 章节反馈分诊 | 完成 | `feedback.py` | LLM 分诊为 chapter / setting；作者确认后才生成变更 |
| P4 派生物与对比导出 | 完成（重跑需手动） | `derived.py` | 派生物建议清单、版本逐文件差异、按版本导出 |
| P5 撤销与丢弃 | 完成 | `versions.py` | `discard`（仅叶子版本，不能丢弃 v001）、`undo_head` |
| R0 章节事实卡 | 完成 | `review_cards.py` | 全书评估与大纲偏离检查的共用底座。每章一次调用提取时间/地点/人物末状态/事件/新确立的事实/伏笔动作/开头结尾状态；只读版本，只写 `_revision/review/`；缓存命中则不再调用；带 quote 的条目必须能在章节原文中核对到（忽略空白与标点），核对不过的丢弃并记入 `dropped`；单章失败不阻塞其它章。仅在已有 v001 后可用。设计与后续分期见 `next_doc/novel_writer_post_completion_review_and_outline_regen_plan.md` |
| R1 确定性检查与问题池 | 完成 | `review_checks.py` | 零 LLM，只读事实卡。检查项：同主体同属性取值不同、「第N天」时间倒退、F+数字伏笔的先回收后种下/无种下/未回收、多章出现的未登记人物；另有两项旧的规则式检查（已死亡人物再出场、相邻章地点衔接）**默认关闭**（维度 `rule_legacy`），因为它们误判多——同一人物死后在回忆、闪回、同名者中出现都会被误报，地点是否相同对判断衔接意义有限。结果都是**候选**（`needs_adjudication`），由 R2 的 LLM 对照原文仲裁；各检查按 `review_dimensions` 开关，「未纳入评估的章节」不受开关影响。问题 id 稳定，重算保留已做的处理。**未覆盖**：伏笔账本与事实卡的交叉核对、自由文本伏笔的顺序 |
| R2 LLM 复核 | 完成 | `review_llm.py`、`ui/post_revision.py`「🔎 评估」 | 四个阶段，均只读版本：**C4 人设与逻辑审阅**（默认启用）：每 5 章一批，提示中附本章出场人物的档案（`大纲规划/人物设定/角色`）、本章事件与结尾、前后邻章的结尾/开头，要求模型找出人物言行与档案相悖（`character_profile`，人设不符）与情节硬伤（`logic`：已丢失或已消耗之物无解释再现、前文事实被无交代推翻、不可能的时空出现、因果断裂）。无人物档案时整段跳过。每条问题都必须带原文引文并经子串核对。**C1 候选仲裁**：每条规则候选取两处原文片段（前后 `review_snippet_chars` 字，无引文的用定位词取上下文），判定 确认 / 误报 / 无法判定 并给理由；**C2 衔接审阅**：按 `review_window` 章一窗（相邻窗口重叠 1 章）审阅转场与人物连续性，问题须带原文引文；**C3 全书维度审阅**：一次调用，只针对已启用的节奏 / 冗余 / 文风（及人物、连续性）维度。所有引文经子串核对，核对不过的证据 `verified=False`，问题 `evidence_ok=False`，界面默认折叠（不参与 R3 的批量采纳）。调用达到 `review_max_llm_calls` 即暂停，已完成单元缓存在 `llm_state.json`，续跑不重复调用；缓存键含章节 sha，章节变化后只重判受影响的窗口与全书，候选消失时清理其仲裁结论。界面「🔎 评估」页：生成事实卡 / 确定性检查 / LLM 复核（后台任务与实时日志），问题池按严重度、类别、来源、状态筛选，展示仲裁结论与引文核实标记，可标记处理状态与备注 |
| R3 采纳与增量复评 | 完成 | `review_adopt.py`、「🔎 评估」页 | **采纳**：问题 → `chapter` 变更（`origin` 记录来源问题、版本与所选方向；`evidence` 为核实过的原文，写入改写提示的「问题定位」）。矛盾类必须先选「以 A 为准改 B / 以 B 为准改 A」，不预选；证据未核实的问题单条采纳需明确允许，批量采纳一律跳过；仲裁为误报的问题不能采纳。采纳后走现有预览 → 确认 → 执行，原稿不动。执行完成后问题标为 `applied`（`sync_applied`）。**增量复评**：基于 head 重生成事实卡（未改章节命中缓存）、确定性检查与 LLM 复核；父版本的仲裁结论沿用未改章节的部分；问题编号不含章节 sha，改动后同一问题仍能对上：父版本已应用的问题消失 → `resolved`，仍在 → `still_open`，新出现的标 `new_since`；作者的驳回随问题带入新版本。**未做**：「stale」只是设计上的状态，界面未单独显示旧版本评估的失效；设定类变更未由评估产生（评估的修正都是改章节对齐） |
| R4 大纲重生成 | 完成 | `outline_regen.py`、「📚 大纲」页 | 以 head 的正文为准反推「实际写成的大纲」。确定性部分由 Python 产出：章号、章名（保留蓝图章名）、实际字数（取整百作 `word_budget`）、出场人物（事实卡人物经角色档案归一）。LLM 只判断功能（`function`）、类型、视角、张力，以及全书幕划分与主线、支线概要（每批 20 章，另一次调用做幕划分）。结果是候选，写入 `_revision/outline/ol_<id>/`，不进入版本。**逐章 diff**（字段变化、未写出的章节、结构变化）；作者可按章勾选、就地改功能描述、选择是否采用幕划分。采纳时派生「仅大纲」新版本：从 head 复制，只写 `大纲规划/蓝图`（经 `blueprint_store` 写入并严格读回比对），正文哈希不变，追加 `修订记录/蓝图变更日志.md`，定稿并设为 head；可 `discard` 撤销。采纳前若 head 已变化则拒绝，要求重新生成。未写出正文的蓝图章节原样保留。**未做**：章节大纲.md 不重写（由规划步骤的 LLM 生成，重写会丢内容，变更日志注明可能不一致）；章节简述不同步（大纲先行书会提示） |
| R5 以大纲为准的偏离检查 | 完成 | `outline_check.py`、「📚 大纲」页、问题池 `source="outline"`、`category="outline_deviation"` | 权威大纲 = 检查版本的蓝图（采纳过的重生成大纲即当前蓝图）。**确定性比对**（不调用模型）：正文有而大纲无的章节（多写）、大纲有而正文无的章节（少写）、字数超出预算 1.5 倍或不足 0.6 倍（轻）、出场人物与大纲不一致（轻）。这些是规则比对，统一标轻，且**不提供采纳选项**（只是提示）。**LLM 偏离判断**（每批 20 章，对照大纲条目、章节简述的必须事件与事实卡）：多写、少写、走样、顺序、幕边界。多写 / 走样 / 顺序 / 幕边界必须带正文引文（子串核对，不过则 `evidence_ok=False` 并默认折叠）；少写可无引文。只提供「以大纲为准修改正文」一种处理，经 R3 采纳为 chapter 变更；未写出的章节与多写的章节没有可改的正文，分别交给大纲重生成与正文续写处理。批次结果缓存在 `outline_check_state.json`，键含章节 sha 与大纲内容，改动后只重判受影响批次。问题状态跨重算保留；R1 / R2 的重算不会误删大纲偏离 |
| R6 收尾 | 完成 | `review_report.py`、CLI、`docs/`、本文档 | 评估报告导出：概况（纳入 / 未纳入章节、丢弃的事实条目、按来源与状态统计、证据未核实数）、改动追踪（已采纳 / 已应用 / 已解决 / 仍存在）、按严重度分组的全部问题（证据与核实标记、模型仲裁结论、变更关联、增量复评新出现、作者备注）。只读整理问题池，写入 `review/rev_<版本>/export.md`；界面「🔎 评估」页提供导出与下载。CLI 子命令与文档已对齐，并有测试逐个检查解析与非 LLM 流程 |
| 自动流程 | 完成 | `review_pipeline.py`、设置页、「🔎 评估」页「一键评估全流程」、CLI `review-pipeline` | **失败自动重试**：每个评估步骤（事实卡 / 确定性检查 / LLM 复核 / 大纲偏离 / 报告）最多 `auto_retry_attempts` 次，等待 `auto_retry_backoff_seconds` 起按 1、2、4… 倍递增；FlowError（前置条件不满足）不重试。模型调用单元内部最多 3 次，执行器异常同样退避。步骤内部的单元失败由缓存兜底，重试只做失败的部分。**自动衔接**：步骤之间不需要点按钮；事实卡失败才停止，其它步骤失败只记录并继续，最后汇报。**自动修改**（`auto_fix_enabled`，默认关闭，因为会改写正文）：每轮挑出证据已核实、非矛盾、未驳回、未判误报、严重度不低于 `auto_fix_min_severity` 的 LLM 复核与大纲偏离问题，以证据章节为目标采纳 → 生成预览（模型判定不涉及的章节默认排除）→ 确认 → 改写（失败按续跑自动继续）→ 增量复评 → 大纲偏离检查；最多 `auto_fix_rounds` 轮，某轮没有可处理问题即停止。矛盾类、确定性规则候选从不自动采纳。状态写入 `pipeline/state.json`，结束原因与各轮结果显示在评估页 |
| 实时进度 | 完成 | `jobs.py`、`ui/post_revision.py` | 生成预览、开始改写、续跑、分诊都在后台线程执行。页面顶部只有一个「当前任务」面板（同一时间只允许一个任务），每 1.5 秒刷新，日志按时间顺序排列、最新在最下（面板显示最近 40 行，完整日志在下方展开项）；页面操作的实时日志与之一样统一放在页面最下方，显示状态、开始时间、已输出行数；包含每章开始判定、每次尝试、判定与校验结果，以及 agent 的逐行输出 |
| 版本导出与阅读 | 完成 | `derived.py`、`ui/reading.py` | 📖 阅读页顶部可选择工作稿或任一版本（v001 只读完稿、修改版本）；选中版本后预览该版本正文，并可分别导出全书合稿（md / txt / docx / epub）和最终稿。导出文件统一放在 `_versions/exports/<版本号>/`，版本目录不留产物；页面列出该版本已导出的文件供下载。版本快照包含 `项目状态/workspace_config.json`（书名） |
| 版本差异 | 完成 | `derived.py`、`ui/post_revision.py` | 「查看差异」输出 git 风格的 unified diff（`diff --git` 头、`@@` 块、`+`/`-` 行，带 diff 语法高亮）；先列出变化文件及增删行数，可选单个文件或全部文件；新增/删除文件有 `new file` / `deleted file` 标记 |
| 操作日志 | 完成 | `ui/post_revision.py` | 每个按钮都经过统一的 `_act`，开始 / 完成（含结果摘要）/ 失败都写入 `jobs/ops.log`，页面底部可展开查看；后台任务另有各自的实时日志 |
| 界面 | 完成 | `ui/post_revision.py`（所有页面操作在后台线程执行，运行日志统一显示在页面最下方：后台任务日志、当前操作的实时日志、操作日志） | 导航「📝 完稿修改」：版本（基线、校验、差异、导出、撤销）/ 变更与预览（排除、确认）/ 运行（后台执行、停止、续跑、派生物建议与日志）/ 章节反馈（分诊、采纳）/ 评估（R2–R3，来源筛选含「大纲偏离」）/ 大纲（R4–R5）/ 设置 |

## 已知限制

- **评估与大纲的模型判断未在真实 LLM 下验证**：R2–R5 的全部测试使用桩执行器。仲裁准确率、偏离判断的召回与误报、事实卡质量需要在本机用一本真书跑一遍后再依赖其结论。
- **候选与问题依赖事实卡**：事实卡只记录被抽取出的事实，隐含的、跨多章推理才暴露的矛盾会漏报；LLM 全书审阅只能部分弥补。
- **增量复评与大纲采纳都派生新版本**：每次采纳都会复制整个版本目录，几百章的书会占用较多磁盘空间（版本可 discard 回收）。
- **章节大纲.md 与章节简述不随大纲重生成同步**：前者由规划步骤生成，后者是写作前约束；变更日志会注明。
- **旧版本的评估不会自动标为失效**：`stale` 状态只在设计中出现，界面没有单独显示。
- **派生物不自动重跑**：人物索引、伏笔账本、全书终审报告只给出建议，由作者决定何时重跑。
- **默认候选仍是子串匹配**：`review_scope=hits` 时只判定命中定位词的章节，纯指代章节需要切到 `all`（成本为全书章数的 LLM 调用）。
- **语义判定依赖 LLM 的判断**：判定结果会写入预览，作者应核对被排除与保留的章节理由（`review.reason`）。
- **执行器未在真实 LLM 下验证**：测试使用桩执行器（按提示中的路径读取原文并替换），调用约定与 `llm_convert` 一致，
  但实际写作质量与提示遵循情况需本机用真实模型跑一次。
- **派生物不自动重跑**：一致性、人物索引、伏笔账本、全书终审报告只给出 `derived_marks` 建议，由作者决定何时重跑。
- **停止是协作式的**：`stop` 写入标记，当前章节的 LLM 调用结束后才停下，不会中断正在进行的调用。
- **滚动改写会累积漂移**：后面章节只看到前一章的最新文本，远处章节的一致性依赖一致性检查兜底。
- **页面操作与后台线程**：所有点击操作（保存、校验、采纳、导出、确定性检查等）都在后台线程执行，同一时间只允许一个页面操作或后台任务，运行中按钮会提示等待。
- **后台线程**：界面的运行在 Streamlit 进程内的守护线程中执行，服务重启会中断它。中断后运行记录会在下次打开运行页时被标记为「已中断」并可续跑；锁的判定依据是进程号是否存在，同机多进程适用，跨机器共享目录不适用。
- **界面刷新依赖 Streamlit 的 fragment 轮询**：单元测试无法验证浏览器里的实时刷新，需要在本机实际点一次确认。
- **实时日志的粒度**：每一行 agent 输出都会实时写入日志；但模型最终的文字回复只有在一次调用结束后才完整可见（框架是非流式的）。
- **中断点的粒度**：恢复时从未完成的章节重新开始，已写入的章节不会重做，但正在进行的那一章的 LLM 调用会丢失，需要重新调用一次。
- **Windows 下的只读与删除**：已加权限修正，但只在 Linux 上测试过。
- 流程完全不读写流水线状态；若作者在完稿后又回去用流水线重跑章节，那是独立于本流程的操作，两者不会自动同步。

## 测试

`tests/test_revision_profile_review.py`（8 例）：无档案则跳过、人设与逻辑问题的引文核对、档案与邻章上下文进入提示、批次缓存、维度开关、估算、规则式死亡 / 地点检查默认关闭且可手动开启。

`tests/test_revision_pipeline.py`（9 例，自动流程）：步骤自动衔接且不擅自采纳、单元失败后步骤自动重试恢复、单步失败不阻断后续、事实卡失败停止、自动修改的采纳 → 预览 → 改写 → 增量复评闭环与终止条件、矛盾类从不自动采纳、严重度门槛、设置边界、界面渲染。

`tests/test_revision_cli_and_report.py`（6 例，R6）：每个 CLI 子命令都能解析 `--help`；非 LLM 的完整流程（确定性检查、估算、问题列表、选项、导出）在真实工作目录上运行；业务错误以退出码 2 返回；评估报告的状态、仲裁、备注与改动追踪；未评估时拒绝导出；导出不改问题池。

`tests/test_revision_outline_check.py`（12 例，R5）：确定性比对（多写 / 少写 / 字数 / 人物双向）、规则偏离不提供采纳选项、LLM 偏离的批次范围、少写可无引文、伪造引文不过核对、「改正文」选项与未写出章节不可改、坏输出重试、批次缓存、作者状态跨重算保留、R1 / R2 重算不误删大纲偏离、采纳生成 chapter 变更、缺卡拒绝、界面冒烟。

`tests/test_revision_outline_regen.py`（14 例，R4）：候选由确定性与判断两部分组成（章名保留、字数取整百、人物归一）、候选不改版本、事实卡缺失拒绝、坏输出重试、幕必须覆盖全书、20 章分批、未写出章节保留、采纳派生仅大纲版本且正文哈希不变、按章勾选与就地改写、未勾选不得改、head 变化拒绝、撤销、diff 纯函数、界面冒烟。

`tests/test_revision_review_adopt.py`（12 例，R3）：矛盾类两个方向且须选定、采纳生成的变更带 origin 与核实过的 evidence、未核实证据门禁、批量采纳跳过规则、误报不可采纳、采纳 → 现有预览 → 执行（原稿不动、改写提示含定位与方向）、`applied` 同步、增量复评的已解决 / 仍存在 / 新出现、仲裁结论沿用、驳回跨版本保留、门禁与界面冒烟。

`tests/test_revision_review_llm.py`（17 例，R2）：窗口切分（相邻重叠一章）、问题与仲裁结论的严格校验、证据上下文取法（引文 → 定位词 → 章首）、端到端（仲裁、窗口、全书、只读版本）、伪造引文折叠而不丢弃、坏 JSON 重试一次、单单元异常隔离、调用上限暂停与续跑（续跑不重复仲裁）、章节改动后只重判受影响的窗口与全书、候选消失时清理仲裁缓存、作者处理状态跨重算保留、维度开关控制各阶段、门禁与估算、评估页 AppTest 冒烟（渲染、折叠提示、无问题提示）。全部使用桩执行器。

`tests/test_revision_review_checks.py`（13 例，R1）：六项检查的正反用例、问题池端到端与报告、维度开关、缺卡如实报告、重算保留状态与其它来源问题、成因消失后问题消失、状态校验、不触碰版本。

`tests/test_revision_review_cards.py`（13 例，R0）：完稿门禁、设置默认与校验、引文归一化核对、结构校验、缓存命中与失效、伪造引文被丢弃、坏 JSON 重试、单章失败隔离、调用上限暂停续跑、默认不设上限、不触碰版本与流水线状态。

`tests/test_revision_flow.py`（36 例）与 `tests/test_revision_flow_ui.py`（12 例，AppTest 冒烟）覆盖：基线门禁与只读、配置校验、变更校验、两种语义的章节集合与警告、
预览确认门禁、retcon / from_chapter 执行、原稿不被触碰、校验失败重试、失败即停、上限暂停与续跑、
基于 head 的二次修改与撤销、分诊（设定 / 章内 / 坏 JSON 重试 / 失败不可采纳）、版本差异、按版本导出、语义判定与排除、全书候选、停止与续跑、模拟崩溃后的中断识别与恢复、存活与死亡进程的锁判定。界面测试覆盖渲染、表单保存、后台生成预览与日志写入（使用不调用 LLM 的路径）；日志流与 agent 输出的映射由 jobs 单元测试覆盖。
