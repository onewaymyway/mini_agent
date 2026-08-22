---
name: browser-core
description: browser-site-scraper 在 capability.yaml 中把本 skill 声明为 explorer.base_tools；本 skill 本身不由探索子agent直接读取正文（探索子agent只读取 explorer/tool_allowlist.json 里的工具名与签名），SKILL.md 的作用是给"实现/维护这些原语的人"一份权威契约文档，静态 skill（skill_type 留空，遵循项目既有约定），不参与 generative-capability 的检索流程。
triggers: browser-core, 浏览器操作原语, 无头浏览器接入, 普通浏览器登录
platforms: windows, macos, linux, pc
---

# browser-core（静态 skill：通用浏览器操作原语，基于 CDP，已有真实实现）

## 这是什么

`browser-core` 是 `next_doc/generative-capability-skill-plan.md` 第 10 节
"迁移路径"第 1 步里提到的静态 skill：把 `browser-site-scraper` 探索子agent
会用到的通用浏览器操作（导航/点击/输入/滚动/提取/等待/截图）从"每个网站
各自实现"里抽出来，定义成一份**跨网站通用**的工具契约，并提供真实实现。

**`browser-core` + `browser-site-scraper` 是 `browser-cdp` 的替代方案**
（阶段十五起明确）：`browser-cdp` 即将从项目中移除，`browser-core` 承接它
"打开浏览器、驱动浏览器完成操作"这部分通用能力，`browser-site-scraper` 的
各 member（如 `baidu`/`zhihu`）承接它"针对具体网站怎么抓"这部分定制逻辑。
`browser-site-scraper` 现在**只依赖 `browser-core`**，不应该再有任何代码
路径指向 `browser-cdp` 目录（阶段十五已经把 `baidu`/`zhihu` 两个 member
从直接依赖 `browser-cdp/src/searchers/*` 改为直接调用 `browser-core/impl/
session_manager.py`，提取逻辑照搬未改，只是换了执行载体）。

- ✅ 已完成（阶段十三）：本文档 —— 每个工具的输入/输出结构、错误约定。
- ✅ 已完成（阶段十三）：`HEADLESS_BROWSER_INTEGRATION.md` —— 接入设计
  记录（阶段十四已按此文档完成实现，该文档同步更新为"实现说明+验证记录"）。
- ✅ 已完成（**阶段十四**）：真实可执行的实现，位于 `impl/` 目录，采用
  与 `browser-cdp` 一致的技术路线——**不依赖 Playwright/Selenium，直接用
  Chrome DevTools Protocol（HTTP `/json/*` 做 tab 发现 + WebSocket 发送
  CDP 命令）**，见下方"实现与依赖"一节。
- ✅ 已完成（**阶段十四，本次改动的核心诉求**）：**不再局限于无头浏览器**。
  `impl/session_manager.py` 提供 `attach` / `launch_headless` /
  `launch_headed` / `auto` 四种会话模式——需要登录的网站可以先由使用者手动
  启动一个普通的、有界面的浏览器并手动登录好，再让 browser-core 以
  `attach` 模式连接这个已登录的浏览器实例，探索子agent不需要、也不会尝试
  自己完成登录（`explorer/prompt.md` 一直要求"遇到登录墙如实报告，不尝试
  绕过"，这一点没有变化——变化的是登录这件事从一开始就不在探索子agent的
  职责范围内，由人在探索开始之前就做好）。
- ✅ 已完成（**阶段十五**）：`launch_headed`（本 skill 自己拉起的普通/有
  界面浏览器）**默认使用同一个持久化的用户数据目录**（不是每次启动都是
  全新 profile），第一次手动登录过某个网站后，后续再打开会自动带着这份
  登录态，不需要反复登录——见下方"会话模式"一节。
- ✅ 已完成（**阶段十五**）：`browser-site-scraper` 的两个人工预置 member
  （`baidu`/`zhihu`）已从依赖 `browser-cdp` 改为依赖 `browser-core`。
