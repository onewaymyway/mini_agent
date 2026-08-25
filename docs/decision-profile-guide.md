# 决策画像（Decision Profile / Digital Twin 第二层）

对应设计文档：`next_doc/proactive-recommendation-and-digital-persona-design.md` 第 4.4 节（阶段三）。

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
/decision_profile           # 查看当前决策画像
/decision_profile update    # 触发一次归纳（需要 agent 提供 llm_helper，否则跳过）
```

## 定时任务

内置 cron job `sys:decision_profile_update`，`interval:604800`（周级），
**默认 `enabled: False`**——这是有意的：建议先让阶段一（日报）和阶段二（推荐）
稳定运行数周、积累足够的行为/决策数据后，再由用户手动执行
`/cron enable sys:decision_profile_update` 开启，避免样本不足导致画像失真。
这个默认值本身也可以通过 `agent_config.json` 的 `digest_advisor.decision_profile_enabled`
配置项覆盖（仅影响 job 首次被写入 `cron_jobs.json` 时的初始状态，之后用户通过
`/cron enable|disable` 做的手动修改不会被配置覆盖）。

## 目前仅支持的两个初期用法（明确限定范围）

1. **决策问答检索**：直接读 `user_value_profile.md` 做检索式回答"为什么当初
   放弃/选择了 X"，回答时引用具体 `evidence_refs`，不脑补细节。本文件不实现
   问答本身，只产出可被检索的画像文档。
2. **`next_action_advisor` 排序加权**（本轮已接入）：`agent_config.json` 的
   `digest_advisor.next_action_profile_weighting_enabled` 开启后，`/next refresh`
   与 `sys:next_action_digest` cron job 会读取本文件归纳出的高置信度模式
   （置信度需 ≥ `next_action_profile_weighting_min_confidence`，默认 0.5），
   对同类候选（`stale_goal` 内部或 `attention_mismatch` 内部）做"排序内加权"：
   候选标题/理由与某条模式关键词重合时优先展示。加权只影响同类候选的相对顺序，
   不跨类别提升、不新增候选、不影响候选本身的产生逻辑。默认关闭。

## 相关配置（`agent_config.json` → `digest_advisor`）

| 字段 | 默认值 | 说明 |
|---|---|---|
| `decision_profile_enabled` | `false` | 控制 `sys:decision_profile_update` cron job 的初始 enabled 状态 |
| `decision_profile_min_evidence_count` | `3` | 归纳一条模式所需的最少独立决策记录数（对应 `MIN_EVIDENCE_COUNT`） |
| `next_action_profile_weighting_enabled` | `false` | 是否用本画像对 `next_action_advisor` 排序做加权 |
| `next_action_profile_weighting_min_confidence` | `0.5` | 参与加权的模式最低置信度门槛 |

## 命令行提示

`/decision_profile [update]` 已加入 `cli/parser.py` 的 `--help` 文本与
`ui/terminal.py` 的斜杠命令自动补全列表。注意命令名是 `/decision_profile`
而不是 `/profile`——后者是既有的"强制刷新用户画像"命令（`UserProfileManager`，
见 `docs/user-profile-guide.md`），两者完全无关，命名上刻意避开重名。

## Kanban 看板

`apps/mini_agent_kanban` 的"📌 目标看板" Tab 有一张"🧭 决策画像"卡片，对接
只读端点 `GET /v1/decision_profile`，展示已归纳出的模式列表（含置信度、
矛盾提示）和完整 Markdown 原文，不会因为看板刷新页面而重复触发归纳（归纳
依赖 LLM 调用，成本比日报/推荐高得多，更不应该被动触发）。详见
`docs/kanban-dashboard-guide.md`。

## 明确不做的事

"模拟用户直接做决策"这类更激进的数字分身用法不在本轮计划内，需要用户主动开关
的独立后续设计——提炼错的画像造成"被误解"的负面体验，比没有这个功能更糟。
