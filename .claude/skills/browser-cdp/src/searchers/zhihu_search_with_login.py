#!/usr/bin/env python3
"""使用已登录的浏览器搜索知乎真实问题

前提：先运行 launch_zhihu_logged_in.py 启动浏览器并登录知乎
然后运行此脚本进行真实搜索

用法:
    # 终端 1: 启动浏览器并登录
    python launch_zhihu_logged_in.py
    
    # 终端 2: 执行搜索
    python zhihu_search_with_login.py "关键词"
    python zhihu_search_with_login.py --batch  # 批量搜索所有 Agent 方向
    python zhihu_search_with_login.py --keywords-file "关键词文件"
"""

import sys
import argparse
import json
import time
from pathlib import Path

# 添加 skill 根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.core.browser_console import cmd_eval, get_session
from src.core.browser_nav import cmd_goto as goto_url

DEFAULT_PORT = 9336

# 15 个 Agent 方向的核心搜索关键词
SEARCH_QUERIES = [
    ("影视推荐工具", "agent_topic_001", "个性化影视内容发现与决策 Agent"),
    ("如何选电影", "agent_topic_001", "个性化影视内容发现与决策 Agent"),
    ("追剧进度管理", "agent_topic_002", "追剧进度管理与剧集深度解读 Agent"),
    ("控制刷短视频时间", "agent_topic_003", "短视频/直播内容智能策展与信息饮食管理 Agent"),
    ("游戏攻略工具", "agent_topic_004", "游戏攻略自动生成与实时辅助 Agent"),
    ("游戏陪玩平台", "agent_topic_005", "游戏陪玩/陪练/代练智能 Agent"),
    ("游戏账号管理", "agent_topic_006", "游戏账号资产管理与交易辅助 Agent"),
    ("社交媒体管理工具", "agent_topic_007", "社交媒体内容智能策展与信息饮食管理 Agent"),
    ("社群运营方法", "agent_topic_008", "兴趣圈层深度运营与社群裂变 Agent"),
    ("新番追踪工具", "agent_topic_009", "二次元/ACG 内容多源聚合与个性化推送 Agent"),
    ("比价工具", "agent_topic_010", "全网比价与智能购物决策 Agent"),
    ("探店 APP 推荐", "agent_topic_011", "本地生活探店/团购/预约全流程 Agent"),
    ("旅行规划工具", "agent_topic_012", "旅行规划预订与行程执行 Agent"),
    ("碎片化学习", "agent_topic_013", "碎片化学习路径规划与知识内化 Agent"),
    ("自学技能反馈", "agent_topic_014", "技能练习陪伴与实时反馈 Agent"),
    ("如何养成习惯", "agent_topic_015", "习惯养成闭环与行为设计 Agent"),
]


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


def search_zhihu(
    query: str,
    port: int = DEFAULT_PORT,
    min_results: int = 30,
    max_results: int = 60,
    max_scrolls: int = 12,
    scroll_pause: float = 1.5,
) -> list:
    """使用已登录的浏览器搜索知乎，必要时向下滚动加载更多结果

    默认的知乎搜索结果页只会渲染首屏那一小批问题，很多场景下达不到
    "至少要拿到 N 条候选" 的要求。这里的策略是：

    1. 先加载搜索页、提取一次首屏结果；
    2. 如果已提取到的去重结果数 < ``min_results``，且还没到 ``max_scrolls``
       次滚动上限，就滚动到页面底部、等待新内容加载，再提取一次；
    3. 每次滚动后跟上一次提取结果比较——如果去重后的问题总数没有增加
       （说明知乎没有再返回新内容，滚不出更多结果了），提前停止，不再
       浪费滚动次数；
    4. 达到 ``min_results``、达到 ``max_scrolls`` 上限、或连续滚动没有新
       增结果，三者任一满足即停止，最终最多保留 ``max_results`` 条。

    Args:
        query: 搜索关键词
        port: CDP 调试端口
        min_results: 至少要拿到的结果数（默认 30），不满足会持续滚动加载
        max_results: 最终保留的结果数上限，避免无限增长
        max_scrolls: 最多滚动次数，防止死循环/过度请求触发风控
        scroll_pause: 每次滚动后等待新内容加载的秒数

    Returns:
        搜索结果列表（按去重后出现顺序），每项 {"text": ..., "href": ...}
    """
    import urllib.parse
    import urllib.request
    encoded_query = urllib.parse.quote(query)
    search_url = f"https://www.zhihu.com/search?type=question&q={encoded_query}"

    print(f"\n  搜索：{query}")
    print(f"  URL: {search_url}")
    print(f"  目标：至少 {min_results} 条，最多 {max_results} 条，最多滚动 {max_scrolls} 次")

    try:
        # 获取 tab 列表
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=3) as resp:
            tabs = json.loads(resp.read().decode())

        # 找到第一个知乎 tab
        zhihu_tab = None
        for tab in tabs:
            if 'zhihu.com' in (tab.get('url') or ''):
                zhihu_tab = tab
                break

        if not zhihu_tab:
            print(f"  ✗ 未找到知乎 tab")
            return []

        ws_url = zhihu_tab.get('webSocketDebuggerUrl')
        if not ws_url:
            print(f"  ✗ 未找到 WebSocket 地址")
            return []

        # 使用 websocket 直接连接（需要设置 Origin 头）
        import websocket

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
                # 滚动没有带来新问题：要么到底了，要么知乎没有更多结果可加载
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


