#!/usr/bin/env python
"""
xianyu_search.py - 闲鱼搜索器

使用 browser-cdp skill 搜索闲鱼，获取二手商品信息。

用法:
    python xianyu_search.py "iPhone 15"
    python xianyu_search.py "机械键盘" --condition 95新
    python xianyu_search.py "笔记本电脑" --output-dir ./xianyu_results

示例:
    python xianyu_search.py "iPhone 15"
    python xianyu_search.py "机械键盘" --condition 95新
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


# ========== 闲鱼专用配置 ==========
XIANYU_BASE = "https://www.goofish.com"
XIANYU_SEARCH_URL = f"{XIANYU_BASE}/search?q={quote('{keyword}')}"

# 默认输出目录
XIANYU_OUTPUT_DIR = Path(__file__).parent.parent.parent / "search_results" / "xianyu"

# 增强选择器 - 支持多种页面结构
XIANYU_SELECTORS = {
    'search_input': ["input[placeholder*='搜索'], input[name='q'], .search-input input, #searchInput"],
    'item_list': [".item, .goods-item, .search-item, .list-item, [class*='item'], [class*='goods']"],
    'item_link': ["a[href*='item'], a[href*='product']"],
    'item_title': [".title, .name, .goods-title, [class*='title']"],
    'item_price': [".price, .money, .amount, [class*='price']"],
    'item_location': [".location, .place, [class*='location']"],
    'item_seller': [".seller, .user-name, [class*='seller']"],
}


class XianyuSearcher(BaseSearcher):
    """闲鱼搜索器"""

    @property
    def source_name(self) -> str:
        return "xianyu"

    @property
    def supported_types(self) -> List[str]:
        return ["secondhand_search", "product_search", "used_goods"]

    def search(
        self,
        query: str,
        condition: Optional[str] = None,
        max_results: int = 20,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
    ) -> List[Dict]:
        """搜索二手商品

        Args:
            query: 搜索关键词
            condition: 成色要求（如：95新、全新）
            max_results: 最大结果数
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间

        Returns:
            商品信息列表
        """
        print(f"[闲鱼] 正在搜索: {query}")
        if condition:
            print(f"  成色: {condition}")

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
        search_url = f"{XIANYU_BASE}/search?q={quote(query)}"
        if condition:
            search_url += f"&condition={quote(condition)}"
        
        print(f"  [URL] 搜索: {search_url}")

        # 使用增强选择器等待页面加载
        wait_selectors = ", ".join(XIANYU_SELECTORS['item_list'][:3])
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", wait_selectors,
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return []

        time.sleep(2.0)

        # 步骤2: 提取搜索结果（使用增强选择器）
        js_search = r"""
