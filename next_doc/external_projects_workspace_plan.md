# 外部项目（用户定制系统）与 daemon 的关系架构设计

> **这篇文档管什么**：mini_agent 作为通用 agent 框架，如何支撑"用户在其上
> 构建的、领域专用、可能有独立数据/独立生命周期的复杂系统"（下称"外部
> 项目"，如 A 股监控分析系统），以及这些外部项目与 mini_agent 主控 daemon
> （下称"大管家"）之间应该是什么关系。
>
> **不管什么**：任何具体外部项目（如股票系统）的业务逻辑本身——那些应该
> 各自建立自己的 `PROJECT.md`/`next_doc`，本篇只定框架层面的通用机制。

## 1. 起源：这个问题是怎么被问出来的

起因是一次具体讨论：如何用 mini_agent 实现一个 A 股监控分析系统（热点
候选池抓取、定时 K 线生成、选股、个股综合分析）。讨论过程中发现，"具体
怎么实现这个股票系统"其实是个相对好回答的问题（无非是把抓取、画图、选
股、分析四类工作分别映射到 skill / workflow / hybrid_exec 上），真正难
的是背后一层更根本的架构问题：

> mini_agent 的能力沉淀机制（skill、workflow、hybrid_exec、memory）本身，
> 应该以什么方式向"用户在框架之上构建的领域系统"开放？这些领域系统和
> mini_agent 的 daemon 进程之间，应该是什么关系？

这个问题如果不先想清楚，直接动手实现股票系统，大概率会把股票系统的代码
和数据直接糊在 daemon 项目目录里、复用 daemon 进程内的全局状态——短期
能跑，但长期会导致：新增第二个、第三个类似系统时无法复用同一套心智模型，
每次都要重新决定"这个系统该放哪、该怎么和 agent 交互"。

因此这篇文档的目标，不是给股票系统定制方案，而是把"mini_agent 框架如何
支撑任意一个这样的外部项目"这件事，作为框架层面的通用能力设计出来。
股票系统只是第一个验证/落地这套机制的具体案例。

## 2. 背景：为什么现有机制不够用

mini_agent 现有的能力沉淀机制（skill 系统、workflow 引擎、hybrid_exec、
self-evolution、memory）目前都隐式假设"只有一个工作根目录"——`project_root`
这个概念贯穿了 `MemoryConfig.store_path`、`SessionConfig.dir` 等配置项的
默认推导逻辑（`None = <project_root>/.agent/...`），而 `project_root`
实际上默认就是 mini_agent 自身所在的项目目录，或者用户交互式启动时所在
的目录。

这在"用户直接用 mini_agent 做交互式编程助手"的场景下没问题——一次会话
一个项目根，天然对应。但当我们讨论"股票监控分析系统"这类需求时，暴露
出几层新矛盾：

1. **数据隔离需求**：股票系统需要自己的候选池账本、K 线缓存、分析报告、
   独立的 memory（"哪个数据源经常挂""历史误判案例"），这些数据不应该
   和 mini_agent 自身的开发记忆、也不应该和未来第二个领域系统（比如一个
   舆情监控系统）混在一起。
2. **生命周期独立需求**：股票系统的核心工作模式是定时批处理（盘前/盘后
   触发），不需要、也不应该依赖 daemon 进程是否在运行——即使 daemon
   从未启动过，用户配一个 OS 级 cron 也该能把它跑起来。
3. **路径独立需求**：股票系统的实际代码/数据路径，用户可能希望放在完全
   不同的位置（不同盘符、独立 git 仓库），而不是 mini_agent 自身的项目
   树下。
4. **"大管家"定位的需求**：daemon 长期定位是 AI 个人助理的主控进程，
   应该能看见、能协助管理用户名下所有在运行的这类外部项目——但"能看见"
   不应该以"外部项目必须依附 daemon 才能存在"为前提，否则跟第 2、3 点
   直接冲突。

这四点分别对应了本文档确立的四条核心原则（见第 4 节）。这些问题不是
股票系统独有的，任何"用户想在 mini_agent 之上搭一个持续运行的领域系统"
都会撞到同样的墙，所以值得作为框架能力解决一次，而不是每个项目各自
想办法绕过去。

## 3. 核心理念

一句话概括整个方案：

> **外部项目是完全自包含、可独立运行的执行单元；mini_agent 的 skill /
> workflow / hybrid_exec / memory 是一套可复用的引擎，通过显式的
> `Workspace` 上下文对外提供，而不是绑定在 daemon 进程内部；daemon 是
> 可选的编排与可观测性层，通过声明式契约与外部项目解耦，而不是外部项目
> 的宿主。**

用两个类比理解这个关系：

- 外部项目与 mini_agent 引擎的关系，类似**一个 Python 脚本与它 import 的
  库**——脚本自己决定何时运行、数据存在哪，库只是被调用，不反向控制脚本
  的生命周期。
- 外部项目与 daemon 的关系，类似 **k8s controller 与它管理的 CRD 资源**，
  或者 **systemd 与 service unit**——控制者通过标准化的声明式清单去发现、
  调度、观察被控者，但从不"托管"被控者的代码或强制被控者依赖自己才能
  运行。

## 4. 核心原则

以下四条原则是本方案所有具体设计的依据，任何后续实现细节如果与某条原则
冲突，应该以原则为准去调整实现，而不是反过来。

### 原则一：引擎与宿主解耦——skill/workflow/hybrid_exec/memory 是接受
显式 `Workspace` 参数的可复用能力，不隐式绑定 daemon 进程或某个固定目录

现状里 `project_root` 隐式等于"当前交互式会话所在目录"，需要升级为一个
显式的 `Workspace` 概念，被所有引擎子系统一致地接受和使用：

```
Workspace(root=<任意路径>)
  → skills 搜索路径  = [root/skills（项目私有）, 全局内置 skills]（分层，本地优先）
  → workflow 定义路径 = root/workflows
  → memory store      = root/memory/
  → session/history    = root/.agent/sessions/
  → 资源策略（网络白名单/并发上限）= root/project.yaml 声明，不继承 daemon 全局策略
```

REPL 交互模式、daemon 模式、外部项目独立运行模式，都只是"同一个引擎、用
不同的 `Workspace.root` 驱动"，不是三套实现。

### 原则二：可独立运行是硬约束，daemon 是可选加成，不是前提

任何外部项目的每一个可执行单元（entrypoint），必须能在完全没有 daemon
进程、甚至没有网络访问 daemon 的情况下，被 `python xxx.py` 或 OS 原生
调度（cron / Windows 计划任务）正确执行、产出正确结果。这是验收标准，
不是"最好如此"——一旦某个 entrypoint 隐式依赖 daemon 的运行时状态（比如
读 daemon 进程内存里的缓存），就违反了这条原则，需要重新设计。

daemon 提供的调度、可观测性、协同维护能力，都是**在这条硬约束满足之后
叠加的便利层**，缺了它，外部项目的核心价值不受影响。

### 原则三：可见性建立在"声明式注册 + 被动可读状态"上，不建立在
"强连接/托管"上

daemon 作为大管家，"看见"一个外部项目在做什么，不应该要求这个外部项目
主动向 daemon 建立连接汇报（那样外部项目又变相依赖 daemon 在线才能正常
工作，违反原则二）。正确方式是两层：

