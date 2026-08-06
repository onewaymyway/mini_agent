#!/usr/bin/env python
"""
dxy_hospital_search.py - 丁香园医院库搜索器

使用 browser-cdp skill 搜索丁香园医院库，获取医院信息、排名、科室等。

用法:
    python dxy_hospital_search.py "北京" --type hospital
    python dxy_hospital_search.py "心血管" --type department --output-dir ./hospital_results
    python dxy_hospital_search.py "三甲医院" --city "上海" --port 9333

示例:
    python dxy_hospital_search.py "北京" --type hospital
    python dxy_hospital_search.py "儿科" --type department --output-dir ./results
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


# ========== 丁香园医院库专用配置 ==========
DXY_BASE = "https://y.dxy.cn"
DXY_HOSPITAL_URL = f"{DXY_BASE}/hospital/"
DXY_RANK_URL = f"{DXY_BASE}/rank"

# 默认输出目录
DXY_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "dxy_hospital"


class DXYHospitalSearcher(BaseSearcher):
    """丁香园医院库搜索器"""

    @property
    def source_name(self) -> str:
        return "dxy_hospital"

    @property
    def supported_types(self) -> List[str]:
        return ["hospital_search", "department_search", "rank_search", "hospital_info"]

    def search(
        self,
        query: str,
        search_type: str = "hospital",
        city: Optional[str] = None,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
        max_results: int = 20,
    ) -> List[Dict]:
        """搜索医院信息

        Args:
            query: 搜索关键词
            search_type: 搜索类型 (hospital/department/rank/info)
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
        print(f"[丁香园医院库] 正在搜索: {query}")

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
        if search_type in ["hospital", "all"]:
            print(f"  [搜索] 医院信息...")
            hospital_results = self._search_hospital(query, city, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(hospital_results)

        if search_type in ["department", "all"]:
            print(f"  [搜索] 科室信息...")
            dept_results = self._search_department(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(dept_results)

        if search_type in ["rank", "all"]:
            print(f"  [搜索] 医院排名...")
            rank_results = self._search_rank(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(rank_results)

        # 保存结果
        if results and output_dir:
            path = save_results(
                results,
                output_dir or str(DXY_OUTPUT_DIR),
                f"dxy_hospital_{query}_{int(time.time())}.json"
            )
            print(f"  [保存] {path}")

        return results

    def _search_hospital(
        self,
        query: str,
        city: Optional[str],
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索医院信息"""
        search_url = f"{DXY_BASE}/hospital/search?keyword={quote(query)}"
        if city:
            search_url += f"&city={quote(city)}"
        print(f"    [URL] 医院搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".hospital-list, .result-list, .hospital-item, .card-item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 医院搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取医院信息
        js_hospital = r"""
(() => {
  const results = [];
  const selectors = [
    '.hospital-list .item',
    '.result-list .item',
    '.hospital-item',
    '.card-item',
    '.hospital-card'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const nameEl = item.querySelector('.name, .hospital-name, h3, h4, .title');
    const linkEl = item.querySelector('a');
    const levelEl = item.querySelector('.level, .grade, .等级');
    const cityEl = item.querySelector('.city, .location, .地区');
    const deptEl = item.querySelector('.department, .dept, .科室');
    const ratingEl = item.querySelector('.rating, .score, .star');
    
    const name = nameEl ? nameEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const level = levelEl ? levelEl.innerText.trim() : '';
    const city = cityEl ? cityEl.innerText.trim() : '';
    const departments = deptEl ? deptEl.innerText.trim() : '';
    const rating = ratingEl ? ratingEl.innerText.trim() : '';
    
    if (name && name.length > 2) {
      results.push({
        name: name,
        url: url,
        level: level,
        city: city,
        departments: departments,
        rating: rating,
        type: 'hospital',
        source_site: 'dxy_hospital',
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

    def _search_department(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索科室信息"""
        search_url = f"{DXY_BASE}/department/search?keyword={quote(query)}"
        print(f"    [URL] 科室搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".dept-list, .result-list, .dept-item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 科室搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取科室信息
        js_dept = r"""
(() => {
  const results = [];
  const selectors = [
    '.dept-list .item',
    '.result-list .item',
    '.dept-item',
    '.department-item'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const nameEl = item.querySelector('.name, .dept-name, h3, h4, .title');
    const linkEl = item.querySelector('a');
    const hospitalEl = item.querySelector('.hospital, .hospital-name');
    const ratingEl = item.querySelector('.rating, .score');
    
    const name = nameEl ? nameEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const hospital = hospitalEl ? hospitalEl.innerText.trim() : '';
    const rating = ratingEl ? ratingEl.innerText.trim() : '';
    
    if (name && name.length > 2) {
      results.push({
        name: name,
        url: url,
        hospital: hospital,
        rating: rating,
        type: 'department',
        source_site: 'dxy_hospital',
      });
    }
  });
  
  return results;
})()
"""
        
        js_dept = js_dept.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_dept,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def _search_rank(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索医院排名"""
        search_url = f"{DXY_BASE}/rank?keyword={quote(query)}"
        print(f"    [URL] 排名搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".rank-list, .result-list, .rank-item, .hospital-rank",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 排名搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取排名信息
        js_rank = r"""
(() => {
  const results = [];
  const selectors = [
    '.rank-list .item',
    '.result-list .item',
    '.rank-item',
    '.hospital-rank'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const rankEl = item.querySelector('.rank, .number, .排名');
    const nameEl = item.querySelector('.name, .hospital-name, h3, h4');
    const linkEl = item.querySelector('a');
    const scoreEl = item.querySelector('.score, .rating, .得分');
    const cityEl = item.querySelector('.city, .location');
    
    const rank = rankEl ? rankEl.innerText.trim() : '';
    const name = nameEl ? nameEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const score = scoreEl ? scoreEl.innerText.trim() : '';
    const city = cityEl ? cityEl.innerText.trim() : '';
    
    if (name && name.length > 2) {
      results.push({
        rank: rank,
        name: name,
        url: url,
        score: score,
        city: city,
        type: 'rank',
        source_site: 'dxy_hospital',
      });
    }
  });
  
  return results;
})()
"""
        
        js_rank = js_rank.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_rank,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def get_hospital_detail(
        self,
        url: str,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
    ) -> Dict:
        """获取医院详情"""
        print(f"[丁香园医院库] 正在获取详情: {url}")

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
            "--wait-selector", ".content, .detail, article, .hospital-detail",
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
    level: '',
    city: '',
    address: '',
    phone: '',
    departments: [],
  };
  
  // 提取正文
  const contentEl = document.querySelector('.content, .detail, article, .hospital-detail, .text');
  if (contentEl) {
    result.content = contentEl.innerText.trim().substring(0, 3000);
  }
  
  // 提取医院名称
  const nameEl = document.querySelector('.hospital-name, .name, h1, h2');
  if (nameEl) {
    result.name = nameEl.innerText.trim();
  }
  
  // 提取等级
  const levelEl = document.querySelector('.level, .grade, .等级');
  if (levelEl) {
    result.level = levelEl.innerText.trim();
  }
  
  // 提取城市
  const cityEl = document.querySelector('.city, .location, .地区');
  if (cityEl) {
    result.city = cityEl.innerText.trim();
  }
  
  // 提取地址
  const addressEl = document.querySelector('.address, .addr, .地址');
  if (addressEl) {
    result.address = addressEl.innerText.trim();
  }
  
  // 提取电话
  const phoneEl = document.querySelector('.phone, .tel, .电话');
  if (phoneEl) {
    result.phone = phoneEl.innerText.trim();
  }
  
  // 提取科室列表
  const deptItems = document.querySelectorAll('.department, .dept-item, .科室');
  deptItems.forEach((dept, i) => {
    if (i < 20) {
      result.departments.push(dept.innerText.trim());
    }
  });
  
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
        description="丁香园医院库搜索器 - 获取医院信息、排名、科室",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python dxy_hospital_search.py "北京" --type hospital
    python dxy_hospital_search.py "心血管" --type department --output-dir ./hospital_results
    python dxy_hospital_search.py "三甲医院" --city "上海" --port 9333
"""
    )

    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--type", type=str, default="hospital",
                        choices=["hospital", "department", "rank", "info", "all"],
                        help="搜索类型 (默认: hospital)")
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
    searcher = DXYHospitalSearcher()

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
        print(f"\n[结果] 共获取 {len(results)} 条医院信息")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未获取到医院信息")


if __name__ == "__main__":
    main()
