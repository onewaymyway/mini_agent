#!/usr/bin/env python
"""
search_examples.py - 搜索器使用示例

演示如何使用 browser-cdp skill 中的各种搜索器。
"""

import asyncio
import json
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.searchers.jd_search import JDSearcher
from src.searchers.pdd_search import PDDSearcher
from src.searchers.douban_search import DoubanSearcher
from src.searchers.sina_news import SinaNewsSearcher
from src.searchers.eastmoney_guba import EastmoneyGubaSearcher
from src.searchers.scholar_search import ScholarSearcher
from src.searchers.base import SearcherConfig


def example_jd_search():
    """京东商品搜索示例"""
    print("=" * 60)
    print("京东商品搜索示例")
    print("=" * 60)
    
    searcher = JDSearcher()
    
    # 命令行方式（推荐）
    # python src/searchers/jd_search.py "iPhone 15" --max-results 10
    
    # Python API 方式
    results = searcher.search(
        query="iPhone 15",
        max_results=5,
        port=9333,
        stealth=True
    )
    
    print(f"找到 {len(results)} 条结果:")
    for i, r in enumerate(results[:3], 1):
        print(f"  {i}. {r.get('title', 'N/A')[:40]}...")
        print(f"     价格: {r.get('price', 'N/A')}")
        print(f"     链接: {r.get('url', 'N/A')[:50]}...")
    
    return results


def example_pdd_search():
    """拼多多商品搜索示例"""
    print("\n" + "=" * 60)
    print("拼多多商品搜索示例")
    print("=" * 60)
    
    searcher = PDDSearcher()
    
    results = searcher.search(
        query="机械键盘",
        max_results=5,
        port=9333,
        stealth=True
    )
    
    print(f"找到 {len(results)} 条结果:")
    for i, r in enumerate(results[:3], 1):
        print(f"  {i}. {r.get('title', 'N/A')[:40]}...")
        print(f"     价格: {r.get('price', 'N/A')}")
        print(f"     销量: {r.get('sales', 'N/A')}")
    
    return results


def example_douban_search():
    """豆瓣搜索示例"""
    print("\n" + "=" * 60)
    print("豆瓣搜索示例")
    print("=" * 60)
    
    searcher = DoubanSearcher()
    
    # 搜索书籍
    book_results = searcher.search(
        query="三体",
        search_type="book",
        max_results=5,
        port=9333,
        stealth=True
    )
    
    print(f"书籍搜索结果 ({len(book_results)} 条):")
    for i, r in enumerate(book_results[:3], 1):
        print(f"  {i}. {r.get('title', 'N/A')[:40]}...")
        print(f"     评分: {r.get('rating', 'N/A')}")
        print(f"     评价数: {r.get('votes', 'N/A')}")
    
    # 搜索电影
    movie_results = searcher.search(
        query="星际穿越",
        search_type="movie",
        max_results=5,
        port=9333,
        stealth=True
    )
    
    print(f"\n电影搜索结果 ({len(movie_results)} 条):")
    for i, r in enumerate(movie_results[:3], 1):
        print(f"  {i}. {r.get('title', 'N/A')[:40]}...")
        print(f"     评分: {r.get('rating', 'N/A')}")
    
    return book_results + movie_results


def example_sina_news():
    """新浪财经新闻示例"""
    print("\n" + "=" * 60)
    print("新浪财经新闻示例")
    print("=" * 60)
    
    searcher = SinaNewsSearcher()
    
    # 获取股票新闻
    results = searcher.search(
        category="stock",
        max_results=10,
        port=9333,
        stealth=True
    )
    
    print(f"股票新闻 ({len(results)} 条):")
    for i, r in enumerate(results[:5], 1):
        print(f"  {i}. {r.get('title', 'N/A')[:50]}...")
        print(f"     时间: {r.get('time', 'N/A')}")
    
    return results


def example_eastmoney_guba():
    """东方财富股吧示例"""
    print("\n" + "=" * 60)
    print("东方财富股吧示例")
    print("=" * 60)
    
    searcher = EastmoneyGubaSearcher()
    
    # 搜索股票帖子
    results = searcher.search(
        stock_code="600519",
        sort="hot",
        max_results=10,
        port=9333,
        stealth=True
    )
    
    print(f"贵州茅台股吧帖子 ({len(results)} 条):")
    for i, r in enumerate(results[:5], 1):
        print(f"  {i}. {r.get('title', 'N/A')[:40]}...")
        print(f"     阅读: {r.get('read_count', 'N/A')}, 评论: {r.get('comment_count', 'N/A')}")
    
    return results


def example_scholar_search():
    """Google Scholar 示例"""
    print("\n" + "=" * 60)
    print("Google Scholar 示例")
    print("=" * 60)
    
    searcher = ScholarSearcher()
    
    results = searcher.search(
        query="transformer architecture",
        max_results=5,
        port=9333,
        stealth=True
    )
    
    print(f"论文搜索结果 ({len(results)} 条):")
    for i, r in enumerate(results[:3], 1):
        print(f"  {i}. {r.get('title', 'N/A')[:50]}...")
        print(f"     作者: {r.get('author', 'N/A')[:30]}...")
        print(f"     引用: {r.get('cited', 'N/A')}")
    
    return results


def example_batch_search():
    """批量搜索示例"""
    print("\n" + "=" * 60)
    print("批量搜索示例")
    print("=" * 60)
    
    keywords = ["iPhone", "华为", "小米"]
    all_results = []
    
    searcher = JDSearcher()
    
    for keyword in keywords:
        print(f"\n搜索: {keyword}")
        results = searcher.search(
            query=keyword,
            max_results=3,
            port=9333,
            stealth=True
        )
        all_results.extend(results)
        
        # 随机延迟，避免触发反爬
        import time
        time.sleep(2)
    
    print(f"\n批量搜索完成，共 {len(all_results)} 条结果")
    return all_results


def example_save_results():
    """保存结果示例"""
    print("\n" + "=" * 60)
    print("保存结果示例")
    print("=" * 60)
    
    searcher = JDSearcher()
    results = searcher.search(
        query="机械键盘",
        max_results=5,
        port=9333,
        stealth=True,
        output_dir="./output"
    )
    
    print(f"结果已保存到 ./output 目录")
    return results


def main():
    """主函数"""
    print("Browser-CDP 搜索器使用示例")
    print("=" * 60)
    print("\n注意：运行前请确保浏览器已启动")
    print("启动命令: python src/core/browser_launch.py --dedicated --name examples")
    print()
    
    # 运行各个示例
    try:
        example_jd_search()
        example_pdd_search()
        example_douban_search()
        example_sina_news()
        example_eastmoney_guba()
        example_scholar_search()
        example_batch_search()
        example_save_results()
        
        print("\n" + "=" * 60)
        print("所有示例运行完成！")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n[错误] {e}")
        print("\n请检查：")
        print("1. 浏览器是否已启动")
        print("2. 调试端口是否正确")
        print("3. 网络连接是否正常")
        sys.exit(1)


if __name__ == "__main__":
    main()
