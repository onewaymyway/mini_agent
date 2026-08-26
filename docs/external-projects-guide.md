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

## 8. 大管家维护工具（`propose_fix` 等）

除了展示状态、手动触发，agent（大管家）还可以调用一组标准工具去
**理解**、**触发**、**协助维护**已注册的外部项目，工具名与
`mini-agent projects` CLI 子命令一一对应但服务于不同调用方（前者给
LLM 工具调用，后者给人 / 脚本）：

| 工具 | 只读？ | 作用 |
|---|---|---|
| `list_projects` | 是 | 列出所有已注册外部项目 |
| `inspect_project` | 是 | 读 manifest + 健康状态 + 最近执行账本 |
| `trigger_run` | 否（执行外部代码，保留人工确认） | 立即触发某个 entrypoint 一次 |
| `propose_fix` | 否（但落在独立分支，不改当前 checkout） | 提出一次维护改动 |

`propose_fix` 的核心机制**直接复用** self-evolution 已有的
"git worktree 隔离 + 提案验证 + 落地"流程
（`evolution/state_repo.py::StateRepo` + `evolution/workspace.py::
EvolutionWorkspace`，与 `skill_propose` 同一套底层类），以**目标外部
项目自己的目录**为 git 仓库根（不是 mini_agent 自身仓库），落地方式：

```python
from mini_agent.external_projects.maintenance import (
    propose_maintenance_fix, land_maintenance_fix,
)

result = propose_maintenance_fix(
    "/data/stock_watch",
    {"entrypoints/run_hotlist_scan.py": new_content},
    "Fix selector after site redesign",
    reason="old-price selector no longer exists",
)
# result.ok=True 时，改动已经 commit 在 result.branch 上（目标项目自己
# 仓库里的一个独立分支），当前 checkout 完全不受影响，尚未合并。

# 人工 review 通过后：
land_maintenance_fix("/data/stock_watch", result.branch)
```

要点：

- 目标项目如果还没有 `.git`，`propose_maintenance_fix()` 会自动
  `git init` 一个（fresh-repo 场景的兜底，与 `skill_propose` 一致）；
  **git worktree 隔离机制对外部项目开箱即用，不需要任何适配层**——
  `StateRepo`/`EvolutionWorkspace` 本来就只依赖"传入的 root 是/能
  成为一个 git 仓库"，不假设 root 是 mini_agent 自身仓库的一部分。
- 校验默认用 `tier="T2"`（改动的 `.py` 文件过语法/ruff lint，且如果
  目标项目自己有 `tests/` 目录会跑一遍其中的测试），而不是
  `skill_propose` 固定用的 `T1`——外部项目的维护对象通常是任意脚本，
  不是 mini_agent 的声明式资产（SKILL.md 等）。校验失败时不写入、不
  commit，`propose_fix` 返回 `validation_errors`。
- 落地（合并分支）永远是一步**显式**动作
  （`land_maintenance_fix()` / 人工 `git merge`），`propose_fix`
  本身不会自动合并——呼应原则四"daemon 的角色始终是触发者/协调者，
  不是执行者本身"。

## 9. 已知限制 / 尚未实现

- **daemon 主循环尚未真正接入调度器**：`run_due_entrypoints()` 本身
  已经过测试、可独立调用，但 daemon 主循环定时（每分钟）调用它这一
  具体接线动作还没做，目前调度只能靠 `mini-agent projects run` 手动
  触发或 OS cron。
- **cron 语法是最小子集**：不支持步进 (`*/5`) 等复杂表达式。
- **补跑策略未定义**：daemon 重启后错过的调度不会自动补跑。
- **`land_maintenance_fix()` 目前还没有对应的 `mini-agent projects`
  CLI 子命令**（合并提案分支目前只能用这个 Python 函数或直接
  `git merge`，`propose_fix` 工具已经在返回消息里给出提示）；工具化
  的 `list_projects`/`inspect_project`/`trigger_run`/`propose_fix`
  已完成（阶段 5）。
- **还没有第二个真实落地的外部项目**（阶段 6：股票监控系统作为首个
  案例）——以上机制目前只有测试场景和单元测试验证过，尚未在真实、
  长期运行的外部项目上跑过。

以上限制均按刻意留白处理，等真实需求出现（第二个外部项目落地后）
再回来补，避免过早设计。

## 10. 相关文档

- [next_doc/external_projects_workspace_plan.md](../next_doc/external_projects_workspace_plan.md) —
  完整的架构设计过程、四条核心原则、为什么否决了"每个项目自己起
  daemon"等备选方案。
- [docs/agent-commit-guard-guide.md](./agent-commit-guard-guide.md) —
  本文档的文档分层模式参照对象。
