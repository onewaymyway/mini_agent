# 决策画像（Decision Profile / Digital Twin 第二层）

对应设计文档：`next_doc/主动推荐与数字分身机制设计方案.md` 第 4.4 节（阶段三）。

## 是什么

在已有的单条决策记录（`history/decision_extraction.py` + `wiki/decision_writer.py`
产出的 `wiki/decisions/*.md`）之上，新增一层**归纳**：周期性识别反复出现、
可追溯的用户价值取向模式，而不是让每条决策孤立存在。

三层结构：

1. **第一层（已有，不变）**：单条决策事实
2. **第二层（本次新增）**：`evolution/decision_profile_builder.py` 做归纳，
   要求每条模式必须有至少 3 条独立决策记录作为证据（`MIN_EVIDENCE_COUNT`），
   不满足的模式不落地，即使 LLM 自己声称满足也会被代码事后过滤
3. **第三层（本次新增）**：`.agent/wiki/user_value_profile.md`，纳入 wiki 体系，
   字段包含 `pattern / confidence / evidence_refs / first_observed /
   last_reinforced / contradicted_by`

## 矛盾处理（关键设计约束）

新一轮归纳如果和已有模式冲突，**不直接覆盖**：

- 同一模式获得新证据 → 置信度上升，更新 `last_reinforced`
- 新证据与旧模式方向相反 → 记录到 `contradicted_by`，置信度下降，
  但旧模式仍然保留在文档中并标注矛盾提示

这样"用户偏好本身在变化"这件事也是可追溯的信息，不会被静默抹掉。

## 使用方式

```
/profile           # 查看当前决策画像
/profile update     # 触发一次归纳（需要 agent 提供 llm_helper，否则跳过）
```

## 定时任务

内置 cron job `sys:decision_profile_update`，`interval:604800`（周级），
**默认 `enabled: False`**——这是有意的：建议先让阶段一（日报）和阶段二（推荐）
稳定运行数周、积累足够的行为/决策数据后，再由用户手动执行
`/cron enable sys:decision_profile_update` 开启，避免样本不足导致画像失真。

## 目前仅支持的两个初期用法（明确限定范围）

1. **决策问答检索**：直接读 `user_value_profile.md` 做检索式回答"为什么当初
   放弃/选择了 X"，回答时引用具体 `evidence_refs`，不脑补细节。本文件不实现
   问答本身，只产出可被检索的画像文档。
2. **`next_action_advisor` 排序加权**：advisor 可选读取本文件产出的 pattern
   列表，对同优先级候选按已验证的高置信度模式调整排序（本轮代码未接入此加权，
   留待观察画像归纳质量后再连接，避免过早让推荐依赖一个还未验证准确的画像）。

## 明确不做的事

"模拟用户直接做决策"这类更激进的数字分身用法不在本轮计划内，需要用户主动开关
的独立后续设计——提炼错的画像造成"被误解"的负面体验，比没有这个功能更糟。
