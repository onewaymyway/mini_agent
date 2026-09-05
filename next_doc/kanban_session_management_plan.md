# 看板会话管理（可撤销登录 token）—— 改进计划

## 实现进度

- [x] `auth.py` 新增 `SessionStore`（`create`/`is_valid`/`touch`/
      `revoke`/`revoke_all_for_user`/`revoke_all`/`list_sessions`）；
      `make_token`/`verify_token` 改成签入并校验 `session_id`，旧格式
      （3 段）token 统一判定失效。单测见
      `tests/test_kanban_session_management.py`（21 项）。
- [x] `app.py`：`render_login_gate` 改成"签名 + `SessionStore.is_valid`"
      双重校验，已登录状态每次 rerun 都重新核对；"退出登录"按钮改成
      先撤销服务端会话再清本地状态；"👤 账户管理" tab 新增"🖥️ 我的
      会话"（所有用户，退出所有其他会话）和"🖥️ 所有会话"（仅管理员，
      可撤销任意会话/撤销全部）。
- [x] `manage_users.py` 新增 `list-sessions`/`revoke-session`/
      `revoke-all-sessions` 子命令，复用同一份 `SessionStore` 逻辑。
- [x] 文档同步：`apps/mini_agent_kanban/README.md`"免登录持久化"节
      改写为"免登录持久化与会话管理（可撤销）"，说明三种撤销方式和
      命令行用法；`docs/kanban-dashboard-guide.md`"功能 Tab 一览"表
      的"👤 账户管理"行补充会话管理描述。
- [x] 验收：单测全部通过；`manage_users.py` 新增子命令手动验证行为
      符合预期；`app.py`/`auth.py`/`manage_users.py` 均通过
      `py_compile` 语法检查。

## 背景

看板登录门禁（`--require-login`）用一个签名 token 存在 URL query param
里（`?auth=...`）维持免登录状态，12 小时内刷新页面不用重新输密码。这个
token 只包含用户名 + 过期时间戳 + HMAC 签名（不含密码/密码哈希），但
它本质上是一份"免密登录凭证"——一旦这个带 token 的 URL 意外泄露（分享
链接时忘了打码、反向代理的访问日志、浏览器历史记录、截图/录屏……），
拿到它的人就能直接以这个身份进入看板，在 token 自然过期（默认 12 小时）
之前一直有效。

旧版本唯一的"撤销"手段是删掉 `.agent/kanban_session_secret` 签名密钥
文件——这会让**所有**已签发的 token 全部失效，等于把所有人都踢下线，
杀伤面太大，不适合"我怀疑自己的某个链接被泄露了，想只撤销那一个会话"
这种日常场景。

这次要加的是：**会话登记表**（谁登录了、用的哪个会话、什么时候登录/
过期/最近活跃），配套"退出所有其他会话"（自助）、管理员踢掉任意会话
两个操作，把撤销粒度从"全体"细化到"单个会话"。

## 方案

### 1. `auth.py` 新增 `SessionStore`

每次登录成功都在这张登记表里留一条记录：

```json
{"<session_id 十六进制>": {
    "username": "alice",
    "issued_at": 1234567890.0,
    "expires_at": 1234567890.0,
    "client_id": "1.2.3.4",
    "last_seen": 1234567890.0
}, ...}
```

方法：`create(username, client_id, ttl_seconds)`、`is_valid(session_id,
username)`、`touch(session_id, min_interval)`（节流更新 `last_seen`，
避免每次 Streamlit rerun 都写一次磁盘）、`revoke(session_id)`、
`revoke_all_for_user(username, except_session_id)`、`revoke_all()`、
`list_sessions(username=None)`（只读，过滤过期，不落盘清理）。

### 2. token 格式加入 `session_id`

```
旧: username:exp:sig
新: username:session_id:exp:sig
```

`make_token(username, session_id, exp, secret)` 不再自己算过期时间，
由调用方从 `SessionStore.create()` 拿到的 `exp` 传入，保证 token 里的
`exp` 和登记表里的 `expires_at` 永远一致。`verify_token(token, secret)`
只验证签名和过期时间，返回 `(username, session_id)` 或 `None`——它本身
不查询 `SessionStore`（保持无 IO 的纯函数，方便单测），调用方必须再用
`SessionStore.is_valid(session_id, username)` 补一道"这个会话是否还
活着"的检查，两步都通过才算真正登录成功。

