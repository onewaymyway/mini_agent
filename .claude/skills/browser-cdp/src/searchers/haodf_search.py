#!/usr/bin/env python
"""
haodf_search.py - 好大夫在线搜索器

使用 browser-cdp skill 搜索好大夫在线，获取医生信息和医院信息。

用法:
    python haodf_search.py "心血管" "北京"
    python haodf_search.py "眼科" "上海" --hospital
    python haodf_search.py "儿科" --output-dir ./haodf_results

示例:
    python haodf_search.py "心血管内科" "北京"
    python haodf_search.py "眼科" "上海" --hospital
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


# ========== 好大夫在线专用配置 ==========
HAODF_BASE = "https://www.haodf.com"
HAODF_SEARCH_URL = f"{HAODF_BASE}/search?keyword={quote('{keyword}')}"
HAODF_DOCTOR_URL = "https://www.haodf.com/wangyu/"

# 默认输出目录
HAODF_OUTPUT_DIR = Path(__file__).parent.parent.parent / "search_results" / "haodf"


class HaodfSearcher(BaseSearcher):
    """好大夫在线搜索器"""

    @property
    def source_name(self) -> str:
        return "haodf"

    @property
    def supported_types(self) -> List[str]:
        return ["doctor_search", "hospital_search", "medical_search"]

    def search(
        self,
        query: str,
        city: Optional[str] = None,
        hospital: bool = False,
        max_results: int = 20,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
    ) -> List[Dict]:
        """搜索医生或医院信息

        Args:
            query: 搜索关键词（科室、疾病、医生姓名）
            city: 城市（可选）
            hospital: 是否搜索医院
            max_results: 最大结果数
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间

        Returns:
            医生或医院列表
        """
        print(f"[好大夫在线] 正在搜索: {query}")
        if city:
            print(f"  城市: {city}")
        print(f"  类型: {'医院' if hospital else '医生'}")

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
        delay = random_delay(1.5, 2.5)
        print(f"  [延迟] 请求前等待 {delay:.1f} 秒")

        # 步骤1: 导航到搜索页
        search_url = f"{HAODF_BASE}/search?keyword={quote(query)}"
        if city:
            search_url += f"&city={quote(city)}"
        if hospital:
            search_url += "&type=hospital"
        
        print(f"  [URL] 搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".search-result, .result-list, .doctor-list, .hospital-list",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return []

        time.sleep(2.0)

        # 步骤2: 提取搜索结果
        js_search = r"""
(() => {
  const results = [];
  // 医生搜索结果选择器
  const doctorItems = document.querySelectorAll('.doctor-item, .result-item, .list-item, .search-result');
  doctorItems.forEach((item, i) => {
    if (i >= 20) return;
    
    const linkEl = item.querySelector('a[href*="wangyu"], a[href*="doctor"]');
    const titleEl = item.querySelector('.doctor-name, .name, h3, h4');
    const hospitalEl = item.querySelector('.hospital, .hospital-name, .dept');
    const titleEl2 = item.querySelector('.title, .position');
    const ratingEl = item.querySelector('.rating, .score, .good-rate');
    const visitsEl = item.querySelector('.visits, .consult-count');
    
    const name = titleEl ? titleEl.innerText.trim() : '';
    const hospital = hospitalEl ? hospitalEl.innerText.trim() : '';
    const title = titleEl2 ? titleEl2.innerText.trim() : '';
    const rating = ratingEl ? ratingEl.innerText.trim() : '';
    const visits = visitsEl ? visitsEl.innerText.trim() : '';
    const href = linkEl ? linkEl.href : '';
    
    if (name) {
      results.push({
        name: name,
        hospital: hospital,
        title: title,
        rating: rating,
        visits: visits,
        url: href,
        type: 'doctor',
      });
    }
  });
  
  // 医院搜索结果选择器
  const hospitalItems = document.querySelectorAll('.hospital-item, .hospital-result');
  hospitalItems.forEach((item, i) => {
    if (i >= 20) return;
    
    const linkEl = item.querySelector('a[href*="hospital"]');
    const nameEl = item.querySelector('.hospital-name, .name, h3, h4');
    const levelEl = item.querySelector('.level, .grade');
    const deptEl = item.querySelector('.dept, .departments');
    
    const name = nameEl ? nameEl.innerText.trim() : '';
    const level = levelEl ? levelEl.innerText.trim() : '';
    const depts = deptEl ? deptEl.innerText.trim() : '';
    const href = linkEl ? linkEl.href : '';
    
    if (name) {
      results.push({
        name: name,
        level: level,
        departments: depts,
        url: href,
        type: 'hospital',
      });
    }
  });
  
  return results;
})()
"""
        search_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_search,
        ])

        if search_result.returncode != 0:
            print(f"[错误] 搜索结果提取失败: {search_result.stderr[:200]}")
            return []

        try:
            items = json.loads(search_result.stdout)
        except json.JSONDecodeError:
            print(f"[错误] JSON 解析失败: {search_result.stdout[:200]}")
            return []

        if not items:
            print(f"[提示] 未找到搜索结果，尝试备用方式...")
            return self._search_fallback(query, city, hospital, port, tab_id, max_results, stealth, output_dir, wait_timeout)

        print(f"  [结果] 找到 {len(items)} 条结果")

        # 步骤3: 获取详情（可选）
        final_results = []
        for i, item in enumerate(items[:max_results]):
            if i > 0:
                delay = random_delay(1.0, 2.0)
                print(f"  [延迟] 等待 {delay:.1f} 秒")
            
            if item.get('type') == 'doctor':
                detail = self._get_doctor_detail(port, tab_id, item.get("url", ""), stealth, wait_timeout)
                if detail:
                    final_results.append(detail)
                else:
                    final_results.append(item)
            else:
                final_results.append(item)

        # 保存结果
        if output_dir:
            path = save_results(
                final_results,
                output_dir,
                f"haodf_{query}_{int(time.time())}.json"
            )
            print(f"  [保存] {path}")

        return final_results

    def _search_fallback(
        self,
        query: str,
        city: Optional[str],
        hospital: bool,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        output_dir: Optional[str],
        wait_timeout: int,
    ) -> List[Dict]:
        """备用搜索方法"""
        print(f"  [备用] 尝试使用备用搜索方式...")
        
        search_url = f"{HAODF_BASE}/search?keyword={quote(query)}"
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", "body",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            return []

        time.sleep(2.0)
        
        js_fallback = r"""
