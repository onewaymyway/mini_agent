# 万物模拟器（world_simulator）—— 外部项目方案

> **状态**：方案讨论中，未开始实现。
> **定位**：一个独立的「外部项目」（`external_projects/world_simulator`），
> 遵循 `next_doc/external_projects_workspace_plan.md` 确立的四条原则
> （引擎与宿主解耦 / 可独立运行是硬约束 / 声明式注册+被动账本 / daemon
> 只能"触发一次独立运行"），复用 `external-project-manager` skill 的
> 脚手架规范，同时参考 `external_projects/stock_watch` 已验证的
> "自带独立 Streamlit 看板 + project.yaml 声明 headless entrypoints"
> 双轨模式。

---

## 1. 背景与目标

做一个能模拟"任意事物发展"的通用引擎：人生模拟、群体演化、事件走向推演等，
核心用途有两个：

1. **决策推演分析**：给定当前处境和几个候选选择，模拟出各自的可能未来，
   辅助用户做决策对比。
2. **持续性模拟/游戏**：创建一个有生命周期的模拟实例，可以推进、暂停、
   恢复、存档分叉，甚至包装成一个轻量"游戏"外壳来玩。

选择做成**独立外部项目**而不是 mini_agent 框架内部模块，原因：

- 这是一个会独立演化、未来可能整体搬走/独立发布的产品形态，跟
  `stock_watch` 的定位（"在 mini_agent 之上搭建的领域专用系统"）完全一致，
  而不是 `goal_mode`/`wiki` 这类"mini_agent 自身能力"。
- 需要自己的一套持续迭代的 UI/交互设计，不适合塞进主 daemon 的通用看板。
- 外部项目机制本身就是为这种"复用引擎能力、但要能独立发布"的场景设计的。

---

## 2. 如何复用 mini_agent 已有能力（这是本方案的核心问题）

外部项目"复用宿主能力"不是 import 框架内部模块乱连，而是有固定的几条
既有通道，逐一对应：

### 2.1 复用 `Workspace` 做路径/技能隔离

`world_simulator` 作为独立 Workspace（`Workspace(root=<项目路径>)`），
自动获得：
- 自己的 `skills/`、`workflows/`、`data/`、`reports/`、`.agent/` 等标准
  目录派生（`workspace.py` 已实现），不用自己重新设计一套路径规则。
- 分层 skill 搜索（本地 `skills/` 优先，全局 skills 兜底）：用于放
  "场景模板生成"这类可复用的结构化生成能力（见 2.3）。
- **不需要挂在 daemon 进程里**也能独立跑——这是外部项目的硬约束，
  `Workspace` 从设计上就不依赖交互式会话上下文。

### 2.2 复用 `LLMHelper` 做无 daemon 依赖的模型调用

推演引擎的每一步（"当前状态 + 决策 → 下一状态 + 叙事"）本质是一次结构化
LLM 调用。不新写一套调用/重试/降级逻辑，直接复用
`src/mini_agent/llm/service.py::LLMHelper.from_config(cfg)`——这正是
`llm_helper_unification_plan.md` 里明确"给主循环之外的 LLM 调用"设计的
统一入口，自带重试策略，`stock_watch` 的非 AI entrypoint 也可以按同样
方式接入（如果需要脱离 workflow 直接调用）。

`cfg` 从哪来：外部项目自己没有 `agent_config.json`/`providers.json` 时，
`config/loader.py::load_config()` 会回退到注册时记录的
`main_project_root`（`registry.py::RegisteredProject.main_project_root`）
去继承宿主的 LLM 配置——这条继承链已经是现成机制，不用重新设计。

### 2.3 复用 workflow 引擎做"结构化生成 + 校验 + 落盘"

"用户一句话意图 → 模拟提案草稿（初始状态/关键变量/推演方向）" 这一步，
和 `goal_mode` 生成 `GoalExecutionSpec`、`workflow/generator.py` 生成
workflow 定义是同一类问题：LLM 输出 → 需要结构化校验 → 需要落盘 →
可能需要重试。直接复用 `workflow/` 里的 `skill_agent` step 类型
（`result_file` 契约、类型强制转换、"resume 3 次→新会话 3 次→失败"的
既有重试策略），而不是自己写一套"调 LLM+解析 JSON+校验"的轮子。

即：`world_simulator` 内部维护自己的 workflow 定义（如
`workflows/generate_scenario.yaml`、`workflows/advance_step.yaml`），
通过 `Workspace.workflows_dir` 被发现，用
`mini-agent workflow run <name> --workspace <path>` headless 触发——
这与 `external-project-manager` skill 文档里"headless 单次执行入口"
的既定用法完全一致（`cli/commands/workflow_cmd.py::run_workflow_cli`）。