def main():
    parser = argparse.ArgumentParser(description="使用已登录的浏览器搜索知乎真实问题")
    parser.add_argument("query", nargs="?", help="搜索关键词")
    parser.add_argument("--batch", action="store_true", help="批量搜索所有 Agent 方向")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"调试端口（默认 {DEFAULT_PORT}）")
    parser.add_argument("--min-results", type=int, default=30, help="每个关键词至少要拿到的结果数，不满足会自动向下滚动加载（默认 30）")
    parser.add_argument("--max-results", type=int, default=60, help="每个关键词最终保留的结果数上限（默认 60）")
    parser.add_argument("--max-scrolls", type=int, default=12, help="每个关键词最多滚动加载的次数，防止死循环/过度触发风控（默认 12）")
    parser.add_argument("--scroll-pause", type=float, default=3, help="每次滚动后等待新内容加载的秒数（默认 3）")
    parser.add_argument("--output", default="zhihu_real_questions.json", help="输出文件")
    parser.add_argument("--keywords-file", help="包含自定义关键词的 JSON 文件路径（数组格式）")
    
    args = parser.parse_args()
    
    print("="*80)
    print("知乎真实问题搜索（使用已登录的浏览器）")
    print(f"调试端口：{args.port}")
    print("="*80)
    
    # 检查浏览器是否运行（使用备用方法，避免 CDP 403 错误）
    try:
        import urllib.request
        with urllib.request.urlopen(f'http://127.0.0.1:{args.port}/json/list', timeout=3) as resp:
            tabs = json.loads(resp.read().decode())
            if tabs:
                print(f"\n[ok] 已连接到端口 {args.port} 的浏览器（找到 {len(tabs)} 个 tab）")
            else:
                raise Exception("没有可用的 tab")
    except Exception as e:
        print(f"\n[error] 无法连接到端口 {args.port} 的浏览器")
        print(f"请先运行：python launch_zhihu_logged_in.py")
        print(f"错误详情：{e}")
        sys.exit(1)
    
    all_results = []
    
    # 确定要搜索的关键词列表
    search_queries = []
    if args.keywords_file:
        # 从文件读取自定义关键词
        with open(args.keywords_file, 'r', encoding='utf-8') as f:
            custom_keywords = json.load(f)
        for i, kw in enumerate(custom_keywords):
            search_queries.append((kw, f"doc_kw_{i}", f"文档关键词: {kw}"))
        print(f"\n使用自定义关键词文件: {args.keywords_file} (共 {len(custom_keywords)} 个关键词)")
    elif args.batch:
        # 使用硬编码的 SEARCH_QUERIES
        search_queries = SEARCH_QUERIES
        print(f"\n开始批量搜索 {len(SEARCH_QUERIES)} 个关键词...\n")
    
    if search_queries:
        for i, (query, content_id, content_title) in enumerate(search_queries, 1):
            print(f"[{i}/{len(search_queries)}] ", end="")
            
            questions = search_zhihu(
                query,
                port=args.port,
                min_results=args.min_results,
                max_results=args.max_results,
                max_scrolls=args.max_scrolls,
                scroll_pause=args.scroll_pause,
            )
            
            for q in questions:
                all_results.append({
                    "content_id": content_id,
                    "content_title": content_title,
                    "query": query,
                    "question_title": q.get("text", ""),
                    "question_url": q.get("href", ""),
                })
            
            time.sleep(2)  # 避免触发风控
        
        print(f"\n\n批量搜索完成！")
        
    elif args.query:
        # 单次搜索
        questions = search_zhihu(
            args.query,
            port=args.port,
            min_results=args.min_results,
            max_results=args.max_results,
            max_scrolls=args.max_scrolls,
            scroll_pause=args.scroll_pause,
        )
        
        for q in questions:
            all_results.append({
                "query": args.query,
                "question_title": q.get("text", ""),
                "question_url": q.get("href", ""),
            })
    else:
        parser.print_help()
        sys.exit(0)
    
    # 保存结果
    # 转换为工作流期望的输出格式
    questions = []
    seen_urls = set()
    for i, r in enumerate(all_results, 1):
        url = r.get("question_url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            questions.append({
                "id": f"q{i}",
                "title": r.get("question_title", ""),
                "url": url,
                "snippet": "",  # 搜索结果页没有摘要，留空
                "matched_keywords": [r.get("query", "")],
                "search_page_meta": {}
            })
    
    output_data = {
        "questions": questions,
        "total_keywords_searched": len(search_queries) if search_queries else (1 if args.query else 0),
        "total_unique_questions": len(questions)
    }
    
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    print(f"\n" + "="*80)
    print(f"共抓取 {len(all_results)} 个真实知乎问题，去重后 {len(questions)} 个")
    print(f"结果已保存到：{output_path}")
    print("="*80)
    
    # 打印前 20 个结果
    if all_results:
        print("\n前 20 个结果:")
        for i, r in enumerate(all_results[:20], 1):
            title = r['question_title'][:50]
            if 'content_title' in r:
                print(f"{i:2d}. [{r['content_title'][:20]}] {title}...")
            else:
                print(f"{i:2d}. {title}...")
            print(f"    {r['question_url']}")


if __name__ == "__main__":
    main()
