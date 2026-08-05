---
name: douyin-search
skill: browser-cdp
script: douyin_search.py
description: 抖音搜索自动化脚本，支持视频搜索、用户搜索，获取视频标题、作者、播放量、点赞数等元数据。
triggers: 抖音, douyin, 短视频搜索, 抖音视频, douyin_search.py
platforms: windows, macos, linux, pc
---

# 抖音搜索自动化脚本 (`douyin_search.py`)

## 用途

使用 browser-cdp skill 搜索抖音，获取视频、用户等元数据信息。

## 技术特征分析

### 网站结构

- **搜索接口**：`https://www.douyin.com/search/{keyword}?type=video`
- **视频详情**：`https://www.douyin.com/video/{aweme_id}`
- **用户主页**：`https://www.douyin.com/user/{sec_uid}`
- **数据格式**：纯前端渲染，所有数据通过 AJAX 加载
- **主要 API**：内部 API 需逆向，前端通过 WebSocket 推送数据

### 反爬机制

| 机制 | 强度 | 说明 |
|------|------|------|
| IP 频率限制 | ⭐⭐⭐⭐⭐ | 极高频率限制，极易封禁 |
| UA 检测 | ⭐⭐⭐⭐ | 严格检测非浏览器 UA |
| 验证码 | ⭐⭐⭐⭐ | 频繁触发滑块验证码 |
| 登录态 | ⭐⭐⭐⭐ | 大部分内容需登录 |
| 设备指纹 | ⭐⭐⭐⭐⭐ | 强设备指纹检测 |
| 行为检测 | ⭐⭐⭐⭐⭐ | 检测自动化行为 |

### 抓取策略

```python
# 推荐策略（谨慎使用）
- 必须使用 browser-cdp + --stealth 模式
- 强烈建议登录专用浏览器实例
- 请求间隔 10-30 秒（低频使用）
- 仅用于低频监控，不适合批量抓取
- 准备好应对验证码挑战
- 建议配合代理池使用
```

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 搜索视频
python src/searchers/douyin_search.py "科技新闻" --type video --max-results 5

# 搜索用户
python src/searchers/douyin_search.py "科技博主" --type user --max-results 5

# 使用已登录的浏览器实例
python src/searchers/douyin_search.py "热点视频" --dedicated --name douyin_session
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（必填） | - |
| `--type` | 搜索类型 (video/user) | video |
| `--max-results` | 最大结果数量 | 5 |
| `--output-dir` | 输出目录 | `./search_results/douyin` |
| `--port` | CDP 调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--dedicated` | 使用专用浏览器实例 | False |
| `--name` | 浏览器实例名称 | - |
| `--wait-timeout` | 页面等待超时(秒) | 60 |

## 输出格式

```json
{
  "title": "视频标题",
  "url": "https://www.douyin.com/video/1234567890",
  "author": "作者昵称",
  "author_id": "sec_uid_xxx",
  "play_count": "100万",
  "like_count": "5万",
  "comment_count": "3000",
  "share_count": "1000",
  "duration": "00:30",
  "source": "douyin",
  "scraped_at": "2026-08-05 10:30:00"
}
```

## 核心实现要点

- 使用 JS 提取视频列表中的元数据
- 播放量、点赞数从 DOM 中提取
- 支持无限滚动加载更多结果
- 必须使用 stealth 模式
- 需要处理验证码挑战

## 注意事项

- ⚠️ 抖音反爬极强，仅建议低频使用
- 高频请求会导致 IP 被封禁
- 建议配合代理池使用
- 准备好手动处理验证码
- 不适合批量抓取场景
- 仅抓取公开可见的元数据
