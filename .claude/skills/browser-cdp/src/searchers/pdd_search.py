#!/usr/bin/env python
"""
pdd_search.py - 拼多多商品搜索自动化脚本

使用 browser-cdp skill 搜索拼多多商品，获取价格、销量、店铺信息。
拼多多反爬相对较弱，可直接调用移动端 API。

用法:
    python pdd_search.py "手机壳" --max-results 10
    python pdd_search.py "蓝牙耳机" --max-results 5 --output-dir ./pdd_results
    python pdd_search.py "充电宝" --port 9333

示例:
    python pdd_search.py "手机壳" --max-results 10
    python pdd_search.py "蓝牙耳机" --max-results 5 --output-dir ./pdd_results
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
from src.searchers.browser_utils import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


# ========== 拼多多专用配置 ==========
PDD_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "pdd"
PDD_API_BASE = "https://mobile.yangkeduo.com/proxy/api"


# ========== 拼多多搜索器 ==========
class PDDSearcher(BaseSearcher):
    """拼多多商品搜索器"""
    
    @property
    def source_name(self) -> str:
        return "pdd"
    
    @property
    def supported_types(self) -> List[str]:
        return ["product_search", "product_detail"]
    
    def search(
        self,
        query: str,
        max_results: int = 10,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30
    ) -> List[Dict]:
        """搜索拼多多商品
        
        Args:
            query: 搜索关键词
            max_results: 最大结果数
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间
            
        Returns:
            商品列表
        """
        print(f"[拼多多搜索] 正在搜索: {query}")
        
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
        
        # 构建搜索 URL（移动端 API）
        search_url = f"{PDD_API_BASE}/search?keyword={quote(query)}&page=1&page_size=20"
        print(f"  [URL] {search_url}")
        
        # 导航到搜索页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", "body",
            "--timeout", str(wait_timeout)
        ])
        
        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return []
        
        time.sleep(1.5)
        
        # 使用 JS 提取商品信息
        js_code = r"""
(() => {
  const results = [];
  
  // 尝试从页面提取商品数据
  const goodsItems = document.querySelectorAll('.goods-item, [class*="goods"], .list-item');
  
  goodsItems.forEach((item, i) => {
    if (i >= 20) return;
    
    const titleEl = item.querySelector('[class*="title"], [class*="name"], .goods-title');
    const title = titleEl ? titleEl.innerText.trim() : '';
    
    const priceEl = item.querySelector('[class*="price"], .price');
    const price = priceEl ? priceEl.innerText.trim() : '';
    
    const salesEl = item.querySelector('[class*="sales"], .sold');
    const sales = salesEl ? salesEl.innerText.trim() : '';
    
    const shopEl = item.querySelector('[class*="shop"], .shop-name');
    const shop = shopEl ? shopEl.innerText.trim() : '';
    
    const linkEl = item.querySelector('a[href*="goods_id"]');
    const url = linkEl ? linkEl.href : '';
    
    if (title && price) {
      results.push({
        title: title,
        price: price,
        sales: sales,
        shop: shop,
        url: url
      });
    }
  });
  
  // 如果 DOM 提取失败，尝试从 window 对象获取
  if (results.length === 0 && window.__INIT_STATE__) {
    const data = window.__INIT_STATE__;
    if (data.goodsList) {
      data.goodsList.forEach(g => {
        results.push({
          title: g.goods_name || '',
          price: g.group_price || g.price || '',
          sales: g.sales_desc || '',
          shop: g.merchant_name || '',
          url: g.goods_url || ''
        });
      });
    }
  }
  
  return results;
})()
"""
        
        extract_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_code
        ])
        
        if extract_result.returncode != 0:
            print(f"[错误] 内容提取失败: {extract_result.stderr[:200]}")
            return []
        
        try:
            raw_results = json.loads(extract_result.stdout)
        except json.JSONDecodeError:
            print(f"[错误] JSON 解析失败: {extract_result.stdout[:200]}")
            return []
        
        # 去重和限制数量
        results = dedup_results(raw_results, by="url")[:max_results]
        
        # 添加元数据
        for r in results:
            r['source'] = 'pdd'
            r['query'] = query
            r['scraped_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
        
        print(f"  [结果] 共提取 {len(results)} 条商品")
        
        # 保存结果
        if output_dir:
            path = save_results(results, output_dir, f"pdd_{query.replace(' ', '_')}.json")
            print(f"  [保存] {path}")
        
        return results
    
    def get_detail(
        self,
        url: str,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True
    ) -> Dict:
        """获取商品详情"""
        print(f"[拼多多详情] 正在获取: {url}")
        
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
            "--wait-selector", "body",
            "--timeout", "30"
        ])
        
        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return {}
        
        time.sleep(1.5)
        
        # 提取详情信息
        js_code = r"""
(() => {
  const result = {};
  
  // 标题
  const titleEl = document.querySelector('[class*="title"], .goods-name');
  result.title = titleEl ? titleEl.innerText.trim() : '';
  
  // 价格
  const priceEl = document.querySelector('[class*="price"], .price');
  result.price = priceEl ? priceEl.innerText.trim() : '';
  
  // 原价
  const originalPriceEl = document.querySelector('[class*="original"], .original-price');
  result.original_price = originalPriceEl ? originalPriceEl.innerText.trim() : '';
  
  // 销量
  const salesEl = document.querySelector('[class*="sales"], .sold');
  result.sales = salesEl ? salesEl.innerText.trim() : '';
  
  // 店铺
  const shopEl = document.querySelector('[class*="shop"], .shop-name');
  result.shop = shopEl ? shopEl.innerText.trim() : '';
  
  // 图片
  const images = [];
  document.querySelectorAll('img').forEach(img => {
    const src = img.src || img.getAttribute('data-src');
    if (src && !src.startsWith('data:') && src.length > 20) {
      images.push(src);
    }
  });
  result.images = images.slice(0, 5);
  
  return result;
})()
"""
        
        extract_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_code
        ])
        
        if extract_result.returncode != 0:
            print(f"[错误] 详情提取失败: {extract_result.stderr[:200]}")
            return {}
        
        try:
            detail = json.loads(extract_result.stdout)
            detail['source'] = 'pdd'
            detail['url'] = url
            detail['scraped_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
            return detail
        except json.JSONDecodeError:
            print(f"[错误] JSON 解析失败: {extract_result.stdout[:200]}")
            return {}


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="拼多多商品搜索自动化脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python pdd_search.py "手机壳" --max-results 10
    python pdd_search.py "蓝牙耳机" --max-results 5 --output-dir ./pdd_results
    python pdd_search.py "充电宝" --port 9333
"""
    )
    
    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--max-results", type=int, default=10, help="最大结果数 (默认: 10)")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--tab", type=str, default=None, help="Tab ID")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式")
    parser.add_argument("--wait-timeout", type=int, default=30, help="等待超时时间 (默认: 30秒)")
    parser.add_argument("--detail", action="store_true", help="获取商品详情")
    
    args = parser.parse_args()
    
    # 创建搜索器
    searcher = PDDSearcher()
    
    # 执行搜索
    results = searcher.search(
        query=args.query,
        max_results=args.max_results,
        port=args.port,
        tab_id=args.tab,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.wait_timeout
    )
    
    # 输出结果
    if results:
        print(f"\n[结果] 共找到 {len(results)} 条商品")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未找到商品")


if __name__ == "__main__":
    main()
