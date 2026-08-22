---
name: doc-template-generation
skill_type: generative-capability
category_summary: 按特定公司/场景格式生成结构化文档（标准报告、周报等模板），未命中或已有模板失效时自动探索补全。
description: 按特定公司/场景格式生成结构化文档（标准报告、周报等模板）。不在主 context 中展开具体模板列表，按需通过 capability_call 工具检索并加载对应 member；未命中或已有模板失效时触发探索（解析样例文档）自动补全并沉淀为新模板 member。
triggers: 生成文档, 按模板出文档, 公司格式报告, doc template
platforms: windows, macos, linux, pc
---

# doc-template-generation（generative-capability skill）

本 skill 与 `browser-site-scraper` 复用**同一套**通用调度引擎
`mini_agent.skills.generative_capability`，仅通过本目录下的
`capability.yaml` / `explorer/` / `members/` 声明领域差异，引擎代码零改动。

## 调用方式

- **agent 在对话中**：调用 `capability_call(skill_name="doc-template-generation", request={...})` 工具。
- **代码/脚本中直接使用引擎**：

```python
from mini_agent.skills.generative_capability import CapabilityEngine

engine = CapabilityEngine("path/to/doc-template-generation")
result = engine.call({
    "text": "帮我按 standard_report 模板生成一份文档",
    "target": {"template_name": "standard_report"},
    "content": {
        "title": "示例报告",
        "body_sections": [{"heading": "概述", "text": "..."}],
    },
})
```

`result.status` 为 `success` / `fail` / `not_implemented`（探索未接入真实
`explore_runner`/`tool_executor` 时的未命中场景，与 `browser-site-scraper`
含义一致，因为这是引擎的通用行为，不是本 skill 自己实现的）。

## 与 browser-site-scraper 的差异（仅在 capability.yaml / explorer 层）

| | browser-site-scraper | doc-template-generation |
|---|---|---|
| `domain_matchers` | `target.url` 的 `domain_pattern` + `text` 的 `keyword` | `target.template_name` 的 `keyword` + `text` 的 `keyword` |
| `intent_schema_template` | `{results: array}` | `{document: {format, sections}}` |
| `explorer.base_tools` | `browser-core`（网页操作原语） | `doc-core`（文档解析/写入原语，占位声明） |
| 预置 member | `baidu`/`zhihu`（包装既有 `browser-cdp` 脚本） | `standard_report`（纯逻辑实现，不依赖任何外部 skill） |

调度骨架（`resolve`/`execute`/`explore`/`distill`）、生命周期状态机
（probation/trusted/degraded/dead）、检索两级过滤逻辑与
`browser-site-scraper` 完全相同，来自 `mini_agent.skills.
generative_capability` 的同一份代码。

## 已知限制

- `explorer/prompt.md`、`explorer/tool_allowlist.json` 中的 `doc-core`
  仍是占位声明：真正可执行的"解析样例文档/写入新模板"原语尚未实现，
  探索能力目前只能通过桩探索器验证接线逻辑，无法在生产环境中真正学会
  一个全新的文档模板。

## 更多信息

引擎设计、状态机、各版本演进历史（包括本 skill 作为"引擎泛化性验证"
落地的过程）见 `next_doc/generative-capability-skill-plan.md`（本文件只
描述"如何调用本 skill"，不重复记录实施过程）。
