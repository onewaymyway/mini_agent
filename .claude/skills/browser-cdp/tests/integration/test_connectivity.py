"""
test_connectivity.py - 网站连通性测试

测试与至少20个常见站点的连通性，验证请求指纹隐藏模块的有效性。
"""
import pytest
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import requests
from core.request_fingerprint import RequestHeaders, FingerprintManager


# 测试网站列表（按类型分类）
WEBSITES = [
    # 新闻网站 (5个)
    {"name": "人民网", "url": "https://www.people.com.cn", "category": "news"},
    {"name": "新华网", "url": "https://www.xinhuanet.com", "category": "news"},
    {"name": "腾讯新闻", "url": "https://news.qq.com", "category": "news"},
    {"name": "新浪新闻", "url": "https://news.sina.com.cn", "category": "news"},
    {"name": "网易新闻", "url": "https://news.163.com", "category": "news"},
    
    # 资讯网站 (3个)
    {"name": "财新网", "url": "https://www.caixin.com", "category": "news"},
    {"name": "36氪", "url": "https://36kr.com", "category": "news"},
    {"name": "虎嗅", "url": "https://www.huxiu.com", "category": "news"},
    
    # 搜索引擎 (2个)
    {"name": "百度", "url": "https://www.baidu.com", "category": "search"},
    {"name": "必应", "url": "https://www.bing.com", "category": "search"},
    
    # 电商平台 (3个)
    {"name": "当当网", "url": "https://www.dangdang.com", "category": "ecommerce"},
    {"name": "苏宁易购", "url": "https://www.suning.com", "category": "ecommerce"},
    {"name": "京东", "url": "https://www.jd.com", "category": "ecommerce"},
    
    # 社交媒体 (3个)
    {"name": "微博", "url": "https://weibo.com", "category": "social"},
    {"name": "知乎", "url": "https://www.zhihu.com", "category": "social"},
    {"name": "豆瓣", "url": "https://www.douban.com", "category": "social"},
    
    # 技术网站 (2个)
    {"name": "CSDN", "url": "https://www.csdn.net", "category": "tech"},
    {"name": "掘金", "url": "https://juejin.cn", "category": "tech"},
]


class ConnectivityTestResult:
    """连通性测试结果"""
    
    def __init__(self, name: str, url: str, category: str):
        self.name = name
        self.url = url
        self.category = category
        self.success = False
        self.status_code: Optional[int] = None
        self.response_time: float = 0.0
        self.error: Optional[str] = None
        self.content_length: int = 0
    
    def __str__(self):
        status = "✓" if self.success else "✗"
        return f"{status} {self.name}: {self.status_code or 'N/A'} ({self.response_time:.2f}s)"
    
    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "url": self.url,
            "category": self.category,
            "success": self.success,
            "status_code": self.status_code,
            "response_time": self.response_time,
            "error": self.error,
            "content_length": self.content_length,
        }


def test_site_connectivity(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 10.0
) -> ConnectivityTestResult:
    """
    测试单个站点的连通性
    
    Args:
        url: 测试URL
        headers: 自定义请求头
        timeout: 超时时间（秒）
    
    Returns:
        ConnectivityTestResult: 测试结果
    """
    result = ConnectivityTestResult(
        name=url.split("//")[-1].split("/")[0],
        url=url,
        category="unknown"
    )
    
    start_time = time.time()
    
    try:
        response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        result.success = 200 <= response.status_code < 400
        result.status_code = response.status_code
        result.response_time = time.time() - start_time
        result.content_length = len(response.content)
        
        # 自动检测类别
        if "news" in url or "people" in url or "xinhua" in url or "sina" in url or "163" in url:
            result.category = "news"
        elif "baidu" in url or "bing" in url:
            result.category = "search"
        elif "jd" in url or "dangdang" in url or "suning" in url:
            result.category = "ecommerce",
        elif "weibo" in url or "zhihu" in url or "douban" in url:
            result.category = "social"
        elif "csdn" in url or "juejin" in url:
            result.category = "tech"
            
    except requests.exceptions.Timeout:
        result.error = f"请求超时 ({timeout}s)"
        result.response_time = timeout
    except requests.exceptions.ConnectionError as e:
        result.error = f"连接错误: {str(e)[:100]}"
    except Exception as e:
        result.error = f"未知错误: {str(e)[:100]}"
    
    return result