(() => {
  const results = [];
  const items = document.querySelectorAll('a[href*="wangyu"], a[href*="hospital"], .item, .result');
  items.forEach((item, i) => {
    if (i >= 20) return;
    const title = item.innerText.trim().substring(0, 100);
    const href = item.href || '';
    if (title && href && title.length > 5 && !title.includes('登录')) {
      results.push({
        name: title,
        url: href,
        type: 'doctor' if 'wangyu' in href else 'hospital',
      });
    }
  });
  return results;
})()
"""
        fallback_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_fallback,
        ])

        try:
            items = json.loads(fallback_result.stdout)
        except:
            return []

        return items[:max_results]

    def _get_doctor_detail(
        self,
        port: int,
        tab_id: str,
        url: str,
        stealth: bool,
        wait_timeout: int,
    ) -> Optional[Dict]:
        """获取医生详情页内容"""
        if not url:
            return None

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", url,
            "--wait-selector", ".doctor-detail, .profile, article",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            return None

        time.sleep(1.5)

        js_detail = r"""
(() => {
  const result = {};
  const nameEl = document.querySelector('.doctor-name, h1, .name');
  result.name = nameEl ? nameEl.innerText.trim() : '';
  
  const hospitalEl = document.querySelector('.hospital, .hospital-name');
  result.hospital = hospitalEl ? hospitalEl.innerText.trim() : '';
  
  const deptEl = document.querySelector('.dept, .department');
  result.department = deptEl ? deptEl.innerText.trim() : '';
  
  const titleEl = document.querySelector('.title, .position');
  result.title = titleEl ? titleEl.innerText.trim() : '';
  
  const introEl = document.querySelector('.intro, .description, .bio');
  result.introduction = introEl ? introEl.innerText.trim().substring(0, 500) : '';
  
  const ratingEl = document.querySelector('.rating, .score');
  result.rating = ratingEl ? ratingEl.innerText.trim() : '';
  
  return result;
})()
"""
        detail_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_detail,
        ])

        try:
            return json.loads(detail_result.stdout)
        except:
            return None

    def get_detail(
        self,
        url: str,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
    ) -> Dict:
        """获取指定页面详情"""
        if tab_id is None:
            result = ensure_browser(port=port, stealth=stealth)
            if result.get("error"):
                return {}
            tab_id = result.get("tab_id")
        
        return self._get_doctor_detail(port, tab_id, url, stealth, 30)


def ensure_browser(port: int = 9333, stealth: bool = True) -> Dict:
    """确保浏览器已连接"""
    cmd = [
        PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
        "--port", str(port),
        "--status",
    ]
    result = run_cmd(cmd)
    
    if result.returncode == 0:
        try:
            status = json.loads(result.stdout)
            if status.get("connected"):
                return {"tab_id": status.get("tab_id"), "port": port}
        except:
            pass
    
    # 启动新浏览器
    cmd = [
        PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
        "--port", str(port),
        "--launch",
    ]
    if stealth:
        cmd.extend(["--stealth"])
    
    result = run_cmd(cmd)
    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            return data
        except:
            pass
    
    return {"error": "浏览器启动失败"}


def run_cmd(cmd: List[str]) -> subprocess.CompletedProcess:
    """执行命令"""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="好大夫在线搜索器 - 获取医生和医院信息",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python haodf_search.py "心血管" "北京"
    python haodf_search.py "眼科" "上海" --hospital
    python haodf_search.py "儿科" --output-dir ./haodf_results
"""
    )

    parser.add_argument("query", help="搜索关键词（科室、疾病、医生姓名）")
    parser.add_argument("--city", type=str, default=None, help="城市（可选）")
    parser.add_argument("--hospital", action="store_true", help="搜索医院而非医生")
    parser.add_argument("--max-results", type=int, default=20, help="最大结果数 (默认: 20)")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--tab", type=str, default=None, help="Tab ID")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式")
    parser.add_argument("--wait-timeout", type=int, default=30, help="等待超时时间 (默认: 30秒)")

    args = parser.parse_args()

    # 创建搜索器
    searcher = HaodfSearcher()

    # 执行搜索
    results = searcher.search(
        query=args.query,
        city=args.city,
        hospital=args.hospital,
        max_results=args.max_results,
        port=args.port,
        tab_id=args.tab,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.wait_timeout,
    )

    # 输出结果
    if results:
        print(f"\n[结果] 共获取 {len(results)} 条信息")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未获取到信息")


if __name__ == "__main__":
    main()
