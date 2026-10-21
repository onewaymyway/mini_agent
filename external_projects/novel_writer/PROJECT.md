# novel_writer — 小说生成外部项目：23 个创作 skill 流水线 + Streamlit 操作界面

> 遵循 `next_doc/external_projects_workspace_plan.md` 的外部项目标准结构：自包含、可独立于
> daemon 运行，可整体移动后用 `mini-agent projects register <路径>` 重新注册。
> 完整设计方案：`next_doc/novel_writer_external_project_plan.md`（已确认，按 M0→M5 分期实施）。
> 使用说明见 [`docs/README.md`](./docs/README.md)。

## 目标

用 skills.zip 里的 23 个小说创作 skill，在 mini_agent 之上搭一个可手动/半自动/全自动写长篇小说的工作台：

1. 每本小说一个工作目录，所有内容（正文、设定、检查报告、系统状态、运行留痕）都在其中。
2. 创作类 skill 由 `mini_agent.workflow.WorkflowRunner` 的 `skill_agent` 步骤执行（M1 起），
   **本项目不实现任何 LLM 调用/重试逻辑**；确定性工作（目录、路径、计数、上下文组装、状态机）由 Python 完成。
3. Streamlit 界面提供书架、总控台、规划、设定库、写作台、审校等页面，支持逐步骤的运行/确认/重跑/编辑。
4. 控制挡位（G0 手动 … G3 全自动）+ 质量档位（草稿/标准/精品）+ 逐步骤闸门覆盖。默认保守：G0 + 标准档。

## 里程碑进度

| 期 | 状态 | 内容 |
|---|---|---|
| M0 脚手架 | **已完成（2026-10-17）** | 骨架、23 个 skill 原样落位、workspace.py（路径表/初始化/恢复/体检）、settings、书架、skill 目录、create_novel/list_novels/health 入口、简版界面（书架/新建/Skill 库）、39 个测试 |
| M1 引擎内核 | **已完成（2026-10-17，待确认）** | 步骤定义/流水线展开、闸门（挡位/覆盖/强制检查点）、状态机与脏标记、孤儿自愈、worker 循环（暂停/停止/预算/审批/needs_user/重试）、skill_agent 运行（`FrameworkInvoker` + `workflows/skill_step.yaml`）、tool_api 运行接口、service_cli、冒烟脚本；32 个桩测试 |
| M2 规划阶段 + UI 第一批 | **已完成（2026-10-17，待确认）** | 新建向导（意图表单）、书架（打开）、驾驶舱四分页：总控台（挡位/预算/流水线表/运行与审批/答复/闸门覆盖/作者指导）、规划（幕结构、章节表编辑、蓝图变更日志）、设定库（人设卡编辑、档案/世界圣经只读）、线索（伏笔/张力只读）；AppTest 冒烟 + 桩全链路 14 个用例 |
| M3 章节循环 + UI 第二批 | **已完成（2026-10-17，待确认）** | 章节 python 步骤 W1–W4/W9/W11（配额与偏差补偿、情节任务、确定性上下文包、风格提示、机器校验、修改循环最多 3 轮、升级人工不推进下一章）；sync_plot（伏笔同步）；写作台、审校、阅读三页；连续三章桩全链路与硬错误升级验收；99 个用例 |
| M4 幕末 / 修订 / 灵感 / 度量 / 设置矩阵 | **已完成（2026-10-17，待确认）** | 幕末步骤（深度审稿、风格对比、节奏、主题、世界巡检、灵感复查、可选推演）与每 10 章快照；修订 L1–L4（L3 同步下游、L4 需确认并标记受影响步骤）；灵感箱（记录 → 评估 → 整合/暂存/丢弃）；度量页与步骤详情（prompt/回执）；设置页的步骤×闸门矩阵与检查点/预算/禁用词；13 个新用例 |
| M5 自动模式完善 | **已完成（2026-10-17，待确认）** | 挡位预设落地（前三章逐章审批、G3 幕末深度审稿保留审批、各挡位运行范围）；预算按运行计数，批量入口 `advance_all_auto`（仅 G2/G3，每本至多 N 章，默认不接 cron）；收件箱（状态推导 + 快照 + 驾驶舱/书架展示）；手写旁路（产出存在即标通过）；AI 选段辅助（润色/扩写/续写，候选稿确认后采用）；新增 `tests/test_m5.py` 60 个用例，全量 173 个通过 |
| M6 质量增强 | **已完成（2026-10-17，待确认）** | 结局设计（design_ending，进入末幕前）与全书终审（final_review）接入流水线；质量档位的步骤差异（草稿档合并伏笔同步并关闭读者模拟，精品档每 3 章读者模拟 + 每 10 章 8 维巡检）；精品档 best-of-N（第二稿 + 机器校验择优）；导出 txt / docx / epub（仅标准库）；新增 `tests/test_m6.py` 15 个用例，全量 188 个通过 |