- ⚠️ 未完成：真实浏览器环境下的端到端验证。当前沙箱环境没有可安装/运行的
  Chrome，因此 `impl/` 下的代码只验证到"CDP 层面的调用/错误处理逻辑正确"
  （见 `HEADLESS_BROWSER_INTEGRATION.md`"验证记录"一节），没有验证过"连上
  一个真实网页、真的点击/输入/提取成功"。这是诚实的状态说明，不是掩盖——
  下一个有真实浏览器环境的人使用前应该先跑一遍该文档给出的自测步骤。

## 实现与依赖（阶段十四）

`impl/` 目录下是本 skill 自己维护的一份精简 CDP 客户端 + 会话管理 + 7 个
工具实现，**不 import `browser-cdp` 的任何代码**，理由：

1. **两者的定位边界不同**。`browser-cdp` 里的实现是针对具体网站高度定制的
   （每个 `*_search.py` 都有自己的选择器、反爬处理），而 `browser-core`
   需要的是与网站无关的**通用原语**（"点击一个元素""等一个选择器出现"）。
   直接复用 `browser-cdp` 的模块会让 browser-core 变得没法脱离 browser-cdp
   独立存在，模糊了"通用原语 vs 网站定制脚本"这条本应清晰的边界。
2. **generative-capability member/impl 脚本是按文件路径动态加载的独立
   文件**（见 `capability_engine.py::_load_member_run()` 与
   `real_tools.py::load_skill_local_tool_implementations()` 的实现），
   不是这个仓库 Python 包的一部分——`browser-core` 整个 `.claude/skills/
   browser-core/` 目录理论上应该能被原样复制到别的项目里独立使用，依赖同
   仓库另一个 skill 目录的内部实现细节会破坏这一点。
3. 技术路线与 `browser-cdp/src/core/cdp_client.py` 保持一致（HTTP `/json/*`
   tab 发现 + WebSocket 发 CDP 命令，同步阻塞风格），但只保留 7 个原语真正
   用得到的能力（`Page.navigate`/`Runtime.evaluate`/`Page.captureScreenshot`），
   去掉了 `browser-cdp` 里面向"几十个网站定制抓取"的重试分类/事件订阅/
   cookie 管理等重型基础设施——那些属于 `browser-cdp` 自己的职责范围。

**依赖**：`requests`（项目 `requirements.txt` 已包含）、`websocket-client`
（`browser-cdp` 已经依赖同一个库，但 `browser-core` 作为独立静态 skill 不
共享 Python 依赖环境假设，需要在运行环境里单独确认/安装：
`pip install websocket-client --break-system-packages`）。可选依赖
`Pillow`：装了才会在 `browser_screenshot_annotated` 的截图上画出元素编号框，
不装也能正常工作，只是拿到的是未标注的原图 + 一份元素坐标列表（见
`impl/browser_core_impl.py::_try_annotate()`）。

**代码组织**（`skill具体功能代码留在skill目录`这条原则的落地）：

```
.claude/skills/browser-core/impl/
  cdp_client.py          # 极简 CDP 客户端（tab 发现/连接/发命令）
  browser_launch.py       # 拉起 headless 或 headed 的 Chrome/Chromium 进程
  session_manager.py      # attach/launch_headless/launch_headed/auto 会话管理；
                            # 阶段十八新增 list_sessions()/close_session()/
                            # close_all_sessions()/detect_headless_hint()
  browser_core_impl.py    # 工具的真实实现（对应下方"工具契约"）
  tools_impl.py            # 导出 TOOL_IMPLEMENTATIONS，供项目侧通用引擎
                            # （real_tools.py::load_skill_local_tool_
                            # implementations，纯粹的"按约定路径动态加载"
                            # 机制，不含任何浏览器/CDP 相关代码）发现
.claude/skills/browser-core/manage.py
                            # 阶段十八新增：独立命令行工具，直接列举/关闭
                            # 调试浏览器会话，不经过 capability_call/探索
                            # 子agent，见下方 browser_list_sessions/
                            # browser_close_session 工具契约一节末尾用法
```

