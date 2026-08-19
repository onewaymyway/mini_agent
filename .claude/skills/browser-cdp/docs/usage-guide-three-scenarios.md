# browser-cdp Skill 使用指南 — 三类场景集成

**版本**: 1.1.0  
**日期**: 2026-08-14  
**适用范围**: 电商搜索、新闻资讯、社交媒体三类场景

---

## 1. 概述

browser-cdp skill 提供三类核心网站交互能力：

| 类别 | 覆盖站点 | Pattern 类 | 核心操作 |
|------|---------|-----------|----------|
| **电商搜索** | 京东、拼多多、淘宝、闲鱼 | `JDSearchPattern`, `PDDSearchPattern`, `TaobaoSearchPattern`, `XianyuSearchPattern` | 关键词搜索、价格/销量提取、商品详情 |
| **新闻资讯** | 知乎、今日头条、新浪财经、财联社 | `ZhihuNewsPattern`, `ToutiaoNewsPattern`, `SinaNewsPattern`, `ClsNewsPattern` | 热榜抓取、新闻搜索、评论互动、无限滚动 |
| **社交媒体** | 小红书、B站 | `XiaohongshuPattern`, `BilibiliPattern` | 内容搜索、热榜、关注/取关、消息推送、无限滚动 |

---

## 2. 快速开始

### 2.1 启动浏览器

```bash
# 方式一：启动专用浏览器实例（推荐）
python src/core/browser_launch.py --dedicated --name browser-cdp-session

# 方式二：连接已有 Chrome（需开启远程调试）
# chrome.exe --remote-debugging-port=9333
```

### 2.2 基础调用示例

```python
import asyncio
from src.interaction_patterns import (
    ZhihuNewsPattern,
    SinaNewsPattern,
    ClsNewsPattern,
    XiaohongshuPattern,
    BilibiliPattern,
)

async def main():
    from src.core.browser_session import BrowserSession
    session = BrowserSession(port=9333)
    await session.connect()

    # 新闻搜索
    zhihu = ZhihuNewsPattern(session)
    results = await zhihu.execute(query="AI 大模型")
    print(f"知乎搜索: {len(results.articles)} 条")

    # 财经热点
    sina = SinaNewsPattern(session)
    hot = await sina.get_hot_list(category="stock")
    print(f"新浪股票热点: {len(hot)} 条")

    # 小红书搜索 + 无限滚动
    xhs = XiaohongshuPattern(session)
    posts = await xhs.search("美食探店", max_results=20)
    print(f"小红书笔记: {len(posts.posts)} 条")

    await session.close()

asyncio.run(main())
```

---

## 3. 电商搜索场景

### 3.1 JDSearchPattern — 京东商品搜索

```python
from src.interaction_patterns import JDSearchPattern

async def jd_example():
    pattern = JDSearchPattern(session, domain="jd.com")
    
    # 关键词搜索
    results = await pattern.execute(query="iPhone 15", max_pages=2)
    
    for item in results.articles:
        print(f"{item.title[:40]}...")
        print(f"  价格: {item.metadata.get('price', 'N/A')}")
        print(f"  销量: {item.metadata.get('sales', 'N/A')}")
        print(f"  链接: {item.url}")
    
    # 商品详情
    detail = await pattern.load_article("https://item.jd.com/100012345678.html")
    print(detail.content[:500])
```

**核心配置**:
| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `search_url` | `https://search.jd.com/Search?keyword={query}` | 搜索 URL 模板 |
| `result_item` | `.gl-item, .item-rect` | 商品卡片选择器 |
| `price_selector` | `.p-price strong` | 价格提取选择器 |
| `sales_selector` | `.p-book-count, .supply-num` | 销量提取选择器 |
| `anti_detection` | `True` | 是否启用反检测 |

### 3.2 PDDSearchPattern — 拼多多商品搜索

```python
from src.interaction_patterns import PDDSearchPattern

async def pdd_example():
    pattern = PDDSearchPattern(session, domain="pinduoduo.com")
    results = await pattern.execute(query="机械键盘", max_pages=1)
    
    for item in results.articles:
        print(f"{item.title[:30]}...  ¥{item.metadata.get('price', '?')}")
```

### 3.3 电商搜索最佳实践

1. **价格范围过滤**：在 query 中直接加入范围
   ```python
   results = await pattern.execute(query="耳机 50-200元", max_pages=1)
   ```
