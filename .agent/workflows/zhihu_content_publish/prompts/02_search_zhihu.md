# 知乎问题搜索 Prompt（skill_agent，挂载 browser-cdp skill）

你现在要使用 browser-cdp skill 真实操作浏览器，在知乎（www.zhihu.com）上搜索问题。

## 关键词文件

以下是文档分析步骤（analyze_doc）的完整产出：

{analyze_doc.output}

其中 `keywords_file` 字段是一个**已经生成好**的绝对路径，指向一份纯 JSON
数组格式的关键词文件（内容就是 `search_keywords` 那份列表，格式已经符合
`zhihu_search_with_login.py --keywords-file` 的要求）。**直接使用这个路径**，
不需要你自己再从 `search_keywords` 字段现算一遍、手工写一份关键词文件——
那一步已经由上游步骤做完了。

## 搜索要求

1. 直接调用下方"执行方式"里的命令，把 `keywords_file` 的路径原样传给
   `--keywords-file` 参数。
2. 命令跑完后，从其输出/落盘结果里获取每个关键词搜到的问题列表——具体是
   直接读脚本的返回内容，还是脚本自己已经落了一份结果文件，以脚本实际
   行为为准；如果搜索结果不完整或某些关键词没搜到，可以针对性地再手工
   在知乎搜索框里补搜，不要求全程只能靠这一条命令。

## 执行方式

请直接调用 browser-cdp skill 的脚本来执行搜索：

```bash
cd .claude/skills/browser-cdp
python src/searchers/zhihu_search_with_login.py --keywords-file "<analyze_doc.output 里的 keywords_file 绝对路径>" --port 9336 --min-results 30 --max-results 60 --max-scrolls 12 --scroll-pause 3
```

**关键修复：使用已登录的知乎浏览器实例**：

- 调试端口：`9336`
- 用户数据目录：`.claude/skills/browser-cdp/temp_data/zhihu_logged_in_profile`
- 这对应 `launch_zhihu_logged_in.py` 启动的已登录浏览器实例
- **不要**使用 `--name=zhihu_session` 或其他配置，那个实例没有知乎登录态。

对脚本没能覆盖到的信息（比如某个问题在搜索结果页上还展示了额外的元信息，
但脚本抓取逻辑没覆盖），可以用浏览器工具手工补充抓取，至少确保每个问题都
包含：
   - 问题标题
   - 问题详情页的完整 URL
   - 搜索结果里展示的简要说明/摘要文字（如果有）
   - 搜索结果里展示的其它元信息（比如已有回答数、关注数——如果搜索页本身就展示了的话）

## 输出要求

脚本会输出一个 JSON 数组，每项包含 `content_id`、`content_title`、`query`、
`question_title`、`question_url` 字段。你需要把它转换成 workflow 下游需要的格式：

**注意：这个 JSON 对象不是靠对话回复交付的，本轮任务结束前系统会额外告诉你一个绝对路径，
你必须用文件写入工具把这个 JSON 对象实际写入那个文件。** 顶层字段：

- `questions`：数组，每个元素是一个问题对象，包含字段：
  - `id`：字符串，用 q1/q2/q3... 顺序编号即可，供后续步骤引用
  - `title`：字符串，问题标题（来自脚本输出的 `question_title`）
  - `url`：字符串，问题详情页的完整 URL（来自脚本输出的 `question_url`）
  - `snippet`：字符串，搜索结果页展示的简要说明/摘要文字（没有则填空字符串）
  - `matched_keywords`：字符串数组，命中的关键词列表（来自脚本输出的 `query` 字段）
  - `search_page_meta`：对象，把搜索页上能看到的其它元信息（比如已有回答数、关注数）原样放进来，没有就是空对象
- `total_keywords_searched`：整数，本次实际搜索的关键词总数
- `total_unique_questions`：整数，去重后的问题总数

**把这个 JSON 对象写入系统告诉你的文件路径（不要用 markdown 代码块标记包裹文件内容），
不需要在对话里重复输出这段 JSON。**

## 收尾要求（重要）

写入文件后，请立即执行以下检查并结束本轮任务，不要再进行任何额外的浏览器操作：

1. 用文件工具读取一次刚写入的文件，确认内容是合法 JSON 且包含 `questions` 字段。
2. 确认无误后，**立刻**用一句简短文字回复任务已完成（例如"已写入 N 个问题到结果文件"），
   然后结束本轮对话——不要继续搜索更多关键词、不要重复翻页、不要做任何与"写入结果文件"
   无关的验证或探索性操作。文件一旦写入并自检通过，多余的操作只会拖长执行时间，没有必要。