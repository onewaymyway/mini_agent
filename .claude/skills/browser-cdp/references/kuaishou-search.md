---
name: kuaishou-search
skill: browser-cdp
script: kuaishou_search.py
description: 快手搜索自动化脚本，支持视频搜索、用户搜索，获取视频标题、作者、播放量、点赞数等元数据。
triggers: 快手, kuaishou, 快手视频, 短视频搜索, kuaishou_search.py
platforms: windows, macos, linux, pc
---

# 快手搜索自动化脚本 (`kuaishou_search.py`)

## 用途

使用 browser-cdp skill 搜索快手，获取视频、用户等元数据信息。

## 技术特征分析

### 网站结构

- **搜索接口**：`https://www.kuaishou.com/search/video?searchKey={keyword}`
- **视频详情**：`https://www.kuaishou.com/short-video/{id}`
- **用户主页**：`https://www.kuaishou.com/profile/{user_id}`
- **数据格式**：前端渲染，数据通过 AJAX 加载
- **主要 API**：内部 API 需逆向

### 反爬机制

| 机制 | 强度 | 说明 |
|------|------|------|
| IP 频率限制 | ⭐⭐⭐⭐ | 高频请求易触发限制 |
| UA 检测 | ⭐⭐⭐ | 检测非浏览器 UA |
| 验证码 | ⭐⭐⭐ | 偶尔触发滑块验证码 |
| 登录态 | ⭐⭐ | 部分功能需登录 |
| 设备指纹 | ⭐⭐⭐ | 中等强度设备检测 |

### 抓取策略

```python
# 推荐策略
- 使用 browser-cdp + --stealth 模式
- 请求间隔 5-15 秒
- 建议低频使用
- 配合代理池效果更佳
```

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 搜索视频
python src/searchers/kuaishou_search.py "美食" --type video --max-results 10

# 搜索用户
python src/searchers/kuaishou_search.py "美食博主" --type user --max-results 5

# 使用已登录的浏览器实例
python src/searchers/kuaishou_search.py "热点" --dedicated --name kuaishou_session
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（必填） | - |
| `--type` | 搜索类型 (video/user) | video |
| `--max-results` | 最大结果数量 | 10 |
| `--output-dir` | 输出目录 | `./search_results/kuaishou` |
| `--port` | CDP 调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--dedicated` | 使用专用浏览器实例 | False |
| `--name` | 浏览器实例名称 | - |
| `--wait-timeout` | 页面等待超时(秒) | 60 |

## 输出格式

```json
{
  "title": "视频标题",
  "url": "https://www.kuaishou.com/short-video/1234567890",
  "author": "作者昵称",
  "author_id": "user_id_xxx",
  "play_count": "100万",
  "like_count": "5万",
  "comment_count": "3000",
  "duration": "00:45",
  "source": "kuaishou",
  "scraped_at": "2026-08-05 10:30:00"
}
```

## 核心实现要点

- 使用 JS 提取视频列表中的元数据
- 播放量、点赞数从 DOM 中提取
- 支持无限滚动加载更多结果
- 必须使用 stealth 模式

## 注意事项

- 快手反爬较强，建议低频使用
- 建议配合代理池使用
- 不适合批量抓取场景
- 仅抓取公开可见的元数据