## 已知限制 / 待验证事项

- M0 不调用任何 LLM；真实 LLM 端到端未验证（M1 起提供桩测试 + `smoke_real_llm.py` 供本机验证）。
- 界面只有 M0 简版三页；Streamlit 用 `AppTest` 做页面冒烟，仍需人工在浏览器里点一遍。
- skills 原文里的路径写法不一致（`project/summaries`、`novel_blueprint.json`、裸 `idea_bank.json`/`simulations/`）：
  不改原文，统一以 `workspace.py::PATHS` 为准并在每步 prompt 注入（M1）。
- 字数统计按"非空白字符数"，面向中文创作；英文按字符计偏大。
- **M1 未在真实 LLM 下验证**：`FrameworkInvoker` 的调用链（改写 skill_name / result_file / 输入 task_prompt）已对照 world_simulator 的用法与 `WorkflowStore` 解析做过核对，但没有密钥无法端到端跑通。请本机运行 `entrypoints/smoke_real_llm.py`。
- **章节循环未展开 W5–W7**（场景架构、对话、推演）：属于 M3/M4 范围中的可选步骤，目前不进入流水线。
- **上下文包没有关键词/TF-IDF 检索**：目前只按固定段落与字符上限组装（方案 7.3 的检索部分留待后续）。
- **机器校验的范围**：字数、禁用词、与前三章的段落重复、超长段落已实现；角色名白名单与伏笔 id 存在性检查未实现。
- **修改循环的预算**：一次修改循环内最多 6 次 LLM 调用（3 轮写作 + 3 轮复核），预算检查只在步骤之间进行，不会在循环中途打断。
- **预算的计数范围（M5）**：章数与调用数上限按**每次 worker 运行**累计，worker 退出即重置。由于前三章逐章审批，G3 的章数预算主要在第 4 章起生效。
- **质量档位只落地了一部分（M6）**：已有的差异是 草稿档（伏笔同步并入人物同步、关闭读者模拟）、精品档（best-of-N、读者模拟每 3 章、每 10 章巡检）。**未实现**：方案 6.4 中的「写后风格检查 / 每 5 章词频」、标准档的「幕末 plot-simulator」、scene-architect 与 dialogue-crafter 的自动触发、精品档「转折章前 plot-simulator」。
- **best-of-N 只有两稿**：精品档的 B 稿由 chapter-writer 另写一遍，择优只看机器校验（硬问题数、提示数、字数偏离），**不由 LLM 评判文笔**。N > 2 未实现。
- **导出是最小结构**：docx 无封面、无目录样式、无图片；epub 为 EPUB 3 的单层目录（每章一页），未做样式表与封面。均未经过真实阅读器验证。
- **收件箱没有外部通知渠道**：快照写入 `_studio/inbox/index.json`，但不推送到 IM / 邮件；渠道未在方案中定义。
- **手写旁路只检查产出存在**：不校验内容质量，也不检查字数或格式。
- **AI 选段辅助**：不做 best-of-N；候选稿质量完全取决于所用模型；采用要求选段在正文中唯一匹配（润色/扩写）。
- **实时输出的粒度受框架限制**：子 agent 的工具调用与结果会实时出现；模型的文字回复只在本轮结束后才能完整看到（框架在轮结束时才落盘会话，且非流式运行）。
- **批量推进的成本**：`advance_all_auto` 每次运行会真实调用 LLM（每本至多 1 章），在启用 cron 前请先用 `--dry-run` 预览，并确认预算设置。
- **结局设计与终审未做**：ending-designer（design_ending）与 final_review 属于方案 4.2 的收尾步骤，本期未加入流水线（归入 M6 或后续）。
- **幕末的风格幕间对比**：只调用 style-guardian，没有区分「有意调整」与「有害漂移」的结构化结果，结论以报告文本呈现。
- **修订与灵感是按需步骤**：它们以可选手动步骤出现，只能从总控台或审校/灵感箱页运行，不会自动排队。
- **章号位数**：仍固定两位（`rel()` 的 nn），超过 99 章需要在后续版本补充。
- **并发限制**：UI/CLI 对 `pipeline_state.json` 的写入与 worker 同时进行时，同进程内有锁，跨进程靠 worker 每轮重新读盘收敛；极端情况下（worker 执行中同一步被人同时批准/重跑）以后写者为准。
- **章节号位数**：`rel()` 的 `nn` 固定两位；超过 99 章的书需要在 M3 引入 `chapter_number_width`（方案基线 40 章，暂不影响）。
- **M2 界面未在真实浏览器里点过**：Streamlit AppTest 只能覆盖渲染与控件回调，请本机 `streamlit run app.py` 手动走一遍向导→总控台→规划。
- **规划页只能改已有章节**：不能增删章节行（需要重新生成蓝图）；编辑会把受影响的已通过步骤标为 stale，由你决定何时重跑。
- **线索页只读**：伏笔的手动登记与兑现标记放在 M4。
- **daemon 入口**：`run_novel` 需要 `--workspace` 参数，**未**登记到 `project.yaml`（daemon 不会传参）。批量入口 `advance_all_auto` 已在 M5 登记，**没有** cron 行（默认关闭）。