- **注册表**（daemon 侧持有）：一份与 daemon 自身代码树无关的清单，记录
  "有哪些外部项目、路径在哪、怎么触发它、权限边界是什么"。
- **状态账本**（外部项目侧持有）：每个外部项目按统一约定，把自己的执行
  记录写进自己 `workspace` 下的账本文件（`jsonl`，类似
  `agent_commit_guard` 的 ledger 设计），不管这次执行是被 daemon 触发、
  被 OS cron 触发、还是用户手动跑的，都写同一份账本。

daemon 需要知道"某个项目现在情况如何"时，是**去读它的账本**，而不是
要求账本的主人主动上报。这样即使外部项目完全脱离 daemon 独立运行了很
长时间，daemon 依然能在被问起时给出准确、可追溯的回答。

### 原则四：daemon 对外部项目的"深度介入"，走的是"触发一次独立运行"，
不是"in-process 直接操作"

大管家除了展示状态，未来还需要能协助维护外部项目本身（比如某个抓取
脚本因为网站改版失效了，agent 应该能帮忙修）。这类操作**不是**在 daemon
自己的进程里直接修改外部项目的文件，而是复用 self-evolution 已有的
"git worktree 隔离 + 提案 + 验证 + 落地"模式，以**该外部项目自己的
`Workspace` 为根**，触发一次独立的 mini_agent 运行去完成这次维护——
daemon 的角色始终是触发者/协调者，不是执行者本身。这跟原则一、原则三
是一脉相承的：不管是"日常运行"还是"被协助维护"，外部项目的执行都发生
在自己的边界内。

## 5. 整体架构

```
┌───────────────────────────────────────────────────────────┐
│  主控 daemon（唯一常驻、用户实际交互入口，"大管家"）           │
│                                                              │
│  - 外部项目注册表：<name> → path / manifest / enabled        │
│  - （可选）调度触发器：按 manifest 里的 schedule 拉起执行      │
│  - 状态聚合：读取各项目账本，拼成统一 kanban 视图              │
│  - 维护任务派发：以目标项目 Workspace 为根触发独立运行去处理    │
└───────────────────┬──────────────────────────────────────────┘
                     │ 触发（可选）／ 读取账本（被动、始终可用）
       ┌─────────────┼───────────────────┬─────────────────────┐
       ▼                                 ▼                     ▼
 外部项目 A（无状态批处理）        外部项目 B（无状态批处理）   外部项目 C（需要持续在线状态）
 例：A 股监控系统                 例：舆情监控系统             例：实时盯盘 IM 机器人
 - headless 单次执行入口           - 同左                      - 自己的常驻进程（特例，非默认）
 - 自己的 workspace（skills/       - 自己的 workspace           - 仍然遵循原则三写自己的账本
   workflows/memory/data 全部       独立，互不感知              - daemon 尝试连健康检查端口，
   在自己目录下，互相隔离）                                       连不上则退化为读账本
 - 可被 daemon 触发，也可被
   OS cron / 用户手动独立触发，
   结果完全一致
```

### 5.1 外部项目的标准结构

```
<任意路径>/<project_name>/
├── project.yaml            # 唯一的、daemon 与项目之间的契约（见 5.2）
├── PROJECT.md               # 人类可读说明：目标、数据源、已知限制
├── requirements.txt / venv  # 自己的依赖环境，独立于 daemon 的运行环境
├── entrypoints/              # headless 单次执行入口，可被任意调度器调用
│   ├── run_hotlist_scan.py
│   └── run_kline_batch.py
├── skills/                  # 项目私有 skill（复用全局内置 skill 时无需复制）
├── workflows/                # 项目私有 workflow 定义
├── config/
├── memory/                   # 项目私有 memory 命名空间，不与主 agent / 其他项目共享
├── data/                      # 项目状态数据（候选池账本等）
├── reports/                   # 面向人的产出物，与内部状态物理分离
└── .agent/
    └── run_status.jsonl       # 原则三所述的执行状态账本
```

### 5.2 `project.yaml`：daemon 与外部项目之间唯一的契约

```yaml
name: stock_watch
entrypoints:
  hotlist_scan:
    cmd: "python entrypoints/run_hotlist_scan.py"
    schedule: "cron: 0 9,13 * * 1-5"   # 项目自己声明触发时机，daemon 只是可选执行者
    timeout_sec: 600
  kline_batch:
    cmd: "python entrypoints/run_kline_batch.py"
    schedule: "cron: 0 16 * * 1-5"
health_check:
  cmd: "python entrypoints/health.py"   # 可选：daemon 探测项目健康状态的标准方式
resources:
  allowed_domains: ["xueqiu.com", "eastmoney.com", "iwencai.com"]
  max_concurrency: 1
```

daemon 只需要解析这份文件，就能知道要不要调度、什么时候调度、怎么判断
健康、给多大资源配额——完全不需要理解项目内部实现，这是"引擎与宿主
解耦"（原则一）在契约层面的体现。

### 5.3 运行形态的选择标准

不是每个外部项目都需要自己常驻一个进程。判断标准：

> 这个项目的某个工作单元，如果进程退出后重新拉起来再跑一次，结果有没有
> 本质区别？

- **没区别**（绝大多数定时批处理任务，如股票系统的四个功能）→ 默认走
  headless 单次执行，被 daemon 或 OS cron 按需触发，不需要自己的常驻
  进程，更不需要自己的 daemon。
- **有区别**（需要维持连续状态，如保持一个实时行情 WebSocket 连接、
  托管一个即时响应的 IM 机器人）→ 允许该项目按需拥有自己的常驻进程，
  这是特例，不是默认架构。即便如此，它依然要遵循原则三写自己的状态
  账本，daemon 依然优先尝试健康检查、连不上就退化为读账本，而不是
  强依赖一条常开连接。

## 6. 为什么这样设计（对照此前讨论过的备选方案）

- **"每个项目自己起一个完整 daemon"为什么不采用**：daemon 承载的 HTTP
  API / SSE / kanban 等基础设施是为"用户需要持续在线交互"设计的，而
  多数外部项目的实际负载是定时批处理，为一次性任务配一整套常驻服务是
  浪费；且如果每个项目各自起 daemon，调度器、健康检查协议、日志聚合
  这些本该共享的东西会在每个项目里重复实现一遍，与"多项目复用同一套
  编排能力"的目标相悖。
- **"外部项目直接 import daemon 内部模块运行"为什么不采用**：会导致
  daemon 升级/重启打断外部项目的执行，且外部项目的依赖版本被迫与 daemon
  自身耦合，与原则二（可独立运行是硬约束）直接冲突。
- **"daemon 主动轮询系统进程表来发现外部项目"为什么不采用**：外部项目
  完全可能是被 OS cron 拉起、daemon 从未参与这次执行，轮询进程表既
  发现不了这种情况，也和"daemon 不是外部项目存在的前提"这一原则相悖；
  声明式注册表 + 被动账本读取，是唯一在"daemon 可能完全不在场"的情况下
  依然成立的可见性方案。

## 7. 长期规划方向

这套机制第一阶段以股票监控系统为验证案例落地，但设计目标是让它对任意
后续的用户定制系统都成立。长期方向：

