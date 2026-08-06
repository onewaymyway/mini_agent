# 贝壳找房搜索器文档

**版本**: 1.0.0  
**创建日期**: 2026-08-06  
**关联脚本**: `src/searchers/beike_search.py`

---

## 1. 功能概述

贝壳找房是中国领先的房产交易平台，本搜索器支持：
- 二手房/租房搜索
- 城市筛选
- 房源列表和详情抓取
- 反检测模式

---

## 2. 使用方式

### 2.1 Python API

```python
from src.searchers import BeikeSearcher, BeikeConfig

# 创建配置
config = BeikeConfig(
    query="三居室",
    city="北京",
    house_type="ershoufang",
    max_results=20,
)

# 创建搜索器
searcher = BeikeSearcher(config=config)

# 执行搜索
results = await searcher.search("三居室")

# 输出结果
for house in results[:10]:
    print(f"{house.title} - {house.price} - {house.community}")

# 关闭资源
await searcher.close()
```

### 2.2 命令行

```bash
cd .claude/skills/browser-cdp
python src/searchers/beike_search.py \
    --port 9333 \
    --tab <tab_id> \
    --keyword "三居室" \
    --city "北京" \
    --type ershoufang \
    --max-results 20 \
    --output output/beike_results.json
```

---

## 3. 配置参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| query | str | "" | 搜索关键词 |
| city | str | "" | 城市名称 |
| house_type | str | "ershoufang" | 房源类型：ershoufang/zuizu |
| max_results | int | 10 | 最大结果数 |
| stealth | bool | True | 是否启用反检测模式 |
| fetch_detail | bool | False | 是否抓取房源详情 |

---

## 4. 输出格式

### 4.1 房源信息结构

```json
{
  "source": "beike",
  "title": "中关村 双榆树 三居室",
  "url": "https://bj.ke.com/...",
  "community": "双榆树小区",
  "district": "海淀区",
  "price": "850万",
  "unit_price": "85000元/平",
  "area": "100平",
  "layout": "3室2厅",
  "floor": "中楼层/20层",
  "direction": "南向",
  "tags": ["近地铁", "满五唯一"],
  "scraped_at": "2026-08-06T10:00:00"
}
```

---

## 5. 技术要点

### 5.1 搜索流程

1. 导航到贝壳找房首页
2. 选择房源类型（二手房/租房）
3. 在搜索框输入关键词
4. 点击搜索按钮
5. 等待搜索结果加载
6. 提取房源列表
7. 无限滚动加载更多

### 5.2 反检测策略

- 启用 stealth 模式移除 webdriver 标识
- 随机延迟 1-3 秒
- 模拟人类滚动行为
- 使用真实浏览器指纹

### 5.3 注意事项

- 部分房源信息需要登录才能查看
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

- [贝壳找房官网](https://www.ke.com)
- [browser-cdp 使用指南](./searchers-guide.md)
