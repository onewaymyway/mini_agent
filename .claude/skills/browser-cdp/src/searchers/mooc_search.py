#!/usr/bin/env python
"""
mooc_search.py - 中国大学MOOC搜索器

使用 browser-cdp skill 搜索中国大学MOOC（icourse163.org）课程，
支持课程列表搜索和课程详情抓取，输出 JSON 格式结果。

用法:
    python mooc_search.py "Python" --max-results 10
    python mooc_search.py "机器学习" --university "北京大学" --output-dir ./mooc_results
    python mooc_search.py "数据结构" --detail --port 9333

示例:
    python mooc_search.py "Python" --max-results 10
    python mooc_search.py "机器学习" --university "清华大学" --output-dir ./mooc_results
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
from src.searchers.utils import random_delay, save_results, dedup_results
from src.searchers.baidu_search import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


# ========== 中国大学MOOC专用配置 ==========
MOOC_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "mooc"
MOOC_BASE = "https://www.icourse163.org"
MOOC_SEARCH_URL = f"{MOOC_BASE}/web/search.htm"


class MoocSearcher(BaseSearcher):
    """中国大学MOOC搜索器"""

    @property
    def source_name(self) -> str:
        return "mooc"

    @property
    def supported_types(self) -> List[str]:
        return ["course_search", "course_detail", "university_filter"]

    def search(
        self,
        query: str,
        max_results: int = 10,
        university: Optional[str] = None,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
    ) -> List[Dict]:
        """搜索MOOC课程

        Args:
            query: 搜索关键词
            max_results: 最大结果数
            university: 按高校筛选（可选）
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间

        Returns:
            搜索结果列表
        """
        print(f"[MOOC搜索] 正在搜索课程: {query}")
        if university:
            print(f"[MOOC搜索] 筛选高校: {university}")

        # 确保浏览器连接
        if tab_id is None:
            result = ensure_browser(port=port, stealth=stealth)
            if result.get("error"):
                print(f"[错误] 浏览器启动失败: {result['error']}")
                return []
            tab_id = result.get("tab_id")
            port = result.get("port", port)
            print(f"[浏览器] 端口: {port}, Tab: {tab_id}")

        # 请求前随机延迟
        delay = random_delay(1.0, 2.0)
        print(f"  [延迟] 请求前等待 {delay:.1f} 秒")

        # 构建搜索 URL
        encoded_query = quote(query)
        search_url = f"{MOOC_SEARCH_URL}?keyword={encoded_query}"
        if university:
            search_url += f"&university={quote(university)}"
        print(f"  [URL] {search_url}")

        # 导航到搜索结果页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".course-card,.course-item,.search-result",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return []

        time.sleep(2.0)

        # 使用 JS 提取搜索结果
        js_code = r"""
