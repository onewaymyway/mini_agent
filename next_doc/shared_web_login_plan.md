# 通用 Streamlit 登录界面改进计划（shared_web_login_plan）

> 状态：已实施。

## 1. 背景

看板的登录门禁（`--require-login`）功能完整：账户、密码哈希、登录限流、会话登记、签名 token、
Cookie 免登录、账户管理页。但它写死在看板里：

- 账户文件、签名密钥、限流记录、会话表都挂在看板所在项目的 `.agent/` 下；
- 登录页、Cookie 逻辑都在 `apps/mini_agent_kanban/app.py` 里，外部项目用不了；
- 外部项目的 Streamlit 页面没有任何登录保护。

需求：把登录界面做成通用的，所有外部项目的 Streamlit 看板都能复用；共享同一套账户数据；
外部项目通过参数声明"需要登录才能使用"；看板的启动选项里可以选择。

## 2. 方案

### 2.1 共享模块 `mini_agent.web_auth`

| 文件 | 职责 |
|---|---|
| `accounts.py` | 账户（PBKDF2 哈希）、登录限流、会话登记、签名 token。由原 `apps/mini_agent_kanban/auth.py` 原样迁移，看板保留一个转发文件，行为不变。 |
| `gate.py` | 通用登录门禁：登录表单、Cookie 免登录、会话失效检测、退出登录。`require_login(app_title)` 是外部项目调用的入口。 |
| `__main__.py` | 账户命令行：`python -m mini_agent.web_auth add/list/remove/set-admin/unset-admin/passwd`。 |

### 2.2 共享账户目录

默认 `~/.agent/web_auth/`，可用环境变量 `MINI_AGENT_WEB_AUTH_DIR` 覆盖：

- `users.json`：账户
- `kanban_session_secret` → 更名为 `web_auth_secret`（签名密钥，所有应用共用）
- `login_attempts.json`：登录限流
- `sessions.json`：会话登记表

所有应用读写同一份文件，因此**一次登录可以在看板和外部项目之间通用**（同一浏览器、同一主机名下
Cookie 共享）；在任一处"退出登录"或被管理员踢掉，都会在所有应用中失效。

看板原有的项目内账户文件（`<项目>/.agent/kanban_users.json`）首次启动时会一次性复制到共享目录
（共享目录不存在时才复制，旧文件保留不删）。

### 2.3 外部项目接入：包装入口（不需要改项目代码）

`project.yaml` 里 `web_app.require_login: true` 声明需要登录；看板启动面板也有对应复选框，
默认值取自 `require_login`。

启用登录时，看板启动 Streamlit 的入口不再是项目的 `entry`，而是看板生成的包装脚本
`.run/auth_entry.py`：

1. `require_login()`：未登录则渲染登录页并 `st.stop()`，项目代码不会执行；
2. 通过后用 `runpy` 执行项目原来的入口；
3. 子进程环境注入 `PYTHONPATH`（mini_agent 的 `src` 目录，保证项目解释器能 import 门禁模块）和
   `MINI_AGENT_WEB_AUTH_DIR`（与看板一致的账户目录）。

由于门禁在项目代码之前执行，项目无法绕过登录。

限制：

- 声明了自定义 `command` 的项目无法注入包装入口，**启动会被拒绝**（不能静默地关掉登录）；
- 包装入口会自己调用 `st.set_page_config`，项目里自己的 `set_page_config` 会被忽略（Streamlit 只允许调用一次）。

### 2.4 依赖与降级

- Cookie 免登录需要 `extra-streamlit-components`。未安装时门禁退化为"仅会话登录"（刷新页面需重新登录），
  仍然有效拦截，不会因缺依赖放行。
- 门禁本身只依赖标准库与 streamlit。

### 2.5 看板变化

- 启动面板新增「🔐 需要登录（共享账户）」复选框，默认取 `require_login`；账户目录为空时给出提示。
- 运行中的卡片显示「🔐 需要登录」或「🔓 无登录」。
- 看板自身的登录门禁改为调用共享 `gate.py`，页面、账户管理、会话管理的行为不变。
- Cookie 名统一为 `mini_agent_web_auth`，因此升级后需要重新登录一次。

## 3. 兼容性与风险

- 看板的账户与会话从项目目录迁移到共享目录：旧会话在升级后失效，需要重新登录一次。
- 签名密钥改名：旧 token 全部失效（同上）。
- 共享账户意味着所有已接入应用的访问权限相同；如需按应用区分权限，需要另立项。
- 包装入口只在看板启动时生成；直接用 `streamlit run app.py` 启动项目不会有登录（这是预期的，
  登录只对经看板启动的实例生效）。

## 4. 验收

- `tests/test_web_auth.py`：账户增删改、密码校验、限流、会话撤销、token 签名与过期、门禁的
  未登录拦截（AppTest）、登录后放行、会话撤销后失效、共享目录环境变量、看板账户迁移。
- `tests/test_web_app_login.py`：`require_login` 解析、包装入口生成、自定义 command 拒绝、
  子进程环境注入、启动面板参数透传。
- 端到端：用看板的启动函数启动一个带登录的外部项目，未登录时页面不包含项目内容，登录后可见。
- 文档：`docs/external-projects-guide.md`、`docs/kanban-dashboard-guide.md`、`project.yaml` schema。

## 5. 实施记录

- 共享模块：`src/mini_agent/web_auth/`（`accounts.py` 迁移自看板 `auth.py`；`gate.py` 通用门禁；`__main__.py` 账户命令行）。
- 看板：`apps/mini_agent_kanban/auth.py` 改为转发；`app.py` 的 `render_login_gate` 委托给共享门禁；启动面板新增「🔐 需要登录」复选框与运行状态；`manage_users.py` 默认指向共享目录。
- 外部项目：`manifest.py` 新增 `web_app.require_login`；`web_app.py` 新增包装入口生成、子进程环境注入、`start/restart` 的登录参数。
- 测试：`tests/test_web_auth.py`（13 个，含 AppTest 门禁场景）、`tests/test_web_app_login.py`（16 个）。
- 端到端：AppTest 驱动生成的包装入口（未登录不渲染内容、错误密码、正确密码放行并保持、会话撤销后要求重新登录、退出登录）；
  通过看板的 `start` 以真实子进程启动登录保护的应用，健康检查正常。

修复过程中发现的问题（同时修复）：
- 原门禁在登录成功的那一轮仍 `return False`，要等下一次重跑才放行（看板原有问题）。改为本轮直接放行，并清空登录页占位。
- 同一轮里 CookieManager 与 `set_page_config` 的先后顺序：包装入口先设置页面配置、再渲染门禁，并屏蔽项目自己的 `set_page_config`。
