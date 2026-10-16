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
| M2–M5 | 未开始 | 见方案第 8 节 |

## 已知限制 / 待验证事项

- M0 不调用任何 LLM；真实 LLM 端到端未验证（M1 起提供桩测试 + `smoke_real_llm.py` 供本机验证）。
- 界面只有 M0 简版三页；Streamlit 用 `AppTest` 做页面冒烟，仍需人工在浏览器里点一遍。
- skills 原文里的路径写法不一致（`project/summaries`、`novel_blueprint.json`、裸 `idea_bank.json`/`simulations/`）：
  不改原文，统一以 `workspace.py::PATHS` 为准并在每步 prompt 注入（M1）。
- 字数统计按"非空白字符数"，面向中文创作；英文按字符计偏大。
- **M1 未在真实 LLM 下验证**：`FrameworkInvoker` 的调用链（改写 skill_name / result_file / 输入 task_prompt）已对照 world_simulator 的用法与 `WorkflowStore` 解析做过核对，但没有密钥无法端到端跑通。请本机运行 `entrypoints/smoke_real_llm.py`。
- **M1 的章节写作链路暂时阻塞**：章节里的 python 步骤（word_quota / plot_tasks / context_pack / style_hint / machine_check / revise_loop / 以及 M3 的 W14）尚无处理器，运行到它们会以 `blocked` 停下，并提示在 M3 实现。规划阶段（P0/P1/P2 前三章设计）可以真实跑通。
- **并发限制**：UI/CLI 对 `pipeline_state.json` 的写入与 worker 同时进行时，同进程内有锁，跨进程靠 worker 每轮重新读盘收敛；极端情况下（worker 执行中同一步被人同时批准/重跑）以后写者为准。
- **章节号位数**：`rel()` 的 `nn` 固定两位；超过 99 章的书需要在 M3 引入 `chapter_number_width`（方案基线 40 章，暂不影响）。
- **daemon 入口**：`run_novel` 需要 `--workspace` 参数，**未**登记到 `project.yaml`（daemon 不会传参）。批量入口 `advance_all_auto` 在 M5 实现。

## 目录结构

```
novel_writer/
├── project.yaml          daemon 契约（create_novel / list_novels / health）
├── PROJECT.md            本文件
├── app.py                Streamlit 薄入口；页面在 ui/
├── ui/                   shelf.py（书架+新建）、skills_page.py
├── novel_writer/         config / fsutil / settings / workspace / shelf / skills_catalog / tool_api
│                         M1: steps / gates / state / pipeline / validators / runner / worker / service_cli
├── workflows/            skill_step.yaml（M1：通用单步 skill_agent 工作流）
├── skills/               23 个 skill（原样，未修改）
├── entrypoints/          create_novel / list_novels / health（+ _common）；M1: run_novel（worker）、smoke_real_llm
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
