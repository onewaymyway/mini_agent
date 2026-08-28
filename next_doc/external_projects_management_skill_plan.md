# 外部项目创建与维护 Skill 设计与实施计划

> **这篇文档管什么**：如何把 `external_projects_workspace_plan.md` §5.1/
> §7 第2点里提到的"外部项目模板化/脚手架化"，落地成一个 mini_agent
> skill（`.claude/skills/external-project-manager`），让用户/agent 能
> 按统一规范创建新外部项目，并对已注册的外部项目做日常维护（体检、看
> 账本、看改进积压、复盘、修复）。
>
> **不管什么**：不改动 `external_projects/` 框架层代码本身（manifest
> 校验、registry、scheduler、ledger、backlog 都已在此前几份计划里实现
> 完毕），本 skill 是这些能力之上的一层"标准操作规程 + 脚手架工具"，
> 供 agent 在对话中调用，而不是新增框架能力。

## 1. 背景

`external_projects_workspace_plan.md` 确立了外部项目的标准结构（§5.1）
和 `project.yaml` 契约（§5.2），`stock_watch` 是第一个落地案例，随后
`external_projects_kanban_integration_plan.md`、
`external_projects_generic_kanban_view_refactor_plan.md`、
`stock_watch_pool_state_tracking_and_kanban_plan.md`、
`stock_watch_continuous_improvement_plan.md` 陆续把看板集成、通用状态
看板视图、状态跟踪、改进积压/复盘机制都补齐了。

但截至目前，"新建一个符合规范的外部项目"这件事仍然是纯手工：新开一个
项目需要人（或 agent）记住 §5.1 的目录结构、`project.yaml` 的完整
schema（`entrypoints`/`params`/`health_check`/`resources`/`review`/
`dashboard.kanban_view`）、`PROJECT.md` 该写哪几节、`run_status.jsonl`/
`improvement_backlog.jsonl` 账本怎么接、注册命令怎么调——这些知识分散
在五份 `next_doc` 和 `stock_watch` 这一个真实案例的代码里，每次新建
第二个外部项目都要重新翻文档、照抄 `stock_watch`，容易漏掉某个约定
（比如忘了给 entrypoint 写 `params.default` 导致看板手动触发时参数错位，
`stock_watch/project.yaml` 里 `fetch_iwencai_cookie` 那段注释记录的
坑）。

这正是 skill 机制要解决的问题：把"如何正确使用一套已有机制"的操作知识
固化下来，而不是每次现场重新摸索。

## 2. 目标与非目标

**目标**：

1. 提供一个可被 agent 调用的 skill，覆盖外部项目全生命周期两大动作：
   - **创建**：交互式收集项目基本信息 → 生成符合 §5.1 结构的骨架 →
     生成结构合法的 `project.yaml` 初稿 → 提示下一步（写业务代码、
     `mini-agent projects register`）。
   - **维护**：体检（health_check + 账本最近记录）、看执行历史
     （ledger）、看改进积压（backlog）、发起复盘（review）、指导"抓取
     失效之类的维护性修复"走 self-evolution 的提案-验证-落地流程。
2. Skill 内容与框架实现（`manifest.py`/`registry.py`/`ledger.py`/
   `backlog.py`/`projects_cmd.py`）保持同步——写成"引用 CLI 命令 + 引用
   代码里已固化的校验规则"，不重复发明一套新协议。
3. 脚手架生成的项目，`mini-agent projects register <path> --no-validate`
   之后应该能被 `load_manifest()` 成功解析（哪怕业务代码还是空的）。

**非目标**：

- 不生成任何具体业务逻辑代码（抓取、分析算法等）——那是创建骨架之后，
  agent 依据用户的具体领域需求另行实现的部分。
- 不新增/修改 `project.yaml` schema 本身、不新增 CLI 子命令——如果实施
  过程中发现现有机制有缺口，记录在本文档第 6 节"发现的问题"，不在本
  skill 里绕过去。
- 不做"多个外部项目间资源仲裁""跨项目经验沉淀"——`external_projects_
  workspace_plan.md` §7 已明确这些留白到出现第二、三个真实案例后再做，
  本 skill 不提前设计。

