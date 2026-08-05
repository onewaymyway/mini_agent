#!/usr/bin/env python
"""
taobao_search.py - 淘宝/天猫商品搜索自动化脚本

使用 browser-cdp skill 搜索淘宝/天猫商品，获取价格、销量、评价、店铺等信息。
淘宝反爬机制较强，必须启用 stealth 模式并配合代理池使用。

用法:
    python taobao_search.py "iPhone 15" --max-results 10
    python taobao_search.py "机械键盘" --max-results 5 --output-dir ./tb_results
    python taobao_search.py "笔记本电脑" --port 9333 --stealth

示例:
    python taobao_search.py "iPhone 15" --max-results 10
    python taobao_search.py "机械键盘" --max-results 5 --output-dir ./tb_results
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
    random_delay, get_random_ua, save_results, dedup_results, clean_text
)
from src.searchers.baidu_search import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


# ========== 淘宝/天猫 专用配置 ==========
TAOBAO_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "taobao"
TAOBAO_BASE = "https://s.taobao.com"
TMALL_BASE = "https://s.tmall.com"


class TaobaoSearcher(BaseSearcher):
    """淘宝/天猫商品搜索器"""

    @property
    def source_name(self) -> str:
        return "taobao"

    @property
    def supported_types(self) -> List[str]:
        return ["product_search", "product_detail"]

    def search(
        self,
        query: str,
        max_results: int = 10,
        platform: str = "taobao",
        sort: Optional[str] = None,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
    ) -> List[Dict]:
        """搜索淘宝/天猫商品

        Args:
            query: 搜索关键词
            max_results: 最大结果数
            platform: 平台 (taobao/tmall/both)
            sort: 排序方式 (sales/desc/price_asc/price_desc)
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式（必须启用）
            output_dir: 输出目录
            wait_timeout: 等待超时时间

        Returns:
            商品列表
        """
        print(f"[淘宝/天猫搜索] 正在搜索: {query} (平台: {platform})")

        # 淘宝反爬强，强制启用 stealth
        if not stealth:
            print("[警告] 淘宝反爬机制较强，建议启用 stealth 模式")
            stealth = True

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
        delay = random_delay(2.0, 4.0)
        print(f"  [延迟] 请求前等待 {delay:.1f} 秒")
        time.sleep(delay)

        # 构建搜索 URL
        base_url = TAOBAO_BASE if platform in ["taobao", "both"] else TMALL_BASE
        url_parts = [f"{base_url}/search?q={quote(query)}"]
        if sort:
            sort_map = {
                "sales": "salesDesc",
                "desc": "desc",
                "price_asc": "priceAsc",
                "price_desc": "priceDesc",
            }
            url_parts.append(f"coo={sort_map.get(sort, '')}")
        search_url = "&".join(url_parts)
        print(f"  [URL] {search_url}")

        # 导航到搜索结果页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", '.item, .grid-item, [class*="product"]',
            "--timeout", str(wait_timeout),
            "--stealth" if stealth else "",
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return []

        time.sleep(2.5)

        # 检查是否触发验证码/登录墙
        js_check = r"""
(() => {
  const captcha = document.querySelector('#nc_1_wrapper, .geetest_panel, [class*="captcha"]');
  const loginWall = document.querySelector('.login-wrap, [class*="login"]');
  const blocked = document.querySelector('[class*="blocked"], [class*="forbidden"]');
  if (captcha) return 'captcha_detected';
  if (loginWall) return 'login_required';
  if (blocked) return 'blocked';
  return 'ok';
})()
"""
        check_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_check,
        ])

        if check_result.returncode == 0:
            output = check_result.stdout.strip()
            if 'captcha_detected' in output:
                print("[警告] 检测到滑块验证码，请手动完成验证后重试")
                return []
            if 'login_required' in output:
                print("[警告] 需要登录，请使用已登录的浏览器实例")
                return []
            if 'blocked' in output:
                print("[警告] 访问被限制，建议更换代理或使用已登录态")

        # 提取搜索结果
        results = self._extract_products(port, tab_id, max_results)

        # 添加元数据
        for r in results:
            r["query"] = query
            r["platform"] = platform
            r["scraped_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

        print(f"  [结果] 共提取 {len(results)} 条结果")

        # 保存结果
        if output_dir:
            path = save_results(
                results,
                output_dir,
                f"taobao_{query.replace(' ', '_')}.json",
            )
            print(f"  [保存] {path}")

        return results

    def _extract_products(
        self, port: int, tab_id: str, max_results: int
    ) -> List[Dict]:
        """提取商品搜索结果"""
        js_code = r"""
