# 看板账户管理 UI —— 改进计划

## 实现进度

- [x] 阶段 1：`auth.py::UserStore` 新增 `is_admin`/`created_at`、
      `set_admin`/`is_admin`/`admin_count`/`list_users_detailed`，
      `add_user` 新增 `is_admin` 参数（默认 `False`，向后兼容）；
      新增 `LastAdminError` 异常，`remove_user`/`set_admin` 共享
      "最后一个管理员不能被删除/降级"保护逻辑。单测见
      `tests/test_kanban_auth_admin.py`（15 项，覆盖旧格式文件兼容、
      admin_count 统计、最后一个管理员保护、兜底期不受保护逻辑影响）。
- [x] 阶段 2：`manage_users.py` 同步更新——`add` 新增 `--admin` flag，
      新增 `set-admin`/`unset-admin` 子命令，`list` 输出加 `[admin]`
      标记（用 `list_users_detailed()`）。保护逻辑复用
      `UserStore`，命令行侧不重复实现。
- [x] 阶段 3：`TAB_DEFS` 拆成 `_BASE_TAB_DEFS`（固定部分）+
      `get_tab_defs(cli_args)`（`--require-login` 为真时在末尾追加
      "👤 账户管理"这一项），`render_tab_nav()`/`main()` 都改成按
      `cli_args` 现算 tab 清单，不再依赖模块级常量。
- [x] 阶段 4：`render_account_mgmt_tab(cli_args)`——A 区"改自己密码"
      （所有登录用户可见，校验当前密码）；B 区"账户列表 + 增删改"
      （仅管理员 / `admin_count() == 0` 兜底期可见）：表格展示
      用户名/是否管理员/创建时间，新增账户、重置他人密码（不要求
      验证旧密码）、切换管理员身份（最后一个管理员的复选框禁用）、
      删除账户（同样保护最后一个管理员；删除自己时立刻清登录态并
      清 URL 里的 auth token）。
- [x] 验收：`tests/test_kanban_auth_admin.py` 15 项全部通过；
      `manage_users.py add/set-admin/unset-admin/list` 手动验证行为
      符合预期（含"最后一个管理员不能被取消"保护）；`app.py`/`auth.py`/
      `manage_users.py` 均通过 `py_compile` 语法检查。


## 背景

看板登录门禁（`apps/mini_agent_kanban/auth.py` + `manage_users.py`，见
`next_doc/kanban_login_gate_plan.md`）已经实现，但账户的增删改目前**只能
在服务器本机跑 `manage_users.py` 命令行**，需要能登录服务器 shell 才能
管理账户——如果看板部署给非技术同事用，或者部署在你自己不方便随时开
终端的环境（比如临时用手机开 SSH），"加一个账户"这种小事也要走命令行，
不方便。这次要加的是：**在看板页面里直接管理账户**，不用再登服务器敲命令。

## 现状数据结构（`auth.py::UserStore`）

```json
{"alice": {"salt": "<hex>", "hash": "<hex>"}}
```

没有"谁是管理员"的概念——任何能登录的账户地位都一样。要在页面里做账户
管理，必须先解决"谁有权限管理账户"这个问题，否则等于每个登录用户都能
互相改密码/删账户，不安全。

## 方案

### 1. 账户记录新增 `is_admin` 字段（向后兼容）

```json
{"alice": {"salt": "<hex>", "hash": "<hex>", "is_admin": true, "created_at": 1234567890.0}}
```

- 旧文件里没有 `is_admin` 字段的账户，读取时按 `False` 处理（`dict.get`
  默认值，不用做文件迁移脚本）。
- `created_at` 纯展示用（账户列表页"创建时间"列），旧账户没有这个字段
  时列表里显示"未知"。

`UserStore` 新增方法（都是在现有 `_load`/`_save` 基础上做的小扩展，不改
现有 `add_user`/`remove_user`/`verify`/`list_users` 的调用方式和返回值，
不影响 `auth.py` 里已经在用这些方法的登录逻辑）：

```python
def add_user(self, username, password, is_admin: bool = False) -> None:
    ...  # 新增 is_admin 参数，默认 False，不传时行为和现在完全一致

def set_admin(self, username: str, is_admin: bool) -> bool: ...
def is_admin(self, username: str) -> bool: ...
def list_users_detailed(self) -> list[dict]:
    """返回 [{"username":, "is_admin":, "created_at":}, ...]，供 UI 表格用；
    list_users() 原方法保留不动（仍只返回用户名列表，manage_users.py 的
    list 命令继续用它）。"""
def admin_count(self) -> int: ...
```

### 2. Bootstrap：谁是第一个管理员

鸡生蛋问题——账户文件里一个管理员都没有时，页面上的"账户管理"入口该给
谁看？两条路径都保留：

- **命令行**：`manage_users.py add <用户名> --admin`，新增 `--admin`
  flag，首次建号时直接指定成管理员（推荐给部署时用）。
