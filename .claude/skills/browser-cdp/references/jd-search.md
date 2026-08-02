---
name: jd-search
skill: browser-cdp
script: jd_search.py
description: 京东商品搜索自动化脚本，支持关键词搜索、价格/销量/评价提取、商品详情获取、结果保存为JSON。
triggers: 京东搜索, jd search, 商品搜索, 价格抓取, jd_search.py
platforms: windows, macos, linux, pc
---

# 京东商品搜索自动化脚本 (`jd_search.py`)

## 用途

使用 browser-cdp skill 搜索京东商品，获取价格、销量、评价数等核心信息。

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 基础搜索
python src/searchers/jd_search.py "iPhone 15" --max-results 10

# 指定端口和输出目录
python src/searchers/jd_search.py "机械键盘" --max-results 5 --port 9333 --output-dir ./jd_results

# 启用反检测模式
python src/searchers/jd_search.py "笔记本电脑" --stealth --max-results 10
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（必填） | - |
| `--max-results` | 最大结果数量 | 10 |
| `--output-dir` | 输出目录 | `./search_results/jd` |
| `--port` | CDP 调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--wait-timeout` | 页面等待超时(秒) | 30 |

## 输出格式

```json
{
  "title": "商品标题",
  "url": "https://item.jd.com/xxxxx.html",
  "price": "¥2999.00",
  "commit": "100万+",
  "shop": "京东自营",
  "sku_id": "100012345678",
  "image_url": "https://img.jd.com/...",
  "source": "jd",
  "scraped_at": "2026-08-02 10:30:00"
}
```

## 核心实现要点

- 使用 JS 提取 `.gl-item` 列表中的商品信息
- 价格从 `.p-price strong` 提取
- 评价数从 `.p-commit strong` 提取
- 支持 URL 去重
- 反检测模式隐藏自动化特征

## 注意事项

- 京东反爬较严格，建议启用 `--stealth` 模式
- 大量搜索可能触发验证码，需手动处理
- 商品价格可能因地区/会员等级不同而有差异
