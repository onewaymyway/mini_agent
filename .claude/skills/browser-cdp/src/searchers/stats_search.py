#!/usr/bin/env python
"""
stats_search.py - 国家数据（统计局）搜索器

使用 browser-cdp skill 搜索国家数据平台，获取统计数据。

用法:
    python stats_search.py "GDP" --indicator "国内生产总值"
    python stats_search.py "人口" --output-dir ./stats_results
    python stats_search.py "CPI" --port 9333

示例:
    python stats_search.py "GDP" --indicator "国内生产总值"
    python stats_search.py "人口" --output-dir ./stats_results
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


# ========== 国家数据平台专用配置 ==========
STATS_BASE = "https://data.stats.gov.cn"
STATS_SEARCH_URL = f"{STATS_BASE}/eaquery.xhtml?kw={quote('{keyword}')}"
STATS_TREE_URL = f"{STATS_BASE}/treeInterface26.xhtml"

# 默认输出目录
STATS_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "stats"


class StatsSearcher(BaseSearcher):
    """国家数据（统计局）搜索器"""

    @property
    def source_name(self) -> str:
        return "stats_cn"

    @property
    def supported_types(self) -> List[str]:
        return ["stats_search", "indicator_search", "data_query"]

    def search(
        self,
        query: str,
        indicator: Optional[str] = None,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
        max_results: int = 20,
    ) -> List[Dict]:
        """搜索统计数据

        Args:
            query: 搜索关键词
            indicator: 指标名称（可选）
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间
            max_results: 最大结果数

        Returns:
            搜索结果列表
        """
        print(f"[国家数据] 正在搜索: {query}")

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

        # 步骤1: 导航到搜索页
        search_url = f"{STATS_BASE}/eaquery.xhtml?kw={quote(query)}"
        print(f"  [URL] 搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".search-result, .result-list, .data-list, table",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return []

        time.sleep(2.0)

        # 步骤2: 提取搜索结果
        results = self._extract_search_results(port, tab_id, query, max_results)

        if not results:
            print(f"[提示] 未找到结果，尝试通过指标树搜索...")
            results = self._search_by_tree(query, port, tab_id, max_results, wait_timeout)

        # 保存结果
        if results and output_dir:
            path = save_results(
                results,
                output_dir or str(STATS_OUTPUT_DIR),
                f"stats_{query}_{int(time.time())}.json"
            )
            print(f"  [保存] {path}")

        return results

    def _extract_search_results(
        self,
        port: int,
        tab_id: str,
        query: str,
        max_results: int,
    ) -> List[Dict]:
        """提取搜索结果"""
        js_extract = r"""
(() => {
  const results = [];
  // 国家数据平台搜索结果选择器
  const selectors = [
    '.search-result tr',
    '.result-list tr',
    'table tbody tr',
    '.data-list tr',
    'tr[data-code]'
  ];
  
  let rows = [];
  for (const selector of selectors) {
    rows = document.querySelectorAll(selector);
    if (rows.length > 0) break;
  }
  
  rows.forEach((row, i) => {
    if (i >= max_results) return;
    
    const cells = row.querySelectorAll('td');
    if (cells.length < 2) return;
    
    const codeEl = cells[0];
    const nameEl = cells[1];
    const descEl = cells.length > 2 ? cells[2] : null;
    
    const code = codeEl ? codeEl.innerText.trim() : '';
    const name = nameEl ? nameEl.innerText.trim() : '';
    const desc = descEl ? descEl.innerText.trim() : '';
    
    if (name) {
      results.push({
        code: code,
        name: name,
        description: desc,
        source: 'stats_cn',
        query: query,
      });
    }
  });
  
  return results;
})()
"""
        
        js_extract = js_extract.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_extract,
        ])

        if result.returncode != 0:
            print(f"[错误] 结果提取失败: {result.stderr[:200]}")
            return []

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            print(f"[错误] JSON 解析失败: {result.stdout[:200]}")
            return []

    def _search_by_tree(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        wait_timeout: int,
    ) -> List[Dict]:
        """通过指标树搜索"""
        print(f"  [备用] 尝试通过指标树搜索...")
        
        # 导航到指标树
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", STATS_BASE,
            "--wait-selector", ".tree, .menu, .category",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 指标树导航失败")
            return []

        time.sleep(2.0)

        # 提取指标树
        js_tree = r"""
