#!/usr/bin/env python
"""
xuetangx_search.py - 学堂在线搜索器

使用 browser-cdp skill 搜索学堂在线，获取课程信息。

用法:
    python xuetangx_search.py "Python"
    python xuetangx_search.py "机器学习" --university 清华
    python xianyu_search.py "人工智能" --output-dir ./xuetangx_results

示例:
    python xuetangx_search.py "Python"
    python xuetangx_search.py "机器学习" --university 清华
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


# ========== 学堂在线专用配置 ==========
XUETANGX_BASE = "https://www.xuetangx.com"
XUETANGX_SEARCH_URL = f"{XUETANGX_BASE}/search/?query={quote('{keyword}')}"

# 默认输出目录
XUETANGX_OUTPUT_DIR = Path(__file__).parent.parent.parent / "search_results" / "xuetangx"

# 增强选择器 - 支持多种页面结构
XUETANGX_SELECTORS = {
    'search_input': ["input[placeholder*='搜索'], input[name='q'], .search-input input, #searchInput"],
    'course_list': [".course-item, .course-card, .search-result-item, [class*='course'], [class*='result']"],
    'course_link': ["a[href*='course'], a[href*='class']"],
    'course_title': [".course-title, .title, h3, h4, [class*='title']"],
    'course_university': [".university, .school, [class*='university']"],
    'course_teacher': [".teacher, .instructor, [class*='teacher']"],
    'course_students': [".students, .enroll-count, [class*='student']"],
    'course_rating': [".rating, .score, [class*='rating']"],
}


class XuetangxSearcher(BaseSearcher):
    """学堂在线搜索器"""

    @property
    def source_name(self) -> str:
        return "xuetangx"

    @property
    def supported_types(self) -> List[str]:
        return ["course_search", "education_search", "mooc_search"]

    def search(
        self,
        query: str,
        university: Optional[str] = None,
        max_results: int = 20,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
    ) -> List[Dict]:
        """搜索课程信息

        Args:
            query: 搜索关键词
            university: 大学名称（可选）
            max_results: 最大结果数
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间

        Returns:
            课程信息列表
        """
        print(f"[学堂在线] 正在搜索: {query}")
        if university:
            print(f"  大学: {university}")

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

        # 步骤1: 导航到搜索页
        search_url = f"{XUETANGX_BASE}/search/?query={quote(query)}"
        if university:
            search_url += f"&university={quote(university)}"

        print(f"  [URL] 搜索: {search_url}")

        # 使用增强选择器等待页面加载
        wait_selectors = ", ".join(XUETANGX_SELECTORS['course_list'][:3])
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", wait_selectors,
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return []

        time.sleep(2.0)

        # 步骤2: 提取搜索结果（使用增强选择器）
        js_search = r"""
