# 璐㈢粡鏂伴椈鎶撳彇妯″潡

> 鏈枃浠惰鐩栧婧愯储缁忔柊闂绘姄鍙栧姛鑳斤紝鏀寔鏂版氮璐㈢粡銆佸悓鑺遍『銆侀洩鐞冦€佸崕灏旇瑙侀椈銆佽储鑱旂ぞ銆佸井淇″叕浼楀彿銆乤rXiv 绛夋暟鎹簮銆?

---

## 1. 鏁版嵁妯″瀷

### 1.1 NewsSource

```python
from enum import Enum

class NewsSource(Enum):
    SINA = "sina"              # 鏂版氮璐㈢粡
    CLS = "cls"                # 璐㈣仈绀?
    WALLSTREETCN = "wallstreetcn"  # 鍗庡皵琛楄闂?
    XUEQIU = "xueqiu"          # 闆悆
    WECHAT = "wechat"          # 寰俊鍏紬鍙?
    ARXIV = "arxiv"            # arXiv
    REGULATOR = "regulator"    # 鐩戠鏈烘瀯
```

### 1.2 NewsCategory

```python
class NewsCategory(Enum):
    MACRO = "macro"            # 瀹忚缁忔祹
    MARKET = "market"          # 甯傚満琛屾儏
    COMPANY = "company"        # 鍏徃璧勮
    POLICY = "policy"          # 鏀跨瓥鐩戠
    INDUSTRY = "industry"      # 琛屼笟鍔ㄦ€?
    INTERNATIONAL = "international"  # 鍥介檯璧勮
```

### 1.3 FinanceNews

```python
from dataclasses import dataclass
from datetime import datetime

@dataclass
class FinanceNews:
    source: NewsSource                    # 鏁版嵁婧?
    category: NewsCategory                # 鏂伴椈鍒嗙被
    title: str                            # 鏍囬
    content: str                          # 姝ｆ枃
    url: str                              # 鍘熸枃閾炬帴
    publish_time: datetime                # 鍙戝竷鏃堕棿
    symbols: List[str] = None             # 鍏宠仈鏍囩殑
    tags: List[str] = None                # 鏍囩
    sentiment_score: float = None         # 鎯呮劅璇勫垎
```
---

## 2. 鏂伴椈鎶撳彇鍣?

### 2.1 鏂版氮璐㈢粡

```python
from finance_toolkit.news import SinaNewsScraper

scraper = SinaNewsScraper()
news_list = scraper.fetch(
    category="market",
    limit=20,
    symbols=["600000.SH", "000001.SZ"]
)
```

**API 绔偣**:
- 瀹炴椂琛屾儏: `https://hq.sinajs.cn/list=<codes>`
- 鏂伴椈鍒楄〃: `https://feed.mix.sina.com.cn/api/rss/newstoken`

### 2.2 璐㈣仈绀?

```python
from finance_toolkit.news import CLSNewsScraper

scraper = CLSNewsScraper()
news_list = scraper.fetch(
    category="macro",
    limit=20,
    keywords=["澶", "闄嶅噯"]
)
```

**API 绔偣**:
- 鐢垫姤鍒楄〃: `https://www.cls.cn/nodeApi/updateTelegraph`
- 涓撻鍒楄〃: `https://www.cls.cn/nodeApi/subjectList`

### 2.3 鍗庡皵琛楄闂?

```python
from finance_toolkit.news import WallstreetcnScraper

scraper = WallstreetcnScraper()
news_list = scraper.fetch(
    category="international",
    limit=20
)
```

**API 绔偣**:
- 蹇鍒楄〃: `https://api.wallstcn.com/api/information/v1/getFlashList`
- 鏂囩珷鍒楄〃: `https://api.wallstcn.com/api/article/v1/getArticleList`

### 2.4 闆悆

```python
from finance_toolkit.news import XueqiuScraper

scraper = XueqiuScraper()
news_list = scraper.fetch(
    category="company",
    limit=20,
    symbols=["600519.SH"]
)
```

**API 绔偣**:
- 鑲＄エ璧勮: `https://xueqiu.com/statuses/original_timeline.json?symbol=<code>`
- 鐑笘鍒楄〃: `https://xueqiu.com/v5/statuses/original_timeline.json`

### 2.5 arXiv

```python
from finance_toolkit.news import ArxivScraper

scraper = ArxivScraper()
papers = scraper.fetch(
    query="quantitative finance",
    limit=10
)
```

**API 绔偣**:
- 鎼滅储: `http://export.arxiv.org/api/query?search_query=<query>&start=0&max_results=10`
---

## 3. 鏂伴椈鑱氬悎

### 3.1 NewsAggregator

