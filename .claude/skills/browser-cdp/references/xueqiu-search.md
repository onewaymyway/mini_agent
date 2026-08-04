# 雪球金融数据搜索自动化脚本

本文档介绍雪球金融数据搜索器（xueqiu_search.py）的使用方法。

## 快速开始

### 1. 启动浏览器（必须登录）

```bash
cd .claude/skills/browser-cdp
python src/core/browser_launch.py --dedicated --name xueqiu_session --start-url "https://xueqiu.com"
```

**重要**：首次使用时需要在浏览器中手动登录雪球，然后按提示继续。

### 2. 运行搜索

```bash
# 行情数据（A股）
python src/searchers/xueqiu_search.py --symbol 600519 --type quote --max-results 5

# 行情数据（美股）
python src/searchers/xueqiu_search.py --symbol AAPL --type quote --max-results 5

# 讨论数据
python src/searchers/xueqiu_search.py --symbol 00700 --type discussion --max-results 20

# 组合持仓
python src/searchers/xueqiu_search.py --symbol P123456 --type portfolio --max-results 20
```

## 搜索器参数

### 通用参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 股票代码或名称（位置参数） | - |
| `--symbol` | 股票代码（如 AAPL, 00700, sh600519） | - |
| `--type` | 数据类型（quote/discussion/portfolio） | quote |
| `--max-results` | 最大结果数 | 10 |
| `--port` | 浏览器调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--no-stealth` | 禁用反检测模式 | - |
| `--output-dir` | 输出目录 | - |
| `--timeout` | 等待超时时间（秒） | 30 |
| `--session` | 浏览器会话名称 | xueqiu_session |

### Python API 使用

```python
from src.searchers.xueqiu_search import XueqiuSearcher
from src.searchers.base import SearcherConfig

# 创建搜索器
searcher = XueqiuSearcher()

# 执行搜索（行情）
results = searcher.search(
    symbol="600519",
    type="quote",
    max_results=5,
    port=9333,
    stealth=True,
    output_dir="./results",
    session_name="xueqiu_session"
)

# 输出结果
for r in results:
    print(f"{r['name']}: {r['current']} ({r['change_percent']}%)")
```

## 输出格式

### 行情数据 JSON

```json
[
  {
    "symbol": "sh600519",
    "name": "贵州茅台",
    "current": 1685.00,
    "change_percent": 1.25,
    "change_amount": 20.70,
    "volume": 1234567,
    "market_cap": 2100000000000,
    "pe_ratio": 35.2,
    "high": 1695.00,
    "low": 1660.00,
    "open": 1668.00,
    "prev_close": 1664.30,
    "source": "xueqiu",
    "scraped_at": "2026-08-03 15:30:00"
  }
]
```

### 讨论数据 JSON

```json
[
  {
    "id": "123456789",
    "text": "茅台今天走势不错，继续持有...",
    "title": "",
    "user": "投资小能手",
    "user_id": "123456",
    "created_at": "2026-08-03 14:30:00",
    "like_count": 25,
    "reply_count": 8,
    "source": "xueqiu",
    "scraped_at": "2026-08-03 15:30:00"
  }
]
```

### 组合持仓 JSON

```json
[
  {
    "symbol": "sh600519",
    "name": "贵州茅台",
    "weight": 35.5,
    "shares": 100,
    "cost": 1500.00,
    "current": 1685.00,
    "profit": 12.3,
    "source": "xueqiu",
    "scraped_at": "2026-08-03 15:30:00"
  }
]
```

## 数据字段说明

### 行情数据

| 字段 | 说明 |
|------|------|
| symbol | 股票代码（带前缀） |
| name | 股票名称 |
| current | 当前价格 |
| change_percent | 涨跌幅（%） |
| change_amount | 涨跌额 |
| volume | 成交量 |
| market_cap | 市值 |
| pe_ratio | 市盈率（TTM） |
| high | 最高价 |
| low | 最低价 |
| open | 开盘价 |
| prev_close | 昨收价 |

### 讨论数据

| 字段 | 说明 |
|------|------|
| id | 帖子ID |
| text | 帖子内容 |
| title | 帖子标题 |
| user | 用户名 |
| user_id | 用户ID |
| created_at | 发布时间 |
| like_count | 点赞数 |
| reply_count | 回复数 |

### 组合持仓

| 字段 | 说明 |
|------|------|
| symbol | 股票代码 |
| name | 股票名称 |
| weight | 持仓占比（%） |
| shares | 持仓数量 |
| cost | 成本价 |
| current | 当前价 |
| profit | 收益率（%） |

## 股票代码格式

| 市场 | 格式示例 | 说明 |
|------|---------|------|
| A股（沪市） | sh600519 | 自动识别6位代码 |
| A股（深市） | sz000858 | 自动识别6位代码 |
| 港股 | 00700.HK | 需加.HK后缀 |
| 美股 | AAPL | 直接使用代码 |

## 已知限制

1. **登录态必须**：雪球大部分 API 需要登录，首次使用需手动登录
2. **xq_a_token 过期**：认证 token 有有效期，需定期刷新登录态
3. **频率限制严格**：每分钟请求数限制较严，需控制抓取节奏
4. **组合数据限制**：非公开组合可能无法访问

## 最佳实践

1. **复用浏览器实例**：使用 `--dedicated --name xueqiu_session` 保持登录态
2. **控制请求频率**：使用 `--stealth` 模式，设置合理延迟（2-4秒）
3. **批量查询**：利用批量行情 API 一次获取多个股票代码
4. **增量更新**：基于时间戳去重，避免重复抓取

## 错误处理

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| 浏览器连接失败 | 端口不通 | 检查浏览器是否启动，使用 `--list-running` 查看 |
| 登录态失效 | token 过期 | 重新登录雪球账号 |
| 验证码检测 | 触发反爬 | 启用 `--stealth` 模式，降低请求频率 |
| 搜索结果为空 | API 返回空 | 检查股票代码格式是否正确 |
| JSON 解析失败 | 接口返回异常 | 检查浏览器控制台输出 |

## 调试技巧

```bash
# 查看浏览器状态
python src/core/browser_launch.py --list-running

# 检查登录状态
python src/core/browser_console.py --port 9333 --tab <id> --eval "document.cookie.indexOf('xq_a_token')"

# 手动导航测试
python src/core/browser_nav.py --port 9333 --tab <id> --goto "https://xueqiu.com/"
```
