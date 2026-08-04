# 链家房产搜索自动化脚本

本文档介绍链家房产搜索器（lianjia_search.py）的使用方法。

## 快速开始

### 1. 启动浏览器

```bash
cd .claude/skills/browser-cdp
python src/core/browser_launch.py --dedicated --name lianjia_session --start-url "https://bj.lianjia.com"
```

### 2. 运行搜索

```bash
# 二手房搜索（北京）
python src/searchers/lianjia_search.py --city bj --type ershoufang --max-results 20

# 租房搜索（上海，指定区域）
python src/searchers/lianjia_search.py --city sh --type zufang --district 朝阳 --max-results 10

# 小区搜索（广州）
python src/searchers/lianjia_search.py --city gz --type xiaoqu --query 天河北 --output-dir ./results
```

## 搜索器参数

### 通用参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--city` | 城市代码（bj/sh/gz/sz/cd/wh/nj/hz/xa/tl） | bj |
| `--type` | 房源类型（ershoufang/zufang/xiaoqu） | ershoufang |
| `--district` | 区域名称（如"朝阳"、"海淀"） | - |
| `--query` | 搜索关键词（小区名/地段） | - |
| `--max-results` | 最大结果数 | 20 |
| `--port` | 浏览器调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--no-stealth` | 禁用反检测模式 | - |
| `--output-dir` | 输出目录 | - |
| `--timeout` | 等待超时时间（秒） | 30 |

### Python API 使用

```python
from src.searchers.lianjia_search import LianjiaSearcher
from src.searchers.base import SearcherConfig

# 创建搜索器
searcher = LianjiaSearcher()

# 执行搜索
results = searcher.search(
    query="天河北",
    city="gz",
    type="ershoufang",
    district="天河",
    max_results=20,
    port=9333,
    stealth=True,
    output_dir="./results"
)

# 输出结果
for r in results:
    print(f"{r['title']}: {r['price']}")
```

## 输出格式

### JSON 格式

```json
[
  {
    "title": "南向两居室 采光好",
    "url": "https://bj.lianjia.com/ershoufang/12345678.html",
    "price": "850万",
    "unit_price": "75000元/平米",
    "info": "2室1厅|85.3平米|南|简装|有电梯",
    "position": "海淀-中关村 中关村公馆",
    "follow": "关注127人带看3次",
    "source": "lianjia",
    "scraped_at": "2026-08-03 15:30:00"
  }
]
```

### CSV 格式

```csv
title,url,price,unit_price,info,position,follow,source,scraped_at
南向两居室 采光好,https://bj.lianjia.com/ershoufang/12345678.html,850万,75000元/平米,2室1厅|85.3平米|南|简装|有电梯,海淀-中关村 中关村公馆,关注127人带看3次,lianjia,2026-08-03T15:30:00
```

## 数据字段说明

| 字段 | 说明 |
|------|------|
| title | 房源标题 |
| url | 详情页链接 |
| price | 总价（万） |
| unit_price | 单价（元/㎡） |
| info | 户型/面积/朝向/装修/电梯信息 |
| position | 区域/小区名称 |
| follow | 关注人数/带看次数 |
| source | 数据源标识 |
| scraped_at | 抓取时间 |

## 已知限制

1. **幽灵房过滤**：自动过滤价格异常、图片缺失、描述空洞的虚假房源
2. **城市支持**：目前支持10个主要城市（bj/sh/gz/sz/cd/wh/nj/hz/xa/tl）
3. **登录态**：部分价格数据可能需要登录态才能显示
4. **分页限制**：默认最多抓取10页，避免触发频率限制

## 最佳实践

1. **控制请求频率**：使用 `--stealth` 模式，设置合理延迟
2. **复用浏览器实例**：使用 `--dedicated --name lianjia_session` 保持会话
3. **按城市分目录存储**：便于增量更新和数据管理
4. **过滤无效数据**：自动过滤幽灵房，提高数据质量

## 错误处理

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| 浏览器连接失败 | 端口不通 | 检查浏览器是否启动，使用 `--list-running` 查看 |
| 验证码检测 | 触发反爬 | 启用 `--stealth` 模式，降低请求频率 |
| 搜索结果为空 | 页面结构变化 | 检查选择器，更新 JS 代码 |
| JSON 解析失败 | 提取内容格式异常 | 检查浏览器控制台输出 |

## 调试技巧

```bash
# 查看浏览器状态
python src/core/browser_launch.py --list-running

# 手动导航测试
python src/core/browser_nav.py --port 9333 --tab <id> --goto "https://bj.lianjia.com/ershoufang/"

# 执行 JS 调试
python src/core/browser_console.py --port 9333 --tab <id> --eval "document.querySelectorAll('.sellListContent li').length"
```
