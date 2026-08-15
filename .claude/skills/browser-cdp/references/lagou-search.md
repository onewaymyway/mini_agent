---
name: lagou-search	skill: browser-cdp
script: lagou_search.py
description: 拉勾网职位搜索自动化脚本，支持关键词、城市筛选，获取职位列表和薪资信息。
triggers: 拉勾网搜索, lagou search, 招聘, 职位搜索, lagou_search.py
platforms: windows, macos, linux, pc
---

# 拉勾网职位搜索自动化脚本 (`lagou_search.py`)

## 用途

使用 browser-cdp skill 搜索拉勾网职位，获取职位标题、薪资、公司、城市等信息。

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 基础搜索
python src/searchers/lagou_search.py "Python" --city 北京 --max-results 20

# 指定输出目录
python src/searchers/lagou_search.py "人工智能" --city 上海 --output-dir ./lagou_results

# 启用反检测模式
python src/searchers/lagou_search.py "Java" --city 深圳 --stealth --max-results 15
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（必填） | - |
| `--city` | 城市名称 | 全部 |
| `--max-results` | 最大结果数量 | 20 |
| `--output-dir` | 输出目录 | `./search_results/lagou` |
| `--port` | CDP 调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--wait-timeout` | 页面等待超时(秒) | 30 |

## 输出格式

```json
{
  "title": "Python工程师",
  "url": "https://www.lagou.com/jobs/xxx.html",
  "salary": "15-25K",
  "company": "公司名称",
  "city": "北京",
  "experience": "3-5年",
  "education": "本科",
  "tags": ["五险一金", "餐补"],
  "source": "lagou",
  "scraped_at": "2026-08-15 11:58:00"
}
```

## 核心实现要点

- 使用 JS 提取 `.position-item` 或 `. job-list-item` 中的职位信息
- 薪资从 `.salary` 提取，支持 "15-25K" 格式
- 公司名称从 `.company-name` 提取
- 城市信息从 `.position-detail` 中提取
- 支持翻页加载（scrollTop方式触发无限滚动）
- 反检测模式隐藏自动化特征
- 验证码检测：检测 slider 滑块验证

## 注意事项

- 拉勾网反爬较严格，建议启用 `--stealth` 模式
- 搜索结果可能需要滑动加载更多
- 部分公司职位需要登录才能查看完整信息
- 建议控制搜索频率，避免触发验证码

## 技术特征

- **前端框架**: React SPA
- **反爬等级**: 3（高度）
- **验证码类型**: slider（滑块验证）
- **登录要求**: 否（推荐登录）
- **目标成功率**: 75%

## 相关配置

参见 `config/websites/lagou.com.json` 获取完整的站点配置。