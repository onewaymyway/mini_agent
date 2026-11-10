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
   novel_to_video 8505。同一机器上冲突时，相关项目均显示配置错误、不启动。
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
