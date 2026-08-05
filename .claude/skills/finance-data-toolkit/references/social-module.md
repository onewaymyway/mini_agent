# 社交媒体数据抓取模块

> 本文件覆盖社交媒体数据抓取功能，支持微博热搜、雪球讨论、同花顺问财等数据源。

---

## 1. 数据模型

### 1.1 SocialSource

```python
from enum import Enum

class SocialSource(Enum):
    WEIBO = "weibo"              # 微博
    XUEQIU = "xueqiu"            # 雪球
    THS_WENCAI = "ths_wencai"    # 同花顺问财
    ZHIHU = "zhihu"              # 知乎
    REDDIT = "reddit"            # Reddit
```

### 1.2 SocialCategory

```python
class SocialCategory(Enum):
    HOT = "hot"                  # 热搜
    DISCUSSION = "discussion"    # 讨论
    ANALYSIS = "analysis"        # 分析
    NEWS = "news"                # 资讯
```

### 1.3 SocialPost

```python
from dataclasses import dataclass
from datetime import datetime

@dataclass
class SocialPost:
    source: SocialSource                    # 数据源
    category: SocialCategory                # 分类
    content: str                            # 内容
    author: str                             # 作者
    publish_time: datetime                  # 发布时间
    like_count: int = 0                     # 点赞数
    comment_count: int = 0                  # 评论数
    share_count: int = 0                    # 转发数
    symbols: List[str] = None               # 关联标的
    url: str = None                         # 原文链接
    sentiment_score: float = None           # 情感评分
```

---

## 2. 社交媒体抓取器

### 2.1 微博热搜

```python
from finance_toolkit.social import WeiboHotScraper

scraper = WeiboHotScraper()
hot_list = scraper.fetch(
    limit=50,
    category="finance"  # 财经类
)
```

**API 端点**:
- 热搜榜: `https://weibo.com/ajax/side/hotSearch`
- 实时热搜: `https://m.weibo.cn/api/container/getIndex?containerid=106003type%3D25%26t%3D3%26disable_hot%3D1%26filter_type%3Drealtimehot`

### 2.2 雪球讨论

```python
from finance_toolkit.social import XueqiuDiscussionScraper

scraper = XueqiuDiscussionScraper()
discussions = scraper.fetch(
    symbol="600519.SH",
    limit=20,
    sort="hot"
)
```

**API 端点**:
- 股票讨论: `https://xueqiu.com/v5/statuses/original_timeline.json?symbol=<code>`
- 热帖列表: `https://xueqiu.com/v5/statuses/hot_timeline.json`

### 2.3 同花顺问财

```python
from finance_toolkit.social import ThsWencaiScraper

scraper = ThsWencaiScraper()
results = scraper.query(
    query="市盈率小于20的银行股",
    limit=20
)
```

**API 端点**:
- 问财搜索: `https://www.iwencai.com/unifiedwap/unified-wap/v2/result/get-robot-data`

---

## 3. 快捷函数

### 3.1 获取微博热搜

```python
from finance_toolkit.social import fetch_weibo_hot

hot_list = fetch_weibo_hot(limit=50)
for item in hot_list[:10]:
    print(f"{item['rank']}. {item['title']} ({item['hot']})")
```

### 3.2 获取雪球热帖

```python
from finance_toolkit.social import fetch_xueqiu_hot

hot_posts = fetch_xueqiu_hot(limit=20)
for post in hot_posts:
    print(f"{post.title}: {post.content[:50]}...")
```

### 3.3 获取同花顺热帖

```python
from finance_toolkit.social import fetch_ths_wencai_hot

hot_posts = fetch_ths_wencai_hot(limit=20)
```

### 3.4 获取所有平台热帖

```python
from finance_toolkit.social import fetch_all_social_hot

all_hot = fetch_all_social_hot(
    sources=[SocialSource.WEIBO, SocialSource.XUEQIU],
    limit=50
)
```

---

## 4. 使用示例

### 4.1 监控股票相关讨论

```python
from finance_toolkit import (
    XueqiuDiscussionScraper,
    fetch_weibo_hot
)

# 获取雪球讨论
xueqiu = XueqiuDiscussionScraper()
discussions = xueqiu.fetch(
    symbol="600519.SH",
    limit=20,
    sort="hot"
)

# 获取微博热搜
hot_list = fetch_weibo_hot(limit=50)

# 分析热度
for post in discussions:
    print(f"[{post.like_count}赞] {post.content[:30]}...")
```

### 4.2 舆情监控

```python
from finance_toolkit.social import (
    fetch_all_social_hot,
    SocialSource
)

# 获取多平台热帖
all_hot = fetch_all_social_hot(
    sources=[SocialSource.WEIBO, SocialSource.XUEQIU],
    limit=100
)

# 筛选特定关键词
keywords = ["茅台", "业绩", "财报"]
filtered = [item for item in all_hot if any(k in item['content'] for k in keywords)]

for item in filtered[:10]:
    print(f"[{item['source']}] {item['content'][:50]}...")
```

---

## 5. 注意事项

1. **反爬策略**: 微博、雪球等有严格反爬机制，建议使用代理轮换
2. **频率限制**: 建议请求间隔不少于 2 秒
3. **数据时效**: 社交媒体数据时效性强，建议实时获取
4. **合规风险**: 注意数据使用合规性，避免大规模抓取

---

> **相关资源**: 
> - `references/troubleshooting.md` - 常见问题排查
