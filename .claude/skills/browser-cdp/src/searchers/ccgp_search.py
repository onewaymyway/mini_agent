#!/usr/bin/env python
"""
ccgp_search.py - 中国政府采购网搜索器

使用 browser-cdp skill 搜索中国政府采购网，获取政府采购公告、中标结果、
更正公告、单一来源公示等信息。

用法:
    python ccgp_search.py "办公设备采购"
    python ccgp_search.py "信息化项目" --type bid --output-dir ./ccgp_results
    python ccgp_search.py "医疗设备" --port 9333

示例:
    python ccgp_search.py "智慧城市建设项目"
    python ccgp_search.py "公务用车采购" --output-dir ./results
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


# ========== 中国政府采购网专用配置 ==========
CCGP_BASE = "https://www.ccgp.gov.cn"
CCGP_SEARCH_URL = f"{CCGP_BASE}/searchindex.html"
CCGP_BID_URL = f"{CCGP_BASE}/cgxinxi/zbgg/index.html"
CCGP_WIN_URL = f"{CCGP_BASE}/cgxinxi/zbjg/index.html"

# 默认输出目录
CCGP_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "ccgp"


class CcgpSearcher(BaseSearcher):
    """中国政府采购网搜索器"""

    @property
    def source_name(self) -> str:
        return "ccgp"

    @property
    def supported_types(self) -> List[str]:
        return ["bid_search", "win_search", "correction_search", "single_source_search", "all"]

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
        province: Optional[str] = None,
    ) -> List[Dict]:
        """搜索政府采购信息

        Args:
            query: 搜索关键词
            search_type: 搜索类型 (bid/win/correction/single_source/all)
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间
            max_results: 最大结果数
            province: 省份名称（可选）

        Returns:
            搜索结果列表
        """
        print(f"[中国政府采购网] 正在搜索: {query}")

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
        if search_type in ["bid", "all"]:
            print(f"  [搜索] 招标公告...")
            bid_results = self._search_bid(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(bid_results)

        if search_type in ["win", "all"]:
            print(f"  [搜索] 中标结果...")
            win_results = self._search_win(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(win_results)

        if search_type in ["correction", "all"]:
            print(f"  [搜索] 更正公告...")
            correction_results = self._search_correction(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(correction_results)

        if search_type in ["single_source", "all"]:
            print(f"  [搜索] 单一来源公示...")
            single_results = self._search_single_source(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(single_results)

        # 去重
        results = self._deduplicate(results)

        # 保存结果
        if results and output_dir:
            path = save_results(
                results,
                output_dir or str(CCGP_OUTPUT_DIR),
                f"ccgp_{query}_{int(time.time())}.json"
            )
            print(f"  [保存] {path}")

        return results

    def _search_bid(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索招标公告"""
        search_url = f"{CCGP_BASE}/searchindex.html?keyword={quote(query)}&type=1"
        print(f"    [URL] 招标搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".search-result, .result-list, .list-item, .news-item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 招标搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取招标信息
        js_bid = r"""
(() => {
  const results = [];
  const selectors = [
    '.search-result .item',
    '.result-list .item',
    '.list-item',
    '.news-item',
    '.search-list li',
    '.result-item'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const titleEl = item.querySelector('.title, .name, h3, h4, a, .list-title');
    const linkEl = item.querySelector('a[href]');
    const dateEl = item.querySelector('.date, .time, .publish-date, .list-date');
    const sourceEl = item.querySelector('.source, .dept, .department, .org');
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const date = dateEl ? dateEl.innerText.trim() : '';
    const source = sourceEl ? sourceEl.innerText.trim() : '';
    
    if (title && title.length > 5) {
      results.push({
        title: title,
        url: url.startsWith('http') ? url : (url.startsWith('/') ? 'https://www.ccgp.gov.cn' + url : 'https://www.ccgp.gov.cn/' + url),
        publish_date: date,
        source: source,
        type: 'bid',
        source_site: 'ccgp',
      });
    }
  });
  
  return results;
})()
"""
        
        js_bid = js_bid.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_bid,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def _search_win(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索中标结果"""
        search_url = f"{CCGP_BASE}/searchindex.html?keyword={quote(query)}&type=2"
        print(f"    [URL] 中标搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".search-result, .result-list, .list-item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 中标搜索导航失败")
            return []

        time.sleep(2.0)

        js_win = r"""
(() => {
  const results = [];
  const selectors = [
    '.search-result .item',
    '.result-list .item',
    '.list-item',
    '.news-item'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const titleEl = item.querySelector('.title, .name, h3, h4, a');
    const linkEl = item.querySelector('a[href]');
    const dateEl = item.querySelector('.date, .time, .publish-date');
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const date = dateEl ? dateEl.innerText.trim() : '';
    
    if (title && title.length > 5) {
      results.push({
        title: title,
        url: url.startsWith('http') ? url : (url.startsWith('/') ? 'https://www.ccgp.gov.cn' + url : 'https://www.ccgp.gov.cn/' + url),
        publish_date: date,
        type: 'win',
        source_site: 'ccgp',
      });
    }
  });
  
  return results;
})()
"""
        
        js_win = js_win.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_win,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def _search_correction(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索更正公告"""
        search_url = f"{CCGP_BASE}/searchindex.html?keyword={quote(query)}&type=3"
        print(f"    [URL] 更正搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".search-result, .result-list, .list-item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 更正搜索导航失败")
            return []

        time.sleep(2.0)

        js_correction = r"""
(() => {
  const results = [];
  const selectors = [
    '.search-result .item',
    '.result-list .item',
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
    const linkEl = item.querySelector('a[href]');
    const dateEl = item.querySelector('.date, .time, .publish-date');
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const date = dateEl ? dateEl.innerText.trim() : '';
    
    if (title && title.length > 5) {
      results.push({
        title: title,
        url: url.startsWith('http') ? url : (url.startsWith('/') ? 'https://www.ccgp.gov.cn' + url : 'https://www.ccgp.gov.cn/' + url),
        publish_date: date,
        type: 'correction',
        source_site: 'ccgp',
      });
    }
  });
  
  return results;
})()
"""
        
        js_correction = js_correction.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_correction,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def _search_single_source(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索单一来源公示"""
        search_url = f"{CCGP_BASE}/searchindex.html?keyword={quote(query)}&type=4"
        print(f"    [URL] 单一来源搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".search-result, .result-list, .list-item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 单一来源搜索导航失败")
            return []

        time.sleep(2.0)

        js_single = r"""
(() => {
  const results = [];
  const selectors = [
    '.search-result .item',
    '.result-list .item',
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
    const linkEl = item.querySelector('a[href]');
    const dateEl = item.querySelector('.date, .time, .publish-date');
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const date = dateEl ? dateEl.innerText.trim() : '';
    
    if (title && title.length > 5) {
      results.push({
        title: title,
        url: url.startsWith('http') ? url : (url.startsWith('/') ? 'https://www.ccgp.gov.cn' + url : 'https://www.ccgp.gov.cn/' + url),
        publish_date: date,
        type: 'single_source',
        source_site: 'ccgp',
      });
    }
  });
  
  return results;
})()
"""
        
        js_single = js_single.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_single,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def _deduplicate(self, results: List[Dict]) -> List[Dict]:
        """去重"""
        seen = set()
        unique = []
        for r in results:
            key = r.get('title', '')[:50]
            if key not in seen:
                seen.add(key)
                unique.append(r)
        return unique

    def get_detail(
        self,
        url: str,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
    ) -> Dict:
        """获取指定页面详情"""
        print(f"[中国政府采购网] 正在获取详情: {url}")

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
            "--wait-selector", ".content, .detail, article, .text, .TRS_Editor",
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
  const contentEl = document.querySelector('.content, .detail, article, .text, .TRS_Editor, .main-content, .article-content');
  if (contentEl) {
    result.content = contentEl.innerText.trim().substring(0, 5000);
  }
  
  // 提取发布日期
  const dateEl = document.querySelector('.publish-date, .date, .time, .info-time');
  if (dateEl) {
    result.publish_date = dateEl.innerText.trim();
  }
  
  // 提取来源
  const sourceEl = document.querySelector('.source, .dept, .department, .info-source');
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
        description="中国政府采购网搜索器 - 获取采购公告、中标结果、更正公告等信息",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python ccgp_search.py "办公设备采购"
    python ccgp_search.py "信息化项目" --type bid --output-dir ./ccgp_results
    python ccgp_search.py "医疗设备" --port 9333
"""
    )

    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--type", type=str, default="all",
                        choices=["bid", "win", "correction", "single_source", "all"],
                        help="搜索类型 (默认: all)")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--tab", type=str, default=None, help="Tab ID")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式")
    parser.add_argument("--wait-timeout", type=int, default=30, help="等待超时时间 (默认: 30秒)")
    parser.add_argument("--max-results", type=int, default=20, help="最大结果数 (默认: 20)")
    parser.add_argument("--province", type=str, default=None, help="省份名称（可选）")

    args = parser.parse_args()

    # 创建搜索器
    searcher = CcgpSearcher()

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
        province=args.province,
    )

    if results:
        print(f"\n[结果] 共获取 {len(results)} 条政府采购信息")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未获取到政府采购信息")


if __name__ == "__main__":
    main()