## 人物档案小文件化（第四阶段）

- 布局：`大纲规划/人物设定/角色/{id}.md` 每个角色一个文件（文件头 `key: JSON` 逐行保存结构字段，正文由系统生成）；`角色索引.md` 由系统生成，写作时只读它与本章出场角色的文件。
- 章后人物更新（精品档与草稿档的 `sync`）：LLM **直接编辑本章出场角色的文件**，不再整体重写人物档案；随后的 `characters_refresh` 步骤确定性校验、重生成正文与索引，内容无变化不写盘。
- 写作上下文只带本章出场角色的摘要（`brief_for`），不再整本放入。
- 旧版 `characters.json` 迁移：`tool_api.migrate_characters`。合法则确定性迁移并回读比对；损坏则 `use_llm=True` 时由 `ledger-convert` 直接转换，结果需 `confirm=True` 才提交；旧文件改名为 `.bak`。
- 未迁移的项目：人物相关步骤会明确报错，提示先迁移，不会静默当作空档案。
- 尚未小文件化：`behavior_index.json`、蓝图、世界圣经（仍为整体 JSON，后续阶段处理）。
- 人物档案的 mtime 只跟随索引（`contamination` 的全书共享状态检查以索引为准）。

## 蓝图拆分（第五阶段）

- 布局：`大纲规划/蓝图/_meta.md`（除 chapters 外的顶层字段）、`第NNN章.md`（每章一个文件，文件头存全部章节字段）、`_index.md`（自动生成的索引，末行带全书内容指纹，任何字段变化都会让下游过期）。
- `plan_blueprint` 仍整本生成，但产出写到草稿 `_draft.json`（由产出校验 `blueprint` 即 `blueprint_problems` 检查）；随后确定性步骤 `blueprint_split` 拆分。规划通过后，下游步骤依赖 `blueprint_split`。
- 读取统一走 `blueprint_store.load_for_ws`，返回与旧版 `小说蓝图.json` 完全相同结构的字典。作者在规划页的章节表编辑会同时写入拆分文件和草稿，因此之后的拆分不会覆盖它；只有重新运行 `plan_blueprint` 才会整本替换。
- 迁移：`tool_api.migrate_blueprint`。合法则确定性拆分并回读比对；损坏则 `use_llm=True` 时由 `ledger-convert` 直接转换，需 `confirm=True` 才提交。迁移同时写出 `_draft.json` 副本，使 `plan_blueprint` 不会被误判为缺失而整本重跑。旧文件改名为 `.bak`。
- 未迁移的项目：读取会明确报错，提示先迁移。
- 共用的 `key: JSON` 编解码已抽到 `kvfile.py`，伏笔、人物、蓝图共用。

## 目录结构

```
novel_writer/
├── project.yaml          daemon 契约（create_novel / list_novels / health）
├── PROJECT.md            本文件
├── app.py                Streamlit 薄入口；页面在 ui/
├── ui/                   listen（听书，M7）、shelf（书架）、wizard（新建向导）、cockpit（驾驶舱）、planning / writing / review / reading / ideas / metrics / settings_page / codex / threads（分页）、components、skills_page
├── novel_writer/         config / fsutil / settings / workspace / shelf / skills_catalog / tool_api
│                         M1: steps / gates / state / pipeline / validators / runner / worker / service_cli
│                         M2: intake / views
│                         M3: chapter_ops（章节 python 步骤与修改循环）
│                         M4: ondemand（修订 / 灵感请求）
│                         M5: inbox（收件箱）、batch（批量推进）；ondemand 增加 assist（AI 选段辅助）
├── workflows/            skill_step.yaml（M1：通用单步 skill_agent 工作流）
├── skills/               23 个 skill（原样，未修改）
├── entrypoints/          create_novel / list_novels / health（+ _common）；M1: run_novel（worker）、smoke_real_llm；M5: advance_all_auto
├── docs/  tests/  data/(novels 与 shelf.json 不进版本库)  config/  reports/
```

