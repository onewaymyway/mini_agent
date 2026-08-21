---
name: browser-site-scraper
skill_type: generative-capability
description: 针对具体网站的网页抓取能力集合（百度/知乎/淘宝/京东等）。不在主 context 中展开具体网站列表，按需通过 capability_engine 检索并加载对应 member；未命中或已有 member 失效时触发探索（阶段三接入）自动补全并沉淀为新 member。
triggers: 抓取某个网站, 网站搜索, 网页数据提取, site scraper
platforms: windows, macos, linux, pc
---

# browser-site-scraper（generative-capability skill）

本 skill 不通过静态加载 references 的方式暴露内容，而是通过通用引擎
`.claude/skills/_engine/capability_engine.py` 按需检索、执行、（后续阶段）探索与固化。

## 调用方式

```python
from capability_engine import CapabilityEngine

engine = CapabilityEngine("path/to/browser-site-scraper")
result = engine.call({
    "text": "帮我用百度搜一下 xxx",
    "target": {"url": "https://www.baidu.com/s?wd=xxx"},
    "query": "xxx",
})
```

`result.status` 为 `success` / `fail` / `not_implemented`（探索未接入时的未命中场景）。

## 依赖

- 底层通用浏览器操作能力: `browser-core`（本次拆分自原 `browser-cdp`，尚待独立拆分，
  当前阶段 member 直接复用 `browser-cdp/src/searchers/*`，见 `capability.yaml`）。

## 当前阶段说明

本 skill 处于方案实施 **阶段一**：resolve/execute 两步骨架 + 确定性匹配 + registry/index
读写已实现并验证；探索子agent（explore/distill）尚未接入，命中/执行失败或检索未命中时
会返回 `not_implemented`，明确提示需要人工新增 member 或等待阶段三上线。

详见 `next_doc/generative-capability-skill-plan.md`。