项目代码（`src/mini_agent/skills/generative_capability/real_tools.py`）
完全不知道、也不需要知道 browser-core 内部是用 CDP 还是别的什么协议实现的，
只认 `<skill_dir>/impl/tools_impl.py` 这一个约定路径和 `TOOL_IMPLEMENTATIONS`
这一个约定变量名。

## 会话模式：`attach` / `launch_headless` / `launch_headed` / `auto`

每个工具的 `input` 都可以附带一个可选的 `session` 字段（缺省为 `auto`），
第一次调用时决定如何建立浏览器连接，同一次探索过程中后续调用复用同一个
会话（不会每次都重新连接/重新启动浏览器）：

```
"session": {
  "mode": "attach" | "launch_headless" | "launch_headed" | "auto",
  "host": "127.0.0.1",     # 可选，默认 127.0.0.1
  "port": 9222,             # 可选，默认 9222
  "user_data_dir": null,    # 可选；launch_headed 默认使用持久化目录，见下方
}
```

- **`attach`**——连接一个**已经在运行**的浏览器（通过其
  `--remote-debugging-port`）。**这是需要登录场景的标准用法**：由使用者
  提前手动启动一个普通的、有界面的浏览器，手动登录好目标网站，再把调试
  端口告诉 browser-core。探索子agent不会、也不应该尝试自己完成登录。
- **`launch_headless`**——本 skill 自己拉起一个无 GUI 浏览器实例，适合
  不需要登录的纯抓取场景；`user_data_dir` 缺省时使用按端口区分的临时
  目录（不强行持久化——大多数 headless 抓取是无状态的一次性任务）。
- **`launch_headed`**——本 skill 自己拉起一个有 GUI 窗口的浏览器实例。
  **`user_data_dir` 缺省时默认使用一个固定的、跨进程/跨端口持久化的目录**
  （`~/.mini_agent/browser-core/profile`，可用环境变量
  `BROWSER_CORE_PROFILE_DIR` 整体覆盖），这是本次改动新增的行为：第一次
  打开这个浏览器手动登录过某个网站后，后续任何一次 `launch_headed`（哪怕
  是全新的 agent 进程、全新的探索）都会自动带着这份登录态，不需要每次都
  重新登录。如果需要多个互不干扰的登录身份，显式传一个不同的
  `session.user_data_dir` 即可覆盖这个默认值。
- **`auto`**（默认）——先尝试连接默认端口，能连上就复用（可能已经是使用者
  准备好、已登录的浏览器）；连不上则退化为启动一个新的**有界面（headed）**
  实例（阶段十六起，此前退化为 headless），并同样使用上面提到的持久化
  `user_data_dir`——默认打开一个看得见的普通浏览器窗口，方便在需要登录的
  场景里直接手动登录，登录一次后续调用都会带着登录态。纯后台抓取场景可以
  显式传 `session.mode = "launch_headless"` 跳过界面。

会话管理的完整实现见 `impl/session_manager.py`，其文件头有更详细的设计
说明。

`browser-site-scraper` 的人工预置 member（`baidu`/`zhihu`）也是通过同一个
`session_manager.get_or_create_session(input.get("session"))` 建立会话
的——调用方（真实用户/agent）可以在 `capability_call` 的 `request` 里带上
`"session": {"mode": "attach", "port": ...}`，让 member 直接复用一个已经
手动登录好的浏览器，见各 member 的 `script.py` 文件头说明。

## 工具契约（探索子agent可调用的全部原语）

