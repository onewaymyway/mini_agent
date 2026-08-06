# 前程无忧搜索器文档

**版本**: 1.0.0  
**创建日期**: 2026-08-06  
**关联脚本**: `src/searchers/51job_search.py`

---

## 1. 功能概述

前程无忧（51job）是中国领先的招聘平台之一，本搜索器支持：
- 关键词搜索职位
- 城市筛选
- 薪资范围筛选
- 职位列表和详情抓取
- 反检测模式

---

## 2. 使用方式

### 2.1 Python API

```python
from src.searchers import FiveOneJobSearcher, FiveOneJobConfig

# 创建配置
config = FiveOneJobConfig(
    query="Python开发",
    city="北京",
    max_results=20,
    stealth=True,
)

# 创建搜索器
searcher = FiveOneJobSearcher(config=config)

# 执行搜索
results = await searcher.search("Python开发")

# 输出结果
for job in results[:10]:
    print(f"{job.title} - {job.company} - {job.salary}")

# 关闭资源
await searcher.close()
```

### 2.2 命令行

```bash
cd .claude/skills/browser-cdp
python src/searchers/51job_search.py \
    --port 9333 \
    --tab <tab_id> \
    --keyword "Python开发" \
    --city "北京" \
    --max-results 20 \
    --output output/51job_results.json
```

---

## 3. 配置参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| query | str | "" | 搜索关键词 |
| city | str | "" | 城市名称 |
| salary_min | int | 0 | 最低薪资（千） |
| salary_max | int | 0 | 最高薪资（千） |
| max_results | int | 10 | 最大结果数 |
| stealth | bool | True | 是否启用反检测模式 |
| enable_infinite_scroll | bool | True | 是否启用无限滚动 |
| fetch_detail | bool | False | 是否抓取职位详情 |

---

## 4. 输出格式

### 4.1 职位信息结构

```json
{
  "source": "51job",
  "title": "Python开发工程师",
  "url": "https://jobs.51job.com/...",
  "company": "某科技公司",
  "salary": "15-25K",
  "location": "北京-朝阳区",
  "experience": "3-5年",
  "education": "本科",
  "published_time": "2026-08-01",
  "scraped_at": "2026-08-06T10:00:00"
}
```

### 4.2 批量结果结构

```json
{
  "source": "51job",
  "query": "Python开发",
  "total_results": 20,
  "results": [...],
  "metadata": {
    "city": "北京",
    "scraped_at": "2026-08-06T10:00:00"
  }
}
```

---

## 5. 技术要点

### 5.1 搜索流程

1. 导航到前程无忧首页
2. 在搜索框输入关键词
3. 点击搜索按钮
4. 等待搜索结果加载
5. 提取职位列表
6. 无限滚动加载更多

### 5.2 反检测策略

- 启用 stealth 模式移除 webdriver 标识
- 随机延迟 1-3 秒
- 模拟人类滚动行为
- 使用真实浏览器指纹

### 5.3 注意事项

- 部分职位信息需要登录才能查看完整内容
- 搜索结果可能受地理位置影响
- 建议设置合理的 max_results（不超过50）

---

## 6. 错误处理

| 错误类型 | 原因 | 解决方案 |
|----------|------|----------|
| 搜索无结果 | 关键词过于具体 | 尝试更通用的关键词 |
| 反爬拦截 | 请求频率过高 | 增加随机延迟 |
| 登录提示 | 需要登录查看 | 使用已登录的浏览器实例 |

---

## 7. 相关资源

- [前程无忧官网](https://www.51job.com)
- [browser-cdp 使用指南](./searchers-guide.md)
