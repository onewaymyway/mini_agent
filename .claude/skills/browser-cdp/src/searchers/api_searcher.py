#!/usr/bin/env python3
"""
api_searcher.py - 通用 API 搜索器

支持 REST API 和 GraphQL API 的搜索，适用于：
- GitHub API v4 (GraphQL)
- Reddit API
- Hacker News API
- 其他 REST/GraphQL API 网站
"""
import asyncio
import json
import logging
import random
import time
from typing import List, Dict, Optional, Any
from urllib.parse import quote, urlencode

from src.searchers.base import BaseSearcher, SearcherConfig, SearchResult
from src.searchers.utils import random_delay

logger = logging.getLogger(__name__)


class APIConfig:
    """API 配置"""
    base_url: str = ""
    api_type: str = "rest"  # "rest" or "graphql"
    auth_type: str = "none"  # "none", "bearer", "basic"
    auth_token: Optional[str] = None
    headers: Dict[str, str] = {}
    rate_limit: int = 10  # 每秒请求数
    timeout: int = 30

    def __init__(self, base_url: str = "", api_type: str = "rest", auth_type: str = "none",
                 auth_token: Optional[str] = None, headers: Optional[Dict[str, str]] = None,
                 rate_limit: int = 10, timeout: int = 30):
        self.base_url = base_url
        self.api_type = api_type
        self.auth_type = auth_type
        self.auth_token = auth_token
        self.headers = headers or {}
        self.rate_limit = rate_limit
        self.timeout = timeout


