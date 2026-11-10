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
| `web_app` | mapping | 否 | 见下，项目自带的 Streamlit 界面（看板探测/启动/打开） |

## `entrypoints.<key>`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `cmd` | str | 是 | 完整 shell 命令，如 `"python entrypoints/foo.py"` |
| `schedule` | str | 否 | 格式固定 `"cron: <5段cron表达式>"`；不填表示不自动定时，只能被手动/`projects run` 触发 |
| `timeout_sec` | int | 否 | 单次执行超时秒数 |
| `concurrency` | str | 否 | `managed`（缺省，占 cron 并发槽位、过资源仲裁）或 `unmanaged`（**仅给不调 LLM 的纯脚本用**：不占槽位、不排队、不过仲裁，仍有去重与超时/watchdog；建议同时写 `timeout_sec`）。其它取值解析报错 |
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

## `web_app`

对应 `next_doc/external_projects_web_app_plan.md`。声明项目自带的
Streamlit 界面。由**看板**（「🗂️ 外部项目」Tab）负责探测是否运行、启动、
停止与打开链接，**不经过 daemon**，也不影响 daemon 的调度。未声明本块的
项目，看板不显示 Streamlit 行。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `port` | int | 是 | 监听端口，范围 1024–65535。同一台机器上多个项目不得重复（注册表里检测到冲突时，相关项目都会显示配置错误）。约定：world_simulator 8502、stock_watch 8503、novel_writer 8504、novel_to_video 8505，新项目请取未占用端口 |
| `entry` | str | 否，缺省 `app.py` | Streamlit 入口文件，相对项目根目录 |
| `python` | str | 否 | 该项目的解释器路径。相对路径按项目根解析（如 `.venv/Scripts/python.exe`）。**缺省 = 启动看板的解释器**（通常与 daemon 为同一个环境） |
| `command` | str | 否 | 完整启动命令，覆盖缺省的 `python -m streamlit run ...`。可用占位符 `{python}`、`{entry}`、`{port}`。按 shell 规则切分，**不经过 shell 执行** |
| `host` | str | 否 | **不填 = 同一台机器**（看板可启停）。填写非本机地址（如 `192.168.1.20`）= 远程部署：看板只探测与打开链接，不启停。`localhost` / `127.0.0.1` 视为本机。**想在本机启动又允许局域网访问，不要填 host，改用 `bind`** |
| `require_login` | bool | 否 | 是否需要登录才能使用（默认 `false`）。`true` 时看板用包装入口先过共享账户登录门禁再执行 `entry`；看板启动面板可逐次改选。声明了自定义 `command` 的项目无法启用（启动被拒绝）；账户目录为空也无法启用。账户见 `python -m mini_agent.web_auth` |
| `bind` | str | 否 | 看板启动时监听地址选择的**建议值**（默认选中）。未声明则为 `0.0.0.0`（所有网卡，局域网可访问）；可填 `127.0.0.1`（仅本机）或本机网卡 IP。必须是合法 IP。启动时用户可在看板改选；**不改变启停归属**。Streamlit 本身无登录保护，开放到局域网前请确认网络可信 |
| `health_path` | str | 否，缺省 `/_stcore/health` | Streamlit 健康检查路径；返回 200 且正文含 `ok` 才视为就绪 |

示例：

```yaml
web_app:
  entry: app.py
  port: 8503
  python: .venv/Scripts/python.exe   # 可选
```

**运行机制**

- 缺省启动命令：`<python> -m streamlit run <entry> --server.port <port>
  --server.address 127.0.0.1 --server.headless true
  --browser.gatherUsageStats false`，工作目录为项目根。
- 启动的是独立新进程，看板或 daemon 重启不会把它带走；重新打开看板后会通过
  端口探测重新识别它。
- 进程 PID 与日志分别记录在 `<项目根>/.run/streamlit.pid` 与
  `<项目根>/.run/streamlit.log`（`.run/` 已加入仓库 `.gitignore`）。
- 看板只能停止由看板启动的进程（以 PID 文件判断归属）。手动在终端启动的
  实例会显示为「运行中（外部启动）」。看板可以在勾选确认后结束它，但只结束
  监听本端口且命令行含 `streamlit` 的进程，其它进程一律拒绝。
- 「重启」：先结束当前实例（看板启动的直接停止；外部启动的需确认后结束），
  等端口释放后再启动并等待就绪。远程项目不支持。
- 状态：未运行 / 启动中 / 运行中（看板启动）/ 运行中（外部启动）/ 端口被
  占用（非 Streamlit 程序）/ 远程可达 / 远程不可达。
- 本块与 `entrypoints` 互不影响；不声明 `schedule`，也不进入 cron。