1. **多外部项目并存下的资源仲裁**：当多个外部项目同时声明需要访问同一
   个稀缺资源（比如同一个数据源、同一个浏览器 CDP 实例）时，daemon 层
   面需要一个统一的资源仲裁/排队机制，避免各项目在 `resources` 里各自
   声明配额但互相不知道对方存在。
2. **外部项目模板化/脚手架化**：把第 5.1 节的标准结构固化成一个
   `mini_agent new-project` 之类的脚手架命令，新建一个外部项目时自动
   生成骨架，降低"要不要遵循这套约定"的门槛。
3. **维护类交互的标准化**：把"用户对 daemon 说'帮我看看 XX 项目最近怎么样
   /修一下抓取失败的问题'"这类请求，沉淀成大管家的一组标准工具调用
   （list_projects / inspect_project / trigger_run / propose_fix），
   而不是每次都现场现写 prompt 去理解某个具体项目——这是"大管家"定位
   真正落地的关键一步，需要在 self-evolution 的 git worktree 隔离机制
   基础上补一层"面向外部项目的提案-验证-落地"封装。
4. **跨项目的经验沉淀**：如果多个外部项目都遇到类似的问题（比如"某类
   网站的反爬策略变化"），是否值得有一层"跨项目共享、但不共享具体业务
   数据"的经验库？这个目前刻意留白，等真的出现第二、第三个外部项目、
   观察到具体的重复模式之后再决定要不要做，避免过早抽象。
5. **权限模型的精细化**：目前 `project.yaml` 的 `resources` 是声明式
   但相对粗粒度（域名白名单、并发上限），长期看可能需要更细的策略
   （比如按 entrypoint 而不是整个项目声明权限），视实际暴露出的需求
   决定是否做，同样不提前设计。

以上四、五两条刻意保持模糊/留白，是因为在只有一个验证案例（股票系统）
的情况下，过早把"跨项目共享""精细化权限"具体设计出来，大概率会设计
错——应该等第二个外部项目出现、能看到真实的共性和差异之后，再回来
补这部分方案，这是刻意的延迟决策，不是遗漏。

## 8. 具体改造计划

> 约定：每完成一项，回来把对应复选框打勾，并在文末"变更记录"补一行。
> 阶段之间如果因为看到实际代码而需要调整方案，调整记在这里，不静默改。

### 阶段 0：设计确认（本文档）
- [x] 起源、背景、核心理念、核心原则、整体架构、长期规划确认

### 阶段 1：引入显式 `Workspace` 抽象（框架核心改造，其余阶段的地基）
- [x] 新增 `mini_agent/workspace.py`：定义 `Workspace` 数据类，聚合
      skills 搜索路径（本地优先 + 全局内置兜底）、workflow 路径、
      memory store 路径、session 路径、资源策略来源
- [x] 梳理 `config/models.py` 里所有隐式依赖 `project_root` 推导路径的
      配置项（`MemoryConfig.store_path`、`SessionConfig.dir` 等），
      改为从传入的 `Workspace` 派生，而不是从"当前目录"隐式推导
      —— **注意**：这一步只做"支持显式传入"，默认行为（不传时退化为
      现状的隐式推导）保持不变，避免破坏现有交互式使用方式
      —— **核实结果（调整记录，见下）**：`MemoryConfig.store_path` /
      `SessionConfig.dir` 已经是"None = 从 `AgentPaths(cfg.project_root)`
      派生"的约定（`memory_factory.py::_load_local`、`session.py`
      `SessionManager.__init__` 均已如此实现），不需要新增一层单独的
      "从 Workspace 派生"逻辑——`Workspace.apply_to(cfg)` 只需要设置
      `cfg.project_root = self.root`，已有派生链路自动生效，改动量比
      原计划小，行为完全等价
- [x] skill loader 支持多路径分层搜索（项目私有优先，全局内置兜底），
      当前是否已支持需要先核实（见 `tools/skill_manager.py`），如未
      支持则补上
      —— **核实结果**：`SkillLoader` 本身已支持多目录构造（按目录顺序
      加载，同名后者覆盖前者），`workflow/resource_bundle.py::
      WorkflowResourceBundle._build_skill_loader()` 已经在用"全局目录
      在前、本地目录在后"的分层约定，只是这个约定被锁在 workflow 私有
      资源包里、没有作为通用能力暴露。`Workspace.skills_search_dirs`
      / `Workspace.build_skill_loader()` 把同一约定提升为可独立于
      workflow 上下文复用的通用方法，不改动 `SkillLoader` 本身
- [x] 单元测试：同一份代码用两个不同 `Workspace.root` 分别跑一次，
      验证 memory/session/skill 解析结果完全隔离、互不污染
      —— `tests/test_workspace.py`，8 个用例全部通过（隔离性 + 本地
      优先覆盖 + `apply_to` 不越权改动其它字段 + 路径规范化）

### 阶段 2：CLI headless 单次执行入口
- [x] 新增 `mini_agent run --workspace <path> --workflow <name>` 命令
      （或复用已有 `cli/commands/workflow_cmd.py` 扩展 `--workspace`
      参数，具体走哪条路径待看现有 `workflow_cmd.py` 实现后再定）
      —— **核实结果（调整记录）**：读代码发现 `mini-agent workflow run
      <name> --project <path>`（`run_workflow_cli()`）已经完整具备这
      个入口的全部特征——只 `load_config(project_root=...)`、不构造
      Agent、不初始化 HTTP/SSE/kanban、前台分支是同步调用跑完即退出。
      新增一条并行的 `mini-agent run` 命令会违反项目"通用/架构解而不是
      一次性 workaround"的既有约定（重复实现同一件事）。因此改为最小
      改动：把 `--workspace`/`-w` 加成 `--project`/`-p` 的别名（见
      `cli/app.py::_extract_project_root`），语义完全一致，只是术语上
      对齐 `Workspace` 概念，供外部项目场景使用；`workflow_cmd.py`
      顶部 docstring 补充说明这条路径即阶段 2 的 headless 入口
- [x] 该命令只加载对应 `Workspace`，不初始化 HTTP API / SSE / kanban
      等 daemon 专属基础设施；执行完退出，产出结构化结果（复用现有
      `WORKFLOW_RESULT_FILE_PATH` 模式）
      —— 结构化结果复用的是已有的 `WorkflowSession`/`step_results`
      落盘机制（`.agent/workflow_sessions/<id>/`），而不是
      `WORKFLOW_RESULT_FILE_PATH`——后者核实后发现是 `python_step`/
      `script` 类型 step 内部"子进程如何把结果回传给 runner"的机制，
      不是"整条 CLI 调用的结果"该用的东西，两者不是同一层次的概念，
      这里不套用避免张冠李戴
- [x] 验收标准：该命令可以被 OS 原生 cron / Windows 计划任务直接调用，
      在 daemon 完全没有启动的情况下产出正确结果
      —— 用 `tests/test_workflow_cli_headless.py` 做了单测层面的验证
      （非真实 cron 环境，但验证了等价条件）：纯 `script` 类型 step
      （不依赖 LLM/网络）跑通全流程、执行前后 `mini_agent.api.*`
      模块集合不变（证明没有引入任何 daemon/HTTP 依赖）、两个不同
      `--project`/`--workspace` 根之间执行记录完全隔离；另新增
      `--workspace`/`-w` 别名解析测试

