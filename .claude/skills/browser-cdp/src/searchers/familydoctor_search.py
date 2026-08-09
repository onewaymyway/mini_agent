#!/usr/bin/env python
"""
familydoctor_search.py - 家庭医生在线搜索器

使用 browser-cdp skill 搜索家庭医生在线，获取医疗资讯、疾病查询、
医院信息、医生信息等内容。

用法:
    python familydoctor_search.py "高血压治疗"
    python familydoctor_search.py "感冒" --type article --output-dir ./familydoctor_results
    python familydoctor_search.py "医院" --port 9333

示例:
    python familydoctor_search.py "颈椎病治疗"
    python familydoctor_search.py "医院查询" --output-dir ./results
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


# ========== 家庭医生在线专用配置 ==========
FAMILYDOCTOR_BASE = "https://www.familydoctor.com.cn"
FAMILYDOCTOR_SEARCH_URL = f"{FAMILYDOCTOR_BASE}/search"
FAMILYDOCTOR_ARTICLE_URL = f"{FAMILYDOCTOR_BASE}/article"
FAMILYDOCTOR_HOSPITAL_URL = f"{FAMILYDOCTOR_BASE}/hospital"

# 默认输出目录
FAMILYDOCTOR_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "familydoctor"


class FamilyDoctorSearcher(BaseSearcher):
    """家庭医生在线搜索器"""

    @property
    def source_name(self) -> str:
        return "familydoctor"

    @property
    def supported_types(self) -> List[str]:
        return ["article_search", "hospital_search", "doctor_search", "disease_search", "all"]

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
        """搜索医疗信息

        Args:
            query: 搜索关键词
            search_type: 搜索类型 (article/hospital/doctor/disease/all)
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间
            max_results: 最大结果数

        Returns:
            搜索结果列表
        """
        print(f"[家庭医生在线] 正在搜索: {query}")

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
            print(f"  [搜索] 医疗资讯...")
            article_results = self._search_article(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(article_results)

        if search_type in ["hospital", "all"]:
            print(f"  [搜索] 医院信息...")
            hospital_results = self._search_hospital(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(hospital_results)

        if search_type in ["doctor", "all"]:
            print(f"  [搜索] 医生信息...")
            doctor_results = self._search_doctor(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(doctor_results)

        if search_type in ["disease", "all"]:
            print(f"  [搜索] 疾病知识...")
            disease_results = self._search_disease(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(disease_results)

        # 去重
        results = self._deduplicate(results)

        # 保存结果
        if results and output_dir:
            path = save_results(
                results,
                output_dir or str(FAMILYDOCTOR_OUTPUT_DIR),
                f"familydoctor_{query}_{int(time.time())}.json"
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
        """搜索医疗资讯"""
        search_url = f"{FAMILYDOCTOR_BASE}/search?keyword={quote(query)}"
        print(f"    [URL] 资讯搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".search-result, .result-list, .article-item, .news-item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 资讯搜索导航失败")
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
        url: url.startsWith('http') ? url : (url.startsWith('/') ? 'https://www.familydoctor.com.cn' + url : 'https://www.familydoctor.com.cn/' + url),
        publish_date: date,
        source: source,
        type: 'article',
        source_site: 'familydoctor',
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

    def _search_hospital(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索医院信息"""
        search_url = f"{FAMILYDOCTOR_BASE}/search?keyword={quote(query)}&type=hospital"
        print(f"    [URL] 医院搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".search-result, .result-list, .hospital-item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 医院搜索导航失败")
            return []

        time.sleep(2.0)

        js_hospital = r"""
(() => {
  const results = [];
  const selectors = [
    '.search-result .item',
    '.result-list .item',
    '.hospital-item'
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
    const levelEl = item.querySelector('.level, .grade, .hospital-level');
    const locEl = item.querySelector('.location, .address, .city');
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const level = levelEl ? levelEl.innerText.trim() : '';
    const location = locEl ? locEl.innerText.trim() : '';
    
    if (title && title.length > 5) {
      results.push({
        title: title,
        url: url.startsWith('http') ? url : (url.startsWith('/') ? 'https://www.familydoctor.com.cn' + url : 'https://www.familydoctor.com.cn/' + url),
        hospital_level: level,
        location: location,
        type: 'hospital',
        source_site: 'familydoctor',
      });
    }
  });
  
  return results;
})()
"""
        
        js_hospital = js_hospital.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_hospital,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def _search_doctor(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索医生信息"""
        search_url = f"{FAMILYDOCTOR_BASE}/search?keyword={quote(query)}&type=doctor"
        print(f"    [URL] 医生搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".search-result, .result-list, .doctor-item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 医生搜索导航失败")
            return []

        time.sleep(2.0)

        js_doctor = r"""
(() => {
  const results = [];
  const selectors = [
    '.search-result .item',
    '.result-list .item',
    '.doctor-item'
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
    const deptEl = item.querySelector('.dept, .department, .specialty');
    const hospitalEl = item.querySelector('.hospital, .hospital-name');
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const dept = deptEl ? deptEl.innerText.trim() : '';
    const hospital = hospitalEl ? hospitalEl.innerText.trim() : '';
    
    if (title && title.length > 5) {
      results.push({
        title: title,
        url: url.startsWith('http') ? url : (url.startsWith('/') ? 'https://www.familydoctor.com.cn' + url : 'https://www.familydoctor.com.cn/' + url),
        department: dept,
        hospital: hospital,
        type: 'doctor',
        source_site: 'familydoctor',
      });
    }
  });
  
  return results;
})()
"""
        
        js_doctor = js_doctor.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_doctor,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def _search_disease(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索疾病知识"""
        search_url = f"{FAMILYDOCTOR_BASE}/search?keyword={quote(query)}&type=disease"
        print(f"    [URL] 疾病搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".search-result, .result-list, .disease-item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 疾病搜索导航失败")
            return []

        time.sleep(2.0)

        js_disease = r"""
(() => {
  const results = [];
  const selectors = [
    '.search-result .item',
    '.result-list .item',
    '.disease-item'
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
        url: url.startsWith('http') ? url : (url.startsWith('/') ? 'https://www.familydoctor.com.cn' + url : 'https://www.familydoctor.com.cn/' + url),
        description: desc,
        type: 'disease',
        source_site: 'familydoctor',
      });
    }
  });
  
  return results;
})()
"""
        
        js_disease = js_disease.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_disease,
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
        print(f"[家庭医生在线] 正在获取详情: {url}")

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
        description="家庭医生在线搜索器 - 获取医疗资讯、医院信息、医生信息、疾病知识",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python familydoctor_search.py "高血压治疗"
    python familydoctor_search.py "感冒" --type article --output-dir ./familydoctor_results
    python familydoctor_search.py "医院" --port 9333
"""
    )

    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--type", type=str, default="all",
                        choices=["article", "hospital", "doctor", "disease", "all"],
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
    searcher = FamilyDoctorSearcher()

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
        print(f"\n[结果] 共获取 {len(results)} 条医疗信息")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未获取到医疗信息")


if __name__ == "__main__":
    main()
