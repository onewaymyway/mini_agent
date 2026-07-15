---
id: <kebab-case-id>
type: topic
tags: []
status: active
confidence: 0.5
created: <YYYY-MM-DD>
updated: <YYYY-MM-DD>
links:
  - target: <aggregated-page-id>
    relation: absorbs
    note: ""
source_entries: []
---

## 综合叙事

跨多篇页面的完整来龙去脉，人工或 LLM 聚合撰写。这是重构计划中"没有可读
的综合层"问题的解法——一次跨模块的大重构，应该有一个地方能读到完整故事，
而不是要去几个实体页面里自己拼。

## 涉及页面

用 frontmatter 的 links（relation: absorbs）列出被聚合的页面，本节可以
补充每篇页面在这个专题里扮演的角色。

## 时间线

（可选）关键节点按时间排列。