### 阶段 3：`project.yaml` 契约 + daemon 侧外部项目注册表
- [x] 定义 `project.yaml` 的 schema（`entrypoints` / `health_check` /
      `resources`），落一份 JSON Schema 或 pydantic 模型供校验
      —— **调整记录**：未引入 pydantic/JSON Schema，改为手写
      dataclass（`EntrypointSpec`/`HealthCheckSpec`/`ResourceSpec`/
      `ProjectManifest`）+ 显式校验函数（`external_projects/
      manifest.py::parse_manifest()`），与 `workspace.py` 已经确立的
      "数据类 + 显式校验"风格保持一致，且不为一份足够小的 schema 新增
      依赖
- [x] daemon 侧新增外部项目注册表存储（如
      `~/.mini_agent/external_projects.json`，与 daemon 自身代码树
      无关的独立位置），支持增/删/查已注册项目
      —— `external_projects/registry.py::ExternalProjectRegistry`，
      JSON 文件存储，`register()` 默认顺带校验目标 `project.yaml`
      是否合法（可用 `validate=False` 跳过），注册表文件损坏时按原则三
      的精神退化为"当前没有已注册项目"而不是抛异常炸掉调用方
- [x] daemon 侧新增按 `project.yaml` 里 `schedule` 触发对应
      entrypoint 的调度器（复用 workflow 引擎已有的 subprocess
      isolation / watchdog 机制，调用目标从"daemon 内部脚本"换成
      "外部项目的 headless 入口"）
      —— **调整记录**：核实后发现 workflow 的 watchdog/runner 是围绕
      "workflow step"这个更重的概念设计的，而外部项目 entrypoint 只是
      "任意一条 shell 命令"，假设比 workflow step 少得多；直接复用会
      引入不必要的耦合，因此改为 `external_projects/scheduler.py` 里
      用标准库 `subprocess.run` 独立实现最小化的 cron 匹配
      （`cron_matches()`，支持 `*`/单值/逗号列表/`-`区间，不支持步进）
      + 触发执行（`run_due_entrypoints()` 供 daemon 后台循环调用，
      `trigger_run()` 供 CLI `projects run` 复用同一条执行路径）。
      daemon 后台循环真正接入 `run_due_entrypoints()` 定时调用，留待
      daemon 主循环模块实际改造时再做（本阶段先把可独立调用、可单测的
      调度判断+触发逻辑做完，接入点是后续的薄改动，不影响验收）
- [x] `/commit-guard` 之外新增一个类似的 `/projects` 系列 CLI 命令
      （`list` / `status <name>` / `run <name> <entrypoint>` /
      `register <path>` / `unregister <name>`），参照
      `agent_commit_guard_guide.md` 的文档模式单独出一份使用指南
      —— **调整记录**：`/commit-guard` 是 REPL 内斜杠命令；核实后发现
      更贴近 `mini-agent projects ...` 使用场景（脚本/cron 里独立调用，
      不需要先进交互模式）的参照对象是 `workflow`/`daemon`/`user`/
      `self` 这几个已有的顶层 CLI 短路子命令（`cli/app.py::main()`），
      因此 `projects_cmd.py` 按这个模式实现（新增 `list`/`status`/
      `run`/`register`/`unregister`，并追加了 `enable`/`disable` 便于
      不移除注册也能临时关闭 daemon 侧调度），在 `cli/app.py` 里新增
      `sys.argv[1] == "projects"` 短路分支；使用指南见新增的
      `docs/external-projects-guide.md`（`agent-commit-guard-guide.md`
      的文档分层模式）

### 阶段 4：状态账本约定 + daemon 侧状态聚合
- [x] 定义 `<project_root>/.agent/run_status.jsonl` 的标准 schema
      （entrypoint / started_at / finished_at / exit_code / trigger /
      错误摘要），trigger 字段区分 `daemon` / `external_cron` /
      `manual`
      —— `external_projects/ledger.py::RunRecord`
  - [x]（2026-08-27 追加）针对实际使用中暴露的两个问题做了增强：
    1. **时间字段改为本地时间**：早期版本 `started_at`/`finished_at`
       存的是 `datetime.now(timezone.utc)`，用户在看板里看自己本地
       白天的一次执行，账本上却显示凌晨时间，容易造成"是不是延迟了/
       是不是跑错时间了"的误解。统一改成
       `datetime.now().astimezone().isoformat()`（本机 wall-clock +
       本机时区偏移量），序列化出来仍是无歧义的 ISO-8601（例如
       `2026-08-27T12:34:42+08:00`），前端/CLI 不需要再做任何时区
       换算就能直接显示成用户认知里的时间。老账本里遗留的 UTC 记录
       不做迁移，本身仍是合法 ISO-8601。
    2. **新增 `detail` 字段**：`error_summary` 只是一行摘要（比如
       `entrypoint exited with code 1`），排查问题时完全没有信息量，
       用户必须额外去翻 daemon 日志或项目自己的日志文件。新增
       `detail: str | null` 字段承载更完整的诊断信息，按触发路径分
       两种来源：
       - `scheduler.py::_run_entrypoint()`（daemon/CLI/看板手动触发
         的子进程路径）：改为 `capture_output=True` 捕获子进程
         stdout/stderr，失败或超时时把 stderr（优先）+ stdout 尾部
         拼进 `detail`；`EntrypointRunResult` 也带上这个字段，手动
         触发的 HTTP 响应（`POST /v1/external_projects/<name>/run`）
         和 CLI `projects run` 都直接透出，不需要用户再去查账本。
       - `ledger.py::track_run()`（entrypoint 脚本自己 import 使用
         的路径）：块内抛异常时自动把完整 `traceback.format_exc()`
         存进 `detail`；如果脚本自己已经知道更精确的诊断信息（比如
         "候选池 N 只标的分别因为什么原因失败"），可以在异常抛出前
         主动设置 `handle.detail = "..."`，`track_run()` 检测到已被
         设置就不再用 traceback 覆盖。`stock_watch` 的
         `entrypoints/_common.py` 在此基础上包了一层
         `set_run_detail(text)` 便捷函数，供 `run_kline_batch.py`
         这类"部分失败不算错、全部失败才报错"的批处理脚本记录失败
         明细（哪些标的、各自什么原因）。
       - `detail` 统一在 `record_run()` 内部截断到
         `ledger.MAX_DETAIL_CHARS`（4000 字符，头部 2/3 + 尾部
         1/3，防止一次异常输出很长时账本文件膨胀失控）。
       —— 涉及改动：`external_projects/ledger.py`（新增
       `truncate_detail()`/`_now_local_iso()`，`RunRecord`/
       `record_run()`/`track_run()` 加 `detail` 字段）、
       `external_projects/scheduler.py`（捕获子进程输出、
       `EntrypointRunResult.detail`）、`api/routes.py`（手动触发
       响应带 `detail`）、`cli/commands/projects_cmd.py`（`status`/
       `ledger`/`run` 三个子命令打印 `detail`）、
       `external_projects/stock_watch/entrypoints/_common.py`
       （`set_run_detail()`）、
       `external_projects/stock_watch/entrypoints/run_kline_batch.py`
       （示范用法：候选池全部失败时记录逐个标的的失败原因）
