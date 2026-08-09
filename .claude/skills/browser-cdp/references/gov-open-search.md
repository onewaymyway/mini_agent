# 全国政府信息公开搜索自动化脚本

本文档介绍全国政府信息公开搜索器（gov_open_search.py）的使用方法。

## 快速开始

### 1. 启动浏览器

```bash
cd .claude/skills/browser-cdp
python src/core/browser_launch.py --dedicated --name gov_open_session --start-url "https://www.gov.cn"
```

### 2. 运行搜索

```bash
# 搜索政策文件
python src/searchers/gov_open_search.py "减税降费" --max-results 20

# 搜索行政法规
python src/searchers/gov_open_search.py "环境保护" --doc_type "行政法规" --max-results 15

# 搜索部门规章
python src/searchers/gov_open_search.py "教育改革" --doc_type "部门规章" --max-results 15

# 搜索地方政府规章
python src/searchers/gov_open_search.py "城市管理" --doc_type "地方政府规章" --max-results 15

# 搜索规范性文件
python src/searchers/gov_open_search.py "安全生产" --doc_type "规范性文件" --max-results 20

# 保存结果
python src/searchers/gov_open_search.py "数字经济" --output-dir ./results
```

## 搜索器参数

### 通用参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（位置参数） | - |
| `--doc_type` | 文档类型（行政法规/部门规章/地方政府规章/规范性文件/政策解读） | 全部 |
| `--max-results` | 最大结果数 | 20 |
| `--port` | 浏览器调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--no-stealth` | 禁用反检测模式 | - |
| `--output-dir` | 输出目录 | - |
| `--timeout` | 等待超时时间（秒） | 30 |
| `--session` | 浏览器会话名称 | gov_open_session |

### Python API 使用

```python
from src.searchers.gov_open_search import GovOpenSearcher

# 创建搜索器
searcher = GovOpenSearcher()

# 执行搜索
results = searcher.search(
    query="减税降费",
    doc_type="政策解读",
    max_results=20,
    port=9333,
    stealth=True,
    output_dir="./results"
)

# 输出结果
for r in results:
    print(f"{r['title']} [{r['doc_type']}]")
```

## 输出格式

### JSON 格式

```json
[
  {
    "id": "gov_12345678",
    "title": "国务院关于促进数字经济发展的指导意见",
    "doc_type": "政策解读",
    "publish_time": "2026-07-15",
    "issuing_dept": "国务院",
    "doc_number": "国发〔2026〕12号",
    "summary": "为深入贯彻落实...",
    "url": "https://www.gov.cn/zhengce/xxxxx.htm",
    "source": "gov_open",
    "scraped_at": "2026-08-03 15:30:00"
  }
]
```

## 数据字段说明

| 字段 | 说明 |
|------|------|
| id | 文档ID |
| title | 文档标题 |
| doc_type | 文档类型 |
| publish_time | 发布时间 |
| issuing_dept | 发布部门 |
| doc_number | 文号 |
| summary | 内容摘要 |
| url | 详情页链接 |
| source | 数据源标识 |
| scraped_at | 抓取时间 |

## 文档类型说明

| 类型代码 | 说明 |
|---------|------|
| 行政法规 | 国务院制定的行政法规 |
| 部门规章 | 国务院各部门制定的规章 |
| 地方政府规章 | 地方政府制定的规章 |
| 规范性文件 | 其他规范性文件 |
| 政策解读 | 政策文件的解读材料 |

## 已知限制

1. **无登录态要求**：政府信息公开网公开信息无需登录
2. **分页限制**：单次最多获取20条结果
3. **关键词匹配**：部分文档标题可能不包含关键词

## 最佳实践

1. **控制请求频率**：使用 `--stealth` 模式，设置合理延迟（2-4秒）
2. **增量更新**：基于发布时间去重
3. **分类筛选**：根据需求选择文档类型

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
python src/core/browser_nav.py --port 9333 --tab <id> --goto "https://www.gov.cn"
```
