"""
测试数据生成器

生成标准化的测试数据（URL、选择器、预期结果）。
"""
from typing import Dict, List, Any, Optional
from datetime import datetime
import json


class TestDataGenerator:
    """测试数据生成器"""
    
    # 测试用 URL 模板
    TEST_URLS = {
        "simple": "https://example.com",
        "ecommerce": "https://www.jd.com",
        "news": "https://news.baidu.com",
        "search": "https://www.baidu.com",
        "social": "https://weibo.com",
        "gov": "https://www.gov.cn",
        "job": "https://www.zhaopin.com",
        "finance": "https://finance.sina.com.cn",
    }
    
    # 测试用选择器模板
    TEST_SELECTORS = {
        "search_input": "input[type='search'], input[name='q'], #kw",
        "search_button": "button[type='submit'], input[type='submit'], .search-btn",
        "link": "a[href]",
        "title": "h1, title",
        "content": "article, .content, #content, main",
        "pagination": ".pagination, .page, [class*='page']",
        "image": "img[src]",
        "button": "button, .btn, [role='button']",
        "input": "input[type!='submit'], input[type!='button'], textarea, select",
    }
    
    # 测试用预期结果模板
    TEST_EXPECTATIONS = {
        "page_load": {
            "status_code": 200,
            "url_contains": "example.com",
            "title_contains": "Example",
            "has_content": True,
        },
        "search_result": {
            "result_count": ">= 5",
            "has_links": True,
            "has_snippets": True,
        },
        "article": {
            "has_title": True,
            "has_content": True,
            "has_images": True,
            "content_length": ">= 100",
        },
    }
    
    def __init__(self):
        self._test_data = {}
    
    def generate_test_case(
        self,
        name: str,
        url: str,
        selectors: Optional[Dict[str, str]] = None,
        expectations: Optional[Dict[str, Any]] = None,
        description: str = ""
    ) -> Dict[str, Any]:
        """生成单个测试用例数据"""
        return {
            "case_id": f"{name.upper()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "name": name,
            "url": url,
            "selectors": selectors or self.TEST_SELECTORS,
            "expectations": expectations or self.TEST_EXPECTATIONS["page_load"],
            "description": description,
            "created_at": datetime.now().isoformat(),
            "priority": "P1",
        }
    
    def generate_ecommerce_test_data(self) -> List[Dict[str, Any]]:
        """生成电商类测试数据"""
        return [
            self.generate_test_case(
                name="jd_homepage",
                url="https://www.jd.com",
                description="京东首页访问测试",
                expectations={
                    "has_search": True,
                    "has_categories": True,
                    "has_promotions": True,
                }
            ),
            self.generate_test_case(
                name="jd_search",
                url="https://www.jd.com",
                description="京东搜索功能测试",
                expectations={
                    "search_results_count": ">= 10",
                    "has_product_cards": True,
                    "has_price_info": True,
                }
            ),
        ]
    
    def generate_news_test_data(self) -> List[Dict[str, Any]]:
        """生成新闻类测试数据"""
        return [
            self.generate_test_case(
                name="news_homepage",
                url="https://news.baidu.com",
                description="新闻首页访问测试",
                expectations={
                    "has_headlines": True,
                    "has_categories": True,
                    "article_count": ">= 10",
                }
            ),
            self.generate_test_case(
                name="news_article",
                url="https://news.baidu.com",
                description="新闻文章提取测试",
                expectations={
                    "has_title": True,
                    "has_content": True,
                    "has_publish_time": True,
                    "has_author": True,
                }
            ),
        ]
    
    def generate_search_test_data(self) -> List[Dict[str, Any]]:
        """生成搜索类测试数据"""
        return [
            self.generate_test_case(
                name="baidu_search",
                url="https://www.baidu.com",
                description="百度搜索功能测试",
                expectations={
                    "search_box_visible": True,
                    "results_count": ">= 5",
                    "has_snippets": True,
                }
            ),
            self.generate_test_case(
                name="bing_search",
                url="https://www.bing.com",
                description="Bing 搜索功能测试",
                expectations={
                    "search_box_visible": True,
                    "results_count": ">= 5",
                    "has_snippets": True,
                }
            ),
        ]
    
    def generate_social_test_data(self) -> List[Dict[str, Any]]:
        """生成社交类测试数据"""
        return [
            self.generate_test_case(
                name="weibo_homepage",
                url="https://weibo.com",
                description="微博首页访问测试",
                expectations={
                    "has_feed": True,
                    "has_hot_topics": True,
                    "post_count": ">= 5",
                }
            ),
            self.generate_test_case(
                name="zhihu_homepage",
                url="https://www.zhihu.com",
                description="知乎首页访问测试",
                expectations={
                    "has_feed": True,
                    "has_hot_questions": True,
                    "post_count": ">= 5",
                }
            ),
        ]
    
    def generate_gov_test_data(self) -> List[Dict[str, Any]]:
        """生成政务类测试数据"""
        return [
            self.generate_test_case(
                name="gov_homepage",
                url="https://www.gov.cn",
                description="政府网站首页访问测试",
                expectations={
                    "has_news": True,
                    "has_policies": True,
                    "article_count": ">= 10",
                }
            ),
        ]
    
    def get_all_test_data(self) -> Dict[str, List[Dict[str, Any]]]:
        """获取所有测试数据"""
        return {
            "ecommerce": self.generate_ecommerce_test_data(),
            "news": self.generate_news_test_data(),
            "search": self.generate_search_test_data(),
            "social": self.generate_social_test_data(),
            "gov": self.generate_gov_test_data(),
        }
    
    def save_test_data(self, output_path: str, data: Optional[Dict[str, List[Dict]]] = None):
        """保存测试数据到 JSON 文件"""
        if data is None:
            data = self.get_all_test_data()
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        return output_path


# 全局生成器实例
generator = TestDataGenerator()


def get_generator() -> TestDataGenerator:
    """获取全局测试数据生成器实例"""
    return generator
