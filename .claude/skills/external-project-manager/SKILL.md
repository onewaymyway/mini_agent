---
name: external-project-manager
description: 创建和维护"外部项目"（用户在 mini_agent 之上搭建的、领域专用、可独立运行的定制系统，如 stock_watch）。当用户想新建一个符合规范的外部项目、或想查看/维护/复盘一个已注册外部项目的运行状况时使用。触发词：新建外部项目、创建一个 XX 监控/分析系统、外部项目脚手架、看看 XX 项目怎么样、XX 项目最近跑得怎么样、XX 项目的改进积压、给 XX 项目做复盘、注册外部项目。不要用于修改 mini_agent 框架本身（skill/workflow/daemon 核心代码），也不要用于与"外部项目"机制无关的普通编程任务。
---

# 外部项目创建与维护

外部项目是用户在 mini_agent 之上构建的、领域专用、可能有独立数据/独立
生命周期的复杂系统（例如 `external_projects/stock_watch`：A 股监控分析
系统）。它们与 mini_agent daemon 的关系遵循
`next_doc/external_projects_workspace_plan.md` 确立的四条核心原则：

1. **引擎与宿主解耦**——外部项目复用 mini_agent 的 skill/workflow/
   memory 能力，但不绑死在 daemon 进程里。
2. **可独立运行是硬约束**——每个 entrypoint 必须能在没有 daemon 的
   情况下被直接执行或被 OS cron 调度。
3. **可见性靠"声明式注册 + 被动可读账本"**——daemon 读外部项目自己写
   的 `.agent/run_status.jsonl`，不要求外部项目主动上报。
4. **daemon 对外部项目的介入走"触发一次独立运行"**，不是 in-process
   直接改代码。

本 skill 把"新建一个符合以上规范的外部项目"和"日常维护一个已注册的外部
项目"这两类操作固化下来，避免每次都重新翻五份 `next_doc` 文档现场摸索。

## 什么时候用这个 skill

- 用户想做一个新的、独立于当前对话主线的领域系统（监控类、批处理分析
  类、定时抓取类都是典型场景）→ 走"创建流程"。
- 用户在问某个已注册外部项目（比如 stock_watch）的运行状况、失败情况、
  改进建议、要不要复盘 → 走"维护流程"。
- 用户想修一个外部项目里失效的功能（比如某个数据源抓取挂了）→ 走
  "维护流程"里"修复"这一条，注意不要在 daemon 自己进程里直接改文件。

**不要用这个 skill 的场景**：修改 mini_agent 框架自身代码（`src/
mini_agent/external_projects/*.py` 这类框架实现）、与外部项目机制无关
的普通编程/写作任务。

## 创建流程

1. **收集信息**（如果用户没有主动给全，用简短问题补齐，不要堆一大串
   表单）：
   - 项目名（会被规范成 `snake_case`，用作 `project.yaml` 的 `name`
     和 daemon 注册表 key）
   - 放置路径（默认 `external_projects/<name>`，也可以是任意路径——
     外部项目允许放在完全不同的位置甚至独立 git 仓库）
   - 一句话目标
   - 预期的 entrypoint 有哪几个（每个功能点最终应该映射到一个
     headless 可执行入口，比如"抓取"、"分析"、"生成报告"可以是三个
     独立 entrypoint，也可以先合并成一个 `main`，之后再拆）

2. **生成骨架**：调用本 skill 目录下的脚手架脚本：
   ```
   python .claude/skills/external-project-manager/scripts/scaffold.py \
       <name> --path <目标路径> --summary "<一句话目标>" \
       --entrypoint <ep1>,<ep2>,...
   ```
   脚本会生成符合 `external_projects_workspace_plan.md` §5.1 标准结构
   的目录骨架（`project.yaml`/`PROJECT.md`/`entrypoints/`/`data/`/
   `reports/`/`tests/`/`config/`/业务包目录/`.gitignore`），并自动用
   框架自己的 `load_manifest()` 校验一遍生成的 `project.yaml`——如果
   校验失败会报错并保留文件供修正，不会静默生成一个不合规的骨架。
   完整字段含义见 `reference/project_yaml_schema.md`。

