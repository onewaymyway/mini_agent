# 学堂在线搜索器

## 概述

学堂在线（www.xuetangx.com）是清华大学发起的在线教育平台，提供国内外名校课程。

## 技术特征

- **技术栈**: SSR，轻度反爬
- **搜索功能**: 课程搜索
- **数据格式**: HTML
- **反爬强度**: ⭐⭐（反爬较弱）
- **登录需求**: 可选

## 使用方法

```bash
# 搜索课程
python xuetangx_search.py "Python"

# 搜索特定大学课程
python xuetangx_search.py "机器学习" --university 清华

# 保存到指定目录
python xuetangx_search.py "人工智能" --output-dir ./xuetangx_results
```

## API 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| query | str | - | 搜索关键词 |
| university | str | None | 大学名称（可选） |
| max_results | int | 20 | 最大结果数 |
| port | int | 9333 | 浏览器调试端口 |
| tab_id | str | None | Tab ID |
| stealth | bool | True | 是否启用反检测模式 |
| output_dir | str | None | 输出目录 |
| wait_timeout | int | 30 | 等待超时时间（秒） |

## 返回数据结构

```json
{
  "title": "Python 语言程序设计",
  "university": "清华大学",
  "teacher": "郑莉",
  "students": "10000+",
  "rating": "4.8",
  "url": "https://www.xuetangx.com/course/xxx",
  "type": "course"
}
```

## 注意事项

1. 学堂在线访问相对稳定，但仍建议控制请求频率
2. 课程详情页包含课程大纲、师资介绍等信息
3. 建议每次搜索间隔 3-5 秒

## 相关文档

- [website-analysis.md](./website-analysis.md) - 网站结构分析
- [anti-detection.md](./anti-detection.md) - 反检测策略
