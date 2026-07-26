# 补全问题详情 Prompt（skill_agent，挂载 browser-cdp skill）

你现在要使用 browser-cdp skill（沿用固定专用实例 `zhihu_session`，复用登录态），逐个打开
上一步筛选出来的知乎问题详情页，补全信息。

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

在每个问题原有字段的基础上，把上面抓到的信息合并进去，整理成如下 JSON 结构直接返回：

```json
{{
  "questions": [
    {{
      "id": "q1",
      "title": "...",
      "url": "...",
      "answer_count": 0,
      "follower_count": 0,
      "view_count": 0,
      "description": "问题的完整描述",
      "created_or_active_time": "...",
      "top_answer": {{
        "author": "...",
        "upvote_count": 0,
        "content": "排名最前面的回答内容"
      }}
    }}
  ],
  "total_enriched": 0
}}
```

只输出这个 JSON，不要输出多余说明文字。如果某个字段页面上确实抓不到，用 null，不要编造。
