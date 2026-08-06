# 亚马逊搜索器文档

**版本**: 1.0.0  
**创建日期**: 2026-08-06  
**关联脚本**: `src/searchers/amazon_search.py`

---

## 1. 功能概述

亚马逊是全球最大的电商平台之一，本搜索器支持：
- 多站点搜索（美国/英国/德国/法国/日本）
- 关键词搜索商品
- 商品列表和详情抓取
- 价格/评分/评价提取
- 反检测模式

---

## 2. 使用方式

### 2.1 Python API

```python
from src.searchers import AmazonSearcher, AmazonConfig

# 创建配置
config = AmazonConfig(
    query="laptop",
    marketplace="us",
    max_results=20,
    fetch_reviews=True,
)

# 创建搜索器
searcher = AmazonSearcher(config=config)

# 执行搜索
results = await searcher.search("laptop")

# 输出结果
for product in results[:10]:
    print(f"{product.title} - {product.price} - {product.rating}")

# 关闭资源
await searcher.close()
```

### 2.2 命令行

```bash
cd .claude/skills/browser-cdp
python src/searchers/amazon_search.py \
    --port 9333 \
    --tab <tab_id> \
    --keyword "laptop" \
    --marketplace us \
    --max-results 20 \
    --reviews \
    --output output/amazon_results.json
```

---

## 3. 配置参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| query | str | "" | 搜索关键词 |
| marketplace | str | "us" | 站点：us/uk/de/fr/jp |
| category | str | "" | 商品分类 |
| max_results | int | 10 | 最大结果数 |
| stealth | bool | True | 是否启用反检测模式 |
| fetch_detail | bool | False | 是否抓取商品详情 |
| fetch_reviews | bool | False | 是否抓取商品评价 |

---

## 4. 输出格式

### 4.1 商品信息结构

```json
{
  "source": "amazon",
  "title": "Apple MacBook Pro 14-inch",
  "url": "https://www.amazon.com/dp/...",
  "price": "$1,299.00",
  "original_price": "$1,499.00",
  "rating": "4.7 out of 5 stars",
  "review_count": "2,345 ratings",
  "availability": "In Stock",
  "prime": true,
  "asin": "B0BSHF7WHW",
  "scraped_at": "2026-08-06T10:00:00"
}
```

### 4.2 评价信息结构

```json
{
  "source": "amazon",
  "title": "Great laptop!",
  "content": "This MacBook Pro is amazing...",
  "reviewer": "John D.",
  "rating": "5.0 out of 5 stars",
  "date": "August 1, 2026",
  "verified": true,
  "scraped_at": "2026-08-06T10:00:00"
}
```

---

## 5. 技术要点

### 5.1 搜索流程

1. 导航到亚马逊搜索页面
2. 输入关键词
3. 等待搜索结果加载
4. 提取商品列表
5. 无限滚动加载更多

### 5.2 反检测策略

- 启用 stealth 模式移除 webdriver 标识
- 随机延迟 1-3 秒
- 模拟人类滚动行为
- 使用真实浏览器指纹

### 5.3 注意事项

- 亚马逊对自动化访问非常敏感，建议降低请求频率
- 部分商品信息需要登录才能查看
- 价格信息可能动态变化
- 建议设置合理的 max_results（不超过30）

---

## 6. 错误处理

| 错误类型 | 原因 | 解决方案 |
|----------|------|----------|
| 搜索无结果 | 关键词过于具体 | 尝试更通用的关键词 |
| 反爬拦截 | 请求频率过高 | 增加随机延迟 |
| 登录提示 | 需要登录查看 | 使用已登录的浏览器实例 |
| CAPTCHA | 触发验证码 | 等待后重试或使用代理 |

---

## 7. 相关资源

- [亚马逊美国](https://www.amazon.com)
- [亚马逊英国](https://www.amazon.co.uk)
- [亚马逊德国](https://www.amazon.de)
- [browser-cdp 使用指南](./searchers-guide.md)
