#!/usr/bin/env python3
"""
知乎搜索适配器 - 适配 zhihu_content_publish 工作流

将新版 zhihu_search.py 的功能适配为工作流期望的接口：
- 读取 keywords_file (JSON 数组)
- 对每个关键词搜索知乎问题
- 输出工作流期望的格式：questions 数组 + 统计字段
"""

import sys
import json
import time
import argparse
from pathlib import Path
from typing import List, Dict, Any

# 添加 browser-cdp skill 目录到路径
SKILL_DIR = Path(__file__).parents[2] / ".claude" / "skills" / "browser-cdp"
sys.path.insert(0, str(SKILL_DIR))

from src.searchers.zhihu_search import (
    search_zhihu_via_baidu,
    fetch_zhihu_question,
    classify_zhihu_url,
    ensure_browser,
    get_random_ua,
    PYTHON_CMD,
    run_cmd,
)
from src.core.browser_launch import ensure_browser as launch_ensure_browser


def load_keywords(keywords_file: str) -> List[str]:
    """加载关键词文件"""
    with open(keywords_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, list):
        return [str(k) for k in data if k]
    elif isinstance(data, dict) and 'search_keywords' in data:
        return [str(k) for k in data['search_keywords'] if k]
    else:
        raise ValueError(f"关键词文件格式不支持: {type(data)}")


def search_questions_for_keywords(
    keywords: List[str],
    port: int = 9336,
    min_results_per_kw: int = 30,
    max_results_per_kw: int = 60,
    max_scrolls: int = 12,
    scroll_pause: float = 3.0,
) -> Dict[str, Any]:
    """为每个关键词搜索知乎问题，返回工作流期望的格式"""
    
    # 确保浏览器运行（复用已登录的知乎实例）
    print(f"[适配器] 连接浏览器端口 {port}...")
    try:
        import urllib.request
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=3) as resp:
            tabs = json.loads(resp.read().decode())
            if not tabs:
                raise Exception("没有可用的 tab")
        print(f"[适配器] 已连接到端口 {port} 的浏览器（找到 {len(tabs)} 个 tab）")
    except Exception as e:
        print(f"[适配器] 无法连接到端口 {port} 的浏览器: {e}")
        print(f"请先运行：python launch_zhihu_logged_in.py")
        sys.exit(1)
    
    # 找到知乎 tab
    zhihu_tab = None
    for tab in tabs:
        if 'zhihu.com' in (tab.get('url') or ''):
            zhihu_tab = tab
            break
    
    if not zhihu_tab:
        # 如果没有知乎 tab，用第一个 tab 导航到知乎
        zhihu_tab = tabs[0]
        print(f"[适配器] 未找到知乎 tab，使用第一个 tab 导航到知乎")
    
    tab_id = zhihu_tab['id']
    
    all_questions = []
    seen_urls = set()
    total_keywords = len(keywords)
    
    for kw_idx, keyword in enumerate(keywords, 1):
        print(f"\n[适配器] [{kw_idx}/{total_keywords}] 搜索关键词: {keyword}")
        
        # 使用百度搜索 site:zhihu.com
        # 这里我们需要修改 search_zhihu_via_baidu 以支持滚动加载更多结果
        # 或者直接使用知乎原生搜索（类似 zhihu_search_with_login.py 的方式）
        
        # 为了获得更多结果，我们使用知乎原生搜索 + 滚动
        questions = search_zhihu_native(
            keyword, port, tab_id,
            min_results=min_results_per_kw,
            max_results=max_results_per_kw,
            max_scrolls=max_scrolls,
            scroll_pause=scroll_pause
        )
        
        for q in questions:
            url = q.get('question_url', '')
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_questions.append({
                    'id': f"q{len(all_questions) + 1}",
                    'title': q.get('question_title', ''),
                    'url': url,
                    'snippet': q.get('snippet', ''),
                    'matched_keywords': [keyword],
                    'search_page_meta': q.get('search_page_meta', {})
                })
        
        time.sleep(2)  # 避免触发风控
    
    # 去重后统计
    unique_questions = []
    seen = set()
    for q in all_questions:
        if q['url'] not in seen:
            seen.add(q['url'])
            unique_questions.append(q)
    
    # 重新编号
    for i, q in enumerate(unique_questions, 1):
        q['id'] = f"q{i}"
    
    return {
        'questions': unique_questions,
        'total_keywords_searched': total_keywords,
        'total_unique_questions': len(unique_questions)
    }


