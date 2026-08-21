"""
browser-site-scraper / members / baidu / script.py

统一接口: run(input: dict) -> dict
input 约定: {"text": "...", "target": {"url": "https://www.baidu.com/s?wd=..."},
             "query": "关键词"}
返回: {"status": "success"|"fail", "data": {"results": [...]} | None, "error": str | None}

本 member 复用 browser-cdp 原有的 src/searchers/baidu_search.py 实现，
只是补上统一的适配层，不重新实现搜索逻辑（迁移阶段一：包装既有脚本，
不重写内部逻辑，符合方案文档"迁移路径"第2步）。
"""

from __future__ import annotations

import sys
from pathlib import Path

# browser-cdp 目录相对本文件的路径:
#   .claude/skills/browser-site-scraper/members/baidu/script.py
#   .claude/skills/browser-cdp/
_BROWSER_CDP_DIR = Path(__file__).resolve().parents[3] / "browser-cdp"


def run(input: dict) -> dict:
    query = input.get("query") or input.get("text", "")
    if not query:
        return {"status": "fail", "data": None, "error": "缺少 query/text 参数"}

    if not _BROWSER_CDP_DIR.exists():
        return {
            "status": "fail",
            "data": None,
            "error": f"依赖目录不存在: {_BROWSER_CDP_DIR}（browser-core 尚未从 browser-cdp 独立拆分）",
        }

    sys.path.insert(0, str(_BROWSER_CDP_DIR))
    try:
        from src.searchers.baidu_search import search_baidu  # type: ignore
    except Exception as e:  # noqa: BLE001
        return {"status": "fail", "data": None, "error": f"加载 baidu_search 失败: {e}"}

    try:
        search_results = search_baidu(query, max_results=input.get("max_results", 10))
    except Exception as e:  # noqa: BLE001
        # 沙盒环境通常没有可用的真实浏览器/CDP 连接，这里如实返回失败原因，
        # 用于验证 execute() 的失败路径与 registry 的 fail_count 记录是否正确，
        # 而不是伪造一份假数据掩盖问题。
        return {"status": "fail", "data": None, "error": f"搜索执行失败(可能无可用浏览器): {e}"}

    try:
        results = [r.to_dict() if hasattr(r, "to_dict") else r for r in search_results.results]
    except Exception:
        results = getattr(search_results, "results", [])

    return {"status": "success", "data": {"results": results}, "error": None}
