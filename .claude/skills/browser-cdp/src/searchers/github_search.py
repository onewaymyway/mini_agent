#!/usr/bin/env python
"""
github_search.py - GitHub 代码仓库/Issue/PR 搜索自动化脚本

使用 browser-cdp skill 搜索 GitHub 代码仓库、Issue、PR 和代码片段。
GitHub 有 API 限流（未登录 60次/小时），浏览器模式可绕过部分限制。

用法:
    python github_search.py "machine learning" --type repo --max-results 10
    python github_search.py "bug authentication" --type issue --max-results 20
    python github_search.py "useState" --type code --max-results 15
    python github_search.py "transformer" --type repo --sort stars --output-dir ./github_results

示例:
    python github_search.py "langchain" --type repo --max-results 10
    python github_search.py "TypeError: undefined" --type issue --max-results 20
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
from src.searchers.baidu_search import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


# ========== GitHub 专用配置 ==========
GITHUB_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "github"
GITHUB_BASE = "https://github.com"

# GitHub 搜索类型映射
SEARCH_TYPES = {
    "repo": "repositories",
    "issue": "issues",
    "pr": "prs",
    "code": "code",
    "user": "users",
}


class GitHubSearcher(BaseSearcher):
    """GitHub 搜索器 - 支持仓库/Issue/PR/代码/用户搜索"""

    @property
    def source_name(self) -> str:
        return "github"

    @property
    def supported_types(self) -> List[str]:
        return ["repo_search", "issue_search", "pr_search", "code_search", "user_search"]

    def search(
        self,
        query: str,
        max_results: int = 10,
        search_type: str = "repo",
        sort: Optional[str] = None,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
    ) -> List[Dict]:
        """搜索 GitHub

        Args:
            query: 搜索关键词
            max_results: 最大结果数
            search_type: 搜索类型 (repo/issue/pr/code/user)
            sort: 排序方式 (stars/recently_updated/created)
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间

        Returns:
            搜索结果列表
        """
        print(f"[GitHub 搜索] 正在搜索: {query} (类型: {search_type})")

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
        github_type = SEARCH_TYPES.get(search_type, "repositories")
        url_parts = [f"https://github.com/search?q={quote(query)}"]
        url_parts.append(f"type={github_type}")
        if sort:
            url_parts.append(f"s={sort}")
        search_url = "&".join(url_parts)
        print(f"  [URL] {search_url}")

        # 导航到搜索结果页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".repo-list, .search-result-list, [data-hpc]",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return []

        time.sleep(2.0)

        # 检查是否触发验证码/限制
        js_check = r"""
