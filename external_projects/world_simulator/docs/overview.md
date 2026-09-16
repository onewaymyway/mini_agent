# world_simulator 项目说明

## 这是什么

`world_simulator`（万物模拟器）是一个通用的"事物发展模拟引擎"，服务
两类场景：

1. **决策推演分析**：给定当前处境和几个候选选择，模拟出各自的可能
   未来，辅助做决策对比（对应「对比视图」页面）。
2. **持续性模拟/游戏**：创建一个有生命周期的模拟实例，可以推进、暂停、
   恢复、存档分叉，甚至交给"自动挡代理"按你设定的原则持续推进。

目前内置两个场景模板：

- **人生模拟**（`life_sim`）：单个人的人生轨迹。
- **群体演化**（`group_evolution`）：公司/社群/文明等群体的结构性
  发展。

它是 mini_agent 之下的一个**独立外部项目**（`external_projects/
world_simulator/`）：可以完全脱离 mini_agent daemon 单独运行，也可以
注册进 daemon 获得健康检查/定时调度。设计取舍的完整讨论见仓库
`next_doc/world_simulator_external_project_plan.md`；分阶段交付记录见
[`../PROJECT.md`](../PROJECT.md)。

## 核心概念

| 概念 | 说明 |
|---|---|
| **模拟实例（simulation / sim_id）** | 一次具体的模拟，比如"某个应届生的人生"。有自己的标题、模板、当前状态、历史。 |
| **时间步（step）** | 从 0 开始的整数。第 0 步是创建时生成的初始状态；之后每"推进一步"产生一个新的 step。 |
| **状态（state）** | 某一步的快照：一句话摘要 + 叙事文本 + 关键变量（自由 JSON，字段语义由模板决定）+ 若干候选分支选项。 |
| **分支（branch）** | 同一实例可以有多条并行时间线。默认都在 `main` 分支；"回滚重新选"的做法是从某个历史节点开一条新分支，原时间线不被销毁，可以在「对比视图」里并排查看。 |
| **推进模式（手动挡 / 自动挡）** | 手动挡：每一步的选择由你在看板上点。自动挡：配置一个"代理"（原则/偏好/风险偏好/`review_mode`），代理按你的设定自己选，随时可查看进展、可切回手动挡。 |
| **成就（游戏化视图）** | 从既有历史派生出的一组只读徽章（启程/长篇在望/命运转折等），纯展示，不影响任何数据。 |

## 目录结构

```
external_projects/world_simulator/
  project.yaml                # headless entrypoints 声明（供 daemon 调度/可见性）
  PROJECT.md                  # 设计/交付记录（工程视角）
  docs/                       # 本目录：使用者视角的说明文档
    README.md                 #   文档入口/导航
    overview.md                #   本文件：项目说明
    testing_guide.md            #   如何测试/验证
  app.py                       # 独立 Streamlit 看板（核心交互入口）
  world_simulator/             # 业务代码包
    spec_generator.py          #   意图 → 模拟提案草稿
    engine.py                  #   核心推演循环 + 增删改查
    state_model.py              #   State/Manifest 数据结构
    store.py                    #   状态快照/历史持久化
    branch_manager.py           #   分叉/切换/对比
    autopilot.py                 #   自动挡代理决策
    achievements.py               #   游戏化视图的成就徽章计算
  skills/                       # 场景模板库（一个模板 = 一个 skill）
    life-sim-template/
    group-evolution-template/
  workflows/                    # generate_scenario / advance_step 定义
  entrypoints/                  # headless 单次执行入口（CLI）
  data/                         # 模拟实例数据（每实例一个子目录）
  tests/                        # 单元测试
```

## 怎么启动

### 方式一：独立看板（推荐日常使用）

```bash
cd external_projects/world_simulator
pip install -r requirements.txt   # 首次运行，装 streamlit
streamlit run app.py --server.port 8502
```

浏览器打开看板后有 6 个页面：模拟列表、创建向导、实例详情/推进面板、
对比视图、存档管理、游戏化视图。日常创建/推进/查看都在这里完成。

### 方式二：命令行 / headless entrypoint

适合脚本化、daemon 定时调度、或者不想开浏览器时快速验证：

```bash
cd external_projects/world_simulator
python entrypoints/create_simulation.py "一句话模拟意图" --template life_sim
python entrypoints/advance_simulation.py <sim_id>
python entrypoints/list_simulations.py
python entrypoints/health.py
```

两种方式底层用的是同一套 `world_simulator/` 业务代码，数据也存在同一份
`data/` 目录下，随时可以混用（比如看板里创建、CLI 里批量推进）。

### 依赖与配置

- 需要 mini_agent 框架已安装且能加载到 LLM 配置（`create_simulation`/
  `advance_simulation` 内部通过 `mini_agent.config.load_config()` 拿
  `cfg`，本项目自己没有 `agent_config.json`/`providers.json` 时会回退
  继承宿主/注册时记录的 `main_project_root` 的配置）。
- `health.py` 不依赖 LLM/网络，只验证存储层可读，因此哪怕没配好 LLM，
  这个 entrypoint 也应该能跑通——用它来判断"环境本身有没有搭对"。
- 没有装 mini_agent 框架时，看板/CLI 里任何需要调用 LLM 的操作（创建、
  推进）会报错并提示"未检测到 mini_agent 框架"；不需要 LLM 的操作
  （列表、健康检查、删除实例、查看历史/分支/成就）不受影响。

## 怎么继续了解

- 想知道"这个功能怎么验证是好的"：看
  [`testing_guide.md`](./testing_guide.md)。
- 想知道"为什么这么设计 / 各阶段交付了什么 / 已知限制有哪些"：看
  [`../PROJECT.md`](../PROJECT.md)。
- 想知道最初的完整方案讨论：看仓库
  `next_doc/world_simulator_external_project_plan.md`。
