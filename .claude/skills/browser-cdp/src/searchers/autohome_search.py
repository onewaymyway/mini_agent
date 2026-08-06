#!/usr/bin/env python
"""
autohome_search.py - 汽车之家搜索器

使用 browser-cdp skill 搜索汽车之家，获取车型信息、参数配置、车主报价等。

用法:
    python autohome_search.py "Model 3"
    python autohome_search.py "宝马3系" --type config
    python autohome_search.py "特斯拉" --output-dir ./autohome_results

示例:
    python autohome_search.py "Model 3"
    python autohome_search.py "宝马3系" --type config
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


# ========== 汽车之家专用配置 ==========
AUTOHOME_BASE = "https://www.autohome.com.cn"
AUTOHOME_SEARCH_URL = f"{AUTOHOME_BASE}/search/word/{quote('{keyword}')}"
AUTOHOME_CAR_URL = "https://car.autohome.com.cn/"

# 默认输出目录
AUTOHOME_OUTPUT_DIR = Path(__file__).parent.parent.parent / "search_results" / "autohome"


class AutohomeSearcher(BaseSearcher):
    """汽车之家搜索器"""

    @property
    def source_name(self) -> str:
        return "autohome"

    @property
    def supported_types(self) -> List[str]:
        return ["car_search", "car_config", "car_price", "car_review"]

    def search(
        self,
        query: str,
        search_type: str = "car",
        max_results: int = 20,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
    ) -> List[Dict]:
        """搜索车型信息

        Args:
            query: 搜索关键词（车型名称）
            search_type: 搜索类型 (car/config/news)
            max_results: 最大结果数
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间

        Returns:
            车型信息列表
        """
        print(f"[汽车之家] 正在搜索: {query}")
        print(f"  类型: {search_type}")

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
        delay = random_delay(1.5, 2.5)
        print(f"  [延迟] 请求前等待 {delay:.1f} 秒")

        # 步骤1: 导航到搜索页
        if search_type == "car":
            search_url = f"{AUTOHOME_BASE}/soa/search?keyword={quote(query)}"
        elif search_type == "config":
            search_url = f"{AUTOHOME_BASE}/config/{quote(query)}"
        else:
            search_url = f"{AUTOHOME_BASE}/search/word/{quote(query)}"
        
        print(f"  [URL] 搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".search-result, .car-list, .config-list, .article-list",
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
  
  // 车型搜索结果
  const carItems = document.querySelectorAll('.search-car-item, .car-item, .list-item, .result-item');
  carItems.forEach((item, i) => {
    if (i >= 20) return;
    
    const linkEl = item.querySelector('a[href*="config"], a[href*="car"]');
    const titleEl = item.querySelector('.car-name, .name, h3, h4, .title');
    const priceEl = item.querySelector('.price, .price-range, .price-text');
    const brandEl = item.querySelector('.brand, .brand-name');
    
    const name = titleEl ? titleEl.innerText.trim() : '';
    const price = priceEl ? priceEl.innerText.trim() : '';
    const brand = brandEl ? brandEl.innerText.trim() : '';
    const href = linkEl ? linkEl.href : '';
    
    if (name) {
      results.push({
        name: name,
        brand: brand,
        price_range: price,
        url: href,
        type: 'car',
      });
    }
  });
  
  // 文章搜索结果
  const articleItems = document.querySelectorAll('.article-item, .news-item');
  articleItems.forEach((item, i) => {
    if (i >= 20) return;
    
    const linkEl = item.querySelector('a');
    const titleEl = item.querySelector('.title, h3, h4');
    const dateEl = item.querySelector('.date, .time');
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const date = dateEl ? dateEl.innerText.trim() : '';
    const href = linkEl ? linkEl.href : '';
    
    if (title) {
      results.push({
        title: title,
        date: date,
        url: href,
        type: 'article',
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
            print(f"[提示] 未找到搜索结果，尝试备用方式...")
            return self._search_fallback(query, search_type, port, tab_id, max_results, stealth, output_dir, wait_timeout)

        print(f"  [结果] 找到 {len(items)} 条结果")

        # 步骤3: 获取详情（可选）
        final_results = []
        for i, item in enumerate(items[:max_results]):
            if i > 0:
                delay = random_delay(1.0, 2.0)
                print(f"  [延迟] 等待 {delay:.1f} 秒")
            
            if item.get('type') == 'car':
                detail = self._get_car_detail(port, tab_id, item.get("url", ""), stealth, wait_timeout)
                if detail:
                    final_results.append(detail)
                else:
                    final_results.append(item)
            else:
                final_results.append(item)

        # 保存结果
        if output_dir:
            path = save_results(
                final_results,
                output_dir,
                f"autohome_{query}_{int(time.time())}.json"
            )
            print(f"  [保存] {path}")

        return final_results

    def _search_fallback(
        self,
        query: str,
        search_type: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        output_dir: Optional[str],
        wait_timeout: int,
    ) -> List[Dict]:
        """备用搜索方法"""
        print(f"  [备用] 尝试使用备用搜索方式...")
        
        search_url = f"{AUTOHOME_BASE}/search/word/{quote(query)}"
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", "body",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            return []

        time.sleep(2.0)
        
        js_fallback = r"""
