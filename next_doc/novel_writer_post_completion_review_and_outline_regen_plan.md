# novel_writer 完稿后评估、大纲重生成与大纲分析方案（设计，待确认后实施）

> 状态：方案已确认（2026-11-20，见第 9 节），按第 7 节分期实施，每期更新文档并打包 diff zip。**R0、R1 已完成**，其余待做。
> 依据：`next_doc/novel_writer_post_completion_revision_plan.md`、`external_projects/novel_writer/docs/revision_flow.md`、
> `docs/outline_first.md`，以及对 `revision_flow/*`、`blueprint_store.py`、`act_planning.py`、`brief.py`、`ui/post_revision.py` 的阅读。

## 1. 需求

在现有「📝 完稿修改」流程上增加三项能力：

1. **完稿自动评估**：读取全书所有章节，分析连贯性、前后矛盾等问题，给出带证据的意见；作者逐条采纳后，按现有流程生成新版本完成修改。
2. **按最新全部章节重新生成大纲**：从已写成的正文反推「实际写出来的大纲」，与原规划大纲对比，作者确认后落入新版本。
3. **以大纲为准检查正文偏离**（已确认的口径，见 4.5）：把大纲当作权威，逐章核对正文是否偏离，对偏离生成修改意见，问题并入同一问题池。

## 2. 现状（与本方案相关的部分）

| 已有 | 位置 | 本方案如何利用 |
|---|---|---|
| 版本化修改：v001 只读，修改产出新版本，head 指针，`discard`/`undo_head` | `revision_flow/versions.py` | 评估只读 head；大纲采纳产出新版本 |
| 变更 → 预览 → 确认 → 滚动改写 | `changes.py`、`executor.py` | 采纳意见＝生成 Change，**完全复用**，不另写改写器 |
| 章节反馈分诊（LLM 输出 JSON，严格校验，重试，作者采纳） | `feedback.py` | 评估的产出/校验/采纳模式照搬 |
| 后台任务 + 实时日志 + 操作日志 | `jobs.py`、`ui/post_revision.py` | 评估、大纲生成作为后台任务，沿用「同一时间只有一个任务」 |
| manifest 记录每个文件 sha256 | `versions.py` | 作为章节事实卡缓存键，免费得到「章节是否变了」 |
| 蓝图小文件存储与严格读取 | `blueprint_store.py` | 大纲落盘只由 Python 写，LLM 不手写文件头格式 |
| 全书终审（consistency-checker 五维，流水线步骤） | `steps.final_review_step` | 保持不动；新评估是它之后的、带证据且可闭环修改的深度评估 |

**缺口**：现有流程只能「作者已知哪里要改」才能发起修改（直接写变更，或对某一章留意见）。没有任何东西替作者**发现**问题，
也没有「正文已经偏离大纲，该如何更新大纲」的手段。

## 3. 设计原则

- **不侵入写作流水线**：沿用 `revision_flow` 的约定——不新增 `steps.py` 步骤，不写 `pipeline_state.json`，不被 worker / 挡位 / G5 托管调用。
  新模块仍放在 `revision_flow/` 下，只读写 `_versions/` 与 `_revision/`。
- **保守的默认值**：评估与大纲生成都是作者手动点击触发；任何「自动采纳」「自动改写」「完稿后自动评估」都不做（如以后要做，另开默认关闭的开关）。
  主观性强的维度（节奏冗余、文风漂移）默认关闭。
- **先估算、后执行**：评估开始前给出预计调用次数（`review-estimate`）。按作者决定**不设调用上限**（`review_max_llm_calls=0`），估算只作提示，不拦截；作者仍可手动设一个上限。
- **LLM 只负责判断，Python 负责落盘与校验**：LLM 输出 JSON 到指定文件，Python 严格校验、不合格带错误重试；引文用子串核对防编造。
- **证据先行**：每条问题必须带可定位的章节引文；引文核对不通过的问题标记「证据未核实」，默认折叠，不参与批量采纳。
- **矛盾由作者裁决以谁为准**：两处矛盾时给出「以 A 为准改 B」「以 B 为准改 A」两个选项，**不预选**。
- **原稿永不改写**：评估只读；大纲采纳也是派生新版本。
- **skills 原样不改**：评估/大纲各步骤复用现有 skill（见第 6 节），任务约束通过 prompt 给出。

## 4. 总体架构

