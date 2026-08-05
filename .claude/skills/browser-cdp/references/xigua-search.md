---
name: xigua-search
skill: browser-cdp
script: xigua_search.py
description: 西瓜视频搜索自动化脚本，支持视频搜索，获取视频标题、作者、播放量、时长等元数据。
triggers: 西瓜视频, xigua, 西瓜, 视频搜索, xigua_search.py
platforms: windows, macos, linux, pc
---

# 西瓜视频搜索自动化脚本 (`xigua_search.py`)

## 用途

使用 browser-cdp skill 搜索西瓜视频，获取视频元数据信息。

## 技术特征分析

### 网站结构

- **搜索接口**：`https://www.ixigua.com/search/{keyword}/video`
- **视频详情**：`https://www.ixigua.com/{aweme_id}`
- **数据格式**：前端渲染，数据通过 AJAX 加载
- **主要 API**：内部 API 需逆向

### 反爬机制

| 机制 | 强度 | 说明 |
|------|------|------|
| IP 频率限制 | ⭐⭐⭐ | 中等频率限制 |
| UA 检测 | ⭐⭐ | 检测非浏览器 UA |
| 验证码 | ⭐ | 较少触发 |
| 登录态 | ⭐ | 搜索无需登录 |
| 频率限制 | ⭐⭐ | 建议请求间隔 3-5 秒 |

### 抓取策略

```python
# 推荐策略
- 使用 browser-cdp + --stealth 模式
- 搜索无需登录
- 请求间隔 3-5 秒
- 适合批量抓取视频元数据
```

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 搜索视频
python src/searchers/xigua_search.py "科技" --max-results 10

# 指定输出目录
python src/searchers/xigua_search.py "美食" --output-dir ./xigua_results --port 9333
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（必填） | - |
| `--max-results` | 最大结果数量 | 10 |
| `--output-dir` | 输出目录 | `./search_results/xigua` |
| `--port` | CDP 调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--wait-timeout` | 页面等待超时(秒) | 30 |

## 输出格式

```json
{
  "title": "视频标题",
  "url": "https://www.ixigua.com/1234567890",
  "author": "作者昵称",
  "play_count": "100万",
  "like_count": "5万",
  "duration": "05:30",
  "source": "xigua",
  "scraped_at": "2026-08-05 10:30:00"
}
```

## 核心实现要点

- 使用 JS 提取视频列表中的元数据
- 播放量、点赞数从 DOM 中提取
- 支持无限滚动加载更多结果
- 反检测模式隐藏自动化特征

## 注意事项

- 西瓜视频反爬较弱，适合批量抓取
- 搜索无需登录
- 建议启用 stealth 模式
- 仅抓取公开可见的元数据