## 运行与维护

- 本地：`python entrypoints/health.py`；`streamlit run app.py`；`python -m pytest tests -q`。
- 数据根可用环境变量 `NOVEL_WRITER_DATA_DIR` 改位置（测试即用它隔离）。
- 注册：`mini-agent projects register external_projects/novel_writer`；`mini-agent projects status novel_writer`。

## 变更记录

- 2026-10-21：修复旧项目升级后 `ch01.plot_merge` 报「伏笔增量缺失或损坏」。根因：旧版本的伏笔同步直接改写整本账本，既没有 `伏笔增量/` 目录，也没有 `plot_merge` 步骤记录；升级后 plot_merge 作为新步骤被当作「待运行」，自动挡从第 1 章开始排队，而旧章节根本没有增量可合并（与数据迁移本身无关，迁移只负责把账本改成小文件）。处理：`state.adopt_legacy_steps` 新增 `_adopt_legacy_plot_merge`，当某章的伏笔同步步骤已通过（标准/精品档 `sync_plot`，草稿档 `sync`）、本章增量文件不存在、plot_merge 无记录或只有「增量缺失」类失败记录时，把该章 plot_merge 认领为已通过。新流程里同步通过必有增量文件，故不会误认领；账本损坏等其它失败不会被吞掉。该逻辑在每轮循环与流水线视图刷新时自动执行，无需用户操作。新增 `tests/test_legacy_plot_merge_adoption.py`（8 例）。
- 2026-10-20：第三阶段（部分）。①记忆快照瘦身：快照不再整份复制人物档案与伏笔账本，只记录章节字数、人物数量、伏笔总数与各状态计数（此前快照体积随章数平方增长）。已确认没有技能或界面读取快照的完整内容。②草稿档伏笔同步统一：草稿档的 sync 步骤改为只输出伏笔增量 plot_delta，并与精品档共用 plot_merge 步骤（抽成 `_plot_merge_step`），不再整体重写账本。新增 `tests/test_phase3_slim.py`（2 例），更新 `tests/test_m6.py` 中草稿档用例的断言。
  - 未完成（需先确认方案）：人物档案 characters.json 拆分与蓝图 blueprint 拆分。两者的同步步骤同样每章整体重写整个文件，需要先设计与伏笔一致的增量与合并方式，再做存储拆分。
- 2026-10-20：损坏旧账本改由 LLM 直接转换（替代此前的逐条 JSON 修复方案，已撤销）。
  - 健康的旧 JSON：确定性迁移，不调用 LLM，自动提交。
  - 损坏的旧 JSON：`tool_api.migrate_plot_ledger(use_llm=True)` 调用技能 `ledger-convert`，LLM 直接读取损坏原文，按目标布局写出 `伏笔/F*.md` 与 `_applied/` 小文件。Python 用同一套严格解析器读回校验（字段、状态枚举、章号、id 与文件名一致），不合格则把具体错误反馈给 LLM 重写，最多 3 次（`novel_writer/llm_convert.py` 的通用重试框架，后续阶段复用）。
  - 原文原因不明的疑似遗漏（原文 id 数量多于转换结果）会作为提示写入复核报告。
  - LLM 转换的结果一律需要复核：生成「伏笔迁移报告.md」，confirm=True 才提交；未启用 LLM 时返回 needs_llm，不做任何改动。旧文件不删除，提交后改名 .bak。
  - 已删除：逐条 JSON 修复模块 `json_repair.py` 及其技能 `json-repair`。
  - 约定（后续阶段）：任何损坏的旧文件（人物档案、蓝图、快照等）迁移，都走「LLM 直接转换 → 严格校验 → 重试 → 复核后提交」这一流程，不再做 JSON 修复。
  - 新增 `tests/test_llm_convert.py`（8 例）；`tests/test_plot_store.py` 中损坏用例改为「需要 LLM 转换、原文件不动」。
