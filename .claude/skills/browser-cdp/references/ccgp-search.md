# 中国政府采购网搜索自动化脚本

本文档介绍中国政府采购网搜索器（ccgp_search.py）的使用方法。

## 快速开始

### 1. 启动浏览器

```bash
cd .claude/skills/browser-cdp
python src/core/browser_launch.py --dedicated --name ccgp_session --start-url "https://www.ccgp.gov.cn"
```

### 2. 运行搜索

```bash
# 招标公告搜索
python src/searchers/ccgp_search.py "办公设备采购" --type bid_search --max-results 20

# 中标结果搜索
python src/searchers/ccgp_search.py "信息化项目" --type win_search --max-results 20

# 更正公告搜索
python src/searchers/ccgp_search.py "合同变更" --type correction_search --max-results 10

# 单一来源公示搜索
python src/searchers/ccgp_search.py "单一来源" --type single_source_search --max-results 10

# 全类型搜索
python src/searchers/ccgp_search.py "智慧城市" --type all --max-results 30

# 指定省份
python src/searchers/ccgp_search.py "医疗设备" --province "广东" --max-results 20

# 保存结果
python src/searchers/ccgp_search.py "公务用车" --output-dir ./results
```

## 搜索器参数

### 通用参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（位置参数） | - |
| `--type` | 搜索类型（bid_search/win_search/correction_search/single_source_search/all） | all |
| `--province` | 省份名称（可选） | - |
| `--max-results` | 最大结果数 | 20 |
| `--port` | 浏览器调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--no-stealth` | 禁用反检测模式 | - |
| `--output-dir` | 输出目录 | - |
| `--timeout` | 等待超时时间（秒） | 30 |
| `--session` | 浏览器会话名称 | ccgp_session |

### Python API 使用

```python
from src.searchers.ccgp_search import CcgpSearcher

# 创建搜索器
searcher = CcgpSearcher()

# 执行搜索
results = searcher.search(
    query="办公设备采购",
    search_type="bid_search",
    max_results=20,
    port=9333,
    stealth=True,
    output_dir="./results"
)

# 输出结果
for r in results:
    print(f"{r['title']}: {r['publish_time']}")
```

## 输出格式

### JSON 格式

```json
[
  {
    "id": "12345678",
    "title": "XX单位办公设备采购项目招标公告",
    "type": "招标公告",
    "publish_time": "2026-08-03",
    "province": "北京市",
    "buyer": "XX单位",
    "budget": "500万元",
    "deadline": "2026-08-20",
    "url": "https://www.ccgp.gov.cn/cgxinxi/zbgg/xxxxx.html",
    "source": "ccgp",
    "scraped_at": "2026-08-03 15:30:00"
  }
]
```

## 数据字段说明

| 字段 | 说明 |
|------|------|
| id | 公告ID |
| title | 公告标题 |
| type | 公告类型（招标公告/中标结果/更正公告/单一来源公示） |
| publish_time | 发布时间 |
| province | 所属省份 |
| buyer | 采购单位 |
| budget | 预算金额 |
| deadline | 投标截止时间 |
| url | 详情页链接 |
| source | 数据源标识 |
| scraped_at | 抓取时间 |

## 搜索类型说明

| 类型代码 | 说明 | 典型内容 |
|---------|------|---------|
| bid_search | 招标公告 | 采购公告、竞争性谈判公告 |
| win_search | 中标结果 | 中标公告、成交结果 |
| correction_search | 更正公告 | 采购文件变更、延期公告 |
| single_source_search | 单一来源公示 | 单一来源采购公示 |
| all | 全类型 | 以上所有类型合并 |

## 已知限制

1. **无登录态要求**：政府采购网公开信息无需登录即可访问
2. **分页限制**：单次最多获取20条结果，需翻页获取更多信息
3. **省份筛选**：部分省份数据可能不完整

## 最佳实践

1. **控制请求频率**：使用 `--stealth` 模式，设置合理延迟（2-4秒）
2. **增量更新**：基于发布时间去重，避免重复抓取
3. **分类筛选**：根据需求选择合适搜索类型，减少无效数据
4. **定时抓取**：建议每日定时抓取最新公告

## 错误处理

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| 浏览器连接失败 | 端口不通 | 检查浏览器是否启动，使用 `--list-running` 查看 |
| 验证码检测 | 触发反爬 | 启用 `--stealth` 模式，降低请求频率 |
| 搜索结果为空 | 关键词无结果 | 尝试更宽泛的关键词 |
| JSON 解析失败 | 页面结构变化 | 检查选择器，更新 JS 代码 |

## 调试技巧

```bash
# 查看浏览器状态
python src/core/browser_launch.py --list-running

# 手动导航测试
python src/core/browser_nav.py --port 9333 --tab <id> --goto "https://www.ccgp.gov.cn/searchindex.html"

# 执行 JS 调试
python src/core/browser_console.py --port 9333 --tab <id> --eval "document.querySelectorAll('.search-result-item').length"
```
