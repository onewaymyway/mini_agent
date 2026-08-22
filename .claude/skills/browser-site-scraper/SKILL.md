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

- 底层通用浏览器操作能力: `browser-core`（阶段十三已作为独立静态 skill
  落地契约文档，见 `.claude/skills/browser-core/SKILL.md`；当前 member
  仍直接复用 `browser-cdp/src/searchers/*`，探索链路调用的 7 个工具名
  与 `browser-core` 契约一一对应，但尚无真实执行器）。

## 已知限制

- 探索能力（`explore()`/`distill()`）需要真实 `tool_executor` 接入才能在
  生产环境生效。`capability_call` 工具**会**注入
  `build_default_tool_executor()`（阶段十二起如此，与
  `text-transform-capability` 共用同一套注入逻辑），但该执行器对
  `browser-core` 下的 7 个工具名仍如实返回"占位声明，未接入真实执行器"
  （见 `real_tools.py`），因此命中失败/未命中最终仍会得到
  `not_implemented`，不会伪造成功。要让探索路径在本 skill 上真正跑通，
  需要先按 `.claude/skills/browser-core/HEADLESS_BROWSER_INTEGRATION.md`
  接入一个真实无头浏览器实现——这项工作量和风险显著大于
  `text-transform-capability` 对应的 `text-core`（纯字符串操作），不在
  阶段十三范围内。

## 更多信息

引擎设计、状态机、各版本演进历史见
`next_doc/generative-capability-skill-plan.md`（本文件只描述"如何调用本
skill"，不重复记录实施过程）。
