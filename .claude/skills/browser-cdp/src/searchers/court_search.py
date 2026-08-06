#!/usr/bin/env python
"""
court_search.py - 中国裁判文书网搜索器

使用 browser-cdp skill 搜索中国裁判文书网，获取裁判文书信息。

用法:
    python court_search.py "民间借贷纠纷"
    python court_search.py "劳动合同" --court "北京市"
    python court_search.py "知识产权" --year 2024 --output-dir ./court_results

示例:
    python court_search.py "买卖合同纠纷"
    python court_search.py "交通事故" --court "上海" --year 2023
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


# ========== 中国裁判文书网专用配置 ==========
COURT_BASE = "https://wenshu.court.gov.cn"
COURT_SEARCH_URL = f"{COURT_BASE}/search?keyword={quote('{keyword}')}"

# 默认输出目录
COURT_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "court"


class CourtSearcher(BaseSearcher):
    """中国裁判文书网搜索器"""

    @property
    def source_name(self) -> str:
        return "court_wenshu"

    @property
    def supported_types(self) -> List[str]:
        return ["judgment_search", "court_document", "legal_case"]

    def search(
        self,
        query: str,
        court: Optional[str] = None,
        year: Optional[int] = None,
        case_type: Optional[str] = None,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
        max_results: int = 20,
    ) -> List[Dict]:
        """搜索裁判文书

        Args:
            query: 搜索关键词
            court: 法院名称（如：北京市、上海市）
            year: 裁判年份
            case_type: 案件类型（民事/刑事/行政/执行）
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间
            max_results: 最大结果数

        Returns:
            搜索结果列表
        """
        print(f"[中国裁判文书网] 正在搜索: {query}")

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

        # 构建搜索URL
        search_url = f"{COURT_BASE}/search?keyword={quote(query)}"
        if court:
            search_url += f"&court={quote(court)}"
        if year:
            search_url += f"&year={year}"
        if case_type:
            search_url += f"&caseType={quote(case_type)}"
        
        print(f"  [URL] 搜索: {search_url}")

        # 步骤1: 导航到搜索页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".search-result, .result-list, .wenshu-list, table",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return []

        time.sleep(2.0)

        # 步骤2: 提取搜索结果
        js_search = r"""
(() => {
  const results = [];
  // 裁判文书网搜索结果选择器
  const selectors = [
    '.search-result .item',
    '.result-list .item',
    '.wenshu-list .item',
    '.document-item',
    'table tbody tr'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= 20) return;
    
    const titleEl = item.querySelector('.title, .case-title, h3, h4, a');
    const linkEl = item.querySelector('a[href*="wenshu"]');
    const courtEl = item.querySelector('.court, .court-name, .org');
    const dateEl = item.querySelector('.date, .publish-date, .time');
    const caseNoEl = item.querySelector('.case-no, .case-number');
    const judgeTypeEl = item.querySelector('.judge-type, .case-type');
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const court = courtEl ? courtEl.innerText.trim() : '';
    const date = dateEl ? dateEl.innerText.trim() : '';
    const caseNo = caseNoEl ? caseNoEl.innerText.trim() : '';
    const judgeType = judgeTypeEl ? judgeTypeEl.innerText.trim() : '';
    
    if (title) {
      results.push({
        title: title,
        url: url,
        court: court,
        publish_date: date,
        case_number: caseNo,
        case_type: judgeType,
        source: 'court_wenshu',
      });
    }
  });
  
  return results;
})()
"""
        
        search_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_search,
        ])

        if search_result.returncode != 0:
            print(f"[错误] 搜索结果提取失败: {search_result.stderr[:200]}")
            return []

        try:
            items = json.loads(search_result.stdout)
        except json.JSONDecodeError:
            print(f"[错误] JSON 解析失败: {search_result.stdout[:200]}")
            return []

        if not items:
            print(f"[提示] 未找到搜索结果，尝试备用选择器...")
            return self._search_fallback(query, court, year, port, tab_id, max_results, stealth, output_dir, wait_timeout)

        print(f"  [结果] 找到 {len(items)} 条裁判文书")

        # 步骤3: 获取详情（可选）
        final_results = []
        for i, item in enumerate(items[:max_results]):
            if i > 0:
                delay = random_delay(1.0, 2.0)
                print(f"  [延迟] 等待 {delay:.1f} 秒")
            
            detail = self._get_detail(port, tab_id, item.get("url", ""), stealth, wait_timeout)
            if detail:
                final_results.append(detail)
            else:
                final_results.append({
                    "source": "court_wenshu",
                    "title": item.get("title", ""),
                    "court": item.get("court", ""),
                    "publish_date": item.get("publish_date", ""),
                    "case_number": item.get("case_number", ""),
                    "case_type": item.get("case_type", ""),
                    "url": item.get("url", ""),
                    "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                })

        # 保存结果
        if output_dir:
            path = save_results(
                final_results,
                output_dir,
                f"court_{query}_{int(time.time())}.json"
            )
            print(f"  [保存] {path}")

        return final_results

    def _search_fallback(
        self,
        query: str,
        court: Optional[str],
        year: Optional[int],
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        output_dir: Optional[str],
        wait_timeout: int,
    ) -> List[Dict]:
        """备用搜索方法"""
        print(f"  [备用] 尝试使用备用搜索方式...")
        
        # 尝试直接访问搜索页
        search_url = f"{COURT_BASE}/search?keyword={quote(query)}"
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", "body",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 备用搜索导航失败")
            return []

        time.sleep(2.0)
        
        # 使用更通用的选择器
        js_fallback = r"""
