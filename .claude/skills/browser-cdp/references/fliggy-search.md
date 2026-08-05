---
name: fliggy-search
skill: browser-cdp
script: fliggy_search.py
description: 飞猪搜索自动化脚本，支持酒店搜索，获取酒店名称、价格、评分、商家信息等信息。
triggers: 飞猪, fliggy, 阿里旅行, 酒店搜索, fliggy_search.py
platforms: windows, macos, linux, pc
---

# 飞猪酒店搜索自动化脚本 (`fliggy_search.py`)

## 用途

使用 browser-cdp skill 搜索飞猪酒店，获取酒店价格、评分、商家信息等核心信息。

## 技术特征分析

### 网站结构

- **搜索接口**：`https://hotels.fliggy.com/search?city={city_id}&checkIn={date}&checkOut={date}`
- **酒店详情**：`https://hotels.fliggy.com/hotel/{hotel_id}.html`
- **数据格式**：JSON API + SSR 混合
- **主要 API**：`https://hotels.fliggy.com/api/search?city={city_id}&checkIn={date}&checkOut={date}`

### 反爬机制

| 机制 | 强度 | 说明 |
|------|------|------|
| IP 频率限制 | ⭐⭐⭐ | 中等频率限制 |
| UA 检测 | ⭐⭐ | 检测非浏览器 UA |
| 验证码 | ⭐ | 较少触发 |
| 登录态 | ⭐⭐ | 部分优惠需登录 |
| 频率限制 | ⭐⭐ | 建议请求间隔 3-5 秒 |

### 抓取策略

```python
# 推荐策略
- 使用 browser-cdp + --stealth 模式
- 酒店列表页 SSR，可直接解析
- 价格数据通过 API 获取
- 请求间隔 3-5 秒
- 适合批量抓取酒店数据
```

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 搜索酒店
python src/searchers/fliggy_search.py "三亚" --checkin 2026-08-10 --checkout 2026-08-15 --max-results 20

# 指定价格范围
python src/searchers/fliggy_search.py "厦门" --min-price 300 --max-price 1000 --max-results 10

# 指定端口和输出目录
python src/searchers/fliggy_search.py "大理" --output-dir ./fliggy_results --port 9333
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `city` | 城市名称（必填） | - |
| `--checkin` | 入住日期 (YYYY-MM-DD) | 明天 |
| `--checkout` | 退房日期 (YYYY-MM-DD) | 后天 |
| `--min-price` | 最低价格（元） | 0 |
| `--max-price` | 最高价格（元） | 9999 |
| `--max-results` | 最大结果数量 | 20 |
| `--output-dir` | 输出目录 | `./search_results/fliggy` |
| `--port` | CDP 调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--wait-timeout` | 页面等待超时(秒) | 30 |

## 输出格式

```json
{
  "name": "酒店名称",
  "url": "https://hotels.fliggy.com/hotel/123456.html",
  "city": "三亚",
  "district": "海棠区",
  "address": "xx路xx号",
  "price": "¥888",
  "original_price": "¥1088",
  "rating": "4.9",
  "review_count": "5678",
  "merchant": "酒店官方",
  "source": "fliggy",
  "scraped_at": "2026-08-05 10:30:00"
}
```

## 核心实现要点

- 使用 JS 提取酒店列表中的核心信息
- 价格从 `.price` 类提取
- 评分从 `.rating` 类提取
- 支持分页加载
- 反检测模式隐藏自动化特征

## 注意事项

- 飞猪是阿里旗下平台，API 相对稳定
- 价格数据实时性高
- 建议指定日期范围获取准确价格
- 仅抓取公开可见的元数据