以下工具名与 `browser-site-scraper/explorer/tool_allowlist.json` 中
`allowed_tools` 完全一致，是探索子agent（`explorer_runtime.build_llm_explorer`）
真正能看到、能调用的工具集合：7 个操作/提取类原语 + 阶段十六新增的 2 个
调试原语（`browser_get_page_source`/`browser_get_debug_snapshot`）+ 阶段
十八新增的 2 个会话管理原语（`browser_list_sessions`/`browser_close_
session`）。每个工具的 `input`/`output` 结构是**契约**——无论未来用
Playwright、Selenium 还是别的什么驱动真实浏览器，
`tool_executor(tool_name, tool_input) -> dict` 的行为都必须符合这份契约，
`capability_engine.py`/`distiller.py`/探索子agent的 prompt 才不需要跟着改。

每个工具的 `input` 都可以额外带一个可选的 `session` 字段（见上一节"会话
模式"），下面各工具的 `input` 示例省略了这个字段，实际调用时按需附加。

### `browser_navigate`

打开一个 URL 并等待页面基本加载完成。

```
input:  {"url": "https://example.com/search?q=xxx"}
output: {"ok": true, "final_url": "...", "title": "..."}
      | {"ok": false, "error": "导航失败的具体原因（超时/DNS失败/证书错误等）"}
```

### `browser_click`

点击页面上的一个元素。

```
input:  {"selector": "css选择器或语义化描述", "index": 0}
        # index 可选，多个匹配元素时选第几个，默认 0
output: {"ok": true} | {"ok": false, "error": "元素未找到/不可点击/被遮挡等"}
```

### `browser_type`

向一个输入框输入文本（不含回车，回车是否触发由 `submit` 参数控制）。

```
input:  {"selector": "...", "text": "...", "submit": false}
output: {"ok": true} | {"ok": false, "error": "..."}
```

### `browser_scroll`

滚动页面或某个可滚动容器，用于触发懒加载/无限滚动。

```
input:  {"selector": null, "direction": "down", "amount": "page"}
        # selector 为 null 表示滚动整个页面；amount 支持 "page"（一屏）
        # 或具体像素数
output: {"ok": true, "reached_bottom": false}
```

### `browser_wait_for_selector`

等待某个选择器出现/消失，用于处理异步渲染内容。

```
input:  {"selector": "...", "state": "visible", "timeout_ms": 8000}
        # state: visible | hidden | attached | detached
output: {"ok": true} | {"ok": false, "error": "超时未等到目标状态"}
```

### `browser_extract_content`

从当前页面提取结构化数据——这是 `browser-site-scraper` 探索链路里最关键
的一步：**最后一次调用本工具的返回值通常直接构成蒸馏产物的 `data` 字段**
（见 `distiller.py` 关于"重放最后一步工具输出"的既有约定，以及
`capability.yaml` 里 `distill.trust_trace_data` 兜底开关的适用场景）。

```
input:  {"schema_hint": {...}, "selector": null}
        # schema_hint 通常直接传 intent_schema，帮助提取逻辑知道要什么结构；
        # selector 可选，限定只从某个容器内提取
output: {"ok": true, "data": {
            "results": [...],       # 优先用容器内 <a href> 链接列表；没有
                                     # 链接时退化为 h1-h3 标题列表
            "headings": [...],       # 容器内 h1-h3 纯文本，最多 50 条
            "text_excerpt": "...",   # 容器纯文本前 4000 字符，兜底信息
            "url": "...", "title": "...",
         }}
      | {"ok": false, "error": "选择器未匹配到容器/页面异常等"}
```

**实现说明**（阶段十四）：`browser_extract_content` 的真实实现是**通用**
的（不针对任何具体网站定制选择器，那是 `browser-site-scraper` 各 member
的职责）——策略是收集容器内的标题层级元素与链接作为"结果条目"候选，附带
纯文本兜底。这不保证适配所有网站结构，是探索子agent"先看一眼页面有什么"
的合理起点，不是精确的语义提取；如果这份通用提取拿到的数据无法通过
`intent_schema` 校验，探索子agent应该组合 `wait_for_selector`/`click`/
`scroll` 之后再调用本工具，而不是指望它自动理解页面的业务含义。

