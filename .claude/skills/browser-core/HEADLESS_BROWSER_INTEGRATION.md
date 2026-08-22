# browser-core 浏览器接入实现记录（阶段十四）

面向对象：使用/维护 `browser-core` 真实实现的人，以及下一个要在**真实
浏览器环境**里验证/扩展它的人。

本文档的角色发生了变化：阶段十三时这里写的是"怎么接、接哪里、有哪些坑"
的**接入指南**（尚无代码）；阶段十四已经按这份指南把代码写出来了
（`.claude/skills/browser-core/impl/`），所以本文档现在是**实现说明 +
验证记录**，同时保留了原指南里仍然适用的坑/设计取舍，供后续维护参考。

沙盒环境（完成本次改动的环境）仍然没有可安装/运行的 Chrome，因此本文档的
"验证记录"一节只覆盖了"CDP 调用/错误处理逻辑本身没有低级 bug、动态加载
机制接线正确"这一层，**没有**覆盖"连上一个真实网页、真的点击/输入/提取
成功"——这是诚实的边界，不是遗漏，下一个有真实浏览器环境的人应该先跑一遍
本文档"给未来验证者的自测步骤"一节，再放心使用。

## 0. 最终没有直接复用 browser-cdp 代码，原因回顾

阶段十三/本文档最初版本设想"接入时应该优先复用 `browser-cdp` 已有的
`cdp_client.py`/`browser_manager.py`/`browser_nav.py` 等模块"。实际实现
时改为**自己写一份精简版**，原因见 `SKILL.md`"实现与依赖"一节：两者的
定位边界不同（通用原语 vs 网站定制脚本），且 generative-capability 的
impl 脚本是按路径独立加载的文件，不应该依赖同仓库另一个 skill 目录的
内部实现细节。这不是"没有按指南做"，是指南本身在真正动手实现时发现的一处
需要修正的判断，如实记录在这里，而不是悄悄改了指南却不说明为什么。

## 1. 代码组织（对应 SKILL.md"实现与依赖"一节）

```
.claude/skills/browser-core/impl/
  cdp_client.py       # 极简 CDP 客户端：tab 发现（HTTP /json/*）、
                        # WebSocket 发命令、Page.navigate/Runtime.evaluate/
                        # Page.captureScreenshot 三个 CDP 方法的薄封装
  browser_launch.py    # spawn_browser(headless: bool)：拉起一个带
                        # --remote-debugging-port 的 Chrome/Chromium 进程，
                        # headless 与 headed 走同一份代码、只是参数不同
  session_manager.py   # 会话管理：attach/launch_headless/launch_headed/auto
                        # 四种模式；模块级字典按 (host, port) 复用会话，
                        # atexit 注册清理（只清理自己 spawn 的进程，不动
                        # attach 模式连接的、使用者自己启动的浏览器）
  browser_core_impl.py  # 7 个工具的真实实现，函数签名统一为
                        # (tool_input: dict) -> dict，任何异常都在函数内部
                        # 捕获转成 {"ok": False, "error": "..."}，不向上抛
  tools_impl.py          # 导出 TOOL_IMPLEMENTATIONS: dict[str, Callable]
```

`tools_impl.py` 是本 skill 与项目侧通用引擎之间**唯一**的接口——项目代码
（`real_tools.py::load_skill_local_tool_implementations()`）只认
`<skill_dir>/impl/tools_impl.py` 这个约定路径和 `TOOL_IMPLEMENTATIONS`
这个约定变量名，对 `impl/` 目录下其余文件的存在、命名、内部结构一无所知，
也不需要知道——这就是"skill 具体功能代码留在 skill 目录，项目代码只保留
通用机制"这条原则在本次改动里的落地方式（对应任务要求）。

## 2. 会话生命周期：怎么解决的（对应原指南第 2 节的三个问题）

- **每次探索调用是否复用同一个浏览器进程？** 是。`session_manager.py` 用
  模块级字典 `_sessions: dict[(host, port), _SessionEntry]`，
  `get_or_create_session()` 对同一个 `(host, port)` 只建立一次连接/只
  启动一次浏览器进程，惰性初始化——命中已有 member 执行成功、根本不需要
  浏览器的请求不会触发任何浏览器启动。
