# 微信公众号文章搜索自动化脚本（wechat_search.py）

通过搜狗微信搜索 (weixin.sogou.com) 搜索公众号文章，自动解析搜狗重定向链接，抓取文章详细内容。

## 核心功能

1. **搜索**：通过搜狗微信搜索 type=2（文章搜索）获取结果列表
2. **重定向解析**：自动解析搜狗 `/link?url=...` 重定向链接，获取真实 `mp.weixin.qq.com` 文章 URL
3. **详情抓取**：导航到文章页，等待 JS 渲染完成，提取纯文本正文、标题、作者、发布时间、封面图
4. **反爬策略**：随机延迟、随机 User-Agent、请求频率控制、Cookie 持久化
5. **结果保存**：输出 JSON 和 Markdown 双格式

## 安装依赖

```bash
cd .claude/skills/browser-cdp
pip install websocket-client requests pillow
```

## 使用方法

```bash
# 基础用法：搜索并抓取前 10 篇文章详情
python wechat_search.py "自主进化Agent" --max-results 10

# 仅获取搜索结果列表，不抓取详情
python wechat_search.py "AI Agent" --max-results 5 --no-detail

# 指定端口、输出目录、无头模式
python wechat_search.py "大模型" --port 9333 --output-dir ./wechat_results --headless

# 调整页面等待超时
python wechat_search.py "RAG" --wait-timeout 60
```

## 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `query` | str | 必填 | 搜索关键词 |
| `--max-results` | int | 10 | 最大抓取文章数 |
| `--no-detail` | flag | False | 仅获取搜索结果，不抓取文章详情 |
| `--port` | int | 9333 | CDP 调试端口 |
| `--output-dir` | str | ./search_results | 输出目录 |
| `--headless` | flag | False | 无头模式运行 |
| `--wait-timeout` | int | 30 | 页面等待超时秒数 |

## 输出格式

### JSON 结构
```json
{
  "query": "自主进化Agent",
  "search_engine": "sogou_weixin",
  "crawl_time": "2026-07-13 18:08:49",
  "total": 6,
  "articles": [
    {
      "title": "AI Agent的自主演化：从工具调用到自我优化的新时代",
      "url": "https://mp.weixin.qq.com/s?src=11&...",
      "redirect_url": "https://weixin.sogou.com/link?url=...",
      "snippet": "2026年的AI领域正经历一场深刻的范式转变...",
      "account": "老婷说",
      "pub_time": "2026年3月23日 22:17",
      "content": "文章完整正文文本...",
      "author": "老婷",
      "publish_time": "2026-03-23 22:17",
      "cover": "https://mmbiz.qpic.cn/...",
      "word_count": 8500
    }
  ]
}
```

### Markdown 结构
- 标题、公众号、发布时间、链接、重定向链接、字数
- 摘要
- 正文内容（前 3000 字预览）
- 分隔符 `---` 分隔每篇文章

## 核心实现要点

### 1. 搜狗微信搜索 URL 构造
```python
SOGOU_WEIXIN_BASE = "https://weixin.sogou.com/weixin"
search_url = f"{SOGOU_WEIXIN_BASE}?type=2&query={quote(query)}&ie=utf8"
# type=2 文章搜索，type=1 公众号搜索
```

### 2. 搜索结果页 JS 提取
```javascript
// 选择器覆盖多种布局
const containers = document.querySelectorAll('.news-box, .vrwrap, [id^="sogou_vr_11002601_box_"]');
// 提取：标题、重定向链接、摘要、公众号、发布时间
```

### 3. 重定向链接解析（关键难点）
```python
def resolve_sogou_redirect(port, tab_id, redirect_url, wait_timeout=15):
    # 1. 记录当前搜索页 URL
    # 2. 导航到重定向链接
    # 3. 等待 URL 变为 mp.weixin.qq.com
    # 4. 获取真实 URL
    # 5. 导航回搜索结果页
    # 6. 返回真实 URL
```

**关键点**：
- 搜狗重定向链接必须通过浏览器导航解析（fetch 因 CORS 无法工作）
- 导航后需等待 `domcontentloaded` 并检查 URL 变化
- 解析完成后必须导航回搜索结果页，以便处理下一条

