"""改进版知乎搜索工具 - 使用百度搜索 site:zhihu.com 直接获取结果"""

import json
import re
import time
import urllib.parse
from pathlib import Path
from typing import List, Dict, Optional
import httpx


class ZhihuSearchSimple:
    """简化的知乎搜索器 - 通过百度搜索 site:zhihu.com 获取结果"""
    
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        self.client = httpx.Client(headers=self.headers, timeout=30.0)
    
    def search(self, query: str, max_results: int = 10) -> List[Dict]:
        """搜索知乎问题
        
        Args:
            query: 搜索关键词
            max_results: 最大结果数
            
        Returns:
            搜索结果列表，每个结果包含 title, url, snippet
        """
        # 构建百度搜索 URL，限定知乎域名
        encoded_query = urllib.parse.quote(f"site:zhihu.com {query}")
        search_url = f"https://www.baidu.com/s?wd={encoded_query}&rn={max_results}"
        
        print(f"  搜索 URL: {search_url[:100]}...")
        
        try:
            response = self.client.get(search_url)
            response.raise_for_status()
            
            # 解析搜索结果
            results = self._parse_baidu_results(response.text, max_results)
            
            # 过滤出知乎问题链接
            question_results = []
            for r in results:
                if 'zhihu.com/question' in r['url']:
                    question_results.append(r)
                elif 'zhuanlan.zhihu.com' in r['url']:
                    # 专栏文章也保留
                    r['type'] = 'column'
                    question_results.append(r)
            
            print(f"  找到 {len(question_results)} 个知乎相关结果")
            return question_results[:max_results]
            
        except Exception as e:
            print(f"  搜索失败：{e}")
            return []
    
    def _parse_baidu_results(self, html: str, max_results: int) -> List[Dict]:
        """解析百度搜索结果的 HTML"""
        results = []
        
        # 尝试多种解析策略
        # 策略 1: 解析标准搜索结果
        pattern1 = r'<h3[^>]*class="[^"]*title[^"]*"[^>]*><a[^>]*href="([^"]+)"[^>]*>([^<]+)</a></h3>'
        pattern2 = r'<div[^>]*class="[^"]*c-abstract[^"]*"[^>]*>([^<]+)</div>'
        
        # 查找标题和链接
        title_matches = re.findall(pattern1, html, re.IGNORECASE)
        snippet_matches = re.findall(pattern2, html, re.IGNORECASE)
        
        for i, (title_html, url) in enumerate(title_matches):
            if i >= max_results:
                break
            
            # 清理标题 HTML
            title = re.sub(r'<[^>]+>', '', title_html).strip()
            if not title:
                continue
            
            # 获取摘要
            snippet = snippet_matches[i] if i < len(snippet_matches) else ""
            snippet = re.sub(r'<[^>]+>', '', snippet).strip()
            
            # 清理文本
            title = self._clean_text(title)
            snippet = self._clean_text(snippet)
            
            results.append({
                "title": title,
                "url": url,
                "snippet": snippet[:200] if snippet else "",
                "type": "question" if "question" in url else "other"
            })
        
        # 策略 2: 如果策略 1 失败，尝试解析 JSON 数据
        if not results:
            json_pattern = r'"content":\s*(\[.+?\]),'
            json_match = re.search(json_pattern, html, re.DOTALL)
            if json_match:
                try:
                    data = json.loads(json_match.group(1))
                    for item in data[:max_results]:
                        if isinstance(item, dict):
                            results.append({
                                "title": item.get("title", ""),
                                "url": item.get("url", ""),
                                "snippet": item.get("abstract", "")[:200],
                                "type": "question"
                            })
                except:
                    pass
        
        return results
    
    def _clean_text(self, text: str) -> str:
        """清理文本内容"""
        # 移除多余空格和换行
        text = re.sub(r'\s+', ' ', text)
        # 移除特殊字符
        text = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', text)
        return text.strip()


def main():
    """测试搜索功能"""
    import sys
    
    if len(sys.argv) < 2:
        print("用法：python zhihu_search_simple.py <关键词> [最大结果数]")
        sys.exit(1)
    
    query = sys.argv[1]
    max_results = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    
    print(f"="*60)
    print(f"知乎搜索 (简化版)")
    print(f"关键词：{query}")
    print(f"最大结果：{max_results}")
    print(f"="*60)
    
    searcher = ZhihuSearchSimple()
    results = searcher.search(query, max_results)
    
    print(f"\n搜索结果:")
    for i, r in enumerate(results, 1):
        print(f"\n{i}. {r['title']}")
        print(f"   URL: {r['url']}")
        if r.get('snippet'):
            print(f"   摘要：{r['snippet'][:100]}...")
    
    # 保存结果
    output_dir = Path("temp/zhihu_search_results")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    output_file = output_dir / f"zhihu_search_{query.replace(' ', '_')}.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"\n结果已保存到：{output_file}")


if __name__ == "__main__":
    main()