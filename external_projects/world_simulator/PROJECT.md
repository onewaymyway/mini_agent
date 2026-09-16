# world_simulator — 通用事物发展模拟引擎：决策推演分析 + 持续性模拟

> 本项目遵循 `next_doc/external_projects_workspace_plan.md` 确立的外部
> 项目标准结构：完全自包含、可独立于 mini_agent daemon 运行。可以整体
> 移动到任意路径、放进独立 git 仓库，只要在 daemon 侧重新
> `mini-agent projects register <新路径>` 一下即可继续被"大管家"看见。

## 目标

一个能模拟"任意事物发展"的通用引擎，服务两类场景：决策推演分析
（给定处境+候选选择，模拟各自可能未来）、持续性模拟/游戏（可推进/
暂停/恢复/存档分叉的模拟实例）。完整方案见
`next_doc/world_simulator_external_project_plan.md`。

阶段一（脚手架 + 核心闭环，已完成）交付的功能点：

1. `create_simulation`：一句话意图 → 生成模拟提案草稿（走
   `generate_scenario` workflow）→ 落盘为 step 0 初始状态。
2. `advance_simulation`：推进模拟实例一步（走 `advance_step`
   workflow），支持传入候选选项 id，也支持不传（由引擎给默认走向）。
3. `list_simulations`：列出所有实例及当前状态摘要。
4. `health`：健康检查（不依赖 LLM/网络，只验证存储层可读）。

阶段一只落地"人生模拟"（`life_sim`）一个模板，`--template` 参数已
预留但暂不接受其它取值；分支/对比（阶段三）、自动挡（阶段四）、
`batch_advance_daily` 调度（阶段五）尚未实现，见下方"变更记录"与主
文档第 7 节的阶段划分。

阶段二（独立看板 MVP，已完成）交付 `app.py`（Streamlit，`streamlit
run app.py`启动）：

1. **模拟列表**：实例卡片（标题/状态/当前步数/推进模式），支持打开
   详情、新建入口。
2. **创建向导**：意图 → 调用 `spec_generator.generate_scenario()` →
   展示可编辑草稿（标题/摘要/变量 JSON/初始候选方向）→
   `engine.materialize_simulation()` 落盘。
3. **实例详情/推进面板**：当前状态 + 变量、时间线（"章节卡片"倒序
   展示）、候选分支渲染成可点击选择卡片、暂停/恢复/标记结束。

视觉主题"夜航日志"（深靛蓝底 + 灯笼金点缀），与 stock_watch 的数据
看板风区分开，见方案第 5 节 + `frontend-design` skill 的设计取向指南。
对比视图（页面 4）、存档管理（页面 5）、游戏化视图（页面 6）留给阶段
三及之后实现。

## 数据源与依赖策略

不依赖任何外部数据源，核心依赖是 mini_agent 框架自身的能力：

- `Workspace`：路径/skill 分层隔离（本项目未来若脱离 mini_agent 环境
  独立运行，路径规则不受影响）。
- `LLMHelper`（经由 `WorkflowRunner`/`skill_agent` 间接使用）：模拟
  推进每一步的核心是一次结构化 LLM 调用，不自己另起一套调用逻辑。
- `workflow` 引擎：`generate_scenario`/`advance_step` 两个 workflow
  定义负责"LLM 输出→结构化校验→重试"，不自己重写这套轮子。
- `mini_agent.utils.atomic_write`：状态快照/历史落盘复用现成的原子
  写工具；未装 mini_agent 时 `world_simulator/store.py` 会降级为普通
  文件写（换取"可独立运行"这条硬约束，见 2.6 节）。

`requirements.txt` 目前为空——阶段一未引入任何本项目私有的第三方
依赖，所有依赖都来自 mini_agent 主库。

## 已知限制 / 待验证事项

- **`spec_generator`/`engine` 的 LLM 调用路径尚未跑过真实模型**：单测
  用 monkeypatch 打桩了 `WorkflowRunner`/`WorkflowStore`，验证的是
  "engine 传给 workflow 引擎的 skill_name/inputs 是否符合约定"以及
  "workflow 结果如何解析回 SimState 并落盘"，没有验证
  `life-sim-template` skill 在真实 LLM 下产出的 JSON 质量/稳定性——
  首次真实调用前建议先用一个简单意图手动跑一遍
  `create_simulation`/`advance_simulation` 做人工检查。
- **`state_history.jsonl` 用整体重写实现追加**：见 `store.py::
  SimStore.append_state` 的注释，阶段一实例数据量小时可接受，实例
  历史变得很长后需要换成真正的追加写（不影响调用方签名）。
- **暂不支持并发推进同一实例**：没有加锁，如果同一 `sim_id` 被两个
  进程同时 `advance`，历史/当前状态可能互相覆盖；阶段一场景（CLI
  手动触发）不会遇到，独立看板（阶段二）落地后需要评估是否要加锁。
- **`--template` 目前只有 `life_sim` 一个合法取值**：传其它值会在
  `generate_scenario`/`advance_step` workflow 触发时因找不到对应
  skill（`<template>-template`）而报错，属于预期行为，不是 bug。

## 目录结构

```
world_simulator/
├── project.yaml            # daemon 与本项目之间的契约
├── PROJECT.md               # 本文件
├── requirements.txt          # 独立依赖环境
├── entrypoints/              # headless 单次执行入口
├── world_simulator/         # 业务代码
├── config/                    # 配置文件
├── data/                      # 项目状态数据（账本类）
├── reports/                   # 面向人的产出物
├── tests/                     # 单元测试
└── .agent/
    ├── run_status.jsonl        # 执行状态账本（框架自动读写，见 ledger.py）
    └── improvement_backlog.jsonl  # 改进积压账本（可选，见 backlog.py）
```

## 运行与维护

- 本地单独跑一个 entrypoint：`python entrypoints/<key>.py`（不依赖
  daemon 是否在运行）。
- 注册到 daemon：`mini-agent projects register <本项目路径>`。
- 查看状态：`mini-agent projects status world_simulator`。
- 查看执行历史：`mini-agent projects ledger world_simulator [limit]`。
- 手动触发某个 entrypoint：`mini-agent projects run world_simulator <entrypoint>`。
- 查看/追加改进积压：`mini-agent projects backlog world_simulator list|add`。
- 发起复盘：`mini-agent projects review world_simulator`（需要
  `project.yaml` 里 `review.enabled: true`）。

## 变更记录

- 2026-09-16：由 external-project-manager skill 生成初始骨架。
- 2026-09-16：完成阶段一（脚手架 + 核心闭环）：`state_model.py` /
  `store.py` / `engine.py` / `spec_generator.py`、`generate_scenario`/
  `advance_step` 两个 workflow、`life-sim-template` skill（阶段一首个
  模板）、四个 entrypoint（`create_simulation`/`advance_simulation`/
  `list_simulations`/`health`）、单元测试（`tests/test_state_and_
  store.py`、`tests/test_spec_and_engine.py`）。"意图→提案→确认→推进→
  查看历史"主链路在 CLI 层面可跑通。
- 2026-09-16：完成阶段二（独立看板 MVP）：新增 `app.py`（Streamlit，
  "夜航日志"主题）+ `world_simulator/engine.py::materialize_simulation()`
  （从 `create_simulation()` 拆出"落盘"这一步，供创建向导在生成草稿后
  插入用户编辑/确认环节）。`streamlit run app.py --server.headless
  true` 本地冒烟测试通过（HTTP 200，无异常日志）。分支/对比、自动挡、
  调度尚未实现，见上方"已知限制"与主方案文档第 7 节。
