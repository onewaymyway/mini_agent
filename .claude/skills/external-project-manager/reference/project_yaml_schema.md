# `project.yaml` 字段参考

> 对齐 `src/mini_agent/external_projects/manifest.py`（`load_manifest()`
> 与各 `_parse_*` 函数）的实际校验规则。字段名/类型如果与该文件出现
> 分歧，以源码为准，本文档需要同步更新（改动 `manifest.py` 时记得回来
> 改这里）。

## 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | str | 是 | 项目名，建议与目录名/注册表 key 一致 |
| `entrypoints` | mapping | 是，至少一个 | key 是 entrypoint 名，value 见下 |
| `health_check` | mapping | 否 | `{cmd: str}`，daemon 探测健康状态的方式 |
| `resources` | mapping | 否 | 见下，缺省 `allowed_domains: []`, `max_concurrency: 1` |
| `review` | mapping | 否 | 见下，缺省 `cadence: weekly`, `enabled: false` |
| `dashboard.kanban_view` | mapping | 否 | 见下，声明式看板状态视图 |

## `entrypoints.<key>`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `cmd` | str | 是 | 完整 shell 命令，如 `"python entrypoints/foo.py"` |
| `schedule` | str | 否 | 格式固定 `"cron: <5段cron表达式>"`；不填表示不自动定时，只能被手动/`projects run` 触发 |
| `timeout_sec` | int | 否 | 单次执行超时秒数 |
| `params` | list | 否 | 见下，声明这个 entrypoint 需要的位置参数 |

`schedule` 只做"是否长得像 cron 表达式"（5 个空白分隔字段）的基本形状
校验，不校验语义合法性（比如字段取值范围）；语义错误会在真正触发调度
时才暴露。

## `entrypoints.<key>.params[]`

每一项：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | str | 是 | 参数名（用于看板渲染输入框标签，不是真正的 `--name=value` 命名参数） |
| `required` | bool | 否，缺省 `true` | 是否必填 |
| `default` | str | 否 | 缺省值；**留空的可选参数如果没给 default，触发时会连同它后面所有参数一起被跳过**（位置参数语义，见下方"坑"） |
| `help` | str | 否 | 说明文字 |

**参数最终以"按声明顺序拼成位置参数追加在 `cmd` 后面"的方式传递**，
与 `sys.argv[1:]` 的读取方式直接对齐，不是 `--flag value` 风格。

**已知的坑**（`stock_watch/project.yaml` 里 `fetch_iwencai_cookie`
注释记录的真实教训）：如果一个可选参数"没填值也没有 `default`"，
`manifest.py::build_cmd_with_params()` 会直接不追加这个参数、也不追加
它后面的所有参数（因为跳过中间一个位置参数会导致后面的参数错位）。
**结论：如果一个 entrypoint 有多个 `params`，要么全部 `required:
true`，要么给每个可选参数都配 `default`，不要出现"中间某个可选参数
既非必填又没有 default"的情况。**

## `health_check`

| 字段 | 类型 | 必填 |
|---|---|---|
| `cmd` | str | 是（如果声明了 `health_check` 块） |

约定：退出码 0 = 健康，非 0 = 不健康。健康检查不应该依赖外部网络/
第三方服务是否可用，只验证"依赖能正常导入 + 本地状态文件可读"。

## `resources`

| 字段 | 类型 | 缺省值 | 说明 |
|---|---|---|---|
| `allowed_domains` | list[str] | `[]` | 域名白名单（声明式，当前框架层不做强制拦截，是文档化+未来仲裁机制的基础） |
| `max_concurrency` | int（≥1） | `1` | 并发上限 |

## `review`

| 字段 | 类型 | 缺省值 | 说明 |
|---|---|---|---|
| `cadence` | str（非空） | `"weekly"` | 描述性字符串，当前不做语义校验 |
| `enabled` | bool | `false` | `false` 时 `mini-agent projects review <name>` 只打印"未开启"提示，不影响其它命令；`true` 也不会自动触发定时复盘（真正的定时接线视 `stock_watch_continuous_improvement_plan.md` 阶段4 的实施进度），只影响这条命令是否打印任务模板 |

## `dashboard.kanban_view`

对应 `next_doc/external_projects_generic_kanban_view_refactor_plan.md`
第2/3节。用于把项目自己产出的一份 JSON 状态文件渲染成通用看板视图，
不需要为每个外部项目单独写前端代码。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `data_file` | str | 是 | 相对项目根的路径，指向一份 JSON 文件；框架按 `external_projects_generic_kanban_view_refactor_plan.md` 约定的方式读取记录数组（参考 stock_watch 的 `data/pool_tracking_latest.json`，记录数组包在 `entries` 键下） |
| `id_field` | str | 是 | 每条记录里作为唯一标识的字段名 |
| `title_field` | str | 是 | 卡片标题字段名 |
| `state_field` | str | 是 | 决定卡片落在哪一列的字段名 |
| `states` | list | 是，至少一个 | 见下，声明看板列 |
| `metric_fields` | list | 否 | 见下，卡片正文展示的数值字段 |
| `detail_list_field` | str | 否 | 卡片展开后展示的列表型详情字段（如"理由列表"） |
| `change_state` | mapping | 否 | 见下，声明"变更状态"表单绑定哪个 entrypoint |

`states[]` 每一项：`value`（状态取值，需与 `state_field` 里实际出现
的值一致）、`label`（列标题展示文本）、`collapsed`（可选 bool，是否
默认折叠，用于"已淘汰"一类不需要日常盯的状态列）。

`metric_fields[]` 每一项：`field`（记录里的字段名）、`label`（展示
名）、`format`（展示格式，如 `"number"`）。

`change_state`：`entrypoint`（要调用的 entrypoint key，通常是一个专门
的"变更状态"entrypoint，如 stock_watch 的 `change_pool_state`）、
`id_param`/`state_param`/`note_param`（对应 entrypoint 的哪几个
`params` 名字，用于看板表单提交时映射参数）。

如果项目数据没有"状态流转"语义（纯跑批产出报告类项目），整个
`dashboard` 块可以省略，看板会退化为通用的账本/日志视图。
