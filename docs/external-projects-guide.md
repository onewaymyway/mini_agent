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

## 5. 状态账本：记录每一次执行

每个外部项目按统一约定，把自己的每次执行记录写进自己
`<root>/.agent/run_status.jsonl`，不管这次执行是被 `mini-agent
projects run` 触发、被 daemon 调度器触发、还是被 OS cron 完全绕开
mini_agent 直接触发的，都写同一份账本、同一个 schema。

**被 `mini-agent projects run` 或 daemon 调度器触发时**：账本自动写，
不需要你的 entrypoint 脚本做任何事。

**被 OS cron 直接触发、完全绕开 mini_agent 时**：脚本自己 import 一个
上下文管理器上报即可：

```python
from mini_agent.external_projects.ledger import track_run

with track_run(".", "hotlist_scan", trigger="external_cron"):
    do_the_actual_scan()  # 正常跑完记一条成功；抛异常自动记失败并重新抛出
```

查看账本：

```bash
mini-agent projects ledger stock_watch          # 最近 20 条
mini-agent projects ledger stock_watch 100      # 最近 100 条
```

## 6. 健康检查与状态聚合

`mini-agent projects status <name>` 会：

1. 如果 `project.yaml` 声明了 `health_check`，主动探测一次
   （30 秒超时），探测结果直接作为健康状态；
2. 没声明，或探测失败到"探测不了"的程度，退化为读账本最后一条记录
   （`exit_code == 0` → healthy，非 0 → unhealthy）；
3. 两者都没有 → `unknown`。

`mini-agent projects list` 只读账本（不主动探测 `health_check`，保持
纯被动、瞬时完成），`LAST_RUN` 列显示最近一次执行是 `OK`/`FAIL`/
`(none)`。

daemon 在运行时，`GET /v1/self/external_projects` 端点会把所有已注册
项目的这套聚合视图（health + 最近 5 条执行记录）一次性返回，供前端
kanban 直接渲染，不需要理解注册表/账本文件本身的存储细节。

## 7. daemon 侧调度器（可选）

`mini_agent.external_projects.scheduler.run_due_entrypoints(registry)`
供 daemon 的后台循环按分钟粒度调用，会触发本分钟内到期、且项目未被
`disable` 的所有 entrypoint，触发后自动写账本。这是"daemon 在场时的
锦上添花"，不触发不影响这些 entrypoint 被 OS cron / 手动独立执行。

## 8. 已知限制 / 尚未实现

- **daemon 主循环尚未真正接入调度器**：`run_due_entrypoints()` 本身
  已经过测试、可独立调用，但 daemon 主循环定时（每分钟）调用它这一
  具体接线动作还没做，目前调度只能靠 `mini-agent projects run` 手动
  触发或 OS cron。
- **cron 语法是最小子集**：不支持步进 (`*/5`) 等复杂表达式。
- **补跑策略未定义**：daemon 重启后错过的调度不会自动补跑。
- **"大管家"维护类交互标准化**（`list_projects`/`inspect_project`/
  `trigger_run`/`propose_fix` 工具化）是阶段 5 的范围，尚未开始。

以上限制均按刻意留白处理，等真实需求出现（阶段 5，或第二个外部
项目落地后）再回来补，避免过早设计。

## 9. 相关文档

- [next_doc/external_projects_workspace_plan.md](../next_doc/external_projects_workspace_plan.md) —
  完整的架构设计过程、四条核心原则、为什么否决了"每个项目自己起
  daemon"等备选方案。
- [docs/agent-commit-guard-guide.md](./agent-commit-guard-guide.md) —
  本文档的文档分层模式参照对象。
