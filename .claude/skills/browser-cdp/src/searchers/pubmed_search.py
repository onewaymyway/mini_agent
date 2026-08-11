#!/usr/bin/env python
"""
pubmed_search.py - PubMed 生物医学文献搜索器

使用 browser-cdp skill 搜索 PubMed 文献，支持关键词搜索、作者搜索、期刊搜索。

用法:
    python pubmed_search.py --query "CRISPR gene editing" --max-results 20
    python pubmed_search.py --author "Smith J" --max-results 10
    python pubmed_search.py --journal "Nature" --max-results 15
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import quote

# 导入基础模块
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.searchers.base import SearcherConfig, SearchResult, BaseSearcher
from src.searchers.utils import (
    random_delay, get_random_ua, save_results, clean_text, truncate_text
)
from src.searchers.browser_utils import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


# ========== PubMed 专用配置 ==========
PUBMED_BASE = "https://pubmed.ncbi.nlm.nih.gov"
PUBMED_SEARCH_URL = "https://pubmed.ncbi.nlm.nih.gov/?term={query}&sort=relevance"
PUBMED_AUTHOR_URL = "https://pubmed.ncbi.nlm.nih.gov/?term={author}+Author"
PUBMED_JOURNAL_URL = "https://pubmed.ncbi.nlm.nih.gov/?term={journal}+Journal"


class PubMedSearcher(BaseSearcher):
    """PubMed 生物医学文献搜索器"""
    
    def __init__(self, config: Optional[SearcherConfig] = None):
        super().__init__(config)
        self._search_type = "query"  # query/author/journal
        self._extra_param = ""
    
    @property
    def source_name(self) -> str:
        return "pubmed"
    
    @property
    def supported_types(self) -> List[str]:
        return ["query", "author", "journal"]
    
    def search(
        self,
        query: str = "",
        search_type: str = "query",
        max_results: int = 20,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
        session_name: Optional[str] = "pubmed_session",
    ) -> List[Dict]:
        """搜索 PubMed 文献
        
        Args:
            query: 搜索关键词、作者名或期刊名
            search_type: 搜索类型（query/author/journal）
            max_results: 最大结果数
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间
            session_name: 浏览器会话名称
            
        Returns:
            文献数据列表
        """
        self._search_type = search_type
        self._extra_param = query
        
        print(f"[PubMed搜索] 类型: {search_type}, 关键词: {query}")
        
        # 确保浏览器连接
        if tab_id is None:
            result = ensure_browser(
                port=port,
                stealth=stealth,
                session_name=session_name,
                dedicated=True,
            )
            if result.get("error"):
                print(f"[错误] 浏览器启动失败: {result['error']}")
                return []
            tab_id = result.get("tab_id")
            port = result.get("port", port)
            print(f"[浏览器] 端口: {port}, Tab: {tab_id}")
        
        # 根据类型执行搜索
        if search_type == "query":
            results = self._search_articles(port, tab_id, query, max_results)
        elif search_type == "author":
            results = self._search_author(port, tab_id, query, max_results)
        elif search_type == "journal":
            results = self._search_journal(port, tab_id, query, max_results)
        else:
            results = self._search_articles(port, tab_id, query, max_results)
        
        # 保存结果
        if output_dir:
            save_results(results, output_dir, f"pubmed_{search_type}_{query[:20]}", "json")
            save_results(results, output_dir, f"pubmed_{search_type}_{query[:20]}", "csv")
        
        print(f"[完成] 共抓取 {len(results)} 篇文献")
        return results
    
    def _search_articles(self, port: int, tab_id: str, query: str, limit: int) -> List[Dict]:
        """搜索文献"""
        encoded_query = quote(query)
        url = PUBMED_SEARCH_URL.format(query=encoded_query)
        
        run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--goto", url,
            "--wait-for", "networkidle",
            "--timeout", str(wait_timeout),
        ])
        
        # 提取文献信息
        js_code = '''
        (function() {
            var results = [];
            var articles = document.querySelectorAll('.rslt .rsltpaper');
            
            articles.forEach(function(article, index) {
                if (index >= ''' + str(limit) + ''') return;
                
                // 标题
                var titleEl = article.querySelector('h2 a');
                if (!titleEl) return;
                
                var title = titleEl.textContent.trim();
                var url = titleEl.href || '';
                
                // PMID
                var pmidEl = article.querySelector('.rsltpmid');
                var pmid = pmidEl ? pmidEl.textContent.trim() : '';
                
                // 作者
                var authorEl = article.querySelector('.rsltauthor');
                var authors = authorEl ? authorEl.textContent.trim() : '';
                
                // 期刊信息
                var journalEl = article.querySelector('.rsltpub');
                var journal_info = journalEl ? journalEl.textContent.trim() : '';
                
                // 摘要
                var abstractEl = article.querySelector('.rsltabs');
                var abstract = abstractEl ? abstractEl.textContent.trim() : '';
                
                // 发表日期
                var dateEl = article.querySelector('.rsltpubdate');
                var pub_date = dateEl ? dateEl.textContent.trim() : '';
                
                results.push({
                    title: title,
                    url: url,
                    pmid: pmid,
                    authors: authors,
                    journal_info: journal_info,
                    abstract: truncate_text(abstract, 300),
                    pub_date: pub_date,
                    source: 'pubmed',
                    scraped_at: new Date().toISOString()
                });
            });
            
            return results;
        })()
        '''
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--eval", js_code,
        ])
        
        try:
            data = json.loads(result.get("result", "[]"))
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            print(f"[警告] JSON 解析失败: {result.get('result')}")
            return []
    
    def _search_author(self, port: int, tab_id: str, author: str, limit: int) -> List[Dict]:
        """搜索作者文献"""
        encoded_author = quote(author)
        url = PUBMED_AUTHOR_URL.format(author=encoded_author)
        
        run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--goto", url,
            "--wait-for", "networkidle",
            "--timeout", str(wait_timeout),
        ])
        
        return self._search_articles(port, tab_id, author, limit)
    
    def _search_journal(self, port: int, tab_id: str, journal: str, limit: int) -> List[Dict]:
        """搜索期刊文献"""
        encoded_journal = quote(journal)
        url = PUBMED_JOURNAL_URL.format(journal=encoded_journal)
        
        run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--goto", url,
            "--wait-for", "networkidle",
            "--timeout", str(wait_timeout),
        ])
        
        return self._search_articles(port, tab_id, journal, limit)
    
    def get_article_detail(self, pmid: str, port: int, tab_id: str) -> Dict:
        """获取文章详情"""
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        
        run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--goto", url,
            "--wait-for", "networkidle",
            "--timeout", str(wait_timeout),
        ])
        
        js_code = '''
        (function() {
            var article = {};
            
            // 标题
            var titleEl = document.querySelector('.docsum-title');
            article.title = titleEl ? titleEl.textContent.trim() : '';
            
            // 摘要
            var abstractEl = document.querySelector('.abstract-content');
            article.abstract = abstractEl ? abstractEl.textContent.trim() : '';
            
            // 作者
            var authorsEl = document.querySelector('.auth-list');
            article.authors = authorsEl ? authorsEl.textContent.trim() : '';
            
            // 期刊信息
            var journalEl = document.querySelector('.journal-info');
            article.journal_info = journalEl ? journalEl.textContent.trim() : '';
            
            // 关键词
            var keywordsEl = document.querySelector('.mesh-terms');
            article.keywords = keywordsEl ? keywordsEl.textContent.trim() : '';
            
            article.pmid = ''' + json.dumps(pmid) + ''';
            article.url = window.location.href;
            article.source = 'pubmed';
            article.scraped_at = new Date().toISOString();
            
            return article;
        })()
        '''
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--eval", js_code,
        ])
        
        try:
            return json.loads(result.get("result", "{}"))
        except json.JSONDecodeError:
            return {}
    
    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取文章详情"""
        raise NotImplementedError("请使用 get_article_detail 方法")
    
    async def health_check(self) -> bool:
        """健康检查"""
        try:
            result = run_cmd([
                PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
                "--goto", PUBMED_BASE,
                "--wait-for", "stable",
                "--timeout", 10,
            ])
            return "error" not in result
        except Exception:
            return False


def main():
    parser = argparse.ArgumentParser(description="PubMed 生物医学文献搜索器")
    parser.add_argument("query", help="搜索关键词、作者名或期刊名")
    parser.add_argument("--type", default="query", choices=["query", "author", "journal"])
    parser.add_argument("--max-results", type=int, default=20)
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--stealth", action="store_true", default=True)
    parser.add_argument("--no-stealth", dest="stealth", action="store_false")
    parser.add_argument("--output-dir", help="输出目录")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--session", default="pubmed_session", help="浏览器会话名称")
    
    args = parser.parse_args()
    
    searcher = PubMedSearcher()
    results = searcher.search(
        query=args.query,
        search_type=args.type,
        max_results=args.max_results,
        port=args.port,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.timeout,
        session_name=args.session,
    )
    
    if results:
        print("\n" + json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
