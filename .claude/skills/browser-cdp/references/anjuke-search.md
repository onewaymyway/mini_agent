---
name: anjuke-search
skill: browser-cdp
script: anjuke_search.py
description: 安居客搜索自动化脚本，支持小区搜索、房源搜索，获取小区名称、均价、房源信息等信息。
triggers: 安居客, anjuke, 房产搜索, 小区搜索, anjuke_search.py
platforms: windows, macos, linux, pc
---

# 安居客房产搜索自动化脚本 (`anjuke_search.py`)

## 用途

使用 browser-cdp skill 搜索安居客，获取小区、房源等房产信息。

## 技术特征分析

### 网站结构

- **小区搜索**：`https://{city}.anjuke.com/xiaoqu/{district}/`
- **房源搜索**：`https://{city}.anjuke.com/sale/`
- **数据格式**：SSR + JSON API 混合
- **主要 API**：内部 API 需逆向

### 反爬机制

| 机制 | 强度 | 说明 |
|------|------|------|
| IP 频率限制 | ⭐⭐ | 较弱频率限制 |
| UA 检测 | ⭐ | 检测较弱 |
| 验证码 | ⭐ | 极少触发 |
| 登录态 | ⭐ | 搜索无需登录 |
| 频率限制 | ⭐⭐ | 建议请求间隔 2-4 秒 |

### 抓取策略

```python
# 推荐策略
- 可直接 requests 抓取（反爬较弱）
- 使用 browser-cdp 处理动态内容
- 请求间隔 2-4 秒
- 适合批量抓取小区数据
```

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 搜索小区
python src/searchers/anjuke_search.py "北京" --type xiaoqu --max-results 20

# 搜索房源
python src/searchers/anjuke_search.py "上海" --type house --max-results 20

# 指定输出目录
python src/searchers/anjuke_search.py "深圳" --output-dir ./anjuke_results --port 9333
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `city` | 城市名称（必填） | - |
| `--type` | 搜索类型 (xiaoqu/house) | xiaoqu |
| `--max-results` | 最大结果数量 | 20 |
| `--output-dir` | 输出目录 | `./search_results/anjuke` |
| `--port` | CDP 调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--wait-timeout` | 页面等待超时(秒) | 30 |

## 输出格式

```json
{
  "name": "小区名称",
  "url": "https://bj.anjuke.com/xiaoqu/123456/",
  "city": "北京",
  "district": "朝阳区",
  "avg_price": "¥85000",
  "price_trend": "持平",
  "property_type": "住宅",
  "developer": "开发商名称",
  "source": "anjuke",
  "scraped_at": "2026-08-05 10:30:00"
}
```

## 核心实现要点

- 使用 JS 提取小区列表中的核心信息
- 均价从 `.price` 类提取
- 支持分页加载
- 反检测模式隐藏自动化特征

## 注意事项

- 安居客反爬较弱，适合批量抓取
- 搜索无需登录
- 建议启用 stealth 模式
- 仅抓取公开可见的元数据
