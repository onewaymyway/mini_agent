---
name: pdd-search
skill: browser-cdp
script: pdd_search.py
description: 拼多多商品搜索自动化脚本，支持关键词搜索、价格/销量提取、商品详情获取。
triggers: 拼多多搜索, pdd search, 拼多多, 商品搜索, pdd_search.py
platforms: windows, macos, linux, pc
---

# 拼多多商品搜索自动化脚本 (`pdd_search.py`)

## 用途

使用 browser-cdp skill 搜索拼多多商品，获取价格、销量等核心信息。

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 基础搜索
python src/searchers/pdd_search.py "手机壳" --max-results 10

# 指定端口和输出目录
python src/searchers/pdd_search.py "蓝牙耳机" --max-results 5 --port 9333 --output-dir ./pdd_results

# 启用反检测模式
python src/searchers/pdd_search.py "零食" --stealth --max-results 10
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（必填） | - |
| `--max-results` | 最大结果数量 | 10 |
| `--output-dir` | 输出目录 | `./search_results/pdd` |
| `--port` | CDP 调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--wait-timeout` | 页面等待超时(秒) | 30 |

## 输出格式

```json
{
  "title": "商品标题",
  "url": "https://mobile.yangkeduo.com/goods.html?xxx",
  "price": "9.9",
  "sales": "10万+",
  "shop": "店铺名称",
  "image_url": "https://img.pddpic.com/...",
  "source": "pdd",
  "scraped_at": "2026-08-02 10:30:00"
}
```

## 核心实现要点

- 使用 JS 提取 `.goods-list` 中的商品信息
- 价格从 `.goods-price` 提取（注意单位是元）
- 销量从 `.goods-sales` 提取
- 支持 URL 去重
- 反检测模式隐藏自动化特征

## 注意事项

- 拼多多反爬较严格，建议启用 `--stealth` 模式
- 大量搜索可能触发验证码
- 价格可能因优惠券/活动而变化
