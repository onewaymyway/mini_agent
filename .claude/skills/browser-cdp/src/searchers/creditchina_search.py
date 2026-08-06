#!/usr/bin/env python
"""
creditchina_search.py - 信用中国搜索器

使用 browser-cdp skill 搜索信用中国，获取企业信用信息、行政处罚、失信名单等。

用法:
    python creditchina_search.py "阿里巴巴"
    python creditchina_search.py "失信" --type blacklist --output-dir ./credit_results
    python creditchina_search.py "行政处罚" --type penalty --port 9333

示例:
    python creditchina_search.py "腾讯"
    python creditchina_search.py "失信被执行" --type blacklist --output-dir ./results
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
from src.searchers.baidu_search import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


# ========== 信用中国专用配置 ==========
CREDIT_BASE = "https://www.creditchina.gov.cn"
CREDIT_SEARCH_URL = f"{CREDIT_BASE}/search?keyword={quote('{keyword}')}"
CREDIT_BLACKLIST_URL = f"{CREDIT_BASE}/xxgk/shixin"
CREDIT_PENALTY_URL = f"{CREDIT_BASE}/xxgk/xingzhengchufa"

# 默认输出目录
CREDIT_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "creditchina"


class CreditChinaSearcher(BaseSearcher):
    """信用中国搜索器"""

    @property
    def source_name(self) -> str:
        return "creditchina"

    @property
    def supported_types(self) -> List[str]:
        return ["credit_search", "blacklist_search", "penalty_search", "credit_info"]

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
        """搜索信用信息

        Args:
            query: 搜索关键词
            search_type: 搜索类型 (credit/blacklist/penalty/all)
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间
            max_results: 最大结果数

        Returns:
            搜索结果列表
        """
        print(f"[信用中国] 正在搜索: {query}")

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
        if search_type in ["credit", "all"]:
            print(f"  [搜索] 信用信息...")
            credit_results = self._search_credit(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(credit_results)

        if search_type in ["blacklist", "all"]:
            print(f"  [搜索] 失信名单...")
            blacklist_results = self._search_blacklist(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(blacklist_results)

        if search_type in ["penalty", "all"]:
            print(f"  [搜索] 行政处罚...")
            penalty_results = self._search_penalty(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(penalty_results)

        # 保存结果
        if results and output_dir:
            path = save_results(
                results,
                output_dir or str(CREDIT_OUTPUT_DIR),
                f"creditchina_{query}_{int(time.time())}.json"
            )
            print(f"  [保存] {path}")

        return results

    def _search_credit(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索信用信息"""
        search_url = f"{CREDIT_BASE}/search?keyword={quote(query)}"
        print(f"    [URL] 信用搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".search-result, .result-list, .credit-item, table",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 信用搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取信用信息
        js_credit = r"""
(() => {
  const results = [];
  const selectors = [
    '.search-result .item',
    '.result-list .item',
    '.credit-item',
    'table tbody tr'
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
    const typeEl = item.querySelector('.type, .category, .分类');
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const date = dateEl ? dateEl.innerText.trim() : '';
    const type = typeEl ? typeEl.innerText.trim() : '';
    
    if (title && title.length > 5) {
      results.push({
        title: title,
        url: url,
        publish_date: date,
        type: type,
        search_type: 'credit',
        source_site: 'creditchina',
      });
    }
  });
  
  return results;
})()
"""
        
        js_credit = js_credit.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_credit,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def _search_blacklist(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索失信名单"""
        search_url = f"{CREDIT_BASE}/xxgk/shixin?keyword={quote(query)}"
        print(f"    [URL] 失信名单搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".blacklist-list, .result-list, .shixin-item, table",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 失信名单搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取失信名单信息
        js_blacklist = r"""
(() => {
  const results = [];
  const selectors = [
    '.blacklist-list .item',
    '.result-list .item',
    '.shixin-item',
    'table tbody tr'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const nameEl = item.querySelector('.name, .entity-name, h3, h4');
    const linkEl = item.querySelector('a');
    const codeEl = item.querySelector('.code, .credit-code, .统一社会信用代码');
    const dateEl = item.querySelector('.date, .publish-date, .time');
    const courtEl = item.querySelector('.court, .court-name, .法院');
    
    const name = nameEl ? nameEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const code = codeEl ? codeEl.innerText.trim() : '';
    const date = dateEl ? dateEl.innerText.trim() : '';
    const court = courtEl ? courtEl.innerText.trim() : '';
    
    if (name && name.length > 2) {
      results.push({
        name: name,
        url: url,
        credit_code: code,
        publish_date: date,
        court: court,
        search_type: 'blacklist',
        source_site: 'creditchina',
      });
    }
  });
  
  return results;
})()
"""
        
        js_blacklist = js_blacklist.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_blacklist,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def _search_penalty(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索行政处罚"""
        search_url = f"{CREDIT_BASE}/xxgk/xingzhengchufa?keyword={quote(query)}"
        print(f"    [URL] 行政处罚搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".penalty-list, .result-list, .penalty-item, table",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 行政处罚搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取行政处罚信息
        js_penalty = r"""
(() => {
  const results = [];
  const selectors = [
    '.penalty-list .item',
    '.result-list .item',
    '.penalty-item',
    'table tbody tr'
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
    const dateEl = item.querySelector('.date, .publish-date, .time');
    const deptEl = item.querySelector('.dept, .department, .处罚机关');
    const amountEl = item.querySelector('.amount, .fine, .罚款');
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const date = dateEl ? dateEl.innerText.trim() : '';
    const dept = deptEl ? deptEl.innerText.trim() : '';
    const amount = amountEl ? amountEl.innerText.trim() : '';
    
    if (title && title.length > 5) {
      results.push({
        title: title,
        url: url,
        publish_date: date,
        department: dept,
        fine_amount: amount,
        search_type: 'penalty',
        source_site: 'creditchina',
      });
    }
  });
  
  return results;
})()
"""
        
        js_penalty = js_penalty.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_penalty,
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
        print(f"[信用中国] 正在获取详情: {url}")

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
            "--wait-selector", ".content, .detail, article, .credit-detail",
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
    department: '',
  };
  
  // 提取正文
  const contentEl = document.querySelector('.content, .detail, article, .credit-detail, .text');
  if (contentEl) {
    result.content = contentEl.innerText.trim().substring(0, 3000);
  }
  
  // 提取发布日期
  const dateEl = document.querySelector('.publish-date, .date, .time');
  if (dateEl) {
    result.publish_date = dateEl.innerText.trim();
  }
  
  // 提取发布部门
  const deptEl = document.querySelector('.department, .dept, .org');
  if (deptEl) {
    result.department = deptEl.innerText.trim();
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
        description="信用中国搜索器 - 获取企业信用信息、失信名单、行政处罚",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python creditchina_search.py "阿里巴巴"
    python creditchina_search.py "失信" --type blacklist --output-dir ./credit_results
    python creditchina_search.py "行政处罚" --type penalty --port 9333
"""
    )

    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--type", type=str, default="all",
                        choices=["credit", "blacklist", "penalty", "all"],
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
    searcher = CreditChinaSearcher()

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
        print(f"\n[结果] 共获取 {len(results)} 条信用信息")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未获取到信用信息")


if __name__ == "__main__":
    main()
