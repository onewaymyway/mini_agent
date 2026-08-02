#!/usr/bin/env python
"""
jd_search.py - 京东商品搜索自动化脚本

使用 browser-cdp skill 搜索京东商品，获取价格、销量、评价等核心信息。

用法:
    python jd_search.py "iPhone 15" --max-results 10
    python jd_search.py "机械键盘" --max-results 5 --output-dir ./jd_results
    python jd_search.py "笔记本电脑" --port 9333 --stealth

示例:
    python jd_search.py "iPhone 15" --max-results 10
    python jd_search.py "机械键盘" --max-results 5 --output-dir ./jd_results
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


# ========== 京东专用配置 ==========
JD_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "jd"
JD_BASE = "https://www.jd.com"


# ========== 京东搜索器 ==========
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
        max_results: int = 10,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30
    ) -> List[Dict]:
        """搜索京东商品
        
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
        
        # 构建搜索 URL
        search_url = f"{JD_BASE}/search?keyword={quote(query)}&enc=utf-8"
        print(f"  [URL] {search_url}")
        
        # 导航到搜索结果页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".gl-item",
            "--timeout", str(wait_timeout)
        ])
        
        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return []
        
        # 等待页面渲染
        time.sleep(2.0)
        
        # 使用 JS 提取商品信息
        js_code = r"""
(() => {
  const items = document.querySelectorAll('.gl-item');
  const results = [];
  items.forEach((item, i) => {
    if (i >= 60) return; // 限制提取数量
    
    // 商品链接
    const linkEl = item.querySelector('a[itemprop="url"]');
    let url = linkEl ? linkEl.href : '';
    if (url && !url.startsWith('http')) {
      url = 'https:' + url;
    }
    
    // 商品标题
    const titleEl = item.querySelector('em, .th-title');
    const title = titleEl ? titleEl.innerText.replace(/\s+/g, ' ').trim() : '';
    
    // 价格
    const priceEl = item.querySelector('.p-price strong, .price strong');
    const price = priceEl ? priceEl.innerText.trim() : '';
    
    // 评价数
    const commitEl = item.querySelector('.p-commit strong');
    const commit = commitEl ? commitEl.innerText.trim() : '';
    
    // 店铺
    const shopEl = item.querySelector('.p-shop a, .shopname');
    const shop = shopEl ? shopEl.innerText.trim() : '';
    
    // SKU ID
    const skuId = item.getAttribute('data-sku') || '';
    
    // 图片
    const imgEl = item.querySelector('img');
    const img = imgEl ? (imgEl.getAttribute('data-lazy-img') || imgEl.src) : '';
    
    if (title && url) {
      results.push({
        title: title,
        url: url.replace('https://item.jd.com/', 'https://item.jd.com/'),
        price: price,
        commit: commit,
        shop: shop,
        sku_id: skuId,
        image_url: img
      });
    }
  });
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
            r['source'] = 'jd'
            r['query'] = query
            r['scraped_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
        
        print(f"  [结果] 共提取 {len(results)} 条商品")
        
        # 保存结果
        if output_dir:
            path = save_results(results, output_dir, f"jd_{query.replace(' ', '_')}.json")
            print(f"  [保存] {path}")
        
        return results
    
    def get_detail(
        self,
        url: str,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True
    ) -> Dict:
        """获取商品详情
        
        Args:
            url: 商品详情页 URL
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            
        Returns:
            商品详情
        """
        print(f"[京东详情] 正在获取: {url}")
        
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
            "--wait-selector", ".sku-name",
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
  const titleEl = document.querySelector('.sku-name, h1');
  result.title = titleEl ? titleEl.innerText.trim() : '';
  
  // 价格
  const priceEl = document.querySelector('.price strong, .p-price strong');
  result.price = priceEl ? priceEl.innerText.trim() : '';
  
  // 评价数
  const commitEl = document.querySelector('.comment-count a');
  result.commit_count = commitEl ? commitEl.innerText.trim() : '';
  
  // 店铺
  const shopEl = document.querySelector('.shopname a, .p-shop a');
  result.shop = shopEl ? shopEl.innerText.trim() : '';
  
  // 参数
  const params = {};
  const paramItems = document.querySelectorAll('.parameter2 p');
  paramItems.forEach(p => {
    const text = p.innerText.trim();
    const match = text.match(/^(.+?)\s*:\s*(.+)$/);
    if (match) {
      params[match[1].trim()] = match[2].trim();
    }
  });
  result.params = params;
  
  // 图片
  const images = [];
  document.querySelectorAll('.spec-viewer .pi-img img, .spec-viewer img').forEach(img => {
    const src = img.getAttribute('data-lazy-img') || img.src;
    if (src && !src.startsWith('data:')) {
      images.push(src);
    }
  });
  result.images = images;
  
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
            detail['source'] = 'jd'
            detail['url'] = url
            detail['scraped_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
            return detail
        except json.JSONDecodeError:
            print(f"[错误] JSON 解析失败: {extract_result.stdout[:200]}")
            return {}


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="京东商品搜索自动化脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python jd_search.py "iPhone 15" --max-results 10
    python jd_search.py "机械键盘" --max-results 5 --output-dir ./jd_results
    python jd_search.py "笔记本电脑" --port 9333 --stealth
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
    searcher = JDSearcher()
    
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
