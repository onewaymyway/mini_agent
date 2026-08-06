#!/usr/bin/env python
"""
stackoverflow_search.py - Stack Overflow 问题搜索自动化脚本

使用 browser-cdp skill 搜索 Stack Overflow 技术问题，获取问题标题、答案、投票数、标签等信息。
Stack Overflow 反爬较弱，但仍建议启用 stealth 模式。

用法:
    python stackoverflow_search.py "python pandas merge dataframe" --max-results 10
    python stackoverflow_search.py "react useEffect cleanup" --max-results 5 --output-dir ./so_results
    python stackoverflow_search.py "javascript async await" --port 9333 --stealth

示例:
    python stackoverflow_search.py "python pandas merge dataframe" --max-results 10
    python stackoverflow_search.py "react useEffect cleanup" --max-results 5 --output-dir ./so_results
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
from src.searchers.utils import (
    random_delay, get_random_ua, save_results, dedup_results, clean_text
)
from src.searchers.browser_utils import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


# ========== Stack Overflow 专用配置 ==========
SO_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "stackoverflow"
SO_BASE = "https://stackoverflow.com"


class StackOverflowSearcher(BaseSearcher):
    """Stack Overflow 问题搜索器"""

    @property
    def source_name(self) -> str:
        return "stackoverflow"

    @property
    def supported_types(self) -> List[str]:
        return ["question_search", "question_detail"]

    def search(
        self,
        query: str,
        max_results: int = 10,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
    ) -> List[Dict]:
        """搜索 Stack Overflow 问题

        Args:
            query: 搜索关键词
            max_results: 最大结果数
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间

        Returns:
            问题列表
        """
        print(f"[Stack Overflow] 正在搜索: {query}")

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
        delay = random_delay(1.5, 3.0)
        print(f"  [延迟] 请求前等待 {delay:.1f} 秒")
        time.sleep(delay)

        # 构建搜索 URL
        search_url = f"{SO_BASE}/search?q={quote(query)}"
        print(f"  [URL] {search_url}")

        # 导航到搜索结果页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".question-summary, .search-result",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return []

        time.sleep(2.0)

        # 检查是否触发验证码
        js_check = r"""
(() => {
  const captcha = document.querySelector('#captcha, .g-recaptcha, [class*="captcha"]');
  const blocked = document.querySelector('[class*="blocked"], [class*="access-denied"]');
  if (captcha) return 'captcha_detected';
  if (blocked) return 'blocked';
  return 'ok';
})()
"""
        check_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_check,
        ])

        if check_result.returncode == 0:
            output = check_result.stdout.strip()
            if 'captcha_detected' in output:
                print("[警告] 检测到验证码，请手动完成验证后重试")
                return []
            if 'blocked' in output:
                print("[警告] 访问被限制，建议启用 stealth 模式或使用代理")

        # 提取搜索结果
        results = self._extract_questions(port, tab_id, max_results)

        # 添加元数据
        for r in results:
            r["query"] = query
            r["scraped_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

        print(f"  [结果] 共提取 {len(results)} 条结果")

        # 保存结果
        if output_dir:
            path = save_results(
                results,
                output_dir,
                f"so_{query.replace(' ', '_')}.json",
            )
            print(f"  [保存] {path}")

        return results

    def _extract_questions(
        self, port: int, tab_id: str, max_results: int
    ) -> List[Dict]:
        """提取问题搜索结果"""
        js_code = r"""
(() => {
  const items = document.querySelectorAll('.question-summary, .search-result');
  const results = [];

  items.forEach((item, i) => {
    if (i >= max_results) return;

    // 标题和链接
    const titleEl = item.querySelector('h3 a, .question-hyperlink');
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = titleEl ? titleEl.href : '';

    // 摘要
    const excerptEl = item.querySelector('.excerpt, .search-result-excerpt');
    const excerpt = excerptEl ? excerptEl.innerText.trim() : '';

    // 投票数
    const votesEl = item.querySelector('.vote-count-post');
    const votes = votesEl ? votesEl.innerText.trim() : '0';

    // 答案数
    const answersEl = item.querySelector('.views, .answer-count');
    const answers = answersEl ? answersEl.innerText.trim() : '0';

    // 查看数
    const viewsEl = item.querySelector('.views');
    const views = viewsEl ? viewsEl.innerText.trim() : '0';

    // 标签
    const tags = [];
    item.querySelectorAll('.post-tag').forEach(t => tags.push(t.innerText.trim()));

    // 作者
    const authorEl = item.querySelector('.user-info a, .question-author');
    const author = authorEl ? authorEl.innerText.trim() : '';

    // 时间
    const timeEl = item.querySelector('relative-time, .relativetime');
    const time = timeEl ? timeEl.getAttribute('datetime') || timeEl.innerText.trim() : '';

    if (title && url) {
      results.push({
        title: title,
        url: url,
        excerpt: excerpt,
        votes: votes,
        answers: answers,
        views: views,
        tags: tags,
        author: author,
        time: time,
        source: 'stackoverflow',
        type: 'question'
      });
    }
  });

  return results;
})()
"""
        js_code = js_code.replace("max_results", str(max_results))
        return self._run_js_extract(port, tab_id, js_code)

    def _run_js_extract(
        self, port: int, tab_id: str, js_code: str
    ) -> List[Dict]:
        """执行 JS 提取并解析结果"""
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

        # 去重
        return dedup_results(raw_results, by="url")

    def get_detail(
        self,
        url: str,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
    ) -> Dict:
        """获取问题详情"""
        print(f"[Stack Overflow 详情] 正在获取: {url}")

        # 确保浏览器连接
        if tab_id is None:
            result = ensure_browser(port=port, stealth=stealth)
            if result.get("error"):
                print(f"[错误] 浏览器启动失败: {result['error']}")
                return {}
            tab_id = result.get("tab_id")
            port = result.get("port", port)

        # 导航到问题详情页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", url,
            "--wait-selector", ".postcell, .answer",
            "--timeout", "30",
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return {}

        time.sleep(2.0)

        # 提取详情信息
        detail = self._extract_question_detail(port, tab_id)
        detail["url"] = url
        detail["source"] = "stackoverflow"
        detail["scraped_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        return detail

    def _extract_question_detail(self, port: int, tab_id: str) -> Dict:
        """提取问题详情"""
        js_code = r"""
