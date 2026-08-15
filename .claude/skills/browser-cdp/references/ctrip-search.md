---
name: ctrip-search	skill: browser-cdp
script: ctrip_search.py
description: 携程酒店搜索自动化脚本，支持目的地、日期筛选，获取酒店评分和价格信息。
triggers: 携程搜索, ctrip search, 酒店搜索, 旅行预订, ctrip_search.py
platforms: windows, macos, linux, pc
---

# 携程酒店搜索自动化脚本 (`ctrip_search.py`)

## 用途

使用 browser-cdp skill 搜索携程酒店，获取酒店评分、价格、位置等核心信息。

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 基础搜索
python src/searchers/ctrip_search.py "北京" --check-in 2026-09-01 --check-out 2026-09-03 --max-results 10

# 指定星级
python src/searchers/ctrip_search.py "上海" --star 5 --output-dir ./ctrip_results

# 启用反检测模式
python src/searchers/ctrip_search.py "杭州" --stealth --max-results 15
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `destination` | 目的地（必填） | - |
| `--check-in` | 入住日期 | 明天 |
| `--check-out` | 退房日期 | 后天才 |
| `--star` | 星级筛选 (1-5) | 全部 |
| `--max-results` | 最大结果数量 | 10 |
| `--output-dir` | 输出目录 | `./search_results/ctrip` |
| `--port` | CDP 调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--wait-timeout` | 页面等待超时(秒) | 30 |

## 输出格式

```json
{
  "title": "北京王府井希尔顿酒店",
  "url": "https://hotels.ctrip.com/hotel/xxx.html",
  "price": 899,
  "original_price": 1299,
  "rating": 4.8,
  "review_count": 3560,
  "location": "东城区王府井",
  "star": 5,
  "tags": ["近地铁", "免费取消"],
  "source": "ctrip",
  "scraped_at": "2026-08-15 11:58:00"
}
```

## 核心实现要点

- 使用 JS 提取 `.hotel-item` 或 `.hotel-list-item` 中的酒店信息
- 价格从 `.price` 提取（注意是每晚价格）
- 评分从 `.rating` 提取，支持小数格式
- 星级信息从 `.star-rating` 提取
- 地址信息从 `.address` 提取
- 支持按价格、评分、距离排序筛选
- 反检测模式隐藏自动化特征
- 验证码检测：检测 slider 滑块验证

## 注意事项

- 携程反爬较严格，建议启用 `--stealth` 模式
- 搜索结果可能需滚动加载更多
- 价格会随日期和库存变化
- 建议控制搜索频率，避免触发验证码

## 技术特征

- **前端框架**: React SPA
- **反爬等级**: 3（高度）
- **验证码类型**: slider（滑块验证）
- **登录要求**: 否
- **目标成功率**: 75%

## 相关配置

参见 `config/websites/ctrip.com.json` 获取完整的站点配置。