# 看板免登录 token 载体迁移（URL query param → Cookie）—— 改进计划

## 实现进度

- [x] `app.py`：新增 `_get_cookie_manager`/`_cookie_get_auth`/
      `_cookie_set_auth`/`_cookie_clear_auth` 四个辅助函数，封装
      `extra-streamlit-components` 的 `CookieManager`；`main()` 在
      `--require-login` 分支最外层每次 rerun 重新创建一个
      `CookieManager` 实例存进 `st.session_state["_cookie_manager"]`，
      本模块其余地方一律从这里取，不重复实例化（原因见
      `_get_cookie_manager()` 的函数说明）。
- [x] `render_login_gate`：读取免登录 token 从 `st.query_params.get
      ("auth")` 改成 `_cookie_get_auth()`；登录成功后签发 token 从
      `update_query_params(auth=make_token(...))` 改成
      `_cookie_set_auth(token, exp)`；token 失效/会话被撤销时的清理从
      `update_query_params(auth=None)` 改成 `_cookie_clear_auth()`。
- [x] "退出登录"按钮、"👤 账户管理" tab 里"删除自己账户"分支：同样从
      `update_query_params(auth=None)` 改成 `_cookie_clear_auth()`；
      两处都补了显式 `st.rerun()`——原来写 `query_params` 会自动触发
      一次 rerun，改成 Cookie 之后不再有这个副作用，需要手动触发才能
      让登录页立刻显示出来。
- [x] `apply_deep_link_query_params`/`get_active_session_id`/
      `set_active_session_id`/`get_pinned_session_ids` 等其余用到
      `st.query_params`（`session_id`/`pinned`/`manifest_id` 等深链接
      参数）的地方**不受影响**——这次只搬"认证 token"这一个 key，
      其余深链接场景仍然需要"体现在 URL 上才能分享/多标签页独立"这个
      特性，继续留在 query params 里，本来也不含敏感信息。
- [x] 依赖：`apps/mini_agent_kanban/requirements.txt` 新增
      `extra-streamlit-components>=0.1.60`，仅 `--require-login` 模式
      需要，未安装时给出明确的 `st.error` 提示 + 安装命令，而不是让人
      看一个陌生的 `ModuleNotFoundError`。
- [x] 文档同步：`apps/mini_agent_kanban/README.md`"免登录持久化与会话
      管理"节改写，说明 token 现在存 Cookie、需要额外依赖、旧版本
      `?auth=...` 链接会在下次访问时自动失效；`auth.py` 模块顶部
      docstring 及 `SessionStore` 相关注释同步更新措辞（"URL 免登录
      token" → "Cookie 免登录 token"）。
- [x] 验收：`app.py`/`auth.py` 均通过 `py_compile` 语法检查；手动确认
      `extra_streamlit_components.CookieManager` 的 `get`/`set`/
      `delete`/`get_all` API 签名符合本次改动的假设（`set()` 会同步
      更新自身内存快照，`delete()` 对不存在的 Cookie 会抛
      `KeyError`，均已在辅助函数里处理）。

## 背景

看板登录门禁（`--require-login`）原来把签名 token 拼进 URL query
param 里（`?auth=...`）维持免登录状态。这个 token 本身的签名机制没有
问题（HMAC 签名 + 过期时间 + `SessionStore` 可撤销，见
`kanban_session_management_plan.md`），但**载体选在 URL 上会带来额外
的泄露面**，和签名算法是否安全无关：

- 浏览器会把完整 URL（含 query string）记进历史记录；
- 看板如果跑在反向代理/Nginx 后面，access log 通常会明文记录完整
  URL；
- 页面里如果有跳转到第三方资源，浏览器可能把当前完整 URL 当作
  `Referer` 头带给第三方站点；
- 用户复制地址栏链接分享、截图、录屏演示，都可能无意中带出这个
  token——`auth.py` 里原有的注释也提到了这个已知坑。

`SessionStore` 的撤销机制能在"发现泄露后"降低影响面，但没法从源头上
阻止 token 出现在 URL 里。

## 方案取舍

讨论过三个方向：

1. **改用浏览器 Cookie**（本次采用）：用
   `extra-streamlit-components` 的 `CookieManager` 组件读写 Cookie，
   token 不再出现在地址栏/历史记录/日志/Referer 里；`make_token`/
   `verify_token`/`SessionStore` 这套后端签名与撤销逻辑完全不用动，
   只改"签好的字符串存哪、从哪读"这一层，改动集中、风险低。代价是
   这不是 HttpOnly Cookie（组件靠注入 JS 操作 `document.cookie`
   实现），页面自身 JS 依然能读到，不能防 XSS 窃取 Cookie。
2. **反向代理 + 真正的 HttpOnly Cookie 会话**：在 Streamlit 前面挂一层
   网关，由网关签发 `HttpOnly + Secure` Cookie，Streamlit 只信任网关
   转发的身份。安全性最高，但需要额外部署组件，和当前"单文件
   Streamlit 脚本、部署简单"的定位不符，对个人项目改造成本明显更高，
   未采用。
3. **去掉免登录持久化，只用 `st.session_state`**：彻底不存在 token
   泄露问题，但会重新挖出"刷新页面就被踢出登录"这个当初引入 URL
   token 就是为了解决的痛点，体验倒退，未采用。

## 已知限制

- Cookie 不是 HttpOnly，无法防御 XSS 窃取；这次改动解决的是"token
  出现在 URL 里"这一类泄露面，属于纵深防御的一层改进，不是终局方案。
  真正要做到 JS 也读不到 Cookie，需要走上面的方案 2。
- `secure`/`same_site` 目前用的是 `same_site="lax"`、未强制
  `secure=True`（兼容纯局域网 HTTP 部署场景）；如果部署在 HTTPS
  反向代理后面，建议自行把 `_cookie_set_auth()` 里的 `secure` 加固为
  `True`。
- 旧版本签发的 `?auth=...` 链接在升级后会失效（新代码不再读取
  `st.query_params.get("auth")`），用户下一次打开看板会看到登录页，
  重新登录一次即可，不需要任何手动迁移步骤。
