#!/usr/bin/env python
"""
66law_search.py - 华律网搜索器

使用 browser-cdp skill 搜索华律网，获取律师信息、法律咨询、案例等。

用法:
    python 66law_search.py "离婚律师" --type lawyer
    python 66law_search.py "合同纠纷" --type case --output-dir ./legal_results
    python 66law_search.py "法律咨询" --city "北京" --port 9333

示例:
    python 66law_search.py "刑事辩护律师"
    python 66law_search.py "劳动纠纷" --type case --output-dir ./results
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
LAW66_BASE = "https://www.66law.cn"
LAW_SEARCH_URL = f"{LAW66_BASE}/search"
LAWYER_URL = f"{LAW66_BASE}/lvshi"
CASE_URL = f"{LAW66_BASE}/case"

# 默认输出目录
LAW66_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "66law"


class Law66Searcher(BaseSearcher):
    """华律网搜索器"""

    @property
    def source_name(self) -> str:
        return "law66"

    @property
    def supported_types(self) -> List[str]:
        return ["lawyer_search", "case_search", "consult_search", "legal_news"]

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
        """搜索法律信息

        Args:
            query: 搜索关键词
            search_type: 搜索类型 (lawyer/case/consult/news/all)
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

        if search_type in ["case", "all"]:
            print(f"  [搜索] 案例信息...")
            case_results = self._search_case(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(case_results)

        if search_type in ["consult", "all"]:
            print(f"  [搜索] 法律咨询...")
            consult_results = self._search_consult(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(consult_results)

        if search_type in ["news", "all"]:
            print(f"  [搜索] 法律资讯...")
            news_results = self._search_news(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(news_results)

        # 保存结果
        if results and output_dir:
            path = save_results(
                results,
                output_dir or str(LAW66_OUTPUT_DIR),
                f"66law_{query}_{int(time.time())}.json"
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
        search_url = f"{LAW66_BASE}/lvshi/search?keyword={quote(query)}"
        if city:
            search_url += f"&city={quote(city)}"
        print(f"    [URL] 律师搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".lawyer-list, .result-list, .lawyer-item, .card-item",
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
    '.card-item',
    '.lawyer-card'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const nameEl = item.querySelector('.name, .lawyer-name, h3, h4, .title');
    const linkEl = item.querySelector('a');
    const firmEl = item.querySelector('.firm, .law-firm, .company');
    const specialtyEl = item.querySelector('.specialty, .field, .practice-area, .擅长');
    const yearsEl = item.querySelector('.years, .experience, .执业年限');
    const ratingEl = item.querySelector('.rating, .score, .star');
    const cityEl = item.querySelector('.city, .location, .地区');
    
    const name = nameEl ? nameEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const firm = firmEl ? firmEl.innerText.trim() : '';
    const specialty = specialtyEl ? specialtyEl.innerText.trim() : '';
    const years = yearsEl ? yearsEl.innerText.trim() : '';
    const rating = ratingEl ? ratingEl.innerText.trim() : '';
    const city = cityEl ? cityEl.innerText.trim() : '';
    
    if (name && name.length > 2) {
      results.push({
        name: name,
        url: url,
        law_firm: firm,
        specialty: specialty,
        experience_years: years,
        rating: rating,
        city: city,
        type: 'lawyer',
        source_site: 'law66',
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

    def _search_case(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索案例信息"""
        search_url = f"{LAW66_BASE}/case/search?keyword={quote(query)}"
        print(f"    [URL] 案例搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".case-list, .result-list, .case-item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 案例搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取案例信息
        js_case = r"""
(() => {
  const results = [];
  const selectors = [
    '.case-list .item',
    '.result-list .item',
    '.case-item',
    '.case-card'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const titleEl = item.querySelector('.title, .case-title, h3, h4, a');
    const linkEl = item.querySelector('a');
    const typeEl = item.querySelector('.case-type, .type, .案件类型');
    const resultEl = item.querySelector('.result, .judgment, .裁判结果');
    const dateEl = item.querySelector('.date, .time, .publish-date');
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const case_type = typeEl ? typeEl.innerText.trim() : '';
    const result = resultEl ? resultEl.innerText.trim().substring(0, 200) : '';
    const date = dateEl ? dateEl.innerText.trim() : '';
    
    if (title && title.length > 5) {
      results.push({
        title: title,
        url: url,
        case_type: case_type,
        judgment_preview: result,
        publish_date: date,
        type: 'case',
        source_site: 'law66',
      });
    }
  });
  
  return results;
})()
"""
        
        js_case = js_case.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_case,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def _search_consult(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索法律咨询"""
        search_url = f"{LAW66_BASE}/zixun/search?keyword={quote(query)}"
        print(f"    [URL] 咨询搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".consult-list, .result-list, .consult-item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 咨询搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取咨询信息
        js_consult = r"""
(() => {
  const results = [];
  const selectors = [
    '.consult-list .item',
    '.result-list .item',
    '.consult-item',
    '.qa-item'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const titleEl = item.querySelector('.title, .question, h3, h4, a');
    const linkEl = item.querySelector('a');
    const answerEl = item.querySelector('.answer, .reply, .content');
    const lawyerEl = item.querySelector('.lawyer, .answerer');
    const dateEl = item.querySelector('.date, .time');
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const answer = answerEl ? answerEl.innerText.trim().substring(0, 200) : '';
    const lawyer = lawyerEl ? lawyerEl.innerText.trim() : '';
    const date = dateEl ? dateEl.innerText.trim() : '';
    
    if (title && title.length > 5) {
      results.push({
        title: title,
        url: url,
        answer_preview: answer,
        lawyer: lawyer,
        publish_date: date,
        type: 'consult',
        source_site: 'law66',
      });
    }
  });
  
  return results;
})()
"""
        
        js_consult = js_consult.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_consult,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def _search_news(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索法律资讯"""
        search_url = f"{LAW66_BASE}/news/search?keyword={quote(query)}"
        print(f"    [URL] 资讯搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".news-list, .result-list, .news-item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 资讯搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取资讯信息
        js_news = r"""
(() => {
  const results = [];
  const selectors = [
    '.news-list .item',
    '.result-list .item',
    '.news-item'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const titleEl = item.querySelector('.title, .headline, h3, h4, a');
    const linkEl = item.querySelector('a');
    const dateEl = item.querySelector('.date, .time, .publish-date');
    const sourceEl = item.querySelector('.source, .author');
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const date = dateEl ? dateEl.innerText.trim() : '';
    const source = sourceEl ? sourceEl.innerText.trim() : '';
    
    if (title && title.length > 5) {
      results.push({
        title: title,
        url: url,
        publish_date: date,
        source: source,
        type: 'news',
        source_site: 'law66',
      });
    }
  });
  
  return results;
})()
"""
        
        js_news = js_news.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_news,
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
            "--wait-selector", ".content, .detail, article, .lawyer-detail",
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
    name: '',
    firm: '',
    specialty: '',
    experience_years: '',
    rating: '',
    phone: '',
    address: '',
  };
  
  // 提取正文
  const contentEl = document.querySelector('.content, .detail, article, .lawyer-detail, .text');
  if (contentEl) {
    result.content = contentEl.innerText.trim().substring(0, 3000);
  }
  
  // 提取律师姓名
  const nameEl = document.querySelector('.lawyer-name, .name, h1, h2');
  if (nameEl) {
    result.name = nameEl.innerText.trim();
  }
  
  // 提取律所
  const firmEl = document.querySelector('.firm, .law-firm, .company');
  if (firmEl) {
    result.firm = firmEl.innerText.trim();
  }
  
  // 提取擅长领域
  const specialtyEl = document.querySelector('.specialty, .field, .practice-area');
  if (specialtyEl) {
    result.specialty = specialtyEl.innerText.trim();
  }
  
  // 提取执业年限
  const yearsEl = document.querySelector('.years, .experience, .执业年限');
  if (yearsEl) {
    result.experience_years = yearsEl.innerText.trim();
  }
  
  // 提取评分
  const ratingEl = document.querySelector('.rating, .score, .star');
  if (ratingEl) {
    result.rating = ratingEl.innerText.trim();
  }
  
  // 提取电话
  const phoneEl = document.querySelector('.phone, .tel, .电话');
  if (phoneEl) {
    result.phone = phoneEl.innerText.trim();
  }
  
  // 提取地址
  const addressEl = document.querySelector('.address, .addr, .地址');
  if (addressEl) {
    result.address = addressEl.innerText.trim();
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
        description="华律网搜索器 - 获取律师信息、案例、法律咨询",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python 66law_search.py "离婚律师" --type lawyer
    python 66law_search.py "合同纠纷" --type case --output-dir ./legal_results
    python 66law_search.py "法律咨询" --city "北京" --port 9333
"""
    )

    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--type", type=str, default="all",
                        choices=["lawyer", "case", "consult", "news", "all"],
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
    searcher = Law66Searcher()

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
        print(f"\n[结果] 共获取 {len(results)} 条法律信息")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未获取到法律信息")


if __name__ == "__main__":
    main()
