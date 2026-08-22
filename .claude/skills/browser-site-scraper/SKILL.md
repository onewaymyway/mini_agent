---
name: browser-site-scraper
skill_type: generative-capability
category_summary: 针对具体网站的网页抓取能力集合（百度/知乎/淘宝/京东等），未命中或已有能力失效时自动探索补全。
description: 针对具体网站的网页抓取能力集合（百度/知乎/淘宝/京东等）。不在主 context 中展开具体网站列表，按需通过 capability_call 工具检索并加载对应 member；未命中或已有 member 失效时触发探索自动补全并沉淀为新 member。
triggers: 抓取某个网站, 网站搜索, 网页数据提取, site scraper
platforms: windows, macos, linux, pc
---

# browser-site-scraper（generative-capability skill）

本 skill 不通过静态加载 references 的方式暴露内容，而是通过通用引擎
`mini_agent.skills.generative_capability.CapabilityEngine` 按需检索、执行、
探索与固化。agent 在对话中调用本 skill，走的是 `capability_call` 工具
（见 `src/mini_agent/tools/capability_call.py`），不需要（也不应该）自己
构造 `CapabilityEngine` 实例。

## 调用方式

- **agent 在对话中**：调用 `capability_call(skill_name="browser-site-scraper", request={...})` 工具。
- **代码/脚本中直接使用引擎**（如手动调试、health_patrol 巡检脚本）：

```python
from mini_agent.skills.generative_capability import CapabilityEngine

engine = CapabilityEngine("path/to/browser-site-scraper")
result = engine.call({
    "text": "帮我用百度搜一下 xxx",
    "target": {"url": "https://www.baidu.com/s?wd=xxx"},
    "query": "xxx",
})
```

`result.status` 为 `success` / `fail` / `not_implemented`（命中/未命中都需要
触发探索、但当前运行环境未接入真实 `explore_runner`/`tool_executor` 时的
如实反馈）。

## 依赖

- 底层通用浏览器操作能力: `browser-core`（独立静态 skill，契约见
  `.claude/skills/browser-core/SKILL.md`；阶段十四起提供真实实现，见
  `.claude/skills/browser-core/impl/`）。**`browser-core` 是本 skill
  现在唯一的浏览器操作依赖**——阶段十五起，`baidu`/`zhihu` 这两个已落盘的
  人工预置 member 已经从直接依赖 `browser-cdp/src/searchers/*` 改为直接
  调用 `browser-core/impl/session_manager.py`（提取逻辑本身未变，只是换了
  执行载体）；探索链路调用的 7 个工具名同样对应 `browser-core` 契约。
  `browser-cdp` 即将被移除，`browser-core` + `browser-site-scraper` 是它
  的替代方案（`browser-core` 提供通用浏览器操作原语，`browser-site-
  scraper` 提供"针对具体网站怎么用这些原语抓取"的 member 层），本 skill
  不应再有任何路径指向 `browser-cdp` 目录。

## 已知限制

- 探索能力（`explore()`/`distill()`）需要真实 `tool_executor` 接入才能在
  生产环境生效。`capability_call` 工具会注入
  `build_default_tool_executor(skill_dir=skill_dir)`（阶段十二起如此，
  阶段十四扩展为自动加载 `explorer.base_tools` 声明的静态 skill 自带
  实现），`browser-core` 下的 7 个工具名现在会真正执行（基于 CDP，见
  `browser-core/HEADLESS_BROWSER_INTEGRATION.md`），因此探索路径**理论上
  可以真正跑通**——但实际成功与否取决于运行环境：
  - 需要本机/服务器有可用的 Chrome/Chromium/Edge（`launch_headless`/
    `launch_headed` 模式），或者提前手动启动好一个带
    `--remote-debugging-port` 的浏览器（`attach` 模式，需要登录的网站
    应该用这种方式，由使用者手动登录好再交给探索子agent/member）；
  - `launch_headed` 模式默认使用一份持久化、跨进程复用的用户数据目录
    （见 `browser-core/SKILL.md`"会话模式"一节），第一次手动登录过某个
    网站之后，后续再用普通（有界面）浏览器打开会自动带着这份登录态，
    不需要每次都重新登录；
  - 沙盒/CI 等没有可用浏览器的环境下，探索/member 执行仍会诚实失败并如实
    返回 `not_implemented`/浏览器层面的具体错误，不会伪造成功——`baidu`
    member 额外做了"检测到验证码/风控拦截页时明确报错"的处理（而不是像
    改造前那样把空结果误判成"成功但没抓到东西"），`zhihu` member 同理
    检测登录墙；
  - `baidu`/`zhihu` 这两个人工预置 member 提取到 0 条结果、且没有命中已知
    反爬/登录墙关键词时（阶段十七起），不再静默返回"成功但空结果"，而是
    返回失败并在 `error` 里附带调试快照（url/title/正文摘要），提示更可能
    是选择器过期/页面结构变化/内容未渲染完成，而不是真的没有搜索结果；
  - `browser_extract_content` 是通用提取，不针对具体网站定制，复杂页面
    结构下探索子agent可能需要多轮 `wait_for_selector`/`click`/`scroll`
    才能拿到符合 `intent_schema` 的数据，见 `browser-core/SKILL.md`
    "已知限制"一节。`baidu`/`zhihu` 这两个人工预置 member 则是自己直接写
    了针对性的提取 JS（不经过通用的 `browser_extract_content`），所以不受
    这条限制影响，但同样适用上一条"0 条结果视为失败"的处理。

## 调试（阶段十六）

- 实际抓取失败时，`browser-core` 的失败返回会尽力附带 `debug` 字段
  （url/title/正文摘要），另外可以直接调用 `browser_get_page_source`/
  `browser_get_debug_snapshot` 两个新增的调试原语拿到更完整的页面 HTML/
  截图，见 `browser-core/SKILL.md`"工具契约"一节。
- 默认会话模式（`auto`）在连不上已有浏览器时，现在会退化为**有界面**
  浏览器（而不是无头），方便需要登录的网站直接在弹出的窗口里手动登录；
  纯后台场景可以在 `request.session.mode` 显式传 `"launch_headless"`。
- 本地调试单个 member/URL 不需要走完整探索循环，可以用
  `dev/debug_run.py`（开发期工具，不进 `_index.json` 检索）：
  ```
  python .claude/skills/browser-site-scraper/dev/debug_run.py \
    --member baidu \
    --request '{"target": {"url": "https://www.baidu.com/s?wd=test"}, "query": "test"}'
  ```
  失败时会打印带 `debug` 字段的错误详情；因为 `browser-core/impl/*.py`
  现在每次调用前都会清缓存重新加载（阶段十六"热更新"），编辑完脚本后
  直接重跑同一条命令即可看到最新代码的效果，不需要重启任何进程。

## 更多信息

引擎设计、状态机、各版本演进历史见
`next_doc/generative-capability-skill-plan.md`（本文件只描述"如何调用本
skill"，不重复记录实施过程）。
