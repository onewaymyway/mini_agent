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
"""

import sys
import argparse
import json
import time
from pathlib import Path

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from browser_console import cmd_eval, get_session
from browser_nav import cmd_goto as goto_url

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


def search_zhihu(query: str, port: int = DEFAULT_PORT, max_results: int = 8) -> list:
    """使用已登录的浏览器搜索知乎
    
    Args:
        query: 搜索关键词
        port: CDP 调试端口
        max_results: 最大结果数
        
    Returns:
        搜索结果列表
    """
    import urllib.parse
    import urllib.request
    encoded_query = urllib.parse.quote(query)
    search_url = f"https://www.zhihu.com/search?type=question&q={encoded_query}"
    
    print(f"\n  搜索：{query}")
    print(f"  URL: {search_url}")
    
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
        import time
        
        origin = f"http://127.0.0.1:{port}"
        ws = websocket.create_connection(ws_url, origin=origin)
        
        # 发送 Page.navigate 命令
        navigate_id = 1
        ws.send(json.dumps({
            "id": navigate_id,
            "method": "Page.navigate",
            "params": {"url": search_url}
        }))
        
        # 等待导航完成
        for _ in range(20):
            result = ws.recv()
            data = json.loads(result)
            if data.get('id') == navigate_id:
                break
            time.sleep(0.5)
        
        time.sleep(3)  # 等待内容加载
        
        # 执行 JS 提取问题
        eval_id = 2
        js_code = """
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
            
            return JSON.stringify(unique.slice(0, 15));
        })()
        """
        
        ws.send(json.dumps({
            "id": eval_id,
            "method": "Runtime.evaluate",
            "params": {
                "expression": js_code,
                "awaitPromise": True
            }
        }))
        
        # 等待结果
        result_json = None
        for _ in range(20):
            result = ws.recv()
            data = json.loads(result)
            if data.get('id') == eval_id:
                result_json = data
                break
            time.sleep(0.5)
        
        ws.close()
        
        if result_json and 'result' in result_json and 'result' in result_json['result']:
            result_str = result_json['result']['result'].get('value', '')
            if result_str:
                questions = json.loads(result_str)
                print(f"  ✓ 找到 {len(questions)} 个问题")
                return questions
        
        print("  ✗ 未找到问题")
        return []
        
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
    parser.add_argument("--max-results", type=int, default=8, help="每个关键词最大结果数")
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
            
            questions = search_zhihu(query, port=args.port, max_results=args.max_results)
            
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
        questions = search_zhihu(args.query, port=args.port, max_results=args.max_results)
        
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
    output_dir = Path("search_results")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    output_file = output_dir / args.output
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    
    print(f"\n" + "="*80)
    print(f"共抓取 {len(all_results)} 个真实知乎问题")
    print(f"结果已保存到：{output_file}")
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
