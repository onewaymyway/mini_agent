# 知乎问题搜索 Prompt（skill_agent，挂载 browser-cdp skill）

你现在要使用 browser-cdp skill 真实操作浏览器，在知乎（www.zhihu.com）上搜索问题。

## 关键修复：使用已登录的知乎浏览器实例

**必须使用以下固定配置**：
- 调试端口：`9336`
- 用户数据目录：`.claude/skills/browser-cdp/temp_data/zhihu_logged_in_profile`
- 这对应 `launch_zhihu_logged_in.py` 启动的已登录浏览器实例

**不要**使用 `--name=zhihu_session` 或其他配置，那个实例没有知乎登录态。

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

## 执行方式

请直接调用 browser-cdp skill 的脚本来执行搜索。推荐使用以下方式：

```bash
cd .claude/skills/browser-cdp
python zhihu_search_with_login.py "<关键词>" --port 9336 --max-results 10
```

或者批量搜索所有关键词：

```bash
cd .claude/skills/browser-cdp
python zhihu_search_with_login.py --batch --port 9336 --max-results 10
```

## 输出要求

把所有关键词搜到的问题去重合并（同一个问题 URL 只保留一条），整理成一个 JSON 对象直接返回，顶层字段：

- `questions`：数组，每个元素是一个问题对象，包含字段：
  - `id`：字符串，用 q1/q2/q3... 顺序编号即可，供后续步骤引用
  - `title`：字符串，问题标题
  - `url`：字符串，问题详情页的完整 URL（如 https://www.zhihu.com/question/xxxxxxx）
  - `snippet`：字符串，搜索结果页展示的简要说明/摘要文字（没有则填空字符串）
  - `matched_keywords`：字符串数组，命中的关键词列表
  - `search_page_meta`：对象，把搜索页上能看到的其它元信息（比如已有回答数、关注数）原样放进来，没有就是空对象
- `total_keywords_searched`：整数，本次实际搜索的关键词总数
- `total_unique_questions`：整数，去重后的问题总数

**只输出这一个 JSON 对象本身，不要输出多余说明文字或 markdown 代码块标记（不要用 ```json 包裹）。**