---
name: dianping-search	skill: browser-cdp
script: dianping_search.py
description: 大众点评商户搜索自动化脚本，支持城市、品类筛选，获取店铺评分和评论信息。
triggers: 大众点评搜索, dianping search, 餐厅搜索, 商户搜索, dianping_search.py
platforms: windows, macos, linux, pc
---

# 大众点评商户搜索自动化脚本 (`dianping_search.py`)

## 用途

使用 browser-cdp skill 搜索大众点评商户，获取店铺评分、评论数、人均消费等核心信息。

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 基础搜索
python src/searchers/dianping_search.py "火锅" --city 北京 --max-results 10

# 指定品类
python src/searchers/dianping_search.py "日料" --city 上海 --category 美食 --output-dir ./dianping_results

# 启用反检测模式
python src/searchers/dianping_search.py "咖啡厅" --city 深圳 --stealth --max-results 15
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（必填） | - |
| `--city` | 城市名称 | 全部 |
| `--category` | 品类分类 | 全部 |
| `--max-results` | 最大结果数量 | 10 |
| `--output-dir` | 输出目录 | `./search_results/dianping` |
| `--port` | CDP 调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--wait-timeout` | 页面等待超时(秒) | 30 |

## 输出格式

```json
{
  "title": "海底捞火锅(朝阳门店)",
  "url": "https://www.dianping.com/shop/xxx",
  "score": 4.8,
  "review_count": 2580,
  "avg_price": 120,
  "city": "北京",
  "category": "火锅",
  "address": "朝阳区三里屯",
  "tags": ["环境好", "服务赞"],
  "source": "dianping",
  "scraped_at": "2026-08-15 11:58:00"
}
```

## 核心实现要点

- 使用 JS 提取 `.shop-list-item` 或 `.shop-item` 中的商户信息
- 评分从 `.score` 提取，支持小数格式
- 评论数从 `.review-count` 提取
- 人均消费从 `.avg-price` 提取
- 地址信息从 `.shop-address` 提取
- 支持按评分、销量排序筛选
- 反检测模式隐藏自动化特征
- 验证码检测：检测 slider 滑块验证

## 注意事项

- 大众点评反爬较严格，建议启用 `--stealth` 模式
- 搜索结果可能需滑动加载更多
- 部分店铺信息需登录后查看完整内容
- 建议控制搜索频率，避免触发验证码

## 技术特征

- **前端框架**: Vue.js SPA
- **反爬等级**: 3（高度）
- **验证码类型**: slider（滑块验证）
- **登录要求**: 否
- **目标成功率**: 75%

## 相关配置

参见 `config/websites/dianping.com.json` 获取完整的站点配置。