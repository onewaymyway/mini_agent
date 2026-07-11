#!/usr/bin/env python3
"""
将抓取的 NVIDIA Build 模型列表生成为 providers.json 格式的配置片段。

生成的格式可直接复制到 providers.json 的 llm_fallback_chain 数组中使用。
每个 NVIDIA 模型生成一个条目，provider 为 "nvidia"，model 为完整模型 ID。

用法:
    python generate_providers_config.py --input nvidia_models_full.json --output providers_nvidia.json
    python generate_providers_config.py --input nvidia_models_full.json --output providers_nvidia.json --api-key "{{SECRET}}"
    python generate_providers_config.py --input nvidia_models_full.json --output providers_nvidia.json --filter-tags coding,reasoning

示例:
    # 生成所有模型的 providers 配置
    python generate_providers_config.py -i nvidia_models_full.json -o providers_nvidia.json

    # 指定 API key 占位符
    python generate_providers_config.py -i nvidia_models_full.json -o providers_nvidia.json --api-key "{{NVIDIA_API_KEY}}"

    # 只生成 Free Endpoint 类型的模型
    python generate_providers_config.py -i nvidia_models_full.json -o providers_nvidia.json --filter-type "Free Endpoint"

    # 只生成带特定标签的模型
    python generate_providers_config.py -i nvidia_models_full.json -o providers_nvidia.json --filter-tags coding,reasoning
"""

import argparse
import json
import os
import sys


def generate_providers_entry(model_id, api_keys=None, key_rotation="passive", key_cooldown=60):
    """生成单个 providers.json 条目"""
    entry = {
        "provider": "nvidia",
        "model": model_id,
        "api_keys": api_keys or ["{{NVIDIA_API_KEY}}"],
        "key_rotation": key_rotation,
        "key_switch_on": ["LLMRateLimitError"],
        "key_cooldown": key_cooldown
    }
    return entry


def generate_providers_config(models, api_key=None, filter_type=None, filter_tags=None, 
                               filter_publisher=None, exclude_names=None):
    """
    生成 providers.json 格式的配置。
    
    Args:
        models: 模型列表（含 full_model_id 字段）
        api_key: API key 占位符
        filter_type: 按端点类型筛选 (如 "Free Endpoint")
        filter_tags: 按标签筛选 (列表，如 ["coding", "reasoning"])
        filter_publisher: 按发布者筛选 (如 "NVIDIA")
        exclude_names: 要排除的模型名称列表
    
    Returns:
        dict: providers.json 格式的配置
    """
    api_keys = [api_key] if api_key else ["{{NVIDIA_API_KEY}}"]
    
    chain = []
    seen_ids = set()
    
    for model in models:
        model_id = model.get('full_model_id', '')
        if not model_id or '/' not in model_id:
            continue
        if model_id in seen_ids:
            continue
        
        # 筛选: 端点类型
        if filter_type:
            model_type = model.get('modelType', '')
            if filter_type.lower() not in model_type.lower():
                continue
        
        # 筛选: 标签
        if filter_tags:
            model_tags = [t.lower() for t in model.get('tags', [])]
            if not any(ft.lower() in model_tags for ft in filter_tags):
                continue
        
        # 筛选: 发布者
        if filter_publisher:
            publisher = model.get('publisher', '')
            if filter_publisher.lower() not in publisher.lower():
                continue
        
        # 排除指定模型
        if exclude_names:
            name = model.get('name', '').lower()
            if any(ex.lower() in name for ex in exclude_names):
                continue
        
        seen_ids.add(model_id)
        entry = generate_providers_entry(model_id, api_keys)
        chain.append(entry)
    
    config = {
        "_comment": f"NVIDIA Build 模型 providers 配置，共 {len(chain)} 个模型。直接复制 llm_fallback_chain 内容到 providers.json 中使用。",
        "llm_fallback_chain": chain,
        "llm_fallback_on": [
            "LLMRateLimitError",
            "LLMTimeoutError",
            "LLMProviderError"
        ],
        "providers": {
            "nvidia": {
                "api_keys": api_keys,
                "key_rotation": "round_robin",
                "key_cooldown": 60
            }
        }
    }
    
    return config


def main():
    parser = argparse.ArgumentParser(description='Generate providers.json config from NVIDIA Build models')
    parser.add_argument('--input', '-i', required=True,
                        help='Input JSON file (from scrape_all.py or scrape_model_details.py)')
    parser.add_argument('--output', '-o', required=True,
                        help='Output providers config JSON file')
    parser.add_argument('--api-key', default=None,
                        help='API key placeholder (default: {{NVIDIA_API_KEY}})')
    parser.add_argument('--filter-type', default=None,
                        help='Filter by endpoint type (e.g. "Free Endpoint")')
    parser.add_argument('--filter-tags', default=None,
                        help='Filter by tags (comma-separated, e.g. "coding,reasoning")')
    parser.add_argument('--filter-publisher', default=None,
                        help='Filter by publisher (e.g. "NVIDIA")')
    parser.add_argument('--exclude', default=None,
                        help='Exclude models by name (comma-separated)')
    args = parser.parse_args()
    
    # 加载模型数据
    with open(args.input, 'r', encoding='utf-8') as f:
        models = json.load(f)
    
    print(f"Loaded {len(models)} models from {args.input}")
    
    # 解析筛选参数
    filter_tags = args.filter_tags.split(',') if args.filter_tags else None
    exclude_names = args.exclude.split(',') if args.exclude else None
    
    # 生成配置
    config = generate_providers_config(
        models,
        api_key=args.api_key,
        filter_type=args.filter_type,
        filter_tags=filter_tags,
        filter_publisher=args.filter_publisher,
        exclude_names=exclude_names
    )
    
    # 保存
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    
    chain_count = len(config['llm_fallback_chain'])
    print(f"\nGenerated providers config with {chain_count} models")
    print(f"Saved to {args.output}")
    
    # 打印摘要
    print(f"\n=== Models in config ===")
    for i, entry in enumerate(config['llm_fallback_chain'], 1):
        print(f"{i:2d}. {entry['model']}")
    
    # 打印使用说明
    print(f"\n=== Usage ===")
    print(f"1. 复制 {args.output} 中的 llm_fallback_chain 数组")
    print(f"2. 粘贴到 providers.json 的 llm_fallback_chain 数组中")
    print(f"3. 替换 {{{{NVIDIA_API_KEY}}}} 为真实的 NVIDIA API Key")
    print(f"4. 可选: 调整 key_rotation, key_cooldown 等参数")


if __name__ == "__main__":
    main()