```python
from finance_toolkit.news import NewsAggregator

aggregator = NewsAggregator()

# 鑱氬悎澶氭簮鏂伴椈
news_list = aggregator.aggregate(
    sources=[NewsSource.SINA, NewsSource.CLS, NewsSource.WALLSTREETCN],
    category=NewsCategory.MACRO,
    limit=50
)

# 鍏抽敭璇嶈繃婊?
filtered = aggregator.filter_by_keywords(
    news_list, keywords=["澶", "闄嶅噯", "闄嶆伅"]
)

# 鍘婚噸
unique = aggregator.deduplicate(filtered)
```

### 3.2 鏂伴椈娴佸鐞?

```python
from finance_toolkit.news import NewsStreamProcessor

processor = NewsStreamProcessor()

# 瀹炴椂鏂伴椈娴?
async for news in processor.stream(
    sources=[NewsSource.CLS, NewsSource.SINA],
    keywords=["澶"]
):
    print(f"{news.title}: {news.content[:50]}...")
```

---

## 4. 鏂伴椈鐩戞帶

### 4.1 杩愯鏂伴椈鐩戞帶

```python
from finance_toolkit.news import run_news_monitor

# 鐩戞帶鐗瑰畾鍏抽敭璇?
run_news_monitor(
    keywords=["澶", "闄嶅噯", "闄嶆伅"],
    sources=[NewsSource.CLS, NewsSource.SINA],
    interval=60,  # 鐩戞帶闂撮殧(绉?
    alert_callback=lambda news: print(f"鏂版秷鎭? {news.title}")
)
```

### 4.2 鍛婅绾у埆

```python
from finance_toolkit.news import AlertLevel

# 鍛婅绾у埆
# LOW: 鏅€氭柊闂?
# MEDIUM: 閲嶈鏂伴椈
# HIGH: 閲嶅ぇ鏂伴椈
# CRITICAL: 绱ф€ユ柊闂?
```

---

## 5. 鎯呮劅鍒嗘瀽

### 5.1 鏂伴椈鎯呮劅鍒嗘瀽

```python
from finance_toolkit.news import SentimentAnalyzer

analyzer = SentimentAnalyzer()

# 鍒嗘瀽鍗曟潯鏂伴椈
result = analyzer.analyze("澶瀹ｅ竷闄嶅噯0.5涓櫨鍒嗙偣")
print(f"鎯呮劅: {result.label} (score={result.score:.3f})")

# 鎵归噺鍒嗘瀽
results = analyzer.batch_analyze(news_list)
```

### 5.2 鎯呮劅鑱氬悎

```python
# 鑱氬悎澶氭潯鏂伴椈鐨勬儏鎰?
agg_result = analyzer.aggregate_sentiment(news_list)
print(f"鏁翠綋鎯呮劅: {agg_result.overall_label}")
print(f"骞冲潎璇勫垎: {agg_result.avg_score:.3f}")
```
---

## 6. 浣跨敤绀轰緥

### 6.1 鑾峰彇浠婃棩璐㈢粡瑕侀椈

```python
from finance_toolkit import (
    SinaNewsScraper,
    CLSNewsScraper,
    NewsAggregator,
    NewsCategory
)

# 鍒涘缓鎶撳彇鍣?
sina = SinaNewsScraper()
cls = CLSNewsScraper()

# 鑾峰彇鏂伴椈
sina_news = sina.fetch(category="market", limit=10)
cls_news = cls.fetch(category="macro", limit=10)

# 鑱氬悎
aggregator = NewsAggregator()
all_news = aggregator.aggregate(
    sources=[sina_news, cls_news],
    category=NewsCategory.MACRO,
    limit=20
)

# 杈撳嚭
for news in all_news:
    print(f"[{news.source.value}] {news.title}")
```

### 6.2 鐩戞帶澶鐩稿叧娑堟伅

```python
from finance_toolkit import run_news_monitor, NewsSource

run_news_monitor(
    keywords=["澶", "闄嶅噯", "闄嶆伅", "MLF"],
    sources=[NewsSource.CLS, NewsSource.SINA],
    interval=30,
    alert_callback=lambda news: print(f"[閲嶈] {news.title}")
)
```

---

## 7. 娉ㄦ剰浜嬮」

1. **棰戠巼闄愬埗**: 鍚勬暟鎹簮鏈夎姹傞鐜囬檺鍒讹紝寤鸿闂撮殧涓嶅皯浜?1 绉?
2. **鍙嶇埇绛栫暐**: 璐㈣仈绀俱€侀洩鐞冪瓑鏈夊弽鐖満鍒讹紝寤鸿浣跨敤浠ｇ悊杞崲
3. **鏁版嵁鏃舵晥**: 鏂伴椈鏁版嵁鏃舵晥鎬у己锛屽缓璁畾鏈熸洿鏂?
4. **鎯呮劅鍒嗘瀽**: 鎯呮劅鍒嗘瀽鍩轰簬璇嶅吀娉曪紝澶嶆潅璇鍙兘涓嶅噯纭?

---

> **鐩稿叧璧勬簮**: 
> - `references/news-scraper.md` - 璇︾粏 API 鏂囨。
> - `references/troubleshooting.md` - 甯歌闂鎺掓煡