### 2.4 复用 skill 机制做"场景模板库"

不同模拟类型（人生模拟 / 群体演化 / 事件推演）各自需要一套"如何生成合理
初始状态""如何评估某个决策的影响"的领域知识，这天然适合做成
`world_simulator/skills/<template-name>/` 下的 skill（复用
`SkillLoader`），而不是在引擎代码里 if-else 分叉写死。新增一种模拟类型
= 新增一个 skill，引擎本身不用改。

### 2.5 复用外部项目标准三件套做"可见性 + 调度"

- `project.yaml`：声明 headless entrypoints（见第 4 节），供 daemon
  cron 调度批量推进任务（比如"每天自动推进一步"）、或未来接入主
  daemon 的"🗂️ 外部项目"tab 做基本状态展示（健康检查、执行账本）。
- `.agent/run_status.jsonl`：复用既有账本约定，任何 entrypoint 执行
  都留痕，daemon 被动可读，不需要主动上报。
- 注册进 `~/.agent/external_projects.json`：让"大管家"（主 daemon）
  知道这个项目存在、能触发、能看健康状态——但**不代表**要把核心交互
  搬进主看板（见第 5 节，核心交互走独立 app）。

### 2.6 复用存储工具做状态持久化

状态快照/历史写入直接用 `mini_agent.utils.atomic_write`（`.tmp` 改名
落盘的既有约定），不重新发明原子写。

### 2.7 明确不复用的部分

- **不复用 `goal_mode`**：goal_mode 是"面向单一用户当前处境的执行引擎"，
  语义上是"帮用户做成一件事"，跟"模拟一个独立的虚拟世界/人生"是两回事，
  混用会让 GoalNode 语义膨胀。
- **不复用主 kanban 的 `dashboard.kanban_view` 通用状态列看板**：那个
  机制是为"候选池状态流转"（如 stock_watch 的 watching→holding）设计
  的扁平列表视图，无法表达"时间线推进+分支+详情面板+游戏化视图"这种
  富交互，所以走独立 app（见下节），这与 `stock_watch/app.py` 已经
  验证过的"两条腿走路"模式一致：简单状态用通用 kanban_view，复杂交互
  自己开 Streamlit app。

---

## 3. 目录结构

```
external_projects/world_simulator/
  project.yaml                # headless entrypoints 声明（供 daemon 调度/可见性）
  PROJECT.md                  # 目标/设计/已知限制，同 stock_watch 规格
  app.py                      # 独立 Streamlit 看板（核心交互入口）
  world_simulator/            # 业务代码包
    __init__.py
    spec_generator.py         # 意图 → 模拟提案草稿（走 workflow skill_agent）
    engine.py                 # 核心推演循环：state+step → next_state+narrative
    state_model.py             # State 数据结构（分模板：人生/群体/事件...）
    store.py                   # 状态快照/历史/分支的持久化（atomic_write）
    branch_manager.py          # 分叉、回滚、对比
  skills/                      # 本项目私有 skill（场景模板库，2.4节）
    life-sim-template/
    group-sim-template/
    event-sim-template/
  workflows/                   # 本项目私有 workflow 定义（2.3节）
    generate_scenario.yaml
    advance_step.yaml
  entrypoints/                 # headless 入口（project.yaml 对应实现）
    create_simulation.py
    advance_simulation.py
    list_simulations.py
    health.py
  data/                        # 模拟实例数据（每个实例一个子目录）
    <sim_id>/
      manifest.json
      state_current.json
      state_history.jsonl
      branches/
  reports/
  tests/
  config/
  .gitignore
```

---

## 4. `project.yaml` entrypoints 设计（草案）

只暴露"适合无差别调度/daemon 触发"的操作；日常创建/推进/查看主要走
独立 app 内部直接操作 `world_simulator` 包，不强制走 entrypoint 子进程
（entrypoint 是给 daemon/cron 用的通道，不是唯一交互方式）。

```yaml
name: world_simulator

entrypoints:
  create_simulation:
    cmd: "python entrypoints/create_simulation.py"
    timeout_sec: 300
    params:
      - name: intent
        required: true
        help: "一句话模拟意图描述"
  advance_simulation:
    cmd: "python entrypoints/advance_simulation.py"
    timeout_sec: 300
    params:
      - name: sim_id
        required: true
      - name: choice
        required: false
        help: "本步选择的选项 id（无则引擎自行推进/给出默认走向）"
  batch_advance_daily:
    cmd: "python entrypoints/advance_simulation.py --all-active --steps 1"
    schedule: "cron: 0 6 * * *"   # 可选：每天自动推进所有"持续模拟"实例一步
    timeout_sec: 900
  health:
    cmd: "python entrypoints/health.py"
    timeout_sec: 60
```

