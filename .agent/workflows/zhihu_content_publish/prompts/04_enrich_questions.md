> [已弃用] enrich_questions 已改为 type: python_step（steps/05_enrich_questions.py +
> prompts/04_enrich_single_question.md），不再使用本文件描述的 skill_agent 多轮对话
> 方式，原因是逐个问题走完整 agent 对话太慢。本文件仅保留作历史记录/回退参考。

# 补全问题详情 Prompt（skill_agent，挂载 browser-cdp skill）

你现在要使用 browser-cdp skill（沿用固定专用实例 ，复用登录态），逐个打开
上一步筛选出来的知乎问题详情页，补全信息。

## 关键修复：使用已登录的知乎浏览器实例

**必须使用以下固定配置**：
- 调试端口：`9336`
- 用户数据目录：`.claude/skills/browser-cdp/temp_data/zhihu_logged_in_profile`
- 这对应 `launch_zhihu_logged_in.py` 启动的已登录浏览器实例

**不要**使用 `--name=zhihu_session` 或其他配置，那个实例没有知乎登录态。

## 待补全的问题列表

{filter_questions.output}

## 对每个问题要做的事

打开该问题的 `url`，抓取详情页上能看到的所有信息，至少包括：
- 回答数量
- 关注者数量
- 浏览次数（如果页面展示了的话）
- 问题的完整描述/补充说明（如果有，往往比搜索结果里的 snippet 更完整）
- 问题创建时间/最近活跃时间（如果页面展示了的话）
- 排名最前面（默认排序下第一个）的回答的完整内容，以及该回答的作者、点赞数

## 输出要求

在每个问题原有字段的基础上，把上面抓到的信息合并进去，整理成一个 JSON 对象。
**注意：这个 JSON 对象不是靠对话回复交付的，本轮任务结束前系统会额外告诉你一个绝对路径，
你必须用文件写入工具把这个 JSON 对象实际写入那个文件。** 顶层字段：

- `questions`：数组，每个元素是一个问题对象（在原有 `id`/`title`/`url` 等字段基础上补充）：
  - `id`、`title`、`url`：沿用上一步的原值
  - `answer_count`：整数，回答数量
  - `follower_count`：整数，关注者数量
  - `view_count`：整数，浏览次数（页面没展示则填 `null`）
  - `description`：字符串，问题的完整描述/补充说明（没有则填 `null`）
  - `created_or_active_time`：字符串，问题创建时间/最近活跃时间（页面没展示则填 `null`）
  - `top_answer`：对象，排名最前面（默认排序下第一个）的回答，包含：
    - `author`：字符串，回答作者
    - `upvote_count`：整数，该回答的点赞数
    - `content`：字符串，该回答的完整内容
- `total_enriched`：整数，本次成功补全信息的问题总数

把这个 JSON 对象写入系统告诉你的文件路径（不要用 markdown 代码块标记包裹文件内容），
不需要在对话里重复输出这段 JSON。如果某个字段页面上确实抓不到，用 `null`，不要编造。