(() => {
  const indicators = [];
  const items = document.querySelectorAll('.tree-node, .menu-item, .category-item, [class*="node"]');
  items.forEach((item, i) => {
    if (i >= 50) return;
    const text = item.innerText.trim();
    const code = item.getAttribute('data-code') || '';
    if (text && text.length > 2) {
      indicators.push({
        code: code,
        name: text,
        source: 'stats_cn',
      });
    }
  });
  return indicators;
})()
"""
        
        tree_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_tree,
        ])

        if tree_result.returncode == 0:
            try:
                all_indicators = json.loads(tree_result.stdout)
                # 过滤匹配查询的指标
                filtered = [i for i in all_indicators if query.lower() in i['name'].lower()]
                return filtered[:max_results]
            except json.JSONDecodeError:
                pass

        return []

    def get_indicator_data(
        self,
        code: str,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        years: List[str] = None,
    ) -> Dict:
        """获取指定指标的数据"""
        print(f"[国家数据] 正在获取指标数据: {code}")

        # 确保浏览器连接
        if tab_id is None:
            result = ensure_browser(port=port, stealth=stealth)
            if result.get("error"):
                print(f"[错误] 浏览器启动失败: {result['error']}")
                return {}
            tab_id = result.get("tab_id")
            port = result.get("port", port)

        # 构建数据查询URL
        if years:
            year_param = ",".join(years)
            data_url = f"{STATS_BASE}/em/query/querydata?tbname=TB{code}&zb={code}&t=1&wd={quote(code)}"
        else:
            data_url = f"{STATS_BASE}/em/query/querydata?tbname=TB{code}&zb={code}&t=1"

        print(f"  [URL] 数据查询: {data_url}")

        # 导航到数据页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", data_url,
            "--wait-selector", "table, .data-table, .chart",
            "--timeout", "30",
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 数据页导航失败")
            return {}

        time.sleep(2.0)

        # 提取数据
        js_data = r"""
(() => {
  const result = {
    code: '',
    name: '',
    data: [],
    units: '',
  };
  
  // 提取指标名称
  const titleEl = document.querySelector('.title, h1, h2, .indicator-name');
  if (titleEl) {
    result.name = titleEl.innerText.trim();
  }
  
  // 提取数据表格
  const tables = document.querySelectorAll('table');
  tables.forEach(table => {
    const rows = table.querySelectorAll('tr');
    rows.forEach((row, i) => {
      if (i === 0) return; // 跳过表头
      const cells = row.querySelectorAll('td');
      if (cells.length >= 2) {
        result.data.push({
          year: cells[0].innerText.trim(),
          value: cells[1].innerText.trim(),
        });
      }
    });
  });
  
  return result;
})()
"""
        
        data_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_data,
        ])

        if data_result.returncode == 0:
            try:
                return json.loads(data_result.stdout)
            except json.JSONDecodeError:
                pass

        return {}

    def list_indicators(
        self,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        max_results: int = 50,
    ) -> List[Dict]:
        """列出所有指标"""
        print(f"[国家数据] 正在获取指标列表...")

        # 确保浏览器连接
        if tab_id is None:
            result = ensure_browser(port=port, stealth=stealth)
            if result.get("error"):
                print(f"[错误] 浏览器启动失败: {result['error']}")
                return []
            tab_id = result.get("tab_id")
            port = result.get("port", port)

        # 导航到首页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", STATS_BASE,
            "--wait-selector", ".tree, .menu, .category",
            "--timeout", "30",
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 首页导航失败")
            return []

        time.sleep(2.0)

        # 提取指标列表
        js_indicators = r"""
(() => {
  const indicators = [];
  const selectors = [
    '.tree-node',
    '.menu-item',
    '.category-item',
    '[class*="node"]',
    '[class*="item"]'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    const text = item.innerText.trim();
    const code = item.getAttribute('data-code') || '';
    if (text && text.length > 2) {
      indicators.push({
        code: code,
        name: text,
        source: 'stats_cn',
      });
    }
  });
  
  return indicators;
})()
"""
        
        js_indicators = js_indicators.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_indicators,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="国家数据搜索器 - 获取统计数据",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python stats_search.py "GDP"
    python stats_search.py "人口" --output-dir ./stats_results
    python stats_search.py --list-indicators
"""
    )

    parser.add_argument("query", nargs="?", help="搜索关键词")
    parser.add_argument("--indicator", type=str, default=None, help="指标名称")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--tab", type=str, default=None, help="Tab ID")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式")
    parser.add_argument("--wait-timeout", type=int, default=30, help="等待超时时间 (默认: 30秒)")
    parser.add_argument("--max-results", type=int, default=20, help="最大结果数 (默认: 20)")
    parser.add_argument("--list-indicators", action="store_true", help="列出所有指标")

    args = parser.parse_args()

    # 创建搜索器
    searcher = StatsSearcher()

    # 列出指标
    if args.list_indicators:
        indicators = searcher.list_indicators(port=args.port, tab_id=args.tab, stealth=args.stealth)
        print(f"\n[指标列表] 共 {len(indicators)} 个指标:")
        for i, ind in enumerate(indicators[:20], 1):
            print(f"  {i}. {ind['name']} (代码: {ind['code']})")
        print(f"\n{json.dumps(indicators, ensure_ascii=False, indent=2)}")
        return

    # 执行搜索
    if args.query:
        results = searcher.search(
            query=args.query,
            indicator=args.indicator,
            port=args.port,
            tab_id=args.tab,
            stealth=args.stealth,
            output_dir=args.output_dir,
            wait_timeout=args.wait_timeout,
            max_results=args.max_results,
        )

        if results:
            print(f"\n[结果] 共获取 {len(results)} 条统计数据")
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            print("[结果] 未获取到统计数据")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
