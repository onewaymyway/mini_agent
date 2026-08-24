"""
browser-site-scraper / members / zhihu / script.py

统一接口: run(input: dict) -> dict

本脚本由 generative-capability 引擎在探索成功、但探索子agent未提交
script_source 时，用 LLM 事后阅读整段探索 trace 总结生成
（source: explored, distill_source_kind: llm_synthesized）。
非人工手写，未逐字重放当次探索的工具调用序列，而是由 LLM 提炼出参数化的
等价逻辑；仍需经过与其它路径完全一致的沙箱自测 + intent_schema 校验才会
落盘。
"""

# -*- coding: utf-8 -*-
"""
知乎搜索结果抓取脚本
用于探索性抓取知乎搜索页面中的内容（问题标题、文章标题、想法摘要等）
"""

import json
import re
import time


def run(input: dict) -> dict:
    """
    抓取知乎搜索结果页面。

    Parameters
    ----------
    input : dict
        必须包含 target.url（知乎搜索页面URL）和 query（可选，提取意图描述）。
    Returns
    -------
    dict
        {"status": "success"|"fail", "data": {"results": [...]}, "error": str}
    """
    # 验证必填字段
    if "target" not in input or "url" not in input.get("target", {}):
        return {
            "status": "fail",
            "error": "缺少必填字段: target.url",
            "data": {"results": []},
        }

    url = input["target"]["url"]
    query = input.get("query", "")

    try:
        from tool_runtime import get_tool_executor
    except ImportError:
        return {
            "status": "fail",
            "error": "无法导入 tool_runtime，请检查运行环境",
            "data": {"results": []},
        }

    executor = get_tool_executor()

    # 步骤1：导航到搜索页面
    nav_result = executor("browser_navigate", {"url": url})
    if not nav_result.get("ok", False):
        return {
            "status": "fail",
            "error": f"导航失败: {nav_result}",
            "data": {"results": []},
        }

    # 步骤2：等待搜索结果渲染
    time.sleep(2)
    wait_result = executor("browser_wait_for_selector", {
        "selector": ".List-item",
        "timeout": 10,
    })
    if not wait_result.get("ok", False):
        # 尝试直接提取
        pass

    # 步骤3：提取搜索结果内容
    extract_result = executor("browser_extract_content", {
        "selectors": [
            {"selector": "a[href*='/question/']", "extraction": "text+href"},
            {"selector": "a[href*='/p/']", "extraction": "text+href"},
            {"selector": "a[href*='/pin/']", "extraction": "text+href"},
        ],
        "format": "json",
    })

    if not extract_result.get("ok", False):
        return {
            "status": "fail",
            "error": f"内容提取失败: {extract_result}",
            "data": {"results": []},
        }

    raw_results = extract_result["data"]["results"]

    # 步骤4：过滤导航栏/Header项，保留实际搜索结果
    nav_patterns = [
        r"https://www\.zhihu\.com/follow",
        r"https://www\.zhihu\.com/$",
        r"https://www\.zhihu\.com/hot",
        r"https://www\.zhihu\.com/column-square",
        r"https://www\.zhihu\.com/ring-feeds",
        r"https://www\.zhihu\.com/project-square",
        r"https://www\.zhihu\.com/fiore",
        r"https://zhida\.zhihu\.com/",
        r"https://www\.zhihu\.com/creator",
        r"https://www\.zhihu\.com/search\?q=.*type=",
    ]

    filtered = []
    for item in raw_results:
        text = item.get("text", "").strip()
        href = item.get("href", "").strip()

        # 跳过导航栏项
        if any(re.search(p, href) for p in nav_patterns):
            continue
        # 跳过纯日期文本
        if re.match(r"^发布于\d{4}-\d{2}-\d{2}", text):
            continue
        # 跳过知乎专栏/知乎用户等标签
        if "知乎专栏" in text or "知乎用户" in text:
            continue
        # 跳过空文本
        if not text:
            continue

        # 判断内容类型
        content_type = "article"
        if "/question/" in href:
            content_type = "question"
        elif "/pin/" in href:
            content_type = "idea"
        elif "/p/" in href:
            content_type = "article"

        filtered.append({
            "title": text,
            "url": href,
            "type": content_type,
            "query": query,
        })

    return {
        "status": "success",
        "data": {"results": filtered},
    }


if __name__ == "__main__":
    # 测试运行
    test_input = {
        "text": "抓取知乎搜索结果页面中关于自主进化Agent的内容",
        "target": {
            "url": "https://www.zhihu.com/search?type=content&q=自主进化Agent"
        },
        "query": "搜索结果中的问题标题、链接、回答摘要"
    }
    result = run(test_input)
    print(json.dumps(result, ensure_ascii=False, indent=2))