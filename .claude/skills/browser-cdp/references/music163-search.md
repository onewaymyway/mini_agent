---
name: music163-search
skill: browser-cdp
script: music163_search.py
description: 网易云音乐搜索自动化脚本，支持歌曲/歌手/专辑搜索，获取歌曲名、歌手、专辑、时长等元数据。
triggers: 网易云音乐, music163, 音乐搜索, 歌曲搜索, music163_search.py
platforms: windows, macos, linux, pc
---

# 网易云音乐搜索自动化脚本 (`music163_search.py`)

## 用途

使用 browser-cdp skill 搜索网易云音乐，获取歌曲、歌手、专辑等元数据信息。

## 技术特征分析

### 网站结构

- **搜索接口**：`https://music.163.com/search?type=1&s={keyword}`（歌曲）、`type=100`（歌手）、`type=10`（专辑）
- **歌曲详情**：`https://music.163.com/#/song?id={song_id}`
- **歌手主页**：`https://music.163.com/#/artist?id={artist_id}`
- **数据格式**：前端通过 AJAX 调用内部 API，返回 JSON 数据
- **主要 API**：`https://music.163.com/api/search/get/web?s={keyword}&type={type}&limit={limit}&offset={offset}`

### 反爬机制

| 机制 | 强度 | 说明 |
|------|------|------|
| IP 频率限制 | ⭐⭐⭐ | 高频请求会返回空结果或 403 |
| UA 检测 | ⭐⭐ | 检测非浏览器 UA |
| 验证码 | ⭐ | 极少触发 |
| 登录态 | ⭐ | 搜索无需登录，但部分数据受限 |
| 频率限制 | ⭐⭐⭐ | 建议请求间隔 3-5 秒 |

### 抓取策略

```python
# 推荐策略
- 使用 browser-cdp + --stealth 模式
- 搜索无需登录，可直接抓取
- 优先调用 API 接口（更稳定）
- 请求间隔 3-5 秒
- 注意版权限制，仅抓取元数据（歌名、歌手、专辑、时长）
- 不支持抓取音频文件（版权保护）
```

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 搜索歌曲
python src/searchers/music163_search.py "晴天" --type song --max-results 10

# 搜索歌手
python src/searchers/music163_search.py "周杰伦" --type artist --max-results 5

# 搜索专辑
python src/searchers/music163_search.py "范特西" --type album --max-results 5

# 指定端口和输出目录
python src/searchers/music163_search.py "稻香" --output-dir ./music163_results --port 9333
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（必填） | - |
| `--type` | 搜索类型 (song/artist/album/playlist) | song |
| `--max-results` | 最大结果数量 | 10 |
| `--output-dir` | 输出目录 | `./search_results/music163` |
| `--port` | CDP 调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--wait-timeout` | 页面等待超时(秒) | 30 |

## 输出格式

```json
{
  "title": "晴天",
  "url": "https://music.163.com/#/song?id=33894312",
  "artist": "周杰伦",
  "album": "叶惠美",
  "duration": "04:29",
  "popularity": "85",
  "source": "music163",
  "scraped_at": "2026-08-05 10:30:00"
}
```

## 核心实现要点

- 使用 JS 提取搜索结果列表中的歌曲信息
- 歌曲时长从 `.dur` 类提取
- 支持 URL 去重
- 反检测模式隐藏自动化特征
- 仅抓取元数据，不下载音频

## 注意事项

- 网易云音乐反爬中等，建议启用 `--stealth` 模式
- 搜索接口相对稳定，但高频请求仍会触发限制
- 部分歌曲可能因版权原因无法获取完整信息
- 不支持抓取歌词和音频文件
