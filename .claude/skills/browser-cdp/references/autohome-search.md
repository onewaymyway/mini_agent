# 汽车之家搜索器

## 概述

汽车之家（www.autohome.com.cn）是中国最大的汽车资讯平台，提供车型信息、参数配置、车主报价、评测文章等。

## 技术特征

- **技术栈**: 大量 AJAX，需动态等待
- **搜索功能**: 车型搜索、参数配置、车主报价
- **数据格式**: JSON API
- **反爬强度**: ⭐⭐⭐（有频率限制）
- **登录需求**: 可选

## 使用方法

```bash
# 搜索车型
python autohome_search.py "Model 3"

# 搜索参数配置
python autohome_search.py "宝马3系" --type config

# 保存到指定目录
python autohome_search.py "特斯拉" --output-dir ./autohome_results
```

## API 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| query | str | - | 搜索关键词（车型名称） |
| search_type | str | "car" | 搜索类型 (car/config/news) |
| max_results | int | 20 | 最大结果数 |
| port | int | 9333 | 浏览器调试端口 |
| tab_id | str | None | Tab ID |
| stealth | bool | True | 是否启用反检测模式 |
| output_dir | str | None | 输出目录 |
| wait_timeout | int | 30 | 等待超时时间（秒） |

## 返回数据结构

车型结果：
```json
{
  "name": "Model 3",
  "brand": "特斯拉",
  "price_range": "23.19-33.59万",
  "url": "https://www.autohome.com.cn/config/xxx.html",
  "type": "car"
}
```

文章结果：
```json
{
  "title": "Model 3 深度评测",
  "date": "2026-08-01",
  "url": "https://www.autohome.com.cn/news/xxx.html",
  "type": "article"
}
```

## 注意事项

1. 汽车之家有频率限制，建议每次搜索间隔 3-5 秒
2. 车型详情页包含详细参数配置表
3. 建议使用 stealth 模式避免被封
4. 搜索结果可能包含多个年款的同一车型

## 相关文档

- [website-analysis.md](./website-analysis.md) - 网站结构分析
- [anti-detection.md](./anti-detection.md) - 反检测策略