### 4. 微信文章页面内容提取
```python
# 等待正文选择器出现（多选择器兜底）
WECHAT_ARTICLE_SELECTORS = [
    "#js_content",
    ".rich_media_content",
    "#img-content",
    ".weui-article",
]

# 导航等待
browser_nav.py --wait-selector "#js_content,.rich_media_content" --timeout 30

# 等待 JS 渲染完成
import time; time.sleep(3)

# 提取纯文本
browser_extract.py --mode text --max-chars 50000

# 提取元数据（标题、作者、时间、封面）
# 通过 JS 选择器获取
```

### 5. 反爬策略
```python
# 随机延迟
random_delay(2.0, 4.0)  # 文章间
random_delay(1.0, 2.0)  # 搜索后

# 随机 User-Agent
USER_AGENTS = ["Chrome/120...", "Firefox/121...", ...]

# Cookie 持久化
# 登录态保持，减少验证码触发
```

### 6. 错误处理与重试
- 页面加载失败：记录错误，继续下一篇
- 重定向解析失败：保留原重定向链接，标记为需人工处理
- 内容提取失败：返回原始内容，由清理模块处理
- 网络异常：指数退避重试（来自 baidu_search 复用）

## 常见问题与解决

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| 搜索结果为空 | 关键词过长/特殊字符/反爬 | 简化关键词、增加延迟、更换 IP |
| 重定向解析失败 | 搜狗反爬升级/需要验证码 | 手动登录搜狗微信、使用已登录浏览器实例 |
| 文章内容为空 | 文章需登录/已删除/反爬 JS 加密 | 尝试登录态、检查文章是否存在 |
| 页面加载超时 | 网络慢/微信服务器响应慢 | 增加 `--wait-timeout` 参数 |
| 编码乱码 | 控制台编码问题 | 设置 `chcp 65001` 或使用 UTF-8 终端 |

## 进阶用法

### 使用已登录的浏览器实例（避免验证码）
```bash
# 1. 启动专用实例并手动登录搜狗微信
python browser_launch.py --dedicated --name wechat_logged --start-url "https://weixin.sogou.com" --port 9334
# 2. 在打开的浏览器中手动登录
# 3. 后续抓取复用该实例
python wechat_search.py "关键词" --port 9334
```

### 批量关键词搜索
```bash
for kw in "自主进化Agent" "AI Agent" "Agent 记忆" "多Agent协作"; do
  python wechat_search.py "$kw" --max-results 5 --output-dir ./batch_results
  sleep 30  # 关键词间延迟

done
```

### 定时监控
```bash
# 加入 crontab 每日抓取
0 8 * * * cd /path/to/skill && python wechat_search.py "自主进化Agent" --max-results 10 --output-dir /data/wechat_daily
```

## 与其他搜索脚本对比

| 特性 | baidu_search.py | zhihu_search.py | wechat_search.py |
|------|----------------|----------------|------------------|
| 搜索源 | 百度网页 | 百度 site:zhihu.com | 搜狗微信搜索 |
| 目标内容 | 通用网页 | 知乎问答/专栏 | 微信公众号文章 |
| 重定向解析 | 百度 link?url= | 百度 link?url= | 搜狗 link?url= |
| 详情页等待 | 通用选择器 | 知乎特定选择器 | 微信特定选择器 |
| 反爬强度 | 中 | 中高 | 高 |
| 登录态价值 | 低 | 中（知乎登录可见更多） | 高（微信登录可绕过验证） |

## 文件结构

```
.claude/skills/browser-cdp/
├── wechat_search.py          # 主脚本
├── baidu_search.py           # 复用：浏览器启动、重定向解析、延迟、重试、Cookie
├── detail_cleaner.py         # 复用：内容清理
├── browser_*.py              # 底层 CDP 操作
└── references/
    └── wechat-search.md      # 本文档
```

## 更新日志

- **v1.0 (2026-07-13)**：初始版本，支持搜狗微信搜索、重定向解析、文章详情抓取、双格式输出

## 相关资源

- [搜狗微信搜索](https://weixin.sogou.com/)
- [微信公众平台](https://mp.weixin.qq.com/)
- [browser-cdp skill 主文档](../SKILL.md)
- [百度搜索脚本文档](baidu-search.md)
- [知乎搜索脚本文档](zhihu-search.md)