class RESTAPISearcher(BaseSearcher):
    """REST API 搜索器"""
    
    def __init__(self, config: Optional[SearcherConfig] = None, api_config: Optional[APIConfig] = None):
        super().__init__(config)
        self.api_config = api_config or APIConfig()
        self._request_count = 0
        self._last_request_time = 0
    
    @property
    def source_name(self) -> str:
        return "api_rest"
    
    @property
    def supported_types(self) -> List[str]:
        return ["api_search", "graphql_search"]
    
    async def search(self, query: str, config: Optional[SearcherConfig] = None) -> List[SearchResult]:
        """执行 REST API 搜索"""
        cfg = config or self.config
        results = []
        
        try:
            # 构建请求 URL
            url = self._build_url(query, cfg)
            
            # 发送请求
            response = await self._make_request(url, cfg)
            
            # 解析响应
            results = self._parse_response(response, cfg)
            
        except Exception as e:
            logger.error(f"REST API 搜索失败: {e}")
        
        return results
    
    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取 API 详情"""
        cfg = config or self.config
        
        try:
            response = await self._make_request(url, cfg)
            return self._parse_response(response, cfg)
        except Exception as e:
            logger.error(f"获取 API 详情失败: {e}")
            return {}
    
    def _build_url(self, query: str, config: SearcherConfig) -> str:
        """构建 API 请求 URL"""
        base_url = self.api_config.base_url
        
        # 根据 API 类型构建 URL
        if "github" in base_url:
            return f"{base_url}/search/repositories?q={quote(query)}&sort=stars&order=desc&per_page={config.max_results}"
        elif "reddit" in base_url:
            return f"{base_url}/search.json?q={quote(query)}&limit={config.max_results}"
        elif "hackernews" in base_url:
            return f"{base_url}/search.json?q={quote(query)}&limit={config.max_results}"
        else:
            # 通用 REST API
            params = urlencode({
                "q": query,
                "limit": config.max_results,
                "offset": 0
            })
            return f"{base_url}?{params}"
    
    async def _make_request(self, url: str, config: SearcherConfig) -> Dict:
        """发送 HTTP 请求"""
        # 速率限制
        await self._throttle()
        
        # 构建请求头
        headers = {
            "Accept": "application/json",
            "User-Agent": random.choice(self.api_config.headers.get("User-Agent", ["Mozilla/5.0"])),
            **self.api_config.headers
        }
        
        # 添加认证
        if self.api_config.auth_type == "bearer" and self.api_config.auth_token:
            headers["Authorization"] = f"Bearer {self.api_config.auth_token}"
        elif self.api_config.auth_type == "basic" and self.api_config.auth_token:
            import base64
            headers["Authorization"] = f"Basic {base64.b64encode(self.api_config.auth_token.encode()).decode()}"
        
        # 使用 CDP 发送请求
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=config.wait_timeout) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    raise Exception(f"API 请求失败：{resp.status}")
    
    def _parse_response(self, response: Dict, config: SearcherConfig) -> List[SearchResult]:
        """解析 API 响应"""
        results = []
        
        # GitHub API 响应格式
        if "items" in response:
            for item in response["items"][:config.max_results]:
                results.append(SearchResult(
                    source=self.source_name,
                    title=item.get("name", item.get("title", "")),
                    url=item.get("html_url", item.get("url", "")),
                    snippet=item.get("description", ""),
                    metadata=item
                ))
        
        # Reddit API 响应格式
        elif "data" in response and "children" in response["data"]:
            for child in response["data"]["children"][:config.max_results]:
                post = child.get("data", {})
                results.append(SearchResult(
                    source=self.source_name,
                    title=post.get("title", ""),
                    url=post.get("url", f"https://reddit.com{post.get('permalink', '')}"),
                    snippet=post.get("selftext", "")[:200],
                    metadata=post
                ))
        
        # Hacker News API 响应格式
        elif "hits" in response:
            for item in response["hits"][:config.max_results]:
                results.append(SearchResult(
                    source=self.source_name,
                    title=item.get("title", ""),
                    url=item.get("url", f"https://news.ycombinator.com/item?id={item.get('objectID')}"),
                    snippet=item.get("story_text", "")[:200],
                    metadata=item
                ))
        
        # 通用格式
        else:
            for item in response.get("results", response.get("data", []))[:config.max_results]:
                results.append(SearchResult(
                    source=self.source_name,
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("snippet", item.get("description", "")),
                    metadata=item
                ))
        
        return results
    
    async def _throttle(self):
        """速率限制"""
        now = time.time()
        elapsed = now - self._last_request_time
        min_interval = 1.0 / self.api_config.rate_limit
        
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)
        
        self._last_request_time = time.time()
        self._request_count += 1


class GraphQLSearcher(BaseSearcher):
    """GraphQL API 搜索器"""
    
    def __init__(self, config: Optional[SearcherConfig] = None, api_config: Optional[APIConfig] = None):
        super().__init__(config)
        self.api_config = api_config or APIConfig(api_type="graphql")
        self._query_templates: Dict[str, str] = {}
    
    @property
    def source_name(self) -> str:
        return "api_graphql"
    
    @property
    def supported_types(self) -> List[str]:
        return ["graphql_search"]
    
    def register_query(self, name: str, query: str):
        """注册 GraphQL 查询模板"""
        self._query_templates[name] = query
    
    async def search(self, query: str, config: Optional[SearcherConfig] = None, query_name: str = "default") -> List[SearchResult]:
        """执行 GraphQL 搜索"""
        cfg = config or self.config
        results = []
        
        try:
            # 获取查询模板
            graphql_query = self._query_templates.get(query_name, self._get_default_query())
            
            # 构建请求体
            variables = {"search": query, "first": cfg.max_results}
            request_body = {
                "query": graphql_query,
                "variables": variables
            }
            
            # 发送请求
            response = await self._make_request(request_body, cfg)
            
            # 解析响应
            results = self._parse_response(response, cfg)
            
        except Exception as e:
            logger.error(f"GraphQL 搜索失败: {e}")
        
        return results
    
    def _get_default_query(self) -> str:
        """获取默认 GraphQL 查询"""
        return """
        query Search($search: String!, $first: Int!) {
          search(query: $search, type: MIXED, first: $first) {
            repository_count
            nodes {
              ... on Repository {
                name
                url
                description
                stargazer_count
              }
              ... on Issue {
                title
                url
                body
              }
            }
          }
        }
        """
    
    async def _make_request(self, body: Dict, config: SearcherConfig) -> Dict:
        """发送 GraphQL 请求"""
        await self._throttle()
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            **self.api_config.headers
        }
        
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.api_config.base_url,
                json=body,
                headers=headers,
                timeout=config.wait_timeout
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    raise Exception(f"GraphQL 请求失败：{resp.status}")
    
    def _parse_response(self, response: Dict, config: SearcherConfig) -> List[SearchResult]:
        """解析 GraphQL 响应"""
        results = []
        
        try:
            data = response.get("data", {})
            search = data.get("search", {})
            nodes = search.get("nodes", [])
            
            for node in nodes[:config.max_results]:
                if node.get("__typename") == "Repository":
                    results.append(SearchResult(
                        source=self.source_name,
                        title=node.get("name", ""),
                        url=node.get("url", ""),
                        snippet=node.get("description", ""),
                        metadata={"stars": node.get("stargazer_count", 0)}
                    ))
                elif node.get("__typename") == "Issue":
                    results.append(SearchResult(
                        source=self.source_name,
                        title=node.get("title", ""),
                        url=node.get("url", ""),
                        snippet=node.get("body", "")[:200],
                        metadata={"type": "issue"}
                    ))
        except Exception as e:
            logger.error(f"解析 GraphQL 响应失败: {e}")
        
        return results
    
    async def _throttle(self):
        """速率限制"""
        await asyncio.sleep(0.1)  # GraphQL 通常速率限制较宽松

    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取 GraphQL 查询详情"""
        return {"url": url, "type": "graphql"}