2. **销量排序**：追加排序参数
   ```python
   results = await pattern.execute(query="手机 销量优先", max_pages=1)
   ```
3. **反检测建议**：高频访问时务必开启 stealth 模式，间隔 ≥ 3s

---

## 4. 新闻资讯场景

### 4.1 ZhihuNewsPattern — 知乎热榜 + 搜索

```python
from src.interaction_patterns import ZhihuNewsPattern

async def zhihu_example():
    pattern = ZhihuNewsPattern(session)
    
    # 知乎热榜
    hot = await pattern.get_hot_list()
    for i, article in enumerate(hot[:10], 1):
        print(f"{i}. {article.title}  🔥{article.metadata.get('hot_score', '?')}")
    
    # 问题搜索
    results = await pattern.execute(query="Python 异步编程", max_pages=1)
    
    # 文章加载 + 评论
    article = await pattern.load_article("https://zhuanlan.zhihu.com/p/12345678")
    comments = await pattern.get_comments(article.url)
    print(f"评论数: {len(comments)}")
```

### 4.2 SinaNewsPattern — 新浪财经（双模式）

```python
from src.interaction_patterns import SinaNewsPattern

async def sina_example():
    pattern = SinaNewsPattern(session)
    
    # RSS 优先模式（无浏览器消耗）
    results = await pattern.execute(query="美联储加息", category="macro")
    
    # 分类浏览
    stock_news = await pattern.execute(category="stock", max_pages=2)
    
    # 热点列表
    hot = await pattern.get_hot_list(top_n=20, category="stock")
    for item in hot:
        print(f"[{item.publish_time}] {item.title}")
```

### 4.3 ClsNewsPattern — 财联社电报（API 优先）

```python
from src.interaction_patterns import ClsNewsPattern

async def cls_example():
    pattern = ClsNewsPattern(session)
    
    # 实时电报（直接调用公开 API）
    telegraphs = await pattern.get_telegraph(limit=50)
    for t in telegraphs[:10]:
        importance = t.metadata.get("importance", "?")
        print(f"[{importance}] {t.title}  {t.publish_time}")
    
    # 按关键词搜索
    results = await pattern.execute(query="央行降准", max_pages=1)
```

### 4.4 新闻资讯最佳实践

1. **API 优先**：财联社等支持 API 的网站优先使用 API 模式，浏览器仅作为降级
2. **RSS 解析**：新浪财经等支持 RSS 的网站优先解析 RSS，跳过浏览器渲染
3. **热榜轮询**：每隔 5-10 分钟刷新热榜，避免请求过快

---

## 5. 社交媒体场景

### 5.1 XiaohongshuPattern — 小红书

```python
from src.interaction_patterns import XiaohongshuPattern

async def xhs_example():
    pattern = XiaohongshuPattern(session)
    
    # 内容搜索
    results = await pattern.search(query="咖啡探店", max_results=20)
    for post in results.posts:
        print(f"📌 {post.title}")
        print(f"   👍{post.like_count}  💬{post.comment_count}  🏷️{post.tags}")
    
    # 无限滚动加载更多
    total = await pattern.infinite_scroll(max_pages=5, scroll_delay=1.5)
    print(f"共加载 {total} 条笔记")
    
    # 关注用户（需登录态）
    follow_ok = await pattern.follow_user("美食博主A", user_id="uid_123")
    
    # 获取消息通知
    notifications = await pattern.get_message_notifications(unread_only=True)
    for n in notifications[:5]:
        print(f"[{n['type']}] {n['content'][:30]}")
```

### 5.2 BilibiliPattern — B站视频搜索

```python
from src.interaction_patterns import BilibiliPattern

async def bili_example():
    pattern = BilibiliPattern(session)
    
    # 视频搜索
    results = await pattern.search(query="AI 大模型 教程", max_results=20)
    for post in results.posts:
        print(f"🎬 {post.title}")
        print(f"   👁️{post.like_count}  💬{post.comment_count}")
    
    # 排行榜
    hot = await pattern.get_hot_list(limit=20)
    for item in hot[:10]:
        print(f"{item.get('rank','?')}. {item.get('title','?')}")
```

### 5.3 社交媒体最佳实践

1. **关注操作**：需确保浏览器有对应站点的登录 Cookie，否则返回 `False`
2. **消息通知**：小红书消息页 (`/message`) 需要登录态
3. **无限滚动**：设置合理的 `scroll_delay`（1.5-3s），过快可能触发风控
4. **请求频率**：连续操作间隔 ≥ 2s，避免被临时封禁

