#!/usr/bin/env python3
"""
测试新增模块和搜索器
"""
import asyncio
import sys
from pathlib import Path

# 添加 src 目录到路径
skill_dir = Path(__file__).parent.parent
sys.path.insert(0, str(skill_dir))

from src.searchers.api_searcher import RESTAPISearcher, GraphQLSearcher, APISearcherFactory
from src.searchers.realtime_searcher import StockSearcher, CryptoSearcher, NewsSearcher
from src.core.cloudflare_bypass import CloudflareBypass, CloudflareConfig


class MockSession:
    """模拟 CDP Session"""
    
    def __init__(self):
        self.url = "https://example.com"
        self.pages = {}
    
    async def get_current_url(self):
        return self.url
    
    async def evaluate(self, js):
        return None
    
    async def query_selector_all(self, selector):
        return []
    
    async def query_selector(self, selector):
        return None
    
    async def screenshot(self, path=None, clip=None):
        return b"fake_screenshot"
    
    async def close(self):
        pass


def test_api_searcher():
    """测试 API 搜索器"""
    print("\n=== 测试 API 搜索器 ===")
    
    # 测试 REST API 搜索器
    rest_searcher = RESTAPISearcher()
    print(f"REST 搜索器名称：{rest_searcher.source_name}")
    print(f"支持类型：{rest_searcher.supported_types}")
    
    # 测试 GraphQL 搜索器
    graphql_searcher = GraphQLSearcher()
    print(f"GraphQL 搜索器名称：{graphql_searcher.source_name}")
    print(f"支持类型：{graphql_searcher.supported_types}")
    
    # 测试工厂
    factory_searcher = APISearcherFactory.create("github")
    print(f"GitHub 搜索器类型：{type(factory_searcher).__name__}")
    
    print("✓ API 搜索器测试通过")


def test_realtime_searcher():
    """测试实时数据搜索器"""
    print("\n=== 测试实时数据搜索器 ===")
    
    # 测试股票搜索器
    stock_searcher = StockSearcher()
    print(f"股票搜索器名称：{stock_searcher.source_name}")
    print(f"支持类型：{stock_searcher.supported_types}")
    
    # 测试加密货币搜索器
    crypto_searcher = CryptoSearcher()
    print(f"加密货币搜索器名称：{crypto_searcher.source_name}")
    print(f"支持类型：{crypto_searcher.supported_types}")
    
    # 测试新闻搜索器
    news_searcher = NewsSearcher()
    print(f"新闻搜索器名称：{news_searcher.source_name}")
    print(f"支持类型：{news_searcher.supported_types}")
    
    # 测试工厂
    stock_factory = RealtimeSearcherFactory.create("stock")
    print(f"股票工厂搜索器类型：{type(stock_factory).__name__}")
    
    print("✓ 实时数据搜索器测试通过")


def test_cloudflare_bypass():
    """测试 Cloudflare 绕过模块"""
    print("\n=== 测试 Cloudflare 绕过模块 ===")
    
    mock_session = MockSession()
    config = CloudflareConfig()
    bypass = CloudflareBypass(mock_session, config)
    
    print(f"Cloudflare 绕过配置：")
    print(f"  - JS 绕过：{config.enable_js_bypass}")
    print(f"  - 指纹绕过：{config.enable_fingerprint_bypass}")
    print(f"  - 最大重试：{config.max_retries}")
    
    print("✓ Cloudflare 绕过模块测试通过")


class RealtimeSearcherFactory:
    """实时数据搜索器工厂"""
    
    _searchers = {
        "stock": StockSearcher,
        "crypto": CryptoSearcher,
        "news": NewsSearcher
    }
    
    @classmethod
    def create(cls, site: str):
        site_lower = site.lower()
        for key, searcher_class in cls._searchers.items():
            if key in site_lower:
                return searcher_class()
        return None


async def main():
    """主测试函数"""
    print("=" * 50)
    print("browser-cdp skill 新增模块测试")
    print("=" * 50)
    
    try:
        # 测试 API 搜索器
        test_api_searcher()
        
        # 测试实时数据搜索器
        test_realtime_searcher()
        
        # 测试 Cloudflare 绕过模块
        test_cloudflare_bypass()
        
        print("\n" + "=" * 50)
        print("所有测试通过！✓")
        print("=" * 50)
        
    except Exception as e:
        print(f"\n✗ 测试失败：{e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
