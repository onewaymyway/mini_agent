"""
steps/02_search_zhihu.py — python_step：用已登录的知乎浏览器实例（固定 CDP
端口 9336，由 `launch_zhihu_logged_in.py` 提前启动），对 analyze_doc 产出的
每个关键词做知乎原生搜索，滚动加载更多结果，输出去重后的问题列表。

[browser-cdp 依赖清理] 此前这一步是 `type: skill_agent, skill_name:
browser-cdp`：挂载整个 browser-cdp skill，让 LLM 子agent自己决定怎么调用
`.claude/skills/browser-cdp/src/searchers/zhihu_search_with_login.py`（见
`prompts/02_search_zhihu.md` 的历史版本）。这个脚本本身其实只用了
`requests`/`urllib`/`websocket-client` + 标准库（脚本顶部两行
`from src.core.browser_console import ...` 是从未被调用过的死 import），
不需要 browser-cdp 任何其它模块——所以这里直接原样把它的搜索逻辑改写成一个
确定性 python_step，不再需要挂载任何 skill、也不需要走"LLM子agent自己判断
要不要补搜"的多轮对话（几十个关键词下来很慢，`enrich_questions` 步骤当年
从 skill_agent 改 python_step 就是同一个理由，见 workflow.yaml 里的旧注释）。
CDP 收发复用同目录下的 `_cdp_client.py`（本 workflow 私有，不依赖任何
`.claude/skills/*` 目录）。

前置条件：
  - 先运行 `steps/launch_zhihu_logged_in.py` 启动一个已登录知乎的浏览器
    实例（固定调试端口 9336），本步骤只连接、不负责登录。
  - Python 环境需要 `requests`/`websocket-client`（`pip install requests
    websocket-client`）。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _cdp_client import (  # noqa: E402
    list_tabs,
    connect_tab,
    CDPError,
    CDPPortNotListeningError,
    CDPNoTabsError,
)

DEFAULT_PORT = 9336
DEFAULT_MIN_RESULTS = 30
DEFAULT_MAX_RESULTS = 60
DEFAULT_MAX_SCROLLS = 12
DEFAULT_SCROLL_PAUSE = 3.0
BETWEEN_KEYWORD_PAUSE = 2.0  # 关键词之间的等待，避免过快触发风控

_EXTRACT_JS = r"""
(() => {
    const allLinks = document.querySelectorAll('a');
    const result = [];
    for (let link of allLinks) {
        const href = link.href;
        const text = link.textContent.trim();
        if (href.includes('zhihu.com/question/') &&
            text.length > 5 &&
            text.length < 150 &&
            !text.includes('登录') &&
            !text.includes('注册') &&
            !text.includes('关注')) {
            let meta = {};
            const container = link.closest('.SearchResult-Card, .List-item, [class*=Item]');
            if (container) {
                const answerEl = container.querySelector('[class*=AnswerCount], [class*=answer]');
                const followEl = container.querySelector('[class*=FollowCount], [class*=follow]');
                if (answerEl) meta.answer_count = answerEl.textContent.trim();
                if (followEl) meta.follow_count = followEl.textContent.trim();
            }
            result.push({text: text.substring(0, 100), href: href, meta: meta});
        }
    }
    const seen = new Set();
    const unique = [];
    for (let item of result) {
        if (!seen.has(item.href)) {
            seen.add(item.href);
            unique.push(item);
        }
    }
    return JSON.stringify({items: unique});
})()
"""

_SCROLL_JS = r"""
(() => { window.scrollTo(0, document.body.scrollHeight); return document.body.scrollHeight; })()
"""


_LAUNCH_HINT = (
    "请先运行 steps/launch_zhihu_logged_in.py 启动已登录知乎的浏览器实例"
    "（固定调试端口 9336），完成登录后再重新执行/续跑本 workflow"
    "（resume_workflow_run）。"
)


def _get_zhihu_session(port: int):
    """找一个已经打开着 zhihu.com 的 tab 并连接，供本轮所有关键词复用同一个
    tab 做 goto，不为每个关键词都新开一个 tab。

    这里刻意把"知乎场景下该怎么办"（remediation 文案、error_code 前缀）
    留在本文件补充，而不是让通用的 `_cdp_client.py` 知道"知乎"这件事——
    同一个 CDP 客户端以后完全可能被别的网站/workflow 复用。
    """
    try:
        tabs = list_tabs(port=port)
    except CDPPortNotListeningError as e:
        # 端口都没监听：浏览器实例大概率根本没启动，这是最常见的一类情况，
        # 也是最容易被 requests.exceptions.ConnectionError/WinError 10061
        # 这类底层网络异常淹没、导致读者（人类或 agent）猜错根因的情况。
        e.error_code = f"ZHIHU_SEARCH_{e.error_code}"  # -> ZHIHU_SEARCH_CDP_PORT_NOT_LISTENING
        e.remediation = f"CDP 端口 {port} 未监听，知乎浏览器实例大概率没有启动。{_LAUNCH_HINT}"
        raise

    if not tabs:
        err = CDPNoTabsError(host="127.0.0.1", port=port)
        err.error_code = f"ZHIHU_SEARCH_{err.error_code}"  # -> ZHIHU_SEARCH_CDP_NO_TABS
        err.remediation = (
            f"CDP 端口 {port} 已监听，但没有任何打开的 tab（浏览器可能被手动关闭）。{_LAUNCH_HINT}"
        )
        raise err

    target = next((t for t in tabs if "zhihu.com" in (t.get("url") or "")), None)
    if target is None:
        # 端口通、有 tab，但没有一个是知乎——原来这里是 `... or tabs[0]`，
        # 会悄悄拿一个不相关的 tab 继续跑，不会立刻报错，而是在后面
        # goto/抓取阶段产出一堆莫名其妙的空结果，比直接报错更难排查。
        err = RuntimeError(
            f"CDP 端口 {port} 有 {len(tabs)} 个 tab，但没有一个是 zhihu.com"
            f"（当前 tab: {[t.get('url') for t in tabs]}）。"
            f"登录会话可能已失效，或该浏览器实例被导航去了别的页面。"
        )
        err.error_code = "ZHIHU_SEARCH_NO_ZHIHU_TAB"
        err.remediation = (
            "请在 launch_zhihu_logged_in.py 启动的浏览器实例中手动打开 zhihu.com "
            "并确认登录状态，再重新执行/续跑本 workflow。"
        )
        raise err

    session = connect_tab(target, port=port)
    for domain in ("Page", "Runtime"):
        try:
            session.send(f"{domain}.enable")
        except Exception:
            pass
    return session


def _search_one_keyword(
    session,
    keyword: str,
    *,
    min_results: int,
    max_results: int,
    max_scrolls: int,
    scroll_pause: float,
) -> list[dict]:
    encoded = urllib.parse.quote(keyword)
    search_url = f"https://www.zhihu.com/search?type=question&q={encoded}"
    print(f"  [搜索] {keyword} -> {search_url}")

    session.send("Page.navigate", {"url": search_url})
    try:
        session.wait_event("Page.loadEventFired", timeout=15.0)
    except CDPError:
        pass  # 知乎是 SPA，有时等不到标准 load 事件，退化成下面的固定等待
    time.sleep(3.0)

    collected: dict[str, dict] = {}

    def merge(raw_value):
        if not raw_value:
            return 0
        try:
            payload = json.loads(raw_value)
        except (TypeError, ValueError):
            return 0
        items = payload.get("items", []) if isinstance(payload, dict) else []
        before = len(collected)
        for item in items:
            href = item.get("href")
            if href and href not in collected:
                collected[href] = item
        return len(collected) - before

    new_count = merge(session.eval_js(_EXTRACT_JS))
    print(f"    首屏提取到 {len(collected)} 个问题（新增 {new_count}）")

    scroll_round = 0
    while len(collected) < min_results and scroll_round < max_scrolls:
        scroll_round += 1
        session.eval_js(_SCROLL_JS)
        time.sleep(scroll_pause)
        new_count = merge(session.eval_js(_EXTRACT_JS))
        print(f"    第 {scroll_round} 次滚动后共 {len(collected)} 个问题（新增 {new_count}）")
        if new_count == 0:
            print("    滚动未产生新结果，提前停止")
            break

    if not collected:
        print("    未找到问题")
        return []

    return list(collected.values())[:max_results]


def _load_keywords(keywords_file: str) -> list[str]:
    data = json.loads(Path(keywords_file).read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [str(k) for k in data if k]
    if isinstance(data, dict) and "search_keywords" in data:
        return [str(k) for k in data["search_keywords"] if k]
    raise ValueError(f"关键词文件格式不支持: {type(data)}")


def run(ctx) -> dict:
    analyzed = ctx.input_json("analyze_doc", {})
    keywords_file = analyzed.get("keywords_file")
    if not keywords_file:
        raise ValueError("analyze_doc 的产出里没有 keywords_file 字段，无法确定要搜索哪些关键词")
    keywords = _load_keywords(keywords_file)
    if not keywords:
        return {"questions": [], "total_keywords_searched": 0, "total_unique_questions": 0,
                 "note": "关键词文件为空"}

    port = int(ctx.params.get("cdp_port", DEFAULT_PORT))
    min_results = int(ctx.params.get("min_results", DEFAULT_MIN_RESULTS))
    max_results = int(ctx.params.get("max_results", DEFAULT_MAX_RESULTS))
    max_scrolls = int(ctx.params.get("max_scrolls", DEFAULT_MAX_SCROLLS))
    scroll_pause = float(ctx.params.get("scroll_pause", DEFAULT_SCROLL_PAUSE))

    print(f"[search_zhihu] 加载到 {len(keywords)} 个关键词: {keywords}")
    session = _get_zhihu_session(port)

    all_questions: list[dict] = []
    seen_urls: set[str] = set()
    try:
        for idx, keyword in enumerate(keywords, 1):
            print(f"[search_zhihu] [{idx}/{len(keywords)}] {keyword}")
            items = _search_one_keyword(
                session, keyword,
                min_results=min_results, max_results=max_results,
                max_scrolls=max_scrolls, scroll_pause=scroll_pause,
            )
            for item in items:
                url = item.get("href", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_questions.append({
                        "id": f"q{len(all_questions) + 1}",
                        "title": item.get("text", ""),
                        "url": url,
                        "snippet": "",
                        "matched_keywords": [keyword],
                        "search_page_meta": item.get("meta", {}),
                    })
            time.sleep(BETWEEN_KEYWORD_PAUSE)
    finally:
        session.close()

    print(f"[search_zhihu] 完成，去重后共 {len(all_questions)} 个问题")
    return {
        "questions": all_questions,
        "total_keywords_searched": len(keywords),
        "total_unique_questions": len(all_questions),
    }