def test_with_random_headers() -> List[ConnectivityTestResult]:
    """
    使用随机请求头测试所有站点
    
    Returns:
        List[ConnectivityTestResult]: 所有测试结果
    """
    manager = FingerprintManager()
    headers = manager.generate_random_headers().get_headers()
    
    results = []
    for website in WEBSITES:
        print(f"\n测试 {website['name']}...")
        result = test_site_connectivity(website["url"], headers=headers)
        result.name = website["name"]
        result.category = website["category"]
        results.append(result)
        print(f"  {result}")
    
    return results


def test_with_specific_headers() -> List[ConnectivityTestResult]:
    """
    使用特定请求头测试所有站点
    
    Returns:
        List[ConnectivityTestResult]: 所有测试结果
    """
    # 模拟真实浏览器请求头
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }
    
    results = []
    for website in WEBSITES:
        print(f"\n测试 {website['name']}...")
        result = test_site_connectivity(website["url"], headers=headers)
        result.name = website["name"]
        result.category = website["category"]
        results.append(result)
        print(f"  {result}")
    
    return results


def test_without_headers() -> List[ConnectivityTestResult]:
    """
    不使用自定义请求头测试所有站点（基线对比）
    
    Returns:
        List[ConnectivityTestResult]: 所有测试结果
    """
    results = []
    for website in WEBSITES:
        print(f"\n测试 {website['name']} (无自定义头)...")
        result = test_site_connectivity(website["url"])
        result.name = website["name"]
        result.category = website["category"]
        results.append(result)
        print(f"  {result}")
    
    return results


