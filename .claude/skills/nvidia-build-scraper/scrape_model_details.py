#!/usr/bin/env python3
"""
NVIDIA Build 模型详情页抓取脚本

从模型详情页抓取完整模型 ID 和规格信息。
详情页 URL 格式: https://build.nvidia.com/{publisher-slug}/{model-slug}

用法:
    python scrape_model_details.py --input models.json --output models_full.json
    python scrape_model_details.py --input models.json --output models_full.json --batch-size 10 --start 0 --end 10

示例:
    # 抓取所有模型详情
    python scrape_model_details.py --input nvidia_models.json --output nvidia_models_full.json

    # 分批抓取（前 10 个）
    python scrape_model_details.py --input nvidia_models.json --output batch_0_10.json --start 0 --end 10
"""

import asyncio
import argparse
import json
import os
import sys
from playwright.async_api import async_playwright

# 发布者名称到 URL slug 的映射
# NVIDIA Build 网站的 publisher slug 与显示名称不完全一致
PUBLISHER_SLUG_MAP = {
    'z.ai': 'z-ai',
    'minimaxai': 'minimaxai',
    'google': 'google',
    'nvidia': 'nvidia',
    'stepfun-ai': 'stepfun-ai',
    'moonshotai': 'moonshotai',
    'qwen': 'qwen',
    'mistral ai': 'mistralai',
    'deepseek ai': 'deepseek-ai',
    'meta': 'meta',
    'abacus.ai': 'abacusai',
    'baai': 'baai',
    'upstage': 'upstage',
}


def get_publisher_slug(publisher):
    """将发布者显示名称转换为 URL slug"""
    key = publisher.lower().strip()
    if key in PUBLISHER_SLUG_MAP:
        return PUBLISHER_SLUG_MAP[key]
    # 默认: 小写、去空格和点
    return key.replace(' ', '').replace('.', '').replace('-', '')


def get_model_slug(name):
    """将模型名称转换为 URL slug"""
    return name.lower().replace(' ', '-')


def build_detail_url(publisher, name):
    """构建详情页 URL"""
    publisher_slug = get_publisher_slug(publisher)
    model_slug = get_model_slug(name)
    return f"https://build.nvidia.com/{publisher_slug}/{model_slug}"


# JavaScript: 从详情页提取信息
EXTRACT_DETAIL_JS = r"""() => {
    const text = document.body.innerText;
    const info = {url: window.location.href, title: document.title};
    
    // 模型 ID - 通常在 Login 按钮之后的第一行
    const lines = text.split('\n').map(l => l.trim()).filter(l => l);
    const loginIdx = lines.findIndex(l => l === 'Login');
    if (loginIdx >= 0 && loginIdx + 1 < lines.length) {
        info.modelId = lines[loginIdx + 1];
    }
    
    // 提取规格字段
    const fields = ['Provider', 'Last Modified', 'Parameters', 'Context Length'];
    info.fields = {};
    for (const field of fields) {
        const regex = new RegExp(field + '\\s*\\n([^\\n]+)', 'i');
        const match = text.match(regex);
        if (match) info.fields[field] = match[1].trim();
    }
    
    // 提取能力信息
    const capMatch = text.match(/Capabilities\s*\n([\s\S]*?)(?=Model Availability|Terms of Use|$)/);
    if (capMatch) info.capabilities = capMatch[1].trim();
    
    // 提取可用性信息
    const availMatch = text.match(/Model Availability\s*\n([\s\S]*?)(?=Terms of Use|$)/);
    if (availMatch) info.availability = availMatch[1].trim();
    
    return info;
}"""

# JavaScript: 移除 Cookie 横幅
REMOVE_COOKIE_BANNER_JS = r"""() => {
    document.querySelectorAll('#onetrust-consent-sdk, .onetrust-pc-dark-filter, [id*="onetrust"], [class*="onetrust"]').forEach(el => el.remove());
}"""


