# 爱奇艺搜索器 (iqiyi_search.py)

## 概述

爱奇艺搜索器通过浏览器自动化搜索爱奇艺的影视内容，支持电影、电视剧、综艺等分类搜索。

## 功能特性

- 关键词搜索：直接在爱奇艺站内搜索
- 内容分类：支持电影/电视剧/综艺/动漫
- 视频详情：抓取视频标题、简介、播放量、发布时间
- 反检测模式：支持 stealth 模式

## 使用方法

### 命令行

```bash
# 搜索电影
python iqiyi_search.py "狂飙" --max-results 10

# 搜索电视剧
python iqiyi_search.py "三体" --type tv

# 搜索综艺
python iqiyi_search.py "乘风破浪" --type variety

# 保存结果
python iqiyi_search.py "电影" --output-dir ./iqiyi_results
```

### Python API

```python
from src.searchers.iqiyi_search import IqiyiSearcher
import asyncio

async def main():
    searcher = IqiyiSearcher(port=9333, stealth=True)
    
    # 搜索
    results = await searcher.search("狂飙", "movie")
    
    # 获取详情
    detail = await searcher.get_detail("https://www.iqiyi.com/v_xxx.html")

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
  "source": "iqiyi",
  "title": "视频标题",
  "url": "https://www.iqiyi.com/v_xxx.html",
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

1. 爱奇艺有较严格的反爬机制
2. 部分视频需要会员才能观看完整内容
3. 建议使用已登录的浏览器会话
4. 搜索页面可能需要验证码

## 技术实现

- 使用 `browser_cdp` 模块控制浏览器
- 直接访问爱奇艺站内搜索
- 使用 JavaScript 提取搜索结果
- 支持异步操作

## 相关文件

- 搜索器源码：`src/searchers/iqiyi_search.py`
- 基础类：`src/searchers/base.py`
- 工具函数：`src/searchers/utils.py`