### `browser_screenshot_annotated`

截图并标注可交互元素编号，供探索子agent在纯 DOM 信息不够用时"看"页面。

```
input:  {"full_page": false}
output: {"ok": true, "image_ref": "供后续多模态消息引用的图片句柄/路径",
         "elements": [{"index": 1, "selector": "...", "role": "button", "text": "..."}]}
      | {"ok": false, "error": "截图失败原因"}
```

### `browser_get_page_source`（阶段十六新增，调试用）

返回当前页面的 HTML 源码，用于排查"选择器为什么找不到元素"——页面结构和
预期不一样、内容是异步渲染的、命中了验证码/登录墙页面等。

```
input:  {"selector": null, "max_length": 20000}
        # selector 可选，限定只取某个容器的 outerHTML；max_length 控制截断长度
output: {"ok": true, "html": "...", "truncated": false, "full_length": 12345}
      | {"ok": false, "error": "..."}
```

### `browser_get_debug_snapshot`（阶段十六新增，调试用）

一次性打包排查失败所需的素材——url/title/正文摘要/HTML 摘要/截图文件
路径，供探索子agent或人工调试脚本在"某一步失败了但不确定为什么"时一次性
拿全上下文，不用再逐个单独调用 `browser_get_page_source`/
`browser_screenshot_annotated`。

```
input:  {}
output: {"ok": true, "url": "...", "title": "...", "body_excerpt": "...",
         "html_excerpt": "...", "screenshot_path": "/tmp/..."}
        # 截图本身失败（如浏览器已断开）不影响其余字段，改为附带
        # "screenshot_error" 字段说明原因，其余调试信息仍会尽力返回
      | {"ok": false, "error": "..."}
```

### `browser_list_sessions`（阶段十八新增，调试用）

列出当前已建立的调试浏览器会话，附带尽力而为的有头/无头判断。用于排查
"这次调用到底连的是哪个浏览器、是不是我以为的那个有界面窗口"。

```
input:  {"probe": [{"host": "127.0.0.1", "port": 9333}]}
        # probe 可选，额外探测尚未建立会话的端口是否有浏览器在监听（只报告
        # alive，不做有头/无头判断，因为没有 CDPSession 就测不了）；不传时
        # 如果已知会话里没有 9222 端口，会自动补一条对 9222 的探测
output: {"ok": true,
         "sessions": [{"host": "...", "port": 9222, "mode": "launch_headed",
                        "alive": true, "spawned_by_us": true, "pid": 12345,
                        "headless": false, "headless_confidence": "certain"}],
         "probed": [{"host": "...", "port": 9333, "alive": false}]}
```

`headless_confidence` 取值：`certain`（本 skill 自己拉起的，确定知道）、
`high`/`medium`（attach 到的会话，靠启发式信号猜测，见
`session_manager.py::detect_headless_hint()`）、`unknown`（两个信号都拿不到）。

**已知限制**：只能看到"本次 `capability_call` 调用内已经建立过的会话"，
看不到上一次调用遗留的浏览器进程（`real_tools.py` 的热更新机制每次都会
清空 `session_manager.py` 的会话记录，见下方"已知限制"一节）——这正是本
工具默认会额外探测标准端口 9222 的原因。

### `browser_close_session`（阶段十八新增，调试用）

关闭一个或全部调试浏览器会话，用于清理"怀疑是遗留的旧浏览器（尤其是
无头的）"。

```
input:  {"host": "127.0.0.1", "port": 9222, "kill_process": true}
        # 或 {"all": true, "kill_process": true} 关闭全部已知会话
output: {"ok": true, "closed_our_session": true, "killed_process": true,
         "pid": 12345, "host": "...", "port": 9222}
      | {"ok": true, "closed_our_session": false, "killed_process": false,
         "pid": null, "note": "本进程没有该 host:port 的会话记录，..."}
```

