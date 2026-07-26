# 知乎问题搜索 Prompt（skill_agent，挂载 browser-cdp skill）

你现在要使用 browser-cdp skill 真实操作浏览器，在知乎（www.zhihu.com）上搜索问题。

## 前置条件

请先按照 browser-cdp skill 的说明，使用固定的专用浏览器实例名 `zhihu_session`
（`--name=zhihu_session`）来 attach/启动浏览器，这样能复用之前的登录状态，不要每次
用不同的 name 或不传 name。如果发现未登录，请提醒我手动登录一次，登录状态会保存在
该专用实例的 profile 里，之后无需重复登录。

## 关键词

以下是本次要检索的关键词/短语列表（来自文档分析结果）：

{analyze_doc.output}

请取出其中的 `search_keywords` 字段，逐个在知乎搜索框中搜索。

## 搜索要求

对每个关键词：
1. 在知乎搜索框输入关键词并搜索，筛选到"问题"这个内容类型（如果知乎搜索页支持按类型筛选）。
2. 尽可能多地翻页/下滑加载更多结果，抓取搜索结果列表里出现的每一个问题，不要只看第一屏。
3. 对每个问题，抓取搜索结果页上能看到的所有相关信息，至少包括：
   - 问题标题
   - 问题详情页的完整 URL
   - 搜索结果里展示的简要说明/摘要文字（如果有）
   - 搜索结果里展示的其它元信息（比如已有回答数、关注数——如果搜索页本身就展示了的话）

## 输出要求

把所有关键词搜到的问题去重合并（同一个问题 URL 只保留一条），整理成如下 JSON 结构直接返回：

```json
{{
  "questions": [
    {{
      "id": "q1",
      "title": "问题标题",
      "url": "https://www.zhihu.com/question/xxxxxxx",
      "snippet": "搜索结果页展示的简要说明",
      "matched_keywords": ["命中的关键词1", "命中的关键词2"],
      "search_page_meta": {{"注意": "把搜索页上能看到的其它字段原样放进来"}}
    }}
  ],
  "total_keywords_searched": 0,
  "total_unique_questions": 0
}}
```

`id` 字段用 q1/q2/q3... 顺序编号即可，供后续步骤引用。只输出这个 JSON，不要输出多余说明文字。
