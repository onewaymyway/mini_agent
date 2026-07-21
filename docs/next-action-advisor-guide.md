# 主动推荐排序（Next Action Advisor）

对应设计文档：`next_doc/主动推荐与数字分身机制设计方案.md` 第 4.2 节（阶段二）。

## 是什么

`evolution/next_action_advisor.py` 明确定位为 `soft_goal_deriver.py` 的"排序 +
讲道理"层，而不是重新做候选发现：

- `soft_goal_deriver` 负责"发现该不该新建一个 Goal"，写入 `GoalBacklog`
- 本模块负责"在已有信息里，这次该优先提醒用户哪一个、为什么"，**只读不写**

候选来源两类：

1. **停滞目标**：`GoalBacklog` 中优先级 ≥1 且超过 7 天无 `last_touched_at` 更新的
   Goal/Objective
2. **注意力错配**：最近 6 小时窗口内，某个 app/域名的时长占比超过 50%，且其名称与
   任何 active Goal 的 title/tags 都没有关键词重合

候选为空时不生成任何输出（克制阈值），不会为了"有话可说"而凑一条平庸建议。

## 分步落地（改进计划要求）

1. **规则层（当前默认路径）**：只用上述两条规则筛选 + 固定优先级排序
   （停滞目标 > 注意力错配），不接 LLM。建议先跑一段时间，观察规则本身
   是否符合直觉。
2. **LLM 排序层（`rank_with_llm=True`）**：对规则筛出的候选做一次 LLM 调用，
   要求输出必须引用已有 `evidence_refs`，不允许引入候选之外的新理由；
   LLM 调用失败时静默回退到规则排序。

## 使用方式

```
/next            # 查看当前推荐（不重新计算）
/next refresh    # 重新扫描候选并排序
```

产出文件：`.agent/next_actions.json`，包含 `rank/kind/ref_id/title/reason/evidence_refs`。

## 定时任务

内置 cron job `sys:next_action_digest`，`interval:10800`（3 小时一次），
task_template 调用 `/next refresh`。候选为空时任务本身也会跳过输出。

## 启动展示与看板

- CLI/daemon 启动时若存在 `shown_at` 为空的推荐，打印排名第一条的摘要，例如：

  ```
  💡 建议：wiki 提取层 O3——已 12 天无进展记录，优先级 2（`/next` 查看全部）
  ```

- 看板"建议"卡片（前端部分，见 kanban dashboard 相关改动）读取同一份
  `next_actions.json` 展示完整列表。

## 有意暂不做的事

- 不会主动打断式推送，除非"注意力错配"信号连续超过设定时长（该推送渠道
  复用已有多客户端推送机制，未在本轮代码中实现，留待后续观察规则准确度后再接入）。
- 不做"计划 vs 实际"式反拖延对比（需要用户主动声明当天计划），本机制刻意
  只做纯行为推断，不引入任何需要用户额外输入的环节。
