---
name: mafengwo-search
skill: browser-cdp
script: mafengwo_search.py
description: 马蜂窝搜索自动化脚本，支持旅游攻略搜索，获取攻略标题、作者、浏览量、点赞数等元数据。
triggers: 马蜂窝, mafengwo, 旅游攻略, 旅行攻略, mafengwo_search.py
platforms: windows, macos, linux, pc
---

# 马蜂窝旅游攻略搜索自动化脚本 (`mafengwo_search.py`)

## 用途

使用 browser-cdp skill 搜索马蜂窝旅游攻略，获取攻略元数据信息。

## 技术特征分析

### 网站结构

- **搜索接口**：`https://so.mafengwo.cn/s?q={keyword}&type=2`
- **攻略详情**：`https://www.mafengwo.cn/i/{article_id}.html`
- **数据格式**：前端渲染，数据通过 AJAX 加载
- **主要 API**：`https://so.mafengwo.cn/s?q={keyword}&type=2&start={offset}`

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
- 使用 browser-cdp + --stealth 模式
- 搜索无需登录
- 请求间隔 2-4 秒
- 适合批量抓取攻略数据
```

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 搜索攻略
python src/searchers/mafengwo_search.py "日本旅游" --max-results 20

# 指定输出目录
python src/searchers/mafengwo_search.py "云南攻略" --output-dir ./mafengwo_results --port 9333
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（必填） | - |
| `--max-results` | 最大结果数量 | 20 |
| `--output-dir` | 输出目录 | `./search_results/mafengwo` |
| `--port` | CDP 调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--wait-timeout` | 页面等待超时(秒) | 30 |

## 输出格式

```json
{
  "title": "攻略标题",
  "url": "https://www.mafengwo.cn/i/12345678.html",
  "author": "作者昵称",
  "views": "10万+",
  "likes": "5000",
  "comments": "300",
  "days": "7天6晚",
  "destination": "日本",
  "source": "mafengwo",
  "scraped_at": "2026-08-05 10:30:00"
}
```

## 核心实现要点

- 使用 JS 提取攻略列表中的元数据
- 浏览量、点赞数从 DOM 中提取
- 支持无限滚动加载更多结果
- 反检测模式隐藏自动化特征

## 注意事项

- 马蜂窝反爬较弱，适合批量抓取
- 搜索无需登录
- 建议启用 stealth 模式
- 仅抓取公开可见的元数据