旧格式 token（3 段）在新的 `split(":")` 逻辑下会因为解包成 3 个值而不是
4 个而抛异常，统一按 `None` 处理——升级后所有历史 token 失效，强制重新
登录，这是预期行为（和当年引入 token 机制、以及轮换签名密钥的效果一致），
不需要迁移脚本。

### 3. `render_login_gate` 的校验逻辑改成"签名 + 会话"双重检查

- `st.session_state.get("authenticated")` 为真时，不再无条件放行——
  每次进到这个函数都用 `session_state["session_id"]` 重新查一次
  `SessionStore.is_valid()`。这样撤销操作最迟在**这个浏览器标签页下一次
  任意 rerun**（点按钮、切 tab……）时就会生效，不需要等到用户手动刷新
  页面或 token 自然过期。
- 校验通过顺带 `session_store.touch(sid)`，更新"最近活跃时间"供会话列表
  展示（节流写盘，默认 5 分钟一次）。
- 登录成功时改成 `session_store.create(...)` 拿到 `(session_id, exp)`
  再签 token，`session_id` 一并存进 `st.session_state`。

### 4. "退出登录"按钮改成真正撤销会话

原来"退出登录"只是清 `st.session_state` 和 URL 里的 query param——这个
token 本身在签名层面依然合法，如果之前被复制分享出去，"退出登录"之后
那份泄露出去的链接其实还能用。现在改成：点击时先
`SessionStore.revoke(session_id)`，再清本地状态，两步都做。

### 5. "👤 账户管理" tab 新增两块

- **B. 我的会话**（所有登录用户可见，插在"改自己密码"和"账户列表"之间）：
  列出自己名下所有有效会话（标出哪个是"当前会话"、登录时间、最近活跃
  时间），"退出所有其他会话"按钮（`revoke_all_for_user(me,
  except_session_id=当前)`），也能单独撤销某一条非当前会话。这是应对
  token 泄露的自助工具——发现有陌生会话在活跃，一键踢掉，不用联系
  管理员。
- **C（原 B）账户列表… 新增"🖥️ 所有会话"**（仅管理员/兜底期可见）：
  列出所有用户当前有效的会话，管理员可以撤销任意一个；额外提供
  "撤销所有会话"的核选项按钮（效果类似轮换签名密钥，但不用真的去动
  密钥文件）。

### 6. `manage_users.py` 同步新增子命令

`list-sessions [--username]` / `revoke-session <session_id>` /
`revoke-all-sessions [--username]`，都是 `SessionStore` 方法的直接
包装，命令行和页面共享同一份逻辑。

### 不做的事

- 不改成基于 Cookie 的会话方案——Streamlit 原生不支持自己写 Cookie，
  需要额外组件/反向代理层配合，改造成本明显更高；当前"URL token + 可
  撤销的会话登记表"已经能覆盖"泄露后能补救"这个核心诉求。
- 不做"记住我这台设备"之类的设备指纹识别——`client_id` 只是尽力而为
  的展示信息（来自 `X-Forwarded-For`，没有反向代理时拿不到），不作为
  安全判断依据。
- 不缩短默认 12 小时 TTL——会话可以随时被撤销之后，"泄露后能不能及时
  止损"已经不完全依赖 TTL 长短，没必要为了这个牺牲日常使用的免登录
  体验。

## 验收方式

- 单测（`tests/test_kanban_session_management.py`，直接测
  `SessionStore`/`make_token`/`verify_token`，不依赖 Streamlit 运行时）：
  - `create`/`is_valid`/`revoke`/`revoke_all_for_user`/`revoke_all`/
    `list_sessions`/`touch` 各自的基本行为和边界情况（过期、用户名不
    匹配、节流窗口内不更新等）
  - 端到端场景：签发 token → 签名校验通过 → 撤销会话 → 同一个 token
    签名校验依然合法，但配合 `is_valid` 复核后应判定失效
  - 旧格式（3 段）token 被 `verify_token` 判定为 `None`
- 手动验证：`--require-login` 起看板 → 登录 → 账户管理 tab"我的会话"
  能看到当前会话 → 用另一个浏览器/隐私窗口登录同一账户 → 回到第一个
  窗口点"退出所有其他会话" → 第二个窗口下一次交互应该被退回登录页 →
  管理员账户能在"所有会话"里看到并撤销任意用户的会话 → 命令行
  `list-sessions`/`revoke-session`/`revoke-all-sessions` 行为符合预期
