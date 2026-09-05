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

顶部状态条常驻展示：运行状态、当前动作（空闲/调用模型中/调用工具中）、当前模型、
当前 session、当前 Turn、自主等级、距下次 Tick 时间、Tick 计数、订阅者数量、
session 存储目录，以及待审批权限请求 / 待回答交互请求（点击展开后可逐条处理）。

## 多会话并行

侧边栏"🗂️ 本页面对话 session"下拉框（或"🗂️ 会话管理"Tab 里的"📌 本页面绑定到
此会话"按钮）决定**当前浏览器标签页**跟哪个 session 对话，绑定信息写在 URL
（`?session_id=xxx`）里，不同标签页各带不同 `session_id` 打开看板即可同时对话
多个互不干扰的 session。不选则退回旧版本的全局默认 session 行为。详见
`docs/kanban-dashboard-guide.md`。

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

看板 UI 本身没有"自助注册"功能——账户创建永远需要已有管理员身份，但账户的
增删改查现在有两条路径，二选一或搭配用都行：

**1. 命令行**（部署时创建第一个管理员账户用这条，因为页面兜底需要"没有任何
管理员"这个前提，命令行是最直接的路径）：

```bash
cd apps/mini_agent_kanban

# 新增账户，--admin 把它设为管理员（交互式输入两遍密码，不回显；密码至少 6 位）
python manage_users.py add alice --admin --users-file "E:\codes\mini_claude_code\.agent\kanban_users.json"

# 删除账户（不能删除最后一个管理员）
python manage_users.py remove alice --users-file "E:\codes\mini_claude_code\.agent\kanban_users.json"

# 列出所有账户（管理员账户带 [admin] 标记，不显示密码）
python manage_users.py list --users-file "E:\codes\mini_claude_code\.agent\kanban_users.json"

# 把已有账户设为 / 取消管理员（不能取消最后一个管理员）
python manage_users.py set-admin alice --users-file "E:\codes\mini_claude_code\.agent\kanban_users.json"
python manage_users.py unset-admin alice --users-file "E:\codes\mini_claude_code\.agent\kanban_users.json"
```

`--users-file` 需要和启动 `app.py` 时传的保持一致（或者都不传，两边都会落到
`<项目根目录>/.agent/kanban_users.json`）。

**2. 页面**：登录后顶部会出现"👤 账户管理" tab（仅 `--require-login` 模式
出现），不用再登服务器敲命令：

- 所有登录用户都能看到"🔑 修改我的密码"（需要输入当前密码验证）
- 只有管理员账户能看到"📋 账户列表"（新增账户、重置他人密码、切换管理员
  身份、删除账户）；非管理员账户点进这个 tab 只会看到"仅管理员可访问"的提示
- 有一个例外：如果账户文件里**一个管理员都没有**（比如账户是升级前用旧版
  `manage_users.py`/旧版 UI 建的，没有 `is_admin` 概念），账户管理对**所有
  登录用户**开放，直到有人被设为管理员为止，避免升级后没人能管理账户的死锁；
  一旦有了第一个管理员，这个兜底立刻失效，恢复"只有管理员可见"
- 无论哪条路径，"最后一个管理员"都不能被删除或取消管理员身份——防止不小心
  把所有人锁在账户管理门外，只能回服务器敲命令补救

账户记录里的 `is_admin` 字段是后加的：旧版本创建的账户文件不需要手动迁移，
读取时按 `is_admin=False` 处理，下次被命令行或页面改一次密码/权限之后才会
补上这个字段。

### 免登录持久化 与 会话管理（可撤销）

登录成功后会在 URL 里附加一个签名 token（`?auth=...`），12 小时内刷新页面/重新
打开标签页不用重新输密码。签名密钥存在 `.agent/kanban_session_secret`，删掉这个
文件会让所有已签发的免登录 token **全部**失效（相当于强制所有人重新登录）——
这是"核选项"，日常场景更常用的是下面的细粒度撤销。

**这个 token 本质上是一份免密登录凭证**：如果带 `?auth=...` 的完整链接意外
泄露（分享链接时忘了打码、反向代理访问日志、浏览器历史记录、截图/录屏……），
拿到它的人能直接登录进去，在 token 过期之前一直有效——所以请像对待密码一样
对待这个链接，不要随手分享。

为了能在怀疑泄露时"只撤销这一个会话"而不用把所有人都踢下线，看板会记一张
会话登记表（`.agent/kanban_sessions.json`：谁登录了、用的哪个会话、登录/过期/
最近活跃时间），配套三种撤销方式：

- **退出登录**（侧边栏"🚪 退出登录"按钮）：撤销当前这一个会话，不影响其他
  设备/标签页的登录。
- **退出所有其他会话**（"👤 账户管理" tab →"🖥️ 我的会话"，所有登录用户可用）：
  只能操作自己名下的会话——列出自己所有有效会话，一键撤销除当前会话之外的
  全部，也能单独撤销某一条。发现有自己不认识的会话在活跃（怀疑链接被泄露）
  时用这个自助补救，不用联系管理员。
- **管理员踢会话**（"👤 账户管理" tab →"🖥️ 所有会话"，仅管理员可见）：能看到
  所有用户的所有有效会话并撤销任意一个，也有"撤销所有会话"的核选项按钮。

撤销生效的时机：目标会话所在的浏览器标签页**下一次任意交互**（点按钮、切
tab……触发 Streamlit rerun）时就会被退回登录页，不需要等 token 自然过期，也
不需要对方手动刷新页面。

命令行同样能做（应急、不方便开浏览器时用）：

```bash
# 列出当前有效会话，可选按用户名过滤
python manage_users.py list-sessions --sessions-file "E:\codes\mini_claude_code\.agent\kanban_sessions.json"
python manage_users.py list-sessions --username alice --sessions-file "E:\codes\mini_claude_code\.agent\kanban_sessions.json"

# 撤销单个会话（session id 从 list-sessions 输出里拿）
python manage_users.py revoke-session <session_id> --sessions-file "E:\codes\mini_claude_code\.agent\kanban_sessions.json"

# 撤销某个用户的全部会话；不传 --username 则撤销所有人的全部会话
python manage_users.py revoke-all-sessions --username alice --sessions-file "E:\codes\mini_claude_code\.agent\kanban_sessions.json"
```

`--sessions-file` 不传时默认用 `<项目根目录>/.agent/kanban_sessions.json`，
需要和启动 `app.py` 时用的项目根目录一致。

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