3. **实现业务逻辑**：把 `entrypoints/<key>.py` 里的 `TODO`/
   `NotImplementedError` 替换成真正的抓取/分析/处理代码。**必须保留**
   模板里 `track_run()` 那段账本样板代码——这是原则三"可见性"的唯一
   实现手段，删掉就等于让这个 entrypoint 对 daemon 不可见。

4. **补全 `PROJECT.md`**：目标、数据源与依赖策略、已知限制——这些是
   给"未来的自己/大管家"看的，不是可选项。参考
   `external_projects/stock_watch/PROJECT.md` 的详细程度。

5. **判断是否需要看板状态视图**：如果这个项目的核心数据有"状态流转"
   语义（类似 stock_watch 候选池的 watching → focused → holding →
   ...），在 `project.yaml` 补 `dashboard.kanban_view` 声明（模板里有
   注释掉的示例块）；具体字段含义和一个完整的真实案例见
   `external_projects/stock_watch/project.yaml` 的 `dashboard` 部分
   以及 `next_doc/external_projects_generic_kanban_view_refactor_plan.md`。
   如果只是"跑批产出报告"，不需要这部分，删掉即可。

6. **本地跑通**：在项目目录下 `python entrypoints/<key>.py`，确认
   `.agent/run_status.jsonl` 出现对应记录，不依赖 daemon 是否在运行。

7. **注册**：`mini-agent projects register <path>`（默认会做 manifest
   校验；如果业务代码还没写完只想先占位，加 `--no-validate`）。

8. **调度**（可选）：如果需要定时跑，确认 `project.yaml` 里对应
   entrypoint 的 `schedule` 字段，并确认 daemon 是否在运行；如果希望
   即使 daemon 不在也能定时跑，額外配一条 OS 级 cron 指向同一个 `cmd`
   （原则二：daemon 只是可选加成，不是前提）。

## 维护流程

先确认项目名是否已注册（`mini-agent projects list`），再按用户意图
选对应命令。完整对照表见 `reference/maintenance_playbook.md`，常用的：

| 用户想做什么 | 命令 |
|---|---|
| 看整体状态（健康检查 + 最近一次执行） | `mini-agent projects status <name>` |
| 看执行历史/有没有失败 | `mini-agent projects ledger <name> [limit]` |
| 看改进积压 | `mini-agent projects backlog <name> list` |
| 手动触发某个 entrypoint | `mini-agent projects run <name> <entrypoint>` |
| 发起周期性复盘 | `mini-agent projects review <name>` |
| 修复一个失效的功能 | **不要**在当前 daemon 进程里直接改外部项目文件；以该项目目录为工作根，走 self-evolution 既有的"提案-验证-落地"流程去改，改完追加一条 backlog 记录留痕 |

维护/修复时的关键约束（呼应原则四）：即便是"帮用户修一下抓取脚本"，
执行动作也应该发生在该外部项目自己的 workspace 边界内（对应目录下跑
测试、跑 entrypoint 验证），而不是把外部项目的代码当成当前对话/当前
daemon 项目的一部分随手改。

## 参考资料

- `reference/project_yaml_schema.md`——`project.yaml` 完整字段参考,
  对齐 `src/mini_agent/external_projects/manifest.py` 的实际校验规则。
- `reference/maintenance_playbook.md`——维护场景的完整对照表。
- `external_projects/stock_watch/`——目前唯一的真实落地案例，拿不准
  某个约定该怎么写时，直接参考它的 `project.yaml`/`PROJECT.md`/
  `entrypoints/` 实际写法。
- `next_doc/external_projects_workspace_plan.md`——四条核心原则与
  §5.1/§5.2 标准结构/契约的原始设计文档。
- `next_doc/external_projects_kanban_integration_plan.md`、
  `next_doc/external_projects_generic_kanban_view_refactor_plan.md`——
  看板集成与通用状态视图的设计细节。
- `next_doc/stock_watch_pool_state_tracking_and_kanban_plan.md`——
  状态机 + 区间涨跌跟踪的完整案例，`dashboard.kanban_view` 的最佳参考。
- `next_doc/stock_watch_continuous_improvement_plan.md`——改进积压/
  周期复盘机制的设计细节。