(() => {
  const captcha = document.querySelector('#captcha, .application-sign-in, [class*="captcha"]');
  const rateLimit = document.querySelector('[class*="rate-limit"], [class*="rate_limit"]');
  if (captcha) return 'captcha_detected';
  if (rateLimit) return 'rate_limited';
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
            if 'rate_limited' in output:
                print("[警告] 检测到速率限制，建议等待后重试或使用已登录态")

        # 根据搜索类型提取结果
        if search_type == "repo":
            results = self._extract_repos(port, tab_id, max_results)
        elif search_type == "issue":
            results = self._extract_issues(port, tab_id, max_results)
        elif search_type == "pr":
            results = self._extract_prs(port, tab_id, max_results)
        elif search_type == "code":
            results = self._extract_code(port, tab_id, max_results)
        elif search_type == "user":
            results = self._extract_users(port, tab_id, max_results)
        else:
            results = self._extract_repos(port, tab_id, max_results)

        # 添加元数据
        for r in results:
            r["query"] = query
            r["search_type"] = search_type
            r["scraped_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

        print(f"  [结果] 共提取 {len(results)} 条结果")

        # 保存结果
        if output_dir:
            path = save_results(
                results,
                output_dir,
                f"github_{search_type}_{query.replace(' ', '_')}.json",
            )
            print(f"  [保存] {path}")

        return results

    def _extract_repos(
        self, port: int, tab_id: str, max_results: int
    ) -> List[Dict]:
        """提取仓库搜索结果"""
        js_code = r"""
(() => {
  const items = document.querySelectorAll('.repo-list .d-flex, .search-result, [data-hpc]');
  const results = [];

  items.forEach((item, i) => {
    if (i >= max_results) return;

    // 仓库名称和链接
    const titleEl = item.querySelector('h2 a, .repo-name, [class*="link"]');
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = titleEl ? titleEl.href : '';

    // 描述
    const descEl = item.querySelector('.col-9, [class*="description"], .d-table-cell.description');
    const description = descEl ? descEl.innerText.trim() : '';

    // 统计信息
    const starsEl = item.querySelector('[aria-label*="star"]');
    const stars = starsEl ? starsEl.getAttribute('aria-label') : '';

    const forksEl = item.querySelector('[aria-label*="fork"]');
    const forks = forksEl ? forksEl.getAttribute('aria-label') : '';

    // 语言
    const langEl = item.querySelector('[itemprop="programmingLanguage"]');
    const language = langEl ? langEl.getAttribute('content') : '';

    // 作者
    const authorEl = item.querySelector('[itemprop="author"]');
    const author = authorEl ? authorEl.getAttribute('content') : '';

    if (title && url) {
      results.push({
        title: title,
        url: url,
        description: description,
        stars: stars,
        forks: forks,
        language: language,
        author: author,
        source: 'github',
        type: 'repo'
      });
    }
  });

  return results;
})()
"""
        js_code = js_code.replace("max_results", str(max_results))
        return self._run_js_extract(port, tab_id, js_code)

    def _extract_issues(
        self, port: int, tab_id: str, max_results: int
    ) -> List[Dict]:
        """提取 Issue 搜索结果"""
        js_code = r"""
(() => {
  const items = document.querySelectorAll('.repo-list .d-flex, .search-result, [data-hpc]');
  const results = [];

  items.forEach((item, i) => {
    if (i >= max_results) return;

    // Issue 标题和链接
    const titleEl = item.querySelector('h2 a, .issue-title, [class*="link"]');
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = titleEl ? titleEl.href : '';

    // 状态 (open/closed)
    const stateEl = item.querySelector('.issue-icon, [class*="state"]');
    const state = stateEl ? stateEl.getAttribute('class') : '';

    // 作者
    const authorEl = item.querySelector('[itemprop="author"]');
    const author = authorEl ? authorEl.getAttribute('content') : '';

    // 评论数
    const commentsEl = item.querySelector('[aria-label*="comment"]');
    const comments = commentsEl ? commentsEl.getAttribute('aria-label') : '';

    // 时间
    const timeEl = item.querySelector('relative-time, time');
    const time = timeEl ? timeEl.getAttribute('datetime') : '';

    if (title && url) {
      results.push({
        title: title,
        url: url,
        state: state,
        author: author,
        comments: comments,
        time: time,
        source: 'github',
        type: 'issue'
      });
    }
  });

  return results;
})()
"""
        js_code = js_code.replace("max_results", str(max_results))
        return self._run_js_extract(port, tab_id, js_code)

    def _extract_prs(
        self, port: int, tab_id: str, max_results: int
    ) -> List[Dict]:
        """提取 PR 搜索结果"""
        js_code = r"""
(() => {
  const items = document.querySelectorAll('.repo-list .d-flex, .search-result, [data-hpc]');
  const results = [];

  items.forEach((item, i) => {
    if (i >= max_results) return;

    // PR 标题和链接
    const titleEl = item.querySelector('h2 a, .issue-title, [class*="link"]');
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = titleEl ? titleEl.href : '';

    // 状态 (open/merged/closed)
    const stateEl = item.querySelector('.issue-icon, [class*="state"]');
    const state = stateEl ? stateEl.getAttribute('class') : '';

    // 作者
    const authorEl = item.querySelector('[itemprop="author"]');
    const author = authorEl ? authorEl.getAttribute('content') : '';

    // 变更文件数
    const filesEl = item.querySelector('[aria-label*="file"]');
    const files = filesEl ? filesEl.getAttribute('aria-label') : '';

    // 时间
    const timeEl = item.querySelector('relative-time, time');
    const time = timeEl ? timeEl.getAttribute('datetime') : '';

    if (title && url) {
      results.push({
        title: title,
        url: url,
        state: state,
        author: author,
        files: files,
        time: time,
        source: 'github',
        type: 'pr'
      });
    }
  });

  return results;
})()
"""
        js_code = js_code.replace("max_results", str(max_results))
        return self._run_js_extract(port, tab_id, js_code)

    def _extract_code(
        self, port: int, tab_id: str, max_results: int
    ) -> List[Dict]:
        """提取代码搜索结果"""
        js_code = r"""
(() => {
  const items = document.querySelectorAll('.repo-list .d-flex, .search-result, [data-hpc]');
  const results = [];

  items.forEach((item, i) => {
    if (i >= max_results) return;

    // 文件路径和链接
    const titleEl = item.querySelector('h2 a, .file-name, [class*="link"]');
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = titleEl ? titleEl.href : '';

    // 代码片段
    const snippetEl = item.querySelector('.breadcrumb, [class*="snippet"]');
    const snippet = snippetEl ? snippetEl.innerText.trim() : '';

    // 仓库
    const repoEl = item.querySelector('[class*="repo"]');
    const repo = repoEl ? repoEl.innerText.trim() : '';

    // 语言
    const langEl = item.querySelector('[itemprop="programmingLanguage"]');
    const language = langEl ? langEl.getAttribute('content') : '';

    if (title && url) {
      results.push({
        title: title,
        url: url,
        snippet: snippet,
        repo: repo,
        language: language,
        source: 'github',
        type: 'code'
      });
    }
  });

  return results;
})()
"""
        js_code = js_code.replace("max_results", str(max_results))
        return self._run_js_extract(port, tab_id, js_code)

    def _extract_users(
        self, port: int, tab_id: str, max_results: int
    ) -> List[Dict]:
        """提取用户搜索结果"""
        js_code = r"""
(() => {
  const items = document.querySelectorAll('.repo-list .d-flex, .search-result, [data-hpc]');
  const results = [];

  items.forEach((item, i) => {
    if (i >= max_results) return;

    // 用户名和链接
    const titleEl = item.querySelector('h2 a, .user-name, [class*="link"]');
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = titleEl ? titleEl.href : '';

    // 简介
    const descEl = item.querySelector('.col-9, [class*="description"]');
    const description = descEl ? descEl.innerText.trim() : '';

    // 关注者数
    const followersEl = item.querySelector('[aria-label*="follower"]');
    const followers = followersEl ? followersEl.getAttribute('aria-label') : '';

    // 仓库数
    const reposEl = item.querySelector('[aria-label*="repository"]');
    const repos = reposEl ? reposEl.getAttribute('aria-label') : '';

    if (title && url) {
      results.push({
        title: title,
        url: url,
        description: description,
        followers: followers,
        repos: repos,
        source: 'github',
        type: 'user'
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
        """获取仓库/Issue/PR 详情"""
        print(f"[GitHub 详情] 正在获取: {url}")

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
            "--wait-selector", "body",
            "--timeout", "30",
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return {}

        time.sleep(2.0)

        # 判断页面类型并提取详情
        js_type_check = r"""
(() => {
  const path = window.location.pathname;
  if (path.startsWith('/search')) return 'search_page';
  if (path.match(/\/issues\/\d+/)) return 'issue';
  if (path.match(/\/pull\/\d+/)) return 'pr';
  if (path.match(/\/tree\//) || path.match(/\/blob\//)) return 'file';
  if (path.match(/\/commit\//)) return 'commit';
  return 'repo';
})()
"""
        type_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_type_check,
        ])

        page_type = "repo"
        if type_result.returncode == 0:
            page_type = type_result.stdout.strip().strip("'\"")

        if page_type == "issue":
            detail = self._extract_issue_detail(port, tab_id)
        elif page_type == "pr":
            detail = self._extract_pr_detail(port, tab_id)
        elif page_type == "repo":
            detail = self._extract_repo_detail(port, tab_id)
        else:
            detail = self._extract_repo_detail(port, tab_id)

        detail["url"] = url
        detail["source"] = "github"
        detail["scraped_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        return detail

    def _extract_repo_detail(self, port: int, tab_id: str) -> Dict:
        """提取仓库详情"""
        js_code = r"""
(() => {
  const result = {};

  // 仓库名称
  const titleEl = document.querySelector('h1[itemprop="name"] a, .repo-title');
  result.name = titleEl ? titleEl.innerText.trim() : '';

  // 描述
  const descEl = document.querySelector('[itemprop="description"], .f4, .repo-description'];
  result.description = descEl ? descEl.innerText.trim() : '';

  // 统计信息
  const starsEl = document.querySelector('[aria-label*="star"]');
  result.stars = starsEl ? starsEl.getAttribute('aria-label') : '';

  const forksEl = document.querySelector('[aria-label*="fork"]');
  result.forks = forksEl ? forksEl.getAttribute('aria-label') : '';

  const langEl = document.querySelector('[itemprop="programmingLanguage"]');
  result.language = langEl ? langEl.getAttribute('content') : '';

  // 作者
  const authorEl = document.querySelector('[itemprop="author"]');
  result.author = authorEl ? authorEl.getAttribute('content') : '';

  // 创建时间
  const createdEl = document.querySelector('relative-time[data-format="YYYY-MM-DD"]');
  result.created_at = createdEl ? createdEl.getAttribute('datetime') : '';

  // 最后更新
  const updatedEl = document.querySelector('relative-time[data-format="relative"]');
  result.updated_at = updatedEl ? updatedEl.getAttribute('datetime') : '';

  // License
  const licenseEl = document.querySelector('[itemprop="license"]');
  result.license = licenseEl ? licenseEl.getAttribute('content') : '';

  // Topics
  const topics = [];
  document.querySelectorAll('.topic-tag').forEach(t => topics.push(t.innerText.trim()));
  result.topics = topics;

  return result;
})()
"""
        return self._run_js_detail(port, tab_id, js_code)

    def _extract_issue_detail(self, port: int, tab_id: str) -> Dict:
        """提取 Issue 详情"""
        js_code = r"""
(() => {
  const result = {};

  // Issue 标题
  const titleEl = document.querySelector('h1.markdown-title, .js-issue-title');
  result.title = titleEl ? titleEl.innerText.trim() : '';

  // 状态
  const stateEl = document.querySelector('.state, .issue-state');
  result.state = stateEl ? stateEl.innerText.trim() : '';

  // 作者
  const authorEl = document.querySelector('[itemprop="author"]');
  result.author = authorEl ? authorEl.getAttribute('content') : '';

  // 创建时间
  const timeEl = document.querySelector('relative-time');
  result.created_at = timeEl ? timeEl.getAttribute('datetime') : '';

  // Issue 正文
  const bodyEl = document.querySelector('.js-comment-body, .markdown-body');
  result.body = bodyEl ? bodyEl.innerText.trim() : '';

  // 评论数
  const commentsEl = document.querySelector('[aria-label*="comment"]');
  result.comments = commentsEl ? commentsEl.getAttribute('aria-label') : '';

  return result;
})()
"""
        return self._run_js_detail(port, tab_id, js_code)

    def _extract_pr_detail(self, port: int, tab_id: str) -> Dict:
        """提取 PR 详情"""
        js_code = r"""
(() => {
  const result = {};

  // PR 标题
  const titleEl = document.querySelector('h1.markdown-title, .js-issue-title');
  result.title = titleEl ? titleEl.innerText.trim() : '';

  // 状态
  const stateEl = document.querySelector('.state, .pr-state');
  result.state = stateEl ? stateEl.innerText.trim() : '';

  // 作者
  const authorEl = document.querySelector('[itemprop="author"]');
  result.author = authorEl ? authorEl.getAttribute('content') : '';

  // 创建时间
  const timeEl = document.querySelector('relative-time');
  result.created_at = timeEl ? timeEl.getAttribute('datetime') : '';

  // PR 正文
  const bodyEl = document.querySelector('.js-comment-body, .markdown-body');
  result.body = bodyEl ? bodyEl.innerText.trim() : '';

  // 变更文件数
  const filesEl = document.querySelector('[aria-label*="file"]');
  result.files = filesEl ? filesEl.getAttribute('aria-label') : '';

  // 提交数
  const commitsEl = document.querySelector('[aria-label*="commit"]');
  result.commits = commitsEl ? commitsEl.getAttribute('aria-label') : '';

  return result;
})()
"""
        return self._run_js_detail(port, tab_id, js_code)

    def _run_js_detail(self, port: int, tab_id: str, js_code: str) -> Dict:
        """执行 JS 提取详情"""
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
        description="GitHub 代码仓库/Issue/PR 搜索脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python github_search.py "machine learning" --type repo --max-results 10
    python github_search.py "bug authentication" --type issue --max-results 20
    python github_search.py "useState" --type code --max-results 15
    python github_search.py "langchain" --type repo --sort stars --output-dir ./github_results
"""
    )

    parser.add_argument("query", help="搜索关键词")
    parser.add_argument(
        "--type",
        choices=["repo", "issue", "pr", "code", "user"],
        default="repo",
        help="搜索类型 (默认: repo)",
    )
    parser.add_argument(
        "--sort",
        choices=["stars", "recently_updated", "created"],
        default=None,
        help="排序方式 (默认: 相关度)",
    )
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
    searcher = GitHubSearcher()

    # 执行搜索
    results = searcher.search(
        query=args.query,
        max_results=args.max_results,
        search_type=args.type,
        sort=args.sort,
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
