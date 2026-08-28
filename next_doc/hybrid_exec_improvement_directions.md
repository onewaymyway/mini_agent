# hybrid_exec 机制现状梳理与改进方向分析

> **实施状态**：§5 优先级建议里的 **A1、A2、B1、A3、B2** 已落地，实施
> 细节见
> `next_doc/hybrid_exec_external_integration_implementation_record.md`；
> 仅 **A4** 仍未做（分析文档已明确建议暂缓），具体原因见该实施记录末尾
> "未落地项"一节。本文档正文保留原样不改，作为当时的分析产出存档。
>
> **这篇文档管什么**：盘点 `hybrid_exec`（脚本/LLM/Agent 混合执行系统）
> 当前的实现现状，重点分析两个方向：①如何让这套机制更容易被"当前
> daemon 项目之外的地方"（其它 workflow、独立脚本、尤其是`external_
> projects` 外部项目）复用；②如何降低"生成一个使用 hybrid_exec 的执行
> 载体"的门槛。本文档是分析/思考产出，给出的是**改进方向的建议**，不
> 是已确认要做的实施计划——是否采纳、按什么顺序做，留待后续单独立项。
>
> **不管什么**：不重新介绍 hybrid_exec 的核心决策逻辑（脚本优先/修复
> 优先/降级兜底），完整设计见 `next_doc/hybrid_exec_design_plan.md`，
> 本文档假定读者已了解或会去读那份文档。

## 1. 现状盘点：hybrid_exec 已经做到了什么程度

读完 `hybrid_exec_design_plan.md`（P1-P4 + §11-14 共 14 个小节的实施
记录）和源码后，现状比预想的成熟：

- **核心编排**（`executor.py::HybridExecutor`）：脚本优先 → 修复
  （LLM 修复 → Agent 修复）→ 降级（LLM 直答 → Agent 直答）的决策链完整
  实现，`ScriptRepository` 有版本管理 + 连续失败退役机制。
- **三种"档位"**：`SCRIPT`/`LLM`/`AGENT` 之外，还有第四档 `SKILL`
  （playbook：不产出可执行代码，产出一份人类可读步骤说明，每次由轻量
  Agent 参照执行——对付"运行时细节易变但整体流程稳定"的任务，比如页面
  结构常变的抓取）。
- **四种接入方式**都已落地，且彼此独立、可任选：
  1. 独立 Python API：`default_executor(project_root).run(TaskSpec(...))`
  2. 独立 CLI：`mini-agent hybrid-exec run/list/show --project <path>`
  3. 主 Agent 内部工具：`run_hybrid_exec_task`/`list_hybrid_exec_tasks`/
     `show_hybrid_exec_task`
  4. workflow 新 step 类型：`hybrid_step`（通过 `register_step_executor()`
     公开扩展点接入，未改动 workflow 包源码本身）
- **LLM 复用链路**已对齐主 Agent：`default_executor()` 会构造一次共享
  `LLMHelper`（含 provider 轮转/cooldown/fallback_chain），避免每次
  探索/修复/兜底都重新 `load_config()`；支持传入已有 `llm`（如
  `python_step` 的 `ctx.llm`）直接复用。
- **可观测性**：`.agent/hybrid_exec/{scripts,runs}/<task_id>/` 落盘，
  `kanban_summary.py` 汇总后接了一个只读端点 `GET /v1/hybrid_exec/
  summary` 和看板 Tab。
- **配套 skill**：`.claude/skills/hybrid-exec-task-generator` 帮助在
  对话中构建/调试 `TaskSpec`。

结论：**核心执行引擎和"在当前项目内部怎么用"这条主线已经相当完整**，
这次分析的重点因此聚焦在题目问的两点——"更容易被别处/外部项目使用"、
"更容易生成执行载体"——这两点确实是当前相对薄弱的环节，下面具体展开。

## 2. 现状问题：hybrid_exec 与"外部项目"机制事实上没有打通

`external_projects_workspace_plan.md` 在最初设计里明确把 hybrid_exec
列为外部项目应该能复用的三大引擎之一（"skill、workflow、hybrid_exec、
memory"），原则一要求这些引擎"通过显式的 `Workspace` 上下文对外提供"。
但实际核对代码后发现，这个打通目前**只停留在设计意图层面**，有以下
四处具体缺口：

### 2.1 `Workspace` 与 `hybrid_exec` 之间没有代码级绑定

`src/mini_agent/workspace.py::Workspace` 已经有
`run_status_path`/`backlog_path`/`project_yaml_path` 等属性对应外部
项目的账本/契约文件，但**没有** `hybrid_exec_scripts_dir` /
`hybrid_exec_runs_dir` 之类的属性；`hybrid_exec.default_executor()`
的签名是 `default_executor(project_root, ...)`，接受一个裸路径，而不是
接受 `Workspace` 对象。两者能协同工作纯粹是因为"裸路径"和
`Workspace.root` 恰好指向同一个目录，是**巧合式兼容**，不是显式设计
出来的集成——如果未来 `Workspace` 增加了路径重映射之类的能力（比如
把 `.agent/` 挪到别处），`hybrid_exec` 不会自动跟着变。

### 2.2 `stock_watch`（唯一真实落地的外部项目）完全没有用 hybrid_exec

核对 `external_projects/stock_watch/entrypoints/*.py`，全部是手写业务
逻辑，零处 `import mini_agent.hybrid_exec`。对照它已经用上的
`external_projects.ledger.track_run()`（`entrypoints/_common.py::
tracked_run()`），后者专门做了**降级容错**——`ImportError` 时退化为
"不写账本、只执行"，保证"即使脱离 mini_agent 环境也能独立跑"（原则
二）。hybrid_exec 没有任何一处提供同款降级包装，如果 stock_watch 想用
`HybridExecutor` 去处理"某个数据源改版后自动修复抓取脚本"这类天然适合
hybrid_exec 的场景，目前得自己现写这层 try/except，没有可复用的模式
可抄。

### 2.3 可观测性只服务于"daemon 自己的项目根"，看不到外部项目的 hybrid_exec 用量

`GET /v1/hybrid_exec/summary`（`api/routes.py`）内部调用
`build_kanban_summary(proj_root)`，`proj_root` 来自
`_project_root_or_503(http_server)`——即 daemon 自身启动时的
`project_root`，是单项目视角。即使某天 `stock_watch` 真的开始用
`hybrid_exec`（数据会落在 `external_projects/stock_watch/.agent/
hybrid_exec/` 下），当前的看板 Tab **看不到**这份数据，除非用户手动
`cd` 过去用 CLI 查——这与"大管家应该能看见所有外部项目在做什么"的
定位矛盾，也是 `external_projects` 侧看板集成（`external_projects_
kanban_integration_plan.md`）没有覆盖到的盲区。

### 2.4 打包/依赖上，`mini_agent` 对外部项目而言不是显式声明的依赖

`stock_watch/requirements.txt` 完全不含 `mini-agent`——外部项目当前
的"能不能 import 到 mini_agent"，实质上依赖"两者恰好装在同一个 Python
环境里"这个隐式假设，而不是一个显式声明、可安装的依赖关系。这对
`ledger.track_run()` 这类"能力增强、缺了也无所谓"的功能问题不大（已经
做了降级），但如果未来希望外部项目**认真依赖** hybrid_exec 作为核心
执行手段（而不只是锦上添花），这种隐式假设就不够牢靠了——换一台机器、
换一个独立 venv 部署这个外部项目时，会在"以为能用、实际 import 失败"
这个坑上栽跟头。

## 3. 改进方向 A：让 hybrid_exec 更容易被外部项目 / 别处复用

### A1（低成本，建议优先）：`Workspace` 补齐 hybrid_exec 相关路径属性

在 `Workspace` 上新增 `hybrid_exec_scripts_dir` / `hybrid_exec_runs_
dir` 两个只读属性（对齐 `run_status_path` 的写法），并让
`default_executor()` 新增一个可选的 `workspace=` 入参（与现有
`project_root` 参数二选一，`workspace` 优先），内部用
`workspace.root` 派生，不改变现有调用方行为。这样"外部项目用
`Workspace` 对象一路传下去"这条线才是真正打通的，而不是靠路径字符串
巧合对齐。

### A2（低成本）：给 hybrid_exec 补一个"检测不到 mini_agent 就降级"的标准包装

在 `hybrid_exec` 包（或者单独一个轻量模块，比如
`hybrid_exec/optional.py`）里提供一个类似 `ledger.py::tracked_run()`
风格的包装函数，例如：

```python
def try_hybrid_exec(task: "TaskSpec", *, project_root) -> Optional["ExecutionResult"]:
    """尝试用 hybrid_exec 执行；检测不到 mini_agent 框架（ImportError）
    或未安装 http/llm 相关可选依赖时返回 None，调用方自行决定兜底逻辑
    （比如退化成一段写死的业务代码）。"""
```

给外部项目一个"哪怕环境不全也不会直接炸"的标准姿势，与 `ledger`/
`backlog` 已经确立的"引擎能力是锦上添花，缺了不影响核心可独立运行"
原则（原则二）保持一致。目前 hybrid_exec 各处（`default_executor`、
CLI）遇到配置缺失是直接抛异常，这对"在 daemon 项目内部使用"没问题，
但对"外部项目试探性使用"不够友好。

### A3（中等成本）：外部项目看板/状态接入 hybrid_exec 用量

两种实现路径，按成本从低到高：

1. **最小改动**：`mini-agent projects status <name>` 命令在打印
   health_check + 账本最后一条记录之外，额外扫一眼
   `<project_path>/.agent/hybrid_exec/` 是否存在，存在则调用
   `build_kanban_summary(project_path)` 附带打印一行摘要（"3 个
   hybrid_exec 任务，当前命中率 xx%"）。纯只读、不影响其它逻辑。
2. **更完整**：`external_projects` 的看板集成（如果/当已经有跨项目
   聚合视图）里，对每个注册的外部项目也拉一次
   `build_kanban_summary(project.path)`，与该项目自己的执行账本一起
   展示——让"大管家"真正对外部项目的 hybrid_exec 用量有感知，而不只是
   对自己项目根有感知。

这一步的前提是先做 A1（`Workspace` 显式集成），否则"外部项目的
hybrid_exec 数据在哪"这件事本身缺乏一个可靠的编程接口去回答，只能
硬编码路径拼接。

### A4（视需求决定，成本较高）：hybrid_exec 作为独立可安装子包的可能性

如果未来出现"外部项目希望认真依赖 hybrid_exec 作为核心执行手段、而不
只是可选增强"的真实需求，值得评估把 `hybrid_exec` 拆成一个独立的、
更小依赖面的可安装包（或者 `pyproject.toml` 里的一个 extras，比如
`pip install mini-agent[hybrid-exec]`），让外部项目能显式声明这个
依赖、而不是依赖"恰好装在同一个环境"。**这一条目前不建议立即做**——
`external_projects_workspace_plan.md` 已经明确"跨项目共享机制"这类
设计要等到出现第二、三个真实案例后再回来看真实需求，避免过早抽象；
这里先记录方向，不是本次建议的优先项。

## 4. 改进方向 B：更容易生成"使用 hybrid_exec 的执行载体"

这里"执行载体"指的是"实际把某个 `TaskSpec` 接进某个可运行入口"的
那层代码/配置——可能是一段独立脚本、一个 workflow 的 `hybrid_step`
定义、或者外部项目的一个 `entrypoints/*.py`。目前这层完全靠手写：

- 独立调用：手写 `TaskSpec(...)` + `default_executor(...).run(...)`。
- workflow 接入：手写 yaml 里的 `hybrid_step` 节点。
- 外部项目 entrypoint 里嵌入 hybrid_exec：目前没有任何示例/模板
  （对照 §2.2，`stock_watch` 也没用上）。
- `hybrid-exec-task-generator` skill 目前定位是"对话中帮用户想清楚
  这个任务该怎么设计 `TaskSpec`"，属于"设计辅助"而非"代码生成"——
  生成 `TaskSpec` 字段草稿后，落地成"某处可执行的载体代码"这一步仍需
  要 agent/用户手写。

改进方向：

### B1（建议优先，成本可控）：新增 `mini-agent hybrid-exec scaffold` 子命令

在 `hybrid_exec/cli.py` 新增一个 `scaffold` 子命令，用法类似：

```bash
mini-agent hybrid-exec scaffold <task_id> \
    --carrier {script,workflow-step,entrypoint} \
    --desc "任务的一句话描述" \
    [--project <path>] [--output <文件路径>]
```

三种 `--carrier` 分别生成三类样板：

- `script`：一段独立可运行的 `.py`，`if __name__ == "__main__":` 里
  拼好 `TaskSpec` + `default_executor(project_root).run(...)`，读取
  `input_data` 的方式（命令行参数/stdin/文件）留 TODO。
- `workflow-step`：一段可以直接粘进某个 workflow yaml 的 `hybrid_step`
  节点 yaml 片段（`id`/`task_id`/`description`/`params` 骨架齐全）。
- `entrypoint`：贴合 `external_projects` 标准结构的
  `entrypoints/<key>.py` 样板——**关键是要包含 §3.A2 提到的降级包装**
  （检测不到 mini_agent 就跳过 hybrid_exec、退回一段"未实现"占位或者
  直接报错，而不是让整个 entrypoint 因为 import 失败而崩溃），并且
  同时带上 `track_run()` 账本样板（与 `external-project-manager`
  skill 里 `entrypoint.py.tmpl` 的风格保持一致，形成"外部项目 entrypoint
  两件套模板"）。

这条建议与本次对话之前刚落地的 `external-project-manager` skill 是
自然衔接的——那个 skill 负责"生成整个外部项目骨架"，这里补的是"给骨架
里某一个具体 entrypoint 生成一段用 hybrid_exec 完成子任务的样板代码"，
两者结合起来才是"外部项目从0到1、且用上框架能力"的完整链路。

### B2（中等成本）：常见任务类型的 `TaskSpec` 模板库

当前 `TaskSpec` 每次都要从零想清楚 `description`/`output_validator`/
`allow_tiers` 怎么填。可以整理一批"常见任务类型"的模板（例如：结构化
信息抽取、文本摘要、格式转换、简单分类判断、网页内容解析这几类在
`hybrid_exec_design_plan.md` 示例和 `examples/hybrid_exec_demo.py`
里已经隐含出现过的模式），每个模板给出：典型 `description` 措辞、一份
`output_validator` 参考实现（比如"JSON 且包含某些必填 key"这种通用
校验器）、建议的 `allow_tiers` 组合。可以落地成
`hybrid-exec-task-generator` skill 下新增的 `reference/task_templates.md`，
供该 skill 在对话中直接引用，而不需要每次都现场设计校验逻辑。

### B3（低成本，锦上添花）：`scaffold` 生成后自动跑一次 dry-run 提示

如果 B1 落地，可以让 `scaffold` 命令在生成样板后，提示一句"这段代码里
的 `TODO` 替换完后，建议先用 `mini-agent hybrid-exec run <task_id>
--project <path> -v` 跑一次看 `attempts` 决策轨迹"——把"生成代码"和
"如何验证代码写对了"这两步之间的断点也补上，参考
`external-project-manager/scripts/scaffold.py` 生成后自动
`load_manifest()` 自证合法的做法（虽然这里没法自动验证"业务逻辑写对
了"，但至少可以自动验证"生成的样板语法正确、能被 import"）。

## 5. 优先级建议

如果要动手实施，建议顺序（成本从低到高，且有依赖关系）：

1. **A1**（`Workspace` 补属性 + `default_executor(workspace=...)`）——
   地基性质，后面几条都依赖它有一个可靠的编程接口去定位 hybrid_exec
   数据目录。
2. **A2**（降级包装 `try_hybrid_exec`）——与 A1 同批做，成本很低，
   且是 B1 的 `entrypoint` 载体模板能"安全嵌入"的前提。
3. **B1**（`hybrid-exec scaffold` 子命令）——直接回应题目"如何更容易
   生成执行载体"，且做完 A2 之后才能把 `entrypoint` 载体模板写得真正
   健壮（含降级分支）。
4. **A3**（外部项目状态命令附带 hybrid_exec 摘要）——依赖 A1，属于
   可见性增强，不阻塞其它项。
5. **B2**（任务模板库）——独立于前面几条，可以随时补充，优先级可以
   放最后，属于"越用越丰富"的内容型工作，不是一次性能做完的架构改动。
6. **A4**（独立可安装子包）——明确暂缓，等真实需求出现再评估。

## 6. 小结

hybrid_exec 的**核心引擎和"当前项目内部四种接入方式"已经相当成熟**，
这次分析暴露出的真正缺口集中在**"引擎与外部项目之间的显式集成"**这一
层——`external_projects_workspace_plan.md` 最初的构想里 hybrid_exec
被列为外部项目应该能复用的三大引擎之一，但目前只有 `ledger`/
`backlog` 两个真正做了"显式路径属性 + 降级包装"这套完整集成，
hybrid_exec 还停留在"路径字符串恰好对得上"的隐式兼容状态。上面 A1/A2/
B1 三条是成本较低、能直接补上这个缺口的具体动作，建议作为下一步实施
的候选起点。
