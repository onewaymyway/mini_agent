# 美团商户搜索器文档

**版本**: 1.0.0  
**创建日期**: 2026-08-06  
**关联脚本**: `src/searchers/meituan_search.py`

---

## 1. 功能概述

美团是中国领先的生活服务平台，本搜索器支持：
- 商户关键词搜索
- 城市筛选
- 商户列表和详情抓取
- 评价抓取
- 反检测模式

---

## 2. 使用方式

### 2.1 Python API

```python
from src.searchers import MeituanSearcher, MeituanConfig

# 创建配置
config = MeituanConfig(
    query="火锅",
    city="北京",
    max_results=20,
    fetch_reviews=True,
)

# 创建搜索器
searcher = MeituanSearcher(config=config)

# 执行搜索
results = await searcher.search("火锅")

# 输出结果
for merchant in results[:10]:
    print(f"{merchant.title} - {merchant.rating} - {merchant.price_per_person}")

# 关闭资源
await searcher.close()
```

### 2.2 命令行

```bash
cd .claude/skills/browser-cdp
python src/searchers/meituan_search.py \
    --port 9333 \
    --tab <tab_id> \
    --keyword "火锅" \
    --city "北京" \
    --max-results 20 \
    --reviews \
    --output output/meituan_results.json
```

---

## 3. 配置参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| query | str | "" | 搜索关键词 |
| city | str | "" | 城市名称 |
| category | str | "" | 商户分类 |
| max_results | int | 10 | 最大结果数 |
| stealth | bool | True | 是否启用反检测模式 |
| fetch_detail | bool | False | 是否抓取商户详情 |
| fetch_reviews | bool | False | 是否抓取商户评价 |

---

## 4. 输出格式

### 4.1 商户信息结构

```json
{
  "source": "meituan",
  "title": "海底捞火锅(王府井店)",
  "url": "https://www.meituan.com/...",
  "category": "火锅",
  "rating": "4.8分",
  "review_count": "1234条评价",
  "price_per_person": "120元",
  "address": "北京市东城区王府井大街...",
  "tags": ["品牌火锅", "环境好", "服务佳"],
  "scraped_at": "2026-08-06T10:00:00"
}
```

### 4.2 评价信息结构

```json
{
  "source": "meituan",
  "title": "海底捞火锅(王府井店)",
  "user_name": "用户123",
  "rating": "5分",
  "content": "味道很好，服务周到...",
  "date": "2026-08-01",
  "scraped_at": "2026-08-06T10:00:00"
}
```

---

## 5. 技术要点

### 5.1 搜索流程

1. 导航到美团首页
2. 在搜索框输入关键词
3. 点击搜索按钮
4. 等待搜索结果加载
5. 提取商户列表
6. 无限滚动加载更多

### 5.2 反检测策略

- 启用 stealth 模式移除 webdriver 标识
- 随机延迟 1-3 秒
- 模拟人类滚动行为
- 使用真实浏览器指纹

### 5.3 注意事项

- 部分商户信息需要登录才能查看
- 评价内容可能受地理位置影响
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

- [美团官网](https://www.meituan.com)
- [browser-cdp 使用指南](./searchers-guide.md)
