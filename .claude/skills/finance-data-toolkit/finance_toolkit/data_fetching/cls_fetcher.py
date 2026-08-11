# -*- coding: utf-8 -*-
"""
财联社快讯数据抓取器
从 cls.cn 抓取实时财经快讯
"""

import json
import re
import sys
import argparse
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict


@dataclass
class NewsItem:
    """新闻条目"""
    id: str
    title: str
    content: str
    summary: str
    source: str
    timestamp: str
    tags: List[str] = None
    url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def parse_cls_news(text: str) -> List[Dict[str, Any]]:
    """从财联社页面提取新闻列表"""
    news_list = []

    # 匹配新闻条目格式
    patterns = [
        r'<div[^>]*class=["\']l-Item[^>]*>[\s\S]*?<a[^>]*>([^<]+)</a>[\s\S]*?<span[^>]*>([^<]+)</span>',
        r'<li[^>]*>[\s\S]*?<span[^>]*>(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2})</span>[\s\S]*?<a[^>]*>([^<]+)</a>',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            if len(match) >= 2:
                news_list.append({
                    'title': match[1].strip() if len(match) > 1 else '',
                    'time': match[0].strip() if match else '',
                })

    return news_list[:50]  # 限制数量


def fetch_latest_news(count: int = 20) -> List[Dict[str, Any]]:
    """抓取财联社最新快讯"""
    url = 'https://www.cls.cn/telegraph'
    print(f"[CLS] 抓取财联社快讯: {url}")

    try:
        skill_dir = Path(__file__).parent.parent.parent.parent
        browser_dir = skill_dir / 'browser-cdp' / 'scripts'

        launch_cmd = [sys.executable, str(browser_dir / 'browser_launch.py'),
                      '--dedicated', '--name', 'cls_news', '--start-url', url]

        result = subprocess.run(launch_cmd, capture_output=True, text=True, encoding='utf-8', timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"浏览器启动失败: {result.stderr}")

        port_match = re.search(r'->\s*[\d.]+:(\d+)', result.stdout)
        if not port_match:
            raise RuntimeError(f"无法解析端口: {result.stdout}")
        port = port_match.group(1)

        import time
        time.sleep(3)

        extract_cmd = [sys.executable, str(browser_dir / 'browser_extract.py'),
                       '--port', port, '--mode', 'text']
        result = subprocess.run(extract_cmd, capture_output=True, text=True, encoding='utf-8', timeout=30)
        text = result.stdout

        news_list = parse_cls_news(text)
        print(f"[CLS] 抓取成功: {len(news_list)} 条快讯")
        return news_list

    except Exception as e:
        print(f"[CLS] 抓取失败: {e}")
        return []


def main():
    parser = argparse.ArgumentParser(description='财联社快讯抓取工具')
    parser.add_argument('--count', type=int, default=20, help='抓取数量')
    parser.add_argument('-o', '--output', help='输出文件')
    args = parser.parse_args()

    news_list = fetch_latest_news(args.count)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump({'timestamp': datetime.now().isoformat(), 'news': news_list},
                      f, ensure_ascii=False, indent=2)
        print(f"数据已保存: {args.output}")
    else:
        print(json.dumps({'timestamp': datetime.now().isoformat(), 'news': news_list},
                         ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