class APISearcherFactory:
    """API 搜索器工厂"""
    
    _searchers = {
        "github": {
            "rest": lambda: RESTAPISearcher(
                api_config=APIConfig(
                    base_url="https://api.github.com",
                    headers={"User-Agent": "Browser-CDP/1.0"}
                )
            ),
            "graphql": lambda: GraphQLSearcher(
                api_config=APIConfig(
                    base_url="https://api.github.com/graphql",
                    headers={"User-Agent": "Browser-CDP/1.0"}
                )
            )
        },
        "reddit": {
            "rest": lambda: RESTAPISearcher(
                api_config=APIConfig(
                    base_url="https://www.reddit.com",
                    headers={"User-Agent": "Browser-CDP/1.0"}
                )
            )
        },
        "hackernews": {
            "rest": lambda: RESTAPISearcher(
                api_config=APIConfig(
                    base_url="https://hn.algolia.com/api/v1",
                    headers={"User-Agent": "Browser-CDP/1.0"}
                )
            )
        }
    }
    
    @classmethod
    def create(cls, site: str, api_type: str = "rest") -> Optional[BaseSearcher]:
        """创建 API 搜索器"""
        site_lower = site.lower()
        for key, types in cls._searchers.items():
            if key in site_lower:
                searcher = types.get(api_type)
                if searcher:
                    return searcher()
        return None


# 预定义的 GitHub GraphQL 查询
GITHUB_QUERIES = {
    "search_repos": """
    query SearchRepos($query: String!, $first: Int!) {
      search(query: $query, type: REPOSITORY, first: $first) {
        repositoryCount
        nodes {
          ... on Repository {
            name
            fullName
            url
            description
            stargazerCount
            primaryLanguage {
              name
            }
          }
        }
      }
    }
    """,
    "search_issues": """
    query SearchIssues($query: String!, $first: Int!) {
      search(query: $query, type: ISSUE, first: $first) {
        issueCount
        nodes {
          ... on Issue {
            title
            url
            body
            state
            labels(first: 5) {
              nodes {
                name
              }
            }
          }
        }
      }
    }
    """
}