# 增强版通用爬虫系统

## 概述

enhanced_crawler.py 是 browser-cdp skill 的增强版爬虫系统，支持多种页面类型的智能抓取和内容提取。

## 核心组件

### 1. ContentType 枚举

支持的内容类型：
- `ARTICLE` - 文章
- `PRODUCT` - 商品
- `IMAGE` - 图片
- `VIDEO` - 视频
- `JOB` - 招聘
- `NEWS` - 新闻
- `BLOG` - 博客
- `FORUM` - 论坛
- `DOCUMENT` - 文档

### 2. SmartExtractor 智能提取器

自动检测内容类型并提取结构化数据：

```python
from src.core.enhanced_crawler import SmartExtractor, ContentType

# 检测内容类型
content_type = SmartExtractor.detect_content_type(html, url)

# 提取内容
extraction = SmartExtractor.extract_from_html(html, content_type)
print(f"标题: {extraction.title}")
print(f"作者: {extraction.author}")
print(f"图片数: {len(extraction.images)}")
```

### 3. UniversalCrawler 通用爬虫

```python
from src.core.enhanced_crawler import UniversalCrawler, create_crawler

# 创建爬虫
crawler = create_crawler(browser_api)

# 爬取单个URL
result = crawler.crawl("https://example.com/article")

# 批量爬取
results = crawler.batch_crawl(["url1", "url2", "url3"])

# 带重试的爬取
result = crawler.crawl_with_retry("https://example.com")

# 获取统计
stats = crawler.get_stats()
```

### 4. MultiPageCrawler 多页爬虫

支持分页和无限滚动：

```python
from src.core.enhanced_crawler import MultiPageCrawler, create_search_crawler

# 创建搜索爬虫
crawler = create_search_crawler(browser_api)

# 爬取搜索结果（带分页）
results = crawler.crawl_search_results(
    search_url="https://example.com/search?q={query}&page={page}",
    query="关键词",
    max_pages=5
)

# 带无限滚动的爬取
result = crawler.crawl_with_infinite_scroll(
    url="https://example.com/feed",
    max_scrolls=10
)
```

### 5. DomainCrawlerFactory 领域爬虫工厂

```python
from src.core.domain_crawlers import DomainCrawlerFactory

# 创建领域爬虫
crawler = DomainCrawlerFactory.create("news", browser_api)
crawler = DomainCrawlerFactory.create("ecommerce", browser_api)
crawler = DomainCrawlerFactory.create("job", browser_api)

# 支持的领域
print(DomainCrawlerFactory.list_domains())
# ['news', 'ecommerce', 'job', 'social', 'video', 'image', 'academic']
```

## 数据存储管道

### CrawlPipeline

```python
from src.data.crawl_pipeline import CrawlPipeline, create_pipeline

# 创建管道（默认SQLite）
pipeline = create_pipeline()

# 保存结果
pipeline.save(crawl_result)

# 加载结果
result = pipeline.load(url)

# 搜索
results = pipeline.search("关键词", limit=20)

# 导出
pipeline.export_to_json(results, "output.json")

# 导入
pipeline.import_from_json("input.json")

# 统计
stats = pipeline.get_stats()
```

### 存储后端

- `SQLiteStorage` - SQLite数据库（默认）
- `JSONStorage` - JSON文件存储
- `MemoryStorage` - 内存缓存

## 使用示例

### 爬取新闻文章

```python
from src.core.domain_crawlers import create_news_crawler
from src.data.crawl_pipeline import create_pipeline

# 创建爬虫和管道
crawler = create_news_crawler(browser_api)
pipeline = create_pipeline()

# 爬取
result = crawler.crawl_news_article("https://news.example.com/article/123")

# 保存
if result.success:
    pipeline.save(result)
    print(f"已保存: {result.extraction.title}")
```

### 爬取商品信息

```python
from src.core.domain_crawlers import create_ecommerce_crawler

# 创建电商爬虫
crawler = create_ecommerce_crawler(browser_api)

# 爬取商品
result = crawler.crawl_product("https://shop.example.com/product/456")

# 提取价格
if result.success and result.extraction:
    price = result.extraction.metadata.get('price', '')
    images = result.extraction.images
```

### 爬取招聘信息

```python
from src.core.domain_crawlers import create_job_crawler

# 创建招聘爬虫
crawler = create_job_crawler(browser_api)

# 搜索职位
results = crawler.crawl_job_search(
    url="https://jobs.example.com/search",
    query="Python工程师",
    location="北京",
    max_pages=3
)

# 处理结果
for result in results:
    if result.success and result.extraction:
        print(f"职位: {result.extraction.title}")
        print(f"公司: {result.extraction.metadata.get('company', '')}")
```

## 配置选项

```python
from src.core.enhanced_crawler import CrawlConfig

config = CrawlConfig(
    wait_time=2.0,           # 页面等待时间
    scroll_to_load=False,    # 是否滚动加载
    scroll_pages=3,          # 滚动页数
    timeout=30,              # 超时时间
    retry_count=3,           # 重试次数
    retry_delay=1.0,         # 重试延迟
)
```

## 扩展自定义选择器

```python
# 自定义选择器
result = crawler.crawl(
    url="https://example.com",
    custom_selectors={
        "title": ["h1.custom-title", ".main-title"],
        "content": [".custom-content", "article.main"]
    }
)
```

## 注意事项

1. **遵守robots.txt**：爬取前请检查网站的爬虫协议
2. **控制频率**：设置合理的wait_time和delay，避免对目标网站造成压力
3. **处理异常**：始终检查result.success和result.error
4. **数据合规**：确保爬取的数据使用符合相关法律法规

## 文件位置

- 核心爬虫：`.claude/skills/browser-cdp/src/core/enhanced_crawler.py`
- 领域爬虫：`.claude/skills/browser-cdp/src/core/domain_crawlers.py`
- 数据管道：`.claude/skills/browser-cdp/src/data/crawl_pipeline.py`