(() => {
  const results = [];
  
  // 尝试多种选择器匹配商品项
  const selectors = ['.item', '.goods-item', '.search-item', '.list-item', '[class*="item"]', '[class*="goods"]'];
  let items = [];
  for (const sel of selectors) {
    items = document.querySelectorAll(sel);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= 20) return;
    
    // 尝试多种选择器匹配链接
    const linkSel = 'a[href*="item"], a[href*="product"], a[class*="link"]';
    const linkEl = item.querySelector(linkSel) || item.querySelector('a[href]');
    
    // 尝试多种选择器匹配标题
    const titleSel = '.title, .name, .goods-title, [class*="title"]';
    const titleEl = item.querySelector(titleSel);
    
    // 尝试多种选择器匹配价格
    const priceSel = '.price, .money, .amount, [class*="price"]';
    const priceEl = item.querySelector(priceSel);
    
    // 尝试多种选择器匹配位置
    const locationSel = '.location, .place, [class*="location"]';
    const locationEl = item.querySelector(locationSel);
    
    // 尝试多种选择器匹配卖家
    const sellerSel = '.seller, .user-name, [class*="seller"]';
    const sellerEl = item.querySelector(sellerSel);
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const price = priceEl ? priceEl.innerText.trim() : '';
    const location = locationEl ? locationEl.innerText.trim() : '';
    const seller = sellerEl ? sellerEl.innerText.trim() : '';
    const href = linkEl ? linkEl.href : '';
    
    if (title) {
      results.push({
        title: title,
        price: price,
        location: location,
        seller: seller,
        url: href,
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
            print(f"[提示] 未找到搜索结果，可能需要登录")
            return []

        print(f"  [结果] 找到 {len(items)} 条结果")

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
                final_results.append(item)

        # 保存结果
        if output_dir:
            path = save_results(
                final_results,
                output_dir,
                f"xianyu_{query}_{int(time.time())}.json"
            )
            print(f"  [保存] {path}")

        return final_results

    def _get_detail(
        self,
        port: int,
        tab_id: str,
        url: str,
        stealth: bool,
        wait_timeout: int,
    ) -> Optional[Dict]:
        """获取商品详情页内容"""
        if not url:
            return None

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", url,
            "--wait-selector", ".detail, .goods-detail, article",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            return None

        time.sleep(1.5)

        js_detail = r"""
(() => {
  const result = {};
  const titleSel = '.goods-title, h1, .title';
  const titleEl = document.querySelector(titleSel);
  result.title = titleEl ? titleEl.innerText.trim() : '';
  
  const priceSel = '.price, .money';
  const priceEl = document.querySelector(priceSel);
  result.price = priceEl ? priceEl.innerText.trim() : '';
  
  const descSel = '.description, .desc, .text';
  const descEl = document.querySelector(descSel);
  result.description = descEl ? descEl.innerText.trim().substring(0, 500) : '';
  
  const sellerSel = '.seller-name, .user-name';
  const sellerEl = document.querySelector(sellerSel);
  result.seller = sellerEl ? sellerEl.innerText.trim() : '';
  
  const locationSel = '.location, .place';
  const locationEl = document.querySelector(locationSel);
  result.location = locationEl ? locationEl.innerText.trim() : '';
  
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

    async def _smart_wait(self, browser, config: SearcherConfig):
        """智能等待页面加载"""
        from src.core.smart_wait import SmartWait
        wait_handler = SmartWait(browser.session)
        await wait_handler.wait_for(config.wait_strategy, idle_timeout=config.wait_timeout)

    async def _extract_results(self, browser, query: str) -> List[Dict]:
        """提取搜索结果"""
        js_code = r"""
        (() => {
            const results = [];
            const selectors = ['.item', '.goods-item', '.search-item', '.list-item'];
            let items = [];
            for (const sel of selectors) {
                items = document.querySelectorAll(sel);
                if (items.length > 0) break;
            }
            items.forEach((item, i) => {
                if (i >= 20) return;
                const linkEl = item.querySelector('a[href*="item"], a[href*="product"]');
                const titleEl = item.querySelector('.title, .name, [class*="title"]');
                const priceEl = item.querySelector('.price, .money, [class*="price"]');
                if (titleEl && linkEl) {
                    results.push({
                        title: titleEl.innerText.trim(),
                        price: priceEl ? priceEl.innerText.trim() : '',
                        url: linkEl.href,
                        source: 'xianyu'
                    });
                }
            });
            return results;
        })()
        """
        return await browser.evaluate(js_code)


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
        description="闲鱼搜索器 - 获取二手商品信息",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python xianyu_search.py "iPhone 15"
    python xianyu_search.py "机械键盘" --condition 95新
    python xianyu_search.py "笔记本电脑" --output-dir ./xianyu_results
"""
    )

    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--condition", type=str, default=None, help="成色要求（如：95新、全新）")
    parser.add_argument("--max-results", type=int, default=20, help="最大结果数 (默认: 20)")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--tab", type=str, default=None, help="Tab ID")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式")
    parser.add_argument("--wait-timeout", type=int, default=30, help="等待超时时间 (默认: 30秒)")

    args = parser.parse_args()

    # 创建搜索器
    searcher = XianyuSearcher()

    # 执行搜索
    results = searcher.search(
        query=args.query,
        condition=args.condition,
        max_results=args.max_results,
        port=args.port,
        tab_id=args.tab,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.wait_timeout,
    )

    # 输出结果
    if results:
        print(f"\n[结果] 共获取 {len(results)} 条商品信息")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未获取到商品信息")


if __name__ == "__main__":
    main()
