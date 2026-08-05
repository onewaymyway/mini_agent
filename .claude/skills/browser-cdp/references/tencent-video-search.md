# 腾讯视频搜索器 (tencent_video_search.py)

## 概述

腾讯视频搜索器通过浏览器自动化搜索腾讯视频的影视内容，支持电影、电视剧、综艺等分类搜索。

## 功能特性

- 关键词搜索：直接在腾讯视频站内搜索
- 内容分类：支持电影/电视剧/综艺/纪录片
- 视频详情：抓取视频标题、简介、播放量、发布时间
- 反检测模式：支持 stealth 模式

## 使用方法

### 命令行

```bash
# 搜索电影
python tencent_video_search.py "流浪地球" --max-results 10

# 搜索电视剧
python tencent_video_search.py "庆余年" --type tv

# 搜索综艺
python tencent_video_search.py "歌手" --type variety

# 保存结果
python tencent_video_search.py "电影" --output-dir ./tencent_results
```

### Python API

```python
from src.searchers.tencent_video_search import TencentVideoSearcher
import asyncio

async def main():
    searcher = TencentVideoSearcher(port=9333, stealth=True)
    
    # 搜索
    results = await searcher.search("流浪地球", "movie")
    
    # 获取详情
    detail = await searcher.get_detail("https://v.qq.com/x/cover/xxx.html")

asyncio.run(main())
```

## 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| query | str | - | 搜索关键词 |
| content_type | str | "all" | 内容类型：all/movie/tv/variety |
| max_results | int | 10 | 最大结果数 |
| output_dir | str | None | 输出目录 |
| port | int | 9333 | 浏览器调试端口 |
| stealth | bool | True | 是否启用反检测模式 |

## 返回格式

```json
{
  "source": "tencent_video",
  "title": "视频标题",
  "url": "https://v.qq.com/x/cover/xxx.html",
  "snippet": "视频简介",
  "metadata": {
    "query": "关键词",
    "type": "movie",
    "cover": "https://..."
  },
  "scraped_at": "2024-01-01T00:00:00Z"
}
```

## 注意事项

1. 腾讯视频有较严格的反爬机制
2. 部分视频需要会员才能观看完整内容
3. 建议使用已登录的浏览器会话
4. 搜索页面可能需要验证码

## 技术实现

- 使用 `browser_cdp` 模块控制浏览器
- 直接访问腾讯视频站内搜索
- 使用 JavaScript 提取搜索结果
- 支持异步操作

## 相关文件

- 搜索器源码：`src/searchers/tencent_video_search.py`
- 基础类：`src/searchers/base.py`
- 工具函数：`src/searchers/utils.py`