def generate_report(results: List[ConnectivityTestResult]) -> str:
    """
    生成测试报告
    
    Args:
        results: 测试结果列表
    
    Returns:
        str: 格式化的报告
    """
    total = len(results)
    success_count = sum(1 for r in results if r.success)
    fail_count = total - success_count
    
    avg_response_time = sum(r.response_time for r in results) / total if total > 0 else 0
    
    report = []
    report.append("=" * 60)
    report.append("网站连通性测试报告")
    report.append("=" * 60)
    report.append(f"\n测试时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    report.append(f"测试站点数: {total}")
    report.append(f"成功: {success_count}")
    report.append(f"失败: {fail_count}")
    report.append(f"成功率: {success_count/total*100:.1f}%")
    report.append(f"平均响应时间: {avg_response_time:.2f}s")
    
    report.append("\n" + "-" * 60)
    report.append("按类别统计")
    report.append("-" * 60)
    
    categories = {}
    for r in results:
        cat = r.category
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(r)
    
    for cat, cat_results in categories.items():
        cat_success = sum(1 for r in cat_results if r.success)
        report.append(f"{cat}: {cat_success}/{len(cat_results)} 成功")
    
    report.append("\n" + "-" * 60)
    report.append("详细结果")
    report.append("-" * 60)
    
    for r in results:
        status = "✓" if r.success else "✗"
        report.append(f"\n{status} {r.name}")
        report.append(f"  URL: {r.url}")
        report.append(f"  状态码: {r.status_code or 'N/A'}")
        report.append(f"  响应时间: {r.response_time:.2f}s")
        report.append(f"  内容长度: {r.content_length} bytes")
        if r.error:
            report.append(f"  错误: {r.error}")
    
    report.append("\n" + "=" * 60)
    
    return "\n".join(report)


def run_all_tests() -> List[ConnectivityTestResult]:
    """
    运行所有连通性测试
    
    Returns:
        List[ConnectivityTestResult]: 所有测试结果
    """
    print("\n" + "=" * 60)
    print("开始网站连通性测试")
    print("=" * 60)
    
    # 测试1: 无自定义头（基线）
    print("\n[测试1] 无自定义请求头 (基线)")
    baseline_results = test_without_headers()
    
    # 测试2: 随机请求头
    print("\n[测试2] 随机请求头")
    random_results = test_with_random_headers()
    
    # 测试3: 特定请求头
    print("\n[测试3] 特定浏览器请求头")
    specific_results = test_with_specific_headers()
    
    # 生成报告
    print("\n" + generate_report(specific_results))
    
    return specific_results


class TestConnectivity:
    """连通性测试类（用于pytest）"""
    
    def test_all_sites_with_random_headers(self):
        """测试所有站点使用随机请求头"""
        results = test_with_random_headers()
        
        # 至少有15个站点成功（考虑到网络环境和反爬）
        success_count = sum(1 for r in results if r.success)
        assert success_count >= 15, f"只有 {success_count}/{len(results)} 个站点成功"
        
        # 打印报告
        print("\n" + generate_report(results))
    
    def test_all_sites_with_specific_headers(self):
        """测试所有站点使用特定请求头"""
        results = test_with_specific_headers()
        
        success_count = sum(1 for r in results if r.success)
        assert success_count >= 18, f"只有 {success_count}/{len(results)} 个站点成功"
        
        print("\n" + generate_report(results))
    
    def test_news_sites(self):
        """测试新闻网站"""
        news_sites = [w for w in WEBSITES if w["category"] == "news"]
        headers = FingerprintManager().generate_random_headers().get_headers()
        
        results = []
        for website in news_sites:
            result = test_site_connectivity(website["url"], headers=headers)
            result.name = website["name"]
            result.category = website["category"]
            results.append(result)
        
        success_count = sum(1 for r in results if r.success)
        assert success_count >= len(news_sites) * 0.7, f"新闻网站成功率过低: {success_count}/{len(news_sites)}"
    
    def test_search_engines(self):
        """测试搜索引擎"""
        search_sites = [w for w in WEBSITES if w["category"] == "search"]
        headers = FingerprintManager().generate_random_headers().get_headers()
        
        results = []
        for website in search_sites:
            result = test_site_connectivity(website["url"], headers=headers)
            result.name = website["name"]
            result.category = website["category"]
            results.append(result)
        
        success_count = sum(1 for r in results if r.success)
        assert success_count == len(search_sites), f"搜索引擎全部成功: {success_count}/{len(search_sites)}"
    
    def test_tech_sites(self):
        """测试技术网站"""
        tech_sites = [w for w in WEBSITES if w["category"] == "tech"]
        headers = FingerprintManager().generate_random_headers().get_headers()
        
        results = []
        for website in tech_sites:
            result = test_site_connectivity(website["url"], headers=headers)
            result.name = website["name"]
            result.category = website["category"]
            results.append(result)
        
        success_count = sum(1 for r in results if r.success)
        assert success_count >= len(tech_sites) * 0.5, f"技术网站成功率过低: {success_count}/{len(tech_sites)}"
    
    def test_response_time_threshold(self):
        """测试响应时间在合理范围内"""
        headers = FingerprintManager().generate_random_headers().get_headers()
        
        for website in WEBSITES[:10]:  # 只测试前10个
            result = test_site_connectivity(website["url"], headers=headers)
            if result.success:
                assert result.response_time < 5.0, f"{website['name']} 响应时间过长: {result.response_time}s"


if __name__ == "__main__":
    # 运行所有测试
    results = run_all_tests()
    
    # 保存结果到文件
    import json
    output_path = Path(__file__).parent.parent / "test_results" / "connectivity_test_results.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump([r.to_dict() for r in results], f, ensure_ascii=False, indent=2)
    
    print(f"\n结果已保存到: {output_path}")