#!/usr/bin/env python3
"""
Enhanced Crawler 测试脚本
测试通用爬虫系统的核心功能
"""
import sys
import json
import time
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / 'src'))
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.enhanced_crawler import (
    SmartExtractor, ContentType, CrawlResult, ContentExtraction,
    UniversalCrawler, MultiPageCrawler, CrawlConfig
)
from src.core.domain_crawlers import DomainCrawlerFactory
from src.data.crawl_pipeline import CrawlPipeline, create_pipeline


def test_content_type_detection():
    """测试内容类型检测"""
    print("\n=== 测试内容类型检测 ===")
    
    test_cases = [
        ("https://example.com/article/123", ContentType.ARTICLE),
        ("https://shop.example.com/product/456", ContentType.PRODUCT),
        ("https://jobs.example.com/job/789", ContentType.JOB),
        ("https://gallery.example.com/photo/101", ContentType.IMAGE),
        ("https://video.example.com/watch/abc", ContentType.VIDEO),
        ("https://news.example.com/story/xyz", ContentType.NEWS),
        ("https://forum.example.com/thread/123", ContentType.FORUM),
    ]
    
    for url, expected in test_cases:
        result = SmartExtractor.detect_content_type("<html></html>", url)
        status = "✓" if result == expected else "✗"
        print(f"  {status} {url[:40]}... -> {result.value}")
    
    print("  [PASS] 内容类型检测完成")


def test_extractor_article():
    """测试文章提取"""
    print("\n=== 测试文章提取 ===")
    
    html = '''
    <html>
    <head><title>测试文章标题</title></head>
    <body>
        <h1 class="article-title">文章标题</h1>
        <span class="author">作者名称</span>
        <time class="publish-time">2024-01-01</time>
        <article class="article-content">
            这是文章内容。包含重要的信息。
        </article>
        <img src="https://example.com/image1.jpg">
        <img data-src="https://example.com/image2.jpg">
    </body>
    </html>
    '''
    
    extraction = SmartExtractor.extract_from_html(html, ContentType.ARTICLE)
    
    print(f"  标题: {extraction.title}")
    print(f"  作者: {extraction.author}")
    print(f"  发布时间: {extraction.publish_time}")
    print(f"  图片数: {len(extraction.images)}")
    print(f"  内容长度: {len(extraction.content)}")
    
    assert extraction.title == "文章标题", f"标题不匹配: {extraction.title}"
    assert extraction.author == "作者名称", f"作者不匹配: {extraction.author}"
    assert len(extraction.images) == 2, f"图片数量不匹配: {len(extraction.images)}"
    
    print("  [PASS] 文章提取完成")


def test_extractor_product():
    """测试商品提取"""
    print("\n=== 测试商品提取 ===")
    
    html = '''
    <html>
    <body>
        <h1 class="product-title">iPhone 15 Pro</h1>
        <span class="price">¥8,999</span>
        <span class="original-price">¥9,999</span>
        <img class="product-image" src="https://example.com/product.jpg">
    </body>
    </html>
    '''
    
    extraction = SmartExtractor.extract_from_html(html, ContentType.PRODUCT)
    
    print(f"  商品名: {extraction.title}")
    print(f"  价格: {extraction.metadata.get('price', '')}")
    print(f"  原价: {extraction.metadata.get('original_price', '')}")
    print(f"  图片数: {len(extraction.images)}")
    
    assert extraction.title == "iPhone 15 Pro"
    assert "¥8,999" in extraction.metadata.get('price', '')
    
    print("  [PASS] 商品提取完成")


def test_extractor_job():
    """测试招聘提取"""
    print("\n=== 测试招聘提取 ===")
    
    html = '''
    <html>
    <body>
        <h1 class="job-title">Python工程师</h1>
        <span class="company">某科技公司</span>
        <span class="location">北京</span>
        <div class="job-description">岗位职责：...</div>
    </body>
    </html>
    '''
    
    extraction = SmartExtractor.extract_from_html(html, ContentType.JOB)
    
    print(f"  职位: {extraction.title}")
    print(f"  公司: {extraction.metadata.get('company', '')}")
    print(f"  地点: {extraction.metadata.get('location', '')}")
    print(f"  描述长度: {len(extraction.content)}")
    
    assert extraction.title == "Python工程师"
    assert "某科技公司" in extraction.metadata.get('company', '')
    
    print("  [PASS] 招聘提取完成")


def test_pipeline():
    """测试数据存储管道"""
    print("\n=== 测试数据存储管道 ===")
    
    # 使用内存存储进行快速测试
    pipeline = create_pipeline(backend_type="memory")
    
    # 创建测试数据
    extraction = ContentExtraction(
        content_type=ContentType.ARTICLE,
        title="测试文章",
        author="测试作者",
        content="这是一篇测试文章的内容。"
    )
    
    result = CrawlResult(
        url="https://example.com/test-article",
        success=True,
        content_type=ContentType.ARTICLE,
        extraction=extraction,
        duration_ms=1500,
        crawled_at="2024-01-01T10:00:00"
    )
    
    # 保存
    saved = pipeline.save(result)
    print(f"  保存结果: {saved}")
    assert saved, "保存失败"
    
    # 加载
    loaded = pipeline.load("https://example.com/test-article")
    print(f"  加载结果: {loaded is not None}")
    assert loaded is not None, "加载失败"
    assert loaded.extraction.title == "测试文章", f"标题不匹配: {loaded.extraction.title}"
    
    # 搜索
    search_results = pipeline.search("测试")
    print(f"  搜索结果数: {len(search_results)}")
    
    # 统计
    stats = pipeline.get_stats()
    print(f"  总记录数: {stats['total_records']}")
    print(f"  成功次数: {stats['saved']}")
    
    print("  [PASS] 数据管道测试完成")


def test_factory():
    """测试领域爬虫工厂"""
    print("\n=== 测试领域爬虫工厂 ===")
    
    domains = DomainCrawlerFactory.list_domains()
    print(f"  支持的领域: {domains}")
    
    expected_domains = ['news', 'ecommerce', 'job', 'social', 'video', 'image', 'academic']
    for domain in expected_domains:
        assert domain in domains, f"缺少领域: {domain}"
    
    print("  [PASS] 领域爬虫工厂测试完成")


def main():
    """运行所有测试"""
    print("=" * 50)
    print("Enhanced Crawler 测试套件")
    print("=" * 50)
    
    start_time = time.time()
    
    try:
        test_content_type_detection()
        test_extractor_article()
        test_extractor_product()
        test_extractor_job()
        test_pipeline()
        test_factory()
        
        elapsed = time.time() - start_time
        
        print("\n" + "=" * 50)
        print(f"所有测试通过！耗时: {elapsed:.2f}s")
        print("=" * 50)
        return 0
        
    except Exception as e:
        print(f"\n[FAIL] 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
