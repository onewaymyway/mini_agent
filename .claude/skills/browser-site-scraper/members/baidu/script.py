"""
browser-site-scraper / members / baidu / script.py

统一接口: run(input: dict) -> dict
input 约定: {"text": "...", "target": {"url": "https://www.baidu.com/s?wd=..."},
             "query": "关键词", "session": {...}}  # session 可选，见下方说明
返回: {"status": "success"|"fail", "data": {"results": [...]} | None, "error": str | None}

本 member 依赖 `browser-core`（**不再依赖 `browser-cdp`**——`browser-cdp`
即将被移除，`browser-core`/`browser-site-scraper` 是它的替代方案，见
`browser-core/SKILL.md` 与 `browser-site-scraper/SKILL.md` 的说明）。

与阶段十四的探索链路（`real_tools.py::load_skill_local_tool_
implementations`）复用的是同一份 `browser-core/impl/`，但走法不同：那条
链路是探索子agent通过通用的 7 个工具原语一步步"摸索"出一个网站怎么抓；
本 member 是**已经知道百度搜索结果页结构**的人工预置 member，直接调用
`browser-core/impl/` 里更底层的 `session_manager`/`cdp_client` 做一次
"导航 + 执行一段百度专用提取 JS"，不经过 `tool_executor` 的通用分发层
（会多一层没必要的间接调用），这是"member 自己知道网站怎么抓、就该自己
直接抓"与"探索子agent不知道时才需要通用原语一步步试"两种场景的正常分工。
"""

from __future__ import annotations

import sys
import urllib.parse
from pathlib import Path

# browser-core/impl 目录相对本文件的路径：
#   .claude/skills/browser-site-scraper/members/baidu/script.py
#   .claude/skills/browser-core/impl/
_BROWSER_CORE_IMPL_DIR = Path(__file__).resolve().parents[3] / "browser-core" / "impl"

# 百度搜索结果页的提取逻辑（迁移自原 browser-cdp/src/searchers/baidu_search.py
# 的 `_extract_results_js`，逻辑未改动，只是换了个执行载体——之前通过
# browser-cdp 自己的 CDP 封装执行，现在通过 browser-core 的
# `CDPSession.eval_js` 执行）。
_EXTRACT_RESULTS_JS = """
(function() {
    var results = [];
    var containers = document.querySelectorAll('#content_left .result, .c-container, .result-op');

    containers.forEach(function(container) {
        var titleEl = container.querySelector('h3 a, .t a, .c-title a, a[mu]');
        var linkEl = container.querySelector('a[href]');
        var snippetEl = container.querySelector('.c-abstract, .abstract, .content-right_8Zs40, .content_1ZcWe');
        var timeEl = container.querySelector('.c-color-gray, .nums_tab, [class*="time"]');

        if (titleEl || linkEl) {
            var title = titleEl ? titleEl.textContent.trim() : '';
            var url = linkEl ? linkEl.href : '';
            var snippet = snippetEl ? snippetEl.textContent.trim() : '';
            var publish_time = timeEl ? timeEl.textContent.trim() : '';

            if (title && title.length > 2 && url && url !== 'javascript:void(0)') {
                results.push({
                    title: title.substring(0, 200),
                    url: url,
                    snippet: snippet.substring(0, 500),
                    published_time: publish_time
                });
            }
        }
    });

    return results;
})()
"""

# 百度对没有真实结果容器的页面（验证码/风控拦截页等）不会抛异常，只会让
# 上面这段 JS 找不到任何 container、返回空数组——这在旧的
# browser-cdp/baidu_search.py 里会被误判成"搜索成功但没有结果"（一个真实
# 出现过的 bug）。这里额外检测一下页面是否命中常见的验证码/异常关键词，
# 命中时**如实报告失败**而不是返回一个看似成功、实则是空壳的结果。
_ANTI_BOT_CHECK_JS = """
(function() {
    var text = document.body ? document.body.innerText : '';
    var indicators = ['验证码', '安全验证', 'unusual traffic', '网络不给力'];
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
        from browser_core_impl import capture_debug_context  # type: ignore
    except Exception as e:  # noqa: BLE001
        return {"status": "fail", "data": None, "error": f"加载 browser-core 失败: {e}"}

    target_url = (input.get("target") or {}).get("url") or (
        f"https://www.baidu.com/s?wd={urllib.parse.quote(query)}"
    )

    try:
        session = session_manager.get_or_create_session(input.get("session"))
        session.navigate(target_url, timeout=20.0)
        blocked_reason = session.eval_js(_ANTI_BOT_CHECK_JS)
        if blocked_reason:
            return {
                "status": "fail",
                "data": None,
                "error": (
                    f"百度返回了疑似验证码/风控拦截页（检测到关键词「{blocked_reason}」），"
                    f"不是真正的搜索结果页，如实报告失败而不是返回空结果。可以尝试改用"
                    f"session.mode='attach' 连接一个手动打开、已经通过验证的浏览器再重试。"
                ),
            }
        raw_results = session.eval_js(_EXTRACT_RESULTS_JS)
    except CDPError as e:
        return {"status": "fail", "data": None, "error": f"搜索执行失败: {e}"}
    except Exception as e:  # noqa: BLE001
        # 沙盒/CI 等环境通常没有可用的真实浏览器，这里如实返回失败原因，
        # 用于验证 execute() 的失败路径与 registry 的 fail_count 记录是否
        # 正确，而不是伪造一份假数据掩盖问题。
        return {"status": "fail", "data": None, "error": f"搜索执行失败(可能无可用浏览器): {e}"}

    if not isinstance(raw_results, list):
        return {"status": "fail", "data": None, "error": f"提取结果返回了非预期结构: {raw_results!r}"}

    max_results = input.get("max_results", 10)
    results = raw_results[:max_results]

    if not results:
        # 阶段十七：已知问题——已知的反爬关键词都没命中，但提取脚本还是找
        # 不到任何结果容器时，此前会直接返回"成功但 results: []"，看起来
        # 像是真的搜索到 0 条结果，实际更可能是选择器过期/页面结构变化/
        # 内容异步渲染还没完成/命中了未知类型的拦截页——对"test"这类肯定
        # 有结果的查询词，0 条结果本身就是一个需要排查的信号，不应该被
        # 静默当成成功。这里附带一份调试快照（url/title/正文摘要）一起
        # 报告失败，而不是让调用方拿着一个内容为空、无从排查的"success"。
        debug = capture_debug_context(session)
        return {
            "status": "fail",
            "data": None,
            "error": (
                "提取到 0 条结果，且未命中已知的验证码/风控关键词——更可能是"
                "选择器过期/页面结构变化/内容尚未渲染完成，而不是真的没有搜索"
                f"结果。调试信息: {debug!r}"
            ),
        }

    return {"status": "success", "data": {"results": results}, "error": None}
