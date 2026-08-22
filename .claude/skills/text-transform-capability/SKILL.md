---
name: text-transform-capability
skill_type: generative-capability
category_summary: 对一段文本做简单确定性变换（大写/反转等），未命中已知变换时自动探索补全。用于验证 generative-capability 机制本身，不建议作为真实业务能力使用。
description: 纯逻辑文本变换能力包（大写/反转/未来可探索出新变换）。不在主 context 中展开具体变换列表，按需通过 capability_call 工具检索并加载对应 member；未命中时触发探索并沉淀为新 member。刻意设计为不依赖任何外部服务、浏览器或 API key，可在任意沙箱/CI 环境里完整跑通 resolve/execute/explore/distill/生命周期/健康巡检 全链路，专门用来验证机制本身是否可用。
triggers: 文本变换测试, text transform capability test, 验证生成式能力skill机制
platforms: windows, macos, linux, pc
---

# text-transform-capability（generative-capability 机制自检 skill）

本 skill 与 `browser-site-scraper`、`doc-template-generation` 复用**同一套**通用
调度引擎 `mini_agent.skills.generative_capability`，引擎代码零改动。它的存在
目的与另外两个不同：**不是一个真实业务能力，而是一个刻意做得很小、很容易跑通、
不依赖任何外部环境（无需浏览器/API key/网络）的"机制自检"skill**，用来验证
`resolve`/`execute`/`explore`/`distill`/生命周期状态机/健康巡检各机制点确实
可用。

配套的测试方法见 `test_cases/text-transform-capability-testing-guide.md`。

## 预置 member

| member_id | 功能 | 匹配关键词 |
|---|---|---|
| `upper` | 把 `content.text` 转为大写 | `upper` / `转大写` / `uppercase` |
| `reverse` | 把 `content.text` 反转 | `reverse` / `反转` / `倒序` |

两个 member 都是纯 Python 字符串操作，`run(input)` 不发起任何网络请求、不依赖
任何第三方库，输入缺少 `content.text` 时会显式返回 `status: fail`（用于验证
schema 校验与失败计数路径），不会抛未捕获异常。

## 调用方式

- **agent 在对话中**：调用 `capability_call(skill_name="text-transform-capability", request={...})` 工具。
- **代码/脚本中直接使用引擎**（推荐用于测试，见配套测试文档）：

```python
from mini_agent.skills.generative_capability import CapabilityEngine

engine = CapabilityEngine("path/to/text-transform-capability")
result = engine.call({
    "text": "帮我把这段文字转大写",
    "target": {"op": "upper"},
    "content": {"text": "hello world"},
})
# result.status == "success"
# result.data == {"result": {"text": "HELLO WORLD"}}
```

`result.status` 为 `success` / `fail` / `not_implemented`，含义与
`browser-site-scraper` / `doc-template-generation` 完全一致（引擎通用行为）。

## 与另外两个 generative-capability skill 的差异（仅在 capability.yaml / explorer 层）

| | browser-site-scraper | doc-template-generation | text-transform-capability |
|---|---|---|---|
| `domain_matchers` | `target.url` 的 `domain_pattern` + `text` 的 `keyword` | `target.template_name` 的 `keyword` + `text` 的 `keyword` | `target.op` 的 `keyword` + `text` 的 `keyword` |
| `intent_schema_template` | `{results: array}` | `{document: {format, sections}}` | `{result: {text: string}}` |
| `explorer.base_tools` | `browser-core`（占位） | `doc-core`（占位） | `text-core`（占位） |
| 预置 member 是否依赖外部服务 | 是（依赖浏览器） | 否（纯逻辑） | 否（纯逻辑，且刻意连本地文件/子进程都不用） |
| 设计目的 | 真实业务能力 | 验证泛化性（第二个领域） | 验证机制本身（可完整自动化测试，无需任何桩之外的外部依赖） |

调度骨架（`resolve`/`execute`/`explore`/`distill`）、生命周期状态机
（probation/trusted/degraded/dead）、检索两级过滤逻辑，与另外两个 skill
完全相同，来自 `mini_agent.skills.generative_capability` 的同一份代码。

## 探索场景设计（用于验证 explore/distill 链路）

`text-transform-capability` 只预置了 `upper` 和 `reverse` 两个 member。测试
文档里会用一个第三方"变换"（如 `shout`，给文本末尾加感叹号）来触发 miss ->
explore -> distill -> 落盘 -> 免探索复用 的完整链路，探索阶段用
`build_stub_explorer` 桩探索器（不需要真实 API key），工具执行器同样是一个
纯内存的桩函数（不依赖 `text-core` 的真实实现——与 `browser-core`/`doc-core`
一样，`text-core` 目前仍是占位声明，本 skill 不主张已经具备"学会新文本变换"
的生产能力，只用于验证引擎接线本身）。

## 已知限制

- `explorer/prompt.md`、`explorer/tool_allowlist.json` 中的 `text-core` 是
  占位声明，没有真正可执行的实现；这是刻意的——本 skill 的目的是验证机制，
  不是提供真实的文本处理能力，真正需要文本变换能力时应该用更合适的静态 skill
  或直接写代码，不建议在生产场景中依赖本 skill。
- 与另外两个 skill 一样，`schema_validator` 仍是常用关键字子集而非完整 JSON
  Schema 规范。

## 更多信息

引擎设计、状态机、各版本演进历史见
`next_doc/generative-capability-skill-plan.md`（本文件只描述"如何调用本
skill"，不重复记录实施过程）。
