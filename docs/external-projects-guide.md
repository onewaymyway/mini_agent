# 外部项目（用户定制系统）使用指南

> **这篇文档管什么**：外部项目机制"现在是什么样"——`Workspace`、
> `project.yaml` 契约、注册表、`mini-agent projects` 命令、headless
> 执行入口，怎么用、数据存哪、已知限制。
> **不管什么**：为什么这样设计、否决过哪些备选方案、各阶段具体怎么
> 实现的——这些记录在
> [next_doc/external_projects_workspace_plan.md](../next_doc/external_projects_workspace_plan.md)，
> 本文档只链接过去，不复制内容。

## 1. 解决什么问题

当你想在 mini_agent 之上搭一个持续运行的、有自己独立数据/生命周期的
领域系统（比如一个股票监控分析系统、一个舆情监控系统），这个系统：

- 不应该和 mini_agent 自身的开发记忆混在一起；
- 不应该依赖 daemon 进程是否在运行才能跑；
- 应该能被 daemon（如果你在用）统一看见、统一管理，但完全脱离
  daemon 独立运行也要能正常工作。

外部项目机制就是解决这个问题的框架能力，不是任何具体业务系统本身。

## 2. 核心概念

### 2.1 `Workspace`

`Workspace(root=<任意路径>)` 是 skill / workflow / memory / session 等
mini_agent 引擎子系统的显式执行上下文根，替代原来隐式的"当前目录就是
project_root"假设。

```python
from mini_agent.workspace import Workspace

ws = Workspace(root="/data/stock_watch")
ws.apply_to(cfg)                  # 让 AppConfig 落到这个根下
loader = ws.build_skill_loader()  # 本地 skills 优先，全局内置兜底
```

两个不同 `root` 的 `Workspace` 之间 memory / session / skill 解析结果
完全隔离，互不污染。

### 2.2 `project.yaml`：daemon 与外部项目之间唯一的契约

```yaml
name: stock_watch
entrypoints:
  hotlist_scan:
    cmd: "python entrypoints/run_hotlist_scan.py"
    schedule: "cron: 0 9,13 * * 1-5"   # 可选；不写就是纯手动/外部触发
    timeout_sec: 600                    # 可选
health_check:                            # 可选
  cmd: "python entrypoints/health.py"
resources:                               # 可选
  allowed_domains: ["xueqiu.com"]
  max_concurrency: 1
```

规则：

- `name` / `entrypoints` 必填，`entrypoints` 至少一项；
- `schedule` 目前只支持 `cron: <5 字段 cron 表达式>` 写法（分 时 日 月
  周，`*`、单值、逗号列表、`-` 区间均支持；不支持步进 `*/5` 等更复杂
  语法——真的需要就交给 OS 原生 cron 直接调这个 entrypoint 的 `cmd`）；
- `timeout_sec` 必须是正整数；
- `resources.max_concurrency` 必须 >= 1。

结构不合法时 `load_manifest()` 抛 `ProjectManifestError`，错误信息会
指出具体哪个字段有问题。

### 2.3 注册表

daemon（或你本机）通过一份与代码树无关的注册表记住"有哪些外部项目、
路径在哪"，默认存放在 `~/.mini_agent/external_projects.json`：

```python
from mini_agent.external_projects import ExternalProjectRegistry

registry = ExternalProjectRegistry()
registry.register("stock_watch", "/data/stock_watch")
registry.list()
registry.unregister("stock_watch")
```

`register()` 默认会先尝试解析目标路径下的 `project.yaml`，解析失败会
拒绝注册（避免注册表里混入不可用的条目）；传 `validate=False` 可以先
占位注册，稍后再补 `project.yaml`。

## 3. `mini-agent projects` 命令

不需要 daemon 在运行，只需要装了 mini_agent：

```bash
mini-agent projects list
mini-agent projects register /data/stock_watch --name stock_watch
mini-agent projects status stock_watch
mini-agent projects run stock_watch hotlist_scan
mini-agent projects enable stock_watch
mini-agent projects disable stock_watch
mini-agent projects unregister stock_watch
```

- `disable` 只影响 daemon 侧调度器是否会自动触发该项目，**不影响**它
  被 OS cron 或你手动直接执行（这是原则二的直接体现：daemon 只是
  可选加成）。
- `run <name> <entrypoint>` 会在该项目的根目录下、以子进程方式执行
  `project.yaml` 里声明的 `cmd`，退出码原样透传，可以直接用在 CI/
  脚本里判断成败。

## 4. headless 单次执行入口（跑一个已保存的 workflow）

如果外部项目的某个 entrypoint 本身是一个 mini_agent workflow，而不是
一段独立脚本，用已有的 workflow CLI 即可，同样不需要 daemon：

```bash
mini-agent workflow run <workflow_name> --workspace /data/stock_watch
```

`--workspace` 是 `--project` 的别名，语义完全一致，只是在外部项目场景
下术语上对齐 `Workspace` 概念。

## 5. daemon 侧调度器（可选）

`mini_agent.external_projects.scheduler.run_due_entrypoints(registry)`
供 daemon 的后台循环按分钟粒度调用，会触发本分钟内到期、且项目未被
`disable` 的所有 entrypoint。这是"daemon 在场时的锦上添花"，不触发
不影响这些 entrypoint 被 OS cron / 手动独立执行。

## 6. 已知限制 / 尚未实现

- **状态账本聚合尚未实现**（`next_doc` 阶段 4 范围）：目前
  `projects status` 只能展示 manifest 内容，看不到历史执行记录；每次
  执行的成败目前只能靠 `projects run` 的退出码或 `health_check` 自行
  判断。
- **健康检查尚未接入调度器**：`project.yaml` 的 `health_check` 字段
  目前只被解析和展示，daemon 还不会主动探测。
- **cron 语法是最小子集**：不支持步进 (`*/5`) 等复杂表达式。
- **补跑策略未定义**：daemon 重启后错过的调度不会自动补跑。

以上限制均按刻意留白处理，等真实需求出现（阶段 4/5，或第二个外部
项目落地后）再回来补，避免过早设计。

## 7. 相关文档

- [next_doc/external_projects_workspace_plan.md](../next_doc/external_projects_workspace_plan.md) —
  完整的架构设计过程、四条核心原则、为什么否决了"每个项目自己起
  daemon"等备选方案。
- [docs/agent-commit-guard-guide.md](./agent-commit-guard-guide.md) —
  本文档的文档分层模式参照对象。