(() => {
  const results = [];

  // 尝试多种选择器匹配课程项
  const selectors = ['.course-item', '.course-card', '.search-result-item', '[class*="course"]', '[class*="result"]'];
  let items = [];
  for (const sel of selectors) {
    items = document.querySelectorAll(sel);
    if (items.length > 0) break;
  }

  items.forEach((item, i) => {
    if (i >= 20) return;

    // 尝试多种选择器匹配链接
    const linkSel = 'a[href*="course"], a[href*="class"]';
    const linkEl = item.querySelector(linkSel) || item.querySelector('a[href]');

    // 尝试多种选择器匹配标题
    const titleSel = '.course-title, .title, h3, h4, [class*="title"]';
    const titleEl = item.querySelector(titleSel);

    // 尝试多种选择器匹配大学
    const universitySel = '.university, .school, [class*="university"]';
    const universityEl = item.querySelector(universitySel);

    // 尝试多种选择器匹配讲师
    const teacherSel = '.teacher, .instructor, [class*="teacher"]';
    const teacherEl = item.querySelector(teacherSel);

    // 尝试多种选择器匹配学生数
    const studentsSel = '.students, .enroll-count, [class*="student"]';
    const studentsEl = item.querySelector(studentsSel);

    // 尝试多种选择器匹配评分
    const ratingSel = '.rating, .score, [class*="rating"]';
    const ratingEl = item.querySelector(ratingSel);

    const title = titleEl ? titleEl.innerText.trim() : '';
    const university = universityEl ? universityEl.innerText.trim() : '';
    const teacher = teacherEl ? teacherEl.innerText.trim() : '';
    const students = studentsEl ? studentsEl.innerText.trim() : '';
    const rating = ratingEl ? ratingEl.innerText.trim() : '';
    const href = linkEl ? linkEl.href : '';

    if (title) {
      results.push({
        title: title,
        university: university,
        teacher: teacher,
        students: students,
        rating: rating,
        url: href,
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
            return self._search_fallback(query, university, port, tab_id, max_results, stealth, output_dir, wait_timeout)

        print(f"  [结果] 找到 {len(items)} 条课程")

        # 步骤3: 获取详情（可选）
        final_results = []
        for i, item in enumerate(items[:max_results]):
            if i > 0:
                delay = random_delay(1.0, 2.0)
                print(f"  [延迟] 等待 {delay:.1f} 秒")
            
            detail = self._get_detail(port, tab_id, item.get("url", ""), stealth, wait_timeout)
            if detail:
                final_results.append(detail)
            else:
                final_results.append(item)

        # 保存结果
        if output_dir:
            path = save_results(
                final_results,
                output_dir,
                f"xuetangx_{query}_{int(time.time())}.json"
            )
            print(f"  [保存] {path}")

        return final_results

    def _search_fallback(
        self,
        query: str,
        university: Optional[str],
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        output_dir: Optional[str],
        wait_timeout: int,
    ) -> List[Dict]:
        """备用搜索方法"""
        print(f"  [备用] 尝试使用备用搜索方式...")
        
        search_url = f"{XUETANGX_BASE}/search/?query={quote(query)}"
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
  const items = document.querySelectorAll('a[href*="course"], .item, .result');
  items.forEach((item, i) => {
    if (i >= 20) return;
    const title = item.innerText.trim().substring(0, 100);
    const href = item.href || '';
    if (title && href && title.length > 5 && !title.includes('登录')) {
      results.push({
        title: title,
        url: href,
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

    def _get_detail(
        self,
        port: int,
        tab_id: str,
        url: str,
        stealth: bool,
        wait_timeout: int,
    ) -> Optional[Dict]:
        """获取课程详情页内容"""
        if not url:
            return None

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", url,
            "--wait-selector", ".course-detail, .course-info, article",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            return None

        time.sleep(1.5)

        js_detail = r"""
(() => {
  const result = {};
  const titleEl = document.querySelector('.course-title, h1, .title');
  result.title = titleEl ? titleEl.innerText.trim() : '';
  
  const universityEl = document.querySelector('.university, .school');
  result.university = universityEl ? universityEl.innerText.trim() : '';
  
  const teacherEl = document.querySelector('.teacher, .instructor');
  result.teacher = teacherEl ? teacherEl.innerText.trim() : '';
  
  const descEl = document.querySelector('.description, .desc, .intro');
  result.description = descEl ? descEl.innerText.trim().substring(0, 500) : '';
  
  const studentsEl = document.querySelector('.students, .enroll-count');
  result.students = studentsEl ? studentsEl.innerText.trim() : '';
  
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
        
        return self._get_detail(port, tab_id, url, stealth, 30)


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
        description="学堂在线搜索器 - 获取课程信息",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python xuetangx_search.py "Python"
    python xuetangx_search.py "机器学习" --university 清华
    python xuetangx_search.py "人工智能" --output-dir ./xuetangx_results
"""
    )

    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--university", type=str, default=None, help="大学名称（可选）")
    parser.add_argument("--max-results", type=int, default=20, help="最大结果数 (默认: 20)")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--tab", type=str, default=None, help="Tab ID")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式")
    parser.add_argument("--wait-timeout", type=int, default=30, help="等待超时时间 (默认: 30秒)")

    args = parser.parse_args()

    # 创建搜索器
    searcher = XuetangxSearcher()

    # 执行搜索
    results = searcher.search(
        query=args.query,
        university=args.university,
        max_results=args.max_results,
        port=args.port,
        tab_id=args.tab,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.wait_timeout,
    )

    # 输出结果
    if results:
        print(f"\n[结果] 共获取 {len(results)} 条课程信息")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未获取到课程信息")


if __name__ == "__main__":
    main()