---

## 6. 错误处理与降级策略

### 6.1 通用异常处理

```python
from src.interaction_patterns import ZhihuNewsPattern

async def safe_execute():
    pattern = ZhihuNewsPattern(session)
    try:
        results = await pattern.execute(query="关键词", max_pages=2)
        if not results.success:
            print(f"执行失败: {results.error_message}")
            # 降级：尝试简化查询
            results = await pattern.execute(query="关键词", max_pages=1)
        return results
    except Exception as e:
        print(f"异常: {e}")
        return None
```

### 6.2 降级策略矩阵

| 场景 | 主方案 | 降级方案 | 重试策略 |
|------|--------|---------|---------|
| API 超时 | 直接 API 调用 | 浏览器 DOM 抓取 | 最多 2 次 |
| DOM 元素未找到 | CSS 选择器重试 | XPath 回退 | 3 次，递增超时 |
| 网络异常 | 随机延迟后重试 | 切换代理节点 | 最多 3 次 |
| 登录态失效 | 弹出重新登录 | 免登录模式降级 | 提示用户操作 |
| 反检测触发 | 切换 UA/代理 | 延长请求间隔 | 2 次后暂停 30s |

---

## 7. 性能调优建议

### 7.1 并发控制

```python
import asyncio
from src.interaction_patterns import ZhihuNewsPattern

async def batch_search():
    queries = ["AI", "大模型", "LLM", "Transformer"]
    pattern = ZhihuNewsPattern(session)
    
    async def search_one(q):
        results = await pattern.execute(query=q, max_pages=1)
        await asyncio.sleep(2)  # 防封禁间隔
        return results
    
    tasks = [search_one(q) for q in queries]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in results if isinstance(r, type(results[0]))]
```

### 7.2 超时配置

```python
# 不同场景推荐超时
TIMEOUT_CONFIG = {
    "news_search":    {"page_load": 10, "network_idle": 8},
    "news_hotlist":   {"page_load": 8,  "network_idle": 5},
    "social_search":  {"page_load": 15, "network_idle": 10},
    "social_scroll":  {"page_load": 10, "network_idle": 8},
    "e_commerce":     {"page_load": 12, "network_idle": 10},
}
```

---

## 8. 完整工作流示例

### 8.1 每日财经资讯聚合

```python
async def daily_finance_digest():
    """整合新浪+财联社的每日财经摘要"""
    sina = SinaNewsPattern(session)
    cls = ClsNewsPattern(session)
    
    # 新浪股票热点
    sina_hot = await sina.get_hot_list(top_n=10, category="stock")
    
    # 财联社电报
    cls_tele = await cls.get_telegraph(limit=20)
    
    # 去重合并（按标题相似度）
    all_items = sina_hot + cls_tele
    unique_titles = set()
    for item in all_items:
        title_key = item.title[:20]
        if title_key not in unique_titles:
            unique_titles.add(title_key)
            print(f"[{item.metadata.get('source','?')}] {item.title}")
    
    return all_items
```

### 8.2 社交媒体热帖监控

```python
async def monitor_hot_posts():
    """监控小红书+B站热点内容"""
    xhs = XiaohongshuPattern(session)
    bili = BilibiliPattern(session)
    
    # 小红书搜索
    xhs_results = await xhs.search(query="AI 工具", max_results=20)
    xhs_sorted = sorted(xhs_results.posts, key=lambda p: p.like_count, reverse=True)
    
    # B站搜索
    bili_results = await bili.search(query="AI 工具", max_results=20)
    bili_sorted = sorted(bili_results.posts, key=lambda p: p.like_count, reverse=True)
    
    print("=== 小红书热门 ===")
    for p in xhs_sorted[:5]:
        print(f"  👍{p.like_count} {p.title[:30]}")
    
    print("\n=== B站热门 ===")
    for p in bili_sorted[:5]:
        print(f"  👍{p.like_count} {p.title[:30]}")
```

---

## 9. 测试验证

运行全部测试确认功能正常：

```bash
cd .claude/skills/browser-cdp
python -m pytest tests/test_news_pattern.py -v       # 新闻类 55 passed
python -m pytest tests/test_social_content_pattern.py -v  # 社交类 32 passed
python -m pytest tests/test_ecommerce_pattern.py -v  # 电商类（如有）
```

---

*文档版本 1.1.0 | 2026-08-14 | browser-cdp skill*