```
          head 版本（只读）
              │
   ┌──────────▼───────────┐
   │ A. 章节事实卡（LLM，逐章）│  缓存键 = 章节文件 sha256；章节没变就不重读
   └──────────┬───────────┘
              │ 共享，评估与大纲生成都依赖它
   ┌──────────┼──────────────────────────┐
   ▼          ▼                          ▼
 B. 确定性检查   C. LLM 复核（仲裁/衔接/全书）   D. 反推大纲（候选，不落盘到版本）
 （零 LLM）                                   │
   └──────┬───┘                              ▼
          ▼                           E. 大纲分析（结构/线索/弧线/偏差）
     问题池 findings ◄────────────────────────┘
          │ 作者逐条采纳（矛盾须选「以谁为准」）
          ▼
   Change（kind=chapter/setting）──► 现有：预览 → 确认 → 滚动改写 → 新版本
          │                                            │
          └────────── 改后增量复评（只重读变动章节）◄────┘

   候选大纲 ──作者确认──► 派生「仅大纲」新版本（正文不动）
```

### 4.1 章节事实卡（共用底座）

每章一次 LLM 调用，输出结构化 JSON，Python 校验后存 `_revision/review/cards/chNNN.<sha8>.json`：

```
{chapter, title,
 time_marker,            # 章内时间标记/相对时间（原文表述）
 location_start, location_end,
 characters: [{name, role_in_chapter, state_end}],   # state_end：本章末状态（伤、情绪、持有物、所知信息）
 events: [str],                                       # 关键事件，按发生顺序
 facts_stated: [{subject, attribute, value, quote}],  # 本章新确立/重申的事实，quote 必须是原文子串
 foreshadow: [{id_or_desc, action: plant|advance|resolve, quote}],
 opening_state, ending_state,                         # 供相邻章节衔接核对
 open_questions: [str]}
```

- `quote` 一律经子串核对（空白/标点归一化），核对失败的条目丢弃并记入卡片的 `dropped`，不进入后续比较。
- 人物名通过 `角色索引` 的名字与别名归一，未登记人物单独列出（也是一类检查结果）。
- 这是整个方案成本最大的一步（N 章 = N 次调用），缓存使「改几章后复评」只需重读变动章节。

### 4.2 评估（需求 1）

**B. 确定性检查（零 LLM，基于事实卡与账本）**

| 检查 | 方法 |
|---|---|
| 已离场人物再出场 | 事实卡中 `state_end` 含死亡/离开等标记 + 之后章节出现该人物 → 候选 |
| 同一主体同一属性取值冲突 | `facts_stated` 按 (subject, attribute) 分组，value 不同 → 候选矛盾对 |
| 伏笔顺序异常 | 回收早于种下、长期未回收、账本与事实卡动作不一致 |
| 时间标记倒退 | `time_marker` 序列与章序不单调（需 LLM 解析不了的保留原文，仅提示） |
| 未登记人物 | 事实卡人物不在角色索引（含别名） |
| 相邻章衔接断裂 | 上章 `location_end`/`ending_state` 与下章 `location_start`/`opening_state` 明显不一致 → 候选 |

**C. LLM 复核（只给相关片段，不给全书）**

1. **候选仲裁**：把 B 产生的候选连同两处原文片段（每处约前后 300 字）交给 LLM，判定 `confirmed / false_positive / uncertain` 与理由。
2. **衔接审阅**：按窗口（默认 5 章一组、相邻窗口重叠 1 章）审阅事实卡的 `ending_state → opening_state` 转场、人物动机连续性。
3. **全书维度审阅**：输入全书章节总结 + 事实卡摘要，按已启用维度给出跨章问题（人物弧线停滞/突变、主线悬空、信息披露顺序等）。

**问题（finding）结构**

```
{id, source: "deterministic"|"llm"|"outline_analysis",
 category: contradiction|continuity|timeline|character_logic|foreshadow|pacing|redundancy|style_drift|outline_*,
 severity: 严重|中|轻,
 chapters: [..],
 evidence: [{chapter, quote, verified: bool}],
 description, why_it_matters,
 options: [{label, instruction, target_chapters, change_draft}],   # 矛盾类 ≥2 个选项；其它 1 个；另有固定选项「不处理」
 status: open|adopted|rejected|applied|resolved|still_open|stale,
 base_version, applied_version, notes}
```

存 `_revision/review/<review_id>/findings.json`；同时渲染 `report.md`（按严重度与类别分组，供阅读与导出）。

### 4.3 采纳与修改（需求 1 的闭环）

- 作者在界面逐条处理：**采纳（选一个选项，可改写 instruction）/ 驳回 / 暂不处理**。矛盾类必须选定选项才能采纳。
- 采纳的问题批量转为 Change（`kind: chapter` 或 `setting`；设定类沿用「作者选择时间语义」规则），然后进入**现有**预览 → 确认 → 执行。
  同一章被多条问题命中时，executor 已支持一章适用多条变更。
- 小改动：Change 增加可选字段 `evidence`（引文与定位），写入改写 prompt，让改写模型知道要改的具体句子与「以谁为准」。
  字段可选、向后兼容，旧变更不受影响。