- **页面兜底**：`UserStore.admin_count() == 0` 时（比如你已经跑
  `--require-login` 但账户是之前用旧版 `manage_users.py`/旧版 UI 建的，
  没有任何人有 `is_admin=True`），账户管理 Tab 对**所有已登录用户**可见
  ——避免升级后没人能管理账户的死锁；一旦有人在页面上把自己或别人设成
  管理员，`admin_count() > 0`，兜底立刻失效，恢复"只有管理员可见"。

### 3. 页面位置：新增 Tab，仅在 `--require-login` 模式下出现

不复用侧栏（侧栏是连接配置，性质不同），在 `TAB_DEFS`（app.py:12058）
里追加一项：

```python
("account_mgmt", "👤 账户管理", lambda client: render_account_mgmt_tab()),
```

但这个 tab **不是永远出现**——`TAB_DEFS` 改成按条件组装：只有
`cli_args.require_login` 为真时才把这一项拼进去（不开登录门禁的部署，
账户管理没有意义，且看不到这个入口对现有用户是零影响，`main()` 里已经
有 `cli_args` 可以拿到）。tab 内部再按"是不是管理员 / 是否兜底期"二次
判断，不是管理员且不在兜底期时，这个 tab 显示"仅管理员可访问"提示而不
是账户列表（tab 按钮本身出现了但点进去看不到敏感内容，比"tab 直接消失"
更简单实现，也不构成信息泄露——只是告诉你"这里需要管理员权限"）。

### 4. Tab 内容

分两块：

**A. 修改自己的密码**（所有已登录用户可见，不需要管理员权限）
- 表单：当前密码、新密码、确认新密码
- 校验当前密码正确 → `store.add_user(username, new_password, is_admin=store.is_admin(username))`
  （复用现有的 upsert 语义，保留原有 `is_admin` 不被误改成 False）
- 成功后不需要强制重新登录——密码哈希变了但当前会话的 token 签名逻辑
  和密码无关（token 只签用户名+过期时间，见 `make_token()`），不影响
  当前登录态

**B. 账户列表 + 增删改**（仅管理员 / 兜底期可见）
- 表格：用户名 / 是否管理员 / 创建时间
- 新增账户：用户名 + 密码 + "设为管理员"复选框
- 重置某账户密码：选用户名 + 新密码，管理员可以不知道对方原密码直接重置
  （和"改自己密码"那条路径分开，不要求验证旧密码——这是管理员权限的
  应有能力）
- 切换管理员身份：勾选框，但**不能把最后一个管理员降级**（`set_admin`
  内部检查：目标账户当前是管理员、且 `admin_count() <= 1` 时拒绝，
  UI 侧同步禁用那个复选框并给提示，防止误操作把所有人锁在门外）
- 删除账户：同样不能删除"最后一个管理员"；删除自己当前登录的账户时，
  操作成功后立刻登出（清 `st.session_state.authenticated` + 清 URL 里
  的 `auth` token），不能删完自己还留在已登录状态里

### 5. `manage_users.py` 同步更新

- `add` 子命令新增 `--admin` flag
- 新增 `set-admin <username>` / `unset-admin <username>` 子命令，内部
  调用同一个 `UserStore.set_admin()`，"最后一个管理员不能降级"的保护
  逻辑写在 `UserStore` 里，命令行和页面自动共享，不用两处各写一遍
- `list` 子命令输出加上 `[admin]` 标记（用 `list_users_detailed()`）

### 不做的事

- 不做"自助注册"——账户创建（无论是命令行还是页面）都需要已有管理员
  身份，看板本身仍然没有对外开放的注册入口，维持 `auth.py` 文件顶部
  注释里"账户应该由管理员创建"的既有原则不变。
- 不做角色细分（比如"只读账户"之类）——目前只有"普通登录用户"和
  "管理员（可以管理其他账户）"两级，够用，不过度设计。
- 不改 token/session 机制本身，`kanban_session_secret`、12 小时 TTL、
  登录失败限流都不动。

## 验收方式

- 单测（`tests/`，新增 `test_kanban_auth_admin.py`，直接测
  `apps/mini_agent_kanban/auth.py` 里的 `UserStore` 新方法，不依赖
  Streamlit 运行时）：
  - 旧格式文件（无 `is_admin` 字段）读出来 `is_admin=False`，不报错
  - `add_user(..., is_admin=True)` 落盘后 `is_admin("x") == True`
  - `admin_count()` 统计正确
  - `set_admin` 在"目标是最后一个管理员"时拒绝并返回 False/抛出
    明确异常（具体选哪种约定实现时定，测试跟着约定走）
  - 兜底逻辑：`admin_count() == 0` 时不受"最后一个管理员"保护逻辑
    影响（因为压根没有管理员，不存在"降级"这一说）
- 手动验证：`--require-login` 起看板 → 用已有账户登录 → 账户管理 tab
  能看到自己 → 加一个新账户设为普通用户 → 用新账户登录确认看不到账户
  管理里的"B 区"，只能看到"改自己密码" → 回到管理员账户尝试把新账户
  设为管理员、再尝试把自己降级验证"最后一个管理员保护"生效
