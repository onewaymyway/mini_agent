# 外部项目（用户定制系统）使用指南

> **这篇文档管什么**：外部项目机制"现在是什么样"——`Workspace`、
> `project.yaml` 契约、注册表、`mini-agent projects` 命令（含 backlog/
> review 子命令）、headless 执行入口、看板集成，怎么用、数据存哪、
> 已知限制。
> **不管什么**：为什么这样设计、否决过哪些备选方案、各阶段具体怎么
> 实现的——这些记录在 [next_doc/external_projects_workspace_plan.md](../next_doc/external_projects_workspace_plan.md)、
> [next_doc/stock_watch_continuous_improvement_plan.md](../next_doc/stock_watch_continuous_improvement_plan.md)、
> [next_doc/external_projects_kanban_integration_plan.md](../next_doc/external_projects_kanban_integration_plan.md)，
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
  stock_analysis:
    cmd: "python entrypoints/run_stock_analysis.py"
    params:                             # 可选（external_projects_kanban_
      - name: code                      # integration_plan.md 阶段6）
        required: true
        help: "股票代码，如 600519"
      - name: name
        required: false
        default: "unnamed"
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
- `params`（可选）：entrypoint 需要传参数时声明，每项
  `{name, required?, default?, help?}`（`required` 默认 `true`）。
  触发时按声明顺序把传入值拼成位置参数（自动做 shell 转义）追加在
  `cmd` 后面，与 entrypoint 脚本读 `sys.argv[1:]` 的写法直接对齐；
  缺必填参数、或传了未声明的参数名，会在真正执行子进程之前就报错
  （`EntrypointParamError`）。看板「▶️ 手动触发」据此渲染输入框，见
  `docs/kanban-dashboard-guide.md`；
- `resources.max_concurrency` 必须 >= 1。

结构不合法时 `load_manifest()` 抛 `ProjectManifestError`，错误信息会
指出具体哪个字段有问题。

### 2.3 注册表

daemon（或你本机）通过一份与代码树无关的注册表记住"有哪些外部项目、
路径在哪"，默认存放在 `~/.agent/external_projects.json`（与
`~/.agent/` 下其它全局数据同一父目录）：

> **[2026-08-31] 路径迁移**：早期版本存放在 `~/.mini_agent/external_projects.json`。
> 首次读取时若发现新路径不存在、旧路径存在，会自动一次性迁移到新路径
> （旧文件保留不删）。不需要手工操作；如果你的 daemon 一直在跑，重启
> 一次即可完成迁移。

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
mini-agent projects backlog stock_watch list        # 改进积压账本，见 §6.1
mini-agent projects review stock_watch               # review 任务模板，见 §6.2
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

**性能说明（2026-08-28 追加）**：`health_check` 的子进程探测结果在
daemon 侧有 60 秒 TTL 缓存（按项目名 + 探测命令为 key），同一分钟内
反复请求 `/v1/self/external_projects`（比如看板来回切 tab）不会每次
都重新 fork 子进程；且该探测已经放进了线程池执行，不会阻塞 daemon
处理其它请求。如果需要绕过缓存拿到当下最新结果，CLI 侧可以直接调用
`external_projects.status.project_status_snapshot(registry, name,
use_cache=False)`（暂无对应 CLI flag，需要时再加）。

**手动触发的阻塞防护（2026-08-28 追加）**：`trigger_run`
（`POST /v1/external_projects/{name}/trigger_run` / `mini-agent
projects run`）内部执行 entrypoint 用的是同步子进程调用，`daemon` 侧
已经把它放进独立线程池执行（不占用主事件循环），所以触发一个长
entrypoint（`timeout_sec` 声明到 900 秒的，如 stock_watch 的
`kline_batch`/`signal_scan`）不会导致 daemon 对其它请求失去响应；但
HTTP 响应本身仍然要等 entrypoint 跑完才返回，看板一侧的请求超时已经
放宽到 960 秒。同一个 `(项目, entrypoint)` 不允许并发触发第二次——
执行期间再次触发会收到 409（"已有一次执行正在进行中"），避免重复
点击堆出多个并发子进程。

## 6.1 改进积压账本（backlog）

```bash
mini-agent projects backlog stock_watch list              # 全部待办
mini-agent projects backlog stock_watch list open         # 按状态筛选
mini-agent projects backlog stock_watch add "选股结果偶尔重复" \
    user_feedback ".agent/improvement_backlog.jsonl:12"    # 新增一条
```

