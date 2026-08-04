#!/usr/bin/env python3
"""批量搜索知乎问题 - 处理所有 75 个关键词，补全缺失的 Agent 方向

使用已登录的浏览器实例 (zhihu_search, port 9335)
读取 search_keyword_groups.json 中的 15 个 Agent × 5 个关键词
输出合并去重后的 zhihu_real_questions.json
"""

import sys
import json
import time
import argparse
from pathlib import Path
from typing import List, Dict, Set
import urllib.request
import websocket

# 添加 skill 根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.core.browser_console import cmd_eval, get_session
from src.core.browser_nav import cmd_goto as goto_url

DEFAULT_PORT = 9335  # zhihu_search 实例端口


_EXTRACT_JS = """
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
            result.push({
                text: text.substring(0, 100),
                href: href
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

_SCROLL_JS = """
(() => {
    window.scrollTo(0, document.body.scrollHeight);
    return document.body.scrollHeight;
})()
"""


def _ws_send(ws, msg_id: int, method: str, params: dict) -> None:
    ws.send(json.dumps({"id": msg_id, "method": method, "params": params}))


def _ws_wait_result(ws, msg_id: int, tries: int = 20, interval: float = 0.5):
    """阻塞等待某个 msg_id 对应的响应，跳过其它 CDP 事件消息"""
    for _ in range(tries):
        raw = ws.recv()
        data = json.loads(raw)
        if data.get('id') == msg_id:
            return data
        time.sleep(interval)
    return None


def _eval_js(ws, eval_id: int, js_code: str):
    """执行一段 JS 并返回 Runtime.evaluate 的字符串结果（value），失败返回 None"""
    _ws_send(ws, eval_id, "Runtime.evaluate", {
        "expression": js_code,
        "awaitPromise": True,
    })
    data = _ws_wait_result(ws, eval_id)
    if data and 'result' in data and 'result' in data['result']:
        return data['result']['result'].get('value')
    return None


def get_or_create_zhihu_tab(port: int):
    """获取或创建知乎 tab 的 WebSocket URL"""
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=3) as resp:
        tabs = json.loads(resp.read().decode())

    # 先找现有的知乎 tab
    for tab in tabs:
        if 'zhihu.com' in (tab.get('url') or ''):
            return tab
    
    # 没有知乎 tab，创建一个新 tab 并导航到知乎首页
    print(f"  [info] 无知乎 tab，创建新 tab...")
    req = urllib.request.Request(f'http://127.0.0.1:{port}/json/new', method='PUT')
    with urllib.request.urlopen(req, timeout=3) as resp:
        new_tab = json.loads(resp.read().decode())
    
    # 等待新 tab 就绪
    time.sleep(1)
    
    # 获取新 tab 的 websocket URL
    ws_url = new_tab.get('webSocketDebuggerUrl')
    if not ws_url:
        # 重新获取 tab 列表
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=3) as resp:
            tabs = json.loads(resp.read().decode())
        for tab in tabs:
            if tab.get('id') == new_tab.get('id'):
                return tab
    
    return new_tab


def search_zhihu(
    query: str,
    port: int = DEFAULT_PORT,
    min_results: int = 20,
    max_results: int = 50,
    max_scrolls: int = 10,
    scroll_pause: float = 2.0,
) -> List[Dict]:
    """使用已登录的浏览器搜索知乎，必要时向下滚动加载更多结果"""
    import urllib.parse
    encoded_query = urllib.parse.quote(query)
    search_url = f"https://www.zhihu.com/search?type=question&q={encoded_query}"

    print(f"\n  搜索：{query}")
    print(f"  URL: {search_url}")
    print(f"  目标：至少 {min_results} 条，最多 {max_results} 条，最多滚动 {max_scrolls} 次")

    try:
        zhihu_tab = get_or_create_zhihu_tab(port)
        if not zhihu_tab:
            print(f"  ✗ 无法获取或创建知乎 tab")
            return []

        ws_url = zhihu_tab.get('webSocketDebuggerUrl')
        if not ws_url:
            print(f"  ✗ 未找到 WebSocket 地址")
            return []

        # 使用 websocket 直接连接
        origin = f"http://127.0.0.1:{port}"
        ws = websocket.create_connection(ws_url, origin=origin)

        msg_id = 0

        def next_id() -> int:
            nonlocal msg_id
            msg_id += 1
            return msg_id

        # 发送 Page.navigate 命令
        navigate_id = next_id()
        _ws_send(ws, navigate_id, "Page.navigate", {"url": search_url})
        _ws_wait_result(ws, navigate_id)

        time.sleep(3)  # 等待首屏内容加载

        # 去重容器：href -> item，保持首次出现的顺序
        collected: dict = {}

        def merge_items(raw_value: str) -> int:
            """解析一次提取结果，合并进 collected，返回本次新增的条数"""
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
        raw_value = _eval_js(ws, next_id(), _EXTRACT_JS)
        new_count = merge_items(raw_value)
        print(f"  · 首屏提取到 {len(collected)} 个问题（新增 {new_count}）")

        # 不满足 min_results 就持续向下滚动加载
        scroll_round = 0
        while len(collected) < min_results and scroll_round < max_scrolls:
            scroll_round += 1

            _eval_js(ws, next_id(), _SCROLL_JS)
            time.sleep(scroll_pause)

            raw_value = _eval_js(ws, next_id(), _EXTRACT_JS)
            new_count = merge_items(raw_value)
            print(f"  · 第 {scroll_round} 次滚动后共 {len(collected)} 个问题（新增 {new_count}）")

            if new_count == 0:
                print(f"  · 滚动未产生新结果，提前停止（共滚动 {scroll_round} 次）")
                break

        ws.close()

        if not collected:
            print("  ✗ 未找到问题")
            return []

        questions = list(collected.values())[:max_results]
        if len(questions) < min_results:
            print(f"  ! 已达到滚动上限/无更多结果，仅拿到 {len(questions)} 条（少于目标 {min_results} 条）")
        else:
            print(f"  ✓ 找到 {len(questions)} 个问题")
        return questions

    except Exception as e:
        print(f"  ✗ 搜索失败：{e}")
        import traceback
        traceback.print_exc()
        return []


def load_existing_results(filepath: Path) -> List[Dict]:
    """加载现有的 zhihu_real_questions.json"""
    if not filepath.exists():
        return []
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    # 兼容两种格式：直接是数组，或包含 questions 字段
    if isinstance(data, dict) and 'questions' in data:
        return data['questions']
    return data


def load_keyword_groups(filepath: Path) -> List[Dict]:
    """加载 search_keyword_groups.json"""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get('keyword_groups', [])


def extract_question_id(url: str) -> str:
    """从知乎问题 URL 提取 question_id"""
    import re
    match = re.search(r'zhihu\.com/question/(\d+)', url)
    if match:
        return match.group(1)
    return url  # fallback to full URL


def main():
    parser = argparse.ArgumentParser(description="批量搜索知乎问题 - 处理所有 75 个关键词")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"调试端口（默认 {DEFAULT_PORT}）")
    parser.add_argument("--min-results", type=int, default=20, help="每个关键词至少要拿到的结果数（默认 20）")
    parser.add_argument("--max-results", type=int, default=50, help="每个关键词最终保留的结果数上限（默认 50）")
    parser.add_argument("--max-scrolls", type=int, default=10, help="每个关键词最多滚动加载的次数（默认 10）")
    parser.add_argument("--scroll-pause", type=float, default=2.0, help="每次滚动后等待新内容加载的秒数（默认 2.0）")
    parser.add_argument("--keywords-file", default="E:\\codes\\mini_claude_code\\temp\\search_keyword_groups.json", help="关键词配置文件路径")
    parser.add_argument("--existing-file", default="E:\\codes\\mini_claude_code\\search_results\\zhihu_real_questions.json", help="现有结果文件路径")
    parser.add_argument("--output", default="E:\\codes\\mini_claude_code\\search_results\\zhihu_real_questions.json", help="输出文件路径")
    parser.add_argument("--only-missing", action="store_true", help="只搜索缺失的 Agent 方向 (agent_11-agent_15)")
    parser.add_argument("--dry-run", action="store_true", help="只显示将要搜索的关键词，不实际执行")
    
    args = parser.parse_args()
    
    print("="*80)
    print("知乎真实问题批量搜索（使用已登录的浏览器 zhihu_search @ port 9335）")
    print(f"调试端口：{args.port}")
    print("="*80)
    
    # 检查浏览器是否运行
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{args.port}/json/list', timeout=3) as resp:
            tabs = json.loads(resp.read().decode())
            if tabs:
                print(f"\n[ok] 已连接到端口 {args.port} 的浏览器（找到 {len(tabs)} 个 tab）")
                # 检查是否有知乎 tab
                zhihu_tabs = [t for t in tabs if 'zhihu.com' in (t.get('url') or '')]
                if zhihu_tabs:
                    print(f"[ok] 找到 {len(zhihu_tabs)} 个知乎 tab")
                else:
                    print(f"[warn] 当前无知乎 tab，搜索时会自动打开")
            else:
                raise Exception("没有可用的 tab")
    except Exception as e:
        print(f"\n[error] 无法连接到端口 {args.port} 的浏览器")
        print(f"错误详情：{e}")
        sys.exit(1)
    
    # 加载现有结果
    existing_results = load_existing_results(Path(args.existing_file))
    print(f"\n[info] 已加载现有结果：{len(existing_results)} 条")
    
    # 统计现有的 content_id 分布
    existing_content_ids = set()
    for r in existing_results:
        cid = r.get('content_id', '')
        if cid:
            existing_content_ids.add(cid)
    print(f"[info] 现有 content_id：{sorted(existing_content_ids)}")
    
    # 加载关键词组
    keyword_groups = load_keyword_groups(Path(args.keywords_file))
    print(f"[info] 加载关键词组：{len(keyword_groups)} 个 Agent 方向")
    
    # 构建搜索任务列表
    search_tasks = []
    for group in keyword_groups:
        agent_id = group['agent_id']
        agent_title = group['agent_title']
        content_id = f"doc_kw_{int(agent_id.split('_')[1]) - 1}"  # agent_01 -> doc_kw_0
        
        # 如果指定了 --only-missing，跳过已有数据的 Agent
        if args.only_missing and content_id in existing_content_ids:
            print(f"  [skip] {agent_id} ({agent_title}) - 已有数据 (content_id: {content_id})")
            continue
        
        for kw in group['search_keywords']:
            search_tasks.append({
                'query': kw,
                'content_id': content_id,
                'content_title': agent_title,
                'agent_id': agent_id
            })
    
    print(f"\n[info] 待搜索关键词总数：{len(search_tasks)}")
    
    if args.dry_run:
        print("\n=== 将要搜索的关键词 ===")
        for i, task in enumerate(search_tasks, 1):
            print(f"{i:2d}. [{task['agent_id']}] {task['query']}")
        return
    
    if not search_tasks:
        print("[info] 无需搜索，所有 Agent 方向均已有数据")
        return
    
    # 执行批量搜索
    all_new_results = []
    
    for i, task in enumerate(search_tasks, 1):
        print(f"\n[{i}/{len(search_tasks)}] ", end="")
        
        questions = search_zhihu(
            task['query'],
            port=args.port,
            min_results=args.min_results,
            max_results=args.max_results,
            max_scrolls=args.max_scrolls,
            scroll_pause=args.scroll_pause,
        )
        
        for q in questions:
            all_new_results.append({
                "content_id": task['content_id'],
                "content_title": task['content_title'],
                "agent_id": task['agent_id'],
                "query": task['query'],
                "question_title": q.get("text", ""),
                "question_url": q.get("href", ""),
            })
        
        time.sleep(2)  # 避免触发风控
    
    print(f"\n\n批量搜索完成！新抓取 {len(all_new_results)} 个问题")
    
    # 合并去重：基于 question_url (或 question_id)
    seen_urls: Set[str] = set()
    merged_results = []
    
    # 先加入现有结果
    for r in existing_results:
        url = r.get('question_url', '')
        if url and url not in seen_urls:
            seen_urls.add(url)
            merged_results.append(r)
    
    # 再加入新结果
    for r in all_new_results:
        url = r.get('question_url', '')
        if url and url not in seen_urls:
            seen_urls.add(url)
            merged_results.append(r)
    
    print(f"去重后总计：{len(merged_results)} 个唯一问题 (原有 {len(existing_results)} + 新增 {len(all_new_results)} - 重复 {len(existing_results) + len(all_new_results) - len(merged_results)})")
    
    # 统计各 content_id 分布
    from collections import Counter
    dist = Counter(r.get('content_id', '') for r in merged_results)
    print(f"\nContent ID 分布：")
    for cid, count in sorted(dist.items()):
        print(f"  {cid}: {count} 条")
    
    # 保存结果
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(merged_results, f, ensure_ascii=False, indent=2)
    
    print(f"\n结果已保存到：{output_path}")
    print("="*80)
    
    # 打印前 20 个新结果
    if all_new_results:
        print("\n新增结果前 20 个:")
        for i, r in enumerate(all_new_results[:20], 1):
            title = r['question_title'][:60]
            print(f"{i:2d}. [{r['agent_id']}] {title}...")
            print(f"    {r['question_url']}")


if __name__ == "__main__":
    main()
