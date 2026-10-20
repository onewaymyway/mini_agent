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
