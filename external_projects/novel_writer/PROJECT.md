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
| M6 质量增强（可选） | 待议 | 不属于已确认范围：best-of-N、结局设计与终审、质量档位的步骤差异 |

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
- **质量档位未落地（M5 未涉及）**：`quality` 字段已保存，但草稿/标准/精品三档在步骤层面的差异（同步拆分、读者模拟频率、场景/对话步骤、best-of-N）尚未实现。
- **结局设计与终审未进入流水线**：ending-designer（design_ending）与 final_review 仍未加入（方案 4.2 收尾阶段，归入 M6 或之后）。
- **收件箱没有外部通知渠道**：快照写入 `_studio/inbox/index.json`，但不推送到 IM / 邮件；渠道未在方案中定义。
- **手写旁路只检查产出存在**：不校验内容质量，也不检查字数或格式。
- **AI 选段辅助**：不做 best-of-N；候选稿质量完全取决于所用模型；采用要求选段在正文中唯一匹配（润色/扩写）。
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
├── ui/                   shelf（书架）、wizard（新建向导）、cockpit（驾驶舱）、planning / writing / review / reading / ideas / metrics / settings_page / codex / threads（分页）、components、skills_page
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

- 2026-10-17：M0 完成（见上表）。
- 2026-10-17：M1 引擎内核完成（待确认）。新增 steps/gates/state/pipeline/validators/runner/worker/service_cli、
  workflows/skill_step.yaml、entrypoints/run_novel.py 与 smoke_real_llm.py；tool_api 增加运行与步骤操作接口；
  测试 71 个全部通过（M0 39 + M1 32）。
- 2026-10-17：M2 完成（待确认）。新增 intake / views 模块，tool_api 增加意图与规划/设定/线索接口；
  ui/ 拆分为 wizard / cockpit / planning / codex / threads / components；app.py 增加「🧭 驾驶舱」导航。
  测试 85 个全部通过（M0 39 + M1 32 + M2 14）。
- 2026-10-17：M5 完成（待确认）。挡位表增加两条「最低审批」规则（前三章章节写作、幕末深度审稿）；预算计数与 `chapter_limit` 收紧；新增 `inbox.py`、`batch.py`、`entrypoints/advance_all_auto.py`；`ondemand.py` 增加 `assist` 请求与采用逻辑；`views.py` / `tool_api.py` 增加收件箱、手写旁路、AI 辅助与批量接口；UI 驾驶舱增加收件箱与手写旁路，写作台增加 AI 辅助，书架增加全局收件箱与批量按钮；`test_engine_m1.py::test_budget_chapters_pauses_not_fails` 改为第 4 章起的场景（前三章逐章审批导致旧场景不再成立）；新增 `tests/test_m5.py`（60 个用例，含界面 AppTest）；全量 173 个通过。
- 2026-10-17：M3 完成（待确认）。新增 chapter_ops 模块与 M3 界面（写作台 / 审校 / 阅读）；runner 处理器可接收 invoker，支持 NeedsUser 挂起；
  tool_api 增加章节总览、单章详情、保存正文、导出全书合稿接口；测试 99 个全部通过（M0 39 + M1 32 + M2 14 + M3 14）。
