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
如实反馈，见方案文档阶段七"已知遗留"）。

## 依赖

- 底层通用浏览器操作能力: `browser-core`（本次拆分自原 `browser-cdp`，尚待独立拆分，
  当前阶段 member 直接复用 `browser-cdp/src/searchers/*`，见 `capability.yaml`）。

## 当前阶段说明

本 skill 处于方案实施 **阶段四**：在阶段三（探索子agent + 蒸馏固化）基础上，
补齐了完整生命周期状态机所需的严格 schema 校验，并接入了定期健康巡检：

- `schema_validator.py`：无第三方依赖的最小 JSON Schema 校验器，支持
  `type`/`required`/`properties`/`items`/`enum` 递归校验。命中执行（`execute()`）
  与探索蒸馏产物（`distiller.distill()`）现在共用同一套严格校验，不再只检查
  "必填字段是否存在"，类型/嵌套结构不对也会被判定失败。
- `health_patrol.py`：低频后台巡检任务，默认只读扫描 + 生成结构化报告：
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

引擎打包成一个可以直接 `import` 的公开接口（原先要求调用方自己
`sys.path.insert(...)` 塞目录、再用 flat 模块名互相引用的写法，阶段七
已彻底移除）。调度骨架、状态机、检索逻辑本身在本阶段**零改动**——阶段五
只做了两件事：把引擎包装成稳定的公开接口，以及新增第二个
generative-capability skill `doc-template-generation` 验证复用性，详见该
目录的 SKILL.md 与本文档阶段五实施记录。

## 阶段六：修复阶段五记录的两处遗留问题

1. **状态流转时间戳（`status_changed_at`）**：`registry.json` 中每个 member
   在 `probation -> trusted` / `-> degraded` / `-> dead` 发生时都会写入
   `status_changed_at`，蒸馏新建/重探索成功回到 `probation` 时同样写入。
   `health_patrol.py::_dead_since()` 现在优先用这个精确时间戳判断
   "进入 dead 多久了"，只有存量数据缺这个字段时才退化为原来的近似算法
   （用 `last_failure` 或 `meta.json` mtime 近似），保持向后兼容。
2. **`distill_trust_trace_data` 一致性兜底**：`capability.yaml` 新增可选
   `distill.trust_trace_data` 开关（本 skill 默认 `false`，不影响生产行为）。
   开启后，仅当蒸馏脚本重放动作序列、最后一个真实工具步骤取不到 `data` 时，
   才把探索阶段已通过 `intent_schema` 校验的 `trace.data` 当兜底常量嵌入
   脚本，而不是像阶段五记录的那样直接判定"探索未能生成可靠方案"。是否用到
   兜底会记入新 member 的 `meta.json -> distill_used_trace_data_fallback`
   字段，保持可审计。该开关只建议在自测/CI 场景临时开启；真实
   `browser-core` 提取类工具通常最后一步就会返回 `data`，无需开启。

## 阶段七：引擎迁入主项目正常子包

`.claude/skills/_engine` 目录已删除，引擎代码现在位于
`src/mini_agent/skills/generative_capability/`（`import mini_agent.skills.
generative_capability`），本 skill 目录下只保留声明式配置与运行时数据
（本文件、`capability.yaml`、`explorer/`、`_index.json`、`registry.json`、
`members/`）。新增 `capability_call` 工具（`src/mini_agent/tools/
capability_call.py`），agent 现在可以在对话中直接调用本 skill，不再需要
人工手动跑 CLI 自测入口。详见方案文档阶段七实施记录。

详见 `next_doc/generative-capability-skill-plan.md`。
