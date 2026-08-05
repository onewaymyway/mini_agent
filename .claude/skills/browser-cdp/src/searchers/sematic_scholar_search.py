#!/usr/bin/env python3
"""
Semantic Scholar 学术论文搜索器
优先使用 API，稳定且快速
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime
import argparse

# 添加父目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.searchers.base import BaseSearcher, SearcherConfig
from src.searchers.utils import save_results, print_results


class SematicScholarSearcher(BaseSearcher):
    """Semantic Scholar 学术论文搜索器"""
    
    @property
    def source_name(self) -> str:
        return "sematic_scholar"
    
    async def search(self, query: str, config: SearcherConfig) -> List[Dict]:
        """搜索论文（优先使用 API）"""
        try:
            import aiohttp
            
            # Semantic Scholar API
            url = "https://api.semanticscholar.org/graph/v1/paper/search"
            params = {
                "query": query,
                "limit": config.max_results,
                "fields": "title,authors,year,citationCount,abstract,tldr,publicationTypes,externalIds"
            }
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers, timeout=30) as resp:
                    if resp.status != 200:
                        print(f"API 错误: {resp.status}")
                        return []
                    
                    data = await resp.json()
                    papers = data.get("data", [])
                    
                    results = []
                    for paper in papers:
                        authors = [a.get("name", "") for a in paper.get("authors", [])]
                        
                        results.append({
                            "title": paper.get("title", ""),
                            "url": f"https://www.semanticscholar.org/paper/{paper.get('paperId', '')}",
                            "authors": authors,
                            "year": paper.get("year"),
                            "citation_count": paper.get("citationCount", 0),
                            "abstract": paper.get("abstract", "")[:500] if paper.get("abstract") else "",
                            "source": self.source_name,
                            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        })
                    
                    return results
                    
        except Exception as e:
            print(f"搜索失败: {e}")
            return []
    
    async def get_detail(self, url: str, config: SearcherConfig) -> Dict:
        """获取论文详情"""
        try:
            import aiohttp
            
            # 从 URL 提取 paperId
            paper_id = url.split("/")[-1]
            
            url = f"https://api.semanticscholar.org/graph/v1/paper/{paper_id}?fields=title,authors,year,citationCount,abstract,tldr"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=30) as resp:
                    if resp.status != 200:
                        return {}
                    
                    data = await resp.json()
                    
                    return {
                        "title": data.get("title", ""),
                        "url": url,
                        "authors": [a.get("name", "") for a in data.get("authors", [])],
                        "year": data.get("year"),
                        "citation_count": data.get("citationCount", 0),
                        "abstract": data.get("abstract", ""),
                        "source": self.source_name
                    }
                    
        except Exception as e:
            print(f"获取详情失败: {e}")
            return {}


def main():
    parser = argparse.ArgumentParser(description="Semantic Scholar 论文搜索")
    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--max-results", type=int, default=10, help="最大结果数量")
    parser.add_argument("--output-dir", default="./search_results/sematic_scholar", help="输出目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    
    args = parser.parse_args()
    
    async def run():
        searcher = SematicScholarSearcher()
        config = SearcherConfig(
            max_results=args.max_results,
            output_dir=args.output_dir
        )
        
        results = await searcher.search(args.query, config)
        
        if args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            print_results(results)
        
        if results:
            save_results(results, args.output_dir, "sematic_scholar")
            print(f"\n结果已保存到: {args.output_dir}")
    
    asyncio.run(run())


if __name__ == "__main__":
    main()
