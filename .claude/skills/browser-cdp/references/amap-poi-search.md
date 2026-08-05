# 高德地图 POI 搜索器 (amap_poi_search.py)

## 概述

高德地图 POI 搜索器通过浏览器自动化搜索高德地图的地点兴趣点，支持关键词搜索和周边搜索。

## 功能特性

- 关键词搜索：搜索地点、商户、兴趣点
- 城市定位：支持指定城市搜索
- POI 详情：抓取地点名称、地址、电话、分类
- 反检测模式：支持 stealth 模式

## 使用方法

### 命令行

```bash
# 搜索咖啡店
python amap_poi_search.py "咖啡店" --location "北京"

# 搜索医院
python amap_poi_search.py "医院" --location "上海" --max-results 20

# 搜索餐厅
python amap_poi_search.py "餐厅" --output-dir ./amap_results
```

### Python API

```python
from src.searchers.amap_poi_search import AmapPOISearcher
import asyncio

async def main():
    searcher = AmapPOISearcher(port=9333, stealth=True)
    
    # 搜索
    results = await searcher.search("咖啡店", "北京")
    
    # 获取详情
    detail = await searcher.get_detail("https://www.amap.com/poi/xxx")

asyncio.run(main())
```

## 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| query | str | - | 搜索关键词 |
| location | str | "" | 城市名称 |
| max_results | int | 10 | 最大结果数 |
| output_dir | str | None | 输出目录 |
| port | int | 9333 | 浏览器调试端口 |
| stealth | bool | True | 是否启用反检测模式 |

## 返回格式

```json
{
  "source": "amap",
  "title": "地点名称",
  "url": "https://www.amap.com/poi/xxx",
  "snippet": "地址信息",
  "metadata": {
    "query": "关键词",
    "location": "城市",
    "address": "详细地址",
    "category": "分类"
  },
  "scraped_at": "2024-01-01T00:00:00Z"
}
```

## 注意事项

1. 高德地图搜索需要浏览器已登录
2. 部分 POI 信息可能需要特定权限
3. 搜索频率不宜过高
4. 建议使用已登录的浏览器会话

## 技术实现

- 使用 `browser_cdp` 模块控制浏览器
- 直接访问高德地图站内搜索
- 使用 JavaScript 提取搜索结果
- 支持异步操作

## 相关文件

- 搜索器源码：`src/searchers/amap_poi_search.py`
- 基础类：`src/searchers/base.py`
- 工具函数：`src/searchers/utils.py`