(() => {
  const items = document.querySelectorAll('.item, .grid-item, [class*="product"], [class*="card"]');
  const results = [];

  items.forEach((item, i) => {
    if (i >= max_results) return;

    // 标题
    const titleEl = item.querySelector('.title, .product-title, [class*="title"] a');
    const title = titleEl ? titleEl.innerText.trim() : '';

    // 链接
    const linkEl = item.querySelector('a[href*="item"]');
    const url = linkEl ? linkEl.href : '';

    // 价格
    const priceEl = item.querySelector('.price, [class*="price"], .g_price');
    const price = priceEl ? priceEl.innerText.trim() : '';

    // 销量
    const salesEl = item.querySelector('.sales, [class*="sales"], .deal-num');
    const sales = salesEl ? salesEl.innerText.trim() : '';

    // 店铺
    const shopEl = item.querySelector('.shop, [class*="shop"], .seller-name');
    const shop = shopEl ? shopEl.innerText.trim() : '';

    // 所在地
    const locationEl = item.querySelector('.location, [class*="location"]');
    const location = locationEl ? locationEl.innerText.trim() : '';

    // 图片
    const imgEl = item.querySelector('img[data-src], img[src*="img"]');
    const image = imgEl ? (imgEl.getAttribute('data-src') || imgEl.getAttribute('src')) : '';

    // 平台标识 (淘宝/天猫)
    const isTmall = item.querySelector('.tag-tmall, [class*="tmall"], .icon-tmall');
    const platform = isTmall ? 'tmall' : 'taobao';

    if (title && url) {
      results.push({
        title: title,
        url: url,
        price: price,
        sales: sales,
        shop: shop,
        location: location,
        image: image,
        platform: platform,
        source: 'taobao',
        type: 'product'
      });
    }
  });

  return results;
})()
"""
        js_code = js_code.replace("max_results", str(max_results))
        return self._run_js_extract(port, tab_id, js_code)

    def _run_js_extract(
        self, port: int, tab_id: str, js_code: str
    ) -> List[Dict]:
        """执行 JS 提取并解析结果"""
        extract_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_code,
        ])

        if extract_result.returncode != 0:
            print(f"[错误] 内容提取失败: {extract_result.stderr[:200]}")
            return []

        try:
            raw_results = json.loads(extract_result.stdout)
        except json.JSONDecodeError:
            print(f"[错误] JSON 解析失败: {extract_result.stdout[:200]}")
            return []

        # 去重
        return dedup_results(raw_results, by="url")

    def get_detail(
        self,
        url: str,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
    ) -> Dict:
        """获取商品详情"""
        print(f"[淘宝/天猫 详情] 正在获取: {url}")

        # 确保浏览器连接
        if tab_id is None:
            result = ensure_browser(port=port, stealth=stealth)
            if result.get("error"):
                print(f"[错误] 浏览器启动失败: {result['error']}")
                return {}
            tab_id = result.get("tab_id")
            port = result.get("port", port)

        # 导航到商品详情页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", url,
            "--wait-selector", ".tb-detail, .item-info, body",
            "--timeout", "30",
            "--stealth" if stealth else "",
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return {}

        time.sleep(2.5)

        # 提取详情信息
        detail = self._extract_product_detail(port, tab_id)
        detail["url"] = url
        detail["source"] = "taobao"
        detail["scraped_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        return detail

    def _extract_product_detail(self, port: int, tab_id: str) -> Dict:
        """提取商品详情"""
        js_code = r"""