- 2026-10-20：损坏旧账本的抢救与修复（通用规则，适用于伏笔账本及后续所有 JSON→Markdown 迁移）。
  - 分层抢救（`novel_writer/json_repair.py`）：严格解析 → 确定性清洗（代码围栏、尾逗号）→ 按数组条目拆分逐条解析。只有无法解析的条目才进入修复，其余条目保持原样；每条都标记来源（strict / lenient / llm）。
  - 修复：待修复条目交给技能 `json-repair`（`skills/json-repair/`），每条单独调用一次；修复结果必须通过结构校验才被接受；修复器异常不中断迁移。
  - 隔离：修复后仍不合格、或 id 重复的条目，原文保存到 `伏笔/_quarantine/`，不进入账本。
  - 提交门槛：只有确定性清洗（或无问题）时自动提交。出现 LLM 修复、隔离、撤销记录丢失或结构截断时，只生成「伏笔迁移报告.md」，需 confirm=True 才提交。旧文件始终不删除，提交后改名 .bak。
  - 接口：`tool_api.migrate_plot_ledger(ws, dry_run, confirm, use_llm)`。
  - 后续阶段约定：任何新的 JSON→Markdown 迁移都必须经过同一套抢救与复核门槛；读取时损坏应报告具体文件，不静默默认。
  - 新增 `tests/test_json_repair.py`（18 例）；`tests/test_plot_store.py` 中损坏旧文件的用例改为「需复核、原文件不动」。
  - 注意：LLM 修复的内容未经人工核对前不应视为可靠，报告中会逐条列出。
- 2026-10-20：伏笔账本小文件化（M9 第二阶段）。①存储：`novel_writer/plot_store.py`，账本拆为目录 `大纲规划/线索设定/伏笔/`：每条伏笔一个 `F*.md`（文件头 `key: JSON值` 逐行保存结构化字段，正文由系统生成），每章增量记录放在 `_applied/`，`_index.md` 为自动生成的紧凑索引（sync_plot 只读索引，输入不再随全书增长）。写入只改动变化的文件；损坏时点名具体文件。②流水线：register_plot 输出 `plot_register.json`（一次性注册结果），新增 `plot_import` 步骤转为小文件账本；plot_import 发现账本已存在则跳过，绝不覆盖已有推进记录；合并 `plot_merge` 直接写小文件存储。③迁移：`tool_api.migrate_plot_ledger(ws, dry_run)`（旧 JSON → 小文件）：先写临时目录并回读比对，一致才落盘；旧文件改名 `.bak`；同时生成 `plot_register.json` 副本，避免 register_plot 被误判为缺失而重跑。未迁移的项目会被明确提示先迁移，不会被静默改写。④读取统一经 `_ledger_or_raise`（损坏则报错，不静默为空）。新增 `tests/test_plot_store.py`（13 例），`tests/test_plot_delta.py` 增补。
  - 已知限制：a) 草稿档 `sync` 步骤仍整体重写账本（尚未纳入）；b) 伏笔正文由系统生成，手改正文会被覆盖，结构化字段请改文件头；c) 人物档案 characters.json、蓝图、快照等其他 JSON 尚未拆分，按同样模式在后续阶段处理；d) 迁移后输出路径改变，已完成步骤的部分下游可能需要一次性重跑。
