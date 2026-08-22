"""
browser-site-scraper / members / zhihu / script.py

统一接口: run(input: dict) -> dict
input 约定: {"text": "...", "target": {"url": "https://www.zhihu.com/search?..."},
             "query": "关键词", "session": {...}}  # session 可选，见下方说明
返回: {"status": "success"|"fail", "data": {"results": [...]} | None, "error": str | None}

依赖 `browser-core`（不再依赖 `browser-cdp`），设计说明与
`members/baidu/script.py` 完全一致，见该文件头注释；本文件不重复展开。

知乎搜索页对未登录访问有比较明显的限制（部分内容需要登录才能看到完整
正文/更多结果），这正是本次改动新增 `attach` 会话模式要解决的场景之一：
如果发现搜索结果异常稀少或页面提示登录，应该建议调用方改用
`session: {"mode": "attach", "port": ...}` 连接一个已经手动登录好知乎的
浏览器，而不是本 member 自己尝试处理登录——登录不是这个 member 的职责，
见 `browser-core/SKILL.md`"会话模式"一节。
"""

from __future__ import annotations

import sys
import urllib.parse
from pathlib import Path

_BROWSER_CORE_IMPL_DIR = Path(__file__).resolve().parents[3] / "browser-core" / "impl"

# 知乎搜索结果页的提取逻辑（迁移自原 browser-cdp/src/searchers/zhihu_search.py
# 的 `_extract_results_js`，逻辑未改动，只是换了个执行载体）。
_EXTRACT_RESULTS_JS = """
(function() {
    var results = [];

    var selectors = [
        '.List-item',
        '.SearchResult-Item',
        '[data-zop-search-result]',
        '.ContentItem',
        '.SearchResult'
    ];

    var containers = [];
    selectors.forEach(function(sel) {
        var elements = document.querySelectorAll(sel);
        elements.forEach(function(el) {
            if (el.querySelector('.ContentItem-title') || el.querySelector('a[href*="/question/"]')) {
                containers.push(el);
            }
        });
    });

    containers.forEach(function(container) {
        var titleEl = container.querySelector('.ContentItem-title, h1, h2, h3, a[href]');
        var linkEl = container.querySelector('a[href*="/question/"], a[href*="/p/"]');
        var snippetEl = container.querySelector('.ContentItem-content, .RichContent-inner, .SearchResult-Content');
        var authorEl = container.querySelector('.AuthorInfo-name, [class*="author"]');

        if (titleEl || linkEl) {
            var title = titleEl ? titleEl.textContent.trim() : '';
            var url = linkEl ? linkEl.href : '';
            var snippet = snippetEl ? snippetEl.textContent.trim() : '';
            var author = authorEl ? authorEl.textContent.trim() : '';

            if (title && title.length > 2 && url && url.startsWith('http')) {
                results.push({
                    title: title.substring(0, 200),
                    url: url,
                    snippet: snippet.substring(0, 500),
                    author: author
                });
            }
        }
    });

    return results;
})()
"""

_LOGIN_WALL_CHECK_JS = """
(function() {
    var text = document.body ? document.body.innerText : '';
    var indicators = ['登录知乎', '打开知乎App', '验证码'];
    for (var i = 0; i < indicators.length; i++) {
        if (text.indexOf(indicators[i]) !== -1) return indicators[i];
    }
    return null;
})()
"""


def run(input: dict) -> dict:
    query = input.get("query") or input.get("text", "")
    if not query:
        return {"status": "fail", "data": None, "error": "缺少 query/text 参数"}

    if not _BROWSER_CORE_IMPL_DIR.exists():
        return {
            "status": "fail",
            "data": None,
            "error": f"依赖目录不存在: {_BROWSER_CORE_IMPL_DIR}（browser-core 未正确安装）",
        }

    impl_dir_str = str(_BROWSER_CORE_IMPL_DIR)
    if impl_dir_str not in sys.path:
        sys.path.insert(0, impl_dir_str)

    try:
        import session_manager  # type: ignore
        from cdp_client import CDPError  # type: ignore
    except Exception as e:  # noqa: BLE001
        return {"status": "fail", "data": None, "error": f"加载 browser-core 失败: {e}"}

    target_url = (input.get("target") or {}).get("url") or (
        f"https://www.zhihu.com/search?type=content&q={urllib.parse.quote(query)}"
    )

    try:
        session = session_manager.get_or_create_session(input.get("session"))
        session.navigate(target_url, timeout=20.0)
        blocked_reason = session.eval_js(_LOGIN_WALL_CHECK_JS)
        raw_results = session.eval_js(_EXTRACT_RESULTS_JS)
    except CDPError as e:
        return {"status": "fail", "data": None, "error": f"搜索执行失败: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"status": "fail", "data": None, "error": f"搜索执行失败(可能无可用浏览器): {e}"}

    if not isinstance(raw_results, list):
        return {"status": "fail", "data": None, "error": f"提取结果返回了非预期结构: {raw_results!r}"}

    if not raw_results and blocked_reason:
        return {
            "status": "fail",
            "data": None,
            "error": (
                f"知乎页面提示需要登录/验证（检测到关键词「{blocked_reason}」），且没有提取到"
                f"任何结果，如实报告失败。可以改用 session.mode='attach' 连接一个已经手动登录"
                f"好知乎的浏览器再重试（见 browser-core/SKILL.md 会话模式一节）。"
            ),
        }

    max_results = input.get("max_results", 10)
    results = raw_results[:max_results]
    return {"status": "success", "data": {"results": results}, "error": None}
