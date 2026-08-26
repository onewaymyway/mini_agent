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
- [ ] 新增 `mini_agent run --workspace <path> --workflow <name>` 命令
      （或复用已有 `cli/commands/workflow_cmd.py` 扩展 `--workspace`
      参数，具体走哪条路径待看现有 `workflow_cmd.py` 实现后再定）
- [ ] 该命令只加载对应 `Workspace`，不初始化 HTTP API / SSE / kanban
      等 daemon 专属基础设施；执行完退出，产出结构化结果（复用现有
      `WORKFLOW_RESULT_FILE_PATH` 模式）
- [ ] 验收标准：该命令可以被 OS 原生 cron / Windows 计划任务直接调用，
      在 daemon 完全没有启动的情况下产出正确结果

### 阶段 3：`project.yaml` 契约 + daemon 侧外部项目注册表
- [ ] 定义 `project.yaml` 的 schema（`entrypoints` / `health_check` /
      `resources`），落一份 JSON Schema 或 pydantic 模型供校验
- [ ] daemon 侧新增外部项目注册表存储（如
      `~/.mini_agent/external_projects.json`，与 daemon 自身代码树
      无关的独立位置），支持增/删/查已注册项目
- [ ] daemon 侧新增按 `project.yaml` 里 `schedule` 触发对应
      entrypoint 的调度器（复用 workflow 引擎已有的 subprocess
      isolation / watchdog 机制，调用目标从"daemon 内部脚本"换成
      "外部项目的 headless 入口"）
- [ ] `/commit-guard` 之外新增一个类似的 `/projects` 系列 CLI 命令
      （`list` / `status <name>` / `run <name> <entrypoint>` /
      `register <path>` / `unregister <name>`），参照
      `agent_commit_guard_guide.md` 的文档模式单独出一份使用指南

### 阶段 4：状态账本约定 + daemon 侧状态聚合
- [ ] 定义 `<project_root>/.agent/run_status.jsonl` 的标准 schema
      （entrypoint / started_at / finished_at / exit_code / trigger /
      错误摘要），trigger 字段区分 `daemon` / `external_cron` /
      `manual`
- [ ] 提供一个轻量库函数（外部项目的 entrypoint 里 import 调用即可）
      负责往这份账本写记录，降低外部项目遵循这个约定的成本，避免
      每个项目自己重新实现一遍写账本逻辑
- [ ] daemon 侧新增"读取已注册项目账本 → 聚合成统一视图"的逻辑，
      接入现有 kanban dashboard 展示
- [ ] （若项目声明了 `health_check`）daemon 侧新增健康检查探测，
      探测失败时退化为读取账本最后一条记录，而不是报错中断

### 阶段 5：维护类交互标准化（大管家能力）
- [ ] 设计"以目标外部项目 `Workspace` 为根触发独立运行"的标准调用
      方式，复用 self-evolution 现有的 git worktree 隔离 + 提案验证
      落地流程，评估是否需要为"外部项目"场景做适配（外部项目未必是
      mini_agent 自身仓库的一部分，git worktree 是否适用需要先验证）
- [ ] daemon 侧新增一组标准工具供大管家 agent 调用：`list_projects`
      `inspect_project`（读 manifest + 最近执行账本 + 日志）
      `trigger_run` `propose_fix`
- [ ] 端到端验证：模拟"某个外部项目的抓取脚本因网站改版失效"场景，
      走完整的"发现问题 → 提案改动 → 验证 → 落地"链路

### 阶段 6：股票监控系统作为首个落地案例
- [ ] 按第 5.1 节标准结构，在阶段 1～4 完成后（不需要等阶段 5）新建
      `stock_watch` 外部项目，验证整套机制在真实场景下是否顺畅
- [ ] 记录落地过程中暴露出的、本文档未预料到的问题，回填进本文档的
      "变更记录"和相关章节

## 9. 涉及文件清单（预期，具体以各阶段实施时为准）

- 新增 `src/mini_agent/workspace.py` — `Workspace` 核心抽象
- 修改 `src/mini_agent/config/models.py` — 支持从显式 `Workspace` 派生
  路径配置
- 修改/新增 `src/mini_agent/tools/skill_manager.py` — 分层 skill 搜索
- 新增 `src/mini_agent/cli/commands/run_headless.py`（或扩展
  `workflow_cmd.py`）— headless 单次执行入口
- 新增 `src/mini_agent/external_projects/` — `project.yaml` 解析、
  注册表管理、调度器、状态聚合
- 新增 `src/mini_agent/cli/commands/projects_cmd.py` — `/projects`
  系列 CLI 命令
- 新增 `docs/external-projects-guide.md` — 功能"毕业"后的稳定使用指南
  （参照 `docs/agent-commit-guard-guide.md` 的文档分层模式，本
  `next_doc/` 文档保留作为设计考古记录）
- （案例）新增外部路径下的 `stock_watch/` 项目 — 不属于 mini_agent
  自身仓库，仅通过注册表关联

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
  失败项是既有已知的 flaky 计时测试，与本次改动无关）。阶段 2（CLI
  headless 单次执行入口）待开始。
