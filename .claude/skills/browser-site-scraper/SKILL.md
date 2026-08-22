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
  `.claude/skills/browser-core/SKILL.md`；阶段十四起已提供真实实现，见
  `.claude/skills/browser-core/impl/`。当前 3 个已落盘的 member 仍直接
  复用 `browser-cdp/src/searchers/*`，是各自蒸馏时生成的定制脚本；探索
  链路调用的 7 个工具名与 `browser-core` 契约一一对应，现在会真正执行）。

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
    应该用这种方式，由使用者手动登录好再交给探索子agent）；
  - 沙盒/CI 等没有可用浏览器的环境下，探索仍会诚实失败并如实返回
    `not_implemented`/浏览器层面的具体错误，不会伪造成功；
  - `browser_extract_content` 是通用提取，不针对具体网站定制，复杂页面
    结构下探索子agent可能需要多轮 `wait_for_selector`/`click`/`scroll`
    才能拿到符合 `intent_schema` 的数据，见 `browser-core/SKILL.md`
    "已知限制"一节。

## 更多信息

引擎设计、状态机、各版本演进历史见
`next_doc/generative-capability-skill-plan.md`（本文件只描述"如何调用本
skill"，不重复记录实施过程）。