`kill_process=true`（默认）只对本 skill 自己 `spawn_browser` 拉起的会话
生效——`attach` 到的、使用者自己启动的浏览器永远不会被杀掉。没有会话记录
时（常见于热更新清空了记录）会在 `note` 里给出系统层面手动关闭的具体命令。

如果不想经过探索子agent/`capability_call`，也可以直接用独立命令行工具
`python .claude/skills/browser-core/manage.py list` /
`manage.py close --port 9222` /
`manage.py close --all`（不消耗探索预算，也不依赖 `Agent.llm_helper`，见
该脚本文件头说明；关闭时如果发现没有会话记录但端口确实存活，会比工具
原语多做一步——先临时 `attach` 建立记录再关闭）。



除了上面两个专门的调试工具，`browser_navigate`/`browser_click`/
`browser_type`/`browser_scroll`/`browser_wait_for_selector`/
`browser_extract_content`/`browser_screenshot_annotated` 这 7 个原有工具
在失败时，只要当时的浏览器会话还能响应，就会尽力在返回里附带一个可选的
`debug` 字段（`{"url": ..., "title": ..., "body_excerpt": ...}`），不需要
额外调用调试工具就能看到失败当下页面大致是什么状态；取调试信息本身失败
（如页面已经导航走/连接已断）时会安静省略这个字段，不会让"取调试信息"
这件事本身又制造一层新的异常掩盖原始错误。

## 反爬/登录墙的处理原则（沿用 explorer/prompt.md 的既有约束）

`browser-site-scraper/explorer/prompt.md` 已明确要求探索子agent"遇到
验证码/登录墙/明显反爬拦截时，如实报告拦截类型，不尝试绕过认证机制"。
本契约不新增任何"反检测"类工具（不提供 `browser_bypass_captcha` 之类的
原语），这是刻意的：`browser-core` 的职责边界是"通用浏览器操作"，不是
"绕过网站的访问控制"，与方案文档第 8 节"安全与成本边界"的精神一致。

## 已知限制

- **未在真实浏览器环境下验证过端到端行为**：当前沙箱没有可安装/运行的
  Chrome，`impl/` 下的代码只验证到"CDP 调用/错误处理逻辑本身没有低级
  bug"（见 `HEADLESS_BROWSER_INTEGRATION.md`"验证记录"一节：用一个不存在
  的调试端口触发 `attach` 模式的诚实失败、确认没有安装 Chrome 时
  `launch_*` 模式的诚实失败、确认 `real_tools.py` 的动态加载机制能正确
  找到并调用这些实现）。真正连上一个网页、真的点击/输入/提取成功，需要在
  有真实浏览器的环境下补一轮验证，见该文档给出的自测步骤。
- `browser_extract_content` 是通用提取（见上文"实现说明"），不针对具体
  网站定制，复杂页面结构下可能拿不到符合 `intent_schema` 的数据，这是预期
  行为而非 bug——`browser-site-scraper` 的探索子agent应该组合其他工具后
  再提取，而不是依赖它"智能理解"页面。
- `browser_screenshot_annotated` 的可视化标注依赖可选的 `Pillow`；未安装
  时仍会返回截图路径与元素坐标列表，只是没有画框（见 `_try_annotate()`）。
- `session_manager.py` 的会话是**进程级**的（模块级字典），不是"探索
  子agent专属"的——同一进程内两次并发的 `capability_call` 如果用相同的
  `(host, port)`，会意外共享同一个浏览器会话/tab。当前 `capability_call`
  是同步调用、单次探索内顺序执行，暂不构成实际问题；如果未来支持并发探索，
  需要在 `session_manager.py` 里补充按调用方隔离的 session key，这是刻意
  留给后续阶段的已知限制，不在本次范围内处理。