async def scrape_details_batch(models, start=0, end=None, timeout_per_page=20000):
    """
    批量抓取模型详情页。
    
    Args:
        models: 模型列表（含 publisher 和 name）
        start: 起始索引
        end: 结束索引（None 表示到末尾）
        timeout_per_page: 每个详情页的超时时间（毫秒）
    
    Returns:
        list: 更新后的模型列表（含详情信息）
    """
    if end is None:
        end = len(models)
    
    batch = models[start:end]
    print(f"Scraping details: models {start} to {end} ({len(batch)} models)")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_viewport_size({'width': 1920, 'height': 1080})
        
        for i, model in enumerate(batch):
            idx = start + i
            url = build_detail_url(model['publisher'], model['name'])
            print(f"[{idx+1}/{len(models)}] {url}")
            
            try:
                await page.goto(url, wait_until='domcontentloaded', timeout=timeout_per_page)
                await page.wait_for_timeout(1500)
                
                # 移除 Cookie 横幅
                await page.evaluate(REMOVE_COOKIE_BANNER_JS)
                
                # 提取详情
                detail = await page.evaluate(EXTRACT_DETAIL_JS)
                
                model_id = detail.get('modelId', '')
                
                # 验证 model_id 格式 (应为 publisher/model-name)
                # 必须包含 / 且左侧部分与 publisher slug 匹配
                publisher_slug = get_publisher_slug(model['publisher'])
                model_slug = get_model_slug(model['name'])
                expected_id = f"{publisher_slug}/{model_slug}"
                
                if model_id and '/' in model_id and model_id != '404 Error':
                    # 验证: model_id 的 publisher 部分应与预期匹配
                    id_parts = model_id.split('/', 1)
                    if id_parts[0] == publisher_slug:
                        model['full_model_id'] = model_id
                    else:
                        # 不匹配，使用 URL 构造的 ID
                        model['full_model_id'] = expected_id
                        model['detail_note'] = f'Extracted ID mismatch: {model_id}'
                else:
                    # 回退: 从 URL 构造
                    model['full_model_id'] = expected_id
                    if model_id == '404 Error':
                        model['detail_error'] = '404 - Page not found'
                
                model['detail_title'] = detail.get('title', '')
                model['detail_fields'] = detail.get('fields', {})
                model['capabilities'] = detail.get('capabilities', '')
                model['availability'] = detail.get('availability', '')
                model['detail_url'] = url
                
                params = model['detail_fields'].get('Parameters', 'N/A')
                print(f"  -> ID: {model['full_model_id']}, Params: {params}")
                
            except Exception as e:
                print(f"  -> Error: {e}")
                model['full_model_id'] = f"{get_publisher_slug(model['publisher'])}/{get_model_slug(model['name'])}"
                model['detail_error'] = str(e)
                model['detail_url'] = url
            
            await page.wait_for_timeout(300)
        
        await browser.close()
    
    return batch


def main():
    parser = argparse.ArgumentParser(description='Scrape NVIDIA Build model details')
    parser.add_argument('--input', '-i', required=True,
                        help='Input JSON file (from scrape_nvidia_models.py)')
    parser.add_argument('--output', '-o', required=True,
                        help='Output JSON file with details')
    parser.add_argument('--start', type=int, default=0,
                        help='Start index (for batch processing)')
    parser.add_argument('--end', type=int, default=None,
                        help='End index (for batch processing)')
    parser.add_argument('--timeout', type=int, default=20000,
                        help='Timeout per page in milliseconds')
    args = parser.parse_args()
    
    # 加载模型列表
    with open(args.input, 'r', encoding='utf-8') as f:
        models = json.load(f)
    
    print(f"Loaded {len(models)} models from {args.input}")
    
    # 抓取详情
    results = asyncio.run(scrape_details_batch(
        models, args.start, args.end, args.timeout
    ))
    
    # 保存结果
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"\nSaved {len(results)} models to {args.output}")
    
    # 打印摘要
    print(f"\n=== Summary ===")
    for i, m in enumerate(results, 1):
        model_id = m.get('full_model_id', 'N/A')
        params = m.get('detail_fields', {}).get('Parameters', 'N/A')
        print(f"{i:2d}. {model_id:50s} Params: {params}")


if __name__ == "__main__":
    main()
