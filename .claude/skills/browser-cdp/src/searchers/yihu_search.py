#!/usr/bin/env python
"""
yihu_search.py - 健康之路搜索器

使用 browser-cdp skill 搜索健康之路，获取健康科普、养生知识、
疾病预防、健康管理等内容。

用法:
    python yihu_search.py "高血压预防"
    python yihu_search.py "养生保健" --type article --output-dir ./yihu_results
    python yihu_search.py "糖尿病" --port 9333

示例:
    python yihu_search.py "颈椎病保健"
    python yihu_search.py "营养饮食" --output-dir ./results
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


# ========== 健康之路专用配置 ==========
YIHU_BASE = "https://www.yihu.com"
YIHU_SEARCH_URL = f"{YIHU_BASE}/search"
YIHU_ARTICLE_URL = f"{YIHU_BASE}/article"

# 默认输出目录
YIHU_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "yihu"


class YihuSearcher(BaseSearcher):
    """健康之路搜索器"""

    @property
    def source_name(self) -> str:
        return "yihu"

    @property
    def supported_types(self) -> List[str]:
        return ["article_search", "topic_search", "expert_search", "all"]

    def search(
        self,
        query: str,
        search_type: str = "all",
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
        max_results: int = 20,
    ) -> List[Dict]:
        """搜索健康科普内容

        Args:
            query: 搜索关键词
            search_type: 搜索类型 (article/topic/expert/all)
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间
            max_results: 最大结果数

        Returns:
            搜索结果列表
        """
        print(f"[健康之路] 正在搜索: {query}")

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
        if search_type in ["article", "all"]:
            print(f"  [搜索] 健康文章...")
            article_results = self._search_article(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(article_results)

        if search_type in ["topic", "all"]:
            print(f"  [搜索] 健康话题...")
            topic_results = self._search_topic(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(topic_results)

        if search_type in ["expert", "all"]:
            print(f"  [搜索] 健康专家...")
            expert_results = self._search_expert(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(expert_results)

        # 去重
        results = self._deduplicate(results)

        # 保存结果
        if results and output_dir:
            path = save_results(
                results,
                output_dir or str(YIHU_OUTPUT_DIR),
                f"yihu_{query}_{int(time.time())}.json"
            )
            print(f"  [保存] {path}")

        return results

    def _search_article(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索健康文章"""
        search_url = f"{YIHU_BASE}/search?keyword={quote(query)}"
        print(f"    [URL] 文章搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".search-result, .result-list, .article-item, .news-item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 文章搜索导航失败")
            return []

        time.sleep(2.0)

        js_article = r"""
(() => {
  const results = [];
  const selectors = [
    '.search-result .item',
    '.result-list .item',
    '.article-item',
    '.news-item',
    '.search-list li',
    '.result-item'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const titleEl = item.querySelector('.title, .name, h3, h4, a, .list-title');
    const linkEl = item.querySelector('a[href]');
    const dateEl = item.querySelector('.date, .time, .publish-date');
    const sourceEl = item.querySelector('.source, .author, .dept');
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const date = dateEl ? dateEl.innerText.trim() : '';
    const source = sourceEl ? sourceEl.innerText.trim() : '';
    
    if (title && title.length > 5) {
      results.push({
        title: title,
        url: url.startsWith('http') ? url : (url.startsWith('/') ? 'https://www.yihu.com' + url : 'https://www.yihu.com/' + url),
        publish_date: date,
        source: source,
        type: 'article',
        source_site: 'yihu',
      });
    }
  });
  
  return results;
})()
"""
        
        js_article = js_article.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_article,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def _search_topic(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索健康话题"""
        search_url = f"{YIHU_BASE}/search?keyword={quote(query)}&type=topic"
        print(f"    [URL] 话题搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".search-result, .result-list, .topic-item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 话题搜索导航失败")
            return []

        time.sleep(2.0)

        js_topic = r"""
(() => {
  const results = [];
  const selectors = [
    '.search-result .item',
    '.result-list .item',
    '.topic-item'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const titleEl = item.querySelector('.title, .name, h3, h4, a');
    const linkEl = item.querySelector('a[href]');
    const descEl = item.querySelector('.desc, .summary, .intro');
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const desc = descEl ? descEl.innerText.trim() : '';
    
    if (title && title.length > 5) {
      results.push({
        title: title,
        url: url.startsWith('http') ? url : (url.startsWith('/') ? 'https://www.yihu.com' + url : 'https://www.yihu.com/' + url),
        description: desc,
        type: 'topic',
        source_site: 'yihu',
      });
    }
  });
  
  return results;
})()
"""
        
        js_topic = js_topic.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_topic,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def _search_expert(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索健康专家"""
        search_url = f"{YIHU_BASE}/search?keyword={quote(query)}&type=expert"
        print(f"    [URL] 专家搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".search-result, .result-list, .expert-item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 专家搜索导航失败")
            return []

        time.sleep(2.0)

        js_expert = r"""
(() => {
  const results = [];
  const selectors = [
    '.search-result .item',
    '.result-list .item',
    '.expert-item'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const titleEl = item.querySelector('.title, .name, h3, h4, a');
    const linkEl = item.querySelector('a[href]');
    const deptEl = item.querySelector('.dept, .hospital, .specialty');
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const dept = deptEl ? deptEl.innerText.trim() : '';
    
    if (title && title.length > 5) {
      results.push({
        title: title,
        url: url.startsWith('http') ? url : (url.startsWith('/') ? 'https://www.yihu.com' + url : 'https://www.yihu.com/' + url),
        department: dept,
        type: 'expert',
        source_site: 'yihu',
      });
    }
  });
  
  return results;
})()
"""
        
        js_expert = js_expert.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_expert,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def _deduplicate(self, results: List[Dict]) -> List[Dict]:
        """去重"""
        seen = set()
        unique = []
        for r in results:
            key = r.get('title', '')[:50]
            if key not in seen:
                seen.add(key)
                unique.append(r)
        return unique

    def get_detail(
        self,
        url: str,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
    ) -> Dict:
        """获取指定页面详情"""
        print(f"[健康之路] 正在获取详情: {url}")

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
            "--wait-selector", ".content, .detail, article, .text, .article-content",
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
    content: '',
    publish_date: '',
    source: '',
    author: '',
  };
  
  // 提取正文
  const contentEl = document.querySelector('.content, .detail, article, .text, .article-content, .main-content');
  if (contentEl) {
    result.content = contentEl.innerText.trim().substring(0, 5000);
  }
  
  // 提取发布日期
  const dateEl = document.querySelector('.publish-date, .date, .time');
  if (dateEl) {
    result.publish_date = dateEl.innerText.trim();
  }
  
  // 提取来源
  const sourceEl = document.querySelector('.source, .dept, .department');
  if (sourceEl) {
    result.source = sourceEl.innerText.trim();
  }
  
  // 提取作者
  const authorEl = document.querySelector('.author, .writer');
  if (authorEl) {
    result.author = authorEl.innerText.trim();
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
        description="健康之路搜索器 - 获取健康科普、养生知识、疾病预防内容",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python yihu_search.py "高血压预防"
    python yihu_search.py "养生保健" --type article --output-dir ./yihu_results
    python yihu_search.py "糖尿病" --port 9333
"""
    )

    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--type", type=str, default="all",
                        choices=["article", "topic", "expert", "all"],
                        help="搜索类型 (默认: all)")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--tab", type=str, default=None, help="Tab ID")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式")
    parser.add_argument("--wait-timeout", type=int, default=30, help="等待超时时间 (默认: 30秒)")
    parser.add_argument("--max-results", type=int, default=20, help="最大结果数 (默认: 20)")

    args = parser.parse_args()

    # 创建搜索器
    searcher = YihuSearcher()

    # 执行搜索
    results = searcher.search(
        query=args.query,
        search_type=args.type,
        port=args.port,
        tab_id=args.tab,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.wait_timeout,
        max_results=args.max_results,
    )

    if results:
        print(f"\n[结果] 共获取 {len(results)} 条健康科普内容")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未获取到健康科普内容")


if __name__ == "__main__":
    main()
