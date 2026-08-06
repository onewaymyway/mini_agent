#!/usr/bin/env python
"""
douban_search.py - 豆瓣搜索自动化脚本

使用 browser-cdp skill 搜索豆瓣书籍/电影/音乐，获取评分、评价数等核心信息。
豆瓣需要登录态，建议首次使用时手动登录。

用法:
    python douban_search.py "三体" --type book --max-results 10
    python douban_search.py "肖申克的救赎" --type movie --output-dir ./douban_results
    python douban_search.py "周杰伦" --type music --port 9333

示例:
    python douban_search.py "三体" --type book --max-results 10
    python douban_search.py "肖申克的救赎" --type movie --output-dir ./douban_results
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


# ========== 豆瓣专用配置 ==========
DOUBAN_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "douban"
DOUBAN_BASE = "https://www.douban.com"

# 搜索类型映射
SEARCH_TYPES = {
    "book": "book",
    "movie": "movie",
    "music": "music",
    "play": "play",
}


class DoubanSearcher(BaseSearcher):
    """豆瓣搜索器"""
    
    @property
    def source_name(self) -> str:
        return "douban"
    
    @property
    def supported_types(self) -> List[str]:
        return ["book_search", "movie_search", "music_search", "detail"]
    
    def search(
        self,
        query: str,
        search_type: str = "book",
        max_results: int = 10,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30
    ) -> List[Dict]:
        """搜索豆瓣内容
        
        Args:
            query: 搜索关键词
            search_type: 搜索类型 (book/movie/music/play)
            max_results: 最大结果数
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间
            
        Returns:
            搜索结果列表
        """
        print(f"[豆瓣搜索] 正在搜索 {search_type}: {query}")
        
        # 验证搜索类型
        if search_type not in SEARCH_TYPES:
            print(f"[错误] 不支持的搜索类型: {search_type}")
            print(f"[提示] 支持的类型: {list(SEARCH_TYPES.keys())}")
            return []
        
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
        search_url = f"{DOUBAN_BASE}/search?query={quote(query)}&s={search_type}&type=subject"
        print(f"  [URL] {search_url}")
        
        # 导航到搜索结果页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".search-result",
            "--timeout", str(wait_timeout)
        ])
        
        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return []
        
        time.sleep(2.0)
        
        # 检查是否需要登录
        js_check = r"""
(() => {
  const loginBtn = document.querySelector('.account-base .bn-login, .login-info a');
  return loginBtn ? 'need_login' : 'ok';
})()
"""
        check_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_check
        ])
        
        if check_result.returncode == 0 and 'need_login' in check_result.stdout:
            print("[提示] 豆瓣需要登录态，请手动登录后重试")
            print("[提示] 使用 --dedicated --name douban_session 保留登录态")
            return []
        
        # 使用 JS 提取搜索结果
        js_code = r"""
(() => {
  const items = document.querySelectorAll('.search-result .result');
  const results = [];
  
  items.forEach((item, i) => {
    if (i >= 30) return;
    
    // 标题
    const titleEl = item.querySelector('.title a, .info h2 a');
    const title = titleEl ? titleEl.innerText.trim() : '';
    
    // 链接
    const linkEl = item.querySelector('.title a, .info h2 a');
    let url = linkEl ? linkEl.href : '';
    if (url && !url.startsWith('http')) {
      url = 'https:' + url;
    }
    
    // 评分
    const rateEl = item.querySelector('.rating_nums');
    const rate = rateEl ? rateEl.innerText.trim() : '';
    
    // 评价数
    const voteEl = item.querySelector('.rating_people');
    const vote = voteEl ? voteEl.innerText.trim() : '';
    
    // 简介
    const infoEl = item.querySelector('.info .pl, .info .intro');
    const info = infoEl ? infoEl.innerText.trim() : '';
    
    if (title && url) {
      results.push({
        title: title,
        url: url,
        rate: rate,
        vote: vote,
        info: info,
        source: 'douban',
        type: 'subject'
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
            r['search_type'] = search_type
            r['scraped_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
        
        print(f"  [结果] 共提取 {len(results)} 条结果")
        
        # 保存结果
        if output_dir:
            path = save_results(results, output_dir, f"douban_{search_type}_{query.replace(' ', '_')}.json")
            print(f"  [保存] {path}")
        
        return results
    
    def get_detail(
        self,
        url: str,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True
    ) -> Dict:
        """获取详情"""
        print(f"[豆瓣详情] 正在获取: {url}")
        
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
            "--wait-selector", ".subject-wrap",
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
  const titleEl = document.querySelector('h1 span, .subject-wrap h1');
  result.title = titleEl ? titleEl.innerText.trim() : '';
  
  // 评分
  const rateEl = document.querySelector('.rating_self .rating_nums');
  result.rate = rateEl ? rateEl.innerText.trim() : '';
  
  // 评价数
  const voteEl = document.querySelector('.rating_self .rating_people span');
  result.vote = voteEl ? voteEl.innerText.trim() : '';
  
  // 导演/作者
  const directorEl = document.querySelector('.subject clearfix .pl a');
  result.director = directorEl ? directorEl.innerText.trim() : '';
  
  // 简介
  const introEl = document.querySelector('.related-info .intro span, .subject .intro');
  result.intro = introEl ? introEl.innerText.trim() : '';
  
  // 标签
  const tags = [];
  document.querySelectorAll('.tags a').forEach(tag => {
    tags.push(tag.innerText.trim());
  });
  result.tags = tags;
  
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
            detail['source'] = 'douban'
            detail['url'] = url
            detail['scraped_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
            return detail
        except json.JSONDecodeError:
            print(f"[错误] JSON 解析失败: {extract_result.stdout[:200]}")
            return {}


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="豆瓣搜索自动化脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python douban_search.py "三体" --type book --max-results 10
    python douban_search.py "肖申克的救赎" --type movie --output-dir ./douban_results
    python douban_search.py "周杰伦" --type music --port 9333
"""
    )
    
    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--type", type=str, default="book",
                       choices=["book", "movie", "music", "play"],
                       help="搜索类型 (默认: book)")
    parser.add_argument("--max-results", type=int, default=10, help="最大结果数 (默认: 10)")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--tab", type=str, default=None, help="Tab ID")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式")
    parser.add_argument("--wait-timeout", type=int, default=30, help="等待超时时间 (默认: 30秒)")
    
    args = parser.parse_args()
    
    # 创建搜索器
    searcher = DoubanSearcher()
    
    # 执行搜索
    results = searcher.search(
        query=args.query,
        search_type=args.type,
        max_results=args.max_results,
        port=args.port,
        tab_id=args.tab,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.wait_timeout
    )
    
    # 输出结果
    if results:
        print(f"\n[结果] 共找到 {len(results)} 条结果")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未找到结果")


if __name__ == "__main__":
    main()