(() => {
  const result = {};

  // 问题标题
  const titleEl = document.querySelector('h1.post-title, .question-title');
  result.title = titleEl ? titleEl.innerText.trim() : '';

  // 问题正文
  const bodyEl = document.querySelector('.postcell .post-text, .question .post-text');
  result.body = bodyEl ? bodyEl.innerText.trim() : '';

  // 投票数
  const votesEl = document.querySelector('.vote-count-post');
  result.votes = votesEl ? votesEl.innerText.trim() : '0';

  // 答案数
  const answersEl = document.querySelector('.answers-count');
  result.answers = answersEl ? answersEl.innerText.trim() : '0';

  // 查看数
  const viewsEl = document.querySelector('.views');
  result.views = viewsEl ? viewsEl.innerText.trim() : '0';

  // 标签
  const tags = [];
  document.querySelectorAll('.post-tag').forEach(t => tags.push(t.innerText.trim()));
  result.tags = tags;

  // 作者
  const authorEl = document.querySelector('.user-details a, .question-hyperlink');
  result.author = authorEl ? authorEl.innerText.trim() : '';

  // 创建时间
  const timeEl = document.querySelector('relative-time, .relativetime');
  result.created_at = timeEl ? timeEl.getAttribute('datetime') || timeEl.innerText.trim() : '';

  // 提取答案列表
  const answers = [];
  document.querySelectorAll('.answer').forEach((ans, i) => {
    if (i >= 5) return; // 最多提取 5 个答案

    const answerBody = ans.querySelector('.post-text');
    const answerVotes = ans.querySelector('.vote-count-post');
    const answerAuthor = ans.querySelector('.user-details a');
    const answerTime = ans.querySelector('relative-time, .relativetime');
    const isAccepted = ans.classList.contains('accepted-answer');

    answers.push({
      body: answerBody ? answerBody.innerText.trim() : '',
      votes: answerVotes ? answerVotes.innerText.trim() : '0',
      author: answerAuthor ? answerAuthor.innerText.trim() : '',
      time: answerTime ? answerTime.getAttribute('datetime') || answerTime.innerText.trim() : '',
      accepted: isAccepted
    });
  });
  result.answers_list = answers;

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
            return json.loads(extract_result.stdout)
        except json.JSONDecodeError:
            print(f"[错误] JSON 解析失败: {extract_result.stdout[:200]}")
            return {}


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="Stack Overflow 问题搜索脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python stackoverflow_search.py "python pandas merge dataframe" --max-results 10
    python stackoverflow_search.py "react useEffect cleanup" --max-results 5 --output-dir ./so_results
    python stackoverflow_search.py "javascript async await" --port 9333 --stealth
"""
    )

    parser.add_argument("query", help="搜索关键词")
    parser.add_argument(
        "--max-results", type=int, default=10, help="最大结果数 (默认: 10)"
    )
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument(
        "--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)"
    )
    parser.add_argument("--tab", type=str, default=None, help="Tab ID")
    parser.add_argument(
        "--stealth", action="store_true", default=True, help="启用反检测模式"
    )
    parser.add_argument(
        "--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式"
    )
    parser.add_argument(
        "--wait-timeout", type=int, default=30, help="等待超时时间 (默认: 30秒)"
    )

    args = parser.parse_args()

    # 创建搜索器
    searcher = StackOverflowSearcher()

    # 执行搜索
    results = searcher.search(
        query=args.query,
        max_results=args.max_results,
        port=args.port,
        tab_id=args.tab,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.wait_timeout,
    )

    # 输出结果
    if results:
        print(f"\n[结果] 共找到 {len(results)} 条结果")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未找到结果")


if __name__ == "__main__":
    main()
