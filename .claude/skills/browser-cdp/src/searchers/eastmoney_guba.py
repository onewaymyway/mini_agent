#!/usr/bin/env python
"""
eastmoney_guba.py - 东方财富股吧帖子抓取脚本

使用 browser-cdp skill 抓取东方财富股吧帖子列表和详情。
支持按股票代码搜索帖子，获取阅读量、评论数、发布时间等核心信息。

用法:
    python eastmoney_guba.py 600519 --max-posts 20
    python eastmoney_guba.py 000001 --sort hot --max-posts 10 --output-dir ./guba_results
    python eastmoney_guba.py 300750 --sort time --page 2

示例:
    python eastmoney_guba.py 600519 --max-posts 20
    python eastmoney_guba.py 000001 --sort hot --max-posts 10 --output-dir ./guba_results
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


# ========== 东方财富股吧专用配置 ==========
GUBA_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "eastmoney_guba"
GUBA_BASE = "https://guba.eastmoney.com"


# ========== 东方财富股吧搜索器 ==========
class EastmoneyGubaSearcher(BaseSearcher):
    """东方财富股吧帖子搜索器"""
    
    @property
    def source_name(self) -> str:
        return "eastmoney_guba"
    
    @property
    def supported_types(self) -> List[str]:
        return ["post_list", "post_detail", "comment_tree"]
    
    def search(
        self,
        stock_code: str,
        max_posts: int = 20,
        sort: str = "time",
        page: int = 1,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30
    ) -> List[Dict]:
        """搜索股吧帖子
        
        Args:
            stock_code: 股票代码（如 600519）
            max_posts: 最大帖子数
            sort: 排序方式 (time/hot)
            page: 页码
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间
            
        Returns:
            帖子列表
        """
        print(f"[东方财富股吧] 正在搜索股票 {stock_code} 的帖子")
        
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
        sort_param = "hot" if sort == "hot" else "time"
        search_url = f"{GUBA_BASE}/list/{stock_code}.html?sort={sort_param}&page={page}"
        print(f"  [URL] {search_url}")
        
        # 导航到股吧列表页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".listItem",
            "--timeout", str(wait_timeout)
        ])
        
        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return []
        
        time.sleep(2.0)
        
        # 使用 JS 提取帖子列表
        js_code = r"""
