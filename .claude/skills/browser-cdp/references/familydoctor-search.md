# 家庭医生在线搜索自动化脚本

本文档介绍家庭医生在线搜索器（familydoctor_search.py）的使用方法。

## 快速开始

### 1. 启动浏览器

```bash
cd .claude/skills/browser-cdp
python src/core/browser_launch.py --dedicated --name familydoctor_session --start-url "https://www.familydoctor.com.cn"
```

### 2. 运行搜索

```bash
# 搜索疾病知识
python src/searchers/familydoctor_search.py "高血压" --max-results 20

# 搜索药品信息
python src/searchers/familydoctor_search.py "降压药" --max-results 15

# 搜索医院信息
python src/searchers/familydoctor_search.py "心血管科" --max-results 10

# 保存结果
python src/searchers/familydoctor_search.py "糖尿病" --output-dir ./results
```

## 搜索器参数

### 通用参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（位置参数） | - |
| `--max-results` | 最大结果数 | 20 |
| `--port` | 浏览器调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--no-stealth` | 禁用反检测模式 | - |
| `--output-dir` | 输出目录 | - |
| `--timeout` | 等待超时时间（秒） | 30 |
| `--session` | 浏览器会话名称 | familydoctor_session |

### Python API 使用

```python
from src.searchers.familydoctor_search import FamilyDoctorSearcher

# 创建搜索器
searcher = FamilyDoctorSearcher()

# 执行搜索
results = searcher.search(
    query="高血压",
    max_results=20,
    port=9333,
    stealth=True,
    output_dir="./results"
)

# 输出结果
for r in results:
    print(f"{r['title']}: {r['url']}")
```

## 输出格式

### JSON 格式

```json
[
  {
    "id": "familydoctor_123456",
    "title": "高血压的病因、症状及治疗方法",
    "type": "疾病知识",
    "summary": "高血压是一种常见的慢性疾病...",
    "url": "https://www.familydoctor.com.cn/q/123456",
    "source": "familydoctor",
    "scraped_at": "2026-08-03 15:30:00"
  }
]
```

## 数据字段说明

| 字段 | 说明 |
|------|------|
| id | 结果ID |
| title | 标题 |
| type | 结果类型 |
| summary | 摘要内容 |
| url | 详情页链接 |
| source | 数据源标识 |
| scraped_at | 抓取时间 |

## 已知限制

1. **无登录态要求**：家庭医生在线公开信息无需登录
2. **分页限制**：单次最多获取20条结果

## 最佳实践

1. **控制请求频率**：使用 `--stealth` 模式，设置合理延迟（2-4秒）
2. **增量更新**：基于 URL 去重

## 错误处理

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| 浏览器连接失败 | 端口不通 | 检查浏览器是否启动 |
| 验证码检测 | 触发反爬 | 启用 `--stealth` 模式 |
| 搜索结果为空 | 关键词无结果 | 尝试更宽泛的关键词 |

## 调试技巧

```bash
# 查看浏览器状态
python src/core/browser_launch.py --list-running

# 手动导航测试
python src/core/browser_nav.py --port 9333 --tab <id> --goto "https://www.familydoctor.com.cn"
```