每条待办记 `source`（`outcome_review`/`user_feedback`/`health_trend`）、
`summary`、可选 `evidence_ref`、`status`（`open`→`proposed`→
`landed`/`dismissed`）。用于回答"这个项目有哪些值得优化但还没处理的
问题"——这是账本（阶段4）"执行成败"之外的另一份同级账本，账本本身
不做任何判断，靠 review session 或用户手动写入/流转状态。

## 6.2 周期性 review 任务模板

```bash
mini-agent projects review stock_watch
```

生成一段可以直接投进 mini_agent 输入队列的任务描述文本：带上最近
执行记录摘要 + 当前 open 状态的 backlog 条目 + 任务边界说明（判断
有没有值得优化的地方、能不能形成具体机械可回归测试的改动、`propose_
fix(change_type="enhancement")` 生成提案分支但不自行落地）。
`project.yaml` 未声明 `review.enabled: true` 时不报错，仍会生成模板
供预览，只是提示"未开启定期 review"。

**这一步只生成文本，不会自动发起 review session**——真正让 agent 按
这份模板执行，需要用户自己把文本发进对话（CLI 下手动复制，看板下用
"复制到对话框"按钮，见 §6.3）。`review.cadence` 到 `cron_scheduler.py`
认识的 cron 表达式的换算规则见 `next_doc/stock_watch_continuous_
improvement_plan.md` 阶段4，daemon 主循环自动按 cadence 定期发起
review 这一具体接线动作目前还没做，见第9节"已知限制"。

## 6.3 看板集成

`next_doc/external_projects_kanban_integration_plan.md`（第一期）把
以上大部分命令行能力接入了看板（`apps/mini_agent_kanban/app.py`
「🗂️ 外部项目」Tab），日常查看/低风险操作不再需要单独开终端：

| 能力 | HTTP 端点 | 对应命令行 |
|---|---|---|
| 注册新项目 | `POST /v1/external_projects/register` | `mini-agent projects register` |
| 手动触发 entrypoint | `POST /v1/external_projects/{name}/trigger_run` | `mini-agent projects run` |
| 执行账本 | `GET /v1/external_projects/{name}/ledger` | `mini-agent projects ledger` |
| 改进积压：查看 | `GET /v1/external_projects/{name}/backlog` | `mini-agent projects backlog list` |
| 改进积压：新增 | `POST /v1/external_projects/{name}/backlog` | `mini-agent projects backlog add` |
| review 任务模板预览 | `GET /v1/external_projects/{name}/review` | `mini-agent projects review` |

全部端点 owner-only，目标项目不存在/manifest 解析失败时返回结构化
错误（4xx + detail）而不是 500。看板侧新增待办时 `source` 固定只能是
`user_feedback`（看板手填语义上就是"人工反馈"，`outcome_review`/
`health_trend` 继续只由 entrypoint/review session 自动写入），且
review 模板预览旁边的「复制到对话框」按钮只是把生成的文本填进看板
"💬 对话"Tab 的输入框、并不自动发送——真正发起 review session 仍由
用户自己点发送。使用细节见
[docs/kanban-dashboard-guide.md](./kanban-dashboard-guide.md)
「🗂️ 外部项目 Tab」一节；`propose_fix`/`land_maintenance_fix`（提出
维护改动/落地分支，见第8节）明确不在这一期看板化范围内，仍然只能
通过 agent 工具调用或 Python 函数/`git merge` 完成。

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
- [next_doc/stock_watch_continuous_improvement_plan.md](../next_doc/stock_watch_continuous_improvement_plan.md) —
  改进积压账本（backlog）、周期性 review 任务模板这两块机制的设计
  过程，以及股票监控系统作为首个落地案例的完整记录。
- [next_doc/external_projects_kanban_integration_plan.md](../next_doc/external_projects_kanban_integration_plan.md) —
  §6.3 看板集成的设计过程与分期考量。
- [docs/kanban-dashboard-guide.md](./kanban-dashboard-guide.md) —
  「🗂️ 外部项目 Tab」一节，看板侧的具体操作说明。
- [docs/agent-commit-guard-guide.md](./agent-commit-guard-guide.md) —
  本文档的文档分层模式参照对象。