- [x] 提供一个轻量库函数（外部项目的 entrypoint 里 import 调用即可）
      负责往这份账本写记录，降低外部项目遵循这个约定的成本，避免
      每个项目自己重新实现一遍写账本逻辑
      —— `ledger.py::record_run()`（底层单次写入）+ `ledger.py::
      track_run()`（推荐用法，`with track_run(root, key, trigger=...)`
      上下文管理器，自动填 `started_at`/`finished_at`，块内抛异常自动
      记为失败并把异常信息写进 `error_summary`，异常本身照常向外抛出
      不吞掉）；`scheduler.py::_run_entrypoint()`（阶段 3 已实现的
      daemon/CLI 触发路径）已经改为触发后自动调用 `record_run()`，不
      需要外部项目自己在 entrypoint 里重复处理"被 daemon/CLI 触发"这
      一种来源，只有"被 OS cron 直接触发、完全绕过 mini-agent"这种
      来源才需要脚本自己用 `track_run()` 上报（`trigger="external_cron"`）
- [x] daemon 侧新增"读取已注册项目账本 → 聚合成统一视图"的逻辑，
      接入现有 kanban dashboard 展示
      —— `external_projects/status.py::aggregate_status()`（单项目
      失败不传染，逐项目 try/except）；新增只读端点 `GET /v1/self/
      external_projects`（`api/routes.py`，仿照 `/self/
      fairness_diagnostics` 的 owner-only + 异常降级为空列表的模式）
      直接把聚合结果透给 daemon 前端；CLI 侧 `projects list`（追加
      `LAST_RUN` 列，只读账本不触发探测）、`projects status`（展示
      health + 最近一次执行 + 最近 5 条记录）、新增 `projects ledger
      <name> [limit]`（完整账本浏览）三处一并接入
- [x] （若项目声明了 `health_check`）daemon 侧新增健康检查探测，
      探测失败时退化为读取账本最后一条记录，而不是报错中断
      —— `status.py::probe_health()`（30s 超时，命令本身抛异常按
      False 处理）+ `project_status_snapshot()`（未声明/探测不了时
      退化为账本最后一条记录，账本也没有时才是 `"unknown"`，全程不
      抛异常中断调用方）

### 阶段 5：维护类交互标准化（大管家能力）
- [x] 设计"以目标外部项目 `Workspace` 为根触发独立运行"的标准调用
      方式，复用 self-evolution 现有的 git worktree 隔离 + 提案验证
      落地流程，评估是否需要为"外部项目"场景做适配（外部项目未必是
      mini_agent 自身仓库的一部分，git worktree 是否适用需要先验证）
      —— **评估结论**：`StateRepo`/`EvolutionWorkspace` 从设计上就
      只依赖"传入的 root 是/能成为一个 git 仓库"，不假设 root 是
      mini_agent 自身仓库的一部分（`StateRepo._ensure_initialized()`
      在没有 `.git` 时会自动 `git init`），因此对外部项目开箱即用，
      不需要任何适配层。新增 `external_projects/maintenance.py::
      propose_maintenance_fix()`/`land_maintenance_fix()`，直接复用
      这两个既有类，只是把"以外部项目自己的目录为根"这件事显式包一
      层，不新增任何隔离逻辑；tier 默认改为 `T2`（lint + 目标项目自己
      `tests/`，若有），而不是 `skill_propose` 固定用的 `T1`——`T1`
      的"声明式资产加载校验"是针对 SKILL.md 等 mini_agent 特有资产
      设计的，对外部项目的任意脚本文件不适用
- [x] daemon 侧新增一组标准工具供大管家 agent 调用：`list_projects`
      `inspect_project`（读 manifest + 最近执行账本 + 日志）
      `trigger_run` `propose_fix`
      —— 新增 `src/mini_agent/tools/external_projects.py`（4 个
      `@tool`），仿照 `tools/evolution.py::skill_propose` 的
      "JSON 字符串返回、失败不抛异常"约定；`list_projects`/
      `inspect_project` 只读、`requires_approval=False`，`trigger_run`
      会真的执行外部项目自己的代码，保留 `requires_approval=True`，
      `propose_fix` 落在独立分支不影响当前 checkout、把关在校验流水线，
      `requires_approval=False`（与 `skill_propose` 同一取舍）。在
      `cli/app.py` 的内置工具 side-effect import 列表里新增一行接入
- [x] 端到端验证：模拟"某个外部项目的抓取脚本因网站改版失效"场景，
      走完整的"发现问题 → 提案改动 → 验证 → 落地"链路
      —— `tests/test_external_projects_maintenance.py::
      test_propose_fix_tool_end_to_end_scrape_repair`：`trigger_run`
      触发一个会抛异常的脚本（模拟失效）→ 账本记下失败、
      `inspect_project` 读到 `health="unhealthy"` → `propose_fix`
      提交修复到独立分支（当前 checkout 仍是坏的）→
      `land_maintenance_fix()` 合并落地 → 再次 `trigger_run` 确认
      `health="healthy"`。**范围说明**：这是单测层面对"整条链路"的
      端到端验证（真实调用每个工具函数，不 mock 中间环节），不是接入
      真实大管家 agent 会话的验证——后者要等阶段 6 有真实外部项目、
      或明确需要时再做，避免为了"更真实"而引入不必要的测试基础设施

### 阶段 6：股票监控系统作为首个落地案例
- [x] 按第 5.1 节标准结构，在阶段 1～4 完成后（不需要等阶段 5）新建
      `stock_watch` 外部项目，验证整套机制在真实场景下是否顺畅
      —— 落地在 `external_projects/stock_watch/`（`external_projects/`
      是一个不属于 mini_agent 自身代码树逻辑的普通目录，只是当前先放
      在仓库里方便随代码一起分发；可以整体移动到任意路径/独立 git 仓库，
      移动后只需重新 `mini-agent projects register <新路径>`，不需要
      改任何一行 `stock_watch` 自己的代码——这也是本次落地对"路径独立
      需求"（第 2 节问题 3）的直接验证）。实现了需求提出的全部四项
      功能，均落成独立 entrypoint：`hotlist_scan`（热点候选池抓取，
      多数据源打分合并 + 淘汰 + Markdown 报告）、`kline_batch`（候选池
      全量标的 K 线批量生成，股票/ETF 分开走 akshare 对应接口）、
      `screener`（选股，直接复用问财 iwencai 自然语言选股结果，不
      自研技术指标引擎，采纳用户在需求里提出的思路）、`stock_analysis`
      （个股综合分析，抓取公告/股吧帖子/新闻，结构化材料留给上层
      mini_agent 会话做 LLM 综合研判，本项目只负责收集/结构化）。
      数据源策略：优先 `akshare`（免费、封装好行情/K线/公告/新闻/部分
      热榜接口），`akshare` 覆盖不到的（股吧帖子网页版兜底、问财网页版
      结果）用项目自己的 `data_sources.py::fetch_html()` 轻量抓取
      （UA + 超时 + 重试退避 + 限速），全程没有引入付费数据源。