def search_zhihu_native(
    query: str,
    port: int,
    tab_id: str,
    min_results: int = 30,
    max_results: int = 60,
    max_scrolls: int = 12,
    scroll_pause: float = 3.0,
) -> List[Dict]:
    """使用知乎原生搜索（类似 zhihu_search_with_login.py）"""
    import urllib.parse
    import websocket
    
    encoded_query = urllib.parse.quote(query)
    search_url = f"https://www.zhihu.com/search?type=question&q={encoded_query}"
    
    print(f"  [原生搜索] {query}")
    print(f"  URL: {search_url}")
    
    ws_url = f"ws://127.0.0.1:{port}/devtools/page/{tab_id}"
    origin = f"http://127.0.0.1:{port}"
    ws = websocket.create_connection(ws_url, origin=origin)
    
    msg_id = 0
    def next_id():
        nonlocal msg_id
        msg_id += 1
        return msg_id
    
    def ws_send(method, params):
        ws.send(json.dumps({"id": next_id(), "method": method, "params": params}))
    
    def ws_wait(msg_id, tries=20, interval=0.5):
        for _ in range(tries):
            raw = ws.recv()
            data = json.loads(raw)
            if data.get('id') == msg_id:
                return data
            time.sleep(interval)
        return None
    
    # 导航到搜索页
    nav_id = next_id()
    ws_send("Page.navigate", {"url": search_url})
    ws_wait(nav_id)
    time.sleep(3)
    
    # 提取 JS
    EXTRACT_JS = r"""
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
            // 尝试获取父容器的元信息
            let meta = {};
            const container = link.closest('.SearchResult-Card, .List-item, [class*=Item]');
            if (container) {
                const answerEl = container.querySelector('[class*=AnswerCount], [class*=answer]');
                const followEl = container.querySelector('[class*=FollowCount], [class*=follow]');
                if (answerEl) meta.answer_count = answerEl.textContent.trim();
                if (followEl) meta.follow_count = followEl.textContent.trim();
            }
            result.push({
                text: text.substring(0, 100),
                href: href,
                meta: meta
            });
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
    return JSON.stringify({
        items: unique,
        scrollY: window.scrollY,
        scrollHeight: document.body.scrollHeight
    });
})()
"""
    
    SCROLL_JS = r"""
(() => {
    window.scrollTo(0, document.body.scrollHeight);
    return document.body.scrollHeight;
})()
"""
    
    def eval_js(js_code):
        eval_id = next_id()
        ws_send("Runtime.evaluate", {
            "expression": js_code,
            "awaitPromise": True,
        })
        data = ws_wait(eval_id)
        if data and 'result' in data and 'result' in data['result']:
            return data['result']['result'].get('value')
        return None
    
    collected = {}
    
    def merge_items(raw_value):
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
    
    # 首屏提取
    raw_value = eval_js(EXTRACT_JS)
    new_count = merge_items(raw_value)
    print(f"  · 首屏提取到 {len(collected)} 个问题（新增 {new_count}）")
    
    # 滚动加载更多
    scroll_round = 0
    while len(collected) < min_results and scroll_round < max_scrolls:
        scroll_round += 1
        eval_js(SCROLL_JS)
        time.sleep(scroll_pause)
        raw_value = eval_js(EXTRACT_JS)
        new_count = merge_items(raw_value)
        print(f"  · 第 {scroll_round} 次滚动后共 {len(collected)} 个问题（新增 {new_count}）")
        if new_count == 0:
            print(f"  · 滚动未产生新结果，提前停止")
            break
    
    ws.close()
    
    if not collected:
        print(f"  ✗ 未找到问题")
        return []
    
    questions = list(collected.values())[:max_results]
    if len(questions) < min_results:
        print(f"  ! 仅拿到 {len(questions)} 条（少于目标 {min_results} 条）")
    else:
        print(f"  ✓ 找到 {len(questions)} 个问题")
    
    # 转换为工作流期望的格式
    result = []
    for q in questions:
        result.append({
            'question_title': q.get('text', ''),
            'question_url': q.get('href', ''),
            'snippet': '',  # 搜索结果页通常没有摘要
            'search_page_meta': q.get('meta', {})
        })
    return result


def main():
    parser = argparse.ArgumentParser(description="知乎搜索适配器 - 为 zhihu_content_publish 工作流服务")
    parser.add_argument("--keywords-file", required=True, help="关键词 JSON 文件路径")
    parser.add_argument("--port", type=int, default=9336, help="CDP 调试端口（默认 9336，已登录知乎实例）")
    parser.add_argument("--min-results", type=int, default=30, help="每关键词最少结果数")
    parser.add_argument("--max-results", type=int, default=60, help="每关键词最多结果数")
    parser.add_argument("--max-scrolls", type=int, default=12, help="最大滚动次数")
    parser.add_argument("--scroll-pause", type=float, default=3.0, help="滚动间隔秒数")
    parser.add_argument("--output", required=True, help="输出文件路径（工作流期望的 result_file）")
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("知乎搜索适配器启动")
    print(f"关键词文件: {args.keywords_file}")
    print(f"输出文件: {args.output}")
    print(f"端口: {args.port}")
    print("=" * 60)
    
    # 加载关键词
    keywords = load_keywords(args.keywords_file)
    print(f"\n加载到 {len(keywords)} 个关键词: {keywords}")
    
    # 执行搜索
    result = search_questions_for_keywords(
        keywords=keywords,
        port=args.port,
        min_results_per_kw=args.min_results,
        max_results_per_kw=args.max_results,
        max_scrolls=args.max_scrolls,
        scroll_pause=args.scroll_pause,
    )
    
    # 写入输出文件
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"\n{'='*60}")
    print(f"搜索完成！")
    print(f"  关键词数: {result['total_keywords_searched']}")
    print(f"  去重后问题数: {result['total_unique_questions']}")
    print(f"  结果已写入: {output_path}")
    print(f"{'='*60}")
    
    # 自检
    with open(output_path, 'r', encoding='utf-8') as f:
        check = json.load(f)
    assert 'questions' in check, "输出缺少 questions 字段"
    assert isinstance(check['questions'], list), "questions 必须是数组"
    print(f"[自检] 输出文件合法，包含 {len(check['questions'])} 个问题")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
