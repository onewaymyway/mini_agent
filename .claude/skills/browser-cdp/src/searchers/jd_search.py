#!/usr/bin/env python
"""
jD_search.py - 京东商品搜索器

使用 browser-cdp skill 搜索京东商品，获取价格、销量、评价等信息。

用法:
    python jd_search.py "iPhone 15"
    python jd_search.py "机械键盘" --max-results 10 --output-dir ./jd_results
    python jd_search.py "笔记本电脑" --port 9333

示例:
    python jd_search.py "iPhone 15" --max-results 20
    python jd_search.py "机械键盘" --sort sales
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


# ========== 京东专用配置 ==========
JD_BASE = "https://search.jd.com"
JD_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "jd"


class JDSearcher(BaseSearcher):
    """京东商品搜索器"""

    @property
    def source_name(self) -> str:
        return "jd"

    @property
    def supported_types(self) -> List[str]:
        return ["product_search", "product_detail"]

    def search(
        self,
        query: str,
        max_results: int = 20,
        sort: Optional[str] = None,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
    ) -> List[Dict]:
        """搜索京东商品"""
        print(f"[京东搜索] 正在搜索: {query}")

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

        # 构建搜索URL
        search_url = f"{JD_BASE}/s_new.php?keyword={quote(query)}&enc=utf-8"
        if sort == "sales":
            search_url += "&psort=3"
        elif sort == "price_asc":
            search_url += "&psort=2"
        elif sort == "price_desc":
            search_url += "&psort=4"
        
        print(f"    [URL] 搜索: {search_url}")

        # 导航到搜索页面
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".gl-item, .item, .goods-item, #J_goodsList .item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 导航失败")
            return []

        time.sleep(2.0)

        # 提取商品信息
        js = r"""
(() => {
  const results = [];
  const items = document.querySelectorAll('#J_goodsList .item, .gl-item, .item');
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const linkEl = item.querySelector('a');
    const imgEl = item.querySelector('img');
    const priceEl = item.querySelector('.p-price strong, .price');
    const commitEl = item.querySelector('.p-commit em, .commit-count');
    const shopEl = item.querySelector('.p-shop a, .shop-name');
    const nameEl = item.querySelector('.p-name em, .name');
    
    const title = nameEl ? nameEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href.replace('http:', 'https:') : '';
    const price = priceEl ? priceEl.innerText.trim() : '';
    const commit = commitEl ? commitEl.innerText.trim() : '';
    const shop = shopEl ? shopEl.innerText.trim() : '';
    const img = imgEl ? imgEl.src : '';
    
    if (title && title.length > 3) {
      results.push({
        title: title.replace(/\s+/g, ' ').trim(),
        url: url,
        price: price,
        commit_count: commit,
        shop: shop,
        image_url: img,
        type: 'product',
        source_site: 'jd',
      });
    }
  });
  
  return results;
})()
"""
        
        js = js.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js,
        ])

        if result.returncode == 0:
            try:
                results = json.loads(result.stdout)
            except json.JSONDecodeError:
                results = []

        # 保存结果
        if results and output_dir:
            path = save_results(
                results,
                output_dir or str(JD_OUTPUT_DIR),
                f"jd_{query}_{int(time.time())}.json"
            )
            print(f"  [保存] {path}")

        return results

    def get_detail(
        self,
        url: str,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
    ) -> Dict:
        """获取商品详情"""
        print(f"[京东搜索] 正在获取详情: {url}")

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
            "--wait-selector", ".sku-info, .product-info, .p-parameter, .detail",
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
    price: '',
    commit: '',
    shop: '',
    specs: [],
    description: '',
  };
  
  // 价格
  const priceEl = document.querySelector('.p-price strong, .price, #price');
  if (priceEl) result.price = priceEl.innerText.trim();
  
  // 评论数
  const commitEl = document.querySelector('.p-commit em, #comment-count');
  if (commitEl) result.commit = commitEl.innerText.trim();
  
  // 店铺
  const shopEl = document.querySelector('.p-shop a, .shop-name, #shopName');
  if (shopEl) result.shop = shopEl.innerText.trim();
  
  // 规格参数
  const specItems = document.querySelectorAll('.parameter2 p, .p-parameter li');
  specItems.forEach(el => {
    const text = el.innerText.trim();
    if (text) result.specs.push(text);
  });
  
  // 商品描述
  const descEl = document.querySelector('.description, .desc, #detail');
  if (descEl) {
    result.description = descEl.innerText.trim().substring(0, 2000);
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
        description="京东商品搜索器 - 搜索京东商品获取价格、销量、评价等信息",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python jd_search.py "iPhone 15"
    python jd_search.py "机械键盘" --max-results 10 --output-dir ./jd_results
    python jd_search.py "笔记本电脑" --sort sales --port 9333
"""
    )

    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--sort", type=str, default=None,
                        choices=["综合", "sales", "price_asc", "price_desc"],
                        help="排序方式 (默认: 综合)")
    parser.add_argument("--max-results", type=int, default=20, help="最大结果数 (默认: 20)")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--tab", type=str, default=None, help="Tab ID")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式")
    parser.add_argument("--wait-timeout", type=int, default=30, help="等待超时时间 (默认: 30秒)")

    args = parser.parse_args()

    # 创建搜索器
    searcher = JdSearcher()

    # 执行搜索
    results = searcher.search(
        query=args.query,
        max_results=args.max_results,
        sort=args.sort,
        port=args.port,
        tab_id=args.tab,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.wait_timeout,
    )

    if results:
        print(f"\n[结果] 共获取 {len(results)} 条商品信息")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未获取到商品信息")


if __name__ == "__main__":
    main()