- [x] 记录落地过程中暴露出的、本文档未预料到的问题，回填进本文档的
      "变更记录"和相关章节 —— 见下方变更记录，核心发现：(1) 框架此前
      对"外部项目的 entrypoint 完全脱离 mini_agent 环境独立运行"这个
      场景，缺一个"账本写入失败时静默降级"的公共封装——`track_run()`
      本身没问题，但要求调用方能 `import mini_agent`，直接
      `python entrypoints/xxx.py` 独立运行、且 mini_agent 未装进同一
      环境时会 `ImportError`；本次在 `stock_watch` 自己的
      `entrypoints/_common.py` 里补了一层 `tracked_run()` 包装做
      `try/except ImportError` 降级，这是"外部项目自己实现"还是"框架
      该提供一个可选依赖的官方 helper"，留到出现第二个外部项目、能看到
      是否是共性问题时再决定要不要把这层降级逻辑上收进框架本身（呼应
      第 7 节"跨项目经验沉淀"的刻意留白原则，不在只有一个案例时提前
      抽象）；(2) 端到端验证发现 `track_run()`/`record_run()` 只识别
      `Exception`，而 Python 惯用的 `raise SystemExit(exit_code)` 继承
      `BaseException` 不会被捕获，会导致"entrypoint 实际以非零码退出，
      账本却错记成功"——这不是框架 bug（`scheduler.py::_run_entrypoint`
      走 subprocess + 自己判断退出码，不受影响，阶段 4 的验收本来就是
      针对这条路径），而是"entrypoint 脚本自己在 `tracked_run` 块内
      直接 `sys.exit()`"这种写法的通用陷阱，`stock_watch` 的
      `_common.py::run_entrypoint()` 里用"非零返回值转普通异常"的方式
      规避，这个模式值得未来写脚手架/文档时提醒用户。

## 9. 涉及文件清单（预期，具体以各阶段实施时为准）

- 新增 `src/mini_agent/workspace.py` — `Workspace` 核心抽象
- 修改 `src/mini_agent/config/models.py` — 支持从显式 `Workspace` 派生
  路径配置
- 修改/新增 `src/mini_agent/tools/skill_manager.py` — 分层 skill 搜索
- ~~新增 `src/mini_agent/cli/commands/run_headless.py`~~ — 阶段 2 核实
  后未新增，复用 `workflow_cmd.py` 现有入口（见阶段 2 变更记录）
- [x] 新增 `src/mini_agent/external_projects/` — `manifest.py`
  （`project.yaml` 解析/校验）、`registry.py`（注册表增删查改）、
  `scheduler.py`（cron 匹配 + 触发执行 + 自动记账）、`ledger.py`
  （账本 schema + `record_run`/`track_run`/`read_ledger`）、
  `status.py`（health_check 探测 + 退化到账本 + 聚合视图）
- [x] 新增 `src/mini_agent/cli/commands/projects_cmd.py` — `projects`
  系列 CLI 命令（`list`/`status`/`run`/`register`/`unregister`/
  `enable`/`disable`/`ledger`），在 `cli/app.py` 新增短路分支接入
- [x] 修改 `src/mini_agent/api/routes.py` — 新增 `GET /v1/self/
  external_projects` 只读端点，透出 `status.py::aggregate_status()`
  聚合结果供 daemon 前端 kanban 使用
- [x] 新增 `docs/external-projects-guide.md` — 功能"毕业"后的稳定使用
  指南（参照 `docs/agent-commit-guard-guide.md` 的文档分层模式，本
  `next_doc/` 文档保留作为设计考古记录）
- [x] 新增 `src/mini_agent/external_projects/maintenance.py` —
  `propose_maintenance_fix()`（复用 `EvolutionWorkspace` 的 git
  worktree 隔离，在目标外部项目自己仓库的独立分支上尝试一次改动并
  校验）、`land_maintenance_fix()`（人工 review 通过后合并分支）
- [x] 新增 `src/mini_agent/tools/external_projects.py` — 大管家标准
  工具集 `list_projects`/`inspect_project`/`trigger_run`/
  `propose_fix`，在 `cli/app.py` 内置工具 side-effect import 列表里
  新增一行接入
- [x] （案例）新增 `external_projects/stock_watch/` 项目 — 按第 5.1
  节标准结构落地：`project.yaml`、`PROJECT.md`、`requirements.txt`、
  `entrypoints/`（`run_hotlist_scan.py`/`run_kline_batch.py`/
  `run_screener.py`/`run_stock_analysis.py`/`health.py`/`_common.py`）、
  `stock_watch/`（`config.py`/`data_sources.py`/`candidate_pool.py`/
  `kline.py`/`screener.py`/`analysis.py`/`report.py`）、
  `config/watchlist.yaml`、`tests/test_offline_logic.py`。当前物理放在
  mini_agent 仓库内只是分发方便，逻辑上不属于 mini_agent 自身代码树，
  可整体移动到任意路径/独立 git 仓库，只需重新注册

## 10. 变更记录

- 2026-08-26：文档创建。方案确认，来源于"A 股监控分析系统"需求讨论
  过程中逐步收敛出的、面向所有外部定制系统的通用架构设计。进入阶段 0
  确认完成，尚未开始阶段 1 的代码改造。
- 2026-08-26：阶段 1 完成。新增 `src/mini_agent/workspace.py`
  （`Workspace` 数据类：`root`/`global_skills_dir` 构造，派生
  `skills_dir`/`skills_search_dirs`/`workflows_dir`/
  `memory_store_path`/`sessions_dir`/`data_dir`/`reports_dir`/
  `run_status_path`（阶段4预留）/`project_yaml_path`（阶段3预留）；
  `build_skill_loader()`、`apply_to(cfg)`）。核实发现 `MemoryConfig.
  store_path`/`SessionConfig.dir` 早已是"None 时从
  `AgentPaths(project_root)` 派生"的既有约定，`SkillLoader` 也早已
  支持多目录分层加载（`workflow/resource_bundle.py` 里已有先例）——
  因此本阶段的落地方式比原计划更轻量：`Workspace` 复用这些既有派生
  逻辑，只新增一个显式、可独立传递的入口对象，未修改
  `config/models.py`/`skills/__init__.py` 本身的任何行为，向后完全
  兼容。新增 `tests/test_workspace.py`（8 用例，覆盖 memory/session/
  skill 三类路径的跨 workspace 隔离性、本地 skill 覆盖全局同名 skill、
  `apply_to()` 不越权修改其它已有配置字段、路径规范化），全部通过；
  抽查跑过 `tests/test_workflow_p11.py` 确认改动未引入新的回归（唯一
  失败项是既有已知的 flaky 计时测试，与本次改动无关）。
- 2026-08-26：阶段 2 完成。核实发现 `cli/commands/workflow_cmd.py::
  run_workflow_cli()`（`mini-agent workflow run <name> --project
  <path>`）已经是原计划要新增的 headless 单次执行入口，因此没有新增
  并行的 `mini-agent run` 命令，改为给 `--project`/`-p` 加一个
  `--workspace`/`-w` 别名（`cli/app.py::_extract_project_root`），
  并在 `workflow_cmd.py` 顶部 docstring 里把这条已有路径与阶段 2 的
  验收标准显式对应起来。新增 `tests/test_workflow_cli_headless.py`
  （3 用例）：别名解析等价性、纯 `script` step 端到端同步执行成功且
  过程中不引入任何 `mini_agent.api.*`（daemon/HTTP）模块、两个不同
  workspace 根之间的工作流定义与执行记录完全隔离，全部通过。阶段 3
  （`project.yaml` 契约 + daemon 侧外部项目注册表）待开始。
