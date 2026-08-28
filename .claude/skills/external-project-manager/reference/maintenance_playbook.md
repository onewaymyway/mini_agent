# 外部项目维护对照表

> 把"用户说的一句话"映射到具体 CLI 调用/操作方式。命令都对应
> `src/mini_agent/cli/commands/projects_cmd.py` 里已实现的子命令，不
> 引用不存在的命令；如果需要的能力当前 CLI 没有，记到对应设计文档的
> "发现的问题"一节，不在这里编一个不存在的命令。

## 1. 体检 / 看整体状态

用户意图："看看 XX 项目现在怎么样"、"XX 项目健康吗"

```
mini-agent projects status <name>
```

内部行为：跑一次 `health_check.cmd`（如果声明了）+ 读账本最后一条
记录，两者合起来给出一个整体状态判断。这是主动探测，会实际执行
`health_check` 命令；如果只想不做任何探测地看一眼列表，用
`mini-agent projects list`（纯读注册表 + 账本最后一条，不触发任何
执行）。

## 2. 看执行历史

用户意图："XX 项目最近跑得怎么样"、"有没有失败过"、"最近几次 kline_batch
的结果"

```
mini-agent projects ledger <name> [limit]
```

读 `.agent/run_status.jsonl`，按时间正序展示每条记录的
`entrypoint`/`started_at`/`finished_at`/`exit_code`/`trigger`/
`error_summary`。`limit` 省略时展示全部。失败记录的 `detail` 字段
（截断后的 traceback 或子进程 stderr 尾部）是定位问题的关键信息，
必要时直接读原始 jsonl 文件里对应行。

## 3. 看改进积压

用户意图："XX 项目有什么值得优化的"、"有没有攒着没处理的改进项"

```
mini-agent projects backlog <name> list
```

追加一条新的改进项：

```
mini-agent projects backlog <name> add "<描述>"
```

积压账本状态流转（`open` → `landed`/`dismissed`）由框架维护，具体
状态含义参考 `next_doc/stock_watch_continuous_improvement_plan.md`
第4节。

## 4. 手动触发某个 entrypoint

用户意图："帮我手动跑一下 XX 项目的 hotlist_scan"、"现在就分析一下
600519"

```
mini-agent projects run <name> <entrypoint> [-- <位置参数...>]
```

具体参数拼接方式（是否需要额外位置参数、参数顺序）看该 entrypoint 在
`project.yaml` 里的 `params` 声明；参考本 skill 的
`reference/project_yaml_schema.md` 里"已知的坑"一节，避免可选参数
被静默跳过。

## 5. 发起周期性复盘

用户意图："该给 XX 项目做每周复盘了"

```
mini-agent projects review <name>
```

前提：`project.yaml` 里 `review.enabled: true`，否则只打印"未开启"
提示。命令本身只是打印一份复盘任务模板（结合账本 + 改进积压账本），
不会自动执行复盘——复盘的实际动作（读账本、判断趋势、决定要不要处理
某个积压项）需要 agent 或用户按打印出的模板去做。

## 6. 修复一个失效的功能

用户意图："XX 项目的抓取好像挂了，帮我修一下"、"某个网站改版了，
更新一下选择器"

**不要**做的事：在当前 daemon 进程/当前对话所在的项目里直接改外部
项目的源文件，当作"顺手改一下"。

**应该**做的事（呼应 `external_projects_workspace_plan.md` 原则四）：

1. 先用第 2 节（ledger）确认失败的具体表现（哪个 entrypoint、
   `error_summary`/`detail` 说明了什么）。
2. 以该外部项目自己的目录为工作根，定位问题代码（通常是数据源模块
   里的选择器/字段名假设失效）。
3. 修改后**在该项目自己的目录下**跑它自己的测试（`pytest` 覆盖到的
   纯逻辑用例）+ 手动跑一次相关 entrypoint 验证修复有效。
4. 修复后追加一条 backlog 记录（状态 `landed`），说明改了什么、为什么
   —— 给未来的自己/大管家留一条可追溯的变更记录，而不是让修复动作
   隐式消失在 git log 里。
5. 如果这类失效是"每隔一段时间就会发生"的模式（比如某个网站改版频率
   较高），考虑在 `PROJECT.md` 的"已知限制"一节补一句说明，帮助未来
   更快定位同类问题。

## 7. 新建一个外部项目

见 `SKILL.md` 的"创建流程"一节，本文档不重复。
