# Mini Agent 看板 (Kanban Dashboard)

基于 Streamlit 的一体化观测/交互面板，在 `apps/mini_agent_webdemo`（纯聊天）的基础上，
补充了会话管理、目标/Cron 看板、产出物浏览、自我状态与诊断信息。

## 前置条件

1. 先启动 mini-agent 的 HTTP daemon（提供 `/v1/*` 接口），例如：
   ```bash
   python -m mini_agent.cli.app daemon start
   ```
   默认监听 `http://127.0.0.1:8765`，Token 明文写在项目 `.agent/agent_api.key`
   （单用户模式）或 `.agent/users/tokens/owner.key`（多用户模式 owner）里。

2. 安装依赖：
   ```bash
   pip install -r apps/mini_agent_kanban/requirements.txt
   ```

## 启动看板

最简单的方式（手动填 Token）：
```bash
cd apps/mini_agent_kanban
streamlit run app.py
```
在左侧栏填入 API Base URL（默认 `http://127.0.0.1:8765/v1`）与 Token 即可连接。

推荐的启动方式（自动读 Token + 开启登录门禁，见下文两节）：
```bash
streamlit run apps/mini_agent_kanban/app.py -- \
    --auto-token \
    --project-root "E:\codes\mini_claude_code" \
    --require-login
```
⚠️ `--` 这一横杠不能省——它告诉 `streamlit run`"后面的参数是转发给脚本自己的"，
少了的话 streamlit 会把这些参数当成自己的参数从而报错。

### 命令行参数一览

| 参数 | 说明 |
|---|---|
| `--auto-token` | 启动时自动从项目 `.agent/` 目录读取 API Token，不用手动粘贴 |
| `--project-root <路径>` | 项目根目录（含 `.agent/` 子目录），配合 `--auto-token` / `--require-login` 使用；不传则用当前工作目录 |
| `--require-login` | 开启看板登录门禁：必须先用账户密码登录才能看到看板内容（见下文"登录鉴权"） |
| `--users-file <路径>` | 账户文件路径，配合 `--require-login` 使用；不传则用 `<项目根目录>/.agent/kanban_users.json` |

## 功能一览

| Tab | 内容 |
|---|---|
| 💬 对话 | 聊天、历史消息、逐 token 实时流式输出、事件流、发送/中断 |
| 🗂️ 会话管理 | 会话列表、新建 / 恢复 / 删除会话 |
| 📌 目标看板 | Goal / Objective 看板（按状态分列，可拖动状态）、新建目标、Cron Job 管理、Objective 执行进度 |
| 📁 产出物 | 浏览 `.agent/` 等目录下产出文件，预览与下载 |
| 🖼️ 产出预览 | 按任务/session 登记的产出物 manifest 语义化展示（图片内联预览、文档下载），支持 `?manifest_id=`/`?session_id=` 深链接 |
| 🧠 自我状态 | 具身智能自省信息（自主循环摘要、活跃目标数、最近活动、多用户会话池） |
| 🔧 诊断 | `/diagnostics` 原始信息，便于排障 |

顶部状态条常驻展示：运行状态、当前 Turn、自主等级、距下次 Tick 时间、Tick 计数、
订阅者数量，以及待审批权限请求（点击展开后可逐条允许/拒绝）。

## 对话流式输出

💬 对话 Tab 已接入 daemon 的 `GET /v1/stream/{turn_id}` SSE 端点：发送消息后会实时
逐 token 显示 Agent 输出（带打字机光标效果），而不是等一整轮跑完才整段刷出来。
页面刷新时如果发现 Agent 正在处理上一轮（比如另一个客户端发的消息），也会自动接管
当前 turn 继续流式显示，不会干等轮询。

工具调用相关事件（`tool_call`/`tool_result`/`tool_error`/权限请求）仍走 `/events`
轮询展示，不受影响。

**已知限制**：流式渲染期间 Streamlit 脚本处于阻塞状态，这段时间页面上其它按钮
（比如"中断"）不会响应交互，直到这一轮结束或 SSE 连接断开。

## 对话内联产出物展示

💬 对话 Tab 现在会把当前 session 已登记的产出物（图片、代码、文档等，来自
`record_artifact()`）直接嵌在对话流里，不用再切去"产出预览"Tab 来回找：