- 2026-10-20：伏笔增量（M9 第一阶段）。精品档 `sync_plot` 不再整体重写伏笔账本，改为只输出本章增量 `伏笔增量/第N章_伏笔增量.json`（planted/advanced/resolved）；新增 Python 步骤 `plot_merge` 确定性合并到账本，内容无实质变化时不写盘，因此下游步骤不会因无变化而过期。增量应用前会撤销该章上一次的效果，重跑同一章幂等。损坏的账本抛出明确错误，不再静默当作空账本。下游依赖改为 `plot_merge`。新增 `novel_writer/plot_delta.py` 与 `tests/test_plot_delta.py`（23 例）。已知未解决：①`sync_plot` 的输入仍包含整本账本，提示词仍随全书增长（第二阶段改为按需读取）；②草稿档的 `sync` 步骤仍整体重写账本（第二阶段统一处理）；③账本仍为 JSON（第二阶段迁移为 Markdown）。
- 2026-10-20：听书预排队（方案 A）。播放当前段时，把后面 2 段（`AHEAD`）提前交给浏览器语音队列，段落切换不再等待合成启动，停顿基本消失。队列用代次号（gen）管理：跳转、停止、重试、改语速/音色都会开启新代次，旧队列残留事件被忽略。出错重试仍从出错段开始。语速/音色变化时，播放中会从当前段按新参数重排队列。模拟验证脚本：`tests/js/listen_queue_sim.js`（预排队、连续播放、停止）与 `listen_queue_sim_retry.js`（出错后从该段重试），需要 Node.js 运行：`node tests/js/listen_queue_sim_retry.js`。注：模拟的是事件顺序，真实语音引擎的延迟需在 Edge 与 Chrome 中实际验证。
- 2026-10-20：听书每段最少字数。新增 `MIN_CHARS = 60`（约 12 秒朗读）：正文中短于该值的片段与相邻片段合并，合并后不超过 `MAX_CHARS = 180`；章节标题仍独立成段。只合并不丢字；两侧都无法合并时保留短段。新增 2 个测试。
- 2026-10-20：驾驶舱流水线表格自动滚动到当前步骤。st.dataframe 不支持程序化滚动，改用自定义组件 `ui/step_table/`（样式与主题同听书播放器）。滚动目标：运行中的步骤，没有则为下一步；只在数据变化后重绘，且只滚动表格容器本身，不影响外层页面。新增 tests/test_step_table.py（2 例）。
- 2026-10-20：修复听书进度被重置到第一章与选择框报错。原因：①保存的章节暂时不在可听列表（正文被清空/改写）时，回退到第一章，随后的回传把进度覆盖；②读完全书时位置回到开头并回传；③播放器切章后直接修改已渲染的选择框，Streamlit 报 StreamlitAPIException 并中断运行。修复：回退改为不晚于保存章节的最近一章（`reading_state.resolve_chapter`）；读完全书停在末段；切章改为记下待同步章节，下一次运行在选择框渲染前应用。新增 3 个测试。
- 2026-10-20：结构化产出内容校验扩展（M8）。①蓝图（plan_blueprint）：chapters 须为非空数组，每条章号须能规范为唯一整数（接受 "1"、"第1章"），word_budget 须为非负整数；②伏笔账本（register_plot、ch*.sync_plot）：须含非空的 foreshadowing/threads/items 数组，每条须有 id，章号字段须可规范为整数。不合格在产出步骤内拦下，带具体问题让 agent 重写，不再静默失效（如 chapter_entry 找不到条目导致字数配额为 0）。合格时写回规范化结果。注：chapter_tasks、quota_file 由 Python 处理器生成，不经 agent，故不需要内容校验，未纳入。测试桩（tests/fakes.py）为蓝图、伏笔账本写入合法占位内容；两处直接写伏笔账本的测试改用合法结构（保留原测试意图）。新增 tests/test_structured_output_checks.py（19 例）。尚未覆盖：characters.json、world_bible.json（下游读取较宽松，待需要时再加）。
- 2026-10-20：择优报告内容校验（M8）。问题根因：技能步骤只检查产出文件是否存在，不检查内容；择优报告 winner 非法（如 "C"、缺失）时，步骤仍被判为通过，直到「择优应用」才报错。修复：①`validators.py` 新增产出内容校验注册表（`OUTPUT_CHECKS`），步骤通过 `extra={"output_check": ...}` 声明；②择优报告校验 JSON 结构与 winner（接受 a/b、候选稿、正文等写法并规范为 A/B，合格后写回规范化结果；不猜测无法识别的值）；③运行器在产出存在但内容不合格时，记入步骤日志并把具体问题作为提示让 agent 重写，同一次执行最多 3 次（原为 2 次）；④「择优应用」步骤使用同一套规范化，失败信息带上具体原因。新增 `tests/test_best_pick_report_check.py`（12 例）；`tests/test_best_pick_llm.py` 中对应用步骤失败位置的断言改为择优步骤本身。尚未覆盖：chapter_tasks、quota_file、plot_tracker 等其他下游结构化产出，待逐项声明内容校验。
- 2026-10-20：听书播放中断的错误处理。播放器不调用外部 API，朗读依赖浏览器语音（Edge/Chrome 在线语音依赖网络），断开往往是语音合成报错或朗读卡住，此前出错时静默停止。现在：①出错时显示原因提示（网络、语音服务不可用、音频设备忙、朗读卡住等）；②同一段自动指数退避重试（1s、2s、4s…最多 5 次）；③连续失败后进入「错误」状态，显示说明，点「↻ 重试」从本段继续，或用上一段/下一段跳过；④看门狗处理「不结束也不报错」的卡住、标签页导致的系统暂停（自动恢复）。浏览器不允许朗读（not-allowed）时不重试，直接提示。
- 2026-10-20：修复听书章节错位。播放器改为始终加载全书可听章节（不再只加载起始章之后的部分），进度计数与百分比按全书计算；「当前章节」选择框随自动连播同步到实际章节；用户主动改选择框时才从所选章首重新连播（位置保存为章首，播放器重新挂载）。进度文字改为「可听章节 k/N」，避免与真实章节号混淆。
- 2026-10-20：听书进度行增加全书百分比（当前段落序号 / 总段数，保留一位小数）。
- 2026-10-20：听书播放器样式与 Streamlit 对齐。颜色、字体、圆角、按钮与输入框样式改为读取 Streamlit 传来的主题（背景、次级背景、文字、主色、字体），并随浅色/深色切换同步更新；不再硬编码颜色。主题变化只更新样式，不重建播放器，不打断朗读。
- 2026-10-20：听书界面修正与段落跳转。①修复听书区域被裁剪：播放器高度改为 ResizeObserver 随内容同步。②播放器显示当前段落原文；新增上一段/下一段、输入本章段落号直接跳转、输入文字查找并跳到含该文字的下一段（到末尾回绕）。空闲时跳转只移动位置，播放或暂停中跳转后从新位置开始朗读；每次跳转都会回传并保存位置。
- 2026-10-20：听书进度精确到段落。每本书各自的 `_studio/reading_state.json` 记录上次收听的章节与章内段落（`listen_segment`，0 为标题），下次从该段继续。播放中每 5 段、以及暂停、切章、改语速/音色、读完全书、关闭页面时回传位置；「停止」改为保留位置（不再回到开头）。续播仅当保存的章节与当前起始章节一致时生效，否则从该章开头。新增 `reading_state.resume_segment` 与按书隔离测试。限制：speechSynthesis 无法可靠报告句内位置，精度为段落（约 180 字以内）；关闭页面时的位置回传是尽力而为，浏览器可能不送达，最多丢失最近 5 段。
- 2026-10-20：听书与阅读状态持久化。播放器改为 Streamlit 自定义组件（`ui/listen_component/index.html`），切章或改语速/音色时回传状态；阅读页的预览章节与听书的起始章节、语速、音色写入 `_studio/reading_state.json`，刷新浏览器或重启 Streamlit 后自动恢复，不用重复选。章节已被删除时回退到第一章。新增 `novel_writer/reading_state.py`、`tests/test_reading_state.py`（6 例）、`tests/test_listen_persistence.py`（3 例，AppTest 模拟刷新与重启）。限制：记忆的是章节级位置，不记段落位置，再次播放从该章开头开始；需在 Edge 与 Chrome 中手动验证回传是否正常。
- 2026-10-20：M7 听书（纯前端朗读，自动连播）。阅读页新增「🎧 听书」折叠区：从所选章节起，浏览器 speechSynthesis 逐段朗读到末章，自动切章，章节标题作为每章第一段朗读；支持暂停/继续/停止、上一章/下一章、语速与音色（仅列出中文语音）。数据组装在 `novel_writer/listen.py`（纯逻辑，每段 ≤180 字以规避 Chrome 长文本中断），界面在 `ui/listen.py`。不生成音频、不调用 LLM、不改动流水线。新增 `tests/test_listen.py`（5 个用例）。已知限制：语音列表取决于浏览器与系统；切换语速/音色在下一段生效；需在 Edge 与 Chrome 中手动验证；切到后台标签页可能被浏览器暂停。
- 2026-10-17：M0 完成（见上表）。
- 2026-10-17：M1 引擎内核完成（待确认）。新增 steps/gates/state/pipeline/validators/runner/worker/service_cli、
  workflows/skill_step.yaml、entrypoints/run_novel.py 与 smoke_real_llm.py；tool_api 增加运行与步骤操作接口；
  测试 71 个全部通过（M0 39 + M1 32）。