(() => {
  const results = [];
  const items = document.querySelectorAll('a[href*="config"], a[href*="car"], .item, .result');
  items.forEach((item, i) => {
    if (i >= 20) return;
    const title = item.innerText.trim().substring(0, 100);
    const href = item.href || '';
    if (title && href && title.length > 5 && !title.includes('登录')) {
      results.push({
        name: title,
        url: href,
        type: 'car' if 'config' in href else 'article',
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

    def _get_car_detail(
        self,
        port: int,
        tab_id: str,
        url: str,
        stealth: bool,
        wait_timeout: int,
    ) -> Optional[Dict]:
        """获取车型详情页内容"""
        if not url:
            return None

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", url,
            "--wait-selector", ".car-info, .config-table, .param-table",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            return None

        time.sleep(2.0)

        js_detail = r"""
(() => {
  const result = {};
  const nameEl = document.querySelector('.car-name, h1, .title');
  result.name = nameEl ? nameEl.innerText.trim() : '';
  
  const brandEl = document.querySelector('.brand, .brand-name');
  result.brand = brandEl ? brandEl.innerText.trim() : '';
  
  const priceEl = document.querySelector('.price, .price-range');
  result.price_range = priceEl ? priceEl.innerText.trim() : '';
  
  // 提取参数配置
  const params = {};
  const paramRows = document.querySelectorAll('.param-row, .config-row, table tr');
  paramRows.forEach(row => {
    const cells = row.querySelectorAll('td, th');
    if (cells.length >= 2) {
      const key = cells[0].innerText.trim();
      const value = cells[1].innerText.trim();
      if (key && value) {
        params[key] = value;
      }
    }
  });
  result.params = params;
  
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
        
        return self._get_car_detail(port, tab_id, url, stealth, 30)


def ensure_browser(port: int = 9333, stealth: bool = True) -> Dict:
    """确保浏览器已连接"""
    cmd = [
        PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
        "--port", str(port),
        "--status",
    ]
    result = run_cmd(cmd)
    
    if result.returncode == 0:
        try:
            status = json.loads(result.stdout)
            if status.get("connected"):
                return {"tab_id": status.get("tab_id"), "port": port}
        except:
            pass
    
    # 启动新浏览器
    cmd = [
        PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
        "--port", str(port),
        "--launch",
    ]
    if stealth:
        cmd.extend(["--stealth"])
    
    result = run_cmd(cmd)
    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            return data
        except:
            pass
    
    return {"error": "浏览器启动失败"}


def run_cmd(cmd: List[str]) -> subprocess.CompletedProcess:
    """执行命令"""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="汽车之家搜索器 - 获取车型信息和参数配置",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python autohome_search.py "Model 3"
    python autohome_search.py "宝马3系" --type config
    python autohome_search.py "特斯拉" --output-dir ./autohome_results
"""
    )

    parser.add_argument("query", help="搜索关键词（车型名称）")
    parser.add_argument("--type", type=str, default="car", choices=["car", "config", "news"], help="搜索类型")
    parser.add_argument("--max-results", type=int, default=20, help="最大结果数 (默认: 20)")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--tab", type=str, default=None, help="Tab ID")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式")
    parser.add_argument("--wait-timeout", type=int, default=30, help="等待超时时间 (默认: 30秒)")

    args = parser.parse_args()

    # 创建搜索器
    searcher = AutohomeSearcher()

    # 执行搜索
    results = searcher.search(
        query=args.query,
        search_type=args.type,
        max_results=args.max_results,
        port=args.port,
        tab_id=args.tab,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.wait_timeout,
    )

    # 输出结果
    if results:
        print(f"\n[结果] 共获取 {len(results)} 条信息")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未获取到信息")


if __name__ == "__main__":
    main()
