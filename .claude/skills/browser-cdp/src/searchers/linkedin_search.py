#!/usr/bin/env python
"""
linkedin_search.py - LinkedIn 职业搜索器

使用 browser-cdp skill 搜索 LinkedIn 职位、公司和人。
LinkedIn 需要登录态，基础搜索可用。

用法:
    python linkedin_search.py --query "python developer" --max-results 10
    python linkedin_search.py --company "google" --max-results 5
    python linkedin_search.py --person "john doe" --max-results 10
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
from src.searchers.utils import random_delay, save_results, dedup_results
from src.searchers.browser_utils import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


# ========== LinkedIn 专用配置 ==========
LINKEDIN_BASE = "https://www.linkedin.com"
LINKEDIN_SEARCH_URL = "https://www.linkedin.com/jobs/search?keywords={query}"
LINKEDIN_COMPANY_URL = "https://www.linkedin.com/company/{company}"
LINKEDIN_PEOPLE_URL = "https://www.linkedin.com/search/results/people/?keywords={query}"


class LinkedInSearcher(BaseSearcher):
    """LinkedIn 职业搜索器"""
    
    def __init__(self, config: Optional[SearcherConfig] = None):
        super().__init__(config)
        self._search_type = "jobs"  # jobs/company/people
        self._extra_param = ""
    
    @property
    def source_name(self) -> str:
        return "linkedin"
    
    @property
    def supported_types(self) -> List[str]:
        return ["jobs", "company", "people"]
    
    def search(
        self,
        query: str = "",
        search_type: str = "jobs",
        max_results: int = 10,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
        session_name: Optional[str] = "linkedin_session",
    ) -> List[Dict]:
        """搜索 LinkedIn 内容"""
        self._search_type = search_type
        self._extra_param = query
        
        print(f"[LinkedIn] 搜索类型: {search_type}, 关键词: {query}")
        
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
        if search_type == "jobs":
            results = self._search_jobs(port, tab_id, query, max_results)
        elif search_type == "company":
            results = self._search_company(port, tab_id, query, max_results)
        elif search_type == "people":
            results = self._search_people(port, tab_id, query, max_results)
        else:
            results = self._search_jobs(port, tab_id, query, max_results)
        
        # 保存结果
        if output_dir:
            save_results(results, output_dir, f"linkedin_{search_type}_{query[:20]}", "json")
            save_results(results, output_dir, f"linkedin_{search_type}_{query[:20]}", "csv")
        
        print(f"[完成] 共抓取 {len(results)} 条结果")
        return results
    
    def _search_jobs(self, port: int, tab_id: str, query: str, limit: int) -> List[Dict]:
        """搜索职位"""
        encoded_query = quote(query)
        url = LINKEDIN_SEARCH_URL.format(query=encoded_query)
        
        run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--goto", url,
            "--wait-for", "networkidle",
            "--timeout", str(wait_timeout),
        ])
        
        delay = random_delay(2.0, 4.0)
        time.sleep(delay)
        
        # 检查登录状态
        js_check = r"""
