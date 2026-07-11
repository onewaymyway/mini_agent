#!/usr/bin/env python3
"""
NVIDIA Build 模型完整抓取流程

一键执行完整流程：
1. 抓取模型列表页（自动翻页）
2. 抓取每个模型的详情页（获取完整模型 ID 和规格）
3. 生成最终 JSON 和 Markdown 报告

用法:
    python scrape_all.py [--url URL] [--output-dir DIR] [--max-pages N] [--batch-size N]

示例:
    # 抓取 Preview + Upgrade Available 模型（默认）
    python scrape_all.py

    # 抓取所有模型
    python scrape_all.py --url "https://build.nvidia.com/models"

    # 指定输出目录和批大小
    python scrape_all.py --output-dir ./output --batch-size 10
"""

import asyncio
import argparse
import json
import os
import sys
import time
from pathlib import Path

# 添加当前目录到 path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scrape_nvidia_models import scrape_model_list
from scrape_model_details import scrape_details_batch, build_detail_url
from generate_providers_config import generate_providers_config


def generate_report(models, output_path):
    """生成 Markdown 报告"""
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("# NVIDIA Build 模型完整列表报告\n\n")
        f.write(f"**抓取时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"**模型总数**: {len(models)}\n\n")
        f.write("## 模型列表\n\n")
        f.write("| # | 完整模型 ID | 发布者 | 类型 | 下载量 | 更新时间 | 描述 |\n")
        f.write("|---|------------|--------|------|--------|----------|------|\n")
        
        for i, m in enumerate(models, 1):
            model_id = m.get('full_model_id', m.get('name', 'N/A'))
            publisher = m.get('publisher', 'N/A')
            model_type = m.get('modelType', 'N/A')
            downloads = m.get('downloads', '-')
            updated = m.get('updated', '-')
            desc = m.get('description', '')[:80].replace('|', '/')
            
            f.write(f"| {i} | `{model_id}` | {publisher} | {model_type} | {downloads} | {updated} | {desc} |\n")
        
        # 统计
        f.write("\n## 统计\n\n")
        
        # 按发布者
        publishers = {}
        for m in models:
            pub = m.get('publisher', 'Unknown')
            publishers[pub] = publishers.get(pub, 0) + 1
        
        f.write("### 按发布者分布\n\n")
        f.write("| 发布者 | 模型数 |\n|--------|--------|\n")
        for pub, count in sorted(publishers.items(), key=lambda x: -x[1]):
            f.write(f"| {pub} | {count} |\n")
        
        # 按类型
        f.write("\n### 按端点类型分布\n\n")
        types = {}
        for m in models:
            t = m.get('modelType', 'Unknown')
            types[t] = types.get(t, 0) + 1
        f.write("| 类型 | 数量 |\n|------|------|\n")
        for t, count in sorted(types.items(), key=lambda x: -x[1]):
            f.write(f"| {t} | {count} |\n")
    
    print(f"Report saved to {output_path}")


async def run_full_pipeline(url, output_dir, max_pages, batch_size):
    """执行完整抓取流程"""
    os.makedirs(output_dir, exist_ok=True)
    
    list_file = os.path.join(output_dir, 'nvidia_models.json')
    full_file = os.path.join(output_dir, 'nvidia_models_full.json')
    report_file = os.path.join(output_dir, 'nvidia_models_report.md')
    providers_file = os.path.join(output_dir, 'providers_nvidia.json')
    
    # Step 1: 抓取列表
    print("=" * 60)
    print("STEP 1: Scrape model list")
    print("=" * 60)
    models = await scrape_model_list(url, max_pages, list_file)
    
    if not models:
        print("No models found, exiting.")
        return
    
    # Step 2: 抓取详情
    print("\n" + "=" * 60)
    print("STEP 2: Scrape model details")
    print("=" * 60)
    
    all_results = []
    total = len(models)
    
    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        print(f"\n--- Batch {start}-{end} ---")
        batch = await scrape_details_batch(models, start, end)
        all_results.extend(batch)
        
        # 增量保存（防止中途失败丢失数据）
        with open(full_file, 'w', encoding='utf-8') as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2)
        
        # 批次间短暂休息
        if end < total:
            await asyncio.sleep(2)
    
    # Step 3: 生成报告
    print("\n" + "=" * 60)
    print("STEP 3: Generate report")
    print("=" * 60)
    generate_report(all_results, report_file)
    
    # Step 4: 生成 providers.json 配置
    print("\n" + "=" * 60)
    print("STEP 4: Generate providers.json config")
    print("=" * 60)
    providers_config = generate_providers_config(all_results)
    with open(providers_file, 'w', encoding='utf-8') as f:
        json.dump(providers_config, f, ensure_ascii=False, indent=2)
    print(f"Providers config saved to {providers_file}")
    print(f"  -> Copy llm_fallback_chain array to providers.json")
    print(f"  -> Replace {{{{NVIDIA_API_KEY}}}} with your real API key")
    
    # 最终摘要
    print("\n" + "=" * 60)
    print("COMPLETE")
    print("=" * 60)
    print(f"Total models: {len(all_results)}")
    print(f"List data:        {list_file}")
    print(f"Full data:        {full_file}")
    print(f"Report:           {report_file}")
    print(f"Providers config: {providers_file}")
    
    # 打印所有模型 ID
    print(f"\n=== All Model IDs ===")
    for i, m in enumerate(all_results, 1):
        model_id = m.get('full_model_id', 'N/A')
        params = m.get('detail_fields', {}).get('Parameters', 'N/A')
        print(f"{i:2d}. {model_id:50s} Params: {params}")


def main():
    parser = argparse.ArgumentParser(description='Scrape NVIDIA Build models - full pipeline')
    parser.add_argument('--url', default='https://build.nvidia.com/models?filters=nimType%3Anim_type_preview%2CnimType%3Anim_type_upgrade_available',
                        help='Model list URL with filters')
    parser.add_argument('--output-dir', '-o', default='./output',
                        help='Output directory for results')
    parser.add_argument('--max-pages', type=int, default=20,
                        help='Maximum pages to scrape')
    parser.add_argument('--batch-size', type=int, default=10,
                        help='Batch size for detail page scraping')
    args = parser.parse_args()
    
    asyncio.run(run_full_pipeline(args.url, args.output_dir, args.max_pages, args.batch_size))


if __name__ == "__main__":
    main()