## 3. Skill 设计

### 3.1 放置位置与命名

`.claude/skills/external-project-manager/`——项目内 skill（而不是
`/mnt/skills`），因为它强依赖 mini_agent 自身的 `external_projects/`
框架代码与 CLI，只对"在 mini_agent 仓库里工作的 agent"有意义。

### 3.2 目录结构

```
.claude/skills/external-project-manager/
├── SKILL.md                        # 主入口：何时用、创建流程、维护流程
├── scripts/
│   └── scaffold.py                 # 生成 §5.1 标准目录骨架 + project.yaml/PROJECT.md 初稿
├── templates/
│   ├── project.yaml.tmpl            # project.yaml 模板（含注释）
│   ├── PROJECT.md.tmpl              # PROJECT.md 模板（含固定小节）
│   ├── health.py.tmpl                # health_check 入口模板
│   ├── entrypoint.py.tmpl            # 单个 entrypoint 骨架模板（含账本写法）
│   └── gitignore.tmpl                # 外部项目自己的 .gitignore 模板
└── reference/
    ├── project_yaml_schema.md        # project.yaml 完整字段参考（对齐 manifest.py）
    └── maintenance_playbook.md       # 维护动作对照表（体检/账本/积压/复盘/修复）
```

### 3.3 创建流程（skill 驱动 agent 怎么做）

1. 向用户确认：项目名（`snake_case`，用作 `project.yaml` 的 `name` 和
   注册表 key）、放置路径（默认 `external_projects/<name>`，也允许任意
   外部路径，呼应原则三"路径独立"）、项目一句话目标、初步预期的
   entrypoint 列表（名字 + 是否定时 + 大致 cron）。
2. 调用 `scripts/scaffold.py` 生成骨架（见 3.4），骨架里的
   `project.yaml`/`PROJECT.md`/`entrypoints/*.py` 全部是"结构合法、内容
   待填"的占位模板，不是空文件。
3. 依据用户的具体领域需求，把每个 entrypoint 的业务逻辑实现进去；每个
   entrypoint 必须调用 `ledger.track_run()` 记账（模板已经带好这段
   样板代码，实现时不能删掉）。
4. 如果这个项目的核心数据有"状态流转"语义（类似 `stock_watch` 的候选
   池状态机），参考 `stock_watch_pool_state_tracking_and_kanban_plan.md`
   补 `dashboard.kanban_view` 声明；如果只是"跑批产出报告"，不需要这部分。
5. 本地跑通至少一个 entrypoint（`python entrypoints/xxx.py`，不依赖
   daemon），确认 `.agent/run_status.jsonl` 有对应记录。
6. `mini-agent projects register <path>`（默认会跑 `load_manifest()`
   校验，失败会直接报错指出哪个字段不对）。
7. 提示用户：如果需要 daemon 定时调度，确认 daemon 是否已在运行；如果
   希望即使 daemon 不在也能跑，配一条 OS 级 cron 指向同一个 `cmd`
   （原则二）。

### 3.4 `scaffold.py` 的设计要点

- 纯标准库实现（不新增依赖），输入通过命令行参数或简单的 stdin 问答
  给出，输出是文件系统的骨架，不做任何网络调用。
- 生成的 `project.yaml` 默认只有一个占位 entrypoint（`main`），字段
  齐全但值是占位符，注释直接引用 `manifest.py` 里的字段语义，减少
  agent/用户需要再去翻源码的概率。
- 生成之后自动跑一遍 `load_manifest(path)`（复用框架已有校验函数，
  `import mini_agent.external_projects.manifest`），如果失败，脚手架
  报错并保留骨架文件供修正，不静默吞掉错误——校验是骨架"生成即合规"
  这个承诺的唯一保证手段。
- 幂等性：目标目录已存在且非空时默认拒绝执行（防止覆盖已有项目），
  提供 `--force` 才允许在已有目录上补齐缺失文件（只补缺失的，不覆盖
  已存在的同名文件）。

### 3.5 维护流程（skill 驱动 agent 怎么做）