(() => {
  const items = document.querySelectorAll('.listItem');
  const results = [];
  
  items.forEach((item, i) => {
    if (i >= 50) return;
    
    // 标题
    const titleEl = item.querySelector('.listTitle a');
    const title = titleEl ? titleEl.innerText.trim() : '';
    
    // 链接
    const linkEl = item.querySelector('.listTitle a');
    let url = linkEl ? linkEl.href : '';
    if (url && !url.startsWith('http')) {
      url = 'https:' + url;
    }
    
    // 阅读量
    const readEl = item.querySelector('.listRead');
    const read = readEl ? readEl.innerText.trim() : '';
    
    // 评论数
    const commentEl = item.querySelector('.listComment');
    const comment = commentEl ? commentEl.innerText.trim() : '';
    
    // 发布时间
    const timeEl = item.querySelector('.listTime');
    const time = timeEl ? timeEl.innerText.trim() : '';
    
    // 作者
    const authorEl = item.querySelector('.listAuthor');
    const author = authorEl ? authorEl.innerText.trim() : '';
    
    if (title && url) {
      results.push({
        title: title,
        url: url,
        read: read,
        comment: comment,
        time: time,
        author: author,
        source: 'eastmoney_guba'
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
        results = dedup_results(raw_results, by="url")[:max_posts]
        
        # 添加元数据
        for r in results:
            r['stock_code'] = stock_code
            r['sort'] = sort
            r['page'] = page
            r['scraped_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
        
        print(f"  [结果] 共提取 {len(results)} 条帖子")
        
        # 保存结果
        if output_dir:
            path = save_results(results, output_dir, f"guba_{stock_code}_{sort}.json")
            print(f"  [保存] {path}")
        
        return results
    
    def get_detail(
        self,
        url: str,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True
    ) -> Dict:
        """获取帖子详情"""
        print(f"[东方财富股吧详情] 正在获取: {url}")
        
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
            "--wait-selector", ".post_content",
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
  const titleEl = document.querySelector('.article h1, .title h1');
  result.title = titleEl ? titleEl.innerText.trim() : '';
  
  // 正文
  const contentEl = document.querySelector('.post_content, .article_content, #content');
  result.content = contentEl ? contentEl.innerText.trim() : '';
  
  // 作者
  const authorEl = document.querySelector('.author, .post_author');
  result.author = authorEl ? authorEl.innerText.trim() : '';
  
  // 时间
  const timeEl = document.querySelector('.time, .post_time');
  result.time = timeEl ? timeEl.innerText.trim() : '';
  
  // 阅读量
  const readEl = document.querySelector('.read_count, .browse');
  result.read = readEl ? readEl.innerText.trim() : '';
  
  // 评论数
  const commentEl = document.querySelector('.comment_count, .reply_count');
  result.comment_count = commentEl ? commentEl.innerText.trim() : '';
  
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
            detail['source'] = 'eastmoney_guba'
            detail['url'] = url
            detail['scraped_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
            return detail
        except json.JSONDecodeError:
            print(f"[错误] JSON 解析失败: {extract_result.stdout[:200]}")
            return {}
    
    def get_comments(
        self,
        post_id: str,
        max_comments: int = 50,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True
    ) -> List[Dict]:
        """获取帖子评论树"""
        print(f"[东方财富股吧评论] 正在获取帖子 {post_id} 的评论")
        
        # 确保浏览器连接
        if tab_id is None:
            result = ensure_browser(port=port, stealth=stealth)
            if result.get("error"):
                print(f"[错误] 浏览器启动失败: {result['error']}")
                return []
            tab_id = result.get("tab_id")
            port = result.get("port", port)
        
        # 导航到评论页
        comment_url = f"{GUBA_BASE}/comment/{post_id}.html"
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", comment_url,
            "--wait-selector", ".comment_item",
            "--timeout", "30"
        ])
        
        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return []
        
        time.sleep(1.5)
        
        # 提取评论列表
        js_code = r"""
(() => {
  const items = document.querySelectorAll('.comment_item, .reply_item');
  const results = [];
  
  items.forEach((item, i) => {
    if (i >= 100) return;
    
    const contentEl = item.querySelector('.comment_content, .reply_content');
    const content = contentEl ? contentEl.innerText.trim() : '';
    
    const authorEl = item.querySelector('.comment_author, .reply_author');
    const author = authorEl ? authorEl.innerText.trim() : '';
    
    const timeEl = item.querySelector('.comment_time, .reply_time');
    const time = timeEl ? timeEl.innerText.trim() : '';
    
    const likeEl = item.querySelector('.like_count, .praise');
    const likes = likeEl ? likeEl.innerText.trim() : '';
    
    if (content) {
      results.push({
        content: content,
        author: author,
        time: time,
        likes: likes,
        source: 'eastmoney_guba'
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
            print(f"[错误] 评论提取失败: {extract_result.stderr[:200]}")
            return []
        
        try:
            comments = json.loads(extract_result.stdout)
        except json.JSONDecodeError:
            print(f"[错误] JSON 解析失败: {extract_result.stdout[:200]}")
            return []
        
        # 限制数量
        comments = comments[:max_comments]
        
        # 添加元数据
        for c in comments:
            c['post_id'] = post_id
            c['scraped_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
        
        print(f"  [结果] 共提取 {len(comments)} 条评论")
        
        return comments


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="东方财富股吧帖子抓取脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python eastmoney_guba.py 600519 --max-posts 20
    python eastmoney_guba.py 000001 --sort hot --max-posts 10 --output-dir ./guba_results
    python eastmoney_guba.py 300750 --sort time --page 2
"""
    )
    
    parser.add_argument("stock_code", help="股票代码（如 600519）")
    parser.add_argument("--max-posts", type=int, default=20, help="最大帖子数 (默认: 20)")
    parser.add_argument("--sort", type=str, default="time", choices=["time", "hot"],
                       help="排序方式 (默认: time)")
    parser.add_argument("--page", type=int, default=1, help="页码 (默认: 1)")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--tab", type=str, default=None, help="Tab ID")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式")
    parser.add_argument("--wait-timeout", type=int, default=30, help="等待超时时间 (默认: 30秒)")
    parser.add_argument("--detail", action="store_true", help="获取帖子详情")
    parser.add_argument("--comments", action="store_true", help="获取评论列表")
    
    args = parser.parse_args()
    
    # 创建搜索器
    searcher = EastmoneyGubaSearcher()
    
    # 执行搜索
    results = searcher.search(
        stock_code=args.stock_code,
        max_posts=args.max_posts,
        sort=args.sort,
        page=args.page,
        port=args.port,
        tab_id=args.tab,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.wait_timeout
    )
    
    # 输出结果
    if results:
        print(f"\n[结果] 共找到 {len(results)} 条帖子")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未找到帖子")


if __name__ == "__main__":
    main()