(() => {
  // 尝试多种选择器以适配不同页面结构
  const selectors = [
    '.course-card',
    '.course-item',
    '.search-result .course',
    '[class*="course"][class*="card"]',
    '.j-course-card'
  ];

  let items = [];
  for (const sel of selectors) {
    items = document.querySelectorAll(sel);
    if (items.length > 0) break;
  }

  const results = [];
  items.forEach((item, i) => {
    if (i >= 30) return;

    // 标题
    const titleEl = item.querySelector('.course-title, .course-name, h3, [class*="title"]');
    const title = titleEl ? titleEl.innerText.trim() : '';

    // 链接
    const linkEl = item.querySelector('a[href*="/course/"]');
    let url = linkEl ? linkEl.href : '';
    if (url && !url.startsWith('http')) {
      url = 'https:' + url;
    }

    // 高校名称
    const uniEl = item.querySelector('.university, .school, [class*="university"], [class*="school"]');
    const university = uniEl ? uniEl.innerText.trim() : '';

    // 讲师
    const teacherEl = item.querySelector('.teacher, .professor, [class*="teacher"], [class*="professor"]');
    const teacher = teacherEl ? teacherEl.innerText.trim() : '';

    // 简介/摘要
    const descEl = item.querySelector('.course-desc, .intro, .description, [class*="desc"]');
    const description = descEl ? descEl.innerText.trim() : '';

    // 学生数
    const studentEl = item.querySelector('.student, .learner, [class*="student"], [class*="learner"]');
    const students = studentEl ? studentEl.innerText.trim() : '';

    // 评分
    const ratingEl = item.querySelector('.rating, .score, [class*="rating"]');
    const rating = ratingEl ? ratingEl.innerText.trim() : '';

    if (title && url) {
      results.push({
        title: title,
        url: url,
        university: university,
        teacher: teacher,
        description: description,
        students: students,
        rating: rating,
        source: 'mooc',
        type: 'course'
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
            "--eval", js_code,
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

        print(f"  [结果] 共提取 {len(results)} 条结果")

        # 保存结果
        if output_dir:
            path = save_results(
                results,
                output_dir,
                f"mooc_{query.replace(' ', '_')}.json",
            )
            print(f"  [保存] {path}")

        return results

    def get_detail(
        self,
        url: str,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        wait_timeout: int = 30,
    ) -> Dict:
        """获取课程详情"""
        print(f"[MOOC详情] 正在获取: {url}")

        # 确保浏览器连接
        if tab_id is None:
            result = ensure_browser(port=port, stealth=stealth)
            if result.get("error"):
                print(f"[错误] 浏览器启动失败: {result['error']}")
                return {}
            tab_id = result.get("tab_id")
            port = result.get("port", port)

        # 导航到课程详情页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", url,
            "--wait-selector", ".course-info,.course-detail,body",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return {}

        time.sleep(2.0)

        # 提取详情信息
        js_code = r"""
(() => {
  const result = {};

  // 课程标题
  const titleEl = document.querySelector('h1, .course-title, .j-course-title, [class*="course-title"]');
  result.title = titleEl ? titleEl.innerText.trim() : '';

  // 高校
  const uniEl = document.querySelector('.university, .school, [class*="university"], [class*="school"]');
  result.university = uniEl ? uniEl.innerText.trim() : '';

  // 讲师
  const teacherEl = document.querySelector('.teacher, .professor, [class*="teacher"], [class*="professor"]');
  result.teacher = teacherEl ? teacherEl.innerText.trim() : '';

  // 课程简介
  const descEl = document.querySelector('.course-desc, .intro, .description, .course-intro, [class*="desc"]');
  result.description = descEl ? descEl.innerText.trim() : '';

  // 学生人数
  const studentEl = document.querySelector('.student, .learner, [class*="student"], [class*="learner"]');
  result.students = studentEl ? studentEl.innerText.trim() : '';

  // 评分
  const ratingEl = document.querySelector('.rating, .score, [class*="rating"]');
  result.rating = ratingEl ? ratingEl.innerText.trim() : '';

  // 课程状态（开课中/已结束）
  const statusEl = document.querySelector('.course-status, .status, [class*="status"]');
  result.status = statusEl ? statusEl.innerText.trim() : '';

  // 课程章节数
  const chapterEl = document.querySelector('.chapter-count, [class*="chapter"]');
  result.chapters = chapterEl ? chapterEl.innerText.trim() : '';

  // 授课语言
  const langEl = document.querySelector('.language, [class*="language"]');
  result.language = langEl ? langEl.innerText.trim() : '';

  // 课程类型（免费/认证）
  const typeEl = document.querySelector('.course-type, .cert-type, [class*="type"]');
  result.course_type = typeEl ? typeEl.innerText.trim() : '';

  return result;
})()
"""
        extract_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_code,
        ])

        if extract_result.returncode != 0:
            print(f"[错误] 详情提取失败: {extract_result.stderr[:200]}")
            return {}

        try:
            detail = json.loads(extract_result.stdout)
            detail['source'] = 'mooc'
            detail['url'] = url
            detail['scraped_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
            return detail
        except json.JSONDecodeError:
            print(f"[错误] JSON 解析失败: {extract_result.stdout[:200]}")
            return {}


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="中国大学MOOC搜索自动化脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python mooc_search.py "Python" --max-results 10
    python mooc_search.py "机器学习" --university "北京大学" --output-dir ./mooc_results
    python mooc_search.py "数据结构" --detail --port 9333
"""
    )

    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--max-results", type=int, default=10, help="最大结果数 (默认: 10)")
    parser.add_argument("--university", type=str, default=None, help="按高校筛选（如：北京大学）")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--tab", type=str, default=None, help="Tab ID")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式")
    parser.add_argument("--wait-timeout", type=int, default=30, help="等待超时时间 (默认: 30秒)")
    parser.add_argument("--detail", action="store_true", help="获取每个结果的课程详情")

    args = parser.parse_args()

    # 创建搜索器
    searcher = MoocSearcher()

    # 执行搜索
    results = searcher.search(
        query=args.query,
        max_results=args.max_results,
        university=args.university,
        port=args.port,
        tab_id=args.tab,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.wait_timeout,
    )

    if not results:
        print("[结果] 未找到结果")
        return 1

    # 可选：获取详情
    if args.detail:
        print(f"\n[详情] 正在获取 {len(results)} 个课程的详情...")
        for i, r in enumerate(results):
            print(f"  [{i + 1}/{len(results)}] {r.get('title', '')[:40]}...")
            detail = searcher.get_detail(
                url=r['url'],
                port=args.port,
                tab_id=args.tab,
                stealth=args.stealth,
                wait_timeout=args.wait_timeout,
            )
            if detail:
                r.update(detail)
            time.sleep(random.uniform(1.0, 2.0))

    # 输出结果
    print(f"\n[结果] 共找到 {len(results)} 条结果")
    print(json.dumps(results, ensure_ascii=False, indent=2))

    # 保存结果
    if args.output_dir:
        output_path = save_results(results, args.output_dir, f"mooc_{args.query.replace(' ', '_')}.json")
        print(f"[保存] 结果已保存到: {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