- 2026-08-26：阶段 3 完成。新增 `src/mini_agent/external_projects/`
  三个模块：`manifest.py`（`project.yaml` schema，手写 dataclass +
  校验函数，未引入 pydantic/JSON Schema，理由见阶段 3 变更记录）、
  `registry.py`（`ExternalProjectRegistry`，JSON 文件存储于
  `~/.mini_agent/external_projects.json`，register/unregister/list/
  get/set_enabled，register 默认顺带校验 manifest 合法性）、
  `scheduler.py`（最小化 cron 匹配 `cron_matches()` + `_run_entrypoint()`
  subprocess 执行 + `run_due_entrypoints()`/`trigger_run()`，未直接
  复用 workflow watchdog，理由见阶段 3 变更记录）。新增
  `src/mini_agent/cli/commands/projects_cmd.py`（`mini-agent projects
  list/status/run/register/unregister/enable/disable`），仿照
  `workflow`/`daemon`/`user`/`self` 的短路模式接入 `cli/app.py::main()`。
  新增 `docs/external-projects-guide.md`（面向用户的稳定使用指南）。
  新增 `tests/test_external_projects.py`（23 用例：manifest 解析
  合法/非法各类场景、registry 增删查改/重复注册/损坏文件容错/多
  store_path 隔离、scheduler cron 匹配与到期触发、CLI 端到端），全部
  通过；另外用真实子进程跑了一遍 `python -m mini_agent projects
  register/status/run/unregister` 全流程做端到端冒烟验证（非纯单测，
  确认命令在裸 `HOME` 环境下、不依赖 daemon 也能正确工作），抽查跑过
  `tests/test_workspace.py`、`tests/test_workflow_cli_headless.py`
  确认改动未引入回归，全部通过。阶段 4（状态账本约定 + daemon 侧状态
  聚合）待开始；daemon 主循环真正接入 `run_due_entrypoints()` 定时调用
  这一具体接线动作留待阶段 4/5 实际改造 daemon 主循环时顺带做（见阶段
  3 第三项调整记录），不阻塞当前验收。
- 2026-08-26：阶段 4 完成。新增 `external_projects/ledger.py`
  （`RunRecord` schema + `record_run()` 底层写入 + `track_run()`
  上下文管理器推荐用法 + `read_ledger()`/`last_record()` 读取，损坏行
  跳过不炸整份账本）；`scheduler.py::_run_entrypoint()` 改为触发后
  自动调用 `record_run()`（成功/非零退出码/超时三种结果都会记账，
  `trigger` 原样透传 `"daemon"`/`"manual"`），阶段 3 的
  `trigger_run`/`run_due_entrypoints` 因此不需要额外改动就自动获得
  记账能力。新增 `external_projects/status.py`（`probe_health()`
  30s 超时探测 + `project_status_snapshot()` 三级退化：health_check
  → 账本最后一条 → `"unknown"` + `aggregate_status()` 批量视图、
  单项目失败不传染）。`api/routes.py` 新增 `GET /v1/self/
  external_projects` 只读端点（owner-only，仿照 `/self/
  fairness_diagnostics` 的异常降级模式）。`cli/commands/
  projects_cmd.py` 的 `list` 追加 `LAST_RUN` 列（只读账本，不主动探测
  health_check，保持"被动可读"）、`status` 追加 health + 最近执行
  展示、新增 `ledger <name> [limit]` 子命令浏览完整账本。新增
  `tests/test_external_projects_ledger_and_status.py`（22 用例：
  账本写读/损坏容错/`track_run` 成功与异常两条路径、health_check
  探测 true/false/未声明、三级退化的每一级、聚合视图多项目/空注册表、
  scheduler 触发后自动记账的成功与失败两条路径、CLI `ledger` 子命令
  端到端），全部通过；连同阶段 3 测试共 52 用例全部通过。另外用真实
  子进程跑了一遍 `register → run → list → status → ledger` 全流程
  （含 `health_check` 声明），确认 `status` 里 `health_source` 正确
  优先取 `health_check` 结果而不是账本。daemon 主循环真正定时调用
  `run_due_entrypoints()`、以及第 5 节的"维护类交互标准化"（阶段 5）
  待开始。
- 2026-08-26：阶段 5 完成。新增 `external_projects/maintenance.py`：
  `propose_maintenance_fix()` 以目标外部项目**自己的目录**为根（不是
  mini_agent 自身仓库），复用 `evolution/state_repo.py::StateRepo` +
  `evolution/workspace.py::EvolutionWorkspace` 的 git worktree 隔离，
  在独立 `evolve/<date>-fix-<slug>` 分支上尝试改动并按 tier 校验（默认
  `T2`：lint 改动的 `.py` 文件 + 若目标项目自己有 `tests/` 则跑一遍），
  校验失败不落盘、不 commit；`land_maintenance_fix()` 是显式的分支合并
  （`StateRepo.merge_branch()` 薄封装），不由 `propose_maintenance_fix()`
  自动调用，落地始终是独立动作（呼应原则四）。**评估结论**（阶段 5
  第一项要求）：`StateRepo`/`EvolutionWorkspace` 从设计上就只依赖"传入
  的 root 是/能成为一个 git 仓库"，不假设 root 是 mini_agent 自身仓库
  的一部分（无 `.git` 时会自动 `git init`），因此对外部项目开箱即用，
  不需要任何新的隔离逻辑或适配层。新增 `src/mini_agent/tools/
  external_projects.py`：大管家标准工具集 `list_projects`/
  `inspect_project`/`trigger_run`/`propose_fix`（`@tool` 装饰器注册，
  仿照 `tools/evolution.py::skill_propose` 的"JSON 字符串返回、失败
  不抛异常"约定；`trigger_run` 会真的执行外部项目自己的代码，保留
  `requires_approval=True`，其余三个把关在只读或独立分支+校验流水线，
  `requires_approval=False`），在 `cli/app.py` 内置工具 side-effect
  import 列表新增一行接入。新增 `tests/test_external_projects_
  maintenance.py`（11 用例：fresh-repo 自动 git init、分支与当前
  checkout 隔离、校验失败不落盘不新增分支、`land_maintenance_fix`
  合并、四个工具的独立单测、以及阶段 5 第三项要求的端到端场景——模拟
  抓取脚本因网站改版失效：`trigger_run` 触发失败并记账 →
  `inspect_project` 读到 `health="unhealthy"` → `propose_fix` 提交
  修复到独立分支（当前 checkout 仍是坏的）→ `land_maintenance_fix`
  落地 → 再次 `trigger_run` 确认 `health="healthy"`），全部通过；连同
  阶段 3/4 测试共 100 用例全部通过，未引入回归。**范围说明**：端到端
  验证是单测层面对整条链路的真实调用（不 mock 中间环节），不是接入
  真实大管家 agent 会话的验证，后者留给阶段 6 有真实外部项目、或明确
  需要时再做。阶段 6（股票监控系统作为首个落地案例）待开始。
