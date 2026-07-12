# 渐进式加载机制详解

## 核心原理

`SkillLoader` 在"skill 级激活"之上，增加了"资源级加载"这一层，两级机制并行：

- **Skill 级**：`skill_activate`/`skill_deactivate` 工具 + 关键词 `auto_activate`，
  控制整个 `SKILL.md` 正文是否注入 system prompt。
- **资源级**（本层新增）：`skill_resource_load`/`skill_resource_unload` 工具 +
  关键词 `auto_activate_resources`，控制某个 skill 激活后，它名下哪些
  `references/*.md` 子文档被进一步注入。

一个 skill 激活后，system prompt 里除了正文，永远会带一份**资源清单**（很轻量，
每条几十 token），列出该 skill 下所有 `resources` 的 id/说明/加载状态/历史使用次数。
agent 任何时候都能看到"还有什么可以加载"，不需要靠运气 `view` 目录。

## 两条加载通道

1. **关键词自动通道**：`resources[].triggers` 命中用户输入时自动加载。
   `triggers` 留空的资源不参与此通道。
2. **Agent 主动通道**：调用 `skill_resource_load(skill_name, resource_id, reason)`，
   无论有没有关键词命中，agent 判断需要就可以主动加载——这是为了覆盖"关键词覆盖不到，
   但 agent 从上下文能判断出需要"的情况（例如资源清单里 `notrigger` 这类特意不设
   `triggers` 的资源，只能靠这条通道加载）。

两条通道走向同一份状态和同一个 tracker，天然幂等：已加载的资源再次 load 会重新从磁盘
读取（支持热编辑），但不会重复计入两份状态。

## 卸载与再加载的行为

- **资源级卸载**（`skill_resource_unload`，或被 token 预算挤出）：只从 context
  移除内容，清单里的条目**不会消失**，状态变回"未加载"；调用记录（次数/最近使用时间）
  **保留**，下次 `skill_resource_list` 依然能看到"历史使用 N 次"，供 agent 判断是否
  值得再加载。
- **父 skill 整体 `deactivate`**：其下所有已加载资源随之清出 context（因为资源内容
  本来挂在父 skill 的上下文块下）。清单状态重置为全部"未加载"，但 tracker 统计
  不清零。
- **父 skill 再次 `activate`**：清单重新展示，**默认不自动恢复**之前加载过的资源
  （避免激活一个 skill 顺带复活一堆旧内容导致 context 膨胀），但清单上会标注历史
  使用次数，方便 agent 自己决定要不要立刻重新加载。

## Token 预算

资源内容和 skill 主体分开计预算：`per_resource_tokens`（默认 3000）控制单个资源
被截断的上限；skill 主体仍用原有的 `per_skill_tokens`/`total_budget`。

## 何时用 `resources`，何时用 `browse_paths`

| 情况 | 归类 |
|---|---|
| 单文件、边界清晰、agent 用到时大概率整份消化（如"高级配置说明""错误排查表"） | `resources` |
| 大型文档库/多文件集合，"哪一段有用"取决于具体 query（如完整 API 手册、多语言 SDK 文档、几十个示例） | `browse_paths` |

`browse_paths` 完全不接入加载机制、不计 token 预算、不在 tracker 里留痕——纯粹是
SKILL.md 正文里的一句路径提示，agent 该用自己的文件工具（`view`/`grep`/`bash find`）
自行检索，不要指望它被整段加载进 context。