(() => {
  const loginBtn = document.querySelector('button[data-control-name="login_flow_cta"]');
  return loginBtn ? 'need_login' : 'ok';
})()
"""
        check_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--eval", js_check
        ])
        
        if check_result.returncode == 0 and 'need_login' in check_result.stdout:
            print("[警告] 需要登录 LinkedIn，请先登录")
            return []
        
        # 提取职位列表
        js_code = '''
(function() {
    var results = [];
    var items = document.querySelectorAll('.jobs-search-results__list-item, [data-jk]');
    
    items.forEach(function(item, index) {
        if (index >= ''' + str(limit) + ''') return;
        
        var titleEl = item.querySelector('.job-card-list__title-title, .base-search-card__title');
        var linkEl = item.querySelector('a[href*="/jobs/"]');
        var companyEl = item.querySelector('.job-card-container__primary-description');
        var locationEl = item.querySelector('.job-card-container__location');
        var dateEl = item.querySelector('.job-card-list__date');
        
        if (!titleEl) return;
        
        var title = titleEl.textContent.trim();
        var url = linkEl ? 'https://www.linkedin.com' + linkEl.href : '';
        var company = companyEl ? companyEl.textContent.trim() : '';
        var location = locationEl ? locationEl.textContent.trim() : '';
        var date = dateEl ? dateEl.textContent.trim() : '';
        
        if (title && url) {
            results.push({
                title: title,
                url: url,
                company: company,
                location: location,
                date: date,
                source: 'linkedin',
                scraped_at: new Date().toISOString()
            });
        }
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
    
    def _search_company(self, port: int, tab_id: str, company: str, limit: int) -> List[Dict]:
        """搜索公司"""
        url = LINKEDIN_COMPANY_URL.format(company=company)
        
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
    var info = {};
    
    var nameEl = document.querySelector('.org-top-card__name');
    info.name = nameEl ? nameEl.textContent.trim() : '';
    
    var descEl = document.querySelector('.org-top-card-summary__description');
    info.description = descEl ? descEl.textContent.trim().substring(0, 500) : '';
    
    var employeeEl = document.querySelector('.org-top-card__employee-count');
    info.employees = employeeEl ? employeeEl.textContent.trim() : '';
    
    var industryEl = document.querySelector('.org-top-card__industry');
    info.industry = industryEl ? industryEl.textContent.trim() : '';
    
    info.url = window.location.href;
    info.scraped_at = new Date().toISOString();
    
    return [info];
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
            return []
    
    def _search_people(self, port: int, tab_id: str, query: str, limit: int) -> List[Dict]:
        """搜索人员"""
        encoded_query = quote(query)
        url = LINKEDIN_PEOPLE_URL.format(query=encoded_query)
        
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
    var results = [];
    var items = document.querySelectorAll('.search-result-card, [data-entity-urn]');
    
    items.forEach(function(item, index) {
        if (index >= ''' + str(limit) + ''') return;
        
        var nameEl = item.querySelector('.entity-result__title-text a, .full-name');
        var linkEl = item.querySelector('a[href*="/in/"]');
        var titleEl = item.querySelector('.entity-result__primary-subtitle');
        var companyEl = item.querySelector('.entity-result__secondary-subtitle');
        
        if (!nameEl) return;
        
        var name = nameEl.textContent.trim();
        var url = linkEl ? 'https://www.linkedin.com' + linkEl.href : '';
        var title = titleEl ? titleEl.textContent.trim() : '';
        var company = companyEl ? companyEl.textContent.trim() : '';
        
        if (name && url) {
            results.push({
                name: name,
                url: url,
                title: title,
                company: company,
                source: 'linkedin',
                scraped_at: new Date().toISOString()
            });
        }
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
            return []
    
    def get_detail(self, url: str, port: int, tab_id: str) -> Dict:
        """获取详情"""
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
    var info = {};
    
    var titleEl = document.querySelector('.top-card__title');
    info.title = titleEl ? titleEl.textContent.trim() : '';
    
    var companyEl = document.querySelector('.topcard__flavor');
    info.company = companyEl ? companyEl.textContent.trim() : '';
    
    var locationEl = document.querySelector('.topcard__flavor--bullet');
    info.location = locationEl ? locationEl.textContent.trim() : '';
    
    var descEl = document.querySelector('.description__text');
    info.description = descEl ? descEl.textContent.trim().substring(0, 1000) : '';
    
    info.url = window.location.href;
    info.scraped_at = new Date().toISOString();
    
    return info;
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
    
    async def health_check(self) -> bool:
        """健康检查"""
        try:
            result = run_cmd([
                PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
                "--goto", LINKEDIN_BASE,
                "--wait-for", "stable",
                "--timeout", 10,
            ])
            return "error" not in result
        except Exception:
            return False


def main():
    parser = argparse.ArgumentParser(description="LinkedIn 职业搜索器")
    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--type", default="jobs", choices=["jobs", "company", "people"])
    parser.add_argument("--max-results", type=int, default=10)
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--stealth", action="store_true", default=True)
    parser.add_argument("--no-stealth", dest="stealth", action="store_false")
    parser.add_argument("--output-dir", help="输出目录")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--session", default="linkedin_session", help="浏览器会话名称")
    
    args = parser.parse_args()
    
    searcher = LinkedInSearcher()
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
