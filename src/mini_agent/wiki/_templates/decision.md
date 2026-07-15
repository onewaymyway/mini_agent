---
id: <kebab-case-id>
type: decision
tags: []
status: settled
confidence: 0.5
created: <YYYY-MM-DD>
updated: <YYYY-MM-DD>
links:
  - target: <affected-entity-id>
    relation: affects
    note: ""
source_entries: []
---

## 问题

要解决什么问题/取舍点，背景是什么。

## 考虑过的方案

1. 方案 A：... — 优缺点
2. 方案 B：... — 优缺点
3. **方案 C（已采纳）**：... — 为什么选它

## 采纳理由

最终选了哪个，为什么。

## 如果要推翻这个决定

需要先验证/满足什么条件，才值得重新论证这个决定。

## 复盘（如适用）

如果这个决定后来被重新讨论或推翻，在这里追加记录，而不是删除原文：

- status: settled -> revisited（被重新提起但维持原判）或 -> overturned（被推翻）
- 被推翻时，新建一条决策页记录替代方案，新页面用
  `links: relation=supersedes` 指向本页面；本页面反向追加
  `links: relation=superseded_by` 指向新页面，保持双向可追溯。

<!--
status 生命周期（决策/取舍知识提炼计划 5.1 节）：
  settled   — 尚未被重新审视，当前有效
  revisited — 被重新提起讨论，但复核后维持原判
  overturned — 被推翻，应存在一条 superseded_by 指向替代它的新决策页

confidence 固定为 0.5：决策复盘是 agent 对自己历史行为的二次解读，主观
重构风险高于规则触发的 lesson（0.6）与人类显式纠正（0.7），因此单独定位
在两者之下，不与它们混用同一套置信度语义。
-->
