#!/usr/bin/env python
"""
huilv_search.py - 华律网搜索器

使用 browser-cdp skill 搜索华律网，获取律师信息、法律咨询、法律知识等。

用法:
    python huilv_search.py "合同纠纷"
    python huilv_search.py "律师" --city 北京 --output-dir ./huilv_results
    python huilv_search.py "法律咨询" --port 9333

示例:
    python huilv_search.py "离婚律师"
    python huilv_search.py "劳动合同" --city 上海
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


# ========== 华律网专用配置 ==========
HUILV_BASE = "https://www.64365.com"
HUILV_SEARCH_URL = f"{HUILV_BASE}/search?keyword={quote('{keyword}')}"
HUILV_LAWYER_URL = f"{HUILV_BASE}/lawyer/search?keyword={quote('{keyword}')}"
HUILV_QA_URL = f"{HUILV_BASE}/ask/search?keyword={quote('{keyword}')}"

# 默认输出目录
HUILV_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "huilv"


class HuilvSearcher(BaseSearcher):
    """华律网搜索器"""

    @property
    def source_name(self) -> str:
        return "huilv"

    @property
    def supported_types(self) -> List[str]:
        return ["lawyer_search", "legal_qa", "legal_article"]

    def search(
        self,
        query: str,
        search_type: str = "all",
        city: Optional[str] = None,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
        max_results: int = 20,
    ) -> List[Dict]:
        """搜索华律网信息

        Args:
            query: 搜索关键词
            search_type: 搜索类型 (lawyer/qa/article/all)
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
        print(f"[华律网] 正在搜索: {query}")

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
        if search_type in ["lawyer", "all"]:
            print(f"  [搜索] 律师信息...")
            lawyer_results = self._search_lawyer(query, city, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(lawyer_results)

        if search_type in ["qa", "all"]:
            print(f"  [搜索] 法律咨询...")
            qa_results = self._search_qa(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(qa_results)

        if search_type in ["article", "all"]:
            print(f"  [搜索] 法律知识...")
            article_results = self._search_article(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(article_results)

        # 保存结果
        if results and output_dir:
            path = save_results(
                results,
                output_dir or str(HUILV_OUTPUT_DIR),
                f"huilv_{query}_{int(time.time())}.json"
            )
            print(f"  [保存] {path}")

        return results

    def _search_lawyer(
        self,
        query: str,
        city: Optional[str],
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索律师信息"""
        search_url = f"{HUILV_LAWYER_URL}"
        if city:
            search_url += f"&city={quote(city)}"
        print(f"    [URL] 律师搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".lawyer-list, .result-list, .lawyer-item, table",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 律师搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取律师信息
        js_lawyer = r"""
(() => {
  const results = [];
  const selectors = [
    '.lawyer-list .item',
    '.result-list .item',
    '.lawyer-item',
    'table tbody tr'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const nameEl = item.querySelector('.name, .lawyer-name, h3, h4');
    const linkEl = item.querySelector('a');
    const firmEl = item.querySelector('.firm, .law-firm, .company');
    const specialtyEl = item.querySelector('.specialty, .field, .practice-area');
    const ratingEl = item.querySelector('.rating, .score');
    
    const name = nameEl ? nameEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const firm = firmEl ? firmEl.innerText.trim() : '';
    const specialty = specialtyEl ? specialtyEl.innerText.trim() : '';
    const rating = ratingEl ? ratingEl.innerText.trim() : '';
    
    if (name) {
      results.push({
        name: name,
        url: url,
        law_firm: firm,
        specialty: specialty,
        rating: rating,
        type: 'lawyer',
        source: 'huilv',
      });
    }
  });
  
  return results;
})()
"""
        
        js_lawyer = js_lawyer.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_lawyer,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def _search_qa(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索法律咨询"""
        search_url = f"{HUILV_QA_URL}"
        print(f"    [URL] 咨询搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".qa-list, .result-list, .qa-item, table",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 咨询搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取咨询信息
        js_qa = r"""
(() => {
  const results = [];
  const selectors = [
    '.qa-list .item',
    '.result-list .item',
    '.qa-item',
    'table tbody tr'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const titleEl = item.querySelector('.title, .question, h3, h4');
    const linkEl = item.querySelector('a');
    const answerEl = item.querySelector('.answer, .reply, .content');
    const dateEl = item.querySelector('.date, .time');
    const lawyerEl = item.querySelector('.lawyer, .asker');
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const answer = answerEl ? answerEl.innerText.trim().substring(0, 200) : '';
    const date = dateEl ? dateEl.innerText.trim() : '';
    const lawyer = lawyerEl ? lawyerEl.innerText.trim() : '';
    
    if (title) {
      results.push({
        title: title,
        url: url,
        answer_preview: answer,
        publish_date: date,
        lawyer: lawyer,
        type: 'qa',
        source: 'huilv',
      });
    }
  });
  
  return results;
})()
"""
        
        js_qa = js_qa.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_qa,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def _search_article(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索法律知识文章"""
        search_url = f"{HUILV_BASE}/article/search?keyword={quote(query)}"
        print(f"    [URL] 文章搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".article-list, .result-list, .article-item, table",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 文章搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取文章信息
        js_article = r"""
(() => {
  const results = [];
  const selectors = [
    '.article-list .item',
    '.result-list .item',
    '.article-item',
    'table tbody tr'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const titleEl = item.querySelector('.title, .article-title, h3, h4');
    const linkEl = item.querySelector('a');
    const summaryEl = item.querySelector('.summary, .excerpt, .content');
    const dateEl = item.querySelector('.date, .time');
    const authorEl = item.querySelector('.author, .writer');
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const summary = summaryEl ? summaryEl.innerText.trim().substring(0, 200) : '';
    const date = dateEl ? dateEl.innerText.trim() : '';
    const author = authorEl ? authorEl.innerText.trim() : '';
    
    if (title) {
      results.push({
        title: title,
        url: url,
        summary: summary,
        publish_date: date,
        author: author,
        type: 'article',
        source: 'huilv',
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

    def get_lawyer_detail(
        self,
        url: str,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
    ) -> Dict:
        """获取律师详情"""
        print(f"[华律网] 正在获取律师详情: {url}")

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
            "--wait-selector", ".detail, .content, .lawyer-detail",
            "--timeout", "30",
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 详情页导航失败")
            return {}

        time.sleep(2.0)

        # 提取详情
        js_detail = r"""
(() => {
  const result = {
    title: document.title,
    url: window.location.href,
    content: '',
    name: '',
    law_firm: '',
    specialty: '',
    rating: '',
    experience: '',
  };
  
  // 提取主要内容
  const contentEl = document.querySelector('.content, .detail, .lawyer-detail');
  if (contentEl) {
    result.content = contentEl.innerText.trim().substring(0, 3000);
  }
  
  // 提取律师姓名
  const nameEl = document.querySelector('.name, .lawyer-name, h1');
  if (nameEl) {
    result.name = nameEl.innerText.trim();
  }
  
  // 提取律所
  const firmEl = document.querySelector('.firm, .law-firm, .company');
  if (firmEl) {
    result.law_firm = firmEl.innerText.trim();
  }
  
  // 提取专业领域
  const specialtyEl = document.querySelector('.specialty, .field, .practice-area');
  if (specialtyEl) {
    result.specialty = specialtyEl.innerText.trim();
  }
  
  // 提取评分
  const ratingEl = document.querySelector('.rating, .score');
  if (ratingEl) {
    result.rating = ratingEl.innerText.trim();
  }
  
  // 提取从业经验
  const experienceEl = document.querySelector('.experience, .years');
  if (experienceEl) {
    result.experience = experienceEl.innerText.trim();
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
        description="华律网搜索器 - 获取律师、法律咨询、法律知识",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python huilv_search.py "合同纠纷"
    python huilv_search.py "律师" --type lawyer --city 北京
    python huilv_search.py "法律咨询" --type qa --output-dir ./huilv_results
"""
    )

    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--type", type=str, default="all",
                        choices=["lawyer", "qa", "article", "all"],
                        help="搜索类型 (默认: all)")
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
    searcher = HuilvSearcher()

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
        print(f"\n[结果] 共获取 {len(results)} 条华律网信息")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未获取到华律网信息")


if __name__ == "__main__":
    main()