- 按 `created_at` 倒序展示（最新在前）
- 相比上一次渲染新出现的产出物默认展开，旧的默认折叠，避免每次刷新都是一整屏
  展开内容淹没对话本身
- 复用"产出预览"Tab 同一套内联预览逻辑：图片直接显示、代码/文本内联展示前
  5000 字符、PDF 提供新标签页预览链接、其余类型提供下载链接

只展示**当前 session**（`status().session_id`）的产出物；跨 session 的产出物
仍需要去"产出预览"Tab 按 session_id 过滤查看。

## Token 自动读取

加 `--auto-token --project-root <项目根目录>` 后，侧边栏会按 mini-agent 自身的
约定自动查找明文 Token 文件，查找顺序：

1. `<项目根目录>/.agent/agent_api.key`（单用户模式，和 `cli/daemon.py::DaemonClient`
   读取逻辑一致）
2. `<项目根目录>/.agent/users/tokens/owner.key`（多用户模式 owner token，兜底）

找不到时侧边栏会列出实际尝试过的完整路径，方便排查是不是 `--project-root` 传错了。
关掉 `--auto-token` 就退回手动输入模式。

## 登录鉴权

加 `--require-login` 后，打开看板会先要求输入账户密码，验证通过才能看到任何看板
内容。这是**看板页面自身的登录门禁**，和上面 mini-agent HTTP API 的 Token 鉴权是
两回事，互不替代：

- API Token → 看板用它去调 daemon 的 HTTP 接口（谁能操作 Agent）
- 看板账户 → 谁能打开这个 Streamlit 页面（本节说的这个）

### 创建 / 管理账户

看板 UI 里没有"注册"功能，账户必须由管理员在服务器上用命令行工具创建：

```bash
cd apps/mini_agent_kanban

# 新增账户（交互式输入两遍密码，不回显；密码至少 6 位）
python manage_users.py add alice --users-file "E:\codes\mini_claude_code\.agent\kanban_users.json"

# 删除账户
python manage_users.py remove alice --users-file "E:\codes\mini_claude_code\.agent\kanban_users.json"

# 列出所有账户（只列用户名，不显示密码）
python manage_users.py list --users-file "E:\codes\mini_claude_code\.agent\kanban_users.json"
```

`--users-file` 需要和启动 `app.py` 时传的保持一致（或者都不传，两边都会落到
`<项目根目录>/.agent/kanban_users.json`）。

### 免登录持久化

登录成功后会在 URL 里附加一个签名 token（`?auth=...`），12 小时内刷新页面/重新
打开标签页不用重新输密码。签名密钥存在 `.agent/kanban_session_secret`，删掉这个
文件会让所有已签发的免登录 token 失效（相当于强制所有人重新登录）。

带 `?auth=` 的完整链接分享给别人，对方也能直接登录进去——请像对待密码一样对待
这个链接，不要随手分享。

### 登录失败限流

连续输错密码会被限流（默认：15 分钟内失败满 5 次锁定 15 分钟），登录页会提示
剩余尝试次数或需要等待的时间。锁定记录存在 `.agent/kanban_login_attempts.json`。

这只是应用层的轻量限流，不是企业级防护：

- 挡得住脚本化的暴力枚举，挡不住"绕开 Streamlit 直接打底层 HTTP 请求"的攻击
  ——但看板本身也没有独立于这个登录表单之外的登录 API，攻击面基本就是这个表单
- 限流分桶优先按反向代理转发的 `X-Forwarded-For` 区分客户端；没有反向代理、
  或代理没设置这个头时，会退化成"同一账户不管谁试都共享同一份失败计数"
- 真要面向公网开放，建议在前面套一层 nginx/caddy 做 HTTPS + IP 限流
  （比如 fail2ban），网络层和应用层限流应该一起上，不是二选一

## 后续可扩展方向

- Ensemble 多候选结果对比展示
- 进化流水线（Skill 提案 / git worktree diff）可视化
- 权限历史与安全网风险等级（T0-T3）统计图表
- 流式渲染期间的非阻塞交互（`st.fragment` 或后台线程+队列方案）