- **会话记录看不到跨调用的历史（阶段十八，已部分缓解）**：`real_tools.py`
  为了支持热更新，每次 `capability_call` 顶层调用都会清空 `session_
  manager.py` 的模块缓存重新加载一次，`_sessions` 字典因此每次调用都从空
  开始——`browser_list_sessions`/`manage.py list` 看到的只是"这一次调用
  内已经建立过的会话"，看不到上一次调用遗留的浏览器进程（哪怕它还在跑）。
  这也是"auto 模式复用了旧的、可能是无头的会话，却看不到新窗口弹出"这类
  疑惑的根源——`auto` 会照常按端口探测存活并复用，只是这次调用"不记得"
  那是谁启动的。阶段十八用"`browser_list_sessions` 默认额外探测标准端口
  9222"这个变通办法缓解了可见性问题，但没有解决"记录不跨调用持久化"这个
  根本限制；如果需要真正跨调用/跨进程的会话 registry，需要把会话元信息
  落盘而不是只放在内存字典里，留给后续阶段按需处理。
- `capability_call.py` 返回 `status: not_implemented` 时，`note` 字段
  阶段十八之前是一句不区分原因的固定文案（"未接入执行器"），容易被误读成
  "browser-core 没接线"；阶段十八已改为据实检测（该 skill 声明的工具是否
  真的都有实现），不再是已知限制，这里记录一下避免后续又被同样的误读困扰。
- **调试上下文可被 member 复用（阶段十七）**：`_debug_context()` 背后的
  实现已改为公开函数 `capture_debug_context(session)`（`_debug_context`
  仍保留、内部改为直接调用它，向后兼容）——`browser-site-scraper` 下不
  经过 `tool_executor` 通用分发层的人工预置 member（`baidu`/`zhihu`）可以
  `from browser_core_impl import capture_debug_context` 直接复用同一份
  调试快照逻辑，不需要重复实现，见 `browser-site-scraper/SKILL.md`"已知
  限制"一节里"0 条结果视为失败"的用法。`real_tools.py` 现在每次调用前会
  清掉 `impl/` 目录下所有文件的模块缓存再重新加载，所以修改
  `browser_core_impl.py`/`session_manager.py`/`cdp_client.py`/
  `browser_launch.py` 后，下一次 `capability_call`（或 `dev/debug_run.py`）
  执行的就是最新代码，不需要重启 agent 进程。副作用是
  `session_manager.py` 模块级的 `_sessions` 复用字典也会跟着清空重建——
  上一次调用里已连接的浏览器 tab 不会跨调用保留在 Python 层，但浏览器
  进程本身、以及它已登录的 cookies/profile 不受影响，下一次调用会重新
  attach 到同一个浏览器进程（可能是不同 tab，会重新导航），登录态不会
  丢失。见 `browser-site-scraper/dev/debug_run.py` 给出的调试循环用法。
- `browser-site-scraper` 依赖本契约，其探索路径现在**理论上可以真正工作**
  （不再是必然 `not_implemented`），但实际成功与否取决于运行环境是否有
  可用浏览器/合适的调试端口，见 `browser-site-scraper/SKILL.md`"已知
  限制"一节的最新描述。
- **`browser-cdp` 尚未真正移除**：本次改动（阶段十五）已经把
  `browser-site-scraper` 的所有代码路径改为只依赖 `browser-core`，但
  `.claude/skills/browser-cdp/` 目录本身还在仓库里，删除它是一个独立的、
  需要单独确认"没有其他 skill/脚本还在用它"的操作，不在本次范围内——本次
  只保证"browser-site-scraper 不再需要它存在"，真正物理删除留给下一个
  确认过全仓库范围内没有遗留引用的阶段执行。
- `launch_headed` 默认持久化的 profile 目录（`~/.mini_agent/browser-core/
  profile`）目前没有任何自动清理/大小限制机制——长期使用会不断积累浏览器
  缓存/历史等数据，这是刻意的（持久化的核心诉求就是"不清空"），但意味着
  需要使用者自己按需清理该目录，本 skill 不会主动做这件事。
