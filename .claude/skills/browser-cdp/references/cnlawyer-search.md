# 华律网搜索自动化脚本

本文档介绍华律网搜索器（cnlawyer_search.py）的使用方法。

## 快速开始

### 1. 启动浏览器

```bash
cd .claude/skills/browser-cdp
python src/core/browser_launch.py --dedicated --name cnlawyer_session --start-url "https://www.64365.com"
```

### 2. 运行搜索

```bash
# 搜索律师信息
python src/searchers/cnlawyer_search.py "刑事辩护" --max-results 20

# 搜索法律咨询
python src/searchers/cnlawyer_search.py "离婚纠纷" --max-results 15

# 搜索法律知识
python src/searchers/cnlawyer_search.py "合同法" --max-results 20

# 保存结果
python src/searchers/cnlawyer_search.py "劳动纠纷" --output-dir ./results
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
| `--session` | 浏览器会话名称 | cnlawyer_session |

### Python API 使用

```python
from src.searchers.cnlawyer_search import CnLawyerSearcher

# 创建搜索器
searcher = CnLawyerSearcher()

# 执行搜索
results = searcher.search(
    query="刑事辩护",
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
    "id": "cnlawyer_123456",
    "title": "刑事辩护律师推荐",
    "type": "律师信息",
    "summary": "专业刑事辩护律师团队...",
    "url": "https://www.64365.com/lawyer/123456.html",
    "source": "cnlawyer",
    "scraped_at": "2026-08-03 15:30:00"
  }
]
```

## 数据字段说明

| 字段 | 说明 |
|------|------|
| id | 结果ID |
| title | 标题 |
| type | 结果类型（律师信息/法律咨询/法律知识） |
| summary | 摘要内容 |
| url | 详情页链接 |
| source | 数据源标识 |
| scraped_at | 抓取时间 |

## 已知限制

1. **无登录态要求**：华律网公开信息无需登录
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
python src/core/browser_nav.py --port 9333 --tab <id> --goto "https://www.64365.com"
```
