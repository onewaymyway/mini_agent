#!/usr/bin/env python3
"""一键获取知乎真实问题

自动完成以下步骤：
1. 启动带登录态的浏览器（如果未运行）
2. 等待用户登录知乎（如果未登录）
3. 自动执行批量搜索
4. 保存结果到文件

用法:
    python run_zhihu_search_auto.py
    
首次运行需要手动登录知乎，后续运行会自动使用已登录的浏览器。
"""

import subprocess
import sys
import time
import json
from pathlib import Path

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).parent))

PORT = 9336

def main():
    print("="*80)
    print("一键获取知乎真实问题")
    print("="*80)
    print()
    
    # 第一步：启动浏览器并等待登录
    print("[步骤 1/3] 启动浏览器并等待知乎登录...")
    print()
    
    result = subprocess.run(
        [sys.executable, "launch_zhihu_logged_in.py", "--auto-continue"],
        cwd=Path(__file__).parent
    )
    
    if result.returncode != 0:
        print("[error] 浏览器启动或登录失败")
        sys.exit(1)
    
    print()
    print("[ok] 知乎已登录！")
    print()
    
    # 第二步：执行批量搜索
    print("[步骤 2/3] 执行批量搜索...")
    print()
    
    result = subprocess.run(
        [sys.executable, "zhihu_search_with_login.py", "--batch", "--port", str(PORT)],
        cwd=Path(__file__).parent
    )
    
    if result.returncode != 0:
        print("[error] 搜索失败")
        sys.exit(1)
    
    print()
    
    # 第三步：显示结果摘要
    print("[步骤 3/3] 处理搜索结果...")
    print()
    
    search_results_dir = Path(__file__).parent / "search_results"
    json_file = search_results_dir / "zhihu_real_questions.json"
    
    if json_file.exists():
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        print(f"[ok] 共获取 {len(data)} 个真实知乎问题")
        print()
        
        # 按内容主题统计
        from collections import Counter
        content_counts = Counter(item.get('content_title', 'Unknown') for item in data)
        
        print("各主题问题数量:")
        for title, count in sorted(content_counts.items(), key=lambda x: -x[1]):
            print(f"  {title}: {count} 个问题")
        
        print()
        print(f"结果已保存到：{json_file}")
        print()
        print("="*80)
        print("完成！")
        print("="*80)
        
        # 打印前 10 个问题
        print("\n前 10 个问题:")
        for i, item in enumerate(data[:10], 1):
            title = item.get('question_title', '')[:50]
            url = item.get('question_url', '')
            print(f"{i:2d}. {title}...")
            print(f"    {url}")
    else:
        print("[warn] 未找到结果文件")


if __name__ == "__main__":
    main()
