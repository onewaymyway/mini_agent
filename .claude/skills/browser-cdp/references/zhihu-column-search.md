# 知乎专栏文章批量搜索与抓取脚本（zhihu_column_search.py）

## 概述

通过百度搜索 `site:zhihu.com` 获取知乎专栏文章链接，自动解析百度重定向链接，抓取并提取结构化内容（标题、作者、发布时间、正文）。

## 用法

```bash
# 基础用法：搜索关键词，抓取前 20 篇文章，翻 3 页
python zhihu_column_search.py "自主Agent" --max-articles 20 --pages 3

# 无头模式（服务器环境）
python zhihu_column_search.py "AI Agent" --max-articles 10 --headless --port 9333

# 指定输出目录
python zhihu_column_search.py "大模型" --max-articles 15 --output-dir ./my_results

# 仅获取搜索结果列表，不抓取详情内容
python zhihu_column_search.py "自主Agent" --no-detail

# 自定义请求间延迟范围（秒）
python zhihu_column_search.py "自主Agent" --delay-range "3,8"
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（必填） | - |
| `--max-articles` | 最大抓取文章数 | 20 |
| `--pages` | 搜索页数（每页约 10 条） | 3 |
| `--output-dir` | 输出目录 | `./search_results` |
| `--port` | CDP 调试端口 | 9333 |
| `--name` | 浏览器实例名称 | `zhihu_column_search` |
| `--headless` | 无头模式 | False |
| `--wait-timeout` | 页面等待超时秒数 | 20 |
| `--max-chars` | 单篇文章最大字符数 | 8000 |
| `--no-detail` | 不抓取详情，仅获取列表 | False |
| `--delay-range` | 请求间延迟范围，格式 min,max | 2,5 |

## 核心功能

### 1. 百度搜索 + 重定向解析
- 使用 `site:zhihu.com` 限定知乎域名
- 自动检测并解析百度重定向链接（`baidu.com/link?`）
- 两种解析策略：JS fetch（不离开页面）+ 导航法（兜底）

### 2. 知乎专栏内容抓取
- 智能等待动态加载的正文内容（`.Post-RichTextContainer` 等选择器）
- 等待条件：正文文本长度 > 100 字符
- 支持多种选择器兜底：`.Post-RichTextContainer`、`.Post-RichText`、`.RichText`、`.article-content`

### 3. 结构化数据提取
- **标题**：`.Post-Title` / `h1.Post-Title` / `.ArticleItem-title` / `h1`
- **作者**：`.AuthorInfo-name` / `.UserLink-link` / `.Post-Author .AuthorInfo-name`
- **发布时间**：`.ContentItem-time` / `[itemprop=datePublished]` / `meta[property=article:published_time]`
- **正文**：清理推荐文章、底部标签、操作栏等无关元素后提取纯文本

### 4. 反爬策略
- 随机 User-Agent 轮换
- 请求前/后随机延迟（可配置范围）
- 翻页间隔延迟
- 失败重试机制（指数退避）
- Cookie 持久化（保持会话）

## 输出文件

运行完成后在输出目录生成三个文件（以时间戳区分）：

| 文件 | 说明 |
|------|------|
| `zhihu_column_<query>_<timestamp>.json` | 完整结构化数据，含搜索索引、文章详情、元数据 |
| `zhihu_column_<query>_<timestamp>.md` | 人类可读的 Markdown 报告，含文章列表、内容摘要、索引表 |
| `zhihu_column_<query>_<timestamp>_index.csv` | 简易索引表，方便 Excel 打开查看 |

## 使用示例

### 示例 1：抓取"自主Agent"相关专栏文章
```bash
cd .claude/skills/browser-cdp
python zhihu_column_search.py "自主Agent" --max-articles 20 --pages 3 --output-dir ./zhihu_results
```

### 示例 2：服务器无头模式批量抓取
```bash
python zhihu_column_search.py "AI Agent" --max-articles 30 --pages 4 --headless --port 9333 --output-dir /data/zhihu_ai_agent
```

### 示例 3：仅获取文章列表用于后续筛选
```bash
python zhihu_column_search.py "大模型" --max-articles 50 --pages 5 --no-detail --output-dir ./zhihu_list
```

## 常见问题与解决

### 1. 百度触发安全验证
**现象**：导航到百度搜索页超时，页面标题显示"百度安全验证"
**解决**：
- 停止当前浏览器实例：`python browser_launch.py --stop-dedicated <name>`
- 重新创建实例（会自动使用新的 User-Agent 和 Cookie）
- 增加 `--delay-range` 延迟范围，如 `5,10`

### 2. 知乎正文加载超时
**现象**：等待正文内容加载时一直显示"内容太短"
**解决**：
- 增加 `--wait-timeout`（默认 20 秒）
- 检查是否需要登录（某些专栏需要登录才能看全文）
- 尝试有头模式（去掉 `--headless`）

### 3. CSS 选择器失效
**现象**：提取不到标题/作者/正文
**解决**：
- 知乎页面结构可能变化，脚本已内置多选择器兜底
- 如仍失败，需手动检查页面结构并更新选择器

### 4. 重定向解析失败
**现象**：百度链接未能解析为真实知乎 URL
**解决**：
- 脚本已实现双策略：JS fetch + 导航法
- 如均失败，会保留原始百度链接并在日志中标记

## 经验总结（开发过程中的关键点）

1. **querySelector 不支持逗号分隔的复合选择器**
   - 错误：`document.querySelector('.a, .b')`
   - 正确：分别尝试 `['.a', '.b']` 循环查找

2. **meta[property=article:published_time] 选择器中的冒号问题**
   - `querySelector` 不支持属性值含冒号的选择器
   - 解决：先 `querySelectorAll('meta[property]')` 再过滤

3. **知乎专栏内容动态加载**
   - 导航完成后正文可能为空
   - 必须轮询等待 `innerText.length > 100` 才算就绪

4. **百度重定向链接必须导航解析**
   - `fetch` 受 CORS 限制无法跟随重定向
   - 必须用 `browser_nav.py` 导航后读取 `location.href`

5. **headless 模式易触发验证码**
   - 调试阶段建议有头模式
   - 生产环境配合代理池和更长延迟

## 相关脚本

| 脚本 | 用途 |
|------|------|
| `zhihu_search.py` | 知乎内容搜索（问答+专栏混合） |
| `zhihu_hot.py` | 知乎热榜抓取 |
| `baidu_search.py` | 百度通用搜索 |
| `detail_cleaner.py` | 详情页内容清理工具 |

## 更新日志

- **2026-07-13** v1.0.0 - 初始版本，支持专栏文章批量搜索与抓取
- 核心功能：百度搜索、重定向解析、动态内容等待、结构化提取、多格式输出
- 反爬策略：随机延迟、UA轮换、Cookie持久化、指数退避重试