对照 `reference/maintenance_playbook.md`，把"用户说的一句话"映射到
具体 CLI 调用：

| 用户意图 | 对应动作 |
|---|---|
| "看看 XX 项目现在怎么样" | `mini-agent projects status <name>`（health_check + 最近一条账本） |
| "XX 项目最近跑得怎么样/有没有失败" | `mini-agent projects ledger <name> [limit]` |
| "XX 项目有什么值得优化的" | `mini-agent projects backlog <name> list` |
| "手动跑一下 XX 项目的某个任务" | `mini-agent projects run <name> <entrypoint>` |
| "XX 项目的抓取好像失效了，帮我修一下" | 不在 daemon 进程内直接改文件；以该项目 `Workspace` 为根，走 self-evolution 既有的"git worktree 隔离 + 提案 + 验证 + 落地"流程（`external_projects_workspace_plan.md` 原则四），修复后追加一条 backlog 记录（`landed`）作为变更留痕 |
| "该给 XX 项目做每周复盘了" | `mini-agent projects review <name>`，读打印出的任务模板，结合账本/积压账本判断是否有值得处理的优化项 |
| "新建一个 XX 领域的外部项目" | 走 3.3 创建流程 |

## 4. 实施阶段

> 约定：每完成一项，回来把对应复选框打勾，并在文末"变更记录"补一行；
> 每完成一个阶段就更新本文档 + 打包一次改动。

### 阶段 1：设计确认（本文档）
- [x] 背景、目标/非目标、skill 目录结构、创建流程、维护流程确认

### 阶段 2：skill 骨架与 `scaffold.py`
- [x] 新增 `.claude/skills/external-project-manager/SKILL.md`
- [x] 新增 `scripts/scaffold.py`：生成 §5.1 标准结构 + 合法 `project.yaml`
      初稿，生成后调用 `load_manifest()` 自证合法
- [x] 新增 `templates/*.tmpl`（project.yaml / PROJECT.md / health.py /
      entrypoint.py / gitignore）
- [x] 自测：在临时目录跑 `scaffold.py`，确认生成结果能被
      `load_manifest()` 解析通过，且 `mini-agent projects register
      --no-validate` 之后 `mini-agent projects status` 不报错

### 阶段 3：维护参考文档
- [x] 新增 `reference/project_yaml_schema.md`（对齐当前
      `manifest.py` 的字段与校验规则）
- [x] 新增 `reference/maintenance_playbook.md`（用户意图 → CLI 动作
      对照表，覆盖体检/账本/积压/复盘/修复五类场景）

### 阶段 4（留待后续，本次不做）：结合真实第二个外部项目验证
- [ ] 等用户用这个 skill 实际新建第二个外部项目后，回来看
      `scaffold.py` 生成的骨架有没有遗漏的约定，按需修订 skill 内容，
      并在本文档补记录——刻意留到有第二个真实案例后再做，避免这次
      只有 stock_watch 一个参照案例时过早收敛成"只适合股票系统"的
      脚手架。

## 5. 验收标准

- `scaffold.py` 在全新空目录下跑出的骨架，`load_manifest(path)` 校验
  通过（结构合法）；`mini-agent projects register <path> --no-validate`
  之后 `mini-agent projects status <name>` 能正常输出（不因缺文件
  报错）。
- `SKILL.md` 的创建流程/维护流程与 `projects_cmd.py` 里实际存在的子
  命令一一对应，没有引用不存在的命令。
- `reference/project_yaml_schema.md` 里列出的每个字段，都能在
  `manifest.py` 里找到对应的 dataclass 字段与校验函数，不臆造字段。

## 6. 发现的问题 / 后续可能需要回来处理的点

（实施过程中如果发现现有机制有缺口，记在这里，不在 skill 里绕过去。
截至阶段 3，未发现需要改动框架层代码的问题。）

## 变更记录

- 2026-08-28：阶段 1 完成，设计确认。
- 2026-08-28：阶段 2 完成，`scaffold.py` + `SKILL.md` + 模板落地，
  自测通过。
- 2026-08-28：阶段 3 完成，两份 reference 文档落地。
