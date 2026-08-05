# 脉脉搜索器 (maimai_search.py)

## 概述

脉脉搜索器通过浏览器自动化搜索脉脉的职场社交内容，支持职场话题、公司动态、匿名爆料等搜索。

## 功能特性

- 关键词搜索：搜索脉脉职场话题和公司动态
- 内容分类：支持话题/公司/洞察/搜索
- 内容详情：抓取帖子标题、内容、作者、点赞数
- 反检测模式：支持 stealth 模式

## 使用方法

### 命令行

```bash
# 搜索职场话题
python maimai_search.py "AI 公司" --max-results 10

# 搜索公司动态
python maimai_search.py "腾讯" --type company

# 搜索职场洞察
python maimai_search.py "职场" --type insight

# 保存结果
python maimai_search.py "互联网" --output-dir ./maimai_results
```

### Python API

```python
from src.searchers.maimai_search import MaimaiSearcher
import asyncio

async def main():
    searcher = MaimaiSearcher(port=9333, stealth=True)
    
    # 搜索
    results = await searcher.search("AI 公司", "topic")
    
    # 获取详情
    detail = await searcher.get_detail("https://www.maimai.cn/d/feed/detail/xxx")

asyncio.run(main())
```

## 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| query | str | - | 搜索关键词 |
| content_type | str | "all" | 内容类型：all/topic/company/insight |
| max_results | int | 10 | 最大结果数 |
| output_dir | str | None | 输出目录 |
| port | int | 9333 | 浏览器调试端口 |
| stealth | bool | True | 是否启用反检测模式 |

## 返回格式

```json
{
  "source": "maimai",
  "title": "帖子标题",
  "url": "https://www.maimai.cn/d/feed/detail/xxx",
  "snippet": "帖子摘要",
  "metadata": {
    "query": "关键词",
    "type": "topic",
    "author": "作者名"
  },
  "scraped_at": "2024-01-01T00:00:00Z"
}
```

## 注意事项

1. 脉脉有严格的反爬机制，建议使用已登录会话
2. 部分匿名爆料内容可能需要特定权限
3. 搜索频率不宜过高，建议添加随机延迟
4. 部分页面可能需要滑块验证

## 技术实现

- 使用 `browser_cdp` 模块控制浏览器
- 直接访问脉脉站内搜索
- 使用 JavaScript 提取搜索结果
- 支持异步操作

## 相关文件

- 搜索器源码：`src/searchers/maimai_search.py`
- 基础类：`src/searchers/base.py`
- 工具函数：`src/searchers/utils.py`
