# 淘宝/天猫商品搜索自动化脚本

## 概述

`taobao_search.py` 是淘宝/天猫商品搜索的自动化脚本，支持商品列表抓取和详情提取。

## 功能特性

- **商品搜索**：搜索淘宝/天猫商品，获取标题、价格、销量、店铺、所在地等信息
- **平台选择**：支持淘宝、天猫或两者同时搜索
- **排序方式**：支持按销量、综合、价格升序/降序排序
- **详情抓取**：获取商品的完整详情，包括价格、销量、评价、规格参数等

## 用法

### 命令行

```bash
# 搜索淘宝商品
cd .claude/skills/browser-cdp
python src/searchers/taobao_search.py "iPhone 15" --max-results 10

# 搜索天猫商品
cd .claude/skills/browser-cdp
python src/searchers/taobao_search.py "机械键盘" --platform tmall --max-results 5 --output-dir ./tb_results

# 按销量排序
cd .claude/skills/browser-cdp
python src/searchers/taobao_search.py "运动鞋" --platform both --sort sales --max-results 10

# 使用已登录浏览器
cd .claude/skills/browser-cdp
python src/searchers/taobao_search.py "笔记本电脑" --port 9333 --stealth
```

### Python API

```python
from src.searchers.taobao_search import TaobaoSearcher

# 创建搜索器
searcher = TaobaoSearcher()

# 搜索商品
results = searcher.search(
    query="iPhone 15",
    max_results=10,
    platform="taobao",
    sort="sales",
    stealth=True,
    output_dir="./results"
)

# 获取商品详情
detail = searcher.get_detail("https://item.taobao.com/item.htm?id=123456")
```

## 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `query` | str | - | 搜索关键词 |
| `--platform` | str | `taobao` | 搜索平台：`taobao`/`tmall`/`both` |
| `--sort` | str | `None` | 排序方式：`sales`/`desc`/`price_asc`/`price_desc` |
| `--max-results` | int | 10 | 最大结果数 |
| `--output-dir` | str | `None` | 输出目录 |
| `--port` | int | 9333 | 浏览器调试端口 |
| `--tab` | str | `None` | Tab ID |
| `--stealth` | bool | `True` | 启用反检测模式（必须） |
| `--wait-timeout` | int | 30 | 等待超时时间（秒） |

## 输出格式

### 搜索结果

```json
{
  "title": "Apple iPhone 15 128GB 蓝色",
  "url": "https://item.taobao.com/item.htm?id=123456",
  "price": "¥5999.00",
  "sales": "10000+人付款",
  "shop": "Apple 官方旗舰店",
  "location": "浙江 杭州",
  "image": "https://img.alicdn.com/imgextra/i1/...",
  "platform": "tmall",
  "source": "taobao",
  "type": "product"
}
```

### 商品详情

```json
{
  "title": "Apple iPhone 15 128GB 蓝色",
  "price": "¥5999.00",
  "original_price": "¥6999.00",
  "sales": "10000+人付款",
  "reviews": "5000+条评价",
  "shop": "Apple 官方旗舰店",
  "shop_score": "描述相符 4.9",
  "location": "浙江 杭州",
  "platform": "tmall",
  "images": ["https://img.alicdn.com/..."],
  "description": "iPhone 15，搭载 A16 芯片...",
  "params": {
    "品牌": "Apple",
    "型号": "iPhone 15",
    "存储容量": "128GB"
  },
  "url": "https://item.taobao.com/item.htm?id=123456",
  "source": "taobao"
}
```

## 技术要点

### 搜索 URL

淘宝搜索 URL 格式：
```
https://s.taobao.com/search?q=<query>&coo=<sort>
```

天猫搜索 URL 格式：
```
https://s.tmall.com/search?q=<query>&coo=<sort>
```

### 反爬策略

- **必须启用 `--stealth` 模式**：淘宝反爬机制极强
- **配合代理池**：建议使用代理池轮换 IP
- **控制请求频率**：每次请求间隔 2-5 秒
- **使用已登录态**：部分商品价格需要登录态才能查看

### 验证码处理

淘宝在检测到异常访问时会弹出滑块验证码：
- 滑块验证码：自动处理
- 登录墙：提示用户使用已登录态
- 访问限制：建议更换代理或等待后重试

## 注意事项

1. **反爬极强**：淘宝是反爬最严格的网站之一，必须启用 stealth 模式
2. **登录态**：部分商品价格、库存需要登录态才能查看
3. **代理池**：建议配合代理池使用，避免 IP 被封
4. **请求频率**：严格控制请求频率，建议每次间隔 3-5 秒
5. **结果去重**：自动基于 URL 去重

## 相关文件

- 搜索器：`src/searchers/taobao_search.py`
- 基础类：`src/searchers/base.py`
- 工具函数：`src/searchers/utils.py`
- 浏览器导航：`src/core/browser_nav.py`
- 内容提取：`src/core/browser_console.py`
- 反检测：`src/core/stealth.py`
- 代理池：`src/core/proxy_pool.py`
