#!/usr/bin/env python
"""
huggingface_search.py - Hugging Face AI模型与博客抓取脚本

通过官方API直接获取Hugging Face模型库、数据集和博客数据。
无需浏览器，直接使用REST API，稳定性高。

用法:
    python huggingface_search.py --type models --query "gpt" --limit 20
    python huggingface_search.py --type blog --limit 10
    python huggingface_search.py --type datasets --query "llm" --output-dir ./hf_results
    python huggingface_search.py --type model --detail "mistralai/Mistral-7B-Instruct-v0.2"

示例:
    python huggingface_search.py --type models --sort downloads --limit 50
    python huggingface_search.py --type blog --limit 20 --output-dir ./hf_blog
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import quote

try:
    import requests
except ImportError:
    print("[错误] 需要安装依赖: pip install requests")
    sys.exit(1)


class HuggingFaceSearcher:
    """Hugging Face API搜索器"""
    
    BASE_URL = "https://huggingface.co"
    API_BASE = f"{BASE_URL}/api"
    
    # 请求头（模拟浏览器）
    HEADERS = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Authorization": "Bearer "  # 可选token
    }
    
    def __init__(self, token: str = None):
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        self.timeout = 30
        self.rate_limit_delay = 0.5  # 请求间隔
    
    def search(
        self,
        query: str = None,
        type: str = "models",
        limit: int = 20,
        sort: str = None,
        output_dir: str = None,
        detail: str = None
    ) -> Dict:
        """
        搜索Hugging Face数据
        
        Args:
            query: 搜索关键词
            type: 数据类型 (models/blog/datasets/models)
            limit: 结果数量
            sort: 排序方式 (downloads/likes/lastModified)
            output_dir: 输出目录
            detail: 获取详情的实体ID
        
        Returns:
            搜索结果字典
        """
        results = {
            "source": "huggingface",
            "type": type,
            "query": query,
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "count": 0,
            "data": []
        }
        
        try:
            if detail:
                # 获取详情
                item = self._get_detail(type, detail)
                if item:
                    results["data"].append(item)
                    results["count"] = 1
            else:
                # 获取列表
                items = self._fetch_list(type, query, limit, sort)
                results["data"] = items
                results["count"] = len(items)
            
            # 保存结果
            if output_dir and results["data"]:
                self._save_results(results, output_dir, type)
            
            print(f"[结果] 共获取 {results['count']} 条{type}数据")
            
        except Exception as e:
            results["error"] = str(e)
            print(f"[错误] {e}")
        
        return results
    
    def _fetch_list(
        self,
        type: str,
        query: str = None,
        limit: int = 20,
        sort: str = None
    ) -> List[Dict]:
        """获取列表数据"""
        endpoint = f"{self.API_BASE}/{type}"
        params = {"limit": min(limit, 100)}
        
        if query:
            params["search"] = query
        if sort:
            params["sort"] = sort
        
        try:
            response = self.session.get(endpoint, params=params, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
            
            # 解析数据
            items = []
            for item in data:
                parsed = self._parse_item(type, item)
                if parsed:
                    items.append(parsed)
            
            return items
            
        except Exception as e:
            print(f"[API] 获取列表失败: {e}")
            return []
    
    def _get_detail(self, type: str, entity_id: str) -> Optional[Dict]:
        """获取单个实体详情"""
        endpoint = f"{self.API_BASE}/{type}/{entity_id}"
        
        try:
            response = self.session.get(endpoint, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
            
            return self._parse_item(type, data)
            
        except Exception as e:
            print(f"[API] 获取详情失败: {e}")
            return None
    
    def _parse_item(self, type: str, data: Dict) -> Optional[Dict]:
        """解析单个条目"""
        try:
            if type == "models":
                return {
                    "id": data.get("modelId") or data.get("id"),
                    "title": data.get("modelId") or data.get("config", {}).get("model_name"),
                    "author": data.get("author"),
                    "downloads": data.get("downloads", 0),
                    "likes": data.get("likes", 0),
                    "tags": data.get("tags", []),
                    "pipeline_tag": data.get("pipeline_tag"),
                    "created_at": data.get("createdAt"),
                    "updated_at": data.get("lastModified"),
                    "url": f"https://huggingface.co/{data.get('modelId') or data.get('id')}",
                    "description": data.get("cardData", {}).get("model-index", [{}])[0].get("modelName", "") if data.get("cardData") else ""
                }
            elif type == "datasets":
                return {
                    "id": data.get("id"),
                    "title": data.get("id"),
                    "author": data.get("author"),
                    "downloads": data.get("downloads", 0),
                    "likes": data.get("likes", 0),
                    "tags": data.get("tags", []),
                    "created_at": data.get("createdAt"),
                    "updated_at": data.get("lastModified"),
                    "url": f"https://huggingface.co/datasets/{data.get('id')}",
                    "description": data.get("description", "")
                }
            elif type == "blog":
                return {
                    "id": data.get("slug"),
                    "title": data.get("title"),
                    "excerpt": data.get("excerpt", ""),
                    "author": data.get("author", {}).get("username"),
                    "published_at": data.get("publishedAt"),
                    "tags": data.get("tags", []),
                    "url": f"https://huggingface.co/blog/{data.get('slug')}",
                    "cover_image": data.get("coverImage")
                }
            elif type == "spaces":
                return {
                    "id": data.get("id"),
                    "title": data.get("id"),
                    "author": data.get("author"),
                    "likes": data.get("likes", 0),
                    "sdk": data.get("sdk"),
                    "url": f"https://huggingface.co/spaces/{data.get('id')}",
                    "description": data.get("description", "")
                }
        except Exception as e:
            print(f"[解析] 解析失败: {e}")
            return None
        
        return None
    
    def _save_results(self, results: Dict, output_dir: str, type: str):
        """保存结果到文件"""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_file = output_path / f"hf_{type}_{timestamp}.json"
        md_file = output_path / f"hf_{type}_{timestamp}.md"
        
        # 保存JSON
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        
        # 保存Markdown
        with open(md_file, 'w', encoding='utf-8') as f:
            f.write(f"# Hugging Face {type.capitalize()} - {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
            f.write(f"共获取 {results['count']} 条数据\n\n")
            
            for i, item in enumerate(results['data'], 1):
                f.write(f"## {i}. {item.get('title', item.get('id', 'N/A'))}\n\n")
                f.write(f"- **ID**: {item.get('id')}")
                if item.get('author'):
                    f.write(f" | **作者**: {item.get('author')}")
                f.write("\n")
                
                if item.get('downloads'):
                    f.write(f"- **下载数**: {item.get('downloads'):,}\n")
                if item.get('likes'):
                    f.write(f"- **点赞数**: {item.get('likes'):,}\n")
                
                tags = item.get('tags', [])
                if tags:
                    f.write(f"- **标签**: {', '.join(tags[:5])}\n")
                
                url = item.get('url', '')
                if url:
                    f.write(f"- **链接**: [{url}]({url})\n")
                
                desc = item.get('description') or item.get('excerpt', '')
                if desc:
                    f.write(f"- **描述**: {desc[:300]}...\n")
                
                f.write("\n---\n\n")
        
        print(f"[保存] 结果已保存到: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Hugging Face AI模型与博客搜索")
    parser.add_argument("--type", type=str, default="models",
                        choices=["models", "datasets", "blog", "spaces"],
                        help="数据类型 (默认: models)")
    parser.add_argument("--query", type=str, help="搜索关键词")
    parser.add_argument("--limit", type=int, default=20, help="结果数量 (默认: 20)")
    parser.add_argument("--sort", type=str, default=None,
                        choices=["downloads", "likes", "lastModified"],
                        help="排序方式")
    parser.add_argument("--output-dir", type=str, default="./search_results/huggingface",
                        help="输出目录")
    parser.add_argument("--detail", type=str, help="获取单个实体详情（实体ID）")
    parser.add_argument("--token", type=str, help="Hugging Face Token（可选，提升限流）")
    
    args = parser.parse_args()
    
    # 创建搜索器
    searcher = HuggingFaceSearcher(token=args.token)
    
    # 执行搜索
    results = searcher.search(
        query=args.query,
        type=args.type,
        limit=args.limit,
        sort=args.sort,
        output_dir=args.output_dir,
        detail=args.detail
    )
    
    # 输出JSON结果
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
