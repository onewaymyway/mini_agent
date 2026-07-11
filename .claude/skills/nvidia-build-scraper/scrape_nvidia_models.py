#!/usr/bin/env python3
"""
NVIDIA Build 模型列表抓取脚本

从 https://build.nvidia.com/models 抓取模型列表，支持：
- 自定义筛选条件（filters 参数）
- 自动翻页（处理 NVIDIA 设计系统的分页组件）
- 绕过 OneTrust Cookie 同意横幅
- 提取模型名称、发布者、描述、标签、下载量、更新时间

用法:
    python scrape_nvidia_models.py [--output OUTPUT] [--url URL] [--max-pages MAX_PAGES]

示例:
    # 抓取 Preview + Upgrade Available 模型
    python scrape_nvidia_models.py

    # 抓取所有模型
    python scrape_nvidia_models.py --url "https://build.nvidia.com/models"

    # 指定输出文件和最大页数
    python scrape_nvidia_models.py --output models.json --max-pages 10
"""

import asyncio
import argparse
import json
import os
import sys
from playwright.async_api import async_playwright

# JavaScript: 从模型卡片提取信息
# 模型卡片使用 NVIDIA 设计系统组件 nv-card-root
EXTRACT_MODELS_JS = r"""() => {
    const models = [];
    const cards = document.querySelectorAll('[class*="nv-card-root"]');
    
    cards.forEach((card, idx) => {
        try {
            const cardText = card.innerText.trim();
            if (!cardText || cardText.length < 10) return;
            
            const lines = cardText.split('\n').map(l => l.trim()).filter(l => l);
            
            let name = '';
            let publisher = '';
            let description = '';
            let tags = [];
            let modelType = '';
            let downloads = '';
            let updated = '';
            
            // 提取标签/徽章
            const badges = card.querySelectorAll('[class*="nv-badge"], [class*="nv-tag-root"]');
            badges.forEach(badge => {
                const text = badge.innerText.trim();
                if (text) tags.push(text);
            });
            
            // 逐行解析卡片内容
            // 典型结构: Publisher -> Badge -> Model Name -> Description -> Tags -> Stats
            for (let i = 0; i < lines.length; i++) {
                const line = lines[i];
                if (!line || line.length > 200) continue;
                
                // 端点类型徽章
                if (line.match(/^(Free Endpoint|Partner Endpoint|Downloadable|Beta)$/)) {
                    modelType = line;
                    continue;
                }
                
                // 下载量 (如 6M, 230K, 473)
                if (line.match(/^\d+[KM]?$/)) {
                    if (!downloads) downloads = line;
                    else if (!updated) updated = line;
                    continue;
                }
                
                // 更新时间 (如 Today, 20d, 1mo, 11mo)
                if (line.match(/^(Today|\d+[dmh]|\d+mo|\d+yr)$/)) {
                    updated = line;
                    continue;
                }
                
                // 发布者 (第一个非数字行)
                if (!publisher && !modelType && !line.match(/^\d/)) {
                    publisher = line;
                    continue;
                }
                
                // 模型名称 (发布者和类型之后，长度 1-100)
                if (publisher && modelType && !name && line.length > 1 && line.length < 100) {
                    name = line;
                    continue;
                }
                
                // 描述 (名称之后，长度 > 20)
                if (name && !description && line.length > 20) {
                    description = line;
                    continue;
                }
                
                // 功能标签 (描述之后，短小关键词)
                if (name && description && line.length < 30 && line.match(/^[a-z\-]+$/i)) {
                    tags.push(line);
                }
            }
            
            // 回退: 如果结构化解析失败，使用前几行
            if (!name && lines.length >= 3) {
                publisher = lines[0];
                modelType = lines[1];
                name = lines[2];
                if (lines.length > 3) description = lines[3];
                if (lines.length > 4) tags.push(lines[4]);
            }
            
            if (name && name.length > 1) {
                models.push({
                    name,
                    publisher,
                    description,
                    tags,
                    modelType,
                    downloads,
                    updated,
                    page: window.pageNum || 1,
                    rawText: cardText.substring(0, 500)
                });
            }
        } catch (e) {}
    });
    
    return models;
}"""

# JavaScript: 移除 OneTrust Cookie 同意横幅
REMOVE_COOKIE_BANNER_JS = r"""() => {
    const selectors = [
        '#onetrust-consent-sdk',
        '.onetrust-pc-dark-filter',
        '[id*="onetrust"]',
        '[class*="onetrust"]',
        '#onetrust-banner-sdk',
        '.ot-fade-in'
    ];
    selectors.forEach(sel => {
        document.querySelectorAll(sel).forEach(el => el.remove());
    });
}"""