- 2026-10-17：M2 完成（待确认）。新增 intake / views 模块，tool_api 增加意图与规划/设定/线索接口；
  ui/ 拆分为 wizard / cockpit / planning / codex / threads / components；app.py 增加「🧭 驾驶舱」导航。
  测试 85 个全部通过（M0 39 + M1 32 + M2 14）。
- 2026-10-19：运行结果实时化 + 非 UTF-8 文本自动转存。①驾驶舱「上一次运行结果」原先只在 worker 整轮结束时写入 `last_run.json`（收件箱同理），运行中表格停在上一轮；现在 `worker._checkpoint` 每执行完一步写入进行中记录（`running: true`）并刷新收件箱，结束时用最终记录（`running: false`）覆盖，`ui/live.py` 在 worker 存活时显示「本次运行进度（进行中）」。②新增 `novel_writer/textio.py`：`read_text_utf8` 严格 UTF-8 优先，失败则按 BOM → 无 BOM 的 UTF-16 特征 → GB18030 检测，解码成功即备份为 `<原名>.原编码.bak` 并就地转存为 UTF-8（回读校验），记录经 `drain_repairs()` 写入运行日志与 last_run 的 `encoding_fixed`；检测或转存失败抛 `TextEncodingError`，消息含路径、大小、文件头字节、各编码失败原因与处理建议，原文件不改动。chapter_ops / runner / views / ondemand / workspace 的文本读取统一改用它；起因：ch19.best_pick_prep 因候选稿为 UTF-16（FF FE 开头）报 UnicodeDecodeError。新增 `tests/test_textio_and_checkpoint.py`。
- 2026-10-17：失败步骤自动重跑。`worker.MAX_AUTO_RETRIES = 3`，每次重跑前等待 `RETRY_BACKOFF_SEC`（5/15/30 秒）；仅针对 failed；暂停/停止、预算用尽时停止重跑；日志与 `last_run.json` 记录重跑次数；收件箱的失败提示注明已重跑次数。`test_engine_m1.py::test_skill_failure_marks_failed_and_stops` 的预期随之改为 4 次尝试；`conftest.py` 把退避时间设为 0；新增 `tests/test_auto_retry.py`（5 个用例）；全量 205 个通过。
- 2026-10-17：「运行下一步」与单步选择的一致性修正。新增 `tool_api.next_step`（与 worker 同一个前沿函数）；「运行下一步」带上 next_step 返回的步骤 id 执行，按钮上方显示将运行的步骤；待批准/待答复时按钮禁用并说明原因（之前静默空转）；单步选择框默认跟随下一步，流水线表增加「下一步」列；新增 `tests/test_next_step.py`（6 个用例）；全量 200 个通过。
- 2026-10-17：运行输出与完成提示（M6 之后的界面修正）。驾驶舱原先只显示「后台 worker 正在运行（步骤：…）」且不更新。现在：子 agent 以 verbose 运行，工具调用与结果写入 `_studio/run.log`；worker 写入步骤开始、结束、耗时与摘要，并以「═══ 运行开始/结束」分隔、追加保留；`_studio/last_run.json` 保存上一次运行的每步结果；新增 `tool_api.run_status` 与 `ui/live.py`（2 秒轮询的 fragment，显示当前步骤与已用时、日志尾部、运行结束横幅与一次性 toast、上一次运行结果表）；`requirements.txt` 的 streamlit 下限升到 1.37；新增 `tests/test_live_output.py`（6 个用例）；全量 194 个通过。
- 2026-10-17：M6 完成（待确认）。`steps.py` 增加 design_ending / final_review / alt_write / best_pick / patrol 与质量档参数；`pipeline.py` 按幕结构插入结局设计、末尾插入全书终审、精品档每 10 章插入巡检；`gates.py` 把 final_review 纳入「幕末审稿最低审批」；`chapter_ops.py` 增加 best_pick 处理器；新增 `exporter.py`；`views.export_book` / `tool_api.export` 支持 fmt；阅读页增加格式选择与下载；`test_engine_m1.py` 的预算用例改为 7 章蓝图（结局设计占用了第 6 章前的位置）；新增 `tests/test_m6.py`（15 个用例）；全量 188 个通过。
- 2026-10-17：M5 完成（待确认）。挡位表增加两条「最低审批」规则（前三章章节写作、幕末深度审稿）；预算计数与 `chapter_limit` 收紧；新增 `inbox.py`、`batch.py`、`entrypoints/advance_all_auto.py`；`ondemand.py` 增加 `assist` 请求与采用逻辑；`views.py` / `tool_api.py` 增加收件箱、手写旁路、AI 辅助与批量接口；UI 驾驶舱增加收件箱与手写旁路，写作台增加 AI 辅助，书架增加全局收件箱与批量按钮；`test_engine_m1.py::test_budget_chapters_pauses_not_fails` 改为第 4 章起的场景（前三章逐章审批导致旧场景不再成立）；新增 `tests/test_m5.py`（60 个用例，含界面 AppTest）；全量 173 个通过。
- 2026-10-17：M3 完成（待确认）。新增 chapter_ops 模块与 M3 界面（写作台 / 审校 / 阅读）；runner 处理器可接收 invoker，支持 NeedsUser 挂起；
  tool_api 增加章节总览、单章详情、保存正文、导出全书合稿接口；测试 99 个全部通过（M0 39 + M1 32 + M2 14 + M3 14）。