- **闭环**：执行完成后，被采纳问题的状态变 `applied`；作者可点「增量复评」，只对变动章节及其相邻章重新生成事实卡并重跑 B/C，
  原问题消失 → `resolved`，仍在 → `still_open`，新引入的问题作为新条目列出。复评是为了发现「改一处引出新矛盾」。

### 4.4 大纲重生成（需求 2）

「重新生成大纲」这里定义为**反推实际大纲**：根据 head 版本的全部章节，得到「书实际写成了什么样」的大纲。

- 输入：事实卡 + 章节总结（已有）+ 当前蓝图。
- **确定性部分由 Python 直接产出**：章号、章名（取自文件名）、实际字数（作为 `word_budget`）、`characters_present`（事实卡人物，经角色索引归一）。
- **LLM 只产出需要判断的部分**：每章 `function`（一句话功能）、`chapter_type`、`pov`、`tension_level`，以及整书的幕划分（`structure.acts` 的 `chapter_range`/幕名）和主线/支线概要。
- 蓝图里其它字段（`meta`、未识别字段）**原样保留**，只替换上述部分。
- 产出是**候选大纲**，存 `_revision/outline/<id>/`（`candidate.json` + 与当前蓝图的差异），**不直接进入版本**。差异包括：章节功能变化、人物出场增删、幕边界移动、实际字数与预算偏差。
- 作者确认（可整份，或按章勾选；也可在差异页就地改某章的 function）→ 派生**仅大纲新版本**：
  通过 `blueprint_store` 的写入与严格校验落盘（`_meta.md`、`第NNN章.md`、`_index.md`、`章节大纲.md`），在 `修订记录/蓝图变更日志.md` 追加一条记录；正文一字不动。
- 与「大纲先行」书的关系：**本期不改 `章节简述/`**（简述是写作前的约束，不是事后记录）；候选大纲页会提示「本书为大纲先行，简述未同步」。

### 4.5 以大纲为准的偏离检查（需求 3，已确认口径）

**方向**：大纲是权威，正文是被检查对象；对偏离给出「改正文」的修改意见。**不做**后续章节规划。

- **权威大纲**由作者选：当前版本的蓝图（默认），或已采纳的重生成大纲（4.4 采纳后它就是当前蓝图）。
- 逐章核对（对象是事实卡，需要时才取原文片段）：
  1. 大纲该章的 `function`、出场人物、（大纲先行的书）简述里的关键事件，在事实卡 `events`/`characters` 中是否体现；
  2. 正文里出现了大纲没有的重要事件或人物（多写）；
  3. 大纲要求的事件缺失或被改写（少写 / 走样）；
  4. 章序与信息披露顺序是否与大纲一致，幕边界、章节字数是否严重偏离预算。
- 每处偏离作为一条问题，带大纲条目原文 + 正文引文（子串核对）+ 修改意见（`改正文` 的 instruction，转 Change 走现有流程）。
- 只有两个处理选项：**改正文以符合大纲** / **不处理**。「以正文为准更新大纲」不放在这里——那是 4.4 重生成大纲的职责（作者看到偏离后可以选去重生成大纲）。
- 判断「是否偏离」交给 LLM，数字类（字数、章序、人物出场增删）由 Python 先算好再交给 LLM 解读。
- 默认手动触发；不自动改任何内容。

## 5. 数据布局（新增，均在 `_revision/` 下）

```
_revision/
├── review/
│   ├── cards/chNNN.<sha8>.json      事实卡缓存（键＝章节 sha256 前 8 位）
│   └── rev_<id>/                    一次评估：run.json（状态、基于版本、调用数、日志）、
│                                    candidates.json（B 的候选）、findings.json、report.md
└── outline/
    └── ol_<id>/                     一次大纲生成：candidate.json、diff.json、analysis.json
```

评估绑定 `base_version`；head 变化后，旧评估标记「基于旧版本」，仍可查看，问题状态显示 `stale`，提示复评。

## 6. 复用的 skill（均不修改）

| 步骤 | skill | 说明 |
|---|---|---|
| 事实卡 | `memory-manager` | 本就负责章节总结与状态抽取 |
| 候选仲裁 / 衔接审阅 | `consistency-checker` | 五维一致性 |
| 全书维度审阅 | `revision-editor`（主动巡检）、`reader-simulator` | 巡检八维度含情感分布、人物出场、伏笔激活、弧线 |
| 反推大纲 | `novel-planner` | 大纲/结构规划 |
| 大纲偏离检查 | `consistency-checker`、`novel-planner` | 对照大纲核对正文、给出修改意见 |

调用签名与现有一致：`invoker(skill=, prompt=, export_dir=, step_id=)`；输出写 JSON 到 `export_dir`，Python 校验。

