# 外部项目 Streamlit 界面：探测 / 启动 / 停止 / 打开

## 背景

`external_projects/` 下多个项目（world_simulator、stock_watch、novel_writer、
novel_to_video）各自带一个 Streamlit 页面，但此前看板只能看到 entrypoint
的执行状态，看不到这些页面是否在运行，也无法从看板拉起或打开。端口只写在
各自的文档字符串里，没有统一登记。

## 已确认的决策

1. **由看板管理，不经过 daemon**。Streamlit 进程由看板直接 `Popen` 拉起，
   是独立新进程：看板或 daemon 重启都不会把它带走，重新打开看板后通过端口
   探测重新识别。
2. **配置写在各自的 `project.yaml` 的 `web_app` 块**：`port`（必填）、
   `entry`、`python`、`command`、`host`、`health_path`。
3. **解释器**：`python` 不指定时使用启动看板的解释器（设计初衷是与 daemon
   同一环境；如果两者分属不同 venv，需要在 `project.yaml` 显式指定）。
4. **端口分配**：world_simulator 8502、stock_watch 8503、novel_writer 8504、
   novel_to_video 8505、info_hub 8506、article_to_video 8507。同一机器上冲突时，相关项目均显示配置错误、不启动。
5. **host**：不填 = 同一台机器，可启停。填非本机地址 = 远程部署，看板只探测
   与打开链接，不启停。远程能力目前不会用到，先保留配置位。
6. **不做 autostart**。只能手动启停。

## 实现

- `external_projects/manifest.py`：`WebAppSpec` 与 `_parse_web_app()`，端口
  范围 1024–65535，字段类型校验。
- `external_projects/web_app.py`（新增）：
  - 探测：TCP 连接 + `/_stcore/health` 返回 `ok`；
  - 归属：`<项目>/.run/streamlit.pid` 记录看板启动的 PID；只允许停止归属
    记录内的进程；
  - 启动：独立会话/进程组，日志写入 `.run/streamlit.log`；Windows 下经
    `mini_agent.utils.win_subprocess` 隐藏控制台窗口；
  - 停止：POSIX 用进程组 SIGTERM，超时 SIGKILL；Windows 用 `taskkill /T`；
  - 注册表读取：直接读本机 `ExternalProjectRegistry`，不经过 daemon。
- `apps/mini_agent_kanban/app.py`：外部项目卡片新增 Streamlit 行。
- 四个项目的 `project.yaml` 补充 `web_app` 块；stock_watch `PROJECT.md`
  中 8501 的错误写法修正为 8503；`.gitignore` 增加 `.run/`。

## 验证

`tests/test_web_app.py`：20 个用例，包括 manifest 校验、命令构造、端口冲突、
状态探测（停止 / 端口被占用 / 外部 Streamlit / 远程 / 陈旧 PID），以及一次
真实的启动 → 就绪 → 停止循环。

## 未做 / 后续

- autostart（明确暂不做）。
- 远程 host 的真实联调（当前只有配置与分支逻辑的单元覆盖）。
- Windows 下的真实启停尚未在目标机器上实测，请在 Windows 看板上验证一遍。

## 补充：局域网访问（bind）

问题：看板启动的外部 Streamlit 固定监听 127.0.0.1，同一局域网的其它设备无法用 IP 访问；
而填写 host 会被视为远程部署，看板拒绝启动，也无法满足需求。

方案：新增 `web_app.bind`（监听地址，默认 127.0.0.1），启动命令改为 `--server.address <bind>`；
探测与停止/重启的端口检查按实际监听地址进行；运行中且监听非回环地址时，看板显示局域网地址
与"无登录保护"提示。host 语义不变（仍表示远程部署）。

验证：单元测试 `tests/test_web_app_bind.py`；端到端用 `bind: 0.0.0.0` 真实启动 Streamlit，
本机局域网 IP 的健康检查返回 ok，而默认仅回环的实例经局域网 IP 不可达。

## 补充二：启动时选择监听地址（2026-11-10）

需求：看板启动外部项目时直接选择监听 IP，默认 0.0.0.0；检测端口固定使用 127.0.0.1。

实现：
- 看板「▶️ 启动」前增加「监听地址」选择：0.0.0.0（默认）/ 127.0.0.1 / 本机局域网 IP / 自定义 IP（经 `normalize_bind` 校验，非法时禁止启动）。
- `start(name, root, spec, bind=...)` 将所选地址写入运行记录（`.run/streamlit.pid` 的 `bind` 字段）；`restart` 未指定时沿用上次地址。
- 探测：固定 127.0.0.1；若实例监听具体网卡 IP，备选探测运行记录中的该 IP（`_find_listening` 一次探测同时返回监听主机与是否在监听）。
- manifest 的 `bind` 降级为启动时的建议值，默认 0.0.0.0。

验证：`tests/test_web_app_bind.py`；端到端用默认 0.0.0.0 与具体网卡 IP 各启动一次，127.0.0.1 探测与局域网健康检查均正常，停止正常。

## 补充三：外部项目卡片精简展示（2026-11-22）

需求：卡片默认只显示最关键的信息与启动管理，其他按钮和功能放进「详情」，点击后再显示。

实现（`apps/mini_agent_kanban/app.py`）：
- `render_external_projects_tab()` 的循环体拆为 `_render_external_project_card()`（精简卡片）、
  `_render_external_project_summary()`（最近一次执行 + 只读的自动调度状态）、
  `_render_external_project_details()`（原卡片里其余全部功能，逻辑不变、整体下沉一层）。
- 精简卡片保留：项目名、健康徽标、「📂 详情/🔼 收起」按钮、`_render_web_app_row()`
  （Streamlit 状态/打开/启动/停止/结束/重启；监听地址与登录选项改为同一行并排）、manifest 错误警告、摘要行。
- 详情区采用「按钮切换 + 条件渲染」而不是 `st.expander`：expander 内容每次重跑都会执行
  （包括改进积压、状态看板的后端请求），条件渲染则收起时完全不请求。展开状态存
  `session_state["ext_proj_detail_open_<name>"]`，默认收起。
- `_render_web_app_row()` 的 Streamlit 日志与远程部署说明拆到 `_render_web_app_extra()`，只在详情里渲染；
  启动超时提示改为「请点『📂 详情』查看日志」。
- 默认行为即为精简；没有新增开关（需求即默认如此）。

验证：新增 `tests/test_kanban_external_projects_compact_card.py`（6 用例：默认收起、摘要内容、
失败详情仅在展开后出现、展开/收起切换与后端请求时机、停止态/运行态下启动管理仍在精简卡片上）；
既有 `test_kanban_external_projects_refresh_entrypoints.py` 改为先点「详情」再操作。

未做：未在真实浏览器里目视检查布局（只有 AppTest 级验证）；Windows 实际渲染未实测。