(() => {
  const result = {};

  // 标题
  const titleEl = document.querySelector('.tb-detail-hd h1, .item-title, [class*="title"] h1');
  result.title = titleEl ? titleEl.innerText.trim() : '';

  // 价格
  const priceEl = document.querySelector('.tb-price, [class*="price"]');
  result.price = priceEl ? priceEl.innerText.trim() : '';

  // 原价
  const origPriceEl = document.querySelector('.tb-original-price, [class*="original-price"]');
  result.original_price = origPriceEl ? origPriceEl.innerText.trim() : '';

  // 销量
  const salesEl = document.querySelector('.tb-sales, [class*="sales"]');
  result.sales = salesEl ? salesEl.innerText.trim() : '';

  // 评价数
  const reviewEl = document.querySelector('.tb-review, [class*="review"]');
  result.reviews = reviewEl ? reviewEl.innerText.trim() : '';

  // 店铺
  const shopEl = document.querySelector('.tb-shop, [class*="shop"] a');
  result.shop = shopEl ? shopEl.innerText.trim() : '';

  // 店铺评分
  const shopScoreEl = document.querySelector('.shop-score, [class*="shop-score"]');
  result.shop_score = shopScoreEl ? shopScoreEl.innerText.trim() : '';

  // 所在地
  const locationEl = document.querySelector('.tb-location, [class*="location"]');
  result.location = locationEl ? locationEl.innerText.trim() : '';

  // 平台标识
  const isTmall = document.querySelector('.tmall-icon, [class*="tmall"]');
  result.platform = isTmall ? 'tmall' : 'taobao';

  // 商品图片
  const images = [];
  document.querySelectorAll('.tb-img img, .slider-img img').forEach(img => {
    const src = img.getAttribute('data-src') || img.getAttribute('src');
    if (src) images.push(src);
  });
  result.images = images;

  // 商品描述
  const descEl = document.querySelector('.tb-desc, .detail-content, [class*="description"]');
  result.description = descEl ? descEl.innerText.trim() : '';

  // 规格参数
  const params = {};
  document.querySelectorAll('.tb-props tbody tr, [class*="params"] tr').forEach(row => {
    const cols = row.querySelectorAll('td');
    if (cols.length >= 2) {
      params[cols[0].innerText.trim()] = cols[1].innerText.trim();
    }
  });
  result.params = params;

  return result;
})()
"""
        extract_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_code,
        ])

        if extract_result.returncode != 0:
            print(f"[错误] 详情提取失败: {extract_result.stderr[:200]}")
            return {}

        try:
            return json.loads(extract_result.stdout)
        except json.JSONDecodeError:
            print(f"[错误] JSON 解析失败: {extract_result.stdout[:200]}")
            return {}


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="淘宝/天猫商品搜索脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python taobao_search.py "iPhone 15" --max-results 10
    python taobao_search.py "机械键盘" --max-results 5 --output-dir ./tb_results
    python taobao_search.py "笔记本电脑" --port 9333 --stealth
    python taobao_search.py "运动鞋" --platform tmall --sort sales --max-results 10
"""
    )

    parser.add_argument("query", help="搜索关键词")
    parser.add_argument(
        "--platform",
        choices=["taobao", "tmall", "both"],
        default="taobao",
        help="搜索平台 (默认: taobao)",
    )
    parser.add_argument(
        "--sort",
        choices=["sales", "desc", "price_asc", "price_desc"],
        default=None,
        help="排序方式 (默认: 相关度)",
    )
    parser.add_argument(
        "--max-results", type=int, default=10, help="最大结果数 (默认: 10)"
    )
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument(
        "--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)"
    )
    parser.add_argument("--tab", type=str, default=None, help="Tab ID")
    parser.add_argument(
        "--stealth", action="store_true", default=True, help="启用反检测模式（必须）"
    )
    parser.add_argument(
        "--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式（不推荐）"
    )
    parser.add_argument(
        "--wait-timeout", type=int, default=30, help="等待超时时间 (默认: 30秒)"
    )

    args = parser.parse_args()

    # 创建搜索器
    searcher = TaobaoSearcher()

    # 执行搜索
    results = searcher.search(
        query=args.query,
        max_results=args.max_results,
        platform=args.platform,
        sort=args.sort,
        port=args.port,
        tab_id=args.tab,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.wait_timeout,
    )

    # 输出结果
    if results:
        print(f"\n[结果] 共找到 {len(results)} 条结果")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未找到结果")


if __name__ == "__main__":
    main()
