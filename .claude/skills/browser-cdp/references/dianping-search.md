# 大众点评搜索器 (dianping_search.py)

## 概述

大众点评搜索器通过浏览器自动化搜索大众点评的商户和评价，支持商户搜索和评价抓取。

## 功能特性

- 关键词搜索：搜索商户、餐厅、服务
- 城市定位：支持指定城市搜索
- 商户详情：抓取商户名称、评分、地址、电话、分类
- 反检测模式：支持 stealth 模式
- 注意：大众点评有较强反爬，建议使用已登录会话

## 使用方法

### 命令行

```bash
# 搜索餐厅
python dianping_search.py "火锅" --city "北京"

# 搜索咖啡店
python dianping_search.py "咖啡店" --city "上海" --max-results 20

# 搜索商户
python dianping_search.py "餐厅" --output-dir ./dianping_results
```

### Python API

```python
from src.searchers.dianping_search import DianpingSearcher
import asyncio

async def main():
    searcher = DianpingSearcher(port=9333, stealth=True)
    
    # 搜索
    results = await searcher.search("火锅", "北京")
    
    # 获取详情
    detail = await searcher.get_detail("https://www.dianping.com/shop/xxx")

asyncio.run(main())
```

## 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| query | str | - | 搜索关键词 |
| city | str | "" | 城市名称 |
| max_results | int | 10 | 最大结果数 |
| output_dir | str | None | 输出目录 |
| port | int | 9333 | 浏览器调试端口 |
| stealth | bool | True | 是否启用反检测模式 |

## 返回格式

```json
{
  "source": "dianping",
  "title": "商户名称",
  "url": "https://www.dianping.com/shop/xxx",
  "snippet": "地址信息",
  "metadata": {
    "query": "关键词",
    "city": "城市",
    "rating": "评分",
    "address": "详细地址",
    "category": "分类"
  },
  "scraped_at": "2024-01-01T00:00:00Z"
}
```

## 注意事项

1. 大众点评有严格的反爬机制，建议使用已登录会话
2. 部分商户信息可能需要登录才能查看
3. 搜索频率不宜过高，建议添加随机延迟
4. 页面可能需要滑块验证

## 技术实现

- 使用 `browser_cdp` 模块控制浏览器
- 直接访问大众点评站内搜索
- 使用 JavaScript 提取搜索结果
- 支持异步操作

## 相关文件

- 搜索器源码：`src/searchers/dianping_search.py`
- 基础类：`src/searchers/base.py`
- 工具函数：`src/searchers/utils.py`