async def scrape_model_list(url, max_pages=20, output_file=None):
    """
    抓取 NVIDIA Build 模型列表页，自动翻页。
    
    Args:
        url: 模型列表页 URL（含 filters 参数）
        max_pages: 最大翻页数（安全限制）
        output_file: 输出 JSON 文件路径
    
    Returns:
        list: 去重后的模型列表
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_viewport_size({'width': 1920, 'height': 1080})
        
        print(f"Navigating to: {url}")
        await page.goto(url, wait_until='networkidle', timeout=60000)
        await page.wait_for_timeout(5000)
        
        # 移除 Cookie 横幅
        await page.evaluate(REMOVE_COOKIE_BANNER_JS)
        await page.wait_for_timeout(1000)
        
        all_models = []
        page_num = 1
        
        while True:
            print(f"\n=== Scraping page {page_num} ===")
            await page.wait_for_timeout(3000)
            await page.evaluate(f'window.pageNum = {page_num}')
            
            models = await page.evaluate(EXTRACT_MODELS_JS)
            print(f"Found {len(models)} models on page {page_num}")
            
            if len(models) == 0:
                print("No models found, stopping.")
                break
            
            all_models.extend(models)
            
            # 尝试翻页
            next_clicked = False
            
            # 策略 1: 点击页码按钮
            # NVIDIA 分页按钮显示为 '11','22','33','44' 格式，实际对应第 1-4 页
            try:
                next_page_num = page_num + 1
                clicked = await page.evaluate(f'''() => {{
                    const buttons = document.querySelectorAll('[data-testid="nv-pagination-page-list"] button, [class*="nv-pagination-page-list"] button');
                    for (const btn of buttons) {{
                        const text = btn.textContent.trim();
                        // 处理显示问题: '11' -> 1, '22' -> 2
                        let num;
                        if (text.length === 2 && text[0] === text[1]) {{
                            num = parseInt(text[0]);
                        }} else {{
                            num = parseInt(text.replace(/[^0-9]/g, ''));
                        }}
                        if (!isNaN(num) && num === {next_page_num}) {{
                            btn.click();
                            return true;
                        }}
                    }}
                    return false;
                }}''')
                if clicked:
                    print(f"Clicked page number {next_page_num}")
                    await page.wait_for_timeout(3000)
                    try:
                        await page.wait_for_load_state('networkidle', timeout=10000)
                    except:
                        pass
                    await page.wait_for_timeout(2000)
                    next_clicked = True
            except Exception as e:
                print(f"Error clicking page number: {e}")
            
            # 策略 2: 点击下一页箭头按钮
            if not next_clicked:
                try:
                    next_clicked = await page.evaluate('''() => {
                        const nextBtn = document.querySelector('button[data-testid="nv-pagination-arrow-button"]:last-child');
                        if (nextBtn && !nextBtn.disabled && nextBtn.getAttribute('aria-disabled') !== 'true') {
                            nextBtn.click();
                            return true;
                        }
                        return false;
                    }''')
                    if next_clicked:
                        print("Clicked next page arrow button")
                        await page.wait_for_timeout(3000)
                        try:
                            await page.wait_for_load_state('networkidle', timeout=10000)
                        except:
                            pass
                        await page.wait_for_timeout(2000)
                except Exception as e:
                    print(f"Error clicking next arrow: {e}")
            
            if not next_clicked:
                print("No next page found, stopping.")
                break
            
            page_num += 1
            if page_num > max_pages:
                print(f"Reached max page limit ({max_pages})")
                break
        
        await browser.close()
        
        # 去重
        seen = set()
        unique_models = []
        for model in all_models:
            key = model['name'].lower().strip()
            if key not in seen:
                seen.add(key)
                unique_models.append(model)
        
        print(f"\n=== Total unique models found: {len(unique_models)} ===")
        
        if output_file:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(unique_models, f, ensure_ascii=False, indent=2)
            print(f"Saved to {output_file}")
        
        return unique_models


def main():
    parser = argparse.ArgumentParser(description='Scrape NVIDIA Build model list')
    parser.add_argument('--url', default='https://build.nvidia.com/models?filters=nimType%3Anim_type_preview%2CnimType%3Anim_type_upgrade_available',
                        help='Model list URL with filters')
    parser.add_argument('--output', '-o', default='./nvidia_models.json',
                        help='Output JSON file path')
    parser.add_argument('--max-pages', type=int, default=20,
                        help='Maximum pages to scrape')
    args = parser.parse_args()
    
    asyncio.run(scrape_model_list(args.url, args.max_pages, args.output))


if __name__ == "__main__":
    main()
