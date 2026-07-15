---
id: <kebab-case-id>
type: entity
tags: []
status: active
confidence: 0.5
created: <YYYY-MM-DD>
updated: <YYYY-MM-DD>
links:
  - target: <other-page-id>
    relation: depends_on   # depends_on | supersedes | part_of | conflicts_with | absorbs ...
    note: ""
source_entries: []
---

## 概述

这是什么（模块 / 工具 / bug 模式 / 外部依赖），一两句话。

## 当前状态

当前的共识/实现现状。对应旧 entity_index.py 里滚动重写的 summary，
但这里是可追加的 section，不是被整体覆盖的单一字符串。

## 历史沿革

按时间顺序记录关键变化点，旧结论不会因为新证据出现而被静默覆盖，
需要时可以直接看到"某个方案曾经是这样、后来为什么变了"。

## 相关

（可选）非结构化的补充说明；结构化的关系用 frontmatter 的 links 表达。