- 2026-08-26：阶段 6 完成。新增 `external_projects/stock_watch/`，
  用户需求原文的四项功能均已实现为独立 headless entrypoint（见本节
  阶段 6 复选框下的详细说明）。数据源全部走免费方案：`akshare` 为主，
  `requests`+`BeautifulSoup` 做 `akshare` 覆盖不到的网页兜底（股吧
  帖子、问财自然语言选股网页版结果），未引入任何付费接口。**验证方式
  与范围说明**：沙箱构建环境没有到财经网站/`akshare` 数据源的出网
  权限（网络白名单只包含包管理器域名），因此本次验证分两层——(1)
  `tests/test_offline_logic.py`（7 用例，候选池合并/去重/衰减/淘汰/
  账本读写容错/报告渲染，全部用固定 mock 数据，不需要网络，全部通过）；
  (2) 装真实 `akshare`/`requests`/`beautifulsoup4`/`mplfinance` 依赖后，
  完整跑通了一遍框架集成链路：`mini-agent projects register` 注册 →
  `projects status` 读到 manifest/health_check 正常 → `projects run
  stock_watch hotlist_scan` 实际触发（因沙箱无出网权限，三个数据源
  依次抓取失败，但**行为完全符合设计**：种子标的仍进入候选池、报告
  正常生成、失败原因逐条记录进日志、整体判定为失败并把 `exit_code=1`
  正确写入账本）→ `projects status`/`projects ledger` 能读到这条失败
  记录 → 用框架 `manifest.py::load_manifest()` 直接校验
  `project.yaml`，字段解析（entrypoints/cron/health_check/resources）
  全部正确。这一层验证的是"框架机制在真实（但网络失败）条件下的行为
  正确性"，不是"抓取逻辑本身在真实网络下能拿到正确数据"——后者见
  PROJECT.md「已知限制」一节，需要用户在有网络的机器上首次运行时核对
  `akshare` 具体接口签名/返回列名、股吧与问财的页面结构是否与代码假设
  一致，这类问题正是阶段 5"维护类交互标准化"（`propose_fix`）机制要
  承接的典型场景。落地过程中的两处框架层发现见本节阶段 6 第二项复选
  框下的说明（entrypoint 独立运行时的账本写入降级、`SystemExit` 不被
  `track_run` 捕获的通用陷阱）。至此 `next_doc/
  external_projects_workspace_plan.md` 规划的全部 6 个阶段均已完成；
  后续如果出现第二个外部项目，再回来处理第 7 节里刻意留白的"跨项目
  经验沉淀"与"权限模型精细化"两项。
- 2026-08-27：用户实测反馈——`stock_watch` 的 `kline_batch` 在真实
  daemon/看板环境下执行 `returncode=1`，但用户能看到的诊断信息只有
  账本里一行 `error_summary: "entrypoint exited with code 1"`（或
  `"kline_batch 以非零退出码结束: 1"`），完全无法定位是哪只标的、
  什么原因失败；同时账本时间戳是 UTC，用户本地白天执行的一条记录显示
  成了凌晨，容易造成误解。据此对阶段 4 的账本 schema 做了一次不破坏
  兼容性的增强（详见阶段 4 复选框下方 2026-08-27 追加说明）：
  `started_at`/`finished_at` 改为本机本地时间（带时区偏移量的
  ISO-8601），新增 `detail` 字段承载失败时的完整诊断信息（子进程
  stdout/stderr 尾部，或 Python 异常的完整 traceback，或 entrypoint
  脚本主动上报的结构化失败明细），并截断到 4000 字符防止账本膨胀。
  `RunRecord`/`record_run()`/`track_run()`/`EntrypointRunResult` 均
  已同步更新，手动触发的 HTTP 响应与 CLI `projects status`/`ledger`/
  `run` 三个子命令都已把 `detail` 透出。`stock_watch` 的
  `run_kline_batch.py` 作为示范用法做了同步改造（候选池全部标的失败
  时记录逐个标的的失败原因）。新增/修改代码未新增测试文件（复用既有
  `tests/test_external_projects_ledger_and_status.py` 等 34 个用例
  验证 schema 变更未破坏既有行为），后续如需要更细粒度的回归覆盖，
  可以在该测试文件里补充 `detail` 字段的专项用例。
- 2026-08-27（续）：daemon 重启、代码确认已生效后，用户反馈"账本里
  已经有 detail 字段了，但看板上还是看不到"——排查发现根因不在后端，
  是 `apps/mini_agent_kanban/app.py`（`external_projects_kanban_
  integration_plan.md` 阶段3新增的「🗂️ 外部项目」tab）从一开始就只
  渲染 `error_summary`，从未读过 `detail` 字段，这次新增的诊断信息
  实际上一直"存了但没显示"。已修复该文件三处：卡片"最近一次执行"
  失败时自动展开详情、"最近 N 条执行记录"列表逐条展示 detail、
  「▶️ 手动触发」结果失败时直接内联展示 detail。这次顺带确认了一个
  历史遗留但非本次引入的现象：daemon 触发一次 entrypoint 会产生两条
  账本记录（子进程内部 `track_run()` 一条 + daemon 父进程
  `_run_entrypoint()` 一条），目前两条都携带有效信息，暂不处理，留作
  后续如有需要再收敛。
- 2026-08-30：本文档"如何接入 daemon"一节遗留的最后一块拼图（`scheduler.py::
  run_due_entrypoints()` 从写出来就没被 daemon 任何调度循环真正调用过）
  已按 `next_doc/external_projects_cron_dispatch_plan.md`（v1.0）落地：
  外部项目 entrypoint 的调度整体挪到 `evolution/cron_scheduler.py` 体系
  （每个到期 entrypoint 成为一条 `run_mode="external_entrypoint"` 的
  `CronJob`，到期判断/并发/仲裁资源与普通 cron job 完全共享），项目
  粒度开关（看板项目卡片"自动调度"）关闭时真删对应 job、打开时按
  `project.yaml` 全量重新生成。新注册项目默认 `enabled=False`（opt-in），
  需要用户在看板/CLI 手动打开后 daemon 才会开始自动调度。详见该文档
  第 6 节"实现记录"。
- 2026-08-30：用 `stock_watch` 落地首个实际使用项目私有 `skills/`/
  `workflows/` 的案例（个股 AI 综合研判）时，读码发现本节 5.1 节画的
  `<root>/skills/`、`<root>/workflows/` 目录约定，框架侧实际路径解析
  （`config/prompt_builder.py::_resolve_skills_dir()`、
  `workflow/store.py::WorkflowStore`）从未真正支持过——一直硬编码走
  普通交互式 agent 那套 `<root>/.claude/skills`、`<root>/.agent/
  workflows` 约定，`阶段1` 新增的 `Workspace.skills_dir`/
  `workflows_dir` 两个属性形同虚设（从未被这两处代码读取）。已按本节
  设计修正这两处框架代码：以 `<root>/project.yaml` 是否存在判定"外部
  项目 vs 普通交互式 agent 目录"，外部项目走 `<root>/skills`/
  `<root>/workflows`，没有 `project.yaml` 的普通目录行为不变。详见
  `next_doc/external_projects_agent_skill_workflow_integration_plan.md`
  第 1 节。
- 2026-08-30（同上，补记）：同一轮实测还发现外部项目触发 workflow/
  skill_agent 时无法拿到 LLM API key（外部项目自己目录下没有
  `providers.json`，环境变量里也未必有）。已给 `load_config()` 加了
  "外部项目自己没有 `agent_config.json`/`providers.json` 时，回退到
  `<主项目根>/external_projects/<name>/` 约定推出的主项目根去找同名
  文件"这条 fallback，外部项目不再需要重复维护一份 API key。详见同一
  文档第 1.4 节。
