#!/usr/bin/env python
"""
gov_cn_search.py - 中国政府网搜索器

使用 browser-cdp skill 搜索中国政府网，获取政策文件、新闻公告、政务信息等。

用法:
    python gov_cn_search.py "乡村振兴"
    python gov_cn_search.py "十四五规划" --type policy --output-dir ./gov_results
    python gov_cn_search.py "国务院" --port 9333

示例:
    python gov_cn_search.py "人工智能政策"
    python gov_cn_search.py "政府工作报告" --output-dir ./results
"""

import argparse
import json
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
from src.searchers.utils import random_delay, save_results
from src.searchers.browser_utils import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


# ========== 中国政府网专用配置 ==========
GOV_CN_BASE = "https://www.gov.cn"
GOV_SEARCH_URL = f"{GOV_CN_BASE}/zhengce/zhengceku/search.html"
GOV_NEWS_URL = f"{GOV_CN_BASE}/xinwen"

# 默认输出目录
GOV_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "gov_cn"


class GovCnSearcher(BaseSearcher):
    """中国政府网搜索器"""

    @property
    def source_name(self) -> str:
        return "gov_cn"

    @property
    def supported_types(self) -> List[str]:
        return ["policy_search", "news_search", "document_search", "government_info"]

    def search(
        self,
        query: str,
        search_type: str = "all",
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
        max_results: int = 20,
    ) -> List[Dict]:
        """搜索政府信息

        Args:
            query: 搜索关键词
            search_type: 搜索类型 (policy/news/document/all)
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间
            max_results: 最大结果数

        Returns:
            搜索结果列表
        """
        print(f"[中国政府网] 正在搜索: {query}")

        # 确保浏览器连接
        if tab_id is None:
            result = ensure_browser(port=port, stealth=stealth)
            if result.get("error"):
                print(f"[错误] 浏览器启动失败: {result['error']}")
                return []
            tab_id = result.get("tab_id")
            port = result.get("port", port)
            print(f"[浏览器] 端口: {port}, Tab: {tab_id}")

        # 随机延迟
        delay = random_delay(1.0, 2.0)
        print(f"  [延迟] 请求前等待 {delay:.1f} 秒")

        results = []

        # 根据搜索类型执行不同搜索
        if search_type in ["policy", "all"]:
            print(f"  [搜索] 政策文件...")
            policy_results = self._search_policy(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(policy_results)

        if search_type in ["news", "all"]:
            print(f"  [搜索] 新闻资讯...")
            news_results = self._search_news(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(news_results)

        if search_type in ["document", "all"]:
            print(f"  [搜索] 政府文件...")
            doc_results = self._search_document(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(doc_results)

        # 保存结果
        if results and output_dir:
            path = save_results(
                results,
                output_dir or str(GOV_OUTPUT_DIR),
                f"gov_cn_{query}_{int(time.time())}.json"
            )
            print(f"  [保存] {path}")

        return results

    def _search_policy(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索政策文件"""
        search_url = f"{GOV_CN_BASE}/zhengce/zhengceku/search.html?keyword={quote(query)}"
        print(f"    [URL] 政策搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".search-result, .result-list, .policy-item, .news-item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 政策搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取政策信息
        js_policy = r"""
(() => {
  const results = [];
  const selectors = [
    '.search-result .item',
    '.result-list .item',
    '.policy-item',
    '.news-item',
    '.list-item'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const titleEl = item.querySelector('.title, .name, h3, h4, a');
    const linkEl = item.querySelector('a');
    const dateEl = item.querySelector('.date, .time, .publish-date');
    const sourceEl = item.querySelector('.source, .dept, .department');
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const date = dateEl ? dateEl.innerText.trim() : '';
    const source = sourceEl ? sourceEl.innerText.trim() : '';
    
    if (title && title.length > 5) {
      results.push({
        title: title,
        url: url,
        publish_date: date,
        source: source,
        type: 'policy',
        source_site: 'gov_cn',
      });
    }
  });
  
  return results;
})()
"""
        
        js_policy = js_policy.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_policy,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def _search_news(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索新闻资讯"""
        search_url = f"{GOV_CN_BASE}/xinwen/search?keyword={quote(query)}"
        print(f"    [URL] 新闻搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".news-list, .result-list, .news-item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 新闻搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取新闻信息
        js_news = r"""
(() => {
  const results = [];
  const selectors = [
    '.news-list .item',
    '.result-list .item',
    '.news-item'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const titleEl = item.querySelector('.title, .headline, h3, h4, a');
    const linkEl = item.querySelector('a');
    const dateEl = item.querySelector('.date, .time, .publish-date');
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const date = dateEl ? dateEl.innerText.trim() : '';
    
    if (title && title.length > 5) {
      results.push({
        title: title,
        url: url,
        publish_date: date,
        type: 'news',
        source_site: 'gov_cn',
      });
    }
  });
  
  return results;
})()
"""
        
        js_news = js_news.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_news,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def _search_document(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索政府文件"""
        search_url = f"{GOV_CN_BASE}/zhengce/search.html?keyword={quote(query)}"
        print(f"    [URL] 文件搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".doc-list, .result-list, .doc-item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 文件搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取文件信息
        js_doc = r"""
(() => {
  const results = [];
  const selectors = [
    '.doc-list .item',
    '.result-list .item',
    '.doc-item'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const titleEl = item.querySelector('.title, .name, h3, h4, a');
    const linkEl = item.querySelector('a');
    const docIdEl = item.querySelector('.doc-id, .file-id');
    const dateEl = item.querySelector('.date, .time');
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const doc_id = docIdEl ? docIdEl.innerText.trim() : '';
    const date = dateEl ? dateEl.innerText.trim() : '';
    
    if (title && title.length > 5) {
      results.push({
        title: title,
        url: url,
        doc_id: doc_id,
        publish_date: date,
        type: 'document',
        source_site: 'gov_cn',
      });
    }
  });
  
  return results;
})()
"""
        
        js_doc = js_doc.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_doc,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def get_detail(
        self,
        url: str,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
    ) -> Dict:
        """获取指定页面详情"""
        print(f"[中国政府网] 正在获取详情: {url}")

        # 确保浏览器连接
        if tab_id is None:
            result = ensure_browser(port=port, stealth=stealth)
            if result.get("error"):
                print(f"[错误] 浏览器启动失败: {result['error']}")
                return {}
            tab_id = result.get("tab_id")
            port = result.get("port", port)

        # 导航到详情页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", url,
            "--wait-selector", ".content, .detail, article, .text",
            "--timeout", "30",
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 详情页导航失败")
            return {}

        time.sleep(2.0)

        # 提取详情内容
        js_detail = r"""
(() => {
  const result = {
    title: document.title,
    url: window.location.href,
    content: '',
    publish_date: '',
    source: '',
  };
  
  // 提取正文
  const contentEl = document.querySelector('.content, .detail, article, .text, .main-content');
  if (contentEl) {
    result.content = contentEl.innerText.trim().substring(0, 3000);
  }
  
  // 提取发布日期
  const dateEl = document.querySelector('.publish-date, .date, .time');
  if (dateEl) {
    result.publish_date = dateEl.innerText.trim();
  }
  
  // 提取来源
  const sourceEl = document.querySelector('.source, .dept, .department');
  if (sourceEl) {
    result.source = sourceEl.innerText.trim();
  }
  
  return result;
})()
"""
        
        detail_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_detail,
        ])

        if detail_result.returncode == 0:
            try:
                return json.loads(detail_result.stdout)
            except json.JSONDecodeError:
                pass

        return {}


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="中国政府网搜索器 - 获取政策文件、新闻资讯、政府文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python gov_cn_search.py "乡村振兴"
    python gov_cn_search.py "十四五规划" --type policy --output-dir ./gov_results
    python gov_cn_search.py "国务院" --port 9333
"""
    )

    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--type", type=str, default="all",
                        choices=["policy", "news", "document", "all"],
                        help="搜索类型 (默认: all)")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--tab", type=str, default=None, help="Tab ID")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式")
    parser.add_argument("--wait-timeout", type=int, default=30, help="等待超时时间 (默认: 30秒)")
    parser.add_argument("--max-results", type=int, default=20, help="最大结果数 (默认: 20)")

    args = parser.parse_args()

    # 创建搜索器
    searcher = GovCnSearcher()

    # 执行搜索
    results = searcher.search(
        query=args.query,
        search_type=args.type,
        port=args.port,
        tab_id=args.tab,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.wait_timeout,
        max_results=args.max_results,
    )

    if results:
        print(f"\n[结果] 共获取 {len(results)} 条政府信息")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未获取到政府信息")


if __name__ == "__main__":
    main()
