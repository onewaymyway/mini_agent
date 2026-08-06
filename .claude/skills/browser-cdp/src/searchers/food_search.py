#!/usr/bin/env python
"""
food_search.py - 美食平台搜索器

使用 browser-cdp skill 搜索美食平台，获取餐厅信息、菜谱、外卖服务等。

用法:
    python food_search.py "北京烤鸭" --type restaurant
    python food_search.py "红烧肉" --type recipe --output-dir ./food_results
    python food_search.py "外卖" --type delivery --port 9333

示例:
    python food_search.py "北京烤鸭" --type restaurant
    python food_search.py "红烧肉" --type recipe --output-dir ./food_results
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


# ========== 美食平台专用配置 ==========
DIANPING_BASE = "https://www.dianping.com"
XIACHUFANG_BASE = "https://www.xiachufang.com"
MEISHIJIE_BASE = "https://www.meishij.net"

# 默认输出目录
FOOD_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "food"


class FoodSearcher(BaseSearcher):
    """美食平台搜索器"""

    @property
    def source_name(self) -> str:
        return "food_platform"

    @property
    def supported_types(self) -> List[str]:
        return ["restaurant_search", "recipe_search", "delivery_search"]

    def search(
        self,
        query: str,
        search_type: str = "restaurant",
        city: Optional[str] = None,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
        max_results: int = 20,
    ) -> List[Dict]:
        """搜索美食信息

        Args:
            query: 搜索关键词
            search_type: 搜索类型 (restaurant/recipe/delivery)
            city: 城市
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间
            max_results: 最大结果数

        Returns:
            搜索结果列表
        """
        print(f"[美食平台] 正在搜索: {query}")

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
        if search_type == "restaurant":
            print(f"  [搜索] 餐厅信息...")
            restaurant_results = self._search_restaurant(query, city, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(restaurant_results)

        elif search_type == "recipe":
            print(f"  [搜索] 菜谱信息...")
            recipe_results = self._search_recipe(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(recipe_results)

        elif search_type == "delivery":
            print(f"  [搜索] 外卖服务...")
            delivery_results = self._search_delivery(query, city, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(delivery_results)

        # 保存结果
        if results and output_dir:
            path = save_results(
                results,
                output_dir or str(FOOD_OUTPUT_DIR),
                f"food_{query}_{int(time.time())}.json"
            )
            print(f"  [保存] {path}")

        return results

    def _search_restaurant(
        self,
        query: str,
        city: Optional[str],
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索餐厅信息"""
        search_url = f"{DIANPING_BASE}/search/restaurant?keyword={quote(query)}"
        if city:
            search_url += f"&city={quote(city)}"
        print(f"    [URL] 餐厅搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".shop-list, .result-list, .shop-item, .restaurant-item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 餐厅搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取餐厅信息
        js_restaurant = r"""
(() => {
  const results = [];
  const selectors = [
    '.shop-list .item',
    '.result-list .item',
    '.shop-item',
    '.restaurant-item'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const nameEl = item.querySelector('.name, .shop-name, h3, h4');
    const linkEl = item.querySelector('a');
    const ratingEl = item.querySelector('.rating, .score, .star');
    const priceEl = item.querySelector('.price, .avg-price');
    const addressEl = item.querySelector('.address, .location');
    const categoryEl = item.querySelector('.category, .cuisine, .tag');
    
    const name = nameEl ? nameEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const rating = ratingEl ? ratingEl.innerText.trim() : '';
    const price = priceEl ? priceEl.innerText.trim() : '';
    const address = addressEl ? addressEl.innerText.trim() : '';
    const category = categoryEl ? categoryEl.innerText.trim() : '';
    
    if (name) {
      results.push({
        name: name,
        url: url,
        rating: rating,
        price_range: price,
        address: address,
        cuisine_type: category,
        type: 'restaurant',
        source: 'food_platform',
      });
    }
  });
  
  return results;
})()
"""
        
        js_restaurant = js_restaurant.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_restaurant,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def _search_recipe(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索菜谱信息"""
        search_url = f"{XIACHUFANG_BASE}/search/?q={quote(query)}"
        print(f"    [URL] 菜谱搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".recipe-list, .result-list, .recipe-item, .dish-item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 菜谱搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取菜谱信息
        js_recipe = r"""
(() => {
  const results = [];
  const selectors = [
    '.recipe-list .item',
    '.result-list .item',
    '.recipe-item',
    '.dish-item'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const nameEl = item.querySelector('.name, .recipe-name, h3, h4, .dish-name');
    const linkEl = item.querySelector('a');
    const authorEl = item.querySelector('.author, .cook, .user');
    const ratingEl = item.querySelector('.rating, .score, .star');
    const timeEl = item.querySelector('.time, .cook-time, .duration');
    
    const name = nameEl ? nameEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const author = authorEl ? authorEl.innerText.trim() : '';
    const rating = ratingEl ? ratingEl.innerText.trim() : '';
    const time = timeEl ? timeEl.innerText.trim() : '';
    
    if (name) {
      results.push({
        name: name,
        url: url,
        author: author,
        rating: rating,
        cook_time: time,
        type: 'recipe',
        source: 'food_platform',
      });
    }
  });
  
  return results;
})()
"""
        
        js_recipe = js_recipe.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_recipe,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    async def get_detail(self, url: str, config=None) -> Dict:
        """获取详情页内容（抽象方法实现）"""
        return {"url": url, "title": "", "content": ""}

    def _search_delivery(
        self,
        query: str,
        city: Optional[str],
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索外卖服务"""
        search_url = f"{DIANPING_BASE}/search/delivery?keyword={quote(query)}"
        if city:
            search_url += f"&city={quote(city)}"
        print(f"    [URL] 外卖搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".delivery-list, .result-list, .delivery-item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 外卖搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取外卖信息
        js_delivery = r"""
(() => {
  const results = [];
  const selectors = [
    '.delivery-list .item',
    '.result-list .item',
    '.delivery-item'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const nameEl = item.querySelector('.name, .shop-name, h3, h4');
    const linkEl = item.querySelector('a');
    const deliveryTimeEl = item.querySelector('.delivery-time, .time');
    const minOrderEl = item.querySelector('.min-order, .min-price');
    const deliveryFeeEl = item.querySelector('.delivery-fee, .fee');
    
    const name = nameEl ? nameEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const delivery_time = deliveryTimeEl ? deliveryTimeEl.innerText.trim() : '';
    const min_order = minOrderEl ? minOrderEl.innerText.trim() : '';
    const delivery_fee = deliveryFeeEl ? deliveryFeeEl.innerText.trim() : '';
    
    if (name) {
      results.push({
        name: name,
        url: url,
        delivery_time: delivery_time,
        min_order: min_order,
        delivery_fee: delivery_fee,
        type: 'delivery',
        source: 'food_platform',
      });
    }
  });
  
  return results;
})()
"""
        
        js_delivery = js_delivery.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_delivery,
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
        description="美食平台搜索器 - 获取餐厅、菜谱、外卖信息",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python food_search.py "北京烤鸭" --type restaurant
    python food_search.py "红烧肉" --type recipe --output-dir ./food_results
    python food_search.py "外卖" --type delivery --port 9333
"""
    )

    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--type", type=str, default="restaurant",
                        choices=["restaurant", "recipe", "delivery"],
                        help="搜索类型 (默认: restaurant)")
    parser.add_argument("--city", type=str, default=None, help="城市")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--tab", type=str, default=None, help="Tab ID")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式")
    parser.add_argument("--wait-timeout", type=int, default=30, help="等待超时时间 (默认: 30秒)")
    parser.add_argument("--max-results", type=int, default=20, help="最大结果数 (默认: 20)")

    args = parser.parse_args()

    # 创建搜索器
    searcher = FoodSearcher()

    # 执行搜索
    results = searcher.search(
        query=args.query,
        search_type=args.type,
        city=args.city,
        port=args.port,
        tab_id=args.tab,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.wait_timeout,
        max_results=args.max_results,
    )

    if results:
        print(f"\n[结果] 共获取 {len(results)} 条美食信息")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未获取到美食信息")


if __name__ == "__main__":
    main()
