#!/usr/bin/env python
"""
scholar_search.py - Google Scholar 学术论文搜索脚本

使用 browser-cdp skill 搜索 Google Scholar 学术论文，获取标题、作者、摘要、引用数等信息。
Google Scholar 有反爬机制，建议使用 stealth 模式并控制请求频率。

用法:
    python scholar_search.py "machine learning" --max-results 10
    python scholar_search.py "transformer architecture" --max-results 5 --output-dir ./scholar_results
    python scholar_search.py "reinforcement learning" --port 9333 --stealth

示例:
    python scholar_search.py "machine learning" --max-results 10
    python scholar_search.py "transformer architecture" --max-results 5 --output-dir ./scholar_results
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
from src.searchers.utils import random_delay, save_results, dedup_results
from src.searchers.browser_utils import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


# ========== Google Scholar 专用配置 ==========
SCHOLAR_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "scholar"
SCHOLAR_BASE = "https://scholar.google.com"


# ========== Google Scholar 搜索器 ==========
class ScholarSearcher(BaseSearcher):
    """Google Scholar 学术论文搜索器"""
    
    @property
    def source_name(self) -> str:
        return "google_scholar"
    
    @property
    def supported_types(self) -> List[str]:
        return ["paper_search", "paper_detail"]
    
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
        """搜索 Google Scholar 论文
        
        Args:
            query: 搜索关键词
            max_results: 最大结果数
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间
            
        Returns:
            论文列表
        """
        print(f"[Google Scholar] 正在搜索: {query}")
        
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
        
        # 构建搜索 URL
        search_url = f"{SCHOLAR_BASE}/scholar?q={quote(query)}&hl=zh-CN&as_sdt=0,5"
        print(f"  [URL] {search_url}")
        
        # 导航到搜索结果页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".gs_r",
            "--timeout", str(wait_timeout)
        ])
        
        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return []
        
        time.sleep(2.0)
        
        # 检查是否触发验证码
        js_check = r"""
(() => {
  const captcha = document.querySelector('#captcha, .g-recaptcha, [class*="captcha"]');
  return captcha ? 'captcha_detected' : 'ok';
})()
"""
        check_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_check
        ])
        
        if check_result.returncode == 0 and 'captcha_detected' in check_result.stdout:
            print("[警告] 检测到验证码，请手动完成验证后重试")
            return []
        
        # 使用 JS 提取论文列表
        js_code = r"""
(() => {
  const items = document.querySelectorAll('.gs_r');
  const results = [];
  
  items.forEach((item, i) => {
    if (i >= 20) return;
    
    // 标题
    const titleEl = item.querySelector('.gs_rt a');
    const title = titleEl ? titleEl.innerText.trim() : '';
    
    // 链接
    const linkEl = item.querySelector('.gs_rt a');
    let url = linkEl ? linkEl.href : '';
    
    // 作者
    const authorEl = item.querySelector('.gs_a');
    const author = authorEl ? authorEl.innerText.trim() : '';
    
    // 摘要
    const snippetEl = item.querySelector('.gs_rs');
    const snippet = snippetEl ? snippetEl.innerText.trim() : '';
    
    // 引用数
    const citedEl = item.querySelector('.gs_fl a[href*="cites"]');
    const cited = citedEl ? citedEl.innerText.trim() : '';
    
    // 年份
    const yearMatch = author.match(/(\d{4})/);
    const year = yearMatch ? yearMatch[1] : '';
    
    if (title && url) {
      results.push({
        title: title,
        url: url,
        author: author,
        snippet: snippet,
        cited: cited,
        year: year,
        source: 'google_scholar'
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
            r['query'] = query
            r['scraped_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
        
        print(f"  [结果] 共提取 {len(results)} 篇论文")
        
        # 保存结果
        if output_dir:
            path = save_results(results, output_dir, f"scholar_{query.replace(' ', '_')}.json")
            print(f"  [保存] {path}")
        
        return results
    
    def get_detail(
        self,
        url: str,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True
    ) -> Dict:
        """获取论文详情"""
        print(f"[Google Scholar 详情] 正在获取: {url}")
        
        # 确保浏览器连接
        if tab_id is None:
            result = ensure_browser(port=port, stealth=stealth)
            if result.get("error"):
                print(f"[错误] 浏览器启动失败: {result['error']}")
                return {}
            tab_id = result.get("tab_id")
            port = result.get("port", port)
        
        # 导航到论文详情页
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
  const titleEl = document.querySelector('h1, .gs_rt h1');
  result.title = titleEl ? titleEl.innerText.trim() : '';
  
  // 作者
  const authorEl = document.querySelector('.gs_a, .author');
  result.author = authorEl ? authorEl.innerText.trim() : '';
  
  // 摘要
  const snippetEl = document.querySelector('.gs_rs, .abstract');
  result.snippet = snippetEl ? snippetEl.innerText.trim() : '';
  
  // 期刊/会议
  const venueEl = document.querySelector('.gs_a');
  result.venue = venueEl ? venueEl.innerText.trim() : '';
  
  // 年份
  const yearMatch = result.venue.match(/(\d{4})/);
  result.year = yearMatch ? yearMatch[1] : '';
  
  // 引用数
  const citedEl = document.querySelector('.gs_fl a[href*="cites"]');
  result.cited = citedEl ? citedEl.innerText.trim() : '';
  
  // PDF 链接
  const pdfEl = document.querySelector('a[href*="pdf"], .gs_or_gtmsmm a');
  result.pdf_url = pdfEl ? pdfEl.href : '';
  
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
            detail['source'] = 'google_scholar'
            detail['url'] = url
            detail['scraped_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
            return detail
        except json.JSONDecodeError:
            print(f"[错误] JSON 解析失败: {extract_result.stdout[:200]}")
            return {}


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="Google Scholar 学术论文搜索脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python scholar_search.py "machine learning" --max-results 10
    python scholar_search.py "transformer architecture" --max-results 5 --output-dir ./scholar_results
    python scholar_search.py "reinforcement learning" --port 9333 --stealth
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
    
    args = parser.parse_args()
    
    # 创建搜索器
    searcher = ScholarSearcher()
    
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
        print(f"\n[结果] 共找到 {len(results)} 篇论文")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未找到论文")


if __name__ == "__main__":
    main()