`batch_advance_daily` 是可选项——只有"持续演化型"模拟（比如"模拟一个
群体，我每天来看进展"）才需要这个自动推进；一次性决策推演场景不需要。

---

## 5. 独立看板（`app.py`）设计

沿用 `stock_watch/app.py` 的技术选型（Streamlit，本地
`streamlit run app.py` 启动，未来可独立部署/发布），页面规划：

1. **模拟列表**：所有实例卡片，展示类型/当前进度/状态（进行中/已暂停/
   已结束），支持新建入口。
2. **创建向导**：用户输入一句话意图 → 调用 `spec_generator`（走
   `generate_scenario` workflow）→ 展示生成的提案草稿（初始状态/关键
   变量/可能方向）→ 用户可编辑字段/追加约束 → 确认创建。**关键约束：
   不让用户从零填表单**，草稿生成失败或用户不满意时才逐字段兜底编辑。
3. **实例详情/推进面板**：当前状态可视化（关键变量数值+趋势图）、
   历史时间线、"推进下一步"按钮、如果引擎给出多个分支选项则展示为
   可点击卡片。
4. **对比视图**：选两条时间线（同一实例的不同分支，或两个独立实例）
   并排展示关键变量差异，服务于"决策推演分析"这个核心场景。
5. **存档管理**：分叉、回滚到某个历史节点、删除实例。
6. **（后续阶段）游戏化视图**：把同一份 `state_history` 按"章节"渲染，
   选项按钮驱动推进，纯前端呈现层，不需要新的数据结构。

---

## 6. 核心引擎设计要点

- `state_model.py`：不做成单一大而全的 schema，而是"基础字段
  （id/时间步/摘要）+ 模板私有字段（自由 JSON）"的组合，模板私有部分
  的结构由对应 skill 定义，引擎不关心具体字段语义。
- `engine.py` 的单步推进 = 一次 `advance_step` workflow 调用：输入
  当前 state + 本次用户选择（可为空），输出 next_state（结构化）+
  叙事文本 + 若干候选分支选项（供下一步选择）。
- 分支/存档：每次"选择"都在 `state_history.jsonl` 追加一条记录；
  "回滚重新选"= 在某个历史节点开一条新分支（复制该节点之前的历史，
  写入 `branches/<branch_id>/`），不销毁原时间线，天然支持对比视图。
- 暂停/恢复：只是"要不要继续调用引擎推进"的控制位
  （`manifest.json.status`），状态本身每步都落盘，天然可恢复。

---

## 7. 分阶段实施计划

- **阶段一（脚手架 + 核心闭环）**：用
  `external-project-manager` skill 的 scaffold 脚本生成骨架
  → 实现 `state_model`/`store`/`engine` 最小闭环
  → 实现 `spec_generator`（先服务"人生模拟"一个模板）
  → CLI/entrypoint 级别验证："一句话意图→生成提案→确认→推进3步→查看历史"
  跑通，不做 UI。
- **阶段二（独立看板 MVP）**：`app.py` 实现模拟列表+创建向导+详情推进
  面板，能替代阶段一的 CLI 验证方式。
- **阶段三（分支/对比）**：`branch_manager` + 对比视图，服务决策推演
  场景。
- **阶段四（batch_advance 调度 + 注册可见性）**：补 `project.yaml`
  调度型 entrypoint，注册进 daemon 注册表，验证"不依赖 daemon 也能
  单独跑，daemon 在场时能看到健康状态"两条路径都通。
- **阶段五（游戏化视图，可选）**：视前四阶段效果决定是否做。
- **阶段六（多模板扩展）**：群体演化、事件推演等模板以 skill 形式
  陆续补充，验证"新增模拟类型不改引擎代码"这条设计假设是否成立。

---

## 8. 待确认问题

1. 项目命名：`world_simulator` 是否合适，还是有其它偏好命名？
2. 阶段一的第一个模板选"人生模拟"是否合适，还是优先做"事件推演"
   （更贴近"决策分析"这个高价值场景）？
3. `batch_advance_daily` 这类自动定时推进，是否是你要的功能，还是
   所有推进都应该是用户主动触发（更可控，避免模拟"自己跑飞"）？
4. 独立看板技术栈沿用 Streamlit（与 stock_watch 一致，复用经验）是否
   OK，还是希望用别的（比如更适合做"游戏感"的技术栈）？

以上确认后，从阶段一开始实现。