- **一次探索结束后要不要关闭？** 用 `atexit.register(_cleanup_all)` 在
  进程退出时统一清理，而不是在每次 `capability_call` 结束后立刻关闭——
  这样同一个长期运行的 agent 进程内多次探索/多次命中同一 member 执行可以
  持续复用同一个浏览器会话（含 launch 模式下积累的 cookies/登录状态），
  避免"探索时登录了，紧接着免探索复用那次又要重新连接一个全新浏览器"的
  体验割裂。代价是长时间运行的 agent 进程如果探索了很多不同网站，浏览器
  子进程会逐渐积累；`reset_session()` 提供了显式清理单个会话的入口，供
  未来需要主动管理生命周期的调用方使用（当前 `capability_call.py` 未调用
  它，属于刻意保守的默认行为，不在本次范围内接线）。
- **并发探索怎么办？** 当前引擎设计里探索是同步阻塞的单次调用，
  `capability_call` 也是同步工具调用，暂不构成并发问题。但
  `session_manager.py` 的会话字典是**进程级**的，如果宿主 agent 框架
  未来支持并发跑多个 `capability_call`，两次并发请求用相同的
  `(host, port)` 会意外共享同一个浏览器 tab——这是已知限制，已记录在
  `SKILL.md`"已知限制"一节，留给后续阶段视实际需要处理（比如按调用方/
  探索会话 id 隔离 session key）。

## 3. `browser_extract_content` 的实现取舍

沿用原指南强调的一点：探索链路的蒸馏产物很大程度上依赖"最后一次工具调用
的返回值里直接带 `data`"这条既有约定。真实实现选择了**通用、非定制化**的
提取策略（收集容器内的标题/链接元素 + 纯文本兜底，见 `SKILL.md`"实现
说明"）而不是尝试针对 `schema_hint` 做"智能"的语义匹配——原因：
`browser-core` 的职责边界是"通用浏览器操作"，"理解某个网站的内容语义
结构"属于 `browser-site-scraper` 各 member（人工预置或蒸馏生成）的职责。
这意味着复杂页面结构下，探索子agent可能需要先用 `wait_for_selector`/
`click` 缩小范围、再调用 `browser_extract_content`（可传 `selector`
限定容器），而不是指望一次调用就拿到语义正确的数据——这是刻意的设计取舍，
不是待办事项。

## 4. 反检测与验证码：仍然明确不做

与原指南结论一致，本次实现**没有**加入任何"绕过反爬/验证码"的逻辑。
`browser_navigate`/`browser_click` 等函数遇到网络/DOM 层面的失败会如实
返回 `{"ok": False, "error": "..."}`，交给探索子agent按 `explorer/
prompt.md` 的既有要求判断是否 `report_failure`；`browser-core` 本身不
识别"这是不是验证码页面"，也不提供任何专门的绕过工具。

## 5. 验证记录（本次沙盒环境实际跑过的部分）

沙盒没有可用的 Chrome/CDP 端口，以下是在这个约束下能做、且已经做过的
验证（可复现，见 `tests/test_generative_capability_real_tools.py::
TestSkillLocalToolImplementationLoading`）：

1. **动态加载接线正确**：`build_default_tool_executor(skill_dir=
   Path(".claude/skills/browser-site-scraper"))` 能正确找到并加载
   `browser-core/impl/tools_impl.py`，7 个工具名全部出现在分发表里，
   项目内置的 `text_transform_apply` 不受影响（叠加而非替换）。
2. **`attach` 模式的诚实失败**：对一个确定没有监听的调试端口
   （`session: {"mode": "attach", "port": 19222}`）调用
   `browser_navigate`，返回 `{"ok": False, "error": "...没有可连接的
   浏览器调试端口。请先手动启动一个带 --remote-debugging-port=19222 的
   浏览器（如果这个抓取目标需要登录，应该在这一步手动登录好），再重试。"}`
   ——不是笼统的"未实现"，是具体到"该怎么解决"的错误信息。
