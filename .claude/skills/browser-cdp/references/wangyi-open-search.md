# 网易公开课搜索器

## 概述

网易公开课（open.163.com）提供国内外名校公开课、TED演讲、纪录片等视频资源。

## 技术特征

- **技术栈**: SSR，轻度反爬
- **搜索功能**: 课程搜索、视频搜索
- **数据格式**: HTML
- **反爬强度**: ⭐⭐（反爬较弱）
- **登录需求**: 无需登录

## 使用方法

```bash
# 搜索课程
python wangyi_open_search.py "Python"

# 搜索特定类型
python wangyi_open_search.py "哈佛大学" --type course

# 保存到指定目录
python wangyi_open_search.py "机器学习" --output-dir ./wangyi_results
```

## API 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| query | str | - | 搜索关键词 |
| search_type | str | "all" | 搜索类型 (all/course/video) |
| max_results | int | 20 | 最大结果数 |
| port | int | 9333 | 浏览器调试端口 |
| tab_id | str | None | Tab ID |
| stealth | bool | True | 是否启用反检测模式 |
| output_dir | str | None | 输出目录 |
| wait_timeout | int | 30 | 等待超时时间（秒） |

## 返回数据结构

```json
{
  "title": "Python 入门教程",
  "description": "零基础学习 Python 编程语言",
  "duration": "45:30",
  "views": "100万+",
  "url": "https://open.163.com/movie/xxx.html"
}
```

## 注意事项

1. 网易公开课访问稳定，无需登录
2. 视频详情页包含播放地址、简介等信息
3. 建议每次搜索间隔 3-5 秒

## 相关文档

- [website-analysis.md](./website-analysis.md) - 网站结构分析
- [anti-detection.md](./anti-detection.md) - 反检测策略
