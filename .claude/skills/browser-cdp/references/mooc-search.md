---
name: mooc-search
skill: browser-cdp
script: mooc_search.py
description: 中国大学MOOC搜索器，支持课程列表搜索和课程详情抓取，输出JSON格式结果。
triggers: 中国大学MOOC, mooc, icourse163, 慕课, 课程搜索, mooc_search.py
platforms: windows, macos, linux, pc
---

# 中国大学MOOC搜索器 (`mooc_search.py`)

## 用途

使用 browser-cdp skill 搜索中国大学MOOC（icourse163.org）课程，支持课程列表搜索和课程详情抓取，输出 JSON 格式结果。

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 基础搜索
python src/searchers/mooc_search.py "Python" --max-results 10

# 按高校筛选
python src/searchers/mooc_search.py "机器学习" --university "北京大学" --output-dir ./mooc_results

# 获取课程详情
python src/searchers/mooc_search.py "数据结构" --detail --port 9333

# 指定输出目录
python src/searchers/mooc_search.py "人工智能" --max-results 5 --output-dir ./mooc_results
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（必填） | - |
| `--max-results` | 最大结果数量 | 10 |
| `--university` | 按高校筛选（如：北京大学） | 不限 |
| `--output-dir` | 输出目录 | 不保存文件 |
| `--port` | CDP 调试端口 | 9333 |
| `--tab` | 指定 Tab ID | 自动创建 |
| `--stealth` | 启用反检测模式 | True |
| `--no-stealth` | 禁用反检测模式 | - |
| `--wait-timeout` | 页面等待超时(秒) | 30 |
| `--detail` | 获取每个结果的课程详情 | False |

## 输出格式

### 搜索结果列表

```json
[
  {
    "title": "Python语言程序设计",
    "url": "https://www.icourse163.org/course/BIT-1001",
    "university": "北京理工大学",
    "teacher": "嵩天",
    "description": "Python语言程序设计入门课程...",
    "students": "已学人数：120万",
    "rating": "4.9",
    "source": "mooc",
    "query": "Python",
    "scraped_at": "2026-08-04 08:30:00"
  }
]
```

### 课程详情（--detail 模式）

```json
{
  "title": "Python语言程序设计",
  "url": "https://www.icourse163.org/course/BIT-1001",
  "university": "北京理工大学",
  "teacher": "嵩天",
  "description": "Python语言程序设计入门课程...",
  "students": "已学人数：120万",
  "rating": "4.9",
  "status": "开课中",
  "chapters": "共14章",
  "language": "中文",
  "course_type": "免费",
  "source": "mooc",
  "scraped_at": "2026-08-04 08:30:00"
}
```

## 核心实现要点

- 继承 `BaseSearcher` 基类，实现 `source_name`、`supported_types`、`search()`、`get_detail()` 方法
- 使用 `browser_nav.py` 导航到搜索结果页和课程详情页
- 使用 `browser_console.py` 执行 JS 提取页面内容
- 支持多种 CSS 选择器以适配不同页面结构
- 支持按高校筛选课程
- 支持获取课程详情（高校、讲师、简介、学生数、评分等）
- 输出 JSON 格式结果，可直接用于后续处理

## 注意事项

- 首次使用需确保浏览器 CDP 服务已启动（端口 9333）
- 中国大学MOOC部分课程需要登录才能查看完整内容
- 搜索频率不宜过高，避免触发反爬机制
- 页面结构可能变化，如提取失败需更新 CSS 选择器
- 课程详情页选择器已做兼容处理，适配多种页面布局
