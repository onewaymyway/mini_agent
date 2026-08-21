"""
browser-site-scraper / members / zhihu / script.py

统一接口: run(input: dict) -> dict
input 约定: {"text": "...", "target": {"url": "https://www.zhihu.com/search?q=..."},
             "query": "关键词"}
返回: {"status": "success"|"fail", "data": {"results": [...]} | None, "error": str | None}

复用 browser-cdp 原有的 src/searchers/zhihu_search.py（通过百度 site:zhihu.com 检索），
适配层不重新实现搜索逻辑，参见 members/baidu/script.py 的同一模式。
"""

from __future__ import annotations

import sys
from pathlib import Path

_BROWSER_CDP_DIR = Path(__file__).resolve().parents[3] / "browser-cdp"


def run(input: dict) -> dict:
    query = input.get("query") or input.get("text", "")
    if not query:
        return {"status": "fail", "data": None, "error": "缺少 query/text 参数"}

    if not _BROWSER_CDP_DIR.exists():
        return {
            "status": "fail",
            "data": None,
            "error": f"依赖目录不存在: {_BROWSER_CDP_DIR}",
        }

    sys.path.insert(0, str(_BROWSER_CDP_DIR))
    try:
        from src.searchers.zhihu_search import search_zhihu_via_baidu  # type: ignore
    except Exception as e:  # noqa: BLE001
        return {"status": "fail", "data": None, "error": f"加载 zhihu_search 失败: {e}"}

    try:
        search_results = search_zhihu_via_baidu(query, max_results=input.get("max_results", 10))
    except Exception as e:  # noqa: BLE001
        return {"status": "fail", "data": None, "error": f"搜索执行失败(可能无可用浏览器): {e}"}

    try:
        results = [r.to_dict() if hasattr(r, "to_dict") else r for r in search_results.results]
    except Exception:
        results = getattr(search_results, "results", [])

    return {"status": "success", "data": {"results": results}, "error": None}