3. **`auto`/`launch_*` 模式在无 Chrome 环境下的诚实失败**：沙盒没有安装
   任何 Chrome/Chromium/Edge，`browser_launch._find_chrome_binary()`
   正确返回 `None`，`browser_navigate`/`browser_click` 等工具返回
   `{"ok": False, "error": "未找到可用的 Chrome/Chromium/Edge 可执行
   文件。请安装浏览器，或改用 attach 模式..."}`，没有抛出未捕获异常。
4. **未命中工具名仍走通用占位提示**：对一个不存在的工具名调用，仍然得到
   项目侧 `real_tools.py` 里既有的"占位声明，尚未接入真实执行器"提示，
   证明 browser-core 的加入没有破坏对其余未实现领域（如 `doc-core`）的
   既有诚实失败行为。
5. **既有回归测试全部通过**：`tests/test_generative_capability_engine.py`
   + `tests/test_generative_capability_real_tools.py` 共 29 个用例全部
   通过，确认本次改动没有影响此前 `text-transform-capability`/
   `doc-template-generation`/引擎骨架本身的行为。

## 6. 给未来验证者的自测步骤（在有真实浏览器的环境下）

1. 安装依赖：`pip install websocket-client --break-system-packages`
   （`requests`/`PyYAML` 项目本身已依赖）；确认本机有 Chrome/Chromium。
2. **最短链路自测**（headless，不需要登录）：
   ```python
   import sys; sys.path.insert(0, "src")
   from pathlib import Path
   from mini_agent.skills.generative_capability.real_tools import build_default_tool_executor

   executor = build_default_tool_executor(
       skill_dir=Path(".claude/skills/browser-site-scraper")
   )
   print(executor("browser_navigate", {"url": "https://example.com"}))
   print(executor("browser_extract_content", {}))
   ```
   预期：`browser_navigate` 返回 `ok: true` 且 `final_url`/`title` 正确；
   `browser_extract_content` 返回 `ok: true` 且 `data.text_excerpt` 里能
   看到 example.com 页面的文本内容。
3. **`attach` 模式自测（登录场景的核心验证）**：手动执行
   `google-chrome --remote-debugging-port=9333 --user-data-dir=/tmp/bc-test`
   打开一个有界面的 Chrome，手动导航/登录任意网站；再调用
   `executor("browser_extract_content", {"session": {"mode": "attach",
   "port": 9333}})`，确认能从这个已登录的会话里正确提取内容——这一步
   验证的正是本次改动的核心诉求。
4. **完整探索闭环自测**：参照 `test_cases/text-transform-capability-
   testing-guide.md` 的结构（真实 `LLMHelper` + 真实 `capability_call`
   工具），对 `browser-site-scraper` 发起一个三个已有 member（baidu/
   zhihu）都覆盖不到的新站点抓取请求，观察探索子agent是否能通过
   `browser_navigate` -> `browser_wait_for_selector` ->
   `browser_extract_content` 的组合拿到满足 `intent_schema` 的数据、
   蒸馏落盘、并在下一次相同请求时免探索复用。
5. 验证过程中如果发现某个工具的真实行为与 `SKILL.md` 契约描述有出入
   （大概率会有，见"已知限制"），应该**更新契约文档**而不是让实现和
   文档各说各话，并把验证结果补充进本文档的"验证记录"一节。

## 7. 完成后需要同步更新的文档（本次已完成的部分标 ✅）

- ✅ `browser-core/SKILL.md`"已知限制"一节——已更新为"理论上可以工作，
  实际取决于运行环境"的准确描述。
- ✅ `browser-site-scraper/explorer/tool_allowlist.json` 的 `note` 字段。
- ✅ `browser-site-scraper/SKILL.md`"已知限制"一节。
- ✅ `next_doc/generative-capability-skill-plan.md`——已新增阶段十四记录。
- ⬜ 真实浏览器环境下的端到端验证结果——留给下一个有条件的人补充进本文档
  第 5 节，不在本次范围内（本次范围内的沙盒环境没有这个条件）。
