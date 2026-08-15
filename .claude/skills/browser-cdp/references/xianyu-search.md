---
name: xianyu-search	skill: browser-cdp
script: xianyu_search.py
description: 闲鱼商品搜索自动化脚本，支持关键词、价格区间筛选，获取商品价格和状态信息。
triggers: 闲鱼搜索, xianyu search, 二手商品, 淘宝二手, xianyu_search.py
platforms: windows, macos, linux, pc
---

# 闲鱼商品搜索自动化脚本 (`xianyu_search.py`)

## 用途

使用 browser-cdp skill 搜索闲鱼二手商品，获取商品价格、成色、卖家位置等信息。

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 基础搜索
python src/searchers/xianyu_search.py "iPhone" --max-results 20

# 指定价格区间
python src/searchers/xianyu_search.py "Switch" --min-price 1000 --max-price 3000 --output-dir ./xianyu_results

# 启用反检测模式
python src/searchers/xianyu_search.py "MacBook" --stealth --max-results 15
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（必填） | - |
| `--min-price` | 最低价格 | 无限制 |
| `--max-price` | 最高价格 | 无限制 |
| `--max-results` | 最大结果数量 | 20 |
| `--output-dir` | 输出目录 | `./search_results/xianyu` |
| `--port` | CDP 调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--wait-timeout` | 页面等待超时(秒) | 30 |

## 输出格式

```json
{
  "title": "iPhone 14 Pro Max 256G 国行在保",
  "url": "https://m.taobao.com/xxx",
  "price": 5888,
  "original_price": 8999,
  "condition": "95新",
  "seller": "买家昵称",
  "location": "浙江杭州",
  "sales_count": 0,
  "tags": ["包邮", "面交"],
  "source": "xianyu",
  "scraped_at": "2026-08-15 11:58:00"
}
```

## 核心实现要点

- 使用 JS 提取 `.item-card` 或 `.goods-item` 中的商品信息
- 价格从 `.price` 提取（注意单位是元）
- 成色从 `.condition` 提取，支持 "95新"、"全新" 等格式
- 卖家信息从 `.seller` 提取
- 位置信息从 `.location` 提取
- 支持按价格、发布时间排序筛选
- 反检测模式隐藏自动化特征
- 验证码检测：检测 slider 滑块验证

## 注意事项

- 闲鱼是淘宝旗下产品，反爬策略与淘宝相似
- 搜索可能触发滑块验证
- 部分商品信息需要登录后才能查看完整内容
- 建议控制搜索频率，避免触发验证码
- 价格可能因协商空间而有所不同

## 技术特征

- **前端框架**: React SPA
- **反爬等级**: 2（中度）
- **验证码类型**: slider（滑块验证）
- **登录要求**: 否
- **目标成功率**: 80%

## 相关配置

参见 `config/websites/xianyu.com.json` 获取完整的站点配置。