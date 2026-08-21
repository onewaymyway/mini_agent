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

本 skill 处于方案实施 **阶段四**：在阶段三（探索子agent + 蒸馏固化）基础上，
补齐了完整生命周期状态机所需的严格 schema 校验，并接入了定期健康巡检：

- `_engine/schema_validator.py`：无第三方依赖的最小 JSON Schema 校验器，支持
  `type`/`required`/`properties`/`items`/`enum` 递归校验。命中执行（`execute()`）
  与探索蒸馏产物（`distiller.distill()`）现在共用同一套严格校验，不再只检查
  "必填字段是否存在"，类型/嵌套结构不对也会被判定失败。
- `_engine/health_patrol.py`：低频后台巡检任务，默认只读扫描 + 生成结构化报告：
  - 检测 `_index.json` / `registry.json` / `members/` 目录三者互相不一致的情况
    （如"脚本能跑但检索不到"、"检索能到但脚本已被清理"），`--fix-inconsistencies`
    时以 `registry.json` 为准做最小修复；
  - 标记长期未被检索命中执行、或长期无成功/失败记录的 member（阈值见
    `capability.yaml -> lifecycle.health_patrol_stale_days`），供人工审查；
  - 标记已进入 `dead` 状态超过保留期（`health_patrol_dead_retention_days`）的
    member，默认只在报告里给出"建议清理"，`--apply-cleanup` 时才真正删除
    （删除前会把 `meta.json` 内容记入报告，避免误删且不可审计）。

探索能力（阶段三）仍需真实 `tool_executor` 接入才能在生产环境生效，阶段四
未改变这一现状。

## 阶段五：作为可复用 SDK 被验证

`.claude/skills/_engine` 现在是一个可以直接 `from _engine import CapabilityEngine`
的包（原先要求调用方自己 `sys.path.insert(...)` 塞入 `_engine` 目录再用
flat 模块名 `import capability_engine` 的写法仍然兼容，但不再是唯一/推荐用法）。
调度骨架、状态机、检索逻辑本身在本阶段**零改动**——阶段五只做了两件事：把引擎
包装成稳定的公开接口，以及新增第二个 generative-capability skill
`doc-template-generation` 验证复用性，详见该目录的 SKILL.md 与本文档阶段五
实施记录。

详见 `next_doc/generative-capability-skill-plan.md`。
