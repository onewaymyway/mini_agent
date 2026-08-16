#!/usr/bin/env python3
"""简单连通性测试脚本"""
import sys
from pathlib import Path

# 添加路径
sys.path.insert(0, str(Path.cwd() / "src"))

import requests
from core.request_fingerprint import FingerprintManager


def main():
    manager = FingerprintManager()
    headers = manager.generate_random_headers().get_headers()
    
    print('Testing 25 common websites...\n')
    
    sites = [
        ('人民网', 'https://www.people.com.cn'),
        ('新华网', 'https://www.xinhuanet.com'),
        ('腾讯新闻', 'https://news.qq.com'),
        ('百度', 'https://www.baidu.com'),
        ('微博', 'https://weibo.com'),
        ('知乎', 'https://www.zhihu.com'),
        ('豆瓣', 'https://www.douban.com'),
        ('CSDN', 'https://www.csdn.net'),
        ('掘金', 'https://juejin.cn'),
        ('当当网', 'https://www.dangdang.com'),
        ('苏宁易购', 'https://www.suning.com'),
        ('京东', 'https://www.jd.com'),
        ('36氪', 'https://36kr.com'),
        ('虎嗅', 'https://www.huxiu.com'),
        ('财新网', 'https://www.caixin.com'),
        ('网易新闻', 'https://news.163.com'),
        ('新浪新闻', 'https://news.sina.com.cn'),
        ('哔哩哔哩', 'https://www.bilibili.com'),
        ('百度贴吧', 'https://tieba.baidu.com'),
        ('搜狐新闻', 'https://news.sohu.com'),
        ('天涯社区', 'https://www.tianya.cn'),
        (' QQ新闻', 'https://news.qq.com'),
        ('凤凰网', 'https://www.ifeng.com'),
        ('观察者网', 'https://www.guancha.cn'),
        ('界面新闻', 'https://www.jiemian.com'),
    ]
    
    success_count = 0
    results = []
    
    for name, url in sites:
        try:
            response = requests.get(url, headers=headers, timeout=10)
            is_success = response.status_code < 400
            success_count += int(is_success)
            status_symbol = '✓' if is_success else '✗'
            print(f'{status_symbol} {name}: {response.status_code} ({len(response.content)} bytes)')
            results.append({
                'name': name,
                'url': url,
                'status_code': response.status_code,
                'size': len(response.content),
                'success': is_success
            })
        except Exception as e:
            error_msg = str(e)[:60]
            print(f'✗ {name}: {error_msg}')
            results.append({
                'name': name,
                'url': url,
                'status_code': None,
                'size': 0,
                'success': False,
                'error': error_msg
            })
    
    print(f'\n{"="*60}')
    print(f'测试结果汇总')
    print(f'{"="*60}')
    print(f'测试站点数: {len(sites)}')
    print(f'成功: {success_count} ({success_count/len(sites)*100:.1f}%)')
    print(f'失败: {len(sites) - success_count}')
    
    # 保存结果
    import json
    output_path = Path.cwd() / 'test_results' / 'connectivity_simple.json'
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f'\n详细结果已保存到: {output_path}')
    
    return success_count >= 20  # 至少20个成功


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
