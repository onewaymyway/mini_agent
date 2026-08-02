"""使用已登录的浏览器抓取真实知乎问题

前提：用户已手动启动带 --remote-debugging-port=9222 的浏览器，并登录知乎
"""

import sys
import argparse
import json
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.browser_console import cmd_eval, get_session
from src.core.browser_nav import cmd_goto as goto_url

PORT = 9222  # 用户手动启动的浏览器调试端口

# 搜索关键词列表
SEARCH_QUERIES = [
    ("影视推荐工具", "agent_topic_001"),
    ("如何选电影", "agent_topic_001"),
    ("追剧进度管理", "agent_topic_002"),
    ("控制刷短视频时间", "agent_topic_003"),
    ("游戏攻略工具", "agent_topic_004"),
    ("游戏陪玩平台", "agent_topic_005"),
    ("游戏账号管理", "agent_topic_006"),
    ("社交媒体管理工具", "agent_topic_007"),
    ("社群运营方法", "agent_topic_008"),
    ("新番追踪工具", "agent_topic_009"),
    ("比价工具", "agent_topic_010"),
    ("探店 APP 推荐", "agent_topic_011"),
    ("旅行规划工具", "agent_topic_012"),
    ("碎片化学习", "agent_topic_013"),
    ("自学技能反馈", "agent_topic_014"),
    ("如何养成习惯", "agent_topic_015"),
]

def search_zhihu_logged_in(query: str, port: int = 9222) -> list:
    """使用已登录的浏览器搜索知乎"""
    import urllib.parse
    encoded_query = urllib.parse.quote(query)
    url = f"https://www.zhihu.com/search?type=question&q={encoded_query}"
    
    print(f"\n搜索：{query}")
    print(f"  访问：{url}")
    
    args = argparse.Namespace(
        port=port,
        host='127.0.0.1',
        tab_id=None,
        url_contains='zhihu.com/search',
        title_contains=None
    )
    
    try:
        session = get_session(args)
        
        # 导航到搜索页面
        goto_url(url, port=port, wait_selector='body', timeout=10)
        time.sleep(3)  # 等待内容加载
        
        # 提取问题链接
        js_code = """
        (() => {
            const allLinks = document.querySelectorAll('a');
            const result = [];
            
            for (let link of allLinks) {
                const href = link.href;
                if (href.includes('zhihu.com/question/') && link.textContent.trim().length > 5) {
                    result.push({
                        text: link.textContent.trim().substring(0, 80),
                        href: href
                    });
                }
            }
            
            // 去重
            const seen = new Set();
            const unique = [];
            for (let item of result) {
                if (!seen.has(item.href)) {
                    seen.add(item.href);
                    unique.push(item);
                }
            }
            
            return JSON.stringify(unique.slice(0, 10));
        })()
        """
        
        result = cmd_eval(session, js_code)
        session.close()
        
        if result:
            questions = json.loads(result)
            print(f"  找到 {len(questions)} 个问题")
            return questions
        else:
            print("  未找到问题")
            return []
            
    except Exception as e:
        print(f"  搜索失败：{e}")
        return []


def main():
    print("="*80)
    print("使用已登录的浏览器抓取真实知乎问题")
    print(f"浏览器调试端口：{PORT}")
    print("="*80)
    print("\n请确保：")
    print("1. 已启动带 --remote-debugging-port=9222 的浏览器")
    print("2. 已在该浏览器中登录知乎")
    print("3. 可以手动打开知乎搜索页面测试\n")
    
    input("按 Enter 继续...")
    
    all_results = []
    
    for i, (query, content_id) in enumerate(SEARCH_QUERIES, 1):
        print(f"\n[{i}/{len(SEARCH_QUERIES)}] ", end="")
        
        questions = search_zhihu_logged_in(query, port=PORT)
        
        for q in questions:
            all_results.append({
                "content_id": content_id,
                "query": query,
                "question_title": q.get("text", ""),
                "question_url": q.get("href", ""),
            })
        
        time.sleep(2)  # 避免触发风控
    
    # 保存结果
    output_dir = Path("temp/zhihu_real_results")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    output_file = output_dir / "zhihu_real_questions_logged_in.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    
    print(f"\n" + "="*80)
    print(f"完成！共抓取 {len(all_results)} 个真实知乎问题")
    print(f"结果已保存到：{output_file}")
    print("="*80)
    
    # 打印前 20 个结果
    if all_results:
        print("\n前 20 个结果:")
        for i, r in enumerate(all_results[:20], 1):
            title = r['question_title'][:50]
            print(f"{i:2d}. {title}...")
            print(f"    {r['question_url']}")


if __name__ == "__main__":
    main()
