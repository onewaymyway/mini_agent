# 拉勾网职位搜索器使用指南

## 概述

拉勾网（lagou.com）是中国领先的互联网招聘平台，本搜索器支持关键词搜索职位、城市筛选、薪资范围筛选等功能。

## 技术特点

- **反爬机制**：拉勾网有较强的反爬机制，需启用 stealth 模式
- **登录要求**：部分职位信息需要登录才能查看完整内容
- **动态加载**：职位列表采用动态加载，需支持无限滚动
- **字体加密**：部分薪资信息可能使用字体加密

## 安装与依赖

```bash
# 确保已安装 browser-cdp 技能
pip install -e .

# 启动浏览器（需先启动 Chrome 并开启远程调试）
python -m src.core.browser_launch --headless --port 9333
```

## 命令行使用

### 基本搜索

```bash
python -m src.searchers.lagou_search \
    --tab <tab_id> \
    --keyword "Python开发" \
    --city "北京" \
    --max-results 20 \
    --output output/lagou_results.json
```

### 带薪资范围搜索

```bash
python -m src.searchers.lagou_search \
    --tab <tab_id> \
    --keyword "Java工程师" \
    --city "上海" \
    --salary-min 20 \
    --salary-max 40 \
    --max-results 30
```

### 禁用反检测模式（调试用）

```bash
python -m src.searchers.lagou_search \
    --tab <tab_id> \
    --keyword "产品经理" \
    --no-stealth
```

### 禁用无限滚动

```bash
python -m src.searchers.lagou_search \
    --tab <tab_id> \
    --keyword "数据分析师" \
    --no-scroll
```

### 抓取职位详情

```bash
python -m src.searchers.lagou_search \
    --tab <tab_id> \
    --keyword "前端开发" \
    --detail \
    --output output/lagou_detail.json
```

## 参数说明

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `--port` | int | 否 | 9333 | CDP 调试端口 |
| `--tab` | str | 是 | - | 浏览器 tab ID |
| `--keyword` | str | 是 | - | 搜索关键词 |
| `--city` | str | 否 | "" | 城市名称 |
| `--salary-min` | int | 否 | 0 | 最低薪资（千） |
| `--salary-max` | int | 否 | 0 | 最高薪资（千） |
| `--max-results` | int | 否 | 50 | 最大结果数 |
| `--output` | str | 否 | - | 输出文件路径 |
| `--no-stealth` | flag | 否 | False | 禁用反检测模式 |
| `--no-scroll` | flag | 否 | False | 禁用无限滚动 |
| `--detail` | flag | 否 | False | 抓取职位详情 |

## Python API 使用

```python
from src.searchers.lagou_search import LagouSearcher, LagouConfig

# 创建配置
config = LagouConfig(
    port=9333,
    tab_id="ABC123",
    query="Python开发",
    city="北京",
    salary_min=20,
    salary_max=50,
    max_results=30,
    stealth=True,
    enable_infinite_scroll=True,
    fetch_detail=False,
)

# 创建搜索器
searcher = LagouSearcher(config=config)

# 执行搜索
results = await searcher.search("Python开发")

# 输出结果
for job in results[:10]:
    print(f"{job.title} - {job.company} - {job.salary}")

# 保存结果
import json
with open('results.json', 'w', encoding='utf-8') as f:
    json.dump([r.to_dict() for r in results], f, ensure_ascii=False, indent=2)

# 关闭资源
await searcher.close()
```

## 输出格式

### 职位列表

```json
{
  "source": "lagou",
  "query": "Python开发",
  "total_results": 20,
  "results": [
    {
      "source": "lagou",
      "title": "Python开发工程师",
      "url": "https://www.lagou.com/jobs/123456.html",
      "snippet": "",
      "company": "某互联网公司",
      "salary": "15-25K",
      "location": "北京-朝阳区",
      "experience": "3-5年",
      "education": "本科",
      "tags": ["Python", "Django", "MySQL"],
      "description": "...",
      "benefits": ["五险一金", "带薪年假"],
      "scraped_at": "2026-08-04T08:20:00"
    }
  ]
}
```

### 职位详情

```json
{
  "title": "Python开发工程师",
  "salary": "15-25K·14薪",
  "company": "某互联网公司",
  "location": "北京-朝阳区-望京",
  "experience": "3-5年",
  "education": "本科",
  "description": "职位描述详情...",
  "benefits": ["五险一金", "带薪年假", "节日福利"]
}
```

## 注意事项

1. **登录状态**：建议先手动登录拉勾网，保持登录状态进行搜索
2. **反爬策略**：启用 stealth 模式可降低被检测风险
3. **请求频率**：批量搜索时建议添加随机延迟
4. **结果质量**：部分职位可能因登录限制无法获取完整信息
5. **页面结构**：拉勾网页面结构可能变化，需定期更新选择器

## 常见问题

### Q: 搜索结果为空？

A: 检查以下几点：
- 确认已登录拉勾网
- 尝试更换关键词或城市
- 检查浏览器 tab 是否正确
- 查看日志中的错误信息

### Q: 薪资信息显示乱码？

A: 拉勾网使用字体加密，当前版本暂不支持解码。可尝试：
- 使用 `--no-stealth` 模式
- 手动查看浏览器页面

### Q: 如何批量搜索多个关键词？

A: 使用 `search_batch` 方法：

```python
queries = ["Python开发", "Java开发", "前端开发"]
results = await searcher.search_batch(queries)
```

## 参考文档

- [BaseSearcher 基类](./searcher-architecture.md)
- [浏览器导航](./browser-cdp-sop.md)
- [反检测模式](./anti-detection.md)
- [无限滚动](./infinite-scroll.md)