(() => {
  const results = [];
  const items = document.querySelectorAll('a[href*="wenshu"], .item, .result, .document');
  items.forEach((item, i) => {
    if (i >= 20) return;
    const title = item.innerText.trim().substring(0, 100);
    const href = item.href || '';
    if (title && href && title.length > 5) {
      results.push({
        title: title,
        url: href,
        court: '',
        publish_date: '',
        case_number: '',
        case_type: '',
        source: 'court_wenshu',
      });
    }
  });
  return results;
})()
"""
        fallback_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_fallback,
        ])

        try:
            items = json.loads(fallback_result.stdout)
        except:
            return []

        return items[:max_results]

    def _get_detail(
        self,
        port: int,
        tab_id: str,
        url: str,
        stealth: bool,
        wait_timeout: int,
    ) -> Optional[Dict]:
        """获取详情页内容"""
        if not url:
            return None

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", url,
            "--wait-selector", ".detail, .content, article, .wenshu-content",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            return None

        time.sleep(1.5)

        js_detail = r"""
(() => {
  const result = {};
  const titleEl = document.querySelector('h1, .title, .page-title, .case-title');
  result.title = titleEl ? titleEl.innerText.trim() : '';
  
  const contentEl = document.querySelector('.content, .detail, article, .wenshu-content, .text');
  result.content = contentEl ? contentEl.innerText.trim().substring(0, 2000) : '';
  
  const courtEl = document.querySelector('.court, .court-name, .org');
  result.court = courtEl ? courtEl.innerText.trim() : '';
  
  const dateEl = document.querySelector('.date, .publish-date, .time');
  result.publish_date = dateEl ? dateEl.innerText.trim() : '';
  
  const caseNoEl = document.querySelector('.case-no, .case-number');
  result.case_number = caseNoEl ? caseNoEl.innerText.trim() : '';
  
  return result;
})()
"""
        detail_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_detail,
        ])

        try:
            return json.loads(detail_result.stdout)
        except:
            return None

    def get_detail(
        self,
        url: str,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
    ) -> Dict:
        """获取指定页面详情"""
        if tab_id is None:
            result = ensure_browser(port=port, stealth=stealth)
            if result.get("error"):
                return {}
            tab_id = result.get("tab_id")
        
        return self._get_detail(port, tab_id, url, stealth, 30)


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="中国裁判文书网搜索器 - 获取裁判文书信息",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python court_search.py "民间借贷纠纷"
    python court_search.py "劳动合同" --court "北京市"
    python court_search.py "知识产权" --year 2024 --output-dir ./court_results
"""
    )

    parser.add_argument("query", help="搜索关键词（如：民间借贷纠纷、劳动合同）")
    parser.add_argument("--court", type=str, default=None, help="法院名称（如：北京市、上海市）")
    parser.add_argument("--year", type=int, default=None, help="裁判年份")
    parser.add_argument("--case-type", type=str, default=None, 
                        choices=["民事", "刑事", "行政", "执行"],
                        help="案件类型")
    parser.add_argument("--max-results", type=int, default=20, help="最大结果数 (默认: 20)")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--tab", type=str, default=None, help="Tab ID")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式")
    parser.add_argument("--wait-timeout", type=int, default=30, help="等待超时时间 (默认: 30秒)")

    args = parser.parse_args()

    # 创建搜索器
    searcher = CourtSearcher()

    # 执行搜索
    results = searcher.search(
        query=args.query,
        court=args.court,
        year=args.year,
        case_type=args.case_type,
        max_results=args.max_results,
        port=args.port,
        tab_id=args.tab,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.wait_timeout,
    )

    # 输出结果
    if results:
        print(f"\n[结果] 共获取 {len(results)} 条裁判文书")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未获取到裁判文书")


if __name__ == "__main__":
    main()