## 7. 分期

| 期 | 内容 | 是否调用 LLM | 交付与验收 |
|---|---|---|---|
| **R0** | 章节事实卡：schema、校验、引文核对、缓存；评估设置项；成本估算；`review` 骨架与 CLI `review-estimate` | 卡片生成用 | 同一章不变则命中缓存；坏 JSON 重试；伪造引文被丢弃；估算与实际调用数一致 |
| **R1** | 确定性检查 B + 问题池 findings 与 `report.md` | 否 | 每类检查有正反用例；零调用数断言 |
| **R2** | LLM 复核 C（仲裁/衔接/全书）+ 「🔎 评估」分页（后台任务、日志、筛选、证据查看） | 是 | 桩执行器全流程；引文核对不过的问题被折叠；超上限暂停可续 |
| **R3** | 采纳 → Change（矛盾选项、`evidence` 字段）→ 现有预览/执行；增量复评与状态流转 | 复评用 | 采纳后产出的 Change 能直接走通现有预览；复评发现新问题；原稿不被触碰 |
| **R4** | 大纲重生成：候选、差异、按章勾选、仅大纲新版本、变更日志 | 是 | 落盘后 `blueprint_store` 严格读取通过；正文哈希不变；可 `discard` |
| **R5** | 以大纲为准的偏离检查（多写/少写/走样/顺序），生成「改正文」意见并入问题池 | 是 | 数值由 Python 算；偏离对照用例；只产出改正文选项 |
| **R6** | 收尾：CLI 全子命令、`docs/revision_flow.md` 与本方案状态更新、评估报告导出 | 否 | 文档与代码一致；全量回归 |

每期按一贯做法：先写/更新测试（桩执行器，不调用真实 LLM）、实现、跑全量回归、更新文档、打包 diff zip（保持目录结构，可直接覆盖）。

## 8. 设置项（`_revision/settings.json`，新增；均有保守默认）

| 键 | 默认 | 含义 |
|---|---|---|
| `review_max_llm_calls` | 0 | 单次评估最多调用次数；**0 = 不设上限**（已确认不设上限） |
| `review_dimensions` | `[contradiction, continuity, timeline, character_logic, foreshadow]` | 启用的维度；`pacing`、`redundancy`、`style_drift` 需手动开启 |
| `review_window` | 5 | 衔接审阅窗口章数（重叠 1 章） |
| `review_snippet_chars` | 300 | 仲裁时每处原文片段的前后长度 |

## 9. 已确认的决定（2026-11-20）

1. **大纲分析口径**：以大纲为准检查正文是否偏离，对偏离生成修改意见（见 4.5）。
2. **不做规划后续章节**；「重新生成大纲」只做反推实际大纲（4.4）。
3. **仅完稿后可用**：必须已有 v001（`resolve_version` 强制检查）。
4. **大纲先行书的 `章节简述/` 不动**。
5. **不设评估调用上限**（默认 `review_max_llm_calls=0`，估算只作提示）。
6. 默认维度：节奏冗余、文风漂移默认关闭（未提出异议，按方案执行）。

## 10. 实施状态

| 期 | 状态 | 备注 |
|---|---|---|
| R0 | 完成 | `revision_flow/review_cards.py`、设置项、CLI `review-estimate`/`review-cards`、`tests/test_revision_review_cards.py`（13 例；人工破坏 6 处逻辑均转红） |
| R1 | 完成 | `revision_flow/review_checks.py`、CLI `review-checks`/`review-findings`、`tests/test_revision_review_checks.py`（13 例；人工破坏 10 处逻辑均转红）。偏离原计划：伏笔账本与事实卡的交叉核对暂缓，自由文本伏笔不做顺序检查（无法可靠匹配） |
| R2–R6 | 待做 | |

## 11. 风险与已知限制

- **成本随章数线性增长**：事实卡是大头；缓存只在章节没变时生效。几百章的书需调高上限，并建议分批评估（后续可加章节范围）。
- **漏报**：靠事实卡里被抽出的事实做比较，没被抽成「事实」的矛盾（隐含的、跨多章推理才暴露的）会漏。LLM 全书审阅只能部分弥补。
- **误报**：仲裁与引文核对能压低但不能清零，所以所有问题都由作者裁决，不自动修改。
- **滚动改写的漂移**沿用现有已知限制：改后靠增量复评兜底，仍需作者抽查。
- **未在真实 LLM 下验证**：测试使用桩执行器；提示词遵循、事实卡质量、仲裁准确率需要在本机用真实模型跑一本书确认。
- **界面**：新分页只能做静态检查与 AppTest 冒烟，浏览器级刷新需本机点一遍。
- **Windows**：路径与只读处理沿用现有工具函数，只在 Linux 上测试过。
